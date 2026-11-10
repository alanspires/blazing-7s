# Native extension stack ledgers

Instruction-led LOCAL cost authority for `TASK-STACK-AUDIT.md`. Costs were established on historical frozen b2 and transfer to candidate06bef891 by its verified instruction/frame identity. `INITIAL-ROOTS.md` is the single authority for complete initial-root producers, selector DAGs, retained incoming prefixes and finite composition. That mathematical proof is independently approved by b1attempt32 under its stated premises. Seven-document review33 and final pinning passed; the exact reviewed06bef891 artifacts are now published locally with install_ready=false. Historical b2 remains provenance only. No whole-legacy temporary-peak, financial-termination or hardware guarantee follows.

# Boot, diagnostic, serial and loader ledgers

## Inputs and conventions

Evidence is the emitted `firmware/build/ext.dis` and the pinned original ROM, disassembled with `m68k-elf-objdump -D -b binary -m m68k:68000`. These are instruction-led calculations, not hardware measurements. Native addresses below identify instructions, not document line numbers.

Historical b2 verified hashes (provenance, not current build pins):
- firmware/build/main.bin: b2ce1bc5c03b8e8af47cf246115968033f62574c2a3ae2d2d7e5c4f225adcb83
- firmware/build/manifest.json: 0606b5ae61de9166c6c0a97eed1de8f4beb7f77bbac08268ed69895c96e04b8e
- firmware/build/ext.elf: 1e35ca42340a2f125340030715800a136200858acf19f36dbedca47c64be7064
- firmware/build/ext.dis: 6d2579a80f4e171d1b80ebd557e6585a0b85b3c580904475eb0a3dbbdf43458b
- Original ROM: 49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a
- install_ready remains false.

Reuse `IRQ-AUDIT.md`'s accepted 1904-byte interrupt allowance, 1024-byte private-E0 worker allowance and commissioning qualification. These are conditional software envelopes under the IRQ-AUDIT premises (valid native state, calibrated timing/bus premises, one filtered terminal AC-fail, no arbitrary external invokers and serialized/nonrecursive private-E0 callback ownership (not a ban on guarded financial recursion)). Exceeding an overapproximated sum is a capacity warning, not a demonstrated overflow. No attempt to prove simultaneous IRQ compatibility by a trace.

Unless noted, resident counts include caller arguments, call returns, fixed saves and nested callees. A subtree stated 'below entry' excludes that entry's already-pushed return. Task initial 26-byte context is consumed by the native restore/RTE; it is not an extra live trampoline.

## Task7 Test10

Task7 b080 saves D2 (4). Before b30a calls 5ab76, b2f4/b2f6 leave 8 argument bytes until b324. Test10's call return is 4 and its D2-D6 save is 20: hook-site prefix = 4+8+4+20 = 36.

| Changed path | Resident bound | +1904 | Margin to2048 (historical b2) |
|---|---:|---:|---:|
| 5ac3c -> motion_test10_poll | 98 IRQ-open | 2002 | 46 |
| 5af80 -> motion_test10_cleanup | 96 IRQ-open | 2000 | 48 |
| 5af86 -> motion_test10_return | 70 IRQ-open | 1974 | 74 |
| 5aee2 ->3fbbc ->3fc34 diagnostic wager | 142 | 2046 | 2 |
| Same root, 3fbcc line-order replacement | 92 | 1996 | 52 |

motion_test10_step fixed frame is24. Poll's open maximum is36+4 hook return+18 saved scratch/SR+4 argument+4 C return+24 fixed+4 restore argument+4 restore return=98. Its deeper work runs under the explicitly saved/restored2700 section; no generic 'motion disabled means no hook' exemption. The stock continuation is tail-entered after extension scratch is unwound. The owned delay branch is smaller; qualified motion is disabled in this image.

motion_test10_exit has4 fixed bytes; its restore point is36+18+4+4+4+4=70. IRQ7 treatment retains the existing one-filtered-terminal-event premise.

Diagnostic3fbbc receives8 arguments, has44 fixed bytes (LINK-28 incl saved FP, three saved registers). Prefix =36+8+4+44=92. Wager hook adds4 return+18 scratch/SR+4 argument+4 C return. game_line_wager has4 fixed and division adds8 arguments+4 return+4 __udivsi3 save:142 total. Line order is a frame-free JMP replacement.

Task7 additionally calls62114 atb280. The57e88->61c50 call belongs to separate57e70, after57b78's RTS boundaries57e64/57e6e; it is not a Task7 fallthrough. The shared evaluators5323a/54b0e also enter through48cce/48cd6. The5c802 call belongs to5c334's argument26 arm, reached by Task6/bootstrap controllers; its address region does not establish a Task7 root. See INITIAL-ROOTS.md for complete upstream producers/prefixes and SETTLEMENT-GUARD.md for admitted settlement recurrence. The Test10 table alone is not complete shared-call coverage.

## Task7 serial reconfiguration

At c1a8, Task7/c0fc prefix is8 (D2 save plus call return); c0fc's earlier arguments were popped at c192. The replacement adds4 return and platform_reconfigure's12 fixed bytes =24 resident before outgoing calls. SC9 entry is40 and52 after its LINK-8.

| Subpath with wrapper resident | Bound | +1904 | Margin to2048 (historical b2) |
|---|---:|---:|---:|
| SC9 timeout0 drain | 82 | 1986 | 62 |
| 16682 delete/SC1 | 120 | 2024 | 24 |
| 15836 initialization | 132 | 2036 | 12 |
| 152ec SC0 creation branch | 110 | 2014 | 34 |
| 152ec ->16714 ->SC4 priority change | 158 | 2062 | -14 |

SC9:40 entry+12 wrapper+26 exception/context+4 helper return=82. Timeout0 skips388a;38c2 and scheduler slices do not recurse; creation/deletion/context hooks are zero under the already-sealed kernel premise.

Delete:28 at16682 entry+12 fixed+12 args+4 call+12 wrapper+26 context=94 at service entry. A deletion helper can add4 return+2 saved SR+4 call+12 saves+4 leaf call=26, giving120. This deliberately charges the full IRQ allowance even where its deepest section is masked.

