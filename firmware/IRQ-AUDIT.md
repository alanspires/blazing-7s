# Native interrupt / profile-publication audit

## Scope and status

Read-only audit of original `roms/s6000_main_S6M00002B5.bin`, SHA-256
`49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a`.
Addresses below are original-ROM addresses, not extension addresses. Ghidra
`bally:/s6000_main_S6M00002B5.bin` supplied decompilation and instruction
listings; host `m68k-elf-objdump` supplied bytes where Ghidra has no function.
No ROM, Ghidra-analysis, machine, serial or SSH changes were made for this audit.

**Established:** the full stock loader exposes an IRQ4-observable zero cap;
the normal settled-idle IRQ6 timer-A path does not evaluate profile-owned
configuration or the payout cache; motion-finalization zero excludes its cache
reader. Normal timer-B retirement has a positive one-shot/acknowledgment argument.

**Not established:** a universal interrupt exclusion proof, safety of arbitrary
retained/corrupted peripheral state satisfying only the C admission predicate,
universal communication-router closure, or whole-firmware stack qualification.
The historical b2 E0 gate has reviewed conditional envelopes2928/4096 private
and1964/2048 native-boundary bytes. Candidate06bef891 implements3072-byte native
capacity and the settlement entry guard. Its emitted-code/producer checks,
affected offline tests and source review passed; complete initial non-E0/boot
mathematical root composition is independently approved by b1attempt32. Stable
maintained-document review33 and final pinning passed. The exact reviewed
06bef891 artifacts are now published locally with install_readyfalse; physical
gates remain pending.
Those historical2048 comparisons are not the candidate's capacity. Exact current
evidence is in `SETTLEMENT-GUARD.md`, **Candidate gates**; complete initial
producers, selector DAGs and finite composition are authoritative in
`INITIAL-ROOTS.md`. The historical b2 serial-cancellation gate remains closed.

**New scoped closure:** all returning same-stack IRQ/private-E0 called-target
domains positively exclude settlement589ba/3d294 and their covered controller/
evaluator entrances, including all motion phases. Selected communication domains
are closed, not every router opcode. See **Guard-domain closure and IRQ6 selected
cases** and `SETTLEMENT-GUARD.md`. The candidate's verified frame/producer identity
preserves these local domains; it does not establish initial task-root ancestry
or physical timer/bus qualification. INITIAL-ROOTS.md supplies the separate
complete mathematical root composition. Maintained-document review and exact-artifact local publication are complete.
Standard locked kernel services and the examined fixed counter descriptors now
have positive destination evidence, conditional on valid initialized objects. The inherited-baseline implementation now replaces the full loader
and has passed its targeted restoration/IRQ4 regressions and two scoped reviews.
That closes the reproduced zero-cap transient, not the remaining whole-system
qualification described here.

## Reproducible IRQ4 native fixture

The durable counterpart is `tests/test_game.py:coin_edge` and
`test_restoration_cut_at_every_write_replays_and_keeps_a_valid_cap`, using the
guarded `tests/cpu.py` harness. It compares every modeled restoration cut point
against native behavior at stable old/new caps and checks exact boot replay.
The original scratch probe is historical auxiliary evidence, not a required
reproduction dependency; the complete CPU contract/results are retained below.

Exact CPU contract:

- Unicorn M68K, `UC_CPU_M68K_M68000`, big-endian.
- Map ROM `000000..07ffff`; zero RAM `400000..43ffff`; zero MMIO
  `ff1000..ff1fff`; zero test stack `e00000..e0ffff`; sentinel `f00000..f00fff`.
- Load unmodified original ROM. All D0–D7/A0–A6 initially zero;
  SR=`2400`, SP=`e0fffc`, long `[SP]=f00000`.
- Execute native `FUN_000411ce` until PC=`f00000`, instruction budget 10000.
  This is the coin-processing body called at IRQ4 `266c`, not a simulated full
  interrupt entry or a peripheral-timing model.
- Set byte `43ca1e=cap`; long `4103a4=600` (previous sensor sample);
  long `41038c=1` (pulse count); word `ff1014=400` (current sensor sample);
  word `41242a=80` (coin-enable output shadow). All other RAM/MMIO remains zero:
  ordinary MODE0, no host inhibit, no diagnostic motion, one-sensor coin path.
- The only substituted native leaf is `549c8`: require its stack argument to be
  counter `43`, return D0=0 (wager counter), pop its return address and resume.
  Native `41152` executes unchanged. A code hook counts its calls; a memory hook
  records actual writes to `ff1004`. Reject a nonreturn or unbalanced SP.
- `ff1004` intentionally starts zero, as in the original experiment. Its write
  events, not its final zero value alone, establish whether the native output
  write ran.

Observed results:

| cap | native `41152` calls | shadow coin-enable bit | writes to `ff1004` | balanced SP |
| ---: | ---: | --- | --- | --- |
| 0 | 1 | clear | one word, value0 | yes |
| 1 | 1 | clear | one word, value0 | yes |
| 3 | 0 | set | none | yes |
| 15 | 0 | set | none | yes |

Native path: IRQ4 `2646` → call `266c:411ce` → cap read `41600:43ca1e`
→ conditional call `41614:41152` → clear shadow `41242a` bit80 and write
`ff1004`.

The output's physical name is independently supported by the pinned board
schematic, not inferred from a function name:
`docs/bally-s6000/MK14-S6MOD-0001_schematics.pdf`, **MPU Board (3 of9),
AS-03356-0424**, sheet **14-18**, **OUTPUT PORT 2A / U29 / FF1004**. Its D7 goes to
U29 (74HC273) D8, Q8 pin19, then U9 (ULN2803) I8 pin8 / C8 pin11, to the
explicitly labeled **COIN ENABLE / HIDRIVE15** net. The supplied schematic crop was visually checked against
that chain; the pinned full PDF is the durable source. This is the 0424 board drawing, not an assumed0438-board mapping.

The CPU result is an offline behavioral difference on the documented output
path, **not an observed hardware failure, credit loss, or proof about all coin
states or the actual connected mechanism**.

## Why runtime full reload is unsuitable

`FUN_0003756c(1)` calls `480` at `37584`, clears protected configuration
`43c87a..43ccdd` at `3759e..375b6`, then calls `49e` at `375bc` **before**
reconstructing it. Successive `38ad0` personality copies begin at `375ec`;
that helper brackets each separate copy with `480/49e`. Scheduler locking does
not hide the between-batch zero cap from IRQ4.

Recommendation: restore a transactionally inherited original **owned-byte
baseline**, not the full loader and not an IRQ4-only ROM special case. Criterion:
clean ownership boundaries and realistic native-equivalence tests. Owned bytes
are cap/lines/all-lines (3), weights32×5 (160), three totals (6), selected
paytable count/records pointer pair (8): **177 bytes**. Original profile252 +
guards32 + context4 + baseline177 =465, before slot metadata at496.

The baseline must originate before first custom activation, survive custom→custom
commits unchanged, be integrity/context checked, and remain referenced by durable
restore intent until restoration completes. Restore only those fields, recompute
native checksum, rebuild line cache, reconcile selector/publication, and preserve
unowned configuration/accounting/fault fields. Do not recapture custom state or
fall back silently to a ROM stock reconstruction. This removes the identified
zeroing transient; it does not itself prove every cross-field observer safe.

Do not mask all interrupts around the loader/application: native interrupt
helpers, loops, cache calls, UART deadlines and kernel calls are involved;
level7 remains possible. No latency qualification supports that workaround.

## IRQ6 entry and bounded dispatch

`isr_autovector_6:2718` captures `ff1018` into `4101ae`, enters the kernel with
trap command16, saves D1–A6, then tests **IFR bit5**, not motor state:

```text
2732: bit5(fe10db) ? 273c:5e654 : 2744:5d6ea
274a: restore registers; kernel interrupt exit command11
```

`FUN_0005d6ea` has a state4 special path. Otherwise its bounded ROM state table
at `5d9de`, relative-target table `5d9ca`, and jump `5d9c6` resolve to:

| motor long4101f6 | target |
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

Values here are hexadecimal. Unknown values go to `5e63c`, not an arbitrary
indirect target. This table was decoded from original bytes; `5d9f2`, `5dcca`
and `5e48a` require host disassembly rather than assumptions from Ghidra's
function boundaries.

## Idle timer-A / finalization exclusion

The audited pre-baseline `game.c:game_prepare_profile` claims profile-change ownership and
rejects an already dispatching/held native event. `native_idle` requires
`MODE&fe=0`, wager0 and `quiescent`: motor1, queue byte410213=0, motion-finalize
byte410165=0, no recovery/alternate/special transaction and no extension motion.
The native apply is scheduler-locked. IRQs remain enabled.

