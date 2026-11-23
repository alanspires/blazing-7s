"""The web control page's journal: one append-only JSON-lines file holding the activity log
and every state-changing command's intent and outcome, kept across restarts.

Each line is one record, a JSON object with

    seq     consecutive integer, assigned by append
    time    UTC ISO 8601 time, assigned by append
    kind    intent, step, outcome, event, spin, profile or page
    source  "page" or "machine"
    submission_id  the page action that caused it, where there is one

and the kind's own fields (plan Implementation Details 5). The journal reads only the fields
it keeps state from:

- `intent` opens an intent; an upload's intent carries `upload_id` (sas_extension.upload).
- `step` is one upload wire send, linked to its open upload by `upload_id`; `op` is the
  extension Op name.
- `outcome` closes the intent whose `seq` it names in `intent`. A money outcome that moved
  credits carries `transfer`: {"direction": "in" | "out", "credits": n}, which the counters
  add up.
- Any record may carry `arm` ({"state": one of ARM_STATES, ...the arm's name, bet, stops
  and extension id}) and `settings` ({setting name: web_state.Setting value}): the newest of
  each is what the journal restores. The worker owns their meaning; the journal only keeps the latest.

append() returns only once the line is on disk (write and fsync). Any failure raises and
leaves the file as it was, so the caller may append again; what a failure means is the
caller's: only a failed intent append proves nothing was sent.

open() scans the file once. A final line without its newline is an append that never
returned, so it is truncated and the truncation is journaled. Any other unreadable line is
corruption nothing explains, and open() refuses the file (JournalCorrupt) rather than skip
evidence. An upload left open with no COMMIT_PROFILE step record never sent COMMIT, the only
step that activates a profile, and staging does not survive a boot
(.rules/firmware-remote-control.md), so the scan closes it as staging_abandoned; nothing is
sent and nothing is resumed.
"""

from __future__ import annotations

import bisect
import fcntl
import json
import os
import threading
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

import sas_extension
from web_state import ArmState, Counters, OutcomeWord

KINDS = frozenset(("intent", "step", "outcome", "event", "spin", "profile", "page"))
SOURCES = frozenset(("page", "machine"))
ARM_STATES = frozenset(state.value for state in ArmState)
TRANSFER_DIRECTIONS = frozenset(("in", "out"))
NO_ARM = MappingProxyType({"state": ArmState.NONE.value})
COMMIT_STEP = sas_extension.Op.COMMIT_PROFILE.name
TORN_TAIL = "journal_torn_tail"
FILE_MODE = 0o600  # intents hold exact request bytes, which include the AFT registration key


class JournalError(Exception):
    pass


class JournalCorrupt(JournalError):
    """A line before the last cannot be read as a journal record."""


