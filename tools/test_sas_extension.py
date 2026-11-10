from __future__ import annotations

import hashlib
import json
import os
import shlex
import struct
import subprocess
import sys
import unittest
import zlib
from dataclasses import replace
from pathlib import Path
from unittest import mock

import sas_extension as se
import sas_profile as sp
import sas_protocol as protocol
from sas_testing import FIXTURES, REPO, FakeLine, TempDirTest, read_jsonl, run_cli

ADDRESS = 1
# firmware/INTEGRATION.md, "Wire protocol v1": restated here so the fake checks the wire
# against the contract rather than against sas_extension's own encoding.
POLL = 0xE0
MAGIC = b"S6RE"
WIRE_VERSION = 1
INFO, STATUS, READ = 0, 1, 2
BEGIN, CHUNK, COMMIT, RESTORE = 16, 17, 18, 19
ARM, CANCEL, SPIN, POSITION, JOG = 32, 33, 34, 35, 36
QUERIES = (INFO, STATUS, READ)
OK, ACCEPTED, BAD_ID, BUSY, INVALID_PROFILE, INCOMPLETE = 0, 1, 4, 5, 7, 8
MAX_PAYLOAD = 64
PROFILE_CAP, OUTCOME_CAP, SPIN_CAP, POSITION_CAP, JOG_CAP = 0x01, 0x02, 0x04, 0x08, 0x10
ALL_CAPS = PROFILE_CAP | OUTCOME_CAP | SPIN_CAP | POSITION_CAP | JOG_CAP
# firmware/src/fw.h: the first firmware's INFO limits.
LIMITS = {"max_chunk": 62, "max_lines": 5, "max_bet_per_line": 3, "max_total_bet": 15,
          "stops": sp.STOPS, "max_award": 1500}


