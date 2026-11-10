#include "game.h"
#include "motion.h"
#include "platform.h"

/* Native game state (notes/remote-game-config.md, "Native implementation
 * handoff"; notes/reel-control.md, "Selected normal outcome"). */
#define MODE 0x438030u            /* full main state; bit 0 = door open */
#define INTERLOCKS 0x43c31fu      /* aggregate open-door/interlock mask */
#define RECOVERY 0x438044u        /* interrupted/recovered spin preservation */
#define EVENT 0x4105dcu           /* task 6 event being dispatched (long) */
#define CALLER_DISPATCH 0xa5e2u   /* return address of the event dispatcher JSR */
#define ALT_TRANSACTION 0x43c340u /* long */
#define ALT_STATE 0x43c350u
#define MOTOR_STATE 0x4101f6u     /* long; 1 = idle */
#define MOTION_QUEUED 0x410213u
#define MOTION_FINALIZE 0x410165u
/* Cleared by normal motion retirement FUN_0005e1e2 (0x5e3b2, 0x5e3b8, 0x5e476). */
#define MOTION_REEL 0x410158u     /* byte: stepping reel index */
#define MOTION_PHASE 0x4101fau    /* long: stepping phase */
#define MOTION_MASK 0x4101a8u     /* word: pending phase mask */
#define HOST_FLAGS 0x43e8d2u      /* word; 0x80 = native skips the line cache */
#define OPTION_FLAGS 0x43dcd1u
#define ALTERNATE_WAGER 0x20u     /* BET ONE does not debit credits */
#define SPECIAL_MATCH 0x43ca2du   /* 15 selects a separate matcher path */
#define SPECIAL_MATCH_PATH 15u

/* Protected configuration block, checked by FUN_0000f2da as a zero word sum. */
#define CONFIG 0x43c87au
#define CONFIG_END 0x43ccdeu
#define CONFIG_SUM 0x43ccdcu
#define MAX_WAGER 0x43ca1eu       /* aggregate cap */
#define LINES 0x43ca1fu
#define STOPS 0x43ca20u
#define REELS 0x43ca21u
#define ALL_LINES 0x43ca27u       /* 1 = evaluate every line, not one per coin */
#define WEIGHTS 0x43c91au         /* [slot * WEIGHT_ROW + reel] */
#define WEIGHT_ROW 5u
#define WEIGHT_SLOTS 32u          /* selector index space, not physical stops */
#define TOTALS 0x43ccd2u          /* words per reel */
#define PAYTABLE_INDEX 0x43862du
#define PAYTABLES 16u
#define PAYTABLE_COUNT 0x43ca92u  /* pointer to count byte, stride 8 */
#define PAYTABLE_RECORDS 0x43ca96u

/* Background stop selector FUN_00008a60 and the evaluator's wager. */
#define SELECTOR_REEL 0x4101d6u
#define SELECTOR_RESIDUAL 0x4101c2u
#define SELECTOR_CANDIDATE 0x4101dau
#define CANDIDATE_STOP 0x4101ddu  /* low byte of each candidate long */
#define LINE_WAGER 0x4103b0u

#define COUNTER_WAGER 0x43u
#define COUNTER_CREDITS 0x47u
#define EVENT_SPIN 1u
#define EVENT_BET_ONE 9u
#define EVENT_SPIN_ALTERNATE 12u
#define LINES_INCONSISTENT 0xffu

enum { PHASE_NONE, PHASE_ARMED, PHASE_BETTING, PHASE_POSTED, PHASE_RUNNING };
enum { ARM_NONE, ARM_PENDING, ARM_BOUND, ARM_HELD };

struct game_runtime {
    uint8_t identity_order; /* offset 0: read by game_hooks.S */
    uint8_t active;
    uint8_t lines;
    uint8_t cap;
    uint8_t max_bet;
    uint8_t phase;
    uint8_t status;
    uint8_t arm;
    uint8_t hold_arm;       /* a failed bound spin keeps the arm until CANCEL */
    uint8_t claimed;        /* this copy loop commits the armed triple */
    uint8_t stops[FW_REELS];
    uint8_t profile_change; /* owned by core from prepare to finish; may latch */
    uint8_t dispatching;    /* task 6 is dispatching an event this hook returned */
    uint16_t held_count;    /* event dequeued after ownership began, redelivered */
    uint32_t held_event;
    uint32_t records;
    uint32_t target;
    uint32_t expected;
    uint64_t id;
};

/* Absolute RAM symbol supplied by link.ld; initialized only by game_init. */
extern struct game_runtime game_state;
#define state game_state
_Static_assert(sizeof(struct game_runtime) <= 0x1000u, "game reservation");
_Static_assert(__builtin_offsetof(struct game_runtime, identity_order) == 0u,
               "game_hooks.S reads the order flag at offset 0");
