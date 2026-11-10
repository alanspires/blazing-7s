from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import unittest
import zlib
from contextlib import redirect_stderr

import sas_commission as cm
from sas_testing import TempDirTest, pattern

# firmware/COMMISSIONING-CONTRACT.md: the wire and record encodings are restated so the fake
# checks them against the contract rather than against sas_commission's own encoding. Sizes
# come from the host's layout; the compiled integration test ties that to the firmware's.
FLAG, ESCAPE = 0x7E, 0x7D
MAGIC, WIRE_VERSION = b"S6CM", 1
INFO, READ, STAGE, INIT, IDENTITY = range(5)
UNKNOWN, READY, DONE, REFUSED = range(4)
OK, BAD_REQUEST, FAULT, INCOMPLETE = 0, 2, 6, 8
PERSIST, RECORD, CAPTURE = cm.LAYOUT.persist_size, cm.LAYOUT.record_size, cm.LAYOUT.capture_size
MAX_CHUNK = cm.LAYOUT.max_chunk
LAYOUT = (cm.LAYOUT.persist_base, PERSIST, cm.LAYOUT.record_base, RECORD, CAPTURE, MAX_CHUNK)  # INFO order
READY_MARK, DONE_MARK = 0xA55A, 0x5AA5
MARKER = PERSIST + RECORD - 2  # the record's marker, as an offset in the capture
# firmware/src/core.c SLOT_MAGIC at fw.h FW_SLOT_MAGIC_OFFSET: existing extension state.
SLOT_MAGIC, SLOT_MAGIC_OFFSET = struct.pack("<I", 0x4C533653), 496
MACHINE_IDENTITY = (b"S6M00002B5 main ROM identity".ljust(32, b"\0") + b"E595211X-05".ljust(12, b"\0")
                    + struct.pack("<II", 0x1B2C3D4E, 0x5F607182))


def frame(payload: bytes) -> bytes:
    body = payload + struct.pack("<I", zlib.crc32(payload))
    escaped = b"".join(bytes((ESCAPE, b ^ 0x20)) if b in (FLAG, ESCAPE) else bytes((b,)) for b in body)
    return bytes((FLAG,)) + escaped + bytes((FLAG,))


def unframe(body: bytes) -> bytes | None:
    """The payload of one frame's bytes between delimiters, or None if its CRC is wrong."""
    data, escaped = bytearray(), False
    for b in body:
        if b == ESCAPE and not escaped:
            escaped = True
            continue
        data.append(b ^ 0x20 if escaped else b)
        escaped = False
    payload = bytes(data[:-4])
    return payload if len(data) > 4 and struct.unpack("<I", data[-4:])[0] == zlib.crc32(payload) else None


def record(original: bytes, marker: int) -> bytes:
    data = bytearray(RECORD)
    data[0:4], data[4] = b"S6CI", 1
    data[8:12] = struct.pack("<I", zlib.crc32(original))
    data[16:16 + PERSIST] = original
    data[RECORD - 6:RECORD - 2] = struct.pack("<I", zlib.crc32(data[:RECORD - 6]))
    data[RECORD - 2:] = struct.pack("<H", marker)
    return bytes(data)


ORIGINAL = pattern(PERSIST, 0x7D)
ENDPOINT = "/dev/ttyTEST"
RECORD_BEFORE = pattern(RECORD, 0x11)


