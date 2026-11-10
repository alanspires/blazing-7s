# Physical reel control: feasibility and implementation boundary

This note is the static investigation of how the stock S6000 firmware moves the reels
and commits a spin's outcome, and where an extension can hook in to do two things from
the host: move the reels directly, and choose the stops of one normal spin. It is for
readers who want to follow or check the firmware extension's design against the
original code. **Status:** the investigation preceded the firmware extension, and its
recommendations were since implemented offline: diagnostic motion in
`firmware/src/motion.c` and `motion_hooks.S`, compiled but disabled
(`MOTION_QUALIFIED=0`); the selected normal outcome in `game.c` and `game_hooks.S`;
and durable request IDs in `core.c`. Live bring-up (`firmware-bringup.md`) completed
one selected-outcome spin; no motor or jog test has been made. Where a proposal below
differs from the code, the code governs; see the **Implemented** remarks in the
selected-outcome, mailbox and motion-handoff sections. The selected-outcome feature is
for a privately owned machine used for entertainment, not for regulated or commercial
machines.

## Sources and terms

Investigated against `roms/s6000_main_S6M00002B5.bin` and the installed personality
`roms/personality_E595211X-05.bin`. Addresses are CPU addresses in
`ghidra/proj/bally.gpr`; personality file offsets are CPU addresses minus `0x100000`.
The installed images were previously matched by SAS ROM signatures (`sas-j10.md`).

- **Personality ROM**: the chip pair holding one game's data (reel geometry, symbol
  table, stop weights, paytable); the main program is shared by many games.
- **Stop**: one of the 22 physical resting positions of a reel, numbered 0–21 here.
  **Stop weights** are the per-stop byte weights from which the game's random selector
  picks each reel's stop; the selector works in a 32-slot index space.
- **SafeRAM**: battery-backed RAM that survives power-off; committed outcomes live there.
- **Test 10**: the operator's reel diagnostic, test number 10 (decimal) in the service
  menus. **Jog** moves one reel by one native jog step sequence; **absolute positioning**
  drives the reels to given stops.
- **Task n**: one of the tasks of the firmware's native VRTX real-time kernel. Task 6
  runs the main game state machine and task 7 the diagnostics. **Yield** means giving
  the CPU back to the scheduler.
- **Motor state long**: the shared 32-bit motion state at `0x4101f6` that the motor
  interrupt dispatches on (table in **Geometry, state and callable ABI**).
- **CCR/SR**: the 68000 condition-code and status registers. A **trampoline** is a
  short jump or call written over original instructions that leads to extension code.
- **The extension**: the experimental firmware added to the main program
  (`firmware/`), reached over the existing J10 SAS connection.

## Goal

The aim was both direct reel positioning and chosen outcomes during normal play,
controlled from the existing host CLI (`tools/sas`). The owner has an EPROM programmer
but preferred a serial route; the broader investigation of maximum bet, paylines,
payouts and probabilities is in `remote-game-config.md`.

## Recommendation

Add a small firmware extension reached through the existing J10 connection. Reuse
Bally's motion engine, optical checks and payout evaluator. This is recommended for
clean separation and realistic verification: the host supplies validated stops, not
motor-coil timing, fabricated wins, or arbitrary RAM writes.

Keep two distinct operations:

- **Position:** run a dedicated diagnostic motion operation; do not commit a normal
  game outcome or invoke the payout test merely to move a reel.
- **Next spin:** supply the stop source for one eligible normal spin, leaving the
  normal wager, motion, verification, payout and jackpot handling intact.

No stock serial command accepting reel targets has been established. SAS `8F`
(`FUN_0002b7de`) reports positions from the game record; it does not set them and is
not proven to report a newly jogged diagnostic position. The default J3 `C`/`P`
handlers examined also do not expose this control. In particular, `CF8` is sound
selection, not remote button input (see **Serial and deployment**).

A button-control bridge could automate Test 10 without replacing ROMs, but it would
require isolated interface hardware and would not by itself select normal-game
outcomes. It is not the recommended solution for the requested combination.

## Geometry and symbols

`FUN_0003756c` loads personality configuration from `0x10d0f8` into `0x43ca1e`.
Its first four bytes are `03 01 16 03`: max wager 3, one payline, 22 stops, three
reels. Use **zero-based stops 0–21**; do not confuse them with the selector's
32-slot space.

The symbol table is `0x10d7d2`, copied to `0x43c87a`, with five bytes per stop:
`symbol[stop * 5 + reel]`. The following IDs and stop triples were checked directly
against the image. Symbol **names are inferred** by matching award patterns to the
cabinet glass, not confirmed by moving the actual reels.

| Inferred symbol | ROM ID | Example stops, left/middle/right |
|---|---|---|
| Blank | `18` | `0,0,0` |
| Single bar | `0A` | `4,4,4` |
| Double bar | `0B` | `5,3,3` |
| Triple bar | `0C` | `3,9,5` |
| Plain seven | `0D` | `1,1,7` |
| Blazing seven | `0E` | `7,7,1` |

Do not promise a particular jackpot amount from those triples. Paytable records at
`0x10d718` include a nominal three-coin Blazing-seven award of 1500 and progressive
flags handled through `FUN_00066286`. Actual awards remain the game's responsibility.
The two-coin row at `0x10d728` contains 700 (`000002bc`), whereas the glass shows 500
(`photos/01-front.jpg`). That discrepancy is unresolved; it is another reason not to
hard-code awards from the glass.

