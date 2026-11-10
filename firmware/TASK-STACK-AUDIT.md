# Non-E0 extension stack qualification

This record states whether the experimental firmware extension's added code fits
on the stacks it runs on, other than the private E0 handler's own stack. It gives
the overall result: every covered family of extension hooks, on native task
stacks and on the bootstrap stack, fits its allocation under the stated premises
once the native task stacks are enlarged from 2048 to 3072 bytes and settlement is
guarded at `0x589ba`. It
also records how that capacity change was made and proved to fit the native kernel
pool, and which gates the published image has passed. The extension's hooks run
inside native tasks on stacks sized for the stock firmware, so their extra stack
use has to be shown to fit; earlier overapproximated sums for the 2048-byte stacks
were too large (capacity warnings, not demonstrated overflows).

**Status:** the software proof is complete and independently reviewed, and the
reviewed artifacts are pinned and published locally. **Physical and installation
gates remain pending**; local publication does not authorize programming chips or
operating the machine. The extension is experimental firmware for one privately
owned machine used for entertainment, not for regulated or commercial machines
([README.md](README.md)).

This record summarizes and owns the capacity and gate status; the proofs it relies
on live in the other audit records, listed under **Authoritative proof
boundaries**: [INITIAL-ROOTS.md](INITIAL-ROOTS.md) (initial roots),
[NATIVE-STACK-LEDGERS.md](NATIVE-STACK-LEDGERS.md) (local costs),
[SETTLEMENT-GUARD.md](SETTLEMENT-GUARD.md) (the settlement guard) and
[IRQ-AUDIT.md](IRQ-AUDIT.md) (interrupts and private E0).

## Terms used in this record

SETTLEMENT-GUARD.md, **Terms used in this record**, defines the shared vocabulary
in more detail. In brief:

- **Native** code, kernel and task stacks are the original firmware's; the
  **extension** is the added code. **Covered** hooks are the patched native sites
  where extension code runs, and **extension-live** work is execution while an
  extension frame is on the stack.
- A **root** is where a stack begins (a task entry, the reset bootstrap, an
  interrupt). **Ordinary roots** are the native task and standard SAS roots
  composed in INITIAL-ROOTS.md, **Complete ordinary-root compositions**; the
  **covered-root proof** shows which covered entries each root can reach, and with
  what stack already in use.
- **R(x)** is the residency at the entry of routine x, including its return
  address: P = R(`0x61172`, normal game start), Q = R(`0x65128`, the evaluator) and
  L = R(`0x3756c`, the runtime loader). The **normal**, **evaluator** and
  **runtime-loader** budgets are the worst P, Q and L plus the covered work below them.
- **FIRST** (first check) is an entry to the settlement routine `0x589ba` with no
  admitted settlement already live; **B** is the residency at that check. **E** is
  the residency at an admitted settlement entry, and **M**, **G**, **S**, **I** and
  **H** are the covered ceiling, next-check growth, guard scratch, interrupt
  allowance and reserve of SETTLEMENT-GUARD.md, **Reserve and induction**.
- **I1904** is the conditional 1904-byte interrupt allowance from IRQ-AUDIT.md.
- **Producer**: an instruction or table that supplies a code address to a computed
  transfer. **Selector**: a mutable value that indexes an immutable table; the
  **AS/BSG selector DAGs** are the two acyclic callback graphs of the controller
  tables. The 27 **controller arms** are the cases of `0x5c334`'s argument switch.
- A **frontier** is where a root's composition stops at a callee covered by a
  separately proved contract (INITIAL-ROOTS.md, **Nonmotor frontiers — complete
  synchronous topology** and **Shared motor contracts for ordinary/bootstrap
  composition**).
- **Positive**: established by enumerating actual targets, not by absent
  cross-references.
- **b2**: an earlier, frozen extension build, kept as proof provenance. The
  **candidate** is the published image `06bef891…7197`.
- **TCB**: kernel task control block. **SC41**: the native kernel's queue-creation
  service, called through TRAP #0.

Addresses are hexadecimal (written with `0x` in prose, without it in tables and
code); byte counts are decimal.

## Scope and gate

**The mathematical covered-root and ordinary-root proof was independently reviewed
and approved; all seven maintained documents were independently reviewed and
approved. Final
pinning and exact-artifact local publication are complete. Physical and
installation gates remain PENDING.** Local artifact publication does not authorize
programming chips or operating the machine.

