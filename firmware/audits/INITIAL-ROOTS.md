# Initial non-E0 and bootstrap roots

This record proves how much native stack can already be in use when the firmware
extension's hooks are first reached from the original program. The extension
(`firmware/README.md`) patches the original S6M00002B5 main program so that some
native code paths call added code, and that added code runs on whatever stack its
native caller is using. In the published image each native task has a 3072-byte stack
and the reset-time (bootstrap) code runs in an 8192-byte region. To show that the added
frames fit, every hook needs a worst-case figure for the stack its native callers
occupy below it. This record finds every native entry point (root) that can reach a
hook, follows every call chain from that root to the hook, and composes the stack costs
into finite ceilings for each allocation.

**Status.** The proof is a software argument about ROM bytes and the published image.
It has been independently reviewed and approved under the premises stated below.
**Hardware and installation remain unqualified.** It covers the hooks reached on native
task stacks and on the bootstrap stack. The extension's private E0 command handler runs
on its own stack and is bounded in `IRQ-AUDIT.md` (hence "non-E0").

**Related records.** `NATIVE-STACK-LEDGERS.md` supplies the local stack cost of each
hook and native routine used here. `SETTLEMENT-GUARD.md` owns the runtime guard at the
native settlement routine 589ba and the induction that bounds everything below an
admitted settlement entry. `IRQ-AUDIT.md` owns the 1904-byte interrupt allowance and
the private E0 stack. `TASK-STACK-AUDIT.md` summarises the result and owns the
3072-byte capacity and the qualification status. **Proof ownership** below states the
exact division.

Addresses are CPU addresses in the map of main program S6M00002B5 with personality
E595211X-05, written in hexadecimal without a `0x` prefix; **Scope, premises and
notation** gives the remaining number conventions.

## Terms used in this record

| Term | Meaning here |
|---|---|
| Native task, Task2 … Task7, Task15 | The original program's tasks under its VRTX kernel, numbered as in the other audit records. Each native task gets one stack slot from the kernel allocator. Task6 (entry 9f2a) dispatches game events through an event/MODE matrix; Task7 (entry b080) is the configuration and diagnostic task. |
| Bootstrap | Reset and boot code that runs on the reset stack in [436000,438000) rather than in a task slot. |
| E0, private E0 | The extension's SAS command E0 handler (`platform_sas_handler`), which switches to its own private 4096-byte stack. `IRQ-AUDIT.md` bounds it; this record covers every other entry. |
| Root, initial root | A place where native control first enters a routine this proof must bound, together with the callers that are on the stack at that moment. "Initial" means the first such entry on a stack, before any admitted settlement check is live (see FIRST below). |
| Incoming prefix | The stack bytes the callers below a root already occupy at its entry, including the return address of the entering call. |
| Covered hook, covered entry | A patch site where native code enters extension code; the sites are listed in **Covered patch-family inventory**. A covered entry is an arrival at one of them, during which extension frames are live on the native stack. |
| Covered-entry port | A call site or table entry through which a native path can synchronously reach a covered entry. "No unmatched covered-entry port" means every such port has a composed bound. |
| Covered ceiling | The largest stack residency a covered hook family can reach, as an incoming prefix plus the family's local envelope. |
| Ordinary root | Roots in Task2, Task3, Task5, Task6 and the standard (non-E0) SAS command handler, as opposed to the Task7 dispatcher roots and the bootstrap. The reproduction script names its window lists the same way. |
| Normal, evaluator and loader ceilings | Covered ceilings of the three shared native entry points with hooks: normal game start 61172 (ceiling P+192), the wager evaluator 65128 (Q+86) and the shared loader 3756c (L+302 after a completed boot, L+402 for general recovery, called the runtime and general loader ceilings). P, Q and L are defined under **Scope, premises and notation**. |
| Early boot | The reset-time hook work tabulated in **Bootstrap and earlier qualifications** (boot validation, the boot-time restore, the final restore and the two clear hooks), measured from the reset stack top 438000. Its maximum is 648 bytes. |
| Settlement check, admitted | The extension hook `game_settlement_entry` intercepts every entry to native 589ba and admits it only if enough stack remains (`SETTLEMENT-GUARD.md`). Work below an admitted entry is bounded by that record's induction, not by this one. |
| FIRST | The first settlement check on a stack: a 589ba entry with no already-live admitted 589ba ancestor. This record bounds the stack at FIRST; the guard handles everything after it. |
| Selector | A mutable RAM value (for example word 43cd72 or 43cd6e) used as an index into an immutable ROM table. The proof never assumes a selector keeps its value between reads. |
| AS and BSG selector graphs | Two instances of the native table-driven callback dispatcher at 487a6. AS binds the 15-entry action array 69de8 with 13 controller records at 69e30; BSG binds the 20-entry action array 6aa3e with 20 controller records at 6aaa0. |
| Selector DAG | The graph whose vertices are a dispatcher's callback indices and whose edges are the further dispatches each callback can cause through the immutable records. If it is a directed acyclic graph (DAG), it has no directed cycle: every strongly connected component (SCC) is a single vertex without a self-loop, so its longest weighted path is a finite stack bound. |
| Controller arm | One case of the shared controller 5c334, which dispatches its argument (2 … 28 decimal) through a 27-entry jump table, plus its default case. |
| Producer | Anything that can create a control transfer to a code address: an encoded call, jump or branch, a PC-relative transfer, a jump-table entry, or a function pointer held in a table or registry. |
| Producer seal | A proof that the complete set of producers into an address range is known and contains only the listed ones. `SETTLEMENT-GUARD.md`, **Producer seal**, seals the settlement region; **62958 producer seal and retained ancestry** below seals the cf54/62958 route. |
| Positive contract, positive domain | A complete statement of what a routine can synchronously call (its outgoing call union), established from its instruction bytes. It is the opposite of inferring safety from a function name, a missing Ghidra cross-reference (xref) or a Ghidra annotation. |
| Frontier | The callees at which a local analysis stops because an accepted positive contract already covers them. |
| Synchronous, asynchronous | A synchronous call runs on the caller's stack. Posting to a queue or creating or resuming a task is asynchronous: the receiver later runs on its own context and becomes its own root. |
| DATA | Table content that is copied, compared or published but never executed as code. |
| Delegated-domain labels | Single letters such as K, N, D, C, R, M, F, U and B, and the router labels R113 and RF6, name accepted positive domains. They are defined in **Positive local-domain boundaries** and **Task7-specific delegated matches**. |
| b2 | The earlier frozen build in which the native costs were first established. It is provenance only and is not the published image. |

## Status and authority

**The mathematical proof for covered roots and ordinary roots has been independently
reviewed and approved. A separate independent review then approved the seven maintained
firmware proof documents as a set. Final pinning and local publication of the image are
complete. Hardware and installation remain unqualified.** Under the premises below, the
complete finite composition leaves no unmatched covered-entry port. The resulting native
ceilings are normal 416, evaluator 306, completed-runtime loader 498 and first
settlement check (FIRST 589ba) 268 bytes. The bootstrap ceilings are FIRST 58, shared
normal 302, general loader 532 and early boot 648. No new callback guard is needed,
because both immutable selector graphs are DAGs.

