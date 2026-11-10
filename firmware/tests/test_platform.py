"""Execute platform code and native CRC/TX preparation; the UART/RTOS is not simulated."""

import pytest
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_READ
from unicorn.m68k_const import (
    UC_M68K_REG_A0,
    UC_M68K_REG_A1,
    UC_M68K_REG_A7,
    UC_M68K_REG_D0,
    UC_M68K_REG_D1,
    UC_M68K_REG_PC,
)

from firmware.tests.cpu import (
    ENTRY_SR,
    PAGE_SIZE,
    RETURN_ADDRESS,
    STACK_TOP,
    Cpu,
    CpuError,
    decode,
    marked_registers,
)
from firmware.tests.kernel import CREATE_QUEUE, PEND, POST
from firmware.tests.support import (
    CONFIG_INITIALIZED,
    CONFIG_MAGIC,
    CREATE_PARENTS,
    DELETE_PARENTS,
    DEVICE_PAGE,
    E0_POLL,
    HOST_ADDRESS,
    HOST_PORT,
    INIT_PORTS,
    NATIVE_TRANSMIT,
    PERSONALITY,
    PORT_PAGE,
    RECONFIGURE,
    RECONFIGURED,
    RX,
    SAFERAM,
    SAFERAM_SIZE,
    SOURCE,
    STORAGE_WORD,
    VIA_IER,
    VIA_IFR,
    build_variant,
    e0_poll,
    e0_request,
    platform_constant,
    production_link,
    production_patches,
    receive,
    run_native_fatal,
    run_to_platform_fatal,
    transmitted,
    trap1_at,
    u8,
)
from firmware.tests.support import fw_constant as c
from tools.sas_protocol import with_crc

TX, TX_STRIDE = 0x414234, 0x15F6
INFO = e0_request(c("OP_INFO"))
DATA = 0x435100

