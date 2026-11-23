"""web_worker on the combined fake machine with a fake clock, driven step by step without HTTP:
pacing, admission at the send, lost replies, journal failures, settings, uploads and the link."""

from __future__ import annotations

import errno
import os
import unittest
from collections.abc import Callable
from dataclasses import replace
from types import MappingProxyType
from unittest import mock

import sas_commands as sc
import sas_extension as se
import sas_machine as sm
import sas_profile as sp
import sas_protocol as protocol
import web_state as ws
import web_worker as ww
from sas_testing import (
    ARM,
    BEGIN,
    BUSY,
    CHUNK,
    COMMIT,
    EXTENSION_POLL,
    INFO,
    INTERRUPTED,
    READ,
    RESTORE,
    SLOT_DOOR,
    SPIN,
    STATUS,
    FakeClock,
    FakeS6000,
    TempDirTest,
    sent_requests,
)
from web_journal import Journal
from web_state import (
    Act,
    Action,
    ArmState,
    Code,
    Family,
    Link,
    OutcomeWord,
    ResultState,
    Setting,
)

ADDRESS = FakeS6000.ADDRESS
CPC = FakeS6000.CENTS_PER_CREDIT
GENERAL_POLL = ADDRESS | protocol.GENERAL_POLL_FLAG
EXTENSION_HEADER = 16  # E0, length, magic, version, op, id: the payload follows
STEP_LIMIT = 20000
FINISHED = (ResultState.DONE, ResultState.REFUSED)
HANDPAY = 0x51


class Crash(BaseException):
    """The server process dying at this point."""


class Machine(FakeS6000):
    """FakeS6000 with hooks keyed like its `lose`: an extension op, or a stock command name.
    `before(key, rest)` and `after(key, rest)` run around each long poll the machine gets;
    `lose_next(pred)` loses the reply to the next long poll pred picks, which still acts;
    `drop_general` leaves that many general polls unanswered."""

    def __init__(self, **options):
        super().__init__(**options)
        self.before: Callable[[object, bytes], None] | None = None
        self.after: Callable[[object, bytes], None] | None = None
        self._lose_next: list[Callable[[object, bytes], bool]] = []
        self.last_answer: float | None = None  # clock time of the last send that got a reply
        self.drop_general = 0

    def _reply(self, message: bytes) -> None:
        super()._reply(message)
        if message:
            self.last_answer = self.clock()

    def lose_next(self, pred: Callable[[object, bytes], bool]) -> None:
        self._lose_next.append(pred)

    def send(self, first, rest):
        key = request_key(first, rest)
        if key is None:
            dropped = first == GENERAL_POLL and self.drop_general > 0 and not self.silent
            if dropped:
                self.drop_general -= 1
                self.silent = True
            try:
                super().send(first, rest)
            finally:
                if dropped:
                    self.silent = False
            return
        if self.before is not None:
            self.before(key, rest)
        chosen = next((pred for pred in self._lose_next if pred(key, rest)), None)
        if chosen is not None:
            self._lose_next.remove(chosen)
            self.lose = self.lose | {key}
        try:
            super().send(first, rest)
        finally:
            if chosen is not None:
                self.lose = self.lose - {key}
        if self.after is not None:
            self.after(key, rest)


def request_key(first: int, rest: bytes) -> object:
    if first == protocol.SYNC_POLL or not rest:
        return None
    if rest[0] == EXTENSION_POLL:
        return rest[7]
    return sc.COMMANDS[rest[0]].name


def initiating_transfer(key: object, rest: bytes) -> bool:
    return key == "aft-transfer" and rest[2] == sm.FULL_TRANSFER


def last_chunk(key: object, rest: bytes) -> bool:
    if key != CHUNK:
        return False
    payload = rest[EXTENSION_HEADER:-2]
    return int.from_bytes(payload[:2], "little") + len(payload) - 2 == sp.PROFILE_SIZE


class Disk:
    """os.write for the journal, failing the lines `fail` picks (every one, or once)."""

    def __init__(self):
        self.fail: Callable[[bytes], bool] | None = None
        self.once = False

    def write(self, fd: int, data) -> int:
        if self.fail is not None and self.fail(bytes(data)):
            if self.once:
                self.fail = None
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        return os.write(fd, data)


def gaps(times: list[float]) -> list[float]:
    return [b - a for a, b in zip(times, times[1:])]  # noqa: RUF007 -- itertools.pairwise needs 3.10; the suites run on 3.9


def kind(name: str) -> Callable[[bytes], bool]:
    return lambda line: f'"kind":"{name}"'.encode() in line


def variant() -> sp.Profile:
    """A valid profile other than stock."""
    stock = sp.stock_profile()
    weights = [list(row) for row in stock.weights]
    weights[1][2] = weights[1][2] + 1 if weights[1][2] < sp.BYTE_MAX else 1
    return replace(stock, weights=tuple(tuple(row) for row in weights))


