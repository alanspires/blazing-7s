"""Test helpers: repository paths, the CLI and machine-test runners, temporary directories,
a scripted serial line, a fake clock, the combined fake machine (stock SAS, the firmware
extension and a game model), sample values for any field layout and nonblank test bytes."""

from __future__ import annotations

import datetime as dt
import io
import json
import random
import struct
import tempfile
import unittest
import zlib
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import sas
import sas_commands as sc
import sas_options as so
import sas_profile as sp
import sas_protocol as protocol
from sas_tables import DENOMINATIONS

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tools" / "fixtures"
MAIN_ROM = REPO / "roms" / "s6000_main_S6M00002B5.bin"
# The ROM signatures the machine reported for each seed, and the images they cover.
ROM_SIGNATURES = FIXTURES / "s6000_rom_signatures.json"
# Meter codes the samples use for "meter" fields, per meter code size: all in Table C-7.
SAMPLE_METERS = {1: (0x05, 0x0D), 2: (0x7F, 0x0C)}


def read_jsonl(path: Path) -> list:
    """The records of a JSON-lines file, one per line."""
    return [json.loads(line) for line in path.read_text().splitlines()]


def pattern(size: int, seed: int) -> bytes:
    """Unrelated nonblank bytes that include the commissioning frame delimiter and escape."""
    return bytes((i * 37 + seed) & 0xFF for i in range(size))


def run_cli(argv: list[str], line: protocol.Line | None = None) -> tuple[int, str, str]:
    """Exit status, stdout and stderr of sas.py's command line on `line`, given as --port.
    Without a line there is no --port, and opening the serial port fails the test."""
    def open_line(port: str) -> protocol.Line:
        if line is None:
            raise AssertionError(f"{argv}: opened {port}")
        return line

    if line is not None:
        argv = ["--port", "/dev/fake", *argv]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = sas.main(argv, open_line=open_line)
    return code, out.getvalue(), err.getvalue()


def run_machine_test(run: Callable[[protocol.Host, Callable[[dict], None]], bool],
                     machine: FakeS6000) -> tuple[bool, list[dict]]:
    """A machine test's verdict and the records it reported, run against `machine`."""
    records: list[dict] = []
    ok = run(protocol.Host(machine, machine.ADDRESS), records.append)
    return ok, records


def sent_requests(line: FakeLine, name: str) -> list[dict]:
    """The values of every `name` long poll sent on `line`, in order, repeats included."""
    command = sc.BY_NAME[name]
    requests = []
    for first, rest in line.sent:
        if first == protocol.SYNC_POLL or not rest or rest[0] != command.code:
            continue
        data = protocol.check_crc(bytes((first,)) + rest)[2:] if command.crc else rest[1:]
        requests.append(dict(sc.decode(command.request, data, length_byte=command.request_length)))
    return requests


class TempDirTest(unittest.TestCase):
    """Each test gets a new empty directory, self.dir, removed afterwards."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.dir = Path(directory.name)


class FakeLine:
    """Records what the host sends and answers from a script, one whole message per read, as
    SerialLine.read_message returns them. Out of replies it is silent, or raises `then`
    (KeyboardInterrupt is how `poll` ends). `timeouts` holds each read's reply wait."""

    def __init__(self, replies=(), then: BaseException | None = None):
        self.sent: list[tuple[int, bytes]] = []
        self.timeouts: list[float] = []
        self.replies = list(replies)
        self.then = then
        self.last_gap = 0.0
        self.closed = False

    def send(self, first, rest):
        self.sent.append((first, rest))

    def wait(self, seconds):
        pass

    def read_message(self, timeout):
        self.timeouts.append(timeout)
        if self.replies:
            return self.replies.pop(0)
        if self.then is not None:
            raise self.then
        return b""

    def close(self):
        self.closed = True

    def messages(self) -> list[bytes]:
        return [bytes((first,)) + rest for first, rest in self.sent]


