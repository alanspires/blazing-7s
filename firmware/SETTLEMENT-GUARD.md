# Settlement entry resource guard

## Status, scope and provenance

**Implementation, emitted-code/producer seal, offline tests, source review, complete mathematical initial-root/ordinary proof and seven-document review are APPROVED. Final pinning and exact-artifact local publication are complete. Hardware and installation remain unqualified.** Candidate symbols are `src/game_hooks.S:game_settlement_entry` and `src/platform_hooks.S:platform_fatal`; `patches.json` implements native3072 capacity and the589ba JMP/NOP detour. [INITIAL-ROOTS.md](INITIAL-ROOTS.md) is the sole initial-root authority: native FIRST268, bootstrap FIRST58, normal416/evaluator306/runtimeLoader498, shared bootstrap302/532 and earlyboot648. Its immutable AS/BSG DAGs and complete positive ports require no new callback guard or finite financial-progress assumption. No release or hardware operation follows from mathematical approval.

Published `build/` contains the exact reviewed image, main SHA-256
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`,
with `install_ready=false`. Historical b2 is retained only as proof provenance. Original `roms/s6000_main_S6M00002B5.bin` SHA-256
`49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a`.
`NATIVE-STACK-LEDGERS.md`, **Inputs and conventions**, pins b2 ELF/disassembly/
manifest hashes. Native instruction/table locators below refer to that original;
extension symbols, not old linked addresses, identify the implementation.
Addresses, field offsets and opcode keys are hexadecimal. Byte counts,
cardinalities, normalized indices and MODE-table labels are decimal.

Evidence was read-only Ghidra analysis of the existing original program,
raw `m68k-elf-objdump -D -b binary -m m68k:68000` instruction reads, immutable
big-endian table decoding and local SP worklists through all returning exits.
The worklists tracked LINK/UNLK, saved registers/SR, retained arguments and
branch merges; they did not assume callee safety from absent xrefs or Ghidra's
incorrect nonreturn annotation on3a1e. Supporting native/table regions were
compared to frozen b2 during the original proof. Main independently decoded the
44-switch and controller tables. This durable consolidation supersedes the
working notes; no temporary file is needed to recover the claims below.

**Promise:** bounded guard overhead and extension-live/covered-hook execution
between checks, including refusal and covered unwind. **Not promised:** safety
of every stock-native temporary peak, finite financial settlement, rollback,
coil-off, arbitrary corruption recovery, hardware timing or installation.
Positive no-covered-entry/balanced-return summaries may exclude pure-native work
only when no extra extension frame survives. Balanced return does not prove
that work cannot temporarily overflow; intact contexts remain a premise.

Inherited premises: valid initialized registries/kernel objects/native pointers
and counter copies; intact call/exception/return contexts; supervisor tasks;
valid native selectors/geometry (including a64c MODE0..29); zero optional kernel
callbacks and disabled unregistered extended services/no external invokers;
enforced cross-call calibration interval[0x60,0x800], conditional timer/bus
model and one filtered terminal IRQ7
assertion from `IRQ-AUDIT.md`. Accepted marker-valid SafeRAM need not originate
from this exact ROM. No fresh-NVM, stable financial-value or MODE-across-wait
assumption is introduced. These remain conditional premises, not installed-board
qualification.

## Entry contract and capacity

The detour replaces eight original bytes at589ba:
`4e56ffb048e73f00` (LINK A6,#-80; MOVEM.L D2-D7,-(SP),108 fixed bytes).
The source design uses JMP+NOP, not JSR, so no additional return is retained.
On admission restore incoming D0/A0/full SR and SP, replay both original
instructions exactly, then JMP589c2. On refusal remove all10 scratch bytes,
restore full SR/registers and tail-JMP `platform_fatal`, which executes TRAP1.
The caller's existing return stays untouched. Bare JMPfc4 would violate the
native six-byte exception ABI and is not the design.

The source capacity patch changes only WORD27b6, configuration27a0+16,
0800→0c00 (2048→3072 bytes per18 native slots). Separate reserve0800 at27a8,
pool base/length, slot count and TCB ownership are unchanged. Existing private
E0 remains4096 bytes. See `TASK-STACK-AUDIT.md`, **Implemented native stack
capacity**, for the closed allocation-route budget
`18*3072+5096=60392`,5144 free bytes, lowest stack402800 and six boot queues.
The published06bef891 image contains this capacity patch; historical b2 did not.

## Reserve and induction

Let p be original589ba entrySP and T the owning exclusive allocation top;
E=T−p is resident bytes including the caller's return, before108 native bytes
or10 guard scratch. Let L be allocation size, M=328 covered ceiling,
G=120 additional residency at the next check, S=10 scratch, I=1904 IRQ allowance.

```text
H = max(M+I, G+S+I, G+6+I, G+122+154) = 2232
admit iff p >= allocation_base + H   (unsigned; equality passes)
equivalently E <= L-H
```

The guard restores scratch before native work or refusal. Before TRAP1's fd0
raises IPL7, charge its6-byte exception plus fullI. After fd0, charge native
fatal122 INCLUDING that exception plus terminal IRQ7 allowance154, not returning
lower IRQs. `fc4` writes diagnostics to absolute RAM, not a fresh stack, then
`13ee→1274/1246→123c` halts. It does not call settlement/recovery or clear a
payment flag. Pending financial work can remain unfinished, as user approved.

| Domain | L | admitted max E | next-check E≤ | next scratch+I | next6+I | next122+154 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Native candidate |3072|840|960|2874|2870|1236|
| Bootstrap |8192|5960|6080|7994|7990|6356|

First native ingress B≤268 has268+10+1904=2182 before any admission. Bootstrap
first B≤58 has58+10+1904=1972. Complete ancestry is authoritative in
`INITIAL-ROOTS.md`, **Complete ordinary-root compositions / Bootstrap and earlier qualifications**.
These are first-check bounds, not limits on later rounds.
Every admitted entry covers E+328+1904≤L. Each next check has E'≤E+120, so its
scratch/refusal fit even if rejected. Returning children restore their entry SP;
ancestors resume under their original admitted E, not the deepest child's E'.
No bound on the number of financial rounds is needed. Fresh-SP reset starts a
new bootstrap induction rather than retaining interrupted ancestors.

## Covered cut graph

| Route below an admitted589ba entry | Covered ceiling / next-check ingress |
| --- | ---: |
|597da/598ae→59f3a→61c50→61172 changed hooks|112+216 =328|
|5990c→61c50→61172 changed hooks|112+196 =308|
|5999e→3d294:3d2a8/3d752→61c50→61172 changed hooks|112+8+196 =316|
|5999e→3d294:3d594/3d6de/3d6fc→next589ba|120|
|recursive return599a4→53dd0(2e)→599b0 epilogue|no covered entry/re-entry|

The192-byte61172 envelope is changed-hook work, **not its entire stock body**;
61c50 adds4 and59f3a adds24 to61172 entry. It includes normal-start,
candidate615ce, commit615fa and shared3fbbc diagnostic hooks. Wrapper scratch
unwinds before stock continuations. `NATIVE-STACK-LEDGERS.md`, **Covered
continuation prefixes and unwind**, preserves actual native prefixes and returns.

589ba has108 fixed bytes. Its5999e call leaves no outgoing argument block;
3d294 has4 saved bytes and each of its three589ba calls has no arguments,
then immediately restores D2/RTS. Thus G=108+4+4+4=120. After5999e returns,
599aa dispatches exactly2e, followed only by FP-relative restore/UNLK/RTS.
Local backwards edges discharge argument blocks; the only growing normal SCC
in this closed cut graph is589ba↔3d294, intercepted at every new589ba entry.

### Positive delegated domains

| Family | Closed positive domain / explicit boundaries |
| --- | --- |
|Owned numeric/scalar/selector|51da2/523a4/5a16c; typed numeric/CRC/copy/protected writes; only explicit599ba/router/accounting boundaries|
|Controllers|3fefa,5aa68(6/7),599ba,4b78e,whole59b76; progressive1/2/7, all30 a64c display callbacks|
|Continuations|61c50/59f3a→61172/3fbbc changed hooks; remaining numeric/display/router/accounting children|
|Accounting|53dd0 selected rows in NATIVE ledger; data descriptors and fixed service branches, no task receiver invoked inline|
|Router|the exact five-protocol cases below; normal numeric/format/queue tails; no incoming business-command dispatcher|

Complete target/child/cleanup and retained-argument facts are in
`NATIVE-STACK-LEDGERS.md`, **Settlement guarded descendant ledgers (frozen-b2
design)**. In particular51da2→599ba adds60 (E+172);523a4→router201 adds72;
5a16c→router113 adds12. Numeric/scalar trees do not hide controller callbacks.
Already-earned native-only E+454 numeric/fatal and E+540 F6 facts are not
silently included in M328 or claimed universally safe.

### Selected communication domain

26a0e saves D2=4. AS entry adds16 (8 arguments+4 return+4 save); other protocols
add12. Arguments are popped between sequential calls: take their maximum, not
the sum. Offset tables/key lists/counts are AS26474/2649c/20 byte keys,
SAS23d92/23dfe/54 word keys, BSG1b906/1b940/29, MIS18c1a/18c48/23,
SDT26ff6/27020/21. For ascending key position i in n entries,
`target=base+signed16(base+2*(n-1-i))`. Defaults are AS269a8, SAS2468a,
BSG1bf12, MIS1905c, SDT270b4.

|key|AS|SAS|BSG|MIS|SDT|
|---|---|---|---|---|---|
|30|264b0|24008|1b97a|18cec|default|
|31|264c6|24306|1b9b0|default|default|
|34|265fc|24196|1bb54|18d4e|default|
|35|26604|243f6|1bb70|1905c(return)|270b4(return)|
|36|26612|default|1bb70|1905c(return)|270b4(return)|
|45|268c8|default|default|default|default|
|105|default|2426a|default|default|default|
|113|default|23f1a|default|default|default|
|201|default|23e6a|default|default|default|
|210|default|245dc|default|default|default|
|f6|26918|default|default|default|default|


AS30→25c98 uses26b0e/26be4/format/counter; AS34→260be uses numeric/26b8e/
44c. SAS30→24b94→578a0(option32); SAS34 uses400/44c/54822/548bc and option1d.
SAS31→27e80/2b8e2/2b898(2) and option21;105→3a1f0;113/201/210 scalar packet
stores. BSG30→26be4/38b96,31→fixed counters/format,34 scalar,35/36→37456/
38b96. MIS30 stores scalar packet data;34 uses549c8(43)/400. Selected common
tails use17b14 or return. AS defaults still perform35cf4 fill first. Key34's
26aae tail writes43cd59 through400; it is included, not an omitted fallthrough.

Numeric descendants are fixed counter/descriptor resolution, BCD formatting,
68bcc/68c30/68b90/68c4c bounded division,35cf4→35ca0 copy, scalar SafeRAM
writes and fixed queue services.17b14 calls only3d82/3d88/68c8a/17a66;
17a66 and68c8a are leaves, copy temporaries are popped. `27e80`'s getters/
195fe and38dfe selector return DATA, not code. Loops are fixed-frame with
balanced calls. These protocols enqueue bytes, never synchronously dispatch
744ca/74af2 business receivers. IRQ-specific bill and new reel/completion
selections and their numeric bounds are in `IRQ-AUDIT.md`, **Guard-domain
closure and IRQ6 selected cases**. No universal router bound is inferred.

| Shared selected helper domain | Positive children and returning cleanup |
| --- | --- |
|256c0/26252 /26b0e|26b0e,35b48,38dfe,549c8/54912,38b96,44c,68bcc/68c30,17b14,23b78;26b0e is numeric/format, not a callback|
|26b8e /26be4|three38b96 calls then pops36 / DATA tables74112/7418e/7420a/74286 and optional400; no callable pointer|
|27e80..280a0|35b48(5/1d),38dfe,549c8(63/94/45/4d),68bcc,195fe; formatter args popped27ffe/28042, final inline clear/MOVEM/RTS|
|195fe /3a1f0|195fe→68b90/68c4c with width4/10 and positive74410 decimal divisor table;3a1f0→3a15c width4, constant100; private counters, no accumulating args|
|38b96 /37456|only68c4c/68b90 with decimal10 or positive10*D2; fixed private-count progression, all saved registers restored|
|2b8e2 /2b898(2) /2eb2e|35ca0 byte-copy and2b886 index leaf, balanced UNLK/RTS;2eb2e leaf, not serial task dispatch|
|35b48 /38dfe|immutable18-byte option descriptors at73bc4;35aec byte/immediate or BCD-reader wrappers;38e36 fourteen-entry table returns DATA; invalid-option TRAP1|
|54822 /548bc|3c00e /3bbae→accepted validator/resolver/conversion/copy families; UNLK discards final retained args, never invokes a game handler|

For the selected43/98 operands, descriptors6a14c=`00438034 0103 0043` and
6a3f4=`0043d49c 0004 0098` seal type/width/id/direct-data bases. Checksum failure
remains a TRAP1 alternative; it is not assumed impossible. Other selected
counter descriptors reuse the valid-object numeric domain.35ca0/17a66/2b886
are leaves;17b14 math/copy loops pop temporary4/8-byte operands before merges.
All normal selected exits restore entry SP, including FP/UNLK-discarded argument
tails. No missing delay fallthrough or unexamined switch arm is called closed.

### Display callbacks

At a64c, a64e zero-extends MODE, loads78f86[MODE], a660 calls it. The complete
valid MODE0..29 domain is below; no stability across a service/wait is assumed.

| Modes | Target |
| --- | --- |
|0|4a64e|
|1,3,5,7,9,11,15,25,29|4c52c|
|2 /4 /6|4b578 /4b56a /4b586|
|8,10,14|4b78e|
|12,13,16,17|4c3aa|
|18,19,22,26|6828e (RTS)|
|20,21|4bb9a|
|23 /24 /27 /28|56176 /4c67c /4c1e0 /48c6e|

All are closed display/numeric/output families, not589ba, changed-hook or
controller dispatch. Crucial boundary:48c6e returns at48c7e/48c94/48c9c/48ca4;
its539a8/52d82/54b06 children call4b586. Evaluator routes48cce→5323a and
48cd6→54b0e belong to separate48ca6 AFTER48ca4 RTS, not its fallthrough.

Legitimate controller entrants remain globally real:539e4→53a10:589ba and
556c6→556e4:61ad0→65128→589ba. The finite initialB268 is not assigned underneath an
admitted guard. Rather, the composed descendant graph above contains no
487a6/489ae/48ada/controller argument producer; its only nonservice callback
is the complete a64c display domain. Queue posts schedule another root on its
selected stack, not a synchronous controller on these saved ancestors.

## Producer seal

This is the **historical original/frozen-b2 baseline** range-specific census.
The current candidate's separate fresh producer/equivalence seal passed under
**Candidate seal and offline gates**; future changed images require resealing.
Regions are[589ba,599ba) and[3d294,3d794); predecessors589b8/3d292 are RTS,
final UNLK/RTS599b6/599b8 and restore/RTS3d790/3d792 bound the bodies.

- Twelve direct589ba calls:250ae,3d594,3d6de,3d6fc,40ade,509e2,513ba,
  53a10,5807a,5829c,656c0,65746; all target entry.
- 3d294 has JSR405f6/43f90/5999e and event-table7837e (event0F,MODE0A),
  all to entry. Local internal switches are not first-ingress producers.
- Encoded branch/DBcc/PC-relative transfers/address constructions, callable
  absolute pointers including low24-bit aliases, vectors,18 task-entry literals
  and both supplied personality ROMs introduce no other external interior entry.
  Nineteen aligned alias matches were instruction fragments, not operands
  (e.g.3c06a displacement0005+SUB9602;55326 SUBQ.B+ADD.W). This scan is only
  one part of the seal; computed producers below are essential.

### RAM and argument producers

| Producer | Complete qualified source domain |
| --- | --- |
|Task8 ca98|cfda registry init;d252:d25a caps20 compact installed slots (not IDs); null checked before call|
|UART/serial13d04/1411e/1643a|163d6..1640e installs four pairs74aca:19732→43e8f0;74ace:19b16→43e8f4;74ad2:100→43e8e8;74ad6:744ca→43e8ec;74aea count4|
|3ed8a/3ed94|3ed38 A3/A4 pairs only3ef6c:(3ee36,3edda),3f704:(3f548,3f4de)|
|48992/48ba8|bounded48972/48976,48b8c/48b8e indices; controller tables below; forwarding489ec..48a04/48b24..48b4a preserve argument pairs|

Task8's complete immediate-target union atcffe..d244:

```text
cbec 3daaa 6816e 49c40 3e832 3df00 3e250 1d7c6
1dbd6 d784 d600 23bee d28e d518 3dcbe e0a0
dbc4 e8d6 f0da da42 3ef52 3f6ea 3f108 3f920
```

The complete ordered69de8/count15 and6aa3e/count20 action arrays are
canonical in INITIAL-ROOTS **Complete15-callback inventory / Complete
callback-index graph and direct frontiers** (index→target columns). This
regional producer proof consumes those exact fixed target sets, not their
invocation-depth bounds. The immutable table/count binding producers are:

| Consumer |69de8/15 +controller69e30/13 producers|6aa3e/20 +controller6aaa0/20 producers|
| --- | --- | --- |
|487a6|53944|54c98|
|489ae|53a62|54ad0|
|48ada|53cb0|54afa|

First ingress into forwarding helpers[48a12,48ada) is positively excluded by
preceding RTS48a10/48a74, complete encoded/address/computed producer domains
and ten reaching switches; their own forwarding cannot bootstrap a first call.
The retained allocation proof in TASK-STACK-AUDIT records those switches.
69e24/2 and6aa8e/3 are six-byte DATA records, not callback arrays.

### Other immutable dispatch producers

| Producer | Complete checked load domain | Frozen regional target |
| --- | --- | --- |
|a5e0|77cc6+120*((event−1)&255)+4*mode_byte|7837e→3d294 only|
|a660|78f86+4*mode_byte|none|
|ad3a /ad72|77cc6 /77db6 +4*mode_byte|none|
|1a0ce/1a13e|744cc+6*index_byte|none|
|1c29e/1c2dc/21c04|74af2 indices0..14|none|
|271c8|69902+48*row+4*state_byte,row<12|none|

74af2's complete targets are1aedc,1aede,1af00,1b0a6,1b0bc,1b0ce (four
entries),1b2a2,1b344,1b438,1b44c,1b490,1b5a2.1acd4's zero selector-return
argument yields only0..14;1a892(0) uses this classification. Byte-domain loads
above intentionally overapproximate valid MODE values and reach linked
extension ROM. Candidate06bef891 passed the required fresh scan below; this
is not an exemption for future changed images.

### All44 geometrically reaching signed-word switches

Each indexed JMP is4efb0002, signed WORD index. Table columns are JMP/table,
normalized decimal index and decoded destination range. External tables exclude
both strict interiors; only four switches already inside the bodies target
local cases. Successful lookup bounds3ca84/5d9c6; failures branch away. Others
have unsigned range checks before index doubling/loading. Other indexed switches
cannot geometrically reach the regions.

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

Configured creation/deletion/switch hooks27c4/27c8/27cc and deferred27c0 are
zero, copied at36fe..370a. Standard2948 services use fixed kernel branches;
2ba4 extended dispatch is disabled under the no-external-registration premise.
Boot A2 continuationsc92/dec come from explicit LEAs to93c,b02,c58,c68,c7e,
c88,cfe,d0c,d20,d30. Arithmetic register JMPs6896a/689d4/68e66/68f18/68f26/
68f8a restore intact saved continuations. Historical extension indirect calls have
fixed function producers and signed-word switches outside native geometric
reach. Candidate06bef891's intentional589c2 continuation is a NEW producer;
its fresh sealing passed below, not by a historical-scan exemption.

## Allocation identity and asynchronous windows

Native resumed supervisor task:
`cfg=LONG100=27a0; ws=LONG(cfg)=400000; CURRENT=LONG400024;
T=LONG(CURRENT+40); L=unsigned WORD27b6; base=T-L`.
Initializer375a writes exclusive top before375e subtracts L;3746..3750 stride50
builds18 TCBs. Creation2cba..2d4a takes a free TCB and creates a26-byte context
from its retained top; restore/RTE consumes it. Deletion2e0c..2e6e removes
waits/timers and frees the TCB without changing top. Recreate overwrites saved
SSP+38 from top+40. +38 is NOT an allocation bound. Reset initializes a new
pool lifetime and cannot resume an old activation. ROM size must be consumed
at initialization, not changed in a live kernel. Fixed0800/0c00 are below8000;
SUBA.W sign extension is equivalent here only, not for arbitrary unsigned sizes.

Scheduler2af2 saves registers,2b30 saves oldSSP+38,2b36 USP.2b54 installs
newSSP BEFORE2b70 publishes CURRENT;2b64 can admit IRQs. Before task RTE,
CURRENT is coherent. A preempted guard's snapshot/registers resume as its own
context; a deleted guard never resumes. No guard lock, retry or long IRQ mask
is needed. The mixed-publication window is closed by the positive synchronous
IRQ exclusion in `IRQ-AUDIT.md`, not by pretending CURRENT is always coherent.

Boot MUST use constant[436000,438000), actual resetSP437ffe; CURRENT is already
nonnull after3736, so nonnull does not establish task mode. The design recognizes
the boot arena independently of CURRENT, with unsigned range/budget checks. Native membership stays
tied to CURRENT/top: selecting an adjacent contiguous slot from SP could hide
an overrun and is not a sound fallback. No supported private-E0/IRQ call remains
after the positive domain closure. PrivateE0[433000,434000) is a real distinct
allocation, not falsely identified by CURRENT or categorically refused based
on SR/private address. IPL7 alone does not identify IRQ7 or justify refusal.

## Bootstrap and first-check covered work

`INITIAL-ROOTS.md` owns the complete initial-root proof, including all27 controller arms, the full6268e domain and exact normal/evaluator/loader/check producer checklist. Its native FIRST268 and bootstrap FIRST58 are complete abstract bounds under valid-object/selector/intact-context and the other inherited premises, not exact-history attainability assertions. The earlier native256/one-descent row and loose bootstrap226 handoff are superseded, NOT alternative proof inputs.

Reset8fa loads437ffe (2 bytes against438000); bca→154e entry6,154e saves12,1dc2→1380 entry22,13e6→6268e Y26 after13e2 removes32 arguments. The complete all-arm composition gives bootstrap first-check alternatives34/50/58, sharednormal302/generalLoader532, all positively attributed to[436000,438000). Earlyboot/clear local costs remain NATIVE **Boot and clear C-live paths** (maximum648 including the2-byte top adjustment). None of these finite initial rows replaces admitted E+328 or qualifies unrelated native temporary peaks.

The first scratch is independently covered BEFORE admission: native268+10+1904=2182≤3072; bootstrap58+10+1904=1972≤8192. Subsequent activations/refusal/unwind use **Reserve and induction**, not a finite number of settlement rounds. No Task6-only lifecycle predicate or fresh native NVM assumption is used to restrict bootstrap arms.

## IRQ, private E0 and exception separation

`IRQ-AUDIT.md`, **Guard-domain closure and IRQ6 selected cases**, preserves all
UART/coin/output/motion phases, the fixed emitted-b2 E0 indirect producers,
selected router cases and component arithmetic. No returning same-stack domain
calls589ba/3d294/controller/evaluator entrances. Queued notifications or E0
`game_submit` requests are asynchronous; native tasks later run on their own
selected stacks. This is a positive target-domain proof, not1904/1024 implying
exclusion. IRQ6 keys76..7A give302,91 gives348, both belowF6=434. IRQ17b14 is50,
its multiply alternative40; general-task17b14 remains88. TotalI stays1904.

DIV0 is distinct from TRAP1. Vector14→f54→108a raises IPL7; optional diagnostic
TRAP0 services16/05 are within sealed kernel domains.115c→1184:118a loads
ROM[0]=437ffe, abandoning the complete interrupted stack.11c8's intermediate
domain is1260→3d26a's fixed321-descriptor loop,140e/14fc protected checks,
15060→1472c input/bit reversal,1274→3a918/3a652/34b3e display/checksum,
426/44c and1246. No covered hook executes there.1196→dee:e02→8f6:8fa reloads
SP again, producing a fresh bootstrap root. Do not invent nonzero denominators,
confuse this with permanent TRAP1 halt, or claim restart financially safe.
IRQ6 selected DIV0 old-stack diagnostics≤140 below router are included in160/154;
registered-IRQ SC16 cannot switch stacks and SC05 cannot schedule there.

Terminal IRQ7's old-stack275a→445e→365d4→41796→bf0 domain has no financial
entry. bf0 selects4385dc and terminates; repeated IRQ7 edges remain excluded.
Forced resets retain the existing supervisor/fresh-SP/no-old-resumer lifetime
premise, not an expanded soft-reset or silicon proof.

## Candidate gates

### Candidate seal and offline gates

Verified published main SHA-256 (the unchanged reviewed candidate):
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`.
Guard-qualification manifest SHA-256 (historical mirrored packaging):
`f91b00c6657f3358444c48793d42fb29a3a92a76245fbbee93f46220d2cc0e6e`.
Current Winbond-packaging manifest SHA-256:
`ef1728ca2cb342666e6b31ebee1be8fdc411322b36f7c509c67dfb296b5f7b01`.
The packaging-only change preserves main and all four compiled artifacts exactly.
`PROGRAMMING.md` records its53 passing builder tests, independent review, two-build
reproduction and exact-artifact local publication. Runtime proof is unchanged;
these pins do not confer installation approval.