class Rig:
    """A worker on `machine` with a journal at `path`; the fake clock advances only when the
    machine waits or reads silence, or when the worker has nothing due."""

    def __init__(self, test: unittest.TestCase, path, machine: Machine | None = None):
        self.test = test
        self.path = path
        self.machine = machine or Machine(clock=FakeClock())
        self.clock = self.machine.clock
        self.disk = Disk()
        self.journal = Journal.open(str(path), write=self.disk.write)
        test.addCleanup(self.journal.close)
        self.unplugged = False
        self.opens = 0
        self.worker = ww.Worker(self.open_line, self.journal, clock=self.clock, address=ADDRESS)

    def open_line(self) -> Machine:
        if self.unplugged:
            raise OSError(errno.ENODEV, os.strerror(errno.ENODEV))
        self.opens += 1
        return self.machine

    @property
    def state(self) -> ws.MachineState:
        return self.worker.snapshot()

    def drive(self, until: Callable[[], bool], check: Callable[[], None] | None = None) -> None:
        for _ in range(STEP_LIMIT):
            if check is not None:
                check()
            if until():
                return
            delay = self.worker.step()
            if delay > 0:
                self.clock.advance(delay)
        raise AssertionError("the worker never got there")

    def run_for(self, seconds: float) -> None:
        end = self.clock() + seconds
        self.drive(lambda: self.clock() >= end)

    def start(self) -> None:
        self.drive(lambda: self.state.fresh)

    def action(self, name: Act, sid: str, **params) -> Action:
        """name with the expected values a page rendered from the current snapshot."""
        state = self.state
        return Action(name, sid, MappingProxyType(params),
                      profile_generation=None if state.profile is None else state.profile.generation,
                      bet_per_line=ws.chosen_bet(state), arm_id=ws.arm_identity(state))

    def submit(self, name: Act, sid: str, **params):
        return self.worker.submit(self.action(name, sid, **params))

    def result(self, sid: str) -> ws.ActionResult:
        return next(r for r in self.state.results if r.submission_id == sid)

    def finish(self, sid: str, states=FINISHED) -> ws.ActionResult:
        self.drive(lambda: self.result(sid).state in states)
        return self.result(sid)

    def act(self, name: Act, sid: str, **params) -> ws.ActionResult:
        self.assertSubmitted(self.submit(name, sid, **params))
        return self.finish(sid)

    def assertSubmitted(self, answer) -> None:
        self.test.assertIsInstance(answer, ww.Submitted)

    def mutations(self, op: int) -> int:
        return [request[0] for request in self.machine.extension.mutations()].count(op)

    def transfers(self) -> list[dict]:
        return [r for r in sent_requests(self.machine, "aft-transfer") if r["transfer_code"] == sm.FULL_TRANSFER]

    def records(self, record_kind: str | None = None) -> list[dict]:
        """Journal records, oldest first."""
        found = self.journal.entries(limit=100000, kinds=None if record_kind is None else (record_kind,))
        return found[::-1]

    def in_progress(self, name: Act) -> bool:
        running = self.state.in_progress
        return running is not None and running.name is name


class WorkerTest(TempDirTest):
    def rig(self, machine: Machine | None = None, name: str = "journal") -> Rig:
        return Rig(self, self.dir / name, machine)

    def restart(self, rig: Rig) -> Rig:
        """The server restarted: the same machine, a new worker on the reopened journal."""
        rig.journal.close()
        return Rig(self, rig.path, rig.machine)


class SurfaceTest(WorkerTest):
    def test_only_the_worker_step_touches_the_line(self):
        rig = self.rig()
        self.assertEqual(rig.machine.sent, [])
        rig.start()
        sent = len(rig.machine.sent)
        subscription = rig.worker.subscribe()
        answer = rig.submit(Act.ADD_CREDITS, "top-up", amount=1)
        rig.worker.snapshot()
        self.assertIsInstance(answer, ww.Submitted)
        self.assertEqual(rig.result("top-up").state, ResultState.QUEUED)
        self.assertEqual(len(rig.machine.sent), sent)
        self.assertIsNotNone(subscription.wait(0))
        self.assertEqual(rig.finish("top-up").outcome, OutcomeWord.MOVED)
        self.assertGreater(len(rig.machine.sent), sent)

    def test_a_subscriber_that_never_reads_does_not_hold_the_worker_back(self):
        rig = self.rig()
        idle = rig.worker.subscribe()
        rig.start()
        rig.act(Act.ADD_CREDITS, "top-up", amount=2)
        latest = idle.wait(0)
        self.assertEqual(latest, rig.state)  # the newest snapshot, not a backlog
        self.assertIsNone(idle.wait(0))

    def test_a_full_queue_and_an_inadmissible_action_are_refused_at_once_into_the_results(self):
        rig = self.rig()
        rig.start()
        for n in range(ww.QUEUE_LIMIT):
            rig.assertSubmitted(rig.submit(Act.ADD_CREDITS, f"top-up-{n}", amount=1))
        full = rig.submit(Act.ADD_CREDITS, "one-too-many", amount=1)
        self.assertEqual((full.code, rig.result("one-too-many").state), (Code.BUSY, ResultState.REFUSED))
        over = rig.submit(Act.ADD_CREDITS, "over", amount=rig.state.aft.limit_credits + 1)
        self.assertEqual(over.code, Code.OVER_LIMIT)

    def test_bet_one_and_max_bet_change_only_the_chosen_bet(self):
        rig = self.rig()
        rig.start()
        sent = len(rig.machine.sent)
        top = rig.state.profile.max_bet_per_line
        rig.assertSubmitted(rig.submit(Act.BET_ONE, "bet"))
        self.assertEqual(ws.chosen_bet(rig.state), top % top + 1)
        rig.assertSubmitted(rig.submit(Act.MAX_BET, "max"))
        self.assertEqual(ws.chosen_bet(rig.state), top)
        self.assertEqual(len(rig.machine.sent), sent)