class FakeExtension(FakeLine):
    """A machine whose extension answers its long poll per the wire contract, keeping the
    state the CLI reads back. A lost reply (`silent` ops) still lets the machine act, as a
    reply lost on the line would; `tamper` rewrites reply data after the command byte.
    `failure` (op, exception, stage) lets the machine act on that op, then raises the
    exception from the host's write ("send") or from its wait for the reply ("read")."""

    def __init__(self, capabilities: int = ALL_CAPS, last_id: int = 41, **limits: int):
        super().__init__()
        self.capabilities = capabilities
        self.limits = LIMITS | limits
        self.last = (last_id, ARM, b"")  # id, op and payload of the latest mutation
        self.last_status = OK
        self.fault = False
        self.active: bytes | None = None
        self.generation = 3
        self.profile_crc = 0
        self.staged: tuple[int, bytearray, set[int]] | None = None
        self.action_status = ACCEPTED
        self.corrupt_commit = False
        self.silent: set[int] = set()
        self.tamper = None
        self.failure: tuple[int, BaseException, str] | None = None
        self._read_failure: BaseException | None = None
        self.requests: list[tuple[int, int, bytes]] = []
        self.executed: list[int] = []

    @property
    def next_id(self) -> int:
        return self.last[0] + 1

    def mutations(self) -> list[tuple[int, int, bytes]]:
        return [r for r in self.requests if r[0] not in QUERIES]

    def send(self, first, rest):
        super().send(first, rest)
        if first == protocol.SYNC_POLL:
            return
        message = protocol.check_crc(bytes((first,)) + rest)
        assert message[:2] == bytes((ADDRESS, POLL)), message.hex(" ")
        length, body = message[2], message[3:]
        assert length == len(body) and body[:4] == MAGIC and body[4] == WIRE_VERSION, message.hex(" ")
        op, id, payload = body[5], int.from_bytes(body[6:14], "little"), body[14:]
        assert len(payload) <= MAX_PAYLOAD, message.hex(" ")
        self.requests.append((op, id, payload))
        status, data = self.answer(op, id, payload)
        reply = MAGIC + bytes((WIRE_VERSION, op, status)) + id.to_bytes(8, "little") + data
        framed = bytes((len(reply),)) + reply
        if self.tamper:
            framed = self.tamper(framed)
        if self.failure is not None and self.failure[0] == op:
            _, exception, stage = self.failure
            self.failure = None
            if stage == "send":
                raise exception
            self._read_failure = exception
        elif op not in self.silent:
            self.replies.append(protocol.with_crc(bytes((ADDRESS, POLL)) + framed))

    def read_message(self, timeout):
        if self._read_failure is not None:
            exception, self._read_failure = self._read_failure, None
            raise exception
        return super().read_message(timeout)

    def answer(self, op: int, id: int, payload: bytes) -> tuple[int, bytes]:
        limits = self.limits
        if op == INFO:
            return OK, struct.pack("<IQHBBBBBI", self.capabilities, 0 if self.fault else self.next_id,
                                   sp.PROFILE_SIZE, limits["max_chunk"], limits["max_lines"],
                                   limits["max_bet_per_line"], limits["max_total_bet"], limits["stops"],
                                   limits["max_award"])
        if op == STATUS:
            return OK, struct.pack("<QBBBIIB", self.last[0], self.last[1], self.last_status,
                                   self.active is not None, self.generation, self.profile_crc, self.fault)
        if op == READ:
            offset, count = struct.unpack("<HB", payload)
            assert count <= MAX_PAYLOAD and offset + count <= sp.PROFILE_SIZE, (offset, count)
            return (INCOMPLETE, b"") if self.active is None else (OK, self.active[offset:offset + count])
        if id == self.last[0] and (op, payload) == self.last[1:]:
            return self.last_status, b""  # the recorded answer; nothing happens again
        if id <= self.last[0]:
            return BAD_ID, b""
        self.last = (id, op, payload)
        self.executed.append(op)
        self.last_status = self.execute(op, payload)
        return self.last_status, b""

    def execute(self, op: int, payload: bytes) -> int:
        if op == BEGIN:
            size, crc = struct.unpack("<HI", payload)
            assert size == sp.PROFILE_SIZE, size
            self.staged = (crc, bytearray(size), set())
            return OK
        if op == CHUNK:
            offset, data = int.from_bytes(payload[:2], "little"), payload[2:]
            assert len(data) <= self.limits["max_chunk"], len(data)
            _, buffer, covered = self.staged
            buffer[offset:offset + len(data)] = data
            covered.update(range(offset, offset + len(data)))
            return OK
        if op == COMMIT:
            crc, buffer, covered = self.staged
            if len(covered) != len(buffer):
                return INCOMPLETE
            if zlib.crc32(buffer) != crc:
                return INVALID_PROFILE
            if self.corrupt_commit:
                buffer[-1] ^= 1
            self.active, self.profile_crc, self.staged = bytes(buffer), crc, None
            self.generation += 1
            return OK
        if op == RESTORE:
            self.active, self.profile_crc = None, 0
            self.generation += 1
            return OK
        return self.action_status


def run_json(argv: list[str], line: FakeLine) -> tuple[int, dict]:
    code, out, _ = run_cli(["--json", *argv], line)
    return code, json.loads(out)


def variant() -> sp.Profile:
    """A valid profile other than stock."""
    stock = sp.stock_profile()
    weights = [list(row) for row in stock.weights]
    weights[0][0] = weights[0][0] + 1 if weights[0][0] < sp.BYTE_MAX else 1
    return replace(stock, weights=tuple(tuple(row) for row in weights))


class ProfileFileTest(TempDirTest):
    def profile_file(self, profile: sp.Profile) -> str:
        path = self.dir / "profile.json"
        sp.write_json(profile, path)
        return str(path)


