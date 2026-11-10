# Native extension stack ledgers

This document records, instruction by instruction, the **local** stack cost of every
native (stock-firmware) code path that the experimental firmware extension changes,
hooks or calls. A local cost is how many bytes a path can push below a stated entry
point (arguments, return addresses, saved registers, frames and nested callees)
before it returns. `TASK-STACK-AUDIT.md` uses these costs to show that each native
task stack has room for the extension's added work plus a worst-case interrupt
allowance, which matters because the extension runs C code and hooks inside native
call chains whose stacks were sized for the stock firmware. All figures are upper
bounds calculated from disassembly, not hardware measurements.

**Status.** The costs were first established on a historical frozen build called
**b2**. They carry over to the published image, candidate `06bef891` (see
**Definitions**), because its instructions and stack frames were verified identical
to b2's. How these local costs combine into complete whole-task bounds is proved in
`INITIAL-ROOTS.md`, the single authority for complete initial-root producers,
selector DAGs, retained incoming prefixes and finite composition; that mathematical
proof has been independently reviewed and approved under its stated premises. An
independent review of all seven maintained proof documents and the final pinning of
build inputs and outputs also passed, and the exact reviewed `06bef891` artifacts
were published locally with `install_ready=false`. Historical b2 remains provenance
only. No guarantee follows for whole-legacy temporary stack peaks, for financial
termination (settlement eventually finishing) or for hardware: **hardware and
installation remain unqualified**, and nothing here authorizes a machine operation.

The stack and interrupt audit records divide the proof as follows:

| Document | What it is authoritative for |
| --- | --- |
| `INITIAL-ROOTS.md` | Initial-root producer domains, the immutable callback selectors and their weighted edges (the AS and BSG selector DAGs), all 27 controller arms, unguarded incoming prefixes, positive synchronous frontier contracts and the complete finite composition |
| `NATIVE-STACK-LEDGERS.md` (this document) | Local emitted costs: numeric, diagnostic and wagering, serial, loader, early-boot and fatal, and admitted-settlement descendant costs |
| `SETTLEMENT-GUARD.md` | The settlement entry guard at `0x589ba`: the admitted cut graph, regional producer seal, allocation identity and the H 2232 / M 328 / G 120 / S 10 induction, plus the candidate seal and offline gates |
| `IRQ-AUDIT.md` | The 1904-byte interrupt allowance I, positive IRQ and private-E0 exclusion, and the calibration, timer and bus premises |
| `TASK-STACK-AUDIT.md` | Native allocation capacity and qualification status |

A summary in one record is never a second authority for what another record owns.

## Definitions

These terms are used throughout. Where a term is owned by another record, the
meaning given here is the one that record uses.

| Term | Meaning |
| --- | --- |
| native | Code and data of the original main program S6M00002B5, `roms/s6000_main_S6M00002B5.bin`. Native addresses are CPU addresses in that program. |
| extension | Code the firmware build adds in the reserved ROM region `[0x79100, 0x80000)` (`firmware/patches.json`), reached through patched hooks in native code. |
| b2, frozen b2 | The historical frozen extension build on which these costs were calculated. It had 2048-byte native task stacks. Its pinned hashes are under **Inputs and conventions**; it is no longer the published build. |
| candidate `06bef891`, published image | The published image, `main.bin` SHA-256 `06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`, with 3072-byte native task stacks and the settlement entry guard. |
| instruction/frame identity | The verified comparison of the final image with b2 recorded in `SETTLEMENT-GUARD.md`, **Candidate seal and offline gates**: the 5,601 previously decoded extension instructions keep their operations and frame operands, the 272 operand changes are exact +104-byte address relocations, and `ext.stack` is identical. It is why b2's local costs apply to `06bef891`; it must be re-established after any code change. |
| Task*N* | Native kernel (VRTX) task *N*. Task6 runs the main game event loop at `0xa5e0`; Task7 runs diagnostics such as motion Test10 and the serial reconfiguration. Code is attributed to a task by its positive call producers, never by its address region. |
| SC*n* | The VRTX kernel service with function code *n* (hexadecimal), called with TRAP #0. Used here: SC0 task creation, SC1 task deletion, SC4 priority change, SC9 mailbox pend, SC20/SC21 scheduler lock and unlock (`firmware/src/game.c`; `IRQ-AUDIT.md`). |
| SR `0x2700`, IPL, IRQ-open | `0x2700` is the status-register value for supervisor mode at interrupt priority level (IPL) 7, which masks every maskable interrupt. A path is IRQ-open where interrupts are not masked. |
| I, interrupt allowance (1904) | Stack bytes charged for interrupts: the sum of the per-level components in `IRQ-AUDIT.md`, **Conservative native interrupt components**. It overapproximates co-occurrence; it is not a trace showing all components live at once. |
| terminal IRQ7 allowance (154) | The IPL 7 (AC-fail) component alone, charged once SR `0x2700` has been set and lower interrupts can no longer arrive. "One filtered terminal AC-fail" is the premise that this interrupt is asserted at most once. |
| service ceiling (64) | Stack bytes charged for any returning native kernel-service wrapper called from a task (`IRQ-AUDIT.md`, **IRQ2 communication and service ledger**). The same services called from an interrupt are charged 26 ("IRQ-only 26"). |
| private E0 | The extension's private SAS long poll `0xE0`. Its handler switches to a separate 4096-byte stack; its worker allowance is 1024 bytes. Standard SAS commands never borrow that stack. |
| native fatal halt (TRAP1) | `platform_fatal` executes TRAP #1, whose handler at `0xfc4` records diagnostics and halts without returning. The exception frame is 6 bytes. A divide-by-zero exception (DIV0) is different: it reloads the reset stack pointer (a fresh SP) and restarts. |
| covered, changed-hook, C-live, extension-live | Work done while an extension frame is live on the stack. Covered hooks are the patched sites in `INITIAL-ROOTS.md`, **Covered patch-family inventory**. C-live means while extension C code is on the stack. A changed-frame bound covers only the frames present while the change is live; native work after the wrapper unwinds is outside it. |
| root, initial root | A place where a call chain starts: reset, a task entry, or a dispatch-table entry. Initial bounds apply before any admitted settlement entry is live. |
| producer, producer seal | A producer is anything that can supply a control transfer or call target: a direct call, a table, a computed load. The producer seal (`SETTLEMENT-GUARD.md`, **Producer seal**) is the census showing every producer that can reach a region is known. |
| selector DAG | The AS and BSG callback selectors are immutable ROM tables that choose which callback runs next. `INITIAL-ROOTS.md` shows both selector graphs are directed acyclic graphs, so callbacks cannot recurse through them. |
| controller | A native function that dispatches further work through controller tables or callbacks, such as `0x3fefa`, `0x599ba` or `0x5aa68`. The controller-table consumers are `0x487a6`, `0x489ae` and `0x48ada` (`SETTLEMENT-GUARD.md`, **Producer seal**). |
| continuation | Native code that runs after a hook returns or tail-jumps, for example `0x61c50` and `0x59f3a`, which continue into the normal-start path at `0x61172`. |
| settlement entry, admitted, E | The extension's `game_settlement_entry` checks remaining stack at every entry of the native settlement function at `0x589ba`; an entry that passes is admitted. E is the resident depth at an admitted entry. M = 328 is the covered ceiling, G = 120 the growth to the next check, S = 10 the guard scratch and H = 2232 the reserve (`SETTLEMENT-GUARD.md`, **Reserve and induction**). |
| FIRST, normal, evaluator, loader bounds | Whole-task bounds that `INITIAL-ROOTS.md` composes from these local costs. FIRST is the depth at a first settlement check, with no already-live admitted `0x589ba` ancestor. Normal is the normal-start changed-hook family at `0x61172` (P + 192), evaluator the wager-evaluator family entered at `0x65128` (Q + 86), and loader the runtime loader tail at `0x3756c` (L + 302). Native results: normal 416, evaluator 306, completed-runtime loader 498, FIRST 268. Bootstrap results: FIRST 58, shared normal 302, general loader 532, early boot 648. |
| bootstrap stack | The reset stack `[0x436000, 0x438000)`, 8192 bytes. The reset stack pointer is `0x437ffe`. |
| router `0x26a0e` | The native communication router. It passes an operation key to the handlers of five protocols, AS, SAS, BSG (Bally Secondary Game), MIS and SDT, each of which selects its case for that key or a default (`SETTLEMENT-GUARD.md`, **Selected communication domain**). Router keys are hexadecimal. |
| accounting dispatcher `0x53dd0` | The native switch over accounting opcodes `0x00`–`0xb4`, through the signed-word table at `0x53dfa`. |
| descriptor | A data record describing a counter or an option: type, width, id and data pointer. Most are immutable ROM records; accounting opcode `0x70` instead builds a temporary descriptor in its stack frame. Descriptors are data, never called as code. |
| MODE | The native game-mode byte that indexes dispatch tables, for example the `0xa64c` display table. |
| SafeRAM, protected stores | SafeRAM is the protected RAM region `0x438000`–`0x43ffff` (`notes/machine.md`). `0x400`, `0x426` and `0x44c` are the native protected-store routines that write it. |
| coil-off | De-energizing the reel-drive latch. No path here claims it. |

### Stack-accounting notation

| Symbol | Meaning |
| --- | --- |
| S(f) | Maximum additional stack **below the entry SP of function f**, excluding the return address already pushed by the call to f. |
| F | A function's fixed bytes: saved frame pointer, locals and saved registers. |
| A | **All** argument bytes outstanding immediately before a call, not only the ones that call newly pushes. |
| edge | Each call contributes its retained arguments A, plus 4 bytes of return address, plus S(callee). A listed peak is F + A + 4 + S(child). |
| R | Whole-task resident depth at a stated entry, including that entry's return address. INITIAL-ROOTS writes P = R(`0x61172`), Q = R(`0x65128`), L = R(`0x3756c`), X = R(`0x5c334`) and Y = R(`0x6268e`). |
| resident, whole-task | Bytes in use on the task's stack, counted from the top of its allocation. |