class PacingTest(WorkerTest):
    def general_poll_gaps(self, machine: Machine, since: int = 0) -> list[float]:
        times = [t for t, (first, rest) in zip(machine.send_times, machine.sent)
                 if first == GENERAL_POLL and not rest][since:]
        return gaps(times)

    def test_sends_are_spaced_and_general_polls_keep_their_gap_under_a_backlog(self):
        rig = self.rig()
        rig.start()
        rig.machine.busy_replies = 2  # the first long poll of the backlog is answered busy twice
        names = [(Act.ADD_CREDITS, {"amount": 3}), (Act.APPLY_PROFILE, {"profile": variant()}),
                 (Act.RESTORE_STOCK, {}), (Act.CASH_OUT, {})]
        for n, (name, params) in enumerate(names):
            rig.assertSubmitted(rig.submit(name, f"action-{n}", **params))
        rig.drive(lambda: all(rig.result(f"action-{n}").state in FINISHED for n in range(len(names))))
        self.assertEqual([rig.result(f"action-{n}").state for n in range(len(names))],
                         [ResultState.DONE] * len(names))
        self.assertEqual(rig.machine.busy_replies, 0)
        self.assertEqual(rig.mutations(COMMIT), 1)
        self.assertGreaterEqual(min(gaps(rig.machine.send_times)), protocol.POLL_INTERVAL - 1e-9)
        poll_gaps = self.general_poll_gaps(rig.machine)
        self.assertGreater(len(poll_gaps), 10)
        self.assertLessEqual(max(poll_gaps), ww.MAX_GENERAL_POLL_GAP)

    def test_a_general_poll_due_during_restores_info_preflight_runs_before_restore_stock(self):
        rig = self.rig()
        rig.start()

        def deadline_during_info(key, rest):
            if key == INFO and rig.in_progress(Act.RESTORE_STOCK):
                rig.clock.advance(ww.GENERAL_POLL_GAP)

        rig.machine.after = deadline_during_info
        self.assertEqual(rig.act(Act.RESTORE_STOCK, "restore").outcome, OutcomeWord.RESTORED)
        keys = [request_key(first, rest) if rest else first for first, rest in rig.machine.sent]
        restore_at = keys.index(RESTORE)
        info_at = max(i for i, key in enumerate(keys[:restore_at]) if key == INFO)
        self.assertIn(GENERAL_POLL, keys[info_at:restore_at])
        self.assertLessEqual(max(self.general_poll_gaps(rig.machine)), ww.MAX_GENERAL_POLL_GAP)


class AdmissionAtTheSendTest(WorkerTest):
    def test_a_spin_rendered_at_another_bet_is_refused_by_the_worker_without_sending(self):
        rig = self.rig()
        rig.start()
        rig.assertSubmitted(rig.submit(Act.SPIN, "spin"))
        rig.assertSubmitted(rig.submit(Act.BET_ONE, "other-page"))  # after the spin was queued
        result = rig.finish("spin")
        self.assertEqual((result.state, result.code), (ResultState.REFUSED, Code.OUT_OF_DATE))
        self.assertEqual(rig.mutations(SPIN), 0)

    def test_a_door_reported_in_the_yield_before_the_send_stops_the_spin(self):
        rig = self.rig()
        rig.start()

        def door_opens(key, rest):
            if key == INFO and rig.in_progress(Act.SPIN):
                rig.machine.open_door(SLOT_DOOR)
                rig.clock.advance(ww.GENERAL_POLL_GAP)  # a general poll is due at the next yield

        rig.machine.after = door_opens
        result = rig.act(Act.SPIN, "spin")
        self.assertEqual((result.state, result.code), (ResultState.REFUSED, Code.DOOR_OPEN))
        self.assertEqual(rig.mutations(SPIN), 0)
        self.assertEqual([r for r in rig.records("intent") if r["submission_id"] == "spin"], [])

    def test_doors_start_unknown_and_a_door_opened_blocks_spin_with_the_door(self):
        rig = self.rig()
        rig.start()
        self.assertIsNone(rig.state.doors)
        self.assertIsNone(ws.admit(rig.state, rig.action(Act.SPIN, "probe")))
        rig.machine.open_door(SLOT_DOOR)
        rig.drive(lambda: ws.open_door(rig.state) is not None)
        self.assertEqual(ws.admit(rig.state, rig.action(Act.SPIN, "probe")).code, Code.DOOR_OPEN)
        self.assertEqual(rig.submit(Act.SPIN, "spin").code, Code.DOOR_OPEN)
        self.assertIn("door_open", [r["event"] for r in rig.records("event")])
        rig.machine.close_door(SLOT_DOOR)
        rig.drive(lambda: ws.open_door(rig.state) is None)
        self.assertIsNone(ws.admit(rig.state, rig.action(Act.SPIN, "probe")))

    def test_deposit_only_availability_refuses_cash_out_and_takes_credits(self):
        machine = Machine(clock=FakeClock())
        machine.available_transfers = sm.TO_MACHINE
        rig = self.rig(machine)
        rig.start()
        self.assertEqual(rig.submit(Act.CASH_OUT, "cash-out").code, Code.AFT_UNAVAILABLE)
        result = rig.act(Act.ADD_CREDITS, "top-up", amount=4)
        self.assertEqual((result.outcome, result.in_house_in_cents_delta), (OutcomeWord.MOVED, 4 * CPC))
        self.assertEqual([t["type"] for t in rig.transfers()], [sm.IN_HOUSE_TO_MACHINE])