def sample_values(fields: tuple) -> tuple[list[str], list[tuple[str, object]]]:
    """Values for every field, as the CLI takes them and as decode returns them. Each field
    gets a different value, so a swapped or misplaced field shows."""
    texts: list[str] = []
    decoded: list[tuple[str, object]] = []
    counter = iter(range(1, 1000))
    for f in fields:
        if isinstance(f, sc.Repeat):
            items = []
            for group in range(2):
                item = {}
                for inner in f.fields:
                    text, item[inner.name] = _sample(inner, next(counter), group)
                    texts.append(text)
                items.append(item)
            decoded.append(("items", items))
            continue
        text, value = _sample(f, next(counter), 0)
        texts.append(text)
        decoded.append((f.name, value))
    return texts, decoded


def _sample(f: sc.Field, n: int, group: int) -> tuple[str, object]:
    if f.name == "meter":
        code = SAMPLE_METERS[f.size][group]
        return f"0x{code:X}", code
    size = f.size if isinstance(f.size, int) else None
    if f.enc == sc.BCD:
        return str(n), n
    if f.enc == sc.DIGITS:
        digits = str(n).zfill(2 * size)
        return digits, digits
    if f.enc == sc.BIN:
        return str(n), n
    if f.enc == sc.BITS:
        return str(n), f"0x{n:0{2 * size}X}"
    if f.enc == sc.HEX:
        raw = [f"{n:02x}"] * (size or 2)
        return "".join(raw), " ".join(raw)
    text = chr(ord("A") + n % 26) * (size or 3)  # ASCII and TEXT
    return text, text