For the following induction, **assume that ownership actually prevents every
native task/diagnostic motion starter until finish**, native geometry/pointers
are valid, and no external corruption occurs. A task-lock alone is not an
interrupt lock, and an admission sample alone is not this invariant.

Positive idle path:

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

`5d368` is the motion starter: its `5d410` store sets410165=1 (unless native
interlocks suppress it), and its `5d382` store can queue motion. Its examined
callers are task motion paths `5792a`, `6162c`, `61ec2`, the excluded idle queue
branch, and `5f452`. These are positive caller/guard edges, not a claim that
an absolute-operand search detects arbitrary writes.

`5ff76(-2, ...)` records/posts an optical fault and **returns at60046 without
changing motor state**. Other fault cases, e.g. -7, can call `60254`, clear
410158 and publish motor10 at602f2. Thus “IRQ6 never changes motor state while
the scheduler is locked” would be false. Fault10 goes through `5dad2`, optical
sampling/ROM phase patterns, and timer-A; a completion can use `5fcb2` → state800
→ `5e610` → state80 → `5e1e2`. These paths do not set410165 to1 or queue a spin.

The relevant cache reader is `5f452`: first `410294` pointer load at5f45a,
additional loads at5f62e/5f6d6. Its examined caller is `5e1e2:5e3ce`, behind:

```text
5e3be: 410165 != 0
5e3c6: 43ca2e != 0
```

Admission's410165=0 survives the listed idle/fault paths. `60310` can only clear
it (60436). Consequently those paths cannot enter `5f452`, even if motor state
changes after admission. `5e1e2` itself reads unowned geometry/feature bytes;
the proposed 177-byte restoration must leave those bytes untouched. Kernel event
posts are not synchronous invocation of task6 while scheduler locking holds.
Standard kernel/computed-memory destinations are positively closed below
under valid objects; arbitrary corruption remains excluded, not a live
lifecycle-investigation task.

## Timer-B pending flag: positive lifecycle evidence and its limit

The schematic `docs/bally-s6000/MK14-S6MOD-0001_schematics.pdf`, drawing
**MPU Board (4 of9)** (PDF page19), identifies the FE10C0 VIA as **6522P2**.
Native `24ae:2542` clears ACR atfe10d7. T2 is therefore an interval one-shot;
IFR=fe10db, IER=fe10dd, T2 low/high are the odd bytes covered by the longword
store atfe10d0.

Manufacturer reference: `docs/components/g65sc22-cmd.pdf`, CMD G65SC22,
February 28, 2001, **Timer 2 Operation**, **Timer 2 One-Shot Mode**,
**Interrupt Operation**, Figures **25, 27, 37 and 38**. The scanned descriptions
were visually checked: after timeout T2's flag logic cannot assert again until
T2-high is reloaded; reading T2-low or writing T2-high clears IFR5; IER writes
with bit7 clear clear selected enables. IFR reads do not acknowledge sources;
IER bit7 reads as1.

U41 is marked **CMD G65SC22PI-2** (observed on 2026-10-17, from photographs that are
not distributed; see `photos/05-mpu-eproms.jpg`). `docs/components/README.md` records
provenance/hash. This replaces reliance on the compatible WDC-family reference with
documentation for the observed model. It is **not a test of actual silicon condition,
clock/bus timing or board behavior**.

Native normal-motion lifecycle:

1. State4's `5d790` writes IER=e0, enabling T1/T2; `5d798` loads T2 from410218.
   That state is entered from an actual motion starter, not the admitted idle
   branch. `5ff2c` changes precomputed timing scratch, not hardware.
2. `5e654` with a nonzero pending phase mask4101a8 checks phase4101fa against
   ROM constant6b808=25 decimal. Below25 it reloads T2 at5e6a2. At25 or later,
   **5e6ae writes IFR=20, then5e6b6 writes IER=20**, clearing its flag and enable.
   This occurs before the optional nested `5d6ea` call at5e6ee.
3. Phase25 subsequently marks the stopped reel80 at5e94a. Normal finalization
   waits for stopped reel state at5e236 before publishing idle1 at5e41e;
   it clears410158/4101fa at5e3b2/5e3b8 and finally clears4101a8 at5e476.
   The nonzero mask used during normal T2 stepping is constructed with bit100
   set at5d8dc/5eb98. Its zero-mask branch must not be mistaken for an
   unconditional acknowledgment path.
4. The admitted idle branch and its examined fault drain call `5fed8` with
   channel0 only, storingfe10c8 (T1). Only channel1 storesfe10d0 (T2).
   They cannot re-enable/reload T2 while queue0/finalize0 and motion ownership
   keep the motion starter unreachable.

**Conditional conclusion / inference:** following normal native motion
retirement into settled idle, T2 has no pending timeout left to route a later
T1 interrupt through `273c:5e654`, and cannot generate a fresh one during the
owned-field update. This closes the normal idle pending-timer path using a
positive retirement/re-arm argument, not a lack of configuration xrefs.

**Limit of the pre-admission-guard implementation:** that C predicate did not
read IFR5, IER5,410158,4101fa or410172. The implemented guard is described below.
In arbitrary retained/startup/peripheral state, motor1/finalize0/queue0 alone do
**not** establish the normal lifecycle premise. `2732` tests the raw
flag even if IER5 is disabled. `5e654` has no motor1 exclusion and uses
`410158-1` as a reel index; a synthetic stale flag with idle index0 is not safe
to hand-wave away. `5cf7e` also has a temporary initialization-state1 write
at5d2f8; diagnostic/reinitialization paths such as60442 are not normal settled
retirement. No physical stale-flag failure has been observed.

### Recommended bounded admission snapshot: IFR5 and IER5

Check **both** byte registers while the native idle/ownership conditions are
stable, within a short saved-SR interrupt section:

```text
ifr = *(volatile uint8_t *)0xfe10db
ier = *(volatile uint8_t *)0xfe10dd
require ((ifr | ier) & 0x20) == 0
```

This is two byte reads, not a poll, timer operation, or whole-application IRQ
mask. Test bit5 only: IER bit7 reads as1; T1 bit6 may legitimately remain enabled
or pending. Use byte accesses to the odd register addresses, not a word access.
The 6522 **Interrupt Operation** description explicitly permits IFR/IER reads;
neither read acknowledges a source or changes the timer. IFR flags are cleared
by writes of1 or the source-specific acknowledgment operations. In particular,
**do not read T2-low atfe10d1**, which clears IFR5, and do not write IFR/IER as
part of admission. The native `2732` read/test likewise examines IFR without
acknowledging T2. These side effects/register semantics remain subject to the
exact installed-silicon qualification noted above.

**Why both:** IFR5=0 alone can mean a T2 countdown is armed but not expired.
IER5=0 alone allows a stale raw flag to divert the next T1 interrupt to5e654.
Normal stock terminal processing writes IFR20 and IER20 at5e6ae/5e6b6, so both
bits really are clear following the normal settled-idle sequence, not merely
inferred from motor1. The writes precede final stopped-reel publication and
idle1, as traced above. Reading both therefore rejects native armed motion as
well as stale/pending T2, without suppressing the ordinary idle T1 source.

Retain the timer-A guard induction: queue0 excludes5d368; finalize0 excludes
5f452; task/diagnostic ownership excludes other motion starters. Idle5e08a and
its examined fault10→800→80 drain use `5fed8(0, ...)`, never reload T2-high or
enable IER5. State4's re-arm sequence is therefore unreachable while that
ownership holds. A native optical fault may still post an event or change motor
state; it does not invalidate the no-T2-rearm conclusion. Never clear that fault
or synthesize native idle to make the checks pass.

**Exact limit:** even IFR5=IER5=0 is not a hardware readout of “T2 unarmed.” An
arbitrarily programmed T2 could be counting with IER5 disabled and assert raw
IFR5 later; native2732 would still dispatch it on a T1 interrupt. Under the
verified normal driver lifecycle, the only IER5 clear occurs at terminal T2
retirement, not while a future initial timeout is armed, which excludes that
case. Unknown commissioning/startup/corrupted peripheral state is not thereby
certified. For a stronger locally checkable native-state contract also require
the normal completion sentinels (byte410158=0, long4101fa=0, word4101a8=0)
with the existing motor1/queue0/finalize0 conditions. Native5e3b2,5e3b8,5e476
clear them at normal retirement. These are not replacements for the peripheral/
lifecycle premise and cannot reveal an arbitrarily externally programmed timer.
The original snapshot proposal did not include410172; its broader lifecycle
is not needed to weaken or extend this scoped proof. No further investigation
is requested here.
Startup-marker processing at5e5e8 sets it to4, but normal retirement **does clear
it at5e424**, immediately after the motor1 publication at5e41e. The initial
claim that normal retirement lacked this clear was contradicted by original
bytes `4239 0041 0172`; nonzero410172 is not established as legitimate settled idle.