class SpinAndArmTest(WorkerTest):
    def arm(self, rig: Rig, sid: str = "arm") -> ws.Outcome:
        outcome = next(o for o in ws.catalogue(rig.state) if o.available and o.kind is ws.Kind.WIN)
        self.assertEqual(rig.act(Act.ARM, sid, outcome=str(outcome.id)).outcome, OutcomeWord.ARMED)
        self.assertEqual(rig.state.arm.state, ArmState.ARMED)
        return outcome

    def test_a_page_spin_is_journaled_with_its_evidence_and_a_machine_game_as_the_machines(self):
        rig = self.rig()
        rig.start()
        before = rig.state.meters
        result = rig.act(Act.SPIN, "spin")
        evidence = result.evidence
        game = rig.machine.game_meters
        self.assertEqual((result.outcome, evidence.games_played_delta), (OutcomeWord.PLAYED, 1))
        self.assertEqual((evidence.total_wager, evidence.paid),
                         (game["coin_in"] - before.coin_in, game["coin_out"] - before.coin_out))
        self.assertEqual(evidence.total_wager, ws.total_wager(rig.state))
        [spin] = rig.records("spin")
        self.assertEqual((spin["source"], spin["submission_id"], tuple(spin["stops"])),
                         ("page", "spin", rig.machine.stops))
        rig.machine.game = (1, (0, 0, 0), False)  # a game played at the machine
        rig.drive(lambda: len(rig.records("spin")) == 2)
        self.assertEqual(rig.records("spin")[-1]["source"], "machine")
        self.assertEqual(rig.state.last_spin.source, "machine")

    def test_a_pending_arm_hands_off_to_cancel_and_to_the_spin_bound_to_it_and_nothing_else(self):
        rig = self.rig()
        rig.start()
        self.arm(rig, "arm-1")
        self.assertEqual(rig.submit(Act.ARM, "second", outcome="0").code, Code.ARM_SET)
        self.assertEqual(rig.submit(Act.APPLY_PROFILE, "apply", profile=variant()).code, Code.ARM_SET)
        self.assertEqual(rig.submit(Act.RESTORE_STOCK, "restore").code, Code.ARM_SET)
        self.assertEqual((rig.mutations(ARM), rig.mutations(COMMIT), rig.mutations(RESTORE)), (1, 0, 0))
        self.assertEqual(rig.act(Act.CANCEL_ARM, "cancel").outcome, OutcomeWord.CANCELLED)
        self.assertEqual(rig.state.arm.state, ArmState.NONE)
        outcome = self.arm(rig, "arm-2")
        result = rig.act(Act.SPIN, "bound")
        self.assertEqual(result.outcome, OutcomeWord.PLAYED)
        [*_, (op, _, payload)] = rig.machine.extension.mutations()
        self.assertEqual((op, len(payload)), (SPIN, 1))  # bet only: it binds to the arm
        self.assertEqual(rig.machine.stops, outcome.stops)
        self.assertEqual(result.evidence.paid,
                         sp.score(rig.state.profile.profile, outcome.stops, ws.chosen_bet(rig.state)).total_award)
        self.assertEqual(rig.state.arm.state, ArmState.NONE)

    def test_a_door_clears_an_arm_only_once_status_shows_it(self):
        rig = self.rig()
        rig.start()
        self.arm(rig)
        before = len([r for r in rig.machine.extension.requests if r[0] == STATUS])
        events = len(rig.records("event"))
        rig.machine.open_door(SLOT_DOOR)
        rig.drive(lambda: rig.state.arm.state is not ArmState.ARMED)
        self.assertEqual(rig.state.arm.state, ArmState.NONE)
        self.assertGreater(len([r for r in rig.machine.extension.requests if r[0] == STATUS]), before)
        arm_events = [r["arm"] for r in rig.records("event")[events:] if "arm" in r]
        self.assertEqual(arm_events, [{"state": ArmState.NONE.value}])  # straight from armed to none

    def test_an_interrupted_bound_spin_holds_the_arm_until_cancel(self):
        rig = self.rig()
        rig.start()
        self.arm(rig)
        rig.machine.games_complete = False
        rig.assertSubmitted(rig.submit(Act.SPIN, "bound"))
        rig.drive(lambda: rig.machine.game is not None)
        rig.machine.complete_game(INTERRUPTED)
        self.assertEqual(rig.finish("bound").outcome, OutcomeWord.INTERRUPTED)
        rig.drive(lambda: rig.state.arm.state is ArmState.HELD)
        self.assertEqual(ws.admit(rig.state, rig.action(Act.SPIN, "probe")).code, Code.ARM_UNSETTLED)
        self.assertEqual(rig.act(Act.CANCEL_ARM, "cancel").outcome, OutcomeWord.CANCELLED)
        self.assertEqual(rig.state.arm.state, ArmState.NONE)

    def test_a_named_arm_is_unknown_after_a_restart_until_status_confirms_it(self):
        rig = self.rig()
        rig.start()
        outcome = self.arm(rig)
        rig = self.restart(rig)
        self.assertEqual(rig.state.arm.state, ArmState.UNKNOWN)
        rig.start()
        self.assertEqual((rig.state.arm.state, rig.state.arm.name), (ArmState.ARMED, outcome.name))

    def test_an_arm_status_cannot_confirm_stays_unknown_until_a_cancel_settles_it(self):
        rig = self.rig()
        rig.start()
        self.arm(rig)
        rig = self.restart(rig)
        extension = rig.machine.extension
        extension.last, extension.last_status = (extension.next_id, SPIN, b"\x01"), BUSY  # refused while down
        rig.start()
        rig.run_for(2 * ww.REFRESH_INTERVAL)
        self.assertEqual(rig.state.arm.state, ArmState.UNKNOWN)
        self.assertEqual(ws.admit(rig.state, rig.action(Act.SPIN, "probe")).code, Code.ARM_UNSETTLED)
        self.assertEqual(rig.act(Act.CANCEL_ARM, "cancel").outcome, OutcomeWord.CANCELLED)
        self.assertEqual(rig.state.arm.state, ArmState.NONE)


