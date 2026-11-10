"""CPU tests of diagnostic motion: compiled hooks/FSM inside the original ROM.

Native RAM fields are set the way the stock motor interrupt leaves them; no motor,
optical sensor, interrupt timing or physical stop is modelled or established here.
The original ROM setup routines serve as the oracle for the bounded motor setup.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.m68k_const import (
    UC_M68K_REG_A0,
    UC_M68K_REG_A1,
    UC_M68K_REG_A2,
    UC_M68K_REG_A3,
    UC_M68K_REG_A4,
    UC_M68K_REG_A5,
    UC_M68K_REG_A6,
    UC_M68K_REG_A7,
    UC_M68K_REG_D0,
    UC_M68K_REG_D1,
    UC_M68K_REG_D2,
    UC_M68K_REG_D3,
    UC_M68K_REG_D4,
    UC_M68K_REG_D5,
    UC_M68K_REG_D6,
    UC_M68K_REG_D7,
)

from firmware.tests.cpu import (
    PAGE_SIZE,
    RETURN_ADDRESS,
    STACK_TOP,
    Cpu,
    marked_registers,
)
from firmware.tests.kernel import DELAY_RETURN, DELAY_TRAP
from firmware.tests.support import (
    DEVICE_PAGE,
    PORT_PAGE,
    SOURCE,
    build_variant,
    commission_constant,
    production_link,
    u8,
)
from firmware.tests.support import fw_constant as c

# Hook contract (reported to the parent for patches.json).
POLL_SITE, POLL_ORIGINAL, POLL_RETURN = 0x5AC3C, "303900ff1014", 0x5AC42
CLEANUP_SITE, CLEANUP_ORIGINAL, CLEANUP_RETURN = 0x5AF80, "4eb90005b5fe", 0x5AF86
EPILOGUE_SITE, EPILOGUE_ORIGINAL = 0x5AF86, "4cdf007c4e75"
INITIAL_WAIT_RESULT = 0x5ABB4  # tst.l %d0; beq 0x5af86 after the first queue wait
LOOP = 0x5AC2A
NATIVE_CLEANUP = 0x5B5FE
ABSOLUTE_SETUP, ABSOLUTE_WAIT = 0x5FB78, 0x5FC96
JOG_SETUP, JOG_WAIT = 0x5F388, 0x5F436

# Native fields (notes/reel-control.md, "Geometry, state and callable ABI").
TEST_SELECTED, MODE, FULL_MODE, INTERLOCKS = 0x41001E, 0x412876, 0x438030, 0x43C31F
INPUTS, MOTOR, COUNTDOWN, ACTIVE_REELS, REEL_STOPS = (
    0xFF1014,
    0x4101F6,
    0x410212,
    0x410183,
    0x41017C,
)
SENSED, TARGET, REEL_STATE, PHASE, PHASE_ORIGIN = (
    0x410152,
    0x410160,
    0x410186,
    0x41018C,
    0x4385D6,
)
RECORDED, SELECTOR, TIMER_COMPENSATION, TICKS = 0x43E012, 0x410028, 0x43862A, 0x4127F6
MOTOR_IDLE, MOTOR_ABSOLUTE, MOTOR_JOG, MOTOR_STOP = 0x01, 0x10, 0x40, 0x80
REEL_IDLE, REEL_STOP = 0x01, 0x80
BUTTONS = 0x1800
COMPARED = ((0x410000, 0x300), (0x43E000, 0x20), (DEVICE_PAGE, PAGE_SIZE), (PORT_PAGE, PAGE_SIZE))

REELS, STOPS = c("REELS"), c("STOPS")
POSITION, JOG = c("OP_POSITION"), c("OP_JOG")
OK, ACCEPTED, BAD_REQUEST, BAD_ID = (
    c(n) for n in ("OK", "ACCEPTED", "BAD_REQUEST", "BAD_ID")
)
BUSY, FAULT, INTERRUPTED, UNSUPPORTED = (
    c(n) for n in ("BUSY", "FAULT", "INTERRUPTED", "UNSUPPORTED")
)
CAPABILITIES = c("CAP_POSITION") | c("CAP_JOG")

# enum motion_phase / motion_cause order in motion.h.
IDLE, PENDING, RUNNING, RELEASING, ABORTING, LATCHED = range(6)
(
    CAUSE_NONE,
    CAUSE_REJECTED,
    CAUSE_CANCELLED,
    CAUSE_NATIVE_FAULT,
    CAUSE_CONTEXT,
    CAUSE_EXIT,
    CAUSE_TIMEOUT,
    CAUSE_MISMATCH,
) = range(8)

PORTS_ADDRESS = 0x435000
DEPTH = PORTS_ADDRESS + 8
ARGS_ADDRESS = 0x435100
PORTS = r"""
#include "platform.h"
#include "game.h"
struct test_ports { uint32_t geometry, game_busy, depth; };
extern struct test_ports test_ports;
uint16_t platform_irq_save(void)
{
    uint16_t status;
    __asm__ volatile ("move.w %%sr,%0\n\tmove.w #0x2700,%%sr"
                      : "=d" (status) : : "cc", "memory");
    ++test_ports.depth;
    return status;
}
void platform_irq_restore(uint16_t status)
{
    --test_ports.depth;
    __asm__ volatile ("move.w %0,%%sr" : : "d" (status) : "cc", "memory");
}
void platform_safe_u8(uint32_t address, uint32_t value)
{
    ((void (*)(uint32_t, uint32_t))(uintptr_t)0x400u)(address, value);
}
int platform_geometry_ok(void) { return test_ports.geometry != 0u; }
int platform_native_fault(void)
{
    return MMIO8(0x438030u) == 0x14u || MMIO8(0x43c31cu) != 0u ||
           MMIO32(0x4101eeu) != 0u || MMIO8(0x43c2e4u) != 0u ||
           MMIO8(0x41d9c1u) != 0u;
}
uint32_t platform_ticks(void) { return MMIO32(0x4127f6u); }
bool game_is_busy(void) { return test_ports.game_busy != 0u; }
"""

PRESERVED = (
    UC_M68K_REG_D0,
    UC_M68K_REG_D1,
    UC_M68K_REG_D2,
    UC_M68K_REG_D3,
    UC_M68K_REG_D4,
    UC_M68K_REG_D5,
    UC_M68K_REG_D6,
    UC_M68K_REG_D7,
    UC_M68K_REG_A0,
    UC_M68K_REG_A1,
    UC_M68K_REG_A2,
    UC_M68K_REG_A3,
    UC_M68K_REG_A4,
    UC_M68K_REG_A5,
    UC_M68K_REG_A6,
)


PATCHES = [
    {
        "name": "motion-test10-poll",
        "offset": hex(POLL_SITE),
        "expected": POLL_ORIGINAL,
        "symbol": "motion_test10_poll",
        "kind": "jsr",
    },
    {
        "name": "motion-test10-cleanup",
        "offset": hex(CLEANUP_SITE),
        "expected": CLEANUP_ORIGINAL,
        "symbol": "motion_test10_cleanup",
        "kind": "jsr",
    },
    {
        "name": "motion-test10-return",
        "offset": hex(EPILOGUE_SITE),
        "expected": EPILOGUE_ORIGINAL,
        "symbol": "motion_test10_return",
        "kind": "jmp",
    },
]


def compile_variant(tmp_path_factory, qualified):
    source = (SOURCE / "motion.c").read_text()
    return build_variant(
        tmp_path_factory,
        "motion-qualified" if qualified else "motion-production",
        sources=("fw.h", "platform.h", "game.h", "motion.h", "motion_hooks.S"),
        files={
            # The same production source, compiled with the qualification switch set.
            "motion.c": ("#define MOTION_QUALIFIED 1\n" if qualified else "") + source,
            "ports.c": PORTS,
        },
        link=production_link("motion_test10_poll", test_ports=PORTS_ADDRESS),
        patches=PATCHES,
    )


@pytest.fixture(scope="module")
def qualified(tmp_path_factory):
    return compile_variant(tmp_path_factory, True)


@pytest.fixture(scope="module")
def production(tmp_path_factory):
    return compile_variant(tmp_path_factory, False)


class Machine:
    """One compiled image with a native Test 10 state that admits a move."""

    def __init__(self, compiled, loop=True):
        self.symbols = compiled.symbols
        self.cpu = Cpu(compiled.image)
        self.cpu.map_io(DEVICE_PAGE)
        self.cpu.map_io(PORT_PAGE)
        self.call("motion_init")
        self.cpu.write_u32(PORTS_ADDRESS, 1)
        self.cpu.write_u32(PORTS_ADDRESS + 4, 0)
        for address, value in (
            (TEST_SELECTED, 0x0A),
            (MODE, 0x16),
            (FULL_MODE, 0x17),
            (INTERLOCKS, 0x01),
            (ACTIVE_REELS, REELS),
            (REEL_STOPS, STOPS),
            (SELECTOR, REELS - 1),
        ):
            self.cpu.write_u8(address, value)
        self.cpu.write_u32(MOTOR, MOTOR_IDLE)
        self.cpu.write_u16(INPUTS, 0xFFFF)
        self.cpu.write_u16(TIMER_COMPENSATION, 0x20)
        self.cpu.write(REEL_STATE, bytes([REEL_IDLE] * REELS))
        self.cpu.write(PHASE, bytes([0, 1, 3]))
        self.cpu.write(PHASE_ORIGIN, bytes([1, 2, 3]))
        self.cpu.write(SENSED, bytes([0, 5, 21]))
        self.set_ticks(0x1000)
        if loop:
            self.poll(owned=False)  # task 7 reached the Test 10 polling loop

    def call(self, name, *args):
        return self.cpu.call(self.symbols[name], *args)

    def submit(self, operation, args, ident):
        self.cpu.write(ARGS_ADDRESS, bytes(args))
        return u8(self.call("motion_submit", operation, ARGS_ADDRESS, len(args), ident >> 32, ident & 0xFFFFFFFF))

    def status(self, ident):
        return u8(self.call("motion_current_status", ident >> 32, ident & 0xFFFFFFFF))

    def phase(self):
        return u8(self.call("motion_phase"))

    def cause(self):
        return u8(self.call("motion_cause"))

    def busy(self):
        return self.call("motion_is_busy") != 0

    def recovery(self):
        return self.call("motion_recovery_required") != 0

    def ticks(self):
        return self.cpu.read_u32(TICKS)

    def set_ticks(self, value):
        self.cpu.write_u32(TICKS, value & 0xFFFFFFFF)

    def critical_sections_balanced(self):
        return self.cpu.read_u32(DEPTH) == 0

    @contextmanager
    def before_section(self, inject):
        """Within the block, run inject() once, as an interrupt arriving just before
        the first mask. Yields the list that records whether it fired."""
        fired = []

        def hook(uc, address, size, data):
            if not fired:
                fired.append(address)
                inject()

        address = self.symbols["platform_irq_save"]
        with self.cpu.hook(UC_HOOK_CODE, hook, begin=address, end=address):
            yield fired

    def guard_native_writes(self):
        """Record native motor/SafeRAM/timer writes made outside a critical section."""
        unmasked = []

        def hook(uc, access, address, size, value, data):
            if int.from_bytes(uc.mem_read(DEPTH, 4), "big") == 0:
                unmasked.append(address)

        for begin, size in COMPARED:
            self.cpu.uc.hook_add(
                UC_HOOK_MEM_WRITE, hook, begin=begin, end=begin + size - 1
            )
        return unmasked

    def _enter(self, site, sr):
        sp = STACK_TOP - 64
        self.cpu.set_state(sp, sr=sr, registers=marked_registers(PRESERVED))
        return sp

    def _check_preserved(self, sp, skip=()):
        uc = self.cpu.uc
        assert uc.reg_read(UC_M68K_REG_A7) == sp
        expected = {r: v for r, v in marked_registers(PRESERVED).items() if r not in skip}
        assert self.cpu.read_registers(expected) == expected

    def poll(self, owned, sr=0x2000):
        """Execute the patched 0x5ac3c instruction through the wrapper."""
        sp = self._enter(POLL_SITE, sr)
        self.cpu.run(POLL_SITE, LOOP if owned else POLL_RETURN, max_insns=100_000)
        self.last_sr = self.cpu.read_sr()
        self._check_preserved(sp, skip=() if owned else (UC_M68K_REG_D0,))
        assert self.last_sr & 0x0700 == sr & 0x0700
        assert self.critical_sections_balanced()

    def cleanup(self, owned):
        """Execute the patched 0x5af80 call; stop at the native callee or delay trap."""
        uc = self.cpu.uc
        sp = self._enter(CLEANUP_SITE, 0x2000)
        if not owned:
            self.cpu.run(CLEANUP_SITE, NATIVE_CLEANUP, max_insns=100_000)
            self._check_preserved(sp - 4)
            assert self.cpu.read_u32(sp - 4) == CLEANUP_RETURN
            return
        self.cpu.run(CLEANUP_SITE, DELAY_TRAP, max_insns=100_000)
        assert (uc.reg_read(UC_M68K_REG_D0), uc.reg_read(UC_M68K_REG_D1)) == (12, 2)
        # Only the kernel's trap service is skipped; it may clobber D0/D1.
        uc.reg_write(UC_M68K_REG_D0, 0xDEADBEEF)
        uc.reg_write(UC_M68K_REG_D1, 0xFEEDFACE)
        self.cpu.run(DELAY_RETURN, LOOP, max_insns=100_000)
        self._check_preserved(sp)
        assert self.critical_sections_balanced()

    def settle(self, stops=None):
        """Leave the fields the native finish/verify and idle countdown leave."""
        if stops is not None:
            targets = bytes((STOPS - stop) % STOPS for stop in stops)
            self.cpu.write(SENSED, targets)
            self.cpu.write(RECORDED, targets)
        self.cpu.write(REEL_STATE, bytes([REEL_IDLE] * REELS))
        self.cpu.write_u8(COUNTDOWN, 0)
        self.cpu.write_u32(MOTOR, MOTOR_IDLE)

    def snapshot(self):
        return [self.cpu.read(address, size) for address, size in COMPARED]


def running(compiled, operation=POSITION, args=(4, 7, 21), ident=10):
    machine = Machine(compiled)
    assert machine.submit(operation, args, ident) == ACCEPTED
    machine.poll(owned=True)
    assert machine.phase() == RUNNING
    return machine


def test_unqualified_build_is_disabled_and_preserves_native_test10(production):
    machine = Machine(production)
    assert machine.call("motion_capabilities") == 0
    assert machine.submit(POSITION, (1, 2, 3), 1) == UNSUPPORTED
    assert machine.submit(JOG, (0,), 2) == UNSUPPORTED
    before = machine.snapshot()
    machine.poll(owned=False)
    machine.cleanup(owned=False)
    assert machine.snapshot() == before
    assert not machine.busy()


def test_motion_initialization_uses_only_its_relocated_reservation(production):
    symbols = production.symbols
    cpu = Cpu(production.image)
    base, end = symbols["motion_state"], symbols["motion_state_end"]
    assert base == symbols["fw_runtime"] + c("RUNTIME_SIZE")
    assert end == symbols["commission_record_start"]
    assert end == commission_constant("RECORD_BASE")
    start, stop = symbols["fw_runtime"], symbols["sas_stack_end"]
    cpu.write(start, b"\xa5" * (stop - start))
    with cpu.written((start, stop - start)) as writes:
        cpu.call(symbols["motion_init"])
    assert writes and all(base <= address < end for address in writes)
    touched = set(writes)
    expected = bytearray(b"\xa5" * (stop - start))
    for address in touched:
        expected[address - start] = 0
    assert cpu.read(start, stop - start) == expected
    assert min(touched) == base
    assert cpu.call(symbols["motion_is_busy"]) == 0


def test_qualified_build_advertises_only_motion(qualified):
    assert Machine(qualified).call("motion_capabilities") == CAPABILITIES


@pytest.mark.parametrize("inputs", [0x0000, 0x8000, 0x1800, 0x7FFF])
@pytest.mark.parametrize("flags", [0x00, 0x1F])
def test_native_poll_reproduces_original_word_load(qualified, inputs, flags):
    machine = Machine(qualified)
    machine.cpu.write_u16(INPUTS, inputs)
    machine.poll(owned=False, sr=0x2000 | flags)
    uc = machine.cpu.uc
    preset = marked_registers(PRESERVED)[UC_M68K_REG_D0]
    assert uc.reg_read(UC_M68K_REG_D0) == (preset & 0xFFFF0000) | inputs
    expected = (
        (flags & 0x10) | (0x08 if inputs & 0x8000 else 0) | (0x04 if inputs == 0 else 0)
    )
    assert machine.last_sr & 0x1F == expected


def test_absolute_setup_matches_native_routine(qualified):
    stops = (4, 7, 0)
    native = Machine(qualified)
    native.cpu.write(ARGS_ADDRESS, bytes(stops))
    native.cpu.enter(ARGS_ADDRESS, 0)
    native.cpu.run(ABSOLUTE_SETUP, ABSOLUTE_WAIT)

    ours = Machine(qualified)
    assert ours.submit(POSITION, stops, 3) == ACCEPTED
    ours.poll(owned=True)
    assert ours.snapshot() == native.snapshot()
    assert ours.cpu.read_u32(MOTOR) == MOTOR_ABSOLUTE
    assert ours.status(3) == ACCEPTED


@pytest.mark.parametrize("reel", range(REELS))
def test_jog_setup_matches_native_routine(qualified, reel):
    native = Machine(qualified)
    native.cpu.write_u8(SELECTOR, reel)  # native callers select the interrupt reel
    native.cpu.enter(reel)
    native.cpu.run(JOG_SETUP, JOG_WAIT)

    ours = Machine(qualified)
    assert ours.submit(JOG, (reel,), 4) == ACCEPTED
    ours.poll(owned=True)
    assert ours.snapshot() == native.snapshot()
    assert ours.cpu.read_u32(MOTOR) == MOTOR_JOG


def test_absolute_move_completes_only_on_settled_verified_stops(qualified):
    stops = (4, 7, 21)
    machine = running(qualified, POSITION, stops, 11)
    machine.poll(owned=True)
    assert machine.status(11) == ACCEPTED
    machine.settle(stops)
    machine.cpu.write_u8(COUNTDOWN, 5)  # finish/verify done, idle countdown not drained
    machine.poll(owned=True)
    assert machine.status(11) == ACCEPTED
    machine.cpu.write_u8(COUNTDOWN, 0)
    machine.poll(owned=True)  # the result is recorded; one more owned pass
    assert (machine.status(11), machine.phase()) == (OK, RELEASING)
    assert machine.busy()
    machine.poll(owned=False)
    assert not machine.busy()
    assert machine.status(11) == OK


def test_jog_completes_and_restores_the_selector(qualified):
    machine = Machine(qualified)
    selector = machine.cpu.read_u8(SELECTOR)
    assert machine.submit(JOG, (0,), 12) == ACCEPTED
    machine.poll(owned=True)
    assert machine.cpu.read_u8(SELECTOR) == 0 != selector
    machine.settle()
    machine.poll(owned=True)
    assert machine.status(12) == OK
    assert machine.cpu.read_u8(SELECTOR) == selector
    machine.poll(owned=False)


def test_settled_mismatch_latches_without_stopping(qualified):
    stops = (4, 7, 21)
    machine = running(qualified, POSITION, stops, 13)
    machine.settle(((stops[0] + 1) % STOPS, *stops[1:]))
    machine.poll(owned=True)
    assert (machine.status(13), machine.phase(), machine.cause()) == (
        FAULT,
        LATCHED,
        CAUSE_MISMATCH,
    )
    assert machine.cpu.read_u32(MOTOR) == MOTOR_IDLE
    machine.poll(owned=True)
    assert machine.busy()


@pytest.mark.parametrize("start", [0x1000, 0xFFFFFF00])
def test_timeout_stops_drains_and_stays_latched(qualified, start):
    machine = Machine(qualified)
    machine.set_ticks(start)
    assert machine.submit(POSITION, (1, 2, 3), 14) == ACCEPTED
    machine.poll(owned=True)
    limit = machine.call("motion_timeout_ticks", POSITION)
    assert 0 < limit < 1 << 31
    machine.set_ticks(start + limit - 1)
    machine.poll(owned=True)
    assert (machine.status(14), machine.cpu.read_u32(MOTOR)) == (
        ACCEPTED,
        MOTOR_ABSOLUTE,
    )
    machine.set_ticks(start + limit)
    machine.poll(owned=True)
    assert (machine.status(14), machine.phase(), machine.cause()) == (
        FAULT,
        ABORTING,
        CAUSE_TIMEOUT,
    )
    assert machine.cpu.read(REEL_STATE, REELS) == bytes([REEL_STOP] * REELS)
    assert machine.cpu.read_u32(MOTOR) == MOTOR_STOP
    machine.poll(owned=True)
    assert machine.phase() == ABORTING
    machine.settle()
    machine.poll(owned=True)
    assert (machine.phase(), machine.cause(), machine.recovery()) == (
        LATCHED,
        CAUSE_TIMEOUT,
        False,
    )
    machine.set_ticks(start + 10 * limit)
    for _ in range(3):
        machine.poll(owned=True)
        machine.cleanup(owned=True)
    assert machine.busy() and machine.status(14) == FAULT
    assert machine.cpu.read_u32(MOTOR) == MOTOR_IDLE  # never written back to a stop


def test_jog_timeout_uses_its_own_bound(qualified):
    machine = running(qualified, JOG, (1,), 15)
    limit = machine.call("motion_timeout_ticks", JOG)
    assert limit < machine.call("motion_timeout_ticks", POSITION)
    machine.set_ticks(machine.ticks() + limit)
    machine.poll(owned=True)
    assert (machine.cause(), machine.cpu.read_u32(MOTOR)) == (CAUSE_TIMEOUT, MOTOR_STOP)


def test_abort_deadline_requires_recovery_and_preserves_cause(qualified):
    machine = running(qualified, POSITION, (1, 2, 3), 16)
    start = machine.ticks() + machine.call("motion_timeout_ticks", POSITION)
    machine.set_ticks(start)
    machine.poll(owned=True)
    abort = machine.call("motion_abort_ticks")
    machine.set_ticks(start + abort - 1)
    machine.poll(owned=True)
    assert machine.phase() == ABORTING
    machine.set_ticks(start + abort)
    machine.poll(owned=True)
    assert (machine.phase(), machine.cause(), machine.recovery()) == (
        LATCHED,
        CAUSE_TIMEOUT,
        True,
    )
    machine.settle()  # a later idle does not release the latch
    machine.poll(owned=True)
    assert machine.busy() and machine.recovery() and machine.status(16) == FAULT


@pytest.mark.parametrize(
    "address,size,value",
    [
        (0x4101EE, 4, 0xFFFFFFFE),
        (0x43C2E4, 1, 1),
        (0x41D9C1, 1, 1),
        (0x43C31C, 1, 1),
    ],
)
def test_native_fault_keeps_native_recovery_and_fault_fields(
    qualified, address, size, value
):
    machine = running(qualified, POSITION, (1, 2, 3), 17)
    machine.cpu.write(address, value.to_bytes(size, "big"))
    machine.poll(owned=True)
    assert (machine.status(17), machine.phase(), machine.cause()) == (
        FAULT,
        ABORTING,
        CAUSE_NATIVE_FAULT,
    )
    assert machine.cpu.read_u32(MOTOR) == MOTOR_ABSOLUTE
    assert machine.cpu.read(REEL_STATE, REELS) == bytes([MOTOR_ABSOLUTE] * REELS)
    machine.settle()
    machine.poll(owned=True)
    assert machine.phase() == LATCHED and not machine.recovery()
    assert machine.cpu.read(address, size) == value.to_bytes(size, "big")
    assert machine.busy()


def test_context_change_stops_jog_and_restores_selector_only_after_idle(qualified):
    machine = Machine(qualified)
    selector = machine.cpu.read_u8(SELECTOR)
    assert machine.submit(JOG, (1,), 18) == ACCEPTED
    machine.poll(owned=True)
    machine.cpu.write_u8(INTERLOCKS, 0x03)  # another door opened
    machine.poll(owned=True)
    assert (machine.cause(), machine.cpu.read_u32(MOTOR)) == (CAUSE_CONTEXT, MOTOR_STOP)
    assert machine.cpu.read_u8(SELECTOR) == 1
    machine.settle()
    machine.poll(owned=True)
    assert machine.phase() == LATCHED
    assert machine.cpu.read_u8(SELECTOR) == selector


def test_test10_exit_while_moving_retains_ownership(qualified):
    machine = running(qualified, JOG, (2,), 19)
    machine.cleanup(owned=True)
    assert (machine.status(19), machine.phase(), machine.cause()) == (
        FAULT,
        ABORTING,
        CAUSE_EXIT,
    )
    assert machine.cpu.read_u32(MOTOR) == MOTOR_STOP
    machine.settle()
    machine.cleanup(owned=True)
    assert machine.phase() == LATCHED
    machine.cleanup(owned=True)  # never reaches the native cleanup or payout test
    assert machine.busy()


def test_test10_exit_cancels_unstarted_request_without_motor_writes(qualified):
    machine = Machine(qualified)
    assert machine.submit(POSITION, (1, 2, 3), 20) == ACCEPTED
    before = machine.snapshot()
    machine.cleanup(owned=False)
    assert machine.snapshot() == before
    assert (machine.status(20), machine.cause(), machine.busy()) == (
        INTERRUPTED,
        CAUSE_CANCELLED,
        False,
    )


def test_completed_move_releases_to_native_cleanup(qualified):
    machine = running(qualified, JOG, (0,), 21)
    machine.settle()
    machine.poll(owned=True)
    machine.cleanup(owned=False)
    assert machine.status(21) == OK and not machine.busy()


def test_claim_rechecks_admission(qualified):
    machine = Machine(qualified)
    assert machine.submit(POSITION, (1, 2, 3), 22) == ACCEPTED
    machine.cpu.write_u16(INPUTS, 0xFFFF & ~BUTTONS)  # CHANGE and SPIN pressed
    before = machine.snapshot()
    machine.poll(owned=False)
    assert machine.snapshot() == before
    assert (machine.status(22), machine.cause(), machine.busy()) == (
        BUSY,
        CAUSE_REJECTED,
        False,
    )


REJECTIONS = [
    (TEST_SELECTED, 1, 0x00),
    (MODE, 1, 0x00),
    (FULL_MODE, 1, 0x14),
    (INTERLOCKS, 1, 0x00),
    (INTERLOCKS, 1, 0x05),
    (ACTIVE_REELS, 1, REELS + 2),
    (REEL_STOPS, 1, STOPS + 10),
    (MOTOR, 4, MOTOR_JOG),
    (COUNTDOWN, 1, 1),
    (0x410213, 1, 1),
    (0x410172, 1, 2),
    (0x410165, 1, 1),
    (REEL_STATE + 1, 1, REEL_STOP),
    (INPUTS, 2, 0xF7FF),
    (INPUTS, 2, 0xEFFF),
    (0x410371, 1, 1),
    (0x410386, 2, 1),
    (0x43CD46, 1, 1),
    (0x43CD78, 1, 1),
    (0x43CD6A, 1, 1),
    (0x43CD91, 1, 1),
    (0x410232, 1, 1),
    (0x4101EE, 4, 0xFFFFFFFE),
    (0x43C2E4, 1, 1),
    (0x41D9C1, 1, 1),
    (PORTS_ADDRESS, 4, 0),
    (PORTS_ADDRESS + 4, 4, 1),
]


@pytest.mark.parametrize("address,size,value", REJECTIONS)
def test_submit_rejects_inadmissible_machine(qualified, address, size, value):
    machine = Machine(qualified)
    machine.cpu.write(address, value.to_bytes(size, "big"))
    assert machine.submit(POSITION, (1, 2, 3), 23) == BUSY
    assert (machine.phase(), machine.status(23)) == (IDLE, BAD_ID)


@pytest.mark.parametrize(
    "operation,args",
    [
        (POSITION, (1, 2)),
        (POSITION, (1, 2, 3, 4)),
        (POSITION, (0, STOPS, 0)),
        (JOG, ()),
        (JOG, (REELS,)),
        (JOG, (0, 0)),
        (c("OP_BET_SPIN"), (1,)),
    ],
)
def test_submit_rejects_invalid_arguments(qualified, operation, args):
    machine = Machine(qualified)
    assert machine.submit(operation, args, 24) == BAD_REQUEST
    assert not machine.busy()


def test_duplicate_never_restarts_and_second_request_is_busy(qualified):
    machine = Machine(qualified)
    assert machine.submit(POSITION, (1, 2, 3), 25) == ACCEPTED
    assert machine.submit(POSITION, (1, 2, 3), 25) == ACCEPTED
    machine.poll(owned=True)
    machine.cpu.write(TARGET, bytes(REELS))  # a restart would rewrite the targets
    assert machine.submit(POSITION, (1, 2, 3), 25) == ACCEPTED
    assert machine.submit(JOG, (0,), 26) == BUSY
    machine.poll(owned=True)
    assert machine.cpu.read(TARGET, REELS) == bytes(REELS)
    assert (machine.status(26), machine.status(0)) == (BAD_ID, BAD_ID)


def test_reboot_discards_motion_without_touching_native_state(qualified):
    machine = running(qualified, POSITION, (1, 2, 3), 27)
    machine.set_ticks(machine.ticks() + machine.call("motion_timeout_ticks", POSITION))
    machine.poll(owned=True)
    assert machine.phase() == ABORTING
    before = machine.snapshot()
    machine.call("motion_init")
    assert machine.snapshot() == before
    assert (machine.busy(), machine.status(27)) == (False, BAD_ID)
    machine.poll(owned=False)
    assert machine.snapshot() == before


def test_requests_wait_for_the_test10_polling_loop(qualified):
    machine = Machine(qualified, loop=False)  # 0x41001e = 0x0a, loop not reached
    assert machine.submit(POSITION, (1, 2, 3), 30) == BUSY
    machine.poll(owned=False)
    assert machine.submit(POSITION, (1, 2, 3), 30) == ACCEPTED


def test_unowned_cleanup_ends_availability(qualified):
    machine = Machine(qualified)
    machine.cleanup(owned=False)
    assert machine.submit(JOG, (0,), 31) == BUSY


@pytest.mark.parametrize(
    "entry,flags",
    [
        (INITIAL_WAIT_RESULT, 0x00),
        (EPILOGUE_SITE, 0x00),
        (EPILOGUE_SITE, 0x1F),
    ],
)
def test_every_test10_return_cancels_pending_and_ends_availability(
    qualified, entry, flags
):
    machine = Machine(qualified)
    assert machine.submit(POSITION, (1, 2, 3), 32) == ACCEPTED
    before = machine.snapshot()
    uc = machine.cpu.uc
    saved = [0x11110000 + n for n in range(5)]  # D2-D6 pushed at 0x5ab76
    sp = STACK_TOP - 24
    machine.cpu.write(
        sp,
        b"".join(v.to_bytes(4, "big") for v in saved)
        + RETURN_ADDRESS.to_bytes(4, "big"),
    )
    machine._enter(entry, 0x2000 | flags)
    uc.reg_write(UC_M68K_REG_A7, sp)
    if entry == INITIAL_WAIT_RESULT:
        uc.reg_write(UC_M68K_REG_D0, 0)  # the first 0x364f2 wait saw a queue change
    d0 = uc.reg_read(UC_M68K_REG_D0)
    machine.cpu.run(entry, RETURN_ADDRESS, max_insns=100_000)
    # The epilogue leaves the CCR it was entered with: tst.l %d0 (Z, X kept) or flags.
    expected = (flags & 0x10) | 0x04 if entry == INITIAL_WAIT_RESULT else flags
    assert machine.cpu.read_sr() & 0x1F == expected
    assert uc.reg_read(UC_M68K_REG_A7) == sp + 24
    registers = (
        UC_M68K_REG_D2,
        UC_M68K_REG_D3,
        UC_M68K_REG_D4,
        UC_M68K_REG_D5,
        UC_M68K_REG_D6,
    )
    assert list(machine.cpu.read_registers(registers).values()) == saved
    assert uc.reg_read(UC_M68K_REG_D0) == d0
    machine._check_preserved(sp + 24, skip=(UC_M68K_REG_D0, *registers))
    assert machine.critical_sections_balanced()
    assert machine.snapshot() == before
    assert (machine.status(32), machine.cause(), machine.busy()) == (
        INTERRUPTED,
        CAUSE_CANCELLED,
        False,
    )
    assert machine.submit(POSITION, (1, 2, 3), 33) == BUSY


def native_recovery_absolute(machine):
    machine.cpu.write_u32(0x4101EE, 0xFFFFFFFE)  # 0x5ff76 records the error first
    machine.cpu.write(REEL_STATE, bytes([MOTOR_ABSOLUTE] * REELS))
    machine.cpu.write_u32(MOTOR, MOTOR_ABSOLUTE)  # then publishes recovery (0x60082)


def native_recovery_during_jog(machine):
    machine.cpu.write_u32(0x4101EE, 0xFFFFFFFA)
    machine.cpu.write(REEL_STATE, bytes([MOTOR_ABSOLUTE] * REELS))
    machine.cpu.write_u32(MOTOR, MOTOR_ABSOLUTE)


@pytest.mark.parametrize(
    "operation,args,inject",
    [
        (POSITION, (1, 2, 3), native_recovery_absolute),
        (JOG, (1,), native_recovery_during_jog),
    ],
)
def test_failure_never_stops_native_recovery_published_before_the_mask(
    qualified, operation, args, inject
):
    machine = running(qualified, operation, args, 34)
    machine.set_ticks(machine.ticks() + machine.call("motion_timeout_ticks", operation))
    with machine.before_section(lambda: inject(machine)) as fired:
        machine.poll(owned=True)
    assert fired
    assert (machine.status(34), machine.phase(), machine.cause()) == (
        FAULT,
        ABORTING,
        CAUSE_NATIVE_FAULT,
    )
    assert machine.cpu.read_u32(MOTOR) == MOTOR_ABSOLUTE
    assert machine.cpu.read(REEL_STATE, REELS) == bytes([MOTOR_ABSOLUTE] * REELS)
    assert machine.cpu.read_u32(0x4101EE) != 0


def test_jog_failure_does_not_stop_a_native_dwell_it_did_not_publish(qualified):
    machine = running(qualified, JOG, (1,), 35)
    machine.set_ticks(machine.ticks() + machine.call("motion_timeout_ticks", JOG))
    with machine.before_section(lambda: machine.cpu.write_u32(MOTOR, 0x800)) as fired:
        machine.poll(owned=True)
    assert fired and machine.cause() == CAUSE_TIMEOUT
    assert machine.cpu.read_u32(MOTOR) == 0x800
    assert machine.cpu.read(REEL_STATE, REELS) == bytes([REEL_IDLE] * REELS)


@pytest.mark.parametrize(
    "address,size,value",
    [
        (0x4101EE, 4, 0xFFFFFFFE),
        (MOTOR, 4, MOTOR_JOG),
        (INPUTS, 2, 0xEFFF),
        (MODE, 1, 0),
    ],
)
def test_claim_admission_and_setup_are_one_atomic_section(
    qualified, address, size, value
):
    machine = Machine(qualified)
    assert machine.submit(POSITION, (1, 2, 3), 36) == ACCEPTED
    expected = []

    def inject():
        machine.cpu.write(address, value.to_bytes(size, "big"))
        expected.append(machine.snapshot())

    with machine.before_section(inject) as fired:
        machine.poll(owned=False)
    assert fired
    assert machine.snapshot() == expected[0]  # no SafeRAM, RAM or timer write
    assert (machine.status(36), machine.cause(), machine.busy()) == (
        BUSY,
        CAUSE_REJECTED,
        False,
    )


def test_submit_admission_is_atomic_with_publication(qualified):
    machine = Machine(qualified)
    with machine.before_section(lambda: machine.cpu.write_u8(0x43C2E4, 1)) as fired:
        assert machine.submit(JOG, (0,), 37) == BUSY
    assert fired and not machine.busy()


@pytest.mark.parametrize("operation,args", [(POSITION, (4, 7, 0)), (JOG, (2,))])
def test_native_state_writes_happen_only_inside_critical_sections(
    qualified, operation, args
):
    machine = Machine(qualified)
    unmasked = machine.guard_native_writes()
    assert machine.submit(operation, args, 38) == ACCEPTED
    machine.poll(owned=True)  # setup
    machine.set_ticks(machine.ticks() + machine.call("motion_timeout_ticks", operation))
    machine.poll(owned=True)  # stop request
    machine.settle()
    machine.poll(owned=True)  # drain and selector restore
    assert machine.phase() == LATCHED
    assert unmasked == []
