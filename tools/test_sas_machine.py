"""sas_machine on the combined fake machine: money, settings, the clock, extension actions and
the reconcilers that settle a lost reply without resending."""

from __future__ import annotations

import datetime as dt
import unittest
from dataclasses import replace

import sas
import sas_commands as sc
import sas_extension as se
import sas_machine as sm
import sas_profile as sp
import sas_protocol as protocol
from sas_testing import (
    ACCEPTED,
    ARM,
    BEGIN,
    CANCEL,
    CHUNK,
    COMMIT,
    FAULT,
    INTERRUPTED,
    OK,
    POWER_APPLIED,
    READ,
    RESTORE,
    SLOT_DOOR,
    SPIN,
    STATUS,
    FakeS6000,
    sent_requests,
)

ADDRESS = FakeS6000.ADDRESS
CPC = FakeS6000.CENTS_PER_CREDIT


def no_pace() -> None:
    pass


class Journal:
    """A record callback that keeps each intent with how many transfers had been sent by then."""

    def __init__(self, machine: FakeS6000):
        self.machine = machine
        self.intents: list[dict] = []
        self.transfers_before: list[int] = []

    def __call__(self, intent: dict) -> None:
        self.intents.append(intent)
        self.transfers_before.append(len(transfers(self.machine)))


def transfers(machine: FakeS6000) -> list[dict]:
    """The initiating 72 requests the machine received; interrogations excluded."""
    return [r for r in sent_requests(machine, "aft-transfer") if r["transfer_code"] == sm.FULL_TRANSFER]


def money(machine: FakeS6000) -> dict:
    return {name: machine.meters[code] for name, code in sm.MONEY_METERS.items()}


class LoseReplies(FakeS6000):
    """Loses the replies to the first `count` `name` polls whose values match `where`, once a
    transfer was sent if `after_transfer`; the machine still acts on each."""

    def __init__(self, name: str, *, count: int, after_transfer: bool = False, **where: object):
        super().__init__()
        self.command = sc.BY_NAME[name]
        self.count, self.after_transfer, self.where = count, after_transfer, where

    def send(self, first, rest):
        before = len(self.replies)
        super().send(first, rest)
        if (self.count and len(self.replies) > before and first != protocol.SYNC_POLL and rest
                and rest[0] == self.command.code and (transfers(self) or not self.after_transfer)):
            latest = sent_requests(self, self.command.name)[-1]
            if all(latest.get(key) == value for key, value in self.where.items()):
                self.replies.pop()
                self.count -= 1


class NeverArrives(FakeS6000):
    """A machine the initiating transfer request never reaches."""

    def _aft_transfer(self, v):
        return None if v["transfer_code"] == sm.FULL_TRANSFER else super()._aft_transfer(v)