_Static_assert(FW_SLOT_CONTEXT_OFFSET == FW_PROFILE_SIZE + FW_PROFILE_GUARD_SIZE,
               "context fingerprint follows the guard records");
_Static_assert(3u + WEIGHT_SLOTS * WEIGHT_ROW + 2u * FW_REELS + 8u == FW_SLOT_BASELINE_SIZE,
               "baseline holds exactly the owned configuration bytes");

static uint32_t counter_read(uint32_t id)
{
    return ((uint32_t (*)(uint32_t))(uintptr_t)0x549c8u)(id);
}

/* -1, 0 or 1 as counter is below, equal to or above n. */
static int32_t counter_compare(uint32_t id, uint32_t n)
{
    return ((int32_t (*)(uint32_t, uint32_t))(uintptr_t)0x548bcu)(id, n);
}

static void bet_one_credit(void)
{
    ((void (*)(void))(uintptr_t)0x4341cu)();
}

/* Native first-wager initialization (new game record, latches and accounting). */
static void first_wager(void)
{
    ((void (*)(void))(uintptr_t)0x420f8u)();
}

static void clear_line_awards(void)
{
    ((void (*)(void))(uintptr_t)0x35fdcu)();
}

static void rebuild_line_cache(void)
{
    ((void (*)(void))(uintptr_t)0x36a7au)();
}

/* VRTX sc_lock/sc_unlock (trap #0, D0=0x20/0x21): nestable; interrupts stay
 * enabled but no task, including the configuration checksum task, can run. */
static void scheduler_lock(void)
{
    ((void (*)(void))(uintptr_t)0x3d82u)();
}

static void scheduler_unlock(void)
{
    ((void (*)(void))(uintptr_t)0x3d88u)();
}

/* VRTX sc_delay: the same one-tick yield 0xae6a makes when its queues are empty. */
static void yield_tick(void)
{
    ((void (*)(uint32_t))(uintptr_t)0x3a1eu)(1u);
}

static uint32_t records_pointer(void)
{
    uint32_t index = MMIO8(PAYTABLE_INDEX);
    return index < PAYTABLES ? MMIO32(PAYTABLE_RECORDS + 8u * index) : 0u;
}

static int custom_present(void)
{
    return records_pointer() - FW_PROFILE_BASE < FW_PERSIST_SIZE;
}

/* 0 = stock semantics; otherwise the active line count. Custom configuration
 * that does not match the cache fails closed rather than paying per total. */
static uint32_t game_lines(void)
{
    if (!state.active)
        return custom_present() ? LINES_INCONSISTENT : 0u;
    if (records_pointer() != state.records || MMIO8(LINES) != state.lines ||
        MMIO8(MAX_WAGER) != state.cap || MMIO8(ALL_LINES) != 1u)
        return LINES_INCONSISTENT;
    return state.lines;
}

static int machine_ok(void)
{
    return !fw_is_faulted() && !platform_native_fault() && platform_geometry_ok() &&
           MMIO8(STOPS) == FW_STOPS && MMIO8(REELS) == FW_REELS;
}

/* No recovered spin, special/bonus game, alternate transaction or motion. */
static int quiescent(void)
{
    return MMIO8(RECOVERY) == 0u && MMIO8(0x43cd46u) == 0u && MMIO8(0x43cd78u) == 0u &&
           MMIO8(0x43cd6au) == 0u && MMIO8(0x43cd91u) == 0u && MMIO8(0x410232u) == 0u &&
           MMIO32(ALT_TRANSACTION) == 0u && MMIO8(ALT_STATE) == 0u &&
           MMIO32(MOTOR_STATE) == 1u && MMIO8(MOTION_QUEUED) == 0u &&
           MMIO8(MOTION_FINALIZE) == 0u && !motion_is_busy();
}

static int wager_mode(uint32_t mode)
{
    return mode == 0u || mode == 2u || mode == 4u;
}

static int ready_for_spin(void)
{
    return wager_mode(MMIO8(MODE)) && MMIO8(INTERLOCKS) == 0u && machine_ok() &&
           quiescent() && game_lines() != LINES_INCONSISTENT;
}

void game_init(void)
{
    uint8_t *p = (uint8_t *)&state;
    uint32_t i;
    for (i = 0; i < sizeof(state); ++i)
        p[i] = 0;
}

bool game_is_busy(void)
{
    return state.phase != PHASE_NONE || state.arm != ARM_NONE || state.claimed ||
           state.profile_change;
}

/* The native game itself is idle: no wager placed and nothing in progress. */
static int native_idle(void)
{
    return (MMIO8(MODE) & 0xfeu) == 0u && quiescent() && counter_read(COUNTER_WAGER) == 0u;
}

