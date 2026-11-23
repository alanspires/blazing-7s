"""web_journal: durable appends, the recovery scan, open intents, restored states and paging."""

from __future__ import annotations

import errno
import os
import stat
import unittest
from datetime import datetime, timedelta, timezone

import web_journal as wj
from sas_testing import TempDirTest
from web_state import Counters, OutcomeWord, Setting

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
UPLOAD_ID = "0123456789abcdef0123"


def event(n: int, **fields) -> dict:
    return {"kind": "event", "source": "machine", "event": "door", "n": n, **fields}


def deposit_intent(submission: str) -> dict:
    return {"kind": "intent", "source": "page", "submission_id": submission, "op": "deposit",
            "transaction_id": submission * 2, "cents": 500, "request_hex": "0172"}


def upload_intent() -> dict:
    # The shape sas_extension.upload records, as the worker appends it.
    return {"kind": "intent", "source": "page", "submission_id": "s-upload", "op": "upload",
            "upload_id": UPLOAD_ID, "profile_crc": 0x1234ABCD, "length": 252,
            "steps": [{"op": "BEGIN_PROFILE", "id": 7}, {"op": "PROFILE_CHUNK", "id": 8},
                      {"op": wj.COMMIT_STEP, "id": 9}],
            "previous": {"active": False, "profile_crc": 0, "generation": 3}}


def step(op: str, id: int, **fields) -> dict:
    return {"kind": "step", "source": "page", "upload_id": UPLOAD_ID, "op": op, "id": id,
            "request_hex": "01e0", **fields}


class Clock:
    """A new second for each record."""

    def __init__(self):
        self.times: list[datetime] = []

    def __call__(self) -> datetime:
        self.times.append(START + timedelta(seconds=len(self.times)))
        return self.times[-1]