STUBS = r"""
#include "platform.h"
#include "game.h"
#include "motion.h"
struct test_state {
    uint32_t active, fault, boot_result, clear_result, complete_result;
    uint32_t boots, restores, invalidations, completions, dispatches;
    uint32_t game_inits, motion_inits, applies, idle, motion_busy;
    uint32_t request_length;
    uint8_t request[FW_REQUEST_HEADER + FW_MAX_PAYLOAD];
    uint32_t prepare_result, prepared, finished, finish_status, last_profile, last_active, restore;
    uint32_t boot, close_core;
};
extern struct test_state test_state;
void test_reset(void) {
    uint8_t *p = (uint8_t *)&test_state;
    for (uint32_t i = 0; i < sizeof(test_state); ++i) p[i] = 0;
    test_state.idle = 1;
}
void test_active(uint32_t n) { test_state.active = n; }
void test_fault(uint32_t n) { test_state.fault = n; }
void test_boot_result(uint32_t n) { test_state.boot_result = n; }
void test_clear_result(uint32_t n) { test_state.clear_result = n; }
void test_complete_result(uint32_t n) { test_state.complete_result = n; }
void test_prepare_result(uint32_t n) { test_state.prepare_result = n; }
void test_close_during_core(uint32_t n) { test_state.close_core = n; }
void test_close_gate(void);
uint32_t test_prepared(void) { return test_state.prepared; }
uint32_t test_finished(void) { return test_state.finished; }
uint32_t test_finish_status(void) { return test_state.finish_status; }
uint32_t test_last_profile(void) { return test_state.last_profile; }
uint32_t test_last_active(void) { return test_state.last_active; }
uint32_t test_restore(void) { return test_state.restore; }
uint32_t test_boot(void) { return test_state.boot; }
uint32_t test_boots(void) { return test_state.boots; }
uint32_t test_restores(void) { return test_state.restores; }
uint32_t test_invalidations(void) { return test_state.invalidations; }
uint32_t test_completions(void) { return test_state.completions; }
uint32_t test_dispatches(void) { return test_state.dispatches; }
uint32_t test_game_inits(void) { return test_state.game_inits; }
uint32_t test_motion_inits(void) { return test_state.motion_inits; }
uint32_t test_applies(void) { return test_state.applies; }
uint32_t test_request_length(void) { return test_state.request_length; }
uint32_t test_request(void) { return (uint32_t)(uintptr_t)test_state.request; }
uint8_t fw_boot(void) { ++test_state.boots; return test_state.boot_result; }
uint8_t fw_restore(void) {
    ++test_state.restores;
    return fw_apply_profile((const uint8_t *)(uintptr_t)test_state.active, false);
}
int fw_is_faulted(void) { return test_state.fault != 0; }
const uint8_t *fw_active_profile(void) {
    return (const uint8_t *)(uintptr_t)test_state.active;
}
uint8_t fw_invalidate(void) {
    ++test_state.invalidations;
    return test_state.clear_result;
}
uint8_t fw_clear_complete(void) {
    ++test_state.completions;
    return test_state.complete_result;
}
uint32_t fw_process(const uint8_t *data, uint32_t length, uint8_t *reply, uint32_t capacity) {
    ++test_state.dispatches;
    if (test_state.close_core) test_close_gate();
    if (test_state.fault) __asm__ volatile ("trap #1");
    test_state.request_length = length;
    if (length < FW_REQUEST_HEADER || length > sizeof(test_state.request) || capacity < length + 1u)
        return 0;
    for (uint32_t i = 0; i < length; ++i) test_state.request[i] = data[i];
    for (uint32_t i = 0; i < 6u; ++i) reply[i] = data[i];
    reply[6] = FW_OK;
    for (uint32_t i = 6u; i < length; ++i) reply[i + 1u] = data[i];
    return length + 1u;
}
void game_init(void) { ++test_state.game_inits; }
void motion_init(void) { ++test_state.motion_inits; }
bool game_idle(void) { return test_state.idle != 0; }
int motion_is_busy(void) { return test_state.motion_busy != 0; }
uint32_t motion_capabilities(void) { return FW_CAP_POSITION | FW_CAP_JOG; }
uint8_t game_prepare_profile(uint8_t *staged, const uint8_t *active) {
    ++test_state.prepared;
    test_state.last_profile = (uint32_t)(uintptr_t)staged;
    test_state.last_active = (uint32_t)(uintptr_t)active;
    return test_state.prepare_result;
}
void game_finish_profile_change(uint8_t status) {
    ++test_state.finished;
    test_state.finish_status = status;
}
uint8_t game_apply_profile(const uint8_t *p, bool restore, bool boot) {
    test_state.last_profile = (uint32_t)(uintptr_t)p;
    test_state.restore = restore;
    test_state.boot = boot;
    ++test_state.applies;
    return FW_OK;
}
uint8_t game_submit(uint8_t op, const uint8_t *p, uint32_t n, uint64_t id) {
    (void)op; (void)p; (void)n; (void)id;
    return FW_ACCEPTED;
}
uint8_t motion_submit(uint8_t op, const uint8_t *p, uint32_t n, uint64_t id) {
    (void)op; (void)p; (void)n; (void)id;
    return FW_ACCEPTED;
}
uint8_t game_current_status(uint64_t id) { (void)id; return FW_OK; }
uint8_t motion_current_status(uint64_t id) { (void)id; return FW_BUSY; }
"""
PROBES = r"""
void test_close_gate(void) { platform_state.closed = 1; }
uint32_t test_closed_address(void) { return (uint32_t)(uintptr_t)&platform_state.closed; }
uint32_t test_active_address(void) { return (uint32_t)(uintptr_t)&platform_state.active; }
"""


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    symbols = {
        "ext_preserve_sram",
        "ext_reset_runtime",
        "ext_boot",
        "ext_restore_before_queues",
        "ext_reload",
        "ext_clear_begin",
        "ext_clear_complete",
        "ext_report_1f",
        "ext_report_53",
        "platform_sas_handler",
        "platform_reconfigure",
    }
    patches = [
        p
        for p in production_patches()
        if p.get("symbol") in symbols
        or p["name"].startswith("calibration-")
        or p["name"] in {"private-E0-variable-length", "reset-runtime-padding"}
    ]
    return build_variant(
        tmp_path_factory,
        "platform",
        sources=("platform.h", "platform_hooks.S", "fw.h", "game.h", "motion.h"),
        files={
            "platform.c": (SOURCE / "platform.c").read_text() + PROBES,
            "stubs.c": STUBS,
        },
        link=production_link(test_state=0x435000),
        patches=patches,
    )


class Platform:
    def __init__(self, compiled):
        self.compiled = compiled
        self.cpu = Cpu(compiled.image, PERSONALITY)
        self.cpu.map_io(PORT_PAGE)
        self.call("test_reset")
        self.closed = self.call("test_closed_address")
        self.active = self.call("test_active_address")
        self.mailbox = compiled.symbols["sas_drain_mailbox"]
        self.cpu.write_u8(HOST_PORT, 1)
        self.cpu.write_u8(HOST_ADDRESS, 1)

    def call(self, name, *args):
        return self.cpu.call(self.compiled.symbols[name], *args)

    def enter_until(self, name, until, args=(), *, registers=None, sr=ENTRY_SR):
        sp = self.cpu.enter(*args, sr=sr, registers=registers)
        self.cpu.run(self.compiled.symbols[name], until)
        return sp

    @property
    def sas_entry(self):
        patch = next(
            p
            for p in self.compiled.manifest["patches"]
            if p["name"] == "private-E0-handler"
        )
        entry = self.cpu.read_u32(patch["offset"])
        assert entry == self.compiled.symbols["platform_sas_handler"]
        return entry

    def sent_frame(self):
        self.enter_until("platform_sas_handler", NATIVE_TRANSMIT)
        return transmitted(self.cpu)


def test_e0_enters_private_stack_before_core(compiled):
    machine = Platform(compiled)
    receive(machine.cpu, e0_poll(INFO))
    native_sp = machine.enter_until(
        "platform_sas_handler", compiled.symbols["fw_process"]
    )
    private_sp = machine.cpu.uc.reg_read(UC_M68K_REG_A7)
    assert private_sp < compiled.symbols["commission_stack_end"]
    assert compiled.symbols["sas_stack_start"] <= private_sp
    assert machine.cpu.read_u32(compiled.symbols["sas_saved_sp"]) == native_sp
    for offset in (4, 12):  # C's request/reply pointers also live on the private stack.
        pointer = machine.cpu.read_u32(private_sp + offset)
        assert (
            compiled.symbols["sas_stack_start"]
            <= pointer
            < compiled.symbols["sas_stack_end"]
        )