class QueryTest(unittest.TestCase):
    def test_info_request_is_the_framed_query_and_reports_the_limits(self):
        machine = FakeExtension()
        code, result = run_json(["extension-info"], machine)
        self.assertEqual(code, 0)
        request = MAGIC + bytes((WIRE_VERSION, INFO)) + bytes(8)
        self.assertEqual(machine.messages()[-1],
                         protocol.with_crc(bytes((ADDRESS, POLL, len(request))) + request))
        reply = result["reply"]
        self.assertEqual({name: reply[name] for name in LIMITS}, LIMITS)
        self.assertEqual((reply["next_id"], int(reply["capabilities"], 16)), (machine.next_id, ALL_CAPS))

    def test_status_reports_a_pending_request_and_the_active_profile(self):
        machine = FakeExtension()
        machine.last_status, machine.active, machine.profile_crc = ACCEPTED, bytes(sp.PROFILE_SIZE), 0x1234ABCD
        code, result = run_json(["extension-status"], machine)
        self.assertEqual(code, 0)
        reply = result["reply"]
        self.assertEqual((reply["last_id"], reply["pending"], reply["active"], reply["generation"],
                          int(reply["profile_crc"], 16), reply["fault"]),
                         (machine.last[0], True, "profile", machine.generation, machine.profile_crc, False))

    def test_replies_that_do_not_answer_the_request_are_rejected(self):
        tampers = {
            "length byte": lambda d: bytes((d[0] + 1,)) + d[1:],
            "magic": d_replace(1, b"S6RF"),
            "version": d_replace(5, bytes((WIRE_VERSION + 1,))),
            "op": lambda d: d[:6] + bytes((d[6] ^ 1,)) + d[7:],
            "unknown status": d_replace(7, b"\xee"),
            "id": lambda d: d[:8] + bytes((d[8] ^ 1,)) + d[9:],
            "extra payload": lambda d: bytes((d[0] + 1,)) + d[1:] + b"\x00",
            "short payload": lambda d: bytes((d[0] - 1,)) + d[1:-1],
        }
        for name, tamper in tampers.items():
            with self.subTest(name):
                machine = FakeExtension()
                machine.tamper = tamper
                code, result = run_json(["extension-info"], machine)
                self.assertEqual(code, 1)
                self.assertEqual(result["error_type"], se.BadReply.__name__)

    def test_private_poll_is_not_a_catalogue_command(self):
        with self.assertRaises(SystemExit) as exit_:
            run_cli([f"{POLL:02X}"], FakeLine())
        self.assertEqual(exit_.exception.code, 2)


def d_replace(at: int, new: bytes):
    return lambda data: data[:at] + new + data[at + len(new):]


ACTIONS = (
    (["outcome-arm", "1", "2", "3"], ARM, bytes((1, 2, 3))),
    (["outcome-cancel"], CANCEL, b""),
    (["game-spin", "2"], SPIN, bytes((2,))),
    (["game-spin", "1", "0", "21", "5"], SPIN, bytes((1, 0, 21, 5))),
    (["reels-position", "0x10", "0", "7"], POSITION, bytes((16, 0, 7))),
    (["reels-jog", "2"], JOG, bytes((2,))),
)