class FakeClock:
    """Monotonic seconds for fakes: time passes only when a fake line waits or reads silence."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# firmware/INTEGRATION.md, "Wire protocol v1": restated here so the fake checks the wire
# against the contract rather than against sas_extension's own encoding.
EXTENSION_POLL = 0xE0
MAGIC = b"S6RE"
WIRE_VERSION = 1
INFO, STATUS, READ, READ_OPTIONS = 0, 1, 2, 3
BEGIN, CHUNK, COMMIT, RESTORE, SET_OPTION = 16, 17, 18, 19, 20
ARM, CANCEL, SPIN, POSITION, JOG = 32, 33, 34, 35, 36
QUERIES = (INFO, STATUS, READ, READ_OPTIONS)
OK, ACCEPTED, BAD_REQUEST, BAD_ID, BUSY, FAULT, INVALID_PROFILE, INCOMPLETE, INTERRUPTED, UNSUPPORTED = (
    0, 1, 2, 4, 5, 6, 7, 8, 9, 10)
MAX_PAYLOAD = 64
OPTIONS_SIZE = so.OPTIONS_SIZE
PROFILE_CAP, OUTCOME_CAP, SPIN_CAP, POSITION_CAP, JOG_CAP, OPTIONS_CAP, SET_OPTION_CAP = (
    0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40)
ALL_CAPS = (PROFILE_CAP | OUTCOME_CAP | SPIN_CAP | POSITION_CAP | JOG_CAP
            | OPTIONS_CAP | SET_OPTION_CAP)
# firmware/src/fw.h: the first firmware's INFO limits.
LIMITS = {"max_chunk": 62, "max_lines": 5, "max_bet_per_line": 3, "max_total_bet": 15,
          "stops": sp.STOPS, "max_award": 1500}
SLOT_DOOR = 0x11  # Table A-1: each door's open code; its close code is one more
DOORS = (SLOT_DOOR, 0x13, 0x15, 0x19, 0x1D)
GAME_STARTED, GAME_ENDED, POWER_APPLIED, AFT_COMPLETE = 0x7E, 0x7F, 0x17, 0x69


class FakeExtension:
    """The extension's side of its private long poll, per the wire contract, keeping the state
    the host reads back. With `action_status` set, every action answers it and changes nothing
    else; otherwise `actions(op, payload)` decides, as FakeS6000's game model does. A machine
    acts even when its reply is lost on the line."""

    def __init__(self, capabilities: int = ALL_CAPS, last_id: int = 41, action_status: int | None = None,
                 options_raw: bytes | None = None, **limits: int):
        self.capabilities = capabilities
        self.limits = LIMITS | limits
        self.last = (last_id, ARM, b"")  # id, op and payload of the latest mutation
        self.last_status = OK
        self.fault = False
        self.active: bytes | None = None
        self.generation = 3
        self.profile_crc = 0
        self.options_raw = so.default_options_block() if options_raw is None else options_raw
        self.staged: tuple[int, bytearray, set[int]] | None = None
        self.action_status = action_status
        self.actions: Callable[[int, bytes], int] = lambda op, payload: ACCEPTED
        self.corrupt_commit = False
        self.tamper: Callable[[bytes], bytes] | None = None  # rewrites reply data after the command byte
        self.requests: list[tuple[int, int, bytes]] = []
        self.executed: list[int] = []

    @property
    def next_id(self) -> int:
        return self.last[0] + 1

    def mutations(self) -> list[tuple[int, int, bytes]]:
        return [r for r in self.requests if r[0] not in QUERIES]

    def profile(self) -> sp.Profile:
        return sp.stock_profile() if self.active is None else sp.decode(self.active)

    def boot(self) -> None:
        """Staging is volatile, and boot marks a pending action INTERRUPTED without resuming it."""
        self.staged = None
        if self.last_status == ACCEPTED:
            self.last_status = INTERRUPTED

    def handle(self, address: int, rest: bytes) -> tuple[int, bytes]:
        """The request's op and the reply message to it."""
        message = protocol.check_crc(bytes((address,)) + rest)
        assert message[1] == EXTENSION_POLL, message.hex(" ")
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
        return op, protocol.with_crc(bytes((address, EXTENSION_POLL)) + framed)

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
        if op == READ_OPTIONS:
            if not (self.capabilities & OPTIONS_CAP):
                return UNSUPPORTED, b""
            offset, count = struct.unpack("<HB", payload)
            assert count <= MAX_PAYLOAD and offset + count <= OPTIONS_SIZE, (offset, count)
            return OK, self.options_raw[offset:offset + count]
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
            if self.staged is None:
                return INCOMPLETE
            offset, data = int.from_bytes(payload[:2], "little"), payload[2:]
            assert len(data) <= self.limits["max_chunk"], len(data)
            _, buffer, covered = self.staged
            buffer[offset:offset + len(data)] = data
            covered.update(range(offset, offset + len(data)))
            return OK
        if op == COMMIT:
            if self.staged is None:
                return INCOMPLETE
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
        if op == SET_OPTION:
            if not (self.capabilities & SET_OPTION_CAP):
                return UNSUPPORTED
            if len(payload) < 3 or payload[1] + 2 != len(payload):
                return BAD_REQUEST
            offset, count = payload[0], payload[1]
            if offset + count > OPTIONS_SIZE:
                return BAD_REQUEST
            buf = bytearray(self.options_raw)
            buf[offset:offset + count] = payload[2:]
            self.options_raw = bytes(buf)
            return OK
        return self.action_status if self.action_status is not None else self.actions(op, payload)


