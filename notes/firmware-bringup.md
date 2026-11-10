# First live firmware bring-up — 2026-10-19

## Scope and current state

This records the first live tests of the guarded extension on the user's ProSlot
6000. It does not replace the scoped software proofs or establish full electrical,
mechanical, fault-recovery or installation qualification. `install_ready` remains
`false`.

The user reported installing the verified Winbond spare U28/U43 pair and a normal
idle display; they were instructed to preserve the original chips. Each test spin
and the profile upload/restart/restore sequence had separate approval. The assistant
sent no SafeRAM-clear, commissioning-initialization or credit-transfer command,
replayed no mutation and performed no firmware reflash during these tests.

Last observed state, evidence run `j113`:

- Stock mode, generation 7, profile CRC `00000000`.
- Request 26 `RESTORE_STOCK`: `OK`, not pending; next request ID 27.
- Extension fault false; credits 1014.
- Coin in 550, coin out 1565, drop 1015, jackpot 6000.
- Games played 185, games won 26, door openings 17, power resets 12.

The corrected production CLI now passes a live upload/readback/restore sequence,
without diagnostic overrides or mutation replay. Both normal commands received
valid replies; credits and all eight native meters stayed unchanged. See
**Normal CLI acceptance after timeout correction** below. Further checks require
an agreed procedure; electrical, mechanical and fault-recovery qualification
remains incomplete.

## Image and evidence

