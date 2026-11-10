import unittest
from functools import partial

import sas_commands as sc
import sas_money_test as money
import sas_protocol as protocol
from sas_testing import FakeS6000, run_machine_test

run = partial(run_machine_test, money.run)


def money_requests(machine: FakeS6000) -> list[tuple[str, dict]]:
    requests = []
    for address, rest in machine.sent:
        if address != machine.ADDRESS or not rest:
            continue
        command = sc.COMMANDS[rest[0]]
        if command.name not in ("legacy-bonus", "aft-transfer"):
            continue
        values = dict(sc.decode(command.request, protocol.check_crc(bytes((address,)) + rest)[2:],
                                length_byte=command.request_length))
        if command.name == "legacy-bonus" or values["transfer_code"] == money.FULL_TRANSFER:
            requests.append((command.name, values))
    return requests


class LostMoneyReply(FakeS6000):
    def __init__(self, drop_at: int):
        super().__init__()
        self.drop_at = drop_at
        self.dropped = False

    def send(self, first, rest):
        super().send(first, rest)
        if not self.dropped and len(money_requests(self)) == self.drop_at:
            self.replies.clear()
            self.dropped = True


class PendingTransfer(FakeS6000):
    def _aft_transfer(self, values):
        reply = super()._aft_transfer(values)
        if values["transfer_code"] == money.INTERROGATE:
            return reply[:1] + (money.TRANSFER_PENDING,) + reply[2:]
        return reply


class ImmediateTransfer(FakeS6000):
    def _aft_transfer(self, values):
        reply = super()._aft_transfer(values)
        if values["transfer_code"] == money.FULL_TRANSFER:
            self.pending.clear()
            return self.last_transfer
        return reply


class LastPollWithdrawal(FakeS6000):
    remaining = 0

    def _aft_transfer(self, values):
        reply = super()._aft_transfer(values)
        if values["transfer_code"] == money.FULL_TRANSFER and values["type"] == money.IN_HOUSE_TO_HOST:
            self.remaining = money.COMPLETION_POLLS
        elif values["transfer_code"] == money.INTERROGATE and self.remaining:
            self.remaining -= 1
            if self.remaining:
                return reply[:1] + (money.TRANSFER_PENDING,) + reply[2:]
        return reply


class InterruptedWithdrawal(FakeS6000):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def send(self, first, rest):
        super().send(first, rest)
        if len(money_requests(self)) == 3:
            raise self.error


class WrongTransferId(FakeS6000):
    def _aft_transfer(self, values):
        reply = super()._aft_transfer(values)
        if values["transfer_code"] == money.INTERROGATE:
            return reply[:9] + ("another-transaction",) + reply[10:]
        return reply


class UnexpectedCredits(FakeS6000):
    def _legacy_bonus(self, values):
        reply = super()._legacy_bonus(values)
        self.meters[money.CURRENT_CREDITS] += money.CREDITS
        return reply


