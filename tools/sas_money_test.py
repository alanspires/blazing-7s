"""Test a legacy bonus and an AFT deposit, withdraw ALL cashable credits, then restore the
starting balance. Lifetime meters retain every movement; this is not an undo of accounting.

This S6000 reports that partial cashouts are unsupported (Table 8.2b). Full-balance testing
must therefore be intentional. Flags stay zero: no receipt, ticket, hopper cashout, forced
handpay, or lock request. Restricted/promotional balances are not supported by this test.

Money instructions are sent once. An uncertain reply or unexpected meter stops the test,
without further money writes, even during restoration. Do not rerun after failure: reconcile
the recorded transaction ID, transfer status and meters first (notes/sas-j10.md, "Money test").
"""

from __future__ import annotations

from typing import Callable
from uuid import uuid4

import sas_protocol as sas
from sas_tables import DENOMINATIONS
from sas_write_test import StepFailed, WriteTest, acked

CREDITS = 1  # credits added by each route
CURRENT_CREDITS = 0x0C  # Table C-7, credits
IN_HOUSE_IN, IN_HOUSE_IN_COUNT = 0xA0, 0xA1  # cents, transfers
IN_HOUSE_OUT, IN_HOUSE_OUT_COUNT = 0xB8, 0xB9
METERS = (CURRENT_CREDITS, IN_HOUSE_IN, IN_HOUSE_IN_COUNT, IN_HOUSE_OUT, IN_HOUSE_OUT_COUNT)
IN_HOUSE_TO_MACHINE, IN_HOUSE_TO_HOST = 0x00, 0x80  # Table 8.3d
FULL_TRANSFER, PEEK, INTERROGATE = 0x00, 0xFE, 0xFF
TRANSFER_PENDING, TRANSFER_OK = 0x40, 0x00  # Table 8.3e
RECEIPT_PENDING = 0x20
AFT_IN_HOUSE_ENABLED = 0x90  # Table 8.2b, bits 7 and 4
TRANSFERS_IN_AND_OUT = 0x03  # Table 8.2b available transfers, bits 0 and 1
LEGACY_BONUS_AWARDED, DEDUCTIBLE = 0x7C, 0
COMPLETION_POLLS = 40  # bounded, paced status reads; never repeat an initiating command