The published `build/main.bin` is
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`; its current
Winbond-packaging manifest is
`ef1728ca2cb342666e6b31ebee1be8fdc411322b36f7c509c67dfb296b5f7b01`. The
packaging-only revision preserves `main.bin` and all four compiled artifacts byte for
byte (a same-toolchain record; across toolchain builds only `main.bin` and the chip
images are expected to reproduce, see `toolchain.md`, **Outputs and
reproducibility**); `PROGRAMMING.md` owns that gate. The image contains native
capacity 3072 and the settlement entry guard, with `install_ready=false`. The eight
reviewed artifacts were copied without rebuilding, and their exact identity, all 20
inputs, the original ROMs, the checksum and the chip lanes were verified. Historical
b2 had 2048-byte native stacks and is no longer the published build.
SETTLEMENT-GUARD.md, **Candidate seal and offline gates**, owns the pins and the
software evidence; physical installation is not approved.

The original ROM SHA-256 is
`49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a`. This
conditional software proof rests on the existing Ghidra analysis, the actual M68000
bytes, immutable tables, complete local control-flow and stack-pointer walks, and
the identity of the emitted instructions and frames. It includes native arguments,
incoming returns, LINK/MOVEM/SR saves, nested hooks, interrupt-mask and publication
windows, and returning delays. Neither function names, address regions nor absent
cross-references establish which task owns a piece of code.

## Authoritative proof boundaries

- [INITIAL-ROOTS.md](INITIAL-ROOTS.md) is the single initial-root authority. It
  holds the complete checklist of incoming producers and roots; the AS/BSG immutable
  selector DAGs and their weighted edges; all 27 controller arms; the attribution of
  Task3, Task6, Task7, SAS, fault and bootstrap roots; the full `0x5c8d8`
  continuation; both the `0xa5e0` and the `0xad72` ingress through
  `0xcf54→0x62958`; and the positive ordinary, shared-motor and frontier contracts.
  Its durable reproduction needs no temporary files or external logs.
- [NATIVE-STACK-LEDGERS.md](NATIVE-STACK-LEDGERS.md) owns the local emitted costs:
  numeric, wager, diagnostic, serial, loader, boot, fatal and guard-descendant.
  Summaries of initial roots elsewhere defer to INITIAL-ROOTS.md; they are not a
  second, provisional census.
- [SETTLEMENT-GUARD.md](SETTLEMENT-GUARD.md) owns the admitted `0x589ba` induction
  (M = 328, G = 120, H = 2232, S = 10), the full regional producer, allocation and
  CURRENT-publication proof, and the refusal and unwind behaviour.
- [IRQ-AUDIT.md](IRQ-AUDIT.md) owns the conditional I1904, the all-phase positive
  exclusions of interrupt and private-E0 code, the calibration, timer and bus
  premises, the terminal IRQ7, and the fresh-SP exception separation. Its E0 and
  serial-drain historical gates remain scoped evidence, not qualification of the
  whole native workload.

The promise covers extension-live and added-code work. It does **not** cover every
untouched legacy temporary stack peak, finite financial settlement, rollback,
coil-off, recovery from arbitrary corrupt pointers or histories, or physical
timing. A positive summary that pure native work makes no covered entry and returns
balanced excludes that work only when no extra extension frame survives across it;
intact contexts remain a premise. The resource guard enforces the covered limit
without bounding financial rounds and without a new callback guard. No investigation
of financial history remains to be done.

The premises are:

- valid initialized native and kernel objects, registries, pointers and counter
  copies, and a valid value of **each** selector;
- immutable producer and forwarder identity;
- intact supervisor call, return and exception contexts;
- zero optional kernel callbacks, disabled unregistered services and no external
  invoker;
- the enforced calibration interval plus the stated timer, bus and silicon model;
- one filtered terminal IRQ7;
- a fresh reset SP, with nothing resuming the old stack.

Marker-valid SafeRAM need not originate from this exact ROM. Mutable selectors,
MODE, protocol and amounts are overapproximated independently; no assumption that a
value stays stable across a wait, or that non-volatile memory is fresh, is added.
I1904 overapproximates which interrupts can occur together; historical excessive
sums were capacity warnings, not overflow witnesses.

## Complete covered composition — published reviewed image

The full inventory, the root derivations and the positive ports are in
INITIAL-ROOTS.md, **Covered patch-family inventory**, **Root-to-covered-entry
checklist** and **Complete allocation-level composition**. This table only
summarizes them. The middle column is the worst stack use without interrupts; the
right column adds I1904 and compares with the allocation.

| Allocation / family | Covered result without IRQs | With I1904 / allocation |
|---|---:|---:|
| Native initial normal/game/diagnostic | max P 224 + 192 = 416 | 2320 / 3072 |
| Native initial evaluator | max Q 220 + 86 = 306 | 2210 / 3072 |
| Native completed-runtime loader | max L 196 + 302 = 498 | 2402 / 3072 |
| Native event / BET / repeat | 244 / 342 / 192 | 2148 / 2246 / 2096, all ≤ 3072 |
| Task7 diagnostic / motion / configuration | 142 / 98 IRQ-open / 158 | 2046 / 2002 / 2062, all ≤ 3072; the deeper masked motion code keeps its separate terminal-IRQ treatment |
| Standard native SAS identity 1F/53 | 170 / 174 | 2074 / 2078, both ≤ 3072; not private E0 |
| Native first 589ba check | B 268 + scratch 10 = 278 | 2182 / 3072 |
| Bootstrap early boot/clear | max 648 | 2552 / 8192 |
| Bootstrap shared normal / general loader | 302 / 532 | 2206 / 2436, both ≤ 8192 |
| Bootstrap first 589ba check | B 58 + scratch 10 = 68 | 1972 / 8192 |
| Native admitted settlement | E ≤ 840, covered E + 328 ≤ 1168 | 3072 / 3072 |
| Bootstrap admitted settlement | E ≤ 5960, covered E + 328 ≤ 6288 | 8192 / 8192 |

The initial ordinary maximum with interrupts open, 498, does not replace the
allocation cap of admitted settlement or the separately qualified masked paths.
Likewise the early-boot 648 is not a cap on later rounds of bootstrap work. The
reset SSP `0x437ffe` contributes 2 bytes against `[436000,438000)`; the early-boot
row already includes them. No unexpected interrupt before the startup state is
initialized is licensed.

The corrected incoming values are:

- Task3: P = 104; Task6 helper prefixes: P = 64 and P = 52;
- `0x43c2→0x5c8d8` with R = 64, giving X = 72;
- `0xcf54` (primary R = 36, secondary R = 56) → `0x62958` with R = 60, giving
  Y = 64 and X = 72.

The shared routine `0x57e70` has a first-check B of 88, not 80. AS FIRST 240 and BSG
Q 220, P 224 and FIRST 268 replace the historical AS 216 and the one-descent rows
208/212/256; those superseded figures are not current proof inputs. The loose
bootstrap 226 handoff was unsupported and has been withdrawn; the all-arm bootstrap
FIRST 58 is now closed. Bootstrap and Task7 attribution comes from positive
producers, not from address ranges.

Private E0 keeps its qualified private bound of 2928/4096 bytes and its native
entry/exit peak of 1964. For this candidate that peak is 1964/3072; the earlier
1964/2048 comparison belongs **only** to historical b2. Commissioning keeps its
separate pre-kernel 4096-byte arena and lifetime proof; INITIAL-ROOTS.md explicitly
accounts for the stackless reset hooks, commissioning and the terminal AC-fail. No
other SAS command may borrow E0's private allocation.

## Approved guarded boundary

The owner approved bounded settlement resource admission, accepting that settlement
may be left unfinished and that the existing native fatal halt may follow.
`src/game_hooks.S:game_settlement_entry` intercepts `0x589ba` before its 108 native
prologue bytes. Admission restores the registers, full SR and SP, replays the
original prologue and continues at `0x589c2`. Refusal removes the 10 scratch bytes,
restores the registers and SR, and tail-enters `platform_fatal`, which uses the
native six-byte exception ABI of TRAP #1. It does not clear payment state, fabricate
success, roll back, restart or promise coil-off.

```text
H = max(M328 + I1904, G120 + S10 + I1904, G120 + 6 + I1904, G120 + 122 + 154) = 2232
```

The native 3072-byte allocation admits E ≤ 840; the bootstrap 8192-byte arena
admits E ≤ 5960. The first check's scratch is bounded separately by the finite
native B 268 and bootstrap B 58; later recursion and unwind rely on the admitted
allocation and the induction, not on a finite number of financial rounds. The
obsolete proposal guarding only `0x5999e` with 832 bytes, and the old lifecycle to-do
items, are not live alternatives. SETTLEMENT-GUARD.md owns the exact cut and the
refusal arithmetic.

## Implemented native stack capacity

The published image changes only the native configured task-stack word at `0x27b6`
(`0x27a0 +0x16`) from `0800` to `0c00`: 3072 bytes for each of the 18 preallocated
slots. The separate `0800` at `0x27a8`, the pool base and size, the slot count, the
native kernel instructions and the extension RAM layout are unchanged. This is
preferable to private Task6/7 entry thunks because it keeps native allocation
ownership and also covers the additional non-E0 SAS callers. No
identity-register-save change is needed under the approved mathematical proof and
its premises. The existing private E0 bridge remains.

The native initializer `3680`, in particular `3714–377a`, reserves fixed areas of
`0x134 +0x800 +0x40` bytes, 18 TCBs of `0x50` bytes and 18 stacks. The six identified
boot queues are three of 64 entries and three of 32; `3610` requests exactly
`14 +4*entries` bytes, and the allocator `37ae` charges that amount without
rounding. For those routes the pool budget is:

```text
18 * stack_size + 5096 <= 65536
3072-byte stacks: heap after queues 0x4013e8
                 lowest stack base 0x402800
                 remaining pool 5144 bytes