bool game_idle(void)
{
    return !game_is_busy() && native_idle();
}

/* Caller holds the IRQ section. */
static void finish(uint8_t status)
{
    state.status = status;
    state.phase = PHASE_NONE;
    if (state.arm == ARM_BOUND && state.hold_arm)
        state.arm = ARM_HELD;
    else if (state.arm != ARM_HELD)
        state.arm = ARM_NONE;
}

uint32_t game_line_wager(uint32_t total)
{
    uint32_t lines = game_lines();
    if (lines == 0u)
        return total;
    /* An indivisible total is refused at SPIN; never pay more than whole lines. */
    return lines == LINES_INCONSISTENT ? 0u : total / lines;
}

void game_evaluate_wager(void)
{
    clear_line_awards();
    MMIO32(LINE_WAGER) = game_line_wager(MMIO32(LINE_WAGER));
}

uint32_t game_repeat_wager(uint32_t counter)
{
    uint32_t total = counter_read(counter), lines = game_lines();
    if (lines == 0u)
        return total;
    /* Zero makes the native repeat path refuse (a previous profile's wager). */
    return lines != LINES_INCONSISTENT && total % lines == 0u ? total : 0u;
}

uint32_t game_bet_one(void)
{
    uint32_t lines = game_lines(), total, step;
    if (lines == 0u) {
        bet_one_credit();
        return 1u;
    }
    if (lines == LINES_INCONSISTENT)
        return 0u;
    total = counter_read(COUNTER_WAGER);
    /* One credit per line; a coin-made remainder is topped up to whole lines. */
    step = lines - total % lines;
    if (total + step > MMIO8(MAX_WAGER) || counter_compare(COUNTER_CREDITS, step) < 0)
        return 0u;
    bet_one_credit();
    /* The native continuation (0x42fb2) initializes a new game only when the
     * wager is exactly one credit; do it there, once, before the other lines. */
    if (total == 0u && step > 1u)
        first_wager();
    while (--step != 0u)
        bet_one_credit();
    return 1u;
}

/* No game may start: profile change owned, diagnostic motion owned or
 * failure-latched, or custom configuration without a matching cache. */
static int starts_blocked(void)
{
    return state.profile_change || motion_is_busy() || game_lines() == LINES_INCONSISTENT;
}

/* A custom profile plays only ordinary wager states with a whole-line wager;
 * alternate-transaction (0x1a), option-dependent (0x1c), special-game and
 * coin-made remainders have no per-line invariant. */
static int custom_start_ok(uint32_t lines)
{
    uint32_t mode = MMIO8(MODE) & 0xfeu, total;
    if ((mode != 2u && mode != 4u) || MMIO32(ALT_TRANSACTION) != 0u || MMIO8(ALT_STATE) != 0u)
        return 0;
    total = counter_read(COUNTER_WAGER);
    return total != 0u && total % lines == 0u;
}

/* FUN_00061172 entry guard: every normal start, whether dispatched or called
 * directly by native alternate-transaction and special-game paths. */
uint32_t game_start_permitted(void)
{
    uint32_t lines;
    if (starts_blocked())
        return 0u;
    lines = game_lines();
    return lines == 0u || custom_start_ok(lines);
}

static int spin_refused(uint32_t event)
{
    uint32_t lines;
    if (event != EVENT_SPIN && event != EVENT_SPIN_ALTERNATE)
        return 0;
    if (starts_blocked())
        return 1;
    lines = game_lines();
    /* Mode 0 repeats the last wager through game_repeat_wager; door-open
     * spins (modes 3/5) never reach the 0x61172 guard. */
    return lines != 0u && (MMIO8(MODE) & 0xfeu) != 0u && !custom_start_ok(lines);
}

static uint32_t bet_step(uint8_t *status, uint32_t *expected)
{
    uint32_t lines = game_lines(), total = counter_read(COUNTER_WAGER);
    if (lines == 0u)
        lines = 1u;
    *status = FW_INTERRUPTED;
    if (!ready_for_spin() || total != state.expected)
        return 0u;
    if (total == state.target) {
        if (MMIO8(MODE) == 0u)
            return 0u; /* event 1 in mode 0 would repeat a previous wager */
        *status = FW_ACCEPTED;
        return EVENT_SPIN;
    }
    if (total > state.target || total % lines != 0u ||
        counter_compare(COUNTER_CREDITS, lines) < 0)
        return 0u;
    *status = FW_ACCEPTED;
    *expected = total + lines;
    return EVENT_BET_ONE;
}

