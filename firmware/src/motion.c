#include "motion.h"
#include "game.h"
#include "platform.h"

/* The abort below is the native stop precedent (0x5fb98, 0x5efc0, 0x5b5fe)
 * applied to an extension-owned move. It and the deadlines are offline-tested
 * only; leave the capability disabled until hardware acceptance qualifies them. */
#ifndef MOTION_QUALIFIED
#define MOTION_QUALIFIED 0
#endif

/* Native RAM and I/O, main ROM S6M00002B5 (notes/reel-control.md). */
#define TEST_SELECTED 0x41001eu  /* u8: 0x0a while task 7 runs Test 10 */
#define TEST10 0x0au
#define MODE 0x412876u           /* normalized mode, 0x16 in Test 10 */
#define FULL_MODE 0x438030u      /* full state, bit 0 = door open */
#define DIAGNOSTIC_MODE 0x16u
#define INTERLOCKS 0x43c31fu     /* only the main-door bit may be set */
#define MAIN_DOOR 0x01u
#define INPUTS 0xff1014u         /* active-low CHANGE 0x0800, SPIN 0x1000 */
#define BUTTONS 0x1800u
#define MOTOR 0x4101f6u          /* u32 shared motor state */
#define MOTOR_IDLE 0x01u
#define MOTOR_ABSOLUTE 0x10u
#define MOTOR_JOG 0x40u
#define MOTOR_STOP 0x80u
#define COUNTDOWN 0x410212u
#define NORMAL_PENDING 0x410213u
#define FLAGS 0x410172u
#define FINALIZE 0x410165u
#define ACTIVE_REELS 0x410183u
#define REEL_STOPS 0x41017cu
#define SENSED 0x410152u
#define TARGET 0x410160u
#define TARGET_PHASE 0x41015au
#define REEL_STATE 0x410186u
#define REEL_IDLE 0x01u
#define REEL_STOP 0x80u
#define PHASE 0x41018cu
#define PHASE_ORIGIN 0x4385d6u
#define RECORDED 0x43e012u       /* SafeRAM settled stop counters */
#define UNKNOWN_STOP 0xffu
#define SELECTOR 0x410028u       /* reel used by the jog interrupt fragment */
#define JOG_STEPS 0x4101b6u      /* u16 */
#define JOG_TIMING 0x410228u     /* u32 pointer */
#define HOLD_MASK 0x4101b0u      /* u16 */
#define JOG_STEPS_INDEX 10u
#define JOG_STEPS_OTHER 9u
#define JOG_TIMING_INDEX 0x6b830u
#define JOG_TIMING_OTHER 0x6b80cu
#define HOLD_TABLE 0x6b858u
#define PAYOUT_WAGER 0x410371u
#define PAYOUT_EDGE 0x410386u    /* u16 */

/* Bounded native helpers; verified stack ABIs, D0/D1/A0 scratch. */
#define NATIVE_STEP_OFFSET 0x602fcu /* (t) -> t ? 9t + 1 : 0 */
#define NATIVE_LOAD_TIMER 0x5fed8u  /* (timer, value): writes FE10C8, no wait */

/* Deadline derivation, all in VIA phi2 cycles so no E-clock frequency is assumed:
 * - tick: VIA FE1080 T1 continuous latch 0x1ccc (0x24e8), long 0x4127f6
 *   advances on every second interrupt (0x2674); <= 0x1ccc + 2 cycles each.
 * - motor interrupt: VIA FE10C0 T1 one-shot rearmed by 0x5fed8 with a 16-bit
 *   count, so each motor-state period is at most 0xffff + 2 cycles.
 * - steps: 200 electrical steps per revolution (0x5fcda; 0x602fc); index search
 *   plus target needs at most two revolutions; three revolutions is the native
 *   initialization index-search limit (0x5e038, edges > 3 * stops).
 * - absolute: dwell 2 (geometry 0x6b890 -> 0x41017a), settle 20 (0x5dbf6) plus
 *   its transition, two equal finish samples (0x5e290), idle countdown 5
 *   (0x5e3aa) plus its transition. Jog: at most 10 steps (0x5f3b6), settle 3
 *   (0x5de80). Abort: the absolute settle allowance as coast-down margin. */