## Direct motion hooks

| Routine / state | Verified role |
|---|---|
| Task entry `0xb080` (not a function in Ghidra), case 10 | Diagnostic dispatcher; calls Test 10, `FUN_0005ab76`. Boot creates this dispatcher as task 7. |
| `FUN_0005ab76` | Reads active-low CHANGE (`FF1014 & 0800`) to advance selector byte `0x410028`; CHANGE plus SPIN (`1000`) invokes the jog routine. Reads physical inputs, not a serial button queue. |
| `FUN_0005f388(reel)` | Jog routine; the argument is a byte in a 32-bit stack slot. Chooses 9 or 10 electrical steps, sets longword motor state `0x4101f6` to `0x40`, then yields until state 1. |
| `FUN_0005fb78(stopBytes*, forceFlag)` | Slow absolute positioning. The ordinary path uses `forceFlag=0`; converts targets, enters state `0x10`, and yields until idle. |
| `FUN_0005d368(reelCount, stopBytes*)` | Normal-speed motion. The first call queues a pointer through `0x410213/0x410214` and returns; the interrupt-side handoff invokes its second stage to start motor state 2. Not a normal-game transaction by itself. |
| `isr_autovector_6`, `0x2718` | Samples `FF1018`; shared interrupt-side motion uses `FUN_0005d6ea` / `FUN_0005e654`. |
| `FUN_0005ec78`, `FUN_0005ee14` | Phase/output and optical-state processing; motor output is `FF100C`, sensed position counters are `0x410152[]`. |

Constraints verified from assembly:

- Jog has no idle check on entry. Its argument chooses timing, but interrupt fragment
  `0x5de50` selects the actual reel using global `0x410028`; both must agree.
- Absolute positioning converts input `p` to `(N-p)%N` at `0x5fbde` before writing
  target `0x410160[]`. Normal-direction spin uses the same conversion. Reverse
  direction has another branch; retain the existing conversion rather than
  duplicating it on the host.
- The ordinary absolute routine can request stopping an already-active motion. A new
  command should reject a busy or conflicting operation instead of using this as
  permission to interrupt an active game.
- Completion depends on mode, motor phase, optical state and dwell, not just a delay.
  Fragment `0x5dad2` accepts main modes `0x12` / `0x16` or motion flags. The native
  transitions and the first version's scope are documented in **Extension boundaries
  in detail**.
- These routines yield and can block. Never call them from the receive interrupt. A
  worker needs exclusive motion ownership and a request buffer that remains valid until
  the interrupt/task handoff consumes it.
- Interrupt fragments defined during analysis (`0x5dad2`, `0x5dc24`, `0x5de50`,
  `0x5ded2`, `0x5e08a`, `0x5e1e2`, `0x5e610`) are jump-table fragments, not standalone
  callable APIs.

Test 10 separately converts sensed stops into `0x41036c[]`, takes wager byte
`0x410371`, and calls `FUN_0003fbbc` to evaluate the combination. Moving reels and
running that payout test must remain separate. The setup manual's **Option 80 — Test 2
and Test 10 Enable** warns that enabling diagnostic meter/jackpot outputs can affect
external progressive accounting; do not change it as part of positioning.

## Normal-spin boundary

1. `FUN_00008a60` updates weighted candidate indices at `0x4101da + 4*reel`. It uses
   `FUN_000380d6` and weights from `0x10d840` / `0x43c91a`, five bytes per row. Each
   installed reel's 22 stop weights sum to 32. This runs in the background; writing its
   candidate buffer opportunistically would race normal selection.
2. `FUN_00061172`, commitment loop `0x615c6–0x615f8`, copies candidate low bytes into
   SafeRAM `0x43e000[]` through `FUN_00000400`. Preservation flag `0x438044` and
   special-game conditions can skip this copy.
3. Continuation `0x616e2` calls `FUN_00036028(0x43e000)`, deriving `0x410146` and
   motion input `0x41011c`; `0x617e4` calls `FUN_0005d368(3, 0x41011c)`.
4. `FUN_00065128` evaluates the committed `0x43e000[]`, maps symbols through
   `FUN_000363f8`, and calls matcher `FUN_000664a0`. Awards accumulate through
   `FUN_000663c2` into `0x43cd08` and continue through the existing payout/jackpot
   logic.

**Proposed hook:** substitute a validated request for the candidate source at the
commitment loop, only when the original normal-game eligibility checks pass. Keep the
SafeRAM writes and everything downstream. Do not merely overwrite physical targets:
that would separate the displayed reels from the outcome used for accounting.

The override should be consumed once, deduplicate transport retries, expose
pending/completed/cancelled/fault status, and not silently survive a restart as a
future forced spin. Precise eligibility around recovery, respins and preserved outcomes
remains an implementation prerequisite. Automatically pressing SPIN or placing a wager
over the CLI is a separate entry-path question; finding the commitment hook does not
establish that remote start is already available.

## Serial and deployment

