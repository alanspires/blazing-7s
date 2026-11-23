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
from sas_testing import (
    ACCEPTED,
    ALL_CAPS,
    ARM,
    BEGIN,
    BUSY,
    CANCEL,
    CHUNK,
    COMMIT,
    FIXTURES,
    INFO,
    JOG,
    LIMITS,
    MAGIC,
    POSITION,
    PROFILE_CAP,
    READ,
    REPO,
    RESTORE,
    SPIN,
    SPIN_CAP,
    STATUS,
    WIRE_VERSION,
    FakeExtension,
    FakeLine,
    FakeS6000,
    TempDirTest,
    read_jsonl,
    run_cli,
)
from sas_testing import EXTENSION_POLL as POLL

ADDRESS = 1
REQUEST_OP_AT = 3 + len(MAGIC) + 1  # address, command, length, magic, version, then the op


def fake(capabilities: int = ALL_CAPS, **options: int) -> FakeS6000:
    """A machine whose extension answers every action with ACCEPTED and changes nothing else,
    so these tests see the wire, not the game model."""
    return FakeS6000(extension=FakeExtension(capabilities, action_status=ACCEPTED, **options))


class InterruptAfterCommit(FakeS6000):
    """Interrupts the wait for the first STATUS reply after COMMIT: the read-back."""

    def send(self, first, rest):
        super().send(first, rest)
        if self.extension.requests and self.extension.requests[-1][0] == COMMIT:
            self.failure = (STATUS, KeyboardInterrupt(), "read")


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
        machine = fake()
        code, result = run_json(["extension-info"], machine)
        self.assertEqual(code, 0)
        request = MAGIC + bytes((WIRE_VERSION, INFO)) + bytes(8)
        self.assertEqual(machine.messages()[-1],
                         protocol.with_crc(bytes((ADDRESS, POLL, len(request))) + request))
        reply = result["reply"]
        self.assertEqual({name: reply[name] for name in LIMITS}, LIMITS)
        self.assertEqual((reply["next_id"], int(reply["capabilities"], 16)),
                         (machine.extension.next_id, ALL_CAPS))

    def test_status_reports_a_pending_request_and_the_active_profile(self):
        machine = fake()
        ext = machine.extension
        ext.last_status, ext.active, ext.profile_crc = ACCEPTED, bytes(sp.PROFILE_SIZE), 0x1234ABCD
        code, result = run_json(["extension-status"], machine)
        self.assertEqual(code, 0)
        reply = result["reply"]
        self.assertEqual((reply["last_id"], reply["pending"], reply["active"], reply["generation"],
                          int(reply["profile_crc"], 16), reply["fault"]),
                         (ext.last[0], True, "profile", ext.generation, ext.profile_crc, False))

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
                machine = fake()
                machine.extension.tamper = tamper
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
                machine = fake()
                first = machine.extension.next_id
                code, result = run_json(argv, machine)
                self.assertEqual(code, 0)
                self.assertEqual(machine.extension.requests, [(INFO, 0, b""), (op, first, payload)])
                self.assertEqual((result["reply"]["id"], result["reply"]["status"]),
                                 (first, se.Status.ACCEPTED.name))

    def test_a_refusal_reports_the_id_and_status(self):
        machine = fake()
        machine.extension.action_status = BUSY
        code, result = run_json(["game-spin", "1"], machine)
        self.assertEqual(code, 1)
        self.assertEqual((result["id"], result["status"]), (machine.extension.last[0], se.Status.BUSY.name))

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
                machine = fake(**options)
                code, _ = run_json(argv, machine)
                self.assertEqual(code, 1)
                self.assertEqual(machine.extension.requests, [(INFO, 0, b"")])

    def test_a_faulted_extension_gets_no_mutation(self):
        machine = fake()
        machine.extension.fault = True
        code, _ = run_json(["outcome-cancel"], machine)
        self.assertEqual(code, 1)
        self.assertEqual(machine.extension.mutations(), [])

    def test_bad_values_are_usage_errors_before_any_traffic(self):
        for argv in (["game-spin", "1", "2"], ["outcome-arm", "1", "2"], ["outcome-arm", "1", "2", "256"],
                     ["reels-jog", "x"], ["reels-position", "-1", "0", "0"], ["outcome-cancel", "1"],
                     ["--id", "5", "extension-info"], ["--id", "5", "profile-upload", "p.json"],
                     ["--id", "0", "game-spin", "1"], ["--id", "5", "meters"], ["profile-upload"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as exit_:
                machine = fake()
                try:
                    run_cli(argv, machine)
                finally:
                    self.assertEqual(machine.sent, [])
            self.assertEqual(exit_.exception.code, 2)

    def test_an_uncertain_mutation_reports_its_id_and_is_not_repeated(self):
        ways = {"lost reply": {"lose": {SPIN}},
                "garbled reply": {"tamper": lambda d: d[:8] + bytes((d[8] ^ 1,)) + d[9:] if d[6] == SPIN else d}}
        for name, faults in ways.items():
            with self.subTest(name):
                machine = fake()
                first = machine.extension.next_id
                machine.lose = faults.get("lose", set())
                machine.extension.tamper = faults.get("tamper")
                code, result = run_json(["game-spin", "2"], machine)
                self.assertEqual(code, 1)
                self.assertEqual((result["id"], result["status"]), (first, "unknown"))
                self.assertEqual(result["error_type"], se.UncertainOutcome.__name__)
                self.assertEqual(machine.extension.mutations(), [(SPIN, first, bytes((2,)))])

    def test_a_port_failure_or_interrupt_during_a_mutation_reports_its_id_and_stops(self):
        commands = {SPIN: ["game-spin", "2"], CHUNK: ["profile-upload", self.profile_path]}
        for op, argv in commands.items():
            for exception in (OSError(5, "Input/output error"), KeyboardInterrupt()):
                for stage in ("send", "read"):
                    with self.subTest(argv=argv[0], exception=type(exception).__name__, stage=stage):
                        machine = fake()
                        machine.failure = (op, exception, stage)
                        code, result = run_json(argv, machine)
                        self.assertEqual(code, 1)
                        attempted = machine.extension.mutations()[-1]
                        self.assertEqual(attempted[0], op)
                        self.assertEqual((result["id"], result["status"], result["error_type"]),
                                         (attempted[1], "unknown", se.UncertainOutcome.__name__))
                        self.assertEqual([r[0] for r in machine.extension.mutations()].count(op), 1)
                        # nothing, not even a sync poll, went out after the attempted mutation
                        self.assertEqual(machine.extension.requests[-1], attempted)
                        self.assertNotEqual(machine.sent[-1][0], protocol.SYNC_POLL)

    def test_only_an_explicit_identical_repeat_reuses_an_id(self):
        machine = fake()
        first = machine.extension.next_id
        machine.lose = {SPIN}
        self.assertEqual(run_json(["game-spin", "2"], machine)[0], 1)
        machine.lose = set()

        code, result = run_json(["--id", str(first), "game-spin", "2"], machine)
        self.assertEqual(code, 0)
        self.assertEqual((result["reply"]["id"], result["reply"]["status"]), (first, se.Status.ACCEPTED.name))
        self.assertEqual(machine.extension.executed, [SPIN])

        code, result = run_json(["--id", str(first), "game-spin", "3"], machine)
        self.assertEqual((code, result["status"]), (1, se.Status.BAD_ID.name))
        sent = len(machine.extension.requests)
        code, _ = run_json(["--id", str(first), "reels-jog", "1"], machine)
        self.assertEqual(code, 1)
        self.assertNotIn(JOG, [op for op, _, _ in machine.extension.requests[sent:]])
        self.assertEqual(machine.extension.executed, [SPIN])


class ProfileTest(ProfileFileTest):
    def test_upload_stages_bounded_chunks_commits_and_reads_everything_back(self):
        chunk = 20
        machine = fake(max_chunk=chunk)
        first = machine.extension.next_id
        expected = sp.encode(sp.stock_profile())
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 0)
        self.assertEqual(machine.extension.active, expected)
        self.assertEqual(machine.extension.requests[0], (INFO, 0, b""))
        mutations = machine.extension.mutations()
        chunks = -(-len(expected) // chunk)
        self.assertEqual([op for op, _, _ in mutations], [BEGIN] + [CHUNK] * chunks + [COMMIT])
        self.assertEqual([id for _, id, _ in mutations], list(range(first, first + len(mutations))))
        reads = [struct.unpack("<HB", payload) for op, _, payload in machine.extension.requests if op == READ]
        self.assertEqual(sorted(i for offset, count in reads for i in range(offset, offset + count)),
                         list(range(len(expected))))
        reply = result["reply"]
        self.assertEqual((reply["id"], reply["generation"], int(reply["profile_crc"], 16)),
                         (mutations[-1][1], machine.extension.generation, zlib.crc32(expected)))

    def test_upload_validates_within_info_caps_before_any_mutation(self):
        machine = fake(max_bet_per_line=sp.stock_profile().max_bet_per_line - 1)
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 1)
        self.assertEqual(result["error_type"], sp.ProfileError.__name__)
        self.assertEqual(machine.extension.requests, [(INFO, 0, b"")])

    def test_upload_stops_at_an_uncertain_chunk(self):
        machine = fake()
        first = machine.extension.next_id
        machine.lose = {CHUNK}
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 1)
        self.assertEqual((result["id"], result["status"]), (first + 1, "unknown"))
        self.assertEqual([op for op, _, _ in machine.extension.mutations()], [BEGIN, CHUNK])

    def test_upload_fails_when_the_committed_profile_reads_back_different(self):
        machine = fake()
        machine.extension.corrupt_commit = True
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 1)
        self.assertEqual(result["error_type"], se.VerifyFailed.__name__)

    def test_an_interrupted_read_back_after_commit_is_not_success(self):
        machine = InterruptAfterCommit(extension=FakeExtension(action_status=ACCEPTED))
        code, result = run_json(["profile-upload", self.profile_file(sp.stock_profile())], machine)
        self.assertEqual(code, 1)
        self.assertEqual(result["error_type"], KeyboardInterrupt.__name__)
        self.assertEqual(machine.extension.mutations()[-1][0], COMMIT)

    def test_download_prints_only_the_active_profile_on_stdout(self):
        for active in (variant(), None):
            with self.subTest(active="stock" if active is None else "profile"):
                machine = fake()
                if active is not None:
                    machine.extension.active = sp.encode(active)
                    machine.extension.profile_crc = zlib.crc32(machine.extension.active)
                code, out, _ = run_cli(["--json", "profile-download"], machine)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out), sp.to_json(active or sp.stock_profile()))
                self.assertEqual(machine.extension.mutations(), [])

    def test_download_with_a_crc_mismatch_prints_nothing(self):
        machine = fake()
        machine.extension.active = sp.encode(variant())
        machine.extension.profile_crc = zlib.crc32(machine.extension.active) ^ 1
        code, out, _ = run_cli(["profile-download"], machine)
        self.assertEqual((code, out), (1, ""))

    def test_restore_returns_to_stock_and_confirms_it(self):
        machine = fake()
        first = machine.extension.next_id
        machine.extension.active = sp.encode(variant())
        machine.extension.profile_crc = zlib.crc32(machine.extension.active)
        code, result = run_json(["profile-restore"], machine)
        self.assertEqual(code, 0)
        self.assertIsNone(machine.extension.active)
        self.assertEqual([op for op, _, _ in machine.extension.requests], [INFO, RESTORE, STATUS])
        self.assertEqual((result["reply"]["id"], result["reply"]["generation"]), (first, machine.extension.generation))

    def test_only_profile_switches_wait_the_longer_reply_budget(self):
        machine = fake()
        for argv in (["profile-upload", self.profile_file(variant())], ["profile-download"],
                     ["profile-restore"], ["outcome-cancel"]):
            self.assertEqual(run_cli(argv, machine)[0], 0)
        self.assertEqual(machine.timeouts,
                         [se.PROFILE_SWITCH_REPLY_TIMEOUT if op in (COMMIT, RESTORE)
                          else protocol.Host.RESPONSE_TIMEOUT for op, _, _ in machine.extension.requests])

    def test_a_lost_profile_switch_reply_is_uncertain_after_its_one_send(self):
        for op, argv in ((COMMIT, ["profile-upload", self.profile_file(variant())]),
                         (RESTORE, ["profile-restore"])):
            with self.subTest(op=op):
                machine = fake()
                machine.lose = {op}
                code, result = run_json(argv, machine)
                self.assertEqual(code, 1)
                attempted = machine.extension.mutations()[-1]
                self.assertEqual(attempted[0], op)
                self.assertEqual((result["id"], result["status"], result["error_type"]),
                                 (attempted[1], "unknown", se.UncertainOutcome.__name__))
                self.assertEqual([r[0] for r in machine.extension.mutations()].count(op), 1)
                self.assertEqual(machine.extension.requests[-1], attempted)
                self.assertEqual(machine.timeouts[-1], se.PROFILE_SWITCH_REPLY_TIMEOUT)


