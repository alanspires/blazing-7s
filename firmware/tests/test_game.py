"""Compiled 68000 tests of native game integration on the patched original ROM.

The fixture links production game/platform/core sources and applies the
"game-" and "private-E0-" patches of patches.json to the hash-checked original
image. Only native leaves that need the RTOS (trap #0) or physical inputs are
replaced by fixture patches with deterministic fakes: counter read/compare, the
one-credit BET ONE primitive, the task-6 event reader, accounting actions,
queue posts, and the VRTX scheduler lock, unlock and delay calls (which the CPU
harness cannot execute). Everything else executed here is original ROM code or
production extension code. None of this establishes physical machine behavior.
"""

from __future__ import annotations

from contextlib import ExitStack

import pytest
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.m68k_const import (
    UC_M68K_REG_A0,
    UC_M68K_REG_A1,
    UC_M68K_REG_A2,
    UC_M68K_REG_A6,
    UC_M68K_REG_A7,
    UC_M68K_REG_D0,
    UC_M68K_REG_D1,
    UC_M68K_REG_D2,
    UC_M68K_REG_D3,
    UC_M68K_REG_PC,
    UC_M68K_REG_SR,
)

from firmware.tests.cpu import PAGE_SIZE, RETURN_ADDRESS, STACK_BASE, STACK_TOP, Cpu
from firmware.tests.support import (
    DEVICE_PAGE,
    NORMAL_START,
    PERSONALITY,
    PORT_PAGE,
    SAFERAM,
    SAFERAM_SIZE,
    VIA_IER,
    VIA_IFR,
    build_variant,
    production_link,
    production_patches,
    u8,
)
from firmware.tests.support import fw_constant as c

SOURCES = (
    "fw.h",
    "platform.h",
    "platform.c",
    "platform_hooks.S",
    "core.c",
    "motion.h",
    "game.h",
    "game.c",
    "game_hooks.S",
)

FAKE_LEAVES = (
    ("fake-counter-read", 0x549C8, "48e73000262f", "test_counter_read"),
    ("fake-counter-compare", 0x548BC, "4e56000048e7", "test_counter_compare"),
    ("fake-bet-credit", 0x4341C, "487800434eb9", "test_bet_credit"),
    ("fake-event-reader", 0x0AE6A, "4e56fffc42a7", "test_event_read"),
    ("fake-scheduler-lock", 0x03D82, "70204e404e75", "test_lock"),
    ("fake-scheduler-unlock", 0x03D88, "70214e404e75", "test_unlock"),
    ("fake-delay", 0x03A1E, "222f0004700c", "test_delay"),
    ("fake-accounting-action", 0x53DD0, "4e5600002f02", "test_action"),
    ("fake-queue-post", 0x03B84, "4e56fffc48e7", "test_queue_post"),
)

TEST_ENV = 0x435000
FIELDS = ("wager", "credits", "repeat", "bets", "locks", "unlocks", "depth", "delays",
          "event_count", "event", "motion_busy", "prepare_in_read", "prepare_status",
          "first_wagers", "wager_at_first", "interleave", "interleaved")
STUBS = r"""
#include "fw.h"
#include "game.h"
#include "motion.h"
struct test_env {
    uint32_t wager, credits, repeat, bets, locks, unlocks, depth, delays;
    uint32_t event_count, event, motion_busy, prepare_in_read, prepare_status;
    uint32_t first_wagers, wager_at_first, interleave, interleaved;
};
extern struct test_env test_env;
void motion_init(void) {}
int motion_is_busy(void) { return (int)test_env.motion_busy; }
uint32_t motion_capabilities(void) { return 0; }
uint8_t motion_submit(uint8_t op, const uint8_t *args, uint32_t length, uint64_t id) {
    (void)op; (void)args; (void)length; (void)id;
    return FW_UNSUPPORTED;
}
uint8_t motion_current_status(uint64_t id) { (void)id; return FW_BAD_ID; }
uint32_t test_counter_read(uint32_t id) {
    if (test_env.interleave != 0u) {  /* task 6 runs while the SAS task reads */
        test_env.interleave = 0;
        test_env.interleaved = game_next_event() | 0x100u;
    }
    return id == 0x43u ? test_env.wager : id == 0x47u ? test_env.credits :
           id == 0xc9u ? test_env.repeat : 0u;
}
int32_t test_counter_compare(uint32_t id, uint32_t n) {
    uint32_t value = test_counter_read(id);
    return value < n ? -1 : value > n;
}
void test_bet_credit(void) { ++test_env.bets; ++test_env.wager; --test_env.credits; }
uint32_t test_event_read(void) {
    uint32_t count = test_env.event_count;
    if (test_env.prepare_in_read != 0u) {
        test_env.prepare_status = game_prepare_profile(
            (uint8_t *)(uintptr_t)test_env.prepare_in_read, 0);
        test_env.prepare_in_read = 0;
    }
    if (count != 0u)
        *(volatile uint32_t *)0x4105dcu = test_env.event;
    test_env.event_count = 0;
    return count;
}
void test_lock(void) { ++test_env.locks; ++test_env.depth; }
void test_unlock(void) { ++test_env.unlocks; if (test_env.depth != 0u) --test_env.depth; }
void test_delay(uint32_t ticks) { test_env.delays += ticks; }
void test_action(uint32_t id, uint32_t value) {
    (void)value;
    if ((id & 0xffffu) == 0x46u) {  /* issued by first-wager initialization 0x420f8 */
        ++test_env.first_wagers;
        test_env.wager_at_first = test_env.wager;
    }
}
void test_queue_post(uint32_t queue, uint32_t event, uint16_t *status) {
    (void)queue; (void)event; (void)status;
}
"""

# Native addresses (notes/remote-game-config.md, "Native implementation handoff").
MODE, INTERLOCKS, RECOVERY, EVENT = 0x438030, 0x43C31F, 0x438044, 0x4105DC
CONFIG_FAULT, MOTOR, LINE_WAGER, REJECTED = 0x43C31C, 0x4101F6, 0x4103B0, 0x4127FC
MAX_WAGER, LINES, ALL_LINES = 0x43CA1E, 0x43CA1F, 0x43CA27
WEIGHTS, TOTALS, COUNT_PTR, RECORDS_PTR = 0x43C91A, 0x43CCD2, 0x43CA92, 0x43CA96
CONFIG, CONFIG_END, OPTION_FLAGS = 0x43C87A, 0x43CCDE, 0x43DCD1
# Native scratch above the test-only extension RAM, then SafeRAM.
COMPARED, COMPARED_END = 0x436000, SAFERAM + SAFERAM_SIZE
CANDIDATES, RESIDUALS, COMMITTED = 0x4101DA, 0x4101C2, 0x43E000
TOTAL_AWARD = 0x43CD08
# 6522 VIA interrupt flag/enable registers (odd byte addresses) and timer 2's bit.
T1, T2 = 0x40, 0x20  # VIA interrupt flag and enable bits
# Native motion completion state cleared by normal retirement (5e1e2):
# reel index (byte), phase (long), pending phase mask (word).
MOTION_SENTINELS = ((0x410158, 1), (0x4101FA, 4), (0x4101A8, 2))
CACHE, CACHE_END = 0x41C74A, 0x41C7E1
ORDER_PAIRS, ORDER_IDENTITY = 0x6AC58, 0x6AC60
DISPATCH_RETURN, SPECIAL_RETURN = 0xA5E2, 0x61E14
SPIN, BET_ONE = 1, 9
SLOT = c("PROFILE_BASE")
SLOTS = (SLOT, SLOT + c("SLOT_SIZE"))
STAGED = 0x435400  # core's slot image under construction, in working RAM
METADATA = slice(c("SLOT_CONTEXT_OFFSET"), c("SLOT_BASELINE_OFFSET") + c("SLOT_BASELINE_SIZE"))
BASELINE = slice(c("SLOT_BASELINE_OFFSET"), METADATA.stop)
CCR_X, CCR_N, CCR_Z, CCR_V, CCR_C = 0x10, 0x08, 0x04, 0x02, 0x01
FLAGS = CCR_X | CCR_N | CCR_Z | CCR_V | CCR_C
SP = STACK_TOP - 0x100
FRAME = STACK_BASE + 0x8000


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    # The production game and private-E0 hooks and their padding, exactly as
    # patches.json applies them, plus jumps to the fake native leaves.
    patches = [p for p in production_patches() if p["name"].startswith(("game-", "private-E0-"))]
    patches += [{"name": name, "offset": hex(offset), "expected": expected, "symbol": symbol, "kind": "jmp"}
                for name, offset, expected, symbol in FAKE_LEAVES]
    return build_variant(tmp_path_factory, "game", sources=SOURCES, files={"test_stubs.c": STUBS},
                         link=production_link("game_init", test_env=TEST_ENV), patches=patches)