Init maximum:28 at15836 entry+4 fixed+4 arg+4 call=40 at15552; +4 fixed+4 call=48 at1cc9c; +4 call=52 at1cd08; +36 fixed+12 args+4 call=104 at35cf4; +12 fixed+12 args+4 leaf return=132. Options5/6 are ROM descriptor type2 at73c1e/73c30, so35b48->35aec is a direct byte getter. Other checked branches are15508/154c0/1546a,18146,1864a,16488's serial initialization (13660/139f2/13eda/14dc2,52-byte driver saves),14a7e and its52-byte driver setters, protected copies, and1dd6/2374 RTC handling. Their peaks are below132 (serial setters120, RTC path<=110). Repeated loops pop their arguments.

Create:28 entry152ec+4 fixed+16 args+4 call+16 wrapper+26 context+16 create scratch=110. New child context26 is written to the child's stack, not additionally pushed on Task7.

Priority change proof:28 at152ec+4 fixed+4 call16714+20 fixed+44 accumulated args+4 call3cfc+12 wrapper+26 context+4 call380a+8 saves+4 call39a8=158. The fifth priority call16770 retains44 argument bytes;16776 pops40, leaving one long reused by the next call. 380a/384a/384e runs at caller IPL before2f0e raises it; the warning cannot be discarded using that later critical section. Task7 remains a normal IRQ-open task here.

Published06bef891 implements the global3072-byte native stack configuration, retaining native allocator ownership and the cancellation boundary. Historical b2 used2048. Emitted/frame identity, producer checks, offline tests, source/mathematical review and seven-document review passed. Main verified the exact eight copied artifacts and all20 production inputs; installation remains unqualified. See `SETTLEMENT-GUARD.md`, **Candidate gates**. The local figures transfer by instruction/frame identity, not by assuming the largest known root is universal.

## Standard SAS identity hooks (not private E0)

The qualified native SAS parent prefix is60 at command-handler entry (parent argument/call8,16366 fixed12, callback return4,19b16 fixed32, dispatch return4). Both28480 (1F) and2c95c (53) have32 fixed bytes. All their prior construction arguments have been popped. 53 additionally retains its zero argument at2cc24 before the patched JSR;1F's replacement at2872a precedes that argument.

1F:60+32+4 hook return+62 save_native+4 C return+4 identity D2 save+4 leaf getter return=170. 53 adds4=174. With1904:2074/2078, warnings26/30 over2048. fw_active_profile/fw_is_faulted are frame-free leaves; no IRQ exclusion applies merely because these are identity requests.

The full-register identity wrappers remain unchanged. Their2074/2078 conservative totals fit the published3072-byte native capacity, so no register-save optimization is needed under the approved mathematical composition and its premises. Native caller UNLK discards1F's synthesized zero argument as before. After the tail JMP278fe, no additional extension frame remains; unchanged TX is outside this changed-frame bound. Do not reuse E0's private stack for these hooks.

## Boot and clear C-live paths

All depths below are relative to reset SSP437ffe, not438000; allow an additional2 bytes against the nominal8192-byte reservation. Reset bca calls154e (4) and154e saves12. Startup hook1906 also retains12 native queue arguments. Boot window is only155c through1906; runtime37dde is not boot.

Emitted helper subtrees below entry:
- write_committed44 =20 fixed+12 args+4 return+8 platform_write_words.
- record_operation220 =160+12+4+44; finish_operation236 =12+4+220.
- write_profile_journal224 =144 fixed+32 accumulated args+4 return+44.
- baseline60 =8 fixed+24 accumulated args+4 fw_crc32 return+12 arguments+4 return+8 fw_crc32_update.
- bootstrap364 =284 fixed+16 retained arguments+4 second-baseline return+60.
- fw_boot556 =180 fixed+8 retained zero arguments+4 bootstrap return+364.
- fw_invalidate396 =28+4+364.
- fw_clear_complete272 =16+28+4+224.
- fw_restore244 on pending completion journaling =4 arg+4 return+236.
- Boot apply152 below game_apply_profile entry:60 fixed+8 context call+8 context fixed+8 fingerprint call+32 fingerprint fixed+24 retained args+4 CRC return+8 CRC fixed. Cache path148 is smaller;84-byte cache subtree reused from accepted audit.

| Hook | Whole C-live peak | +1904 +2 |
|---|---:|---:|
|155c boot validation |646|2552|
|1730 ->3756c ->37dde boot restore |426|2332|
|1906 final boot restore |350|2256|
|38386 clear begin |506|2412|
|38526 clear complete |378|2284|

155c:16 native+4 hook+62 save+4 C+4 fw_boot+556=646.
Boot loader:24 loader entry+68 fixed+16 tail retained args+4 hook+62 save+4 C+4 fw_restore+244=426.
1906:16 native+12 queue args+4 hook+62 save+4 boot_restore+4 platform_restore+4 fw_restore+244=350.
Clear begin:reset->3825a entry4+8 saved+4 call3836e+8 saved+12 args+4 hook+62 save+4 C+4 core+396=506.
Clear complete replaces the same shape with8 outstanding native args and272 core subtree, giving378. Direct cross-reference checks find3836e only at3833c and3825a only atb9e. This is reset-time clear, not a runtime task hook.

The original25b0 prelude runs before C saves (only the extra4 extension return). Its checked copy/fault leaves fit below the above boot maximum. C startup-fault code is0F, excluding38a06's code1/2 alternate branch:38a06 subtree48; platform_startup_fault56 below entry. It never starts tasks. These conservative arithmetic totals are not permission to admit unexpected interrupts before startup state is initialized.

## Runtime loader tail — local formulas

Let L be whole-task depth at3756c entry, including its return. At37dde, loader fixed68 plus16 retained arguments; extension call4+save62+C call4 gives platform_restore entry L+154. The replaced54a88 prelude is smaller.

For completed boot, no pending restore completion and no profile-change ownership: a custom slot is validated then rejected before admission/caches/writes; stock null-profile path does not rewrite native config. game_apply_profile's maximum is116 (60 fixed+8 call+48 slot_ok/validator). Therefore fw_restore<=144, platform_restore<=148, and whole local loader-tail bound is L+302. General recovery (without that lifecycle reduction) is L+402, dominated by244 fw_restore journaling. These do not claim rejection undoes native loader mutations that occurred before the tail.