class JournalDown(Exception):
    pass


class SeamTest(unittest.TestCase):
    """The pace seam and record-before-send, on the extension API the page's worker calls."""

    def extension(self, machine: FakeS6000) -> tuple[se.Extension, list[int]]:
        """An Extension whose pace notes how many requests the machine had seen by then."""
        paced: list[int] = []
        ext = se.Extension(protocol.Host(machine, ADDRESS),
                           pace=lambda: paced.append(len(machine.extension.requests)))
        return ext, paced

    def test_pace_runs_once_before_every_host_call_and_never_inside_one(self):
        machine = fake(max_chunk=60)
        machine.busy_replies = 1  # the first request is repeated inside its one host call
        ext, paced = self.extension(machine)
        se.upload(ext, variant(), record=lambda intent: None)
        se.download(ext)
        se.restore(ext, record=lambda intent: None)
        se.act(ext, se.Op.CANCEL_OUTCOME, b"", record=lambda intent: None)
        self.assertEqual(paced, list(range(len(machine.extension.requests))))

    def test_a_mutation_is_recorded_with_its_exact_request_before_it_is_sent(self):
        machine = fake()
        ext, _ = self.extension(machine)
        seen: list[tuple[dict, int]] = []
        result = se.act(ext, se.Op.BET_SPIN, bytes((2,)),
                        record=lambda intent: seen.append((intent, len(machine.extension.mutations()))))
        [(intent, sent_before)] = seen
        self.assertEqual(sent_before, 0)
        self.assertEqual((intent["op"], intent["id"]), (se.Op.BET_SPIN.name, result["id"]))
        self.assertEqual(bytes.fromhex(intent["request_hex"]), machine.messages()[-1])

    def test_a_record_that_fails_sends_nothing(self):
        machine = fake()
        ext, _ = self.extension(machine)

        def record(intent: dict) -> None:
            raise JournalDown

        for request in (lambda: se.act(ext, se.Op.BET_SPIN, bytes((2,)), record=record),
                        lambda: se.upload(ext, variant(), record=record),
                        lambda: se.restore(ext, record=record)):
            with self.assertRaises(JournalDown):
                request()
        self.assertEqual(machine.extension.mutations(), [])

    def test_upload_records_one_parent_intent_then_each_step_before_its_send(self):
        machine = fake(max_chunk=60)
        ext, _ = self.extension(machine)
        previous = ext.status()
        start = len(machine.extension.requests)
        records: list[tuple[dict, int]] = []
        profile = variant()
        result = se.upload(ext, profile, record=lambda r: records.append((r, len(machine.extension.mutations()))))
        [(intent, sent_before)] = [(r, n) for r, n in records if r.get("kind") == "intent"]
        self.assertEqual(sent_before, 0)
        data = sp.encode(profile)
        self.assertEqual((intent["op"], intent["profile_crc"], intent["length"], bytes.fromhex(intent["profile_hex"])),
                         ("upload", zlib.crc32(data), len(data), data))
        self.assertEqual(intent["previous"], {"active": previous.active, "profile_crc": previous.profile_crc,
                                              "generation": previous.generation})
        self.assertEqual([op for op, _, _ in machine.extension.requests[start:start + 3]], [INFO, STATUS, BEGIN])
        steps = [(r, n) for r, n in records if r.get("kind") == "step"]
        self.assertEqual({r["upload_id"] for r, _ in steps}, {intent["upload_id"]})
        self.assertEqual([{"op": r["op"], "id": r["id"]} for r, _ in steps], intent["steps"])
        self.assertEqual([n for _, n in steps], list(range(len(steps))))  # each recorded before its own send
        self.assertEqual([(se.Op[r["op"]], r["id"]) for r, _ in steps],
                         [(op, id) for op, id, _ in machine.extension.mutations()])
        self.assertEqual([bytes.fromhex(r["request_hex"]) for r, _ in steps],
                         [m for m in machine.messages() if m[:2] == bytes((ADDRESS, POLL))
                          and m[REQUEST_OP_AT] in (BEGIN, CHUNK, COMMIT)])
        self.assertEqual(result["id"], steps[-1][0]["id"])


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