class LostReplyTest(WorkerTest):
    def test_a_lost_reply_at_each_step_is_sent_once_and_reconciled(self):
        def extension_op(op: int):
            return (lambda key, rest: key == op), (lambda rig: rig.mutations(op))

        cases = {
            "deposit": (Act.ADD_CREDITS, {"amount": 5}, (initiating_transfer, lambda rig: len(rig.transfers())),
                        OutcomeWord.HAPPENED),
            "spin": (Act.SPIN, {}, extension_op(SPIN), OutcomeWord.HAPPENED),
            "arm": (Act.ARM, {"outcome": "0"}, extension_op(ARM), OutcomeWord.HAPPENED),
            "upload begin": (Act.APPLY_PROFILE, {"profile": variant()}, extension_op(BEGIN), OutcomeWord.STAGING_ABANDONED),
            "upload commit": (Act.APPLY_PROFILE, {"profile": variant()}, extension_op(COMMIT),
                              OutcomeWord.COMMITTED_VERIFIED),
            "restore": (Act.RESTORE_STOCK, {}, extension_op(RESTORE), OutcomeWord.HAPPENED),
        }
        for case, (name, params, (lose, sends), outcome) in cases.items():
            with self.subTest(case):
                rig = self.rig(name=case)
                rig.start()
                rig.machine.lose_next(lose)
                result = rig.act(name, "action", **params)
                self.assertEqual((result.state, result.outcome), (ResultState.DONE, outcome))
                self.assertEqual(rig.state.checking, {})
                self.assertEqual(sends(rig), 1)
                if case == "deposit":
                    self.assertEqual((result.credits_delta, rig.state.counters.top_ups), (5, 1))
                if case == "spin":
                    self.assertEqual(result.evidence.games_played_delta, 1)
                if case == "upload begin":
                    self.assertEqual(rig.mutations(COMMIT), 0)

    def test_a_lost_commit_with_status_unavailable_holds_profile_and_arm_changes_until_it_is_read(self):
        rig = self.rig()
        rig.start()

        def status_goes_quiet(key, rest):
            if key == COMMIT:
                rig.machine.lose = rig.machine.lose | {STATUS, READ}

        rig.machine.lose_next(lambda key, rest: key == COMMIT)
        rig.machine.after = status_goes_quiet
        rig.assertSubmitted(rig.submit(Act.APPLY_PROFILE, "apply", profile=variant()))
        result = rig.finish("apply", (ResultState.CHECKING, *FINISHED))
        self.assertEqual(result.state, ResultState.CHECKING)
        self.assertIn(Family.EXTENSION, rig.state.checking)
        mutations = len(rig.machine.extension.mutations())
        rig.run_for(2 * ww.REFRESH_INTERVAL)
        for name, params in ((Act.APPLY_PROFILE, {"profile": variant()}), (Act.RESTORE_STOCK, {}),
                             (Act.ARM, {"outcome": "0"})):
            self.assertIsInstance(rig.submit(name, f"then-{name.value}", **params), ws.Refusal)
        rig.run_for(ww.REFRESH_INTERVAL)
        self.assertEqual(len(rig.machine.extension.mutations()), mutations)
        self.assertIn(Family.EXTENSION, rig.state.checking)
        rig.machine.lose = set()
        result = rig.finish("apply", (ResultState.DONE,))
        self.assertEqual(result.outcome, OutcomeWord.COMMITTED_VERIFIED)
        self.assertEqual((rig.mutations(COMMIT), rig.state.checking), (1, {}))
        self.assertTrue(rig.state.profile.custom)