Reconciled local caller transitions:
-63cd0 entry R:63db8 loader L=R+60 (20 fixed+36 args+4 return), local maximum R+362.
-64056 entry R:64120 loader L=R+40 (16+20+4), local maximum R+342.
-64056 entry R:6430a loader L=R+56 (16+36+4), local maximum R+358.
-64126 calls63cd0 while still retaining20 arguments, so child entry R+40 and its loader-tail local maximum R+402.

The36 retained arguments at6430a are4 at642b6 +8 at642c0/c4 +8 at642d6/d8 +8 at642ea/ec +4 at642fa (642f8 replaces the top) +4 at64306. Thus the loader-entry addition is56. Address6429c is an extension word of PEA6429a, not a function entry.

These parameterized local formulas alone are not whole-task bounds; upstream closure must be composed before comparing allocations. INITIAL-ROOTS composes SAS8C498, Task2/fault458 and corrected shared Task6 runtime470 from these local formulas. Do not present the local formulas alone as whole-task qualification.

## Fatal paths

Native platform_fatal usesTRAP1->fc4; fc4 stores register diagnostics to absolute RAM, not the stack, then sets2700 atfd0 and JMPs13ee. Its largest checked native subtree from platform_fatal entry is122 including the6-byte exception:6+8 arg/call1274+24 fixed+16 args+4 call3a652+20 fixed+20 args+4 call3a50a+20 fixed. Formatting/checksum/display paths do not lower SR;3a50a is a leaf. Protected fault counter writes restore the incoming2700.

For the reconfiguration wrapper's own invariant failures:<=28 at platform_fatal entry; untilfd0,<=34 resident plus1904=1938. Afterfd0,150 resident plus154 one-terminal-IRQ7 allowance=304. Boot calibration rejection is similarly tiny:34 beforeTRAP,156 after native fatal subtree; adding154 gives310. No coil-off/recovery claim. Native unexpected kernel/object errors remain outside the valid-object ownership premises.

## Integration limits

`INITIAL-ROOTS.md` retains the complete positive initial-root composition and its scoped mathematical approval. This ledger supplies local costs, not a second incoming-root census. The global3072 capacity and settlement entry guard are implemented/tested in candidate06bef891; frame identity supports reuse of these local costs. Re-establish that identity after code changes. Seven-document review and exact-artifact local publication are complete; installation remains unqualified.

The promise is extension-live/added-code work. Pure-native temporary peaks, financial termination, external invokers, corrupt kernel objects and repeated terminal interrupt edges are not qualified. Hardware and installation remain separate; no machine operation follows.

# BET ONE accounting and numeric ledgers

## Scope, evidence, and convention

These calculations use frozen `firmware/build/main.bin` SHA256 `b2ce1bc5c03b8e8af47cf246115968033f62574c2a3ae2d2d7e5c4f225adcb83`, verified before and after analysis.

Evidence: original `roms/s6000_main_S6M00002B5.bin` disassembled with m68k-elf-objdump, plus emitted `firmware/build/ext.dis:game_bet_one` at7bfa4 and `game_bet_one_entry` at7cd4c. All38 listed native accounting, numeric, first-wager, progressive, descriptor, and selected option dependency regions were byte-compared against frozen main.bin and match. Instruction addresses are stable binary locators, not document line numbers.

S(f) means maximum additional stack **below function-entry SP**, excluding the already-present return to f. Each edge contributes retained outgoing arguments +4 return +S(callee). Fixed bytes include saved FP, locals, and saved registers. Loops below clean up arguments on every iteration; no recursive accounting edges were found. These are upper bounds over branches, not measured traces or claims of joint branch reachability.

Reuse the 1904-byte IRQ allowance and returning native service ceiling64 from `IRQ-AUDIT.md` (SC20/21 included). Operation73's router has S(26a0e)=204; its positive per-route bounds are AS64, SAS68, BSG192, MIS128 and SDT24. Incoming-controller/root aggregation remains separate.

## Result

For the direct Task6 event9 route `9f2a -> a5e0 ->42f08 ->42fac ->game_bet_one_entry ->game_bet_one`:

- Returning changed-frame path: **284 bytes** resident maximum.
- Including the examined native accounting-corruption TRAP1 paths and conservatively charging their full fatal subtree: **322 bytes** resident maximum.
- Uniform conservative sum with the existing IRQ envelope: **322+1904=2226 <=3072**, leaving846 bytes. The returning-only sum is2188, leaving884 bytes.
- Both322 and284 are well below the requested1168-byte noninterrupt budget.

The3KiB capacity is present in published image06bef891; historical b2 had2048-byte stacks. These local bounds alone do not qualify all roots or code changes. The verified candidate frame identity and complete INITIAL-ROOTS composition supply that scoped mathematical bridge; arbitrary corrupted objects, whole-legacy temporary peaks and hardware remain outside it. Other callers use the complete initial-root aggregation in INITIAL-ROOTS.md. If R is whole-task resident depth **at42f08 entry including its return**, this local closure contributes at most **R+248 returning** or **R+286 including the examined fatal paths**. Direct Task6 hasR=36.

## Positive Task6/C caller accounting

`9f2a`: LINK-8 and D2-D6 save =12+20=32. Task entry's initial kernel context is consumed by restore/RTE, not a surviving return trampoline. At a5e0 the main event loop has no retained outgoing arguments. Event9 table77cc6+(9-1)*120 points directly to42f08 for native states0 and2. Other states' targets are not silently counted as this path.

`42f08` has no frame; earlier helper arguments are popped by42f86. Its lock call42fa6 has returned before the patched42fac. Therefore:

| Location | Whole-task resident bytes |
|---|---:|
|42f08 entry|36|
|game_bet_one_entry entry (42fac JSR)|40|
|game_bet_one entry (7cd4c JSR)|44|
|game_bet_one after D3/D2 saves|52|
|4341c or420f8 entry from C|56|

