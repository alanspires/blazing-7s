---
paths:
  - "tools/web_*.py"
  - "tools/web_control.service"
  - "tools/web/**"
  - "tools/test_web_*.py"
---

# Web control page

The page's server (`web_control`) owns the machine's SAS line and serves every control to
anyone on the network. Machine operations themselves live in `sas_machine` and
`sas_extension` (`sas-host.md`, `sas-host-machine-tests.md`, `firmware-remote-control.md`);
this slice owns how the page schedules, admits, journals and reconciles them.

## One owner of the line

- Only `web_worker`'s thread touches `Line`, `Host` and `Extension`; `Host` is a lock-free
  single-caller state machine. HTTP threads use `snapshot()`, `subscribe()` and `submit()`
  only, and read immutable `MachineState` snapshots. Publishing never waits on a subscriber.
- `PacedLine` spaces every actual send `POLL_INTERVAL` after the previous one, the host's
  own syncs, repeats and busy retries included. A general poll falls due
  `GENERAL_POLL_GAP` after the previous general poll, answered or not (counting only
  answered ones would let a silent machine stall every long poll). Due polls run in
  `pace()`, before a host call and never inside one, so a retransmission sequence is never
  split.
- The page goes offline only after one unbroken run of unanswered general polls lasting
  `OFFLINE_AFTER` with no validated reply of any kind inside it (general poll, long-poll
  data reply or ACK): a single miss after a long read never counts. An exception 17, an
  offline mark or a reopen during a full refresh makes it run again before the snapshot
  is fresh.
- Only `LineLost` (an `OSError` from the line, raised through `PacedLine`, or the device
  failing to open) closes and reopens the handle. Any other `OSError` outside the journal
  paths ends the worker, so `web_control` exits non-zero; a missing personality ROM fails
  at `Worker` construction.
- While the page runs it owns the port: nothing else may hold it, and `tools/sas` must not
  run against the machine.

## Intent before send, reconcile, never resend

- The operation's `record(intent)` callback is the only place an intent is appended and
  the last step before a send, after any yield: it re-runs `web_state.admit` and the
  page's expected generation, bet and arm against the latest state, then appends and
  fsyncs the intent with the submission ID. A raise there means nothing was sent. An
  upload's later steps are not re-admitted; `record` checks that this upload may continue
  (link fresh, active profile identity still the recorded `previous`, no door, power or
  unexplained ACCEPTED evidence) and stops it otherwise.
- Only a failed intent append proves nothing was sent. A failed step, outcome or spin
  append comes after a possible send: the intent stays open, its family is held as
  checking, and reconciliation resolves it before the hold clears. Never report "nothing
  sent" or a finished result without durable evidence.
- A lost reply holds the action's family as checking and runs its reconciler (money:
  `reconcile_transfer`; extension: `reconcile_extension`; play: the 6E probe); the result
  is journaled and shown. A dropped HTTP reply never resends: the submission ID is the
  handle for the result in the snapshot's `results` ring and `GET /state.json`.
- Actions that could erase another's reconciliation evidence are serialized: no money
  action while a money intent is open; no extension mutation while an extension intent is
  open or STATUS shows ACCEPTED, except the firmware's own handoff for a correlated pending
  arm (exactly CANCEL_OUTCOME and the BET_SPIN bound to it).
- A lost restore reply never closes `happened`: `restored` only on the
  acknowledged restore's STATUS proof (`sas_extension.stock_restored`: matching OK, stock
  active, no fault); a matching INTERRUPTED or FAULT closes with that word and no profile
  record; still ACCEPTED holds the family checking, with STATUS's fault flag shown on the
  checking notice and banner (`data-fault`) and in `/state.json` (`extension_fault`), never
  relabelled terminal; a matching OK that STATUS contradicts is held `inconsistent`.
- A restore the machine answers with a non-OK status closes `refused` with that status on
  its result (`machine_status`) and has STATUS read again. Only a status the firmware
  answers before any side effect (`web_state.UNCHANGED_REFUSALS`) may be told as "nothing
  changed"; FAULT can follow a partial profile transition, so the page claims neither
  restored nor unchanged and lets STATUS show the active game.
- Terminal restore feedback on Odds (stopped, or refused) is shown whatever the odds draft
  holds, valid or not, with the draft kept and its note beside it.
- Every verified profile change (apply `committed_verified`, restore `restored`,
  acknowledged or reconciled) closes through one worker finalizer: the `profile` record
  naming its intent is journaled before the outcome, the journal folds it onto the open
  intent, and the outcome's result carries the committed `generation`.
- An `inconsistent` money, upload or restore result keeps the intent open and its family
  held by design: no page control releases it. A restart, or a reconnect, re-runs
  reconciliation from the journal; anything further is a person reading the machine and
  the journal, never an automatic send. An upload `pending` on a possible COMMIT holds
  every profile mutation and re-checks at each periodic refresh and reconnect; one with no
  COMMIT step record closes as `staging_abandoned` without a send.
- At start-up and after every reconnect, a full refresh and reconciliation of every open
  intent run before the snapshot is fresh and actions are admitted.

## Admission