def native_return(machine, value=0):
    cpu = machine.cpu
    sp = cpu.uc.reg_read(UC_M68K_REG_A7)
    cpu.uc.reg_write(UC_M68K_REG_PC, cpu.read_u32(sp))
    cpu.uc.reg_write(UC_M68K_REG_A7, sp + 4)
    cpu.uc.reg_write(UC_M68K_REG_D0, value)


def uart_returns(machine):
    """Return only from the UART/RTOS leaf; CRC and native sender remain real."""
    sent = []

    def complete(uc, address, size, data):
        sent.append(transmitted(machine.cpu)[1])
        native_return(machine)

    machine.cpu.uc.hook_add(UC_HOOK_CODE, complete, begin=NATIVE_TRANSMIT, end=NATIVE_TRANSMIT)
    return sent


def test_reconfiguration_refuses_core_entry_before_deleting_native_tasks(compiled):
    machine = Platform(compiled)
    receive(machine.cpu, e0_poll(INFO))
    # Pause task 7 at the native deletion entry, then run a separate SAS caller.
    machine.cpu.enter(stack_top=STACK_TOP - 0x1000 + 4)
    machine.cpu.run(RECONFIGURE, DELETE_PARENTS)
    _, sent = machine.sent_frame()
    assert machine.call("test_dispatches") == 0
    assert sent == bytes((0x81,))


RECONFIGURE_CALLEES = (DELETE_PARENTS, INIT_PORTS, CREATE_PARENTS)
QUIESCED = platform_constant("SAS_QUIESCED")


def core_packet(machine, valid=True):
    receive(machine.cpu, e0_poll(INFO if valid else b""))


def reconfiguration_callees(machine, after=None):
    """Unit boundary at the three original functions; real-kernel tests run them."""
    called = []

    def return_call(uc, address, size, data):
        assert machine.cpu.read_u8(machine.closed) == 1
        assert machine.cpu.read_u8(machine.active) == 0
        assert machine.cpu.read_u32(machine.mailbox) == 0
        called.append(address)
        if after:
            after(address)
        native_return(machine, 0xDEADBEEF)

    for address in RECONFIGURE_CALLEES:
        machine.cpu.uc.hook_add(UC_HOOK_CODE, return_call, begin=address, end=address)
    return called


def test_boot_discards_only_retained_adapter_gate_state(compiled):
    machine = Platform(compiled)
    machine.cpu.write_u8(machine.closed, 1)
    machine.cpu.write_u8(machine.active, 1)
    machine.cpu.write_u32(machine.mailbox, QUIESCED)
    saved_sp = compiled.symbols["sas_saved_sp"]
    machine.cpu.write_u32(saved_sp, 0xDEADBEEF)
    machine.call("platform_boot")
    assert machine.cpu.read_u8(machine.closed) == 0
    assert machine.cpu.read_u8(machine.active) == 0
    assert machine.cpu.read_u32(machine.mailbox) == 0
    assert (
        machine.cpu.read_u32(saved_sp) == 0xDEADBEEF
    )  # overwritten on entry, not a busy flag


@pytest.mark.parametrize("must_wait", [False, True])
@pytest.mark.parametrize("sr", [0x2000, 0x2500])
def test_reconfiguration_drains_then_calls_original_order_and_reopens(
    compiled, must_wait, sr
):
    machine = Platform(compiled)
    machine.cpu.write_u8(machine.active, must_wait)
    called = reconfiguration_callees(machine)
    pending = []

    def consume(uc, address, size, data):
        sp = uc.reg_read(UC_M68K_REG_A7)
        mailbox, timeout, error = (machine.cpu.read_u32(sp + n) for n in (4, 8, 12))
        assert (mailbox, timeout) == (machine.mailbox, 0)
        assert machine.cpu.read_u16(error) == 0xFFFF
        pending.append(mailbox)
        # Unit port completion: the notifier retired ownership before posting.
        machine.cpu.write_u8(machine.active, 0)
        machine.cpu.write_u16(error, 0)
        native_return(machine, QUIESCED)

    machine.cpu.uc.hook_add(UC_HOOK_CODE, consume, begin=PEND, end=PEND)
    registers = marked_registers()
    sp = STACK_TOP - 64
    machine.cpu.write(sp, bytes(range(64)))
    machine.cpu.set_state(sp, sr=sr, registers=registers)
    if must_wait:
        machine.cpu.run(RECONFIGURE, PEND)
        assert machine.cpu.read_sr() & 0x0700 == sr & 0x0700  # restored before blocking
        assert machine.cpu.read_u8(machine.closed) == 1
        machine.cpu.run(PEND, RECONFIGURED)
    else:
        machine.cpu.run(RECONFIGURE, RECONFIGURED)
    assert pending == ([machine.mailbox] if must_wait else [])
    assert called == list(RECONFIGURE_CALLEES)
    assert machine.cpu.uc.reg_read(UC_M68K_REG_A7) == sp
    assert machine.cpu.read(sp, 64) == bytes(range(64))
    assert machine.cpu.read_sr() & 0x0700 == sr & 0x0700
    scratch = (UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_A0, UC_M68K_REG_A1)
    preserved = {r: v for r, v in registers.items() if r not in scratch}
    assert machine.cpu.read_registers(preserved) == preserved
    assert (
        machine.cpu.read_u8(machine.closed) == machine.cpu.read_u8(machine.active) == 0
    )
    assert machine.cpu.read_u32(machine.mailbox) == 0
    core_packet(machine)
    machine.sent_frame()
    assert machine.call("test_dispatches") == 1