Take the snapshot at the final admission/publication boundary before writes,
with profile ownership already claimed; revalidate after acquiring scheduler
exclusion if there is an intervening scheduling opportunity. Restore the prior
SR exactly and keep IRQ masking out of native cache/kernel calls. Level7's
terminal power-fail handling remains outside this ordinary-IRQ snapshot.

Recommendation: use the dual-bit snapshot and preserve the positive no-rearm
invariant; reject unfamiliar states. Criterion: a locally checkable contract
and realistic failure tests instead of long IRQ masking or historical state
alone. Implemented in `game.c:motion_retired` and `platform.c:platform_timer2_active`.
Preparation checks the snapshot after claiming ownership. Runtime application
checks it again under the scheduler lock, before the owned-write batch, together
with ownership, native faults and the shared `native_idle` predicate (including
zero wager). This is not a per-store polling loop. Rejection before preparation
returns BUSY without side effects; failed post-commit application returns FAULT
and retains exclusion. The native counter reader nests the scheduler lock.

Compiled regressions verify byte-only IFR/IER reads, no timer acknowledgment,
T1 preservation, saved-SR restoration, pending/enabled T2 and stale-sentinel
refusal, changed admission conditions before application, and no configuration/
cache/publication effects on refusal. The wager-change fixture is an admission
contract test, not evidence of a real coin race. Boot recovery bypasses these
runtime predicates through the explicit window below.

### Boot versus runtime application

Reset calls `154e` at `bca`. Within it, `155c` initializes extension state,
`1730` can call the native loader, and `1906` is the final early restore hook.
Only after `154e` returns does `be8` call VIA initialization `24ae`, followed by
kernel start at `bec`. Timer-state admission is therefore inappropriate within
that early restore window: this boot has not initialized the VIA yet.

The loader-tail hook `37dde` is shared with runtime alternate-paytable paths
`63db8`, `64120`, `6430a`. Neither lack of profile ownership nor a call to that
hook proves startup. The implementation distinguishes the `1906` hook with
`platform_boot_restore`, closing a window initialized only by `platform_boot`.
Runtime application of an active profile
without prepared ownership must fault, not gain a weaker admission path.
Settled stock remains a no-write operation. A runtime loader can already have
changed native fields before its tail; rejection is not rollback of those writes.

## Other interrupt roots and stack boundary

Previously inspected roots remain relevant but are not a whole-system closure:

- IRQ1–3 autovectors2642: RTE only; actual UARTs use their vectored entries.
- IRQ4:2646: coin body above, queue/accounting,39040 output sequencing and kernel
  tick handling.
- IRQ5:26e4: shadow412426/412428 → ff1000/ff1002 plus kernel entry/exit.
- DUART1:13874: progressive1372a→1475c→16976→16b08; J3 ring/framing1377a.
- DUART2:13d98: A13bf4→33dfa bill/accounting; B13c46→callback43e8f0.
- DUART3:141b2: ring handling; B14068→the same callback43e8f0.
  Initialization from ROM74aca sets that callback to19732 (`sas_rx_byte`);
  examined ISR receive framing queues data, rather than calling E0 synchronously.
- Kernel writable hooks copied at36fe..370a from ROM27c0..27cc are zero in the
  original setup. The standard services and fixed descriptors below now have
  positive destination evidence; this is not a global exclusion of every alias.

### Computed-memory paths with positive destination evidence

Assume valid initialized kernel objects/native pointers, no external corruption
and enforced runtime motion ownership. Standard service numbers at `0x2814`
select bounded ROM branches. Mailbox `0x303e` and queue-post
`0x34e6 → 0x37ee/0x353c` store message **values** in queues/TCBs; they do not
interpret those values as destination pointers. Tick `0x3258` traverses native
TCB delay links. Lock `0x33e0` increments kernel byte offsets `0x28/0x29`;
nonzero `0x28` prevents scheduler path `0x2a90` and its context-switch callback.
These examined destinations exclude owned configuration and the payout cache.

Counter reader `0x549c8 → 0x3bfca → 0x3c998` resolves the following original-ROM
descriptors (counter IDs here are hexadecimal):

| Counter | Descriptor | Type / length | Data span, including redundancy |
| --- | --- | --- | --- |
| 0x43 | 0x6a14c | 1 / 3 | 0x438034..0x438036 |
| 0x46 | 0x6a164 | 0 / 4 | 0x43d61c..0x43d625 |
| 0x47 | 0x6a16c | 0 / 4 | 0x43d546..0x43d54f |

Their bases are nonzero, excluding `0x3c9f4`'s dynamic fallback. IRQ4 accounting
operation `0xa8 → 0x45c94` uses descriptor `0x69f5c`, fixed base `0x4380b2`,
length 5—not the wager/configuration/cache region. Bill pointers `0x4104ba[i]`
are initialized at `0x1ae4..0x1b02` to `0x43cdb0 + 0x22*i` for ten records;
this bounds `0x258d6`'s bill-record indirection. IRQ6 fault-counter path
`0x6016e → 0x3b04a` constructs `0x43e47a + class*0x10 + reel*2` destinations.

Communication-router closure remains separate. Ghidra's caller listing for
`0x18970` misses computed dispatch `0x18bea → 0x18ce4 → 0x18970`.
`0x18970 → 0x18a8e` reads the owned cap into BSG status 14. For the examined
bill-ISR event `0x37` producer `0x34746`, event-time bill states are
`0x0d/0x0e/0x0f/0x10`; the reset arm requires `0x09/0x0b`. That particular
path is excluded by producer-domain evidence, not absent caller references.
Do not generalize it to every event-router arm.

### Stack accounting and the remaining IRQ6 cycle

The pinned schematic **MPU Board (1 of9), AS-03356-0424** labels DUART3/IPL1,
DUART2/IPL2, DUART1/IPL3, VIA1/IPL4, zero-cross/IPL5, VIA2/IPL6 and AC-fail/IPL7.
Kernel configuration `0x27a0+0x14` contains mask 6: critical SR is `0x2600`,
not `0x2700`. Configuration `+0x0a=0x0040` sets kernel `0xf3=2` at `0x3722`.
The interrupt-stack switch at `0x2834` requires **exactly 1**, so these supervisor
tasks do not take it. IRQ/trap frames remain on the interrupted task's stack.

A normal active ISR retains 66 bytes before its body's nested calls: exception
frame 6, saved D0 4, saved D1–A6 56. Examined locked standard kernel services use
at most 48 more bytes below the trap caller's SP, excluding nested interrupts;
tick path `0x37f6 → 0x37ee` supplies that local maximum. These are components,
not a total envelope.

For the earlier `d9a5f9f0…` build, emitted instructions gave resident depths of
24 bytes at `0x19b16` entry, 60 at `platform_sas_handler`, 260 at `fw_process`,
and 844 at `game_apply_profile`. The native path is
`0x16192/0x16242 → 0x16366 → [0x43e8f4]=0x19b16`, installed from `0x74ace`.
These include live arguments and returns, not just compiler `.stack` entries.
They must be recalculated for the final build and are not maximum stack usage.

During active motion, cross-calls
`0x5d81c → 0x5e654 → 0x5e6ee → 0x5d6ea` add 68 bytes per complete nested round.
Both calls precede the later motion-progress updates, so a simple progress-count
argument does not bound the recursion. Calibration `0x5df1a..0x5df28` stores
measured latency plus `0x60` (96 decimal) at `0x43862a`. The range guards below constrain calibration without clamping it; timer/bus
premises and the whole nested-interrupt envelope still require qualification.

The no-rearm induction excludes this cycle during an admitted profile transition,
not during general E0 processing. INFO/upload/status and pre-admission journaling
can retain large parent frames while motion is active. The conditional bound
was historically awaiting enforcement. The implemented checks and conditional
components below close the scoped software contract; installed-board bus/timer
qualification remains separate. A trace alone cannot prove those premises.

### Conditional timer bound, not an installation qualification

The pinned **MPU Board (4 of9)** connects U41/VIA2 pin25 to CPU **E**, not the
nearby DUART/I2C clocks. **MPU Board (1 of9)** gives CPU CLK as 14.7456 MHz.
Motorola's [MC68000UM](https://www.nxp.com/docs/en/reference-manual/MC68000UM.pdf),
**3.7 M6800 Peripheral Control — Enable (E)**, states:

> A single period of clock E consists of 10 MC68000 clock periods (six clocks
> low, four clocks high).

It also specifies E is free-running regardless of MPU bus state. The schematic
therefore implies about 1.47456 MHz for VIA ticks, not a measurement of this board.

Let `C` be native calibration word `0x43862a`. `0x5fed8/0x5ff2c` construct
`R = (raw_period - C) & 0xffff`, without a borrow/minimum-period check.
`0x5df12..0x5df28` accepts a larger unsigned measured word and adds `0x60`
without saturation. Raw period `0x14f0` with `C=0x14f0` gives reload 0; with
`C=0x14ef` it gives 1. These are **input-domain arithmetic counterexamples** to
using raw-table minima, not proven native-history or recursion counterexamples.

A sufficient, non-maximal interval for the static argument is
`0x0060 <= C <= 0x0800`. With raw-table minimum 5,360, reloads are at least
3,312 ticks. The argument additionally assumes:

- Timer behavior matches the stated 6522 model.
- Non-VIA bus cycles take at most 6 CPU clocks; VIA cycles at most 20.
- IRQ6 cannot be delayed by another interrupt that later returns.
- Calibration stays constant during the state-4 cross-call cycle.

Under those assumptions, each opposite-handler handoff is bounded by
`C + 1024` E ticks, at most 3,072: T1 reload `0x5d738` through IFR6 check
`0x5e6e4`, and T2 reload `0x5e6a2` through IFR5 check `0x5d812`.
The audited prefixes contain at most 44 non-poll instructions bounded by
128 CPU clocks each, plus the twelve-instruction polling and low-byte guards.
The T1 exit window is at least 1,264 ticks under the interval, preventing a missed
whole-period window under the stated bus bounds. Since `3072 < 3312`, neither
freshly reloaded timer can expire within the opposite handoff: at most **one
cross-call, zero complete recursive rounds**.

An independent ROM review reproduced 40 non-poll instructions per handoff,
within the conservative 44, and twelve instructions in each polling loop.
The `0x5ff2c` helper branch bypasses the recursive call, rather than adding
uncounted work to it. The review accepted the low-byte/split-read and exit-window
argument under the stated premises. This approves the conditional argument,
not those premises on the installed board.

The implemented guards cover retained boot state, the calculated candidate
before its native store, and initial T2 arming. Thus they do not rely on a single
saved-value snapshot or on native calibration repairing an out-of-range value.
This remains conditional on the native write/lifecycle and valid-pointer premises;
arbitrary corruption and the installed-board bus limits have not been qualified.
No calibration is clamped and no timer value is substituted.

The historical policy comparison considered enforcing the calibrated timer/bus
contract versus requiring settled-motor/T2-clear exclusion across all deep E0
processing. The latter would change INFO/upload/status admission and could block
future diagnostic POSITION/JOG; it was not selected or implemented. The chosen
calibration enforcement below and private-E0 bridge retain command behavior and
close their scoped conditional software gates. No motion-time BUSY policy or
new lifecycle/financial investigation is requested. Installed-board timing and
unknown peripheral state remain outside the proof.

### Calibration lifecycle and implemented enforcement policy

The three native clear variants zero both calibration bytes: `0x387b6/0x38850`
clear `[0x438000,0x43f800)` and `0x388e4` clears `[0x438002,0x43e800)`.
**C=0 is legitimate virgin initialization**, not automatically corruption.
Normal reset preserves C. Within the focused write check and valid-pointer SRAM
premises, the calculated live update is `0x5df28`; adjacent header/game-record
writes do not overlap the word. This is not an arbitrary-pointer exclusion proof.

`0x5cf7e` establishes raw period `0x4f64` (20,324 ticks). During state `0x20`,
`0x5e080 → 0x5e642 → 0x5fed8` reloads it without subtracting C. Sampling starts
when `0x4101b6 >= 25` decimal. Native `0x5deec` waits for T1 low >=15 decimal,
then reads high at `0x5def8` and low at `0x5df02`; `0x5df10` subtracts the
assembled counter from the raw-period word. Thus D5 reflects IRQ-masked latency,
exception/kernel/dispatch and local sampling delay modulo counter behavior, not
the preceding 300-tick task delay. Proving an untouched-history ceiling of
`0x0800` requires bounds on all relevant IRQ6-masked intervals and coherent reads;
the fifteen-count guard does not cover arbitrary bus stalls. Large retained C
can suppress later updates, so native calibration is not a guaranteed repair.

The implemented boundaries preserve accepted timer/calibration values, not
cycle-identical instruction timing:

1. At boot-only `0x1906`, accept C=0 or `0x0060..0x0800`, before `0x5cf64`.
2. At `0x5df1a..0x5df28`, reject a computed candidate outside the strict interval
   before storing it. Do not clamp or substitute a timer value.
3. Before T2 enable/load `0x5d790/0x5d798`, require the strict interval, so a
   virgin zero cannot enter the cross-call regime without becoming calibrated.

The established non-returning rejection path is **TRAP #1 → 0x0fc4** (vector
entry `0x84`). It sets SR=`0x2700`, records native diagnostics, then follows
`0x13ee → 0x1274` display / `0x1246` fault counter to watchdog-only loop
`0x123c`. It does not return or invoke calibration-dependent reloads.
**It does not de-energize the last reel-drive latch.** This is stock CPU fatal
halt, not a qualified mechanical shutdown or restart policy.

Do not substitute `0x5ff76`: its motor-fault path can publish state `0x10` and
continue through `0x60254` and calibration-dependent reload/recovery.
No inspected recoverable rejection path currently satisfies the required
non-recursive failure condition.

The user **approved the native fatal-halt policy** for these three checks.
`src/platform_hooks.S` shares the range constants/check macro; `patches.json`
pins the complete replaced instructions. The candidate guard tests the actual
stacked word passed to `0x426`, including wrapped additions. The T2 patch uses
an eight-byte JSR+NOP region, not a partial overwrite of the native instruction.
Thirty calibration tests, 27 platform tests and 48 builder tests passed; two
scoped reviews approved the ABI and patch-builder changes. Allowed paths match
native register/SR/SP and stored/timer values. Fatal tests first require the
executor to reject TRAP1, then explicitly model its exception frame and execute
the real `0xfc4 → 0x123c` path; they do not simulate physical exception timing.

Fresh inspection of image
`ca7e0c9653df3d8648851d1679935fda674d72bd0bca003d0b45daabed20f6c5`
shows that `0x5d78e BNE → 0x5d80a` skips the guard to reach the cross-call check
at `0x5d812`. The arm branch instead executes `0x5d808 BRA → 0x5d822`, skipping
that check. Both recursive handoff prefixes therefore exclude the new hook;
their previous instruction bounds survive for this positive branch reason.
Initial T2-arm timing changes and is not cycle-identical. The guard's fatal path
retains its four-byte call return and two-byte saved SR before TRAP1; these must
be included in the whole stack envelope.

This is software approval, not approval to flash, operate the programmer or test
the machine. No clamping, automatic repair or motor-power-off promise is added.
Bus/silicon and mechanical qualification remain separate gates.

Native cache routine36a7a has LINK−0x20 and seven saved registers: **64 bytes**, not
its decompiler's spurious32-KiB local array. The examined installed-option5/6
cache/helper paths use at most84 bytes below its entry SP, excluding caller's
return address. Stock loader3756c has68 fixed frame/save bytes before outgoing
arguments/nested calls. These are local facts, not total stack bounds.

Tasks created through3c54 use supervisor mode. Interrupt save frames can consume
an interrupted task's stack; kernel switch conditions and nesting matter.
`5d6ea ↔ 5e654` is a real call cycle with hardware-dependent waits, so summing
compiler `.su` frame sizes is not certification of the 2-KiB native task stack.
That historical whole-stack gap is addressed, for the scoped conditional
software promise, by **Conservative native interrupt components** and the later
private-E0/initial-root/admitted-guard proofs. It is not universal untouched
native temporary-peak or installed-board qualification; no repeated boot/RAM
audit or financial-history investigation is requested.

### Native SAS request ownership

Two configurable parent roots, `0x16192` and `0x16242`, do not by themselves
establish concurrent core requests. Before invoking callback `0x43e8f4`, their
common `0x16366` path must create child task ID17 at `0x16410..0x16422`.
Kernel create rejects a duplicate live ID at `0x2cba..0x2cce`; its error causes
immediate TRAP1 at `0x16432`, before that parent's callback. Child `0x32562`
loops through `0x327fc → 0x32570` rather than returning to delete itself.

