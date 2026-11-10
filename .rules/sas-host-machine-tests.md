---
paths:
  - "tools/fixtures/s6000_money_*"
  - "tools/fixtures/s6000_write_test*"
  - "tools/sas_write_test.py"
  - "tools/sas_money_test.py"
  - "tools/test_sas_write_test.py"
  - "tools/test_sas_money_test.py"
---

# SAS host machine tests

Read `sas-host.md` for protocol, CLI and shared test infrastructure; `global.md`
for live-machine authorization. These routines have different recovery contracts.

## Money safety

- `MoneyTest.round_trip` is a full-balance test: legacy bonus, AFT deposit,
  withdrawal of ALL cashable credits, then restoration of the starting balance.
  The captured S6000 does not support partial withdrawals. Restoring credits
  does not undo lifetime accounting; never describe this as a reversible test.
- Preflight must reject disabled play, locks, unavailable in-house transfers,
  restricted/promotional balances, pending transfers or receipts, inconsistent
  assets or credit readings, and a full balance exceeding the transfer limit.
  Convert credits using the reported whole-cent denomination, not a fixed dollar.
- Money requests are sent once (`retry=False`), including restoration. Record
  each AFT transaction ID, direction, amount and asset BEFORE sending. Use a new
  ID per transfer and retain it for recovery.
- Pending status permits bounded interrogation, never resending the initiating
  request. Check identity, asset, type and all amounts on every response;
  completion must have no pending receipt. A different read acknowledges the
  final status; exception `69` is not required when status was retrieved first.
- ACK alone does not establish payment: verify legacy bonus event, last bonus,
  bonus-meter delta and credits; verify AFT credit, amount and count deltas.
- Any uncertainty, unexpected meter change, refusal, interruption or exhausted
  pending poll budget stops further money writes. Do not add a `finally` refund
  or reuse the non-money restoration helper. Failure can leave zero credits or
  extra test credits, even when the last initiating request took effect.
- Never rerun after failure as recovery. Preserve output and reconcile the last
  intent with credits, extended credit/in-house meters, legacy bonus meters and
  `aft-transfer 0xFE 0` before any new money movement. `FE` observes without
  completing the exchange; `FF` retrieves completion for acknowledgment.
- Outgoing money flags stay zero: no receipts, tickets, hopper cashout, forced
  handpay or lock request. Never introduce restricted/promotional transfers.
  Registration stays unchanged; success verifies restored credits, enabled play
  and an unlocked machine.

## Non-money writes are not a complete undo

- `WriteTest` attempts per-step restoration even after protocol/check failures;
  one failed restore must not prevent other non-money cleanup attempts.
- Final play/AFT status/registration are compared with the initial snapshot;
  clock restoration includes elapsed time. Sound and all bills end enabled,
  game delay zero and real-time events off, not at unknown original values.
  Sound/bills/delay have only ACK verification, not setting read-back.
- Do not attempt shutdown unless the disable-game step demonstrated the `6E`
  invalid-component probe. Startup sent before shutdown reaches its disabled
  state can be ignored; preserve bounded startup/probe recovery despite silence.
- Clock writes are intentionally silent on this firmware; verify via read-back.
  Unregistering retains key/POS ID: restore their saved values before cancelling
  registration again. Do not overwrite an already registered machine in this step.
- Keep status-only probes empty/zero and auto-rebet OFF. They are not all reads:
  validation status clears the validation-ID flag and posts `3F`; ticket-data
  probes clear received flags. Do not expand this test to redemption, progressive
  jackpot setters, multiplied jackpots or tournament entry/exit.

## Verification obligations

- Preserve fake-machine coverage for lost replies at every money-write stage,
  interrupted withdrawal with logged intent, pending/mismatched transfers,
  unexpected credits, and immediate/last-poll completion without duplicate writes.
- Non-money failure tests must still prove startup, bills-on and delay-zero
  cleanup attempts, and suppression of shutdown when the probe cannot verify it.
- Captured fixtures are historical evidence, not current-state guarantees or
  authority to repeat live writes. See `notes/sas-j10.md`, **Write test** and
  **Money test**, for firmware rationale and recovery evidence.
