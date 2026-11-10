# Settlement entry resource guard

This record proves that the stack guard the experimental firmware extension
installs at the native settlement routine `0x589ba` keeps the extension's stack
use inside the stack allocation that owns it, however often settlement re-enters
itself. Settlement can re-enter itself through `0x3d294`, several extension hooks
run beneath it, and the proof assumes no bound on the number of financial rounds.
Instead of a fixed-depth budget, `game_settlement_entry` checks at every entry that a proven reserve of 2232
bytes remains, and halts through the native fatal path when it does not. The
record covers the guard's entry contract, the reserve arithmetic and the
induction behind it, the call graph below an admitted entry, the census showing
that control cannot enter the guarded bodies any other way, how the guard
identifies its allocation, its separation from interrupts, private E0 and
exceptions, and the offline gates the published image passed.

**Status:** the software proof and its offline gates are complete and were
independently reviewed. **Hardware and installation remain unqualified**; nothing
here authorizes programming chips or operating the machine. The extension is
experimental firmware for one privately owned machine used for entertainment,
not for regulated or commercial machines ([README.md](../README.md)).

This is one of five audit records that together qualify the extension's stacks:

- [INITIAL-ROOTS.md](INITIAL-ROOTS.md) is the sole authority for initial roots:
  every producer of an incoming call, the selector graphs, the controller arms and
  the first-entry bounds this record starts from.
- [NATIVE-STACK-LEDGERS.md](NATIVE-STACK-LEDGERS.md) holds the local,
  instruction-level costs, including the descendants of an admitted settlement entry.
- [IRQ-AUDIT.md](IRQ-AUDIT.md) owns the 1904-byte interrupt allowance, the
  exclusion of settlement from interrupt and private-E0 code, and the timer premises.
- [TASK-STACK-AUDIT.md](TASK-STACK-AUDIT.md) owns the native stack capacity and
  the overall gate status.

This record owns the cut graph below an admitted `0x589ba` entry, the regional
producer seal, the allocation identity, the reserve induction (H = 2232, M = 328,
G = 120, S = 10) and the guard's refusal and unwind behaviour.

## Terms used in this record

- **Native** means the original firmware: its code, its VRTX kernel and the
  kernel's task stacks. The **extension** is the added code linked into
  `[0x79100, 0x80000)` (`patches.json`).
- **Covered hook**, **covered entry**: a patched native site at which extension
  code runs (INITIAL-ROOTS.md, **Covered patch-family inventory**). **Covered
  work** is execution while such an extension frame is live on the stack
  (**extension-live** work). A **covered ceiling** is the most bytes covered work
  can add above a stated reference point.
- **Root**: a point where a stack begins: a native task entry (its initial
  26-byte context is consumed by the kernel's restore/RTE), the reset bootstrap,
  an interrupt or the private E0 handler. A **covered root** is a root from which
  a covered entry can be reached. **Ordinary roots** are the native task roots
  composed in INITIAL-ROOTS.md, **Complete ordinary-root compositions** (Tasks 2,
  3, 5 and 6 and standard SAS); Task7 and the bootstrap are composed in their own
  sections there.
- **FIRST** (first check): an entry to `0x589ba` with no already-live admitted
  `0x589ba` ancestor. **B** is the stack residency at such a first check:
  FIRST 268 bytes on native stacks and FIRST 58 on the bootstrap stack.
- **R(x)** is the residency at the entry of routine x, including the incoming
  return address. INITIAL-ROOTS.md writes P = R(`0x61172`, normal game start),
  Q = R(`0x65128`, the evaluator), L = R(`0x3756c`, the loader), X = R(`0x5c334`)
  and Y = R(`0x6268e`). The **normal**, **evaluator** and **runtime-loader**
  budgets (416, 306 and 498 bytes) are the worst P, Q and L plus the covered
  envelope of the hooks below each. In **Reserve and induction** the letter L
  instead means the allocation size.
- **Admission** and **refusal** are the guard's two outcomes: continue into native
  settlement, or halt through TRAP #1.
- **I = 1904** is the conditional interrupt allowance from IRQ-AUDIT.md: the
  stack reserved for nested interrupts arriving at any point.
- **Producer**: an instruction, table, registry or pointer that supplies a code
  address later used by a computed transfer (an indirect call or jump, a table
  dispatch, a PC-indexed switch or a vector). The **producer seal** is the census
  showing that every producer able to reach the guarded bodies targets only their
  entry.
- **Selector**: a mutable RAM value used to index an immutable table, such as the
  display MODE byte or a controller-record index. The **AS** and **BSG** selector
  graphs are the callback graphs of the two immutable controller-table instances
  (action arrays `69de8`/15 and `6aa3e`/20). INITIAL-ROOTS.md shows both are
  directed acyclic graphs (**selector DAGs**), so callback nesting is bounded.
- **Controller**: a native routine that dispatches through such tables
  (`0x487a6`, `0x489ae`, `0x48ada`); the 27 **controller arms** are the cases of
  `0x5c334`'s argument switch (INITIAL-ROOTS.md, **All 27 arms of 5c334, plus
  default — exact composition**).
- **Positive** (domain, exclusion, contract): established by enumerating the
  actual targets or children, never by the absence of cross-references, a function
  name or an address range.
- **b2** (frozen b2): an earlier extension build, pinned in NATIVE-STACK-LEDGERS.md,
  **Inputs and conventions**. The local costs were first established on it and
  carry over to the published image (the **candidate**, `main.bin`
  SHA-256 `06bef891…7197`) through verified instruction and frame identity.
- **CURRENT**: the kernel's current-task pointer, the long word at `0x400024`.
  **TCB**: a kernel task control block (`0x50` bytes); TCB+`0x38` holds the saved
  supervisor stack pointer (SSP) and TCB+`0x40` the allocated stack top.
- **Private E0**: the extension's private SAS long-poll `0xE0` handler, which runs
  on its own 4096-byte stack at `[0x433000, 0x434000)`.
- **SafeRAM**: the native SafeRAM region, `0x438000`–`0x43ffff`
  (COMMISSIONING-CONTRACT.md).
