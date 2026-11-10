# S6000 extension implementation contract

This is the contract between the firmware extension and the native S6M00002B5 ROM, and
between the extension and its host client: the behavior it must provide, the profile
format, the private E0 wire protocol, the native hook points, the RAM layout,
persistence and recovery, stack boundaries and startup ordering. It is for readers who
want to check the code in `src/` or `../tools/sas_extension.py` against the intended
design, or to change either side. Addresses are CPU addresses in the main program's
map; `FUN_xxxxxxxx` names are the Ghidra project's names for native routines.

**Status: offline prototype implemented and tested; not installation qualified.**
See `README.md` for the recorded gates, artifacts and outstanding qualification.
The build involves no live machine operations. The original ROMs remain read-only
inputs.

## Approved behavior

- Preserve the stock game until an explicit profile activation.
- Profiles charge `bet_per_line × enabled_lines`; maximum five lines (native storage
  has only five line accumulators). Initial implemented operating cap is 1–3 credits
  per line, up to 15 total; codec byte widths are not operating-limit evidence.
- Persist profiles in battery-backed SRAM with checked, power-loss-safe activation.
- CLI bet-and-spin, a selected next normal outcome, and diagnostic reel positioning.
- No additional password/approval guard; still reject busy, fault and conflicting
  operations. Native sensors, accounting and recovery are not bypassed.
- Programmer: XGecu T48. The owner reports spare “Main W/PRINTER” chips detected as
  Winbond W27C02, ID DA85. These require 256 KiB lane images, not the original
  512 KiB mirrored packaging. Exact markings/speed, backups and physical
  program/readback remain qualification gates; see `PROGRAMMING.md`.

## Build

`build.py` uses `patches.json`: base path/hash, extension start/end/expected fill,
and nonoverlapping expected-byte patches (hex replacement or symbol plus
`jsr`, `jmp`, `pointer`, or explicit whole-region `jsr-nop`). Padded calls preserve
the complete expected instruction region; ordinary symbolic widths stay strict.
Sources `src/*.c` and `src/*.S` link via `link.ld`. ROM extension candidate is
`0x79100..0x7ffff`, with the final word reserved for checksum compensation. Original
boot checksum is retained. Outputs include the 512 KiB merged image and exact,
unmirrored 256 KiB even/U28 and odd/U43 lanes for the fixed Winbond W27C020/W27C02
DIP32 target. Manifest format `s6000-offline-build/2` records the intended device,
DA85 signature and capacity; it does not verify the physical chips. Build manifests
always say `install_ready: false` until a separate physical qualification process exists.

## Profile format v1

The host codec is `tools/sas_profile.py`. Binary profile is exactly 252 bytes:

| Offset | Size | Meaning |
|---|---|---|
| 0 | 4 | ASCII `S6PF` |
| 4 | 1 | Version 1 |
| 5 | 1 | Enabled line count, 1–5 |
| 6 | 1 | Maximum bet per line, 1–3 in the first firmware ABI |
| 7 | 1 | Record count: 11 (installed game's count) |
| 8 | 66 | 22 rows × 3 reels of unsigned byte weights; each reel total positive |
| 74 | 2 | Reserved zero |
| 76 | 176 | 11 native 16-byte payout records |

Physical symbols/strips stay unchanged. Runtime line order is always center, top,
bottom, descending diagonal, ascending diagonal; do not use native even-count
ordering, which introduces a row outside the intended visible three-row area.
Nominal awards are 0–1500 credits per record. A zero-award match ends that line
without paying or counting as a nominal hit; it never falls through to a later
record. Records preserve the original shape (three symbols, two `0x24` unused symbols,
max/min eligible per-line wager, flags, line mask, match count 3, index, trailing
zero). Progressive flags are restricted to the center line; actual progressive
amounts are not replaced by a nominal math preview. Firmware operating limits must
be validated independently of the codec's field widths.

## Wire protocol v1

Private long poll `0xE0` in this pinned ROM, variable-length request. It is not a
standard SAS feature and must not be added to the stock command catalogue. Require
capability discovery before mutations. SAS framing/CRC/serial transport are reused:
on the wire each message is the SAS address byte, `0xE0`, the data below and the
SAS CRC.

Request data after command: `[length:u8][S6RE:4][version:u8=1][op:u8][id:u64le][payload]`.
`length` counts everything after itself (14 + payload length); maximum payload 64.
Reply data after command: `[length:u8][S6RE:4][version:u8=1][op:u8][status:u8][id:u64le][payload]`.
`length` is 15 + payload length; maximum reply payload 64. The reply repeats the
request's op and ID. Only OK replies to INFO, STATUS and READ_PROFILE carry a
payload; every other reply, including a mutation's, has an empty payload. The host
requires the exact payload length for the op. A transport ACK is not operation
completion.

All mutation IDs are monotonically increasing nonzero integers, allocated from
read-back of the machine's durable last ID. Query ID is zero. An identical repeat
of the latest mutation returns its recorded state without execution; mismatched
reuse and older IDs are refused. Persist an accepted/reserved ID BEFORE any side
effect. After reboot, interrupted motion/spin requests are reported interrupted,
never executed again. Profile commit recovery is separate from game-action replay.

Operations:

| Code | Name | Request payload |
|---|---|---|
| 0 | INFO | empty; capabilities and limits, next mutation ID |
| 1 | STATUS | empty; last operation ID/state, active profile generation/CRC32 |
| 2 | READ_PROFILE | offset:u16le, count:u8 (1 to 64, within the 252-byte active profile) |
| 16 | BEGIN_PROFILE | total_length:u16le=252, CRC32:u32le |
| 17 | PROFILE_CHUNK | offset:u16le, bytes (at most 62) |
| 18 | COMMIT_PROFILE | empty; require complete valid upload and idle machine |
| 19 | RESTORE_STOCK | empty; idle-only restoration when custom, no native change when already stock |
| 32 | ARM_OUTCOME | three zero-based stop bytes |
| 33 | CANCEL_OUTCOME | empty; cannot cancel a committed game |
| 34 | BET_SPIN | bet_per_line:u8 (1 to the active profile's maximum bet per line; 3 in stock mode); optional three stop bytes |
| 35 | POSITION | three zero-based stop bytes (diagnostic operation) |
| 36 | JOG | reel:u8 (0 to 2) |

Stop bytes are 0 to 21. Operations 16 to 19 need capability PROFILE (`0x01`), 32 and
33 OUTCOME (`0x02`), 34 SPIN (`0x04`), 35 POSITION (`0x08`) and 36 JOG (`0x10`);
an operation whose capability INFO does not advertise is answered UNSUPPORTED.

The INFO reply payload is 23 bytes, little-endian: capabilities:u32,
next_id:u64 (zero when mutations are prohibited), profile_size:u16, max_chunk:u8,
max_lines:u8, max_bet_per_line:u8, max_total_bet:u8, stops:u8, max_award:u32.
The STATUS reply payload is 20 bytes, little-endian: last_id:u64, last_op:u8,
last_status:u8, active:u8 (nonzero when a custom profile is active), generation:u32,
profile_crc:u32, fault:u8. (`src/core.c:fw_process`, `../tools/sas_extension.py`
`INFO` and `STATUS`.)

Statuses: OK=0, ACCEPTED=1, BAD_REQUEST=2, BAD_VERSION=3, BAD_ID=4,
BUSY=5, FAULT=6, INVALID_PROFILE=7, INCOMPLETE=8, INTERRUPTED=9,
UNSUPPORTED=10. Any additional wire change must update host and firmware together.

## Native integration evidence / constraints

- SAS handler checks native CRC at `FUN_0002749a`; receive data is at
  `0x4106fb + (host_port-1)*0x340`, starting with the variable length byte.
- Transmit buffer is `0x414234 + (host_port-1)*0x15f6`; first byte is count,
  then address, command and our data. `FUN_000278fe(0)` appends the native CRC
  and sends. These ABIs need CPU tests of the actual original routines.
- SRAM reservation: `0x430000..0x435fff`, kernel pool positively bounded
  to `0x400000..0x40ffff`; leave supervisor stack arena `0x436000..0x437fff`.
  Reset preservation is CPU-tested. Actual battery retention/address decoding
  remain physical qualification; see `PROVISIONING.md`.
- Main task event-reader hook `0xa53e` replaces `JSR 0xae6a`. Native events: 9 is
  credit BET ONE; 1 is SPIN/repeat-bet; 4 is a physical coin, NEVER fabricate it.
- Leave `FUN_0004341c` a one-credit primitive. Expand only BET ONE caller `0x42fac`
  to whole-line cost; repeat/max-bet `0x432f8` already loops over total credits.
  A first multi-line wager calls native `0x420f8` after its first credit, before
  adding the remaining lines; the native continuation initializes only wager one.
- Physical coin deposits remain one real coin. SPIN must not accept a total wager
  that cannot be divided by the active number of lines.
- Evaluator `0x65128` uses wager at `0x4103b0`; native credit/meter wager must remain
  TOTAL while matcher eligibility becomes PER LINE. `0x43ca27=1` enables all lines.
- Source-selection hook candidate `0x615ce` must preserve stock recovery guards;
  post-commit hook `0x615fa` can record consumption. Never override physical targets
  independently of committed outcomes.
- Direct-motion routines contain unbounded waits and native fault recovery may move
  reels. They are not safe synchronous SAS APIs. The motion module owns an already
  running Test 10 loop with bounded command states, fault latching and cleanup.
  Production keeps `MOTION_QUALIFIED=0`; INFO advertises no diagnostic motion yet.
  Requests require an observed loop pass, not just the selected-test byte. The
  `0x5af86` epilogue hook reconciles every return, including initial-wait exits.
  Fault checks, ownership tests and motor writes share one bounded IRQ section.
- The `0x61172` entry guard covers event-driven and direct native normal starts.
  It honors profile/motion ownership, including latched failure. Custom profiles
  additionally require ordinary whole-line wagering; other native start paths
  are refused rather than evaluated under unaudited semantics.

## Persistence and native configuration recovery

The authoritative layout and byte limits are in `src/fw.h`; `link.ld` assigns the
working-state symbols. Two 512-byte profile slots occupy `0x430000..0x4303ff`.
Two 128-byte profile journals occupy `0x430400..0x4304ff`; two 128-byte operation
journals occupy `0x430500..0x4305ff`. Core working state occupies the 1024-byte
reservation `[0x430600,0x430a00)`, including its separate upload and temporary
slot buffers. `fw_process` is serialized by the native dispatcher lifecycle;
the serial drain below prevents its asynchronous deletion during core processing.
This does not promise delivery of the response. Motion runtime
has `[0x430a00,0x431000)`. Commissioning's durable record occupies
`0x431000..0x4317ff`, with its working context at `0x431800`. Game runtime retains
`[0x432000,0x433000)`, used earlier for commissioning register saves.
`[0x433000,0x434000)` is a shared private stack: commissioning owns it before
kernel startup, then the serialized E0 callback owns it. Commissioning restores
native SP before normal startup and cannot resume concurrently with E0.
Platform runtime at `0x434000` is limited to 16 bytes, followed by the AC-fail guard
at `0x434010`, E0's saved caller SP at `0x434020`, the serial completion mailbox
at `0x434024`, and commissioning IO at `0x434100`.
Staged archive bytes occupy `0x435000..0x435dff`. See `COMMISSIONING-CONTRACT.md`.

Operation journals contain the exact latest request and its length, not merely a
CRC digest. CRC32 checks stored integrity; it is not used as proof that two
requests are identical. Commit markers are aligned two-byte writes. CPU tests
interrupt every modeled word write, assuming physical word-write atomicity.

A virgin store is initialized with a durable ID-zero baseline before accepting
mutations. Automatic bootstrap accepts only uniformly zero/FF storage or exact
prefixes of those canonical initial writes. **Arbitrary nonblank SRAM is not
formatted automatically.** The pre-boot commissioning service instead requires
an exact durable host archive and explicit first-install intent, refuses recognized
extension history, and initializes through a separate READY/DONE transaction.
`PROVISIONING.md` records the implemented route and physical qualification still
required. Normal SAS remains unreachable from the original startup-fault loop.

Before a runtime profile change, native admission takes ownership and excludes
physical event dispatch. Ownership lasts through native application and the
operation's durable final result. An in-flight or held native event prevents
admission. Waiting task 6 yields instead of starving the SAS worker.

Runtime preparation requires native idle (including zero wager) and a short
saved-SR motion snapshot: motor state 1, queue/finalization zero, completion
byte `0x410158`, long `0x4101fa` and word `0x4101a8` zero, and neither IFR nor
IER bit 5 set. `platform_timer2_active` reads only the VIA bytes at `0xfe10db`
and `0xfe10dd`; it never acknowledges a source. T1 remains unaffected. Under
scheduler exclusion, application rechecks this same idle/snapshot contract and
ownership before the owned-write batch. Failure before preparation returns BUSY;
failed application returns FAULT and keeps play inhibited. This relies on the
normal timer lifecycle in `IRQ-AUDIT.md`, not arbitrary peripheral state.

At slot offset 288, outside the wire profile and its two guard records, the slot
stores 177 original native configuration bytes: cap/lines/all-lines (3), the 32×5
weight array (160), three totals (6), and the selected count/records pointers (8).
They are serialized in address order by `game.c:next_field`, not by a second
field table. The first custom activation captures them from stock. Later custom
commits inherit exactly the same baseline, using core's active-slot authority
rather than inferring stock from the current paytable pointers.

Offset 284 holds a CRC32 fingerprint of unowned configuration, native
reel-set/paytable/alternate-wager context, and that baseline. Inheritance and
application require this fingerprint to match; a native option change cannot
refresh the fingerprint around an obsolete baseline. Offset 492 holds the
little-endian u32 slot format value 2. The slot CRC covers all of these fields;
old slots without the baseline format fail closed, without a compatibility path
or reformatting. Slot/journal magic values remain recognizable by commissioning.

Boot restoration reapplies only owned fields and the zero configuration sum.
Each protected store is read back at the whole field width, including both halves
of a pointer; a mismatch stops immediately without retry. The final checksum is
verified before cache rebuild/publication. Mismatched context or unrelated
corruption fails closed; native fault latches
are never cleared. **Restore stock before changing other native game options.**
There is no silent fallback to a stock ROM reconstruction after a mismatch.

RESTORE_STOCK commits profile-journal kind 3 (RESTORING), retaining the source
slot, its profile CRC and its generation (u32 at record offset 24). A matching
ACCEPTED operation makes boot strictly validate and replay that exact baseline,
even if some pointers already reached stock before power loss. It does not call
the full native configuration loader. A matching failed restoration faults boot
and retains its source for investigation; do not use commissioning to erase that
history. Only durable OK establishes completed restoration. Every profile-journal
request must already exist in operation history: a profile request ID newer than
the selected operation ID faults boot before application, persistence writes or
offering another mutation ID.

After completion, later boots do not read the old baseline, so a new custom
commit can reuse its slot safely. Already-stock restoration changes no native
configuration or profile journal and requires no idle admission; the request ID
and result still go through the operation journal. Profile commit and stock
restoration become durable OK only after native application succeeds.

## Native task and settlement resource boundary

The published image's capacity patch changes only the native task-stack size word at `0x27b6`
from 2048 to 3072 bytes for each of 18 preallocated slots. The separate reserve
at `0x27a8`, pool size, native allocator, TCB ownership and extension SRAM layout
remain unchanged. This configuration is consumed during initialization; it must
not be changed in a running kernel. `TASK-STACK-AUDIT.md` records the allocation
proof and the separate publication/qualification status.

`game_hooks.S:game_settlement_entry` intercepts every supported `0x589ba` entry
before its original prologue. It uses the owning TCB's allocation top at `+0x40`,
not saved SSP at `+0x38`; bootstrap instead uses `[0x436000,0x438000)` even when
CURRENT is already nonnull. Admission requires 2232 bytes remaining, with equality
allowed. Both exits remove the ten scratch bytes and restore the incoming registers/SR.
Success replays the original prologue and continues at `0x589c2`; exhaustion
preserves the caller's stack shape and tail-enters native TRAP1 fatal halt.

This guards the reviewed extension-live resource envelope, not every stock-native
temporary peak, arbitrary corrupt contexts or financial termination. Refusal may
leave settlement unfinished; it does not clear accounting state, roll back, retry
or de-energize the reel-drive latch. The positive first-entry, callback-selector,
IRQ and return-path proofs are essential—this check alone does not establish them.
See `SETTLEMENT-GUARD.md` and `INITIAL-ROOTS.md`; installation remains separately
blocked pending physical qualification.

## Runtime E0 stack boundary

`platform_hooks.S:platform_sas_handler` is the patched no-argument entry. It
unconditionally saves native SP, selects `sas_stack_end`, calls
`platform.c:platform_sas_process`, restores native SP and returns. No C prologue
runs before switching. The bridge itself changes no interrupt mask, allocation
or TCB stack-top field; the separate capacity patch is described above.
The caller's ordinary C ABI still applies.

The native kernel saves the actual supervisor SP at `0x2b30` to `TCB+0x38` and
loads it at `0x2b54`; it separately saves/restores USP. Creation uses the original
allocated top at `TCB+0x40`. The pinned supervisor-task configuration does not
switch to an alternate interrupt stack. Thus returning interrupts and scheduling
retain the currently selected stack, not an assumed address within the pool.

The ownership contract is one live E0 callback, valid native task objects, and no
resumption of a deleted task. Native serial reconfiguration deletes the old parent
before creating a replacement. A replacement therefore overwrites the saved SP
and abandoned private frames; a stale saved pointer is not a busy condition.
Recursive/concurrent entry, user-mode tasks and external kernel hooks are outside
this contract. Stack reuse alone does **not** repair asynchronous transaction
cancellation; the separate drain below protects core processing, not response
delivery.

Both windows need a bound: the published image's native 3 KiB stack before/after the
switch and the private 4 KiB stack during C processing. The reviewed conditional
peaks remain 1964 and 2928 bytes respectively; the native peak previously fit the
historical 2048-byte allocation. `IRQ-AUDIT.md` records emitted-image accounting,
real native-service regressions and the timer/interrupt premises. This E0 result
alone does not qualify Task6, Task7 or boot/restore callers: their complete scope
and gate status are in `TASK-STACK-AUDIT.md` and `INITIAL-ROOTS.md`. The drain's
worker paths fit the E0 allowance; its Task7 wrapper is included in that separate
non-E0 composition. Physical qualification remains independent.

## Serial reconfiguration boundary

`platform.c:platform_reconfigure` replaces the complete 18-byte callsite at
`0xc1a8`, preserving native `16682 → 15836 → 152ec` deletion/initialization/
recreation order and continuation `0xc1ba`. Task7 serializes this configuration
route. The communications test resumes suspended parents before returning to
its separate mailbox-dispatch case; no scheduler lock or IRQ mask encloses the
drain under the qualified native history. See `IRQ-AUDIT.md`, **Implemented
serial drain and offline gate**, for instruction evidence and limits.

Every `fw_process` call claims active ownership. Reconfiguration closes admission
atomically and captures whether it owes a wait, then restores SR before native
SC9. Core return clears ownership and captures notification before native SC8:
posting may wake a drainer that deletes the sender before the post wrapper returns.
A captured wait always consumes one token, even when active has already cleared.
The mailbox must be empty before closing and before reopening; native wrapper
status outputs are words. `platform_boot` resets this working state explicitly.

Admission stays closed through native recreation. A closed-gate E0 request is
NACKed without reaching core. The barrier ends at core return, **not UART TX or
completion of an asynchronous game action**; a stalled response remains
cancellable. Unexpected ownership, token or kernel-status failures use native
`platform_fatal` (TRAP1), not retries, evidence clearing or a new recovery path.
Native fatal halt does not de-energize the reel-drive latch.

Real-kernel tests cover both publication cuts and post/pend orderings, repeated
drains, admission closure and stuck-TX cancellation. Controlled scheduling,
register-memory MMIO and disabled ports are explicit fixture limits; these are
not whole-UI, physical timing or full Task7 stack proofs.

## Verified startup ordering and exclusion

Static ROM references and CPU tests establish the following ordering; this is not
physical battery-retention or timing qualification:

- `commission_reset` initializes the AC-fail guard before native reset proceeds.
  The `0xa58` commissioning gate follows scratch/ROM checks, precedes kernel and
  SafeRAM initialization, and uses a private stack. The original earlier seed/reset
  counter writes remain; this is not a read-only whole reset. A journaled native
  clear retains its normal operator recovery branch through `fw_clear_pending`.
- Original reset's nonzero-pattern test touches `0x400000..0x40001f`. Its zero pass
  clears through `0x437fff` on a cold/uninitialized boot, or only through `0x412871`
  when marker `0x43c332` is `0x55aa`. `ext_preserve_sram` skips
  `0x430000..0x435fff` only on the cold pass, retaining the native stack clear.
- Both reset paths reach `0x9d8`. `ext_reset_runtime` clears the retained platform
  ready flag without using a stack, then reproduces the original sentinel write.
  This prevents a pre-initialization native reload from reusing stale runtime.
- `FUN_0003825a` checks ADC/battery and native validity marker `0x43fff4`;
  `FUN_000025b0` checks native identity/initialization state. Neither checks the
  game-configuration sum. Their independent checks remain unchanged.
- Journal loading at `0x155c` opens an explicit boot window. The dedicated
  `0x1906` hook calls `platform_boot_restore`, restores and closes that window.
  Optional native loading at `0x1730` reaches the shared `0x37dde` loader tail
  while it is still open. Runtime uses of that tail cannot reopen it.
- Reset calls native `0x154e` at `0xbca`; after it returns, `0xbe8` initializes
  the VIA and `0xbec` starts the kernel. Early restoration therefore does not
  assume runtime idle/timer state. Runtime reapplication without prepared
  ownership faults when custom configuration is active; settled stock is a
  no-write operation. This does not undo native writes preceding a loader tail.
- The configuration checker runs via task entry `0xf4b6`, `FUN_0000f3fc` and
  `FUN_0000f2da`; that task is created after `0x1906`. Payout/cache construction
  at `0x1b24` and outcome recovery at `0x1c16` also follow restoration.
- `FUN_0000f2da` sums big-endian words `0x43c87a..0x43ccdd`; a failure latches
  `0x43c31c`. A later valid sum does not clear that latch.
- Native scheduler lock/unlock wrappers `0x3d82`/`0x3d88` issue trap commands
  `0x20`/`0x21`. Handlers `0x33e0`/`0x33f2` maintain nested counts at kernel
  workspace offsets 40/41. Configuration writes, their checksum and cache rebuild
  use this lock rather than masking interrupts throughout. IRQ4 does read the
  wager cap: whole-field protected writes expose its old or new value, not a
  temporary zero from full-block clearing. `IRQ-AUDIT.md` records the native
  regression, conditional IRQ6 lifecycle evidence and qualified kernel/stack
  boundaries. This is not a universal interrupt-exclusion proof.

Native clear has separate invalidation/completion hooks because it does not erase
this reserved region itself. An interrupted clear faults rather than reviving an
old profile. Nonblank provisioning now has an offline-tested software path; real
retention, commissioning transport/power-fail behavior, interrupt/motor timing,
accounting recovery and physical line alignment remain installation blockers.
The conditional extension-live stack proof does not qualify untouched native
peaks or behavior outside its valid-context and hardware premises.