@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("error", [0, 5, 0x0100])
def test_every_core_return_retires_ownership_and_posts_before_transmitting(
    compiled, valid, error
):
    machine = Platform(compiled)
    machine.call("test_close_during_core", 1)
    core_packet(machine, valid)
    events = []
    post_pc = []

    def core_entry(uc, address, size, data):
        assert machine.cpu.read_u8(machine.active) == 1
        events.append("core")

    def post(uc, address, size, data):
        sp = uc.reg_read(UC_M68K_REG_A7)
        mailbox, token, status = (machine.cpu.read_u32(sp + n) for n in (4, 8, 12))
        assert (mailbox, token) == (machine.mailbox, QUIESCED)
        assert machine.cpu.read_u8(machine.closed) == 1
        assert machine.cpu.read_u8(machine.active) == 0
        assert machine.cpu.read_u32(mailbox) == 0
        assert machine.cpu.read_u16(status) == 0xFFFF
        events.append("post")
        post_pc.append(machine.cpu.read_u32(sp))
        machine.cpu.write_u32(mailbox, token)
        machine.cpu.write_u16(status, error)
        native_return(machine, status)  # SC8 returns the error pointer, NOT status

    machine.cpu.uc.hook_add(
        UC_HOOK_CODE,
        core_entry,
        begin=compiled.symbols["fw_process"],
        end=compiled.symbols["fw_process"],
    )
    machine.cpu.uc.hook_add(UC_HOOK_CODE, post, begin=POST, end=POST)
    # No shared adapter-state cleanup is permitted after the potentially deleting post.
    late_writes = []
    machine.cpu.uc.hook_add(
        UC_HOOK_MEM_WRITE,
        lambda uc, kind, address, size, value, data: (
            late_writes.append((address, size)) if post_pc else None
        ),
        begin=compiled.symbols["platform_state"],
        end=machine.active,
    )
    if error:
        machine.enter_until(
            "platform_sas_handler", compiled.symbols["platform_sas_process"]
        )
        run_to_platform_fatal(
            machine.cpu, compiled, compiled.symbols["platform_sas_process"], NATIVE_TRANSMIT
        )
    else:
        machine.sent_frame()
        events.append("uart")
    assert events == (["core", "post"] if error else ["core", "post", "uart"])
    assert late_writes == []
    assert machine.cpu.read_u8(machine.active) == 0
    assert machine.cpu.read_u8(machine.closed) == 1
    assert machine.cpu.read_u32(machine.mailbox) == QUIESCED


@pytest.mark.parametrize(
    "error,token", [(5, QUIESCED), (0x0100, QUIESCED), (0, QUIESCED ^ 0x10000)]
)
def test_bad_pend_status_or_token_never_deletes_native_tasks(compiled, error, token):
    machine = Platform(compiled)
    machine.cpu.write_u8(machine.active, 1)
    called = reconfiguration_callees(machine)

    def pend(uc, address, size, data):
        sp = uc.reg_read(UC_M68K_REG_A7)
        machine.cpu.write_u8(machine.active, 0)
        machine.cpu.write_u16(machine.cpu.read_u32(sp + 12), error)
        native_return(machine, token)

    machine.cpu.uc.hook_add(UC_HOOK_CODE, pend, begin=PEND, end=PEND)
    machine.cpu.uc.reg_write(UC_M68K_REG_A7, STACK_TOP - 64)
    run_to_platform_fatal(machine.cpu, compiled, RECONFIGURE, RECONFIGURED)
    assert called == []
    assert machine.cpu.read_u8(machine.closed) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "closed",
        "stale-token",
        "core-active",
        "core-token",
        "reopen-active",
        "reopen-token",
    ],
)
def test_unexpected_gate_state_fails_without_erasing_evidence(compiled, fault):
    machine = Platform(compiled)
    core_packet(machine)
    if fault == "closed":
        machine.cpu.write_u8(machine.closed, 1)
    if fault in ("stale-token", "core-token"):
        machine.cpu.write_u32(machine.mailbox, QUIESCED)
    if fault == "core-active":
        machine.cpu.write_u8(machine.active, 1)

    def corrupt(address):
        if address == RECONFIGURE_CALLEES[-1]:
            if fault == "reopen-active":
                machine.cpu.write_u8(machine.active, 1)
            elif fault == "reopen-token":
                machine.cpu.write_u32(machine.mailbox, QUIESCED)

    called = reconfiguration_callees(machine, corrupt)
    if fault.startswith("core-"):
        machine.enter_until(
            "platform_sas_handler", compiled.symbols["platform_sas_process"]
        )
        run_to_platform_fatal(
            machine.cpu, compiled, compiled.symbols["platform_sas_process"], NATIVE_TRANSMIT
        )
    else:
        machine.cpu.uc.reg_write(UC_M68K_REG_A7, STACK_TOP - 64)
        run_to_platform_fatal(machine.cpu, compiled, RECONFIGURE, RECONFIGURED)
    assert called == (list(RECONFIGURE_CALLEES) if fault.startswith("reopen-") else [])
    if fault in ("stale-token", "core-token", "reopen-token"):
        assert machine.cpu.read_u32(machine.mailbox) == QUIESCED
    if fault in ("core-active", "reopen-active"):
        assert machine.cpu.read_u8(machine.active) == 1
    if not fault.startswith("core-"):
        assert machine.cpu.read_u8(machine.closed) == (fault != "stale-token")
    assert machine.call("test_dispatches") == 0