static uint32_t advance(void)
{
    uint8_t phase = state.phase, status = FW_ACCEPTED;
    uint32_t event = 0, expected = state.expected;
    uint16_t irq;
    if (phase == PHASE_NONE)
        return 0u;
    if (!machine_ok())
        status = FW_FAULT;
    else if (phase == PHASE_ARMED)
        status = ready_for_spin() ? FW_ACCEPTED : FW_INTERRUPTED;
    else if (phase == PHASE_BETTING)
        event = bet_step(&status, &expected);
    else if (phase == PHASE_POSTED)
        status = FW_INTERRUPTED; /* FUN_00061172 returned without committing */
    else if (wager_mode(MMIO8(MODE) & 0xfeu))
        status = FW_OK; /* the started game returned to an idle wager state */
    irq = platform_irq_save();
    if (state.phase != phase) {
        event = 0u;
    } else if (status != FW_ACCEPTED) {
        finish(status);
        event = 0u;
    } else if (event == EVENT_SPIN) {
        state.phase = PHASE_POSTED;
    } else {
        state.expected = expected;
    }
    platform_irq_restore(irq);
    return event;
}

static uint32_t deliver(uint32_t count)
{
    state.dispatching = count != 0u;
    return count;
}

uint32_t game_next_event(void)
{
    uint32_t count;
    uint16_t irq;
    state.dispatching = 0;
    if (state.profile_change) {
        yield_tick();
        return 0u;
    }
    if (state.held_count != 0u) {
        state.dispatching = 1;
        MMIO32(EVENT) = state.held_event;
        count = state.held_count;
        state.held_count = 0;
        return deliver(spin_refused(MMIO32(EVENT)) ? 0u : count);
    }
    count = ((uint32_t (*)(void))(uintptr_t)0xae6au)() & 0xffffu;
    if (count != 0u) {
        irq = platform_irq_save();
        /* Ownership began while the reader was inside the RTOS: keep the event
         * for after the profile change instead of dispatching it now. */
        if (state.profile_change) {
            state.held_event = MMIO32(EVENT);
            state.held_count = (uint16_t)count;
            count = 0u;
        } else {
            state.dispatching = 1; /* counter reads below may reschedule */
        }
        platform_irq_restore(irq);
        if (count == 0u) {
            yield_tick();
            return 0u;
        }
        return deliver(spin_refused(MMIO32(EVENT)) ? 0u : count);
    }
    count = advance();
    if (count == 0u)
        return 0u;
    MMIO32(EVENT) = count;
    return deliver(1u);
}

static int claim_eligible(uint32_t caller)
{
    return caller == CALLER_DISPATCH && MMIO8(MODE) == 6u && MMIO8(INTERLOCKS) == 0u &&
           MMIO32(EVENT) == EVENT_SPIN && machine_ok() && MMIO8(RECOVERY) == 0u &&
           MMIO8(0x43cd46u) == 0u && MMIO8(0x43cd78u) == 0u && MMIO8(0x43cd6au) == 0u &&
           MMIO8(0x43cd91u) == 0u && MMIO8(0x410232u) == 0u &&
           MMIO32(ALT_TRANSACTION) == 0u && MMIO8(ALT_STATE) == 0u &&
           !motion_is_busy() && game_lines() != LINES_INCONSISTENT;
}

uint32_t game_candidate(uint32_t reel, uint32_t caller)
{
    if (reel == 0u) {
        int eligible = claim_eligible(caller);
        uint16_t irq = platform_irq_save();
        /* Decided once per copy loop; the triple never changes source midway. */
        state.claimed = eligible &&
            ((state.phase == PHASE_ARMED && state.arm == ARM_PENDING) ||
             (state.phase == PHASE_POSTED && state.arm == ARM_BOUND));
        platform_irq_restore(irq);
    }
    if (state.claimed && reel < FW_REELS)
        return state.stops[reel];
    return MMIO8(CANDIDATE_STOP + 4u * reel);
}

void game_commit(void)
{
    uint16_t irq = platform_irq_save();
    if (state.claimed) {
        state.claimed = 0;
        if (state.arm != ARM_HELD)
            state.arm = ARM_NONE;
        if (state.phase == PHASE_ARMED || state.phase == PHASE_POSTED)
            state.phase = PHASE_RUNNING;
    } else if (state.phase == PHASE_POSTED && state.arm != ARM_BOUND) {
        state.phase = PHASE_RUNNING;
    } else if (state.phase != PHASE_NONE && state.phase != PHASE_RUNNING) {
        /* A game started without the request's outcome: never leak it later. */
        finish(FW_INTERRUPTED);
    }
    platform_irq_restore(irq);
}

static int valid_stops(const uint8_t *stops)
{
    uint32_t i;
    for (i = 0; i < FW_REELS; ++i)
        if (stops[i] >= FW_STOPS)
            return 0;
    return 1;
}

static void accept(uint8_t phase, uint64_t id)
{
    state.phase = phase;
    state.status = FW_ACCEPTED;
    state.id = id;
}