C has no local array or LINK frame. The reader's old4-byte argument remains while modulo arguments are pushed;7bff0 pops12 before comparison. Comparison's8 arguments are popped at7c004. Every call to4341c/420f8 has zero outstanding C arguments. The loop7c020..7c02a does not grow SP. `counter_read` at7b904 tail-JMPs549c8, adding no return.

Preflight reader:52+4 arg+4 call+S(549c8)172=232. Compare:52+8+4+S(548bc)172=236. Modulo:52+12+4+S(__umodsi3)16=84 (8 arguments,4 nested return,4 libgcc save). game_lines/custom_present are shallow, below these values. Refusal unlock runs after C has unwound:40+4+64=108. Unmodified42fb2 onward is outside this changed-frame bound.

## Numeric/helper closure

### Leaf and subordinate bounds

| Function | S | Derivation / scope |
|---|---:|---|
|400,426,44c|10|LINK0=4, A0=4, saved SR=2; no child call|
|480 /49e|0 /4|Global nesting count;49e temporarily saves D0. No child call|
|500|26|LINK0=4, five saved registers20, temporary SR2; bounded copy loop|
|3a8f4|4|D2 checksum loop; no calls|
|3a974,39f96,69bce|0|Copy/nonzero/subtract leaves|
|69bf6|10|LINK0+saved D2+saved SR; no calls|
|68bcc|8|All division branches; D2-D3 maximum|
|68c30|12|May BSR68bcc:4+8|
|68c4c|16|D2 save4+BSR4+68bcc8 (the actual modulo12 callers are smaller)|
|3a15c|28|12 saved+4 call+68c30(12); division path is smaller|
|3a23e|34|12 fixed+8 args+4 call+400(10); arithmetic path<=28|
|3a944|34|12 saved+8 args+4 call+400(10)|
|3b678|38|16 saved+8 args+4 call+400(10); both padding/copy loops pop8|
|3c9f4|8|A2-A3 save; all valid descriptor/address-selection branches are leaves, including zero-pointer dynamic record addresses|
|3cf86|16|A2-A5 save; all ten widths select pointer arithmetic, no calls|
|3c998|60|20 fixed+20 retained args+4 call+3cf86(16); type1 branch20+8+4+8=40|
|3bc5c|108|24 fixed+20 args+4 call+3c998(60); checksum/mirror-repair arms are smaller|
|38fd4,38ff2|0|Byte-swap leaves|
|38f1c|82|28 fixed+40 accumulated args+4 call+44c(10) at38fc4; earlier swap calls retain8/16/24/32|
|35840|12|D2-D4 CRC loop, no calls|
|35a26|110|16 fixed+8 args+4 call+38f1c(82); CRC44 and final protected store50 are smaller|
|36896|0|Native host-personality predicate leaf|
|34a18|54|12 fixed+28 accumulated args+4 call+400(10)|
|34a84|46|8 fixed+24 accumulated args+4 call+400(10), including failed-copy repair|
|349b2|70|4 fixed+8 args+4 call+34a18(54); read/repair arm58|

For3bc5c, valid type0 descriptors either pass their first checksum, recover from the mirror, or take the native fatal sink if both copies fail. The returning maximum108 is its descriptor-resolution call, not an assumption that checksum failure is impossible. Type1 simply returns. The two descriptor resolvers' invalid width/id traps cannot be reached with the audited immutable descriptors; native fatal accounting failures are separately charged below.

### Numeric function subtrees and retained arguments

In the following lists A is **all outstanding argument bytes immediately before the call**, not just that callee's newly supplied arguments. Each listed peak is F+A+4+S(child).

**3bd24: F=24, S=140**

| Callsite ->child | A | Peak |
|---|---:|---:|
|3bd30 ->3bc5c|4|140|
|3bd48 ->3c998|24|112|
|3bd4e ->480|24|52|
|3bd64 ->3a23e|36|98|
|3bd80 ->3a8f4|8|40|
|3bd8e ->400|8|46|
|3bda4 ->3a944|20|82|
|3bdb6 ->400|28|66|
|3bdbc ->49e|28 on mirrored branch,0 otherwise|60|

3bd6a pops36; type1 skips mirror writes. 3bd86 pops only4 before replacing the top argument, so the following mirror/checksum calls retain8/20/28, not independently8/12/8.

**3bdca: F=36, S<=158**

| Callsite ->child | A | Peak |
|---|---:|---:|
|3bddc ->3bc5c|4|152|
|3bdf4 ->3c998|24|124|
|3be08 ->3a15c|36|104|
|3be0e ->480|36|76|
|3be24 ->69bf6|48|98|
|3be40 ->3a8f4|8|52|
|3be4e ->400|8|58|
|3be64 ->3a944|20|94|
|3be76 ->400|28|78|
|3be80 ->36896|0|40|
|3be90 ->35a26|8|158|
|3be96 ->49e|8 if bank-accounting branch,0 otherwise|52|

3be2a pops48. Mirrored path3be7c pops28 before36896. This deliberately includes native AS accounting at35a26, not just the simple BCD increment. BET_ONE uses descriptor6a3f4 pointing43d49c, which matches35a26's first bank branch; the fallback is not an invented generic alternative. This branch executes under480 but no IRQ subtraction is needed for the conservative bound.

**3bea6: F=44, S=160**

| Callsite ->child | A | Peak |
|---|---:|---:|
|3beb8 ->3bc5c|4|160|
|3bed0 ->3c998|24|132|
|3bed6 ->480|24|72|
|3bef0 ->3b678|40|126|
|3bf06 ->3a15c|12|88|
|3bf18 ->69bce|24|72|
|3bf46 ->3a944 (null-output branch)|12|94|
|3bf62 ->3a8f4|8|60|
|3bf70 ->400|8|66|
|3bf86 ->3a944|20|102|
|3bf98 ->400|28|86|
|3bfb4 ->3a974 (nonnull-output branch)|12|60|
|3bfba ->49e|12 on nonnull-output branch,0 otherwise|64|

3bef6 pops40;3bf1e pops24 before possible underflow TRAP1 at3bf26. Null-output path pops12 before testing mirror type; mirror path pops28. All BET_ONE callers supply null output, but the nonnull branch is included and is smaller.

