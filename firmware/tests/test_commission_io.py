"""Actual pre-kernel hooks/adapter with register-memory UART, not hardware timing."""

import struct
import zlib

import pytest
from unicorn.m68k_const import (
    UC_M68K_REG_A0,
    UC_M68K_REG_A7,
)

from firmware.tests.cpu import ENTRY_SR, Cpu, CpuError, marked_registers
from firmware.tests.support import (
    DEVICE_PAGE,
    PERSONALITY,
    PORT_PAGE,
    SAFERAM,
    SAFERAM_SIZE,
    WATCHDOG,
    build_production,
    commission_constant,
    u8,
)
from firmware.tests.support import fw_constant as c

DELIMITER, ESCAPE = commission_constant("DELIMITER"), commission_constant("ESCAPE")
FRAME_BODY = commission_constant("REPLY_HEADER") + commission_constant("MAX_PAYLOAD") + struct.calcsize("<I")


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return build_production(tmp_path_factory)


class Machine:
    def __init__(self, compiled, persistent=None):
        self.compiled = compiled
        self.cpu = Cpu(compiled.image, PERSONALITY)
        self.cpu.map_io(DEVICE_PAGE)
        self.cpu.map_io(PORT_PAGE)
        if persistent is not None:
            self.cpu.write(c("PROFILE_BASE"), persistent)
        self.cpu.write(SAFERAM, b"\x96" * SAFERAM_SIZE)
        self.writes = self.cpu.record_accesses()

    def call(self, symbol, *args, max_insns=4_000_000):
        symbols = self.compiled.symbols
        guard = self.cpu.read(symbols["commission_stack_start"] - 16, 16)
        result = self.cpu.call(symbols[symbol], *args, stack_top=symbols["commission_stack_end"],
                               max_insns=max_insns)
        assert self.cpu.read(symbols["commission_stack_start"] - 16, 16) == guard
        return result

    def step(self, status=0, byte=0):
        self.cpu.write_u8(commission_constant("UART_SR"), status)
        self.cpu.write_u8(commission_constant("UART_DATA"), byte)
        self.call("commission_io_step", max_insns=200_000_000)

    def wire(self, data):
        self.writes.clear()
        for byte in data:
            self.step(1, byte)
        # The encoded frame has a fixed protocol bound, not a timing wait.
        for _ in range(2 * FRAME_BODY + 2):
            self.step(4)
        return bytes(store.value for store in self.writes if store.address == commission_constant("UART_DATA"))

    def assert_safe(self):
        assert self.cpu.read(SAFERAM, SAFERAM_SIZE) == b"\x96" * SAFERAM_SIZE
        assert not any(SAFERAM <= store.address < SAFERAM + SAFERAM_SIZE for store in self.writes)


def frame(payload):
    body = payload + struct.pack("<I", zlib.crc32(payload))
    escaped = bytearray((DELIMITER,))
    for byte in body:
        if byte in (DELIMITER, ESCAPE):
            escaped.extend((ESCAPE, byte ^ 0x20))
        else:
            escaped.append(byte)
    escaped.append(DELIMITER)
    return bytes(escaped)


def test_reset_guard_ignores_retained_native_mode_and_reproduces_reset(compiled):
    m = Machine(compiled)
    guard = compiled.symbols["commission_irq_guard"]
    m.cpu.write_u32(guard, compiled.symbols["CM_NATIVE_GUARD"])
    m.cpu.run(0x800, 0x808)
    assert m.cpu.read_u32(guard) == 0
    assert m.cpu.uc.reg_read(UC_M68K_REG_A7) == int.from_bytes(compiled.image[:4], "big")
    assert m.cpu.read_sr() == 0x2700
    m.assert_safe()


@pytest.mark.parametrize("unknown", [False, True])
def test_real_gate_prevents_native_startup_until_persistence_is_known(compiled, unknown):
    persistent = bytes((0x39 if unknown else 0,)) * c("PERSIST_SIZE")
    m = Machine(compiled, persistent)
    registers = marked_registers()
    sp, sr = int.from_bytes(compiled.image[:4], "big"), 0x2715
    m.cpu.set_state(sp, sr=sr, registers=registers)
    target = compiled.symbols["commission_service"] if unknown else 0xA5E
    m.cpu.run(0xA58, target, max_insns=4_000_000)
    if unknown:
        assert m.cpu.uc.reg_read(UC_M68K_REG_A7) == compiled.symbols["commission_stack_end"]
        assert m.cpu.read(c("PROFILE_BASE"), len(persistent)) == persistent
        assert m.cpu.read_u32(compiled.symbols["commission_irq_guard"]) == 0
    else:
        assert m.cpu.uc.reg_read(UC_M68K_REG_A7) == sp
        registers[UC_M68K_REG_A0] = 0x43DBAE
        assert m.cpu.read_registers(registers) == registers
        assert m.cpu.read_sr() == sr
        assert m.cpu.read_u32(compiled.symbols["commission_irq_guard"]) == compiled.symbols["CM_NATIVE_GUARD"]
    m.assert_safe()