class FakeS6000(FakeLine):
    """A machine at ADDRESS that answers polls the way the S6000's handlers do
    (notes/sas-j10.md, "Write test" and "Money test"), keeping the state they change, with the
    firmware extension (`extension`, a FakeExtension) and a game model behind it.

    Options and hooks for tests: with `shows_disable` False the 6E probe does not show
    disabled play; after `silent_after` (a command name) it stops answering; with
    `needs_sync` it ignores polls until it sees a sync, at start and after each boot; `lose`
    holds command names and extension ops whose replies are lost although the machine acts;
    `failure` (command name or op, exception, "send" or "read") acts, then raises from the
    host's write or its wait for the reply; `inject` puts stale bytes in front of the next
    reply or in its place; `hang_up` makes the line raise SerialClosed; `busy_replies` answers
    that many long polls busy without acting; `clock`, a FakeClock, records every send's time
    in `send_times` and advances on waits and silent reads."""

    ADDRESS = 1
    ASSET = 12345
    DENOMINATION = 0x06
    CENTS_PER_CREDIT = int(DENOMINATIONS[DENOMINATION] * 100)
    ALL_BILLS = 0xFFFFFFFF
    TRANSFER_LIMIT_CENTS = 898500

    def __init__(self, shows_disable: bool = True, silent_after: str | None = None,
                 refuses_transfers_out: bool = False, needs_sync: bool = False,
                 clock: FakeClock | None = None, extension: FakeExtension | None = None, seed: int = 0):
        super().__init__()
        self.shows_disable = shows_disable
        self.silent_after = silent_after
        self.refuses_transfers_out = refuses_transfers_out
        self.transfer_refusal: int | None = None  # a Table 8.3e code every transfer gets instead
        self.needs_sync = needs_sync
        self.synced = not needs_sync
        self.clock = clock
        self.send_times: list[float] = []
        self.silent = False
        self.game_enabled = True
        self.shutdown_flag = 0  # 0x43D150; the main loop's 1 -> 2 and 3 -> 0 happen at once here
        self.sound = (1, 1)
        self.machine_time = dt.datetime(2026, 10, 18, 9, 30, 0)  # noqa: DTZ001 -- SAS time is zone-less
        self.clock_writable = True
        self.real_time = False
        self.locked = False
        self.registration = (0x80, bytes(20), 0)  # status, key, POS ID
        self.pending: list[int] = []  # exceptions not yet reported
        self.skip_replies = 0  # polls to leave unanswered, as the machine does after startup
        self.busy_replies = 0
        self.lose: set = set()
        self.failure: tuple[object, BaseException, str] | None = None
        self._read_failure: BaseException | None = None
        self.stale: list[tuple[bytes, bool]] = []
        self.hung_up = False
        self.bill_mask = 0x03
        self.game_delay = 7
        self.available_transfers = 0x13  # Table 8.2b: in and out, bonus to machine
        self.meters = {0x0C: 1014, 0xA0: 0, 0xA1: 0, 0xB8: 0, 0xB9: 0}
        self.game_meters = dict.fromkeys((f.name for f in sc.BY_NAME["meters"].reply), 0)  # 1C, in order
        self.legacy_deductible = 7000
        self.last_bonus = 0
        self.last_transfer: tuple | None = None  # the 72 reply for the most recent transfer
        self.doors: set[int] = set()  # open codes of the doors standing open
        self.stops = (0, 0, 0)  # the reels' last stops, as 8F reports them
        self.random = random.Random(seed)
        self.games_complete = True  # a started game ends at the next poll
        self.game: tuple[int, tuple[int, ...], bool] | None = None  # bet, stops, bound to an arm
        self.arm: tuple[int, ...] | None = None
        self.arm_state: str | None = None  # "pending", "bound" or "held"
        self.extension = extension or FakeExtension()
        self.extension.actions = self._extension_action

    def state(self) -> tuple:
        return (self.game_enabled, self.shutdown_flag, self.sound, self.real_time, self.locked,
                self.registration, self.machine_time)

    # Test controls

    def inject(self, data: bytes, *, replace: bool = False) -> None:
        """Stale bytes for the next reply: in front of it, or as all of it."""
        self.stale.append((data, replace))

    def hang_up(self) -> None:
        self.hung_up = True

    def reconnect(self) -> None:
        self.hung_up = self.closed = False

    def open_door(self, door: int = SLOT_DOOR) -> None:
        """Opening a door posts its exception and sets the interlock; a pending arm is
        interrupted, as firmware/src/game.c advance() does."""
        assert door in DOORS, door
        self.doors.add(door)
        self.pending.append(door)
        if door == SLOT_DOOR:
            self.game_meters["slot_door_opened"] += 1
        if self.arm_state == "pending":
            self.arm, self.arm_state = None, None
            if self.extension.last[1] == ARM and self.extension.last_status == ACCEPTED:
                self.extension.last_status = INTERRUPTED

    def close_door(self, door: int = SLOT_DOOR) -> None:
        self.doors.discard(door)
        self.pending.append(door + 1)

    def power_off(self) -> None:
        self.silent = True

    def power_on(self) -> None:
        self.silent = False
        self.boot()

    def boot(self) -> None:
        """A restart: staging dropped, a pending action interrupted, events off, a sync needed."""
        self.extension.boot()
        if self.arm_state == "pending":
            self.arm, self.arm_state = None, None
        self.game = None
        self.real_time = False
        self.synced = not self.needs_sync
        self.game_meters["power_reset"] += 1
        self.pending.append(POWER_APPLIED)

    def complete_game(self, status: int = OK) -> None:
        """End the running game: scored with sas_profile.score and paid on OK; a bound arm is
        consumed on OK and held otherwise."""
        bet, stops, bound = self.game
        self.game = None
        if status == OK:
            profile = self.extension.profile()
            result = sp.score(profile, stops, bet)
            self.meters[0x0C] += result.total_award - result.total_bet
            self.game_meters["coin_in"] += result.total_bet
            self.game_meters["coin_out"] += result.total_award
            self.game_meters["games_played"] += 1
            self.game_meters["games_won"] += result.total_award > 0
            self.stops = stops
            self.pending += [GAME_STARTED, GAME_ENDED]
            if bound:
                self.arm, self.arm_state = None, None
        elif bound:
            self.arm_state = "held"
        if self.extension.last[1] == SPIN:
            self.extension.last_status = status

    # The line

    def send(self, first, rest):
        super().send(first, rest)
        if self.clock is not None:
            self.send_times.append(self.clock())
        if self.hung_up:
            raise sas.SerialClosed("fake serial line hung up")
        if self.silent:
            return
        if first == protocol.SYNC_POLL:
            self.synced = True
            return
        if not self.synced:
            return
        if self.game is not None and self.games_complete:
            self.complete_game()
        if self.skip_replies:
            self.skip_replies -= 1
            return
        if first == self.ADDRESS | protocol.GENERAL_POLL_FLAG:
            code = self.pending.pop(0) if self.pending else 0x1F
            self._reply(self._event(code) if self.real_time else bytes((code,)))
            return
        if self.busy_replies:
            self.busy_replies -= 1
            self._reply(bytes((self.ADDRESS, protocol.BUSY)))
            return
        if rest[0] == EXTENSION_POLL:
            key, reply = self.extension.handle(first, rest)
        else:
            command = sc.COMMANDS[rest[0]]
            key = command.name
            if self.real_time and self.pending:  # §12.3: an event goes out instead of the reply
                self._reply(self._event(self.pending.pop(0)))
                return
            data = rest[1:-2] if command.crc else rest[1:]
            values = dict(sc.decode(command.request, data, length_byte=command.request_length))
            answer = getattr(self, "_" + command.name.replace("-", "_"))(values)
            reply = b""
            if answer == "ACK":
                reply = bytes((self.ADDRESS,))
            elif answer is not None:
                body = sc.encode(command.reply, [str(v) for v in answer], length_byte=command.reply_length)
                reply = protocol.with_crc(bytes((self.ADDRESS, command.code)) + body)
            self.silent = command.name == self.silent_after
        if self.failure is not None and self.failure[0] == key:
            _, exception, stage = self.failure
            self.failure = None
            if stage == "send":
                raise exception
            self._read_failure = exception
            return
        if key not in self.lose:
            self._reply(reply)

    def read_message(self, timeout):
        if self.hung_up:
            raise sas.SerialClosed("fake serial line hung up")
        if self._read_failure is not None:
            exception, self._read_failure = self._read_failure, None
            raise exception
        message = super().read_message(timeout)
        if not message and self.clock is not None:
            self.clock.advance(timeout)
        return message

    def wait(self, seconds):
        if self.clock is not None:
            self.clock.advance(seconds)

    def _reply(self, message: bytes) -> None:
        if self.stale:
            data, replace = self.stale.pop(0)
            message = data if replace else data + message
        if message:
            self.replies.append(message)

    def _event(self, code: int) -> bytes:
        return protocol.with_crc(bytes((self.ADDRESS, protocol.EVENT, code)))

    # The game behind the extension's actions (firmware/src/game.c, simplified)

    def _ready_for_spin(self) -> bool:
        return not self.doors and self.game_enabled and self.shutdown_flag != 2

    def _extension_action(self, op: int, payload: bytes) -> int:
        if op == ARM:
            if self.arm is not None or self.game is not None or not self._ready_for_spin():
                return BUSY
            self.arm, self.arm_state = tuple(payload), "pending"
            return ACCEPTED
        if op == CANCEL:
            if self.arm is None:
                return BAD_REQUEST
            if self.arm_state == "bound":
                return BUSY
            self.arm, self.arm_state = None, None
            return OK
        if op == SPIN:
            profile = self.extension.profile()
            bet, stops = payload[0], tuple(payload[1:]) or None
            if not 1 <= bet <= profile.max_bet_per_line:
                return BAD_REQUEST
            if not self._ready_for_spin() or self.meters[0x0C] < bet * profile.lines:
                return BUSY
            bound = self.arm_state == "pending"
            if bound:
                if stops is not None and stops != self.arm:
                    return BAD_REQUEST  # never silently replace an arm
                stops, self.arm_state = self.arm, "bound"
            elif self.game is not None or self.arm is not None:
                return BUSY
            if stops is None:
                stops = tuple(self.random.randrange(sp.STOPS) for _ in range(sp.REELS))
            self.game = (bet, stops, bound)
            return ACCEPTED
        return UNSUPPORTED  # diagnostic reel moves are disabled in the installed firmware

    # Stock SAS handlers

    def _authentication(self, v):
        assert (v["action"], v["addressing"], v["component"]) == (2, 0, "ff"), v
        playing = self.game_enabled and self.shutdown_flag != 2
        return 0, 0xC5 if playing or not self.shows_disable else 0x80

    def _enable_game(self, v):
        self.game_enabled = bool(v["enable"])
        return "ACK"

    def _shutdown(self, v):
        self.shutdown_flag = 2
        return "ACK"

    def _startup(self, v):
        if self.shutdown_flag == 2:
            self.shutdown_flag = 0
            self.skip_replies = protocol.POLL_TRIES  # the machine left a whole poll unanswered
        return "ACK"

    def _sound_off(self, v):
        self.sound = (0, self.sound[1])
        return "ACK"

    def _reel_sounds_off(self, v):
        self.sound = (self.sound[0], 0)
        return "ACK"

    def _sound_on(self, v):
        self.sound = (1, 1)
        return "ACK"

    def _date_time(self, v):
        return self.machine_time.strftime("%m%d%Y"), self.machine_time.strftime("%H%M%S")

    def _set_date_time(self, v):
        """No reply at all, as FUN_0002eaac."""
        if self.clock_writable:
            stamp = v["date"] + v["time"]
            self.machine_time = dt.datetime.strptime(stamp, "%m%d%Y%H%M%S")  # noqa: DTZ007 -- SAS time is zone-less

    def _real_time_events(self, v):
        self.real_time = bool(v["enable"])
        return "ACK"

    def _meters(self, v):
        return tuple(self.game_meters.values())

    def _reel_stops(self, v):
        return (bytes(self.stops).hex() + "ff" * (9 - len(self.stops)),)

    def _aft_lock(self, v):
        if v["lock_code"] == 0 and not self.locked:
            self.locked = True
            self.pending.append(0x6F)
        elif v["lock_code"] == 0x80:
            self.locked = False
        aft_status = 0xF0 | (0x08 if self.registration[0] == 1 else 0)
        return (self.ASSET, 0 if self.locked else 0xFF, self.available_transfers, 0x01, aft_status, 70,
                self.meters[0x0C] * self.CENTS_PER_CREDIT, 0, 0,
                self.TRANSFER_LIMIT_CENTS, "10172026", 0)

    def _aft_register(self, v):
        status, key, pos_id = self.registration
        code = v["code"]
        new_key = bytes.fromhex(v.get("registration_key", "").replace(" ", ""))
        if code == 0x00:
            self.registration = (0x00, new_key, v["pos_id"])
        elif code == 0x01:
            self.registration = (0x01, new_key, v["pos_id"]) if status == 0 and any(key) else (0x80, key, pos_id)
        elif code == 0x80:
            if status != 0x80:
                self.pending.append(0x6E)
            self.registration = (0x80, key, pos_id)
        status, key, pos_id = self.registration
        return status, self.ASSET, key.hex(), pos_id

    def _auto_rebet(self, v):
        return "ACK"

    def _reset_handpay(self, v):
        return (0x02,)

    def _handpay_reset_method(self, v):
        return (0x02,)

    def _validation_number(self, v):
        return (0x80,)

    def _aft_receipt_data(self, v):
        return "ACK"

    def _set_validation_id(self, v):
        return None  # system validation: the handler answers only for secure enhanced

    def _validation_status(self, v):
        self.pending.append(0x3F)
        return self.ASSET, 0x3F, 30, 30

    def _extended_ticket_data(self, v):
        return (0,)

    def _ticket_data_set(self, v):
        return (0,)

    def _bills_on(self, v):
        self.bill_mask = self.ALL_BILLS
        return "ACK"

    def _bills_off(self, v):
        self.bill_mask = 0
        return "ACK"

    def _configure_bills(self, v):
        self.bill_mask = int(v["bills"], 16)
        return "ACK"

    def _delay_game(self, v):
        self.game_delay = v["delay"]
        return "ACK"

    def _info(self, v):
        return "BS", "105", self.DENOMINATION, 3, 0, "0x0168", "E59521", "9085"

    def _features(self, v):
        return 0, "0xA6", "0x5D", "0x01", "000000"

    def _credits(self, v):
        return (self.meters[0x0C],)

    def _extended_meters(self, v):
        values = [0]
        for item in v.get("items", []):
            values += [item["meter"], self.meters.get(item["meter"], 0)]
        return values

    def _legacy_bonus(self, v):
        credits = int(v["amount"])
        self.meters[0x0C] += credits
        self.legacy_deductible += credits
        self.last_bonus = credits
        self.pending.append(0x7C)
        return "ACK"

    def _legacy_bonus_meters(self, v):
        return 0, self.legacy_deductible, 0, 0

    def _legacy_bonus_win(self, v):
        return 0, 0, 0, self.last_bonus

    def _aft_transfer(self, v):
        """Cashable in-house transfers complete at once and post 69; cashouts must be full."""
        if v["transfer_code"] in (0xFE, 0xFF):
            return self.last_transfer or (0, 0xFF, 0xFF)
        kind, cents = v["type"], v["cashable_cents"]
        credits = cents // self.CENTS_PER_CREDIT
        status = 0x00
        if self.transfer_refusal is not None:
            status, cents = self.transfer_refusal, 0
        elif kind == 0x00 and cents > self.TRANSFER_LIMIT_CENTS:
            status, cents = 0x84, 0
        elif kind == 0x00:
            self.meters.update({0x0C: self.meters[0x0C] + credits, 0xA0: self.meters[0xA0] + cents,
                                0xA1: self.meters[0xA1] + 1})
        elif kind == 0x80 and not self.refuses_transfers_out:
            if credits != self.meters[0x0C]:
                status, cents = 0x86, 0
            else:
                self.meters.update({0x0C: 0, 0xB8: self.meters[0xB8] + cents,
                                    0xB9: self.meters[0xB9] + 1})
        else:
            status, cents = 0x87, 0
        self.pending.append(AFT_COMPLETE)
        self.last_transfer = (1, status, 0xFF, kind, cents, 0, 0, 0, self.ASSET, v["transaction_id"],
                              "10182026", "120000", "00000000", 0, 0, 0, 0)
        return (0, 0x40, 0xFF, kind, v["cashable_cents"], 0, 0, 0, self.ASSET, v["transaction_id"],
                "00000000", "000000", "00000000", 0, 0, 0, 0)
