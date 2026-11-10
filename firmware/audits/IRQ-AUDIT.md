# Native interrupt and profile-publication audit

This record audits the interrupt side of the original main program, S6M00002B5, as it
affects the experimental firmware extension ([README.md](../README.md)). It answers two
questions. First, can an interrupt observe or disturb the game configuration while the
extension publishes a new profile? The audit finds the stock full configuration reload
unsuitable and recommends an owned-byte restoration plus a bounded admission check, which the
extension implements. Second, how much stack can
interrupts and the extension's private E0 worker consume, and can any returning interrupt or
that worker reach the guarded settlement routine? The answers matter because every stack
budget in the other audit records adds this document's 1904-byte interrupt allowance, and the
settlement proof relies on its interrupt exclusions.

**Status.** This is software analysis plus offline CPU-emulated tests. Its conclusions hold
only under the premises stated with them. The published image (`main.bin` SHA-256
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`) carries
`install_ready=false`; hardware timing, bus behavior, silicon condition and installation
remain unqualified.

**Related records.** Each audit record owns one part of the extension's stack and
reachability argument; the others cite it rather than repeat it:

- [INITIAL-ROOTS.md](INITIAL-ROOTS.md): initial task roots (non-E0 and bootstrap), immutable
  callback selector graphs and the complete finite stack composition.
- This document: the conditional 1904-byte interrupt allowance (I1904), positive IRQ and
  private-E0 exclusion, the calibration, timer and bus premises, terminal IRQ7 and fresh-SP
  exception separation.
- [SETTLEMENT-GUARD.md](SETTLEMENT-GUARD.md): the guard on the native settlement entry, its
  admission induction and the producer and allocation seals.
- [NATIVE-STACK-LEDGERS.md](NATIVE-STACK-LEDGERS.md): local, instruction-led stack costs.
- [TASK-STACK-AUDIT.md](TASK-STACK-AUDIT.md): qualification status and native task-stack
  capacity.

## Terms used in this document

Addresses are CPU addresses in the original ROM's map, written in hexadecimal with or without
`0x` (`5e654` and `0x5e654` are the same address). Router and event keys, opcodes, field
offsets and motor states are hexadecimal; byte counts, instruction counts and tick counts are
decimal unless marked. An arrow `a → b` means "a calls (or dispatches to) b"; `a:b` means "the
call to b at instruction a". Paths in code spans are relative to the repository root; links are
relative to this file.

| Term | Meaning here |
| --- | --- |
| Native | Code and data of the unmodified original ROM. |
| Extension | Code the firmware build adds (reserved region `[0x79100, 0x80000)` in `firmware/patches.json`), entered through hooks patched over native instructions. |
| Profile; owned bytes | A profile is the uploaded game configuration (paylines, credits per line, stop weights, payouts). The *owned bytes* are the 177 configuration bytes a profile replaces; the *owned-byte baseline* is the saved copy of their original values. |
| Cap | The maximum-bet byte at `0x43ca1e` (`MAX_WAGER`, "aggregate cap", in `firmware/src/game.c`). |
| E0; private E0 | SAS long poll `0xE0`, the extension's private command. *Private E0* is its processing on a separate 4 KiB stack, `[0x433000, 0x434000)`, entered through the frame-free bridge `platform_sas_handler`. The *worker* is the code that runs on that stack. |
| IRQn / IPLn | 68000 interrupt priority level n. The board's sources per level are listed in **Stack accounting and the remaining IRQ6 cycle**. A *returning* interrupt ends with RTE and keeps its frames on the interrupted task's stack (*same-stack*); the *terminal* IRQ7 (AC power fail) halts instead of returning. |
| VIA, T1, T2, IFR, IER | The 6522 Versatile Interface Adapter at `0xfe10c0` (VIA2, U41) and its two timers. IFR (`0xfe10db`) holds interrupt flags and IER (`0xfe10dd`) interrupt enables; bit 5 belongs to T2 and bit 6 to T1, so IFR5 and IER5 are T2's flag and enable. Timer A and timer B are T1 and T2. |
| Kernel; SCn | The VRTX real-time kernel in the original ROM. Services are requested with TRAP #0 and a service code in D0; *SCn* names service code n in hexadecimal (for example SC20/SC21 lock and unlock the scheduler, SC8/SC9 post and pend a mailbox). |
| TCB; CURRENT; workspace+28 | A TCB is a kernel task control block: saved supervisor SP at `TCB+0x38`, allocated stack top at `TCB+0x40`. CURRENT is the kernel's current-task pointer (workspace `0x400000` + `0x24`). *workspace+28* is the kernel byte at offset `0x28`, nonzero while the scheduler is locked or a registered interrupt is active. |
| Settlement; guard | The native financial settlement routine `0x589ba` and its recursive partner `0x3d294`. The extension guards every `0x589ba` entry with `game_settlement_entry` ([SETTLEMENT-GUARD.md](SETTLEMENT-GUARD.md)). |
| Covered | An extension hook whose stack use the settlement and root proofs account for (`INITIAL-ROOTS.md`, **Covered patch-family inventory**). *Covered controller/evaluator entrances* are native entries that such hooks intercept, such as game start `0x61172` and the wager evaluator `0x65128`. |
| Positive evidence | A conclusion drawn from the complete set of actual targets (decoded tables, immutable pointers, branch-complete call lists, producer domains), never from the absence of cross-references. A *called-target domain* is the complete set of code a root can reach by synchronous calls. |
| Producer; seal | A producer is an instruction that creates a code address later used by an indirect call or jump (for example an LEA of a function). The *producer seal* (`SETTLEMENT-GUARD.md`, **Producer seal**) checks that the final linked image contains exactly the verified producers and frames; *resealing* repeats that check after code changes. |
| Selected arm | A case of a dispatcher or router that is reachable with the keys the analyzed producers can emit, as opposed to every case of that dispatcher. |
| AS, SAS, BSG, MIS, SDT | The five communication protocols that router `0x26a0e` dispatches to (routers `0x2640c`, `0x23d40`, `0x1b8ca`, `0x18bea`, `0x26fc8`). SAS is the Slot Accounting System and BSG the Bally Secondary Game link; AS, MIS and SDT are the labels this analysis uses for the other three. |
| SafeRAM | The battery-backed RAM at `0x438000..0x43ffff` ([notes/machine.md](../../notes/machine.md)). |
| TRAP1 fatal halt | The native non-returning failure path, TRAP #1 → `0x0fc4` → watchdog-only loop `0x123c` (see **Calibration lifecycle and implemented enforcement policy**). |
| Envelope; allowance; component | Stack budgets in bytes. A *component* is one interrupt level's worst inspected path; an *allowance* is a budget accepted for a whole domain; an *envelope* is a total compared with an allocation. These sums overapproximate: they assume every maximum can coexist, which no trace shows. |
| Historical b2; candidate `06bef891` | Firmware images, identified by the first hex digits of their `main.bin` SHA-256 (table below). |

Firmware images named in this record:

| Image (`main.bin` SHA-256) | Role here |
| --- | --- |
| `d9a5f9f0…` | Earlier build whose emitted resident depths are quoted in **Stack accounting and the remaining IRQ6 cycle**. |
| `ca7e0c9653df3d8648851d1679935fda674d72bd0bca003d0b45daabed20f6c5` | Image inspected for the calibration-guard hook placement. |
| `d15f83984deaca1bb606b9a71c1d179714c0fec51bd96d4f9bccdb8fa5417e03` | Slot-workspace reduction; the first serial-cancellation probe ran on it. |
| `984cb4f4e1f54ce53c50b0ad4ee18a6be9d25cd63e8e85f162ec86bf143345d2` | Private E0 stack qualification, before the serial drain. |
| `b2ce1bc5c03b8e8af47cf246115968033f62574c2a3ae2d2d7e5c4f225adcb83` ("historical b2", "frozen b2") | First implementation of the serial drain, with 2048-byte native task stacks. It is retained as proof provenance and is no longer the published build. |
| `06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197` ("candidate `06bef891`") | The published image: 3072-byte native task stacks and the settlement entry guard. |

## Scope and status

This is a read-only audit of the original `roms/s6000_main_S6M00002B5.bin`, SHA-256
`49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a`.
Addresses are original-ROM addresses, not extension addresses. The Ghidra program
`bally:/s6000_main_S6M00002B5.bin` supplied decompilation and instruction listings, and the
host's `m68k-elf-objdump` supplied bytes where Ghidra has no function defined. The audit made
no changes to the ROM, to the Ghidra analysis, or to the machine, and used no serial or SSH
connection.

**Established:**

- The full stock loader exposes a zero cap that IRQ4 can observe.
- The normal settled-idle IRQ6 timer-A path does not evaluate profile-owned configuration or
  the payout cache, and a zero motion-finalization byte keeps it away from the cache reader.
- Normal timer-B retirement has a positive argument from the timer's one-shot behavior and
  its acknowledgment.

**Not established:** a universal interrupt-exclusion proof; safety of arbitrary retained or
corrupted peripheral state that merely satisfies the C admission predicate; closure of every
communication-router path; or stack qualification of the whole firmware.

The historical b2 E0 gate has reviewed conditional envelopes of 2928/4096 bytes on the
private stack and 1964/2048 bytes at the native boundary. Candidate `06bef891` implements
3072-byte native task-stack capacity and the settlement entry guard. Its emitted-code and
producer checks, the affected offline tests and a source review passed, and the complete
mathematical composition of the initial non-E0 and boot roots was independently reviewed and
approved. A review of the maintained documents and the final pinning of the build inputs also
passed. The exact reviewed `06bef891` artifacts are published locally with
`install_ready=false`; the physical gates remain pending. The historical 2048-byte comparisons
are not the candidate's capacity. Current evidence is in `SETTLEMENT-GUARD.md`,
**Candidate gates**; the complete initial producers, selector graphs and finite composition
are authoritative in `INITIAL-ROOTS.md`. The historical b2 serial-cancellation gate remains
closed.

**Scoped closure.** Every called-target domain of the returning same-stack interrupts and of
the private E0 worker positively excludes settlement (`589ba`/`3d294`) and its covered
controller and evaluator entrances, in every motion phase. The selected communication domains
are closed, not every router opcode. See **Guard-domain closure and IRQ6 selected cases** and
`SETTLEMENT-GUARD.md`. The candidate's verified frame and producer identity preserves these
local domains. It does not establish the ancestry of the initial task roots or qualify the
physical timer and bus; `INITIAL-ROOTS.md` supplies the separate complete mathematical root
composition.

The standard locked kernel services and the examined fixed counter descriptors have positive
destination evidence, conditional on valid initialized objects. The implementation now replaces
the full loader with an inherited-baseline restoration, which passed its targeted restoration
and IRQ4 regressions and two scoped reviews. That closes the reproduced zero-cap transient, not
the remaining whole-system qualification described in this document.

## Reproducible IRQ4 native fixture

The durable form of this experiment is the test
`firmware/tests/test_game.py:coin_edge` together with
`test_restoration_cut_at_every_write_replays_and_keeps_a_valid_cap`, both run on the guarded
`firmware/tests/cpu.py` harness. The test compares every modeled restoration cut point against
native behavior at stable old and new caps, and checks that boot replays the restoration
exactly. The original scratch probe is historical supporting evidence and is not needed to
reproduce the result; its complete CPU contract and results are recorded here.

CPU contract:

- Unicorn M68K, `UC_CPU_M68K_M68000`, big-endian.
- Mapped regions: ROM `000000..07ffff`; zeroed RAM `400000..43ffff`; zeroed MMIO
  `ff1000..ff1fff`; zeroed test stack `e00000..e0ffff`; sentinel `f00000..f00fff`.
- The unmodified original ROM is loaded. D0–D7 and A0–A6 start at zero; SR=`2400`,
  SP=`e0fffc`, and `[SP]=f00000` as a long (the return address into the sentinel).
- The native `FUN_000411ce` runs until PC reaches `f00000`, with an instruction budget of
  10000. This is the coin-processing body that IRQ4 calls at `266c`; the probe does not
  simulate a full interrupt entry or model peripheral timing.
- Inputs: byte `43ca1e=cap`; long `4103a4=600` (previous sensor sample); long `41038c=1`
  (pulse count); word `ff1014=400` (current sensor sample); word `41242a=80` (coin-enable
  output shadow). All other RAM and MMIO stay zero, which means ordinary MODE0, no host
  inhibit, no diagnostic motion and the one-sensor coin path.
- The only substituted native leaf is `549c8`. The substitute requires its stack argument to be
  counter `43`, returns D0=0 (the wager counter), pops its return address and resumes. Native
  `41152` runs unchanged. A code hook counts its calls and a memory hook records actual writes
  to `ff1004`. A path that does not return or leaves SP unbalanced is rejected.
- `ff1004` deliberately starts at zero, as in the original experiment. Its write events, not
  its final zero value, show whether the native output write ran.

Observed results:

| cap | native `41152` calls | shadow coin-enable bit | writes to `ff1004` | balanced SP |
| ---: | ---: | --- | --- | --- |
| 0 | 1 | clear | one word, value 0 | yes |
| 1 | 1 | clear | one word, value 0 | yes |
| 3 | 0 | set | none | yes |
| 15 | 0 | set | none | yes |

The native path is IRQ4 `2646` → call `266c:411ce` → cap read `41600:43ca1e` → conditional
call `41614:41152`, which clears bit 80 of shadow `41242a` and writes `ff1004`. With a cap of
0 or 1 that call runs; with 3 or 15 it does not.

The output's physical meaning comes from the pinned board schematic, not from a function
name: `vendor-docs/bally-s6000/MK14-S6MOD-0001_schematics.pdf`, **MPU Board (3 of 9),
AS-03356-0424**, sheet **14-18**, **OUTPUT PORT 2A / U29 / FF1004**. Its D7 drives U29
(74HC273) input D8, output Q8 at pin 19, then U9 (ULN2803) input I8 at pin 8 and output C8 at
pin 11, which reaches the net explicitly labeled **COIN ENABLE / HIDRIVE15**. A crop of the
schematic was checked visually against that chain; the pinned full PDF is the durable source.
This is the drawing for the 0424 board, not an assumed mapping from the 0438 board.

The CPU result is an offline behavioral difference on the documented output path. It is
**not an observed hardware failure or credit loss, and not a proof about every coin state or
about the mechanism actually connected**.

## Why runtime full reload is unsuitable

`FUN_0003756c(1)` calls `480` at `37584`, clears the protected configuration
`43c87a..43ccdd` at `3759e..375b6`, and then calls `49e` at `375bc`, **before** it rebuilds
that configuration. The successive `38ad0` personality copies begin at `375ec`, and that helper
brackets each copy separately with `480/49e`. Scheduler locking therefore does not hide the
zero cap between batches from IRQ4.

**Recommendation:** restore a transactionally inherited **owned-byte baseline** taken from the
original configuration, rather than running the full loader or adding an IRQ4-only special case
for the ROM. The deciding criteria are clean ownership boundaries and realistic
native-equivalence tests. The owned bytes are cap, lines and all-lines (3 bytes), weights
32 × 5 (160), three totals (6), and the selected paytable's count and records
pointer pair (8): **177 bytes**. The layout is: original profile 252 + guards 32 + context 4 +
baseline 177 = 465 bytes, before slot metadata at 496.

The baseline must be captured before the first custom activation, survive custom-to-custom
commits unchanged, be checked for integrity and context, and stay referenced by a durable
restore intent until restoration completes. Restoration writes only those fields, recomputes
the native checksum, rebuilds the line cache, reconciles the selector and its publication, and
preserves configuration, accounting and fault fields the extension does not own. It must not
recapture custom state, and it must not fall back silently to a stock reconstruction from ROM.
This removes the identified zeroing transient; it does not by itself prove every cross-field
observer safe.

Masking all interrupts around the loader or the application is not an option: native interrupt
helpers, loops, cache calls, UART deadlines and kernel calls are involved, and level 7 remains
possible. No latency qualification supports that workaround.

## IRQ6 entry and bounded dispatch

`isr_autovector_6:2718` captures `ff1018` into `4101ae`, enters the kernel with trap
command 16, saves D1–A6, and then tests **IFR bit 5**, not the motor state:

```text
2732: bit5(fe10db) ? 273c:5e654 : 2744:5d6ea
274a: restore registers; kernel interrupt exit command11
```

So a pending T2 flag sends IRQ6 to the T2 stepping handler `5e654`; otherwise it goes to the
T1 state machine `5d6ea`.

`FUN_0005d6ea` has a special path for state 4. Otherwise its bounded ROM state table at
`5d9de`, relative-target table `5d9ca` and jump `5d9c6` resolve as follows:

| motor long `4101f6` | target |
| ---: | --- |
| 1 | `5e08a` |
| 2 | `5d9f2` |
| 10 | `5dad2` |
| 20 | `5ded2` |
| 40 | `5de50` |
| 80 | `5e1e2` |
| 200 | `5dc24` |
| 400 | `5dcca` |
| 800 | `5e610` |
| 1000 | `5e48a` |

The motor values are hexadecimal. Unknown values go to `5e63c`, not to an arbitrary indirect
target. The table was decoded from the original bytes; `5d9f2`, `5dcca` and `5e48a` need host
disassembly rather than assumptions based on Ghidra's function boundaries.

## Idle timer-A / finalization exclusion

This section shows, under the premises stated below, that while the machine is idle and the
extension owns a profile change, IRQ6's timer-A (T1) path cannot reach the code that reads the
payout cache.

The audited pre-baseline `game.c:game_prepare_profile` claims ownership of the profile change
and rejects a native event that is already dispatching or held. `native_idle` requires
`MODE&fe=0`, wager 0 and `quiescent`: motor state 1, queue byte `410213`=0, motion-finalize
byte `410165`=0, no recovery, alternate or special transaction, and no extension motion. The
native apply runs with the scheduler locked; interrupts stay enabled.

The induction below **assumes that this ownership really does prevent every native task or
diagnostic motion starter until the change finishes**, that native geometry and pointers are
valid, and that nothing corrupts memory from outside. A task lock alone is not an interrupt
lock, and a single admission sample alone does not establish this invariant.

The idle path, traced positively:

```text
5d6ea → state1 → 5e08a
  410213 != 0 → 5d368                    excluded by queue0
  otherwise countdown/sample ff1018
    changed optical input → 5ff76(-2, reel)
  5fed8(0, ffff)                        timer-A only
  optional 3a14(0) kernel call
  optional startup-marker438627==3 processing / 5ff76(-7, reel)
  5e64a return
```

`5d368` is the motion starter. Its store at `5d410` sets `410165`=1 (unless native interlocks
suppress it), and its store at `5d382` can queue motion. Its examined callers are the task
motion paths `5792a`, `6162c` and `61ec2`, the idle queue branch (excluded because the queue
byte is 0), and `5f452`. These are positive caller and guard edges; the audit does not claim
that a search for absolute operands would detect arbitrary writes.

`5ff76(-2, ...)` records and posts an optical fault and **returns at `60046` without changing
the motor state**. Other fault cases, for example -7, can call `60254`, clear `410158` and
publish motor state 10 at `602f2`. The statement "IRQ6 never changes the motor state while the
scheduler is locked" would therefore be false. Fault state 10 goes through `5dad2`, optical
sampling with ROM phase patterns, and timer-A; a completion can use `5fcb2` → state 800 →
`5e610` → state 80 → `5e1e2`. None of these paths sets `410165` to 1 or queues a spin.

The relevant cache reader is `5f452`: its first load of the `410294` pointer is at `5f45a`,
with further loads at `5f62e` and `5f6d6`. Its examined caller is `5e1e2:5e3ce`, which sits
behind two tests:

```text
5e3be: 410165 != 0
5e3c6: 43ca2e != 0
```

The value `410165`=0 required at admission survives the idle and fault paths listed above;
`60310` can only clear it (at `60436`). Those paths therefore cannot enter `5f452`, even if the
motor state changes after admission. `5e1e2` itself reads geometry and feature bytes the
extension does not own, so the proposed 177-byte restoration must leave those bytes untouched.
Kernel event posts do not synchronously invoke task 6 while the scheduler lock holds. The
standard kernel and computed-memory destinations are positively closed below, under valid
objects; arbitrary corruption remains excluded as a premise, not an open lifecycle
investigation.

## Timer-B pending flag: positive lifecycle evidence and its limit

This section shows when the T2 interrupt flag can still be pending at idle, because a pending
T2 flag would send the next T1 interrupt into the stepping handler `5e654`.

The schematic `vendor-docs/bally-s6000/MK14-S6MOD-0001_schematics.pdf`, drawing **MPU Board (4 of 9)**
(PDF page 19), identifies the FE10C0 VIA as **6522P2**. Native `24ae:2542` clears the auxiliary
control register (ACR) at `fe10d7`, so T2 runs as an interval one-shot. IFR is `fe10db`, IER is
`fe10dd`, and the T2 low and high bytes are the odd bytes covered by the longword store at
`fe10d0`.

Manufacturer reference: `vendor-docs/components/g65sc22-cmd.pdf`, CMD G65SC22, February 28, 2001,
sections **Timer 2 Operation**, **Timer 2 One-Shot Mode** and **Interrupt Operation**, and
Figures **25, 27, 37 and 38**. The scanned descriptions were checked visually. After a timeout,
T2's flag logic cannot assert again until T2-high is reloaded; reading T2-low or writing
T2-high clears IFR5; writing IER with bit 7 clear clears the selected enables. Reading IFR does
not acknowledge any source, and IER bit 7 reads as 1.

U41 is marked **CMD G65SC22PI-2** (observed on 2026-10-17 in photographs that are not
distributed; see `photos/05-mpu-eproms.jpg`). `vendor-docs/components/README.md` records the
datasheet's provenance and hash. This replaces reliance on the compatible WDC-family reference
with documentation for the observed model. It is **not a test of the actual silicon's
condition, clock or bus timing, or board behavior**.

The native normal-motion lifecycle:

1. State 4's `5d790` writes IER=e0, enabling T1 and T2, and `5d798` loads T2 from `410218`.
   State 4 is entered from an actual motion starter, not from the admitted idle branch.
   `5ff2c` changes precomputed timing scratch, not hardware.
2. With a nonzero pending phase mask `4101a8`, `5e654` compares phase `4101fa` with the ROM
   constant `6b808`=25 decimal. Below 25 it reloads T2 at `5e6a2`. At 25 or later,
   **`5e6ae` writes IFR=20 and then `5e6b6` writes IER=20**, clearing T2's flag and its enable.
   This happens before the optional nested `5d6ea` call at `5e6ee`.
3. Phase 25 then marks the stopped reel 80 at `5e94a`. Normal finalization waits for the
   stopped-reel state at `5e236` before publishing idle state 1 at `5e41e`; it clears `410158`
   and `4101fa` at `5e3b2` and `5e3b8`, and finally clears `4101a8` at `5e476`. The nonzero
   mask used during normal T2 stepping is constructed with bit 100 set, at `5d8dc` and `5eb98`.
   The zero-mask branch of `5e654` must not be mistaken for an unconditional acknowledgment
   path.
4. The admitted idle branch and its examined fault drain call `5fed8` only with channel 0,
   which stores `fe10c8` (T1). Only channel 1 stores `fe10d0` (T2). These paths therefore
   cannot re-enable or reload T2 while the zero queue byte, the zero finalize byte and motion
   ownership keep the motion starter unreachable.

**Conditional conclusion (inference):** after normal native motion retires into settled idle,
T2 has no pending timeout left that could route a later T1 interrupt through `273c:5e654`, and
it cannot generate a fresh one while the owned fields are updated. This closes the normal idle
pending-timer path with a positive retirement and no-re-arm argument, not with an absence of
configuration cross-references.

**Limit of the implementation before the admission guard:** that C predicate did not read
IFR5, IER5, `410158`, `4101fa` or `410172`; the guard implemented since is described below. In
arbitrary retained, startup or peripheral state, motor state 1 with zero finalize and queue
bytes does **not** establish the normal lifecycle premise. `2732` tests the raw flag even when
IER5 is disabled. `5e654` has no exclusion for motor state 1 and uses `410158-1` as a reel
index, so a synthetic stale flag with idle index 0 cannot be waved away as safe. `5cf7e` also
makes a temporary initialization-state-1 write at `5d2f8`, and diagnostic or reinitialization
paths such as `60442` are not normal settled retirement. No physical stale-flag failure has
been observed.

### Recommended bounded admission snapshot: IFR5 and IER5

Check **both** byte registers while the native idle and ownership conditions are stable,
inside a short interrupt-masked section that saves and restores SR:

```text
ifr = *(volatile uint8_t *)0xfe10db
ier = *(volatile uint8_t *)0xfe10dd
require ((ifr | ier) & 0x20) == 0
```

This is two byte reads: not a poll, not a timer operation, and not an interrupt mask around the
whole application. Test bit 5 only: IER bit 7 reads as 1, and T1's bit 6 may legitimately stay
enabled or pending. Use byte accesses to the odd register addresses, not a word access. The
6522 **Interrupt Operation** description explicitly permits reading IFR and IER; neither read
acknowledges a source or changes the timer. IFR flags are cleared by writing 1s or by the
source-specific acknowledgment operations. In particular, **do not read T2-low at `fe10d1`**,
which clears IFR5, and do not write IFR or IER as part of admission. The native test at `2732`
likewise examines IFR without acknowledging T2. These side effects and register semantics
remain subject to the qualification of the installed silicon noted above.

**Why both bits:** IFR5=0 alone can mean a T2 countdown is armed but has not expired. IER5=0
alone still lets a stale raw flag divert the next T1 interrupt to `5e654`. Normal stock
terminal processing writes IFR=20 and IER=20 at `5e6ae`/`5e6b6`, so both bits really are clear
after the normal settled-idle sequence; this is not merely inferred from motor state 1. Those
writes precede the final stopped-reel publication and idle state 1, as traced above. Reading
both therefore rejects armed native motion as well as a stale or pending T2, without
suppressing the ordinary idle T1 source.

The timer-A guard induction still applies: the zero queue byte excludes `5d368`; the zero
finalize byte excludes `5f452`; task and diagnostic ownership excludes the other motion
starters. Idle `5e08a` and its examined fault drain 10 → 800 → 80 use `5fed8(0, ...)` and never
reload T2-high or enable IER5, so state 4's re-arm sequence is unreachable while that ownership
holds. A native optical fault may still post an event or change the motor state; it does not
invalidate the no-T2-re-arm conclusion. Never clear that fault or synthesize native idle to make
the checks pass.

**Exact limit:** even IFR5=IER5=0 is not a hardware readout of "T2 unarmed." An arbitrarily
programmed T2 could be counting with IER5 disabled and assert the raw IFR5 later, and native
`2732` would still dispatch it on a T1 interrupt. Under the verified normal driver lifecycle the
only IER5 clear happens at terminal T2 retirement, not while a future initial timeout is armed,
which excludes that case. Unknown commissioning, startup or corrupted peripheral state is not
thereby certified. A stronger locally checkable native-state contract also requires the normal
completion sentinels (byte `410158`=0, long `4101fa`=0, word `4101a8`=0) in addition to the
existing motor-state-1, zero-queue and zero-finalize conditions; native `5e3b2`, `5e3b8` and
`5e476` clear them at normal retirement, and `game.c:motion_retired` checks them. They do not
replace the peripheral and lifecycle premise, and they cannot reveal an arbitrarily externally
programmed timer.

The original snapshot proposal did not include `410172`; its broader lifecycle is not needed
to weaken or extend this scoped proof, and no further investigation of it is requested.
Startup-marker processing at `5e5e8` sets it to 4, but normal retirement **does clear it, at
`5e424`**, immediately after the motor-state-1 publication at `5e41e`. An earlier claim that
normal retirement lacked this clear was contradicted by the original bytes `4239 0041 0172`
(CLR.B `0x410172`). A nonzero `410172` is not established as a legitimate settled-idle state.

Take the snapshot at the final admission and publication boundary before any writes, with
profile ownership already claimed. If a scheduling opportunity intervenes, check again after
acquiring scheduler exclusion. Restore the prior SR exactly, and keep interrupt masking out of
native cache and kernel calls. Level 7's terminal power-fail handling lies outside this
ordinary-interrupt snapshot.

**Recommendation:** use the dual-bit snapshot, preserve the positive no-re-arm invariant, and
reject unfamiliar states. The deciding criteria are a contract that can be checked locally and
realistic failure tests, instead of long interrupt masking or reliance on historical state
alone.

This is implemented in `game.c:motion_retired` and `platform.c:platform_timer2_active`.
Preparation checks the snapshot after claiming ownership. Runtime application checks it again
under the scheduler lock, before the batch of owned writes, together with ownership, native
faults and the shared `native_idle` predicate (which includes zero wager). This is not a polling
loop around each store. A rejection before preparation returns BUSY with no side effects; a
failed application after commit returns FAULT and keeps exclusion. The native counter reader
nests the scheduler lock.

Compiled regressions verify: byte-only IFR and IER reads; no timer acknowledgment; T1
preservation; restoration of the saved SR; refusal on a pending or enabled T2 and on stale
sentinels; refusal when admission conditions change before application; and no
configuration, cache or publication effects on refusal. The wager-change fixture is an
admission contract test, not evidence of a real coin race. Boot recovery bypasses these runtime
predicates through the explicit window described next.

### Boot versus runtime application

Reset calls `154e` at `bca`. Inside it, `155c` initializes extension state, `1730` can call the
native loader, and `1906` is the final early restore hook. Only after `154e` returns does `be8`
call VIA initialization `24ae`, followed by the kernel start at `bec`. Timer-state admission is
therefore inappropriate inside that early restore window: the VIA has not yet been initialized
during this boot.

The loader-tail hook `37dde` is shared with the runtime alternate-paytable paths `63db8`,
`64120` and `6430a`. Neither the lack of profile ownership nor a call through that hook proves
that the machine is starting up. The implementation distinguishes the `1906` hook with
`platform_boot_restore`, which closes a boot window that only `platform_boot` opens. Runtime
application of an active profile without prepared ownership must fault; it must not gain a
weaker admission path. Settled stock configuration remains a no-write operation. A runtime
loader may already have changed native fields before its tail, and a rejection does not roll
those writes back.

## Other interrupt roots and stack boundary

The other interrupt roots inspected earlier still matter, but this list is not a whole-system
closure:

- IRQ1–3 autovectors at `2642`: RTE only. The actual UARTs use their own vectored entries.
- IRQ4 at `2646`: the coin body above, queue and accounting work, output sequencing at `39040`
  and kernel tick handling.
- IRQ5 at `26e4`: copies shadows `412426`/`412428` to `ff1000`/`ff1002`, plus kernel entry and
  exit.
- DUART1 at `13874`: progressive path `1372a` → `1475c` → `16976` → `16b08`; J3 ring and
  framing at `1377a`.
- DUART2 at `13d98`: channel A `13bf4` → `33dfa` for bill handling and accounting; channel B
  `13c46` → the callback at `43e8f0`.
- DUART3 at `141b2`: ring handling; channel B `14068` → the same callback `43e8f0`.
  Initialization from ROM `74aca` sets that callback to `19732` (`sas_rx_byte`). The examined
  receive framing in the interrupt queues data rather than calling E0 synchronously.
- The kernel's writable hooks, copied by the instructions at `36fe..370a` from ROM `27c0..27cc`, are zero in the
  original setup. The standard services and fixed descriptors below now have positive
  destination evidence; this is not a global exclusion of every alias.

### Computed-memory paths with positive destination evidence

Assume valid initialized kernel objects and native pointers, no external corruption, and
enforced runtime motion ownership. Under those premises:

- Standard service numbers at `0x2814` select bounded ROM branches.
- Mailbox `0x303e` and queue post `0x34e6 → 0x37ee/0x353c` store message **values** in queues
  and TCBs; they do not interpret those values as destination pointers.
- Tick `0x3258` traverses the native TCB delay links.
- Lock `0x33e0` increments the kernel bytes at offsets `0x28/0x29`; a nonzero `0x28` blocks
  the scheduler path `0x2a90` and its context-switch callback.

These examined destinations exclude the owned configuration and the payout cache.

The counter reader `0x549c8 → 0x3bfca → 0x3c998` resolves these original-ROM descriptors
(counter IDs are hexadecimal):

| Counter | Descriptor | Type / length | Data span, including redundancy |
| --- | --- | --- | --- |
| 0x43 | 0x6a14c | 1 / 3 | 0x438034..0x438036 |
| 0x46 | 0x6a164 | 0 / 4 | 0x43d61c..0x43d625 |
| 0x47 | 0x6a16c | 0 / 4 | 0x43d546..0x43d54f |

Their bases are nonzero, which excludes the dynamic fallback at `0x3c9f4`. The IRQ4 accounting
operation `0xa8 → 0x45c94` uses descriptor `0x69f5c`, with fixed base `0x4380b2` and length 5,
not the wager, configuration or cache region. The bill pointers `0x4104ba[i]` are initialized
at `0x1ae4..0x1b02` to `0x43cdb0 + 0x22*i` for ten records, which bounds the bill-record
indirection in `0x258d6`. The IRQ6 fault-counter path `0x6016e → 0x3b04a` constructs
destinations `0x43e47a + class*0x10 + reel*2`.

Closure of the communication router is a separate matter. Ghidra's caller listing for `0x18970`
misses the computed dispatch `0x18bea → 0x18ce4 → 0x18970`, and `0x18970 → 0x18a8e` reads the
owned cap into BSG status 14. For the examined bill-interrupt event `0x37` from producer
`0x34746`, the bill states at event time are `0x0d/0x0e/0x0f/0x10`, while the reset arm requires
`0x09/0x0b`. That particular path is excluded by evidence about the producer's domain, not by
missing caller references. Do not generalize it to every arm of the event router.

### Stack accounting and the remaining IRQ6 cycle

The pinned schematic **MPU Board (1 of 9), AS-03356-0424** labels the interrupt sources:
DUART3 on IPL1, DUART2 on IPL2, DUART1 on IPL3, VIA1 on IPL4, zero-cross on IPL5, VIA2 on IPL6
and AC-fail on IPL7. Kernel configuration `0x27a0+0x14` contains mask 6, so the kernel's
critical SR is `0x2600`, not `0x2700` (level 7 stays unmasked). Configuration `+0x0a=0x0040`
sets kernel `0xf3=2` at `0x3722`. The interrupt-stack switch at `0x2834` requires the value to
be **exactly 1**, so these supervisor tasks never take it: interrupt and trap frames stay on
the interrupted task's stack.

A normal active interrupt handler holds 66 bytes before its body makes nested calls: exception
frame 6, saved D0 4, saved D1–A6 56. The examined locked standard kernel services use at most
48 more bytes below the trap caller's SP, excluding nested interrupts; the tick path
`0x37f6 → 0x37ee` sets that local maximum. These are components, not a total envelope.

For the earlier `d9a5f9f0…` build, the emitted instructions gave resident depths of 24 bytes at
the entry to `0x19b16`, 60 at `platform_sas_handler`, 260 at `fw_process` and 844 at
`game_apply_profile`. The native path is `0x16192/0x16242 → 0x16366 → [0x43e8f4]=0x19b16`,
installed from `0x74ace`. These depths include live arguments and return addresses, not just
the compiler's `.stack` entries. They must be recalculated for the final build and are not
maximum stack usage.

During active motion the cross-calls `0x5d81c → 0x5e654 → 0x5e6ee → 0x5d6ea` add 68 bytes per
complete nested round. Both calls come before the later motion-progress updates, so a simple
argument from progress counts does not bound the recursion. Calibration at `0x5df1a..0x5df28`
stores the measured latency plus `0x60` (96 decimal) at `0x43862a`. The range guards below
constrain calibration without clamping it; the timer and bus premises and the envelope of all
nested interrupts still need qualification.

The no-re-arm induction excludes this cycle during an admitted profile transition, not during
general E0 processing: INFO, upload, status and pre-admission journaling can hold large parent
frames while motion is active. That conditional bound was at first waiting for enforcement. The
implemented checks and the conditional components below close the scoped software contract;
qualification of the installed board's bus and timer is separate. A trace alone cannot prove
those premises.

### Conditional timer bound, not an installation qualification

This section bounds the IRQ6 cross-call recursion to a single handoff, under stated timer and
bus premises.

The pinned **MPU Board (4 of 9)** connects U41/VIA2 pin 25 to the CPU's **E** output, not to
the nearby DUART or I2C clocks. **MPU Board (1 of 9)** gives the CPU clock as 14.7456 MHz.
Motorola's [MC68000UM](https://www.nxp.com/docs/en/reference-manual/MC68000UM.pdf), section
**3.7 M6800 Peripheral Control — Enable (E)**, states:

> A single period of clock E consists of 10 MC68000 clock periods (six clocks
> low, four clocks high).

It also specifies that E runs freely regardless of the MPU bus state. The schematic therefore
implies VIA ticks of about 1.47456 MHz; this is not a measurement of this board.

Let `C` be the native calibration word at `0x43862a`. `0x5fed8/0x5ff2c` construct
`R = (raw_period - C) & 0xffff`, without a borrow or minimum-period check. `0x5df12..0x5df28`
accepts a larger unsigned measured word and adds `0x60` without saturation. A raw period of
`0x14f0` with `C=0x14f0` gives reload 0; with `C=0x14ef` it gives 1. These are
**counterexamples in the input domain** to using the raw-table minima, not proven
counterexamples from native history or recursion.

A sufficient (not maximal) interval for the static argument is `0x0060 <= C <= 0x0800`. With
the raw-table minimum of 5,360 ticks, reloads are then at least 3,312 ticks. The argument also
assumes:

- Timer behavior matches the stated 6522 model.
- Non-VIA bus cycles take at most 6 CPU clocks; VIA cycles at most 20.
- IRQ6 cannot be delayed by another interrupt that later returns.
- Calibration stays constant during the state-4 cross-call cycle.

Under those assumptions each handoff to the opposite handler is bounded by `C + 1024` E ticks,
at most 3,072: from the T1 reload at `0x5d738` to the IFR6 check at `0x5e6e4`, and from the T2
reload at `0x5e6a2` to the IFR5 check at `0x5d812`. The audited prefixes contain at most 44
non-polling instructions of at most 128 CPU clocks each, plus the twelve-instruction polling
loops and the low-byte guards. Under the interval, the T1 exit window is at least 1,264 ticks,
so the stated bus bounds cannot make it miss a whole-period window. Since `3072 < 3312`,
neither freshly reloaded timer can expire within the opposite handoff: there is at most **one
cross-call and zero complete recursive rounds**.

An independent review of the ROM reproduced 40 non-polling instructions per handoff, within the
conservative 44, and twelve instructions in each polling loop. The helper branch in `0x5ff2c`
bypasses the recursive call rather than adding uncounted work to it. The review accepted the
low-byte and split-read argument and the exit-window argument under the stated premises. This
approves the conditional argument, not those premises on the installed board.

The implemented guards cover retained boot state, the calculated candidate before its native
store, and the initial T2 arming. They therefore do not rely on a single snapshot of a saved
value or on native calibration repairing an out-of-range value. The result remains conditional
on the native write and lifecycle premises and on valid pointers; arbitrary corruption and the
installed board's bus limits have not been qualified. No calibration is clamped and no timer
value is substituted.

Historically, two policies were compared: enforcing the calibrated timer and bus contract, or requiring a
settled motor with T2 clear (excluding the cycle) across all deep E0 processing. The latter
would change admission of INFO, upload and status, and could block future diagnostic
POSITION/JOG; it was neither selected nor implemented. The calibration enforcement below and
the private-E0 bridge keep command behavior unchanged and close their scoped conditional
software gates. No BUSY-during-motion policy and no new lifecycle or financial investigation is
requested. Installed-board timing and unknown peripheral state remain outside the proof.

### Calibration lifecycle and implemented enforcement policy

The three native clear variants zero both calibration bytes: `0x387b6/0x38850` clear
`[0x438000,0x43f800)` and `0x388e4` clears `[0x438002,0x43e800)`. **C=0 is a legitimate virgin
initialization**, not automatically corruption. A normal reset preserves C. Within the focused
write check and the valid-pointer SafeRAM premise, the calculated live update is at
`0x5df28`; adjacent header and game-record writes do not overlap the word. This is not an
exclusion proof against arbitrary pointers.

`0x5cf7e` sets the raw period `0x4f64` (20,324 ticks). During state `0x20`,
`0x5e080 → 0x5e642 → 0x5fed8` reloads it without subtracting C. Sampling starts when
`0x4101b6 >= 25` decimal. Native `0x5deec` waits for T1-low >= 15 decimal, then reads the high
byte at `0x5def8` and the low byte at `0x5df02`; `0x5df10` subtracts the assembled counter from
the raw-period word. D5 therefore reflects IRQ-masked latency, exception, kernel and dispatch
time and local sampling delay, modulo counter behavior; it does not reflect the preceding
300-tick task delay. Proving an untouched-history ceiling of `0x0800` would need bounds on every
relevant IRQ6-masked interval and coherent reads; the fifteen-count guard does not cover
arbitrary bus stalls. A large retained C can suppress later updates, so native calibration is
not a guaranteed repair.

The implemented boundaries preserve the accepted timer and calibration values, not
cycle-identical instruction timing:

1. At the boot-only hook `0x1906`, before `0x5cf64`, accept C=0 or `0x0060..0x0800`.
2. At `0x5df1a..0x5df28`, reject a computed candidate outside the strict interval before it
   is stored. Do not clamp or substitute a timer value.
3. Before the T2 enable and load at `0x5d790/0x5d798`, require the strict interval, so a virgin
   zero cannot enter the cross-call regime without first being calibrated.

The established non-returning rejection path is **TRAP #1 → `0x0fc4`** (vector entry `0x84`).
It sets SR=`0x2700`, records native diagnostics, then follows `0x13ee → 0x1274` (display) and
`0x1246` (fault counter) to the watchdog-only loop `0x123c`. It does not return or invoke the
calibration-dependent reloads. **It does not de-energize the last reel-drive latch.** This is
the stock CPU fatal halt, not a qualified mechanical shutdown or restart policy.

Do not substitute `0x5ff76`: its motor-fault path can publish state `0x10` and continue through
`0x60254` into calibration-dependent reload and recovery. No inspected recoverable rejection
path satisfies the required non-recursive failure condition.

The project owner **approved the native fatal-halt policy** for these three checks.
`firmware/src/platform_hooks.S` shares the range constants and the check macro, and
`firmware/patches.json` pins the complete replaced instructions. The candidate guard tests the
actual stacked word passed to `0x426`, including wrapped additions. The T2 patch uses an
eight-byte JSR+NOP region, not a partial overwrite of the native instruction. Thirty
calibration tests, 27 platform tests and 48 builder tests passed, and two scoped reviews
approved the ABI and patch-builder changes. Allowed paths match native registers, SR, SP and
stored and timer values. The fatal tests first require the executor to reject TRAP1, then
explicitly model its exception frame and execute the real `0xfc4 → 0x123c` path; they do not
simulate physical exception timing.

A fresh inspection of image
`ca7e0c9653df3d8648851d1679935fda674d72bd0bca003d0b45daabed20f6c5`
shows that `0x5d78e BNE → 0x5d80a` skips the guard to reach the cross-call check at `0x5d812`,
while the arming branch executes `0x5d808 BRA → 0x5d822`, skipping that check. Both recursive
handoff prefixes therefore exclude the new hook, and their earlier instruction bounds survive
for this positive branch reason. The timing of the initial T2 arming changes and is not
cycle-identical. On the fatal path the guard keeps its four-byte call return and two-byte saved
SR before TRAP1; these must be counted in the whole stack envelope.

This is software approval, not approval to flash chips, operate the programmer or test the
machine. No clamping, automatic repair or motor power-off is promised. Bus, silicon and
mechanical qualification remain separate gates.

The native cache routine `36a7a` has LINK −0x20 and seven saved registers: **64 bytes**, not the
spurious 32-KiB local array its decompilation shows. The examined installed-option-5/6 cache and
helper paths use at most 84 bytes below its entry SP, excluding the caller's return address. The
stock loader `3756c` has 68 fixed bytes of frame and saves before its outgoing arguments and
nested calls. These are local facts, not total stack bounds.

Tasks created through `3c54` run in supervisor mode. Interrupt save frames can consume an
interrupted task's stack, and the kernel's switch conditions and nesting matter.
`5d6ea ↔ 5e654` is a real call cycle with hardware-dependent waits, so summing the compiler's
`.su` frame sizes does not certify the 2-KiB native task stack. For the scoped conditional
software promise, that historical whole-stack gap is addressed by **Conservative native
interrupt components** and the later private-E0, initial-root and admitted-guard proofs. It is
not qualification of every untouched native temporary peak or of the installed board; no
repeated boot or RAM audit or financial-history investigation is requested.

### Native SAS request ownership

Two configurable parent roots, `0x16192` and `0x16242`, do not by themselves establish that
core requests can run concurrently. Before invoking callback `0x43e8f4`, their common path
`0x16366` must create child task ID 17 at `0x16410..0x16422`. Kernel task creation rejects a
duplicate live ID at `0x2cba..0x2cce`, and that error causes an immediate TRAP1 at `0x16432`,
before the parent's callback runs. The child `0x32562` loops through `0x327fc → 0x32570` rather
than returning to delete itself.

The examined native reconfiguration path also tears down in a positive order: `0x16682`
deletes the per-port parents using the same task IDs as creation in `0x152ec`, and only after
that loop does `0x166f4` delete child 17. Caller `0xc1a8` then initializes the protocols through
`0x15836` and creates new parents through `0x152ec`. The delete wrapper is `0x3c8a` (service 1);
`0x3cb0` is suspension, not deletion. This supports a single active dispatcher under valid
native task IDs and no out-of-band service calls; it is **not scheduler-lock exclusion**.

This is ownership evidence for the serialized-core contract stated in `fw.h`, not a universal
guarantee against external invokers. Boot, reset and clear ordering and runtime native
transitions remain separate premises. The absence of two live callers does not by itself prove
consistency after asynchronous task deletion. The serial drain implemented below closes that
reproduced lifecycle failure for the examined native route. No machine incident is claimed.

### Earlier stack-capacity warning

In the guarded image described above, `fw_process` has 572 fixed bytes below its entry SP: LINK
allocates 528 bytes plus the saved A6 (4), and MOVEM saves 40. Its inlined `commit_profile` slot
buffer is paid even by pre-admission journaling. At
`record_operation → write_committed → fw_storage_write/platform_write_words`, the resident depth
from the audited SAS task top is:

```text
260 fw_process entry + 572 fixed
+ 16 call/arguments + 160 record_operation fixed
+ 16 call/arguments + 20 write_committed fixed
+ 16 call/arguments + 8 platform_write_words saved = 1068 bytes
```

The word writer restores the incoming SR between writes, so this is a point where interrupts
are open, not a fully masked persistence operation. Emitted prologues and live call sites,
rather than compiler `.su` totals alone, establish these local costs.

An auditor-reported candidate sum for nesting IRQ1 through IRQ7 was
`1068 + 194 + 378 + 114 + 70 + 66 + 110 + 154 = 2154`, above 2048. This historical
overapproximation was not a witness of a simultaneous state. The conditional components and the
private allocation below supersede that warning. It was a capacity warning, not a demonstrated
overflow, an observed machine failure or an upper bound for the whole stack. Calibration guards
alone do not close it. Moving stack locals into shared work storage would also require positive
serialization of the core: a shared callback symbol does not prove that only one SAS task can
enter it. The positive native ownership argument above is the relevant evidence. The core-owned
512-byte slot workspace is implemented and reviewed inside the existing reserved SRAM. The
numbers above describe the older image; the recalculated `d15f8398` costs below supersede them
for that historical build.

### Slot-workspace reduction

Image SHA-256 `d15f83984deaca1bb606b9a71c1d179714c0fec51bd96d4f9bccdb8fa5417e03` has a 112-byte
fixed `fw_process` frame, down from 572. The core runtime occupies 918 bytes within
`[0x430600,0x430a00)`. The change passed 52 core tests, 15 selected integration tests and a
scoped source review; these do not certify the stack.

The chain in the emitted disassembly `firmware/build/ext.dis` (a build output) is:

```text
native SAS parent → platform_sas_handler → fw_process
 → fw_submit → game_submit → 0x548bc(counter47)
 → 0x3bbae → 0x3bc5c → 0x3c998 → 0x3cf86
```

`fw_process:0x7afe6` holds 372 bytes including its callers. The submission at `0x7b684`, the
wrapper `fw_submit:0x7d68c` and `game_submit:0x7c430` reach 460. The counter comparison called
at `0x7c60e` reaches **644 bytes**; its scheduler lock restores the caller's SR, so this remains
a depth with interrupts open. Operation and profile journal writes reach 616 and 628 bytes. An
earlier figure of 608 stopped at the word writer's entry and omitted its two register saves.
These counts include live arguments, return addresses and the changed helper frames, rather than
subtracting the buffer size from an old total.

### Conservative native interrupt components

The audit reports one component per priority level. Each includes the exception frame,
register saves and the returning kernel services the level uses, but excludes higher-priority
interrupts nested inside it. IRQ2 has the explicit ledger in the next section instead of its
initial 400-byte allowance. An independent instruction review accepted these scoped,
conditional components:

| IPL | Bytes | Dominating inspected path |
| ---: | ---: | --- |
| 1 | 194 | `0x141b2 → 0x14068 → 0x19732 → 0x14b48 → 0x14450` |
| 2 | 640 | bill validation, selected communication dispatch and returning services below |
| 3 | 114 | `0x13874 → 0x1372a → 0x1475c → 0x16976 → 0x16b08` |
| 4 | 302 | `0x2646 → 0x411ce → 0x53dd0(A8) → 0x45c94 → 0x3bdca → 0x3bc5c` |
| 5 | 66 | `0x26e4` register-save/output-write path |
| 6 | 434 | completion/F6/counter47, including an outer T2-handler allowance |
| 7 | 154 | `0x275a → 0x41796 → 0x41b3e → 0x549c8(43)` |

Their sum, 1904 bytes, is the interrupt allowance I1904 used throughout the audit records
(derived in **IRQ6 prefix and component arithmetic**).

### IRQ2 communication and service ledger

The entry at `0x13d98` holds 66 bytes of exception frame and register saves. The driver calls at
`0x13db0` are sequential, and each FIFO iteration returns before the next begins. RX-A
`0x13bf4` saves 16 bytes, giving 94 bytes, calls included, at the entry to `0x33dfa`. RX-B
`0x13c46` either uses the ring or calls `[0x43e8f0]`; the constructor `0x16366` and the tables
`0x74aca/0x74ada` install the qualified target `0x19732` there. RX-B, the TX handlers
`0x13d24/0x13d5e`, the ring helper `0x14718` and the output helper `0x364ca` are all smaller.

The event keys reachable from IRQ2 are 37, F0, F1, F2, and F6 indirectly via
`0x34202 → 0x34d22` (all keys hexadecimal). The selected dispatch targets are:

| Router | Selected arms |
| --- | --- |
| `0x2640c` | `37 → 0x26704 → 0x258d6`; `F6 → 0x26918 → 0x549c8(47)`; other keys default |
| `0x23d40` | `37/F0/F1/F2 → 0x242b2 → 0x246ac`; F6 default |
| `0x1b8ca` | `37 → 0x1bbf0`; `F0/F1/F2 → 0x1beb4`; F6 default |
| `0x18bea` | `37 → 0x18caa`, payload or reset/status sender; other keys default |
| `0x26fc8` | `37 → 0x270b4` return; other keys default |

Every valid bill-status alternative is allowed, so this bound does not depend on a higher
interrupt preserving one particular status. The conservative subtree ceilings below include
live arguments and call returns:

| Subtree | Bytes / derivation |
| --- | --- |
| Numeric/BCD family | 272: fixed-descriptor getter/copy/compare, CRC and formatting |
| `0x34492` | 336: 32 fixed + 20 outgoing + 4 return + 272 |
| `0x3440e` | 384: 16 + 16 + 4 + 336, rounded |
| `0x341ac` | 392: 4 outgoing + 4 return + 384 |
| Each selected protocol arm | 384 |
| `0x26a0e` | 400: 4 fixed + 8 outgoing + 4 return + 384 |
| `0x34746` | 424: 4 + 16 + 4 + 400 |
| `0x34d22` | 428: 8 + 16 + 4 + 400 |
| `0x34202` | 440: 8 outgoing + 4 return + 428 |
| Every direct `0x33dfa` callee | 448 |

The protocol allowance includes the retained formatting arguments and counter-47 getter of
`0x258d6`; the conversion and send branches of `0x246ac`; the selected format and queue tail of
`0x1b8ca`; and the reset/status sending of
`0x18bea → 0x18970 → 0x18a8e/0x18936 → 0x18828 → 0x14c66`. `0x14c66` is charged 128 bytes and
the queue copy `0x17b14` 80 bytes. That 80 is justified by the **selected active-IRQ** maximum
of `17b14`, 50 bytes, not by the general task-side arithmetic 20+4+64, which gives 88, not 80.
The active-IRQ figure is fixed 20 + call 4 + IRQ service 26 = 50; the multiply at `17b9a/17d04`
retains 8 and saves 8, giving 20+8+4+8=40 (not 36). The general task-side allowance remains
20+4+64=88. The premise that a registered interrupt cannot schedule is stated in
**Guard-domain closure and IRQ6 selected cases**. No increase of the 640 or 434 components
follows.

The returning wrappers and kernel services fit within the included 64-byte service ceiling:
`0x3d82/0x3d88` (SC20/21), `0x3a32` (SC8), `0x3a1e` (SC0c), `0x3b84` (SC26) and the retry
wrapper `0x3d8e`. Interrupt accounting prevents a task switch while IRQ2's C frames are live.
Retry arguments are popped at `0x14cb0` and `0x3dc8`, so these backward edges do not accumulate
stack; that is not a liveness guarantee.

The total is 94 driver prefix + 8 fixed in `0x33dfa` + 24 outgoing allowance + 4 return + 448
direct-callee ceiling = 578, rounded up to **640**. The largest inspected outgoing branch of
`0x33dfa` uses 16 bytes rather than the allowed 24.

### IRQ6 completion contribution

The IRQ6 path is `0x5e1e2 → 0x5f452 → 0x34d22(6) → 0x26a0e(F6) → 0x2640c → 0x26918 →
0x2696a:0x549c8(47)`. It needs nonzero finalization and line-enable fields, a zero selection
mask, option 32 enabled, full mode 2, `0x43d12e.bit5=1` and `0x43e8d2.bit0=1`. AS
initialization can coexist with SAS without claiming child task 17. Admitted profile changes
exclude this completion path; general and pre-admission E0 processing does not. Direct T1 entry
costs 410 bytes; the 434 component conservatively includes an outer T2 frame without asserting
that both branches occur together.

### Historical private E0 stack and scoped offline qualification

The E0 stack qualification image, from before the serial-drain change, has SHA-256
`984cb4f4e1f54ce53c50b0ad4ee18a6be9d25cd63e8e85f162ec86bf143345d2`. It implements the
frame-free bridge `platform_sas_handler:0x7d960`:

```text
0x7d960  save caller SP at 0x434020
0x7d966  select private top 0x434000
0x7d96c  JSR platform_sas_process:0x7d79e
0x7d972  restore caller SP
0x7d978  RTS
```

The private range `[0x433000,0x434000)` is commissioning's stack before the kernel starts, so it
cannot still be in use when runtime E0 begins. Motion's 24-byte state moves to
`[0x430a00,0x431000)`. Linker and size assertions bound the reservations. No native allocation,
TCB top, interrupt mask or core algorithm is changed.

Task creation at `0x2d0a..0x2d4a` builds a new 26-byte context from `TCB+0x40`. A context switch
saves the actual SSP at `0x2b30` into `TCB+0x38` and loads it at `0x2b54`; USP is separate.
SC9/SC0c blocking uses the same context. The standard service table, inquiry and scheduler
paths apply no pool or stack-top test when a task resumes. The inquiry callers `0x15138`,
`0x751a` and `0x16714` use IDs, priority and status, not stack fields. The creation, deletion
and switch hooks at configuration `0x27c4..0x27cc` are zero. Deletion unlinks the task at
`0x2e12` and removes its waits and timers at `0x2e24..0x2e6e`; recreation overwrites the saved
SSP before inserting the replacement.

With one live callback, and deletion that is irrevocable before any replacement, an old saved
SP is disposable: each new entry overwrites it. Suspension alone does not make it disposable.
Returning interrupts preserve whichever stack is current. User-mode tasks, external hooks,
corrupt kernel objects and recursive or concurrent callback entry are excluded as premises.

The inspected counter chain charges 588 bytes to the private stack: 644 minus the 60
native-prefix bytes, plus 4 for the bridge's worker return. The C frames are unchanged. This is
one checked path, **not** the bound for the whole worker. The independent review instead uses
1024 bytes for all reachable worker paths: 224 worker + 160 core + 512 branch subtree = 896,
rounded upward. This covers profile preparation and application, journals, counters, CRC,
NACK and send, and the returning kernel services. With the revised native interrupt components
totaling 1904:

```text
native entry/exit: 60 +1904   =1964 <=2048  (84 bytes spare)
private worker:   1024 +1904 =2928 <=4096  (1168 bytes spare)
```

The 26-byte initial context created for a task is consumed by register restoration and RTE; it
leaves no surviving task-entry trampoline. The native-window bound requires the bridge to stay
frame-free; any later entry or exit helper needs new accounting. These sums deliberately
overapproximate simultaneous interrupt compatibility; they do not demonstrate an overflow. They
require valid native pointers and counter copies, no external service invokers, the timer and
bus premises above and a single filtered terminal AC-fail assertion. Repeated IPL7 edges and
every unmodified SAS command are outside this result, and it does not authorize hardware
installation.

The scoped review of source, tests and ledger approved the result. The affected gates passed
221 platform, motion, commissioning, game and host cases and seven abandonment subtests.
`firmware/tests/test_e0_kernel.py` adds three passing real-kernel tests, and the two existing
CPU-status regressions also pass. Its temporary image contains the full production sources plus
test-only task programs, not kernel stubs, and it checks the production bridge and C bytes and
the original kernel bytes. TRAP0/RTE and explicit IRQ5 entry are modeled architecture
boundaries; UART completion is an explicit test event.

The tests execute native initialization, SC12 blocking and resumption, SC1 deletion and SC0
recreation of the same TCB. They verify unchanged allocated tops, retention of the private SSP,
a distinct replacement depth, no old continuation, restored registers and SP, and the stack
guards. IRQ5 runs at every bridge boundary on both stacks. Deliberate scratch mutations (no
stack switch, reuse of a stale saved SP, an out-of-bounds private top) are detected. No physical
timing claim follows.

**Historical E0-only scope:** that scoped audit did not bound Task6 event, normal-start,
outcome, evaluator and wager work, Task7 polling and cleanup (even with `MOTION_QUALIFIED=0`),
or the whole boot and restore. Their separate complete mathematical composition is in
`INITIAL-ROOTS.md`; do not extend the E0-only result itself to them. Commissioning keeps its
earlier separate qualification. Reasoning about reset and lifetimes assumes supervisor
execution, a fresh reset SP and no resumption of an activation from before the reset; arbitrary
soft-reset paths were not newly investigated here.

### Reproduced serial-cancellation inconsistency

This section records a lifecycle failure that native serial reconfiguration could cause before
the serial drain existed.

On the earlier `d15f8398` image, an offline probe discarded one compiled call stack while keeping
RAM; it did not run the native scheduler or model interrupt timing. After the durable ACCEPTED
marker but before runtime publication, INFO offered an ID already reserved in the journal. After
the durable final OK marker but before runtime publication, an identical retry stayed ACCEPTED
and a new request returned BUSY. An explicit `fw_boot` reconciled both cases, but normal serial
reconfiguration does not run that recovery path.

A second probe, on the published `984cb4f4…` image, reproduced both windows using actual native
SC1 deletion, not merely a discarded call stack. It executes the real `fw_boot`, the production
SAS bridge and core, and the full unchanged `c1a8 → 16682 → 15836 → 152ec → c1ba` sequence. A
memory event on the commit marker selects a cut after the word writer restores SR and before
runtime publication. A test-only interrupt and tick shim wakes the configuration task through
native SC22/SC18/SC17. Registered parent 13 is deleted and appears on the free list; it never
reaches its return marker. A separately scheduled observer then makes INFO, STATUS and retry
calls without a reboot or host-side repair of TCBs or contexts.

The historical scratch probe (not needed for reproduction) uses explicit test priorities and
callers, the narrow TRAP0/RTE CPU model and register-memory MMIO. Ports are disabled and the
native configuration is marked initialized (`0x55aa`), which avoids virgin RTC setup; parent
recreation runs without creating enabled-port tasks, and child 17 is absent. These are offline
lifecycle results, not physical timing, an observed machine incident or proof of an actual
duplicate side effect.

### Implemented serial drain and offline gate

The native context and scoped cancellation gates are closed for the pinned ROM, under valid task
IDs, a balanced kernel history and no external corruption. `platform.c:platform_reconfigure`
first implemented the protocol below in historical image
`b2ce1bc5c03b8e8af47cf246115968033f62574c2a3ae2d2d7e5c4f225adcb83`, and the reviewed `06bef891`
publication preserves it. This is not installation qualification.

Task7's mailbox dispatcher `b080` serializes the relevant routes. Its case 8 calls the
communications test `132ee` at `b2b8`. The test suspends the registry parents at `132fc`, but
every normal path to its epilogue `13656` first resumes them through `16614`: the early exits
`1336c/133d6/1340a/134f6/13560/13630` and the completion at `13646`. Helper `131b4` can
additionally resume them at `132cc`; it never suspends. The test returns through `b2be → b088`,
not into configuration, which is the separate case 11, `b32c → b33e:c0fc`. The communications
test therefore never leaves a suspended SAS owner for the drain to wait on.

Task7 is created by the supervisor-task constructor at `1a72..1a80`. The configuration route
introduces no lasting scheduler lock or interrupt mask: `044c` restores SR at `478`; `35aec`
reads the type-2 descriptor `73bc4` through its direct byte-read arm; `4e37a → 548bc` balances
`548da:3d82` against `548fc:3d88`; and `36540` uses mailbox inquiry and delay. This is not a
guarantee for arbitrary histories or external invokers. The short critical section must restore
its SR before pending on the completion mailbox.

The hook replaces the entire 18-byte **call site** `c1a8..c1b9`, not a function entry:

```text
4eb900016682 4eb900015836 4eb9000152ec
```

Its no-argument wrapper must preserve the native callee-saved registers and execute close and
drain → `16682` → `15836` → `152ec` → reopen, then continue at `c1ba`. The verified order is
deletion before protocol initialization and recreation; it does not mean nothing global changed
earlier: `c116` already clears `43e8de`.

The private one-slot mailbox uses native SC8/SC9 through the wrappers `3a32/3a52`. SC8 requires
a nonzero token; its errors 5 (full) and 6 (zero token) leave the mailbox unchanged. The
wrappers' errors are **WORD outputs; SC8's D0 is not its status**. SC9 consumes a latched token
or parks atomically, and timeout 0 does not join the delay list. Probes on the real ROM cover
both post/pend orders and deletion of the notifier while its post wrapper is still pending at
`3a42`.

The reviewed ownership protocol is:

1. Atomically close core admission, require an empty mailbox and latch a local
   `must_wait = active`. Guard every `fw_process` call, including STATUS polling.
2. When the core returns, clear active and capture the notification before posting. A woken
   drainer may delete the notifier before its SC8 wrapper returns, so no shared cleanup may
   remain afterward.
3. If `must_wait` was true, consume exactly one completion token even if active has since
   cleared. Rechecking active instead would leave a stale token.
4. Keep admission closed through the original delete, initialize and create sequence, and
   reopen only after it completes with no active core and no pending token.

The completion boundary is in `platform.c:platform_sas_process`, immediately after `fw_process`
returns and before NACK handling, response construction or transmission through `278fe`. Do not
wait for UART TX: a stalled transmitter may itself need reconfiguration. Core consistency and
transport delivery are separate contracts. The mailbox is the aligned longword
`sas_drain_mailbox` at `434024`, following `sas_saved_sp`; `platform_boot` clears it and both
admission flags. Unexpected ownership, token or kernel-status failures enter `platform_fatal`,
the shared native TRAP1 leaf; they neither discard evidence nor attempt recovery. As with the
calibration failure policy, the native fatal halt does not switch off coil power.

The affected gates passed **123 tests plus six subtests**: 64 platform, 30 calibration, 15
selected builder, two host SAS, seven drain, three E0 kernel and two CPU-status cases. Ruff
passed, and an independent review of source, tests and evidence approved the scoped gate.
`firmware/tests/test_serial_reconfigure.py` exercises both journal-publication windows in both
post/pend orders, closed admission during initialization and recreation, repeated inactive
drains, native suspension and resumption before the gate, and cancellation of stuck post-core
TX. The shared native executor is `firmware/tests/kernel.py`; CPU execution semantics were not
changed.

A controlled test interrupt using native SC4 schedules the early-post case immediately after the
close restores SR, before the branch that decides whether to pend. This is an ordering test, not
a claim about physical priorities or timing. The suspended-owner fixture runs native `16614`,
not the whole communications UI, and ports stay disabled in these kernel cases. Eight
negative controls built as temporary variants were rejected: six with a missing hook, one with
the guard wrongly extended through TX, and one that wrongly skipped completion after rechecking
active. The durable gate tests and the CPU-boundary regressions are in the repository; the
historical scratch mutation logs are not needed to recover the protocol, the native locators or
the qualified result.

In historical published b2 the bridge remains five instructions at `7dac8..7dae0`, calling
`platform_sas_process:7d86a`. The worker's fixed frame rises from 180 to 188 bytes, and the
inspected counter path from 588 to 596. Its core-entry depth is 212, and the new post-core SC8
path reaches 212 before exception and kernel accounting. An independent review confirms that
these paths, fatal branches included, stay within the existing **1024-byte worker allowance**;
the historical b2 envelopes remain 2928/4096 private and 1964/2048 native under their stated
premises. Candidate `06bef891` keeps the same peaks with native capacity 3072, so its native
figure is 1964/3072. That capacity change is not attributed to the historical b2 bytes.

The configuration wrapper `7d7d0` has 12 fixed bytes. Task7 holds 8 bytes before the patched
call site: its saved D2 plus the `b33e:c0fc` return; `b106` and `c192` remove accumulated
arguments. The wrapper adds 16 bytes at each original native callee entry (12 → 28). SC9 entry
is 40 bytes from the task top, 52 after its LINK, before exception and kernel accounting. These
are local costs, **not a bound for all of Task7**. The complete non-E0 mathematical composition is
in `INITIAL-ROOTS.md`; the maintained-document review and local publication are complete, and
physical qualification remains separate.

For historical b2, all 20 build inputs (including the M68000 libgcc variant) and eight published
artifacts were verified against the reviewed snapshot without rebuilding it. It has 38 patches,
19437 extension bytes and a boot word sum of zero. The original ROMs are unchanged, and
`install_ready=false`.

## Normal AC-fail reservation result

The examined terminal path IRQ7 `275a → 445e → 365d4 → 41796 → bf0` has positive destination
evidence: it writes native SafeRAM, not the reserved extension area `430000..435fff`. `365d4` copies
8 bytes to the current native record + `4ef`; the twelve startup records of `50e` bytes occupy
`43862e..43c2d5`. Seed and counter updates target native SafeRAM, and the inspected input and
display state uses fixed `410xxx` scratch and MMIO.

This is conditional on valid native record pointers and no stack overflow, and it does not cover
arbitrarily corrupted pointers. It is not a claim about battery retention, and it is not an
allocation proof based on missing cross-references.

## Next verification boundary

The durable IRQ4 and timer-admission regressions keep their scope of register memory and the
coin body; they do not model physical peripheral timing. The historical E0 and serial-drain
software gates remain closed under their premises. For the settlement candidate, the emitted
ABI, tests and negative controls, the final-image producer and frame seal, the source review and
the complete mathematical composition of initial and ordinary roots all passed independent
review. `INITIAL-ROOTS.md` keeps every selector, the producer and root checklist and the finite
bounds. Full reads of the records, an independent review of all seven maintained documents, all
three embedded reproduction scripts and the final verification of inputs and artifacts passed.
The exact eight reviewed artifacts are published locally without a rebuild, still with
`install_ready=false`; see `SETTLEMENT-GUARD.md`, **Candidate gates**. No financial-history
investigation or hardware run is claimed. Physical bus, timer and silicon qualification remains
separate.

## Guard-domain closure and IRQ6 selected cases

This section proves that no returning interrupt and no private-E0 worker path can reach the
settlement routine or a covered hook. It is a positive **called-target** exclusion for the
returning same-stack interrupt bodies and the frozen-b2 private E0 worker; it is not inferred
from the 1904 and 1024 byte budgets. It reuses these premises: valid initialized native
pointers, objects, counter copies and selectors; supervisor execution with intact contexts;
zero optional kernel callbacks and no external invokers; the calibrated timer and bus premises;
and a single terminal IRQ7 assertion. No fresh-NVM or settled-idle assumption is used to leave
out active motion phases.

Candidate `06bef891` preserves these domains in its final linked image; any later code change
requires resealing. `SETTLEMENT-GUARD.md`, **Producer seal**, supplies the regional seal over
computed and interior entries, and **Candidate seal and offline gates** records the independent
final-image check, the passed affected tests and the source review. These checks preserve the
local IRQ and private-E0 exclusions. `INITIAL-ROOTS.md` separately closes the ordinary-task
callback selectors and their nesting; the interrupt component numbers alone never established
that conclusion.

### Returning UART domains

| IRQ / root | Complete callable driver and parser families |
| --- | --- |
| 1, `141b2` | `14032/14068/1413e/14178`; ordinary `14718` rings; RX-B `1411e` calls `43e8f0` |
| 2, `13d98` | `13bf4` → `33dfa` bill; `13c46` ring or `43e8f0`; `13d24/13d5e` TX |
| 3, `13874` | `1372a` → `1475c` → `16976` framing / `279a8` BCD validation / `16b08` response state; `1377a` → `186e6` or `272be` framing, or `14718`; `137fa/1383a` TX |

The constructor at `163d6..1640e` copies the immutable `74aca:19732` into `43e8f0`. The entry
`74ace:19b16` belongs to the **different** pointer `43e8f4`, which the serial **task** uses.
`19732` handles SAS framing, mailbox `3a32`, `14b48` kicks, or `1a432` DUART cleanup and TRAP1;
it does not execute the `744ca` command table, `19b16` or E0 inline. The only self-call of
`16b08` sets `fb00`=1 before re-entering, then uses response and data publication. `186e6` and
`272be` finish or reset byte buffers; they are not business-command handlers. The other
`146xx`/`14xxx` alternatives are hardware and ring leaves. The bounded six-port switch in `14b48`
chooses `14374/14396/143b8/143da/1442e/14450`, not a packet callback. The IRQ1–3 autovectors at
`2642` only execute RTE.

IRQ2's selected keys 37/F0/F1/F2/F6 and every valid bill-status alternative are kept in
**IRQ2 communication and service ledger**. Its exact arms in the five protocols end in numeric,
format, queue and TX families, never in the incoming `744ca`/`74af2` business selectors. Those
positive selections, not an absence of call references, exclude a financial callee. The
640-byte bound is unchanged.

### IRQ4/5 called targets

IRQ4 at `2646` calls `411ce`, optionally `39040`, the kernel tick, and a scalar `44c`
decrement. The union of calls out of `411ce` is `400`/`44c`, `3b84`, `35b48`, `36896`,
`41152/41170/411ae`, `549c8` and `53dd0`. All three `53dd0` calls (at `41470`, `414b6` and
`416ee`) load the literal opcode A8, for which table `53dfa` selects `45c94` with the fixed
numeric descriptor `69f5c`; no arbitrary opcode is inferred. `39040` → `394bc/3953a/39a52/39aac/400`
manipulates output masks and linked DATA records, not code pointers. `41152/41170/411ae` use
enables, counters and options. The tick `3258` → `37f6/37ee` changes ready and delay links.
`3b84` posts a future task event rather than running a handler inline. IRQ5 at `26e4` performs
only shadow and MMIO stores plus kernel entry and exit.

### IRQ6 all-phase called targets

**IRQ6 entry and bounded dispatch** already covers all ten valid state-table targets, the
default `5e63c` and the special state 4. A branch-complete worklist of direct calls in
`5d6ea..5e654` gives:

`5ff2c, 5e654, 5ee14, 5ed22, 5ff76, 3a14, 5fcb2, 5eba8, 5fed8, 3b84, 400, 26a0e, 68c4c, 5ec78, 5f022, 426, 5d368, 68b90, 60442, 5ee00, 5f452, 60310`.

`5e654` adds `35b48`, `578a0`, `34d22` and the cross-call back to `5d6ea`. Their descendants are:

- optical and phase sampling: `5eba8/5ec78/5ed22/5ee00/5ee14`;
- timers: `5fed8/5ff2c/5fcb2/5fcda`;
- the random-number generator `5fe2a → 380d6`;
- scalar SafeRAM and MMIO stores;
- faults: `5ff76 → 6016e` with the fixed `3b04a` counters, or the `60254` motor tables;
- `60310/60442` motor finalization and reinitialization;
- `49f5e` output mask, including the fixed-byte predicate `49f6c → 39fb4 → 39f96`
  (`INITIAL-ROOTS.md`, **Positive local-domain boundaries**), not a hidden controller.

`5f452` reads payout and cache DATA and numeric counter 43, queues and starts motion through
`5d368`, and emits notifications. It does not call `65128` or the controller tables. A refusal
by the calibration hook uses the established TRAP1 halt.

IRQ6 produces communication keys in three places: F6 via `34d22`; reel + 76 (hex) at
`5dd9e/5e93c`, for valid reel indices 0..4, which gives keys 76–7A; and 91 at `5ea44/5f798`.
Their additional selected table targets are:

| Keys | AS `2640c` | SAS `23d40` | BSG `1b8ca` | MIS `18bea` | SDT `26fc8` |
| --- | --- | --- | --- | --- | --- |
| 76–7A | `269a8` default | `23f7a` | `1bcdc` | `1905c` default | `270b4` default |
| 91 | `269a8` default | `2468a` default | `1bd8e` | `1905c` default | `270b4` default |

The `26a0e` prelude `37322` runs only for keys 76..7A: `548bc(counter43, cap)`, `68c4c` and a
30-entry fixed-frame scan of cache status. The immutable descriptor `6a14c` is
`00438034 0103 0043` (type 1, width 3, nonzero pointer), which selects the immediate return of
`3bc5c` and the direct-pointer arm `3ca34` of `3c9f4`. The local subtree ceilings, each
measured below the routine's own entry, are S(`3bbae`)=100, S(`548bc`)=124 and S(`37322`)=148;
adding the router's 12-byte prefix gives S(`26a0e`)=160. SAS `23f7a` and BSG `1bcdc` compute a scalar reel position
with DIVU and `68c4c`, optionally `38b96`, and then call `17b14`. BSG key 91's `1bd8e` calls the
leaf `15288` and the common send; fixed frame 76 + 12 argument bytes + 4 call + the IRQ-only
`17b14` (50) = 142, and the 12-byte router prefix gives 154. The AS default fill `35cf4` costs
64 bytes below protocol entry, and the default disables sending. The SAS, MIS and SDT defaults
return. No selected case falls into a business arm.

### IRQ6 prefix and component arithmetic

S is measured below the `26a0e` entry and excludes that entry's existing return address. The
common exception, kernel and register prefix is 66 bytes, and the body's return 4. The T1
handler's fixed frame (F) is 40 bytes and the T2 handler's 20. The conditional timing argument
permits an outer T2 frame (+24) around T1, or an outer T1 frame (+44) around T2, but not a whole
recursive round. Both prefixes remain protected by the calibrated timer and bus premises.

| Router branch | Whole IRQ depth at entry | S | Whole component |
| --- | ---: | ---: | ---: |
| 76..7A | 142 | 160 | 302 |
| 91 | 194 | 154 | 348 |
| existing F6 | 222 | 212 | 434 |

At `5f798`, the 194 is 66 + 4 + 40 + 24 (outer T2) + 4 (return to `5f452`) + 48 (fixed frame of
`5f452`) + 4 (key) + 4 (call). Direct T2 entry is smaller. A final control-flow and SP worklist
over the original ROM, covering `5d6ea..5e654` (including the ten `5d9ca` entries),
`5e654..5eba8` and `5f452..5fb78`, found zero unknown local control-flow edges. Depths before
each JSR, relative to the function's entry, were `5dd9e`=44, `5e3ce`=40, `5d81c`=40;
`5e6ee`=20, `5e93c`=24, `5ea44`=24; `5f76c`=52, `5f798`=52. Each edge adds its 4-byte return;
retained arguments are included, not just prologue sums. Normal returning hooks preserve this
shape, and final emitted equivalence was verified for candidate `06bef891`.

The IRQ-only `17b14` has 20 fixed bytes, and `3d82/3d88` are frame-free. A supervisor TRAP0 adds
a 6-byte exception frame and 20 bytes of saved registers; the selected `33e0/33f2` make no
calls. While a registered interrupt is active, workspace+28 stays nonzero, so `2956` branches to
`298c` and never reaches the scheduler `2a90`. The peak is 20 + 4 + 26 = 50. At `17b9a/17d04`,
the multiply holds 8 bytes of retained scratch + 4 return + 8 saved, so 20 + 8 + 4 + 8 = 40. The
general task-side allowance is 88, **not** 80 and not the IRQ figure of 26.

Therefore max(434, 302, 348) = 434, and the accepted components sum to
194 + 640 + 114 + 302 + 66 + 434 + 154 = 1904. This overapproximates co-occurrence. It does not
claim that all maxima coexist, nor that every untouched native subtree is qualified.

### Private E0 called-target worklist

The worklist of direct, PC-relative and tail calls from the frozen-b2 `platform_sas_process`
includes every fixed register-call producer that the compiler hoisted, listed below. These are
immutable LEA-loaded code addresses, not host or `fw_runtime` callbacks. Candidate `06bef891`
preserves these target families through its verified instruction and relocation seal; future
changes require resealing. The addresses below are b2 provenance.

| Family | Fixed producer → target / call sites |
| --- | --- |
| `get64` A2 | `7a01e` → `get32`; `7a024/7a034` |
| `write_committed` A2 | `7a106` → `fw_storage_write`; `7a10c/7a128/7a15e` |
| `record_operation` A4/A3 | `7a526` → `get64`, `7a54e` → copy; `7a52c/7a554/7a5b4/7a5da` |
| `fw_validate_profile` A4 | `7a84e` → `valid_symbol`; `7a88e/7a89e/7a8ae` |
| `fw_process` A2 | `7b2e4` → zero at `7b2ea/7b2f6`; `7b4c0` → `put32` at `7b4c6/7b4de` |
| `fw_process` A3 | `7b3f4` → `fw_crc32` at `7b3fa/7b4d2`; `7b748` → `put64` at `7b798/7b7f8` |
| `game_apply_profile` A2 | `7ca38` → `config_sum`; `7ca3e/7ca4c/7ca66` |
| `game_apply_profile` A3/A4/A5 | `7ca08/7ca0e/7ca14` → `platform_safe_u8/u16/u32`; `7ccc4/7ccd2/7cb94` |

The core dispatch at `7b278` with table `7b27c` is bounded by a byte compare with 20 decimal
(0x14) and BHI at `7b264/7b26a`: 21 targets inside the function, none of them a native game
callback. The union of native boundary calls is:

- `185ac` CRC;
- `27536` NACK and `278fe` reply → `14c66` copy, retry `3a1e` → `14b48` six-port kicks;
- `3a32` mailbox;
- `3d82/3d88` fixed locks;
- `400/426/44c` scalar stores;
- `platform_write_words`: range-validated writes, SR and watchdog;
- `549c8/548bc` with the fixed counter 43/47 numeric descriptors;
- `36a7a` → options 5/6, `363c2(7)`, `68c4c` and bounded DATA scans;
- the TRAP1 native diagnostic halt.

The counter helpers end in the numeric families recorded in `NATIVE-STACK-LEDGERS.md`, not in
controllers. Concrete b2 calls include `7d8ec:185ac`, `7d4fc:27536`, `7da26:278fe`,
`7d9d2:3a32`, `7ca7e:36a7a`, `7c60e:548bc`, the tail call to `549c8` at `7b904`, and the scalar
tail adapters.

`game_submit` only records an asynchronous request; it does not call `4341c`, `420f8`, `ae6a`
or `61172`. The owned 177-byte restoration does not call `3756c`. In the production image
(`MOTION_QUALIFIED=0`) motion requests return UNSUPPORTED. These positive domains, not the
1024-byte ceiling, exclude a synchronous financial entry on the private stack. Notifications
can resume native financial tasks, but on **their own** selected contexts.

### CURRENT publication, terminal IRQ7 and fresh-SP exceptions

Kernel queue and mailbox messages are **values**. The fixed standard services at `2948` update
queues and TCBs; the configured optional hooks at `27c0..27cc` are zero, and unregistered
extended dispatch stays disabled. Interrupt entry (command 16) establishes nesting; exit
(command 11) happens after the body and its register restore. Returning internal services
cannot schedule while workspace+28 is nonzero. Ordinary context switches resume the selected
task's SSP; they never execute task handlers inline on an interrupt or private-E0 call chain.

Therefore no returning interrupt runs the settlement guard during the mixed publication window
`2b54..2b70`, in which the new task's SSP is already loaded but CURRENT still names the old
task. IRQ7's terminal domain on the old stack, `275a → 445e → 365d4 → 41796 → bf0`, uses valid
record copies, seed and counter updates, `41b3e`/counter 43, `24ae`, the `4185c` sensor and
`448c`; `4185c` uses `400/411ae`, not wagers. `bf0` selects `4385dc` and halts on the watchdog.
Repeated IRQ7 assertions remain excluded.

The divide-by-zero vector at `14` leads `f54 → 108a → 115c → 1184:118a`, which loads SP=`437ffe`
and abandons the old frames. For the new IRQ6 cases the old-stack maximum below the router is
92 (fault site) + 6 (exception) + 42 (diagnostic SC05) = 140, below the 160 and 154 above.
SC16's active-IRQ `+f2` stays above 1, so it cannot take the alternate-stack copy at `284c`, and
SC05 cannot schedule while `+28` is nonzero. Its snapshot IDs at `74eb4` end with `00`, so there
is no growing loop. No premise of a nonzero `41017c` is invented. Later native diagnostics
either halt or reload SP again through `1196 → dee → 8f6`; `SETTLEMENT-GUARD.md`,
**IRQ, private E0 and exception separation**, preserves the intermediate domain in which no
covered hook runs.

Bootstrap financial entry is real, but it belongs to the fresh arena `[436000,438000)`, not to
old interrupt or private-E0 residency. TRAP1, by contrast, halts without silently discarding its
stack before writing diagnostics. Neither is a promise of financially safe recovery.