class ActionTest(ProfileFileTest):
    def setUp(self):
        super().setUp()
        self.profile_path = self.profile_file(sp.stock_profile())

    def test_each_action_reads_info_then_sends_its_values_once_under_the_next_id(self):
        for argv, op, payload in ACTIONS:
            with self.subTest(argv=argv):
                machine = FakeExtension()
                first = machine.next_id
                code, result = run_json(argv, machine)
                self.assertEqual(code, 0)
                self.assertEqual(machine.requests, [(INFO, 0, b""), (op, first, payload)])
                self.assertEqual((result["reply"]["id"], result["reply"]["status"]),
                                 (first, se.Status.ACCEPTED.name))

    def test_a_refusal_reports_the_id_and_status(self):
        machine = FakeExtension()
        machine.action_status = BUSY
        code, result = run_json(["game-spin", "1"], machine)
        self.assertEqual(code, 1)
        self.assertEqual((result["id"], result["status"]), (machine.last[0], se.Status.BUSY.name))

    def test_info_rules_out_mutations_before_any_is_sent(self):
        stops, max_bet = LIMITS["stops"], LIMITS["max_bet_per_line"]
        cases = {
            "stop past the strip": ({}, ["outcome-arm", str(stops), "0", "0"]),
            "spin stop past the strip": ({}, ["game-spin", "1", "0", "0", str(stops)]),
            "bet over the cap": ({}, ["game-spin", str(max_bet + 1)]),
            "no bet": ({}, ["game-spin", "0"]),
            "no such reel": ({}, ["reels-jog", str(sp.REELS)]),
            "no spin capability": ({"capabilities": ALL_CAPS & ~SPIN_CAP}, ["game-spin", "1"]),
            "no profile capability": ({"capabilities": ALL_CAPS & ~PROFILE_CAP}, ["profile-restore"]),
        }
        for name, (options, argv) in cases.items():
            with self.subTest(name):
                machine = FakeExtension(**options)
                code, _ = run_json(argv, machine)
                self.assertEqual(code, 1)
                self.assertEqual(machine.requests, [(INFO, 0, b"")])

    def test_a_faulted_extension_gets_no_mutation(self):
        machine = FakeExtension()
        machine.fault = True
        code, _ = run_json(["outcome-cancel"], machine)
        self.assertEqual(code, 1)
        self.assertEqual(machine.mutations(), [])

    def test_bad_values_are_usage_errors_before_any_traffic(self):
        for argv in (["game-spin", "1", "2"], ["outcome-arm", "1", "2"], ["outcome-arm", "1", "2", "256"],
                     ["reels-jog", "x"], ["reels-position", "-1", "0", "0"], ["outcome-cancel", "1"],
                     ["--id", "5", "extension-info"], ["--id", "5", "profile-upload", "p.json"],
                     ["--id", "0", "game-spin", "1"], ["--id", "5", "meters"], ["profile-upload"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as exit_:
                machine = FakeExtension()
                try:
                    run_cli(argv, machine)
                finally:
                    self.assertEqual(machine.sent, [])
            self.assertEqual(exit_.exception.code, 2)

    def test_an_uncertain_mutation_reports_its_id_and_is_not_repeated(self):
        ways = {"lost reply": {"silent": {SPIN}},
                "garbled reply": {"tamper": lambda d: d[:8] + bytes((d[8] ^ 1,)) + d[9:] if d[6] == SPIN else d}}
        for name, faults in ways.items():
            with self.subTest(name):
                machine = FakeExtension()
                first = machine.next_id
                machine.silent = faults.get("silent", set())
                machine.tamper = faults.get("tamper")
                code, result = run_json(["game-spin", "2"], machine)
                self.assertEqual(code, 1)
                self.assertEqual((result["id"], result["status"]), (first, "unknown"))
                self.assertEqual(result["error_type"], se.UncertainOutcome.__name__)
                self.assertEqual(machine.mutations(), [(SPIN, first, bytes((2,)))])

    def test_a_port_failure_or_interrupt_during_a_mutation_reports_its_id_and_stops(self):
        commands = {SPIN: ["game-spin", "2"], CHUNK: ["profile-upload", self.profile_path]}
        for op, argv in commands.items():
            for exception in (OSError(5, "Input/output error"), KeyboardInterrupt()):
                for stage in ("send", "read"):
                    with self.subTest(argv=argv[0], exception=type(exception).__name__, stage=stage):
                        machine = FakeExtension()
                        machine.failure = (op, exception, stage)
                        code, result = run_json(argv, machine)
                        self.assertEqual(code, 1)
                        attempted = machine.mutations()[-1]
                        self.assertEqual(attempted[0], op)
                        self.assertEqual((result["id"], result["status"], result["error_type"]),
                                         (attempted[1], "unknown", se.UncertainOutcome.__name__))
                        self.assertEqual([r[0] for r in machine.mutations()].count(op), 1)
                        # nothing, not even a sync poll, went out after the attempted mutation
                        self.assertEqual(machine.requests[-1], attempted)
                        self.assertNotEqual(machine.sent[-1][0], protocol.SYNC_POLL)

    def test_only_an_explicit_identical_repeat_reuses_an_id(self):
        machine = FakeExtension()
        first = machine.next_id
        machine.silent = {SPIN}
        self.assertEqual(run_json(["game-spin", "2"], machine)[0], 1)
        machine.silent = set()

        code, result = run_json(["--id", str(first), "game-spin", "2"], machine)
        self.assertEqual(code, 0)
        self.assertEqual((result["reply"]["id"], result["reply"]["status"]), (first, se.Status.ACCEPTED.name))
        self.assertEqual(machine.executed, [SPIN])

        code, result = run_json(["--id", str(first), "game-spin", "3"], machine)
        self.assertEqual((code, result["status"]), (1, se.Status.BAD_ID.name))
        sent = len(machine.requests)
        code, _ = run_json(["--id", str(first), "reels-jog", "1"], machine)
        self.assertEqual(code, 1)
        self.assertNotIn(JOG, [op for op, _, _ in machine.requests[sent:]])
        self.assertEqual(machine.executed, [SPIN])


class ProfileTest(ProfileFileTest):
    def test_upload_stages_bounded_chunks_commits_and_reads_everything_back(self):
        chunk = 20
        machine = FakeExtension(max_chunk=chunk)
        first = machine.next_id
        expected = sp.encode(sp.stock_profile())
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 0)
        self.assertEqual(machine.active, expected)
        self.assertEqual(machine.requests[0], (INFO, 0, b""))
        mutations = machine.mutations()
        chunks = -(-len(expected) // chunk)
        self.assertEqual([op for op, _, _ in mutations], [BEGIN] + [CHUNK] * chunks + [COMMIT])
        self.assertEqual([id for _, id, _ in mutations], list(range(first, first + len(mutations))))
        reads = [struct.unpack("<HB", payload) for op, _, payload in machine.requests if op == READ]
        self.assertEqual(sorted(i for offset, count in reads for i in range(offset, offset + count)),
                         list(range(len(expected))))
        reply = result["reply"]
        self.assertEqual((reply["id"], reply["generation"], int(reply["profile_crc"], 16)),
                         (mutations[-1][1], machine.generation, zlib.crc32(expected)))

    def test_upload_validates_within_info_caps_before_any_mutation(self):
        machine = FakeExtension(max_bet_per_line=sp.stock_profile().max_bet_per_line - 1)
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 1)
        self.assertEqual(result["error_type"], sp.ProfileError.__name__)
        self.assertEqual(machine.requests, [(INFO, 0, b"")])

    def test_upload_stops_at_an_uncertain_chunk(self):
        machine = FakeExtension()
        first = machine.next_id
        machine.silent = {CHUNK}
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 1)
        self.assertEqual((result["id"], result["status"]), (first + 1, "unknown"))
        self.assertEqual([op for op, _, _ in machine.mutations()], [BEGIN, CHUNK])

    def test_upload_fails_when_the_committed_profile_reads_back_different(self):
        machine = FakeExtension()
        machine.corrupt_commit = True
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 1)
        self.assertEqual(result["error_type"], se.VerifyFailed.__name__)

    def test_an_interrupted_read_back_after_commit_is_not_success(self):
        machine = FakeExtension()
        machine.failure = (STATUS, KeyboardInterrupt(), "read")
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 1)
        self.assertEqual(result["error_type"], KeyboardInterrupt.__name__)
        self.assertEqual(machine.mutations()[-1][0], COMMIT)

    def test_download_prints_only_the_active_profile_on_stdout(self):
        for active in (variant(), None):
            with self.subTest(active="stock" if active is None else "profile"):
                machine = FakeExtension()
                if active is not None:
                    machine.active = sp.encode(active)
                    machine.profile_crc = zlib.crc32(machine.active)
                code, out, _ = run_cli(["--json", "profile-download"], machine)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out), sp.to_json(active or sp.stock_profile()))
                self.assertEqual(machine.mutations(), [])

    def test_download_with_a_crc_mismatch_prints_nothing(self):
        machine = FakeExtension()
        machine.active = sp.encode(variant())
        machine.profile_crc = zlib.crc32(machine.active) ^ 1
        code, out, _ = run_cli(["profile-download"], machine)
        self.assertEqual((code, out), (1, ""))

    def test_restore_returns_to_stock_and_confirms_it(self):
        machine = FakeExtension()
        first = machine.next_id
        machine.active = sp.encode(variant())
        machine.profile_crc = zlib.crc32(machine.active)
        code, result = run_json(["profile-restore"], machine)
        self.assertEqual(code, 0)
        self.assertIsNone(machine.active)
        self.assertEqual([op for op, _, _ in machine.requests], [INFO, RESTORE, STATUS])
        self.assertEqual((result["reply"]["id"], result["reply"]["generation"]), (first, machine.generation))

    def test_only_profile_switches_wait_the_longer_reply_budget(self):
        machine = FakeExtension()
        for argv in (["profile-upload", self.profile_file(variant())], ["profile-download"],
                     ["profile-restore"], ["outcome-cancel"]):
            self.assertEqual(run_cli(argv, machine)[0], 0)
        self.assertEqual(machine.timeouts,
                         [se.PROFILE_SWITCH_REPLY_TIMEOUT if op in (COMMIT, RESTORE)
                          else protocol.Host.RESPONSE_TIMEOUT for op, _, _ in machine.requests])

    def test_a_lost_profile_switch_reply_is_uncertain_after_its_one_send(self):
        for op, argv in ((COMMIT, ["profile-upload", self.profile_file(variant())]),
                         (RESTORE, ["profile-restore"])):
            with self.subTest(op=op):
                machine = FakeExtension()
                machine.silent = {op}
                code, result = run_json(argv, machine)
                self.assertEqual(code, 1)
                attempted = machine.mutations()[-1]
                self.assertEqual(attempted[0], op)
                self.assertEqual((result["id"], result["status"], result["error_type"]),
                                 (attempted[1], "unknown", se.UncertainOutcome.__name__))
                self.assertEqual([r[0] for r in machine.mutations()].count(op), 1)
                self.assertEqual(machine.requests[-1], attempted)
                self.assertEqual(machine.timeouts[-1], se.PROFILE_SWITCH_REPLY_TIMEOUT)


