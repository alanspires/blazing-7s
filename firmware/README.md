# Experimental S6000 serial-control firmware

**Experimental, not generally installation-qualified.** The offline build gates
below preceded a separately approved, limited live bring-up using the user's
verified spare chips. See [the live record](../notes/firmware-bringup.md) for the
two stock spins, idle-restart retention, profile round-trip and measured host
reply-deadline issue. Those checks do not close the remaining physical gates.
The build manifest deliberately remains `install_ready: false`.

This targets only the pinned S6M00002B5 main ROM and installed E595211X-05
personality. It adds private SAS long poll `0xE0`; the stock firmware cannot run
these extension commands. Original ROM files remain unchanged.

## What is implemented

- JSON profile upload, activation, read-back verification and stock restoration.
- One to five logical paylines, one to three credits per line, at most 15 total
  credits per spin. Native meters retain the total wager.
- Configurable stop weights and eleven ordered payout records, with nominal
  awards from 0 to 1500 per record. Symbols and physical reel strips stay fixed.
- Remote bet-and-spin and a one-shot selected outcome for an eligible normal game.
- Durable request identities, exact-repeat checking, interrupted-action reporting,
  guarded profile slots, and recovery of interrupted profile changes.
- Pre-boot first-install commissioning: an exact, durable local archive (on the computer running `tools/sas`) before
  explicit initialization, interrupted-write recovery and refusal to erase known
  request history. See [PROVISIONING.md](PROVISIONING.md); hardware remains unqualified.
- Diagnostic positioning/jog state machines and native hooks, **disabled in the
  default image** (`MOTION_QUALIFIED=0`). INFO does not advertise these capabilities;
  they must not be enabled before motor/timeout/abort hardware qualification.

A zero-award matching record ends that line without paying; it does not fall
through. Nominal math excludes external bonus/progressive amounts. A logical line
is not proof that the corresponding presentation on the cabinet glass is correct.
When enabling extra lines, deliberately configure the normal records' line masks;
progressive flags remain restricted to the center line.

## Build outputs

From the repository root, after the setup in [toolchain.md](toolchain.md):

```sh
firmware/.venv/bin/python firmware/build.py firmware/patches.json
```

Outputs are in `firmware/build/`:

- `main.bin`: merged 512 KiB CPU image.
- `U28_even.bin`, `U43_odd.bin`: exact 256 KiB (262,144-byte) even/U28 and
  odd/U43 lanes for the Winbond W27C020/W27C02 DIP32 spares; no mirroring or padding.
- `manifest.json`: input/output hashes, exact patches, symbols, compiler details,
  checksum verification and the explicit not-install-ready status.
- `ext.elf`, `ext.map`, `ext.dis`, `ext.stack`: inspection and stack-usage artifacts.

The original boot checksum remains enabled. Per-function stack reports do not
establish total native call-chain or interrupt stack bounds.

## CLI

These two commands are usable now without serial access or SSH:

```sh
tools/sas profile-stock > profile.json
tools/sas profile-preview profile.json
```

The wrapper runs online commands on the Linux SAS host over SSH. It requires
`SAS_HOST` (the SSH destination) and `SAS_PORT` (the adapter's serial device on that
host), with no defaults; `SAS_DIR` is optional (default `blazing-7s`). Setup is in
[`notes/sas-j10.md`](../notes/sas-j10.md), **SAS host setup**. These commands also
require the modified firmware and separate authorization for experimental live use:

```text
tools/sas extension-info
tools/sas extension-status
tools/sas profile-upload profile.json
tools/sas profile-download
tools/sas profile-restore
tools/sas outcome-arm S0 S1 S2
tools/sas outcome-cancel
tools/sas game-spin BET [S0 S1 S2]
```

Stops are zero-based `0..21`; BET is credits **per line**. Only profile COMMIT and
RESTORE use a two-second first-byte reply budget; ordinary SAS keeps its default.
A missing reply means an uncertain outcome, not permission to repeat with a new ID. Check status and
reconcile first. An explicit `--id` repeats only that exact latest request without
re-executing it. ACCEPTED means pending, not completed.