static uint8_t submit_arm(const uint8_t *stops, uint64_t id)
{
    uint8_t result = FW_BUSY;
    uint32_t i;
    uint16_t irq = platform_irq_save();
    if (!game_is_busy() && ready_for_spin()) {
        for (i = 0; i < FW_REELS; ++i)
            state.stops[i] = stops[i];
        state.arm = ARM_PENDING;
        accept(PHASE_ARMED, id);
        result = FW_ACCEPTED;
    }
    platform_irq_restore(irq);
    return result;
}

static uint8_t submit_cancel(uint64_t id)
{
    uint8_t result = FW_BAD_REQUEST;
    uint16_t irq = platform_irq_save();
    if (state.claimed || state.arm == ARM_BOUND ||
        (state.phase != PHASE_NONE && state.phase != PHASE_ARMED)) {
        result = FW_BUSY; /* committed or bound to a running spin */
    } else if (state.arm != ARM_NONE) {
        state.arm = ARM_NONE;
        state.phase = PHASE_NONE;
        state.status = FW_OK;
        state.id = id;
        result = FW_OK;
    }
    platform_irq_restore(irq);
    return result;
}

static uint8_t submit_spin(const uint8_t *args, uint32_t length, uint64_t id)
{
    uint32_t lines = game_lines(), total, target, i;
    uint8_t result = FW_BUSY;
    uint16_t irq;
    if (lines == LINES_INCONSISTENT)
        return FW_FAULT;
    if (lines == 0u)
        lines = 1u;
    target = (uint32_t)args[0] * lines;
    if (args[0] == 0u || args[0] > (state.active ? state.max_bet : MMIO8(MAX_WAGER)) ||
        target > MMIO8(MAX_WAGER) || (length == 1u + FW_REELS && !valid_stops(args + 1)))
        return FW_BAD_REQUEST;
    if (!ready_for_spin())
        return FW_BUSY;
    total = counter_read(COUNTER_WAGER);
    if (total > target || total % lines != 0u || (MMIO8(MODE) == 0u && total != 0u) ||
        (total < target && counter_compare(COUNTER_CREDITS, target - total) < 0))
        return FW_BUSY;
    irq = platform_irq_save();
    if (state.arm == ARM_PENDING && state.phase == PHASE_ARMED && !state.claimed) {
        if (length == 1u + FW_REELS)
            for (i = 0; i < FW_REELS; ++i)
                if (args[1u + i] != state.stops[i])
                    result = FW_BAD_REQUEST; /* never silently replace an arm */
        if (result != FW_BAD_REQUEST) {
            state.arm = ARM_BOUND;
            state.hold_arm = 1;
            result = FW_ACCEPTED;
        }
    } else if (!game_is_busy()) {
        if (length == 1u + FW_REELS) {
            for (i = 0; i < FW_REELS; ++i)
                state.stops[i] = args[1u + i];
            state.arm = ARM_BOUND;
            state.hold_arm = 0;
        }
        result = FW_ACCEPTED;
    }
    if (result == FW_ACCEPTED) {
        state.target = target;
        state.expected = total;
        accept(PHASE_BETTING, id);
    }
    platform_irq_restore(irq);
    return result;
}

uint8_t game_submit(uint8_t operation, const uint8_t *args, uint32_t length,
                    uint64_t id)
{
    if (id == 0u)
        return FW_BAD_ID;
    switch (operation) {
    case FW_OP_ARM_OUTCOME:
        if (length != FW_REELS || !valid_stops(args))
            return FW_BAD_REQUEST;
        return submit_arm(args, id);
    case FW_OP_CANCEL_OUTCOME:
        return length == 0u ? submit_cancel(id) : FW_BAD_REQUEST;
    case FW_OP_BET_SPIN:
        if (length != 1u && length != 1u + FW_REELS)
            return FW_BAD_REQUEST;
        return submit_spin(args, length, id);
    default:
        return FW_UNSUPPORTED;
    }
}

uint8_t game_current_status(uint64_t id)
{
    uint16_t irq;
    if (id == 0u || id != state.id)
        return FW_BAD_ID;
    if (state.status == FW_ACCEPTED && !machine_ok()) {
        irq = platform_irq_save();
        if (state.status == FW_ACCEPTED && state.phase != PHASE_NONE)
            finish(FW_FAULT);
        platform_irq_restore(irq);
    }
    return state.status;
}

static uint32_t config_sum(uint32_t end)
{
    uint32_t address, sum = 0;
    for (address = CONFIG; address < end; address += 2u)
        sum += MMIO16(address);
    return sum & 0xffffu;
}

static uint32_t get_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static int in_range(uint32_t address, uint32_t start, uint32_t size)
{
    return address - start < size;
}