class FakeService:
    """The firmware's commissioning service on the far side of a serial line, as a cm.Port.

    INIT word-writes in the contract's order: invalidate the marker, write the record body,
    commit READY, zero the persistence, mark DONE. After `power_fails_after` writes the machine
    loses power: it stops answering until restart(). Ops in `silent` act but their reply is
    lost; `noise` goes out before each reply; with `trickle` replies arrive a byte per read.
    After `changes_after_reads` READs something else changes the first stored byte."""

    def __init__(self, persistence: bytes = ORIGINAL, record_area: bytes = RECORD_BEFORE):
        self.storage = bytearray(persistence + record_area)
        self.identity = MACHINE_IDENTITY
        self.layout = LAYOUT
        self.power_fails_after: int | None = None
        self.changes_after_reads: int | None = None
        self.writes = 0
        self.silent: set[int] = set()
        self.noise = b""
        self.trickle = False
        self.ops: list[int] = []
        self.opened: str | None = None  # the device name the command line last opened
        self.closed = False
        self.received = bytearray()
        self.outgoing = bytearray()
        self.restart()

    def restart(self) -> None:
        self.powered = True
        self.staged = bytearray(CAPTURE)
        self.covered: set[int] = set()
        self.mode = self.boot_mode()

    def boot_mode(self) -> int:
        marker = struct.unpack_from("<H", self.storage, MARKER)[0]
        if marker in (READY_MARK, DONE_MARK):
            area = self.storage[PERSIST:]
            intact = struct.unpack_from("<I", area, RECORD - 6)[0] == zlib.crc32(area[:RECORD - 6])
            return REFUSED if not intact else READY if marker == READY_MARK else DONE
        if self.storage[SLOT_MAGIC_OFFSET:SLOT_MAGIC_OFFSET + 4] == SLOT_MAGIC:
            return REFUSED
        return UNKNOWN

    # cm.Port

    def write(self, data: bytes) -> None:
        assert not self.closed
        if not self.powered:
            return
        for b in data:
            if b != FLAG:
                self.received.append(b)
                continue
            body, self.received = bytes(self.received), bytearray()
            payload = unframe(body) if body else None
            if payload is not None:
                self.handle(payload)

    def read(self, timeout: float) -> bytes:
        count = 1 if self.trickle else len(self.outgoing)
        data, self.outgoing = bytes(self.outgoing[:count]), self.outgoing[count:]
        return data

    def reset_input(self) -> None:
        self.outgoing.clear()

    def close(self) -> None:
        self.closed = True

    # The service

    def handle(self, payload: bytes) -> None:
        assert payload[:4] == MAGIC and payload[4] == WIRE_VERSION, payload.hex(" ")
        op, body = payload[5], payload[6:]
        assert len(body) <= 64, len(body)
        self.ops.append(op)
        status, data = self.answer(op, body)
        if self.powered and op not in self.silent:
            self.outgoing += self.noise + frame(MAGIC + bytes((WIRE_VERSION, op, status)) + data)

    def answer(self, op: int, body: bytes) -> tuple[int, bytes]:
        if op == INFO:
            return OK, struct.pack("<BIHIHHH", self.mode, *self.layout)
        if op == IDENTITY:
            return OK, self.identity
        if op == READ:
            offset, count = struct.unpack("<HB", body)
            assert 1 <= count <= MAX_CHUNK and offset + count <= CAPTURE, (offset, count)
            if self.ops.count(READ) - 1 == self.changes_after_reads:
                self.storage[0] ^= 1
            return OK, bytes(self.storage[offset:offset + count])
        if op == STAGE:
            return self.stage(struct.unpack_from("<H", body)[0], body[2:]), b""
        assert op == INIT and body == b"FIRST-INSTALL", body
        return self.init(), b""

    def stage(self, offset: int, data: bytes) -> int:
        assert 1 <= len(data) <= MAX_CHUNK and offset + len(data) <= CAPTURE, (offset, len(data))
        span = range(offset, offset + len(data))
        if any(i in self.covered and self.staged[i] != data[i - offset] for i in span):
            return BAD_REQUEST
        self.staged[offset:offset + len(data)] = data
        self.covered.update(span)
        return OK

    def plan(self) -> list[tuple[int, bytes]]:
        """INIT's word writes (capture offset, value) for the staged original."""
        desired = record(bytes(self.staged[:PERSIST]), READY_MARK)
        return ([(MARKER, bytes(2))]
                + [(PERSIST + i, desired[i:i + 2]) for i in range(0, RECORD - 2, 2)]
                + [(MARKER, struct.pack("<H", READY_MARK))]
                + [(i, bytes(2)) for i in range(0, PERSIST, 2)]
                + [(MARKER, struct.pack("<H", DONE_MARK))])

    def init(self) -> int:
        if self.mode in (DONE, REFUSED):
            return FAULT
        if len(self.covered) != CAPTURE:
            return INCOMPLETE
        plan = self.plan()
        committed = plan.index((MARKER, struct.pack("<H", READY_MARK))) + 1
        original = bytes(self.staged[:PERSIST])
        if self.mode == READY:
            current = bytes(self.storage[:PERSIST])
            words = [i for i in range(0, PERSIST, 2) if current[i:i + 2] != original[i:i + 2]]
            zeroed = (words[-1] + 2) if words else 0
            if (self.storage[PERSIST:] != record(original, READY_MARK) or any(current[:zeroed])):
                return BAD_REQUEST
            start = committed
        else:
            torn = self.torn_states(plan, committed)
            if self.storage[:PERSIST] != original or self.storage[PERSIST:] not in torn:
                return BAD_REQUEST
            start = 0
        for offset, value in plan[start:]:
            if self.writes == self.power_fails_after:
                self.powered = False
                return OK  # never sent
            self.storage[offset:offset + 2] = value
            self.writes += 1
        self.mode = DONE
        return OK

    def torn_states(self, plan: list[tuple[int, bytes]], committed: int) -> list[bytes]:
        """The record area after each prefix of the record writes, from the staged one."""
        area = bytearray(self.staged)
        states = [bytes(area[PERSIST:])]
        for offset, value in plan[:committed - 1]:
            area[offset:offset + 2] = value
            states.append(bytes(area[PERSIST:]))
        return states

    def init_count(self) -> int:
        return self.ops.count(INIT)