**3c090: F=56, S=192**

| Callsite ->child | A | Peak |
|---|---:|---:|
|3c0a2 ->3bc5c (destination)|4|172|
|3c0ba ->3c998|24|144|
|3c0c2 ->3bc5c (source)|24|192|
|3c0da ->3c998|44|164|
|3c0e4 ->480|0|60|
|3c128 ->3b678 (width-padding branch)|16|114|
|3c142 ->3a944 (both width branches converge)|12|106|
|3c15e ->3a8f4|8|72|
|3c16c ->400|8|78|
|3c182 ->3a944|20|114|
|3c194 ->400|28|98|
|3c19a ->49e|28 on mirrored branch,0 otherwise|92|

The crucial retained24 at3c0c2 survives from the first resolver; replacing the top with A3 does not pop it. 3c0e0 pops44. Wider-destination branch3c12e pops16 before preparing the common copy. This is why the second validator, not the first one, determines192.

### Counter-reader and comparator closure

-3bc18 F24: validator24+4+4+108=140 dominates its resolver and nonzero check.
-3bfca F24: resolver's20 args remain for validator,24+20+4+108=156; conversion3a04e is an8-byte leaf.
-3bbae F32: validator32+4+4+108=148; numeric conversion and3aa4a (4-byte leaf) are smaller.
-54912 F8:8+4+4+140=156.
-549c8 F8:8+4+4+156=172.
-548bc F12:12+8+4+148=172.
SC20/21 calls under these functions fit the reused64 ceiling and are smaller than the accounting branches.

## Remaining53dd0 arms and4341c

The53dd0 dispatch table at53dfa was decoded directly for operations46/4c/55/56/a6. Its fixed frame is8. Targets are542d0->46cce,5431c->472a2,54382->4687a,5438c->468de,54748->47c60. Only4c pushes an extra4-byte argument. The lock/unlock service paths are at most8+4+64=76 below53dd0 entry.

| Route | Target S |53dd0 S for route| Derivation / retained args |
|---|---:|---:|---|
|55 ->4687a|190|202|468c6 retains28 before3bdca:28+4+158;349b2 retains32, giving106|
|56 ->468de|190|202|4691e/4696c retain28 before3bdca; second3bea6 retains20, giving184|
|a6 ->47c60|204|216|8 args+4 call+3c090(192)|
|46 ->46cce|152|164|8 args+4 call+3bd24(140);500 path retains28 but is58|
|4c ->472a2|156|172|LINK0=4+8 args+4 call+3bd24(140); router additionally pushes4|

All468de branches through46a9e were read, including:
- two funded-counter variants, each retaining12/20/28 across subtract/subtract/increment;
- aggregate counter and optional secondary subtraction, with the intervening read retaining16;
- unfunded-credit option, increment then349b2 (8 then12 args);
- final ordinary-credit subtraction and SAS credit paths;
-578a0 ->35b48(option32), whose immutable descriptor73f48 is type2 pointing43e8a1: leaf35aec byte getter, S(578a0)=20;
- final3bdca call6a3bc with8 args. All are below190.

Immutable descriptor facts: widths3..5 in the relevant counter set.6a1b4 has zero direct pointer/type1/id50;3c9f4 resolves it through the leaf dynamic current-record arm3caf0, not an unknown function pointer. Type0 pointers used by the optional bank accounting are43d49c (6a3f4) and43d4ba (6a21c). Type1 counters may skip mirror work; bounds above still conservatively cover both types.

4341c has no fixed frame:

| Edge from4341c | Retained args | S including edge | Whole-task direct-route peak (entry56) |
|---|---:|---:|---:|
|43420 ->54912|4|164|220|
|4342c ->37dee|0|54|110|
|43438 ->53dd0(a6)|8|228|284|
|43446 ->53dd0(56)|8|214|270|
|43452 ->53dd0(55)|12|218|274|
|4345c ->26a0e(73)|16|224|280|

The55 call retains12 because4344c replaces the old top with zero and4344e pushes only the new opcode; event73 adds another4, then43462 pops16.37dee F20 has maximum50 below entry (two final SafeRAM stores retain16); its variable loop pops8 each iteration, and integer modulo has bounded register-only helpers. Thus **S(4341c)=228**.

## First-wager420f8 closure

420f8 F4. Its configuration-clearing runs accumulate at most40 argument bytes, all discharged before following phases. Other leaves/helpers:50086 is leaf;38ad0 S38 (16 fixed+8 args+4+40010);35fdc S30 (4 fixed+12 args+4+40010). All were read through RTS.

|420f8 subpath| Below420f8-entry peak |
|---|---:|
|421fc ->53dd0(46),8 args|4+8+4+164=180|
|423d8 ->53dd0(4c),20 retained args|4+20+4+172=200|
|423fa ->1d48a(operation6),8 args|4+8+4+108=124|
|largest straight SafeRAM clear,40 args|4+40+4+10=58|
|42388 ->500,32 args|4+32+4+26=66|
|423a8 ->38ad0,12 args|4+12+4+38=58|
|42488 ->35fdc|4+4+30=38|

Therefore **S(420f8)=200**, whole-task direct-route peak56+200=256.

Progressive proof:420f8 passes exactly6/0 at423f4..423fa.1d48a F20 calls leaf1cda6; when enabled it passes those arguments to1c438. When the progressive route is not selected, its comparisons for3/1/2 all fail for6 and it returns at1d4f8.1c438 F8 selects table1c460 entry(op-1)=5 ->1c46e; it increments43d458, clears43d457, and calls **leaf1ce06**, then unlocks. It does **not** select1c9b0. S(1c438)<=76 using the64 native service ceiling; S(1d48a)<=20+8+4+76=108. No progressive transmission/task body is synchronously called on this operation.

## Accounting fatal branches

Reuse the already inspected native TRAP1 ->fc4 ->13ee fatal subtree122 bytes **including its6-byte exception frame** from the preceding Task35 local ledger. It sets2700 atfd0, does not return, and is not motor fault/recovery/coil-off.