def test_e0_stack_layout_reuses_only_the_retired_commissioning_stack(compiled):
    s = compiled.symbols
    assert s["fw_runtime_end"] == s["fw_runtime"] + c("RUNTIME_SIZE")
    assert s["motion_state"] == s["fw_runtime_end"]
    assert s["commission_saved_regs"] == s["game_state"]
    assert s["motion_state_end"] <= s["commission_state"] < s["commission_saved_regs"]
    assert (
        s["sas_stack_start"] == s["commission_stack_start"] > s["commission_saved_regs"]
    )
    assert s["sas_stack_end"] == s["commission_stack_end"] == s["platform_state"]
    assert s["platform_state"] < s["commission_irq_guard"]
    assert s["commission_irq_guard"] + 4 <= s["sas_saved_sp"]
    assert s["sas_saved_sp"] + 4 <= s["sas_drain_mailbox"]
    assert s["sas_drain_mailbox"] % 4 == 0
    assert s["sas_drain_mailbox"] + 4 <= s["commission_io_state"]


@pytest.mark.parametrize("sr", [0x2015, 0x251F, 0x2700])
@pytest.mark.parametrize("case", ["valid", "crc", "payload", "port"])
def test_patched_e0_returns_to_native_stack_with_callee_registers_and_irq_mask(
    compiled, sr, case
):
    machine = Platform(compiled)
    data = INFO
    if case == "payload":
        data = b""
    body = bytearray(e0_poll(data))
    if case == "crc":
        body[-1] ^= 1
    receive(machine.cpu, body)
    if case == "port":
        machine.cpu.write_u8(HOST_PORT, 0)
    sent = uart_returns(machine)
    s = compiled.symbols
    saved_sp = s["sas_saved_sp"]
    machine.cpu.write_u32(saved_sp, 0xDEADBEEF)
    guards = {
        address: machine.cpu.read(address, 16)
        for address in (s["sas_stack_start"] - 16, s["sas_stack_end"])
    }
    registers = marked_registers()
    worker = s["platform_sas_process"]
    assert machine.sas_entry == s["platform_sas_handler"]
    native_sp = machine.enter_until(
        "platform_sas_handler", worker, registers=registers, sr=sr
    )
    assert machine.cpu.uc.reg_read(UC_M68K_REG_A7) == s["sas_stack_end"] - 4
    assert machine.cpu.read_u32(saved_sp) == native_sp
    assert machine.cpu.read_registers(registers) == registers
    assert machine.cpu.read_sr() & 0x0700 == sr & 0x0700
    machine.cpu.run(worker, RETURN_ADDRESS)
    assert machine.cpu.uc.reg_read(UC_M68K_REG_A7) == native_sp + 4
    assert machine.cpu.read_u32(saved_sp) == native_sp  # no clear/ownership flag
    assert machine.cpu.read_sr() & 0x0700 == sr & 0x0700
    scratch = (UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_A0, UC_M68K_REG_A1)
    preserved = {r: v for r, v in registers.items() if r not in scratch}
    assert machine.cpu.read_registers(preserved) == preserved
    assert all(
        machine.cpu.read(address, len(value)) == value
        for address, value in guards.items()
    )
    if case == "valid":
        reply = data[:6] + bytes((c("OK"),)) + data[6:]
        assert sent == [e0_poll(reply)]
    else:
        assert sent == ([] if case == "port" else [bytes((0x81,))])
    assert machine.call("test_dispatches") == (case in ("valid", "payload"))


def test_replacement_caller_overwrites_abandoned_bridge_frames(compiled, subtests):
    """Model retirement outside core; the drain barrier now forbids mid-core deletion."""
    s = compiled.symbols
    start, end = s["platform_sas_handler"], s["ext_preserve_sram"]
    cuts = [pc for pc in decode(compiled.image[start:end], start) if start <= pc < end]
    cuts.append(s["platform_sas_process"])
    for cut in cuts:
        with subtests.test(cut=hex(cut)):
            machine = Platform(compiled)
            uart_returns(machine)
            receive(machine.cpu, e0_poll(INFO))
            native_sp = machine.enter_until("platform_sas_handler", cut)
            old_frame = machine.cpu.read(native_sp, 4)
            replacement_sp = machine.cpu.enter(stack_top=native_sp - 0x100 + 4, sr=0x2500)
            machine.cpu.run(machine.sas_entry, RETURN_ADDRESS)
            assert machine.cpu.uc.reg_read(UC_M68K_REG_A7) == replacement_sp + 4
            assert machine.cpu.read_u32(s["sas_saved_sp"]) == replacement_sp
            assert machine.cpu.read(native_sp, len(old_frame)) == old_frame
            assert machine.cpu.read_sr() & 0x0700 == 0x0500


