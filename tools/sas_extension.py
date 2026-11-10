"""Host side of the S6000 firmware extension's private long poll (firmware/INTEGRATION.md,
"Wire protocol v1"; the firmware's contract is firmware/src/fw.h).

The extension answers long poll EXTENSION_POLL, which is not a standard SAS command and is
deliberately absent from the stock catalogue in `sas_commands`. Its data after the command
byte is one length byte, then

    request: MAGIC, VERSION, op, id (u64 LE), payload
    reply:   MAGIC, VERSION, op, status, id (u64 LE), payload

Queries carry id 0 and may be repeated. Mutations carry consecutive IDs starting at the
next_id an INFO reply gives, are sent exactly once, and a mutation whose reply is missing or
malformed is reported as UncertainOutcome with its ID: whether the machine acted is unknown.
The firmware returns a mutation's recorded status without acting again only for the same
ID with the same request, so the one way to repeat it is explicitly, with that ID.

A reply status is the machine's answer to the request, not proof that a game action has
finished: ACCEPTED means the action was taken on, and STATUS reports its later state.

Reply wait: every request waits the SAS host's own timeout for its reply's first byte,
except the profile switches in REPLY_TIMEOUTS. COMMIT_PROFILE and RESTORE_STOCK write the
profile journal (and for a commit the slot) to storage and rewrite the native game
configuration before they reply (firmware/src/core.c, commit_profile and restore_stock).
Live, their first reply bytes came after 436 ms and 320 ms, past the host's 0.25 s,
while every other request answered within tens of milliseconds (notes/firmware-bringup.md,
"Measured profile reply latency"). Those are single observations, not an upper bound:
PROFILE_SWITCH_REPLY_TIMEOUT is the probe window that saw them, and a reply later than it
is still UncertainOutcome, never a reason to send again.
"""

from __future__ import annotations

import enum
import struct
import zlib
from collections.abc import Iterator
from dataclasses import dataclass

import sas_profile as sp
import sas_protocol as sas

EXTENSION_POLL = 0xE0  # the private long poll in the pinned ROM's extension
MAGIC = b"S6RE"
VERSION = 1
REQUEST_HEADER = len(MAGIC) + 2 + 8  # magic, version, op, id
REPLY_HEADER = REQUEST_HEADER + 1  # and status
MAX_PAYLOAD = 64
CHUNK_OFFSET = struct.Struct("<H")  # PROFILE_CHUNK payload: offset, then the bytes
READ_REQUEST = struct.Struct("<HB")  # READ_PROFILE payload: offset, count
BEGIN_REQUEST = struct.Struct("<HI")  # BEGIN_PROFILE payload: total length, CRC-32
INFO = struct.Struct("<IQHBBBBBI")
STATUS = struct.Struct("<QBBBIIB")
ID = struct.Struct("<Q")
ID_MAX = (1 << 64) - 1
PROFILE_SWITCH_REPLY_TIMEOUT = 2.0  # seconds to the first reply byte; see the module notes


class Op(enum.IntEnum):
    INFO = 0
    STATUS = 1
    READ_PROFILE = 2
    BEGIN_PROFILE = 16
    PROFILE_CHUNK = 17
    COMMIT_PROFILE = 18
    RESTORE_STOCK = 19
    ARM_OUTCOME = 32
    CANCEL_OUTCOME = 33
    BET_SPIN = 34
    POSITION = 35
    JOG = 36


QUERIES = frozenset((Op.INFO, Op.STATUS, Op.READ_PROFILE))


class Status(enum.IntEnum):
    OK = 0
    ACCEPTED = 1
    BAD_REQUEST = 2
    BAD_VERSION = 3
    BAD_ID = 4
    BUSY = 5
    FAULT = 6
    INVALID_PROFILE = 7
    INCOMPLETE = 8
    INTERRUPTED = 9
    UNSUPPORTED = 10


class Capability(enum.IntFlag):
    PROFILE = 0x01
    OUTCOME = 0x02
    SPIN = 0x04
    POSITION = 0x08
    JOG = 0x10


REQUIRES = {
    Op.BEGIN_PROFILE: Capability.PROFILE, Op.PROFILE_CHUNK: Capability.PROFILE,
    Op.COMMIT_PROFILE: Capability.PROFILE, Op.RESTORE_STOCK: Capability.PROFILE,
    Op.ARM_OUTCOME: Capability.OUTCOME, Op.CANCEL_OUTCOME: Capability.OUTCOME,
    Op.BET_SPIN: Capability.SPIN, Op.POSITION: Capability.POSITION, Op.JOG: Capability.JOG,
}
ACTIONS = frozenset((Op.ARM_OUTCOME, Op.CANCEL_OUTCOME, Op.BET_SPIN, Op.POSITION, Op.JOG))
# Requests whose reply wait is not the host's own timeout.
REPLY_TIMEOUTS = {Op.COMMIT_PROFILE: PROFILE_SWITCH_REPLY_TIMEOUT,
                  Op.RESTORE_STOCK: PROFILE_SWITCH_REPLY_TIMEOUT}