SAS's six-byte command table begins at `0x744ca`; `sas_rx_byte` rejects entries whose
length is `ff`. For example, `E0` and `F0` point to `FUN_00035f5a` with that
unsupported length in the stock ROM. These were candidate extension slots, **not
assigned commands** in the stock firmware (the extension uses `E0`;
`firmware/INTEGRATION.md`, **Wire protocol v1**). A private opcode needs a check for
protocol conflicts, framing, capability/version discovery, validated request dispatch
and status replies alongside the CLI. A packet ACK is not proof of completed physical
motion.

Default J3 command family `C` is dispatched by `FUN_00022d02`, its `CF` family by
`FUN_00022c28`. `CF8` / `FUN_00022bbc` posts an event from table `0x743d0` (values
1–18) to queue 4. The consumer, the task at `0x609b6` (not defined as a function in
the Ghidra project), invokes `FUN_00060c0c` / `FUN_00060cd4` and honors sound-disable
flags. This does not emulate CHANGE/SPIN for Test 10. The other examined `CF`
handlers concern options, status, meters and time.

The main program is on removable U28/U43 EPROMs, not an established serial-updatable
firmware store (`roms/README.md`). The practical deployment is a newly programmed
pair, keeping the original pair for code rollback, or a compatible EPROM emulator.
A one-time extension can then support serial profile updates, rather than requiring a
new ROM pair for every parameter change. Original 27C4001 dumps contain two repeated
halves: split the merged 512 KiB image into even/odd lanes and reproduce the original
chip capacity/mirroring, then read back and compare. (Superseded for the spares
actually used: they are Winbond W27C02 parts that take exact, unmirrored 256 KiB
lanes; see `firmware/PROGRAMMING.md`.) Replacing code does **not** roll back changed
SafeRAM or meters.

Boot checksum evidence: instructions `0xa32–0xa54` call the word-sum helper `0xc14`
over `000000–0007ef` and `000800–07ffff`; the resulting 16-bit big-endian word sum
must be zero. The original image checks as `0000`. A patched build must retain the
check and satisfy it, not disable it. The image has 28,606 zero bytes from `0x79042`
through `0x7ffff`; this is a space candidate, not proof that the whole region is
unreferenced. Startup also compares program/personality identity strings in
`FUN_000025b0`, with mismatch handling at `FUN_00038a06`; the replacement-version and
SafeRAM implications need resolution before installation.

## Build and acceptance prerequisites

These were the prerequisites set before building; the firmware documents
(`firmware/README.md`, `firmware/INTEGRATION.md`) record which are met.

1. Finish diagnostic entry/exit and fault handling (`0x4101ee`, `0x43c2e4`), normal
   override eligibility, request-buffer placement and ownership. Determine how a host
   requests a spin if remote start is required, not just the next physical SPIN.
2. Build a deterministic, original-hash-checked 68000 patch and private command codec.
   Validate arguments, duplicate requests, busy/fault states and cancellation; test hook
   execution offline, the checksum, even/odd reconstruction and no-request behavior
   before installing anything.
3. Resolve chip-programming access and recovery, including existing machine state. Do
   not assume a SAS dump is a complete SafeRAM backup.
4. On-machine acceptance must confirm actual strip alignment and position feedback, a
   jog/absolute move with no wager or award, and an eligible selected normal spin whose
   wager, displayed stops, award and meters agree. Verify that unarmed spins still use
   the original selector and that retries/restarts cannot cause an extra forced spin.
   Offline CPU execution cannot establish motor/sensor behavior.

Decompiler caveat: Ghidra marks scheduling helper `FUN_00003a1e` as non-returning,
which hides continuations, and it loses register-based arithmetic arguments around
`FUN_00068c4c`. The yielding loops and target conversions above were checked in
assembly. `FUN_0006162c` is an analysis label for a post-yield continuation, not an
independently callable function with the decompiler's inferred stack signature.

## Extension boundaries in detail

This part of the investigation was read-only: it changed no Ghidra symbols, types or
comments, CLI files, ROM bytes or machine state, and sent no serial traffic. In it,
addresses and mode values are hexadecimal even without a `0x` prefix, and a native
routine is often written by its address alone: `364f2(2)` is a call of
`FUN_000364f2` with argument 2, and `34d22(16)` a call of `FUN_00034d22` with
argument `0x16`. **Test 10** keeps its decimal operator-facing number (the selector
byte holds `0x0a`).

### Scope and implementation decision

Assumption: the first direct-position/jog version requires an **already running native
Test 10**, not remote takeover from normal idle. It returns `requires-diagnostic`
outside that state, `busy` for an active movement/test transaction, and `fault` for
unsafe state. No extra password, approval flag or write-enable setting is proposed.
Native door and fault checks are operational gates, not a new write guard. Remote bet
and start are a separate design (`remote-game-config.md`, **Native inputs, counters and
normal start**).

**Recommendation:** service a fixed extension-owned mailbox inside task 7's Test 10
loop. This wins on implementation simplicity and ownership: no new task, stack, queue
consumer or interrupt-side blocking routine is needed. Do not call Test 10 from the
SAS worker. Serial reception only validates and copies a request; task 7 owns motion.
The extension designs in this section are recommendations, not the installed code
(see the **Implemented** remarks).