Addresses, opcodes, router keys, option numbers and descriptor fields are
hexadecimal and carry a `0x` prefix, in tables as in prose. Byte counts, event
numbers, MODE values, progressive operation numbers and test numbers are decimal.
Extension addresses (`0x79100` and above) are b2 link addresses; extension code is
otherwise identified by symbol.

# Boot, diagnostic, serial and loader ledgers

This part covers the Task7 hooks, the standard SAS identity hooks, boot and clear,
the runtime loader tail and the fatal paths.

## Inputs and conventions

The evidence is the emitted extension disassembly `firmware/build/ext.dis` and the
pinned original ROM, disassembled with `m68k-elf-objdump -D -b binary -m m68k:68000`.
(`firmware/build/` holds build outputs written by `firmware/build.py`; it is not
committed.) These are instruction-led calculations, not hardware measurements.
Native addresses identify instructions, not document line numbers.

Verified hashes of historical b2 (provenance, not current build pins):

| File | SHA-256 |
| --- | --- |
| firmware/build/main.bin | `b2ce1bc5c03b8e8af47cf246115968033f62574c2a3ae2d2d7e5c4f225adcb83` |
| firmware/build/manifest.json | `0606b5ae61de9166c6c0a97eed1de8f4beb7f77bbac08268ed69895c96e04b8e` |
| firmware/build/ext.elf | `1e35ca42340a2f125340030715800a136200858acf19f36dbedca47c64be7064` |
| firmware/build/ext.dis | `6d2579a80f4e171d1b80ebd557e6585a0b85b3c580904475eb0a3dbbdf43458b` |
| Original ROM | `49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a` |

`install_ready` remains false.

The ledgers reuse three accepted results from `IRQ-AUDIT.md`: the 1904-byte
interrupt allowance, the 1024-byte private-E0 worker allowance and the commissioning
qualification. These are conditional software envelopes under the IRQ-AUDIT
premises:

- valid native state;
- the calibrated timing and bus premises;
- one filtered terminal AC-fail interrupt;
- no arbitrary external invokers;
- serialized, nonrecursive ownership of the private-E0 callback (this is not a ban
  on guarded financial recursion).

Exceeding an overapproximated sum is a capacity warning, not a demonstrated
overflow. The document makes no attempt to prove, by an execution trace, which
interrupt frames can be live at the same time.

Unless noted otherwise, resident counts include caller arguments, call returns,
fixed saves and nested callees. A subtree stated "below entry" excludes that
entry's already-pushed return. A task's initial 26-byte context is consumed by the
native restore and RTE; it is not an extra live trampoline.

## Task7 Test10

Test10 is the Task7 diagnostic motion test. Task7 at `0xb080` saves D2 (4 bytes).
Before `0xb30a` calls `0x5ab76`, the pushes at `0xb2f4` and `0xb2f6` leave 8
argument bytes on the stack until `0xb324`. Test10's call return is 4 bytes and its
D2–D6 save is 20, so the prefix at the hook sites is 4 + 8 + 4 + 20 = **36**.

The table gives each changed path's resident bound, the bound plus the 1904-byte
interrupt allowance, and the margin that total leaves against historical b2's
2048-byte stack:

| Changed path | Resident bound | +1904 | Margin to 2048 (historical b2) |
| --- | ---: | ---: | ---: |
| `0x5ac3c` → `motion_test10_poll` | 98 IRQ-open | 2002 | 46 |
| `0x5af80` → `motion_test10_cleanup` | 96 IRQ-open | 2000 | 48 |
| `0x5af86` → `motion_test10_return` | 70 IRQ-open | 1974 | 74 |
| `0x5aee2` → `0x3fbbc` → `0x3fc34`, diagnostic wager | 142 | 2046 | 2 |
| Same root, `0x3fbcc` line-order replacement | 92 | 1996 | 52 |

**Poll.** `motion_test10_step` has a fixed frame of 24 bytes. The poll hook's
IRQ-open maximum is 36 prefix + 4 hook return + 18 saved scratch and SR + 4 argument
+ 4 C return + 24 fixed + 4 restore argument + 4 restore return = 98. Its deeper work
runs inside an explicitly saved and restored SR `0x2700` section; there is no
generic "motion is disabled, so the hook does nothing" exemption. The stock
continuation is tail-entered after the extension scratch is unwound. The owned
delay branch is smaller. Qualified motion is disabled in this image.

**Return.** `motion_test10_exit` has 4 fixed bytes; the restore point is
36 + 18 + 4 + 4 + 4 + 4 = 70. Its IRQ7 treatment keeps the existing premise of one
filtered terminal event.

**Diagnostic wager.** The diagnostic function `0x3fbbc` receives 8 argument bytes
and has 44 fixed bytes (a LINK of −28 plus the saved frame pointer, and three saved
registers). The prefix is 36 + 8 + 4 + 44 = 92. The wager hook adds 4 return + 18
scratch and SR + 4 argument + 4 C return. `game_line_wager` has 4 fixed bytes, and
its division adds 8 arguments + 4 return + 4 for `__udivsi3`'s save, for a total of
142. The line-order hook is a frame-free JMP replacement, so it stays at 92.

**Other Task7 calls.** Task7 also calls `0x62114` at `0xb280`. Three nearby calls
are not Task7 paths:

- The call `0x57e88` → `0x61c50` belongs to the separate function `0x57e70`, which
  begins after `0x57b78`'s RTS boundaries at `0x57e64` and `0x57e6e`; it is not a
  Task7 fallthrough.
- The shared evaluators `0x5323a` and `0x54b0e` are also entered through `0x48cce`
  and `0x48cd6`.
- The `0x5c802` call belongs to `0x5c334`'s argument-26 arm (decimal 26, the `0x1a`
  arm in `INITIAL-ROOTS.md`), reached by Task6 and bootstrap controllers. Its
  address region does not establish a Task7 root.

See `INITIAL-ROOTS.md` for the complete upstream producers and prefixes and
`SETTLEMENT-GUARD.md` for admitted settlement recurrence. The Test10 table alone is
not complete coverage of shared calls.

## Task7 serial reconfiguration