def run(argv: list[str], device: FakeService | None = None,
        stdin: bytes = b"") -> tuple[int, bytes, dict | None]:
    """Exit status, stdout and the failure record of sas_commission's command line. Without a
    device, opening the serial port fails the test."""
    def open_port(port: str) -> FakeService:
        if device is None:
            raise AssertionError(f"{argv}: opened {port}")
        device.opened, device.closed = port, False  # a new descriptor on the same machine
        return device

    out, err = io.BytesIO(), io.StringIO()
    with redirect_stderr(err):
        code = cm.main(argv, open_port=open_port, stdin=io.BytesIO(stdin), stdout=out)
    return code, out.getvalue(), json.loads(err.getvalue()) if code else None


def captured(device: FakeService) -> cm.Archive:
    return cm.capture(cm.Session(device), ENDPOINT)


class FramingTest(unittest.TestCase):
    def test_host_frames_carry_any_payload_and_noise_never_yields_a_frame(self):
        payloads = [bytes((FLAG, ESCAPE, 0x20, FLAG ^ 0x20)) * 8, bytes(range(cm.MAX_BODY - cm.CRC.size))]
        for payload in payloads:
            sent = cm.encode_frame(payload)
            self.assertNotIn(FLAG, sent[1:-1])
            self.assertEqual(unframe(sent[1:-1]), payload)
        bad_crc = bytearray(frame(payloads[0]))
        bad_crc[3] ^= 1
        oversized = frame(bytes(cm.MAX_BODY - cm.CRC.size + 1))
        stream = (b"line noise" + frame(payloads[0]) + bytes(bad_crc) + bytes((FLAG, FLAG)) + oversized
                  + bytes((FLAG, ESCAPE, FLAG)) + frame(payloads[1]))
        decoder = cm.FrameDecoder()
        decoded = [p for b in stream for p in decoder.feed(bytes((b,)))]
        self.assertEqual(decoded, payloads)
        self.assertEqual(cm.FrameDecoder().feed(stream), payloads)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class NoisyLine:
    """A cm.Port that never delivers a frame: one stray byte per read, each read taking
    `step` seconds of the fake clock."""

    def __init__(self, clock: FakeClock, step: float):
        self.clock, self.step = clock, step
        self.waits: list[float] = []

    def write(self, data: bytes) -> None:
        pass

    def read(self, timeout: float) -> bytes:
        self.waits.append(timeout)
        self.clock.now += self.step
        return b"\x00"

    def reset_input(self) -> None:
        pass

    def close(self) -> None:
        pass


class DeadlineTest(unittest.TestCase):
    def test_a_reply_has_one_deadline_however_the_bytes_trickle(self):
        clock, step = FakeClock(), 1.0
        line = NoisyLine(clock, step)
        timeout = 4 * step
        with self.assertRaises(cm.NoReply):
            cm.Session(line, reply_timeout=timeout, clock=clock).info()
        self.assertEqual(len(line.waits), int(timeout / step))
        self.assertEqual(line.waits, [timeout - i * step for i in range(len(line.waits))])

    def test_a_reply_has_a_byte_bound_however_fast_the_bytes_come(self):
        line = NoisyLine(FakeClock(), 0.0)
        with self.assertRaises(cm.BadReply):
            cm.Session(line, clock=line.clock).info()
        self.assertEqual(len(line.waits), cm.MAX_REPLY_BYTES)