The success criteria for this static pass were exact original-byte hook sites, source
and motion ABI, completion/fault evidence, and an honest boundary on recovery. The
hook, ABI and completion investigation is complete; safe automatic fault recovery,
allocated mailbox RAM and actual hardware acceptance were **not** complete at the time.

### Native diagnostic entry and exit

- Main task 6 is `0x9f2a` (boot task creation at `0x1a64`); its event dispatch at
  `0xa5b4–0xa5e0` uses `0x77cc6 + (event-1)*120 + state*4`, where state is the full
  byte `0x438030`, not normalized `0x412876`. Queues 1/2/3 are polled by `0xae6a`; its
  empty-queue path yields one tick. Some of these entries are not defined as functions
  in Ghidra; `m68k-elf-objdump` on the host confirms their assembly.
- Native test entry is `0xb3e8`. Event `0x0e` points to it only in states `0x01`,
  `0x15`, `0x1d` (ROM pointers `0x782e2`, `0x78332`, `0x78352`). It tests `0x43c31f`
  bit 0 and returns if clear. It updates `0x41d866–0x41d86b`, saves the prior full
  state to `0x43cda9`, calls `FUN_00034d22(0x16)`, suspends task 5 via `0x3cb0`, posts
  sound event 1 to queue 4, resets test selection, and calls `FUN_0000b490`.
  **Calling only `34d22(16)` is not equivalent entry.**
- `FUN_00034d22` sets normalized `0x412876=argument&fe`; full `0x438030` gets bit 0
  when `0x43c31f != 0`. Thus ordinary native Test 10 with its main door open has
  normalized mode `0x16`, full state `0x17`, and `0x41001e=0x0a`. `0x43c31f` is an
  aggregate door/open-interlock mask: `FUN_00043468` sets bit 0 via
  `FUN_000435da(1)`; other setters use `04`, `20`, `80`, and peripheral open states use
  `08`/`40`. Reject other bits rather than ignoring other doors.
- Task 7 entry `0xb080` is created at `0x1a7a`; it consumes test queue `0x41051c`.
  Test 10 is called at `0xb30a`, bracketed by `0x41001e=0x0a` and clearing that byte
  at `0xb310`. `0xb490` selects/posts other tests. Do not create a second consumer for
  `0x41051c` or inject physical-button state into it.
- Native event `0x0d` in state `0x16/17` reaches `0x5c8d8`, then
  `FUN_0005c334(state&fe)`. Its `case 0x16` clears `0x43cd13`; with no error stack
  (`0x438032==0`) it calls `34d22(0)`, otherwise `34d22(0x14)` and `FUN_0006262c`.
  **Do not restore `0x43cda9` blindly on exit.** Error recovery `FUN_0006268e` also
  manipulates the error stack and preservation flag; it is not an extension fault-clear
  API.

These establish the native transitions, not permission to enter them from arbitrary
idle, recovery or payout states. First-version serial direct motion returns
`requires-diagnostic` unless Test 10 is already at its cooperative polling loop. Do
not expose the native entry/exit routines as arbitrary serial calls.

### Smallest Test 10 integration and original bytes

All addresses equal main-ROM file offsets; the bytes were checked against Ghidra memory
and the original file with SHA-256
`49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a`.

| Proposed site | Original bytes | Contract |
|---|---|---|
| `0x5ac3c` | `30 39 00 ff 10 14` | `MOVE.W (FF1014).L,D0`, directly after the first `364f2(2)` wait/check in the Test 10 loop. Replace in the extension build with a six-byte extension call. |
| `0x5af80` | `4e b9 00 05 b5 fe` | Cleanup `JSR FUN_0005b5fe`. The wrapper must reconcile an owned move before ordinary cleanup. |
| `0x615ce` | `20 7c 00 41 01 dd` | The normal commitment loop's candidate base load into A0; source resolver described below. |
| `0x615fa` | `4a 39 00 43 cd 59` | Post-loop `TST.B (43CD59).L`; a useful completion/consume hook after all SafeRAM writes. |

At `5ac3c`, preserve task 7's registers (`D3.b=reel count`, `D6.b=stop count`,
`D5.b=CHANGE debounce`, others live). The no-request path must reproduce the original
word load, including D0's unchanged upper word and the instruction's CCR effect, then
continue at `5ac42`. With a claimed mailbox request, perform one bounded worker step and
return to `5ac2a`, **not** `5ac42`. A six-byte call trampoline can replace its own saved
return address for this; SP is balanced at this site. This keeps the existing
`364f2(2)` cooperative wait but skips **all** physical selector/jog, bet-selection and
payout-test code during ownership. On completion, return to the loop once before
restoring physical input handling. Do not fall through to the payout test as part of a
remote move.

A queue-invalid/exit condition branches to `5af80` before the worker hook. The cleanup
wrapper must cancel an unstarted request, or drain/abort an owned move, before
tail-calling the original `5b5fe`; it must not release ownership just because the
Test 10 queue became invalid. `5b5fe` writes motor output and yields; it is not itself
proof of a safe completed move.