class RecordedLine(FakeLine):
    """Replays live exchanges: each poll must be the request sent live, and its live reply comes
    back only if its first byte arrived within the wait the host gives that read."""

    def __init__(self, exchanges: list[dict]):
        super().__init__()
        self.exchanges = list(exchanges)
        self.exchange: dict | None = None

    def send(self, first, rest):
        super().send(first, rest)
        if first == protocol.SYNC_POLL:
            return
        self.exchange = self.exchanges.pop(0)
        assert bytes((first,)) + rest == bytes.fromhex(self.exchange["tx"]), self.exchange["sequence"]

    def read_message(self, timeout):
        exchange, self.exchange = self.exchange, None
        if exchange is not None and exchange["first_byte_ms"] / 1000 <= timeout:
            self.replies.append(bytes.fromhex(exchange["rx"]))
        return super().read_message(timeout)


def recorded_profile(exchanges: list[dict]) -> bytes:
    """The profile the recorded chunk requests staged."""
    data = bytearray()
    for exchange in exchanges:
        if exchange["operation"] == se.Op.PROFILE_CHUNK.name:
            payload = bytes.fromhex(exchange["tx"])[3 + se.REQUEST_HEADER:-2]
            assert se.CHUNK_OFFSET.unpack_from(payload)[0] == len(data), exchange["sequence"]
            data += payload[se.CHUNK_OFFSET.size:]
    return bytes(data)