#define PHI2_PER_TICK (2u * (0x1cccu + 2u))
#define PHI2_PER_PERIOD (0xffffu + 2u)
#define STEPS_PER_REVOLUTION 200u
#define REVOLUTIONS 3u
#define DWELL_PERIODS 2u
#define ABSOLUTE_SETTLE_PERIODS (20u + 1u)
#define JOG_SETTLE_PERIODS (3u + 1u)
#define FINISH_PERIODS 2u
#define IDLE_PERIODS (5u + 1u)
#define ABSOLUTE_PERIODS (REVOLUTIONS * STEPS_PER_REVOLUTION + DWELL_PERIODS + \
                          ABSOLUTE_SETTLE_PERIODS + FINISH_PERIODS + IDLE_PERIODS)
#define JOG_PERIODS (JOG_STEPS_INDEX + JOG_SETTLE_PERIODS + FINISH_PERIODS + IDLE_PERIODS)
#define ABORT_PERIODS (ABSOLUTE_SETTLE_PERIODS + FINISH_PERIODS + IDLE_PERIODS)
#define TICKS(periods) (((periods) * PHI2_PER_PERIOD + PHI2_PER_TICK - 1u) / PHI2_PER_TICK)
_Static_assert(ABSOLUTE_PERIODS <= UINT32_MAX / PHI2_PER_PERIOD, "deadline overflow");
_Static_assert(TICKS(ABSOLUTE_PERIODS) < 0x80000000u, "wrapping deadline");

struct motion_runtime {
    uint64_t id;
    uint32_t since;          /* tick when the current deadline started */
    uint8_t phase;
    uint8_t cause;
    uint8_t status;          /* FW_* result for id */
    uint8_t operation;
    uint8_t stops[FW_REELS]; /* immutable until terminal status */
    uint8_t reel;
    uint8_t selector;        /* native 0x410028 before a jog */
    uint8_t selector_saved;
    uint8_t recovery;
    uint8_t loop;            /* task 7 is inside the Test 10 polling loop */
};

/* Absolute RAM symbol supplied by link.ld; initialized only by motion_init.
 * Every transition runs in one saved-SR section: the SAS submit, the task-7
 * step and the Test 10 exit are atomic against each other and the motor
 * interrupts. Sections are bounded by FW_REELS and never wait or yield. */
extern struct motion_runtime motion_state;
#define state motion_state
_Static_assert(sizeof(struct motion_runtime) <= 0x600u, "motion reservation");

static uint8_t read8(uint32_t address)
{
    return MMIO8(address);
}

void motion_init(void)
{
    uint8_t *p = (uint8_t *)&state;
    uint32_t i;
    for (i = 0; i < sizeof(state); ++i)
        p[i] = 0;
}

int motion_is_busy(void)
{
    return state.phase != MOTION_IDLE;
}

uint32_t motion_capabilities(void)
{
    return MOTION_QUALIFIED ? FW_CAP_POSITION | FW_CAP_JOG : 0u;
}

uint32_t motion_timeout_ticks(uint8_t operation)
{
    return operation == FW_OP_JOG ? TICKS(JOG_PERIODS) : TICKS(ABSOLUTE_PERIODS);
}

uint32_t motion_abort_ticks(void)
{
    return TICKS(ABORT_PERIODS);
}

uint8_t motion_phase(void)
{
    return state.phase;
}

uint8_t motion_cause(void)
{
    return state.cause;
}

int motion_recovery_required(void)
{
    return state.recovery;
}

/* Live geometry, Test 10 context and conflicting ownership, valid while moving. */
static int context_ok(void)
{
    return read8(TEST_SELECTED) == TEST10 && read8(MODE) == DIAGNOSTIC_MODE &&
           (read8(FULL_MODE) & 0xfeu) == DIAGNOSTIC_MODE &&
           read8(INTERLOCKS) == MAIN_DOOR && read8(ACTIVE_REELS) == FW_REELS &&
           read8(REEL_STOPS) == FW_STOPS && platform_geometry_ok() && !game_is_busy();
}

/* Native settled idle: finish/verify done (0x5e41e) and its countdown drained. */
static int motor_settled(void)
{
    uint32_t r;
    if (MMIO32(MOTOR) != MOTOR_IDLE || read8(NORMAL_PENDING) != 0u ||
        read8(COUNTDOWN) != 0u || read8(FLAGS) != 0u || read8(FINALIZE) != 0u)
        return 0;
    for (r = 0; r < FW_REELS; ++r)
        if (read8(REEL_STATE + r) != REEL_IDLE)
            return 0;
    return 1;
}

static int admissible(void)
{
    return context_ok() && !platform_native_fault() && motor_settled() &&
           (MMIO16(INPUTS) & BUTTONS) == BUTTONS && read8(PAYOUT_WAGER) == 0u &&
           MMIO16(PAYOUT_EDGE) == 0u && read8(0x43cd46u) == 0u &&
           read8(0x43cd78u) == 0u && read8(0x43cd6au) == 0u &&
           read8(0x43cd91u) == 0u && read8(0x410232u) == 0u;
}