class DepositAndCashOutTest(unittest.TestCase):
    def test_deposit_moves_exactly_the_amount_at_registration_status_80_recorded_before_its_one_send(self):
        machine = FakeS6000()
        registration = machine.registration
        self.assertEqual(registration[0], 0x80)  # the cabinet's unenrolled status
        start = money(machine)
        journal = Journal(machine)
        credits = 5
        outcome = sm.deposit(protocol.Host(machine, ADDRESS), credits, record=journal, pace=no_pace)
        self.assertEqual((outcome.status, outcome.code, outcome.cents), (sm.TRANSFER_MOVED, sm.TRANSFER_OK, credits * CPC))
        after = money(machine)
        self.assertEqual({name: after[name] - start[name] for name in after},
                         {"credits": credits, "in_cents": credits * CPC, "in_count": 1, "out_cents": 0, "out_count": 0})
        [sent] = transfers(machine)
        [intent] = journal.intents
        self.assertEqual(journal.transfers_before, [0])
        self.assertEqual((intent["transaction_id"], intent["type"], intent["cents"], intent["asset"]),
                         (sent["transaction_id"], sm.IN_HOUSE_TO_MACHINE, credits * CPC, machine.ASSET))
        self.assertIn(bytes.fromhex(intent["request_hex"]), machine.messages())
        self.assertEqual(outcome.transaction_id, sent["transaction_id"])
        self.assertEqual(machine.registration, registration)

    def test_a_record_that_fails_sends_nothing(self):
        for direction in ("deposit", "cash_out"):
            with self.subTest(direction=direction):
                machine = FakeS6000()
                host = protocol.Host(machine, ADDRESS)

                def record(intent: dict) -> None:
                    raise OSError("journal append failed")

                with self.assertRaises(OSError):
                    if direction == "deposit":
                        sm.deposit(host, 1, record=record, pace=no_pace)
                    else:
                        sm.cash_out(host, record=record, pace=no_pace)
                self.assertEqual(transfers(machine), [])

    def test_cash_out_moves_the_whole_balance_and_nothing_else(self):
        machine = FakeS6000()
        registration = machine.registration
        balance = machine.meters[sm.MONEY_METERS["credits"]]
        start = money(machine)
        outcome = sm.cash_out(protocol.Host(machine, ADDRESS), record=Journal(machine), pace=no_pace)
        self.assertEqual((outcome.status, outcome.cents), (sm.TRANSFER_MOVED, balance * CPC))
        after = money(machine)
        self.assertEqual({name: after[name] - start[name] for name in after},
                         {"credits": -balance, "in_cents": 0, "in_count": 0, "out_cents": balance * CPC,
                          "out_count": 1})
        [sent] = transfers(machine)
        self.assertEqual((sent["type"], sent["cashable_cents"]), (sm.IN_HOUSE_TO_HOST, balance * CPC))
        for flag_field in ("restricted_cents", "nonrestricted_cents"):
            self.assertEqual(sent[flag_field], 0)
        self.assertEqual(int(sent["flags"], 16), 0)
        self.assertEqual(machine.registration, registration)

    def test_a_missing_invalid_or_inconsistent_registration_reply_refuses_both_directions_before_sending(self):
        class NoRegistrationReply(FakeS6000):
            def _aft_register(self, v):
                return None

        class ShortRegistrationReply(FakeS6000):
            def _aft_register(self, v):
                return super()._aft_register(v)[:3]  # no POS ID

        class OtherAsset(FakeS6000):
            def _aft_register(self, v):
                status, asset, key, pos_id = super()._aft_register(v)
                return status, asset + 1, key, pos_id

        for machine_type in (NoRegistrationReply, ShortRegistrationReply, OtherAsset):
            for direction in ("deposit", "cash_out"):
                with self.subTest(machine=machine_type.__name__, direction=direction):
                    machine = machine_type()
                    journal = Journal(machine)
                    host = protocol.Host(machine, ADDRESS)
                    with self.assertRaises(sm.MachineError):
                        if direction == "deposit":
                            sm.deposit(host, 1, record=journal, pace=no_pace)
                        else:
                            sm.cash_out(host, record=journal, pace=no_pace)
                    self.assertEqual((transfers(machine), journal.intents), ([], []))

    def test_deposit_only_availability_admits_deposits_and_refuses_cash_out_before_sending(self):
        machine = FakeS6000()
        machine.available_transfers = sm.TO_MACHINE
        host = protocol.Host(machine, ADDRESS)
        self.assertEqual(sm.deposit(host, 1, record=Journal(machine), pace=no_pace).status, sm.TRANSFER_MOVED)
        with self.assertRaises(sm.NotAdmitted):
            sm.cash_out(host, record=Journal(machine), pace=no_pace)
        self.assertEqual(len(transfers(machine)), 1)

    def test_the_limit_converts_exactly_and_an_amount_over_it_is_refused_before_sending(self):
        machine = FakeS6000()
        limit_credits = machine.TRANSFER_LIMIT_CENTS // CPC
        host = protocol.Host(machine, ADDRESS)
        journal = Journal(machine)
        with self.assertRaises(sm.NotAdmitted):
            sm.deposit(host, limit_credits + 1, record=journal, pace=no_pace)
        self.assertEqual((transfers(machine), journal.intents), ([], []))
        self.assertEqual(sm.deposit(host, limit_credits, record=journal, pace=no_pace).status, sm.TRANSFER_MOVED)

    def test_a_correlated_terminal_rejection_means_nothing_moved(self):
        for direction, code in (("deposit", 0x87), ("cash_out", 0x87), ("cash_out", 0x84)):
            with self.subTest(direction=direction, code=f"{code:02X}"):
                machine = FakeS6000()
                machine.transfer_refusal = code
                start = money(machine)
                host = protocol.Host(machine, ADDRESS)
                if direction == "deposit":
                    outcome = sm.deposit(host, 3, record=Journal(machine), pace=no_pace)
                else:
                    outcome = sm.cash_out(host, record=Journal(machine), pace=no_pace)
                self.assertEqual((outcome.status, outcome.code), (sm.TRANSFER_REJECTED, code))
                self.assertEqual(money(machine), start)
                # one initiating request and no partial attempt after a refusal
                self.assertEqual([r["transfer_code"] for r in transfers(machine)], [sm.FULL_TRANSFER])


