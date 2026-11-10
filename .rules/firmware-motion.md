---
paths:
  - "firmware/src/motion*"
  - "firmware/tests/test_motion.py"
  - "notes/reel-control.md"
---
# Diagnostic reel motion

See `firmware.md` for shared firmware constraints, `firmware-game.md` for normal
outcome selection/start inhibition, and `firmware-native-guards.md` for calibration.

## Qualification and ownership

- Keep `MOTION_QUALIFIED=0` until hardware acceptance proves absolute/jog targets,
  optical feedback, stop/drain behavior and deadline margins. Default capabilities
  are zero and submissions return `FW_UNSUPPORTED`; compiled hooks or passing CPU
  tests do not authorize enabling motion or installation.
- Operate only inside an already-running native Test 10 polling loop (decimal 10,
  selector `0x0a`). Setting mode/selector bytes is not native diagnostic entry.
  Do not enter Test 10 from the SAS worker, add a queue consumer, or expose native
  entry/recovery routines as serial calls.
- SAS submission validates and copies; task 7 alone starts/polls motion. Admission
  and publication, claim and motor setup, and failure transitions use bounded
  saved-SR sections. Never wait/yield while masked or call stock blocking jog/
  absolute routines; retain the native motor ISR and optical verification.
- Recheck `admissible()` when claiming, not only at submission: Test 10 context,
  main-door-only interlocks, installed geometry, no game ownership/native fault,
  fully settled motor, released CHANGE/SPIN, no diagnostic wager/edge or excluded
  special-game latches. Reject conflicts; never clear native state to gain entry.

## Test 10 hook contract

- Keep all three patches in `firmware/patches.json` aligned with the assembly:
  poll, cleanup, and return epilogue. The epilogue covers returns bypassing both
  other hooks; it cancels pending requests and ends loop availability.
- Unowned poll replays the original word input load, preserving D0's upper word,
  X and other registers while reproducing N/Z/V/C. Owned poll redirects to the
  native cooperative loop, skipping physical selector/jog and payout-test paths.
- Unowned cleanup tail-calls native cleanup with its original stack. Owned cleanup
  yields through the native two-tick delay before looping, even for an invalid
  queue; it must not release ownership or enter payout evaluation.
- Preserve epilogue registers, CCR and stack exactly. A selected-test byte alone
  is insufficient admission: a poll must have established loop availability.

## Results and failure lifecycle

- `IDLE → PENDING → RUNNING` reports `FW_ACCEPTED`, not physical completion.
  A successful poll records `FW_OK` in `RELEASING` but still owns that pass;
  the following pass releases to native handling. `motion_is_busy()` includes
  pending, releasing, aborting and latched states, preserving start inhibition.
- An unstarted exit returns `FW_INTERRUPTED`; failed claim returns `FW_BUSY`.
  Neither writes motors. After setup, failures report `FW_FAULT`: native fault,
  exit, or context/selector change precedes settled-state checking. Settled
  evidence precedes timeout, even on a late poll; an absolute mismatch latches.
- Deadlines use unsigned wrapping native-tick subtraction and the derived
  `motion_timeout_ticks()`/`motion_abort_ticks()` bounds, not wall-clock guesses.
  `fail()` requests stop only when no native fault is visible and the motor still
  has this operation's published state. Never overwrite native recovery/dwell,
  manufacture idle, clear fault fields, or repeatedly issue stop requests.
- `ABORTING` drains to `LATCHED`; abort expiry additionally sets recovery-required
  without replacing the original cause. Neither later idle nor drained failure
  releases ownership. Only boot/native reinitialization may call `motion_init()`;
  it discards module state, not native faults, and is not a remote recovery API.
- The module retains only its current ID/result; duplicates return that status
  without restarting. Do not infer a durable request journal from this local FSM.

## Native geometry and completion

- Require three reels and 22 physical stops, zero-based `0..21`, not the weighted
  selector's 32 slots. Convert absolute targets once to `(22-stop)%22` in firmware.
  Jog is one native jog of a validated reel, not arbitrary signed steps.
- Publish the shared motor long last after bounded setup. Absolute setup uses
  the unknown sensed sentinel and SafeRAM unknown recorded stop; preserve native
  phase calculation. Jog's argument and ISR selector must agree; preserve its
  native 9/10-step choice and restore the saved selector only after idle.
- `motor_settled()` requires shared/per-reel idle, drained countdown, no queued
  normal motion, flags or finalization marker. Do not clear `FINALIZE` to qualify:
  native finalization can alter committed outcomes. POSITION also compares both
  sensed and SafeRAM-recorded stops with converted targets; JOG success does not
  certify an absolute stop. Neither ACK nor motor stop/finish state is completion.

## Verification boundary

- Run `firmware/.venv/bin/python -m pytest firmware/tests/test_motion.py` for motion
  changes. Preserve original-ROM setup equivalence, hook ABI/no-request behavior,
  atomic admission/publication, exit bypasses, wraparound deadlines and fault
  ownership tests. The qualified variant is test-only.
- These tests execute compiled hooks/FSM and native setup but inject settled RAM
  and tick events; they do not simulate motors, optics or interrupt timing.
  Hardware qualification remains separate from CPU success.