At `0xc1a8`, inside the Task7 function `0xc0fc`, the Task7 prefix is 8 bytes
(Task7's D2 save plus the return address of the call into `0xc0fc`); `0xc0fc`'s
earlier arguments were popped at `0xc192`. The patch at `0xc1a8` replaces
the three consecutive native calls there, to `0x16682`, `0x15836` and `0x152ec`
(delete, initialize, create), with one call to `platform_reconfigure`
(`firmware/patches.json`, patch `private-E0-drain-before-serial-reconfigure`). The
replacement hook adds a 4-byte return and `platform_reconfigure`'s 12 fixed bytes,
giving 24 resident bytes before any outgoing call. The SC9 (mailbox pend) wrapper is
entered at 40 bytes and reaches 52 after its LINK (saved frame pointer plus 8 bytes
of locals).

| Subpath, including the wrapper | Bound | +1904 | Margin to 2048 (historical b2) |
| --- | ---: | ---: | ---: |
| SC9 drain with timeout 0 | 82 | 1986 | 62 |
| `0x16682` deletion / SC1 | 120 | 2024 | 24 |
| `0x15836` initialization | 132 | 2036 | 12 |
| `0x152ec` SC0 creation branch | 110 | 2014 | 34 |
| `0x152ec` → `0x16714` → SC4 priority change | 158 | 2062 | −14 |

**SC9 drain.** 40 entry + 12 wrapper + 26 exception and context + 4 helper return =
82. Timeout 0 skips `0x388a`; `0x38c2` and the scheduler slices do not recurse. The
creation, deletion and context hooks are zero under the already-sealed kernel
premise.

**Deletion.** 28 at the `0x16682` entry + 12 fixed + 12 arguments + 4 call + 12
wrapper + 26 context = 94 at service entry. A deletion helper can add 4 return + 2
saved SR + 4 call + 12 saves + 4 leaf call = 26, giving 120. This deliberately
charges the full interrupt allowance even where the deepest section is masked.

**Initialization.** The maximum chain is:

| Point | Added | Resident |
| --- | --- | ---: |
| `0x15552` | 28 at the `0x15836` entry + 4 fixed + 4 argument + 4 call | 40 |
| `0x1cc9c` | + 4 fixed + 4 call | 48 |
| `0x1cd08` | + 4 call | 52 |
| `0x35cf4` | + 36 fixed + 12 arguments + 4 call | 104 |
| leaf | + 12 fixed + 12 arguments + 4 leaf return | 132 |

Options `0x5` and `0x6` are ROM descriptors of type 2 at `0x73c1e` and `0x73c30`,
so `0x35b48` → `0x35aec` is a direct byte getter. The other checked branches are
`0x15508`/`0x154c0`/`0x1546a`, `0x18146`, `0x1864a`, `0x16488`'s serial
initialization (`0x13660`/`0x139f2`/`0x13eda`/`0x14dc2`, with 52-byte driver saves),
`0x14a7e` and its 52-byte driver setters, protected copies, and the `0x1dd6`/`0x2374`
RTC handling. Their peaks are below 132 (serial setters 120, RTC path at most 110).
Repeated loops pop their arguments.

**Creation.** 28 at the `0x152ec` entry + 4 fixed + 16 arguments + 4 call + 16
wrapper + 26 context + 16 creation scratch = 110. The new child's 26-byte context is
written to the child's own stack, not pushed on Task7's.

**Priority change.** 28 at `0x152ec` + 4 fixed + 4 call to `0x16714` + 20 fixed + 44
accumulated arguments + 4 call to `0x3cfc` + 12 wrapper + 26 context + 4 call to
`0x380a` + 8 saves + 4 call to `0x39a8` = 158. The fifth priority call at `0x16770`
retains 44 argument bytes; `0x16776` pops 40, leaving one long word that the next
call reuses. `0x380a`, `0x384a` and `0x384e` run at the caller's IPL, before `0x2f0e`
raises it, so the −14-byte warning against b2's 2048-byte stack cannot be dismissed
by pointing at that later critical section. Task7 remains a normal IRQ-open task
here.

**Published capacity.** The published `06bef891` image implements the global
3072-byte native stack configuration, keeping native allocator ownership and the
cancellation boundary; historical b2 used 2048. Against 3072 every row above fits.
Emitted-code and frame identity, producer checks, offline tests, source and
mathematical review, and the seven-document review all passed. The exact eight
published artifacts, copied without rebuilding, and all 20 production inputs were
verified; installation remains unqualified. See `SETTLEMENT-GUARD.md`, **Candidate
gates**. The local figures transfer by instruction/frame identity, not by assuming
that the largest known root is universal.

## Standard SAS identity hooks (not private E0)

The extension hooks two standard SAS long polls, `0x1F` and `0x53`, which run on the
native task stack rather than the private E0 stack. The qualified native SAS parent
prefix is 60 bytes at command-handler entry: parent argument and call 8, `0x16366`
fixed 12, callback return 4, `0x19b16` fixed 32, dispatch return 4. The handlers
`0x28480` (`0x1F`) and `0x2c95c` (`0x53`) each have 32 fixed bytes, and all their
earlier construction arguments have been popped. The `0x53` handler additionally
retains its zero argument at `0x2cc24` before the patched JSR; the `0x1F`
replacement at `0x2872a` comes before that argument.

For `0x1F`: 60 + 32 + 4 hook return + 62 `save_native` + 4 C return + 4 identity D2
save + 4 leaf getter return = 170. `0x53` adds 4, giving 174. With 1904 these are
2074 and 2078, warnings of 26 and 30 bytes over 2048. `fw_active_profile` and
`fw_is_faulted` are frame-free leaves. No interrupt exclusion applies merely because
these are identity requests.

The full-register identity wrappers remain unchanged. Their conservative totals of
2074 and 2078 fit the published 3072-byte native capacity, so no register-save
optimization is needed under the approved mathematical composition and its
premises. The native caller's UNLK discards `0x1F`'s synthesized zero argument, as
before. After the tail JMP to `0x278fe` no extension frame remains; the unchanged
transmit path is outside this changed-frame bound. These hooks must not reuse E0's
private stack.

## Boot and clear C-live paths

All depths here are relative to the reset SSP `0x437ffe`, not `0x438000`, so allow
an additional 2 bytes against the nominal 8192-byte reservation. Reset at `0xbca`
calls `0x154e` (4 bytes), and `0x154e` saves 12. The startup hook at `0x1906` also
retains 12 bytes of native queue arguments. The boot window runs only from `0x155c`
through `0x1906`; the runtime hook at `0x37dde` is not boot.

Emitted helper subtrees, below each helper's entry:

| Helper | Bytes | Derivation |
| --- | ---: | --- |
| `write_committed` | 44 | 20 fixed + 12 arguments + 4 return + 8 `platform_write_words` |
| `record_operation` | 220 | 160 + 12 + 4 + 44 |
| `finish_operation` | 236 | 12 + 4 + 220 |
| `write_profile_journal` | 224 | 144 fixed + 32 accumulated arguments + 4 return + 44 |
| `baseline` | 60 | 8 fixed + 24 accumulated arguments + 4 `fw_crc32` return + 12 arguments + 4 return + 8 `fw_crc32_update` |
| `bootstrap` | 364 | 284 fixed + 16 retained arguments + 4 second-`baseline` return + 60 |
| `fw_boot` | 556 | 180 fixed + 8 retained zero arguments + 4 `bootstrap` return + 364 |
| `fw_invalidate` | 396 | 28 + 4 + 364 |
| `fw_clear_complete` | 272 | 16 + 28 + 4 + 224 |
| `fw_restore` (pending-completion journaling) | 244 | 4 argument + 4 return + 236 |
| boot apply, below `game_apply_profile` entry | 152 | 60 fixed + 8 context call + 8 context fixed + 8 fingerprint call + 32 fingerprint fixed + 24 retained arguments + 4 CRC return + 8 CRC fixed |

The cache path of the boot apply is smaller, 148; its 84-byte cache subtree is
reused from an earlier accepted audit.

| Hook | Whole C-live peak | +1904 +2 |
| --- | ---: | ---: |
| `0x155c` boot validation | 646 | 2552 |
| `0x1730` → `0x3756c` → `0x37dde` boot restore | 426 | 2332 |
| `0x1906` final boot restore | 350 | 2256 |
| `0x38386` clear begin | 506 | 2412 |
| `0x38526` clear complete | 378 | 2284 |

Derivations:

- `0x155c`: 16 native + 4 hook + 62 save + 4 C + 4 `fw_boot` + 556 = 646.
- Boot loader: 24 loader entry + 68 fixed + 16 retained tail arguments + 4 hook + 62
  save + 4 C + 4 `fw_restore` + 244 = 426.
- `0x1906`: 16 native + 12 queue arguments + 4 hook + 62 save + 4
  `platform_boot_restore` + 4 `platform_restore` + 4 `fw_restore` + 244 = 350.
- Clear begin: reset → `0x3825a` entry 4 + 8 saved + 4 call to `0x3836e` + 8 saved +
  12 arguments + 4 hook + 62 save + 4 C + 4 core call + 396 = 506.
- Clear complete has the same shape with 8 outstanding native arguments and the
  272-byte core subtree, giving 378.

Direct cross-reference checks find `0x3836e` called only at `0x3833c` and `0x3825a`
only at `0xb9e`. This is the reset-time clear, not a runtime task hook.

The original `0x25b0` prelude runs before the C saves, adding only the extra 4-byte
extension return; its checked copy and fault leaves fit below the boot maximum
above. The C startup-fault code is `0x0F`, excluding `0x38a06`'s alternate branch
for codes 1 and 2: the `0x38a06` subtree is 48 and `platform_startup_fault` is 56
below entry. It never starts tasks. These conservative arithmetic totals are not
permission to admit unexpected interrupts before the startup state is initialized.

## Runtime loader tail — local formulas

Let L be the whole-task depth at the `0x3756c` entry, including its return. At
`0x37dde` the loader has 68 fixed bytes plus 16 retained arguments; the extension
call 4 + save 62 + C call 4 puts the `platform_restore` entry at **L + 154**. The
replaced `0x54a88` prelude is smaller.

After a completed boot, with no pending restore completion and no profile-change
ownership, a custom slot is validated and then rejected before admission, caches or
writes, and the stock null-profile path does not rewrite the native configuration.
`game_apply_profile`'s maximum is 116 (60 fixed + 8 call + 48 `slot_ok` and
validator). Therefore `fw_restore` ≤ 144, `platform_restore` ≤ 148, and the whole
local loader-tail bound is **L + 302**. General recovery, without that lifecycle
reduction, is **L + 402**, dominated by the 244-byte `fw_restore` journaling. These
bounds do not claim that the rejection undoes native loader changes made before the
tail.

Reconciled local caller transitions, with R the whole-task depth at the caller's
entry:

| Caller entry R | Loader call | L | Local maximum |
| --- | --- | --- | --- |
| `0x63cd0` | `0x63db8` | R + 60 (20 fixed + 36 arguments + 4 return) | R + 362 |
| `0x64056` | `0x64120` | R + 40 (16 + 20 + 4) | R + 342 |
| `0x64056` | `0x6430a` | R + 56 (16 + 36 + 4) | R + 358 |
| `0x64126` calls `0x63cd0` while still retaining 20 arguments | via `0x63cd0` | child entry at R + 40 | R + 402 |

The 36 retained argument bytes at `0x6430a` are 4 at `0x642b6`, 8 at
`0x642c0`/`0x642c4`, 8 at `0x642d6`/`0x642d8`, 8 at `0x642ea`/`0x642ec`, 4 at
`0x642fa` (`0x642f8` replaces the top of stack) and 4 at `0x64306`; the addition at
loader entry is therefore 56. Address `0x6429c` is an extension word of the PEA at
`0x6429a`, not a function entry.

These parameterized local formulas are not whole-task bounds on their own: the
upstream closure must be composed before comparing with an allocation.
`INITIAL-ROOTS.md` composes SAS command `0x8C` (498), Task2/fault (458) and the
corrected shared Task6 runtime (470) from them. Do not present the local formulas
alone as whole-task qualification.

## Fatal paths

Native `platform_fatal` uses TRAP #1 → `0xfc4`. The handler at `0xfc4` stores
register diagnostics to absolute RAM, not to the stack, then sets SR `0x2700` at
`0xfd0` and jumps to `0x13ee`. Its largest checked native subtree, measured from
`platform_fatal` entry, is **122 bytes including the 6-byte exception**: 6 + 8
argument and call to `0x1274` + 24 fixed + 16 arguments + 4 call to `0x3a652` + 20
fixed + 20 arguments + 4 call to `0x3a50a` + 20 fixed. The formatting, checksum and
display paths do not lower SR, and `0x3a50a` is a leaf. Protected fault-counter
writes restore the incoming `0x2700`.

For the reconfiguration wrapper's own invariant failures, the depth is at most 28
at `platform_fatal` entry. Until `0xfd0` it is at most 34 resident, plus 1904 =
1938. After `0xfd0` it is 150 resident plus the 154-byte terminal IRQ7 allowance =
304. A boot calibration rejection is similarly small: 34 before the TRAP and 156
after the native fatal subtree, plus 154 = 310. No coil-off or recovery is claimed.
Unexpected native kernel or object errors remain outside the valid-object ownership
premises.