def test_gate_keeps_native_operator_clear_recovery_reachable(compiled):
    m = Machine(compiled)
    assert u8(m.call("fw_invalidate")) == c("OK")
    before = m.cpu.read(c("PROFILE_BASE"), c("PERSIST_SIZE"))
    assert m.call("fw_clear_pending") == 1
    m.cpu.uc.reg_write(UC_M68K_REG_A7, int.from_bytes(compiled.image[:4], "big"))
    m.cpu.run(0xA58, 0xA5E, max_insns=4_000_000)
    assert m.cpu.read(c("PROFILE_BASE"), len(before)) == before
    m.assert_safe()


@pytest.mark.parametrize("normal", [False, True])
def test_ac_fail_vector_preserves_native_path_or_terminally_stops_commissioning(compiled, normal):
    m = Machine(compiled, b"\x39" * c("PERSIST_SIZE"))
    guard = compiled.symbols["commission_irq_guard"]
    m.cpu.write_u32(guard, compiled.symbols["CM_NATIVE_GUARD"] if normal else 0)
    # Model an already-taken exception: this does not qualify electrical NMI timing.
    m.cpu.set_state(compiled.symbols["commission_stack_end"])
    sp = m.cpu.push_supervisor_exception(ENTRY_SR, compiled.symbols["commission_storage_write"], ENTRY_SR)
    exception = m.cpu.read(sp, 6)
    start = int.from_bytes(compiled.image[0x7C:0x80], "big")
    target = 0x275A if normal else compiled.symbols["commission_power_failed"]
    m.cpu.run(start, target)
    assert m.cpu.uc.reg_read(UC_M68K_REG_A7) == sp
    assert m.cpu.read(sp, len(exception)) == exception
    if not normal:
        m.writes.clear()
        with pytest.raises(CpuError) as halted:
            m.cpu.run(target, 0x275A, max_insns=32)
        assert halted.value.code == "instruction-budget"
        assert m.cpu.uc.reg_read(UC_M68K_REG_A7) == compiled.symbols["commission_stack_end"]
        assert m.writes and all(store.address == WATCHDOG for store in m.writes)
    assert m.cpu.read(c("PROFILE_BASE"), c("PERSIST_SIZE")) == b"\x39" * c("PERSIST_SIZE")
    m.assert_safe()


def test_uart_initialization_has_only_the_qualified_register_scope(compiled):
    m = Machine(compiled)
    m.call("commission_io_init")
    writes = [(store.address, store.size, store.value) for store in m.writes if store.address >= DEVICE_PAGE]
    assert writes == [
        (commission_constant("UART_IMR"), 1, 0),
        *((commission_constant("UART_CR"), 1, value) for value in (0x0A, 0x2A, 0x3A, 0x4A, 0x1A)),
        (commission_constant("UART_MR"), 1, 0x33), (commission_constant("UART_MR"), 1, 0x07),
        (commission_constant("UART_ACR"), 1, 0x80), (commission_constant("UART_SR"), 1, 0xCC),
        (commission_constant("UART_CR"), 1, 0x05),
    ]
    for address, size, value in writes:
        if address in (commission_constant("UART_ACR"), commission_constant("UART_SR")):
            assert m.cpu.read(address, size) == value.to_bytes(size, "big")
    m.assert_safe()


@pytest.mark.parametrize("bad", ["crc", "overflow", "escape", "uart"])
def test_frames_fail_closed_then_resynchronize_at_delimiter(compiled, bad):
    m = Machine(compiled, b"\x39" * c("PERSIST_SIZE"))
    m.call("commission_boot")
    m.call("commission_io_init")
    info = b"S6CM\x01\x00"
    original = m.cpu.read(c("PROFILE_BASE"), c("PERSIST_SIZE"))
    malformed = frame(info)
    if bad == "crc":
        malformed = malformed[:-2] + bytes((malformed[-2] ^ 1, malformed[-1]))
    elif bad == "overflow":
        malformed = bytes((DELIMITER,)) + b"x" * (FRAME_BODY + 1) + bytes((DELIMITER,))
    elif bad == "escape":
        malformed = malformed[:-1] + bytes((ESCAPE, DELIMITER))
    else:
        for byte in malformed[:4]:
            m.step(1, byte)
        m.step(0xF1)
        malformed = malformed[4:]
    assert m.wire(malformed) == b""
    good = m.wire(frame(info))
    assert good.startswith(bytes((DELIMITER,)) + b"S6CM\x01\x00\x00")
    assert m.cpu.read(c("PROFILE_BASE"), len(original)) == original
    m.assert_safe()


def test_commission_storage_cannot_cross_into_runtime_or_native_memory(compiled):
    m = Machine(compiled)
    data = compiled.symbols["commission_capture"]
    m.cpu.write(data, b"\xa5\x5a" * 8)
    base, size = commission_constant("RECORD_BASE"), commission_constant("RECORD_SIZE")
    for address, length in ((base + 1, 2), (base, 1), (base - 2, 4),
                            (base + size - 2, 4), (0xFFFFFFFF, 2), (SAFERAM, 2)):
        assert m.call("commission_storage_write", address, data, length) == 0
    assert m.call("commission_storage_write", base, data, 16) == 1
    assert m.cpu.read(base, 16) == b"\xa5\x5a" * 8
    assert [(store.address, store.size) for store in m.writes if base <= store.address < base + size] == [
        (base + i, 2) for i in range(0, 16, 2)
    ]
    m.assert_safe()