/* Configuration bytes a profile owns; everything else in the block is native. */
static int owned(uint32_t address, uint32_t index)
{
    return address == MAX_WAGER || address == LINES || address == ALL_LINES ||
           in_range(address, WEIGHTS, WEIGHT_SLOTS * WEIGHT_ROW) ||
           in_range(address, TOTALS, 2u * FW_REELS) ||
           in_range(address, PAYTABLE_COUNT + 8u * index, 4u) ||
           in_range(address, PAYTABLE_RECORDS + 8u * index, 4u);
}

/* Next owned field at or after *address, in address order (the baseline
 * layout): its width, or 0 past the block. Paytable pointers are longs and
 * reel totals words, always written whole. */
static uint32_t next_field(uint32_t *address, uint32_t index)
{
    for (; *address < CONFIG_SUM; ++*address) {
        if (!owned(*address, index))
            continue;
        if (*address == PAYTABLE_COUNT + 8u * index ||
            *address == PAYTABLE_RECORDS + 8u * index)
            return 4u;
        return in_range(*address, TOTALS, 2u * FW_REELS) ? 2u : 1u;
    }
    return 0u;
}

/* Integrity fingerprint binding a baseline to the unowned configuration and
 * its native selection, none of which a profile change writes. A CRC check,
 * not authentication. */
static uint32_t native_fingerprint(const uint8_t *baseline)
{
    uint32_t index = MMIO8(PAYTABLE_INDEX), address, crc = UINT32_MAX;
    uint8_t context[3];
    for (address = CONFIG; address < CONFIG_SUM; ++address)
        if (!owned(address, index))
            crc = fw_crc32_update(crc, (const uint8_t *)(uintptr_t)address, 1u);
    context[0] = MMIO8(0x43862cu); /* reel-set index */
    context[1] = (uint8_t)index;
    context[2] = MMIO8(OPTION_FLAGS) & ALTERNATE_WAGER;
    crc = fw_crc32_update(crc, context, sizeof(context));
    return ~fw_crc32_update(crc, baseline, FW_SLOT_BASELINE_SIZE);
}

static void capture_baseline(uint8_t *baseline)
{
    uint32_t index = MMIO8(PAYTABLE_INDEX), address, width, i = 0, k;
    for (address = CONFIG; (width = next_field(&address, index)) != 0u; address += width)
        for (k = 0; k < width; ++k)
            baseline[i++] = MMIO8(address + k);
}

static uint32_t reel_total(const uint8_t *p, uint32_t reel)
{
    uint32_t stop, total = 0;
    for (stop = 0; stop < FW_STOPS; ++stop)
        total += p[8u + stop * FW_REELS + reel];
    return total;
}

/* The value a custom profile gives the owned field at address. */
static uint32_t custom_field(const uint8_t *p, uint32_t address, uint32_t index)
{
    uint32_t slot = (address - WEIGHTS) / WEIGHT_ROW, reel = (address - WEIGHTS) % WEIGHT_ROW;
    if (address == MAX_WAGER)
        return (uint32_t)p[5] * p[6];
    if (address == LINES)
        return p[5];
    if (address == ALL_LINES)
        return 1u;
    if (address == PAYTABLE_COUNT + 8u * index)
        return (uint32_t)(uintptr_t)(p + 7);
    if (address == PAYTABLE_RECORDS + 8u * index)
        return (uint32_t)(uintptr_t)(p + FW_PROFILE_RECORD_OFFSET);
    if (in_range(address, TOTALS, 2u * FW_REELS))
        return reel_total(p, (address - TOTALS) / 2u);
    return slot < FW_STOPS && reel < FW_REELS ? p[8u + slot * FW_REELS + reel] : 0u;
}

static uint32_t field(uint32_t address, uint32_t width)
{
    return width == 4u ? MMIO32(address) : width == 2u ? MMIO16(address) : MMIO8(address);
}

/* Writes each owned field that differs from the slot's custom profile, or from
 * its baseline when restoring, through the native SafeRAM writers, which mask
 * interrupts across a whole field: an interrupt handler sees each field old or
 * new, never zeroed (IRQ4's coin path reads the cap). Power loss can tear a
 * long; apply's fingerprint excludes owned bytes, so a replay completes it.
 * The writers do not verify their stores: each field is read back whole, and
 * the first mismatch is FW_FAULT with no further write. *selector is set if
 * the selector's weights or totals changed. */