Deepest reachable accounting-corruption trap is the second3bc5c under3c090:
56 (4341c entry)+12 (a6 call)=68 at53dd0;
+8 fixed+4 call=80 at47c60;
+8 args+4 call=92 at3c090;
+56 fixed+24 retained+4 call=176 atsecond3bc5c;
+24 fixed=200 at3bcc2 (both checksums invalid; previous args already popped).
Adding122 gives **322**. Beforefd0 the prefix is206, so even charging full1904 there is2110. Uniformly charging1904 to all322 is conservative and still below3072.3bea6's underflow trap at3bf26 is shallower, at most148 before exception on this root. Invalid ROM descriptor widths/IDs and corrupt kernel ownership are outside the frozen valid-object premise; no general corrupt-pointer stack guarantee is claimed.

## Integration boundary

The BET ONE numeric/accounting and first-wager subtrees have a complete direct Task6 changed-frame bound and an R-parameterized formula for other callers. Incoming prefixes belong to INITIAL-ROOTS.md; native allocator ownership/capacity belongs to TASK-STACK-AUDIT.md. This path needs no local stack optimization or semantic restriction under the approved global3KiB mathematical composition and its stated premises; no installation approval is implied.

# Initial-root composition boundary

Complete initial roots, including fault-controller ancestry, Task3P104, Task6 helperP64/P52,43c2 and both a5e0/ad72→cf54→62958 routes, are authoritative in `INITIAL-ROOTS.md`. Its AS/BSG selectors are acyclic: AS FIRST240; BSGQ220/P224/FIRST268. It composes X72/Y64, shared57e70 FIRST88, normal416/evaluator306/completed-runtimeLoader498, bootstrap FIRST58/sharednormal302/generalLoader532/early648. These are initial-entry bounds, not later-recursion limits.

The admitted589ba proof remains `SETTLEMENT-GUARD.md`, **Covered cut graph / Reserve and induction**, using M328/G120/H2232/S10/I1904. No finite financial-round count is needed or claimed. Historical one-descent/loose-bootstrap calculations and lifecycle-investigation TODOs are superseded, not live qualification requirements. The local numeric/continuation ledgers below remain their own cost/domain authority.

# Settlement guarded descendant ledgers (frozen-b2 design)

These are the consolidated positive native call-domain inputs to
`SETTLEMENT-GUARD.md`, **Covered cut graph**. They were read through returning
exits using original-ROM instructions and independently composed against frozen
b2. Main verified candidate06bef891's final producer and instruction/frame seal;
`SETTLEMENT-GUARD.md`, **Candidate seal and offline gates**, records the passed
checks, full affected tests and independent source review. This preserves the
local admitted-region proof, not an alternative to INITIAL-ROOTS.md for pre-first-check ancestry.
S excludes the existing incoming return; F includes FP/locals/register saves;
each edge adds **all retained arguments** plus4 return. Already documented
numeric/descriptor/fatal families are reused by the headings **Numeric/helper
closure**, **Counter-reader and comparator closure**, and **Accounting fatal
branches**, not duplicated here. General task service ceiling64 is distinct
from IRQ-only26. No exact-ROM native financial-history premise is added.

## Additional owned numeric helpers

|Helper|F|S|Controlling edge (A=retained args)|
|---|---:|---:|---|
|3bae6|44|180|3bb18 second3bc5c A24:44+24+4+108|
|3c00e|36|168|3c03c validator A20:36+20+4+108|
|3c2f6|64|200|3c32c second validator A24:64+24+4+108|
|3c728|48|164|3c73e validator A4; padding A40 peak130 is smaller|
|3c7ec|40|152|3c7fe validator A4|
|3c8da|32|144|3c8ec validator A4|
|547d4|8|192|5480a ->3c00e A12|
|54872|8|164|548a4 ->3c8da A8|
|5495c|12|204|549b0 ->3bae6 A8|
|54a12|8|188|54a42 ->3c728 A12|
|3ab76|12|56|3ab86 ->3a15c A12; A24 additive leaf smaller|
|3aba2|24|68|3abc4/3abee ->3a15c A12; largest A36 subtract leaf peak64|
|3c968|12|56|3c978 ->3a15c A12; comparator A28 peak48|
|3b73a|12|36|3b76e ->68c30 A8 (previous protected-write args retained)|
|3b798|12|64|3b7d2 ->3b73a A12; loop pops12 every iteration|
|3b77e|4|84|3b78e ->3b798 A12|
|3a038/3a0d4/3a0ea|4|24|A8+return+3a04e8|
|3af58/3af6a|4|36|A4+return+24|
|35aec|0|44|type4/5 A4+return+36; type2/8 leaf|
|35b48|4|56|A4+return+44, uniform valid-descriptor bound|
|38dfe|8|72|A4+return+35b48; all following personality cases are leaf table reads|
|34968|8|62|A4+return+34a84(46)|
|34990|4|70|A8+return+34a18(54)|
|38dc8|8|90|A8+return+34990(70)|


All these helpers restore their saves/FP on each returning exit; loops discharge
arguments per iteration. Data-selected descriptor addresses are not callable
pointers. Underflow/checksum failures use the native TRAP1 halt, separately from
valid immutable descriptor width/id selection. No helper here calls a controller,
covered game hook or stack-growing callback cycle.

## Selected settlement accounting opcodes

`53dd0` has F8; dispatcher check `53de6` bounds opcode0..b4 and signed-word
table `53dfa` selects cases. The previously closed46/4c/55/56/a6/A8 domains
remain in **Remaining53dd0 arms and4341c** and `IRQ-AUDIT.md`, **IRQ4/5 called
targets**. Each selected case forwards4 bytes unless marked noarg; entry
contributes F8+arguments+4 return+target S. The following are additional selected,
not universal opcode coverage:

|Hex op|Target S|53dd0 S|
|---|---:|---:|
|04 ->4411c|178|194|
|05 ->44174|174|190|
|06 ->441bc|174|190|
|0a ->443dc(noarg)|228|240|
|0c ->4418c|156|172|
|0d ->44482(noarg)|170|182|
|0e ->444fe|174|190|
|0f ->44516|186|202|
|10 ->445b0|182|198|
|11 ->446ac|174|190|
|12 ->44680(noarg)|170|182|
|14 ->44788|174|190|
|15 ->44804|156|172|
|19 ->44aac|178|194|
|1a ->44bdc|188|204|
|1b ->44c88|174|190|
|2e ->45b94(noarg)|178|190|
|37 ->46098|156|172|
|39 ->4645a|174|190|
|46 ->46cce(noarg)|152|164|
|66 ->46ae8|180|196|
|67 ->46b02|174|190|
|68 ->441a4|156|172|
|69 ->46d14|174|190|
|6a ->47390(noarg)|170|182|
|6b ->473a6(noarg)|170|182|
|6c ->447ec|174|190|
|96 ->44cb8|174|190|
|97 ->44850|178|194|
|98 ->448dc|174|190|


Targets are numeric/protected-store functions, not callbacks. For example
`443dc` F12 calls `3c2f6` with12 retained bytes (228), and `45b94` retains12
at its second increment (178). Its later common increment retains8. This
positively closes post-recursion operation2e; it is not a financial-consumption
argument. After optional argument removal, dispatcher tails join `547c8`
(SC_UNLOCK, D2 restore, UNLK/RTS).

The following22 rows complete the continuation/controller boundary union. Their
positive child families and cleanup are part of the domain proof; numerical
whole-native maxima for these rows are not required by the extension-live gate.

| Hex opcode | Case -> target | Complete additional descendant class |
|---|---|---|
|00|53f64 ->44094|two3bdca counter additions;UNLK discards retained args|
|13|5405a ->446f0|3bdca,38dc8,44c,400,3c090; explicit12/28-byte cleanup|
|17|54092 ->44a1a|conditional3bdca;UNLK|
|18|540a0 ->44a3e|36896 leaf,3bdca,38dc8;12-byte cleanup|
|2f|541ae ->45cac|3bd24 or400/426; common8-byte cleanup|
|30|541b8 ->45e1e|349b2,548bc,3a04e,3b092,3ae3a,54912; data-indexed record pointers, no code-pointer call|
|31|541c2 ->45d04|547d4,3b77e,3c7ec/3c090,3bd24;UNLK|
|54|54378 ->4674a|two3c090;16-byte cleanup|
|57|54396 ->46c6a|two3bdca,349b2;20-byte cleanup|
|58|543a0 ->4684a|3bd24;UNLK|
|59|543ae ->46d48|35b48(10),3bd24;12-byte cleanup or immediateRTS|
|5a|543b8 ->46d94|3bd24;UNLK|
|5c|543d0 ->46e3c|3c090;8-byte cleanup|
|61|5440a ->4794a|conditional3bd24;8-byte cleanup|
|62|54414 ->467a4|data-indexed immutable descriptor ->3bd24;8-byte cleanup EACH loop, savedD2 restored|
|6f|544ae ->44a98|3bdca;8-byte cleanup|
|70|544b8 ->4797a|index<256 constructs stack data descriptor, then3bdca;UNLK|
|71|544c6 ->47a50|47a24 + two3bd24;16-byte cleanup, restoresD2/D3|
|72|544d4 ->47a84|same domain, different immutable descriptors|
|73|544e2 ->47ab8|same domain, different immutable descriptors|
|74|544f0 ->47aee|same domain, different immutable descriptors|
|94|5467c ->47d1c|3bdca;UNLK|


Opcode30's additional tree is `349b2→34a84/34a18`, `3b092→39eb8/39ed2→69bf6`
(fixed width4/5 BCD-add leaf), and `3ae3a→3ad9c/3a20a/3a92e/3a9f8/35a26`.
`3ad9c` validates/repairs a fixed five-byte record using checksum/copy/scalar
helpers or TRAP1; `3a20a/3a92e/3a9f8` specialize the accepted conversion,
checksum and copy helpers to width5. `35a26→38f1c/35840/426`; `38f1c` calls
only byte-order leaves `38fd4/38ff2` and44c. Final20-byte cleanup restores SP.
`47a24→38dfe/68bcc`; the first returns DATA and a zero divisor is retained as
DIV0/fresh-SP, not excluded by a made-up nonzero invariant. No selected row
interprets its amount, counter pointer or queue message as code.

## Owned scalar and selector boundaries

| Family | Positive children / boundary and balanced-return evidence |
| --- | --- |
|23b78|Boolean leaf through23bec;23bee is a different function|
|4fd96|type2 option5,363c2,400; eight-byte cleanup in loops, RTS4fe92;4fe94 is separate|
|33c2e /58970|numeric3a04e / fixed queue services; no receiver invocation|
|5ab18 /5a256 /59af8|548bc / option6 /35b48+4ef34; no callback|
|36948 /1e1aa /1e1c6|option getter or scalar leaves|
|1e258|literal5c266 operation4 selects a leaf; not arbitrary controller dispatch|
|24d6a /24f58|selected15/96/97/98, numeric counters,17b14 publication; retained48/40 arguments at24f46/25060; loop25066 pops40|
|51da2|frame-free51dbc/520ac; numeric/limit helpers; only external599ba boundary is entry+60 via520ac:52222|
|523a4|numeric547d4/5270e and selected6a; only external router201 boundary entry+72 at524a4|
|5a16c|numeric15 and fixed waits; only router113 boundary entry+12; each wait pops4|
|34d22|scalar mode/data publication; routerF6 boundary entry+16 at34db4|

`51dbc/520ac` use `5270e→53dd0(14/37)` and `527e2/52852→38dfe` data
selection. No additional callback is hidden behind a limit/paytable pointer.
All normal child returns balance; TRAP1 halts and DIV0 resets SP. At native-only
sites the already-earned optional ceilings E+454 numeric/fatal and E+540 F6
are **not** included in the covered328 promise and are not silently qualified.

## Controller and continuation composition

The complete child domains are numeric/scalar/output/formatter/queue services,
with only the following router/accounting boundaries. All selected boundaries
are closed in **Selected settlement accounting opcodes** above and
`SETTLEMENT-GUARD.md`, **Selected communication domain**. No stability of MODE
across waits, unlocks or sequential protocols is assumed.