**Concurrency boundary:** this gives task-local exclusion from Test 10 buttons, not a
global mutex. Recheck mode and doors while moving; a native mode/error transition can
run in task 6 or the interrupt. A shared `motion_owned` / recovery latch must also
reject a remote bet/start request. For a central normal-start guard, `61172` has the
original eight-byte prologue `4e 56 ff fc 48 e7 3c 00`
(`LINK A6,-4; MOVEM D2-D5,-(SP)`); an entry trampoline can return busy before that prologue when
ownership is held, otherwise replay it and continue at `6117a`. The existing early exit
`61920` is reachable only **after** the frame exists. The door-open spin path
`FUN_0005792a` is separate; keep the CLI's closed-door/test-mode gate rather than
assuming this one entry guards everything.

### Geometry, state and callable ABI

- Installed geometry is `03 01 16 03` at personality `0x10d0f8`: three reels,
  **22 physical stops**, maximum index **21**. Motion geometry table `0x6b890` has three
  16-byte records for 22-stop mechanisms with 5, 4 and 3 reels; their first bytes are
  respectively `16 05`, `16 04`, `16 03`. Nothing here supports treating the selector's
  32-slot index space as 32 physical stops. The first version should require exactly
  the installed 3-reel/22-stop geometry, not advertise a generic 255.
- `FUN_0005f388`: push the zero-extended reel index as a 32-bit stack argument; the
  byte is at `0x0b(A6)` after LINK. The actual interrupt reel comes from byte
  `0x410028`; set/check it to the same index under ownership. It tests
  `sensed[index] % 21` to choose 10 versus 9 electrical steps. This is one native jog,
  not a signed arbitrary-step API. D2 is saved; D0/D1/A0/CCR are scratch.
- `FUN_0005fb78`: the stack arguments are the target byte pointer at `8(A6)` and the
  long `forceFlag` at `0x0c(A6)`; push the flag first and the pointer second; the caller
  removes 8 bytes. Use `forceFlag=0`. D2 is saved; D0/D1/A0/CCR are scratch. Do not
  pass temporary receive-buffer storage. Normal-speed `5d368` takes count, then
  pointer, in the ordinary 32-bit argument slots, and initially returns after posting
  pointer `0x410214` and byte `0x410213=1`; interrupt idle fragment `5e08a` consumes
  that pointer, calls the second stage, then clears `410213`. It is not needed for
  first-version direct slow positioning.

| Address / size | Verified meaning |
|---|---|
| `4101f6` / long | Shared motor state: 1 idle, 2 start normal, 4 running, `10` slow absolute, `20` initialization, `40` jog, `80` finish/verify, `800` dwell, `1000` reinitialization. State dispatch uses table `5d9ca/5d9de`. |
| `410183`, `41017c` / bytes | Active reel count and stops/reel; require 3/22 here. |
| `410152[]`, `410160[]`, `41015a[]` / bytes | Sensed stop counters, converted targets, target electrical phases. Absolute target is `(N-requested_stop)%N`. |
| `410186[]`, `41018c[]` / bytes | Per-reel motion states and current electrical phases. `80` means stopped awaiting finalization; 1 is finalized idle. |
| `410192[]`, `410198[]`, `41019e[]`, `410166[]` / bytes | Optical state, stable-state dwell, edge/step count, recorded index-edge count. |
| `4101b6` / word; `410228` / pointer | Jog/initialization step count and timing-table pointer. |
| `410212`, `410213` / bytes; `410214` / pointer | Settle/monitor countdown, normal-motion pending flag, queued target pointer. |
| `410172`, `410165` / bytes | Motion flags; normal-game finalization marker. Require both zero before direct motion. |
| `4101ee` / signed long; `43c2e4` / byte | Motion error code; stored affected reel+1. |
| `41d9c1`, `41d9c2` / bytes | Diagnostic fault latch and reel+1, set by `5ff76` in mode `16`. |

`410165==0` is particularly important: idle finalization can call `FUN_00060310` when
it is nonzero. That routine changes **committed outcomes** `43e000[]` as well as
`43e018[]`/`43e01e[]`, then clears the marker. A direct mover must not accidentally
inherit that normal-game transaction marker. Do not clear it to force eligibility;
return busy instead.

### Admission, real completion and bounded failure

Recommended conservative admission for direct motion, all checked again by task 7 when
claiming: `41001e==0x0a`, `412876==0x16`, full mode normalized to `0x16`, `43c31f==01`
(the expected native main-door-only interlock state, no other open interlocks), motor
long==1, `410213==0`, `410212==0`, `410172==0`, `410165==0`, all active reel
states==1, and all three fault indicators `4101ee`, `43c2e4`, `41d9c1` zero. Require
CHANGE and SPIN released (`FF1014 & 1800 == 1800`) before claiming. Require no
diagnostic payout-test wager (`410371==0`) or queued diagnostic wager edge
(`410386==0`), and reject special-game latches `43cd46`, `43cd78`, `43cd6a`, `43cd91`,
`410232` when nonzero. These last broad exclusions are **proposed conservative gates**,
not a recovered native predicate. Payout/jackpot modes `08/0a/0c/0e/10/18/1a/1c`,
general fault mode `14`, initialization/recovery and any non-Test-10 context are
excluded by the mode whitelist. Do not identify a retained last-win accumulator such as
`43cd08` as an unpaid payout solely because it is nonzero.

Completion is **not** a packet ACK, a cleared handoff, elapsed time or phase `80`:

- Absolute fragment `5dad2` requires sensed==target, dwell>=`41017a`, matching
  electrical phase and nonzero optical state; all reels must become `80`.