The published image, `build/main.bin` in `firmware/` (a build output of
`firmware/build.py`; the directory is not committed), has SHA-256
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`. Its current
Winbond-packaging manifest is
`ef1728ca2cb342666e6b31ebee1be8fdc411322b36f7c509c67dfb296b5f7b01`, with
`install_ready=false`. `PROGRAMMING.md` records the packaging-only revision: the merged
main image and all four compiled artifacts stayed byte-identical, so this root proof is
unchanged. That identity is a same-toolchain record; across toolchain builds only
`main.bin` and the chip images are expected to reproduce (`toolchain.md`, **Outputs and
reproducibility**). All eight reviewed artifacts were published by copying them without
a rebuild, and the copy was checked for exact byte identity, all 20 production input
hashes, the original ROMs, the boot checksum and the chip lanes. The historical build b2
is no longer the published build. `SETTLEMENT-GUARD.md`, **Candidate seal and offline
gates**, owns the passed emitted-code, producer, frame and source gates: 529 tests plus
six subtests, and one passing rerun of the allocator cleanup test. Those are software
checks, not publication or hardware approval.

The original `roms/s6000_main_S6M00002B5.bin` has SHA-256
`49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a`. The evidence is
the existing read-only Ghidra analysis of that program (`ghidra/proj`, program `bally`),
raw M68000 `objdump` disassembly of the binary, decoding of immutable big-endian tables,
and complete local control-flow-graph (CFG) and stack-pointer (SP) walks. Missing
Ghidra function definitions, and Ghidra's false non-returning annotation on the native
delay routine 3a1e, are not treated as reachability evidence. Returning delays, all
conditional alternatives and all retained arguments are included. The local walks were
reproduced: 115 Task7 windows with six switches, 77 ordinary windows with four switches,
and the additional six-target table at 14f74 with its original-to-candidate identity.
Separate scoped reviews of the AS/BSG, frontier and Task7 analyses preceded the final
review of the whole mathematical proof. Nothing outside this repository is needed to
check the proof below.

### Proof ownership

This document (`INITIAL-ROOTS.md`) is authoritative for **initial-root producer domains,
immutable callback selectors and their weighted edges, all 27 controller arms, unguarded
incoming prefixes and positive synchronous frontier contracts**. The other records own the rest:

- `NATIVE-STACK-LEDGERS.md` owns local numeric, diagnostic and wagering, serial, loader,
  early-boot and fatal, and admitted-descendant costs.
- `SETTLEMENT-GUARD.md` owns the admitted 589ba cut graph, the regional producer seal,
  allocation identity and the induction over H2232, M328, G120 and S10 (the guard
  reserve, covered ceiling, next-check growth and guard scratch).
- `IRQ-AUDIT.md` owns the interrupt allowance I1904 and the positive exclusion of IRQ
  and private-E0 paths.
- `TASK-STACK-AUDIT.md` owns qualification status and native allocation capacity.

A summary elsewhere is not a second authoritative root ledger.

### Scope, premises and notation

The promise covers work done while extension code is live, that is, added code. It does
not cover every untouched legacy temporary stack peak, financial termination, rollback,
coil-off (de-energizing the reel-drive latch), recovery from arbitrary corruption, or
physical qualification. A summary saying that native work returns balanced and reaches
no covered entry does not prove that its native peak is small, and intact contexts
remain a premise. Queuing or resuming another context is not a synchronous call into its
receiver. Infinite waits are not excluded by any liveness assumption.

The premises are:

- valid initialized kernel and native objects, registries, pointers and counter copies,
  and a valid value in each indexed selector at every read;
- immutable table and forwarder identity;
- intact call, return and exception contexts, and supervisor execution;
- optional kernel callbacks set to zero, unregistered services disabled, and no
  external invoker;
- the enforced calibration interval and the timer, bus and silicon premises of
  `IRQ-AUDIT.md`;
- one filtered terminal IRQ7;
- a fresh SP on reset, with no old context resuming.

SafeRAM (the battery-backed native accounting RAM) that carries a valid marker need not
have been written by this exact ROM. Mutable amount, MODE, protocol and selector reads are each
overapproximated independently, including after waits. The proof assumes no fresh data,
no financial progress, no history of the same mode and no value that stays stable
across a call.

Number conventions: addresses, opcodes and field offsets are hexadecimal. Byte counts,
cardinalities, callback IDs and the native matrix event and MODE labels are decimal.
BSG event operands and the arm values of 5c334 and 6268e are explicitly marked
hexadecimal; AS exceptions to the decimal rule are marked where they occur.

Stack notation:

- R(f) is the whole-task residency at entry to routine f, including the incoming
  return address. P = R(61172), Q = R(65128), L = R(3756c), X = R(5c334) and
  Y = R(6268e).
- D is the local pre-call residency at a call site. It includes fixed saves and all
  retained arguments, and each child edge adds D+4 (the return address).
- F is a routine's fixed frame (LINK space and saved registers), as in "9f2a F32".
- B is the residency at a 589ba entry that is the FIRST check ("first B"), and E is
  the residency at a 589ba entry in `SETTLEMENT-GUARD.md`'s sense.
- A task's initial 26-byte context is consumed by the restore and RTE that start it.
- FIRST means that no admitted 589ba ancestor is already live.
- The native capacity of 3072 bytes per task and the bootstrap region [436000,438000)
  of 8192 bytes are distinct. The reset SP 437ffe contributes 2 bytes against the
  bootstrap region.

## Local-cost input locators

These headings preserve the local costs this record composes:

- `NATIVE-STACK-LEDGERS.md`: **Task7 Test10**, **Task7 serial reconfiguration**,
  **Standard SAS identity hooks (not private E0)**, **Boot and clear C-live paths**,
  **Runtime loader tail — local formulas**, **Fatal paths**, **BET ONE accounting and
  numeric ledgers**, **Positive continuation child domains** and **Covered continuation
  prefixes and unwind**.
- `SETTLEMENT-GUARD.md`: **Producer seal**, **Selected communication domain** and
  **Display callbacks** supply the complete global computed-producer classes and the
  selected child classes.
- `IRQ-AUDIT.md`: **Conservative native interrupt components** and **Guard-domain
  closure and IRQ6 selected cases** supply I1904 and the positively excluded entry
  domains.

These are qualified inputs, not conclusions drawn from a name or from a lack of xrefs.

## Covered patch-family inventory

Each row names a covered hook site (native address and extension symbol), the root or
allocation it runs under, and its non-IRQ stack result in bytes. "Total" adds the
1904-byte IRQ allowance.

| Covered family / site | Allocation / root | Non-IRQ result |
|---|---|---|
| a53e `game_next_event` | Task6 9f2a | 244, including the counter 43 and spin-refused branches; total 2148. |
| 42fac `game_bet_one_entry` | Task6 matrix 42f08 | R(42f08)+286 including fatal paths. The uniform handler entry 56 gives 342, total 2246; the direct entry 36 gives 322. The returning bound is R+248. |
| 4324e `game_repeat_wager` | Task6 matrix 4320a | The uniform 56 gives 192, total 2096; the DATA fallback adds no calls. The untouched 4341c after the wrapper returns is not an extra C-live path. |
| 61172 `game_start_entry` | Normal-start roots | The reviewed local envelope P+192 for the complete changed-hook family, **not** the whole native 61172. The complete initial-root maximum P224 gives 416; under a live admitted settlement, E+328 applies instead. |
| 615ce `game_candidate_entry` | Same 61172 roots | P+70, subsumed by P+192. |
| 615fa `game_commit_entry` | Same 61172 roots | P+62, subsumed by P+192. |
| 3fbcc `game_diag_line_order_entry` | 61172:6171e or Task7 Test10:5aee2 | Normal P+80; Test10 direct 92. |
| 3fc34 `game_diag_wager_entry` | Same diagnostic roots | Normal P+130; Test10 direct 142, total 2046. |
| 65168 `game_evaluate_wager` | 5323a / 54b5c / 61bd2 → 65128 | Q+86, including 35fdc while C code is live. The complete initial-root maximum Q220 gives 306. |
| 65184 `game_line_order_entry` | Same evaluator roots | Q+40, subsumed by Q+86. |
| 5ac3c `motion_test10_poll` | Task7 Test10 | 98 with IRQs open, total 2002; deeper masked sections keep their qualified IRQ7 treatment. |
| 5af80 `motion_test10_cleanup` | Same | 96 with IRQs open, total 2000. |
| 5af86 `motion_test10_return` | Same | 70 with IRQs open, total 1974. |
| c1a8 `platform_reconfigure` | Task7 c0fc | 158, total 2062; includes the priority lookup before the later mask raise. |
| 2872a `ext_report_1f` | Native SAS 28480, not private E0 | 170, total 2074. |
| 2cc26 `ext_report_53` | Native SAS 2c95c, not private E0 | 174, total 2078. |
| 37dde `ext_reload` | Shared native loaders | Completed-boot, no-ownership formula L+302; general recovery L+402 where that lifecycle reduction is unavailable. Native SAS command 8C, at 498, is the complete initial runtime maximum; the corrected shared Task6 family, at 470, is smaller; boot uses the general formula (below). |
| 155c `ext_boot`; 1906 `ext_restore_before_queues` | Bootstrap | 648 / 352 from 438000, including reset's 2-byte top adjustment. |
| 38386 `ext_clear_begin`; 38526 `ext_clear_complete` | Reset → 3825a → 3836e only | 508 / 380 from 438000. Positive reset-only provenance, not task roots. |
| 589ba `game_settlement_entry` | Native or bootstrap | H2232, scratch 10; admitted E ≤ 840 (native) / 5960 (bootstrap); covered E+328. Independent of the initial finite root values. |

Wrappers restore their scratch before tail continuations, so no permanent extra
extension frame survives into native work. The initial normal and evaluator maxima do
not apply to descendants under a live admitted settlement; those use the guarded
formula.

## Complete ordinary-root compositions

### Task2/3/5/6 and standard SAS, not Task7

Each row composes a root's complete retained prefix (or an accepted input) up to the
first covered hook or first guard. Arrows separate successive entry residencies.

| Root / family | Complete retained-prefix composition or accepted input | Covered ceiling / first guard |
|---|---|---|
| Task6 matrix and secondary dispatch | 9f2a F32; direct handler 36; a634→aa6a entry 40 with F12; callbacks with no additional arguments give the uniform handler entry 56 | Event 244, BET 342, repeat 192; direct 61172 with P ≤ 56 → 248; direct 61ad0 with Q68/P72 → 154/264. |
| Task6 a634→aa6a→acb6:8f02 or acfa:8ecc | 40 → 56 → 61c50 = 60 → P64; the pre-call 12 at acb6/acfa is exactly aa6a's saves | Normal 256. |
| Task6 a61c→af74→b074:8ecc | 36 → 44 → 61c50 = 48 → P52 | Normal 244. |
| Task3 633c→72ee:8c1e→8c3e→8ecc/8f02 | F88; the whole local walk proves zero arguments at 72ee. Entries 92 → 96 → 61c50 = 100 → P104. 8c1e→8c3e is a tail jump, not another call. | Normal 296. Task3's 753c→42ee ends at 435e; it does not fall through into 43c2. |
| Task6 event 31 → 63c84→64056→63cd0 | Uniform 56 → 60 → 100; loader L160; P140. The direct route 36 → 40 → 80 gives P120. | Normal 332; runtime loader 462. The alternative direct 63c84→63cd0 is smaller. |
| SAS command 8C → 2f8d6→63c84/64056 | SAS entry 60; 2f8d6 F28; controller 92/96; nested 63cd0 = 136, L196, P176 | Normal 368; loader 498, total 2402. This is the native SAS parent, never E0's private arena. |
| Task2 fault ancestry 46f4→4754→4d90→4399a | 4 → 40 → 52 → 43e62 = 60; the alternate 5c34 routes reach 4399a at 32/44; the final accepted fault-family 43e62 ≤ 72 | 64a7a ≤ 76, 63cd0 ≤ 96, P ≤ 136 → 328; loader 458. The alternate 3d294 entry ≤ 76 gives P88 / normal 280 and first B 84, all smaller. |
| Task6 clear wrappers 434e0 / 43548 / 435b0 → 4399a | 4399a ≤ 64 → 43e62 ≤ 72 | Same as the preceding fault family. 435da ends at 4389c; 4394a belongs to the separate routine 4389e. |
| Task6 a408→4604:4692 or a40e→43e8:4452→43c2 | Both intermediate entries are 36; F8 + argument 4 + return 4 gives 43c2 = 52. 43c2 (LINK 0) retains its 4-byte error argument through 43de, so the child 5c8d8 = 64. | 5c906 adds argument 4 + return 4: X = 72. This exceeds the old blanket handler figure 56 for 5c8d8; use the complete all-arm formulas below: normal 340 / runtime loader 470. |
| Task6 direct matrix 5c8d8 (event 13, every MODE except 21) | Uniform R(5c8d8) ≤ 56 → X ≤ 64 | Smaller than X72. The complete prelude and suffix have no additional covered port. |
| Task6 matrix 6268e (event 13, MODE 21) | Uniform Y ≤ 56 → X ≤ 64 | Complete covered edges are smaller than Y64/X72. |
| Task6 event 3, MODE 20 or 21 → cf54→62958→6268e | The direct a5e0 and the secondary ad72 both select 77e06/77e0a → cf54. The secondary aa6a entry 40 + saved 12 + return 4 gives 56; cf72 adds 4 → 60; 62992 removes the optional 12 before the 62996 BSR adds 4 → Y64 → X72. | The positive producer and domain seal is closed. The 17-entry local switch at aaaa has no cf54 target, but that does **not** exclude the distinct ad72 producer. cf54's alternate c8c6 branch has no synchronous covered exit. |
| Task6 matrix 64f56→65072:61172 | R(64f56) ≤ 56; F4 and no retained arguments → P64 | Normal 256. |
| Initial 404f0 / 43e62 / direct-matrix 3d294 family (Task2/6) | Accepted incoming 3d294 ≤ 100; its fixed 4 + return 4 → 61c50, then 4 → P ≤ 112 | Normal 304 before any guard; first B ≤ 108. The new X72 → 43e62 route gives 3d294 ≤ 88 / B96, smaller. |
| Task6 matrix 40600 | Uniform 56; pre-call 20 at 4061a/40af8/40ade → 61c50 or 589ba = 80; P84 | Normal 276; first B 80. |
| Task6 matrix 57e70 and the shared 5c334 arm 14 (hex) | Direct R(57e70) ≤ 56; the shared X72 adds 12, giving R(57e70) ≤ 84; no retained arguments at the normal or guard calls | Normal P ≤ 92 → 284; first B ≤ 88. This corrects the earlier shared B80 / normal 276 row. |
| Task5 8f60→506be/50f68 | Accepted first B ≤ 56 | First check with scratch and IRQ allowance 1970; then the guarded formula. |
| Task6 queue 0F, MODE 0 → 2509c→250ae | Accepted first B ≤ 60, direct 40 | First check 1974; then the guarded formula. The Task15 publication is asynchronous, not another synchronous ancestor. |

The 43c2 arithmetic was reread directly from the instructions. 43cc, 43d2 and 43d8
return before 43de, but the original 4-byte error argument stays on the stack until the
UNLK at 43e4. The intermediate loops pop their delay arguments before 4452 and 4692.
These ancestors do not borrow a smaller matrix-handler prefix.

The 62958 path was also reread at ad68, ad6e and ad72, at cf72, and at 62992/62996.
Direct ROM decoding gives 77db6+4×20 = 77e06 and 77db6+4×21 = 77e0a, and both entries
hold cf54. The completed producer seal covers direct, PC-relative, computed and
interior candidates, plus a census of every aligned value in the final candidate image
whose low 24 bits fall in the range. The alternative cf54→c8c6 only publishes or creates
other contexts, installs registry DATA and calls the matched numeric, display and queue
domains. It neither enters 6268e synchronously nor creates another ordinary covered
root.

### Complete AS/BSG selector DAG inputs and direct controller branches

| Controller family | Bound and positive domain | Covered result |
|---|---|---|
| 48ca6→5323a→65128 | Uniform 48ca6 = 56; wrapper 4 + evaluator return 4 → Q64 | Evaluator 150; eventual first B ≤ 112 from Q+48. |
| 48ca6→54b0e→54db0→61ad0 | 56 → 60; 54be2 retains 8 plus return 4 → 72; 55152 pre-call 56 plus return 4 → 132; Q144/P148 | Evaluator 230 / normal 340; first B 192. Direct evaluation through 54b5c is smaller. 54b0e's 54c70 events 1/3/6 select BSG callback 2, whose finite DAG has no covered exit. |
| 48cde→53336→535be:61ad0 | 56 → 60; F20 + return 4 → 84; Q96/P100 | Evaluator 182 / normal 292; first B 144. |
| 48cde AS 53c92→48ada | R(48cde) = 56; the complete immutable 13-record, 15-action DAG gives FIRST+184; no P, Q or loader exit before the guard | First B 240; scratch + IRQ 2154. Replaces 216. 53a44→489ae can select only callback 7, which has no covered exit. |
| 48cde BSG 54adc→48ada | 56 → 60 → 88. The complete 20-record, 20-action DAG gives relative Q132/P136/B180 from the 48ada entry | Q220/P224/first B 268; evaluator 306 / normal 416; scratch + IRQ 2182. Replaces 208/212/256 and 294/404. |
| BSG nested direct action 19 → 55170 → event 12 (hex) → action 14 | From callback 19: Q+104/P+108/B+152; the direct call from 48ada adds 28 | This is the controlling compatible immutable-selector route, not a generic-function SCC or an assumed chain with a single nested descent. |
| BSG 489ae and external 54c70 producers | Record field +0c selects IDs 0/7/10 only; 54b0e events 1/3/6 select 2; 54c42 event 13 (hex) selects 15 | No covered exit under the accepted finite graph and frontier summaries; no deeper ordinary P/Q root is hidden here. |

Both the AS and BSG graphs are acyclic even with independently mutable valid selectors,
so they need no new guard. 55890's complete leaf contract and 40438's fixed-queue,
no-inline-receiver contract close their named non-numeric frontiers; both are accepted
independently (see **55890** and **40438**). Every synchronous callback edge keeps its
actual ancestors on the stack; an asynchronous event becomes its receiver's own root.

## All 27 arms of 5c334, plus default — exact composition

5c334 saves D2/D3, 8 bytes. It reads its argument at 12(SP), subtracts 2, compares the
normalized index unsigned against 26 at 5c342/5c344, and dispatches through the signed
WORD table 5c352 via 5c34e. The 27 entries therefore cover arguments 2 … 28 decimal; every
other long value goes to 5c80e. No state or history reduction is applied. The input
column below is **hexadecimal**.

The code letters in the last column are defined in the list after the table. Repeated D
arms are listed one by one so that all 27 producer rows stay visible.

| Input | Immutable target | Branch family / complete covered or native-only domain |
|---|---|---|
| 02 | 5c5a4 | F: mode publication, optional 43e62 at X+12 |
| 03 | 5c80e | D |
| 04 | 5c80e | D |
| 05 | 5c80e | D |
| 06 | 5c3fa | A: stores, counter, mode, display and fixed queues only |
| 07 | 5c80e | D |
| 08 | 5c524 | F plus motor 3da1a at X+12; error 62422(07) at X+16 |
| 09 | 5c80e | D |
| 0a | 5c524 | same branch as 08 |
| 0b | 5c80e | D |
| 0c | 5c61c | N: options, protected stores, accounting, display and fixed queues |
| 0d | 5c80e | D |
| 0e | 5c524 | same branch as 08 |
| 0f | 5c80e | D |
| 10 | 5c61c | same N branch |
| 11 | 5c80e | D |
| 12 | 5c3e2 | 34d22 only, child X+16 |
| 13 | 5c80e | D |
| 14 | 5c6c2 | C: 6262c at X+12; optional 582ec or 57e70 at X+12 |
| 15 | 5c80e | D |
| 16 | 5c388 | C0: scalar stores, mode, 4e412 and 6262c; no direct covered entry |
| 17 | 5c80e | D |
| 18 | 5c5f2 | mode / error 62422(0d) at X+16, fixed returns |
| 19 | 5c80e | D |
| 1a | 5c748 | L: 64566, 5cf7e and 5efc0 at X+12; 64a7a at X+12 |
| 1b | 5c80e | D |
| 1c | 5c50e | mode plus fixed queue (1, 26 hex, 0), return |
| outside 02 … 1c | 5c80e | D: 34d22 and optional scalar stores / fixed queue (2, 18 hex, 0) |

The complete local walk has 313 states and at most 20 local bytes, and every returning
path restores a balanced SP (termination is not established). This is **not** a whole-native peak claim. The child depths
used above are: a call with no arguments retains the 8 saved bytes, giving X+12; one
argument gives X+16; two arguments give X+20. At 5c468:a64c an earlier mode argument is
still on the stack, so that child is at X+16, not X+12. It is a display child with no
covered entry under the accepted 30-entry domain; its native-only peak is not charged
here.

Composition by branch family:

- **F (5c598/5c5ba → 43e62):** the accepted shared fault-controller contract, child
  X+12. Its 64a7a is at X+16 and the nested 63cd0 at X+36; normal P = X+76 and loader
  L = X+96. The covered ceilings are therefore normal X+268, completed-runtime loader
  X+398 and general loader X+498. Its 3d294 entrance is at X+16; the pre-check normal
  P = X+28 and first B = X+24. Mode reads inside 43e62 are independent and are **not**
  constrained to the outer 5c334 argument.
- **L (5c802 → 64a7a):** child X+12; the nested 63cd0 = X+32 and normal P = X+72;
  loader L = X+92, so normal X+264 and runtime/general loader X+394/494. The direct
  64d4a route has P = X+44, normal X+236; at X72 it is P116/308, not the larger nested
  P144/336. The preceding 64566 port has an empty synchronous covered-entry set
  (**Nonmotor frontiers — complete synchronous topology**), and 5cf7e/5efc0 have the
  positive no-covered-entry summaries of the reviewed Task7 analysis (**Shared motor
  contracts for ordinary/bootstrap composition**). None of them is inferred to be a leaf.
- **C (5c73c → 57e70):** child X+12; normal P = X+20, covered X+212; first B = X+16.
  6262c uses the accepted output, mailbox, options and queue domain. 582ec is positively
  closed: it calls only 46b4(08) and 4360, with no synchronous covered entry.
- **A, N, C0 and D:** the accepted scalar, numeric, mode, display and service
  contracts, including the complete a64c and 6262c domains, establish that these matched
  callees reach no covered entry. 3da1a, 5cf7e and 5efc0 use the positive contracts of the
  reviewed Task7 analysis below; 64566 and 582ec use the complete positive domains of the
  ordinary frontier analysis below. 62422(07/0d) matches its whole accepted error domain
  (router key 30, progressive operation 4 and the normal tails); no analogy between
  opcodes and no restricted lifecycle predicate is needed.

For the complete ordinary X72 route, F gives P148 / normal 340, L168 / runtime loader 470
and first B 96, and C gives normal 284 / B88. For the boot route X34, F gives P110 /
normal 302, L130 / **general loader 532** and first B 58, and C gives normal 246 / B50.
Every other callee of the 27 arms has its positive contract with no additional covered
port matched. These are whole-family bounds, not merely the largest branches observed.
The old blanket assumption of a 56-byte handler entry cannot replace X72.

## Complete 6268e local branch and frontier union

6268e has no frame. Its complete 180-state local walk reaches at most 16 bytes, and its
returns are balanced. Both 5c334 calls, at 626ea and 628d6, retain one long, so
X = Y+8. Every branch after a returning call continues in the same parent; no false
non-returning annotation prunes SC_DELAY (the native delay wrapper at 3a1e; see
`NATIVE-STACK-LEDGERS.md`, **Positive continuation child domains**) or any other valid
continuation.

| Local branch / site (MODE values hexadecimal) | Child residency and domain |
|---|---|
| Entry refusal 62920 → 62928 | 6262c = Y+4; accepted native-only output and queue domain |
| 626cc | 5c866 = Y+4; protected stores, router key F5, accounting 03; no covered entry |
| 626ea / 628d6, including a nonzero 438033 and the default / mode 06 / 1c cases | 5c334 at X = Y+8; **all 27 arms** above, with no lifecycle pruning |
| Mode 08 at 627b4 | 34d22 = Y+8; 1cda6 = Y+4; alternatives 582ec / 58300 / 57e70 = Y+4 or 5c334 = Y+8 |
| Mode 0a at 6282c | 34d22 = Y+8; matching argument cleanup at 628dc |
| Mode 0e at 62840 | 34d22 = Y+8; 42e9a / 42eae / 42d2c = Y+4; later 3da1a = Y+4 |
| Mode 18 at 6288a | 34d22 = Y+8; 62b98 = Y+4; 5636 or 52da = Y+4 |
| Mode 1a at 628ba | 34d22 = Y+8; matching cleanup at 628dc |
| Common tails 62902 / 6290e | 1d48a (operation 04) = Y+12; 4e51c(02) = Y+8 |
| Final 62940 / 6294c | 53dd0 (operation 01) = Y+16; one earlier stored long remains beneath the current argument pair |

The matched existing domains are: the 400/44c stores, 4ef16 scalar/output, 34d22 → F6,
the 1cda6/36896 scalar selectors, 6262c output/queue, and 1d48a(04) via 1c438/1c9b0 →
426 plus the leaf 1ce06 and fixed queue and numeric work. None of them synchronously
invokes a receiver or a controller. The remaining ordinary union is completely matched in
this document, in the table below, rather than re-audited recursively here:

| Positive frontier | Exact sealed synchronous domain |
|---|---|
| 5c866 | Protected stores, router key F5, 53dd0(03); normal returns. |
| 58300 | Numeric and option work, 523a4 → router key 201, 58634/587d0; 62348; selected accounting 66/67; 599ba / 3fefa / 5aa68(06), mode F6, fixed queues. 599ba is separate from 589ba; no hidden covered fallthrough. |
| 42d2c / 42e9a / 42eae / 582ec | Selected accounting 50/51 and the finite 5bd3a; router keys 90/31/F6; typed numeric work; 46b4/4360 or 587c0 → 58634; positive 3fefa / display. The normal returns precede the wager root 42f08; 105a discards the SP rather than retaining an ancestor. |
| 62b98 / 5636 / 52da | 4e4e / 5fc4 / 46b4, selected accounting 1e/1f/20/27 and the finite 5bd3a (accounting 21 … 26), router keys 37/202/203/211/F6; exact scalar, output, queue and native context services. Resuming Task5 does not synchronously call its settlement root. |
| 64566 / 6488e | Complete post-delay numeric, output, input, queue and TX domains; selected accounting 93/52/81, router key 36, 3976c; ends before 64a7a. No extra controller, loader or game entry. |
| 4e51c(02) | Option 0c plus immutable six-byte output-mask DATA; no code pointer and no write to Task8's callback array. |
| 53dd0(01/03) | 440bc → 54912(40) / 3bd24; 440f6 → 3bd24 / 3bdca; exact numeric targets, not an inference from the previously selected operation 04. |
| 5c8d8 suffix | All of router keys 59/6c/6e, options 39/3a, the 36352 DATA loop, protected stores and accounting 01; no new covered port after 5c906. |
| cf54 alternate c8c6 | Registry installation, a finite numeric tree with ABI returns, selected accounting 35/8f/2e/10, fixed queues and display, and Task8 creation; no synchronous covered exit. |

The five-protocol router matches of the ordinary analysis keep their exact keys and
default arms: 59 → AS 25e8a / SAS 24044; 6c/6e → SAS 245fc / 24616; 90 → BSG 1bd7e;
F5 → SAS 2446e; 202/203/211 → SAS 2448e / 244ba / 244a4; 37 keeps every valid
bill-status arm (see **Selected routers — all five protocols**). Other protocols use
their actual default arm, and the normal queue and TX tails are included. Numeric and
descriptor alternatives include the checksum and underflow fatal paths; the proof
introduces no invariant about financial values or history. The reviewed Task7 analysis
separately closes 5cf7e, 5efc0 and 3da1a. This exhausts the union that was previously
outstanding; no port is treated as closed merely because no direct hook call appears in
it.

The complete covered formulas from 6268e are:

- via 5c334 → 43e62: P = Y+84, changed normal Y+276; L = Y+104, runtime/general loader
  Y+406/506; first B = Y+32;
- via 5c334 → 57e70: P = Y+28, normal Y+220; first B = Y+24;
- direct 57e70: P = Y+12, normal Y+204; first B = Y+8;
- the direct 5c334 → 64a7a routes are 4 bytes below the first shared fault row and
  cannot exceed it;
- no initial evaluator Q and no additional covered root occurs through any remaining
  branch, because the complete positive frontier union above is synchronously free of
  hooks.

With the sealed ingress Y64, X72 agrees with the ordinary F maxima. With the bootstrap
Y26, X34 and the same state-independent all-arm contracts give normal 302 / general
loader 532 / **first B 58**. This 58 is the complete abstract upper bound for the initial
bootstrap, not a claim that all its predicates can hold at once. It replaces the older,
loose 226-byte hand-off. No Task6 lifecycle predicate and no assumption of fresh native
NVM was needed.

### Full 5c8d8 continuation composition

The frame-free body continues after the argument cleanup that follows 5c906. 5c92c calls
53dd0(01) while retaining 12 local bytes; its suffix 5c936 … 5ca4a includes 26a0e with
keys 59/6e/6c, typed options 39/3a, 36352 and protected stores. At 5c9b2 the router key
6e call retains 12 bytes, not only its new 4. The other router sites have 4 outgoing
bytes. Their complete selected router and option descendants are positively matched by
the ordinary frontier analysis, and none is an inline game root. The normal suffix
returns at 5ca4a, and the distinct Task7 root 5ca4c follows with no fallthrough. This
closes the whole 5c8d8 root, not just 5c906 → 5c334: the prelude and the suffix have
empty synchronous covered-entry sets.

## Root-to-covered-entry checklist

This is the explicit completeness checklist. It does not add a premise about arbitrary
code pointers.

| Entry / incoming family | Composed source / status |
|---|---|
| 61172 via the direct matrix pointers 77cce / 77cd6 / 77d2e / 781f6 / 781fe / 78256 | Task6 uniform P56; accepted native dispatch domain |
| 61172:65072 | 64f56 via matrix 78166, P64 |
| 61172:64046 | 63cd0 via 63c84/64056 (SAS and Task6) and via 64a7a (shared fault / 5c334); P176 for SAS or 148 for the new ordinary branch |
| 61172:64d4a | 64a7a directly from the shared fault / 5c334; the new X72 gives direct P116, and P120 through 43e62 |
| 61172:61e0e via 61c50:8efa / 8f2c | Task3 P104 and Task6 helpers P64/P52 |
| 61c50:3d2a8 / 3d752 | Initial 3d294, P ≤ 112; a live admitted recurrence uses the guard, not this finite prefix |
| 61c50:4061a / 40af8 | Task6 matrix 40600, P84 |
| 61c50:57e88 / 582bc | Task6 matrix / shared 5c334, P ≤ 92; boot P38/54; the complete frontier and producer closure is matched |
| 61c50:5990c / 5a082 | Admitted 589ba directly or via 59f3a; E+328 guarded region |
| 61c50:61b80 / 61bea and 65128:61bd2 | 61ad0 via the matrix, 53336, 54db0, and BSG callbacks 14/18 with the finite DAG including 19; corrected P224/Q220 |
| 65128:5323a / 54b5c | 48ca6 fixed branches, finite Q64/68; the 54db0 continuation Q144 |
| 589ba from Task5, 2509c, 40600, the initial 3d294 and 57e70 | The finite first rows above; the prefix is not reset under an admitted ancestry |
| 589ba from AS 539e4 or 65128:656c0 / 65746 | AS first 240; BSG Q220+44/48 = 264/268; the other initial Q rows are smaller |
| 3756c shared loader | SAS command 8C 498; old fault 458; corrected ordinary 470; bootstrap general 532; all ports positively closed as above |
| Other Task7 primary dispatch roots | The reviewed complete positive no-covered-entry analysis below; the Test10 and reconfiguration positive rows remain closed |
| Early bootstrap, clear, commissioning and E0 boundaries | The explicit tables below and the earlier qualification; not omitted from the task inventory |

The census of exact incoming calls is supplemented by the accepted native matrix,
callback and PC-switch producer domains. The regional interior seal of 589ba/3d294 is
reused **only** for those ranges; it is not presented as a new universal proof covering
the interior of every P/Q address. The 62958 BSR producer is sealed explicitly
(**62958 producer seal and retained ancestry**); a census of absolute JSRs alone was
insufficient. Every initial ingress in the checklist has either a finite root prefix or a positive no-covered-entry
contract. Any descendant with a live admitted 589ba ancestor remains in the separate
guarded induction.

## Original qualified incoming-prefix inputs

The composition reuses the following original, instruction-led incoming facts rather than
silently re-proving them from a local window:

- Task6 F32 / direct 36 / secondary 56 (9f2a, a5e0, a634→aa6a / ad72);
- the qualified SAS parent 60 and 2f8d6 F28 (`NATIVE-STACK-LEDGERS.md`, **Standard SAS
  identity hooks (not private E0)** and **Runtime loader tail — local formulas**);
- the initial 3d294 ≤ 100 through 404f0:405f6 (12 fixed + 28 retained), through 43e62
  and through the native matrix;
- Task5 8f60→506be/50f68 FIRST ≤ 56, at 509e2/513ba;
- Task6 event 0f, MODE 0 → 2509c:250ae FIRST ≤ 60 (direct 40).

These are bounded initial-prefix inputs under the positive premises about the native
dispatcher, callbacks, objects and contexts; they are not whole-task temporary-peak
claims. Their concrete parents and sites are preserved in the complete ordinary-root
table and in the initial call inventory. A future new or foreign invoker may not borrow
these prefixes.

Task6's exact six 61172 matrix cells, the 61ad0 matrix cells and the controller pointers
are in **Initial call producer inventory**. The directly encoded callers are supplemented
by the complete native matrix, callback, PC-switch, registry and forwarder classes of
`SETTLEMENT-GUARD.md`, **Producer seal**. Reusing the frozen costs additionally requires
the passed candidate instruction and frame identity. A regional scan of the guarded body
alone does not prove universal upstream P/Q ingress.

## Completed Task3 missing prefix

Task creation at 1a16 selects the task entry 633c. The prologue at 633c, LINK −56,
includes the saved FP (4 bytes), and 6340 saves seven registers (28 bytes): F = 88.

A complete local SP worklist over 633c … 8a60 reached 2,298 states with a stable local
maximum of 132 and no unresolved local CFG edge. It models the actual LINK/UNLK and
MOVEM, every push, pop and LEA argument cleanup, both successors of each conditional and
the fallthrough after each completed call. Calls are summaries, not proofs of the
callees. At 72ee the walk reaches **exactly** 88 bytes, so **zero** outgoing argument
bytes survive there. The native delay 3a1e is treated as returning; Ghidra's erroneous
non-returning annotation is not used.

| Task3 ordinary route | Whole-task residency |
| --- | ---: |
| 633c after the native prologue | 88 |
| 72ee → 8c1e entry | 92 |
| 8c1e:8d84 → 8ecc or 8dda → 8f02 entry | 96 |
| 8ecc:8efa or 8f02:8f2c → 61c50 entry | 100 |
| 61c50:61e0e → 61172 entry P | 104 |
| Covered changed-hook ceiling using the existing P+192 | 296 |

8c1e, 8ecc and 8f02 have no frame on those outgoing paths. The cached complete walks had
pre-call D = 0 at 8d84/8dda and 8efa/8f2c; their earlier temporary store and queue
arguments are popped before these calls. This closes that **specific** Task3 ancestry,
not a universal Task3 call graph or every native peak. In particular, 753c→42ee has a
pre-call of 92 (one outgoing long), but 42ee ends at 435e; it does **not** fall through
into the separate routine 43c2 at 43c2. Do not infer another route into 5c8d8 merely from
their adjacent addresses.

## Completed additional Task6 helper prefixes

The native Task6 entry 9f2a has a fixed frame of 32, and a directly dispatched handler is
entered at 36. Its a634→aa6a call retains one long, so aa6a is entered at 40. aa6a saves
D2-D4 (12 bytes). The bounded switch at aaaa (table aaae) has 17 targets (index 0 … 16);
the full local SP walk completes at 200 states with a local maximum of 20. The pre-calls
acb6 → 8f02, acfa → 8ecc and the ad3a/ad72 register callbacks are exactly 12, with no
extra arguments.

| Task6 non-matrix helper route | Entry values / P |
| --- | --- |
| a634→aa6a→acb6:8f02 or acfa:8ecc → 61c50 → 61172 | 40 → 56 → 60 → P64 |
| a61c→af74→b074:8ecc → 61c50 → 61172 | 36 → 44 → 48 → P52 |

The complete local walk of af74 … b080 reaches 77 states with a local maximum of 16; the
b074 pre-call of 4 is its single D2 save, with no arguments. The RTS at b07e precedes the
distinct Task7 entry b080, so this is not Task7 ancestry.

## Fault-controller family

This subsection covers **Task2 and the direct Task6-clear roots only**, not the shared
5c334 ancestry, which is composed separately under **All 27 arms of 5c334, plus default —
exact composition**.

Residency includes incoming returns; residency at a task's entry starts at zero. The
local SP walks resolve the bounded switches at 4884, 4db4, 5d34 and 43ebc. The last one's
27-entry table 43ec0 includes 43f68 and 43fa0; they are cases inside 43e62, not separate
unused functions. Importantly, 435da restores D2 at 4389a and ends with the RTS at 4389c.
The 4394a call belongs to the separate frame-free routine 4389e.

| Native path / location | Whole-task bytes |
|---|---:|
| Task2 46f4 → 4754 entry | 4 |
| 4754:4c38 → 4d90 entry | 40 |
| 4d90:4dd0 / 4e0e → 4399a entry | 52 |
| 4399a:43a9e → 43e62 entry on that route | 60 |
| Alternate 5c34 → 4399a / 5c34 → 5ada → 4399a | 32 / 44 |
| Task6 clear wrappers 434e0 / 43548 / 435b0 → 4399a | 64 |
| Combined listed Task2 / direct Task6-clear 43e62 entry | 72 |
| Onward 64a7a / 63cd0 entries | 76 / 96 |

Task6's uniform handler-entry ceiling of 56 includes its secondary dispatch: a634→aa6a
retains 4 argument bytes, aa6a saves 12, and ad3a/ad72 retain the 12 saved-register bytes
and no outgoing arguments at the dispatch. A direct a5e0 dispatch is instead entered at
36. Using 56 for all relevant matrix handlers is conservative; it is not proof that every
row is reachable through that secondary route. Through the loader formula R(63cd0)+362,
these Task2 and direct Task6-clear roots are bounded by 458 non-interrupt bytes, below SAS
command 8C's 498. The separate shared route X72 → 43e62 is entered at 84 and has the
runtime-loader bound 470; the narrower 72/458 row must not be applied to that ancestry.

## Initial call producer inventory

| Covered / native entry | Known incoming producers |
| --- | --- |
| 61172 | 61e0e BSR; 64046, 64d4a, 65072 JSR; matrix pointers 77cce / 77cd6 / 77d2e / 781f6 / 781fe / 78256 |
| 65128 | 5323a, 54b5c, 61bd2 JSR; no literal matrix target found |
| 61c50 | 8efa, 8f2c, 3d2a8, 3d752, 4061a, 40af8, 57e88, 582bc, 5990c, 5a082, 61b80, 61bea |
| 61ad0 | 535be, 55152, 556a6, 556e4; matrix 7863e / 7868e (event 21, MODE 6/26) |
| 5323a / 54b0e | 48cce / 48cd6, both in 48ca6; matrix 78696 (event 21, MODE 28) |
| 53336 / 54adc | 48d06 / 48d0e, both in 48cde; matrix 78e16 |
| 64f56 | matrix 78166 (event 10, MODE 26) |
| 63c84 | 2f964 / 2f9de and matrix 78ad6 / 78b3e (event 31, MODE 0/26) |
| 64056 / 63cd0 | 2f96a or 63cc8 / 63cc0; local child offsets 40/40 into the next 63cd0 and 40 into 61172, as in the durable ledger |
| 64a7a | 43fd2 or 5c802; local 63cd0 offset 20 and direct 61172 offset 32 |
| 57e70 | 5c73c, 62822 and matrix 77d5e (event 2, MODE 8) |
| 5c334 | 5c906, 626ea, 628d6; each with one 4-byte argument, then a 4-byte return |
| 5c8d8 | 43de (in the separate routine 43c2); matrix event 13, every MODE 0 … 29 except 21 |
| 6268e | 13e6 (bootstrap); matrix 782ba (event 13, MODE 21) |

6268e additionally has the BSR at 62958:62996, rooted at cf54 through **both** a5e0 and the
secondary ad72. The complete computed, interior and PC-relative domains below supplement
this direct-call inventory; a census of absolute JSRs alone was insufficient. The regional
589ba/3d294 seal applies only to its own bodies, not to every upstream address.

## AS selector DAG

### Immutable selector table — all 13 records

In this section AS event and record indices and action IDs are decimal unless prefixed
with 0x; code addresses and field offsets remain hexadecimal. Records are 20 bytes apart.
The dispatch fields are WORD +0 (entry action), WORD +4 (expiry event), WORD +6 (polling
action), WORD +0c (alternate event) and WORD +12 (DATA index). Here +12 is hexadecimal
0x12 (18 decimal), **not** +0c. The DATA area holds two **six-byte** records, not function
pointers.

| Event | +0 action | +4 expiry event | +6 poll action | +0c alternate event | +12 DATA index |
|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 1 | 0 | 0 |
| 1 | 2 | 5 | 0 | 6 | 1 |
| 2 | 2 | 5 | 0 | 6 | 1 |
| 3 | 2 | 5 | 0 | 6 | 1 |
| 4 | 2 | 5 | 0 | 6 | 1 |
| 5 | 2 | 0 | 0 | 6 | 1 |
| 6 | 7 | 7 | 0 | 0 | 0 |
| 7 | 8 | 0x801b | 13 | 0 | 0 |
| 8 | 9 | 9 | 0 | 0 | 0 |
| 9 | 10 | 10 | 0 | 0 | 0 |
| 10 | 11 | 0 | 0 | 0 | 0 |
| 11 | 12 | 0 | 14 | 0 | 0 |
| 12 | 2 | 5 | 0 | 6 | 1 |

The DATA bytes are `[00 00 00 00 00 00]` and `[03 32 64 00 00 00]`. 487a6:4883a reads the
index at +12, 48842/48846 bound it against 2, and 4886a … 4890c publish six bytes. Every
immutable record selects the valid index 0 or 1, and no DATA selector is callable.

| Consumer and native evidence | Exact callback domain and prefix |
|---|---|
| 487a6: 487d0 … 487d8 check the low WORD event < 13; 4896a reads record +0; 48972/48976 check action < 15; 48992 JSR (A0) | For events 0 … 12, only the actions `{0,2,7,8,9,10,11,12}`. LINK 0 (`4e560000`) plus MOVEM (`48e73c30`) saves 28 fixed bytes. 4895a removes 16 bytes of store arguments **before** the callback: edge +32. Actions 13 and 14 are never selected. |
| 489ae: check at 489be; reread at 489d8; 489e6 reads +0c; zero skips; 48a04 → 487a6 | The only nonzero event is 6, hence callback 7 only. LINK 0 plus MOVEM D2/D3/A2 gives 16 fixed bytes; seven forwarded longs, 28; return 4: +48 to 487a6, then +32 = +80 to callback 7. |
| 48ada: timer == 1 branch at 48b0c; reread at 48b36; 48b44 reads +4; 48b4a → 487a6 | Expiry events `{0,5,7,9,10,0x801b}`; valid action images `{0,2,8,10,11}`. 0x801b takes the error 1b path at 487c2 … 487c6, not an action call. Fixed 24; 32 retained argument bytes (one old store argument survives beneath the seven forwarded longs); return 4: +60 to 487a6 and +92 to its callback. |
| 48ada: reread at 48b76; 48b84 reads +6; 48b8c/48b8e bound the action; 48ba8 JSR (A0) | Direct actions `{0,1,13,14}`. Fixed 24 and no retained arguments at 48ba8: +28 to the callback. The timer-decrement arguments are popped at 48b72. The initial range check is not treated as freezing later reads of 43cd6e. |
| 48ada: tail 48baa … 48bea | Only the ROM 7fe test, the optional af1c test, the record +0c test and the 3b84 queue service; no further callback dispatch. The raw decoding was checked even though Ghidra eliminates this tail. |
| 5391c: 53920 … 53944 | LINK 0, fixed 4; seven longs, 28; JSR return 4: +36 to 487a6 and +68 to its callback. The outer event argument stays owned by the outer frame. |
| 53a44:53a62 / 53c92:53cb0 | Six longs, 24, no fixed frame, return 4: +28 to 489ae / 48ada. No callback body calls either wrapper. |

487a6's high-bit event path and its out-of-range path call 62422, not a callback. The
already closed error, router, progressive and service domain of 62422 is kept; 62422 is
not presumed to halt. The debug queues of 48ada likewise do not run another task's
receiver on this stack.

## Complete 15-callback inventory

"None" means that this body, together with its delegated positive helper domain, has no
AS dispatch edge and no covered exit before the guard. It does **not** mean universal
native stack safety.

| Index | Target | Positive body / outgoing event or covered exit |
|---:|---|---|
| 0 | 487a4 | RTS. |
| 1 | 53dcc | TRAP1, then the RTS encoding at 53dce; inherited terminal-fatal handling, no AS forward. |
| 2 | 539b0 | Push 0 → 5394e; inline display-state writes, return. |
| 3 | 487a4 | RTS; not selected by any covered AS consumer. |
| 4 | 487a4 | RTS; not selected. |
| 5 | 487a4 | RTS; not selected. |
| 6 | 487a4 | RTS; not selected. |
| 7 | 539d2 | 539d2 → 53a6e, then 539dc → 5394e(2). 53a6e uses the 66bfe / 66fb4 / 66286 numeric DATA resolution, stores, TRAP1 and 53dd0(0x83), not a controller. |
| 8 | 539b8 | 549c8(0x8e), decrement of the returned byte, 5396e display-state writes bracketed by 480/49e; no event dispatch. |
| 9 | 539d0 | RTS. |
| 10 | 53a2a | 53a2a → 539e4 (+4), then FIRST 589ba (+20). |
| 11 | 539e4 | Three 400 stores retain 16 bytes at 53a10 → 589ba: exit +20. The later store and cleanup come **after** the first guard. |
| 12 | 53cea | 40438(3, 0x25), inline display-state writes, 426(timer, 0), 16-byte cleanup, RTS. |
| 13 | 53a2e | If the display-state byte == 8, 53a3c → 5391c(event 8), then return. Event 8 → action 9 (RTS), never back to 13. |
| 14 | 53d96 | Optional 53da4 → 53d2a (+4), otherwise inline display-state writes and return. The full switch and forward domain is below. |

For the exact selected accounting wrappers: 53dd0(0x83) → 47c8c calls 3bd24/3c090; 0x84 →
467f0 calls 3bd24 with an 8-byte cleanup on each iteration; 0x85 → 47cdc calls 3bd24.
These immediate bodies were checked to match the inherited numeric boundary, without
reopening the numeric descendants. 0x62 → 467a4 is already documented in
`NATIVE-STACK-LEDGERS.md`, **Settlement guarded descendant ledgers (frozen-b2 design)**.
The other boundaries of 53b36 are 35fdc, 400/44c and the fixed services at 38130. No AS
controller family is therefore smuggled in through an unqualified generic 53dd0 opcode
domain.

## Callback 14: complete switch and independently mutable reads

53d2a zero-extends 43cd72, bounds it to 0 … 12 at 53d34/53d36, and dispatches through the
signed offsets at 53d42. The 13 decoded targets are
`53d5c,53d64,53d64,53d64,53d64,53d64,53d64,53d64,53d76,53d84,53d64,53d92,53d64`.

| Initial switch value | Arm and subsequent event / exit |
|---|---|
| 0 | 53d5c → 53b36. Branches below; no argument at this call. |
| 1 … 7, 10, 12 | 53d64 **rereads** 43cd72; 53d6c → 5391c(reread WORD). Do not restrict this second read to the values of the first switch arm. Accepted events 0 … 12 map through field +0; other WORDs take checked error paths. |
| 8 | 53d7a → 5391c(7) → action 8. |
| 9 | 53d88 → 5391c(10) → action 11. |
| 11, or initial value > 12 | 53d92 TRAP1; no forward. |

The branches of 53b36 (all frame-free):

| Call site | Event / covered domain | Actual outgoing retained bytes | Callback 14 → child-callback increment |
|---|---|---:|---:|
| 53bec → 5391c | event 12 → action 2 | 12: earlier 400 arguments 8 + event 4 | 4+4+16+36+32 = 92 |
| 53c44 → 5391c | The **zero-extended** BYTE 43cd47 + 1, added as a WORD: event 1 … 256. The accepted 1 … 12 → actions `{2,7,8,9,10,11,12}`; 13 … 256 → checked error | 20: the arguments of the two 38130 calls, 16, + event 4 | 4+4+24+36+32 = 100 |
| 53c8a → 53a2a | Direct callback 10, then 539e4 → 589ba. The branch begins at 53c50 → 53a6e, whose frame returns before this exit | 0 | 4+4+4 = 12 to callback 10 |

The byte at 53c44 is read **after** both 38130 services, and no earlier compare or store
constrains it. In particular, byte 8 gives event 9 → action 10, and byte 9 gives event
10 → action 11. These are allowed abstract selector values, not a claim about a
realizable financial history. Both earlier service argument blocks are still resident:
only the LEA 20 at 53c4a removes them, after the nested dispatch returns.

## Weighted callback-index graph and exits

All weights are relative to the source callback's entry. Alternative edges are kept where
useful; taking the maximum weight at each merge is enough for the bound.

| Source → destination | Weight | Producer / domain |
|---|---:|---|
| 10 → 11 | 4 | 53a2a direct BSR to 539e4 |
| 13 → 9 | 76 | event 8: argument 4 + return 4 to 5391c, +36 dispatcher, +32 callback |
| 14 → `{0,2,7,8,9,10,11,12}` | 80 | 53da4 +4, 53d6c argument 4 + return 4, +36, +32; the independently reread valid event 0 … 12 |
| 14 → 8 / 11 | 80 | 53d7a event 7 / 53d88 event 10 |
| 14 → 2 | 92 | 53bec event 12 via 53b36 |
| 14 → `{2,7,8,9,10,11,12}` | 100 | 53c44 event 1 … 12 via 53b36; invalid larger events are error exits |
| 14 → 10 | 12 | 53d5c → 53b36:53c8a direct 53a2a, with the outer 53da4 |
| 11 → FIRST 589ba | 20 | 53a10; 16 retained bytes + return 4 |

There are no other graph edges. The only sources of recursive-looking forwards are 13 and
14, and field +0 can select neither. Every nontrivial graph path ends in a leaf or in
10 → 11 → guard. **No nontrivial SCC and no self-loop exists.** The longest growth between
callback entries is 104 (14 → 10 at 100, then 11 at 4); the longest covered prefix is 124
after the final 20. An AS callback can cause at most one nested 487a6 descent, not an
arbitrarily repeated 76-byte edge.

| Entry | FIRST 589ba increment | Initial 61172 / 65128 / shared-loader exits **before** it |
|---|---:|---|
| callback 11 | 20 | None |
| callback 10 | 24 | None |
| callback 14 | 124 | None |
| callbacks 0 … 9, 12, 13 | No covered exit | None (positive bodies and delegated domains above) |
| 487a6, valid AS event | 56 = 32+24 | None |
| 5391c | 92 = 36+56 | None |
| 489ae / 53a44 | No covered exit: only callback 7 | None |
| 48ada expiry branch | 116 = 60+32+24 | None |
| 48ada direct branch | 152 = 28+124 | None |
| 53c92 | 180 = 28+152 | None |
| 48cde AS branch 48ce8 | 184 = 4+180 | None |

The qualifying maximal abstract route is:
`48cde→53c92→48ada:48ba8→[14]53d96→53d2a(0)→53b36:53c44→5391c(9)→487a6→[10]53a2a→[11]539e4:53a10→589ba`.
Its entries relative to 48cde are `0,4,32,60,64,68,92,128,160,164,184`.
With the inherited R(48cde) ≤ 56, callback 14 ≤ 116, the nested callback 10 ≤ 216,
callback 11 ≤ 220 and FIRST 589ba ≤ 240. This is an overapproximation that needs no
fresh-data or financial-history premise. **Do not** attach the P/Q values downstream of an
admitted settlement to it as unguarded exits; the route stops at FIRST 589ba. No new
reserve, threshold or publication decision is made here.

## BSG selector DAG

### Explicit selector premises

- The parameter binding is 6aa3e with count 20, 6aaa0 with count 20 and 6aa8e with count
  3, preserved by qualified native forwarders. The counts are 20, 20 and 3, not arbitrary
  pointers supplied by a host.
- At **each** unchecked controller-record load in 5528a/5544a, WORD 43cd6e must be a valid
  index 0 … 19 for this BSG instance. It is reread independently; an earlier range check
  is **not** claimed to make a later load atomic. This is the explicit inherited premise of
  a valid controller selector, not a new proof about every retained SafeRAM history or
  about arbitrary corruption. AS's valid range 0 … 12 is a subset, but no stability of the
  same mode is needed or inferred from that observation.
- 55170 checks its independently read WORD 43cd72 against 19 decimal (0x13). All outcomes
  0 … 19 are allowed, and larger values TRAP1. No assumption links it to an earlier 43cd6e.
- The current native record pointers and history indices and the kernel contexts are valid
  under the pre-existing object and history-pointer premises. No numeric amount, nonzero
  divisor, progress of a hardware response or monotonic financial state is introduced.
  Every mutable branch is overapproximated independently.
- The numeric, service and fault ports keep their accepted contracts: no covered entry,
  and either return or halt / fresh SP. This graph does not assert that they cannot
  temporarily overflow; purely native work remains outside the extension-live peak
  promise under the intact-context premises.

## Immutable binding and consumer fields

54c70 executes LINK 0; PEA 3, 6aa8e, 0x14, 6aaa0, 0x14, 6aa3e; pushes WORD 0a(FP) in a
long; and calls 54c98 → 487a6. F4 + arguments 28 + return 4 = **36** below the 54c70 entry.
487a6's seven-long ABI at FP offsets 8 / 0c / 10 / 14 / 18 / 1c / 20 is: event, action
array, action count, controller array, controller count, six-byte data array, data count.
LINK 0 saves FP (4) and MOVEM D2-D5/A2-A3 saves 24, giving F28. 487d0 … 487d8 check
event < count 20; bit 15, at 487ba … 487c0, instead selects the native error routine 62422,
never a callback.

| Consumer | Immutable selected field / check | Call prefix |
| --- | --- | ---: |
| 487a6 | WORD(controller + 20×event + 00) at 4896a; index < 20 at 48972/48976; load 6aa3e[index] at 48984 … 48990; JSR at 48992 | 28 fixed + 0 arguments + 4 = 32 |
| 489ae | WORD(controller + 20×(independently read 43cd6e) + 0c) at 489e6 becomes an EVENT for 487a6 at 48a04; no direct callback | 16 fixed + 28 arguments + 4 = 48 into 487a6 |
| 48ada forwarding | WORD(controller + 20×(independent 43cd6e) + 04) at 48b44 becomes an EVENT for 487a6 at 48b4a | 24 fixed + 32 retained + 4 = 60 into 487a6 |
| 48ada direct | WORD(controller + 20×(independent 43cd6e) + 06) at 48b84 is an action index, bounded at 48b8c/48b8e; JSR at 48ba8 | 24 fixed + 0 arguments + 4 = 28 into the callback |

The 32 at 48b4a includes the old long retained from 426, pushed before the seven forwarded
argument longs; 489ae has no such extra long. Each caller restores FP and its saves and returns;
48ada's forwarding arm jumps directly to the 48bea epilogue. Its direct arm has no second
callback. The original ROM byte 7fe = 0 bypasses the optional later data-post arm, which in
any case is not an inline controller invocation.

The 20 rows are decoded from the immutable 20-byte records at 6aaa0. The +04 and +0c
columns hold hexadecimal event values; the +00 and +06 action IDs are decimal:

| Row | Record | +00 action | +04 event | +06 direct action | +0c event |
| --- | --- | ---: | --- | ---: | --- |
| 0 | 6aaa0 | 0 | 0000 | 1 | 0000 |
| 1 | 6aab4 | 2 | 0002 | 0 | 000b |
| 2 | 6aac8 | 3 | 000a | 0 | 000b |
| 3 | 6aadc | 2 | 0004 | 0 | 000b |
| 4 | 6aaf0 | 3 | 0005 | 0 | 000b |
| 5 | 6ab04 | 4 | 000a | 0 | 000b |
| 6 | 6ab18 | 2 | 0007 | 0 | 000b |
| 7 | 6ab2c | 3 | 0008 | 0 | 000b |
| 8 | 6ab40 | 4 | 0009 | 0 | 000b |
| 9 | 6ab54 | 5 | 000a | 0 | 000b |
| 10 | 6ab68 | 6 | 000a | 0 | 000b |
| 11 | 6ab7c | 7 | 000c | 0 | 0000 |
| 12 | 6ab90 | 8 | 000d | 0 | 0000 |
| 13 | 6aba4 | 9 | 000f | 0 | 000e |
| 14 | 6abb8 | 10 | 000f | 0 | 0000 |
| 15 | 6abcc | 11 | 801b | 16 | 0000 |
| 16 | 6abe0 | 12 | 801b | 17 | 0000 |
| 17 | 6abf4 | 13 | 0012 | 0 | 0000 |
| 18 | 6ac08 | 14 | 0000 | 18 | 0000 |
| 19 | 6ac1c | 15 | 801b | 19 | 0000 |

So 487a6's field 00 selects the IDs {0,2,3,4,5,6,7,8,9,10,11,12,13,14,15}. 489ae's field
0c emits the events {0,0b,0e} → IDs {0,7,10}: **no covered exit**. 48ada's field 04 emits
{0,2,4,5,7,8,9,0a,0c,0d,0f,12,801b}; 801b takes the error arm, and the other events give
the IDs {0,3,4,5,6,8,9,11,14}. 48ada's field 06 selects exactly the IDs {0,1,16,17,18,19}.
Together these cover all 20 callbacks, not an arbitrary code pointer. The record's data
selector at +12 is bounded by count 3, and the six-byte data copy is DATA publication, not
another executable selector.

## Every 54c70 producer and exact event domain

These are the addresses of the JSR **opcodes**. (An earlier list, starting 551ce, named
their operands, which sit at opcode + 2.) 487a6 is entered 36 bytes below the entry to
54c70, and its callback adds another 32, so from a producer with local pre-call D the selected
callback is entered at D+72.

| Parent / actual JSR site(s) | Events, independently overapproximated | Pre-call D | Selected IDs |
| --- | --- | ---: | --- |
| 54b0e:54c12 / 54c1e / 54c2a | 01 / 03 / 06 literal | 8 each | 2 |
| 54c42:54c66 | 13 literal (decimal 19) | 4 | 15 |
| 55170:551cc | 01 | 4 | 2 |
| 55170:551da | 03 | 4 | 2 |
| 55170:551e8 | 06 | 4 | 2 |
| 55170:551f6 | 0a | 4 | 6 |
| 55170:55204 | 0b | 4 | 7 |
| 55170:55212 / 55234 | 0c | 4 | 8 |
| 55170:5526a | 11 (decimal 17) | 4 | 13 |
| 55170:55278 | 12 (decimal 18) | 4 | 14 |
| callback 2 = 5528a:552bc | WORD 6aaac + 20×(valid 43cd6e) → {0,0b,0e} | 4 | 0, 7, 10 |
| callback 9 = 5544a:5546c | the same independent ROM-record read → {0,0b,0e} | 4 | 0, 7, 10 |
| callback 11 = 55492:554ce | 10 literal (decimal 16) | 8 | 12 |
| callback 16 = 554da:554f2 / 5551a | 10 / 11 literal | 4 | 12 / 13 |
| callback 17 = 55532:55540 | 11 literal | 4 | 13 |

All 19 absolute JSR producers are included. The constant table-binding and forwarder
producers are the accepted native domain; no unknown dynamic event callback is silently
substituted. No mode or state identity is carried from one mutable read to the next; in
particular, callbacks 2 and 9 do **not** use the entry event as an invariant. If the
valid-index premise is lost, these unchecked record reads are unqualified; this graph
does not repair them.

55170's signed-word switch at 55186 (table 5518a) has 20 entries; word 43cd72 is
zero-extended and range-checked at 55170 … 5517c. The normalized indices are decimal:

| Index | Case target | Emitted event / outcome |
| --- | --- | --- |
| 0 | 551b2 | queue post only, return |
| 1, 2 | 551c8 | 01 |
| 3, 4, 5 | 551d6 | 03 |
| 6, 7, 8, 9 | 551e4 | 06 |
| 10 | 551f2 | 0a |
| 11, 12, 13 | 55200 | 0b |
| 14, 15, 16 | 5520e | 0c |
| 17 | 5521c | 0c or 11; both are allowed, independently of every earlier snapshot |
| 18 | 55274 | 12 |
| 19 | 55282 | TRAP1 |
| > 19 | 55286 | TRAP1 |

Only callback 19 (557c0) calls 55170, at 55802. It saves A2 (4 bytes) and has no outgoing
argument, so 55170 is entered at the callback's entry + 8. 55170 itself has no frame and
adds argument 4 + return 4 on the way to 54c70, so callback 19 → next callback is
8+8+36+32 = **84**. Its match tests merely allow a call or a return; no premise about
response history or stability excludes an arm.

## Complete callback-index graph and direct frontiers

| ID | Target | Fixed F | Next callback IDs / retained edge bytes | Other actual call frontiers |
| --- | --- | ---: | --- | --- |
| 0 | 487a4 | 0 | none | RTS |
| 1 | 54ca2 | 0 | none | 62422 (error 0e), return |
| 2 | 5528a | 0 | 0, 7, 10 @ 76 | 54cb0 scalar/status; optional event production above |
| 3 | 55356 | 0 | none | 552c6(1), return |
| 4 | 55364 | 0 | none | 552c6(2), return |
| 5 | 55372 | 0 | none | 552c6(3), return |
| 6 | 55380 | 4 | none | 54cb0 scalar/status |
| 7 | 553be | 4 | none | 54cb0 scalar/status |
| 8 | 553fe | 8 | none | 54ce4 scalar/status |
| 9 | 5544a | 0 | 0, 7, 10 @ 76 | optional event production |
| 10 | 55476 | 4 | none | 54d20 status; 62422 (error 1b) |
| 11 | 55492 | 4 | 12 @ 80 | 54d20 status or 62422 (error 1b) |
| 12 | 55530 | 0 | none | RTS |
| 13 | 55560 | 8 | none | 55890 record/match boundary; 400/426; 54cb0; 5ff76 motor-fault boundary |
| 14 | 55650 | 4 | none | 61ad0 **covered exit**; 400/426, 3d82/3d88 |
| 15 | 556fe | 4 | none | 40438 request/data boundary; 400/426 |
| 16 | 554da | 0 | 12, 13 @ 76 | 54d20 status or 62422 (error 1b) |
| 17 | 55532 | 0 | 13 @ 76 | 62422 (error 1b) or return |
| 18 | 556c6 | 0 | none | 61ad0 **covered exit**; 400 scalar stores |
| 19 | 557c0 | 4 | 2, 6, 7, 8, 13, 14 @ 84 | 55170; scalar/status return alternatives |

The edge weight 76 = 0 fixed + 4 argument + 4 return + 36 helper + 32 dispatcher. Callback
11 retains its 4-byte D2 save, so its edge adds 80. Callbacks 16 and 17 have no frame and
add 76. Callback 19's extra 55170 call yields 84. No edge returns to 19 or enters 18, and
only 14 and 18 reach the covered 61ad0 exits. The possible targets 0, 7 and 10 of
callbacks 2 and 9 produce no further events. Callback 11 ends at 12; 16 and 17 end at 12
and 13. A topological postorder is
[0,1,7,10,2,3,4,5,6,8,9,12,11,13,14,15,16,17,18,19]. No directed cycle remains. The largest
increment between callback entries is 160, on 19 → 2 → 0/7/10; it is a native-only path,
**not** a whole-native peak or a reserve term. No purely native SCC is dismissed by fiat:
this selector graph has none.

### Closed callback ports

54cb0 and 54ce4 are fixed scalar packet/status stores under 3d82/3d88; 54d20 queries and
stores status and uses the same returning services. 552c6 takes a literal 1, 2 or 3,
bounds it, performs a 16-bit DATA-mask scan and publishes through 54cb0 or 62422; it has no
unlisted 54c70 producer. The **whole** error domain of 62422 is
`NATIVE-STACK-LEDGERS.md`, **Positive continuation child domains**: router key 30 (all
five protocols), progressive operation 04, and the queue and normal tails. 5ff76 uses the
fixed motor-counter, phase-DATA and queue domain of `IRQ-AUDIT.md`, **IRQ6 all-phase
called targets**; it is not a mechanical shutdown. Numeric and store children use the
positive local-domain table below. 55890 and 40438 are **closed** by the exact inspected
contracts that follow; they are not outstanding dependencies.

## 55890

The body 55890 … 558ea saves D2-D4 (12 bytes). It has no calls, no computed transfers and
no external data writes. Register and data comparisons and a bit rotation run in a local
loop bounded by D4 < 11; both exits restore D2-D4 and return (RTS at 558e2/558ea). The
next body begins at 558ec and is not a fallthrough. This is a positive summary (a leaf,
with no covered entry and no controller re-entry), not merely an absence of Ghidra xrefs.

## 40438

The complete body 40438 … 404a8 has LINK −4 plus D2-D4 saves (20 fixed bytes). Its
outgoing call union is exactly 480, 3c14, 3be4, 3b84 and 49e. These are the scalar nesting
lock and unlock and the already sealed fixed native queue services; queue values are not
code pointers, and posts do not synchronously run a receiver on this caller's saved
frames. There is no other direct or indirect call or callback.

The inquiry arguments at 4044a … 40454 are removed by the LEA 12 at 4045a. Each receive
uses 8 argument bytes, removed at 4046e; each repost uses 12, removed at 4048a. The loop
40462 … 40496 does not accumulate arguments. 40498 unlocks, 404a0 restores D2-D4 from FP,
and the UNLK at 404a6 and the RTS at 404a8 restore the entry shape. The return value counts
the matching queue values. This matches the existing contract (valid initialized kernel
objects, fixed services, no external hook); it is not a new liveness or whole-native-peak
claim.

### BSG covered P/Q and FIRST 589ba exits

In callback 14, 556a6 has a pre-call of 4 (the D2 save, no arguments), so 61ad0 is entered
at the callback's entry + 8. In callback 18, 556e4 retains 12 bytes of store arguments, so
61ad0 is entered at the callback's entry + 16. The existing 61ad0 local pre-call of 8 at
61bd2 gives Q = R(61ad0)+12; 61b80/61bea followed by 61c50:61e0e gives P = R(61ad0)+16.
The existing evaluator calls at 656c0/65746 give FIRST 589ba ≤ Q+48 (40 fixed + retained
zero 4 + return 4 at 65746); the earlier 656c0 edge is 44. These bounds overapproximate
the branches; none is a history witness.

| Starting callback | Max Q below its entry | Max P | Max FIRST 589ba entry |
| --- | ---: | ---: | ---: |
| 14 | 20 | 24 | 68 |
| 18 | 28 | 32 | 76 |
| 19 → 55170 → event 12 (hex) → 14 | 104 | 108 | 152 |
| all other IDs | no covered exit | no covered exit | no covered exit |

The callback 19 case uses 55170 selector 18 (decimal), event 12 (hex) and action 14. On
that route it reaches the evaluator hooks 65168/65184 or the normal 61172 changed hooks
**before** any 589ba check, so later settlement admission cannot bound it retroactively.
Unlike the earlier alarm about 5544a, this is an explicit, selector-compatible ROM path.
No claim is made that all the loose mutable predicates can physically hold together.

### BSG consumer/root composition

| Consumer entry R | Q ceiling | P ceiling | FIRST 589ba ceiling | Reason |
| --- | ---: | ---: | ---: | --- |
| 487a6 (any valid BSG event) | R+52 | R+56 | R+100 | 32 to callback 14; its covered offsets 20/24/68 |
| 54c70 (any supplied event) | R+88 | R+92 | R+136 | 36 into 487a6 |
| 489ae with this BSG binding | none | none | none | field 0c selects only 0/7/10 |
| 48ada field 04 forwarding arm | R+112 | R+116 | R+160 | 60 into 487a6 |
| 48ada direct field 06 arm | R+132 | R+136 | R+180 | 28 to callback 19; 104/108/152 |
| 48ada, all normal arms | R+132 | R+136 | R+180 | the maximum of the two; direct callback 18 is smaller |

For the **existing** conservative Task6 handler-prefix row of 56: 48cde is entered at 56,
54adc at 60 and 48ada at 88. This BSG family therefore supplies **Q ≤ 220, P ≤ 224 and
FIRST 589ba ≤ 268**, replacing 208/212/256 as a safe recomposition input for that
particular conservative root. The covered local envelopes of **Covered patch-family
inventory** give Q+86 ≤ 306 and P+192 ≤ 416, not the old 294/404. These are finite
BSG-family bounds composed with the complete root checklist above. Any other external
caller must use its own prefix and may not inherit 56; using a tighter actual incoming
prefix is the job of the root's owner.

FIRST 268 is not reused after a recursive settlement. An admitted 589ba still uses its
locally valid 328/120 cut graph. Nothing here changes that implementation, selects a new
threshold or proves financial termination. The complete composition above includes both
DAGs and every ordinary root; the native FIRST 268 replaces the historical 256 and is not
reused after a recursive admission.

## Positive local-domain boundaries

These are positive domains, not heuristics based on a helper's name or on missing xrefs.
The letters are used throughout the ordinary-root sections.

| ID | Exact accepted boundary / stable locator |
|---|---|
| N | The 400/426/44c protected stores; 35b48 → 35aec immutable options; the fixed-descriptor counters 54912 / 5495c / 549c8 / 548bc / 547d4 / 54a12; 3bd24 / 3bdca / 3bea6 / 3c090 / 3c446 / 3c7ec and their validator, converter, BCD and copy subtrees. `NATIVE-STACK-LEDGERS.md`: **Leaf and subordinate bounds**, **Numeric function subtrees and retained arguments**, **Counter-reader and comparator closure**, **Additional owned numeric helpers**. They manipulate DATA descriptors and never invoke descriptor contents as code. |
| D | The formatter and data-selector trees 38c52 / 38d14 / 4e188 / 3a652 / 3a764 / 38dc8 / 38dfe; the 34b3e display stores; 35cf4 → 35ca0 / 18580 byte fill and copy; the 3a50a checksum/format leaf. `NATIVE-STACK-LEDGERS.md`: **Controller and continuation composition**, **Additional owned numeric helpers**; `SETTLEMENT-GUARD.md`: **Selected communication domain**. |
| K | The fixed services 3a1e / 3a32 / 3b84 / 3bb0 / 3be4 / 3c14 / 3cb0 / 3cd6 / 3c54 / 3d82 / 3d88, and 3d8e → 3a32 (TRAP on an unhandled post error). `IRQ-AUDIT.md`: **IRQ2 communication and service ledger**; `NATIVE-STACK-LEDGERS.md`: **Task7 serial reconfiguration**. Valid object IDs, zero optional kernel callbacks and intact saved contexts remain required. Creating, posting to or resuming a context schedules it; it does not run that task's receiver on these ancestors. |
| C | The whole 3fefa, 599ba, 5aa68(6), 59af8, 62348, 62422 and 6262c domains and the display domain a64c. `NATIVE-STACK-LEDGERS.md`: **Controller and continuation composition**, **Positive continuation child domains**; `SETTLEMENT-GUARD.md`: **Display callbacks**. These are their complete accepted bodies; adjacency to 61ad0, 64a7a or 48ca6 plays no part. |
| R | **Only** the selected router keys listed below, for each of the five protocols independently, including the normal tails. `SETTLEMENT-GUARD.md`: **Selected communication domain**; `IRQ-AUDIT.md`: **IRQ2 communication and service ledger**. No universal domain for the router 26a0e is imported. |
| M | The independently reviewed sections **Exact Task7 motor slice and calibration separation** and **Shared motor contracts for ordinary/bootstrap composition** of this document. In particular 5fb78 → 400 / 3a1e / 602fc / 68c4c; 5efc0 → 3a1e; 5cf7e and 3da1a have complete positive summaries with empty entry sets. No motor IRQ-phase body is silently imported as hook-free. |
| F | TRAP1 → the fatal halt at fc4; 105a/108a → the fresh-SP reset at 118a; and the hardware divide-by-zero (DIV0), which is in the same fresh-SP class. `NATIVE-STACK-LEDGERS.md`: **Fatal paths**; `SETTLEMENT-GUARD.md`: **IRQ, private E0 and exception separation**. Later bootstrap work is real, but 118a discards the old SP and uses 437ffe, so it is not a covered descendant that retains its parent. |

The independently reviewed Task7 and shared-motor analysis, and its passing 115-window
reproduction, are accepted inputs here and are not rerun. The AS and BSG DAGs and the
whole 43e62, 64a7a and 57e70 contracts are the other accepted inputs; none is re-audited
in this section.

## Nonmotor frontiers — complete synchronous topology

Every returning fallthrough after 3a1e is included, even where Ghidra declares the call
non-returning. Conditional paths are united without correlating mutable reads. The
statements below that an entry set is empty are deductions made by composing with the
exact domains above; they make no claim about temporary stack use by untouched native
code.

| Front; actual return boundary | Complete positive outgoing summary / exact selector matches |
|---|---|
| 5c866; RTS 5c8d2 / 5c8d6 | 400 scalar stores; 26a0e(F5) at 5c8b8; 53dd0(03,1) at 5c8c8. F5 and 03 are sealed below. No business callback. |
| 58300; RTS 58632 | 35b48 options 2f/0f/2a; 5495c(44,45), 547d4(44), 54a12(45), 548bc(45), 549c8(44), 3b77e/3a038, 400/44c and arithmetic; 57888 scalar, 523a4's accepted numeric tree and **only** router key 201; 58634 and 587d0 (below); the 1cda6/1e1aa scalar tests; 3b84 fixed queue events; 599ba, 3fefa, 34d22(F6), 5aa68(6). In particular 599ba lies **after** the guarded body's RTS at 599b8 and is not 589ba. |
| 58634; RTS 587be; the 587c0 wrapper, RTS 587ce | 63ad8, an inline clear of 4127e6 (RTS 63ade); 34e82 display; the 3a764 / 4e188 / 3a652 / 38d14 formatters, 549c8(47), 36948, 34b3e; **after** the delay at 58750: 3c14 receives, 3bb0 posts and the accepted whole continuation 62348. No received value is executed as code. 587c0 only calls 58634 and then tests DATA. |
| 587d0; RTS 58840 / 588d2 / 5896e | 548bc(47), 44c and 68c30; 53dd0(67) or (66) **only**. Both are already in `NATIVE-STACK-LEDGERS.md`, **Selected settlement accounting opcodes**. No fallthrough into the separate 58970 or into 589ba. |
| 42d2c; RTS 42e90 / 42e98 | 53dd0(50/51), 548bc(47), 68bcc, 1e1aa, the 105a fresh reset; 5bd3a (below); 5aaf0 scalar, the 4360 fixed sender, 3b84; 26a0e(90/31), 34d22(F6), 54912(47), 400; 3fefa, a64c; 35b48(2), 4ef34 and 4e412 → 4e452(1) mask stores. Router key 90 is matched below; 31 and F6 are accepted R. |
| 62b98; RTS 62ba6 | Only the conditional 62ba0 → 4e4e. The complete body of 4e4e, 4e4e … 5286, calls: 400/44c, 3a1e (returning), the 35f5a leaf, 3b84, the 5fc4 output/control sender, 34d22(F6), 3cd6 (Task5 RESUME), the 38ad0 DATA copy, 68bcc, 35b48 options 0/4/16; 53dd0(1e/1f/20); 5288 → router key 37; router key 37 directly, and 3a32. Posting queue 2 event 0f/1e or resuming Task5 is not a synchronous 589ba call. The four-way table at 50fa and its descendants are below. |
| 5636; RTS 5662 | 35b48(4), 36948 scalar; 46b4(18) → the 3dd4 hardware predicate / the 4278 MMIO transmitter / 3a1e; the 4360 fixed sender. 46b4 returns at 46f2, before the separate Task2 entry 46f4. |
| 52da; RTS 52ea / 530c / 53f6 / 53fe | 5bd3a; 35b48(4), 36948; the accepted whole continuation 62348; 4360; 53dd0(27); 5aaf0, 3b84; 34d22(F6), including the independently read byte 43cda9; 4a64e, an exact accepted display callback; 44c/400; the 404aa queue drain; the 8b98 option/counter predicate; 5fc4. Operation 27 expands to 4572e, whose **only** non-numeric child is 5bd3a. No raw financial-progress invariant is needed. |
| 64566; RTS 6488c | The 49f82/49f5e MMIO/output leaves; 4e3c2/4e37a (counter 43, output); 363c2 input, the 4fc64 input/MMIO/display test; 400/44c; 53dd0(93/52/81); 26a0e(36); 35b48(05/10); 3976c, the exact accepted typed change/format subtree; 3cb0 (Task5 SUSPEND); 3b84, 59af8, 34e82, 38d14, 3c14/3be4, 34b3e, 14d3e → 14c66 TX, 3bb0, the 4ef3e output leaf, 64d5a (the same TX/output contract); 6488e (below) or 4e2fc. The local loop changes DATA; it is not a function-call SCC. |
| 6488e; RTS 64a26 | 54912(43), 3a1e (**returning**) followed by 400/44c; the output-mask leaves 49f82, 4e51c(02), 4e3c2, 4e306, 4ee2c; 363c2/4fc64; 34e82, 38c52, 35b48(05), 549c8(43), 53dd0(52/93), 64d5a → 14d3e TX, 38d14/34b3e. The post-delay body is essential. It ends at 64a26, before the separate 64a28 and the covered controller 64a7a. |
| 582ec; RTS 582fe | 46b4(08) and 4360 only. |
| 42e9a; RTS 42eac | 46b4(0e) and 4360 only. |
| 42eae; RTS 42ed6 / 42f06 | 34d22(F6), 3fefa or 587c0 → 58634; 3b84 queue 4 / event 9 and queue 2 / event 1e. It ends at 42f06, before the patched wager root 42f08. |
| 4e51c(02); RTS 4e60a | Only 35b48(0c), then at most four six-byte immutable DATA records based at 6aeaa/6aeac/6aeaf. It clears bits in the output shadows 41002a … 41002d and 412426. There is no code-pointer call, and it does not write Task8's callback array at 41002e. The general operation 3's optional substitution of 6 is also DATA-only, but 02 suffices here. |

The other small scalar and output fronts above are exact, not umbrella labels. 57888
calls only 35b48(16) and then returns a Boolean. 4fc64 calls only 363c2(07) and performs
local DATA/MMIO tests and the 34c22 display transmission; the whole body of 34c22,
34c22 … 34d20, is an eight-byte MMIO/character-map loop with no child call. 49f82 only
sets output bits. 49f5e **includes** its debug alternative 49f6c → 39fb4(43e8c5) →
39f96(width 1), where 39f96 … 39fb2 is an inline nonzero-byte predicate with no calls
(Ghidra omits this branch as dead; the proof does not). 4e2fc, 4e306, 4ee2c and 4ef3e
only apply fixed masks. 8b98 reads MMIO, calls 35b48(14/00) and 548bc(47), then returns 0
or 1. 35f5a only sets 4127fc = 1. 5aaf0 changes two DATA state longs. Their normal RTSs
do not fall into adjacent controllers. 3d8e calls 3a32 and then TRAP1 on error, never a
callback.

## Selected additional accounting — not blanket 53dd0 coverage

53dd0's literal opcode is bounded to 0 … b4 at 53de6, and the signed-word table at 53dfa
selects the cases below. The native lock and unlock tails are the fixed K services;
argument cleanup joins at 547c8, followed by MOVEM/UNLK/RTS. The direct children of every
listed target end in N or D, in the explicit 5bd3a subtree, in fixed output or scalar
work, or at the exact additional accounting boundary 48. All the numeric underflow and
checksum-fatal alternatives remain F.

| Opcode | Case → target | Positive descendant class |
|---|---|---|
| 01 | 53f72 → 440bc | 54912(40); a conditional 3bd24 reset of the immutable 6a134. |
| 03 | 53f8a → 440f6 | 3bd24(6a13c, 0), 3bdca(6a144, value). |
| 1e | 540f4 → 44ce8 | The 34926 bill-code index; 35b48(3); the 3424e/4c92 DATA lookup; 400/44c, 3bdca/38dc8. Personality alternatives choose DATA descriptor indices at 6a946 … 6a9dc, not code. Requires the existing premise of a valid bill object and index, not a personality that stays stable between reads. |
| 1f | 540fe → 44f2e | 44c, 3bdca, 3c4e0, 38dfe, 38dc8, 68c8a/68bcc/68c30; 3fad8 → 3fa64 protected DATA journal updates. 3fad8 uses the 3a15c conversion, 400 and 38ad0 DATA copies; 3fa64 only advances protected ring indices through 400. 3fad8 returns at 3fbba, before 3fbbc's diagnostic hooks. |
| 20 | 54108 → 454f8 | 54912(48), 3bdca(6a174, bill-record amount); no code DATA. |
| 21/22/23/24/25/26 | 54112 → 4553c; 5411c → 455b4; 54126 → 45606; 54130 → 45650; 54148 → 456e4; 5413a → 4569a | Only 3bdca on fixed descriptors; 21/22 additionally 38dfe and 68bcc; 25/26 forward numeric amounts. No code-pointer call. |
| 27 | 54156 → 4572e | 3bdca, 44c; a conditional 5bd3a. The set 21 … 26 that 5bd3a selects is distinct from 27, so this is finite composition, not a recursive closure over generic dispatch. |
| 35 | 541ee → 45fca | 3c8da, 3c446, 3c7ec on immutable native counter descriptors; used only in cf54's alternate c8c6 path below. |
| 50 | 5434c → 473bc | 3c8da, 547d4(69/13f), 3acc0, 3c968, 3a142, 39f80, 479be, 3ac7c, 3bdca/3bd24/38dc8, 3bc18/3bea6/3bb62/3c600, 44c/68bcc. 479be calls only 3c4e0/3c600/3bb62/3bd24 on fixed descriptors; there is no second dispatcher. |
| 51 | 54356 → 476ce | 3c8da, 3a142, 39f80, 57888, 3bbae/3bb62/3c728, 3c4e0/3a038/38dc8; 479be; 3bdca, 420b6(48), 3bc18/3c600/547d4/3bd24/3acc0, 44c. All are typed numeric work except the separately matched 48. |
| 52/81/93 | 54360 → 46832; 54592 → 46dac; 54672 → 47d08 | Only 3bdca, on the immutable 6a3fc / 6a584 / 6a864 respectively (93 increments by 1). |
| 48 | 542e8 → 46fbc | 3bdca(6a21c / 6a124 / 6a12c), an optional 38dc8(43d752); no callback. |
| 8f | 54646 → 447b2 | 3bdca, 38dc8 on fixed descriptors; used only in cf54's alternate path, at 1de96. |

The already accepted accounting operations 00/04/10/2e/37/66/67/94 remain as documented;
no numeric peak is changed here.

### 5bd3a and local DATA selectors

- The full body of 5bd3a runs to 5bf16 (5bf18 is a **separate** function). It calls the
  5bfa6 cache update; 5aaf0; 53dd0 **only** with 21/22/23/24/25/26; the router **only** with
  keys 202/203/211; optionally 3a32; 3b84; 35cf4; 68c4c; 400/426/44c; 5c266(05). The state
  41d396 − 2 is checked to be 0 … 7 before 5bd66 (table 5bd6a). All the default states are
  included, not assumed impossible.
- 5bfa6 runs to 5c1ec and calls 5c266(01), 35cba/400/426, 68c4c and 3a304 (a width-5
  numeric conversion). Its eight-way switch at 5c034 (table 5c038) selects DATA moves and
  conversions, not code pointers.
- 5c266's switch at 5c28c (table 5c290) has five checked operation cases that return
  scalar state; only 68c4c and 35b48(22) can be called, and no controller callback. 5bc56
  is an inline seven-byte DATA snapshot 410494 … 41049a → 41d37e … 41d384, RTS 5bc9c, used
  by 612a. The six-case switch at 5bce0 belongs to the separate 5bc9e, not to 5bc56; it is
  included only in the geometric producer seal below.
- 553a calls 400, 34d22(F6), 8b98 and 5fc4. The **full** body of 55d4 includes its 5616
  alternative through the RTS at 5634 as well as the RTS at 5614; it uses
  35b48/36948/587c0/5aa68(06) or 3b84, not callbacks. The complete body of 612a, through
  633a, uses 5bc56, 35cba, 400, 38dfe and arithmetic, 3a29c (width 1) and 3a2ea (width 4).
- 35cba loops over DATA through 400. 3a304, 3a29c and 3a2ea specialize 3a23e to widths 5,
  1 and 4. 3b0fe → 3a470 converts fixed bytes to ASCII nibbles; 3a470 itself is an inline
  nibble loop, not a dispatcher that selects code. 69c42 is an inline SBCD loop. These are
  positive parser and conversion boundaries, not generic numeric labels.

## Selected routers — all five protocols

The router 26a0e dispatches a key separately for each of five protocols, labelled AS,
SAS, BSG, MIS and SDT as in `SETTLEMENT-GUARD.md`, **Selected communication domain**. The
tables are: AS, signed table 26474 with ascending byte keys at 2649c, count 20; SAS,
23d92 / 23dfe, count 54, WORD keys; BSG, 1b906 / 1b940, count 29; MIS, 18c1a / 18c48, count
23; SDT, 26ff6 / 27020, count 21. For ascending-key position i among n entries,
target = table + signed16(table + 2×(n−1−i)). A dash below means the protocol's actual
default: AS 269a8 (after the 35cf4 fill), SAS 2468a, BSG 1bf12, MIS 1905c, SDT 270b4. **Do
not** interchange the table and key-list addresses.

| Key | AS | SAS | BSG | MIS | SDT | Exact positive child summary |
|---|---|---|---|---|---|---|
| 37 | 26704 | 242b2 | 1bbf0 | 18caa | — | Already closed for **all** valid bill-status alternatives in the IRQ2 ledger: 258d6 bill-record DATA/format; 246ac conversion/send; BSG numeric/packet work; MIS's four checked status cases at 18cc2 (table 18cc6), including the 18970 reset/status sender; no business receiver. The task context changes the cost of the scheduling services, not the synchronous domain. |
| 59 | 264f2 | 24044 | — | — | — | AS 25e8a builds ASCII/configuration DATA through 26b0e/35b48/38b96/549c8/3b0fe/400 and accepted format/copy leaves; SAS 24044 … 240ec uses only options 05/06/1d, 400, inline timer/packet stores and the common tail 2468c. The adjacent 240ee is a **different** case and is not mistaken for this arm. |
| 6c | — | 245fc | — | — | — | Clears the scalar flag 41d9cf, then the normal packet/queue tail 2468c. |
| 6e | — | 24616 | — | — | — | An independent test of 41d851 selects packet DATA bytes, then the 2468c tail. |
| 90 | — | — | 1bd7e | — | — | Scalar flags, then the normal 1bf02 packet / 17b14 tail. |
| f5 | — | 2446e | — | — | — | Scalar notification byte and flag, normal packet tail. |
| 202/203/211 | — | 2448e / 244ba / 244a4 | — | — | — | Scalar packet/status stores, normal queue tail. No protocol business-command dispatch. |

The inherited literal keys 30/31/36/F6/201 are matched to `SETTLEMENT-GUARD.md`,
**Selected communication domain**, including the common tails and the independent
protocol selection. No key is generalized from another merely because they share a tail.
17b14 is allowed its general task ceiling of 88 when doing numeric work; the IRQ-active
figure of 50 is not substituted here. This section supplies topology, not new peaks.

## Full 5c8d8 continuation contract

5c8d8 first calls 5c866 (above) and the protected stores, then pushes its MODE-derived
argument and executes BSR 5c334 at 5c906. The local pre-call D = 4, so X = R(5c8d8)+8. The
argument is popped at 5c90a, **before** the later work. Both returning successors continue
to the actual RTS at 5ca4a:

| Site / retained local D before the JSR | Exact operation |
|---|---|
| 5c920 / 8 | 400(43c31e, 3). The first argument stays while the next operation is prepared. |
| 5c92c / 12 | 53dd0(01, 0), then 5c932 pops 12; not merely the dispatcher argument 8. |
| 5c944 / 4 | 26a0e(59). |
| 5c954, 5c974 / 8; 5c95c, 5c97c / 8 | 35b48(3a); 36352 called with the DATA address the option returned and the literal 8. 36352 is a loop that assembles bytes into an integer, with no child calls and no code pointer. |
| 5c98c / 8 | 44c scalar snapshot. |
| 5c9a8 / 8, then 5c9b2 / 12 | A 400 scalar marker followed by 26a0e(6e); cleanup of 12 at 5c9b8. |
| 5c9c0 / 5c9de / 4 | 35b48(39), read independently; local scalar alternatives. |
| 5ca08 / 5ca20 / 4 | 26a0e(6c/6e), then cleanup of 4. |
| 5ca32 / 5ca42 / 8 | 400 scalar marker / clear. |

So 5c906 is the **only** synchronous port to a covered descendant in the whole of 5c8d8,
and its all-27-arm 5c334 domain and prefix are composed above. The continuation after the
call does not re-enter 6268e, 5c334, 61172, 65128 or FIRST 589ba. The uniform direct
handler R56 gives X64; the positively proved 43c2 → 5c8d8 R64 gives X72. Do not reset the
retained error argument of 43c2 to the direct matrix row of 56.

## 62958 producer seal and retained ancestry

### Positive producer classes

The complete native computed-producer classes supplied by `SETTLEMENT-GUARD.md` are
reused, with the following local interval and value matches (not merely Ghidra xrefs):

1. Task6's a5d6 loads the immutable matrix base 77cc6; the native event index is
   zero-extended and multiplied by 120, the MODE contributes 4×MODE, and a5e0 calls the
   resulting pointer. The existing valid event and MODE domains apply independently at
   each read. A complete census of the long values gives **exactly** 77e06/77e0a → cf54
   (event 3, decimal MODE 20/21). No matrix pointer enters 62958 or the interior of either
   body. The bounded 17-entry local signed-word switch at aaaa likewise selects neither
   body. Distinguish that switch from the secondary callback ad72: ad72 loads 77db6+4×MODE,
   so MODE 20/21 selects the **same** 77e06/77e0a → cf54 pointers. Its pre-call residency
   of 52 plus return 4 gives R(cf54) = 56; it is a covered producer, not an excluded one.
2. cf54 is the only ordinary immutable source of 62958: cf72 is JSR 62958. A whole-ROM scan
   of aligned absolute and PC-relative JSR/JMP and of Bcc/BSR/DBcc candidates into
   [cf54,cf8c) or [62958,629b0) finds no other external instruction edge. The actual
   interior branches are the local cf62, 62960, 6296a and 62974. The apparent branch 6028
   at 62980 is the **low word** of JSR 36028, not an instruction edge.
3. A census of longs and of low-24-bit aliases into those ranges: cf74 is cf72's operand,
   and 77e06/77e0a are the matrix pointers. The raw 6100cf72 at 21284 is BSR 1e1f8, and the
   6100cf5c at 2aa4a is BSR 279a8; neither produces an absolute or interior pointer. No
   other alias candidate remains.
4. Every native signed16 switch producer that could geometrically reach either interval
   was decoded with its actual bounded index domain, and **none** selects either interval.
   The 36 relevant JMP locations and counts are: 50fa/4, 56c8/8, 5d34/12, aaaa/17, b134/16,
   c456/16, dac4/4, fb10/11, fcbe/15, 11730/6, 118e2/12, 1235a/21, 12522/10, 131fa/6,
   14a9a/8, 14aea/6, 14b7e/6, 14e76/6, 14f74/6, 5a96a/5, 5b948/4, 5bce0/6, 5bd66/8,
   5c034/8, 5c28c/5, 5c34e/27, 5d9c6/10, 60fe6/13, 629cc/30, 62e38/6, 63906/4, 63a94/4,
   6610a/8, 6632c/7, 67b4e/13, 67ee4/4. (The first 19 can geometrically reach cf54; the
   remaining 17 can reach 62958.) Each table begins at JMP+4, and
   target = table + signed16(table + 2×index). Tables further away cannot reach either
   interval with a signed 16-bit offset; the MIS selector at 18cc6, for example, is outside
   both reach intervals. The additional row 14f74/14f78 uses the checked indices 0 … 5
   (compare at 14f64, WORD load at 14f70), with targets 14f84 / 14fa0 / 14fba / 14fd4 / 14fee
   / 15008; none enters either interval. The original and candidate bytes [14f64,15024) were
   compared and are identical.
5. The accepted complete producer domains of `SETTLEMENT-GUARD.md`, **Producer seal**,
   supply the remaining classes: the reset and IRQ vectors and the 18 native task-entry
   literals; Task8's compact 20-slot registry (cf8c/d252); serial callbacks and ring and
   framing producers; extended services and the zero kernel callbacks; the a64c display
   table; the progressive slots; and the action arrays 69de8/6aa3e with the immutable
   controller records 69e30/6aaa0 and the DATA 69e24/6aa8e. Their exact tuple target sets
   contain neither interval. DATA getters and formatters do not reinterpret values as
   callable code. Ordinary call and return destinations require intact contexts. No claim
   is made about a foreign invoker or a corrupt pointer.

This seals the ancestry of 62958 to the Task6 matrix source cf54 under the **same**
positive premises about native producers and valid state, rather than from missing direct
references alone. No Task7 or bootstrap pointer selects cf54 or 62958; the bootstrap's
existing direct ingress 1380:13e6 → 6268e remains a **separate**, already owned ingress.

### Actual bytes / local SP

cf54 clears a flag and runs the 36896 scalar test; at cf72, JSR 62958 has D = 0. Its
alternate path, the BSR c8c6 at cf64, is separate (below) and does not fall into 62958.
62958 has no LINK or MOVEM. The optional 62976 … 6298c uses the 36028 DATA copy and the
exact M domain of 5fb78. 62992 is LEA 12(SP),SP, which discharges **all** optional
arguments; 62996 is 6100fcf6 (BSR 6268e), with D = 0. 6299a … 629aa posts queue 4 / event
1, and the RTS is at 629ae. It does not chain into the adjacent 629b0.

| Edge | Complete retained residency / result |
|---|---:|
| Task6 direct matrix a5e0 or secondary ad72 → cf54 | R = 36 direct; R = 56 secondary (aa6a entry 40 + saved 12 + return 4) |
| cf72 JSR 62958 | R+4 → 60, conservatively |
| 62996 BSR 6268e, **after** the optional 12-byte cleanup | R+4 → Y64 |
| 6268e → 5c334 at 626ea / 628d6 | Y+8 → X72 |

Complete local SP worklists: cf54, 13 states, local maximum 4; 62958, 19 states, local
maximum 12; 5c8d8, 94 states, local maximum 12; 5c866, 28 states, local maximum 12. The
call offsets above are the same on **both** branches, and the largest optional
temporaries are not retained at 62996. The maxima are raw local bookkeeping, not peaks of
whole subtrees.

### cf54 alternate c8c6 path — no new covered port

cf64 branches to c8c6, whose raw body runs through ca12 (including the Task8 creation at
c9f8; Ghidra has no function defined there). Its complete outgoing union is: 44c/400;
the 5044e/50f16 MMIO leaves; 3b84; 34d22(F6); 34e40 → 480/49e (interrupt-mask restore)
plus DATA clears; the cf8c registry installation; 53dd0(35); 1de96; a64c; and 3c54(Task8,
entry ca14). Task8's ca14 is **created and scheduled**, not called synchronously. The
original registry invocation at ca98 belongs to that separately restored task context, so
no invoking callback survives on cf54's ancestors.

cf8c calls only 35b48(05/06), the 18580 DATA fills, the d252 installer, 36896/1e1aa and the
3f3c4 scalar parser. d252 writes a supplied constant callback pointer and its ID to the
next compact slot if and only if 41fc26 < 20, then returns; it **never** calls that
pointer. Its complete set of supplied constants is in `SETTLEMENT-GUARD.md`, **RAM and
argument producers**, and no member enters either sealed interval. Only a subset is
installed before the 20-slot cap, and mutable option selection cannot invent an extra
target.

The body of 3f3c4, 3f3c4 … 3f4dc, calls **only** 3f308 and the fixed numeric wrappers
68f1c / 68e7e / 68e3e / 68e68 / 68e54 / 68e94. 3f308 parses at most 5 DATA characters from
the 10d42a records and has no child call. The wrapper targets are 68a6c / 68daa / 6877c /
6884c / 68938 / 68778; their arithmetic branches and calls stay within 68778 … 68cb4 plus
68daa. The register JMPs are ABI **returns**, not callback loads: 68e62 pops the return
address into A0, discards 8 argument bytes and executes JMP (A0); 68f22 copies the return
address from (SP) into A0, replaces that slot with the result and executes JMP (A0). The
numeric cores 6896a/689d4 likewise pop their own return. This is positive return
provenance under the intact-context premise, not an assumption that every indirect jump
is safe.

1de96 includes **all** post-delay continuations through 1e0fc (Ghidra's decompilation
truncates it): 3a116 numeric DATA; 38dfe/68bcc/38c52; 34b3e/3a1e; 53dd0(8f/2e/10);
3d82/3d88; 44c/400; and queue 2 / event 0f. The last is asynchronous. No financial or
coherence predicate and no assumption about elapsed time before a return is needed for the
empty synchronous covered-entry set. a64c and the selected accounting operations 35/8f are
the exact accepted or expanded boundaries above.

## Composition-only additional requested matches

- 6268e's literal progressive call 1d48a(op 04) matches `NATIVE-STACK-LEDGERS.md`,
  **Controller and continuation composition**: 1c438's selected 04 arm → 1c9b0 → 426 and
  the leaf 1ce06; the alternative fixed numeric and 17b14 queue branches remain covered. It
  is not arbitrary progressive-task dispatch.
- 6268e's 53dd0(01/03) calls match the exact 01/03 rows above, not the earlier opcode 04 by
  analogy.
- 5c334's 62422 ports for arguments 07/0d match the **whole** of 62422: router key 30 (all
  five protocols) plus progressive operation 04 and the normal tails, per
  `NATIVE-STACK-LEDGERS.md`, **Positive continuation child domains**. No restricted premise
  about the ordinary state is imported.
- The **full** continuation of 5c8d8 and the 62958 root are supplied above. The whole
  43e62, 64a7a and 57e70 contracts and the 27-arm composition of 5c334 are kept above. M
  supplies 5cf7e, 5efc0 and 3da1a; no duplicate motor or IRQ audit was done.

## Task7 complete dispatcher and shared motor domains

### Task7-specific delegated matches

| ID | Exact already-sealed boundary / stable locator |
|---|---|
| K | The fixed native services 3a14 / 3a1e / 3a32 / 3a52 / 3a7e / 3b84 / 3d82 / 3d88 and suspend/resume 3cb0/3cd6: `IRQ-AUDIT.md`, **IRQ2 communication and service ledger**, **IRQ4/5 called targets**, **Implemented serial drain and offline gate**; `NATIVE-STACK-LEDGERS.md`, **Task7 serial reconfiguration**. Valid kernel objects and IDs, balanced contexts and zero optional kernel callbacks remain required. Scheduling resumes a saved context; it does not call a business receiver on these ancestors. |
| N | The 400/426/44c protected leaves; 35b48 → 35aec valid immutable option descriptors; the 549c8/548bc counter readers and comparators; 3a15c/3b678/68bcc/68c4c; 3af58: `NATIVE-STACK-LEDGERS.md`, **Leaf and subordinate bounds**, **Counter-reader and comparator closure**, **Additional owned numeric helpers**. This is the positive validated numeric tree, not an inference from a numeric-looking name. |
| D | The 38c52/4e128 formatter trees → arithmetic, copy, 3a50a and 34b3e leaves: `NATIVE-STACK-LEDGERS.md`, **Controller and continuation composition**. 3a50a also has explicit leaf evidence in **Fatal paths**. 35cf4 → 35ca0 byte fill and copy: `SETTLEMENT-GUARD.md`, **Selected communication domain**. Small wrappers and leaves inspected directly are listed below. |
| U | 16488's driver initialization 13660 / 139f2 / 13eda / 14dc2 and the six hardware setters of 14a7e: `NATIVE-STACK-LEDGERS.md`, **Task7 serial reconfiguration**. 14b48's six-port TX switch 14374 / 14396 / 143b8 / 143da / 1442e / 14450: `IRQ-AUDIT.md`, **Returning UART domains**. These are ring, MMIO and driver helpers, not 19732/19b16 or business dispatch. |
| B | The 34492 numeric bill-value checker and 36948: `IRQ-AUDIT.md`, **IRQ2 communication and service ledger** (3440e/34492, all valid bill-status alternatives), and `NATIVE-STACK-LEDGERS.md`, **Owned scalar and selector boundaries**. The complete direct union of 3440e itself is also inspected below; no general bill-task root is imported. |
| R113 | **Only** the 26a0e key 113 from 42ee:433a. `SETTLEMENT-GUARD.md`, **Selected communication domain**: SAS 23f1a scalar packet stores; the AS/BSG/MIS/SDT defaults (AS fills, then sends nothing). The normal queue and TX tails are sealed there. |
| RF6 | **Only** 34d22 → 26a0e key F6. `NATIVE-STACK-LEDGERS.md`, **Owned scalar and selector boundaries**, and `IRQ-AUDIT.md`, **IRQ2 communication and service ledger**: AS 26918 → 549c8(47); the other protocols take their default. No arbitrary router key is admitted. |
| M | `IRQ-AUDIT.md`, **IRQ6 all-phase called targets**, supplies the already closed RNG 5fe2a → 380d6, the arithmetic and the motor-helper domains. The motor slice that Task7 uses is exposed separately below, so that the broader IRQ graph's calibration entries are **not** silently imported into a hook-free summary. |
| F | TRAP1 → the fc4 fatal halt, as distinct from the fresh-SP divide-by-zero (DIV0) path: `NATIVE-STACK-LEDGERS.md`, **Fatal paths**, and `SETTLEMENT-GUARD.md`, **IRQ, private E0 and exception separation**. No bootstrap or financial-controller invocation that retains an ancestor. |

### Complete root direct-call unions

The Task7 entry b080 saves D2 (4 bytes). Its 197-state local walk resolves the 16 slots
of the switch at b134 (table b138), including the loop at b088. The two positive roots are
5ab76 (Test10) and c0fc (configuration). The 13 roots below exhaust the other primary
targets, with 41170/41152 additionally called in the 49f9e arm (`IRQ-AUDIT.md`, **IRQ4/5
called targets**). The pre-calls at b18e, b264, b280, b29c and b33e have D = 4. The one at
b30a has D = 12, because 8 bytes of the original 400 arguments survive b2f4/b2f6 until
b324; with return 4 and the 20 bytes that 5ab76 saves, the hook site is at 36. The
diagnostic 142 and configuration 158 costs, and the motion costs 98/96/70 with IRQs open,
remain in `NATIVE-STACK-LEDGERS.md`; the deeper masked motion code keeps its separately
qualified terminal-IRQ treatment. 57e88 is in the distinct routine 57e70, and 5c802
belongs to the 5c334 arm for argument 1a (hex); neither is a Task7 root.

"Local states" counts reachable instruction addresses, not elapsed time or stack states.
All 13 walks had zero unresolved local successors. Every outgoing target is expanded
below or appears in the exact delegated-domain table above. The RTS locations rule out
mistakes from adjacency.

| Root | Local states; reachable RTS | Complete direct-call union |
|---|---|---|
| 5152a | 436; 51d12 | 34b3e, 35b48, 35cf4, 3649e, 36540, 366a8, 38c52, 3a714, 51d14, 51d2e |
| 57b78 | 214; 57e64 | 3a1e, 34b3e, 36540, 57ad8, 57b38 |
| c864 | 34; c8c4 | 3a1e, c5ba, c61e, c6a8, c6e8, c746, 36540, 4e3c2 |
| 5ca4c | 145; 5cc84 | 3a1e, 34b3e, 36028, 364f2, 369cc, 38c52, 51d14, 5d368, 5efc0, 5fb78, 60442 |
| 62114 | 140; 62346 | 3a1e, 34b3e, 35cf4, 36028, 36390, 364f2, 38c52, 5f2da, 5fb78 |
| 5cc86 | 191; 5cf62 | 34b3e, 35cf4, 36390, 364f2, 36540, 38c52, 3af58, 51d14 |
| 132ee | 253; 1365e | 131b4, 149f4, 14a22, 14b48, 16488, 165a6, 16614, 34b3e, 364f2, 36540 |
| 49f9e | 214; 4a260 | 364f2, 4a2a8, 4a346, 4a3bc |
| be16 | 178; c0d4 | c0d6, 34e5a, 35cf4, 36390, 364f2, 36540, 38c52 |
| 6833c | 235; 6873a / 6876c | 1dd6, 3a1e, 3b84, 1509e, 150c6, 34b3e, 35cf4, 36540, 38be0, 3a804, 51d2e, 68290 |
| c1f8 | 135; c43a | 3a1e, 3b84, 34b3e, 35cf4, 364f2, 36540, 369cc, 36a48, 38be0, 38c52, 38fd4, 3ba8c, 3de62 |
| b53c | 357; bba6 / bbb8 | 400, 44c, 3a1e, 3b84, 42ee, 4360, 26be4, 34b3e, 35b48, 35cf4, 36540, 369cc, 3811e, 38c52, 4e37a, 4e3c2 |
| c4d0 | 59; c5b8 | 34b3e, 364f2, 38c52, 48d4e, 48d7e |

All 13 rows therefore have an **empty set of synchronous covered exits**, by the positive
frontier compositions below. The direct unions alone are not claimed to be sufficient.

## Common waits, input, display and numeric wrapper frontier

| Inspected owner(s) | Complete next frontier / positive operation |
|---|---|
| 51d14 | 35cf4 only; RTS 51d2c. It is a four-byte fill wrapper, **not** the separate numeric/controller routine 51da2. |
| 51d2e | 36390, 364f2, 369cc, 36a48; RTS 51d50 / 51d86 / 51da0. |
| 364f2 / 36540 | 3a32 + 3a52 / 3a1e + 3a32 + 3a7e. Mailbox receive, inquiry and repost, not a call into code from a returned message. K. |
| 369cc | Only 3a1e plus MMIO/input polling. Every fallthrough after a delay is included, through 36a46. |
| 36390 … 363c2 / 36a48 … 36a7a | Only 3a1e; each entry branches into a shared local polling tail. The backward branches 363b4/363be and 36a6c/36a76 are included, not treated as unknown cross-function edges. |
| 3649e / 3633a | Character-to-display scalar leaf / one-shot flag leaf. |
| 366a8 | 3633a, 36896, 36722, 34b3e; returns a DATA pointer. |
| 36722 | 36896, 34e82, 34b3e. The full alternate error-display loop is included; it neither resets the SP nor dispatches a task. |
| 34e82 | 35cf4, 363c2, 374ce, 37498. The last three are an indexed byte getter, a scalar predicate and data-pointer writes, with no child call. So the error-display branch closes, not merely the ordinary return. |
| 34e5a / 3de62 / c0d6 | 35cf4 / 35cf4 / two 34b3e calls; c0d6 returns at c0fa. |
| 38be0 / 3811e / 35d60 | Nibble-formatting / NUL-terminated byte-copy / fixed byte-copy leaves; no callable pointer. |
| 3a714 / 3a804 / 3a1a2 | 3a50a / 3a50a / 3a15c only; D/N. |
| 3ba8c | 3b920 and 3a50a; no code dispatched by a selector. |
| 3b920 | 3a15c, 3b8f0, 3b63e, 3b678. 3b8f0 calls **only** 69ba6 (an ABCD loop leaf, RTS 69bcc); 3b63e calls **only** 400. The rest ends at exact N leaves. No new numeric peak is computed. |
| 36028 | The 363d4 DATA-copy leaf, and 363f8 → 68c4c only; the local output arrays are DATA. |
| 4e37a / 4e3c2 | 548bc(counter 43) plus inline output-bit writes; N. |
| 26be4 (b53c:b612) | Called with the literal index 0b and flag 0; the existing `SETTLEMENT-GUARD.md`, **Selected communication domain**, table row "Shared selected helper domain", entry 26b8e/26be4: a DATA lookup in 74112 / 7418e / 7420a / 74286, an optional 400, no callable pointer. |

The 35b48 options selected in these roots and frontiers are 3, 4, 0c and 38 (all
hexadecimal), not arbitrary 53dd0 operations, and the uniform valid-descriptor option
closure N applies to them. 60ae uses exactly the 549c8 counter IDs `f3,fc,73,6c,75,6f,66`;
4e37a and 4e3c2 use 548bc(43). No already closed numeric descendant was reopened for a new
numeric bound.

## Root-specific positive frontiers

### 57b78: output testing, not 57e70

57ad8 → 35d60 and 4e452(3); 57b38 → 35d60. 4e452 calls **only** 35b48(0c) and then uses the
immutable six-byte DATA records based at 6aeaa/6aeac/6aeaf and the alternate 6af3c scan to
OR output masks. Its possible substitution of operation 3 by 6 changes a DATA row, not
code. This closes both 57b38 calls, including the one at 57dd2 after a returning delay.
57b78 restores and returns at 57e64, and its walk has no edge into 57e70 or 57e88. The old
attribution to the adjacent function is positively excluded by this completed local body,
not by xrefs.

### c864 and b53c: fixed coin/test services and selected notification

| Owner | Complete next frontier |
|---|---|
| c5ba | 35cf4, 34b3e, 3a1e. |
| c61e | 4e37a, 369cc, 4e3c2. |
| c6a8 | Inline state selection returning 0, 1 or 2. |
| c6e8 | Selects the string DATA 73b82 / 73b88 / 6948c, then 3811e and 34b3e. |
| c746 | param2 → c656, param1 → c684, otherwise return. |
| c656 | 3b84(queue 2, event 1e) and 3a1e; no inline event handler. |
| c684 | c770 and 3a1e. |
| c770 | 400, 3a1e, 42ee, 34b3e, 38c52; returns at c862. |
| 42ee | The 3dd4 predicate leaf, the 4278 inline MMIO transmitter, 400, 26a0e(113), 3a32; RTS 435e. |
| 4360 | 3dd4, 4278 and 3a32 only; RTS 4398. |

At 42ee:4336 the literal 0113 is pushed and 433a calls 26a0e: stop at R113, not at an
unspecified router domain. Every return of 4278 (4296 / 42aa / 42be / 42ec) follows only
MMIO and polling. 42ee does **not** fall into 4360 or 43c2, and 4360 does **not** fall
through 439a/43c2. Accordingly, b53c:baba and c770's call of 42ee do not acquire the
Task6-only path 43c2 → 5c8d8 → 5c334. The accepted Task6 row R52 → R64 → R72 is
unaffected.

### 49f9e: complete post-delay bill/display frontier

| Owner | Complete next frontier |
|---|---|
| 4a2a8 | 51d14, 3a1a2, 34b3e, 3a1e. |
| 4a346 | 51d14, 38c52, 34b3e. |
| 4a3bc, through RTS 4a64c | 3a1e, 4c92, 60ae, 3424e, 3440e, 34926, 34b3e, 35b48, 4e128, 51d14. Crucially, 4a5aa → 3440e occurs **after** the delays and is included. |
| 60ae | The seven exact 549c8 IDs listed above; numeric sum and predicate only. |
| 34926 | An inline arithmetic leaf mapping a bill code to an index. |
| 4c92 / 3424e | All 16 bounded personality arms plus the defaults return indexed DATA; no child calls. The exact table rows are below. No stability between these independent selector reads is required. |
| 3440e | 3424e, 34492, 34926, 35b48(3/4), 36948; inspected through 34490. 34492 is the already closed numeric value checker B, not a controller. |

No 53dd0 dispatcher, bill receiver, financial-entry callback or generic router appears in
this frontier. B's positive numeric subtree is reused, rather than concluding that there
is no covered entry merely from the helper name 3440e.

### 132ee: serial test vs scheduling other owners

131b4 → 34b3e, 364f2, 16614 or 3b84(queue 4, event 2); its six-way local display switch is
fully decoded below. 149f4 is a ring-count DATA leaf. 14a22 → 14f52, whose six arms only
advance and wrap the six ring pointers. 14b48 uses the exact six-port TX destinations of U.

165a6 → the 68c8a multiply leaf and 3cb0 (suspend); 16614 → 68c8a and 3cd6 (resume). Each
loops over seven registry entries, and their error alternatives TRAP1. 16488 → 13660,
139f2, 13eda, 14dc2 and 14a7e: these exact initialization and setter domains are already
closed in U. The rate selector and port selector of 14a7e are reproduced below, with only
six hardware setters. Nothing runs the suspended or resumed parent function inline.
`IRQ-AUDIT.md`, **Implemented serial drain and offline gate**, supplies the positive proof
of 132ee/131b4 resume ownership; this root reaches no configuration call to c1a8.

### 6833c: full RTC/MMIO diagnostic continuation

After the first delay, the raw body continues through 6876c, not 6847c. The
additional calls of 1dd6 at 68572/68652 and of 68290 at 686b2 are included.

1dd6 → 400, 2374, 23be, 3d82, 3d88; 2374 and 23be are inline RTC bit and MMIO leaves (RTS
23bc / 23de). 1509e → 15022/15060 → the 1472c bit-reversal leaf, and 400; 150c6 → 400 with
MMIO reads. 68290 is a pure ROM-memory comparison loop through 6833a. Neither it nor 1dd6
invokes the boot routine 154e, the clear 3836e, 3756c or calibration. 3a804 ends at D's
3a50a leaf. All the late error, display and loop alternatives return to these same closed
frontiers.

### c4d0: command publication/wait, not controller invocation

48d4e → 558ec and 48d7e → 559de, under independent protocol predicates. The full body of
558ec, to 559dc, and the full body of 559de, to 55bbc, call **only** 3a1e, 3d82, 3d88 and
364f2. They write display and command RAM and wait for an independently changing status;
no call to 5391c, 54c70, 487a6, 489ae or 48ada and no function-pointer call exists in those
completed bodies. The indexed longs at 6ac30 that 559de uses are timeout DATA passed to
364f2, not callback pointers. The valid-native-selector premise applies at that read; this
call-domain result requires no value to stay stable across waits.

## Exact Task7 motor slice and calibration separation

This exposes the finite task-side slice, rather than calling the entire IRQ6 domain
hook-free.

| Owner; complete native body end | Complete synchronous outgoing union |
|---|---|
| 5fb78; 5fcb0 | 400, 3a1e, 602fc, 68c4c |
| 5f2da; 5f386 | 3a1e, 5fed8 |
| 5efc0; 5f020 | 3a1e |
| 60442; 604f8 | 400, 426, 5fed8 |
| 5d368; 5d38e / 5d3ce / 5d6e8 | 44c, 3a14, 3b84, 34d22, 5eba8, 5fcda, 5fe2a, 5fed8, 5ff2c, 68c4c |
| 5fcda; 5fe28 | 602fc, 68bcc |
| 5fe2a; 5fed6 | 400, 380d6 (the exact RNG subtree already sealed by M) |
| 602fc; 60304 / 6030e | Inline arithmetic leaf |
| 5eba8; 5ec76 | Inline indexed phase/DATA and MMIO writes; no calls |
| 5fed8; 5ff1e / 5ff2a | Inline timer/MMIO leaf; no calls |
| 5ff2c; 5ff68 / 5ff74 | Inline timer/MMIO leaf; no calls |

5d368:5d3ae → 34d22(6) is included **even though** Ghidra removes its debug branch. It
publishes a mode and invokes **only** router key F6, which RF6 closes. The other debug
branch posts queue 2 / event 15 at 5d3c0. The ordinary branch configures motion and
returns at 5d6e8, immediately **before** the distinct IRQ phase function 5d6ea.

The task-side calls from 5ca4c and 62114 therefore end in this table; they do not
synchronously enter 5d6ea or 5e654. The calibration patch sites 5d790/5d798 and
5df1a … 5df28 belong to that separate IRQ phase body, and 1906 is boot-only. Publishing
timer state may cause a later IRQ, but it is not a BSR or JSR into that body. No
Task7-native calibration prefix is added; the inherited IRQ6 accounting remains necessary.
The raw body of 5cc86 ends at 5cf62, before the separate initialization functions
5cf64/5cf7e, so no calibration path is inferred from adjacency.

## Every newly encountered local signed-table selector

Formula: `target=base+signed16(ROM[base+2*index])`. The counts and ranges come from the
native compares before each JMP. Every case entry was re-decoded from its own address, so
`objdump`'s linear reading of inline table words as instructions cannot mislead the walk.

| JMP; table; complete index domain | Targets in order / consequence |
|---|---|
| 4cb4; 4cb8; 0 … 15 from unsigned(byte 43e8ce − 10) | 4d56, 4cd8, 4ce6, 4cf4, 4d02, 4d0e, 4d1a, 4d26, 4d32, 4d3e, 4d4a, 4d7a, 4d7a, 4d7a, 4d62, 4d6e. Default 4d7a; all merge at the DATA load 4d84 and RTS 4d8e. |
| 34274; 34278; 0 … 15 from unsigned(byte 43e8ce − 10) | 3439c, 34298, 342b0, 342c8, 342e0, 342f8, 34310, 34328, 34340, 34358, 34370, 34386, 343e8, 343e8, 343bc, 343d2. Default 343e8; DATA load and multiply, then RTS 3440c. |
| 131fa; 131fe; 0 … 5 | 1320a, 1321c, 13226, 13238, 13242, 13254. Only inline display-byte assignments, common 13264; default 13264. |
| 14a9a; 14a9e; 0 … 7 from (rate − 1) | 14aae, 14ab4, 14aba, 14ac0, 14ac6, 14ace, 14aca, 14ad2. Only baud-byte constants; default 14ad4. |
| 14aea; 14aee; port 0 … 5 | 14afa, 14b06, 14b12, 14b1e, 14b2a, 14b36 → 144a4, 144c0, 144dc, 144f8, 14514, 14530 respectively. Default 14b40; the exact U setters. |
| 14f74; 14f78; port 0 … 5 | 14f84, 14fa0, 14fba, 14fd4, 14fee, 15008. Ring-pointer comparisons and wrap writes only; default 15020. |

The new root and frontier graph has no unresolved computed-code domain. No shortcut based
on function SCCs or missing xrefs was used. The existing 14b48 TX dispatch and the 16-entry
dispatcher of the root b080 keep their already accepted seals rather than being
rediscovered here.

### Shared motor contracts for ordinary/bootstrap composition

These are contracts on relative entry domains, not new Task7 ancestry. 5efc0 is reached
from 5ca4c at 5cab0/5cb24. 5cf7e and 3da1a were **not** reached in the 13-root closure
above; they were added explicitly for the ordinary 6268e and all-27-arm 5c334
composition. Their callers and prefixes are composed by the all-27-arm and 6268e tables
above. All three contracts have **empty synchronous covered-entry sets** under the same
inherited premises, so there is no new entry prefix to charge below them.

| Shared frontier | Complete positive synchronous summary |
|---|---|
| 5efc0 | A 27-instruction walk, only 3a1e, RTS 5f020; may update motor-state DATA. Its waits do not invoke a motor phase body synchronously. |
| 5cf7e | A 235-instruction walk through the RTSs 5d0bc / 5d316 / 5d366. Complete outgoing union: 400, 3a1e, 369cc, 5ee00, 5fed8, 5ff76. The immutable timing/phase tables 6b73c / 6b7a4 / 6b890 / 6b858 are DATA; there is no computed code target. 5ee00 is an MMIO leaf; 5fed8 is the exact timer leaf above; K and N close the remaining scalar and wait edges. |
| 5cf7e fault alternative | 5d0b0 → 5ff76 receives the literal (−8, 0), so it is not discarded by assuming a valid motor initialization or history. Even allowing, conservatively, the **full** already sealed callable domain of 5ff76 gives only 400, 3b84, 6016e and 60254. 6016e → 400/3b04a fixed motor counters; 60254 is an inline phase/DATA-state writer, RTS 602fa. Stop at `IRQ-AUDIT.md`, **IRQ6 all-phase called targets** and **Computed-memory paths with positive destination evidence**, for the exact 6016e → 3b04a destination class, not the whole IRQ phase dispatcher. No call to 5e654 or 5d6ea is introduced. |
| 3da1a | A 34-instruction walk through RTS 3daa8. Complete outgoing union: 400, 44c, 3b84, 26a0e, 34d22, 35b48, 4e412, 4ef34, 53dd0. Match **only** 53dd0(0b), 35b48(2), 34d22(0 or 10) → router key F6, 26a0e(31), and queue 4 / event 0a. The exact selected domains are below; no generic router or accounting claim is made. |

The **return** from 5cf7e's 300-tick delay is essential: 5d302 → 3a1e, then 5d30a/5d30c
publish state 20, then RTS 5d316. This schedules later IRQ calibration activity, not a
same-stack call into 5df1a. The alternate path calls 5fed8 at 5d350, publishes 80 and
returns at 5d366. No timing bound and no repair of calibration history is assumed.

The exact additional delegates of 3da1a:

- At 3da28 the literal 0b selects the signed offset at `53dfa+2*0b` → 53ff2; 53ff4 calls
  44456, then 53ffc branches to the accepted 547c8 unlock/return tail. 44456:44466 calls
  3bea6 with the immutable descriptor 6a16c and a null output; 44478 calls
  3c090(6a19c, 6a1cc); 44480 returns. These exact numeric subtrees are already positively
  closed by `NATIVE-STACK-LEDGERS.md`, **Numeric function subtrees and retained
  arguments**. Opcode 0b was **not** among the published rows of selected settlement
  opcodes; this narrow wrapper match supplies the missing selected boundary without
  auditing its numeric descendants or generalizing 53dd0.
- The literal 31 at 3da96 has the complete five-protocol domain of `SETTLEMENT-GUARD.md`,
  **Selected communication domain**: AS 264c6; SAS 24306 → 27e80 / 2b8e2 / 2b898(2) and
  option 21; BSG 1b9b0 fixed counters and format; MIS and SDT default. The selected common
  tails are numeric, queue and TX work only. Both 34d22 call sites keep RF6 whatever the
  changing MODE.
- 4ef34, 4ef0c and 4ef20 are inline output-bit leaves (RTS 4ef3c / 4ef14 / 4ef28). The
  complete union of 4e412 is 4e452(1), 4ef0c, 4ef20; 4e452 is already closed above as option
  0c plus DATA-selected mask stores. No business callback runs inline.

## Bootstrap and earlier qualifications

| Reset/boot hook | Non-IRQ from 438000 | With I1904 |
|---|---:|---:|
| 154e:155c boot validation | 648 | 2552 |
| 154e:1730 → 3756c:37dde restore | 428 | 2332 |
| 154e:1906 final restore | 352 | 2256 |
| b9e → 3825a → 3836e:38386 clear begin | 508 | 2412 |
| same → 38526 clear complete | 380 | 2284 |

The accepted boot proof includes the 25b0 prelude and the C startup fault; calibration
refusal is accounted separately, inside the fatal budget. No permission for an unexpected
IRQ before initialization is introduced.

Late bootstrap: bca → 154e is entered at 6; the native F12 and the 1dc2 return of 4 bring
1380 to an entry of 22; 13e6 passes no arguments, giving Y26. The complete alternatives up
to the first guard are:

- direct 62822 → 57e70: entry 30 → B34, pre-check P38 / normal 230;
- 626ea → 5c334: X34 → 5c73c:57e70 entry 46 → B50, pre-check P54 / normal 246;
- 5c334 → 43e62 entry 46 → 3d294 entry 50 → B58;
- 5c334 → 43e62 → 64a7a entry 50 → 63cd0 entry 70 → P110 / normal 302, loader L130 /
  general 532.

Every remaining bootstrap branch of 6268e and 5c334 positively has no additional covered
entry, so these rows exhaust the bootstrap's initial normal, loader and first-guard
alternatives. The shared normal 302 and general loader 532 stay below the early boot 648.
The complete bootstrap first 58 replaces the historical loose 226; it applies to the first
entry only. Later admitted bootstrap work still uses 6288+1904 = 8192, not the finite early
bound of 648.

The other patch entrances are accounted explicitly:

- 9bc `ext_preserve_sram` and 9d8 `ext_reset_runtime` are stackless JMP continuations,
  adding residency 0.
- `commission_reset` at 800 and `commission_gate` at a58 use the earlier qualified private
  4096-byte arena and pre-kernel ownership: register diagnostics stay outside the native
  SP, and ownership ends before E0. Reuse `COMMISSIONING-CONTRACT.md`, **Boot and
  hardware-adapter boundary** and **Memory**, not the native 3072 formula.
- The commissioning AC-fail vector at 7c keeps the accepted terminal guard route. Native
  IRQ7 already contributes to I1904; do not add it twice.
- Private E0 uses the accepted native-entry 1964 and private 2928 envelopes of
  `IRQ-AUDIT.md`, not this ordinary root formula. Its positive domain (no settlement,
  mixed CURRENT: the window in which the new task's SSP is loaded while CURRENT still names
  the old task; see `IRQ-AUDIT.md`, **CURRENT publication, terminal IRQ7 and fresh-SP
  exceptions**) remains sealed.

## Complete allocation-level composition

| Allocation / covered family | Non-IRQ ceiling / qualified scope | Plus I1904 / allocation |
|---|---:|---:|
| Native initial normal / game diagnostic family | max P224+192 = 416 | 2320 / 3072 |
| Native initial evaluator | max Q220+86 = 306 | 2210 / 3072 |
| Native runtime loader | max L196+302 = 498 | 2402 / 3072 |
| Native event / BET / repeat | 244 / 342 / 192 | 2148 / 2246 / 2096, all ≤ 3072 |
| Task7 diagnostic / motion / configuration | 142 / max 98 with IRQs open / 158; masked motion keeps its separate accepted treatment | 2046 / 2002 / 2062, all ≤ 3072 |
| Standard native SAS identity 1F/53 | 170 / 174 | 2074 / 2078, both ≤ 3072 |
| Native first 589ba check | max B268 + scratch 10 = 278 | 2182 / 3072 |
| Bootstrap early boot / clear | max 648 | 2552 / 8192 |
| Bootstrap shared normal / general loader | 302 / 532 | 2206 / 2436, both ≤ 8192 |
| Bootstrap first 589ba check | max B58 + scratch 10 = 68 | 1972 / 8192 |
| Native admitted settlement | max E840 + M328 = 1168 | 3072 / 3072 |
| Bootstrap admitted settlement | max E5960 + M328 = 6288 | 8192 / 8192 |

## Durable reproduction

These are read-only host-analysis scripts run against the pinned original ROM. They are
**not** Ghidra scripts, firmware builds or physical tests. Save each block to a file and run
it with `python3` from the repository root; the first block also needs `m68k-elf-objdump`
from the firmware toolchain (`toolchain.md`, **Native tools and isolated Python**). The 115
Task7 windows with six switches and the 77 ordinary windows with four switches were first
checked separately; the single engine below consolidates both groups. All three blocks (the
combined local walk and both selector-graph checks) pass. Local CFG and graph checks do not,
by themselves, prove the delegated callees, the retained-SP bookkeeping or physical timing.

### Complete local windows and switches

Calls are allowed to return, and conditional mutable reads are not correlated. Analysis
windows stop at an actual RTS or TRAP, not at a guessed adjacent function. Every case entry
is re-decoded so that `objdump` cannot read inline table words as instructions. 55d4
includes its 5616 alternative through 5634. The return provenance of the float register JMPs
(68e62 and 68f22) is stated in the cf54 alternative contract (**cf54 alternate c8c6 path —
no new covered port**); this walker does not accept them generically.

```python
TASK7_WINDOWS="""
b53c:bbba be16:c0d6 c1f8:c43c c4d0:c5ba c864:c8c6 132ee:13660 49f9e:4a262
5152a:51d14 57b78:57e70 5ca4c:5cc86 5cc86:5cf64 62114:62348 6833c:6876e 51d14:51d2e
51d2e:51da2 36540:36594 364f2:36540 369cc:36a48 36390:363c2 36a48:36a7a 366a8:36722
36722:36896 3633a:36352 3649e:364ca 57ad8:57b38 57b38:57b78 4e452:4e51c 35d60:35d96
c5ba:c61e c61e:c656 c656:c684 c684:c6a8 c6a8:c6e8 c6e8:c746 c746:c770
c770:c864 4a2a8:4a346 4a346:4a3bc 4a3bc:4a64e 3424e:3440e 60ae:612a 4c92:4d90
34926:34968 48d4e:48d7e 48d7e:48dbc 558ec:559de 559de:55d18 3de62:3df00 3ba8c:3bae6
131b4:132ee 16488:165a6 165a6:16614 16614:16682 149f4:14a22 14a22:14a7e 14a7e:14b48
68290:6833c 1dd6:1eae 2374:23be 23be:24ae 1509e:150c6 150c6:15138 15060:1509e
15022:15060 5d368:5d6ea 5efc0:5f022 5fb78:5fcb2 5f2da:5f388 60442:604fa 5fed8:5ff2c
5ff2c:5ff76 5fcda:5fe2a 5eba8:5ec78 602fc:60310 5fe2a:5fed8 3b920:3ba8c 3b8f0:3b920
3b63e:3b678 38be0:38c52 3a714:3a73c 3a804:3a82c 3a1a2:3a1bc 36028:3608e 363d4:363f8
363f8:3649e 3811e:38130 34e5a:34e82 34e82:34fc2 363c2:363d4 374ce:374de 37498:374ce
42ee:4360 4360:445e 4278:42ee 3dd4:4278 3440e:34492 4e37a:4e3c2 4e3c2:4e412
1472c:1475c 14f52:15022 380d6:38106 35cf4:35d24 5cf7e:5d368 3da1a:3daaa 5ee00:5ee14
5ff76:6016e 6016e:60254 60254:602fc 44456:44482 4e412:4e436 4ef34:4ef3e 4ef0c:4ef16
4ef20:4ef2a c0d6:c0fc 69ba6:69bce
"""
ORDINARY_WINDOWS="""
5c866:5c8d8 5c8d8:5ca4c 58300:58634 58634:587c0 587c0:587d0 587d0:58970
42d2c:42e9a 62b98:62ba8 5636:5664 52da:5400 64566:6488e 6488e:64a28
582ec:58300 42e9a:42eae 42eae:42f08 4e51c:4e60c cf54:cf8c 62958:629b0
4e4e:5288 5288:52da 5bd3a:5bf18 5bfa6:5c1ee 5c266:5c30e 5bc56:5bc9e
553a:55d4 55d4:5636 5616:5636 612a:633c 5fc4:60ae 46b4:46f4
440bc:440de 440f6:4411c 44ce8:44f2e 44f2e:454a4 454f8:4553c
4553c:455b4 455b4:45606 45606:45650 45650:4569a 456e4:4572e 4569a:456e4
4572e:458ec 473bc:476ce 476ce:4794a 479be:47a24 420b6:420d0
46832:4684a 46dac:46e3c 47d08:47d1c 46fbc:47012 45fca:46002 447b2:447ec
4fc64:4fd96 57888:578a0 4e2fc:4e306 4e306:4e33a 4ee2c:4eefc 49f82:49f9e
49f5e:49f82 8b98:8c1e 35f5a:35f66 34c22:34d22 3fa64:3fad8 3fad8:3fbbc
c8c6:ca14 cf8c:d252 d252:d28e 3f3c4:3f4de 3f308:3f3c4 1de96:1e0fe 34e40:34e5a
36352:36390 25e8a:260be 1cda6:1ce06 63ad8:63b18 39fb4:39fca 39f96:39fb4
"""
RANGES=[tuple(int(x,16) for x in p.split(':'))
        for p in (TASK7_WINDOWS+' '+ORDINARY_WINDOWS).split()]