class UploadContinuationTest(WorkerTest):
    def test_a_power_up_reported_at_the_yield_before_commit_abandons_staging_without_commit(self):
        rig = self.rig()
        rig.start()

        def boot_after_last_chunk(key, rest):
            if last_chunk(key, rest):
                rig.machine.boot()
                rig.clock.advance(ww.GENERAL_POLL_GAP)  # the yield before COMMIT polls

        rig.machine.after = boot_after_last_chunk
        result = rig.act(Act.APPLY_PROFILE, "apply", profile=variant())
        self.assertEqual(result.outcome, OutcomeWord.STAGING_ABANDONED)
        self.assertEqual(rig.mutations(COMMIT), 0)
        self.assertNotIn(se.Op.COMMIT_PROFILE.name, [r["op"] for r in rig.records("step")])
        self.assertEqual(rig.state.checking, {})
        rig.start()
        self.assertFalse(rig.state.profile.custom)

    def test_a_restart_after_an_acknowledged_chunk_never_uploads_again(self):
        rig = self.rig()
        rig.start()

        def crash_at_the_second_chunk(key, rest):
            if key == CHUNK and rig.mutations(CHUNK) == 1:
                raise Crash

        rig.machine.before = crash_at_the_second_chunk
        rig.assertSubmitted(rig.submit(Act.APPLY_PROFILE, "apply", profile=variant()))
        with self.assertRaises(Crash):
            rig.drive(lambda: False)
        self.assertEqual((rig.mutations(BEGIN), rig.mutations(CHUNK)), (1, 1))
        rig.machine.before = None
        mutations = len(rig.machine.extension.mutations())
        rig = self.restart(rig)
        rig.start()
        rig.run_for(ww.REFRESH_INTERVAL)
        self.assertEqual(len(rig.machine.extension.mutations()), mutations)
        self.assertEqual(rig.records("outcome")[-1]["result"], OutcomeWord.STAGING_ABANDONED.value)
        self.assertEqual(rig.state.checking, {})
        self.assertIsNone(ws.admit(rig.state, rig.action(Act.APPLY_PROFILE, "again", profile=variant())))


class JournalFailureTest(WorkerTest):
    def test_a_failed_intent_append_refuses_and_sends_nothing(self):
        rig = self.rig()
        rig.start()
        rig.disk.fail = kind("intent")
        result = rig.act(Act.ADD_CREDITS, "top-up", amount=5)
        self.assertEqual((result.state, result.code), (ResultState.REFUSED, Code.JOURNAL))
        self.assertEqual((rig.transfers(), rig.state.checking), ([], {}))
        rig.disk.fail = None
        self.assertEqual(rig.act(Act.ADD_CREDITS, "again", amount=5).outcome, OutcomeWord.MOVED)

    def test_a_failed_outcome_append_holds_money_until_it_is_durable(self):
        rig = self.rig()
        rig.start()
        rig.disk.fail = kind("outcome")
        rig.assertSubmitted(rig.submit(Act.ADD_CREDITS, "top-up", amount=5))
        result = rig.finish("top-up", (ResultState.CHECKING, *FINISHED))
        self.assertEqual(result.state, ResultState.CHECKING)
        self.assertIn(Family.MONEY, rig.state.checking)
        self.assertEqual(rig.submit(Act.ADD_CREDITS, "second", amount=1).code, Code.CHECKING)
        rig.run_for(ww.REFRESH_INTERVAL)
        self.assertIn(Family.MONEY, rig.state.checking)
        rig.disk.fail = None
        result = rig.finish("top-up", (ResultState.DONE,))
        self.assertEqual((result.outcome, rig.state.checking, len(rig.transfers())), (OutcomeWord.MOVED, {}, 1))
        self.assertEqual(rig.records("outcome")[-1]["result"], OutcomeWord.MOVED.value)

    def test_a_failed_step_append_after_begin_was_sent_is_settled_from_the_machine(self):
        rig = self.rig()
        rig.start()
        rig.disk.fail = lambda line: b'"kind":"step"' in line and b'"op":"PROFILE_CHUNK"' in line
        rig.disk.once = True
        result = rig.act(Act.APPLY_PROFILE, "apply", profile=variant())
        self.assertEqual(result.outcome, OutcomeWord.STAGING_ABANDONED)
        self.assertEqual((rig.mutations(BEGIN), rig.mutations(CHUNK), rig.mutations(COMMIT)), (1, 0, 0))
        self.assertEqual(rig.state.checking, {})