- **SCnn**: native kernel service nn, called through TRAP #0 (for example SC41
  creates a queue).
- **Terminal IRQ7**: the level-7 (AC power-fail) interrupt, which ends in a halt.

Notation. Addresses, field offsets and opcode keys are hexadecimal; byte counts,
cardinalities, normalized indices and MODE-table labels are decimal. Prose writes
addresses with `0x`; tables and code blocks drop the prefix. A value in
parentheses after a routine, as in `0x53dd0(2e)`, is the selector or option
argument passed to it, in hexadecimal. `A→B` means A calls or transfers to B;
`A:B` names location B reached within or from A. Ranges `[a, b)` include a and
exclude b.

## Status, scope and provenance

The implementation, the emitted-code and producer seal, the offline tests, the
source review, the complete mathematical initial-root and ordinary-root proof, and
the review of the seven maintained documents are all **approved**. The final
artifacts are pinned and were published locally. **Hardware and installation
remain unqualified.** Mathematical approval does not authorize a release or any
hardware operation.

The implementation is `src/game_hooks.S:game_settlement_entry` and
`src/platform_hooks.S:platform_fatal`; `patches.json` raises native stack capacity
to 3072 bytes and installs the JMP/NOP detour at `0x589ba`. INITIAL-ROOTS.md
supplies the initial bounds: native FIRST 268, bootstrap FIRST 58, native
normal 416, evaluator 306 and runtime loader 498, shared bootstrap normal 302 and
general loader 532, and early boot 648. Because its AS and BSG selector graphs are
immutable and acyclic, and its ports are positively closed, no new callback guard
and no assumption of finite financial progress is needed.