class MoneyTest:
    def __init__(self, t: WriteTest):
        self.t = t
        info = t.read("info")
        cents = DENOMINATIONS.get(info["denomination"], 0) * 100
        self.require("whole-cent denomination", cents >= 1 and cents == int(cents), cents)
        self.cents_per_credit = int(cents)
        status = t.aft_status()
        self.asset = status["asset"]
        self.require("machine unlocked", status["lock_status"] == 0xFF, status)
        self.require("play enabled", not t.play_disabled())
        self.require("in-house AFT enabled",
                     int(status["aft_status"], 16) & AFT_IN_HOUSE_ENABLED == AFT_IN_HOUSE_ENABLED)
        self.require("transfers available both ways",
                     int(status["available_transfers"], 16) & TRANSFERS_IN_AND_OUT == TRANSFERS_IN_AND_OUT)
        self.require("cashable credits only", status["restricted_cents"] == status["nonrestricted_cents"] == 0)
        self.registration = t.registration()
        self.require("consistent asset number", self.asset != 0 and self.registration["asset"] == self.asset)
        self.key = self.registration["registration_key"].replace(" ", "")
        latest = t.read("aft-transfer", PEEK, 0)
        self.require("no existing transfer or receipt pending",
                     latest["status"] != TRANSFER_PENDING and latest["receipt_status"] != RECEIPT_PENDING)
        self.start = self.meters()
        self.start_credits = self.start[CURRENT_CREDITS]
        total_cents = (self.start_credits + 2 * CREDITS) * self.cents_per_credit
        self.require("full balance within transfer limit", total_cents <= status["transfer_limit_cents"],
                     {"cents": total_cents, "limit": status["transfer_limit_cents"]})
        self.require("credit meters agree before writes",
                     self.start_credits * self.cents_per_credit == status["cashable_cents"]
                     and t.read("credits")["value"] == self.start_credits, self.start_credits)

    def require(self, label: str, ok: bool, detail: object = "") -> None:
        if not self.t.check(label, ok, detail):
            raise StepFailed(label)

    def meters(self) -> dict[int, int]:
        reply = self.t.read("extended-meters", 0, *(f"0x{m:02X}" for m in METERS))
        meters = {item["meter"]: item["value"] for item in reply["items"]}
        self.require("all requested meters present", meters.keys() == set(METERS), meters)
        return meters

    def check_moved(self, label: str, before: dict[int, int], expected: dict[int, int]) -> None:
        after = self.meters()
        moved = {m: after[m] - before[m] for m in METERS if after[m] != before[m]}
        self.require(f"{label}: meter changes", moved == expected, moved)

    def transfer(self, kind: int, credits: int) -> None:
        """Send once, interrogate pending status, and acknowledge completion with a read.
        Exception 69 is optional if status is retrieved first (SAS §8.3)."""
        cents = credits * self.cents_per_credit
        transaction_id = uuid4().hex[:20]  # SAS transaction IDs allow at most 20 ASCII bytes
        self.t.report({"step": "aft_transfer", "transaction_id": transaction_id, "type": kind,
                       "cents": cents, "asset": self.asset,
                       "about": f"Send once: type {kind:02X}, {cents} cents, ID {transaction_id}"})
        reply = self.t.read("aft-transfer", FULL_TRANSFER, 0, kind, cents, 0, 0, 0, self.asset,
                            self.key, transaction_id, "00000000", 0, "", 0, retry=False)
        for attempt in range(COMPLETION_POLLS + 1):
            self.require("transfer accepted", reply.get("status") in (TRANSFER_PENDING, TRANSFER_OK), reply)
            self.require("transfer identity and amounts match",
                         (reply.get("type"), reply.get("asset"), reply.get("transaction_id"),
                          reply.get("cashable_cents"), reply.get("restricted_cents"),
                          reply.get("nonrestricted_cents"))
                         == (kind, self.asset, transaction_id, cents, 0, 0), reply)
            if reply["status"] == TRANSFER_OK:
                self.require("no receipt pending", reply.get("receipt_status") != RECEIPT_PENDING)
                self.t.read("credits")  # different poll acknowledges the final status
                return
            if attempt == COMPLETION_POLLS:
                break
            reply = self.t.read("aft-transfer", INTERROGATE, 0)
        raise StepFailed(f"transfer {transaction_id} still pending; inspect before any further write")

    def legacy_bonus(self) -> None:
        """8A queues the bonus; 7C and the credit/bonus meters confirm payment."""
        before = self.meters()
        deductible = self.t.read("legacy-bonus-meters", 0)["deductible"]
        self.t.report({"step": "legacy_bonus", "credits": CREDITS, "deductible_before": deductible,
                       "about": f"Send once: {CREDITS} legacy bonus credit(s)"})
        self.require("legacy bonus acknowledged",
                     acked(self.t.send("legacy-bonus", CREDITS, DEDUCTIBLE, retry=False)))
        for _ in range(COMPLETION_POLLS):
            if any(code == LEGACY_BONUS_AWARDED for code, _ in self.t.drain()):
                break
        else:
            raise StepFailed("legacy bonus not confirmed; inspect before any further write")
        self.require("last legacy bonus matches", self.t.read("legacy-bonus-win")["bonus"] == CREDITS)
        self.require("legacy bonus meter increased",
                     self.t.read("legacy-bonus-meters", 0)["deductible"] - deductible == CREDITS)
        self.check_moved("legacy bonus", before, {CURRENT_CREDITS: CREDITS})

    def move(self, kind: int, credits: int) -> None:
        before = self.meters()
        self.transfer(kind, credits)
        incoming = kind == IN_HOUSE_TO_MACHINE
        meter, count = (IN_HOUSE_IN, IN_HOUSE_IN_COUNT) if incoming else (IN_HOUSE_OUT, IN_HOUSE_OUT_COUNT)
        self.check_moved("AFT", before, {CURRENT_CREDITS: credits if incoming else -credits,
                                         meter: credits * self.cents_per_credit, count: 1})

    def round_trip(self) -> None:
        self.legacy_bonus()
        self.move(IN_HOUSE_TO_MACHINE, CREDITS)
        total = self.start_credits + 2 * CREDITS
        self.require("balance before full withdrawal", self.meters()[CURRENT_CREDITS] == total, total)
        self.move(IN_HOUSE_TO_HOST, total)
        self.require("balance zero before restoration", self.t.read("credits")["value"] == 0)
        if self.start_credits:
            self.move(IN_HOUSE_TO_MACHINE, self.start_credits)
        final = self.t.aft_status()
        self.require("original balance restored",
                     self.meters()[CURRENT_CREDITS] == self.t.read("credits")["value"] == self.start_credits
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
    except (sas.ProtocolError, StepFailed, OSError, KeyboardInterrupt) as e:
        t.check("money test stopped", False,
                f"{type(e).__name__}: {e}. No further money writes. Reconcile credits, bonus meters and "
                "`aft-transfer 0xFE 0` with this log before recovery; do not rerun the test.")
        return False
    return True