def profile(lines=1, max_bet=PERSONALITY[0xD0F8]):
    """Stock records and weights; per-line cap defaults to the stock maximum."""
    data = bytearray(b"S6PF")
    data += bytes((c("VERSION"), lines, max_bet, PERSONALITY[0xD716]))
    for stop in range(c("STOPS")):
        data += PERSONALITY[0xD840 + stop * 5:0xD840 + stop * 5 + c("REELS")]
    data += bytes(2)
    data += PERSONALITY[0xD718:0xD718 + c("PROFILE_RECORD_COUNT") * c("PROFILE_RECORD_SIZE")]
    assert len(data) == c("PROFILE_SIZE")
    for record in range(c("PROFILE_RECORD_OFFSET"), len(data), c("PROFILE_RECORD_SIZE")):
        data[record + 9] = min(data[record + 9], max_bet)  # wager range within the cap
        data[record + 10] = min(data[record + 10], data[record + 9])
    return bytes(data)


class Machine:
    ARGS = 0x435800

    def __init__(self, compiled):
        self.symbols = compiled.symbols
        self.cpu = Cpu(compiled.image, PERSONALITY)
        self.cpu.map_io(PORT_PAGE)  # SafeRAM write-enable and watchdog bytes only
        self.cpu.map_io(DEVICE_PAGE)  # VIA registers: plain memory, no 6522 model
        self.cpu.write_u8(VIA_IER, 0x80)  # IER reads bit 7 as one; all sources off
        self.cpu.call(0x3756C, 1)  # native stock configuration loader
        self.cpu.write_u32(MOTOR, 1)
        self.cpu.write_u32(0x4100B6, 0x43862E)  # game-record pointer, as boot 0x158c sets it
        assert u8(self.call("fw_boot")) == c("OK")
        self.call("game_init")
        self.env(credits=100)
        self.active = None  # core's committed slot

    def call(self, symbol, *args):
        return self.cpu.call(self.symbols[symbol], *args)

    def env(self, **values):
        for name, value in values.items():
            self.cpu.write_u32(TEST_ENV + 4 * FIELDS.index(name), value)

    def read_env(self, name):
        return self.cpu.read_u32(TEST_ENV + 4 * FIELDS.index(name))

    def prepare(self, data=None, active=None):
        """Core's prepare port: data staged in a zeroed slot image (NULL for a
        stock restoration), active the committed slot core has in effect."""
        if data is not None:
            self.cpu.write(STAGED, data + bytes(c("SLOT_SIZE") - len(data)))
        return u8(self.call("game_prepare_profile", STAGED if data is not None else 0, active or 0))

    def staged(self):
        return self.cpu.read(STAGED, c("SLOT_SIZE"))

    def apply(self, slot=None, restore=False, boot=False):
        """Core's apply port; boot is startup recovery, otherwise runtime."""
        return u8(self.call("game_apply_profile", slot or 0, int(restore), int(boot)))

    def commit(self, data):
        """Runtime COMMIT_PROFILE as core sequences it around the ports."""
        status = self.prepare(data, self.active)
        if status != c("OK"):
            return status
        slot = SLOTS[0] if self.active in (None, SLOTS[1]) else SLOTS[1]
        self.cpu.write(slot, self.staged())
        status = self.apply(slot)
        self.call("game_finish_profile_change", status)
        self.active = slot
        return status

    def restore(self):
        """Runtime RESTORE_STOCK: a no-op for core when already stock."""
        if self.active is None:
            return c("OK")
        status = self.prepare(active=self.active)
        if status != c("OK"):
            return status
        status = self.apply(self.active, restore=True)
        self.call("game_finish_profile_change", status)
        self.active = None
        return status

    def submit(self, op, args=b"", ident=1):
        self.cpu.write(self.ARGS, args or b"\0")
        return u8(self.call("game_submit", op, self.ARGS, len(args), ident >> 32, ident & 0xFFFFFFFF))

    def status(self, ident=1):
        return u8(self.call("game_current_status", ident >> 32, ident & 0xFFFFFFFF))

    def busy(self):
        return u8(self.call("game_is_busy")) != 0

    def run(self, start, until, registers=None):
        self.cpu.set_state(SP, registers=registers)
        self.cpu.run(start, until)

    def reg(self, register):
        return self.cpu.uc.reg_read(register)

    def next_event(self, native_event=0):
        """One pass of task 6's patched event read; returns (count, event)."""
        self.env(event_count=1 if native_event else 0, event=native_event)
        self.run(0xA53E, 0xA544)
        return self.reg(UC_M68K_REG_D0) & 0xFFFF, self.cpu.read_u32(EVENT)

    def bet_one(self, accepted=True):
        """Native BET ONE from the patched call. Accepted runs the native
        continuation (first-wager check, 0x420f8) up to its state change and
        returns the new state; refused returns through 0x42f9e (JSR 0x35f5a)."""
        self.cpu.write_u32(REJECTED, 0)
        self.cpu.enter(stack_top=SP + 4)
        self.cpu.run(0x42FAC, 0x43072 if accepted else RETURN_ADDRESS)
        return self.cpu.read_u32(self.reg(UC_M68K_REG_A7)) if accepted else None

    def start(self, permitted):
        """Native normal start FUN_00061172 entry: proceeds into its body with the
        native frame, or returns to the caller (a wrong path exhausts the budget)."""
        self.cpu.enter(stack_top=SP + 4, registers={UC_M68K_REG_A6: 0x1234})
        if permitted:
            self.cpu.run(NORMAL_START, 0x6117A)
            assert self.reg(UC_M68K_REG_A6) == SP - 4  # LINK A6,#-4 replayed
            assert self.reg(UC_M68K_REG_A7) == SP - 8 - 16  # local + D2-D5
            assert self.cpu.read_u32(SP - 4) == 0x1234
        else:
            self.cpu.run(NORMAL_START, RETURN_ADDRESS)
            assert self.reg(UC_M68K_REG_D0) == 0
            assert self.reg(UC_M68K_REG_A6) == 0x1234

    def copy_loop(self, candidates=(1, 2, 3), caller=DISPATCH_RETURN):
        """Native commit loop 0x615ae..0x61600 of a started normal game."""
        for reel, stop in enumerate(candidates):
            self.cpu.write_u32(CANDIDATES + 4 * reel, stop)
        self.cpu.write_u32(FRAME + 4, caller)
        self.run(0x615AE, 0x61600, {UC_M68K_REG_A6: FRAME})
        return tuple(self.cpu.read(COMMITTED, c("REELS")))

    def config_sum(self):
        return sum(self.cpu.read_u16(a) for a in range(CONFIG, CONFIG_END, 2)) & 0xFFFF