def test_native_fatal_halt_does_not_return_through_the_stack_bridge(compiled):
    machine = Platform(compiled)
    machine.cpu.map_io(DEVICE_PAGE)  # real native fatal display at 0x34b3e
    machine.call("test_fault", 1)  # fixture core injects the actual TRAP1 opcode
    receive(machine.cpu, e0_poll(INFO))
    s = compiled.symbols
    native_sp = machine.enter_until("platform_sas_handler", s["platform_sas_process"])
    bridge_return = machine.cpu.read_u32(s["sas_stack_end"] - 4)
    with pytest.raises(CpuError) as trapped:
        machine.cpu.run(s["platform_sas_process"], RETURN_ADDRESS)
    trap = trap1_at(machine.cpu, trapped.value)
    sp = machine.cpu.uc.reg_read(UC_M68K_REG_A7)
    assert s["sas_stack_start"] <= sp < s["sas_stack_end"]
    # Only architectural exception entry is modeled; the fatal handler is real.
    visited = run_native_fatal(machine.cpu, trap)
    assert bridge_return not in visited
    assert machine.cpu.read_u32(s["sas_saved_sp"]) == native_sp
    assert machine.cpu.read_u32(native_sp) == RETURN_ADDRESS


@pytest.mark.parametrize("cold", [True, False])
def test_native_reset_clear_preserves_journals_without_using_the_stack(compiled, cold):
    machine = Platform(compiled)
    protected_start, protected_end, scratch_end = 0x430000, 0x436000, SAFERAM
    protected = bytes((0xA7,)) * (protected_end - protected_start)
    machine.cpu.write(protected_start, protected)
    machine.cpu.write(protected_end, bytes((0x5A,)) * (scratch_end - protected_end))
    start = protected_start - 1 if cold else 0x412871
    machine.cpu.write(start - 1, b"\x5a\x5a\x5a")
    # Restore the first protected byte overwritten by the boundary fixture.
    machine.cpu.write(protected_start, protected)
    machine.cpu.write_u16(CONFIG_INITIALIZED, 0 if cold else CONFIG_MAGIC)
    machine.cpu.set_state(scratch_end - 2, registers={UC_M68K_REG_A0: start, UC_M68K_REG_D1: 0})
    machine.cpu.run(0x990, 0x9CE)
    assert machine.cpu.uc.reg_read(UC_M68K_REG_A7) == scratch_end - 2
    assert machine.cpu.read(protected_start, len(protected)) == protected
    assert machine.cpu.read_u8(start - 1) == 0x5A
    assert machine.cpu.read_u8(start) == 0
    if cold:
        assert machine.cpu.uc.reg_read(UC_M68K_REG_A0) == scratch_end
        assert machine.cpu.read(protected_end, scratch_end - protected_end) == bytes(
            scratch_end - protected_end
        )
    else:
        assert machine.cpu.uc.reg_read(UC_M68K_REG_A0) == start + 1
        assert machine.cpu.read_u8(start + 1) == 0x5A


def test_reset_invalidates_retained_runtime_before_any_native_reload(compiled):
    machine = Platform(compiled)
    runtime = compiled.symbols["platform_state"]
    machine.cpu.write(runtime, b"\x01\x01\x33")
    persistent = bytes((0xA7,)) * c("PERSIST_SIZE")
    machine.cpu.write(c("PROFILE_BASE"), persistent)
    registers = marked_registers()
    sp = STACK_TOP - 2
    sr = 0x2515
    machine.cpu.set_state(sp, sr=sr, registers=registers)
    machine.cpu.run(0x9D8, 0x9E2)
    assert machine.cpu.read_u8(runtime) == 0
    assert machine.cpu.read_u32(0x412872) == 0xA5A5A5A5
    assert machine.cpu.uc.reg_read(UC_M68K_REG_A7) == sp
    assert machine.cpu.read_registers(registers) == registers
    assert machine.cpu.read_sr() == (sr & ~0x0F) | 0x08
    assert machine.cpu.read(c("PROFILE_BASE"), len(persistent)) == persistent
    machine.call("platform_restore")
    assert machine.call("test_restores") == 0
    machine.call("platform_boot")
    machine.call("platform_restore")
    assert machine.call("test_restores") == 1