class RecordedReplyTest(ProfileFileTest):
    """The live profile upload and stock restore whose replies were captured in full
    (fixtures/s6000_profile_reply_timing.json), replayed through the real host and extension.
    The recorded arrivals are single observations, not bounds on later replies."""

    RECORDING = json.loads((FIXTURES / "s6000_profile_reply_timing.json").read_text())

    def replay(self, action: str, line: RecordedLine) -> dict:
        """The result of the recorded action, run again over `line`."""
        ext = se.Extension(protocol.Host(line, ADDRESS))
        if action == "upload":
            recording = self.RECORDING[action]
            data = recorded_profile(recording["exchanges"])
            source = recording["source"]
            self.assertEqual((f"{zlib.crc32(data):08X}", hashlib.sha256(data).hexdigest()),
                             (source["profile_crc"], source["profile_sha256"]))
            return se.upload(ext, self.profile_file(sp.decode(data)))
        return se.restore(ext)

    def line(self, action: str) -> RecordedLine:
        return RecordedLine(self.RECORDING[action]["exchanges"])

    def test_the_live_upload_and_restore_replay_to_their_verified_results(self):
        for action in ("upload", "restore"):
            with self.subTest(action):
                line = self.line(action)
                result = self.replay(action, line)
                self.assertEqual(result, self.RECORDING[action]["verified"]["result"])
                self.assertEqual(line.exchanges, [])

    def test_a_profile_switch_reply_later_than_its_wait_is_uncertain_after_one_send(self):
        for action, op in (("upload", se.Op.COMMIT_PROFILE), ("restore", se.Op.RESTORE_STOCK)):
            with self.subTest(action):
                [switch] = [x for x in self.RECORDING[action]["exchanges"] if x["operation"] == op.name]
                line = self.line(action)
                early = switch["first_byte_ms"] / 1000 / 2
                with mock.patch.dict(se.REPLY_TIMEOUTS, {op: early}), \
                        self.assertRaises(se.UncertainOutcome) as caught:
                    self.replay(action, line)
                self.assertEqual((caught.exception.op, caught.exception.id), (op, switch["request_id"]))
                self.assertIsInstance(caught.exception.cause, protocol.NoResponse)
                request = bytes.fromhex(switch["tx"])
                self.assertEqual(line.messages().count(request), 1)
                self.assertEqual(line.messages()[-1], request)