| Owner | Child families and exact delegated entry offsets below owner |
| --- | --- |
|3fefa (F12)|404aa queue drain;4570→router210 +24;36594 stores;35962 queue/copy;380d6 RNG; progressive7; accounting2f/30/31 +24/28/28|
|5aa68(6 or7), F4|4e412,option2;34d22→F6 +28; router31 +16;a64c;58970 queue; arguments popped5aa8c and before5aae0|
|599ba (frame-free)|4e412,options2/11,5a256,59b4c; F6 +24; router31 +8; accounting18/15 +12/16; explicit argument cleanup and RTS (599f2/59ae6 and final router cleanup)|
|59b76, F20|4df4a,38dfe,arithmetic, progressive1, mailbox/delay; F6 +44; accounting17 +36; retry/delay cleanups59ca8/59cee/59d42; body includes RTS59ea4|
|4b78e, F28|fixed counters,4e188,3a652,63ac6; no controller callback; reader retains12, formatter28 at4b8be|
|1d70c|progressive2 with current masked snapshot; later independent accounting71..74 +12; no assumed stability between reads|

Progressive `1d48a` operations1/2/7 select `1c438` cases `1c4a6/1c4d4/1c4b8`
and leaf `1ce06`, or17b14 data publication. Operations3/4/5 used below61172
select numeric/queue work; op4's `1c9b0` uses426 and leaf1ce06. They do not
invoke the asynchronous progressive task inline. All use balanced unlock/return.

The complete30-entry `a64c` display domain and its separation from legitimate
controller entrants are durable in `SETTLEMENT-GUARD.md`, **Display callbacks**.
Display children use counters, protected writes, BCD/display formatters, option
getters and bounded local switches; none invokes another controller callback.
In particular `4bb9a→63b62/(4bbba or4bf22)`, `4c3aa→4cea4→4de7e`,
`4b56a/4b578→4cc38/4cb2a`, and `56176→4c7ec/6828e`. Formatter trees
`38c52/38d14/4e128/4e188/4e1e0/3a652/3a6b6/3a73c/3a764` terminate in
fixed division, byte-copy, checksum and3a50a/34b3e leaves. Options are immutable
type2 direct reads except10/11 type4 and1b type5, whose BCD-reader wrappers
are included. Local switches `4b372/4cc58/4dba4/4dce4/4ddb0/63a94` and
`5c28c` have bounded tables, not arbitrary function pointers. Delays/waits may
last indefinitely but do not accumulate frames.

### Positive continuation child domains

The root closure includes these branch families, not just the straight61e0e
normal-start edge. Delegated routers/accounting use the exact selected rows
already retained above and in SETTLEMENT-GUARD; fixed kernel services only
queue values or resume saved contexts.

| Parent | Complete child families / covered or delegated boundaries |
| --- | --- |
|61c50|early62348;400/426,620aa,delay3a1e,queue3b84,61ff8→50086 leaf;61172;53dd0(4c/62/61/5c)|
|62348|64a28 scalar/counter/option family;629b0→53dd0(00);62422→router30 and progressive4;6262c→4360 mailbox,49f82/41152/output leaves,option reads,queue/mailbox|
|61172|numeric54912/548bc/549c8/4341c;400/426/44c,option/personality,delay/input396e2/3976c,cache/copy36028/38ad0;34d22→F6;a64c;router34;progressive3/5;3fbbc diagnostic;queue3b84;5d368 native motion;format38c52/34b3e;64d5a→14d3e→14c66 TX; selected53dd0 rows|
|3fbbc→664a0→6728a→67e76|numeric/typed options,3a04e conversions,protected stores,RNG380d6/fixed arithmetic/sampling/copy leaves;selected accounting6f/61/70 and3fefa; no evaluator/controller table callback|
|59f3a|63ac6,5a102→34b3e formatting,mailbox3a52,400/44c/500,queue3b84;599ba;53dd0(13/14);1d70c;a64c;61c50;34d22;3fefa;delay3a1e;5aa68(6)|

The diagnostic downward chain uses entry increments36 below61172,76 below
3fbbc,64 below664a0,72 below6728a and28 below67e76 to3fefa. It is native-only
after each early changed-hook wrapper returns; it is not charged as a second192
frame. Its only controller boundary is the already closed3fefa family. ROM7f2=0
excludes61172:61678→604fa; ROM7fe=0 excludes5d368:5d3ae→34d22 and the alternate
5d3c0 branch. These are fixed-ROM control-flow facts, not assumed RAM history.
Other mutable predicates are overapproximated. Local bounded switches include
6632c/67b4e/67ee4,60fe6,629cc and14b7e; compare/range checks precede indexed
word loads, and defaults/returning continuations are included. No missing
SC_DELAY fallthrough is taken as an unreachable branch.

### Covered continuation prefixes and unwind

| Covered native point | Below61c50 entry | Below59f3a entry |
| --- | ---: | ---: |
|61172 entry / game_start_entry|4|24|
|615ce candidate JMP|28|48|
|615fa commit JSR entry|32|52|
|3fbbc entry via6171e|40|60|
|3fbcc line-order JMP|84|104|
|3fc34 wager JSR entry|88|108|

The accepted192-byte **changed-hook**, not whole-native61172, envelope gives
196 below61c50 and216 below59f3a. `61e0e` has no retained arguments;
`59f3a:5a082` adds20. The diagnostic entry adds36 below61172 (F24+A8+4).
`game_start_entry` refusal returns without creating native F24; success restores
its wrapper then replays that frame and JMPs6117a. Candidate/commit wrappers
restore scratch before native continuation. No permanent extension frame remains.

`59f3a` F16: loop59f7a removes16 each iteration; timeout59fc4 and5a0e2 retain24
until FP-based restore/UNLK5a0fe/RTS5a100. `61c50` is frame-free;61ebc pops16
before RTS61ec0. `61172` restores from FP slots at61920, UNLK61926 discards
final arguments before RTS61928. Deeper diagnostic frames3fbbc/664a0/6728a/
67e76 are ordinary balanced returning frames. These facts bound covered work
on return; they do not assert safety of excluded native-only temporary peaks.