static uint8_t target(uint8_t stop)
{
    return (uint8_t)((FW_STOPS - stop) % FW_STOPS);
}

/* Caller holds the section; motor long is published last, as natively. */
static void start_absolute(void)
{
    uint32_t (*step_offset)(uint32_t) =
        (uint32_t (*)(uint32_t))(uintptr_t)NATIVE_STEP_OFFSET;
    uint32_t r;
    for (r = 0; r < FW_REELS; ++r) {
        uint8_t t = target(state.stops[r]);
        MMIO8(TARGET + r) = t;
        MMIO8(SENSED + r) = FW_STOPS; /* native unknown/index-search sentinel */
        MMIO8(TARGET_PHASE + r) =
            (uint8_t)((step_offset(t) + read8(PHASE_ORIGIN + r)) % 4u);
        MMIO8(REEL_STATE + r) = MOTOR_ABSOLUTE;
        platform_safe_u8(RECORDED + r, UNKNOWN_STOP);
    }
    MMIO32(MOTOR) = MOTOR_ABSOLUTE;
}

/* Caller holds the section; motor long is published last, as natively. */
static void start_jog(void)
{
    void (*load_timer)(uint32_t, uint32_t) =
        (void (*)(uint32_t, uint32_t))(uintptr_t)NATIVE_LOAD_TIMER;
    int index = read8(SENSED + state.reel) % 21u == 0u;
    uint16_t hold = 0;
    uint32_t r;
    state.selector = read8(SELECTOR);
    state.selector_saved = 1;
    MMIO8(SELECTOR) = state.reel;
    MMIO16(JOG_STEPS) = index ? JOG_STEPS_INDEX : JOG_STEPS_OTHER;
    MMIO32(JOG_TIMING) = index ? JOG_TIMING_INDEX : JOG_TIMING_OTHER;
    for (r = 0; r < FW_REELS; ++r)
        hold |= MMIO16(HOLD_TABLE + 8u * r + 2u * read8(PHASE + r));
    MMIO16(HOLD_MASK) = hold;
    load_timer(0u, 0xffffu);
    MMIO32(MOTOR) = MOTOR_JOG;
}

static void restore_selector(void)
{
    if (state.selector_saved) {
        MMIO8(SELECTOR) = state.selector;
        state.selector_saved = 0;
    }
}

/* Caller holds the section. The reported failure stays FW_FAULT and the module
 * keeps ownership. Only the state this module published is stopped: once a
 * native fault is visible, native recovery (0x5ff76 may publish state 0x10) owns
 * the motor, so no stop is written over it. */
static void fail(uint8_t cause, uint32_t now)
{
    uint32_t published = state.operation == FW_OP_JOG ? MOTOR_JOG : MOTOR_ABSOLUTE;
    uint32_t r;
    state.cause = cause;
    state.status = FW_FAULT;
    state.since = now;
    state.phase = MOTION_ABORTING;
    if (platform_native_fault() || MMIO32(MOTOR) != published)
        return;
    for (r = 0; r < FW_REELS; ++r)
        MMIO8(REEL_STATE + r) = REEL_STOP;
    MMIO32(MOTOR) = MOTOR_STOP;
}

static int stops_match(void)
{
    uint32_t r;
    for (r = 0; r < FW_REELS; ++r) {
        uint8_t t = target(state.stops[r]);
        if (read8(SENSED + r) != t || read8(RECORDED + r) != t)
            return 0;
    }
    return 1;
}

static void run(uint32_t exiting, uint32_t now)
{
    if (platform_native_fault()) {
        fail(MOTION_CAUSE_NATIVE_FAULT, now);
    } else if (exiting) {
        fail(MOTION_CAUSE_EXIT, now);
    } else if (!context_ok() || (state.operation == FW_OP_JOG &&
                                 read8(SELECTOR) != state.reel)) {
        fail(MOTION_CAUSE_CONTEXT, now);
    } else if (motor_settled()) {
        /* Observed settled state is evidence even if this poll ran late. */
        if (state.operation == FW_OP_POSITION && !stops_match()) {
            state.cause = MOTION_CAUSE_MISMATCH;
            state.status = FW_FAULT;
            state.phase = MOTION_LATCHED;
        } else {
            restore_selector();
            state.status = FW_OK;
            state.phase = MOTION_RELEASING;
        }
    } else if (now - state.since >= motion_timeout_ticks(state.operation)) {
        fail(MOTION_CAUSE_TIMEOUT, now);
    }
}