static uint8_t write_owned(const uint8_t *slot, bool restore, int *selector)
{
    const uint8_t *baseline = slot + FW_SLOT_BASELINE_OFFSET;
    uint32_t index = MMIO8(PAYTABLE_INDEX), address, width, value, i = 0, k;
    *selector = 0;
    for (address = CONFIG; (width = next_field(&address, index)) != 0u; address += width) {
        if (restore)
            for (value = 0, k = 0; k < width; ++k)
                value = (value << 8) | baseline[i + k];
        else
            value = custom_field(slot, address, index);
        i += width;
        if (field(address, width) == value)
            continue;
        if (width == 4u)
            platform_safe_u32(address, value);
        else if (width == 2u)
            platform_safe_u16(address, value);
        else
            platform_safe_u8(address, value);
        if (field(address, width) != value)
            return FW_FAULT;
        *selector |= in_range(address, WEIGHTS, WEIGHT_SLOTS * WEIGHT_ROW) ||
                     in_range(address, TOTALS, 2u * FW_REELS);
    }
    return FW_OK;
}

static int native_eligible(void)
{
    return MMIO8(PAYTABLE_INDEX) < PAYTABLES && platform_geometry_ok() &&
           MMIO8(STOPS) == FW_STOPS && MMIO8(REELS) == FW_REELS &&
           (MMIO8(OPTION_FLAGS) & ALTERNATE_WAGER) == 0u &&
           MMIO8(SPECIAL_MATCH) != SPECIAL_MATCH_PATH;
}

/* A committed slot native tables may point into: persistent, a valid profile
 * and the two zero guard records the native line cache reads past. */
static int slot_ok(const uint8_t *p)
{
    uint32_t i;
    if ((uint32_t)(uintptr_t)p - FW_PROFILE_BASE >
            FW_PERSIST_SIZE - FW_SLOT_BASELINE_OFFSET - FW_SLOT_BASELINE_SIZE ||
        fw_validate_profile(p) != FW_OK)
        return 0;
    for (i = FW_PROFILE_SIZE; i < FW_PROFILE_SIZE + FW_PROFILE_GUARD_SIZE; ++i)
        if (p[i] != 0u)
            return 0;
    return 1;
}

/* The live native context is the one the slot's baseline was taken from. */
static int context_matches(const uint8_t *slot)
{
    return native_eligible() &&
           native_fingerprint(slot + FW_SLOT_BASELINE_OFFSET) ==
               get_le32(slot + FW_SLOT_CONTEXT_OFFSET);
}

/* Restart each reel's weighted walk at a positive-weight candidate so stale
 * residuals from the previous weights cannot select a zero-weight stop. */
static void reconcile_selector(void)
{
    uint32_t reel, slot, i;
    for (reel = 0; reel < FW_REELS; ++reel) {
        slot = MMIO32(SELECTOR_CANDIDATE + 4u * reel) % WEIGHT_SLOTS;
        for (i = 0; i < WEIGHT_SLOTS && MMIO8(WEIGHTS + slot * WEIGHT_ROW + reel) == 0u; ++i)
            slot = (slot + 1u) % WEIGHT_SLOTS;
        MMIO32(SELECTOR_CANDIDATE + 4u * reel) = slot;
        MMIO32(SELECTOR_RESIDUAL + 4u * reel) = MMIO8(WEIGHTS + slot * WEIGHT_ROW + reel);
    }
    MMIO32(SELECTOR_REEL) = 0u;
}

static void publish(const uint8_t *profile, int selector_changed)
{
    uint16_t irq = platform_irq_save();
    state.active = profile != 0;
    state.identity_order = profile != 0;
    state.records = profile ? (uint32_t)(uintptr_t)(profile + FW_PROFILE_RECORD_OFFSET) : 0u;
    state.lines = profile ? profile[5] : 0u;
    state.max_bet = profile ? profile[6] : 0u;
    state.cap = profile ? (uint8_t)(profile[5] * profile[6]) : 0u;
    if (selector_changed)
        reconcile_selector();
    platform_irq_restore(irq);
}

/* Runtime admission (firmware/audits/IRQ-AUDIT.md, "Recommended bounded admission
 * snapshot"): native motion retired normally and VIA timer 2 is neither pending
 * nor enabled. Under the audit's normal-lifecycle premise, IRQ6 then does not
 * route through stepping handler 0x5e654 while owned fields change; this does
 * not certify arbitrary peripheral state. One snapshot with interrupts briefly
 * masked; the original status register is restored exactly. */
static int motion_retired(void)
{
    uint16_t irq = platform_irq_save();
    int retired = MMIO32(MOTOR_STATE) == 1u && MMIO8(MOTION_QUEUED) == 0u &&
                  MMIO8(MOTION_FINALIZE) == 0u && MMIO8(MOTION_REEL) == 0u &&
                  MMIO32(MOTION_PHASE) == 0u && MMIO16(MOTION_MASK) == 0u &&
                  !platform_timer2_active();
    platform_irq_restore(irq);
    return retired;
}