Main independently reproduced the final-image comparison:5,601 prior decoded
extension instructions retain their operations/frame operands;272 operand changes
are exact +104-byte address relocations. `ext.stack` is identical. Existing native
patches preserve instruction shapes and symbolic targets; only the capacity,
settlement JMP and padding patches are added. All44 bounded switches and the
recorded native producer arrays match. The new589c2 operand at7cea0 is outside
a5e0's actual byte-domain target-load set; no layout workaround was used.
`tests/test_settlement_guard.py:test_final_image_bounded_dispatch_loads_do_not_bypass_guard`
checks the overlapping final-image producer domains on each fresh test build.
This preserves the local proof domains; producer identity does not bound how
many nested callback invocations occur before first guard entry.

The emitted guard has10 scratch bytes, unsigned threshold/equality admission,
full register/SR restoration, original prologue replay and restored-scratch
TRAP1 refusal. Native capacity is3072; the separate27a8 reserve is unchanged.
At guard qualification, all20 production input SHAs, including M68000 libgcc,
and seven output artifact SHAs/sizes matched the historical candidate manifest.
Main reconfirmed those pins before stable-document review. The41 patches give a
zero boot checksum. That manifest used mirrored512KiB chip outputs; the current
Winbond bundle uses exact256KiB lanes. Its20 inputs and output pins were separately
checked, with only build.py changed and all runtime artifacts identical.
`install_ready=false` remains in both versions.