class SettingsTest(WorkerTest):
    def lose_a_sound_reply(self, rig: Rig) -> None:
        rig.machine.lose_next(lambda key, rest: key == "sound-off")
        self.assertEqual(rig.act(Act.SOUND, "lost", on=False).outcome, OutcomeWord.UNKNOWN)
        self.assertEqual(rig.state.settings.sound, Setting.UNKNOWN)

    def keys_during(self, rig: Rig, name: Act, inject: bytes | None = None, replace_reply: bool = False) -> list:
        """The long polls the machine gets while `name` runs. `inject` goes in front of the first
        boundary reply, or with `replace_reply` takes the place of every one."""
        seen = []

        def watch(key, rest):
            if rig.in_progress(name):
                if key == "credits" and inject is not None and (replace_reply or "credits" not in seen):
                    rig.machine.inject(inject, replace=replace_reply)
                seen.append(key)

        rig.machine.before = watch
        return seen

    def test_a_late_ack_in_front_of_the_boundary_reply_is_not_taken_and_the_fresh_tap_is_acked(self):
        rig = self.rig()
        rig.start()
        self.lose_a_sound_reply(rig)
        seen = self.keys_during(rig, Act.SOUND, inject=bytes((ADDRESS,)))
        result = rig.act(Act.SOUND, "fresh", on=True)
        self.assertEqual(result.outcome, OutcomeWord.ACKED)
        self.assertEqual(rig.state.settings.sound, Setting.ON)
        self.assertGreater(seen.index("sound-on"), seen.index("credits"))
        self.assertGreater(seen.count("credits"), 1)  # the stale reply was discarded and read again
        self.assertEqual(seen.count("sound-on"), 1)

    def test_with_no_attributable_boundary_reply_the_tap_is_sent_once_and_stays_unknown(self):
        rig = self.rig()
        rig.start()
        self.lose_a_sound_reply(rig)
        seen = self.keys_during(rig, Act.SOUND, inject=bytes((ADDRESS,)), replace_reply=True)
        result = rig.act(Act.SOUND, "fresh", on=True)
        self.assertEqual((result.outcome, rig.state.settings.sound), (OutcomeWord.UNKNOWN, Setting.UNKNOWN))
        self.assertEqual(seen.count("sound-on"), 1)
        self.assertEqual(len(sent_requests(rig.machine, "sound-on")), 1)
        self.keys_during(rig, Act.SOUND)
        self.assertEqual(rig.act(Act.SOUND, "next", on=True).outcome, OutcomeWord.ACKED)
        self.assertEqual(rig.state.settings.sound, Setting.ON)

    def test_unknown_survives_a_restart_and_the_new_process_reads_a_boundary_first(self):
        rig = self.rig()
        rig.start()
        self.lose_a_sound_reply(rig)
        rig = self.restart(rig)
        self.assertEqual(rig.state.settings.sound, Setting.UNKNOWN)
        rig.start()
        self.assertEqual(rig.state.settings.sound, Setting.UNKNOWN)
        seen = self.keys_during(rig, Act.SOUND, inject=bytes((ADDRESS,)))
        self.assertEqual(rig.act(Act.SOUND, "fresh", on=False).outcome, OutcomeWord.ACKED)
        self.assertLess(seen.index("credits"), seen.index("sound-off"))
        self.assertEqual(rig.state.settings.sound, Setting.OFF)

    def test_a_disconnect_during_the_boundary_refuses_the_tap_without_sending(self):
        rig = self.rig()
        rig.start()
        self.lose_a_sound_reply(rig)

        def unplug(key, rest):
            if key == "credits" and rig.in_progress(Act.SOUND):
                rig.unplugged = True
                rig.machine.hang_up()

        rig.machine.before = unplug
        result = rig.act(Act.SOUND, "fresh", on=True)
        self.assertEqual((result.state, result.code), (ResultState.REFUSED, Code.OFFLINE))
        self.assertEqual(rig.state.link, Link.OFFLINE)
        rig.machine.before = None
        rig.unplugged = False
        rig.machine.reconnect()
        rig.start()
        rig.run_for(ww.REFRESH_INTERVAL)
        self.assertEqual(sent_requests(rig.machine, "sound-on"), [])
        self.assertEqual(rig.state.settings.sound, Setting.UNKNOWN)