static uint8_t validate_change(const uint8_t *staged)
{
    if (staged != 0 && fw_validate_profile(staged) != FW_OK)
        return FW_INVALID_PROFILE;
    if (staged != 0 && !native_eligible())
        return FW_UNSUPPORTED;
    if (!machine_ok())
        return FW_FAULT;
    /* Neither a captured baseline nor a restoration may hide a block the
     * sticky checksum task has not yet seen corrupted. */
    if (config_sum(CONFIG_END) != 0u)
        return FW_FAULT;
    return native_idle() && motion_retired() ? FW_OK : FW_BUSY;
}

/* Core's active slot, not the live pointers, decides between inheriting and
 * capturing a baseline: a changed paytable index alone can make custom
 * configuration look stock. Scheduler locked. */
static uint8_t take_baseline(uint8_t *staged, const uint8_t *active)
{
    uint32_t i;
    if (active == 0) {
        if (staged == 0 || state.active || custom_present())
            return FW_FAULT;
        capture_baseline(staged + FW_SLOT_BASELINE_OFFSET);
        i = native_fingerprint(staged + FW_SLOT_BASELINE_OFFSET);
        staged[FW_SLOT_CONTEXT_OFFSET] = (uint8_t)i;
        staged[FW_SLOT_CONTEXT_OFFSET + 1u] = (uint8_t)(i >> 8);
        staged[FW_SLOT_CONTEXT_OFFSET + 2u] = (uint8_t)(i >> 16);
        staged[FW_SLOT_CONTEXT_OFFSET + 3u] = (uint8_t)(i >> 24);
        return FW_OK;
    }
    /* Never carry a baseline into a context other than its own. */
    if (!state.active || state.records != (uint32_t)(uintptr_t)(active + FW_PROFILE_RECORD_OFFSET) ||
        game_lines() == LINES_INCONSISTENT || !slot_ok(active) || !context_matches(active))
        return FW_FAULT;
    if (staged != 0)
        for (i = 0; i < 4u + FW_SLOT_BASELINE_SIZE; ++i)
            staged[FW_SLOT_CONTEXT_OFFSET + i] = active[FW_SLOT_CONTEXT_OFFSET + i];
    return FW_OK;
}

uint8_t game_prepare_profile(uint8_t *staged, const uint8_t *active)
{
    uint8_t result = FW_BUSY;
    uint16_t irq = platform_irq_save();
    /* Exclude task 6 first; only then is the native idle state stable. */
    if (!game_is_busy() && !state.dispatching && state.held_count == 0u) {
        state.profile_change = 1;
        result = FW_OK;
    }
    platform_irq_restore(irq);
    if (result != FW_OK)
        return result;
    result = validate_change(staged);
    if (result == FW_OK) {
        scheduler_lock();
        result = take_baseline(staged, active);
        scheduler_unlock();
    }
    if (result != FW_OK)
        state.profile_change = 0; /* rejected before any side effect */
    return result;
}

void game_finish_profile_change(uint8_t status)
{
    /* FW_BUSY: the change was abandoned before any side effect. Anything else
     * but success may have left a partial transition: stay inhibited. */
    if (status == FW_OK || status == FW_BUSY)
        state.profile_change = 0;
}

/* Final check before a runtime write, under the scheduler lock (the native
 * counter reader nests it): the profile change is owned and the native game
 * is still as prepare admitted it. */
static int runtime_admitted(void)
{
    return state.profile_change && machine_ok() && native_idle() && motion_retired();
}

static uint8_t apply_slot(const uint8_t *slot, bool restore, bool boot)
{
    uint8_t result = FW_FAULT;
    int selector = 0;
    if (!slot_ok(slot))
        return FW_INVALID_PROFILE;
    scheduler_lock();
    if ((boot || runtime_admitted()) && context_matches(slot))
        result = write_owned(slot, restore, &selector);
    /* Also completes an owned write torn by power loss; verified like a field.
     * A failure leaves the sum for the native checksum task, and the cache and
     * publication untouched. */
    if (result == FW_OK && config_sum(CONFIG_END) != 0u) {
        platform_safe_u16(CONFIG_SUM, (0u - config_sum(CONFIG_SUM)) & 0xffffu);
        if (config_sum(CONFIG_END) != 0u)
            result = FW_FAULT;
    }
    if (result == FW_OK) {
        if ((MMIO16(HOST_FLAGS) & 0x80u) == 0u)
            rebuild_line_cache();
        publish(restore ? 0 : slot, selector);
        result = restore && custom_present() ? FW_FAULT : FW_OK;
    }
    scheduler_unlock();
    return result;
}

uint8_t game_apply_profile(const uint8_t *slot, bool restore, bool boot)
{
    if (slot != 0)
        return apply_slot(slot, restore, boot);
    if (restore)
        return FW_FAULT;
    /* Settled stock: never repair a native checksum or wipe recovery state. */
    publish(0, 0);
    return custom_present() ? FW_FAULT : FW_OK;
}