## Integration limits

`INITIAL-ROOTS.md` holds the complete positive initial-root composition and its
scoped mathematical approval. This ledger supplies local costs, not a second census
of incoming roots. The global 3072-byte capacity and the settlement entry guard are
implemented and tested in candidate `06bef891`, and frame identity supports reusing
these local costs there. That identity must be re-established after any code
change. The seven-document review and the exact-artifact local publication are
complete; installation remains unqualified.

The promise covers extension-live, added-code work only. Pure-native temporary
peaks, financial termination, external invokers, corrupt kernel objects and
repeated terminal interrupt edges are not qualified. Hardware and installation are
separate, and no machine operation follows.

# BET ONE accounting and numeric ledgers

This part bounds the BET ONE hook and the native accounting, numeric and first-wager
code beneath it.

## Scope, evidence, and convention

These calculations use frozen `firmware/build/main.bin` (historical b2) with SHA256
`b2ce1bc5c03b8e8af47cf246115968033f62574c2a3ae2d2d7e5c4f225adcb83`, verified before
and after the analysis.

The evidence is the original `roms/s6000_main_S6M00002B5.bin` disassembled with
m68k-elf-objdump, plus the emitted `firmware/build/ext.dis:game_bet_one` at `0x7bfa4`
and `game_bet_one_entry` at `0x7cd4c` (b2 linked addresses). All 38 listed native
accounting, numeric, first-wager, progressive, descriptor and selected option
dependency regions were byte-compared against frozen `main.bin` and match.
Instruction addresses are stable binary locators, not document line numbers.

S(f) is the maximum additional stack **below the function-entry SP**, excluding the
already-present return to f. Each edge contributes its retained outgoing arguments
+ 4 return + S(callee). Fixed bytes include the saved frame pointer, locals and
saved registers. The loops below clean up their arguments on every iteration, and no
recursive accounting edges were found. These are upper bounds over branches, not
measured traces or claims that the branches can be reached together.

The ledger reuses two figures from `IRQ-AUDIT.md`: the 1904-byte interrupt
allowance and the 64-byte ceiling for returning native services (SC20/SC21
included). Router operation `0x73` has S(`0x26a0e`) = 204; its positive per-protocol
bounds are AS 64, SAS 68, BSG 192, MIS 128 and SDT 24. Aggregation over incoming
controllers and roots is separate.

## Result

For the direct Task6 event 9 route `0x9f2a` → `0xa5e0` → `0x42f08` → `0x42fac` →
`game_bet_one_entry` → `game_bet_one`:

- The returning changed-frame path has a resident maximum of **284 bytes**.
- Including the examined native TRAP1 paths for accounting corruption, and
  conservatively charging their full fatal subtree, the resident maximum is **322
  bytes**.
- Adding the existing interrupt envelope uniformly gives **322 + 1904 = 2226 ≤
  3072**, leaving 846 bytes. The returning-only sum is 2188, leaving 884 bytes.
- Both 322 and 284 are well below the requested 1168-byte noninterrupt budget.

The 3 KiB capacity is present in the published image `06bef891`; historical b2 had
2048-byte stacks. These local bounds do not, by themselves, qualify all roots or any
code change. The verified candidate frame identity and the complete INITIAL-ROOTS
composition supply that scoped mathematical bridge; arbitrary corrupted objects,
whole-legacy temporary peaks and hardware remain outside it. Other callers use the
complete initial-root aggregation in `INITIAL-ROOTS.md`. If R is the whole-task
resident depth **at the `0x42f08` entry, including its return**, this local closure
contributes at most **R + 248 returning**, or **R + 286 including the examined fatal
paths**. Direct Task6 has R = 36.

## Positive Task6/C caller accounting

The Task6 function at `0x9f2a` has a LINK of −8 and a D2–D6 save, 12 + 20 =
32 bytes. The task entry's initial kernel context is consumed by restore and RTE; it
is not a surviving return trampoline. At `0xa5e0` the main event loop has no
retained outgoing arguments. The event 9 table entry at `0x77cc6` + (9 − 1) × 120
points directly to `0x42f08` for native states 0 and 2. The targets of other states
are not silently counted as this path.

The matrix handler at `0x42f08` has no frame; earlier helper arguments are
popped by `0x42f86`, and its lock call at `0x42fa6` has returned before the patched
`0x42fac`. Therefore:

| Location | Whole-task resident bytes |
| --- | ---: |
| `0x42f08` entry | 36 |
| `game_bet_one_entry` entry (JSR at `0x42fac`) | 40 |
| `game_bet_one` entry (JSR at `0x7cd4c`) | 44 |
| `game_bet_one` after its D3/D2 saves | 52 |
| `0x4341c` or `0x420f8` entry from C | 56 |

The C function has no local array and no LINK frame. The counter reader's old 4-byte
argument remains while the modulo arguments are pushed; `0x7bff0` pops 12 bytes
before the comparison. The comparison's 8 argument bytes are popped at `0x7c004`.
Every call to `0x4341c` or `0x420f8` has zero outstanding C arguments. The loop
`0x7c020`..`0x7c02a` does not grow SP. `counter_read` at `0x7b904` tail-jumps to
`0x549c8` and adds no return. (`0x7b904`–`0x7c02a` are b2 extension addresses.)

| C call | Peak |
| --- | --- |
| Preflight reader | 52 + 4 argument + 4 call + S(`0x549c8`) 172 = 232 |
| Compare | 52 + 8 + 4 + S(`0x548bc`) 172 = 236 |
| Modulo | 52 + 12 + 4 + S(`__umodsi3`) 16 = 84 (8 arguments, 4 nested return, 4 libgcc save) |
| `game_lines`, `custom_present` | shallow, below these values |
| Refusal unlock, after C has unwound | 40 + 4 + 64 = 108 |

The unmodified code from `0x42fb2` onward is outside this changed-frame bound.

## Numeric/helper closure

### Leaf and subordinate bounds

| Function | S | Derivation and scope |
| --- | ---: | --- |
| `0x400`, `0x426`, `0x44c` | 10 | LINK #0 = 4, A0 = 4, saved SR = 2; no child call |
| `0x480` | 0 | Global nesting count; no child call |
| `0x49e` | 4 | Global nesting count; temporarily saves D0; no child call |
| `0x500` | 26 | LINK #0 = 4, five saved registers 20, temporary SR 2; bounded copy loop |
| `0x3a8f4` | 4 | D2 checksum loop; no calls |
| `0x3a974`, `0x39f96`, `0x69bce` | 0 | Copy, nonzero and subtract leaves |
| `0x69bf6` | 10 | LINK #0 + saved D2 + saved SR; no calls |
| `0x68bcc` | 8 | All division branches; D2–D3 maximum |
| `0x68c30` | 12 | May BSR to `0x68bcc`: 4 + 8 |
| `0x68c4c` | 16 | D2 save 4 + BSR 4 + `0x68bcc` 8 (the actual modulo-12 callers are smaller) |
| `0x3a15c` | 28 | 12 saved + 4 call + `0x68c30` (12); the division path is smaller |
| `0x3a23e` | 34 | 12 fixed + 8 arguments + 4 call + `0x400` (10); the arithmetic path is at most 28 |
| `0x3a944` | 34 | 12 saved + 8 arguments + 4 call + `0x400` (10) |
| `0x3b678` | 38 | 16 saved + 8 arguments + 4 call + `0x400` (10); both padding and copy loops pop 8 |
| `0x3c9f4` | 8 | A2–A3 save; every valid descriptor and address-selection branch is a leaf, including zero-pointer dynamic record addresses |
| `0x3cf86` | 16 | A2–A5 save; all ten widths select pointer arithmetic, no calls |
| `0x3c998` | 60 | 20 fixed + 20 retained arguments + 4 call + `0x3cf86` (16); the type 1 branch is 20 + 8 + 4 + 8 = 40 |
| `0x3bc5c` | 108 | 24 fixed + 20 arguments + 4 call + `0x3c998` (60); the checksum and mirror-repair arms are smaller |
| `0x38fd4`, `0x38ff2` | 0 | Byte-swap leaves |
| `0x38f1c` | 82 | 28 fixed + 40 accumulated arguments + 4 call + `0x44c` (10) at `0x38fc4`; the earlier swap calls retain 8, 16, 24 and 32 |
| `0x35840` | 12 | D2–D4 CRC loop, no calls |
| `0x35a26` | 110 | 16 fixed + 8 arguments + 4 call + `0x38f1c` (82); the CRC path (44) and the final protected store (50) are smaller |
| `0x36896` | 0 | Native host-personality predicate leaf |
| `0x34a18` | 54 | 12 fixed + 28 accumulated arguments + 4 call + `0x400` (10) |
| `0x34a84` | 46 | 8 fixed + 24 accumulated arguments + 4 call + `0x400` (10), including failed-copy repair |
| `0x349b2` | 70 | 4 fixed + 8 arguments + 4 call + `0x34a18` (54); the read/repair arm is 58 |

For `0x3bc5c`, valid type 0 descriptors either pass their first checksum, recover
from the mirror copy, or take the native fatal sink if both copies fail. The
returning maximum of 108 is its descriptor-resolution call, not an assumption that
checksum failure is impossible. Type 1 simply returns. The invalid width and id
traps of the two descriptor resolvers cannot be reached with the audited immutable
descriptors; native fatal accounting failures are charged separately below.

### Numeric function subtrees and retained arguments

In the following tables, A is **all outstanding argument bytes immediately before
the call**, not just the arguments that callee newly receives. Each listed peak is
F + A + 4 + S(child).

**`0x3bd24`: F = 24, S = 140**