- `web_state.admit` is the one admission function: views render its refusals as real
  `disabled` controls with the reason, `submit` refuses with it, and the worker re-runs it
  against fresh reads before sending. Never add a second check that can disagree.
- Admission comes from machine-reported state: AFT availability per direction, the
  transfer limit converted exactly from cents, doors (unknown until a baseline), play
  enabled and the extension's capabilities. Display state never authorizes a write.
- A spin or arm carries the profile generation, per-line bet and arm identity the page
  rendered; any difference refuses it as out of date without sending. Bet one only changes
  the server's shared chosen bet and never depends on credits; Max bet sets the chosen bet
  to `max_bet_per_line` and spins at that bet, admitted like a spin at `max_bet_per_line`.
- An odds draft is admitted for the generation its form carries, in the preview, the
  no-script Preview (GET `/odds`, `View.generation`) and the apply POST, never the
  snapshot's; a draft with no generation is admitted for none. `live.js` rebases its editor
  in place only onto its own apply verified at the active generation (`data-applied`),
  keeping every field node and the focused or post-apply-edited values. Its max bet per line is always the active profile's:
  the editor shows it and has no field for it.
- A refusal by the worker after HTTP admission reaches people only through the `results`
  ring. The page that submitted it learns it by submission ID: the status region lists every
  refused result in the ring (`data-refused`), and `live.js` flashes, once and with its code,
  the refusal of a submission its own 202 answer named, unless a region shows it already.
  Without JavaScript the views show the newest refused result with its code where the
  action lives (Play banner, Odds status, Settings row).
- Refusals answer 503 for `Code.OFFLINE` and `Code.STALE` and 409 otherwise;
  `Code.JOURNAL` and `Code.INVALID_PROFILE` come only from the worker.

## Next-result lifecycle

- Armed only when STATUS shows ARM_OUTCOME ACCEPTED with the journaled arm's id; the
  journal names it, STATUS decides it. STATUS never reads back stops.
- A bound spin ending INTERRUPTED or FAULT leaves the arm `held` (the firmware keeps it
  until CANCEL). A refused bound spin, an ACCEPTED arm no journaled intent explains, or a
  restart STATUS cannot confirm leaves it `unknown`, and it stays unknown until STATUS
  shows it consumed, cleared or cancelled.
- Held and unknown offer only Cancel: spin, arm and profile changes wait for it.

## Settings without read-back

- Sound and bills have no SAS read-back; only an ACK attributable to the command confirms
  them. They are sent once with `retry=False`. A lost
  reply makes the setting `unknown`, which survives restart through the journal; nothing
  resends on its own, and a person's next tap sends a fresh command for the target chosen
  (unknown settings show both On and Off).
- Before a fresh command after `unknown`, the worker completes `sas_machine.ack_boundary`
  (a validated 1A data reply a bare byte cannot satisfy); only the fresh command's own
  immediate ACK after that boundary confirms it. With no attributable boundary reply on a
  fresh link the tap sends once and stays `unknown`; a lost or stale link during the
  boundary refuses the tap without sending.
- On an empty journal both start `unknown`: the page has never seen them set.

## Edge, configuration and privacy

- No logins, roles or confirmations. Safety at the edge is same-origin only: the
  `Host` header must be the `--listen` address or an `--allow-host` name at the bound port,
  and a POST's `Origin` (else `Referer`) must match it. Bodies, handler threads, event
  streams and the queue are bounded, and every connection has a socket deadline.
- `--port`, `--listen` and `--journal` are required arguments with no defaults and no
  config file; the unit template carries placeholders only. Never add a default host,
  port, device or path.
- Journal intent and step records carry `request_hex`, which includes the AFT registration
  key: they never reach `MachineState.log`, Activity (`web_state.ACTIVITY_KINDS`) or any
  view, and the journal file is created mode 0600.
- A final journal line without its newline is an append that never returned and is
  truncated on open; any other unreadable line refuses to start.
- `read_state` reads the extension's INFO and STATUS: the page needs the extension chips
  to ever have a fresh snapshot.
- The live check (`web_control_live_check`) moves money and needs the owner's approval per
  run. Its output and any evidence keep allowlisted semantic fields only: never the URL,
  raw HTTP or serial bytes, exception text, environment, host, user, device or path.

## Verification

- Stdlib suites (`global.md` list): `test_sas_machine`, `test_web_journal`,
  `test_web_state`, `test_web_worker`, `test_web_views`, `test_web_app` and
  `test_web_control_live_check`, on the combined fake machine (`sas_testing.FakeS6000`)
  with fake clocks; no elapsed-time assertions. Keep lost-reply coverage at every money,
  spin, arm, cancel, upload and restore step, journal failures before and after a send,
  and restart reconciliation.
- `test_web_browser.py` (pytest-playwright, Chromium) covers live updates across pages,
  one-tap actions, lost-reply and offline disablement, a silent stream showing unreachable,
  an edited draft surviving a live update, the editor rebased in place after its own apply
  (a focused, partly selected field keeping its node, value and selection), a queued spin's
  refusal shown to the page that sent it behind a newer result, and phone Play without
  scrolling.
- Validate a placeholder-substituted copy of the unit with `systemd-analyze verify`
  outside the repository.