import subprocess,re,collections,struct
b=open('roms/s6000_main_S6M00002B5.bin','rb').read()
assert 0x53dfa+struct.unpack_from('>h',b,0x53dfa+2*0xb)[0] == 0x53ff2
assert b[0x53ff4:0x53ffa] == bytes.fromhex('4eb9 0004 4456')
spec={0x4cb4:(0x4cb8,16),0x34274:(0x34278,16),0x131fa:(0x131fe,6),0x14a9a:(0x14a9e,8),0x14aea:(0x14aee,6),0x14f74:(0x14f78,6),0x50fa:(0x50fe,4),0x5bd66:(0x5bd6a,8),0x5c034:(0x5c038,8),0x5c28c:(0x5c290,5)}
sw={j:[base+struct.unpack_from('>h',b,base+2*i)[0] for i in range(n)] for j,(base,n) in spec.items()}
for j,t in sw.items(): print('SWITCH',hex(j),[hex(x) for x in t])
s=subprocess.check_output(['m68k-elf-objdump','-D','-b','binary','-m','m68k:68000','--insn-width=16','roms/s6000_main_S6M00002B5.bin'],text=True)
# Re-decode at each function/case entry: inline tables can desynchronize linear objdump.
for lo,hi in RANGES:
 for a in [lo]+[t for ts in sw.values() for t in ts if lo<=t<hi]:
  s+='\n'+subprocess.check_output(['m68k-elf-objdump','-D','-b','binary','-m','m68k:68000','--insn-width=16',f'--start-address={a}',f'--stop-address={hi}','roms/s6000_main_S6M00002B5.bin'],text=True)