| Call site | Child | A | Peak |
| --- | --- | ---: | ---: |
| `0x3bd30` | `0x3bc5c` | 4 | 140 |
| `0x3bd48` | `0x3c998` | 24 | 112 |
| `0x3bd4e` | `0x480` | 24 | 52 |
| `0x3bd64` | `0x3a23e` | 36 | 98 |
| `0x3bd80` | `0x3a8f4` | 8 | 40 |
| `0x3bd8e` | `0x400` | 8 | 46 |
| `0x3bda4` | `0x3a944` | 20 | 82 |
| `0x3bdb6` | `0x400` | 28 | 66 |
| `0x3bdbc` | `0x49e` | 28 on the mirrored branch, 0 otherwise | 60 |

`0x3bd6a` pops 36; type 1 skips the mirror writes. `0x3bd86` pops only 4 before
replacing the top argument, so the following mirror and checksum calls retain 8, 20
and 28, not independently 8, 12 and 8.

**`0x3bdca`: F = 36, S ≤ 158**

| Call site | Child | A | Peak |
| --- | --- | ---: | ---: |
| `0x3bddc` | `0x3bc5c` | 4 | 152 |
| `0x3bdf4` | `0x3c998` | 24 | 124 |
| `0x3be08` | `0x3a15c` | 36 | 104 |
| `0x3be0e` | `0x480` | 36 | 76 |
| `0x3be24` | `0x69bf6` | 48 | 98 |
| `0x3be40` | `0x3a8f4` | 8 | 52 |
| `0x3be4e` | `0x400` | 8 | 58 |
| `0x3be64` | `0x3a944` | 20 | 94 |
| `0x3be76` | `0x400` | 28 | 78 |
| `0x3be80` | `0x36896` | 0 | 40 |
| `0x3be90` | `0x35a26` | 8 | 158 |
| `0x3be96` | `0x49e` | 8 on the bank-accounting branch, 0 otherwise | 52 |

`0x3be2a` pops 48. On the mirrored path, `0x3be7c` pops 28 before `0x36896`. This
bound deliberately includes native AS accounting at `0x35a26`, not just the simple
BCD increment. BET ONE uses descriptor `0x6a3f4`, which points to `0x43d49c`; that
matches `0x35a26`'s first bank branch, and the fallback is not an invented generic
alternative. This branch executes under `0x480`, but no interrupt subtraction is
needed for the conservative bound.

**`0x3bea6`: F = 44, S = 160**

| Call site | Child | A | Peak |
| --- | --- | ---: | ---: |
| `0x3beb8` | `0x3bc5c` | 4 | 160 |
| `0x3bed0` | `0x3c998` | 24 | 132 |
| `0x3bed6` | `0x480` | 24 | 72 |
| `0x3bef0` | `0x3b678` | 40 | 126 |
| `0x3bf06` | `0x3a15c` | 12 | 88 |
| `0x3bf18` | `0x69bce` | 24 | 72 |
| `0x3bf46` | `0x3a944` (null-output branch) | 12 | 94 |
| `0x3bf62` | `0x3a8f4` | 8 | 60 |
| `0x3bf70` | `0x400` | 8 | 66 |
| `0x3bf86` | `0x3a944` | 20 | 102 |
| `0x3bf98` | `0x400` | 28 | 86 |
| `0x3bfb4` | `0x3a974` (nonnull-output branch) | 12 | 60 |
| `0x3bfba` | `0x49e` | 12 on the nonnull-output branch, 0 otherwise | 64 |

`0x3bef6` pops 40; `0x3bf1e` pops 24 before the possible underflow TRAP1 at
`0x3bf26`. The null-output path pops 12 before testing the mirror type; the mirror
path pops 28. All BET ONE callers supply a null output, but the nonnull branch is
included and is smaller.

**`0x3c090`: F = 56, S = 192**

| Call site | Child | A | Peak |
| --- | --- | ---: | ---: |
| `0x3c0a2` | `0x3bc5c` (destination) | 4 | 172 |
| `0x3c0ba` | `0x3c998` | 24 | 144 |
| `0x3c0c2` | `0x3bc5c` (source) | 24 | 192 |
| `0x3c0da` | `0x3c998` | 44 | 164 |
| `0x3c0e4` | `0x480` | 0 | 60 |
| `0x3c128` | `0x3b678` (width-padding branch) | 16 | 114 |
| `0x3c142` | `0x3a944` (both width branches converge) | 12 | 106 |
| `0x3c15e` | `0x3a8f4` | 8 | 72 |
| `0x3c16c` | `0x400` | 8 | 78 |
| `0x3c182` | `0x3a944` | 20 | 114 |
| `0x3c194` | `0x400` | 28 | 98 |
| `0x3c19a` | `0x49e` | 28 on the mirrored branch, 0 otherwise | 92 |

The crucial 24 retained bytes at `0x3c0c2` survive from the first resolver call;
replacing the top with A3 does not pop them. `0x3c0e0` pops 44. The
wider-destination branch at `0x3c12e` pops 16 before preparing the common copy. This
is why the second validator call, not the first, determines 192.

### Counter-reader and comparator closure

| Function | F | S | Controlling edge |
| --- | ---: | ---: | --- |
| `0x3bc18` | 24 | 140 | validator: 24 + 4 + 4 + 108; dominates its resolver and nonzero check |
| `0x3bfca` | 24 | 156 | the resolver's 20 argument bytes remain for the validator: 24 + 20 + 4 + 108; conversion `0x3a04e` is an 8-byte leaf |
| `0x3bbae` | 32 | 148 | validator: 32 + 4 + 4 + 108; numeric conversion and `0x3aa4a` (a 4-byte leaf) are smaller |
| `0x54912` | 8 | 156 | 8 + 4 + 4 + 140 |
| `0x549c8` | 8 | 172 | 8 + 4 + 4 + 156 |
| `0x548bc` | 12 | 172 | 12 + 8 + 4 + 148 |

SC20/SC21 calls under these functions fit the reused 64-byte ceiling and are smaller
than the accounting branches.

## Remaining 53dd0 arms and 4341c

The `0x53dd0` dispatch table at `0x53dfa` was decoded directly for operations
`0x46`, `0x4c`, `0x55`, `0x56` and `0xa6`. The dispatcher's fixed frame is 8 bytes.
The cases and their targets are `0x542d0` → `0x46cce`, `0x5431c` → `0x472a2`,
`0x54382` → `0x4687a`, `0x5438c` → `0x468de` and `0x54748` → `0x47c60`. Only `0x4c`
pushes an extra 4-byte argument. The lock and unlock service paths are at most
8 + 4 + 64 = 76 below the `0x53dd0` entry.

| Route | Target S | `0x53dd0` S for route | Derivation and retained arguments |
| --- | ---: | ---: | --- |
| `0x55` → `0x4687a` | 190 | 202 | `0x468c6` retains 28 before `0x3bdca`: 28 + 4 + 158; `0x349b2` retains 32, giving 106 |
| `0x56` → `0x468de` | 190 | 202 | `0x4691e` and `0x4696c` retain 28 before `0x3bdca`; the second `0x3bea6` retains 20, giving 184 |
| `0xa6` → `0x47c60` | 204 | 216 | 8 arguments + 4 call + `0x3c090` (192) |
| `0x46` → `0x46cce` | 152 | 164 | 8 arguments + 4 call + `0x3bd24` (140); the `0x500` path retains 28 but is 58 |
| `0x4c` → `0x472a2` | 156 | 172 | LINK #0 = 4 + 8 arguments + 4 call + `0x3bd24` (140); the dispatcher additionally pushes the 4-byte argument |

All `0x468de` branches through `0x46a9e` were read, including:

- two funded-counter variants, each retaining 12, 20 and 28 bytes across
  subtract, subtract and increment;
- the aggregate counter and optional secondary subtraction, with the intervening
  read retaining 16;
- the unfunded-credit option: an increment, then `0x349b2` (8, then 12 argument
  bytes);
- the final ordinary-credit subtraction and the SAS credit paths;
- `0x578a0` → `0x35b48` (option `0x32`), whose immutable descriptor at `0x73f48` is
  type 2 pointing to `0x43e8a1`: the leaf `0x35aec` byte getter, so S(`0x578a0`) =
  20;
- the final call at `0x46a96` to `0x3bdca`, passing descriptor `0x6a3bc`, with 8 argument bytes.

All are below 190.

Immutable descriptor facts: widths are 3 to 5 in the relevant counter set.
Descriptor `0x6a1b4` has a zero direct pointer, type 1 and id `0x50`; `0x3c9f4`
resolves it through the leaf dynamic current-record arm at `0x3caf0`, not through an
unknown function pointer. The type 0 pointers used by the optional bank accounting
are `0x43d49c` (descriptor `0x6a3f4`) and `0x43d4ba` (descriptor `0x6a21c`). Type 1
counters may skip mirror work; the bounds above still cover both types
conservatively.

`0x4341c` has no fixed frame:

| Edge from `0x4341c` | Retained arguments | S including edge | Whole-task direct-route peak (entry 56) |
| --- | ---: | ---: | ---: |
| `0x43420` → `0x54912` | 4 | 164 | 220 |
| `0x4342c` → `0x37dee` | 0 | 54 | 110 |
| `0x43438` → `0x53dd0` (`0xa6`) | 8 | 228 | 284 |
| `0x43446` → `0x53dd0` (`0x56`) | 8 | 214 | 270 |
| `0x43452` → `0x53dd0` (`0x55`) | 12 | 218 | 274 |
| `0x4345c` → `0x26a0e` (`0x73`) | 16 | 224 | 280 |

The `0x55` call retains 12 because `0x4344c` replaces the old top of stack with zero
and `0x4344e` pushes only the new opcode. The router call for operation `0x73` adds
another 4, and then `0x43462` pops 16. `0x37dee` (F = 20) has a maximum of 50 below
entry (its two final SafeRAM stores retain 16); its variable loop pops 8 each
iteration, and its integer modulo uses bounded, register-only helpers. Thus
**S(`0x4341c`) = 228**.