The examined native reconfiguration path also has a positive teardown order:
`0x16682` deletes the per-port parents using the same task IDs as creation in
`0x152ec`; only after that loop does `0x166f4` delete child17. Caller `0xc1a8`
then initializes protocols through `0x15836` and creates new parents through
`0x152ec`. The delete wrapper is `0x3c8a`/service1; `0x3cb0` is suspension,
not deletion. This supports a single active dispatcher under valid native task
IDs and no out-of-band service calls; it is **not scheduler-lock exclusion**.

This is ownership evidence for the existing `fw.h` serialized-core contract,
not a universal guarantee against external invokers. Boot/reset/clear ordering
and runtime native transitions remain separate premises. Absence of two live
callers does not itself prove consistency after asynchronous task deletion.
The separately implemented serial drain below closes that reproduced lifecycle
failure for the examined native route. No machine incident is claimed.

### Earlier stack-capacity warning

In the guarded image above, `fw_process` has 572 fixed bytes below its entry SP:
LINK allocates528 plus saved A6 (4), then MOVEM saves40. Its inlined
`commit_profile` slot buffer is paid even by pre-admission journaling. At
`record_operation → write_committed → fw_storage_write/platform_write_words`,
resident depth from the audited SAS task top is:

```text
260 fw_process entry + 572 fixed
+ 16 call/arguments + 160 record_operation fixed
+ 16 call/arguments + 20 write_committed fixed
+ 16 call/arguments + 8 platform_write_words saved = 1068 bytes
```

The word-writer restores the incoming SR between writes, so this is an IRQ-open
point, not a fully masked persistence operation. Emitted prologues and live call
sites, rather than compiler `.su` totals alone, establish these local costs.

An auditor-reported candidate IRQ1-through-IRQ7 nesting sum is
`1068 + 194 + 378 + 114 + 70 + 66 + 110 + 154 = 2154`, above2048.
This historical overapproximation was not a simultaneous-state witness. The
conditional components and private allocation below supersede that warning. This
is a capacity warning, not a demonstrated overflow, observed machine failure or
whole-stack upper bound. Calibration guards alone do not close it. Any reduction
of stack locals into shared work storage also requires positive core serialization;
a shared callback symbol does not prove only one SAS task can enter it. The
positive native ownership argument above is the relevant evidence. The core-owned 512-byte slot workspace is now implemented and reviewed inside
the existing reserved SRAM. These preceding numbers describe the older image;
the recalculated d15 costs below supersede them for that historical build.

### Slot-workspace reduction

Image SHA-256
`d15f83984deaca1bb606b9a71c1d179714c0fec51bd96d4f9bccdb8fa5417e03`
has a 112-byte fixed `fw_process` frame, down from572. Core runtime occupies918
bytes within `[0x430600,0x430a00)`. The change passed52 core tests and15 selected
integration tests, plus a scoped source review; these are not stack certification.

The emitted `build/ext.dis` chain is:

```text
native SAS parent → platform_sas_handler → fw_process
 → fw_submit → game_submit → 0x548bc(counter47)
 → 0x3bbae → 0x3bc5c → 0x3c998 → 0x3cf86
```

`fw_process:0x7afe6` retains372 bytes including its callers; the submission at
`0x7b684`, wrapper `fw_submit:0x7d68c` and `game_submit:0x7c430` reach460.
The counter comparison called at `0x7c60e` reaches **644 bytes**. Its scheduler
lock restores the caller's SR, so this remains an IRQ-open depth. Operation and
profile journal writes reach616 and628 respectively. The earlier608 figure stopped
at word-writer entry and omitted its two register saves. These counts include live
arguments, returns and changed helper frames rather than subtracting the buffer
size from an old total.

### Conservative native interrupt components

The read-only audit reports these per-priority components, including exception/
register saves and returning kernel services, but excluding higher interrupts.
IRQ2 now has the explicit ledger below rather than its initial400-byte allowance.
Independent instruction review accepted these scoped, conditional components:

| IPL | Bytes | Dominating inspected path |
| ---: | ---: | --- |
| 1 | 194 | `0x141b2 → 0x14068 → 0x19732 → 0x14b48 → 0x14450` |
| 2 | 640 | bill validation, selected communication dispatch and returning services below |
| 3 | 114 | `0x13874 → 0x1372a → 0x1475c → 0x16976 → 0x16b08` |
| 4 | 302 | `0x2646 → 0x411ce → 0x53dd0(A8) → 0x45c94 → 0x3bdca → 0x3bc5c` |
| 5 | 66 | `0x26e4` register-save/output-write path |
| 6 | 434 | completion/F6/counter47, including an outer T2-handler allowance |
| 7 | 154 | `0x275a → 0x41796 → 0x41b3e → 0x549c8(43)` |

### IRQ2 communication and service ledger

Entry `0x13d98` retains66 bytes for exception/register saves. Driver calls at
`0x13db0` are sequential; FIFO iterations return before repeating. RX-A
`0x13bf4` saves16 bytes, giving94 bytes including calls at `0x33dfa` entry.
RX-B `0x13c46` takes the ring or `[0x43e8f0]`; constructor `0x16366` and tables
`0x74aca/0x74ada` install the qualified target `0x19732`. RX-B, TX handlers
`0x13d24/0x13d5e`, ring helper `0x14718` and output helper `0x364ca` are smaller.

The reachable IRQ2 event keys are37, F0, F1, F2, and indirect F6 via
`0x34202 → 0x34d22` (all keys hexadecimal). Selected dispatch targets are:

| Router | Selected arms |
| --- | --- |
| `0x2640c` | `37 → 0x26704 → 0x258d6`; `F6 → 0x26918 → 0x549c8(47)`; other keys default |
| `0x23d40` | `37/F0/F1/F2 → 0x242b2 → 0x246ac`; F6 default |
| `0x1b8ca` | `37 → 0x1bbf0`; `F0/F1/F2 → 0x1beb4`; F6 default |
| `0x18bea` | `37 → 0x18caa`, payload or reset/status sender; other keys default |
| `0x26fc8` | `37 → 0x270b4` return; other keys default |

All valid bill-status alternatives are allowed; this bound does not rely on a
higher interrupt preserving one particular status. Conservative subtree ceilings
include live arguments and call returns:

| Subtree | Bytes / derivation |
| --- | --- |
| Numeric/BCD family | 272: fixed-descriptor getter/copy/compare, CRC and formatting |
| `0x34492` | 336:32 fixed +20 outgoing +4 return +272 |
| `0x3440e` | 384:16 +16 +4 +336, rounded |
| `0x341ac` | 392:4 outgoing +4 return +384 |
| Each selected protocol arm | 384 |
| `0x26a0e` | 400:4 fixed +8 outgoing +4 return +384 |
| `0x34746` | 424:4 +16 +4 +400 |
| `0x34d22` | 428:8 +16 +4 +400 |
| `0x34202` | 440:8 outgoing +4 return +428 |
| Every direct `0x33dfa` callee | 448 |

The protocol allowance includes `0x258d6`'s retained formatting arguments and
counter47 getter; `0x246ac`'s conversion/send branches; `0x1b8ca`'s selected
format/queue tail; and `0x18bea → 0x18970 → 0x18a8e/0x18936 → 0x18828 → 0x14c66`
reset/status sending. `0x14c66` is charged128 bytes and queue-copy `0x17b14`80.
That80 is supported by the **selected active-IRQ**17b14 maximum50, not the
incorrect general arithmetic20+4+64=80. Its fixed20+call4+IRQ service26=50;
17b9a/17d04 multiply retains8 and saves8, giving20+8+4+8=40, not36.
General task-side allowance remains20+4+64=88. See the new closure section for
the registered-IRQ no-scheduling premise. No640/434 component increase follows.
Returning wrappers/kernel services fit the included64-byte service ceiling:
`0x3d82/0x3d88` (SC20/21), `0x3a32` (SC8), `0x3a1e` (SC0c), `0x3b84` (SC26),
and retry wrapper `0x3d8e`. ISR accounting prevents switching tasks while IRQ2's
C frames remain live. Retry arguments are popped at `0x14cb0` and `0x3dc8`, so
these backedges do not accumulate stack; that is not a liveness guarantee.

The total is94 driver prefix +8 fixed `0x33dfa` +24 outgoing allowance +4 return
+448 direct-callee ceiling =578, rounded to **640**. The largest inspected
`0x33dfa` outgoing branch uses16 bytes rather than the allowed24.

### IRQ6 completion contribution