d={}
for l in s.splitlines():
 p=l.split('\t')
 if len(p)>=3 and re.fullmatch(r'\s*[0-9a-f]+:',p[0]):
  ins=' '.join(p[2:]).split(None,1)
  if ins:d[int(p[0].strip()[:-1],16)]=(len(p[1].split())*2,ins[0],ins[1] if len(ins)>1 else '',l)
for lo,hi in RANGES:
 seen=set();todo=[lo];calls=collections.defaultdict(list);errors=[];returns=[]
 while todo:
  a=todo.pop()
  if a in seen:continue
  seen.add(a)
  if not lo<=a<hi or a not in d:errors.append(hex(a));continue
  size,op,args,line=d[a]
  assert not op.startswith('.') and op not in ('rte','rtr','rtd','stop','illegal'), line
  target=re.search(r'0x([0-9a-f]+)',args)
  if op.startswith(('jsr','bsr')):calls[target[1] if target else args].append(hex(a));todo.append(a+size)
  elif op=='rts':returns.append(hex(a))
  elif a in sw: todo.extend(sw[a])
  elif op.startswith('bra') or op=='jmp':
   if not target or '%pc@' in args:errors.append(line)
   else:todo.append(int(target[1],16))
  elif (op.startswith('b') and op not in ['btst','bset','bclr','bchg']) or op.startswith('db'):
   if target:todo.append(int(target[1],16))
   else:errors.append(line)
   todo.append(a+size)
  elif op=='trap':pass
  else:todo.append(a+size)
 assert not errors, (hex(lo),errors)
 print(hex(lo),hex(hi),'states',len(seen),'returns',returns,'unresolved',errors)
 print('union',' '.join(sorted(calls,key=lambda x:int(x,16) if re.fullmatch('[0-9a-f]+',x) else 0)))