## First-wager 420f8 closure

`0x420f8` has F = 4. Its configuration-clearing runs accumulate at most 40 argument
bytes, all discharged before the following phases. Its other leaves and helpers:
`0x50086` is a leaf; `0x38ad0` has S = 38 (16 fixed + 8 arguments + 4 + `0x400`
(10)); `0x35fdc` has S = 30 (4 fixed + 12 arguments + 4 + `0x400` (10)). All were
read through their RTS.

| `0x420f8` subpath | Peak below the `0x420f8` entry |
| --- | ---: |
| `0x421fc` → `0x53dd0` (`0x46`), 8 arguments | 4 + 8 + 4 + 164 = 180 |
| `0x423d8` → `0x53dd0` (`0x4c`), 20 retained arguments | 4 + 20 + 4 + 172 = 200 |
| `0x423fa` → `0x1d48a` (operation 6), 8 arguments | 4 + 8 + 4 + 108 = 124 |
| largest straight SafeRAM clear, 40 arguments | 4 + 40 + 4 + 10 = 58 |
| `0x42388` → `0x500`, 32 arguments | 4 + 32 + 4 + 26 = 66 |
| `0x423a8` → `0x38ad0`, 12 arguments | 4 + 12 + 4 + 38 = 58 |
| `0x42488` → `0x35fdc` | 4 + 4 + 30 = 38 |

Therefore **S(`0x420f8`) = 200**, and the whole-task direct-route peak is 56 + 200 =
256.

**Progressive call.** `0x420f8` passes exactly 6 and 0 at `0x423f4`..`0x423fa`.
`0x1d48a` (F = 20) calls the leaf `0x1cda6`; when the progressive route is enabled it
passes those arguments to `0x1c438`. When it is not selected, its comparisons for 3,
1 and 2 all fail for 6 and it returns at `0x1d4f8`. `0x1c438` (F = 8) selects table
`0x1c460` entry (op − 1) = 5 → `0x1c46e`; that case increments `0x43d458`, clears
`0x43d457` and calls the **leaf `0x1ce06`**, then unlocks. It does **not** select
`0x1c9b0`. Using the 64-byte native service ceiling, S(`0x1c438`) ≤ 76 and
S(`0x1d48a`) ≤ 20 + 8 + 4 + 76 = 108. No progressive transmission or task body is
called synchronously by this operation.

## Accounting fatal branches

This section reuses the native TRAP1 → `0xfc4` → `0x13ee` fatal subtree of 122 bytes
**including its 6-byte exception frame**, already inspected in **Fatal paths**
above. It sets SR `0x2700` at `0xfd0`, does not return, and is not a motor fault,
recovery or coil-off path.

The deepest reachable accounting-corruption trap is the second `0x3bc5c` under
`0x3c090`:

| Point | Added | Resident |
| --- | --- | ---: |
| `0x53dd0` | 56 (`0x4341c` entry) + 12 (`0xa6` call) | 68 |
| `0x47c60` | + 8 fixed + 4 call | 80 |
| `0x3c090` | + 8 arguments + 4 call | 92 |
| second `0x3bc5c` | + 56 fixed + 24 retained + 4 call | 176 |
| `0x3bcc2` (both checksums invalid; earlier arguments already popped) | + 24 fixed | 200 |

Adding 122 gives **322**. Before `0xfd0` the prefix is 206, so even charging the full
1904 there gives 2110. Uniformly charging 1904 to the whole 322 is conservative and
still below 3072. `0x3bea6`'s underflow trap at `0x3bf26` is shallower, at most 148
before the exception on this root. Invalid ROM descriptor widths or ids and corrupt
kernel ownership are outside the frozen valid-object premise; no general stack
guarantee for corrupt pointers is claimed.

## Integration boundary

The BET ONE numeric and accounting subtrees and the first-wager subtree have a
complete changed-frame bound for direct Task6 and an R-parameterized formula for
other callers. Incoming prefixes belong to `INITIAL-ROOTS.md`; native allocator
ownership and capacity belong to `TASK-STACK-AUDIT.md`. Under the approved global
3 KiB mathematical composition and its stated premises, this path needs no local
stack optimization or semantic restriction. No installation approval is implied.

# Initial-root composition boundary

The complete initial roots are authoritative in `INITIAL-ROOTS.md`, including the
fault-controller ancestry, Task3 P 104, the Task6 helpers P 64 and P 52, `0x43c2`, and
both `0xa5e0`/`0xad72` → `0xcf54` → `0x62958` routes. Its AS and BSG selector graphs
are acyclic: AS FIRST 240; BSG Q 220, P 224, FIRST 268. It composes X 72 and Y 64,
shared `0x57e70` FIRST 88, native normal 416, evaluator 306 and completed-runtime
loader 498, and bootstrap FIRST 58, shared normal 302, general loader 532 and early
boot 648. These are initial-entry bounds, not limits on later recursion.

The admitted `0x589ba` proof remains in `SETTLEMENT-GUARD.md`, **Covered cut graph**
and **Reserve and induction**, using M 328, G 120, H 2232, S 10 and I 1904. No finite
count of financial rounds is needed or claimed. The historical one-descent and loose
bootstrap calculations, and the earlier lifecycle-investigation to-do items, are
superseded; they are not live qualification requirements. The local numeric and
continuation ledgers below remain their own authority for costs and call domains.

# Settlement guarded descendant ledgers (frozen-b2 design)

These ledgers are the consolidated positive native call-domain inputs to
`SETTLEMENT-GUARD.md`, **Covered cut graph**: everything a covered path can call
below an admitted settlement entry. They were read through every returning exit
using original-ROM instructions and independently composed against frozen b2. The
final producer seal and instruction/frame seal of candidate `06bef891` were
verified; `SETTLEMENT-GUARD.md`, **Candidate seal and offline gates**, records the
passed checks, the full affected tests and the independent source review. This part
preserves the local proof for the admitted region; it is not an alternative to
`INITIAL-ROOTS.md` for ancestry before the first check.

S excludes the existing incoming return; F includes the frame pointer, locals and
register saves; each edge adds **all retained arguments** plus a 4-byte return. The
numeric, descriptor and fatal families already documented under **Numeric/helper
closure**, **Counter-reader and comparator closure** and **Accounting fatal
branches** are reused, not duplicated. The general task service ceiling of 64 is
distinct from the IRQ-only figure of 26. No premise is added that the native
financial history originated from this exact ROM.

## Additional owned numeric helpers

| Helper | F | S | Controlling edge (A = retained arguments) |
| --- | ---: | ---: | --- |
| `0x3bae6` | 44 | 180 | `0x3bb18` → second `0x3bc5c`, A = 24: 44 + 24 + 4 + 108 |
| `0x3c00e` | 36 | 168 | `0x3c03c` validator, A = 20: 36 + 20 + 4 + 108 |
| `0x3c2f6` | 64 | 200 | `0x3c32c` second validator, A = 24: 64 + 24 + 4 + 108 |
| `0x3c728` | 48 | 164 | `0x3c73e` validator, A = 4; the padding call (A = 40, peak 130) is smaller |
| `0x3c7ec` | 40 | 156 | `0x3c7fe` validator, A = 4: 40 + 4 + 4 + 108 |
| `0x3c8da` | 32 | 148 | `0x3c8ec` validator, A = 4: 32 + 4 + 4 + 108 |
| `0x547d4` | 8 | 192 | `0x5480a` → `0x3c00e`, A = 12 |
| `0x54872` | 8 | 168 | `0x548a4` → `0x3c8da`, A = 8: 8 + 8 + 4 + 148 |
| `0x5495c` | 12 | 204 | `0x549b0` → `0x3bae6`, A = 8 |
| `0x54a12` | 8 | 188 | `0x54a42` → `0x3c728`, A = 12 |
| `0x3ab76` | 12 | 56 | `0x3ab86` → `0x3a15c`, A = 12; the A = 24 additive leaf is smaller |
| `0x3aba2` | 24 | 68 | `0x3abc4`/`0x3abee` → `0x3a15c`, A = 12; the largest subtract leaf (A = 36) peaks at 64 |
| `0x3c968` | 12 | 56 | `0x3c978` → `0x3a15c`, A = 12; the comparator (A = 28) peaks at 48 |
| `0x3b73a` | 12 | 36 | `0x3b76e` → `0x68c30`, A = 8 (the previous protected-write arguments are retained) |
| `0x3b798` | 12 | 64 | `0x3b7d2` → `0x3b73a`, A = 12; the loop pops 12 every iteration |
| `0x3b77e` | 4 | 84 | `0x3b78e` → `0x3b798`, A = 12 |
| `0x3a038`, `0x3a0d4`, `0x3a0ea` | 4 | 24 | A = 8 + return + `0x3a04e` (8) |
| `0x3af58`, `0x3af6a` | 4 | 36 | A = 4 + return + 24 |
| `0x35aec` | 0 | 44 | types 4 and 5: A = 4 + return + 36; types 2 and 8 are leaves |
| `0x35b48` | 4 | 56 | A = 4 + return + 44, a uniform bound for valid descriptors |
| `0x38dfe` | 8 | 72 | A = 4 + return + `0x35b48`; all the following personality cases are leaf table reads |
| `0x34968` | 8 | 62 | A = 4 + return + `0x34a84` (46) |
| `0x34990` | 4 | 70 | A = 8 + return + `0x34a18` (54) |
| `0x38dc8` | 8 | 90 | A = 8 + return + `0x34990` (70) |