The IRQ6 path is `0x5e1e2 → 0x5f452 → 0x34d22(6) → 0x26a0e(F6)
→ 0x2640c → 0x26918 → 0x2696a:0x549c8(47)`. It needs nonzero finalization
and line-enable fields, a zero selection mask, option32 enabled, full mode2,
`0x43d12e.bit5=1` and `0x43e8d2.bit0=1`. AS initialization can coexist with SAS
without claiming child17. Admitted profile changes exclude this completion path;
general/pre-admission E0 does not. Direct T1 entry costs410;434 conservatively
includes an outer T2 frame without asserting that both branches co-occur.

### Historical private E0 stack and scoped offline qualification

The E0 stack qualification image, before the serial-drain change, has SHA-256
`984cb4f4e1f54ce53c50b0ad4ee18a6be9d25cd63e8e85f162ec86bf143345d2`
implements the frame-free bridge `platform_sas_handler:0x7d960`:

```text
0x7d960  save caller SP at0x434020
0x7d966  select private top0x434000
0x7d96c  JSR platform_sas_process:0x7d79e
0x7d972  restore caller SP
0x7d978  RTS
```

The private range `[0x433000,0x434000)` is commissioning's stack before kernel
startup; it cannot still be active when runtime E0 begins. Motion's24-byte state
moves to `[0x430a00,0x431000)`. Linker/size assertions bound the reservations.
No native allocation, TCB top, interrupt mask or core algorithm is changed.

Creation `0x2d0a..0x2d4a` builds a new26-byte context from `TCB+0x40`. Switching
saves actual SSP at `0x2b30` to `TCB+0x38` and loads it at `0x2b54`; USP is
separate. SC9/SC0c blocking uses the same context. The standard service table,
inquiry and scheduler paths impose no resume-time pool/top test. Inquiry callers
`0x15138`, `0x751a` and `0x16714` use IDs/priority/status, not stack fields.
Creation/deletion/switch hooks at configuration `0x27c4..0x27cc` are zero.
Deletion unlinks at `0x2e12`, removes waits/timers at `0x2e24..0x2e6e`, and
recreation overwrites saved SSP before inserting the replacement.

One live callback and irrevocable deletion before replacement make an old saved
SP disposable: each new entry overwrites it. Suspension alone does not. Returning
interrupts preserve whichever stack is current. User-mode tasks, external hooks,
corrupt kernel objects and recursive/concurrent callback entry are excluded.

The inspected counter chain charges588 bytes to the private stack:644 minus60
native-prefix bytes plus4 for the bridge's worker return. C frames are unchanged.
This is one checked path, **not** the whole-worker bound. Independent review
instead uses1024 bytes for all reachable worker paths:224 worker +160 core +512
branch subtree =896, rounded upward. This includes profile preparation/application,
journals, counters, CRC, NACK/send and returning kernel services. The revised
native interrupt components total1904:

```text
native entry/exit: 60 +1904   =1964 <=2048  (84 bytes spare)
private worker:   1024 +1904 =2928 <=4096  (1168 bytes spare)
```

Creation's26-byte initial context is consumed by register restoration and RTE;
it adds no surviving task-entry trampoline. The native-window bound requires the
bridge to stay frame-free; later entry/exit helpers need new accounting. These
sums deliberately overapproximate simultaneous interrupt compatibility, not
demonstrate an overflow. They require valid native pointers/counter copies, no
external service invokers, the timer/bus premises above and one filtered terminal
AC-fail assertion. Repeated IPL7 edges and every unmodified SAS command are
outside this result. They do not authorize hardware installation.

The scoped source/test/ledger review is approved. The affected gates passed221
platform/motion/commissioning/game/host cases and seven abandonment subtests.
`tests/test_e0_kernel.py` adds three passing real-kernel tests; the two existing
CPU-status regressions also pass. Its temporary image contains the full production
sources plus test-only task programs, not kernel stubs. It checks production
bridge/C bytes and original kernel bytes. TRAP0/RTE and explicit IRQ5 entry are
modeled architecture boundaries; UART completion is an explicit test event.

The tests execute native initialization, SC12 blocking/resumption, SC1 deletion
and SC0 recreation of the same TCB. They verify unchanged allocated tops, private
SSP retention, distinct replacement depth, no old continuation, restored registers/
SP and stack guards. IRQ5 executes at every bridge boundary on both stacks.
Scratch mutations without switching, with stale saved-SP reuse, and with an
out-of-bounds private top are detected. No physical timing claim follows.

**Historical E0-only scope:** that scoped audit did not bound Task6
event/normal-start/outcome/evaluator/wager, Task7 polling/cleanup even with
`MOTION_QUALIFIED=0`, or whole boot/restore. Their separate complete mathematical
composition is now in INITIAL-ROOTS.md; do not extend the E0-only result itself
to them. Commissioning retains its earlier separate
qualification. Reset/lifetime reasoning assumes supervisor execution, a fresh
reset SP and no resumption of a pre-reset activation; arbitrary soft-reset paths
were not newly investigated here.

### Reproduced serial-cancellation inconsistency

On the earlier d15f8398 image, an offline probe discarded one compiled call stack while
retaining RAM; it did not execute the native scheduler or model IRQ timing.
After the durable ACCEPTED marker but before runtime publication, INFO offered
an ID already reserved in the journal. After the durable final OK marker but
before runtime publication, an identical retry remained ACCEPTED and a new
request returned BUSY. Explicit `fw_boot` reconciled both cases, but normal
serial reconfiguration does not run that recovery path.

A second probe on the published `984cb4f4…` image reproduced both windows using
actual native SC1 deletion, not merely a discarded call stack. It executes real
`fw_boot`, the production SAS bridge/core and the full unchanged
`c1a8 → 16682 → 15836 → 152ec → c1ba` sequence. A commit-marker memory event
selects a cut after the word-writer restores SR, before runtime publication.
A test-only interrupt/tick shim wakes the configuration task through native
SC22/SC18/SC17. Registered parent13 is deleted and appears on the free list;
it never reaches its return marker. A separately scheduled observer then makes
INFO/STATUS/retry calls without reboot or host-side TCB/context repair.

The historical scratch probe (not a required reproduction dependency) uses explicit test priorities/callers, the narrow TRAP0/RTE CPU model and
register-memory MMIO. Ports are disabled and native configuration is marked
initialized (`0x55aa`), avoiding virgin RTC setup; parent recreation executes
without creating enabled-port tasks. Child17 is absent. These are offline
lifecycle results, not physical timing, an observed machine incident or proof of
an actual duplicate side effect.

### Implemented serial drain and offline gate

The native context and scoped cancellation gates are closed for the pinned ROM
under valid task IDs, balanced kernel history and no external corruption.
`platform.c:platform_reconfigure` first implemented the protocol below in historical
image `b2ce1bc5c03b8e8af47cf246115968033f62574c2a3ae2d2d7e5c4f225adcb83`.
The reviewed06bef891 publication preserves that protocol.
This is not installation qualification.

Task7's mailbox dispatcher `b080` serializes the relevant routes. Its case8
calls communications test `132ee` at `b2b8`. The test suspends registry parents
at `132fc`, but every normal path to epilogue `13656` first resumes them through
`16614`: early exits `1336c/133d6/1340a/134f6/13560/13630`, or completion
`13646`. Helper `131b4` can additionally resume at `132cc`; it never suspends.
The test returns through `b2be → b088`, not into configuration. Configuration
is separate case11, `b32c → b33e:c0fc`. Thus the communications test does not
leave a suspended SAS owner for this drainer to wait on.

Task7 is created by the supervisor-task constructor at `1a72..1a80`. The
configuration route introduces no lasting scheduler lock or IRQ mask:
`044c` restores SR at `478`; `35aec` reads the type2 descriptor `73bc4` through
its direct byte-read arm; `4e37a → 548bc` balances `548da:3d82` and `548fc:3d88`;
`36540` uses mailbox inquiry and delay. This is not an arbitrary-history or
external-invoker guarantee. Restore the short critical section's SR before
pending on the completion mailbox.

The hook is the entire 18-byte **callsite** `c1a8..c1b9`, not a function entry:

```text
4eb900016682 4eb900015836 4eb9000152ec
```

Its no-argument wrapper must preserve native callee-saved registers and execute
close/drain → `16682` → `15836` → `152ec` → reopen, then continue at `c1ba`.
The verified order is deletion before protocol initialization/recreation, not
absence of earlier global changes: `c116` already clears `43e8de`.

The private one-slot mailbox uses native SC8/SC9, wrappers `3a32/3a52`.
SC8 requires a nonzero token; errors5 (full) and6 (zero token) preserve the
mailbox. Wrapper errors are **WORD outputs; SC8's D0 is not its status**.
SC9 consumes a latched token or parks atomically; timeout0 does not join the
delay list. Real-ROM probes cover both post/pend orders and deletion of the
notifier while its post wrapper is still pending at `3a42`.