class LinkTest(WorkerTest):
    def test_back_online_after_a_power_cycle_a_full_refresh_and_the_reconciliation_come_before_controls(self):
        rig = self.rig(Machine(clock=FakeClock(), needs_sync=True))
        rig.start()

        def power_fails(key, rest):
            if initiating_transfer(key, rest):
                rig.machine.power_off()

        rig.machine.lose_next(initiating_transfer)
        rig.machine.after = power_fails
        rig.assertSubmitted(rig.submit(Act.ADD_CREDITS, "top-up", amount=5))
        self.assertEqual(rig.finish("top-up", (ResultState.CHECKING, *FINISHED)).state, ResultState.CHECKING)
        rig.drive(lambda: rig.state.link is Link.OFFLINE)
        self.assertIn(Family.MONEY, rig.state.checking)
        rig.machine.after = None
        infos = len(sent_requests(rig.machine, "info"))
        rig.machine.power_on()

        def never_fresh_while_money_is_unsettled():
            state = rig.state
            if state.fresh and state.link is Link.ONLINE:
                self.assertNotIn(Family.MONEY, state.checking)

        rig.drive(lambda: rig.state.fresh, check=never_fresh_while_money_is_unsettled)
        result = rig.result("top-up")
        self.assertEqual((result.state, result.outcome, result.credits_delta), (ResultState.DONE, OutcomeWord.HAPPENED, 5))
        self.assertEqual(len(rig.transfers()), 1)
        self.assertGreater(len(sent_requests(rig.machine, "info")), infos)  # the full refresh
        records = rig.records()
        outcome = next(r["seq"] for r in records if r["kind"] == "outcome" and r.get("submission_id") == "top-up")
        found = [r["seq"] for r in records if r["kind"] == "page" and r.get("event") == "machine_found"]
        self.assertLess(outcome, found[-1])

    def test_a_hung_up_line_is_closed_and_reopened_through_the_factory(self):
        rig = self.rig()
        rig.start()
        rig.unplugged = True
        rig.machine.hang_up()
        rig.drive(lambda: rig.state.link is Link.OFFLINE)
        self.assertTrue(rig.machine.closed)
        rig.run_for(sum(ww.REOPEN_BACKOFF))  # reopening fails while unplugged
        self.assertEqual(rig.opens, 1)
        sent = len(rig.machine.sent)
        rig.unplugged = False
        rig.machine.reconnect()
        rig.start()
        self.assertEqual(rig.opens, 2)
        self.assertEqual(rig.machine.sent[sent][0], protocol.SYNC_POLL)  # the resync on the new handle
        self.assertEqual(rig.state.link, Link.ONLINE)

    def test_one_unanswered_general_poll_after_a_long_read_neither_goes_offline_nor_restarts_recovery(self):
        rig = self.rig()
        rig.start()
        slow = []

        def slow_first_read(key, rest):
            if key == "info" and not rig.state.fresh and not slow:
                slow.append(key)
                rig.clock.advance(ww.OFFLINE_AFTER + ww.GENERAL_POLL_GAP)  # a read far slower than the fake's
                rig.machine.drop_general = protocol.POLL_TRIES  # every try of the next general poll

        rig.machine.after = slow_first_read
        rig.machine.boot()  # exception 17: a full refresh follows
        rig.drive(lambda: not rig.state.fresh)

        def never_offline():
            self.assertIsNot(rig.state.link, Link.OFFLINE)

        rig.drive(lambda: rig.state.fresh, check=never_offline)
        self.assertEqual((slow, rig.machine.drop_general), (["info"], 0))  # the miss happened mid-refresh
        self.assertNotIn("machine_lost", [r.get("event") for r in rig.records("page")])

    def test_a_power_up_reported_during_the_full_refresh_starts_it_again(self):
        rig = self.rig()
        rig.start()
        refreshes = []

        def power_up_mid_refresh(key, rest):
            if key == "info" and not rig.state.fresh:  # the full refresh's first read
                refreshes.append(key)
                if len(refreshes) == 1:
                    rig.machine.boot()
                    rig.clock.advance(ww.GENERAL_POLL_GAP)  # its exception 17 arrives at the next yield

        rig.machine.after = power_up_mid_refresh
        rig.machine.boot()
        rig.drive(lambda: not rig.state.fresh)
        fresh_while_one_refresh = []
        rig.drive(lambda: rig.state.fresh,
                  check=lambda: fresh_while_one_refresh.append(rig.state.fresh and len(refreshes) < 2))
        self.assertEqual(len(refreshes), 2)
        self.assertNotIn(True, fresh_while_one_refresh)

    def test_an_os_error_that_is_not_the_lines_ends_the_worker_instead_of_reopening_the_line(self):
        rig = self.rig()
        missing = FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT))
        unreadable = mock.patch.object(sp, "stock_profile", side_effect=missing)  # the full refresh reads it
        with unreadable, self.assertRaises(FileNotFoundError):
            rig.drive(lambda: rig.opens > 1)  # reopening the line would be the defect
        self.assertEqual((rig.opens, rig.machine.closed), (1, False))
        self.assertNotIn("machine_lost", [r.get("event") for r in rig.records("page")])
        self.assertIsNot(rig.state.link, Link.OFFLINE)

    def test_a_silent_machine_goes_offline_on_the_open_line_and_comes_back(self):
        rig = self.rig()
        rig.start()
        rig.machine.power_off()
        rig.drive(lambda: rig.state.link is Link.OFFLINE)
        self.assertGreaterEqual(rig.clock() - rig.machine.last_answer, ww.OFFLINE_AFTER)
        self.assertLessEqual(rig.clock() - rig.machine.last_answer, ww.OFFLINE_AFTER + ww.MAX_GENERAL_POLL_GAP)
        self.assertEqual(rig.submit(Act.ADD_CREDITS, "top-up", amount=1).code, Code.OFFLINE)
        self.assertFalse(rig.machine.closed)
        rig.machine.power_on()
        rig.start()
        self.assertEqual((rig.opens, rig.state.link), (1, Link.ONLINE))


class PhysicalEventsTest(WorkerTest):
    def test_events_at_the_machine_during_a_transfer_are_journaled_and_the_transfer_still_settles(self):
        rig = self.rig()
        rig.start()

        def machine_activity(key, rest):
            if initiating_transfer(key, rest):
                rig.machine.open_door(SLOT_DOOR)
                rig.machine.pending.append(HANDPAY)
                rig.machine.game = (1, (0, 0, 0), False)  # played at the machine
            if rig.in_progress(Act.ADD_CREDITS):
                rig.clock.advance(ww.GENERAL_POLL_GAP)  # a general poll at every yield of the transfer

        rig.machine.after = machine_activity
        amount = 5
        result = rig.act(Act.ADD_CREDITS, "top-up", amount=amount)
        self.assertEqual((result.outcome, result.in_house_in_cents_delta), (OutcomeWord.MOVED, amount * CPC))
        self.assertEqual(len(rig.transfers()), 1)
        rig.drive(lambda: len(rig.records("spin")) == 1)
        records = rig.records()
        intent = next(r["seq"] for r in records if r["kind"] == "intent")
        outcome = next(r["seq"] for r in records if r["kind"] == "outcome")
        door = next(r["seq"] for r in records if r.get("event") == "door_open")
        self.assertLess(intent, door)
        self.assertLess(door, outcome)  # reported while the transfer was running
        self.assertIn("handpay", [r.get("event") for r in records])
        self.assertEqual(rig.records("spin")[0]["source"], "machine")
        self.assertIsNotNone(ws.open_door(rig.state))


if __name__ == "__main__":
    unittest.main()