All these helpers restore their saves and frame pointer on every returning exit, and
their loops discharge arguments every iteration. Data-selected descriptor addresses
are not callable pointers. Underflow and checksum failures use the native TRAP1
halt, separately from the selection of valid immutable descriptor widths and ids. No
helper here calls a controller, a covered game hook or a stack-growing callback
cycle.

## Selected settlement accounting opcodes

The accounting dispatcher at `0x53dd0` has F = 8. Its check at `0x53de6` bounds the
opcode to `0x00`..`0xb4`, and the signed-word table at `0x53dfa` selects the case. The domains of opcodes `0x46`, `0x4c`, `0x55`, `0x56`,
`0xa6` and `0xa8`, closed earlier, remain in **Remaining 53dd0 arms and 4341c** and in
`IRQ-AUDIT.md`, **IRQ4/5 called targets**. Each selected case forwards a 4-byte
argument unless marked "none"; the dispatcher entry contributes F 8 + arguments + 4
return + the target's S. The following opcodes are additionally selected; this is
not coverage of every opcode.

| Opcode | Target | Argument forwarded | Target S | `0x53dd0` S |
| --- | --- | --- | ---: | ---: |
| `0x04` | `0x4411c` | 4 bytes | 178 | 194 |
| `0x05` | `0x44174` | 4 bytes | 174 | 190 |
| `0x06` | `0x441bc` | 4 bytes | 174 | 190 |
| `0x0a` | `0x443dc` | none | 228 | 240 |
| `0x0c` | `0x4418c` | 4 bytes | 156 | 172 |
| `0x0d` | `0x44482` | none | 170 | 182 |
| `0x0e` | `0x444fe` | 4 bytes | 174 | 190 |
| `0x0f` | `0x44516` | 4 bytes | 186 | 202 |
| `0x10` | `0x445b0` | 4 bytes | 182 | 198 |
| `0x11` | `0x446ac` | 4 bytes | 174 | 190 |
| `0x12` | `0x44680` | none | 170 | 182 |
| `0x14` | `0x44788` | 4 bytes | 174 | 190 |
| `0x15` | `0x44804` | 4 bytes | 156 | 172 |
| `0x19` | `0x44aac` | 4 bytes | 178 | 194 |
| `0x1a` | `0x44bdc` | 4 bytes | 188 | 204 |
| `0x1b` | `0x44c88` | 4 bytes | 174 | 190 |
| `0x2e` | `0x45b94` | none | 178 | 190 |
| `0x37` | `0x46098` | 4 bytes | 156 | 172 |
| `0x39` | `0x4645a` | 4 bytes | 174 | 190 |
| `0x46` | `0x46cce` | none | 152 | 164 |
| `0x66` | `0x46ae8` | 4 bytes | 180 | 196 |
| `0x67` | `0x46b02` | 4 bytes | 174 | 190 |
| `0x68` | `0x441a4` | 4 bytes | 156 | 172 |
| `0x69` | `0x46d14` | 4 bytes | 174 | 190 |
| `0x6a` | `0x47390` | none | 170 | 182 |
| `0x6b` | `0x473a6` | none | 170 | 182 |
| `0x6c` | `0x447ec` | 4 bytes | 174 | 190 |
| `0x96` | `0x44cb8` | 4 bytes | 174 | 190 |
| `0x97` | `0x44850` | 4 bytes | 178 | 194 |
| `0x98` | `0x448dc` | 4 bytes | 174 | 190 |

The targets are numeric and protected-store functions, not callbacks. For example,
`0x443dc` (F = 12) calls `0x3c2f6` with 12 retained bytes (228), and `0x45b94`
retains 12 at its second increment (178); its later common
increment retains 8. This positively closes the post-recursion operation `0x2e`; it
is not an argument that settlement consumes funds and finishes. After the optional
argument removal, the dispatcher's case tails join `0x547c8` (`SC_UNLOCK`, the SC21
scheduler unlock, then D2 restore, UNLK and RTS).

The following 22 rows complete the boundary union of continuations and controllers.
Their positive child families and cleanup are part of the call-domain proof;
whole-native numeric maxima for these rows are not required by the extension-live
gate.

| Opcode | Case → target | Complete additional descendant class |
| --- | --- | --- |
| `0x00` | `0x53f64` → `0x44094` | two `0x3bdca` counter additions; UNLK discards the retained arguments |
| `0x13` | `0x5405a` → `0x446f0` | `0x3bdca`, `0x38dc8`, `0x44c`, `0x400`, `0x3c090`; explicit 12- and 28-byte cleanup |
| `0x17` | `0x54092` → `0x44a1a` | conditional `0x3bdca`; UNLK |
| `0x18` | `0x540a0` → `0x44a3e` | `0x36896` leaf, `0x3bdca`, `0x38dc8`; 12-byte cleanup |
| `0x2f` | `0x541ae` → `0x45cac` | `0x3bd24` or `0x400`/`0x426`; common 8-byte cleanup |
| `0x30` | `0x541b8` → `0x45e1e` | `0x349b2`, `0x548bc`, `0x3a04e`, `0x3b092`, `0x3ae3a`, `0x54912`; data-indexed record pointers, no code-pointer call |
| `0x31` | `0x541c2` → `0x45d04` | `0x547d4`, `0x3b77e`, `0x3c7ec`/`0x3c090`, `0x3bd24`; UNLK |
| `0x54` | `0x54378` → `0x4674a` | two `0x3c090`; 16-byte cleanup |
| `0x57` | `0x54396` → `0x46c6a` | two `0x3bdca`, `0x349b2`; 20-byte cleanup |
| `0x58` | `0x543a0` → `0x4684a` | `0x3bd24`; UNLK |
| `0x59` | `0x543ae` → `0x46d48` | `0x35b48` (option `0x10`), `0x3bd24`; 12-byte cleanup or an immediate RTS |
| `0x5a` | `0x543b8` → `0x46d94` | `0x3bd24`; UNLK |
| `0x5c` | `0x543d0` → `0x46e3c` | `0x3c090`; 8-byte cleanup |
| `0x61` | `0x5440a` → `0x4794a` | conditional `0x3bd24`; 8-byte cleanup |
| `0x62` | `0x54414` → `0x467a4` | data-indexed immutable descriptor → `0x3bd24`; 8-byte cleanup on EACH loop iteration, saved D2 restored |
| `0x6f` | `0x544ae` → `0x44a98` | `0x3bdca`; 8-byte cleanup |
| `0x70` | `0x544b8` → `0x4797a` | an index below 256 constructs a data descriptor on the stack, then `0x3bdca`; UNLK |
| `0x71` | `0x544c6` → `0x47a50` | `0x47a24` + two `0x3bd24`; 16-byte cleanup, restores D2/D3 |
| `0x72` | `0x544d4` → `0x47a84` | same domain, different immutable descriptors |
| `0x73` | `0x544e2` → `0x47ab8` | same domain, different immutable descriptors |
| `0x74` | `0x544f0` → `0x47aee` | same domain, different immutable descriptors |
| `0x94` | `0x5467c` → `0x47d1c` | `0x3bdca`; UNLK |

Opcode `0x30`'s additional tree is `0x349b2` → `0x34a84`/`0x34a18`;
`0x3b092` → `0x39eb8`/`0x39ed2` → `0x69bf6` (a fixed width 4/5 BCD-add leaf); and
`0x3ae3a` → `0x3ad9c`/`0x3a20a`/`0x3a92e`/`0x3a9f8`/`0x35a26`. `0x3ad9c` validates or
repairs a fixed five-byte record using checksum, copy and scalar helpers, or TRAP1;
`0x3a20a`, `0x3a92e` and `0x3a9f8` specialize the accepted conversion, checksum and
copy helpers to width 5. `0x35a26` calls `0x38f1c`, `0x35840` and `0x426`; `0x38f1c`
calls only the byte-order leaves `0x38fd4`/`0x38ff2` and `0x44c`. The final 20-byte
cleanup restores SP. `0x47a24` calls `0x38dfe` and `0x68bcc`: the first returns data, and a zero divisor is retained as
the DIV0 fresh-SP alternative rather than excluded by a made-up nonzero invariant.
No selected row interprets its amount, counter pointer or queue message as code.

## Owned scalar and selector boundaries

| Family | Positive children, boundaries and balanced-return evidence |
| --- | --- |
| `0x23b78` | Boolean leaf through `0x23bec`; `0x23bee` is a different function |
| `0x4fd96` | type 2 option `0x5`, `0x363c2`, `0x400`; eight-byte cleanup in loops, RTS at `0x4fe92`; `0x4fe94` is separate |
| `0x33c2e`, `0x58970` | numeric `0x3a04e` / fixed queue services; no receiver invocation |
| `0x5ab18`, `0x5a256`, `0x59af8` | `0x548bc` / option `0x6` / `0x35b48` + `0x4ef34`; no callback |
| `0x36948`, `0x1e1aa`, `0x1e1c6` | option getter or scalar leaves |
| `0x1e258` | the literal `0x5c266` operation 4 selects a leaf; not arbitrary controller dispatch |
| `0x24d6a`, `0x24f58` | selected accounting `0x15`/`0x96`/`0x97`/`0x98`, numeric counters, `0x17b14` publication; 48 and 40 retained argument bytes at `0x24f46` and `0x25060`; the loop at `0x25066` pops 40 |
| `0x51da2` | frame-free `0x51dbc`/`0x520ac`; numeric and limit helpers; the only external boundary is `0x599ba`, at entry + 60 via `0x520ac`:`0x52222` |
| `0x523a4` | numeric `0x547d4`/`0x5270e` and selected accounting `0x6a`; the only external boundary is router key `0x201`, at entry + 72 at `0x524a4` |
| `0x5a16c` | accounting opcode `0x15` and fixed waits; the only boundary is router key `0x113`, at entry + 12; each wait pops 4 |
| `0x34d22` | scalar mode and data publication; router key `0xF6` boundary at entry + 16 at `0x34db4` |