class Faults:
    """os.write and os.fsync that fail as armed: a write that stops halfway and then fails,
    or an fsync that fails, each once."""

    def __init__(self):
        self.torn_write = False
        self.failing_fsync = False
        self.synced: list[int] = []

    def write(self, fd: int, data) -> int:
        if self.torn_write:
            self.torn_write = False
            os.write(fd, bytes(data[:len(data) // 2]))
            raise OSError(errno.ENOSPC, "injected: disk full")
        return os.write(fd, data)

    def fsync(self, fd: int) -> None:
        if self.failing_fsync:
            self.failing_fsync = False
            raise OSError(errno.EIO, "injected: fsync failed")
        self.synced.append(os.fstat(fd).st_mode)
        os.fsync(fd)


class JournalTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.path = str(self.dir / "journal.jsonl")
        self.clock = Clock()
        self.faults = Faults()

    def open(self) -> wj.Journal:
        journal = wj.Journal.open(self.path, clock=self.clock, write=self.faults.write,
                                  fsync=self.faults.fsync)
        self.addCleanup(journal.close)
        return journal

    def reopen(self, journal: wj.Journal) -> wj.Journal:
        journal.close()
        return self.open()


class DurabilityTest(JournalTest):
    def test_creation_syncs_the_file_and_its_directory_entry(self):
        self.open()
        kinds = [stat.S_IFMT(mode) for mode in self.faults.synced]
        self.assertIn(stat.S_IFREG, kinds)
        self.assertIn(stat.S_IFDIR, kinds)

    def test_records_survive_reopening_in_order(self):
        journal = self.open()
        written = [journal.append(event(n)) for n in range(3)]
        journal = self.reopen(journal)
        self.assertEqual(journal.entries(limit=10), written[::-1])
        self.assertEqual([r["seq"] for r in written], [1, 2, 3])
        self.assertEqual([datetime.fromisoformat(r["time"]) for r in written], self.clock.times)
        self.assertEqual(journal.append(event(3))["seq"], 4)

    def test_failed_write_or_fsync_raises_and_adds_nothing(self):
        for fault in ("torn_write", "failing_fsync"):
            with self.subTest(fault):
                journal = self.open()
                before = journal.entries(limit=100)
                setattr(self.faults, fault, True)
                with self.assertRaises(OSError):
                    journal.append(deposit_intent("s1"))
                self.assertEqual(journal.open_intents(), [])
                self.assertEqual(journal.entries(limit=100), before)
                # The caller may append again; the failed line left nothing behind.
                retried = journal.append(event(0))
                journal = self.reopen(journal)
                self.assertEqual(journal.entries(limit=100), [retried, *before])
                self.assertEqual(journal.open_intents(), [])
                journal.close()

    def test_torn_final_line_is_truncated_and_journaled(self):
        journal = self.open()
        kept = journal.append(event(0))
        journal.close()
        torn = b'{"seq":2,"time":"2026-01-01T00:00:09'
        with open(self.path, "ab") as f:
            f.write(torn)
        journal = self.open()
        note, *rest = journal.entries(limit=10)
        self.assertEqual(rest, [kept])
        self.assertEqual((note["kind"], note["event"], note["dropped_bytes"]),
                         ("page", wj.TORN_TAIL, len(torn)))
        journal.append(event(1))
        self.assertEqual(len(self.reopen(journal).entries(limit=10)), 3)

    def test_unreadable_interior_line_refuses_to_open(self):
        journal = self.open()
        journal.append(event(0))
        journal.append(event(1))
        journal.close()
        with open(self.path, "rb") as f:
            first, second = f.read().splitlines(keepends=True)
        with open(self.path, "wb") as f:
            f.write(first[:-5] + b"\n" + second)
        with self.assertRaises(wj.JournalCorrupt):
            self.open()

    def test_a_second_open_is_refused_while_the_journal_is_open(self):
        self.open()
        with self.assertRaises(wj.JournalError):
            self.open()


class IntentTest(JournalTest):
    def test_outcome_closes_only_its_intent_across_reopening(self):
        journal = self.open()
        first = journal.append(deposit_intent("s1"))
        second = journal.append(deposit_intent("s2"))
        journal.append({"kind": "outcome", "source": "page", "intent": first["seq"],
                        "submission_id": "s1", "result": "nothing_changed"})
        journal = self.reopen(journal)
        self.assertEqual([i.record for i in journal.open_intents()], [second])
        with self.assertRaises(ValueError):
            journal.append({"kind": "outcome", "source": "page", "intent": first["seq"],
                            "result": "happened"})
        self.assertEqual(len(journal.entries(limit=10)), 3)

    def test_upload_without_a_commit_step_closes_as_staging_abandoned_on_reopen(self):
        journal = self.open()
        intent = journal.append(upload_intent())
        journal.append(step("BEGIN_PROFILE", 7))
        journal.append(step("BEGIN_PROFILE", 7, result="acked"))
        journal.append(step("PROFILE_CHUNK", 8))
        journal = self.reopen(journal)
        self.assertEqual(journal.open_intents(), [])
        closed = journal.entries(limit=1)[0]
        self.assertEqual((closed["kind"], closed["intent"], closed["result"], closed["submission_id"]),
                         ("outcome", intent["seq"], OutcomeWord.STAGING_ABANDONED.value, "s-upload"))
        self.assertEqual(self.reopen(journal).entries(limit=1), [closed])

    def test_upload_with_a_commit_step_stays_open_with_its_steps(self):
        journal = self.open()
        intent = journal.append(upload_intent())
        steps = [journal.append(step("BEGIN_PROFILE", 7)), journal.append(step("PROFILE_CHUNK", 8)),
                 journal.append(step(wj.COMMIT_STEP, 9))]
        journal = self.reopen(journal)
        (open_upload,) = journal.open_intents()
        self.assertEqual((open_upload.record, open_upload.upload_id, open_upload.steps),
                         (intent, UPLOAD_ID, tuple(steps)))
        self.assertTrue(open_upload.commit_sent)

    def test_counters_add_up_moved_credits_across_reopening(self):
        journal = self.open()
        moves = [("in", 5000), ("in", 100), ("out", 5100), (None, 0)]
        for n, (direction, credits) in enumerate(moves):
            intent = journal.append(deposit_intent(f"s{n}"))
            outcome = {"kind": "outcome", "source": "page", "intent": intent["seq"], "result": "happened"}
            if direction is not None:
                outcome["transfer"] = {"direction": direction, "credits": credits}
            journal.append(outcome)
        expected = Counters(credits_added=5100, top_ups=2, credits_cashed_out=5100, cash_outs=1)
        self.assertEqual(journal.counters(), expected)
        self.assertEqual(self.reopen(journal).counters(), expected)


class RestoredStateTest(JournalTest):
    def test_arm_and_setting_states_are_restored(self):
        journal = self.open()
        self.assertEqual((journal.arm(), journal.settings()), (wj.NO_ARM, {}))
        armed = {"state": "armed", "name": "3 Blazing 7s", "bet": 3, "stops": [1, 2, 3], "id": 12}
        journal.append(event(0, arm=armed, settings={"sound": "on", "bills": "on"}))
        journal.append(event(1, settings={"sound": Setting.UNKNOWN.value}))
        journal = self.reopen(journal)
        self.assertEqual(journal.arm(), armed)
        self.assertEqual(journal.settings(), {"sound": Setting.UNKNOWN.value, "bills": "on"})
        held = {**armed, "state": "held"}
        journal.append(event(2, arm=held, settings={"sound": "off"}))
        journal = self.reopen(journal)
        self.assertEqual((journal.arm(), journal.settings()), (held, {"sound": "off", "bills": "on"}))


class PagingTest(JournalTest):
    def test_entries_page_newest_first(self):
        journal = self.open()
        for n in range(10):
            journal.append(event(n) if n % 2 else {"kind": "spin", "source": "machine", "n": n})
        journal = self.reopen(journal)
        pages, before = [], None
        while True:
            page = journal.entries(limit=4, before=before)
            if not page:
                break
            pages.append([r["seq"] for r in page])
            before = page[-1]["seq"]
        self.assertEqual(pages, [[10, 9, 8, 7], [6, 5, 4, 3], [2, 1]])
        self.assertEqual([r["seq"] for r in journal.entries(limit=3, before=8, kinds={"event"})],
                         [6, 4, 2])


if __name__ == "__main__":
    unittest.main()