@pytest.fixture
def machine(compiled):
    return Machine(compiled)


def test_stock_loader_baseline_is_inactive_native_behavior(machine):
    assert machine.config_sum() == 0
    assert machine.apply() == c("OK")
    machine.cpu.write_u32(LINE_WAGER, 3)
    machine.run(0x65168, 0x6516E)
    assert machine.cpu.read_u32(LINE_WAGER) == 3
    machine.run(0x65184, 0x651A6)
    assert machine.reg(UC_M68K_REG_A2) == ORDER_IDENTITY
    machine.cpu.write_u8(LINES, 2)  # native even-count ordering is reproduced
    machine.run(0x3FBCC, 0x3FBEE)
    assert machine.reg(UC_M68K_REG_A2) == ORDER_PAIRS
    assert machine.bet_one() == 2  # bet placed, below the cap
    assert machine.read_env("bets") == 1
    assert (machine.read_env("first_wagers"), machine.read_env("wager_at_first")) == (1, 1)
    assert machine.cpu.read_u8(0x438628) == 1  # set by native 0x420f8


def moved_weights(data):
    """Move each reel's stop-0 weight to stop 1: same totals, new selector state."""
    data = bytearray(data)
    for reel in range(c("REELS")):
        data[11 + reel] += data[8 + reel]
        data[8 + reel] = 0
    return bytes(data)


def owned_addresses(index=0):
    """The configuration bytes a profile owns, in address order: the baseline layout."""
    pointers = [base + 8 * index + i for base in (COUNT_PTR, RECORDS_PTR) for i in range(4)]
    return sorted([MAX_WAGER, LINES, ALL_LINES, *range(WEIGHTS, WEIGHTS + 32 * 5),
                   *range(TOTALS, TOTALS + 2 * c("REELS")), *pointers])


def config(machine):
    return machine.cpu.read(CONFIG, CONFIG_END - CONFIG)


def outside_config(machine):
    """Native RAM other than the configuration block: credits, meters, accounting."""
    compared = machine.cpu.read(COMPARED, COMPARED_END - COMPARED)
    return compared[:CONFIG - COMPARED] + compared[CONFIG_END - COMPARED:]


def test_profile_activation_writes_coherent_configuration_only(machine):
    stock_cache = machine.cpu.read(CACHE, CACHE_END - CACHE)
    machine.cpu.write(CACHE, bytes(CACHE_END - CACHE))
    data = moved_weights(profile(lines=3))
    saferam = outside_config(machine)
    assert machine.commit(data) == c("OK")
    assert outside_config(machine) == saferam
    assert machine.cpu.read_u8(MAX_WAGER) == 3 * PERSONALITY[0xD0F8]
    assert machine.cpu.read_u8(LINES) == 3
    assert machine.cpu.read_u8(ALL_LINES) == 1
    assert machine.cpu.read_u32(COUNT_PTR) == SLOT + 7
    assert machine.cpu.read_u32(RECORDS_PTR) == SLOT + c("PROFILE_RECORD_OFFSET")
    for stop in range(32):
        row = machine.cpu.read(WEIGHTS + stop * 5, 5)
        expected = data[8 + stop * 3:11 + stop * 3] if stop < c("STOPS") else bytes(3)
        assert row == expected + bytes(2)
    for reel in range(c("REELS")):
        assert machine.cpu.read_u16(TOTALS + 2 * reel) == sum(data[8 + reel::3][:c("STOPS")])
        candidate = machine.cpu.read_u32(CANDIDATES + 4 * reel)  # never a zero-weight stop
        assert machine.cpu.read_u32(RESIDUALS + 4 * reel) == data[8 + candidate * 3 + reel] > 0
    assert machine.config_sum() == 0
    assert machine.cpu.read(CACHE, CACHE_END - CACHE) == stock_cache  # same records, guarded
    assert machine.read_env("depth") == 0 < machine.read_env("locks")
    assert not machine.busy()


def test_baseline_is_captured_once_inherited_and_restored_exactly(machine):
    stock = config(machine)
    original = bytes(machine.cpu.read_u8(a) for a in owned_addresses())
    assert len(original) == c("SLOT_BASELINE_SIZE")
    assert machine.commit(moved_weights(profile(lines=3))) == c("OK")
    captured = machine.cpu.read(SLOTS[0], c("SLOT_SIZE"))
    assert captured[BASELINE] == original  # first capture: the stock owned bytes
    assert machine.commit(profile(lines=5, max_bet=1)) == c("OK")
    # Inherited unchanged from the previous valid slot, never recaptured from
    # the custom configuration now live.
    assert machine.cpu.read(SLOTS[1], c("SLOT_SIZE"))[METADATA] == captured[METADATA]
    sentinel = 0x43CD7A  # loader-initialized special-game state, outside the block
    machine.cpu.write_u8(sentinel, 0x55)
    saferam = outside_config(machine)
    assert machine.restore() == c("OK")
    assert config(machine) == stock  # every owned byte and the original sum
    assert outside_config(machine) == saferam  # no native loader, no accounting writes
    assert machine.cpu.read_u8(CONFIG_FAULT) == 0
    for reel in range(c("REELS")):
        candidate = machine.cpu.read_u32(CANDIDATES + 4 * reel)
        assert machine.cpu.read_u32(RESIDUALS + 4 * reel) == machine.cpu.read_u8(
            WEIGHTS + candidate * 5 + reel) > 0
    assert machine.read_env("depth") == 0
    assert machine.apply() == c("OK")  # settled stock is consistent
    # Completed stock: the next commit captures afresh and may reuse the source slot.
    assert machine.commit(profile(lines=2)) == c("OK")
    assert machine.active == SLOTS[0]
    assert machine.cpu.read(SLOTS[0], c("SLOT_SIZE"))[METADATA] == captured[METADATA]
    assert machine.restore() == c("OK")
    assert config(machine) == stock


@pytest.mark.parametrize("change", ["paytable-index", "unowned-byte", "core-says-stock"])
def test_inheritance_refuses_a_changed_or_unrecorded_context(machine, change):
    assert machine.commit(profile(lines=3)) == c("OK")
    active = machine.active
    if change == "paytable-index":  # the selected pointers now look stock
        machine.cpu.write_u8(0x43862D, machine.cpu.read_u8(0x43862D) + 1)
    elif change == "unowned-byte":  # a native option changed, block sum kept zero
        machine.cpu.write_u16(CONFIG, machine.cpu.read_u16(CONFIG) + 1)
        machine.cpu.write_u16(CONFIG + 2, machine.cpu.read_u16(CONFIG + 2) - 1)
        assert machine.config_sum() == 0
    else:
        active = None
    before = config(machine)
    assert machine.prepare(profile(lines=5), active) == c("FAULT")
    assert machine.staged()[METADATA] == bytes(METADATA.stop - METADATA.start)
    assert not machine.busy()
    assert config(machine) == before
    if change != "core-says-stock":
        assert machine.prepare(active=active) == c("FAULT")  # nor restore from it
        assert not machine.busy()
        assert machine.apply(active, restore=True, boot=True) == c("FAULT")
        assert config(machine) == before


def test_restore_refuses_a_source_baseline_that_does_not_match(machine):
    assert machine.commit(profile(lines=3)) == c("OK")
    baseline = SLOT + c("SLOT_BASELINE_OFFSET")
    machine.cpu.write_u8(baseline, machine.cpu.read_u8(baseline) ^ 1)
    before = config(machine)
    assert machine.prepare(active=SLOT) == c("FAULT")
    assert machine.apply(SLOT, restore=True, boot=True) == c("FAULT")
    assert config(machine) == before
    assert machine.apply(restore=True, boot=True) == c("FAULT")  # a restoration needs its source