def decode_recorded_reply(exchange: dict) -> bytes:
    """The payload of a recorded extension reply."""
    return protocol.check_crc(bytes.fromhex(exchange["rx"]))[3 + se.REPLY_HEADER:]


class RecordedReplyTest(ProfileFileTest):
    """The live profile upload and stock restore whose replies were captured in full
    (fixtures/s6000_profile_reply_timing.json), replayed through the real host and extension.
    The recorded arrivals are single observations, not bounds on later replies."""

    RECORDING = json.loads((FIXTURES / "s6000_profile_reply_timing.json").read_text())

    def replay(self, action: str, line: RecordedLine, record=lambda intent: None) -> dict:
        """The result of the recorded action, run again over `line`."""
        ext = se.Extension(protocol.Host(line, ADDRESS), pace=lambda: None)
        if action == "upload":
            recording = self.RECORDING[action]
            data = recorded_profile(recording["exchanges"])
            source = recording["source"]
            self.assertEqual((f"{zlib.crc32(data):08X}", hashlib.sha256(data).hexdigest()),
                             (source["profile_crc"], source["profile_sha256"]))
            return se.upload(ext, sp.decode(data), record=record)
        return se.restore(ext, record=record)

    def line(self, action: str) -> RecordedLine:
        """The recorded exchanges. The live upload predates the STATUS read that upload now makes
        before BEGIN, so that one exchange is synthesized: the stock game, one generation before
        the recorded commit, under the ID before INFO's next ID."""
        exchanges = list(self.RECORDING[action]["exchanges"])
        if action == "upload":
            info = se.INFO.unpack(decode_recorded_reply(exchanges[0]))
            verified = self.RECORDING[action]["verified"]["status"]
            payload = struct.pack("<QBBBIIB", info[1] - 1, ARM, 0, 0, verified["generation"] - 1, 0, 0)
            [query] = {x["tx"] for x in exchanges if x["operation"] == se.Op.STATUS.name}
            reply = MAGIC + bytes((WIRE_VERSION, STATUS, 0)) + bytes(8) + payload
            exchanges.insert(1, {"sequence": "synthesized", "operation": "STATUS", "tx": query,
                                 "rx": protocol.with_crc(bytes((ADDRESS, POLL, len(reply))) + reply).hex(),
                                 "first_byte_ms": 0})
        return RecordedLine(exchanges)

    def test_records_carry_the_exact_requests_the_live_run_sent(self):
        for action in ("upload", "restore"):
            with self.subTest(action):
                records: list[dict] = []
                self.replay(action, self.line(action), records.append)
                sent = [x["tx"] for x in self.RECORDING[action]["exchanges"]
                        if x["operation"] not in (se.Op.INFO.name, se.Op.STATUS.name, se.Op.READ_PROFILE.name)]
                self.assertEqual([r["request_hex"] for r in records if "request_hex" in r], sent)

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
