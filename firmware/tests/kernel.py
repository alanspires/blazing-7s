"""Native VRTX fixture; not a scheduler model or hardware/timing qualification.

The bare guarded Cpu executes the original kernel instructions with the selected
production task-stack configuration. Only verified TRAP0/RTE
opcode/vector pairs receive explicit 68000 supervisor exception semantics.
Unknown exceptions, user-mode/trace returns and instruction faults fail closed.
"""
from __future__ import annotations

from unicorn import UC_HOOK_INTR
from unicorn.m68k_const import (
    UC_M68K_REG_A7,
    UC_M68K_REG_D0,
    UC_M68K_REG_PC,
    UC_M68K_REG_SR,
)

from firmware.tests.cpu import (
    REGISTERS,
    RETURN_ADDRESS,
    STACK_BASE,
    SUPERVISOR,
    TRACE,
    TRAP,
    TRAP_VECTOR_BASE,
    Cpu,
    CpuError,
)
from firmware.tests.support import (
    DEVICE_PAGE,
    HOST_ADDRESS,
    HOST_PORT,
    MAIN_ROM,
    PORT_PAGE,
    RX,
    build_production,
    build_variant,
)

STATE = (*REGISTERS, UC_M68K_REG_A7)

# 68000 exception architecture. Unicorn reports RTE as QEMU's EXCP_RTE.
RTE, RTE_EXCEPTION = 0x4E73, 0x100
AUTOVECTOR_BASE = 24
IPL = 0x0700
MAX_EXCEPTIONS = 1000

# Original-ROM kernel: configuration pointer, its fields and object layout.
CONFIG_POINTER, CONFIG_WORKSPACE, CONFIG_POOL_SIZE = 0x100, 0x00, 0x04
CONFIG_TASK_STACK, CONFIG_TASKS = 0x16, 0x1C
WS_LIVE, WS_FREE, WS_DELAYED, WS_CURRENT = 0x00, 0x04, 0x08, 0x24
WS_HEAP_END, WS_HEAP_FREE = 0x1C, 0x20
TCB_NEXT, TCB_DELAY_NEXT, TCB_ID, TCB_STATE = 0x00, 0x04, 0x0D, 0x0F
TCB_SSP, TCB_STACK_TOP = 0x38, 0x40  # saved SSP; stack top allocated at kernel init
STATE_DELAYED, STATE_DORMANT = 0x20, 0x80
KERNEL_CODE = (0x2600, 0x3D00)
KERNEL_INIT, KERNEL_INIT_DONE, KERNEL_START = 0xB86, 0xB8C, 0xBEC  # reset's own call sites
CREATE, DELETE, CREATE_QUEUE = 0x3C54, 0x3C8A, 0x3B56
DELAY_TRAP, DELAY_RETURN = 0x3A24, 0x3A26  # inside native sc_delay wrapper 0x3a1e
POST, PEND = 0x3A32, 0x3A52  # native post and pend wrappers

HOST_ID = 0x41  # the fixture machine's SAS address
TX_BUSY_TABLE, TX_BUFFER_TABLE = 0x74E0E, 0x74DDE
RAM = (0x400000, 0x40000)
STATUS = STACK_BASE + 0x10  # service status word, far below the boot frame
UNWRITTEN_STATUS = 0xFFFF


def compile_tasks(tmp_path_factory, task_source: str):
    """Full production build plus added callsites; no native-leaf substitutions."""
    fixture = build_variant(tmp_path_factory, "kernel", files={"test_kernel_tasks.S": task_source})
    production = build_production(tmp_path_factory)
    # Not the production image: the test tasks are extra. The code under test is.
    for name in ("platform_sas_handler", "platform_sas_process", "platform_reconfigure"):
        start = production.symbols[name]
        end = min(a for a in production.symbols.values() if a > start)
        assert fixture.symbols[name] == start
        assert fixture.image[start:end] == production.image[start:end]
    for name in ("sas_saved_sp", "sas_stack_start", "sas_stack_end", "sas_drain_mailbox"):
        assert fixture.symbols[name] == production.symbols[name]
    base = MAIN_ROM.read_bytes()
    config = int.from_bytes(base[CONFIG_POINTER:CONFIG_POINTER + 4], "big")
    stack_size = config + CONFIG_TASK_STACK
    low, high = KERNEL_CODE
    # Only the task-stack configuration word may differ, never kernel instructions.
    assert fixture.image[low:stack_size] == base[low:stack_size]
    assert fixture.image[stack_size + 2:high] == base[stack_size + 2:high]
    assert fixture.image[stack_size:stack_size + 2] == production.image[stack_size:stack_size + 2]
    return fixture


def pattern(address: int, size: int) -> bytes:
    return bytes((a * 7 + 0x5A) & 0xFF for a in range(address, address + size))