The full affected offline command passed **529 tests and six subtests**:

```sh
PYTHONDONTWRITEBYTECODE=1 firmware/.venv/bin/python -B -m pytest -v firmware/tests tools/test_sas_firmware.py tools/test_sas_commission_firmware.py
```

This includes all67 settlement-guard cases and ten deliberately broken controls,
real native allocation/recycle tests, modeled IRQ boundaries and native fatal
execution. The cached-return-stop defect was reproduced with a failing regression
and fixed in the shared CPU executor; the duplicate game-fixture hook inventory
was removed. A subsequent allocator-only `pairwise` cleanup passed its targeted
rerun. Ruff passed on the eight touched Python files. Independent source/test
review and review of the finite aggregation rows found no findings. These are
CPU/software checks, not physical timing, payout or installation qualification.

### Mathematical proof approval and publication

The complete initial-root/ordinary composition is independently **APPROVED by b1attempt32**. INITIAL-ROOTS retains all producer/root checklist rows, AS/BSG immutable selectors/weighted edges, all27 controller arms, full5c8d8 suffix, corrected a5e0 AND secondaryad72→cf54→62958, Task7 primary/sharedmotor domains and every selected numeric/accounting/router port. Main reproduced115 windows/six switches and77 windows/four switches; the additional36th geometric14f74 switch has six checked targets and original/candidate identity. No true unmatched covered-entry port remains under the stated premises.