class ExtensionError(Exception):
    pass


class BadReply(ExtensionError):
    """The reply does not answer the request that was sent."""


class Unavailable(ExtensionError):
    """INFO rules the operation out before anything is sent: a missing capability, a value
    outside the machine's limits, an incompatible profile geometry or no usable next ID."""


class Refused(ExtensionError):
    """The machine answered a request with a status other than success."""

    def __init__(self, op: Op, id: int, status: Status):
        super().__init__(f"{op.name} id {id}: {status.name}")
        self.op, self.id, self.status = op, id, status


class UncertainOutcome(ExtensionError):
    """A mutation went to the line and no valid reply came back, because the reply was
    missing or malformed, the serial port failed or the user interrupted: it may or may not
    have happened."""

    def __init__(self, op: Op, id: int, cause: BaseException):
        super().__init__(f"{op.name} id {id}: outcome unknown ({type(cause).__name__}: {cause}); "
                         "not repeated, check extension-status")
        self.op, self.id, self.cause = op, id, cause


class VerifyFailed(ExtensionError):
    """Reading back what the machine holds does not show what was committed or read."""


@dataclass(frozen=True)
class Info:
    capabilities: int
    next_id: int  # 0: the extension is faulted or out of IDs and takes no mutation
    profile_size: int
    max_chunk: int
    max_lines: int
    max_bet_per_line: int
    max_total_bet: int
    stops: int
    max_award: int

    def caps(self) -> dict[str, int]:
        """The firmware's limits as sas_profile caps."""
        return {"max_bet_per_line": self.max_bet_per_line, "max_lines": self.max_lines,
                "max_total_bet": self.max_total_bet, "max_award": self.max_award}

    def report(self) -> dict:
        supported = [c.name.lower() for c in Capability if self.capabilities & c]
        return {"capabilities": f"0x{self.capabilities:08X}", "supports": supported,
                "next_id": self.next_id, "profile_size": self.profile_size, "max_chunk": self.max_chunk,
                "max_lines": self.max_lines, "max_bet_per_line": self.max_bet_per_line,
                "max_total_bet": self.max_total_bet, "stops": self.stops, "max_award": self.max_award}


@dataclass(frozen=True)
class MachineStatus:
    last_id: int
    last_op: int
    last_status: int
    active: bool  # a profile is active; otherwise the stock game
    generation: int
    profile_crc: int
    fault: bool

    def report(self) -> dict:
        return {"last_id": self.last_id, "last_operation": _name(Op, self.last_op),
                "last_status": _name(Status, self.last_status),
                "pending": self.last_status == Status.ACCEPTED,
                "active": "profile" if self.active else "stock", "generation": self.generation,
                "profile_crc": f"{self.profile_crc:08X}", "fault": self.fault}


def encode_request(op: Op, id: int, payload: bytes = b"") -> bytes:
    """The long poll's data after its command byte."""
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"{op.name}: {len(payload)}-byte payload, more than {MAX_PAYLOAD}")
    body = MAGIC + bytes((VERSION, op)) + ID.pack(id) + payload
    return bytes((len(body),)) + body


def decode_reply(data: bytes | None, op: Op, id: int) -> tuple[Status, bytes]:
    """Status and payload of the reply to the request (op, id), or BadReply."""
    if data is None:
        raise BadReply(f"{op.name} id {id}: ACK instead of a reply")
    if (len(data) < 1 + REPLY_HEADER or data[0] != len(data) - 1
            or data[0] > REPLY_HEADER + MAX_PAYLOAD):
        raise BadReply(f"{op.name} id {id}: reply length: {data.hex(' ')}")
    body = data[1:]
    if body[:len(MAGIC)] != MAGIC or body[4] != VERSION:
        raise BadReply(f"{op.name} id {id}: not a version {VERSION} reply: {data.hex(' ')}")
    if body[5] != op or ID.unpack_from(body, 7)[0] != id:
        raise BadReply(f"{op.name} id {id}: reply to another request: {data.hex(' ')}")
    try:
        status = Status(body[6])
    except ValueError:
        raise BadReply(f"{op.name} id {id}: unknown status {body[6]}") from None
    return status, body[REPLY_HEADER:]


