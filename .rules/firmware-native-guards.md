---
paths:
  - "firmware/tests/test_e0_kernel*.py"
  - "firmware/tests/test_serial_reconfigure*.py"
  - "firmware/tests/test_settlement_guard*.py"
  - "firmware/tests/test_task_stacks*.py"
  - "firmware/tests/test_calibration*.py"
  - "firmware/tests/kernel.py"
  - "firmware/IRQ-AUDIT.md"
  - "firmware/SETTLEMENT-GUARD.md"
  - "firmware/NATIVE-STACK-LEDGERS.md"
  - "firmware/TASK-STACK-AUDIT.md"
  - "firmware/INITIAL-ROOTS.md"
  - "firmware/src/platform.c"
  - "firmware/src/platform_hooks.S"
  - "firmware/src/game_hooks.S"
---
# Native resource guards

See `firmware.md` for firmware-wide requirements, `firmware-remote-control.md`
for request semantics, `firmware-game.md` for game ownership, and
`firmware-commissioning.md` for pre-kernel stack ownership. This rule covers only
resource guards in the shared adapters, not those flows' business behavior.

## Allocation and settlement admission

- `native-task-stack-capacity` changes only the initialization word at `0x27b6`
  to 3072 bytes per native slot. Keep the separate reserve at `0x27a8`, pool,
  allocator and TCB ownership distinct; never change capacity in a running kernel.
  Recheck queue/slot exhaustion and recycling when allocation inputs change.
- `game_settlement_entry` must intercept every supported `0x589ba` entry before
  its original prologue, using JMP plus padding, not an extra JSR return.
  Use allocated top `TCB+0x40`, never saved SSP `TCB+0x38`. Bootstrap SP in
  `[0x436000,0x438000)` takes precedence even when CURRENT is nonnull.
- Admission allows equality at 2232 remaining bytes. Both exits remove all ten
  scratch bytes and restore registers/full SR. Success replays the original
  108-byte prologue and continues at `0x589c2`; refusal tail-enters
  `platform_fatal` with the original caller stack shape.
- The reserve is a composed proof, not a tunable safety margin: covered ceiling
  328, next-check growth 120, scratch 10 and conditional IRQ allowance 1904.
  First-entry prefixes, recursive refusal and ancestor unwind need separate
  accounting. Never replace this induction with a finite financial-round limit.
- Fatal means native TRAP1 with its six-byte exception ABI, not a bare jump into
  its handler. It can leave settlement unfinished; it does not roll back, clear
  accounting, retry, fabricate success or de-energize the reel-drive latch.

## Private E0 and serial cancellation

- `platform_sas_handler` switches stacks before any C prologue and restores the
  caller SP afterward, without changing IPL or TCB allocation metadata. Only one
  nonrecursive supervisor E0 callback may own the private stack. Commissioning
  cannot resume after handing this same arena to runtime E0.
- The kernel saves the actual selected SSP; blocking and IRQ return must preserve
  private frames. Deleted owners must never resume. A replacement overwrites
  abandoned frames and saved SP; a stale saved pointer is not a busy flag.
  Other SAS commands must not borrow E0's private allocation. Bound both windows:
  the reviewed peaks are 2928/4096 private and 1964/3072 native bytes; E0 evidence
  alone does not qualify Task6, Task7 or bootstrap.
- Stack reuse does not make transaction cancellation safe. `sas_core_begin/end`
  surround core processing; `platform_reconfigure` atomically closes admission
  and captures whether it owes a wait, then restores SR before SC9. Do not hold
  scheduler exclusion or an IRQ mask across the drain.
- A captured wait consumes exactly one token even if active has since cleared.
  Clear ownership and capture notification before SC8: waking Task7 can delete
  the sender before its post wrapper returns. Kernel status outputs are words.
- Require an empty mailbox before closing and reopening; reset working state in
  `platform_boot`. Keep admission closed across native delete/init/create
  (`16682 → 15836 → 152ec`). Closed-gate requests NACK without entering core.
  Invariant/token/status failures go to TRAP1, not retries or evidence clearing.
- The barrier ends at core return, not UART TX or asynchronous game completion.
  Stuck post-core TX remains cancellable; do not extend the drain to await it.

## Calibration and conditional proof

- Boot permits virgin zero or calibration `0x0060..0x0800`; candidate stores and
  initial T2 arming require the strict interval. Check the actual candidate word,
  including ADDI overflow, before storing. Never clamp or substitute timer values.
  Accepted hooks preserve native ABI and values, not cycle-identical timing.
- Calibration bounds support the IRQ6 cross-call proof only with the documented
  timer model, bus limits and constant cross-call calibration. They are not board
  measurements. Preserve valid objects/selectors, intact supervisor contexts,
  disabled optional kernel hooks/external invokers, one filtered terminal IRQ7,
  and fresh reset SP with no old resumer as explicit proof premises.
- The promise is extension-live/covered work, not every untouched native temporary
  peak, corrupt-context safety, financial termination or installation approval.
  Mutable selectors cannot be assumed stable across waits; absent xrefs and
  balanced returns alone do not prove reachability exclusion or stack capacity.

## Renew evidence when code changes

- `INITIAL-ROOTS.md` owns incoming roots, selector DAGs and complete composition;
  `NATIVE-STACK-LEDGERS.md` owns local emitted costs; `SETTLEMENT-GUARD.md` owns
  admission induction and producer/allocation seals; `IRQ-AUDIT.md` owns IRQ/E0
  envelopes and timer premises; `TASK-STACK-AUDIT.md` owns capacity/gate status.
  Update the relevant authority rather than copying a second proof into a rule.
- Source, patch, linker, compiler/options or libgcc changes require fresh emitted
  frame/instruction and final-image producer evidence before reusing old bounds.
  Include retained arguments, returns, SR/register saves, native descendants and
  IRQ-open windows, not just `.stack` totals. Final linked bytes matter: native
  indexed dispatch loads overlap extension ROM. Recheck bypass domains even for
  layout-only changes; changed roots/selectors require renewed composition and
  the applicable `INITIAL-ROOTS.md` **Durable reproduction** checks.
- Run affected CPU gates: `test_task_stacks.py` for capacity/allocation;
  `test_settlement_guard.py` for threshold/ABI, negative controls, IRQ/fatal and
  final-image dispatch; `test_e0_kernel.py` for switch/block/delete/IRQ behavior;
  `test_serial_reconfigure.py` for both publication cuts and mailbox orders,
  repeated drains, closure and stuck TX; `test_calibration.py` for calibration
  boundaries/native equivalence/fatal sink. Shared fixture edits affect all these.
- Published-image approval is pinned by `SETTLEMENT-GUARD.md` **Candidate seal and
  offline gates**, not inherited by a fresh build. Compare actual hashes and
  production inputs; packaging-only proof reuse requires identical runtime and
  compiled artifacts. Historical 2048-byte comparisons are not current capacity.
  Offline bounds do not close physical gates.
- `kernel.py` executes original VRTX instructions with production configuration,
  but added test callers make its fixture image non-production. Only verified
  TRAP0/RTE opcode/vector pairs get explicit exception handling; unknown
  exceptions, user/trace returns and instruction faults fail closed. IRQ injection
  is controlled, MMIO is register memory, and serial-drain ports are disabled.
  Settlement tests exercise controlled native stack shapes, not financial-history
  witnesses. These tests cannot replace whole-root proofs or hardware timing tests.