@dataclass(frozen=True)
class OpenIntent:
    """An intent with no outcome yet, and for an upload the step records journaled for it."""

    record: dict
    steps: tuple[dict, ...] = ()

    @property
    def seq(self) -> int:
        return self.record["seq"]

    @property
    def upload_id(self) -> str | None:
        return self.record.get("upload_id")

    @property
    def commit_sent(self) -> bool:
        """A COMMIT_PROFILE step was journaled, so COMMIT may have reached the machine."""
        return any(step["op"] == COMMIT_STEP for step in self.steps)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Journal:
    """Use Journal.open. Methods are safe to call from several threads."""

    def __init__(self, path: str, fd: int, *, clock: Callable[[], datetime],
                 write: Callable[[int, bytes], int], fsync: Callable[[int], None]):
        self.path = path
        self._fd = fd
        self._clock, self._write, self._fsync = clock, write, fsync
        self._lock = threading.Lock()
        self._broken: str | None = None
        self._size = 0
        # The line index, in file order: seq, byte offset, byte length and kind of each record.
        self._seqs: list[int] = []
        self._offsets: list[int] = []
        self._lengths: list[int] = []
        self._kinds: list[str] = []
        self._open: dict[int, OpenIntent] = {}
        self._uploads: dict[str, int] = {}  # upload_id -> seq of its open intent
        self._counters = Counters()
        self._arm: dict = dict(NO_ARM)
        self._settings: dict[str, str] = {}

    @classmethod
    def open(cls, path: str, *, clock: Callable[[], datetime] = _now,
             write: Callable[[int, bytes], int] = os.write,
             fsync: Callable[[int], None] = os.fsync) -> Journal:
        """Open the journal at path, creating it if it does not exist. Its directory must
        exist. `clock` returns the current time as an aware datetime; `write` and `fsync` are
        os.write and os.fsync, injectable to test failures."""
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, FILE_MODE)
        journal = cls(path, fd, clock=clock, write=write, fsync=fsync)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JournalError(f"{path}: the journal is already open") from None
            # Every open, not only the creating one: a creation whose sync failed is
            # indistinguishable from an existing file afterwards.
            fsync(fd)
            directory = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
            try:
                fsync(directory)
            finally:
                os.close(directory)
            journal._scan()
        except BaseException:
            os.close(fd)
            raise
        return journal

    def close(self) -> None:
        with self._lock:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1

    def append(self, record: dict) -> dict:
        """Write one record durably and return it as stored, with its seq and time. The
        record gives kind, source and its own fields. A record the journal's state rules out
        raises ValueError or TypeError and a write or fsync failure raises OSError; either way
        nothing was added."""
        with self._lock:
            if self._broken is not None:
                raise JournalError(f"{self.path}: unusable after a failed append: {self._broken}")
            if "seq" in record or "time" in record:
                raise ValueError("append assigns seq and time")
            stored = {"seq": self._seqs[-1] + 1 if self._seqs else 1,
                      "time": self._clock().astimezone(timezone.utc).isoformat(timespec="milliseconds"),
                      **record}
            self._check(stored)
            line = json.dumps(stored, separators=(",", ":"), allow_nan=False).encode() + b"\n"
            self._write_durably(line)
            self._index(stored, self._size, len(line))
            self._size += len(line)
            self._fold(stored)
            return dict(stored)

    def entries(self, *, limit: int, before: int | None = None,
                kinds: Collection[str] | None = None) -> list[dict]:
        """Up to `limit` records older than seq `before` (or the newest), newest first,
        only of `kinds` when given. Reads only the lines it returns."""
        with self._lock:
            end = len(self._seqs) if before is None else bisect.bisect_left(self._seqs, before)
            found = []
            for i in range(end - 1, -1, -1):
                if len(found) == limit:
                    break
                if kinds is None or self._kinds[i] in kinds:
                    found.append(self._read(i))
            return found

    def counters(self) -> Counters:
        with self._lock:
            return self._counters

    def open_intents(self) -> list[OpenIntent]:
        """Intents with no outcome, oldest first."""
        with self._lock:
            return [self._open[seq] for seq in sorted(self._open)]

    def arm(self) -> dict:
        """The newest journaled arm, {"state": "none"} if none was journaled."""
        with self._lock:
            return dict(self._arm)

    def settings(self) -> dict[str, str]:
        """Each journaled setting's newest state, a web_state.Setting value."""
        with self._lock:
            return dict(self._settings)

    # The scan

    def _scan(self) -> None:
        offset = 0
        torn = 0
        with os.fdopen(os.dup(self._fd), "rb") as f:
            for line in f:
                if not line.endswith(b"\n"):
                    torn = len(line)
                    break
                try:
                    record = json.loads(line)
                    self._check_stored(record)
                    self._check(record)
                except (ValueError, TypeError, KeyError) as e:
                    raise JournalCorrupt(f"{self.path}: record {len(self._seqs) + 1} at byte "
                                         f"{offset} is unreadable: {e}") from e
                self._index(record, offset, len(line))
                self._fold(record)
                offset += len(line)
        self._size = offset
        if torn:
            os.ftruncate(self._fd, offset)
            self.append({"kind": "page", "source": "page", "event": TORN_TAIL, "dropped_bytes": torn})
        for intent in list(self._open.values()):
            if intent.upload_id is not None and not intent.commit_sent:
                outcome = {"kind": "outcome", "source": "page", "intent": intent.seq,
                           "result": OutcomeWord.STAGING_ABANDONED.value, "upload_id": intent.upload_id}
                if "submission_id" in intent.record:
                    outcome["submission_id"] = intent.record["submission_id"]
                self.append(outcome)

    def _check_stored(self, record: dict) -> None:
        """What append assigns: an object with the next seq and a UTC time."""
        if not isinstance(record, dict):
            raise TypeError("not a JSON object")
        if not isinstance(record.get("seq"), int) or (self._seqs and record["seq"] <= self._seqs[-1]):
            raise ValueError(f"seq {record.get('seq')!r} does not follow {self._seqs[-1:]}")
        if datetime.fromisoformat(record["time"]).utcoffset() is None:
            raise ValueError(f"time {record['time']!r} has no UTC offset")

    # State shared by the scan and append

    def _check(self, record: dict) -> None:
        """Raise ValueError (TypeError for a wrong field type) unless the current state
        admits record."""
        kind = record.get("kind")
        if kind not in KINDS:
            raise ValueError(f"kind {kind!r}")
        if record.get("source") not in SOURCES:
            raise ValueError(f"source {record.get('source')!r}")
        if not isinstance(record.get("submission_id", ""), str):
            raise TypeError("submission_id is not a string")
        if "arm" in record and (not isinstance(record["arm"], dict)
                                or record["arm"].get("state") not in ARM_STATES):
            raise ValueError(f"arm {record['arm']!r}")
        if "settings" in record and not (
                isinstance(record["settings"], dict)
                and all(isinstance(v, str) for v in record["settings"].values())):
            raise ValueError(f"settings {record['settings']!r}")
        if kind == "intent" and record.get("upload_id") in self._uploads:
            raise ValueError(f"upload {record['upload_id']} is already open")
        if kind == "step":
            upload = self._uploads.get(record.get("upload_id"))
            if upload is None:
                raise ValueError(f"step for upload {record.get('upload_id')!r}, which is not open")
            if record.get("intent", upload) != upload or not isinstance(record.get("op"), str):
                raise ValueError(f"step {record!r} does not match open upload intent {upload}")
        if kind == "outcome":
            if record.get("intent") not in self._open:
                raise ValueError(f"outcome for intent {record.get('intent')!r}, which is not open")
            transfer = record.get("transfer")
            if transfer is not None and not (
                    isinstance(transfer, dict) and transfer.get("direction") in TRANSFER_DIRECTIONS
                    and isinstance(transfer.get("credits"), int) and transfer["credits"] >= 0):
                raise ValueError(f"transfer {transfer!r}")

    def _fold(self, record: dict) -> None:
        kind = record["kind"]
        if kind == "intent":
            self._open[record["seq"]] = OpenIntent(record)
            if "upload_id" in record:
                self._uploads[record["upload_id"]] = record["seq"]
        elif kind == "step":
            seq = self._uploads[record["upload_id"]]
            intent = self._open[seq]
            self._open[seq] = OpenIntent(intent.record, (*intent.steps, record))
        elif kind == "outcome":
            intent = self._open.pop(record["intent"])
            if intent.upload_id is not None:
                del self._uploads[intent.upload_id]
            transfer = record.get("transfer")
            if transfer is not None:
                c = self._counters
                if transfer["direction"] == "in":
                    self._counters = Counters(c.credits_added + transfer["credits"], c.top_ups + 1,
                                              c.credits_cashed_out, c.cash_outs)
                else:
                    self._counters = Counters(c.credits_added, c.top_ups,
                                              c.credits_cashed_out + transfer["credits"], c.cash_outs + 1)
        if "arm" in record:
            self._arm = dict(record["arm"])
        self._settings.update(record.get("settings", {}))

    # The file

    def _index(self, record: dict, offset: int, length: int) -> None:
        self._seqs.append(record["seq"])
        self._offsets.append(offset)
        self._lengths.append(length)
        self._kinds.append(record["kind"])

    def _read(self, i: int) -> dict:
        data = os.pread(self._fd, self._lengths[i], self._offsets[i])
        if len(data) != self._lengths[i]:
            raise JournalError(f"{self.path}: record {self._seqs[i]} is cut short on disk")
        return json.loads(data)

    def _write_durably(self, line: bytes) -> None:
        """Write and fsync line at the end of the file. On failure, cut the file back to its
        last durable end before raising, so a retried append does not follow a torn line."""
        try:
            view = memoryview(line)
            while view:
                written = self._write(self._fd, view)
                if written <= 0:
                    raise OSError(f"{self.path}: write wrote nothing")
                view = view[written:]
            self._fsync(self._fd)
        except BaseException:
            try:
                os.ftruncate(self._fd, self._size)
            except OSError as e:
                self._broken = f"cutting back the failed line: {e}"
            raise