def test_boot_reapply_completes_a_torn_owned_write(machine):
    data = profile(lines=5)
    assert machine.commit(data) == c("OK")
    machine.cpu.write_u8(LINES, 1)  # power lost after this owned byte, before the sum
    assert machine.config_sum() != 0
    machine.call("game_init")
    assert machine.apply(SLOT, boot=True) == c("OK")
    assert machine.cpu.read_u8(LINES) == 5
    assert machine.config_sum() == 0
    assert machine.cpu.read_u8(CONFIG_FAULT) == 0  # nothing here sets or clears it


@pytest.mark.parametrize("change", ["native-byte", "reel-set", "fault-flag-kept"])
def test_native_context_change_faults_without_repair(machine, change):
    data = profile(lines=2)
    assert machine.commit(data) == c("OK")
    machine.call("game_init")
    if change == "native-byte":
        machine.cpu.write_u8(CONFIG, machine.cpu.read_u8(CONFIG) ^ 1)  # unrelated corruption
    elif change == "reel-set":
        machine.cpu.write_u8(0x43862C, 1)
    else:
        machine.cpu.write_u8(CONFIG, machine.cpu.read_u8(CONFIG) ^ 1)
        machine.cpu.write_u8(CONFIG_FAULT, 1)
    before = config(machine)
    flag = machine.cpu.read_u8(CONFIG_FAULT)
    assert machine.apply(SLOT, boot=True) == c("FAULT")
    assert machine.apply(SLOT, restore=True, boot=True) == c("FAULT")
    assert config(machine) == before
    assert machine.cpu.read_u8(CONFIG_FAULT) == flag
    assert machine.read_env("depth") == 0


def test_settled_stock_never_repairs_and_custom_pointers_fault(machine):
    machine.cpu.write_u16(CONFIG, machine.cpu.read_u16(CONFIG) ^ 0x0101)
    corrupted = config(machine)
    assert machine.apply(boot=True) == c("OK")  # settled stock: no repair
    assert config(machine) == corrupted
    assert machine.prepare(profile(lines=3)) == c("FAULT")  # nor capture it as a baseline
    machine.cpu.write_u16(CONFIG, machine.cpu.read_u16(CONFIG) ^ 0x0101)
    assert machine.commit(profile(lines=3)) == c("OK")
    machine.call("game_init")
    assert machine.apply(boot=True) == c("FAULT")
    assert machine.cpu.read_u32(RECORDS_PTR) == SLOT + c("PROFILE_RECORD_OFFSET")


# Native IRQ4 coin path (called by IRQ4 at 0x266c) and its coin-disable leaf.
COIN_PATH, COIN_DISABLE = 0x411CE, 0x41152
COIN_SHADOW, COIN_LATCH, COIN_ENABLE = 0x41242A, 0xFF1004, 0x80
# State the coin-edge setup writes: RE's irq4_cap_probe.py ordinary idle falling
# coin edge (previous sensor sample 0x600, current 0x400, pulse count 1, coin
# enable set in the shadow, output latch zero) with every other value the path
# reads zero, as in that probe. (address, size, value)
COIN_EDGE = (
    (0x4103A4, 4, 0x600), (0xFF1014, 2, 0x400), (0x41038C, 4, 1),
    (COIN_SHADOW, 2, COIN_ENABLE), (COIN_LATCH, 2, 0),
    (0x43E076, 4, 0), (0x410023, 1, 0), (0x43E8DE, 4, 0), (0x43F2B2, 2, 0),
    (0x410390, 4, 0), (0x412876, 1, 0), (0x41247A, 1, 0), (0x412470, 2, 0),
    (0x4103BC, 4, 0),
)


def coin_edge(machine):
    """Run the native coin path on COIN_EDGE with wager 0 (counter fake).
    Returns (shadow enable bit, native disable calls, latch write values)."""
    cpu = machine.cpu
    for address, size, value in COIN_EDGE:
        cpu.write(address, value.to_bytes(size, "big"))
    machine.env(wager=0)
    with cpu.visits(COIN_DISABLE) as disables, cpu.accesses((COIN_LATCH, 2)) as latch:
        cpu.enter(stack_top=SP + 4)
        cpu.run(COIN_PATH, RETURN_ADDRESS)
    return (bool(cpu.read_u16(COIN_SHADOW) & COIN_ENABLE), len(disables),
            tuple(store.value for store in latch))


def restore_cut_points(machine):
    """Configuration images at every power-cut point of the runtime restoration:
    before each bus store (the native long writer stores two words, IRQs masked
    between them), and at the end."""
    with machine.cpu.cut_points(lambda: config(machine), (CONFIG, CONFIG_END - CONFIG)) as images:
        assert machine.restore() == c("OK")
    return images


def test_restoration_cut_at_every_write_replays_and_keeps_a_valid_cap(machine):
    stock, stock_cap = config(machine), machine.cpu.read_u8(MAX_WAGER)
    data = moved_weights(profile(lines=1, max_bet=1))
    assert machine.commit(data) == c("OK")
    source, custom = machine.active, config(machine)
    stable = {}
    for image in (custom, stock):  # native behavior at each stable cap
        machine.cpu.write(CONFIG, image)
        stable[image[MAX_WAGER - CONFIG]] = coin_edge(machine)
    assert set(stable) == {1, stock_cap}
    assert stable[1] != stable[stock_cap]  # the comparison distinguishes the caps
    machine.cpu.write(CONFIG, custom)
    saferam = outside_config(machine)
    images = restore_cut_points(machine)
    assert outside_config(machine) == saferam  # unowned and native accounting
    assert (images[0], images[-1]) == (custom, stock)
    records = slice(RECORDS_PTR - CONFIG, RECORDS_PTR - CONFIG + 4)
    assert any(image[records] not in (custom[records], stock[records]) for image in images)
    for image in images:
        cap = image[MAX_WAGER - CONFIG]
        assert cap in stable  # old or new, never zero
        machine.cpu.write(CONFIG, image)
        assert coin_edge(machine) == stable[cap]
        machine.call("game_init")  # reboot; core replays the pending restoration
        assert machine.apply(source, restore=True, boot=True) == c("OK")
        assert config(machine) == stock
        assert machine.cpu.read_u8(CONFIG_FAULT) == 0


# Native protected writers' two-byte store instructions (byte; word; second
# word of a long) and the native line-cache builder.
BYTE_STORE, WORD_STORE, LONG_LOW_STORE = 0x41C, 0x442, 0x476
CACHE_BUILDER, CONFIG_SUM = 0x36A7A, 0x43CCDC


