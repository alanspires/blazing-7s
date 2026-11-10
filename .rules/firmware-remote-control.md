---
paths:
  - "firmware/src/core.c"
  - "firmware/src/fw.h"
  - "firmware/src/platform.c"
  - "firmware/src/platform.h"
  - "firmware/src/platform_hooks.S"
  - "firmware/tests/test_core.py"
  - "firmware/tests/test_platform.py"
  - "firmware/INTEGRATION.md"
  - "tools/sas_extension.py"
  - "tools/sas_profile.py"
  - "tools/test_sas_extension.py"
  - "tools/test_sas_profile.py"
  - "tools/test_sas_firmware.py"
  - "tools/fixtures/s6000_profile_reply_timing.json"
  - "notes/remote-game-config.md"
  - "notes/firmware-reply-probe-*"
---
# Firmware remote control

See `firmware.md` for build and qualification constraints, `sas-host.md` for stock
SAS transport, `firmware-game.md` for native wagering/outcomes, and
`firmware-native-guards.md` for serial drain, calibration and stack boundaries.

## Wire identity and uncertain outcomes

- Keep `fw.h` and `sas_extension.py` in lockstep: private E0 is not a stock SAS
  catalogue command. Validate outer address/command/length/CRC before core entry.
  Inner S6RE v1 headers are 14 request / 15 reply bytes, excluding the outer
  length byte; payloads are at most 64 bytes. IDs and protocol integers are LE.
  Match reply version, operation, ID, status and exact payload length; ACK is not
  an extension result.
- Discover capabilities and limits before mutation. INFO `next_id=0` prohibits
  mutations (fault or exhausted IDs). Queries use ID zero; mutations use increasing
  nonzero u64 IDs. Host allocation starts at INFO's next ID; firmware permits gaps.
- Reserve the ID and exact request durably before execution. Only an identical
  latest request may reuse its ID; compare length and bytes, never just CRC.
  Older IDs and changed reuse fail. A recorded refusal is also stable: retrying
  that ID after conditions improve returns the refusal rather than executing.
- Send mutations once with `retry=False`. Missing/malformed replies, serial errors
  and interruption after transmission may mean the operation happened. Preserve
  `UncertainOutcome` and its ID; reconcile STATUS/readback, never auto-reissue with
  a new ID. Explicit repeat must use the exact latest request.
- ACCEPTED is pending, not completed. STATUS/exact repeat polls and journals a
  terminal action result. Pending actions block new mutations except an armed
  outcome's cancel/spin handoff. Boot marks interrupted actions INTERRUPTED and
  never resubmits them; profile recovery is a separate lifecycle.
- Only COMMIT_PROFILE and RESTORE_STOCK use the named two-second first-byte budget.
  Keep ordinary SAS's default unchanged. Captured replies exceeded 250 ms; the
  observations and two-second window are not worst-case guarantees. A later reply
  still means uncertainty, not permission to retransmit.

## Profile validation and nominal math

- Keep host encode/decode validation and `fw_validate_profile` aligned under INFO
  caps. Offline codec defaults are field-width limits, not safe operating limits.
  Firmware permits 1–5 lines, 1–3 credits per line, 15 total, and nominal awards
  0–1500 per record (at most 7500 per spin); retain the u32 aggregate overflow check.
- Profiles keep the pinned personality's strips, symbols, groups and line patterns.
  The 252-byte S6PF v1 payload has 22×3 byte weights and eleven ordered native
  records. Each reel total must be positive; it need not equal stock's 32.
  Native record awards are BE, unlike protocol integers. Reserved bytes stay zero.
- Used symbols are only 0A–0E, 18, 80 and 81; 24 is allowed only in the two unused
  symbol columns. Preserve match count three, exact record ordinal and zero tail.
  Inclusive per-line wager bounds must cover every allowed bet. Masks are nonzero
  subsets of enabled lines; flags are only 00/10/20/40, with nonzero flags restricted
  to center mask 01. Relaxing these restrictions requires re-proving native cache safety.