class LostMoneyReplyTest(unittest.TestCase):
    """A reply lost at each money step: the request went once, Uncertain carries the intent, and
    reconcile_transfer finds what happened by reading."""

    def lose_at(self, step: str) -> FakeS6000:
        if step == "request":
            return LoseReplies("aft-transfer", count=1, transfer_code=sm.FULL_TRANSFER)
        if step == "interrogation":  # every repeat of the read, so the read fails
            return LoseReplies("aft-transfer", count=protocol.POLL_TRIES, transfer_code=sm.INTERROGATE)
        # the read that acknowledges completion
        return LoseReplies("extended-meters", count=protocol.POLL_TRIES, after_transfer=True)

    def test_each_lost_step_sends_once_and_reconciles_to_what_the_machine_did(self):
        for step in ("request", "interrogation", "completion read"):
            for direction in ("deposit", "cash_out"):
                with self.subTest(step=step, direction=direction):
                    machine = self.lose_at(step)
                    host = protocol.Host(machine, ADDRESS)
                    journal = Journal(machine)
                    with self.assertRaises(sm.Uncertain) as caught:
                        if direction == "deposit":
                            sm.deposit(host, 2, record=journal, pace=no_pace)
                        else:
                            sm.cash_out(host, record=journal, pace=no_pace)
                    [sent] = transfers(machine)
                    self.assertEqual(caught.exception.intent, journal.intents[-1])
                    self.assertEqual(caught.exception.intent["transaction_id"], sent["transaction_id"])
                    reconciled = sm.reconcile_transfer(host, caught.exception.intent, pace=no_pace)
                    self.assertEqual(reconciled.result, sm.HAPPENED)
                    self.assertEqual(len(transfers(machine)), 1)

    def test_a_refusal_whose_reply_was_lost_reconciles_to_nothing_changed(self):
        machine = LoseReplies("aft-transfer", count=1, transfer_code=sm.FULL_TRANSFER)
        machine.transfer_refusal = 0x87
        host = protocol.Host(machine, ADDRESS)
        with self.assertRaises(sm.Uncertain) as caught:
            sm.deposit(host, 2, record=Journal(machine), pace=no_pace)
        self.assertEqual(sm.reconcile_transfer(host, caught.exception.intent, pace=no_pace).result, sm.NOTHING_CHANGED)

    def test_a_request_that_never_arrived_reconciles_to_nothing_changed(self):
        machine = NeverArrives()
        host = protocol.Host(machine, ADDRESS)
        with self.assertRaises(sm.Uncertain) as caught:
            sm.deposit(host, 2, record=Journal(machine), pace=no_pace)
        self.assertEqual(sm.reconcile_transfer(host, caught.exception.intent, pace=no_pace).result, sm.NOTHING_CHANGED)

    def test_meters_that_disagree_with_the_peek_reconcile_to_inconsistent(self):
        machine = LoseReplies("aft-transfer", count=1, transfer_code=sm.FULL_TRANSFER)
        host = protocol.Host(machine, ADDRESS)
        with self.assertRaises(sm.Uncertain) as caught:
            sm.deposit(host, 2, record=Journal(machine), pace=no_pace)
        machine.meters[sm.MONEY_METERS["in_count"]] += 1
        self.assertEqual(sm.reconcile_transfer(host, caught.exception.intent, pace=no_pace).result, sm.INCONSISTENT)

    def test_a_line_failure_after_the_send_is_uncertain_with_the_error_as_its_cause(self):
        machine = FakeS6000()

        def unplug_after_recording(intent: dict) -> None:
            machine.failure = ("aft-transfer", sas.SerialClosed("unplugged"), "read")

        with self.assertRaises(sm.Uncertain) as caught:
            sm.deposit(protocol.Host(machine, ADDRESS), 2, record=unplug_after_recording, pace=no_pace)
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(len(transfers(machine)), 1)