**Restore stock and verify it before changing other native game options or removing
the extension ROMs.** A committed custom profile depends on extension code and
persistent payout pointers. If restoration is unavailable, do not swap ROMs and
resume play; use a qualified recovery procedure.

## Offline verification recorded

Published image SHA-256:
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`.

The records below were made with one toolchain installation. A fresh build with the
documented toolchain reproduces `main.bin` and the two chip images; `ext.elf`,
`ext.map` and the manifest depend on the toolchain build and can differ
(`toolchain.md`, **Outputs and reproducibility**).

### Winbond packaging gate

The fixed-target builder now emits exact 256 KiB chip lanes and manifest format
`s6000-offline-build/2`, with the intended device/signature/capacity metadata.
**Independent packaging review passed; the exact verified artifacts are published
locally in `firmware/build/`.** The merged firmware is unchanged; `PROGRAMMING.md`
records the current lane and manifest hashes.

- All53 builder tests passed; the packaging regressions reject mirrored output.
- Two fresh production builds from different checkout/output directories reproduced
  all eight artifacts. Main and all four compiled artifacts exactly match the
  previously stack-qualified image; only packaging and its metadata change.
- Full-lane interleaving reproduces all of main.bin without slicing/truncation.
  All20 input hashes, output hashes/sizes, checksum and unchanged originals passed.
- Device metadata describes the intended target, not verified physical chips.
  `install_ready` stays false; no hardware operation accompanies this work.

### Guarded native-task stack gate

The implementation, complete root composition and all seven maintained documents
have independent approval under explicit extension-live premises. Main published
the exact eight tested artifacts without rebuilding, verified all copied bytes and
20 production inputs, and reconfirmed unchanged originals, checksum and chip lanes.
All three documented reproduction scripts passed. This is local offline publication,
not installation approval.

- **529 tests plus six subtests passed**, including 67 settlement-guard cases.
  The subsequent allocator-fixture cleanup passed its affected regression. The
  CPU harness's cached-stop defect has a regression that failed before its fix.
- Native task slots grow from 2048 to 3072 bytes through the configuration word
  at `0x27b6`; all 18 slots fit the unchanged kernel pool. Allocation, free-list
  lifecycle and stack/SRAM separation are checked in `TASK-STACK-AUDIT.md`.
- Every supported settlement entry at `0x589ba` now checks the owning allocation
  before its prologue. A successful check preserves registers/SR and native
  behavior; refusal restores its scratch and tail-enters native TRAP1. It does
  not retry, roll back accounting or turn off motor power.
- Positive root, selector and continuation coverage gives initial non-IRQ peaks
  of 498 bytes for native covered work and 648 for early bootstrap. First
  settlement entry is bounded by 268 native / 58 bootstrap bytes. Adding the
  reviewed 1904-byte interrupt allowance keeps these below their allocations.
- Admitted settlement is bounded separately at **3072 native / 8192 bootstrap
  bytes including IRQs**, without assuming a finite number of financial retries.
  The complete selector/root proof is in `INITIAL-ROOTS.md`; local ledgers and
  induction are in `NATIVE-STACK-LEDGERS.md` and `SETTLEMENT-GUARD.md`.
- Private E0 remains bounded at 2928/4096 bytes; its native entry/exit peak remains
  1964 bytes, now compared with the published 3072-byte native allocation.
  Timer, bus, valid-context and filtered-terminal-IRQ7 premises still apply.
- The published image has 41 patches, 19,541 extension bytes and zero boot word
  sum. It retains `install_ready: false`. Its original mirrored chip packaging
  is superseded by the separately verified Winbond packaging gate above; runtime
  code and the conditional stack proof are unchanged. Physical qualification remains separate.

This is not a bound on every untouched stock-native temporary peak, financial
termination, corrupt contexts or hardware behavior. The following sections record
**earlier revisions**; their open stack questions are superseded only within the
explicit scope of the guarded gate above.

### Earlier serial-cancellation gates (b2 image)

- **123 targeted tests plus six subtests passed**: platform/calibration/builder/
  host SAS and native-kernel/CPU gates. Independent review approved the source,
  tests and released evidence; Ruff passed.
- Native serial reconfiguration now closes core admission, drains an active
  request through a private native mailbox, then preserves the original
  delete/init/create order. Cleanup precedes notification because the woken
  drainer can delete the sender before its post wrapper returns.
- Both reproduced journal/publication failures now pass in both mailbox orders.
  Repeated drains, closed admission and cancellation during stuck post-core TX
  are covered. Eight deliberately broken temporary-build controls were rejected.
  Controlled scheduling and disabled ports are fixture limits, not hardware proof.
- The five-instruction E0 stack bridge is unchanged. Its worker frame grows by
  eight bytes but remains within the reviewed 1024-byte allowance, preserving
  the conditional E0 envelopes below. The Task7 wrapper adds 16 bytes at
  original callee entries; its then-open caller envelope is included in the
  guarded native-task gate above.
- All20 build inputs, including the correct M68000 libgcc variant, and eight
  published artifacts match the reviewed snapshot. There are38 patches,19437
  extension bytes and zero boot word sum. Original ROMs are unchanged and
  `install_ready=false`; the chip outputs still have mirrored 512 KiB packaging.

Private E0 stack gates, before the cancellation change:

- 221 affected platform/motion/commissioning/game/host cases and seven subtests
  passed across the corrective runs. Three real-kernel tests and two existing
  CPU-model tests also passed. Independent source, test and stack-ledger review
  approved this scoped change.
- E0 switches to a private 4 KiB stack before entering C and restores the native
  caller stack afterward. Native pool allocation and TCB stack-top fields are
  unchanged. Commissioning uses this storage only in its earlier pre-kernel phase;
  motion state moved below the commissioning record.
- At that revision, conditional envelopes were **2,928/4,096 bytes privately**
  and **1,964/2,048 bytes at native entry/exit**, leaving only 84 native bytes.
  The guarded candidate changes native capacity, not these peaks. New entry/exit
  helpers still require accounting. `IRQ-AUDIT.md` records the worker/IRQ ledgers
  and hardware premises.
- Native SC12 blocking/resumption, SC1 deletion/SC0 recreation and IRQ5 boundary
  tests execute the real kernel, with explicit 68000 exception entry/return
  modeling. They detect broken stack switching and stale caller-SP reuse; they
  do not model physical timing or repair cancellation consistency.
- All 20 production inputs and eight published artifacts were verified without
  rebuilding the passed image. Original ROMs are unchanged, boot sum is zero,
  there are 37 patches, and `install_ready` remains false.
- **That gate closed E0 only.** Task6, Task7 and boot/restore coverage was still
  outstanding then; the guarded native-task gate above supplies its separate
  scoped proof. Stack switching alone did not fix cancellation; the serial drain
  does. Hardware checks remain open.

Earlier slot-workspace gates:

- 52 core tests and 15 selected game/host/commissioning integrations passed;
  scoped review approved the change. The 512-byte temporary slot now belongs
  to core runtime, using 918 bytes of its 1024-byte reservation.
- Emitted `fw_process` fixed residency fell from 572 to 112 bytes, but the
  checked C/native counter path still retained 644 bytes on the native stack.
  The buffer change alone did not establish capacity; the private-stack design
  and refined interrupt ledger above supersede the earlier estimates.
- An offline task-abandonment probe also reproduced stale request state around
  durable journal publication. Moving a buffer did not fix that separate
  correctness issue; the current serial drain closes the reproduced failure.
- `install_ready` remains false. No physical programmer or machine test is claimed.

Earlier calibration gates:

- 30 calibration, 27 platform and 48 build tests passed; two scoped reviews
  approved the hooks/ABI and padded-call builder changes.
- Boot accepts virgin zero or calibration `0x0060..0x0800`. Candidate updates and
  initial T2 arming require the strict range. Rejection takes native TRAP1's
  nonreturning fatal halt, **not coil power-off**. No timer value is clamped.
- Allowed paths match native registers, SR, SP and stored/timer values. Initial
  T2-arm execution is not cycle-identical. Fresh control-flow inspection shows
  the guard branch bypasses both recursive handoff checks; the conditional
  recursive-prefix argument still depends on the stated bus/timer premises.
- The production image has 37 patches, 19,097 extension bytes and zero boot sum;
  `install_ready` remains false. Winbond packaging is complete; see `PROGRAMMING.md`.
- **Earlier stack warning:** pre-admission journaling retained 1,068 bytes at an
  IRQ-open point, and a candidate nested-interrupt sum exceeded the native 2 KiB
  allocation. This was not a demonstrated overflow. The later private-stack
  qualification above supersedes that warning for E0 under its stated premises.

Earlier runtime-admission gates (before the calibration hooks):

- 82 game and 2 host-to-firmware SAS tests passed after consolidating final
  admission with the shared idle predicate, including zero wager. The unchanged
  platform and motion gates passed 27 and 81 tests respectively.
- Runtime profile changes require prepared ownership, normal motion completion
  sentinels and timer 2 neither pending nor enabled. The final check runs under
  scheduler exclusion, before the write batch, with only a short saved-SR mask.
  Register tests verify byte-only IFR/IER reads and no timer acknowledgment.
- Early boot recovery uses an explicit startup window, closed by the dedicated
  `0x1906` hook before VIA initialization/kernel start. A runtime native reload
  cannot gain this bypass; active-profile reapplication without ownership faults.
- Both scoped reviews approved. Two independent builds reproduced all eight
  artifacts; originals are unchanged, the boot sum is zero, there are 35 patches
  and 19,005 extension bytes. Ruff and whitespace checks passed.
- The full native stack envelope is **not** qualified: active-motion IRQ6 has a
  recursive timer-service path that general E0 requests can encounter. Its timing
  bound and remaining communication-router paths are tracked in `IRQ-AUDIT.md`.

Earlier restoration gates (before the admission guard):

- 47 core, 64 game and 2 host-to-firmware SAS tests passed. These cover an
  inherited 177-byte original baseline, interrupted restoration at every modeled
  bus write, exact configuration restoration and native IRQ4 coin-output
  equivalence at each cut point. The full native loader is no longer called.
- Damaged operation markers cannot hide a newer committed profile/restoration
  record or offer its ID again. Dropped byte, pointer-half and checksum stores
  fault before cache rebuild/publication; each regression failed before its fix.
- Two focused reviewers approved the final fixes. The earlier shared-interface
  change also passed platform, commissioning, build and motion gates.
- Two independent builds reproduced all eight artifacts byte-for-byte, including
  the manifest. Chip-lane interleaving/mirroring and unchanged original ROMs were
  checked. There are 35 patches, the boot word sum is zero, and the extension is
  18,781 bytes. `install_ready` remains false. The 512 KiB chip packaging still
  needs replacement for the user's 256 KiB Winbond spares.
- Ruff and whitespace checks passed. Remaining interrupt/stack qualification is
  tracked separately in `IRQ-AUDIT.md`.

Earlier commissioning gates (before the restoration change):

- 74 compiled persistence tests: archive matching, each modeled word-write cut,
  readback failures, READY/DONE recovery, corrupt/existing state refusal and
  preservation of the native operator-clear recovery route.
- 12 compiled boot/UART tests: real gate/continuation, private stack, AC-fail guard,
  bounded storage writes, framing/error recovery and register initialization.
- The real host archive workflow through compiled framing/core/adapter passed,
  including both image CRCs and non-erasure of a later acknowledged request ID.
  This caught and corrected a personality hash-range error: the pinned image is
  64 KiB, not the entire 128 KiB address window. No unverified mirror was added.
- 17 host tests plus 115 subtests passed, including real archive filesystem writes;
  3 Mac wrapper tests passed against local SSH/rsync process fakes.
- The shared CRC/word-writer change passed 35 core and 25 platform tests. Subsequent
  native-clear eligibility logic is covered by the commissioning gates above.
  Both existing host-to-firmware SAS profile integration tests also passed again.
- Three area-scoped reviews passed. Ruff, shell syntax and whitespace checks passed.
- The commissioning-only build had 35 patches, zero boot word sum and 17,865
  extension bytes; it was independently reproduced before the restoration change.
- At that revision, the conservative sum of emitted C frames in core/commission/IO/platform,
  plus one return address per function and an exception frame, is 2,790 bytes
  against the 4,096-byte private stack. The pre-kernel path has no recursion and
  calls no native task/profile/action routines. This does not bound normal native
  gameplay stacks or establish hardware exception timing.

Earlier game gates, before commissioning was added: the broad offline run passed
270 tests and 358 subtests; review-found wagering/admission/motion defects were then
fixed with regressions. The affected game/host gate passed 58 tests (56 game,
2 integration), and diagnostic motion passed 81 tests. Those game/motion sources
were not changed by commissioning.

The relevant commands are:

```sh
firmware/.venv/bin/python -m pytest -v firmware/tests tools/test_sas_firmware.py tools/test_sas_commission_firmware.py
firmware/.venv/bin/python -m pytest tools/test_sas_profile.py tools/test_sas_extension.py tools/test_sas_cli.py
firmware/.venv/bin/python -m pytest firmware/tests/test_game.py tools/test_sas_firmware.py
firmware/.venv/bin/python -m pytest firmware/tests/test_motion.py
firmware/.venv/bin/python -m pytest firmware/tests/test_serial_reconfigure.py firmware/tests/test_e0_kernel.py firmware/tests/test_cpu_status.py
firmware/.venv/bin/python -m pytest firmware/tests/test_commission.py firmware/tests/test_commission_io.py tools/test_sas_commission_firmware.py
firmware/.venv/bin/python -m pytest tools/test_sas_commission.py tools/test_sas_commission_wrapper.py
ruff check tools/sas_commission.py tools/test_sas_commission.py tools/test_sas_commission_wrapper.py tools/test_sas_commission_firmware.py
ruff check firmware tools/sas_profile.py tools/test_sas_profile.py tools/sas_extension.py tools/test_sas_extension.py tools/test_sas_firmware.py tools/sas.py
bash -n tools/sas
git diff --check
```

These are compiled 68000 CPU tests, not a machine simulator. Native ROM routines
run where practical; explicit fakes replace UART, RTOS, physical inputs and some
accounting leaves. The end-to-end integration tests exercise the real host codec,
SAS framing/CRC, compiled platform/core/game and native configuration routines.
They do not establish physical serial timing, scheduling, payouts or reel safety.

## Installation blockers and operating limits

1. **First-install commissioning is implemented offline, not hardware-qualified.**
   Qualify the actual capture/initialization transport, AC-fail/write inhibition and
   native boot transition without risking existing credits or request history.
   [PROVISIONING.md](PROVISIONING.md) records the procedure and remaining checks.
2. Qualify battery retention and physical word-write atomicity for the reserved
   SRAM, its address decoding, and the original RAM/stack allocation assumptions.
3. Complete physical spare-chip identification and electrical/timing qualification.
   [PROGRAMMING.md](PROGRAMMING.md) records the user's W27C02/DA85 spares and
   successful full-lane readback comparisons; these do not establish device
   authenticity or board margins. Preserve originals; do not erase them.
4. The reproduced stock-loader/IRQ4 transient is fixed with owned-field baseline
   restoration and native-equivalence regressions. E0 and the guarded image's
   covered non-E0 task/boot paths have conditional software bounds, not whole-stock
   stack or hardware qualification. Qualify physical interrupt/watchdog/serial
   timing, scheduler exclusion, accounting and power-loss recovery. The scoped
   serial-cancellation and guarded native-task software/publication gates are closed.
5. Qualify normal spins, line presentation, sensors, payouts and external awards.
   Diagnostic motion additionally needs proven absolute/jog targets, stop/drain
   behavior and deadline margins before enabling its capabilities.

Custom profiles reject unsupported alternate/special normal-start paths. Whether
refusing special respins is appropriate for this personality remains a hardware/
behavior acceptance question. MAX BET with insufficient credits can leave a wager
not divisible by the line count; SPIN is then refused until BET ONE or real coins
top it up. Power loss during the three native outcome writes can leave a mixed
triple for native recovery; the extension never replays the interrupted request.
A latched profile failure inhibits event dispatch until reboot. Motion failures
retain ownership and inhibit normal starts; no native fault is cleared to escape.

[INTEGRATION.md](INTEGRATION.md) defines the ABI, RAM layout, startup ordering and
recovery contracts. Offline implementation and packaging are complete; limited
live checks are recorded separately. Further physical qualification requires
separate approval; this is not a finished replacement release.