@pytest.mark.parametrize("store,target", [
    (BYTE_STORE, WEIGHTS),  # a weight byte
    (LONG_LOW_STORE, RECORDS_PTR + 2),  # the second word of a paytable pointer
    (WORD_STORE, CONFIG_SUM),  # the block checksum
])
def test_a_lost_protected_store_faults_before_cache_or_publication(machine, store, target):
    assert machine.commit(moved_weights(profile(lines=3))) == c("OK")
    assert machine.cpu.read_u8(WEIGHTS) == 0  # restoring changes this weight
    published = machine.cpu.read(machine.symbols["game_state"], 4)
    before = config(machine)
    lost = []

    def drop(uc, address, size, data):
        if uc.reg_read(UC_M68K_REG_A0) == target:  # the store never reaches SafeRAM
            lost.append(target)
            uc.reg_write(UC_M68K_REG_PC, address + 2)

    with (machine.cpu.hook(UC_HOOK_CODE, drop, begin=store, end=store),
          machine.cpu.visits(CACHE_BUILDER) as builds):
        status = machine.prepare(active=machine.active)
        assert status == c("OK")
        assert machine.apply(machine.active, restore=True) == c("FAULT")
    assert lost == [target]  # one store dropped, never retried
    assert builds == []  # no cache rebuild from an unverified block
    assert machine.cpu.read(machine.symbols["game_state"], 4) == published
    after = config(machine)
    for address, size in ((target, 1 if store == BYTE_STORE else 2), (CONFIG_SUM, 2)):
        kept = slice(address - CONFIG, address - CONFIG + size)
        assert after[kept] == before[kept]  # the lost store; no checksum over it
    machine.call("game_finish_profile_change", c("FAULT"))
    assert machine.busy()  # the failed change keeps play inhibited


def test_apply_rejects_unguarded_or_relocated_slots(machine):
    data = profile(lines=2)
    assert machine.prepare(data) == c("OK")
    machine.call("game_finish_profile_change", c("BUSY"))
    assert machine.apply(STAGED) == c("INVALID_PROFILE")  # working RAM, not persistent
    machine.cpu.write(SLOT, machine.staged())
    machine.cpu.write_u8(SLOT + c("PROFILE_SIZE"), 1)
    before = config(machine)
    assert machine.apply(SLOT) == c("INVALID_PROFILE")
    assert machine.apply(SLOT, restore=True) == c("INVALID_PROFILE")
    assert config(machine) == before


@pytest.mark.parametrize("fault", ["alternate-wager", "invalid", "bad-sum", "native-fault", "busy"])
def test_prepare_rejects_before_ownership_or_writes(machine, fault):
    data = profile(lines=2)
    expected = {"alternate-wager": "UNSUPPORTED", "invalid": "INVALID_PROFILE",
                "bad-sum": "FAULT", "native-fault": "FAULT", "busy": "BUSY"}[fault]
    if fault == "alternate-wager":
        machine.cpu.write_u8(OPTION_FLAGS, 0x20)
    elif fault == "invalid":
        data = data[:5] + bytes((c("MAX_LINES") + 1,)) + data[6:]
    elif fault == "bad-sum":
        machine.cpu.write_u16(CONFIG, machine.cpu.read_u16(CONFIG) ^ 0x0101)
    elif fault == "native-fault":
        machine.cpu.write_u8(CONFIG_FAULT, 1)
    else:
        machine.env(wager=1)
    before = config(machine)
    assert machine.prepare(data) == c(expected)
    assert not machine.busy()
    assert config(machine) == before
    assert machine.staged()[METADATA] == bytes(METADATA.stop - METADATA.start)


def test_profile_change_ownership_holds_task6_events_until_release(machine):
    assert machine.prepare(profile(lines=2)) == c("OK")
    assert machine.busy()
    assert machine.submit(c("OP_ARM_OUTCOME"), bytes((1, 2, 3))) == c("BUSY")
    delays = machine.read_env("delays")
    assert machine.next_event(BET_ONE)[0] == 0
    assert machine.read_env("event_count") == 1  # still queued natively
    assert machine.read_env("delays") == delays + 1  # yielded like an empty queue
    machine.call("game_finish_profile_change", c("BUSY"))  # abandoned, no side effect
    assert not machine.busy()
    assert machine.next_event(BET_ONE) == (1, BET_ONE)


def test_event_dequeued_after_ownership_is_held_and_redelivered(machine):
    machine.cpu.write(STAGED, profile(lines=2) + bytes(c("SLOT_SIZE") - c("PROFILE_SIZE")))
    # The fake reader runs prepare before returning its event: the RTOS switch
    # to the SAS task that can occur inside 0xae6a's queue traps.
    machine.env(prepare_in_read=STAGED)
    assert machine.next_event(BET_ONE)[0] == 0
    assert machine.read_env("prepare_status") == c("OK")
    assert machine.busy()
    machine.cpu.write_u32(EVENT, 0)
    machine.call("game_finish_profile_change", c("OK"))
    assert machine.next_event() == (1, BET_ONE)  # delivered once, not lost


def test_prepare_excludes_task6_before_checking_native_idle(machine):
    # A queued BET ONE and a task-6 pass that runs while prepare reads the wager.
    machine.env(event_count=1, event=BET_ONE, interleave=1)
    assert machine.prepare(profile(lines=2)) == c("OK")
    assert machine.read_env("interleaved") == 0x100  # the pass dispatched nothing
    assert machine.read_env("event_count") == 1  # BET ONE still queued natively
    assert machine.read_env("wager") == 0
    machine.call("game_finish_profile_change", c("BUSY"))
    machine.run(0xA53E, 0xA544)
    assert (machine.reg(UC_M68K_REG_D0) & 0xFFFF, machine.cpu.read_u32(EVENT)) == (1, BET_ONE)


def test_failed_change_latches_and_dispatch_in_flight_blocks_prepare(machine):
    assert machine.next_event(BET_ONE) == (1, BET_ONE)  # task 6 now dispatching it
    assert machine.prepare(profile(lines=2)) == c("BUSY")
    machine.bet_one()
    machine.env(wager=0)
    machine.next_event()  # dispatch finished; reader runs again
    assert machine.prepare(profile(lines=2)) == c("OK")
    machine.call("game_finish_profile_change", c("FAULT"))  # partial transition
    assert machine.busy()
    assert machine.next_event(SPIN)[0] == 0
    machine.call("game_init")
    assert not machine.busy()


def test_active_profile_uses_fixed_line_order_and_per_line_eligibility(machine):
    assert machine.commit(profile(lines=4)) == c("OK")
    for start, until in ((0x65184, 0x651A6), (0x3FBCC, 0x3FBEE)):
        machine.run(start, until)
        assert machine.reg(UC_M68K_REG_A2) == ORDER_IDENTITY
    machine.cpu.write_u32(LINE_WAGER, 8)
    machine.run(0x65168, 0x6516E)
    assert machine.cpu.read_u32(LINE_WAGER) == 2
    sentinels = {UC_M68K_REG_D0: 12, UC_M68K_REG_D1: 0x11223344, UC_M68K_REG_D3: 12,
                 UC_M68K_REG_A0: 0x400100, UC_M68K_REG_A1: 0x400200}
    machine.run(0x3FC34, 0x3FC3A, sentinels)
    assert machine.cpu.read_u32(LINE_WAGER) == 3
    assert {r: machine.reg(r) for r in sentinels} == sentinels


@pytest.mark.parametrize("x", [0, CCR_X])
@pytest.mark.parametrize("total", [0, 12])
def test_diagnostic_wager_wrapper_reproduces_move_flags(machine, x, total):
    assert machine.commit(profile(lines=4)) == c("OK")
    machine.run(0x3FC34, 0x3FC3A, {UC_M68K_REG_D0: total, UC_M68K_REG_D3: total,
                                   UC_M68K_REG_SR: 0x2700 | x | CCR_N | CCR_V | CCR_C})
    assert machine.cpu.read_sr() & FLAGS == x | (CCR_Z if total == 0 else 0)


@pytest.mark.parametrize("x", [0, CCR_X])
@pytest.mark.parametrize("flag", [0, 1])
def test_commit_wrapper_reproduces_tst_flags(machine, x, flag):
    machine.cpu.write_u8(0x43CD59, flag)
    machine.run(0x615FA, 0x61600, {UC_M68K_REG_SR: 0x2700 | x | CCR_N | CCR_V | CCR_C})
    assert machine.cpu.read_sr() & FLAGS == x | (0 if flag else CCR_Z)