def extension(machine: FakeS6000) -> se.Extension:
    return se.Extension(protocol.Host(machine, ADDRESS), pace=no_pace)


class ExtensionActionTest(unittest.TestCase):
    def test_a_spin_plays_one_game_scored_by_the_active_profile(self):
        machine = FakeS6000()
        stops = (3, 7, 11)
        bet = 2
        start = machine.meters[sm.MONEY_METERS["credits"]]
        ext = extension(machine)
        result = sm.spin(ext, bet, stops, record=lambda intent: None)
        self.assertEqual(result["status"], se.Status.ACCEPTED.name)
        ext.status()  # time passes: the game ends
        expected = sp.score(sp.stock_profile(), stops, bet)
        self.assertEqual(machine.meters[sm.MONEY_METERS["credits"]], start - expected.total_bet + expected.total_award)
        self.assertEqual(sm.reel_stops(protocol.Host(machine, ADDRESS)), stops)
        self.assertEqual(machine.game_meters["games_played"], 1)

    def test_an_open_door_refuses_spin_and_arm(self):
        machine = FakeS6000()
        machine.open_door(SLOT_DOOR)
        ext = extension(machine)
        for request in (lambda: sm.spin(ext, 1, record=lambda intent: None),
                        lambda: sm.arm(ext, (1, 2, 3), record=lambda intent: None)):
            with self.assertRaises(se.Refused) as caught:
                request()
            self.assertEqual(caught.exception.status, se.Status.BUSY)

    def test_a_lost_spin_arm_or_cancel_reply_is_sent_once_and_reconciled(self):
        cases = {SPIN: lambda ext: sm.spin(ext, 1, record=keep),
                 ARM: lambda ext: sm.arm(ext, (1, 2, 3), record=keep),
                 CANCEL: lambda ext: sm.cancel(ext, record=keep)}
        for op, request in cases.items():
            with self.subTest(op=se.Op(op).name):
                machine = FakeS6000()
                ext = extension(machine)
                if op == CANCEL:
                    sm.arm(ext, (1, 2, 3), record=keep)
                machine.lose = {op}
                with self.assertRaises(sm.Uncertain) as caught:
                    request(ext)
                self.assertEqual([r[0] for r in machine.extension.mutations()].count(op), 1)
                machine.lose = set()
                reconciled = sm.reconcile_extension(ext, caught.exception.intent)
                self.assertEqual(reconciled.result, sm.HAPPENED)
                self.assertEqual([r[0] for r in machine.extension.mutations()].count(op), 1)

    def test_a_refused_spin_whose_reply_was_lost_reconciles_to_nothing_changed(self):
        machine = FakeS6000()
        machine.open_door(SLOT_DOOR)
        machine.lose = {SPIN}
        ext = extension(machine)
        with self.assertRaises(sm.Uncertain) as caught:
            sm.spin(ext, 1, record=keep)
        machine.lose = set()
        self.assertEqual(sm.reconcile_extension(ext, caught.exception.intent).result, sm.NOTHING_CHANGED)

    def test_a_lost_restore_reply_is_sent_once_and_reconciled_to_what_status_proves(self):
        cases = {  # STATUS after the lost reply: the restore's status, custom profile still active, fault
            "restored": (OK, False, False, sm.RESTORED),
            "interrupted before the switch": (INTERRUPTED, True, False, sm.INTERRUPTED),
            "faulted": (FAULT, True, True, sm.FAULT),
            "pending with a fault": (ACCEPTED, True, True, sm.PENDING),
            "OK with a profile active": (OK, True, False, sm.INCONSISTENT),
        }
        for case, (status, active, fault, result) in cases.items():
            with self.subTest(case):
                machine = FakeS6000()
                ext = extension(machine)
                se.upload(ext, variant(), record=keep)
                fake = machine.extension
                custom = (fake.active, fake.profile_crc)
                machine.lose = {RESTORE}
                intents: list[dict] = []
                with self.assertRaises(se.UncertainOutcome):
                    se.restore(ext, record=intents.append)
                machine.lose = set()
                fake.last_status, fake.fault = status, fault
                if active:
                    fake.active, fake.profile_crc = custom
                self.assertEqual(sm.reconcile_extension(ext, intents[-1]).result, result)
                self.assertEqual([r[0] for r in fake.mutations()].count(RESTORE), 1)