class CaptureTest(TempDirTest):
    def test_capture_pipes_into_a_new_archive_holding_the_exact_storage(self):
        device = FakeService()
        device.noise, device.trickle = frame(b"S6CM stale")[:-2] + b"\x00\x7d", True
        code, info, _ = run(["commission-info", "--port", ENDPOINT], device)
        self.assertEqual(code, 0)
        report, expected = json.loads(info), cm.Identity.decode(MACHINE_IDENTITY).report()
        self.assertEqual({name: report[name] for name in expected}, expected)
        self.assertEqual(report["mode"], cm.Mode(UNKNOWN).name)

        self.assertTrue(device.closed)
        code, document, _ = run(["--port", ENDPOINT, "commission-capture"], device)
        self.assertEqual((code, device.opened, device.closed), (0, ENDPOINT, True))
        path = self.dir / "original.json"
        code, _, _ = run(["archive-save", str(path)], stdin=document)
        self.assertEqual(code, 0)
        archive = cm.load_archive(path)
        self.assertEqual(path.read_bytes(), document)
        self.assertEqual(archive.capture, ORIGINAL + RECORD_BEFORE)
        self.assertEqual(archive.identity, cm.Identity.decode(MACHINE_IDENTITY))
        self.assertEqual(archive.provenance.endpoint, ENDPOINT)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [path.name])
        self.assertEqual(run(["archive-verify", str(path)])[:2], (0, document))
        self.assertNotIn(STAGE, device.ops)
        self.assertNotIn(INIT, device.ops)
        self.assertEqual(bytes(device.storage), ORIGINAL + RECORD_BEFORE)

    def test_an_archive_is_never_replaced(self):
        path = self.dir / "original.json"
        first = captured(FakeService()).encode()
        path.write_bytes(first)
        later = captured(FakeService(pattern(PERSIST, 3))).encode()
        code, _, failure = run(["archive-save", str(path)], stdin=later)
        self.assertEqual(code, 1)
        self.assertEqual(failure["error_type"], cm.ArchiveExists.__name__)
        self.assertEqual(path.read_bytes(), first)
        link, elsewhere = self.dir / "link.json", self.dir / "elsewhere.json"
        os.symlink(elsewhere, link)
        code, _, failure = run(["archive-save", str(link)], stdin=later)
        self.assertEqual((code, failure["error_type"]), (1, cm.ArchiveExists.__name__))
        self.assertFalse(elsewhere.exists())
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), sorted((path.name, link.name)))

    def test_an_archive_is_read_only_from_a_regular_file(self):
        path, link, fifo = self.dir / "original.json", self.dir / "link.json", self.dir / "fifo.json"
        archive = captured(FakeService())
        cm.save_archive(archive, path)
        os.symlink(path, link)
        os.mkfifo(fifo)  # no writer: opening it to read would wait for one
        self.assertEqual(run(["archive-verify", str(path)])[0], 0)
        self.assertEqual(run(["archive-verify", str(link)])[:2], (1, b""))
        code, document, failure = run(["archive-verify", str(fifo)])
        self.assertEqual((code, document, failure["error_type"]), (1, b"", cm.ArchiveError.__name__))
        with self.assertRaises(cm.ArchiveExists):
            cm.save_archive(archive, fifo)

    def test_a_silent_machine_gives_no_archive(self):
        device = FakeService()
        device.silent = {READ}
        code, document, failure = run(["commission-capture", "--port", ENDPOINT], device)
        self.assertEqual((code, document), (1, b""))
        self.assertEqual(failure["error_type"], cm.NoReply.__name__)
        self.assertEqual((device.opened, device.closed), (ENDPOINT, True))

    def test_storage_changing_during_the_capture_gives_no_archive(self):
        device = FakeService()
        device.changes_after_reads = -(-CAPTURE // MAX_CHUNK)  # once the first pass is read
        with self.assertRaises(cm.Mismatch) as unstable:
            captured(device)
        self.assertEqual(unstable.exception.subject, cm.Mismatch.CAPTURE)

    def test_existing_state_or_a_started_transaction_is_not_captured(self):
        slot = bytearray(ORIGINAL)
        slot[SLOT_MAGIC_OFFSET:SLOT_MAGIC_OFFSET + 4] = SLOT_MAGIC
        cases = {"existing extension state": (FakeService(bytes(slot)), cm.Mismatch.MODE)}
        archive = captured(FakeService())
        done = FakeService()
        cm.initialize(cm.Session(done), archive, first_install=True)
        cases["done"] = (done, cm.Mismatch.MODE)
        ready = FakeService().plan().index((MARKER, struct.pack("<H", READY_MARK)))
        for name, cut, subject in (("record body started", 3, cm.Mismatch.RECORD),
                                   ("ready", ready + 3, cm.Mismatch.MODE)):
            device = FakeService()
            device.power_fails_after = cut
            with self.assertRaises(cm.UncertainOutcome):
                cm.initialize(cm.Session(device), archive, first_install=True)
            device.restart()
            cases[name] = (device, subject)
        for name, (device, subject) in cases.items():
            with self.subTest(name), self.assertRaises(cm.Mismatch) as refused:
                captured(device)
            self.assertEqual(refused.exception.subject, subject)


class ArchiveTest(TempDirTest):
    def test_corrupt_or_foreign_archives_are_refused_before_the_port_opens(self):
        document = captured(FakeService()).encode()
        variants = {"truncated": document[:-40], "empty": b"", "oversized": document + bytes(cm.MAX_ARCHIVE_SIZE)}
        for position in range(0, len(document), 97):
            flipped = bytearray(document)
            flipped[position] ^= 1
            variants[f"bit flip at {position}"] = bytes(flipped)
        content = json.loads(document)
        foreign = {"schema": ("schema", 2), "layout": ("layout", {**content["layout"], "max_chunk": 30}),
                   "captured mode": ("captured_mode", cm.Mode(READY).name), "extra field": ("comment", "x")}
        for name, (field, value) in foreign.items():
            changed = {**content, field: value}
            del changed["archive_sha256"]
            changed["archive_sha256"] = hashlib.sha256(canonical(changed)).hexdigest()
            variants[name] = canonical(changed)
        for name, data in variants.items():
            with self.subTest(name):
                with self.assertRaises(cm.ArchiveError):
                    cm.Archive.decode(data)
                code, _, failure = run(["commission-initialize", "--first-install", "--port", ENDPOINT], stdin=data)
                self.assertEqual((code, failure["error_type"]), (1, cm.ArchiveError.__name__))
                path = self.dir / name
                path.write_bytes(data)
                self.assertEqual(run(["archive-verify", str(path)])[:2], (1, b""))


def canonical(content: dict) -> bytes:
    """The archive's documented canonical JSON: sorted keys, one-space indent, final newline."""
    return (json.dumps(content, sort_keys=True, indent=1) + "\n").encode()


class InitializeTest(unittest.TestCase):
    def initialize(self, device: FakeService, archive: cm.Archive) -> tuple[int, dict | None]:
        code, out, failure = run(["commission-initialize", "--first-install", "--port", ENDPOINT], device,
                                 archive.encode())
        return code, json.loads(out) if code == 0 else failure

    def assert_commissioned(self, device: FakeService) -> None:
        self.assertEqual(bytes(device.storage), bytes(PERSIST) + record(ORIGINAL, DONE_MARK))
        device.restart()
        self.assertEqual(device.mode, DONE)

    def test_usage_errors_open_nothing(self):
        archive = captured(FakeService())
        for argv in (["commission-initialize"], ["--first-install", "commission-capture"],
                     ["commission-info", "extra"], ["archive-verify"], ["--port", ENDPOINT, "archive-verify", "f"],
                     ["no-such-command"], ["commission-info"], ["commission-capture"],
                     ["--first-install", "commission-initialize"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as usage:
                run(argv, stdin=archive.encode())
            self.assertEqual(usage.exception.code, 2)

    def test_first_install_intent_is_required(self):
        archive = captured(FakeService())
        device = FakeService()
        with self.assertRaises(cm.IntentRequired):
            cm.initialize(cm.Session(device), archive, first_install=False)
        self.assertEqual(device.ops, [])

    def test_initialize_stages_the_archive_and_sends_init_once(self):
        device = FakeService()
        device.noise, device.trickle = b"\x7e\x7e\x7d", True
        archive = captured(device)
        argv = ["--first-install", "commission-initialize", "--port", ENDPOINT]  # options either side
        code, out, _ = run(argv, device, archive.encode())
        report = json.loads(out)
        self.assertEqual((code, device.opened, device.closed), (0, ENDPOINT, True))
        self.assertEqual((report["init_sent"], report["mode"]), (True, cm.Mode(DONE).name))
        self.assertEqual(bytes(device.staged), archive.capture)
        self.assertEqual(device.init_count(), 1)
        self.assert_commissioned(device)

        code, report = self.initialize(device, archive)
        self.assertEqual((code, report["init_sent"]), (0, False))
        self.assertEqual(device.init_count(), 1)

    def test_a_machine_unlike_the_archive_is_not_written(self):
        archive = captured(FakeService())
        other = FakeService()
        other.identity = MACHINE_IDENTITY[:-1] + bytes((MACHINE_IDENTITY[-1] ^ 1,))
        moved = FakeService()
        moved.layout = (LAYOUT[0], PERSIST, LAYOUT[2] + 0x800, *LAYOUT[3:])
        changed = FakeService(bytes(PERSIST))
        slot = bytearray(ORIGINAL)
        slot[SLOT_MAGIC_OFFSET:SLOT_MAGIC_OFFSET + 4] = SLOT_MAGIC
        refused = FakeService(bytes(slot))
        cases = {"identity": (other, cm.Mismatch.IDENTITY), "layout": (moved, cm.Mismatch.LAYOUT),
                 "persistence": (changed, cm.Mismatch.PERSISTENCE), "refused": (refused, cm.Mismatch.MODE)}
        for name, (device, subject) in cases.items():
            with self.subTest(name):
                before = bytes(device.storage)
                code, failure = self.initialize(device, archive)
                self.assertEqual((code, failure["error_type"], failure["subject"]),
                                 (1, cm.Mismatch.__name__, subject))
                self.assertEqual(device.ops.count(STAGE) + device.init_count(), 0)
                self.assertEqual(bytes(device.storage), before)

    def test_a_refused_stage_stops_before_init(self):
        device = FakeService()
        archive = captured(device)
        device.stage(0, bytes((archive.capture[0] ^ 1,)))
        with self.assertRaises(cm.Refused) as refused:
            cm.initialize(cm.Session(device), archive, first_install=True)
        self.assertEqual((refused.exception.op, refused.exception.status), (cm.Op.STAGE, cm.Status.BAD_REQUEST))
        self.assertEqual(device.init_count(), 0)
        self.assertEqual(bytes(device.storage), archive.capture)

    def test_a_lost_init_reply_is_uncertain_and_never_repeated(self):
        device = FakeService()
        archive = captured(device)
        device.silent = {INIT}
        code, failure = self.initialize(device, archive)
        self.assertEqual((code, failure["error_type"]), (1, cm.UncertainOutcome.__name__))
        self.assertEqual(device.init_count(), 1)
        self.assertEqual(device.ops[-1], INIT)
        device.silent = set()
        code, report = self.initialize(device, archive)
        self.assertEqual((code, report["init_sent"]), (0, False))
        self.assertEqual(device.init_count(), 1)
        self.assert_commissioned(device)

    def test_power_loss_in_any_init_phase_resumes_from_the_original_archive_only(self):
        plan = FakeService().plan()
        ready = plan.index((MARKER, struct.pack("<H", READY_MARK)))
        cuts = sorted({0, 1, 2, ready // 2, ready - 1, ready, ready + 1, (ready + len(plan)) // 2,
                       len(plan) - 2, len(plan) - 1})
        foreign = captured(FakeService(pattern(PERSIST, 5)))
        for cut in cuts:
            with self.subTest(writes=cut):
                device = FakeService()
                archive = captured(device)
                device.power_fails_after = cut
                with self.assertRaises(cm.UncertainOutcome):
                    cm.initialize(cm.Session(device), archive, first_install=True)
                device.power_fails_after = None
                device.restart()
                if cut > ready:
                    with self.assertRaises(cm.Mismatch) as wrong:
                        cm.initialize(cm.Session(device), foreign, first_install=True)
                    self.assertEqual(wrong.exception.subject, cm.Mismatch.RECORD)
                report = cm.initialize(cm.Session(device), archive, first_install=True)
                self.assertTrue(report["init_sent"])
                self.assertEqual(device.init_count(), 2)
                self.assert_commissioned(device)


if __name__ == "__main__":
    unittest.main()