@pytest.mark.parametrize("x", [0, CCR_X])
def test_candidate_wrapper_replaces_only_d1_low_byte(machine, x):
    machine.cpu.write_u32(CANDIDATES, 17)
    machine.cpu.write_u32(FRAME + 4, DISPATCH_RETURN)
    sentinels = {UC_M68K_REG_D0: 0, UC_M68K_REG_D1: 0xA1B2C300, UC_M68K_REG_D2: 0,
                 UC_M68K_REG_D3: 0x0BADF00D, UC_M68K_REG_A1: 0x400300, UC_M68K_REG_A6: FRAME}
    machine.run(0x615CE, 0x615D8, {**sentinels, UC_M68K_REG_SR: 0x2700 | x})
    assert machine.reg(UC_M68K_REG_D1) == 0xA1B2C311
    assert machine.reg(UC_M68K_REG_A0) == 0x4101DD
    assert {r: machine.reg(r) for r in sentinels if r != UC_M68K_REG_D1} == {
        r: v for r, v in sentinels.items() if r != UC_M68K_REG_D1}
    assert machine.cpu.read_sr() & CCR_X == x


def test_bet_one_charges_whole_lines_and_refuses_without_side_effects(machine):
    assert machine.commit(profile(lines=3)) == c("OK")
    cap = 3 * PERSONALITY[0xD0F8]
    assert machine.bet_one() == 2
    assert (machine.read_env("wager"), machine.read_env("bets")) == (3, 3)
    # Native first-wager initialization ran once, after the first credit only.
    assert (machine.read_env("first_wagers"), machine.read_env("wager_at_first")) == (1, 1)
    machine.env(wager=4, bets=0)  # a physical coin added one credit (its own init)
    assert machine.bet_one() == 2
    assert (machine.read_env("wager"), machine.read_env("bets")) == (6, 2)
    assert machine.bet_one() == 4  # the aggregate cap
    assert machine.read_env("wager") == cap
    assert machine.read_env("first_wagers") == 1
    machine.env(credits=2, bets=0, wager=0)
    unlocks = machine.read_env("unlocks")
    machine.bet_one(accepted=False)
    assert machine.read_env("bets") == 0
    assert machine.read_env("unlocks") == unlocks + 1  # releases 0x42fa6's lock
    assert machine.cpu.read_u32(REJECTED) == 1
    machine.env(credits=100, wager=cap)
    machine.bet_one(accepted=False)
    assert machine.read_env("wager") == cap


def test_single_line_profile_keeps_the_native_first_wager_path(machine):
    assert machine.commit(profile(lines=1)) == c("OK")
    assert machine.bet_one() == 2
    assert (machine.read_env("first_wagers"), machine.read_env("wager_at_first")) == (1, 1)


@pytest.mark.parametrize("profile_lines", [0, 3])
def test_normal_start_guard_honors_motion_and_the_line_invariant(machine, profile_lines):
    if profile_lines:
        assert machine.commit(profile(lines=profile_lines)) == c("OK")
    whole = max(profile_lines, 1)
    machine.cpu.write_u8(MODE, 2)
    machine.env(wager=2 * whole)
    machine.start(permitted=True)
    machine.env(motion_busy=1)  # latched diagnostic failure after Test 10 exit
    machine.start(permitted=False)
    assert machine.next_event(SPIN)[0] == 0
    machine.cpu.write_u8(MODE, 0)
    assert machine.next_event(SPIN)[0] == 0  # no repeat debit either
    machine.env(motion_busy=0)
    assert machine.next_event(SPIN) == (1, SPIN)


@pytest.mark.parametrize("case", ["indivisible", "alternate-mode", "alternate-state", "special-mode"])
def test_normal_start_guard_refuses_custom_without_whole_lines(machine, case):
    assert machine.commit(profile(lines=3)) == c("OK")
    machine.cpu.write_u8(MODE, 2)
    machine.env(wager=6)
    if case == "indivisible":
        machine.env(wager=4)
    elif case == "alternate-mode":
        machine.cpu.write_u8(MODE, 0x1A)
    elif case == "alternate-state":
        machine.cpu.write_u8(0x43C350, 2)  # set by 0x64040 before its direct start
    else:
        machine.cpu.write_u8(MODE, 6)  # special-game respin 0x61e0e
    machine.start(permitted=False)


def test_indivisible_wagers_cannot_spin_or_repeat(machine):
    assert machine.commit(profile(lines=3)) == c("OK")
    machine.cpu.write_u8(MODE, 2)
    machine.env(wager=4)
    assert machine.next_event(SPIN)[0] == 0
    machine.env(wager=6)
    assert machine.next_event(SPIN) == (1, SPIN)
    for repeat, expected in ((4, 0), (6, 6)):
        machine.env(repeat=repeat)
        machine.run(0x4324A, 0x43254)
        assert machine.reg(UC_M68K_REG_D0) == expected


@pytest.mark.parametrize("mode", [0x1A, 0x1C])
def test_alternate_spin_paths_cannot_evaluate_a_custom_profile(machine, mode):
    machine.cpu.write_u8(MODE, mode)
    machine.env(wager=3)
    assert machine.next_event(SPIN) == (1, SPIN)  # stock: native routing unchanged
    machine.cpu.write_u8(MODE, 0)
    machine.env(wager=0)
    machine.next_event()
    assert machine.commit(profile(lines=3)) == c("OK")
    machine.cpu.write_u8(MODE, mode)
    machine.env(wager=3)
    assert machine.next_event(SPIN)[0] == 0
    assert machine.next_event(12)[0] == 0


def evaluate(machine, stops, total):
    """Native normal evaluation 0x65168..0x654e6 (patched) of committed stops."""
    machine.cpu.write_u8(MODE, 6)
    machine.cpu.write_u8(0x412876, 6)  # normalized mode: the matcher credits game awards
    machine.cpu.write(COMMITTED, bytes(stops))
    machine.cpu.write_u32(LINE_WAGER, total)
    machine.run(0x65168, 0x654E6, {UC_M68K_REG_A6: FRAME})
    return machine.cpu.read_u32(TOTAL_AWARD)


def test_native_matcher_pays_the_first_matching_record_even_when_zero(machine):
    data = profile(lines=1)
    record = 8  # three single bars; the later "any bar" record also matches
    award = slice(c("PROFILE_RECORD_OFFSET") + record * c("PROFILE_RECORD_SIZE"),
                  c("PROFILE_RECORD_OFFSET") + record * c("PROFILE_RECORD_SIZE") + 4)
    single_bars = next(stop for stop in range(c("STOPS"))
                       if PERSONALITY[0xD7D2 + stop * 5:0xD7D5 + stop * 5] == data[award.start + 4:award.start + 7])
    assert machine.commit(data) == c("OK")
    assert evaluate(machine, (single_bars,) * 3, 1) == int.from_bytes(data[award], "big") > 0
    zero = data[:award.start] + bytes(4) + data[award.stop:]
    machine.cpu.write_u8(MODE, 0)
    assert machine.restore() == c("OK")
    assert machine.commit(zero) == c("OK")
    assert evaluate(machine, (single_bars,) * 3, 1) == 0