```

The fixed 5096 bytes are the 2420 bytes of fixed areas, 1440 of TCBs and 1236 of
queues (3 × 270 + 3 × 142).

This maximum over allocation routes is closed under the existing premises: valid
state, an initialized registry, intact contexts and no external service invoker.
All 42 identified TRAP #0 sites have fixed service selectors. The identified SC41
routes create the fixed boot queue IDs 1–6, and an existing ID is rejected before
any allocation. Reset (`dee →8f6`) reloads the SP and reinitializes the pool before
the queues are created, so reset histories do not accumulate allocations. Task
creation and deletion and mailbox operations add no bump allocations.

The closed producer domains include Task8 registration (`cfda/d252`), the UART and
serial fixed table installation (`163d6–1640e`), the generic callback pairs through
`3ed38`, and the `48ada/489ae` controller-table producers. Both supplied personality
ROMs were included in the reference census. The tables `69e24/2` and `6aa8e/3` are
six-byte data records copied to protected fields, not additional function-pointer
tables.

The helpers `48a12` and `48a76` cannot seed an additional callback domain. A first
entry into their combined interval `[48a12,48ada)` is excluded:

- the preceding `48a10/48a74` are RTS instructions;
- no encoded external transfer or address construction in the production image or in
  either personality ROM targets the interval;
- every reachable computed-call producer domain excludes it;
- the ten signed-word PC-indexed switches with enough reach (`40dfe`, `42014`,
  `4268a`, `43ebc`, `4b372`, `4cc58`, `4dba4`, `4dce4`, `4ddb0`, `4ff78`) have
  bounded, decoded target tables that exclude it;
- roots, explicit boot continuations and intact saved return contexts introduce no
  first entry, and the helpers' own forwarding calls cannot bootstrap reachability.

This is a reachable-domain argument, not an inference from absent cross-references
alone.

A temporary offline experiment ran the frozen image's native initializer, queue and
task services, and task deletion and re-creation, with only the configuration word
substituted in memory. Both 2048 and 3072 passed: six queues, all 18 task slots,
rejection of a nineteenth task without changing the live set, retention of the
recycled stack top, and an intact guard area above `0x410000`. The 3072 case reported
5144 bytes free. The published build was not changed by it. This corroborates the
allocation arithmetic; it is not scheduler, interrupt, timing or complete-workload
qualification. The candidate's maintained allocation regression is reported below.

## Completed software and publication gates

SETTLEMENT-GUARD.md, **Candidate seal and offline gates**, records:

- the emitted guard ABI and patches;
- the 5,601 extension instructions decoded from the prior build, with 272 exact
  +104-byte relocations, and an identical `ext.stack`;
- the seal of the 44 switches and the producer arrays;
- threshold and negative-control tests, modelled interrupt and native fatal tests,
  and the regression over all 18 slots and six queues, exhaustion and recycling;
- 529 tests plus six subtests, one passing rerun after the allocator cleanup, and an
  independent source and test review.

The scoped AS/BSG, frontier and Task7 reviews approved those analyses, and the local
window groups (115 Task7 windows with six switches, 77 ordinary windows with four
switches) were independently reproduced. The final mathematical review of the ordinary-root and root composition
approved it, including the 36th switch at `0x14f74` and the identity of the original
and candidate.

All seven documents were read in full and independently reviewed. The review's narrow
clarification of the Task2/direct Task6-clear scope was applied, and all three durable
reproduction scripts ran successfully. Final verification of the inputs, manifest and
artifacts, and exact-byte local publication, are complete; `install_ready=false`
remains. A changed source or image needs renewed evidence for whatever it affects and
a fresh producer and frame seal; it does not automatically reuse this image's
approval. Physical timer, bus, silicon and mechanical qualification and installation
qualification are separate, later gates. No serial, SSH, programmer, chip or machine
operation is authorized.

## Next boundary

The scoped software gate for native-task stacks is complete, and so is the
Winbond-sized chip-image packaging. Next is separately approved physical
qualification. This proof requests no further root, callback or financial-history
investigation and no additional guard implementation.