- Jog fragment `5de50` exhausts `4101b6`, marks reels `80`, and calls `5fcb2(3)`.
  `5fcb2` enters state `800` with a countdown, then `5e610` enters `80`.
- Finish/verify fragment `5e1e2` compares masked **fresh** `FF1018` samples with
  `4101a6`, writes sensed stops to `43e012[]` and phases to `4385ce[]`, then sets motor
  long=1 at `5e41e`, clears flags, and sets reel states=1. Check the whole final state
  on the next task iteration, including countdown==0 and no new fault. For absolute
  moves, additionally compare sensed/recorded positions to the requested stops using
  the native `(22-p)%22` convention; report measured stops, not merely the supplied
  targets. Jog success does not prove a requested absolute stop was reached.

Both callable direct APIs have **unbounded** wait loops: `5f436–5f44a` and
`5fc96–5fcaa` repeatedly yield until motor long==1, without a deadline or fault exit.
So a bounded worker cannot simply call them synchronously and put a timeout around its
caller. Recommended source implementation: reproduce only their bounded setup in
extension `start_jog` / `start_absolute`, then poll completion in Test 10's existing
loop. Absolute setup is `5fbd8–5fc94`; jog setup is `5f38e–5f434` (the reel argument
prologue must be adapted). Keep stock interrupt motion and optical checks; do not
branch into these fragments as if they were C APIs. No electrical timing belongs in
the serial interrupt handler.

Use wrapping tick subtraction on long `4127f6` (incremented by autovector 4 at `269c`)
for movement and abort deadlines. Tick-to-seconds calibration and a
mechanism-appropriate bound are not established here; do not invent a timeout number.
On timeout or cancel, the stock abort precedent `5fb98–5fbd6` sets every active reel
phase/state to `80`, then motor long=`80`, and waits for real idle; `5efc0` has the
same stop request in mode `12`. Applying that precedent to an extension-owned move is
an **inference requiring verification**. Serialize the short state publication against
motor interrupts; never mask interrupts while waiting. Do not assign motor long=1 to
manufacture completion.

Fault `FUN_0005ff76` can start state `10` recovery, including a single-reel reposition
for error -2 in diagnostic mode. So any fault or deadline must latch `fault`/`timeout`,
retain exclusive ownership and inhibit normal starts until the abort has genuinely
drained; it must not clear `4101ee`, `43c2e4`, `41d9c1` or call `6268e` to make the
command look successful. If the abort deadline also expires, report
`recovery-required`, stop accepting moves, and require native service or
reinitialization. **Blocker:** no statically established universal
force-stop-and-resume API exists for every fault path, so automatic remote recovery is
not implementation-ready. A checked instruction-level abort/fault test and hardware
optical/drive acceptance would settle that boundary.

### Selected normal outcome: exact one-shot source hook

Do not modify the background random-number/candidate state. Native guards at
`615ae–615c4` allow the copy only when `438044==0` and
`(43ca34!=0 || 43cd46==0)`. Keep these branches unchanged. In particular, the
personality flag can permit reselection during special games: passing the native
branches alone is **not** proof of an eligible fresh normal spin.

At the proposed source hook `615ce`, D2 is the reel index, D0.w is `4*D2`, and the
original instruction loads A0=`4101dd`. The next instruction, `615d4`, loads
`D1.b = A0[D0.w]`; `615d8–615ea` performs the original SafeRAM byte write to
`43e000[D2]` via `FUN_00000400`. A resolver returning A0 can therefore choose between
the original candidate base and an immutable **stride-four** extension buffer (stop
bytes at offsets 0, 4, 8). Preserve every register except A0 and preserve CCR (MOVEA
did not change it); especially preserve D0, D1's upper bytes, D2, A6 and SP. The
default no-request path returns the original A0 exactly.

Conservative first-version eligibility at **D2==0**: a valid pending request was
admitted in ordinary closed-door idle/wager states `00/02/04`; now the full mode is
`06`, `43c31f==0`, task-6 event `4105dc==1`, the original caller return at `4(A6)` is
`0xa5e2`, `438044==0`, and `43cd46`, `43cd78`, `43cd6a`, `43cd91`, `410232`, all fault
fields and extension motion ownership are zero. Exclude alternate transaction fields
`43c340`/`43c350` when nonzero. This is a **proposed strict eligibility subset**,
justified by event 1's ROM entries: states 02/04 call 61172, door-open states 03/05
call the separate 5792a, state 06 calls the re-spin handler 61ec2, and state 1a uses
the alternate game path. It intentionally does not support selected outcomes in those
other transactions. Evaluate eligibility once and claim the entire triple; never
switch sources halfway through the three writes. A CLI shim that adds a JSR wrapper
changes the caller-return guard; prefer the native event or a tail transfer, or
deliberately replace that guard with a validated fresh-spin transaction latch shared
with the start implementation.

`438044` is set during interrupted/recovered spin handling (`5c334`, case 6; `6268e`;
`64a7a`), and native continuation `6167e–61686` clears it only after the commit
decision. Keep that recovery behavior. `61c50/61e0e` calls 61172 for special spins with
a different caller return; `61ec2` independently writes selected unlocked reels and
must remain untouched. A pending request must be cancelled or rejected on those
transitions, not leak forward into a later game.