def test_native_evaluation_pays_each_enabled_line_at_the_per_line_wager(machine):
    lines = 3
    data = bytearray(profile(lines=lines))
    record = lambda index: c("PROFILE_RECORD_OFFSET") + index * c("PROFILE_RECORD_SIZE")
    single_bars, any_bar = record(8), record(9)
    data[any_bar + 12] = (1 << lines) - 1  # "any bar" now pays on every enabled line
    data = bytes(data)
    stop = next(s for s in range(c("STOPS"))
                if PERSONALITY[0xD7D2 + s * 5:0xD7D5 + s * 5] == data[single_bars + 4:single_bars + 7])
    assert machine.commit(data) == c("OK")
    award = lambda offset: int.from_bytes(data[offset:offset + 4], "big")
    # Center pays its first match (single bars); top and bottom rows hold other bars.
    assert evaluate(machine, (stop,) * 3, lines) == award(single_bars) + 2 * award(any_bar)
    assert machine.cpu.read_u32(LINE_WAGER) == 1


def test_custom_configuration_without_matching_cache_fails_closed(machine):
    assert machine.commit(profile(lines=3)) == c("OK")
    machine.call("game_init")  # reboot before the profile is restored
    machine.cpu.write_u8(MODE, 2)
    machine.env(wager=3)
    assert machine.next_event(SPIN)[0] == 0
    machine.bet_one(accepted=False)
    machine.cpu.write_u32(LINE_WAGER, 3)
    machine.run(0x65168, 0x6516E)
    assert machine.cpu.read_u32(LINE_WAGER) == 0


def play_bet_spin(machine, bet_per_line, lines, committed_candidates=(1, 2, 3)):
    """Drive the request through native-equivalent handler outcomes."""
    for _ in range(bet_per_line):
        count, event = machine.next_event()
        assert (count, event) == (1, BET_ONE)
        machine.bet_one()
        machine.cpu.write_u8(MODE, 2)
    assert machine.read_env("wager") == bet_per_line * lines
    assert machine.next_event() == (1, SPIN)
    machine.cpu.write_u8(MODE, 6)  # FUN_00061172 started the game
    return machine.copy_loop(committed_candidates)


def test_remote_bet_and_spin_completes_after_the_native_game(machine):
    assert machine.commit(profile(lines=3)) == c("OK")
    stops = bytes((5, 3, 3))
    assert machine.submit(c("OP_BET_SPIN"), bytes((2,)) + stops) == c("ACCEPTED")
    assert machine.busy()
    assert play_bet_spin(machine, 2, 3) == tuple(stops)
    assert machine.next_event() == (0, SPIN)  # reels still moving
    assert machine.status() == c("ACCEPTED")
    machine.cpu.write_u8(MODE, 0)
    machine.next_event()
    assert machine.status() == c("OK")
    assert not machine.busy()
    machine.cpu.write_u8(MODE, 6)
    assert machine.copy_loop((7, 8, 9)) == (7, 8, 9)  # consumed exactly once


def test_interference_or_refused_spin_interrupts_without_replay(machine):
    assert machine.commit(profile(lines=3)) == c("OK")
    assert machine.submit(c("OP_BET_SPIN"), bytes((2,))) == c("ACCEPTED")
    assert machine.next_event() == (1, BET_ONE)
    machine.bet_one()
    machine.cpu.write_u8(MODE, 2)
    machine.env(wager=4)  # player inserted a coin
    assert machine.next_event()[0] == 0
    assert machine.status() == c("INTERRUPTED")
    assert machine.submit(c("OP_BET_SPIN"), bytes((2,)), ident=2) == c("BUSY")  # 4 % 3
    machine.env(wager=6)
    assert machine.submit(c("OP_BET_SPIN"), bytes((2,)), ident=3) == c("ACCEPTED")
    assert machine.next_event() == (1, SPIN)
    assert machine.next_event()[0] == 0  # native start returned without committing
    assert machine.status(3) == c("INTERRUPTED")
    assert not machine.busy()


def test_armed_outcome_is_used_once_by_an_eligible_physical_spin(machine):
    assert machine.submit(c("OP_ARM_OUTCOME"), bytes((4, 4, 4))) == c("ACCEPTED")
    machine.cpu.write_u8(MODE, 6)
    machine.cpu.write_u32(EVENT, SPIN)
    sentinels = {UC_M68K_REG_D3: 0x13572468}
    machine.cpu.uc.reg_write(UC_M68K_REG_D3, sentinels[UC_M68K_REG_D3])
    assert machine.copy_loop() == (4, 4, 4)
    assert machine.reg(UC_M68K_REG_D3) == sentinels[UC_M68K_REG_D3]
    assert machine.status() == c("ACCEPTED")
    machine.cpu.write_u8(MODE, 0)
    machine.next_event()
    assert machine.status() == c("OK")
    machine.cpu.write_u8(MODE, 6)
    assert machine.copy_loop((9, 9, 9)) == (9, 9, 9)


@pytest.mark.parametrize("game", ["special-caller", "recovered", "door-open"])
def test_armed_outcome_aborts_instead_of_leaking_into_other_games(machine, game):
    assert machine.submit(c("OP_ARM_OUTCOME"), bytes((4, 4, 4))) == c("ACCEPTED")
    machine.cpu.write_u8(MODE, 6)
    machine.cpu.write_u32(EVENT, SPIN)
    machine.cpu.write(COMMITTED, bytes((7, 7, 7)))
    if game == "special-caller":
        assert machine.copy_loop(caller=SPECIAL_RETURN) == (1, 2, 3)
    elif game == "recovered":
        machine.cpu.write_u8(RECOVERY, 1)
        assert machine.copy_loop() == (7, 7, 7)  # native preservation skips the copy
    else:
        machine.cpu.write_u8(MODE, 1)
        machine.cpu.write_u8(INTERLOCKS, 1)
        machine.next_event()
    assert machine.status() == c("INTERRUPTED")
    assert not machine.busy()


def test_bet_spin_binds_an_armed_outcome_and_a_failure_holds_it(machine):
    assert machine.submit(c("OP_ARM_OUTCOME"), bytes((4, 5, 6))) == c("ACCEPTED")
    assert machine.submit(c("OP_BET_SPIN"), bytes((1, 4, 5, 7)), ident=2) == c("BAD_REQUEST")
    assert machine.submit(c("OP_BET_SPIN"), bytes((1,)), ident=3) == c("ACCEPTED")
    machine.env(credits=0)
    assert machine.next_event()[0] == 0
    assert machine.status(3) == c("INTERRUPTED")
    assert machine.busy()  # held, never silently armed for a later game
    machine.env(credits=100)
    assert machine.submit(c("OP_ARM_OUTCOME"), bytes((1, 1, 1)), ident=4) == c("BUSY")
    machine.cpu.write_u8(MODE, 6)
    machine.cpu.write_u32(EVENT, SPIN)
    assert machine.copy_loop() == (1, 2, 3)
    machine.cpu.write_u8(MODE, 0)
    assert machine.submit(c("OP_CANCEL_OUTCOME"), ident=5) == c("OK")
    assert not machine.busy()
    assert machine.submit(c("OP_CANCEL_OUTCOME"), ident=6) == c("BAD_REQUEST")


def test_bound_outcome_commits_through_remote_spin(machine):
    assert machine.submit(c("OP_ARM_OUTCOME"), bytes((4, 5, 6))) == c("ACCEPTED")
    assert machine.submit(c("OP_BET_SPIN"), bytes((1, 4, 5, 6)), ident=2) == c("ACCEPTED")
    assert play_bet_spin(machine, 1, 1) == (4, 5, 6)
    machine.cpu.write_u8(MODE, 0)
    machine.next_event()
    assert machine.status(2) == c("OK")


def test_reboot_discards_requests_and_faults_stop_tracking(machine):
    assert machine.submit(c("OP_ARM_OUTCOME"), bytes((4, 4, 4))) == c("ACCEPTED")
    machine.call("game_init")
    assert machine.status() == c("BAD_ID")
    assert not machine.busy()
    assert machine.next_event() == (0, 0)
    assert machine.submit(c("OP_BET_SPIN"), bytes((1,)), ident=2) == c("ACCEPTED")
    machine.cpu.write_u8(CONFIG_FAULT, 1)
    assert machine.status(2) == c("FAULT")
    assert machine.next_event()[0] == 0
    assert not machine.busy()


