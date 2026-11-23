"""Test a legacy bonus and an AFT deposit, withdraw ALL cashable credits, then restore the
starting balance. Lifetime meters retain every movement; this is not an undo of accounting.

This S6000 reports that partial cashouts are unsupported (Table 8.2b). Full-balance testing
must therefore be intentional. Flags stay zero: no receipt, ticket, hopper cashout, forced
handpay, or lock request. Restricted/promotional balances are not supported by this test.

The transfers are `sas_machine.deposit` and `cash_out`, the page's own; this test adds its
round-trip policy on top: both directions available, play enabled and the full balance
within the transfer limit before anything moves.

Money instructions are sent once. An uncertain reply or unexpected meter stops the test,
without further money writes, even during restoration. Do not rerun after failure: reconcile
the recorded transaction ID, transfer status and meters first (notes/sas-j10.md, "Money test").
"""

from __future__ import annotations

from collections.abc import Callable

import sas_machine as sm
import sas_protocol as sas
from sas_write_test import StepFailed, WriteTest, acked

CREDITS = 1  # credits added by each route
TRANSFERS_IN_AND_OUT = sm.TO_MACHINE | sm.TO_HOST
LEGACY_BONUS_AWARDED, DEDUCTIBLE = 0x7C, 0


class MoneyTest:
    def __init__(self, t: WriteTest):
        self.t = t
        t.pace()
        state = sm.read_aft_state(t.host, pace=t.pace, observe=t.report)
        for check in sm.transfer_checks(state):
            self.require(check.label, check.ok, check.detail)
        self.require("play enabled", not t.play_disabled())
        self.require("transfers available both ways",
                     int(state.lock["available_transfers"], 16) & TRANSFERS_IN_AND_OUT == TRANSFERS_IN_AND_OUT)
        self.cents_per_credit = state.cents_per_credit
        self.registration = state.registration
        self.start_credits = state.credits
        total_cents = (self.start_credits + 2 * CREDITS) * self.cents_per_credit
        self.require("full balance within transfer limit", total_cents <= state.lock["transfer_limit_cents"],
                     {"cents": total_cents, "limit": state.lock["transfer_limit_cents"]})

    def require(self, label: str, ok: bool, detail: object = "") -> None:
        if not self.t.check(label, ok, detail):
            raise StepFailed(label)

    def meters(self) -> dict[str, int]:
        return self.t.machine(sm.money_meters)

    def check_moved(self, label: str, before: dict[str, int], expected: dict[str, int]) -> None:
        after = self.meters()
        moved = {name: after[name] - before[name] for name in after if after[name] != before[name]}
        self.require(f"{label}: meter changes", moved == expected, moved)

    def record(self, intent: dict) -> None:
        """Report the transfer's identity before it is sent, for recovery."""
        self.t.report({"step": "aft_transfer", **intent,
                       "about": f"Send once: type {intent['type']:02X}, {intent['cents']} cents, "
                                f"ID {intent['transaction_id']}"})

    def legacy_bonus(self) -> None:
        """8A queues the bonus; 7C and the credit/bonus meters confirm payment."""
        before = self.meters()
        deductible = self.t.read("legacy-bonus-meters", 0)["deductible"]
        self.t.report({"step": "legacy_bonus", "credits": CREDITS, "deductible_before": deductible,
                       "about": f"Send once: {CREDITS} legacy bonus credit(s)"})
        self.require("legacy bonus acknowledged",
                     acked(self.t.send("legacy-bonus", CREDITS, DEDUCTIBLE, retry=False)))
        for _ in range(sm.COMPLETION_POLLS):
            if any(code == LEGACY_BONUS_AWARDED for code, _ in self.t.drain()):
                break
        else:
            raise StepFailed("legacy bonus not confirmed; inspect before any further write")
        self.require("last legacy bonus matches", self.t.read("legacy-bonus-win")["bonus"] == CREDITS)
        self.require("legacy bonus meter increased",
                     self.t.read("legacy-bonus-meters", 0)["deductible"] - deductible == CREDITS)
        self.check_moved("legacy bonus", before, {"credits": CREDITS})

    def move(self, kind: int, credits: int) -> None:
        """One transfer through sas_machine; a withdrawal is always the whole balance."""
        before = self.meters()
        self.t.pace()
        if kind == sm.IN_HOUSE_TO_MACHINE:
            outcome = sm.deposit(self.t.host, credits, record=self.record, pace=self.t.pace, observe=self.t.report)
        else:
            outcome = sm.cash_out(self.t.host, record=self.record, pace=self.t.pace, observe=self.t.report)
        self.require("transfer moved", outcome.status == sm.TRANSFER_MOVED, outcome)
        cents = credits * self.cents_per_credit
        self.require("transfer amount", outcome.cents == cents, {"moved": outcome.cents, "expected": cents})
        direction = "in" if kind == sm.IN_HOUSE_TO_MACHINE else "out"
        self.check_moved("AFT", before, {"credits": credits if direction == "in" else -credits,
                                         f"{direction}_cents": cents, f"{direction}_count": 1})

    def round_trip(self) -> None:
        self.legacy_bonus()
        self.move(sm.IN_HOUSE_TO_MACHINE, CREDITS)
        total = self.start_credits + 2 * CREDITS
        self.require("balance before full withdrawal", self.meters()["credits"] == total, total)
        self.move(sm.IN_HOUSE_TO_HOST, total)
        self.require("balance zero before restoration", self.t.read("credits")["value"] == 0)
        if self.start_credits:
            self.move(sm.IN_HOUSE_TO_MACHINE, self.start_credits)
        final = self.t.aft_status()
        self.require("original balance restored",
                     self.meters()["credits"] == self.t.read("credits")["value"] == self.start_credits
                     and final["cashable_cents"] == self.start_credits * self.cents_per_credit
                     and final["restricted_cents"] == final["nonrestricted_cents"] == 0, final)
        self.require("registration unchanged", self.t.registration() == self.registration)
        self.require("play enabled and unlocked", not self.t.play_disabled() and final["lock_status"] == 0xFF)
        self.t.drain()


def run(host: sas.Host, report: Callable[[dict], None]) -> bool:
    t = WriteTest(host, report)
    report({"step": "money_test", "about": "Adds two credits, withdraws ALL, restores the original balance"})
    try:
        t.drain()
        MoneyTest(t).round_trip()
    except (sas.ProtocolError, StepFailed, sm.MachineError, OSError, KeyboardInterrupt) as e:
        t.check("money test stopped", False,
                f"{type(e).__name__}: {e}. No further money writes. Reconcile credits, bonus meters and "
                "`aft-transfer 0xFE 0` with this log before recovery; do not rerun the test.")
        return False
    return True