At `615fa`, finish only a claim made by this invocation: all three SafeRAM writes have
returned, so mark the request consumed/committed, release its immutable source buffer,
then reproduce the original `TST.B (43cd59).L`, including CCR, and continue at
`61600`. This hook also executes when the native copy was skipped; without a claim it
must not consume or rewrite anything. Claim/consume is not physical completion or proof
of payout; preserve downstream conversion `616e2`, motion call `617e4`, evaluator
`65128`, and all native recovery/payout handlers.

**Implemented** (`game_candidate_entry`): instead of resolving A0 to a stride-four
buffer, the hook replaces both `615ce` and `615d4`, computes the committed stop
directly into D1's low byte, leaves A0 = `4101dd` as the original did, and jumps to
`615d8`. Eligibility is decided once by `game_candidate` at reel zero.

### Mailbox, request IDs and remaining build blockers

The proposed design used one fixed-capacity mailbox shared with the SAS task: command
kind, connection/boot session identity, monotonic 64-bit request ID, argument bytes,
state and result. Echo the exact session and ID in every reply. Accept only fixed
command shapes: `position(stop0,stop1,stop2)`, `jog(reel)` and
`next-outcome(stop0,stop1,stop2)`; validate each stop in 0–21 and the reel in 0–2 at
receipt and at claim. No raw addresses or arbitrary step counts are supplied by the
host. Copy all bytes before atomically publishing `pending`; task 7 (motion) or the
commit hook atomically claims it. Reject a second outstanding request. Retain an ID
high-water mark plus bounded active/terminal results: duplicates return the saved
status; an older ID whose result was evicted returns `stale/unknown` and never
executes again. Do not silently evict and then treat an old ID as new. Never release
an active source/target buffer to the receiver. Expose at least pending,
running/committing, committed, completed, cancelled, busy, requires-diagnostic, fault,
timeout and recovery-required as appropriate. An ACK means accepted, not moved or
spun.

On reboot, discard all pending/executing requests and establish a new session identity
before accepting operations. A host reconnect must not resend an old operation as a new
ID; return unknown/indeterminate for an interrupted operation, not fabricated
completion. **Blocker (at the time):** reset-safe session identity and mailbox RAM
placement were not assigned. The 18 initial 0x800-byte task stacks and an apparently
free ROM region do not prove any particular RAM byte is free. The Test 10 integration
avoids another task/stack, but a RAM liveness/allocation audit and offline
duplicate/reboot/partial-commit tests were required before building.

**Implemented** differently: there is no session identity or evicting mailbox.
Requests use the extension's durable, monotonic request IDs and exact-repeat checking
in `core.c` (`firmware/INTEGRATION.md`, **Wire protocol v1**), and RAM is reserved
explicitly in `firmware/link.ld`.

### Motion-module handoff: bounded FSM and suggested C API

The normal-start shim of the wager design reads the original event and injects native
event 1/9; it does not call 61172 through a wrapper. So the `4(A6)==0xa5e2`
source-hook caller check remains applicable. Share the same ownership/recovery latch
with that shim. This motion module does not implement wagers, spin events or the
selected-outcome source hook.

**Implemented** differently: `firmware/src/motion.h` is the actual contract. Its phases
are `IDLE -> PENDING -> RUNNING -> RELEASING`, with failures going through `ABORTING`
to `LATCHED`; results are `FW_*` statuses, and the request ID comes from the core, not
a session. The proposal below is kept as the design record.

The proposed finite-state machine (FSM) uses states `READY -> START -> RUN -> DONE`,
with failures, cancel or diagnostic exit leading to `ABORT -> FAILED_LATCHED`. Every
poll performs bounded work only. A successful DONE releases ownership after reporting
one terminal result. An unstarted cancellation is terminal without touching motors.
FAILED_LATCHED never releases the start-inhibit latch automatically, even if the motor
later reaches idle; native reinitialization or reset is required. Preserve the
original failure cause when reporting a subsequent abort-deadline failure.

**START absolute**, after admission and atomic claim, has exactly three bounded
per-reel iterations, matching `5fbd8–5fc94`:

1. For each reel r, let `t=(22-request.stop[r])%22`; write `410160[r]=t` and
   `410152[r]=22` (the native unknown/index-search sentinel).
2. Compute the electrical step offset `q = (t==0 ? 0 : 9*t+1)`. This is the exact pure
   helper `FUN_000602fc`. Write `41015a[r]=(q+4385d6[r])%4`.
3. Write per-reel state `410186[r]=0x10`, and SafeRAM byte `43e012[r]=0xff` via the
   bounded writer `FUN_00000400` (it only enables SafeRAM and writes the byte).
4. Publish motor long `4101f6=0x10` **last**, exactly as native setup does. Do not set
   `410165`, alter committed stops, invoke `60310`, or call the stock wait loop.

**START jog**, matching `5f38e–5f434`:

1. Save the prior `410028` in module state, then set it to the validated reel r; keep
   it unchanged throughout RUN/ABORT. No physical selector path executes while the
   worker redirects the Test 10 continuation.
2. Set word `4101b6=(410152[r]%21==0 ? 10 : 9)` (21, 10 and 9 here are decimal). Set
   pointer `410228=0x6b830` for the 10-step case, otherwise `0x6b80c`.