static void drain(uint32_t now)
{
    uint32_t r, quiet = MMIO32(MOTOR) == MOTOR_IDLE && read8(NORMAL_PENDING) == 0u &&
                        read8(COUNTDOWN) == 0u;
    for (r = 0; r < FW_REELS; ++r)
        quiet = quiet && read8(REEL_STATE + r) == REEL_IDLE;
    /* Native fault fields stay set; draining only means the motor stopped. */
    if (quiet) {
        restore_selector();
        state.phase = MOTION_LATCHED;
    } else if (now - state.since >= motion_abort_ticks()) {
        state.recovery = 1;
        state.phase = MOTION_LATCHED;
    }
}

static void finish_unstarted(uint8_t cause, uint8_t status)
{
    state.cause = cause;
    state.status = status;
    state.phase = MOTION_IDLE;
}

/* Returns nonzero when this Test 10 pass is owned. Caller holds the section. */
static uint32_t step(uint32_t exiting, uint32_t now)
{
    switch (state.phase) {
    case MOTION_PENDING:
        if (exiting) {
            finish_unstarted(MOTION_CAUSE_CANCELLED, FW_INTERRUPTED);
            return 0;
        }
        if (!admissible()) {
            finish_unstarted(MOTION_CAUSE_REJECTED, FW_BUSY);
            return 0;
        }
        state.since = now;
        state.phase = MOTION_RUNNING;
        if (state.operation == FW_OP_JOG)
            start_jog();
        else
            start_absolute();
        return 1;
    case MOTION_RUNNING:
        run(exiting, now);
        return 1;
    case MOTION_RELEASING:
        state.phase = MOTION_IDLE;
        return 0;
    case MOTION_ABORTING:
        drain(now);
        return 1;
    case MOTION_LATCHED:
        return 1;
    default:
        return 0;
    }
}

uint32_t motion_test10_step(uint32_t exiting)
{
    uint16_t sr = platform_irq_save();
    uint32_t owned = step(exiting, platform_ticks());
    /* An unowned exit leaves the loop: 0x5b5fe and the epilogue follow. */
    state.loop = owned || !exiting;
    platform_irq_restore(sr);
    return owned;
}

void motion_test10_exit(void)
{
    uint16_t sr = platform_irq_save();
    state.loop = 0;
    if (state.phase == MOTION_PENDING) {
        finish_unstarted(MOTION_CAUSE_CANCELLED, FW_INTERRUPTED);
    } else if (state.phase == MOTION_RELEASING) {
        state.phase = MOTION_IDLE;
    } else if (state.phase == MOTION_RUNNING) {
        /* Owned passes never branch to the epilogue; keep the inhibit anyway. */
        fail(MOTION_CAUSE_EXIT, platform_ticks());
    }
    platform_irq_restore(sr);
}

uint8_t motion_submit(uint8_t operation, const uint8_t *args, uint32_t length,
                      uint64_t id)
{
    uint8_t result = FW_BUSY;
    uint16_t sr;
    uint32_t r;
    if (!MOTION_QUALIFIED)
        return FW_UNSUPPORTED;
    if (id != 0u && id == state.id)
        return state.status;
    if (id == 0u)
        return FW_BAD_ID;
    if (operation == FW_OP_POSITION) {
        if (length != FW_REELS)
            return FW_BAD_REQUEST;
        for (r = 0; r < FW_REELS; ++r)
            if (args[r] >= FW_STOPS)
                return FW_BAD_REQUEST;
    } else if (operation != FW_OP_JOG || length != 1u || args[0] >= FW_REELS) {
        return FW_BAD_REQUEST;
    }
    sr = platform_irq_save();
    /* Only a running Test 10 loop can claim or cancel the request later. */
    if (state.phase == MOTION_IDLE && state.loop && admissible()) {
        state.id = id;
        state.operation = operation;
        for (r = 0; r < FW_REELS; ++r)
            state.stops[r] = operation == FW_OP_POSITION ? args[r] : 0u;
        state.reel = operation == FW_OP_JOG ? args[0] : 0u;
        state.cause = MOTION_CAUSE_NONE;
        state.recovery = 0;
        state.selector_saved = 0;
        state.status = FW_ACCEPTED;
        state.phase = MOTION_PENDING;
        result = FW_ACCEPTED;
    }
    platform_irq_restore(sr);
    return result;
}

uint8_t motion_current_status(uint64_t id)
{
    return id != 0u && id == state.id ? state.status : FW_BAD_ID;
}