class Kernel:
    """Bare CPU with the original kernel started by reset's own call sites."""

    def __init__(self, compiled, tasks, tx_busy=0, *, personality=None):
        self.compiled = compiled
        self.cpu = cpu = Cpu(compiled.image, personality)
        cpu.map_io(DEVICE_PAGE)
        cpu.map_io(PORT_PAGE)
        self.vector = None
        self.pushed: list[int] = []  # bytes written by modeled exception entries
        cpu.uc.hook_add(UC_HOOK_INTR, self._record_vector)
        config = cpu.read_u32(CONFIG_POINTER)
        self.workspace = cpu.read_u32(config + CONFIG_WORKSPACE)
        self.task_stack = cpu.read_u16(config + CONFIG_TASK_STACK)
        self.tasks = cpu.read_u16(config + CONFIG_TASKS)
        self.private = (compiled.symbols["sas_stack_start"], compiled.symbols["sas_stack_end"])
        self.tx_busy = cpu.read_u32(TX_BUSY_TABLE)
        self.tx_buffer = cpu.read_u32(TX_BUFFER_TABLE)
        cpu.write_u8(HOST_PORT, 1)
        cpu.write_u8(HOST_ADDRESS, HOST_ID)
        cpu.write_u8(RX, 0)  # too short: the production body NACKs without core work
        cpu.write_u16(self.tx_busy, tx_busy)
        self.run(KERNEL_INIT, KERNEL_INIT_DONE)
        assert cpu.uc.reg_read(UC_M68K_REG_D0) & 0xFFFF == 0
        for entry, ident, priority in tasks:
            assert self.service(
                CREATE, self.symbol(entry), self.symbol(ident), self.symbol(priority)
            ) == 0

    def symbol(self, name: str) -> int:
        return self.compiled.symbols[name]

    def _record_vector(self, uc, vector, data):
        self.vector = vector

    def _record_push(self) -> None:
        sp = self.cpu.uc.reg_read(UC_M68K_REG_A7)
        self.pushed.extend(range(sp, sp + 6))

    def _exception(self, error: CpuError) -> int:
        vector, self.vector = self.vector, None
        opcode = self.cpu.read_u16(error.address)
        if (vector, opcode) == (TRAP_VECTOR_BASE, TRAP):  # TRAP #0 only
            handler = self.cpu.enter_trap(error.address)
            self._record_push()
            return handler
        if (vector, opcode) == (RTE_EXCEPTION, RTE):
            sp = self.cpu.uc.reg_read(UC_M68K_REG_A7)
            sr, pc = self.cpu.read_u16(sp), self.cpu.read_u32(sp + 2)
            if sr & (SUPERVISOR | TRACE) != SUPERVISOR:
                raise error  # user mode/trace is outside this model
            self.cpu.uc.reg_write(UC_M68K_REG_A7, sp + 6)
            self.cpu.uc.reg_write(UC_M68K_REG_SR, sr)
            return pc
        raise error

    def run(self, start: int, until: int) -> None:
        pc = start
        for _ in range(MAX_EXCEPTIONS):
            try:
                self.cpu.run(pc, until)
                return
            except CpuError as error:
                if error.code != "exception":
                    raise
                pc = self._exception(error)
                self.cpu.uc.reg_write(UC_M68K_REG_PC, pc)
            if pc == until:
                return
        raise AssertionError("exception budget exhausted")

    def call(self, address: int, *args: int) -> None:
        sp = self.cpu.enter(*args)
        self.run(address, RETURN_ADDRESS)
        assert self.cpu.uc.reg_read(UC_M68K_REG_A7) == sp + 4

    def service(self, address: int, *args: int) -> int:
        self.cpu.write_u16(STATUS, UNWRITTEN_STATUS)
        self.call(address, *args, STATUS)
        status = self.cpu.read_u16(STATUS)
        assert status != UNWRITTEN_STATUS
        return status

    def start(self, until: str) -> None:
        self.run(KERNEL_START, self.symbol(until))

    def resume(self, until: int | str) -> None:
        pc = self.cpu.uc.reg_read(UC_M68K_REG_PC)
        self.run(pc, until if isinstance(until, int) else self.symbol(until))

    def interrupt(self, level: int, *, entry: int | None = None, until: int | None = None) -> None:
        """68000 exception entry while stopped, executing the ROM autovector by default.

        Test-only IRQ/service shims must explicitly supply their entry/stop PCs;
        this models a controlled scheduling event, never physical timing.
        """
        sr = self.cpu.read_sr()
        assert (sr & IPL) >> 8 < level
        pc = self.cpu.uc.reg_read(UC_M68K_REG_PC)
        self.cpu.push_supervisor_exception(sr, pc, (sr & ~(TRACE | IPL)) | SUPERVISOR | level << 8)
        self._record_push()
        self.run(entry if entry is not None else self.cpu.read_u32((AUTOVECTOR_BASE + level) * 4),
                 pc if until is None else until)

    def chain(self, head: int, link: int) -> list[int]:
        nodes, node = [], self.cpu.read_u32(head)
        while node:
            nodes.append(node)
            assert len(nodes) <= self.tasks
            node = self.cpu.read_u32(node + link)
        return nodes

    def live(self, name: str) -> int | None:
        ident = self.symbol(name)
        found = [t for t in self.chain(self.workspace + WS_LIVE, TCB_NEXT)
                 if self.cpu.read_u8(t + TCB_ID) == ident]
        assert len(found) <= 1
        return found[0] if found else None

    def native(self, tcb: int) -> tuple[int, int]:
        top = self.cpu.read_u32(tcb + TCB_STACK_TOP)
        return top - self.task_stack, top

    def plant(self, low: int, high: int) -> None:
        self.cpu.write(low, pattern(low, high - low))

    def intact(self, low: int, high: int) -> bool:
        return self.cpu.read(low, high - low) == pattern(low, high - low)

    def ram(self) -> bytes:
        return self.cpu.read(*RAM)

    def registers(self) -> dict[int, int]:
        return self.cpu.read_registers(STATE)