class MoneyTestTest(unittest.TestCase):
    def test_full_balance_round_trip_restores_credits_but_keeps_accounting(self):
        for machine in (FakeS6000(), ImmediateTransfer(), LastPollWithdrawal()):
            with self.subTest(machine=type(machine).__name__):
                start = machine.meters[money.CURRENT_CREDITS]
                deductible = machine.legacy_deductible
                ok, records = run(machine)
                self.assertEqual([r for r in records if "check" in r and not r["ok"]], [])
                self.assertTrue(ok)
                self.assertEqual(machine.meters[money.CURRENT_CREDITS], start)
                self.assertEqual(machine.legacy_deductible - deductible, money.CREDITS)
                transfers = [v for name, v in money_requests(machine) if name == "aft-transfer"]
                amounts = [(money.IN_HOUSE_TO_MACHINE, money.CREDITS),
                           (money.IN_HOUSE_TO_HOST, start + 2 * money.CREDITS),
                           (money.IN_HOUSE_TO_MACHINE, start)]
                self.assertEqual([(v["type"], v["cashable_cents"]) for v in transfers],
                                 [(kind, credits * machine.CENTS_PER_CREDIT) for kind, credits in amounts])
                self.assertEqual(len({v["transaction_id"] for v in transfers}), len(transfers))
                for v in transfers:
                    self.assertLessEqual(len(v["transaction_id"]), 20)  # SAS wire limit
                    self.assertEqual(int(v["flags"], 16), 0)
                    self.assertEqual((v["restricted_cents"], v["nonrestricted_cents"]), (0, 0))
                for kind, meter, count in ((money.IN_HOUSE_TO_MACHINE, money.IN_HOUSE_IN, money.IN_HOUSE_IN_COUNT),
                                           (money.IN_HOUSE_TO_HOST, money.IN_HOUSE_OUT, money.IN_HOUSE_OUT_COUNT)):
                    moved = [credits for direction, credits in amounts if direction == kind]
                    self.assertEqual(machine.meters[meter], sum(moved) * machine.CENTS_PER_CREDIT)
                    self.assertEqual(machine.meters[count], len(moved))

    def test_uncertain_money_reply_never_repeats_or_starts_another_write(self):
        for drop_at in range(1, 5):  # legacy, deposit, full withdrawal, restoration
            with self.subTest(drop_at=drop_at):
                machine = LostMoneyReply(drop_at)
                start = machine.meters[money.CURRENT_CREDITS]
                ok, _ = run(machine)
                self.assertFalse(ok)
                self.assertTrue(machine.dropped)
                self.assertEqual(len(money_requests(machine)), drop_at)
                expected = (start + money.CREDITS, start + 2 * money.CREDITS, 0, start)
                self.assertEqual(machine.meters[money.CURRENT_CREDITS], expected[drop_at - 1])

    def test_interrupted_withdrawal_leaves_its_recovery_identity_in_the_log(self):
        for error in (OSError(), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                machine = InterruptedWithdrawal(error)
                ok, records = run(machine)
                self.assertFalse(ok)
                self.assertEqual(machine.meters[money.CURRENT_CREDITS], 0)
                requests = money_requests(machine)
                self.assertEqual(len(requests), 3)
                withdrawal = requests[-1][1]
                intents = [r for r in records if "transaction_id" in r]
                self.assertEqual(intents[-1]["transaction_id"], withdrawal["transaction_id"])
                self.assertEqual(intents[-1]["type"], withdrawal["type"])
                self.assertEqual(intents[-1]["cents"], withdrawal["cashable_cents"])

    def test_refused_full_withdrawal_stops_with_only_test_credits_added(self):
        machine = FakeS6000(refuses_transfers_out=True)
        start = machine.meters[money.CURRENT_CREDITS]
        ok, _ = run(machine)
        self.assertFalse(ok)
        self.assertEqual(machine.meters[money.CURRENT_CREDITS], start + 2 * money.CREDITS)
        self.assertEqual(machine.meters[money.IN_HOUSE_OUT], 0)
        self.assertEqual(len(money_requests(machine)), 3)

    def test_pending_or_mismatched_transfer_stops_before_withdrawal(self):
        for machine in (PendingTransfer(), WrongTransferId()):
            with self.subTest(machine=type(machine).__name__):
                ok, _ = run(machine)
                self.assertFalse(ok)
                self.assertEqual(len(money_requests(machine)), 2)
                self.assertEqual(machine.meters[money.IN_HOUSE_OUT], 0)

    def test_existing_transfer_or_disabled_play_prevents_all_money_writes(self):
        pending, disabled = FakeS6000(), FakeS6000()
        pending.last_transfer = (0, money.TRANSFER_PENDING, 0xFF)
        disabled.game_enabled = False
        for machine in (pending, disabled):
            with self.subTest(machine=machine):
                ok, _ = run(machine)
                self.assertFalse(ok)
                self.assertEqual(money_requests(machine), [])

    def test_unexpected_credit_change_stops_before_another_money_write(self):
        machine = UnexpectedCredits()
        ok, _ = run(machine)
        self.assertFalse(ok)
        self.assertEqual(len(money_requests(machine)), 1)


if __name__ == "__main__":
    unittest.main()
