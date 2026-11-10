"""Test helpers: repository paths, the CLI and machine-test runners, temporary directories,
a scripted serial line, sample values for any field layout and nonblank test bytes."""

from __future__ import annotations

import datetime as dt
import io
import json
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import sas
import sas_commands as sc
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


class FakeS6000(FakeLine):
    """A machine at ADDRESS that answers the polls the write test sends the way the S6000's
    handlers do (notes/sas-j10.md, "Write test"), keeping the state they change. With
    `shows_disable` False, the 6E probe does not show disabled play; after `silent_after` (a
    command name) it stops answering."""

    ADDRESS = 1
    ASSET = 12345
    DENOMINATION = 0x06
    CENTS_PER_CREDIT = int(DENOMINATIONS[DENOMINATION] * 100)
    ALL_BILLS = 0xFFFFFFFF

    def __init__(self, shows_disable: bool = True, silent_after: str | None = None,
                 refuses_transfers_out: bool = False):
        super().__init__()
        self.shows_disable = shows_disable
        self.silent_after = silent_after
        self.refuses_transfers_out = refuses_transfers_out
        self.silent = False
        self.game_enabled = True
        self.shutdown_flag = 0  # 0x43D150; the main loop's 1 -> 2 and 3 -> 0 happen at once here
        self.sound = (1, 1)
        self.clock = dt.datetime(2026, 10, 18, 9, 30, 0)  # SAS carries civil time without a zone
        self.real_time = False
        self.locked = False
        self.registration = (0x80, bytes(20), 0)  # status, key, POS ID
        self.pending: list[int] = []  # exceptions not yet reported
        self.skip_replies = 0  # polls to leave unanswered, as the machine does after startup
        self.bill_mask = 0x03
        self.game_delay = 7
        self.meters = {0x0C: 1014, 0xA0: 0, 0xA1: 0, 0xB8: 0, 0xB9: 0}
        self.legacy_deductible = 7000
        self.last_bonus = 0
        self.last_transfer: tuple | None = None  # the 72 reply for the most recent transfer

    def state(self) -> tuple:
        return (self.game_enabled, self.shutdown_flag, self.sound, self.real_time, self.locked,
                self.registration, self.clock)

    def send(self, first, rest):
        super().send(first, rest)
        if self.silent or first == protocol.SYNC_POLL:
            return
        if self.skip_replies:
            self.skip_replies -= 1
            return
        if first == self.ADDRESS | protocol.GENERAL_POLL_FLAG:
            code = self.pending.pop(0) if self.pending else 0x1F
            self.replies.append(self._event(code) if self.real_time else bytes((code,)))
            return
        command = sc.COMMANDS[rest[0]]
        if self.real_time and self.pending:  # §12.3: an event goes out instead of the reply
            self.replies.append(self._event(self.pending.pop(0)))
            return
        data = rest[1:-2] if command.crc else rest[1:]
        values = dict(sc.decode(command.request, data, length_byte=command.request_length))
        reply = getattr(self, "_" + command.name.replace("-", "_"))(values)
        if reply == "ACK":
            self.replies.append(bytes((self.ADDRESS,)))
        elif reply is not None:
            body = sc.encode(command.reply, [str(v) for v in reply], length_byte=command.reply_length)
            self.replies.append(protocol.with_crc(bytes((self.ADDRESS, command.code)) + body))
        self.silent = command.name == self.silent_after

    def _event(self, code: int) -> bytes:
        return protocol.with_crc(bytes((self.ADDRESS, protocol.EVENT, code)))

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
        return self.clock.strftime("%m%d%Y"), self.clock.strftime("%H%M%S")

    def _set_date_time(self, v):
        self.clock = dt.datetime.strptime(v["date"] + v["time"], "%m%d%Y%H%M%S")  # SAS carries no zone

    def _real_time_events(self, v):
        self.real_time = bool(v["enable"])
        return "ACK"

    def _aft_lock(self, v):
        if v["lock_code"] == 0 and not self.locked:
            self.locked = True
            self.pending.append(0x6F)
        elif v["lock_code"] == 0x80:
            self.locked = False
        aft_status = 0xF0 | (0x08 if self.registration[0] == 1 else 0)
        return (self.ASSET, 0 if self.locked else 0xFF, 0x13, 0x01, aft_status, 70,
                self.meters[0x0C] * self.CENTS_PER_CREDIT, 0, 0,
                898500, "10172026", 0)

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
        if kind == 0x00:
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
        self.pending.append(0x69)
        self.last_transfer = (1, status, 0xFF, kind, cents, 0, 0, 0, self.ASSET, v["transaction_id"],
                              "10182026", "120000", "00000000", 0, 0, 0, 0)
        return (0, 0x40, 0xFF, kind, v["cashable_cents"], 0, 0, 0, self.ASSET, v["transaction_id"],
                "00000000", "000000", "00000000", 0, 0, 0, 0)