class Extension:
    """The extension's requests over a SAS host."""

    def __init__(self, host: sas.Host):
        self.host = host

    def info(self) -> Info:
        return Info(*INFO.unpack(self._query(Op.INFO, b"", INFO.size)))

    def status(self) -> MachineStatus:
        last_id, last_op, last_status, active, generation, crc, fault = STATUS.unpack(
            self._query(Op.STATUS, b"", STATUS.size))
        return MachineStatus(last_id, last_op, last_status, bool(active), generation, crc, bool(fault))

    def read_profile(self, size: int) -> bytes:
        """The active profile's first `size` bytes, in reads of at most MAX_PAYLOAD."""
        data = bytearray()
        while len(data) < size:
            count = min(MAX_PAYLOAD, size - len(data))
            data += self._query(Op.READ_PROFILE, READ_REQUEST.pack(len(data), count), count)
        return bytes(data)

    def mutate(self, op: Op, id: int, payload: bytes = b"") -> Status:
        """Send a mutation exactly once. Once its bytes may have reached the line, anything but
        a valid reply is UncertainOutcome, an interrupt included."""
        request = encode_request(op, id, payload)  # a request that cannot be encoded is never sent
        try:
            reply = self.host.long_poll(EXTENSION_POLL, request, retry=False,
                                        timeout=REPLY_TIMEOUTS.get(op))
            return self._check(reply, op, id, 0)[0]
        except (sas.ProtocolError, BadReply, OSError, KeyboardInterrupt) as e:
            raise UncertainOutcome(op, id, e) from e

    def _query(self, op: Op, payload: bytes, size: int) -> bytes:
        reply = self.host.long_poll(EXTENSION_POLL, encode_request(op, 0, payload))
        status, data = self._check(reply, op, 0, size)
        if status != Status.OK:
            raise Refused(op, 0, status)
        return data

    @staticmethod
    def _check(reply: bytes | None, op: Op, id: int, size: int) -> tuple[Status, bytes]:
        """The reply's status and payload, which must be `size` bytes for OK and empty otherwise."""
        status, data = decode_reply(reply, op, id)
        expected = size if status == Status.OK else 0
        if len(data) != expected:
            raise BadReply(f"{op.name} id {id}: {status.name} with {len(data)} payload bytes, "
                           f"not {expected}")
        return status, data


def info(ext: Extension) -> dict:
    return ext.info().report()


def status(ext: Extension) -> dict:
    return ext.status().report()


def download(ext: Extension) -> tuple[sp.Profile, MachineStatus]:
    """The active profile, checked against the CRC STATUS reports before and after reading
    it, or the stock profile when the stock game is active."""
    info = ext.info()
    _check_geometry(info)
    data, machine = _read_active(ext, info)
    profile = sp.stock_profile() if data is None else sp.decode(data, **info.caps())
    return profile, machine


def upload(ext: Extension, path: str) -> dict:
    """Validate the JSON profile at path within the machine's caps, stage it in chunks of at
    most INFO's max_chunk, commit it, and read the whole committed profile back."""
    info = ext.info()
    _require(info, Op.BEGIN_PROFILE)
    _check_geometry(info)
    caps = info.caps()
    data = sp.encode(sp.read_json(path, **caps), **caps)
    crc = zlib.crc32(data)
    offsets = range(0, len(data), info.max_chunk)
    ids = _ids(info, 2 + len(offsets))
    _expect(ext, Op.BEGIN_PROFILE, next(ids), BEGIN_REQUEST.pack(len(data), crc))
    for offset in offsets:
        _expect(ext, Op.PROFILE_CHUNK, next(ids),
                CHUNK_OFFSET.pack(offset) + data[offset:offset + info.max_chunk])
    commit = next(ids)
    _expect(ext, Op.COMMIT_PROFILE, commit)
    stored, machine = _read_active(ext, info)
    if (stored != data or machine.profile_crc != crc
            or (machine.last_id, machine.last_op, machine.last_status) != (commit, Op.COMMIT_PROFILE, Status.OK)):
        raise VerifyFailed(f"COMMIT_PROFILE id {commit} answered OK, but the machine does not hold "
                           f"the uploaded profile as its committed one: {machine.report()}")
    return {"id": commit, "status": Status.OK.name, "generation": machine.generation,
            "profile_crc": f"{crc:08X}"}


def restore(ext: Extension, explicit_id: int | None = None) -> dict:
    """Return to the stock game, then confirm with STATUS that it is active."""
    info = ext.info()
    _require(info, Op.RESTORE_STOCK)
    id = _id_for(ext, info, Op.RESTORE_STOCK, explicit_id)
    _expect(ext, Op.RESTORE_STOCK, id)
    machine = ext.status()
    if (machine.active or machine.fault
            or (machine.last_id, machine.last_op, machine.last_status) != (id, Op.RESTORE_STOCK, Status.OK)):
        raise VerifyFailed(f"RESTORE_STOCK id {id} answered OK, but STATUS shows {machine.report()}")
    return {"id": id, "status": Status.OK.name, "generation": machine.generation}