The reviewed ownership protocol is:

1. Atomically close core admission, require an empty mailbox and latch a local
   `must_wait = active`. Guard every `fw_process` call, including STATUS polling.
2. On core return, clear active and capture notification before posting. A woken
   drainer may delete the notifier before its SC8 wrapper returns; no shared
   cleanup may remain afterward.
3. If `must_wait` was true, consume exactly one completion token even if active
   has since cleared. Rechecking active instead would leave a stale token.
4. Keep admission closed through the original delete/init/create sequence and
   reopen only after it completes with no active core or pending token.

The completion boundary is `platform.c:platform_sas_process` immediately after
`fw_process` returns, before NACK handling, response construction or transmission
through `278fe`. Do not wait for UART TX: a stalled transmitter may itself need
reconfiguration. Core consistency and transport delivery are distinct contracts.
The mailbox is the aligned longword `sas_drain_mailbox` at `434024`, following
`sas_saved_sp`; `platform_boot` clears it and both admission flags. Unexpected
ownership, token or kernel-status failures enter `platform_fatal`, the shared
native TRAP1 leaf. They do not discard evidence or attempt recovery. As with the
calibration failure policy, native fatal halt is not coil power-off.

The affected gates passed **123 tests plus six subtests**: 64 platform, 30
calibration, 15 selected builder, two host SAS, seven drain, three E0 kernel and
two CPU-status cases. Ruff passed and independent source/test/evidence review
approved the scoped gate. `tests/test_serial_reconfigure.py` exercises both
journal-publication windows in both post/pend orders, closed admission during
initialization/recreation, repeated inactive drains, native suspension/resumption
before the gate, and cancellation of stuck post-core TX. The shared native
executor is `tests/kernel.py`; CPU execution semantics were not changed.

A controlled test IRQ using native SC4 schedules the early-post case immediately
after close restores SR, before the branch deciding whether to pend. This is an
ordering test, not a claim about physical priorities/timing. The suspended-owner
fixture runs native `16614`, not the whole communications UI. Ports remain
disabled in these kernel cases. Eight temporary-build negative controls were
rejected: six missing-hook cases, a guard incorrectly extended through TX, and
completion incorrectly skipped after rechecking active. Durable gate tests and the CPU-boundary regressions are
in the repository; historical scratch mutation logs are not needed to recover
the protocol, native locators or qualified result.

The historical published-b2 bridge remains five instructions at `7dac8..7dae0`, calling
`platform_sas_process:7d86a`. The worker's fixed frame rises180→188 bytes; the
inspected counter path rises588→596. Its core-entry depth is212 and the new
post-core SC8 path reaches212 before exception/kernel accounting. Independent
review confirms these paths, including fatal branches, remain within the existing
**1024-byte worker allowance**; the historical b2 envelopes remain2928/4096
private and1964/2048 native under their stated premises. Candidate06bef891
retains the same peaks with native capacity3072:1964/3072. This capacity change
is not silently attributed to the historical b2 bytes.

The configuration wrapper `7d7d0` has12 fixed bytes. Task7 retains8 bytes before
the patched callsite: its saved D2 plus `b33e:c0fc` return; `b106` and `c192`
remove accumulated arguments. The wrapper adds16 bytes at each original native
callee entry (12→28). SC9 entry is40 bytes from the task top,52 after its LINK,
before exception/kernel accounting. These are local costs, **not a full Task7
bound**. Complete non-E0 mathematical composition is now INITIAL-ROOTS.md;
maintained-document review and local publication are complete; physical qualification remains separate.

For historical b2, all20 build inputs (including the M68000 libgcc variant) and eight published
artifacts were verified against the reviewed snapshot without rebuilding it.
There are38 patches,19437 extension bytes and zero boot word sum. Original ROMs
are unchanged; `install_ready=false`.

## Normal AC-fail reservation result

The examined terminal path IRQ7:275a→445e→365d4→41796→bf0 has positive native
SafeRAM destinations, not the reserved430000..435fff area. `365d4` copies8 bytes
to current native record+4ef; the twelve startup records of50e bytes occupy
43862e..43c2d5. Seed/counter updates target native SafeRAM; inspected
input/display state uses fixed410xxx scratch/MMIO.

This is conditional on valid native record pointers and no stack overflow;
it does not cover arbitrary corrupted pointers. It is not a battery-retention
claim and not a no-xref-based allocation proof.

## Next verification boundary

The durable IRQ4 and timer-admission regressions retain their register-memory/
coin-body scope, not physical peripheral timing. Historical E0 and serial drain
software gates remain closed under their premises. The settlement candidate's
emitted ABI, tests/negative controls, final-image producer/frame seal and source
review and complete mathematical initial-root/ordinary composition passed
(b1attempt32). INITIAL-ROOTS.md retains all selectors, producer/root checklist
and finite bounds. Main's full reads, independent seven-document review33,
all three embedded reproduction scripts and final input/artifact verification
passed. The exact eight reviewed artifacts are published locally without a
rebuild, still install_readyfalse. See `SETTLEMENT-GUARD.md`, **Candidate gates**. No financial-history investigation or
hardware run is claimed. Physical bus/timer/silicon qualification remains separate.

## Guard-domain closure and IRQ6 selected cases

This is a positive **called-target** exclusion for returning same-stack IRQ
bodies and the frozen-b2 private E0 worker, not inference from the1904/1024
numbers. Reuse valid initialized native pointers/objects/counter copies and
selectors, supervisor/intact contexts, zero optional kernel callbacks and no
external invokers, calibrated timer/bus premises and one terminal IRQ7 assertion.
No fresh-NVM or settled-idle assumption is used to omit active motion phases.
Candidate06bef891 preserves these domains in its final linked image; subsequent
code changes require resealing. `SETTLEMENT-GUARD.md`, **Producer seal**, supplies
the regional computed/interior seal; **Candidate seal and offline gates** records
Main's independent final-image check, passed affected tests and source review.
These checks preserve local IRQ/private-E0 exclusions. INITIAL-ROOTS.md
separately closes ordinary-task callback selectors/nesting; the IRQ component
numbers alone never established that conclusion.

### Returning UART domains

| IRQ / root | Complete callable driver/parser families |
| --- | --- |
|1,141b2|14032/14068/1413e/14178; ordinary14718 rings; RX-B1411e calls43e8f0|
|2,13d98|13bf4→33dfa bill;13c46 ring or43e8f0;13d24/13d5e TX|
|3,13874|1372a→1475c→16976 framing/279a8 BCD validation/16b08 response state;1377a→186e6 or272be framing, or14718;137fa/1383a TX|

Constructor163d6..1640e copies immutable74aca:19732 into43e8f0.74ace:19b16
belongs to DIFFERENT43e8f4, used by the serial TASK.19732 handles SAS framing,
mailbox3a32,14b48 kicks or1a432 DUART cleanup/TRAP1; it does not execute the
744ca command table/19b16/E0 inline.16b08's sole self-call setsfb00=1 before
re-entry, then uses response/data publication.186e6/272be finish/reset byte
buffers, not business-command handlers. Other146xx/14xxx alternatives are
hardware/ring leaves.14b48's bounded six-port switch chooses14374/14396/
143b8/143da/1442e/14450, not a packet callback. IRQ1–3 autovectors2642 are RTE-only.

IRQ2's selected keys37/F0/F1/F2/F6 and every valid bill-status alternative are
preserved in **IRQ2 communication and service ledger**; its exact five-protocol
arms terminate in numeric/format/queue/TX families, not incoming744ca/74af2
business selectors. Those positive selections, not absence of call references,
exclude a financial callee. The640-byte bound remains unchanged.

### IRQ4/5 called targets

IRQ4:2646→411ce, optional39040, kernel tick and scalar44c decrement.411ce's
outgoing union is400/44c,3b84,35b48,36896,41152/41170/411ae,549c8,53dd0.
All three53dd0 calls41470/414b6/416ee load literalA8:53dfa selects45c94,
fixed numeric descriptor69f5c. No arbitrary opcode is inferred.
39040→394bc/3953a/39a52/39aac/400 manipulates output masks and linked DATA
records, not code pointers.41152/41170/411ae use enables/counters/options.
Tick3258→37f6/37ee changes ready/delay links.3b84 posts a future task event,
not an inline handler. IRQ5:26e4 only shadow/MMIO stores and kernel entry/exit.

### IRQ6 all-phase called targets

