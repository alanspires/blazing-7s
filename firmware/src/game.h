#ifndef S6000_GAME_H
#define S6000_GAME_H

#include <stdbool.h>
#include <stdint.h>
#include "fw.h"

/* Native game integration: profile activation, per-line wagering, remote
 * bet-and-spin and the one-shot selected outcome.
 *
 * Results are FW_* statuses. A remote request reports FW_ACCEPTED until the
 * native game it started (or that consumed the armed outcome) has returned to
 * an idle wager state, then FW_OK. FW_INTERRUPTED means the request did not
 * do what was asked (refused bet, player interference, ineligible or
 * recovered game); FW_FAULT means a native fault was seen while it was active.
 * Nothing here clears native faults or replays a request after reset. */

/* Boot only, before any hook can run: discards every request, the cache and
 * any profile-change ownership. */
void game_init(void);

/* Profile change protocol (core, through platform ports):
 *   prepare -> durable journal/slot writes -> apply -> durable status -> finish.
 * prepare takes ownership first, then validates native eligibility, a zero
 * configuration sum, native idle with retired motion and timer 2 neither
 * pending nor enabled, and core's active slot (NULL for stock)
 * against the module's cache. Rejection releases ownership; success retains it
 * until finish. While owned, no request is accepted and task 6 dispatches no
 * event.
 *   staged != NULL: a custom commit. Validates the wire profile at offset 0 and
 *     fills the slot's original-configuration baseline (FW_SLOT_BASELINE_OFFSET)
 *     and its context fingerprint (FW_SLOT_CONTEXT_OFFSET): inherited from
 *     active after verifying active's fingerprint against the live context,
 *     or first captured from the live stock configuration when active is NULL.
 *   staged == NULL: a stock restoration from active's baseline, which must
 *     match the live context.
 * Returns FW_OK, FW_BUSY, FW_UNSUPPORTED, FW_INVALID_PROFILE or FW_FAULT. */
uint8_t game_prepare_profile(uint8_t *staged, const uint8_t *active);
/* FW_OK, or FW_BUSY for a change abandoned before any side effect, releases
 * ownership; any other status keeps play inhibited until game_init. */
void game_finish_profile_change(uint8_t status);
/* Writes only owned configuration bytes, and only when the live native context
 * matches the slot's stored fingerprint; otherwise FW_FAULT with nothing
 * written. A slot outside persistent storage, invalid or without zero guard
 * records is FW_INVALID_PROFILE. Completes any torn owned write and the
 * block's zero sum.
 *   boot: startup recovery before native tasks and VIA initialization; no
 *     runtime idle or timer state is assumed. Otherwise a write additionally
 *     requires profile-change ownership and is revalidated against the runtime
 *     admission (idle native game, retired motion, timer 2 inactive): a native
 *     runtime reload has no ownership and faults, without undoing that reload.
 *   (slot, false): apply the committed custom profile.
 *   (slot, true): restore the original configuration from the slot's baseline.
 *   (NULL, false): settled stock; writes nothing, FW_FAULT if custom
 *     configuration is present.
 *   (NULL, true) is a contract violation: FW_FAULT. */
uint8_t game_apply_profile(const uint8_t *slot, bool restore, bool boot);
/* A request is active, an outcome is armed or held, a selected outcome is
 * being copied, or a profile change is owned. Diagnostic motion must refuse
 * while this is true. */
bool game_is_busy(void);
/* Not busy and the native game is quiescent enough to change its profile. */
bool game_idle(void);
/* SAS worker: ARM_OUTCOME, CANCEL_OUTCOME and BET_SPIN. Validates and
 * publishes; native events are injected later by task 6. */
uint8_t game_submit(uint8_t operation, const uint8_t *args, uint32_t length,
                    uint64_t id);
/* FW_BAD_ID unless id is the module's current request. */
uint8_t game_current_status(uint64_t id);

/* Native hook entry points (patch targets; see game_hooks.S for contracts). */
uint32_t game_next_event(void);                 /* 0xa53e, replaces JSR 0xae6a */
uint32_t game_repeat_wager(uint32_t counter);   /* 0x4324e, replaces JSR 0x549c8 */
void game_evaluate_wager(void);                 /* 0x65168, replaces JSR 0x35fdc */
uint32_t game_line_wager(uint32_t total);
uint32_t game_bet_one(void);
uint32_t game_start_permitted(void);
uint32_t game_candidate(uint32_t reel, uint32_t caller);
void game_commit(void);

#endif