def keep(intent: dict) -> None:
    """A record callback for tests that do not look at the intent."""


def variant() -> sp.Profile:
    """A valid profile other than stock."""
    stock = sp.stock_profile()
    weights = [list(row) for row in stock.weights]
    weights[1][2] = weights[1][2] + 1 if weights[1][2] < sp.BYTE_MAX else 1
    return replace(stock, weights=tuple(tuple(row) for row in weights))


class UploadReconcileTest(unittest.TestCase):
    """A reply lost at each upload step: every step goes once, and reconcile_extension decides
    from the step records the journal kept, STATUS and the CRC-bracketed read-back."""

    def upload(self, machine: FakeS6000, lose: int, on_step=None) -> tuple[dict, list[dict]]:
        """The parent intent and the step records of an upload whose `lose` reply is lost."""
        ext = extension(machine)
        intents: list[dict] = []
        steps: list[dict] = []

        def record(r: dict) -> None:
            if r["kind"] == "intent":
                intents.append(r)
                return
            if on_step is not None:
                on_step(r)
            steps.append(dict(r, result=sm.STEP_LOST if se.Op[r["op"]] == lose else sm.STEP_ACKED))

        machine.lose = {lose}
        with self.assertRaises(se.UncertainOutcome):
            se.upload(ext, variant(), record=record)
        machine.lose = set()
        [intent] = intents
        return intent, steps

    def sent(self, machine: FakeS6000, op: int) -> int:
        return [r[0] for r in machine.extension.mutations()].count(op)

    def test_a_lost_begin_or_chunk_reply_abandons_staging_without_any_send(self):
        for op in (BEGIN, CHUNK):
            with self.subTest(op=se.Op(op).name):
                machine = FakeS6000()
                intent, steps = self.upload(machine, op)
                sent = len(machine.sent)
                result = sm.reconcile_extension(extension(machine), intent, steps=steps)
                self.assertEqual(result.result, sm.STAGING_ABANDONED)
                self.assertEqual(len(machine.sent), sent)
                self.assertEqual(self.sent(machine, op), 1)
                self.assertEqual(self.sent(machine, COMMIT), 0)

    def test_a_lost_commit_reply_reconciles_to_committed_verified_on_read_back(self):
        machine = FakeS6000()
        intent, steps = self.upload(machine, COMMIT)
        result = sm.reconcile_extension(extension(machine), intent, steps=steps)
        self.assertEqual(result.result, sm.COMMITTED_VERIFIED)
        self.assertEqual(self.sent(machine, COMMIT), 1)

    def test_a_lost_commit_with_status_and_read_back_unavailable_stays_pending_until_they_answer(self):
        machine = FakeS6000()
        intent, steps = self.upload(machine, COMMIT)
        ext = extension(machine)
        machine.lose = {STATUS, READ}
        self.assertEqual(sm.reconcile_extension(ext, intent, steps=steps).result, sm.PENDING)
        machine.lose = {READ}
        self.assertEqual(sm.reconcile_extension(ext, intent, steps=steps).result, sm.PENDING)
        machine.lose = set()
        self.assertEqual(sm.reconcile_extension(ext, intent, steps=steps).result, sm.COMMITTED_VERIFIED)
        self.assertEqual(self.sent(machine, COMMIT), 1)

    def test_a_lost_commit_the_machine_refused_after_a_boot_reconciles_to_aborted(self):
        machine = FakeS6000()

        def boot_before_commit(step: dict) -> None:
            if step["op"] == se.Op.COMMIT_PROFILE.name:
                machine.boot()  # staging is volatile, so the COMMIT that follows is refused

        intent, steps = self.upload(machine, COMMIT, on_step=boot_before_commit)
        self.assertIn(POWER_APPLIED, machine.pending)
        result = sm.reconcile_extension(extension(machine), intent, steps=steps)
        self.assertEqual(result.result, sm.ABORTED)
        self.assertIsNone(machine.extension.active)

    def test_a_machine_matching_neither_target_nor_previous_is_inconsistent(self):
        machine = FakeS6000()
        intent, steps = self.upload(machine, COMMIT)
        machine.extension.active = sp.encode(sp.stock_profile())
        machine.extension.profile_crc ^= 1
        machine.extension.last = (machine.extension.next_id, RESTORE, b"")
        self.assertEqual(sm.reconcile_extension(extension(machine), intent, steps=steps).result, sm.INCONSISTENT)

    def test_a_lost_read_back_after_an_acknowledged_commit_reconciles_from_status_and_read_back(self):
        machine = FakeS6000()
        ext = extension(machine)
        intents: list[dict] = []
        steps: list[dict] = []

        def record(r: dict) -> None:
            (intents if r["kind"] == "intent" else steps).append(dict(r, result=sm.STEP_ACKED))

        machine.lose = {READ}
        with self.assertRaises(protocol.ProtocolError):
            se.upload(ext, variant(), record=record)
        self.assertEqual(sm.reconcile_extension(ext, intents[0], steps=steps).result, sm.PENDING)
        machine.lose = set()
        self.assertEqual(sm.reconcile_extension(ext, intents[0], steps=steps).result, sm.COMMITTED_VERIFIED)
        self.assertEqual(self.sent(machine, COMMIT), 1)