Initial native normal416/evaluator306/completed-runtimeLoader498/FIRST268 and bootstrap FIRST58/sharednormal302/generalLoader532/early648 are now complete composed bounds, not a largest-known-row shortcut. Admitted M328/G120/H2232/S10/I1904 and allocation ownership are unchanged. The earlier syntactic5544a callback cycle is NOT a reachable-history/overflow witness: actual immutable selectors make both AS/BSG graphs acyclic. No additional resource cut or financial-history investigation remains requested.

Publication record:

1. Main read all seven maintained documents in full. Independent review33 approved them; Main applied its clarification that72/458 covers only Task2/direct Task6-clear roots, not shared X72 ancestry. All three embedded reproduction scripts in INITIAL-ROOTS passed.
2. Main rechecked all20 production inputs, the candidate/manifest pins and original ROMs, then copied the exact eight reviewed artifacts into `build/` without rebuilding. All copied bytes, boot checksum, mirrored chip lanes and the unchanged separate reserve matched.
3. This closes the scoped offline software/publication gate only. `install_ready=false` remains; no programmer, serial, chip or machine operation is authorized. Winbond image packaging was subsequently completed as recorded in PROGRAMMING.md; physical timer/bus/silicon/mechanical and installation qualification remain separate.

Any changed source/image requires affected tests and fresh emitted/producer/frame evidence, not automatic reuse of this image's approval.

This document promises only bounded guard overhead and extension-live/covered work, including first check, refusal and covered unwind, under the inherited premises. It is not whole-legacy temporary-peak safety, financial termination, payout recovery, coil-off or hardware qualification.