- Native consumers read two extra records: committed slots must contain 32 zero
  guard bytes beyond the wire payload, validated at boot and covered by slot CRC.
  Never publish pointers into an unguarded upload or scratch buffer.
- Preview charges bet per line × lines and evaluates the fixed first line patterns
  in record order. A zero-award first match blocks later payouts and is not a hit.
  Progressive amounts are nominal record awards; external bonuses are excluded.
  Neither preview nor nominal caps proves actual external-award or cabinet safety.
- BEGIN replaces volatile staging; chunks may arrive out of order or repeat equal
  bytes. Reject a conflicting overlap atomically. COMMIT needs full coverage,
  matching CRC and valid profile; staging does not survive boot.

## Durable activation and recovery

- Keep persistence layout authoritative in `fw.h`: alternating slots and journals,
  CRC-protected records, marker cleared before body, verified body before final
  aligned atomic two-byte marker. Intact-marker corruption must fault, not silently
  select an older acknowledged configuration or request. Sequence exhaustion fails closed.
- Bootstrap only uniform zero/FF storage or exact interrupted virgin-baseline
  prefixes. Establish durable ID-zero history first. Never format arbitrary nonblank
  SRAM or erase recognized history; see `firmware-commissioning.md`.
- Preparation owns native exclusion through application and durable final status.
  Require idle including zero wager and retired motion; recheck ownership/admission
  under scheduler exclusion before writes. Commit slot, then profile journal, then
  apply native configuration; only successful application may become durable OK.
- First activation captures the original native configuration baseline; subsequent
  custom commits inherit it from core's active slot, not inferred native pointers.
  `game.c:take_baseline` binds that baseline to unowned configuration and selection
  context. Never refresh the fingerprint around stale data. Old slot formats and
  mismatched context fail closed without stock reconstruction or migration shims.
- Apply only owned fields via protected whole-field writes, read each back, then
  verify configuration sum before cache rebuild/publication. Stop at the first
  mismatch without retry; never clear native fault latches or run the full loader
  as a substitute for restoring the recorded baseline.
- RESTORING journals retain exact source slot, CRC and generation. Pending recovery
  validates and reapplies that baseline even if native pointers already look stock.
  Failed restoration faults boot and retains evidence. Only durable OK retires the
  source requirement, allowing later slot reuse. Already-stock restore is a native
  and profile-journal no-op, but still records its operation ID/result.
- A profile journal newer than operation history faults before application or a new
  mutation ID. `fw_boot` loads history; `fw_restore` applies and finishes pending
  profile changes. Startup restoration must precede native consumers, caches and
  checksum tasks. The explicit boot window closes at `platform_boot_restore`;
  runtime loader tails cannot reopen it or bypass prepared ownership.
- Native clear must durably invalidate before clearing and mark completion only
  afterward. Interrupted clear faults rather than reviving an old profile; request
  history survives. Retained runtime readiness must be reset before native reload.
- Restore stock and verify STATUS before changing native game options or removing
  extension ROMs. If restoration is unavailable, use qualified recovery, not a ROM
  swap and resumed play. Persistent custom payout pointers depend on the extension.

## Verification and reporting

- Upload success requires full byte readback, CRC and matching completed commit ID.
  Bracket active-profile reads with stable generation/CRC/fault state. Stock download
  is the host's pinned personality model, not a live dump of native configuration.
  Custom/faulted SAS identity must not advertise the stock return percentage.
- For protocol/profile changes run affected host codec/extension tests plus compiled
  core/platform and `tools/test_sas_firmware.py` integration. Preserve word-cut,
  dropped-write, exact-repeat/CRC-collision, boot/clear and baseline-replay coverage.
  Replay the captured reply-budget fixture without wall-clock timing assertions.
  CPU ports and trace replay do not establish physical atomicity or timing bounds.