```

### AS table-driven graph check

The recorded instruction-led edge weights are evaluated over all 15 vertices. A directed cycle raises
an error instead of being treated as an assumed reachable history.

```python
import struct, functools
b = open('roms/s6000_main_S6M00002B5.bin', 'rb').read()
a = struct.unpack_from('>15I', b, 0x69de8)
r = [struct.unpack_from('>10H', b, 0x69e30 + 20*i) for i in range(13)]
print([hex(x) for x in a])
print([(i, *(x[j] for j in (0,2,3,6,9))) for i,x in enumerate(r)])
e = {i: [] for i in range(15)}
e[10] = [(11,4)]
e[13] = [(r[8][0],76)]
e[14] = [(x[0],80) for x in r]          # independent 43cd72 reread
# byte+1: all accepted values 1..12; other values go to checked error
e[14] += [(x[0],100) for x in r[1:]]
e[14] += [(r[12][0],92),(r[7][0],80),(r[10][0],80),(10,12)]
active = set()
@functools.cache
def bound(i):
    if i in active: raise ValueError('cycle')
    active.add(i)
    v = 20 if i == 11 else None
    for j,w in e[i]:
        q = bound(j)
        if q is not None: v = max(v or 0, w+q)
    active.remove(i)
    return v
print([(i,bound(i)) for i in range(15)])
assert bound(10) == 24 and bound(11) == 20 and bound(14) == 124
```

### BSG table-driven graph check

```python
import struct, functools
b=open('roms/s6000_main_S6M00002B5.bin','rb').read()
a=struct.unpack_from('>20I',b,0x6aa3e)
r=[struct.unpack_from('>10H',b,0x6aaa0+20*i) for i in range(20)]
e={i:[] for i in range(20)}
for i in (2,9): e[i]=[(r[x[6]][0],76) for x in r]
e[11]=[(r[0x10][0],80)]
e[16]=[(r[0x10][0],76),(r[0x11][0],76)]
e[17]=[(r[0x11][0],76)]
e[19]=[(r[k][0],84) for k in (1,3,6,0x0a,0x0b,0x0c,0x11,0x12)]
active=set()
@functools.cache
def bound(i):
    if i in active: raise ValueError('cycle')
    active.add(i)
    v=(20,24,68) if i==14 else ((28,32,76) if i==18 else None)
    for j,w in e[i]:
        q=bound(j)
        if q is not None:
            q=tuple(w+x for x in q)
            v=q if v is None else tuple(max(x,y) for x,y in zip(v,q))
    active.remove(i)
    return v