class OfflineTest(ProfileFileTest):
    def test_profile_stock_prints_the_stock_profile(self):
        code, out, _ = run_cli(["profile-stock"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), sp.to_json(sp.stock_profile()))

    def test_profile_preview_reports_every_bet_or_the_one_asked_for(self):
        profile = variant()
        path = self.profile_file(profile)
        rows = sp.preview(profile)
        code, out, _ = run_cli(["--json", "profile-preview", path])
        self.assertEqual(code, 0)
        items = json.loads(out)["reply"]["items"]
        self.assertEqual([item["bet_per_line"] for item in items], [row.bet_per_line for row in rows])
        bet = profile.max_bet_per_line
        code, out, _ = run_cli(["--json", "profile-preview", path, str(bet)])
        self.assertEqual(code, 0)
        [item] = json.loads(out)["reply"]["items"]
        [row] = [row for row in rows if row.bet_per_line == bet]
        self.assertEqual((item["award_mass"], item["hit_mass"], item["outcome_mass"], item["nominal_return"]),
                         (row.award_mass, row.hit_mass, row.outcome_mass, str(row.nominal_return)))

    def test_profile_preview_refuses_a_bet_the_profile_does_not_take(self):
        path = self.profile_file(sp.stock_profile())
        with self.assertRaises(SystemExit) as exit_:
            run_cli(["profile-preview", path, str(sp.stock_profile().max_bet_per_line + 1)])
        self.assertEqual(exit_.exception.code, 2)

    def test_profile_preview_of_an_invalid_profile_fails(self):
        path = self.dir / "bad.json"
        path.write_text(json.dumps(sp.to_json(sp.stock_profile()) | {"lines": 0}))
        self.assertEqual(run_cli(["profile-preview", str(path)])[0], 1)