The published image is the exact reviewed build in `build/`: `main.bin` SHA-256
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`,
with `install_ready=false`. The build directory is not committed; a fresh build
must reproduce that hash ([GETTING-STARTED.md](../../GETTING-STARTED.md), **Check
the build**). Historical b2 is kept only as proof provenance; NATIVE-STACK-LEDGERS.md, **Inputs and conventions**, pins its ELF,
disassembly and manifest hashes. The original ROM
`roms/s6000_main_S6M00002B5.bin` has SHA-256
`49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a`.
Native instruction and table locators below refer to that original ROM; extension
code is identified by its symbols, not by old linked addresses.

The evidence is read-only Ghidra analysis of the original program, raw
`m68k-elf-objdump -D -b binary -m m68k:68000` instruction reads, decoding of
immutable big-endian tables, and local stack-pointer worklists followed through
every returning exit. The worklists tracked LINK/UNLK, saved registers and SR,
retained arguments and branch merges. They did not assume a callee was safe
because it had no cross-references, and did not trust Ghidra's incorrect
non-returning annotation on `0x3a1e`. The supporting native and table regions were
compared with frozen b2 during the original proof, and the 44 switch tables and the
controller tables were decoded a second time independently. This record is
self-contained: no working notes or temporary files are needed to recover its claims.

**Promised:** bounded guard overhead, and bounded extension-live (covered-hook)
execution between checks, including refusal and the unwinding of covered work.
**Not promised:** safety of every temporary stack peak of stock native code,
finite financial settlement, rollback, coil-off, recovery from arbitrary
corruption, hardware timing or installation. A positive summary showing that
native work makes no covered entry and returns balanced may exclude that work only
when no extra extension frame survives across it. A balanced return does not prove
the work cannot overflow temporarily; intact contexts remain a premise.

Inherited premises:

- registries, kernel objects, native pointers and counter copies are valid and
  initialized;
- call, exception and return contexts are intact, and tasks run in supervisor mode;
- native selectors and geometry are valid, including display MODE 0..29 at `0xa64c`;
- optional kernel callbacks are zero, unregistered extended services are disabled,
  and there is no external invoker;
- the cross-call calibration interval `[0x60, 0x800]` is enforced, the conditional
  timer and bus model holds, and there is one filtered terminal IRQ7 assertion,
  all as stated in `IRQ-AUDIT.md`.

Accepted marker-valid SafeRAM need not originate from this exact ROM. No premise of
freshly initialized non-volatile memory, stable financial values, or a MODE that
stays the same across a wait is introduced. These remain conditional premises, not
qualification of an installed board.

## Entry contract and capacity

The detour replaces eight original bytes at `0x589ba`, `4e56ffb048e73f00`:
LINK A6,#-80 followed by MOVEM.L D2-D7,-(SP), which together occupy 108 fixed
stack bytes. The source uses a JMP plus a NOP rather than a JSR, so no additional
return address is retained.

- **Admission** restores the incoming D0, A0, full SR and SP, replays both original
  instructions exactly, then jumps to `0x589c2`.
- **Refusal** removes all 10 scratch bytes, restores the full SR and registers, and
  tail-jumps to `platform_fatal`, which executes TRAP #1. The caller's existing
  return stays untouched. A bare JMP to the TRAP #1 handler at `0xfc4` would break
  the native six-byte exception ABI, and is not the design.

The capacity patch changes only the word at `0x27b6` (configuration `0x27a0` +
`0x16`) from `0800` to `0c00`, that is from 2048 to 3072 bytes for each of the 18
native slots. The separate reserve word `0800` at `0x27a8`, the pool base and
length, the slot count and TCB ownership are unchanged, and the private E0 stack
stays at 4096 bytes. `TASK-STACK-AUDIT.md`, **Implemented native stack capacity**,
gives the closed allocation-route budget `18*3072+5096=60392`, 5144 free bytes,
lowest stack base `0x402800` and the six boot queues. The published image
`06bef891` contains this capacity patch; historical b2 did not.

## Reserve and induction

Let:

- p be the stack pointer at the original `0x589ba` entry and T the exclusive top
  of the allocation that owns it;
- E = T − p be the resident bytes, including the caller's return, before the 108
  native prologue bytes or the 10 guard scratch bytes;
- L be the allocation size;
- M = 328 be the covered ceiling, G = 120 the additional residency at the next
  check, S = 10 the guard scratch and I = 1904 the interrupt allowance.

```text
H = max(M+I, G+S+I, G+6+I, G+122+154) = 2232
admit iff p >= allocation_base + H   (unsigned; equality passes)
equivalently E <= L-H
```

The first term of H covers covered work below an admitted entry with an interrupt
at its peak. The other three cover the next check, at most G above: its scratch
with an interrupt, its refusal's 6-byte TRAP #1 exception frame with an interrupt,
and the fatal path after the interrupt mask is raised to level 7, with terminal IRQ7 still
possible. The guard restores its
scratch before native work or refusal continues. Before the TRAP #1 handler raises
the interrupt mask to level 7 at `0xfd0`, the 6-byte exception frame plus the full
I must fit. After `0xfd0`, the native fatal path costs 122 bytes, including that
exception, plus the 154-byte terminal IRQ7 allowance; lower-priority interrupts can
no longer arrive. The handler at `fc4` writes its diagnostics to absolute RAM, not
to a fresh stack, and then halts through `13ee→1274/1246→123c`. It does not
call settlement or recovery code and does not clear a payment flag. Pending
financial work can therefore remain unfinished, which the owner accepted.

| Domain | L | admitted max E | next-check E ≤ | next scratch + I | next 6 + I | next 122 + 154 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Native candidate | 3072 | 840 | 960 | 2874 | 2870 | 1236 |
| Bootstrap | 8192 | 5960 | 6080 | 7994 | 7990 | 6356 |

Each row's last three columns are the worst totals at the next check; all stay
within L.

**First check.** A first native entry has B ≤ 268, so 268 + 10 + 1904 = 2182 bytes
fit before any admission. A first bootstrap entry has B ≤ 58, giving
58 + 10 + 1904 = 1972. The complete ancestry is in `INITIAL-ROOTS.md`, **Complete
ordinary-root compositions** and **Bootstrap and earlier qualifications**. These
bound only the first check, not later rounds.

**Induction.** Every admitted entry satisfies E + 328 + 1904 ≤ L, so all covered
work below it fits. Each next check has E′ ≤ E + 120, so its scratch and its
refusal fit even if it is rejected. Returning children restore their entry SP:
ancestors resume under their own admitted E, not the deepest child's E′. No bound
on the number of financial rounds is needed. A reset that reloads a fresh SP starts
a new bootstrap induction instead of retaining interrupted ancestors.

## Covered cut graph

Every route from an admitted `0x589ba` entry to covered work or to the next check:

| Route below an admitted 589ba entry | Covered ceiling / next-check ingress |
| --- | ---: |
| 597da/598ae → 59f3a → 61c50 → 61172 changed hooks | 112 + 216 = 328 |
| 5990c → 61c50 → 61172 changed hooks | 112 + 196 = 308 |
| 5999e → 3d294:3d2a8/3d752 → 61c50 → 61172 changed hooks | 112 + 8 + 196 = 316 |
| 5999e → 3d294:3d594/3d6de/3d6fc → next 589ba | 120 |
| recursive return 599a4 → 53dd0(2e) → 599b0 epilogue | no covered entry or re-entry |

The first column of each route is a call site inside `0x589ba`'s body; 112 is its
108 fixed bytes plus the call's 4-byte return. The route through
`0x3d294:0x3d2a8/0x3d752` adds 8 more (4 saved bytes and a 4-byte return).
The 192-byte envelope at `0x61172` is the work of the **changed hooks**, not its
entire stock body; `0x61c50` adds 4 and `0x59f3a` adds 24 to the residency at
`0x61172` entry (hence 196 and 216). The envelope includes the normal-start hook,
candidate `0x615ce`, commit `0x615fa` and the shared `0x3fbbc` diagnostic hooks.
Wrapper scratch unwinds before the stock code continues.
`NATIVE-STACK-LEDGERS.md`, **Covered continuation prefixes and unwind**, records
the actual native prefixes and returns.

`0x589ba` has 108 fixed bytes. Its call at `0x5999e` leaves no outgoing argument
block; `0x3d294` saves 4 bytes, and each of its three calls back to `0x589ba` passes
no arguments and is immediately followed by the D2 restore and RTS. So
G = 108 + 4 + 4 + 4 = 120. After the `0x5999e` call returns, `0x599aa` dispatches
exactly `2e`, followed only by frame-pointer-relative restores, UNLK and RTS. Local
backward edges discharge their argument blocks; the only growing cycle of normal
calls in this closed cut graph is `0x589ba`↔`0x3d294`, and every new `0x589ba` entry
in it is intercepted by the guard.

### Positive delegated domains

The cut graph delegates the rest of the work below an admitted entry to these
closed domains:

| Family | Closed positive domain / explicit boundaries |
| --- | --- |
| Owned numeric/scalar/selector | 51da2/523a4/5a16c; typed numeric, CRC, copy and protected writes; only explicit 599ba/router/accounting boundaries |
| Controllers | 3fefa, 5aa68(6/7), 599ba, 4b78e, whole 59b76; progressive 1/2/7, all 30 a64c display callbacks |
| Continuations | 61c50/59f3a → 61172/3fbbc changed hooks; remaining numeric/display/router/accounting children |
| Accounting | 53dd0 selected rows in the NATIVE ledger; data descriptors and fixed service branches, no task receiver invoked inline |
| Router | exactly the five-protocol cases below; normal numeric/format/queue tails; no incoming business-command dispatcher |

The complete targets, children, cleanup and retained arguments are in
`NATIVE-STACK-LEDGERS.md`, **Settlement guarded descendant ledgers (frozen-b2
design)**. In particular `0x51da2→0x599ba` adds 60 bytes (E + 172),
`0x523a4` → router key `0x201` adds 72, and `0x5a16c` → router key `0x113` adds 12.
The numeric and scalar trees hide no controller callbacks. The native-only facts
already established there, E + 454 for numeric and fatal work and E + 540 for the
F6 case, are not silently included in M = 328, nor claimed safe in general.

### Selected communication domain

The native communication router `0x26a0e` serves five protocols, labelled AS, SAS,
BSG, MIS and SDT. It saves D2 (4 bytes). Entering the AS handler adds 16 bytes
(8 of arguments, 4 of return, 4 saved); the other protocols add 12. Arguments are
popped between sequential calls, so their costs combine by maximum, not by sum.

Each protocol has an offset table, an ascending key list and a count. AS keys are
bytes; SAS, BSG, MIS and SDT keys are words. For key position i (ascending) among n
entries, `target=base+signed16(base+2*(n-1-i))`, where base is the offset table.

| Protocol | Offset table (base) | Key list | Count n | Key width | Default target |
| --- | --- | --- | ---: | --- | --- |
| AS | 26474 | 2649c | 20 | byte | 269a8 |
| SAS | 23d92 | 23dfe | 54 | word | 2468a |
| BSG | 1b906 | 1b940 | 29 | word | 1bf12 |
| MIS | 18c1a | 18c48 | 23 | word | 1905c |
| SDT | 26ff6 | 27020 | 21 | word | 270b4 |

The selected keys (hexadecimal) and their targets:

|key|AS|SAS|BSG|MIS|SDT|
|---|---|---|---|---|---|
|30|264b0|24008|1b97a|18cec|default|
|31|264c6|24306|1b9b0|default|default|
|34|265fc|24196|1bb54|18d4e|default|
|35|26604|243f6|1bb70|1905c (return)|270b4 (return)|
|36|26612|default|1bb70|1905c (return)|270b4 (return)|
|45|268c8|default|default|default|default|
|105|default|2426a|default|default|default|
|113|default|23f1a|default|default|default|
|201|default|23e6a|default|default|default|
|210|default|245dc|default|default|default|
|f6|26918|default|default|default|default|

What each selected case does:

- **AS:** key 30 → `0x25c98` uses `0x26b0e`/`0x26be4`, formatting and a counter;
  key 34 → `0x260be` uses numeric code, `0x26b8e` and `0x44c`. AS defaults still
  perform the `0x35cf4` fill first.
- **SAS:** key 30 → `0x24b94` → `0x578a0` (option 32); key 34 uses
  `0x400`/`0x44c`/`0x54822`/`0x548bc` and option 1d; key 31 →
  `0x27e80`/`0x2b8e2`/`0x2b898(2)` and option 21; key 105 → `0x3a1f0`; keys
  113/201/210 are scalar packet stores.
- **BSG:** key 30 → `0x26be4`/`0x38b96`; key 31 → fixed counters and formatting;
  key 34 is scalar; keys 35/36 → `0x37456`/`0x38b96`.
- **MIS:** key 30 stores scalar packet data; key 34 uses `0x549c8(43)`/`0x400`.
- The selected common tails use `0x17b14` or return. Key 34's `0x26aae` tail
  writes `0x43cd59` through `0x400`; it is included, not an omitted fallthrough.

The numeric descendants are fixed counter and descriptor resolution, BCD
formatting, bounded division in `0x68bcc`/`0x68c30`/`0x68b90`/`0x68c4c`, the
`0x35cf4→0x35ca0` copy, scalar SafeRAM writes and fixed queue services. `0x17b14`
calls only `0x3d82`/`0x3d88`/`0x68c8a`/`0x17a66`; `0x17a66` and `0x68c8a` are leaves,
and copy temporaries are popped. The getters of `27e80`, `0x195fe` and the `0x38dfe`
selector return DATA, not code. Loops have fixed frames and balanced calls. These
protocols enqueue bytes; they never synchronously dispatch the `0x744ca`/`0x74af2`
business receivers. The interrupt-specific bill and new reel/completion selections
and their numeric bounds are in `IRQ-AUDIT.md`, **Guard-domain closure and IRQ6
selected cases**. No bound on the router as a whole is inferred.

| Shared selected helper domain | Positive children and returning cleanup |
| --- | --- |
| 256c0/26252 / 26b0e | 26b0e, 35b48, 38dfe, 549c8/54912, 38b96, 44c, 68bcc/68c30, 17b14, 23b78; 26b0e is numeric/format, not a callback |
| 26b8e / 26be4 | three 38b96 calls, then pops 36 / DATA tables 74112/7418e/7420a/74286 and optional 400; no callable pointer |
| 27e80..280a0 | 35b48(5/1d), 38dfe, 549c8(63/94/45/4d), 68bcc, 195fe; formatter arguments popped at 27ffe/28042; final inline clear/MOVEM/RTS |
| 195fe / 3a1f0 | 195fe → 68b90/68c4c with width 4/10 and the positive 74410 decimal divisor table; 3a1f0 → 3a15c width 4, constant 100; private counters, no accumulating arguments |
| 38b96 / 37456 | only 68c4c/68b90 with decimal 10 or positive 10*D2; fixed private-count progression, all saved registers restored |
| 2b8e2 / 2b898(2) / 2eb2e | 35ca0 byte copy and the 2b886 index leaf, balanced UNLK/RTS; 2eb2e is a leaf, not serial task dispatch |
| 35b48 / 38dfe | immutable 18-byte option descriptors at 73bc4; 35aec byte/immediate or BCD-reader wrappers; the 38e36 fourteen-entry table returns DATA; an invalid option takes TRAP #1 |
| 54822 / 548bc | 3c00e / 3bbae → accepted validator, resolver, conversion and copy families; UNLK discards the final retained arguments; never invokes a game handler |

For the selected operands 43 and 98, descriptors `0x6a14c` = `00438034 0103 0043`
and `0x6a3f4` = `0043d49c 0004 0098` fix the type, width, id and direct-data base.
A checksum failure remains a TRAP #1 alternative; it is not assumed impossible. The
other selected counter descriptors reuse the valid-object numeric domain.
`0x35ca0`, `0x17a66` and `0x2b886` are leaves; the math and copy loops of `0x17b14`
pop their temporary 4- and 8-byte operands before merges. Every normal selected
exit restores the entry SP, including argument tails discarded by the frame
pointer and UNLK. No case is called closed while a delay fallthrough or a switch
arm is unexamined.

### Display callbacks

At `0xa64c`, the instruction at `0xa64e` zero-extends MODE, loads `78f86[MODE]`,
and `0xa660` calls it. The complete valid domain MODE 0..29 is below; the MODE is
not assumed to stay the same across a service call or wait.

| Modes | Target |
| --- | --- |
|0|4a64e|
|1, 3, 5, 7, 9, 11, 15, 25, 29|4c52c|
|2 / 4 / 6|4b578 / 4b56a / 4b586|
|8, 10, 14|4b78e|
|12, 13, 16, 17|4c3aa|
|18, 19, 22, 26|6828e (RTS)|
|20, 21|4bb9a|
|23 / 24 / 27 / 28|56176 / 4c67c / 4c1e0 / 48c6e|

All are closed display, numeric or output families; none is `0x589ba`, a changed
hook or a controller dispatch. The crucial boundary is `0x48c6e`: it returns at
`0x48c7e`/`0x48c94`/`0x48c9c`/`0x48ca4`, and its children `0x539a8`/`0x52d82`/`0x54b06`
call `0x4b586`. The evaluator routes `0x48cce→0x5323a` and `0x48cd6→0x54b0e` belong
to the separate routine `0x48ca6` that starts **after** the RTS at `0x48ca4`; they
are not a fallthrough of `0x48c6e`.

Two legitimate controller routes into settlement do exist elsewhere in the
firmware: `0x539e4` calls `0x589ba` from `0x53a10`, and `0x556c6` calls `0x61ad0`
from `0x556e4`, which reaches `0x589ba` through `0x65128`. The finite initial bound
B = 268 is not applied underneath an admitted guard. Instead, the composed
descendant graph above contains no producer of `0x487a6`/`0x489ae`/`0x48ada` or of
any controller argument; its only callback outside fixed services is the complete
`0xa64c` display domain. Queue posts schedule another root on that root's own
stack; they do not run a controller synchronously on these saved ancestors.

## Producer seal

This is the **historical baseline census on the original ROM and frozen b2**,
specific to the guarded address ranges. The current candidate passed its own fresh
producer and equivalence seal, recorded under **Candidate seal and offline gates**;
any future changed image must be resealed.

The guarded regions are `[0x589ba, 0x599ba)` and `[0x3d294, 0x3d794)`. Their
predecessors `0x589b8` and `0x3d292` are RTS instructions, and the bodies end at the
final UNLK/RTS `0x599b6`/`0x599b8` and the restore/RTS `0x3d790`/`0x3d792`.

- There are twelve direct calls of `0x589ba`, all to its entry: `0x250ae`,
  `0x3d594`, `0x3d6de`, `0x3d6fc`, `0x40ade`, `0x509e2`, `0x513ba`, `0x53a10`,
  `0x5807a`, `0x5829c`, `0x656c0` and `0x65746`.
- `0x3d294` is entered by JSRs at `0x405f6`, `0x43f90` and `0x5999e` and from event
  table entry `0x7837e` (event `0x0F`, MODE `0x0A`), all at its entry. Its local
  internal switches are not first-entry producers.
- Encoded branches, DBcc and PC-relative transfers, address constructions, callable
  absolute pointers (including aliases through the low 24 address bits), vectors,
  the 18 task-entry literals and both supplied personality ROMs introduce no other
  external entry into the interiors. Nineteen aligned alias matches were instruction
  fragments, not operands (for example `0x3c06a`: displacement 0005 followed by SUB
  9602; `0x55326`: SUBQ.B followed by ADD.W). This scan is only one part of the
  seal; the computed producers below are essential.

### RAM and argument producers

| Producer | Complete qualified source domain |
| --- | --- |
| Task8 ca98 | cfda registry init; d252:d25a caps the registry at 20 compact installed slots (not IDs); a null entry is checked before the call |
| UART/serial 13d04/1411e/1643a | 163d6..1640e installs four pairs: 74aca: 19732 → 43e8f0; 74ace: 19b16 → 43e8f4; 74ad2: 100 → 43e8e8; 74ad6: 744ca → 43e8ec; 74aea count 4 |
| 3ed8a/3ed94 | 3ed38 A3/A4 pairs only: 3ef6c: (3ee36, 3edda), 3f704: (3f548, 3f4de) |
| 48992/48ba8 | bounded 48972/48976 and 48b8c/48b8e indices; the controller tables below; forwarders 489ec..48a04/48b24..48b4a preserve argument pairs |

The complete union of Task8's immediate targets, at `cffe..d244`:

```text
cbec 3daaa 6816e 49c40 3e832 3df00 3e250 1d7c6
1dbd6 d784 d600 23bee d28e d518 3dcbe e0a0
dbc4 e8d6 f0da da42 3ef52 3f6ea 3f108 3f920
```

The complete ordered action arrays `69de8` (count 15) and `6aa3e` (count 20) are
listed in INITIAL-ROOTS.md, **Complete 15-callback inventory** and **Complete
callback-index graph and direct frontiers** (the index → target columns). This
regional producer proof uses those exact fixed target sets, not their bounds on
invocation depth. The immutable producers that bind each table and count are:

| Consumer | 69de8/15 + controller 69e30/13 producers | 6aa3e/20 + controller 6aaa0/20 producers |
| --- | --- | --- |
|487a6|53944|54c98|
|489ae|53a62|54ad0|
|48ada|53cb0|54afa|

A first entry into the forwarding helpers `[0x48a12, 0x48ada)` is positively
excluded: the preceding instructions `0x48a10`/`0x48a74` are RTS, the complete
encoded, address-construction and computed producer domains avoid the interval, and
so do the ten switches that can reach it. The helpers' own forwarding cannot
bootstrap a first call. The allocation proof in TASK-STACK-AUDIT.md lists those
switches. `69e24`/2 and `6aa8e`/3 are six-byte DATA records, not callback arrays.

### Other immutable dispatch producers

| Producer | Complete checked load domain | Frozen regional target |
| --- | --- | --- |
|a5e0|77cc6 + 120*((event−1)&255) + 4*mode_byte|7837e → 3d294 only|
|a660|78f86 + 4*mode_byte|none|
|ad3a / ad72|77cc6 / 77db6 + 4*mode_byte|none|
|1a0ce/1a13e|744cc + 6*index_byte|none|
|1c29e/1c2dc/21c04|74af2 indices 0..14|none|
|271c8|69902 + 48*row + 4*state_byte, row < 12|none|

The complete targets of `74af2` are `0x1aedc`, `0x1aede`, `0x1af00`, `0x1b0a6`,
`0x1b0bc`, `0x1b0ce` (four entries), `0x1b2a2`, `0x1b344`, `0x1b438`, `0x1b44c`,
`0x1b490` and `0x1b5a2`. The zero selector-return argument of `0x1acd4` yields
only indices 0..14, and `0x1a892(0)` uses this classification. The byte-domain
loads above deliberately overapproximate the valid MODE values and reach into the
linked extension ROM. Candidate `06bef891` passed the required fresh scan below;
this is not an exemption for future changed images.

### All 44 geometrically reaching signed-word switches

Each indexed JMP is `4efb0002` with a signed WORD index. These are all the
PC-indexed switches whose table geometry could reach the guarded regions. The
columns are the JMP and table addresses, the normalized decimal index range and the
decoded destination range. The external tables exclude both region interiors; only
the four switches marked internal, already inside the bodies, target local cases.
Successful lookups bound `0x3ca84` and `0x5d9c6`, and failures branch away; all
others have unsigned range checks before the index is doubled and loaded. No other
indexed switch can geometrically reach the regions.

```text
35b02/35b06: 0..6   →35b14..35b44
3704e/37052: 0..3   →3705a..370e2
370a0/370a4: 0..4   →370ae..370e2
37210/37214: 0..3   →3721c..372a4
37262/37266: 0..4   →37270..372a4
37c74/37c78: 0..4   →37c82..37cba
38e32/38e36: 0..13  →38e52..38ee6
3ca14/3ca18: 0..9   →3ca2c..3ca50
3ca84/3ca88: 0..25  →3caf0..3cf40
3cfb0/3cfb4: 0..9   →3cfc8..3d21a
3d3ea/3d3ee: 0..3   →3d3f6..3d440  [internal]
3d52e/3d532: 0..3   →3d53a..3d5e4  [internal]
3e046/3e04a: 0..11  →3e062..3e16a
3f562/3f566: 0..31  →3f5a6..3f6ce
40dfe/40e02: 0..3   →40e0a..40e18
42014/42018: 0..12  →42032..42044
4268a/4268e: 0..12  →426a8..426ba
43ebc/43ec0: 0..26  →43ef6..43fe0
53378/5337c: 0..17  →533a0..53546
536e8/536ec: 0..17  →53710..5387c
53d3e/53d42: 0..12  →53d5c..53d92
53df6/53dfa: 0..180 →53f64..547c6
55186/5518a: 0..19  →551b2..55282
570bc/570c0: 0..5   →570cc..571be
5733c/57340: 0..4   →5734a..5737a
5783a/5783e: 0..3   →57846..57852
58d7e/58d82: 0..3   →58d8a..58da8  [internal]
5916a/5916e: 0..3   →59176..591d0  [internal]
5a344/5a348: 0..3   →5a350..5a376
5a4dc/5a4e0: 0..9   →5a4f4..5a55e
5a61c/5a620: 0..5   →5a62c..5a748
5a6e6/5a6ea: 0..6   →5a6f8..5a73a
5a75a/5a75e: 0..4   →5a768..5a7b0
5a82e/5a832: 0..5   →5a83e..5a958
5a8f6/5a8fa: 0..6   →5a908..5a94a
5a96a/5a96e: 0..4   →5a978..5a9a6
5b948/5b94c: 0..3   →5b954..5b974
5bce0/5bce4: 0..5   →5bcf0..5bcf6
5bd66/5bd6a: 0..7   →5bd7a..5be2e
5c034/5c038: 0..7   →5c048..5c0cc
5c28c/5c290: 0..4   →5c29a..5c2f8
5c34e/5c352: 0..26  →5c388..5c80e
5d9c6/5d9ca: 0..9   →5d9f2..5e610
60fe6/60fea: 0..12  →61004..6108a
```

The remaining computed-transfer classes are closed as follows:

- The configured task creation, deletion and switch hooks at `0x27c4`/`0x27c8`/`0x27cc`
  and the deferred hook at `0x27c0` are zero, copied at `0x36fe..0x370a`.
- The standard kernel services through `0x2948` use fixed kernel branches; extended
  dispatch through `0x2ba4` is disabled under the no-external-registration premise.
- The boot A2 continuations at `0xc92`/`0xdec` come from explicit LEAs to `0x93c`,
  `0xb02`, `0xc58`, `0xc68`, `0xc7e`, `0xc88`, `0xcfe`, `0xd0c`, `0xd20` and `0xd30`.
- The arithmetic register JMPs at `0x6896a`/`0x689d4`/`0x68e66`/`0x68f18`/`0x68f26`/`0x68f8a`
  return to intact saved continuations.
- The historical extension's indirect calls have fixed function producers, and its
  signed-word switches lie outside native geometric reach.

Candidate `06bef891`'s intentional continuation to `0x589c2` is a **new** producer.
It passed its own fresh seal below, not a historical-scan exemption.

## Allocation identity and asynchronous windows

How the guard finds the allocation of a resumed native supervisor task:

```text
cfg     = LONG(0x100)         = 0x27a0
ws      = LONG(cfg)           = 0x400000
CURRENT = LONG(0x400024)
T       = LONG(CURRENT+0x40)  exclusive allocation top
L       = unsigned WORD(0x27b6)
base    = T - L
```

The initializer writes the exclusive top at `0x375a` before `0x375e` subtracts L;
`0x3746..0x3750` builds the 18 TCBs with stride `0x50`. Task creation
(`0x2cba..0x2d4a`) takes a free TCB and builds a 26-byte context from its retained
top; restore/RTE consumes that context. Deletion (`0x2e0c..0x2e6e`) removes waits
and timers and frees the TCB without changing its top. Re-creation overwrites the
saved SSP at TCB+`0x38` from the top at TCB+`0x40`; TCB+`0x38` is **not** an
allocation bound. A reset starts a new pool lifetime and cannot resume an old
activation. The configured size must be consumed at initialization, never changed
in a live kernel. The fixed sizes `0800` and `0c00` are below `0x8000`, so SUBA.W's
sign extension gives the same result here, and only here, not for arbitrary
unsigned sizes.

The scheduler saves registers at `0x2af2`, saves the old SSP to TCB+`0x38` at
`0x2b30` and the USP at `0x2b36`. It installs the new SSP at `0x2b54` **before**
publishing CURRENT at `0x2b70`, and interrupts can be admitted at `0x2b64`. By the
time a task's RTE runs, CURRENT is coherent. A preempted guard resumes with its own
snapshot and registers as its own context; a deleted guard never resumes. No guard
lock, retry or long interrupt mask is needed. The window in which the new SSP is
installed but CURRENT still names the old task is closed by the positive
synchronous interrupt exclusion in `IRQ-AUDIT.md`, not by pretending CURRENT is
always coherent.

The bootstrap **must** use the constant arena `[0x436000, 0x438000)`, with actual
reset SP `0x437ffe`. CURRENT is already nonnull after `0x3736`, so a nonnull
CURRENT does not establish task mode. The design recognizes the bootstrap arena
independently of CURRENT, with unsigned range and budget checks. Native membership
stays tied to CURRENT and its top: picking an adjacent contiguous slot from the SP
could hide an overrun and is not a sound fallback. No supported private-E0 or
interrupt call remains after the positive domain closure. The private E0 stack
`[0x433000, 0x434000)` is a real, distinct allocation; the guard neither
misidentifies it through CURRENT nor refuses it categorically on the basis of SR or
a private address. Interrupt level 7 in SR alone does not identify IRQ7 or justify
refusal.

## Bootstrap and first-check covered work

`INITIAL-ROOTS.md` owns the complete initial-root proof, including all 27 controller
arms, the full `0x6268e` domain and the exact checklist of normal, evaluator, loader
and check producers. Its native FIRST 268 and bootstrap FIRST 58 are complete
abstract bounds under the valid-object, valid-selector, intact-context and other
inherited premises; they do not assert that an exact history attains them. The
earlier native 256 one-descent row and the loose bootstrap 226 handoff are
superseded and are **not** alternative proof inputs.

The reset code at `0x8fa` loads `0x437ffe` (2 bytes below `0x438000`). Then
`0xbca→0x154e` enters with 6 bytes, `0x154e` saves 12, `0x1dc2→0x1380` enters with
22, and `0x13e6→0x6268e` gives Y = 26 after `0x13e2` removes 32 argument bytes. The
complete composition over all arms gives bootstrap first-check alternatives of 34,
50 and 58, shared normal 302 and general loader 532, all positively attributed to
`[0x436000, 0x438000)`. The local early-boot and clear costs stay in NATIVE-STACK-LEDGERS.md,
**Boot and clear C-live paths** (at most 648, including the 2-byte top adjustment).
None of these finite initial rows replaces the admitted E + 328 bound or qualifies
unrelated native temporary peaks.

The first scratch is covered independently, **before** any admission: native
268 + 10 + 1904 = 2182 ≤ 3072; bootstrap 58 + 10 + 1904 = 1972 ≤ 8192. Later
activations, refusal and unwind use **Reserve and induction**, not a finite number
of settlement rounds. No Task6-only lifecycle predicate or assumption of freshly
initialized native non-volatile memory is used to restrict the bootstrap arms.

## IRQ, private E0 and exception separation

`IRQ-AUDIT.md`, **Guard-domain closure and IRQ6 selected cases**, covers every UART,
coin, output and motion phase, the fixed indirect producers of the emitted b2 E0
code, the selected router cases and the component arithmetic. No returning
same-stack interrupt or E0 domain calls `0x589ba`, `0x3d294`, or a controller or
evaluator entrance. Queued notifications and E0 `game_submit` requests are
asynchronous: native tasks run them later on their own stacks. This is a positive
target-domain proof; it does not infer exclusion from the 1904/1024 allowances. The
IRQ6 keys `0x76`–`0x7A` cost 302 bytes and key `0x91` costs 348, both below
the F6 case's 434. `0x17b14` costs 50 bytes in interrupt context (40 on its multiply
alternative), while its general task ceiling remains 88. The total I stays 1904.

A divide-by-zero (DIV0) is distinct from TRAP #1. Its vector at `0x14` leads
`0xf54→0x108a`, which raises the interrupt mask to level 7; the optional diagnostic
TRAP #0 services 16 and 05 lie within sealed kernel domains. `0x115c→0x1184:0x118a`
loads ROM[0] = `0x437ffe`, abandoning the entire interrupted stack. The intermediate
domain of `0x11c8` is:

- `0x1260→0x3d26a`'s fixed loop over 321 descriptors;
- the `0x140e`/`0x14fc` protected checks;
- `0x15060→0x1472c` input and bit reversal;
- `0x1274→0x3a918/0x3a652/0x34b3e` display and checksum;
- `0x426`/`0x44c` and `0x1246`.

No covered hook executes there. `0x1196→0xdee:0xe02→0x8f6:0x8fa` reloads the SP
again, producing a fresh bootstrap root. This analysis does not invent nonzero
denominators, does not confuse this path with the permanent TRAP #1 halt, and does
not claim a restart is financially safe. The IRQ6-selected DIV0 old-stack
diagnostics, at most 140 bytes below the router, are included in 160/154; a
registered-IRQ SC16 cannot switch stacks and SC05 cannot schedule there.

The terminal IRQ7's old-stack domain
`0x275a→0x445e→0x365d4→0x41796→0xbf0` has no financial entry. `0xbf0` selects
`0x4385dc` and terminates; repeated IRQ7 edges remain excluded. Forced resets keep
the existing premise of supervisor mode, a fresh SP and no resumer of the old
stack; this is not an expanded soft-reset or silicon proof.

## Candidate gates

### Candidate seal and offline gates

Pinned hashes:

| Artifact | SHA-256 |
| --- | --- |
| Published `main.bin` (the unchanged reviewed candidate) | `06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197` |
| Guard-qualification manifest (historical mirrored packaging) | `f91b00c6657f3358444c48793d42fb29a3a92a76245fbbee93f46220d2cc0e6e` |
| Current Winbond-packaging manifest | `ef1728ca2cb342666e6b31ebee1be8fdc411322b36f7c509c67dfb296b5f7b01` |

The packaging-only change preserves `main.bin` and all four compiled artifacts
exactly (a same-toolchain record; across toolchain builds only `main.bin` and the
chip images are expected to reproduce, see `toolchain.md`, **Outputs and
reproducibility**). `PROGRAMMING.md` records its 53 passing builder tests, its
independent review, the two-build reproduction and the exact-artifact local
publication. The runtime proof is unchanged, and these pins confer no installation
approval.

**Final-image comparison, independently reproduced.** The 5,601 extension
instructions decoded from the prior (b2) build keep their operations and frame
operands in the candidate; the 272 operand changes are exact +104-byte address
relocations. `ext.stack` is identical. The existing native patches keep their
instruction shapes and symbolic targets; only the capacity, settlement JMP and
padding patches are new. All 44 bounded switches and the recorded native producer
arrays match. The new `0x589c2` operand, at `0x7cea0`, lies outside the set of
targets that `0xa5e0`'s byte-domain load can actually fetch; no layout workaround
was needed.
`tests/test_settlement_guard.py:test_final_image_bounded_dispatch_loads_do_not_bypass_guard`
checks the overlapping final-image producer domains on every fresh test build. This
preserves the local proof domains; producer identity does not bound how many nested
callback invocations occur before the first guard entry.

**Emitted guard.** It uses 10 scratch bytes, admits on an unsigned threshold with
equality passing, restores all registers and the full SR, replays the original
prologue, and refuses through TRAP #1 with its scratch restored. Native capacity is
3072; the separate reserve at `0x27a8` is unchanged. At guard qualification, all 20
production input SHA-256 hashes, including the M68000 libgcc, and the hashes and
sizes of the seven output artifacts matched the historical candidate manifest; those
pins were reconfirmed before the stable-document review. The 41 patch records (the
39 entries of `patches.json`, the extension bytes and the checksum compensation
word) give a zero boot checksum. That manifest used mirrored 512 KiB chip outputs;
the current Winbond bundle uses exact 256 KiB lanes. Its 20 inputs and its output
pins were checked separately: only `build.py` changed, and all runtime artifacts are
identical. `install_ready=false` remains in both versions.

The full affected offline command passed **529 tests and six subtests**:

```sh
PYTHONDONTWRITEBYTECODE=1 firmware/.venv/bin/python -B -m pytest -v firmware/tests tools/test_sas_firmware.py tools/test_sas_commission_firmware.py
```

This includes all 67 settlement-guard cases with ten deliberately broken controls,
real native allocation and recycling tests, modelled interrupt boundaries and native
fatal execution. A defect in the shared CPU executor (a cached return-stop) was
reproduced with a failing regression and then fixed, and a duplicate hook inventory
in the game fixture was removed. A later allocator-only `pairwise` cleanup passed its
targeted rerun. Ruff passed on the eight touched Python files. Independent review of
the source and tests, and of the finite aggregation rows, found no issues. These are
CPU and software checks, not qualification of physical timing, payout or installation.

### Mathematical proof approval and publication

The complete initial-root and ordinary-root composition was independently reviewed
and **approved**. INITIAL-ROOTS.md keeps every row of the producer and root
checklist, the AS/BSG immutable selectors and weighted edges, all 27 controller
arms, the full `0x5c8d8` suffix, the corrected `0xa5e0` **and** secondary
`0xad72→0xcf54→0x62958` routes, the Task7 primary and shared-motor domains, and
every selected numeric, accounting and router port. The 115 Task7 windows with six
switches and the 77 ordinary windows with four switches were independently
reproduced; the additional 36th geometric switch, at `0x14f74`, has six checked
targets and is identical in the original and the candidate. No unmatched
covered-entry port remains under the stated premises.

The initial native normal 416, evaluator 306, completed-runtime loader 498 and
FIRST 268, and the bootstrap FIRST 58, shared normal 302, general loader 532 and
early boot 648, are complete composed bounds, not a shortcut through the largest
known row. The admitted M = 328, G = 120, H = 2232, S = 10 and I = 1904, and the
allocation ownership, are unchanged. The earlier syntactic callback cycle through
`0x5544a` is **not** a reachable history or an overflow witness: the actual
immutable selectors make both the AS and BSG graphs acyclic. No further resource cut
or financial-history investigation remains requested.

Publication record:

1. All seven maintained documents were read in full and independently reviewed and
   approved. The review's clarification was applied: 72/458 covers only
   Task2 and direct Task6-clear roots, not shared X72 ancestry. All three
   reproduction scripts embedded in INITIAL-ROOTS.md passed.
2. All 20 production inputs, the candidate and manifest pins and the original ROMs
   were rechecked; then the exact eight reviewed artifacts were copied into `build/`
   without rebuilding. All copied bytes, the boot checksum, the mirrored chip lanes
   and the unchanged separate reserve matched.
3. This closes only the scoped offline software and publication gate.
   `install_ready=false` remains; no programmer, serial, chip or machine operation is
   authorized. Winbond image packaging was completed afterwards, as recorded in
   PROGRAMMING.md; physical timer, bus, silicon, mechanical and installation
   qualification remain separate.

Any changed source or image needs the affected tests and fresh emitted, producer and
frame evidence; it does not inherit this image's approval.

This record promises only bounded guard overhead and bounded extension-live
(covered) work, including the first check, refusal and covered unwind, under the
inherited premises. It is not safety of every temporary legacy peak, financial
termination, payout recovery, coil-off or hardware qualification.