def act(ext: Extension, op: Op, payload: bytes, explicit_id: int | None = None) -> dict:
    """Send a game or diagnostic action. Its status is OK or ACCEPTED; ACCEPTED is not done."""
    if op not in ACTIONS:
        raise ValueError(f"{op.name} is not an action")
    info = ext.info()
    _require(info, op)
    _check_action(op, payload, info)
    id = _id_for(ext, info, op, explicit_id)
    result = ext.mutate(op, id, payload)
    if result not in (Status.OK, Status.ACCEPTED):
        raise Refused(op, id, result)
    return {"id": id, "status": result.name}


def _expect(ext: Extension, op: Op, id: int, payload: bytes = b"") -> None:
    result = ext.mutate(op, id, payload)
    if result != Status.OK:
        raise Refused(op, id, result)


def _require(info: Info, op: Op) -> None:
    if not info.capabilities & REQUIRES[op]:
        raise Unavailable(f"{op.name}: the extension does not support {REQUIRES[op].name.lower()}")
    if info.next_id == 0:
        raise Unavailable(f"{op.name}: the extension takes no mutation (faulted or out of IDs); "
                          "see extension-status")


def _check_geometry(info: Info) -> None:
    if info.profile_size != sp.PROFILE_SIZE or info.stops != sp.STOPS:
        raise Unavailable(f"the machine's {info.profile_size}-byte profiles over {info.stops} stops "
                          f"are not this host's {sp.PROFILE_SIZE} bytes over {sp.STOPS}")
    if not 1 <= info.max_chunk <= MAX_PAYLOAD - CHUNK_OFFSET.size:
        raise Unavailable(f"max_chunk {info.max_chunk} does not fit a chunk request")


def _check_action(op: Op, payload: bytes, info: Info) -> None:
    stops = payload[1:] if op == Op.BET_SPIN else b"" if op in (Op.JOG, Op.CANCEL_OUTCOME) else payload
    if any(stop >= info.stops for stop in stops):
        raise Unavailable(f"{op.name}: stops {list(stops)} are not all below {info.stops}")
    if op == Op.BET_SPIN and not 1 <= payload[0] <= info.max_bet_per_line:
        raise Unavailable(f"{op.name}: bet {payload[0]} per line is outside 1..{info.max_bet_per_line}")
    if op == Op.JOG and payload[0] >= sp.REELS:
        raise Unavailable(f"{op.name}: reel {payload[0]} is not below {sp.REELS}")


def _ids(info: Info, count: int) -> Iterator[int]:
    last = info.next_id + count - 1
    if last > ID_MAX:
        raise Unavailable(f"{count} mutations from id {info.next_id} exceed the 64-bit ID space")
    return iter(range(info.next_id, last + 1))


def _id_for(ext: Extension, info: Info, op: Op, explicit_id: int | None) -> int:
    """INFO's next ID, or an explicitly given one: either the next ID or the machine's last
    request, which it answers from its record when the request is the same."""
    if explicit_id is None or explicit_id == info.next_id:
        return info.next_id
    machine = ext.status()
    if explicit_id != machine.last_id or machine.last_op != op:
        raise Unavailable(f"{op.name}: --id {explicit_id} is neither the next ID {info.next_id} nor "
                          f"the last request (id {machine.last_id}, {_name(Op, machine.last_op)})")
    return explicit_id


def _read_active(ext: Extension, info: Info) -> tuple[bytes | None, MachineStatus]:
    """The active profile's bytes (None for stock) and the STATUS they were read under."""
    before = ext.status()
    if before.fault:
        raise VerifyFailed(f"the extension is faulted: {before.report()}")
    if not before.active:
        return None, before
    data = ext.read_profile(info.profile_size)
    after = ext.status()
    if (after.active, after.generation, after.profile_crc, after.fault) != (
            before.active, before.generation, before.profile_crc, before.fault):
        raise VerifyFailed(f"the active profile changed while it was read: {after.report()}")
    if zlib.crc32(data) != before.profile_crc:
        raise VerifyFailed(f"profile read back has CRC {zlib.crc32(data):08X}, "
                           f"STATUS reports {before.profile_crc:08X}")
    return data, before


def _name(kind: type[enum.IntEnum], value: int) -> str | int:
    try:
        return kind(value).name
    except ValueError:
        return value