print([hex(x) for x in a])
print([(i,bound(i)) for i in range(20)])
assert bound(14)==(20,24,68) and bound(18)==(28,32,76)
assert bound(19)==(104,108,152)
assert tuple(28+x for x in bound(19))==(132,136,180)
assert tuple(88+28+x for x in bound(19))==(220,224,268)
```

### Producer and SP reproduction locators

Decode the 36 geometrically relevant signed tables for cf54/62958, and all the 27-arm, AS
and BSG records, from the exact bases and counts above. The global computed-producer
classes and the 44-table **regional** seal of the guarded body remain in
`SETTLEMENT-GUARD.md`, **Producer seal**; this local scan of 36 tables does not replace
them. The maintained gate
`tests/test_settlement_guard.py:test_final_image_bounded_dispatch_loads_do_not_bypass_guard`
(in `firmware/`) rechecks overlapping final-image loads after future code changes; the
passed candidate seal is recorded in `SETTLEMENT-GUARD.md`.

In both the original and the candidate, a **complete** scan of aligned values whose low 24
bits fall in [cf54,cf8c) ∪ [62958,629b0) finds exactly cf74 → 62958, 21284 → cf72,
2aa4a → cf5c and 77e06/77e0a → cf54. The classification of the instruction fragments and
**both** producers a5e0 and ad72 are given above, and explicit PC-relative and absolute
transfers and all bounded computed classes were also matched. A value scan alone is not a
producer proof.

Retained-SP locators: Task3 F88 and D88 at 72ee; aa6a entry 40 with F12, and D12 at ad72;
D0 at cf72; the cleanup of 12 at 62992 → D0 at 62996; 43c2's old error argument through
43de; D4 at 5c906; D12 at 5c92c/5c9b2; 6430a has 36 bytes of retained arguments + 16 fixed
+ 4 return. These instructions, together with the complete printed local CFGs, allow
independent checks without any file outside this repository. `NATIVE-STACK-LEDGERS.md`
owns the separate emitted and local cost arithmetic.

## Stable-document and release boundary

The mathematical covered-root and ordinary proof is independently approved, including the
36th switch at 14f74 and the candidate identity. The complete aggregate has no unmatched
covered-entry port under its premises. The native FIRST 268 and the bootstrap FIRST 58 give
first-check scratch-plus-IRQ figures of 2182 and 1972. Admitted work remains under
M328/G120/H2232/S10/I1904, not under a finite bound on financial rounds. The unsupported
loose bootstrap hand-off of 226 and the superseded native 256 / one-descent rows are not
proof inputs.

All seven maintained documents were read in full, and the review's clarification of the
Task2 / direct Task6-clear scope (**Fault-controller family**) was applied. The final
document review and the three embedded reproduction scripts passed. After all 20 inputs and
the manifest were rechecked, the exact eight reviewed artifacts were published without a
rebuild and the copied bytes were verified. `install_ready=false` remains. No guarantee
follows about whole-legacy temporary peaks, financial termination, corruption recovery, or
physical timer, bus, silicon or mechanical behaviour, and no machine operation is
authorized. Chip-image packaging is complete; the next step is separately approved physical
qualification.