def test_boot_window_spans_startup_restores_only(compiled):
    machine = Platform(compiled)
    machine.call("platform_boot")  # 0x155c
    machine.call("platform_restore")  # optional startup load's tail (0x1730 -> 0x37dde)
    assert (machine.call("test_applies"), machine.call("test_boot")) == (1, 1)
    machine.enter_until("ext_restore_before_queues", CREATE_QUEUE)  # 0x1906
    assert (machine.call("test_applies"), machine.call("test_boot")) == (2, 1)
    machine.call("platform_restore")  # a runtime reload's tail
    assert (machine.call("test_applies"), machine.call("test_boot")) == (3, 0)
    machine.call("platform_before_clear")  # clear helpers never reopen the window
    machine.call("platform_after_clear")
    assert u8(machine.call("fw_apply_profile", c("PROFILE_BASE"), 0)) == c("OK")
    assert machine.call("test_boot") == 0
    machine.call("platform_boot")  # only a real boot does
    assert u8(machine.call("fw_apply_profile", c("PROFILE_BASE"), 0)) == c("OK")
    assert machine.call("test_boot") == 1


def test_timer2_probe_reads_only_flag_and_enable_bytes(compiled):
    machine = Platform(compiled)
    machine.cpu.map_io(DEVICE_PAGE)
    results = {}
    with machine.cpu.accesses(
        (DEVICE_PAGE, PAGE_SIZE), kind=UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE
    ) as accesses:
        for ifr, ier in ((0, 0x80), (0xC0, 0xC0), (0xA0, 0x80), (0, 0xA0)):
            machine.cpu.write_u8(VIA_IFR, ifr)
            machine.cpu.write_u8(VIA_IER, ier)
            results[ifr, ier] = u8(machine.call("platform_timer2_active"))
    assert results == {(0, 0x80): 0, (0xC0, 0xC0): 0, (0xA0, 0x80): 1, (0, 0xA0): 1}
    assert sorted({(a.kind, a.address, a.size) for a in accesses}) == [
        (UC_MEM_READ, VIA_IFR, 1),
        (UC_MEM_READ, VIA_IER, 1),
    ]


def test_native_clear_disables_runtime_reapplication_until_boot(compiled):
    machine = Platform(compiled)
    machine.call("platform_boot")
    machine.call("platform_before_clear")
    machine.call("platform_restore")
    assert machine.call("test_invalidations") == 1
    assert machine.call("test_restores") == 0


def test_geometry_faults_and_explicit_boot_initialization(compiled):
    machine = Platform(compiled)
    assert machine.call("platform_geometry_ok") == 1
    assert machine.call("platform_native_fault") == 0
    expected = sum(
        c("CAP_" + name) for name in ("PROFILE", "OUTCOME", "SPIN", "POSITION", "JOG")
    )
    assert machine.call("fw_capabilities") == expected
    machine.call("platform_boot")
    assert machine.call("test_boots") == 1
    assert machine.call("test_game_inits") == 1
    assert machine.call("test_motion_inits") == 1
    assert machine.call("test_restores") == 0
    machine.call("platform_restore")
    assert machine.call("test_restores") == machine.call("test_applies") == 1
    machine.cpu.write_u8(0x43C31C, 1)
    assert machine.call("platform_native_fault") == 1


@pytest.mark.parametrize("staged,restore", [(0, True), (DATA, False)])
def test_profile_ports_forward_admission_ownership_and_recovery(
    compiled, staged, restore
):
    machine = Platform(compiled)
    active = c("PROFILE_BASE")
    machine.call("test_prepare_result", c("UNSUPPORTED"))
    assert u8(machine.call("fw_prepare_profile_change", staged, active)) == c("UNSUPPORTED")
    assert machine.call("test_prepared") == 1
    assert (machine.call("test_last_profile"), machine.call("test_last_active")) == (
        staged,
        active,
    )
    machine.call("fw_finish_profile_change", c("BUSY"))
    assert machine.call("test_finished") == 1
    assert machine.call("test_finish_status") == c("BUSY")
    assert u8(machine.call("fw_apply_profile", active, restore)) == c("OK")
    assert machine.call("test_last_profile") == active
    assert machine.call("test_restore") == restore


def test_storage_is_bounded_word_atomic_and_does_not_touch_saferam(compiled):
    machine = Platform(compiled)
    data = bytes(range(64))
    machine.cpu.write(DATA + 1, data)
    saferam_before = machine.cpu.read(SAFERAM, SAFERAM_SIZE)
    with machine.cpu.accesses((c("PROFILE_BASE"), c("PERSIST_SIZE"))) as stores:
        assert (
            machine.call("fw_storage_write", c("PROFILE_BASE"), DATA + 1, len(data))
            == 1
        )
    assert machine.cpu.read(c("PROFILE_BASE"), len(data)) == data
    assert [(store.address, store.size) for store in stores] == [
        (c("PROFILE_BASE") + i, STORAGE_WORD) for i in range(0, len(data), STORAGE_WORD)
    ]
    before = machine.cpu.read(c("PROFILE_BASE"), c("PERSIST_SIZE"))
    for address, length in (
        (c("PROFILE_BASE") - 2, 2),
        (c("PROFILE_BASE") + 1, 2),
        (c("PROFILE_BASE"), 1),
        (0xFFFFFFFF, 2),
        (c("PROFILE_BASE") + c("PERSIST_SIZE") - 2, 4),
    ):
        assert machine.call("fw_storage_write", address, DATA + 1, length) == 0
    assert machine.cpu.read(c("PROFILE_BASE"), c("PERSIST_SIZE")) == before
    assert machine.cpu.read(SAFERAM, SAFERAM_SIZE) == saferam_before