class SettingsTest(unittest.TestCase):
    def test_a_lost_play_reply_is_settled_by_the_6e_probe_alone(self):
        class SlowHandler(FakeS6000):
            """The 6E probe shows a play change one read late, as a handler that has not run yet."""

            probes_behind = 0

            def _enable_game(self, v):
                self.probes_behind = 1
                return super()._enable_game(v)

            def _authentication(self, v):
                if self.probes_behind:
                    self.probes_behind -= 1  # still the state before the change
                    return 0, sm.NO_SUCH_COMPONENT if self.game_enabled else sm.CANNOT_AUTHENTICATE_NOW
                return super()._authentication(v)

        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                machine = SlowHandler()
                machine.game_enabled = not enabled
                machine.lose = {"enable-game"}
                result = sm.set_play(protocol.Host(machine, ADDRESS), enabled, record=keep, pace=no_pace)
                self.assertEqual((result.confirmed, result.acked, result.play_enabled), (True, False, enabled))
                self.assertEqual(len(sent_requests(machine, "enable-game")), 1)

    def test_ack_only_settings_send_once_and_report_acked_or_unknown(self):
        for lost, expected in ((False, sm.ACKED), (True, sm.UNKNOWN)):
            for name, operation in (("sound-off", sm.set_sound), ("bills-off", sm.set_bills)):
                with self.subTest(command=name, lost=lost):
                    machine = FakeS6000()
                    machine.lose = {name} if lost else set()
                    journal: list[dict] = []
                    result = operation(protocol.Host(machine, ADDRESS), False, record=journal.append)
                    self.assertEqual(result, expected)
                    self.assertEqual(len(sent_requests(machine, name)), 1)
                    self.assertEqual([bytes.fromhex(i["request_hex"]) for i in journal], machine.messages()[-1:])

    def test_ack_boundary_needs_a_validated_data_reply(self):
        stale = bytes((ADDRESS,))  # an address-valued byte, as a late ACK is
        for name, injections in (("bare byte as the reply", [(stale, True)]),
                                 ("stale byte in front of the reply", [(stale, False)])):
            with self.subTest(name):
                machine = FakeS6000()
                for data, replace in injections:
                    machine.inject(data, replace=replace)
                credits = sm.ack_boundary(protocol.Host(machine, ADDRESS), pace=no_pace)
                self.assertEqual(credits, machine.meters[sm.MONEY_METERS["credits"]])
                self.assertGreater(len(sent_requests(machine, "credits")), 1)  # the stale reply was not taken

    def test_ack_boundary_fails_when_only_bare_bytes_come_back(self):
        machine = FakeS6000()
        for _ in range(sm.BOUNDARY_READS * protocol.POLL_TRIES):
            machine.inject(bytes((ADDRESS,)), replace=True)
        with self.assertRaises(sm.ReadFailed):
            sm.ack_boundary(protocol.Host(machine, ADDRESS), pace=no_pace)

    def test_ack_boundary_lets_a_line_failure_through(self):
        machine = FakeS6000()
        machine.hang_up()
        with self.assertRaises(sas.SerialClosed):
            sm.ack_boundary(protocol.Host(machine, ADDRESS), pace=no_pace)

    def test_the_clock_is_set_once_and_confirmed_by_7e(self):
        when = dt.datetime(2026, 11, 2, 20, 15, 30)  # noqa: DTZ001 -- SAS time is zone-less
        for writable in (True, False):
            with self.subTest(writable=writable):
                machine = FakeS6000()
                before = machine.machine_time
                machine.clock_writable = writable
                journal: list[dict] = []
                result = sm.set_clock(protocol.Host(machine, ADDRESS), when, record=journal.append, pace=no_pace)
                self.assertEqual((result.confirmed, result.read), (writable, when if writable else before))
                self.assertEqual(len(sent_requests(machine, "set-date-time")), 1)
                self.assertEqual(len(journal), 1)


class FakeMachineTest(unittest.TestCase):
    def test_needs_sync_ignores_polls_after_a_boot_until_it_sees_a_sync(self):
        machine = FakeS6000(needs_sync=True)
        host = protocol.Host(machine, ADDRESS)
        self.assertEqual(sm.current_credits(host), machine.meters[sm.MONEY_METERS["credits"]])
        machine.boot()
        with self.assertRaises(sm.ReadFailed):
            sm.money_meters(host)  # a new poll: the host sends no sync before it
        machine.send(protocol.SYNC_POLL, b"")
        self.assertEqual(sm.current_credits(host), machine.meters[sm.MONEY_METERS["credits"]])

    def test_read_state_reports_the_machine(self):
        machine = FakeS6000()
        reading = sm.read_state(protocol.Host(machine, ADDRESS), extension(machine), pace=no_pace)
        self.assertEqual((reading.credits, reading.cents_per_credit, reading.play_enabled, reading.machine_time),
                         (machine.meters[sm.MONEY_METERS["credits"]], CPC, True, machine.machine_time))
        self.assertEqual(reading.money_meters, money(machine))
        self.assertEqual(reading.extension_status.last_id, machine.extension.last[0])


if __name__ == "__main__":
    unittest.main()