`0x51dbc` and `0x520ac` use `0x5270e` → `0x53dd0` (accounting opcodes `0x14` and
`0x37`) and the data selection `0x527e2`/`0x52852` → `0x38dfe`. No further callback is hidden behind a limit
or paytable pointer. All normal child returns balance; TRAP1 halts and DIV0 resets
SP. At sites that only native code reaches, the already-earned optional ceilings
E + 454 (numeric and fatal) and E + 540 (router key `0xF6`) are **not** included in
the covered 328 promise, and are not silently qualified.

## Controller and continuation composition

The complete child domains are numeric, scalar, output, formatter and queue
services, with only the router and accounting boundaries listed below. All selected
boundaries are closed in **Selected settlement accounting opcodes** above and in
`SETTLEMENT-GUARD.md`, **Selected communication domain**. No stability of MODE
across waits, unlocks or sequential protocols is assumed.

| Owner | Child families and exact delegated entry offsets below the owner |
| --- | --- |
| `0x3fefa` (F = 12) | `0x404aa` queue drain; `0x4570` → router key `0x210` at +24; `0x36594` stores; `0x35962` queue and copy; `0x380d6` RNG; progressive 7; accounting `0x2f`/`0x30`/`0x31` at +24/+28/+28 |
| `0x5aa68` (argument 6 or 7), F = 4 | `0x4e412`, option `0x2`; `0x34d22` → key `0xF6` at +28; router key `0x31` at +16; `0xa64c`; `0x58970` queue; arguments popped at `0x5aa8c` and before `0x5aae0` |
| `0x599ba` (frame-free) | `0x4e412`, options `0x2`/`0x11`, `0x5a256`, `0x59b4c`; key `0xF6` at +24; router key `0x31` at +8; accounting `0x18`/`0x15` at +12/+16; explicit argument cleanup and RTS (`0x599f2`/`0x59ae6` and the final router cleanup) |
| `0x59b76`, F = 20 | `0x4df4a`, `0x38dfe`, arithmetic, progressive 1, mailbox and delay; key `0xF6` at +44; accounting `0x17` at +36; retry and delay cleanups at `0x59ca8`/`0x59cee`/`0x59d42`; the body includes the RTS at `0x59ea4` |
| `0x4b78e`, F = 28 | fixed counters, `0x4e188`, `0x3a652`, `0x63ac6`; no controller callback; the reader retains 12 and the formatter 28 at `0x4b8be` |
| `0x1d70c` | progressive 2 with the current masked snapshot; later independent accounting `0x71`..`0x74` at +12; no assumed stability between reads |

Progressive `0x1d48a` operations 1, 2 and 7 select the `0x1c438` cases
`0x1c4a6`/`0x1c4d4`/`0x1c4b8` and the leaf `0x1ce06`, or the `0x17b14` data
publication. Operations 3, 4 and 5, used below `0x61172`, select numeric and queue
work; operation 4's `0x1c9b0` uses `0x426` and the leaf `0x1ce06`. None of
them invokes the asynchronous progressive task inline. All use a balanced unlock
and return.

The complete 30-entry `0xa64c` display domain, and its separation from
legitimate controller entrants, are recorded in `SETTLEMENT-GUARD.md`, **Display
callbacks**. The display children use counters, protected writes, BCD and display
formatters, option getters and bounded local switches; none invokes another
controller callback. In particular: `0x4bb9a` → `0x63b62`/(`0x4bbba` or
`0x4bf22`); `0x4c3aa` → `0x4cea4` → `0x4de7e`; `0x4b56a`/`0x4b578` →
`0x4cc38`/`0x4cb2a`; and `0x56176` → `0x4c7ec`/`0x6828e`. The formatter trees
`0x38c52`, `0x38d14`, `0x4e128`, `0x4e188`, `0x4e1e0`, `0x3a652`, `0x3a6b6`, `0x3a73c`
and `0x3a764` terminate in fixed division, byte-copy, checksum and the
`0x3a50a`/`0x34b3e` leaves. Options are
immutable type 2 direct reads, except `0x10`/`0x11` (type 4) and `0x1b` (type 5),
whose BCD-reader wrappers are included. The local switches `0x4b372`,
`0x4cc58`, `0x4dba4`, `0x4dce4`, `0x4ddb0`, `0x63a94` and `0x5c28c` have bounded tables, not arbitrary
function pointers. Delays and waits may last indefinitely, but they do not
accumulate frames.

### Positive continuation child domains

The root closure includes these branch families, not just the straight normal-start
edge at `0x61e0e`. Delegated routers and accounting use the exact selected rows
above and in SETTLEMENT-GUARD; fixed kernel services only queue values or resume
saved contexts.

| Parent | Complete child families and covered or delegated boundaries |
| --- | --- |
| `0x61c50` | early `0x62348`; `0x400`/`0x426`, `0x620aa`, delay `0x3a1e`, queue `0x3b84`, `0x61ff8` → `0x50086` leaf; `0x61172`; `0x53dd0` (`0x4c`/`0x62`/`0x61`/`0x5c`) |
| `0x62348` | `0x64a28` scalar/counter/option family; `0x629b0` → `0x53dd0` (`0x00`); `0x62422` → router key `0x30` and progressive 4; `0x6262c` → `0x4360` mailbox, `0x49f82`/`0x41152`/output leaves, option reads, queue and mailbox |
| `0x61172` | numeric `0x54912`/`0x548bc`/`0x549c8`/`0x4341c`; `0x400`/`0x426`/`0x44c`, option and personality, delay and input `0x396e2`/`0x3976c`, cache and copy `0x36028`/`0x38ad0`; `0x34d22` → key `0xF6`; `0xa64c`; router key `0x34`; progressive 3/5; `0x3fbbc` diagnostic; queue `0x3b84`; `0x5d368` native motion; formatting `0x38c52`/`0x34b3e`; `0x64d5a` → `0x14d3e` → `0x14c66` TX; selected `0x53dd0` rows |
| `0x3fbbc` → `0x664a0` → `0x6728a` → `0x67e76` | numeric and typed options, `0x3a04e` conversions, protected stores, RNG `0x380d6`, fixed arithmetic, sampling and copy leaves; selected accounting `0x6f`/`0x61`/`0x70` and `0x3fefa`; no evaluator or controller table callback |
| `0x59f3a` | `0x63ac6`, `0x5a102` → `0x34b3e` formatting, mailbox `0x3a52`, `0x400`/`0x44c`/`0x500`, queue `0x3b84`; `0x599ba`; `0x53dd0` (`0x13`/`0x14`); `0x1d70c`; `0xa64c`; `0x61c50`; `0x34d22`; `0x3fefa`; delay `0x3a1e`; `0x5aa68` (argument 6) |

The diagnostic downward chain uses entry increments of 36 below `0x61172`, 76 below
`0x3fbbc`, 64 below `0x664a0`, 72 below `0x6728a` and 28 below `0x67e76` to reach
`0x3fefa`. It is native-only after each early changed-hook wrapper returns, so it is
not charged as a second 192-byte frame. Its only controller boundary is the already
closed `0x3fefa` family. ROM word `0x7f2` = 0 excludes the call at `0x61678` in
`0x61172` to `0x604fa`; ROM word `0x7fe` = 0 excludes the call at `0x5d3ae` in
`0x5d368` to `0x34d22`, and the alternate `0x5d3c0` branch. These
are fixed-ROM control-flow facts, not assumptions about RAM history. Other mutable
predicates are overapproximated. The bounded local switches include
`0x6632c`/`0x67b4e`/`0x67ee4`, `0x60fe6`, `0x629cc` and `0x14b7e`; their compare and
range checks precede the indexed word loads, and their defaults and returning
continuations are included. The code after a delay call (`SC_DELAY`, the wrapper at
`0x3a1e`, which Ghidra wrongly annotates as non-returning) is never treated as
unreachable.

### Covered continuation prefixes and unwind

| Covered native point | Below the `0x61c50` entry | Below the `0x59f3a` entry |
| --- | ---: | ---: |
| `0x61172` entry / `game_start_entry` | 4 | 24 |
| `0x615ce` candidate JMP | 28 | 48 |
| `0x615fa` commit JSR entry | 32 | 52 |
| `0x3fbbc` entry via `0x6171e` | 40 | 60 |
| `0x3fbcc` line-order JMP | 84 | 104 |
| `0x3fc34` wager JSR entry | 88 | 108 |

The accepted 192-byte **changed-hook** envelope (not a bound on the whole native
body of `0x61172`) gives 196 below `0x61c50` and 216 below `0x59f3a`. The call at `0x61e0e`
has no retained arguments; the call at `0x5a082` in `0x59f3a` adds 20. The diagnostic entry adds 36 below `0x61172` (F 24 + A 8 + 4).
`game_start_entry`'s refusal returns without creating the native 24-byte frame; on
success it restores its wrapper, replays that frame and jumps to `0x6117a`. The
candidate and commit wrappers restore their scratch before the native continuation.
No permanent extension frame remains.

`0x59f3a` has F = 16: its loop at `0x59f7a` removes 16 each iteration, and
the timeout at `0x59fc4` and `0x5a0e2` retain 24 until the frame-pointer-based
restore, UNLK at `0x5a0fe` and RTS at `0x5a100`. `0x61c50` is frame-free;
`0x61ebc` pops 16 before the RTS at `0x61ec0`. `0x61172` restores from its
frame-pointer slots at `0x61920`, and the UNLK at `0x61926` discards the final
arguments before the RTS at `0x61928`. The deeper diagnostic frames
`0x3fbbc`/`0x664a0`/`0x6728a`/`0x67e76` are ordinary balanced returning frames.
These facts bound covered work on return; they do not assert the safety of excluded
native-only temporary peaks.