3. Clear word `4101b0`, then for i=0,1,2 OR the big-endian word
   `*(uint16_t *)(0x6b858 + 8*i + 2*41018c[i])` into it.
4. Call the bounded timer loader `FUN_0005fed8(0,0xffff)`, then publish motor long
   `4101f6=0x40` last. `5fed8` subtracts word `43862a` outside state 20, encodes the
   timer bytes and writes FE10C8; it has no yielding or polling loop.

Publish related motor fields in a short critical section that saves the status
register (SR) against the motor interrupts and restores the previous mask exactly.
Never keep that section across a poll, a yield, a deadline wait or a normal game call.
Recheck admission inside claim/publication, not just on receipt. The setup loops are
bounded by the installed reel count, not host-provided lengths. Request target bytes
stay immutable until terminal status, although absolute setup itself consumes them
synchronously.

**RUN:** on each Test 10 poll, check fault latches, mode/interlock changes,
cancellation and `uint32_t(now-start_tick) >= move_timeout`. If none applies, check
the complete real-idle predicate described above. Absolute success requires all
measured stops to match; an idle mismatch is a failure, not success. Restore the saved
jog selector only after real idle; do not restore it while a motor interrupt can still
use it. Record terminal status before releasing the mailbox.

**ABORT:** issue the native stop precedent once, in a short critical section: set all
three `410186[r]=0x80`, then `4101f6=0x80` last. Do not zero fault/optical fields or
write an artificial idle. Poll for actual idle with a separate abort deadline. Even
when it drains, retain FAILED_LATCHED for timeout, fault or unsafe exit; if it does not
drain, expose `recovery-required` and retain ownership and start inhibition. This is an
implementable **proposed abort**, not a proven universal physical stop: native flags
and fault recovery can redirect it to reinitialization. Its fault matrix must pass
offline and hardware acceptance before this command is enabled.

**Cleanup wrapper:** `5af80` calls the module poll with `test10_exiting=true` before
native cleanup. If ownership remains, do not call `5b5fe`; return to `5ac2a` and
cooperatively yield two ticks in the assembly adapter if the invalid queue would
otherwise make a tight loop. Its own saved return can be changed from `5af86` to
`5ac2a`; no caller argument cleanup is outstanding at this call. A latched recovery
state must not fall through into the native Test 10 payout evaluator or release
normal-start inhibition. No new task is required.

The proposed public C interface, kept as a design record (the implemented interface
is `firmware/src/motion.h`):

```c
#include <stdbool.h>
#include <stdint.h>

typedef enum { MOTION_POSITION, MOTION_JOG } MotionOp;
typedef enum {
    MOTION_PENDING, MOTION_RUNNING, MOTION_COMPLETED, MOTION_CANCELLED,
    MOTION_BUSY, MOTION_REQUIRES_DIAGNOSTIC, MOTION_INVALID,
    MOTION_STALE_SESSION, MOTION_STALE_ID, MOTION_UNKNOWN,
    MOTION_FAULT, MOTION_TIMEOUT, MOTION_RECOVERY_REQUIRED
} MotionStatus;
typedef struct {
    uint64_t session, id;
    MotionOp op;
    uint8_t stops[3];  /* POSITION only */
    uint8_t reel;      /* JOG only */
} MotionRequest;
typedef struct {
    uint64_t session, id;
    MotionStatus status;
    int32_t native_fault;
    uint8_t diagnostic_fault, affected_reel_plus_one;
    uint8_t measured_stops[3];
    bool measured_valid, start_inhibited;
} MotionResult;
typedef struct {
    uint32_t move_timeout_ticks, abort_timeout_ticks;
} MotionLimits;
typedef enum {
    MOTION_PASS_NATIVE_INPUT, MOTION_REPEAT_TEST10, MOTION_DEFER_EXIT
} MotionPollAction;

/* Startup/native reinit only. Session must change on reboot; limits nonzero,
   each below 2^31 ticks. No serial 'clear fault' operation. */
bool motion_init(uint64_t session, MotionLimits limits);
/* SAS worker: validate and publish; never starts motors or waits. */
MotionStatus motion_submit(const MotionRequest *request);
/* Task 7 only, at the two hook sites; never calls stock blocking motion APIs. */
MotionPollAction motion_poll(uint32_t now, bool test10_exiting);
/* Cancel/query use exact session+ID; duplicate requests never restart motion. */
MotionStatus motion_cancel(uint64_t session, uint64_t id);
bool motion_query(uint64_t session, uint64_t id, MotionResult *result);
bool motion_start_inhibited(void);
```

Serial framing and packing are not this native C struct layout. Hardware addresses,
short critical-section primitives, SafeRAM/timer calls and reads of the tick/mode
snapshot belong in a small internal 68000 adapter, not in public arbitrary-memory APIs.
Bind the real adapter in firmware; test the FSM against a deterministic state/register
model with tick events, including timeout, fault-directed recovery, exit while moving,
duplicate IDs and a reboot between SafeRAM writes. Elapsed-wall-clock tests and
sleep-based completion assumptions are not appropriate.

Direct takeover from normal idle and automatic fault recovery remain explicitly
unsupported.