class WrapperTest(ProfileFileTest):
    """tools/sas with ssh and rsync replaced by stubs that record their arguments."""

    REMOTE_DIR = "/remote/blazing-7s"
    HOST = "user@sas-host"
    PORT = "/dev/ttyTEST"

    def setUp(self):
        super().setUp()
        stubs = self.dir / "bin"
        stubs.mkdir()
        self.log = self.dir / "calls.jsonl"
        recorder = (f"#!{sys.executable}\nimport json, os, sys\n"
                    "with open(os.environ['STUB_LOG'], 'a') as log:\n"
                    "    log.write(json.dumps(sys.argv) + '\\n')\n")
        for name in ("ssh", "rsync"):
            (stubs / name).write_text(recorder)
        (stubs / "python3").write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n")
        for stub in stubs.iterdir():
            stub.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}", "STUB_LOG": str(self.log),
                    "HOME": str(self.dir), "SAS_HOST": self.HOST, "SAS_PORT": self.PORT, "SAS_DIR": self.REMOTE_DIR}

    def wrapper(self, *argv: str, unset: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        env = {name: value for name, value in self.env.items() if name not in unset}
        return subprocess.run([str(REPO / "tools" / "sas"), *argv], cwd=self.dir, env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return read_jsonl(self.log)

    def test_offline_profile_commands_run_locally_with_local_paths(self):
        stock = self.wrapper("profile-stock")
        self.assertEqual(stock.returncode, 0, stock.stderr)
        self.assertEqual(json.loads(stock.stdout), sp.to_json(sp.stock_profile()))
        name = Path(self.profile_file(sp.stock_profile())).name
        preview = self.wrapper("--json", "profile-preview", name, "1")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(len(json.loads(preview.stdout)["reply"]["items"]), 1)
        self.assertEqual(self.calls(), [])

    def test_list_and_help_run_locally_without_any_host_setting(self):
        for argv in (("list",), ("help", "meters")):
            with self.subTest(argv=argv):
                result = self.wrapper(*argv, unset=("SAS_HOST", "SAS_PORT", "SAS_DIR"))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(result.stdout)
        self.assertEqual(self.calls(), [])

    def test_online_commands_need_both_host_and_port_before_contacting_anything(self):
        for argv in (("meters",), ("commission-info",)):
            for missing in (("SAS_HOST",), ("SAS_PORT",), ("SAS_HOST", "SAS_PORT")):
                with self.subTest(argv=argv, missing=missing):
                    result = self.wrapper(*argv, unset=missing)
                    self.assertNotEqual(result.returncode, 0)
                    for name in ("SAS_HOST", "SAS_PORT"):
                        self.assertIn(name, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_upload_copies_the_local_profile_and_runs_with_the_remote_copy(self):
        local = Path(self.profile_file(sp.stock_profile()))
        result = self.wrapper("--json", "profile-upload", local.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        destination = f"{self.HOST}:{self.REMOTE_DIR}/"
        [copy] = [c for c in calls if Path(c[0]).name == "rsync" and Path(c[-2]).resolve() == local.resolve()]
        self.assertTrue(copy[-1].startswith(destination), copy)
        remote_copy = copy[-1][len(destination):]
        command = shlex.split(calls[-1][-1])
        self.assertEqual(Path(calls[-1][0]).name, "ssh")
        self.assertEqual(command[-2:], ["profile-upload", remote_copy])
        self.assertIn("--json", command)
        self.assertEqual(command[command.index("--port") + 1], self.PORT)

    def test_upload_of_a_missing_file_contacts_nothing(self):
        result = self.wrapper("profile-upload", "missing.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
