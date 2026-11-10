---
paths:
  - "firmware/src/game.c"
  - "firmware/src/game.h"
  - "firmware/src/game_hooks.S"
  - "firmware/tests/test_game.py"
---
# Native game wagering and selected outcomes

## Ownership boundaries

- Follow `firmware.md` for build, patch ABI and CPU-harness contracts;
  `firmware-remote-control.md` owns profile format, protocol and persistence.
  `game_settlement_entry` lives here, but its stack-resource proof belongs to
  `firmware-native-guards.md`. Diagnostic motion belongs to `firmware-motion.md`.
- Preserve inactive stock semantics. Never bypass native interlocks, recovery,
  sensors, accounting or payout routing to make a remote request succeed.

## Whole-line wagering

- For custom profiles, native credits, meters and repeat wagers remain aggregate
  totals: `T = bet_per_line * lines`. Normalize only evaluator scratch to `T/L`,
  including Test 10; retain native award clearing before normal evaluation.
- Keep the native one-credit primitive unchanged. `game_bet_one` expands only
  its BET ONE caller, preflighting the entire increment against credits and cap.
  A coin-made remainder is topped up to the next whole line; physical deposits
  remain actual coin units. Never synthesize physical-coin event 4.
- On a first multiline bet, run native first-wager initialization exactly once,
  after the first credit and before the remaining credits. The native continuation
  only initializes wager one; the refusal adapter must release its caller's lock.
- Refuse indivisible custom SPIN totals and incompatible repeat wagers; do not
  multiply the native repeat/max-bet loop, which already operates in total credits.
- `game_lines` must fail closed on custom pointers without matching runtime cache,
  or mismatched records, line count, aggregate cap or all-lines setting. Such a
  configuration cannot bet/start and evaluates with zero wager, not stock totals.
- Custom normal and diagnostic evaluation use fixed center, top, bottom, then
  two-diagonal order, regardless of line count. Stock retains its special 2/4-line
  ordering. `game_state.identity_order` must remain at offset zero for assembly.
- Preserve native ordered first-match evaluation: a zero-award match ends the
  line rather than falling through. Logical line support is not cabinet alignment
  evidence; enabling lines also requires appropriate payout-record masks.

## Remote admission and native start ownership

- `game_submit` publishes work; task 6 executes it. `game_next_event` preserves
  native event priority and returns availability/count, not the event enum.
  Inject BET ONE (9) and SPIN (1) only after the native reader returns empty.
- Admit remote work only in closed-door ordinary modes 0/2/4, with clear
  interlocks, valid geometry, no faults, recovery, special/alternate transaction,
  native motion or extension motion ownership. Do not clear blockers to admit it.
- BET_SPIN targets a paid aggregate wager, not an additional wager. Reject totals
  above target, indivisible totals, insufficient remaining credits, invalid caps
  or stops, and idle mode with a nonzero wager. Recheck readiness and the expected
  wager at every injected step. Never post idle-mode SPIN to repeat an old wager.
- Unexpected wager changes, refused bets or a posted start returning without
  commit interrupt the request; faults terminate tracking as FAULT. Do not replay
  or refund through invented accounting operations. ACCEPTED lasts until the
  started native game returns to an ordinary wager state; commit is not completion.
- Keep both event filtering and `game_start_permitted` at native normal-start
  entry: direct alternate/special callers bypass the dispatcher. Profile-change
  ownership, motion ownership (including latched failure) and inconsistent custom
  cache block starts. Custom direct starts additionally require ordinary wager
  modes, no alternate transaction and a nonzero whole-line wager.
- Profile ownership excludes task-6 dispatch before idle validation. Retain an
  event dequeued during ownership acquisition and redeliver it once after release;
  dispatch already in flight prevents acquisition. Failed partial profile changes
  keep play inhibited; do not weaken this boundary when changing event handling.

## One-shot selected outcome

- ARM holds one immutable validated triple. BET_SPIN may bind that pending arm;
  supplied stops must match it, never silently replace it. An inline BET_SPIN
  outcome and a separately armed outcome have different failure lifetimes.
- `game_candidate` decides eligibility once at reel zero: native dispatcher
  caller, SPIN event, full running mode 6, and fresh normal-game safety conditions.
  Claim the whole triple; never change its source between reel writes.
- Substitute only the committed-outcome source. Preserve native copy/recovery
  guards, SafeRAM writes and downstream conversion, motion and evaluation of that
  same outcome. Do not rewrite background candidates or independent motor targets.
- `game_commit` consumes only an actual claim after the copy loop. The hook also
  runs when native guards skip copying: an ineligible start interrupts the request,
  rather than leaking its outcome into a later game.
- A failed spin bound to a separately pending ARM leaves it held until CANCEL,
  not eligible for later physical spins. An inline outcome is discarded on failure.
  CANCEL refuses a claimed/bound/running request; it clears pending or held arms.
  `game_init` discards runtime requests and arms; it never replays them after reset.

## Verification boundary

- Exercise changes through `firmware/tests/test_game.py`: its compiled fixture
  applies production `game-` and `private-E0-` patches, including padding, to the
  hash-checked ROM. Keep tests for stock behavior, refusal without debit, whole-line
  evaluation, direct-start blocking, one-shot consumption and interference.
- The fixture fakes counter reads/comparisons, one-credit debit, event reads,
  accounting actions, queue posts, scheduler lock/unlock/delay and motion ports.
  It executes native first-wager continuation, SafeRAM writes, cache building,
  selected-stop copy and evaluator paths with production game/platform/core code.
- Remote-play helpers manually drive native-equivalent mode transitions; they do
  not run a full physical spin. VIA registers are plain memory, not a 6522 model.
  These tests prove neither real debit atomicity nor physical completion, payout,
  battery retention or installation safety.