Main SHA-256:
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`.

Winbond-packaging manifest SHA-256:
`ef1728ca2cb342666e6b31ebee1be8fdc411322b36f7c509c67dfb296b5f7b01`.

[Programming record](../firmware/PROGRAMMING.md) identifies the exact chip images.
Full lane readbacks were checked before installation. This report does not infer
installed byte identity solely from a SAS signature or extension version.

[Captured evidence](firmware-bringup-evidence.json) preserves the exact job outputs
for `j83` through `j101`. Each run includes its original log file name. These are
semantic host results, not raw serial timing traces. Missing replies alone do not
identify their cause.

## Baseline and first boot

`j83`, with original ROMs still installed:

| Meter | Value |
| --- | ---: |
| Credits | 1014 |
| Coin in | 548 |
| Coin out | 1563 |
| Drop | 1015 |
| Jackpot | 6000 |
| Games played | 183 |
| Games won | 25 |
| Door openings | 14 |
| Power resets | 9 |

This snapshot is not a whole-SRAM backup.

After the user installed the new pair and powered on, raw commissioning INFO did
not answer (`j84`). Normal SAS did answer (`j85`), followed by the new E0 INFO and
STATUS (`j86`). Credits and financial/game meters were unchanged; door openings
and power resets each increased by one.

E0 reported stock mode, generation 1, no fault or pending operation, next ID 1,
252-byte profiles, 22 stops, maximum five lines, three credits per line, fifteen
total credits, and maximum award 1500. Advertised capabilities were profile,
outcome and spin (`0x00000007`), not standalone positioning or jogging.

No commissioning INIT was sent. `firmware/src/commission.c:commission_boot` can
continue normal startup when the core accepts the stored state; a first install
is not guaranteed to stop in the raw service. The live observation establishes
normal runtime, not successful commissioning-service qualification or the exact
pre-boot RAM contents.

The stock listing in `j87` is generated from the host's pinned personality ROM.
`tools/sas_extension.py:download` does not read a custom profile when STATUS says
stock. It is not a live dump of the native paytable.

## Approved losing spin and idle restart

Exactly one `game-spin 1` was sent under request ID 1 (`j88`). It answered
`ACCEPTED`, then STATUS reported `OK`, not pending, and no fault (`j89`).

Observed accounting: credits 1014 to 1013; coin in 548 to 549; games played 183 to
184; coin out and games won unchanged. Reported zero-based stops were 11, 6, 7.
This establishes one completed losing stock game through telemetry.

INFO reported next ID 2 (`j90`). Following a user-controlled power cycle, `j91`
retained the completed request, next ID, stock mode and all financial/game state.
Door and reset counters increased by one. This is an idle-restart retention check,
not an interrupted-write or electrical power-fail test.

## Approved small winning outcome

Offline analysis of the hash-pinned personality predicted a two-credit ordinary
award for a one-credit center-line wager at zero-based stops 0, 0, 0: symbols
24/24/24, paytable record 10, flags zero. No progressive award was requested.

The user approved exactly one such spin. Request ID 2 was sent once (`j92`) and
completed `OK`, not pending and without an extension fault (`j93`). Actual reported
stops matched 0, 0, 0. Credits became 1014; coin in became 550; coin out became 1565;
games played became 185 and games won became 26. Thus the one-credit wager and
two-credit award reconciled exactly.

The initial balance was restored by the ordinary award, not by a refund or rollback.
Both games remain in lifetime accounting. No other award class is qualified by this
single small-win test.

## Approved stock-equivalent profile test

The test profile is the host's pinned stock personality data encoded as a custom
profile: one line, maximum three credits per line, stock weights and eleven
stock payout records. No play or credit-transfer command was permitted during this
phase.

- Encoded size: 252 bytes.
- CRC32: `58E5D120`.
- Encoded SHA-256:
  `c402b2b7a10d3ccd0e69b5946198ab6e68e519357f34022e321f3eb7e1e4b477`.
- JSON SHA-256:
  `f48fe7b84a6cd54eadb2fa4d44ead96115dc779b3fa50bb87331ade691a9c8f0`.

Preflight `j94` confirmed stock generation 1, next ID 3, no fault/pending operation
and 1014 credits. Upload used BEGIN ID 3, chunk IDs 4–8 and COMMIT ID 9. The COMMIT
reply was missing (`j95`); the client correctly stopped with `UncertainOutcome`
and did not retransmit.

Read-only reconciliation `j96` found COMMIT ID 9 `OK`, active custom profile,
generation 2, expected CRC, no fault or pending operation, and unchanged credits.
`j97` downloaded the actual active profile, validated its framing/format/CRC and
compared all encoded bytes with the staged profile. It matched exactly.

After a user-controlled power cycle, `j98` retained ID 9, next ID 10, generation 2,
CRC and financial/game counters. `j99` downloaded the live profile again and
verified exact equality with both the staged bytes and pre-restart readback.
This qualifies persistence for this stock-equivalent profile over this idle restart;
no custom-profile gameplay or changed odds/paylines were exercised.

## Initial restore and missing replies

The approved restore to stock was sent once as ID 10 (`j100`). Its reply was also
missing, again without retransmission. Read-only reconciliation `j101` confirmed
RESTORE ID 10 `OK`, stock generation 3, next ID 11, no pending operation or extension
fault, and unchanged financial/game counters and credits.

Both state changes were verified, but neither initial profile-changing CLI call
completed normally end-to-end. Read-only reconciliation alone did not establish
the cause. The later separately approved probe below supplies timing evidence.

Host evidence: `tools/sas_protocol.py:Host.RESPONSE_TIMEOUT` allows 0.25 seconds for
the first reply byte. `tools/sas.py:SerialLine.read_message` waits for that first
byte, then a message gap. `NoResponse` means the host received no bytes in that
window; it does not prove that the firmware never emitted a later reply.
`tools/sas_extension.py:Extension.mutate` uses `retry=False`. Ordinary INFO/STATUS,
chunk requests and the two BET_SPIN acknowledgments worked. Those initial tests
captured no raw timing trace.

## Measured profile reply latency

The user separately approved one stock-equivalent upload followed by restore,
without spins or transfers. Diagnostic staging `j102` verified all nine pinned
host/profile files and opened no serial port. The single-threaded probe retained
the production serial receive/framing loop, recorded its real `os.read` chunks,
used a two-second first-byte observation window and disabled retransmission for
every command. Its source is preserved as
[historical probe text](firmware-reply-probe-source.txt), not a maintained CLI.
It uses the pre-fix host adapter interface and is not intended for direct reuse.

Full, unchanged JSON-lines outputs are preserved locally:

- `j103`: [upload trace](firmware-reply-probe-upload.jsonl), SHA-256
  `8703f9597220bc15508a5cb8a804f9c4dce7a10de4a1605c0292ece382efcd0b`.
- `j104`: [restore trace](firmware-reply-probe-restore.jsonl), SHA-256
  `f7c5006cef3a17813325c9f4368e774d46fc5f68dc452f8a70bb80d4e537f9a5`.
- Probe source SHA-256, also printed by `j102`:
  `d1577222397928a6bbb0d605ec6bfc88f85b051fffe6a455686fa05d1a0460a2`.

Upload used BEGIN ID 11, chunks 12–16 and COMMIT ID 17. Restore used ID 18.
Each mutation was sent once. Both operations returned matching, CRC-valid,
20-byte `OK` replies; upload also verified all 252 profile bytes.

| Operation | Request ID | First bytes after receive wait began | Last chunk |
| --- | ---: | ---: | ---: |
| COMMIT_PROFILE | 17 | 436.322878 ms | 462.230631 ms |
| RESTORE_STOCK | 18 | 320.326730 ms | 346.423004 ms |

These are host-observed receive latencies, not wire-level timestamps or bounds on
all future operations. Both first arrivals exceed the old 250 ms budget. This
reproduces the deadline mismatch while successfully receiving each late reply;
no firmware change or request replay was needed. It does not prove an exact
internal firmware execution duration.

The probe finished in stock generation 5, next ID 19, no fault/pending action,
1014 credits and unchanged financial/game counters. The host-only correction
keeps the ordinary SAS default and selects a named, per-call two-second first-byte
budget only for COMMIT_PROFILE and RESTORE_STOCK. Mutations still do not retry;
missing or invalid acknowledgments still mean an uncertain outcome. This probe
predates the revised production client; its later normal-CLI check is recorded
separately below.

### Host-only verification

The focused review approved the timeout policy, single-send failure semantics and
all 17 fixture exchanges against the raw traces. The captured exchange replay
fails with `NoResponse` at COMMIT/RESTORE when the per-operation policy is removed,
and succeeds with it; deliberately shorter waits remain uncertain after one send.
No test depends on wall-clock duration.

- Affected host/CPU run: 94 tests and 385 subtests passed, including both compiled
  host-to-firmware integrations.
- Final documented host-only command: 131 unittest tests passed; the separate
  pytest commissioning-wrapper selection passed three tests. The initial expanded
  command incorrectly included the pytest-only wrapper in unittest; the runner
  instructions were corrected, not the tests skipped.
- Ruff on touched host files (including the shared fake), shell syntax and diff
  whitespace checks passed. All 20 firmware inputs and eight published artifacts,
  exact chip lanes, boot checksum and unchanged original ROMs were reverified.

These offline checks preceded the normal-CLI live acceptance below. Runtime
firmware and chip images were not changed.

## Normal CLI acceptance after timeout correction

The user approved further checks. The first bounded step used the committed
production `tools/sas` wrapper and ordinary CLI, without the historical diagnostic
probe or a global timeout override. The profile was again stock-equivalent; no
spins, transfers, initialization, reflash, power interruption or fault injection
were performed.

[Acceptance evidence](firmware-cli-acceptance-evidence.json) preserves all 17 saved
JSON files and six exact job logs (`j108`–`j113`). These are semantic CLI results,
not a new serial timing capture. The tested host source is a commit in private
development history that is not in the public repository; the evidence records it as
`<private-commit>`, with the serial endpoint and job-log paths likewise replaced by
placeholders.

- `j108` confirmed the prior stock generation 5 / ID 18 / next ID 19 baseline,
  1014 credits and unchanged meters before any profile mutation. The locally
  generated profile matched the earlier encoded SHA-256 and CRC.
- `j109` stopped before invoking upload: the source-set check found one extra
  remote `tools/sas_meters.py`. Every current source file matched. The extra file
  exactly matched an obsolete module deleted earlier in private development history;
  no current tool imported it. `j110` removed only that hash-verified obsolete source, without
  opening serial. `j111` then verified all 11 current remote files, the 250 ms
  ordinary default and the two 2 s per-operation budgets. This was deployment
  residue, not a failed machine command or a timeout-fix defect.
- `j112` invoked normal `profile-upload` once. It completed normally with COMMIT
  ID 25 `OK`, generation 6 and CRC `58E5D120`. A separate normal `profile-download`
  matched all 252 encoded bytes and SHA-256
  `c402b2b7a10d3ccd0e69b5946198ab6e68e519357f34022e321f3eb7e1e4b477`.
  INFO/STATUS, credits and all eight native meters were checked afterward.
- `j113` invoked normal `profile-restore` once. It completed normally with ID 26
  `OK`, stock generation 7, no fault/pending action and next ID 27. Credits
  remained 1014; all eight native meters exactly matched the preflight snapshot.

This closes the reproduced profile-reply deadline defect for the observed normal
CLI round-trip. It is not a universal two-second timing guarantee, a long-duration
stress test or qualification of changed profiles/gameplay. No firmware or chip
change was needed.

## Remaining scope

The limited live checks above do not establish SRAM write atomicity, power-fail
write inhibition/timing, long-duration retention, all native game paths,
progressive/handpay behavior, standalone reel diagnostics, or physical fatal-halt
and recovery behavior. See [provisioning](../firmware/PROVISIONING.md) and
[settlement guard](../firmware/SETTLEMENT-GUARD.md) for the original premises.
The offline-era statements in those documents predate this live record; they are
not evidence that these later tests were never performed.

Offline and bounded live host-policy verification are complete. The user reports
that measurement equipment is unavailable and requests proceeding without it.
Instrumented electrical/timing checks therefore remain unverified, not passed.
Use the limited serial-functional evidence above without claiming full hardware
qualification: `install_ready` remains `false`, and standalone reel positioning
and jogging remain disabled. No live probing, power interruption, fault injection
or further game/money action is underway; no reflash is required for the host-only
correction.
