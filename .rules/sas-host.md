---
paths:
  - "tools/sas"
  - "tools/sas.py"
  - "tools/serial_*.py"
  - "tools/sas_protocol.py"
  - "tools/sas_commands.py"
  - "tools/sas_client.py"
  - "tools/sas_tables.py"
  - "tools/sas_testing.py"
  - "tools/sas_write_test.py"
  - "tools/sas_money_test.py"
  - "tools/test_sas_protocol.py"
  - "tools/test_sas_commands.py"
  - "tools/test_sas_cli.py"
  - "tools/test_sas_write_test.py"
  - "tools/test_sas_money_test.py"
  - "tools/fixtures/s6000_dump.jsonl"
  - "tools/fixtures/s6000_rom_signatures.json"
  - "tools/fixtures/s6000_money_*"
  - "tools/fixtures/s6000_write_test*"
  - "vendor-docs/sas/**"
  - "notes/sas-j10.md"
---

# Stock SAS host

## Ownership and transport boundary

- This slice owns stock J10 SAS transport, command codecs and captured-reply verification.
  Live write/money flows belong to `sas-host-machine-tests.md`; private extension/profile
  semantics to `firmware-remote-control.md`; pre-boot commissioning to
  `firmware-commissioning.md`; J3 to `j3-hhu.md`.
- `tools/sas` (SSH wrapper) and `tools/sas.py` (CLI and `SerialLine`) also dispatch
  extension and commissioning commands; those semantics stay with their firmware rules.
  The wrapper has no host defaults: online commands require `SAS_HOST` and `SAS_PORT`
  and exit before any SSH/rsync call without them (`SAS_DIR` defaults to `blazing-7s`);
  `list`, `help`, `profile-stock` and `profile-preview` stay local. `sas.py` and
  `sas_commission.py` require `--port` for serial commands. Never reintroduce a
  default host, port or path.
  Run serial I/O on the Linux adapter host, not through a network-forwarded serial stream: SAS permits only 5 ms between bytes.
  Linux mark/space parity supplies the ninth wakeup bit; macOS does not supply CMSPAR.
- Consult `notes/sas-j10.md` **The port** and **Steps** before wiring. The measured board
  is RS-232, not TTL; board revisions differ. Keep TITO@Home disconnected while the PC is
  the host, power off before harness changes, and keep clear of J10's +12 V pin.
  `serial_listen` is receive-only; `serial_txd_toggle` actively drives TXD.
- `SerialLine.send` changes parity after the wakeup byte. FTDI `tcdrain`/flush alone does
  not prove that byte has left the adapter; changing parity too early corrupts wakeup.
  Any replacement for the existing frame-delay workaround needs adapter-level evidence,
  not just scripted-line tests. Receive framing must tolerate USB latency as well as
  machine inter-byte gaps.

## Poll state is part of correctness

- Preserve `Host.send`'s implied ACK/NACK state machine. Sync (`80`) precedes the first
  poll, an answered identical poll, and an identical poll that exhausted `POLL_TRIES`.
  Repeating an unanswered poll requests retransmission; inserting an ACK there loses
  that meaning. The S6000 also needs the sync response window before the next poll.
- A bare NACK is an answer, not a retryable missing reply. Missing, corrupt and busy
  replies use the bounded retry path. `retry=False` must send only once even when a
  real-time event replaces the reply; callers must reconcile an uncertain write by reads.
- Real-time events can replace long-poll replies. Keep them in `Host.events`, acknowledge
  the event before retrying, and do not expect a later general poll to return it again.
  A one-byte general-poll reply is an exception even when its value resembles an address.
- Per-call receive timeouts apply to every attempt of that call without mutating the
  host default. Keep this behavior covered for clients sharing the stock transport.
- CRC is CRC-16/KERMIT, appended low byte first. Type R requests omit CRC; data replies
  still carry it. A bare ACK is distinct from a data reply.

## Command safety and decoding

- The stock command set and fixed request lengths must match the main ROM's
  `sas_rx_byte` table, as checked by `TableTest`. Do not add private firmware commands
  to this registry. Layout provenance is SAS 6.02 or, for removed EFT commands,
  `vendor-docs/sas/sastest-simulator-2021/sastest.ini`; a layout is not proof of live support.
- `Command.read` is a safety allowlist of exact arguments, not a synonym for type R.
  `dump` must use only those values. Replies to `pending-cashout`, `ticket-data` and
  `sas-progressive-win` can start exchanges or consume queued state; keep them excluded.
  Validation/ticket setters are not harmless reads merely because their input is empty.
  AFT dump interrogation uses `FE`, not completion-retrieving `FF`.
- Preserve BCD/digit strings versus little-endian binary. Dates and validation numbers
  need leading zeroes. Selected-meter sizes come from Table C-7; extended meters carry
  their own lengths. Never assume all meters use the same size or accounting unit.
- Variable-length requests may omit trailing fields; fixed requests must be complete.
  Decoding intentionally preserves unknown/trailing bytes as `extra` and invalid BCD as
  hex, and accepts replies ending before optional fields. Decoding success alone does
  not establish that a higher-level operation returned all required evidence.
- `sas_client.poll` retains raw reply data and typed errors for diagnostics. Silence
  can mean an unavailable feature; `dump` succeeds if any command answers, not only if
  every command succeeds. Do not interpret its exit status as full-machine qualification.

## Signature and offline evidence

- ROM signature requests ACK first; their result arrives on a later general poll.
  The stock machine can stop answering while computing. Keep bounded polling that
  tolerates this silence, rather than treating it as an ordinary immediate reply.
  CRC chaining follows the supplied image order; matching seeded CRCs is not proof of
  byte identity or independent integrity checks. See **ROM signature** in the J10 notes
  for observed stalls and events; do not infer their cause.
- For stock protocol/codec changes, run `test_sas_protocol`, `test_sas_commands` and
  `test_sas_cli` using the host unittest environment in `global.md`; include affected
  machine-flow tests when shared helpers change. Use injected `Line`/`FakeLine`, not
  live hardware or elapsed-time assertions.
- Keep ROM-table checks, every-command CLI round trips, captured-reply decoding,
  cross-poll meter agreement and captured ROM signatures intact. Captures are historical
  evidence, not defaults for current machine state; changed layouts must explain any
  undecoded bytes rather than silently normalizing fixtures to the new implementation.