Reuse **IRQ6 entry and bounded dispatch** for all ten valid state-table targets
and default5e63c, plus special state4. A branch-complete5d6ea..5e654 direct-call
worklist gives5ff2c,5e654,5ee14,5ed22,5ff76,3a14,5fcb2,5eba8,5fed8,3b84,
400,26a0e,68c4c,5ec78,5f022,426,5d368,68b90,60442,5ee00,5f452,60310.
5e654 adds35b48,578a0,34d22 and cross-call5d6ea. Descendants are optical/phase
sampling5eba8/5ec78/5ed22/5ee00/5ee14; timer5fed8/5ff2c/5fcb2/5fcda;
RNG5fe2a→380d6; scalar SafeRAM/MMIO; fault5ff76→6016e fixed3b04a counters
or60254 motor tables;60310/60442 motor finalization/reinit;49f5e output mask,
including49f6c→39fb4→39f96 fixed-byte predicate (INITIAL-ROOTS positive
local-domain contracts), not a hidden controller.
5f452 reads payout/cache DATA, numeric counter43, queues/starts motion through
5d368 and emits notifications. It does not call65128 or controller tables.
Calibration hook refusal uses the established TRAP1 halt.

Communication producers are F6 via34d22, reel+76h at5dd9e/5e93c (valid index0..4),
and91 at5ea44/5f798. Their additional selected table targets are:

| Keys | AS2640c | SAS23d40 | BSG1b8ca | MIS18bea | SDT26fc8 |
| --- | --- | --- | --- | --- | --- |
|76–7A|269a8 default|23f7a|1bcdc|1905c default|270b4 default|
|91|269a8 default|2468a default|1bd8e|1905c default|270b4 default|

26a0e prelude37322 runs only for76..7A:548bc(counter43,cap),68c4c and a
30-entry fixed-frame cache-status scan. Immutable descriptor6a14c is
`00438034 0103 0043` (type1,width3,nonzero pointer), selecting3bc5c's immediate
return and3c9f4's direct pointer arm3ca34.3bbae S100,548bc S124,37322 S148,
so router prefix12 gives160. SAS23f7a and BSG1bcdc compute scalar reel position
by DIVU/68c4c, optionally38b96, then17b14. BSG91:1bd8e calls leaf15288 and
common send; F76+12args+4call+IRQ17b14(50)=142, router prefix12 gives154.
AS default35cf4 fill costs64 below protocol entry; default disables send.
SAS/MIS/SDT defaults return. No selected case falls into a business arm.

### IRQ6 prefix and component arithmetic

S is below26a0e entry, excluding its existing return. Common exception/kernel/
register prefix66, body return4;T1 F40,T2 F20. The conditional timing argument
permits an outerT2+24 around T1 OR outerT1+44 around T2, not a whole recursive
round. Both prefixes remain protected by the calibrated timer/bus premises.

| Router branch | Whole IRQ depth at entry | S | Whole component |
| --- | ---: | ---: | ---: |
|76..7A|142|160|302|
|91|194|154|348|
|existingF6|222|212|434|

At5f798 the194 includes66+4+40+24 outerT2+4 return-to5f452+48 fixed5f452+
4key+4call. DirectT2 is smaller. A final original-ROM CFG/SP worklist over
5d6ea..5e654 (including ten5d9ca entries),5e654..5eba8 and5f452..5fb78 returned
zero unknown local CFG edges. Pre-JSR depths relative to each function's entry
were5dd9e=44,5e3ce=40,5d81c=40;5e6ee=20,5e93c=24,5ea44=24;
5f76c=52,5f798=52. Each edge adds its4-byte return; retained arguments are
included, not merely prologue sums. Normal returning hooks preserve this shape;
final emitted equivalence was verified for candidate06bef891.

IRQ-only17b14 has20 fixed bytes;3d82/3d88 are frame-free. Supervisor TRAP0
adds6 exception+20 saved registers; selected33e0/33f2 have no calls. Active
registered IRQ workspace+28 stays nonzero, so2956 branches298c, excluding
scheduler2a90. Peak=20+4+26=50. At17b9a/17d04 multiply has8 retained scratch+
4return+8save, giving40. General task-side allowance is88, NOT80 or IRQ26.
Thus max(434,302,348)=434 and accepted components
194+640+114+302+66+434+154=1904. This overapproximates co-occurrence; it is not
a claim all maxima coexist or every untouched native subtree is qualified.

### Private E0 called-target worklist

The frozen-b2 `platform_sas_process` direct/PC-relative/tail worklist includes
all fixed compiler-hoisted register-call producers below. These are immutable
LEA-loaded code addresses, not host/fw_runtime callbacks. Candidate06bef891
preserves these target families by the verified instruction/relocation seal;
future changes require resealing. Old addresses below are b2 provenance.

| Family | Fixed producer→target / callsites |
| --- | --- |
|get64 A2|7a01e→get32;7a024/7a034|
|write_committed A2|7a106→fw_storage_write;7a10c/7a128/7a15e|
|record_operation A4/A3|7a526→get64,7a54e→copy;7a52c/7a554/7a5b4/7a5da|
|fw_validate_profile A4|7a84e→valid_symbol;7a88e/7a89e/7a8ae|
|fw_process A2|7b2e4→zero at7b2ea/7b2f6;7b4c0→put32 at7b4c6/7b4de|
|fw_process A3|7b3f4→fw_crc32 at7b3fa/7b4d2;7b748→put64 at7b798/7b7f8|
|game_apply_profile A2|7ca38→config_sum;7ca3e/7ca4c/7ca66|
|game_apply_profile A3/A4/A5|7ca08/7ca0e/7ca14→platform_safe_u8/u16/u32;7ccc4/7ccd2/7cb94|

Core dispatch7b278/table7b27c is bounded by byte compare20 decimal (0x14)
and BHI at7b264/7b26a,
21 intra-function targets, not native game callbacks. Native boundary union:
185ac CRC;27536 NACK and278fe reply→14c66 copy/retry3a1e→14b48 six-port kicks;
3a32 mailbox;3d82/3d88 fixed locks;400/426/44c scalar stores;
platform_write_words range-validated writes/SR/watchdog;549c8/548bc fixed43/47
numeric descriptors;36a7a→options5/6,363c2(7),68c4c and bounded DATA scans;
TRAP1 native diagnostic halt. Counter helpers terminate in the numeric families
in NATIVE-STACK-LEDGERS, not controllers. Concrete b2 calls include7d8ec:185ac,
7d4fc:27536,7da26:278fe,7d9d2:3a32,7ca7e:36a7a,7c60e:548bc,
7b904 tail549c8 and scalar tail adapters.

`game_submit` only records an asynchronous request, not4341c/420f8/ae6a/
61172. Owned177-byte restoration does not call3756c. Production motion0 returns
UNSUPPORTED. These positive domains, not the1024 ceiling, exclude synchronous
financial entry on the private stack. Notifications can resume native financial
tasks on THEIR selected contexts.

### CURRENT publication, terminal IRQ7 and fresh-SP exceptions

Kernel queue/mailbox messages are VALUES. Fixed standard2948 services update
queues/TCBs; configured optional hooks27c0..27cc are zero and unregistered
extended dispatch remains disabled. IRQ entry16 establishes nesting; exit11
occurs after body/register unwind. Returning internal services cannot schedule
while workspace+28 is nonzero. Ordinary context switches resume selected SSP;
they do not execute task handlers inline on an IRQ/private caller chain.

Therefore no returning IRQ runs the settlement guard during mixed publication
2b54..2b70 (newSSP, oldCURRENT). IRQ7's terminal old-stack domain
275a→445e→365d4→41796→bf0 uses valid record copies,seed/counter updates,
41b3e/counter43,24ae,4185c sensor and448c;4185c uses400/411ae, not wagers.
bf0 selects4385dc and watchdog-halts. Repeated IRQ7 assertions remain excluded.

DIV0 vector14→f54→108a→115c→1184:118a loadsSP437ffe, abandoning old frames.
For the new IRQ6 cases the old-stack maximum below router is92 fault-site+
6exception+42 diagnostic SC05=140, below160/154. SC16's active-IRQ+f2 stays
above1 and cannot take284c's alternate-stack copy; SC05 cannot schedule with
+28 nonzero. Its snapshot IDs at74eb4 terminate with00; no growing loop.
No nonzero41017c premise is invented. Subsequent native diagnostics halt or
1196→dee→8f6 reloadsSP again; `SETTLEMENT-GUARD.md`, **IRQ, private E0 and
exception separation**, preserves the intermediate no-covered-hook domain.
Bootstrap financial entry is real but belongs to fresh[436000,438000), not
old IRQ/private-E0 residency. TRAP1 instead halts without silently discarding
its stack before diagnostics. Neither is a financially safe recovery promise.