@pytest.mark.parametrize("blocker", ["motion", "door", "special", "fault"])
def test_requests_refuse_conflicting_native_states(machine, blocker):
    if blocker == "motion":
        machine.env(motion_busy=1)
    elif blocker == "door":
        machine.cpu.write_u8(MODE, 1)
        machine.cpu.write_u8(INTERLOCKS, 1)
    elif blocker == "special":
        machine.cpu.write_u8(0x43CD46, 1)
    else:
        machine.cpu.write_u8(MODE, 0x14)
    assert machine.submit(c("OP_ARM_OUTCOME"), bytes((1, 2, 3))) == c("BUSY")
    assert machine.submit(c("OP_BET_SPIN"), bytes((1,)), ident=2) == c("BUSY")
    assert not machine.busy()
    # An open door blocks spins but not an operator's idle profile change.
    assert (u8(machine.call("game_idle")) != 0) == (blocker == "door")


@pytest.mark.parametrize("state", ["t2-pending", "t2-enabled", *(hex(a) for a, _ in MOTION_SENTINELS)])
def test_runtime_admission_refuses_unretired_timer2_or_motion(machine, state):
    if state == "t2-pending":
        machine.cpu.write_u8(VIA_IFR, 0x80 | T2)
    elif state == "t2-enabled":
        machine.cpu.write_u8(VIA_IER, 0x80 | T2)
    else:
        address, size = next(s for s in MOTION_SENTINELS if hex(s[0]) == state)
        machine.cpu.write(address, (1).to_bytes(size, "big"))
    before = config(machine)
    assert machine.prepare(profile(lines=3)) == c("BUSY")
    assert not machine.busy()
    assert config(machine) == before


@pytest.mark.parametrize("change", ["t2-enabled", "t2-pending", "reel-index", "motor", "wager"])
def test_change_between_prepare_and_apply_faults_before_any_write(machine, change):
    data = profile(lines=3)
    assert machine.prepare(data) == c("OK")
    machine.cpu.write(SLOT, machine.staged())
    # Changed while core wrote its journal and slot.
    if change == "t2-enabled":
        machine.cpu.write_u8(VIA_IER, 0x80 | T2)
    elif change == "t2-pending":
        machine.cpu.write_u8(VIA_IFR, 0x80 | T2)
    elif change == "reel-index":
        machine.cpu.write_u8(MOTION_SENTINELS[0][0], 1)
    elif change == "wager":  # admission contract only; not a modeled coin race
        machine.env(wager=1)
    else:
        machine.cpu.write_u32(MOTOR, 2)
    before = config(machine)
    published = machine.cpu.read(machine.symbols["game_state"], 4)
    with machine.cpu.visits(CACHE_BUILDER) as builds:
        assert machine.apply(SLOT) == c("FAULT")
    assert config(machine) == before
    assert builds == []
    assert machine.cpu.read(machine.symbols["game_state"], 4) == published
    machine.call("game_finish_profile_change", c("FAULT"))
    assert machine.busy()  # a failed post-commit change stays inhibited


def test_runtime_reload_tail_without_ownership_faults(machine):
    stock = config(machine)
    assert machine.commit(profile(lines=3)) == c("OK")
    machine.cpu.write(CONFIG, stock)  # a runtime native reload rebuilt stock
    assert machine.apply(SLOT) == c("FAULT")  # its tail, with no profile ownership
    assert config(machine) == stock  # nothing written; the reload is not undone either


def via_accesses(machine, operation):
    """Run operation, recording VIA page accesses and the interrupt sections
    (entries of platform_irq_save/restore) in order."""
    events = []
    cpu = machine.cpu

    def access(uc, kind, address, size, value, data):
        events.append(("write" if kind == UC_MEM_WRITE else "read", address, size))

    with ExitStack() as hooks:
        hooks.enter_context(cpu.hook(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, access,
                                     begin=DEVICE_PAGE, end=DEVICE_PAGE + PAGE_SIZE - 1))
        for name in ("platform_irq_save", "platform_irq_restore"):
            address = machine.symbols[name]
            hooks.enter_context(cpu.hook(UC_HOOK_CODE, lambda *_, n=name: events.append(n),
                                         begin=address, end=address))
        result = operation()
    return result, events


def masked_reads(events):
    """VIA accesses, each with whether it lies inside an interrupt section."""
    inside, reads = False, []
    for event in events:
        if event == "platform_irq_save":
            inside = True
        elif event == "platform_irq_restore":
            inside = False
        else:
            reads.append((event, inside))
    return reads


@pytest.mark.parametrize("ifr,ier", [(0, 0x80), (0x80 | T1, 0x80 | T1)])  # idle; T1 on, pending
def test_runtime_admission_reads_two_via_bytes_and_accepts_timer1(machine, ifr, ier):
    machine.cpu.write_u8(VIA_IFR, ifr)
    machine.cpu.write_u8(VIA_IER, ier)
    via = machine.cpu.read(DEVICE_PAGE, PAGE_SIZE)
    status, events = via_accesses(machine, lambda: machine.commit(profile(lines=3)))
    assert status == c("OK")
    reads = masked_reads(events)
    # prepare's admission and apply's revalidation: IFR then IER, byte reads only,
    # each pair inside one interrupt section.
    assert reads == [(("read", VIA_IFR, 1), True), (("read", VIA_IER, 1), True)] * 2
    assert machine.cpu.read(DEVICE_PAGE, PAGE_SIZE) == via  # nothing acknowledged or written


@pytest.mark.parametrize("sr", [0x2000, 0x2300, 0x2600])
def test_runtime_admission_restores_the_exact_status_register(machine, sr):
    machine.cpu.write(STAGED, profile(lines=3) + bytes(c("SLOT_SIZE") - c("PROFILE_SIZE")))
    cpu = machine.cpu
    cpu.enter(STAGED, 0, sr=sr)
    cpu.run(machine.symbols["game_prepare_profile"], RETURN_ADDRESS)
    assert u8(machine.reg(UC_M68K_REG_D0)) == c("OK")
    assert cpu.read_sr() & 0xFF00 == sr  # system byte: trace, supervisor, interrupt mask


def test_motion_flag_410172_is_outside_the_admission_contract(machine):
    # Normal retirement clears it too (0x5e424); it is excluded from the contract
    # pending its broader lifecycle. This asserts only that exclusion, not that a
    # nonzero value is native settled idle.
    machine.cpu.write_u8(0x410172, 4)
    assert machine.prepare(profile(lines=3)) == c("OK")


def test_boot_recovery_assumes_no_runtime_idle_or_timer_state(machine):
    data = profile(lines=5)
    assert machine.commit(data) == c("OK")
    machine.cpu.write_u8(LINES, 1)  # power lost during an owned write
    # Retained, not yet reinitialized state: VIA setup and motion follow 0x1906.
    machine.cpu.write_u8(VIA_IFR, 0xFF)
    machine.cpu.write_u8(VIA_IER, 0xFF)
    for address, size in MOTION_SENTINELS:
        machine.cpu.write(address, b"\xff" * size)
    machine.cpu.write_u32(MOTOR, 0)
    machine.call("game_init")
    status, events = via_accesses(machine, lambda: machine.apply(SLOT, boot=True))
    assert status == c("OK")
    assert masked_reads(events) == []  # the VIA is not consulted at boot
    assert machine.cpu.read_u8(LINES) == 5
    assert machine.config_sum() == 0