@pytest.mark.parametrize("width,value", [(1, 0xA5), (2, 0xA55A), (4, 0x12345678)])
def test_native_protected_writers_preserve_exact_data(compiled, width, value):
    machine = Platform(compiled)
    address = 0x438100
    machine.call(f"platform_safe_u{width * 8}", address, value)
    assert machine.cpu.read(address, width) == value.to_bytes(width, "big")
    assert machine.cpu.read_u8(0xFF101C) == 1


@pytest.mark.parametrize("port", [1, 6])
def test_native_crc_and_adapter_frame_agree_with_host(compiled, port):
    machine = Platform(compiled)
    receive(machine.cpu, e0_poll(INFO), port)
    assert machine.cpu.call(0x2749A) & 0xFFFF == 1
    output_port, sent = machine.sent_frame()
    assert output_port == port - 1
    expected_data = INFO[:6] + bytes((c("OK"),)) + INFO[6:]
    assert sent == e0_poll(expected_data)
    assert machine.call("test_dispatches") == 1
    request = machine.call("test_request")
    assert machine.cpu.read(request, machine.call("test_request_length")) == INFO


@pytest.mark.parametrize("fault", ["crc", "length", "command", "oversize"])
def test_invalid_outer_frames_never_reach_core(compiled, fault):
    machine = Platform(compiled)
    frame = bytearray(bytes((1, E0_POLL, len(INFO))) + INFO)
    if fault == "length":
        frame[2] += 1
    elif fault == "command":
        frame[1] ^= 1
    body = bytearray(with_crc(frame))
    if fault == "crc":
        body[-1] ^= 1
    receive(machine.cpu, body)
    if fault == "oversize":
        machine.cpu.write_u8(RX, 255)
    _, sent = machine.sent_frame()
    assert sent == bytes((0x81,))
    assert machine.call("test_dispatches") == 0


@pytest.mark.parametrize(
    "wrapper,target,args,counter",
    [
        ("ext_restore_before_queues", CREATE_QUEUE, (1, 0x40, 0), None),
        ("ext_clear_begin", 0x35CF4, (0x41000A, 0x26, 8), "test_invalidations"),
        ("ext_clear_complete", 0x3864A, (0xFFFF, 0x41000A), "test_completions"),
    ],
)
def test_tail_hooks_preserve_native_stack_registers_and_status(
    compiled, wrapper, target, args, counter
):
    machine = Platform(compiled)
    registers = marked_registers()
    sr = 0x2515
    sp = machine.enter_until(wrapper, target, args, registers=registers, sr=sr)
    assert machine.cpu.uc.reg_read(UC_M68K_REG_A7) == sp
    assert machine.cpu.read_u32(sp) == RETURN_ADDRESS
    assert tuple(machine.cpu.read_u32(sp + 4 + i * 4) for i in range(len(args))) == args
    assert machine.cpu.read_registers(registers) == registers
    assert machine.cpu.read_sr() == sr
    if counter:
        assert machine.call(counter) == 1


@pytest.mark.parametrize(
    "operation,setting",
    [
        ("platform_boot", "test_boot_result"),
        ("platform_before_clear", "test_clear_result"),
        ("platform_after_clear", "test_complete_result"),
    ],
)
def test_failed_boot_or_clear_enters_native_fault_not_game(
    compiled, operation, setting
):
    machine = Platform(compiled)
    machine.call(setting, c("FAULT"))
    machine.enter_until(operation, 0x38A06)
    sp = machine.cpu.uc.reg_read(UC_M68K_REG_A7)
    assert machine.cpu.read_u32(sp + 4) == 0x0F
    assert machine.call("test_restores") == 0


@pytest.mark.parametrize(
    "command,wrapper,offset,size,args",
    [
        (0x1F, "ext_report_1f", 13, 22, ()),
        (0x53, "ext_report_53", 15, 24, (0,)),
    ],
)
def test_custom_profile_reporting_preserves_framing_and_marks_unknown_percentage(
    compiled, command, wrapper, offset, size, args
):
    machine = Platform(compiled)
    initial = bytearray(range(size + 1))
    initial[0], initial[1], initial[2] = size, 1, command
    machine.cpu.write(TX, initial)
    machine.call("platform_sas_identity")
    assert machine.cpu.read(TX, len(initial)) == initial
    machine.call("test_active", DATA)
    sp = machine.enter_until(wrapper, 0x278FE, args)
    actual_sp = machine.cpu.uc.reg_read(UC_M68K_REG_A7)
    assert actual_sp == sp - (4 if command == 0x1F else 0)
    assert machine.cpu.read_u32(actual_sp) == RETURN_ADDRESS
    assert machine.cpu.read_u32(actual_sp + 4) == 0
    actual = machine.cpu.read(TX, len(initial))
    assert actual[:offset] == initial[:offset]
    assert actual[offset : offset + 6] == b"CUSTOM"
    assert actual[offset + 6 : offset + 10] == b" " * 4
