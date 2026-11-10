"""Host side of the replacement firmware's pre-boot commissioning service
(firmware/COMMISSIONING-CONTRACT.md).

Commissioning is two separate, explicit phases:

1. capture: read the reserved persistence and the commissioning record area of a machine
   with no commissioning transaction (mode UNKNOWN) exactly, and produce the original archive
   with the machine's native identities, ROM CRC-32s and provenance. The archive is saved to
   a new local path (never replacing a file), fully synced and read back. Nothing on the
   machine is written.
2. initialize: with `first_install=True` only, check the saved archive against the machine
   (layout, identities, current bytes), stage the archived capture exactly and send INIT
   once. INIT is never repeated: a missing or malformed reply is UncertainOutcome. Running
   initialize again with the same archive is the explicit way to resume or confirm.

The wire is raw 19200 8N1, stop-and-wait. Each frame is delimited by FLAG, with FLAG and
ESCAPE bytes escaped as ESCAPE, byte ^ ESCAPE_XOR; its unescaped body is the payload and
the payload's CRC-32, little-endian:

    request: MAGIC, VERSION, op, payload
    reply:   MAGIC, VERSION, op, status, payload

The serial line is a `Port`, so the same session runs against the compiled firmware's UART.
CRC-32 values detect change; they do not prove identity. The archive's SHA-256 digests guard
its own integrity.

A READ reply does not echo its offset, so a reply arriving after its request timed out could
be taken for a later chunk. Input is discarded before every request and any missing reply
ends the command, which leaves only a reply delayed past its deadline yet just after the
discard. A capture is accepted only when two full passes agree, and initialize compares the
machine's current bytes with the archive exactly before staging: a misread chunk fails one of
those checks instead of reaching INIT.
"""

from __future__ import annotations

import argparse
import enum
import fcntl
import hashlib
import json
import os
import select
import socket
import stat
import struct
import sys
import tempfile
import time
import zlib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Protocol

from sas_client import failure
from sas_extension import Status

BAUD = 19200
MAGIC = b"S6CM"
VERSION = 1
MAX_PAYLOAD = 64
REQUEST_HEADER = len(MAGIC) + 2  # magic, version, op
REPLY_HEADER = REQUEST_HEADER + 1  # and status
CRC = struct.Struct("<I")
FLAG, ESCAPE, ESCAPE_XOR = 0x7E, 0x7D, 0x20
MAX_BODY = REPLY_HEADER + MAX_PAYLOAD + CRC.size
# Bytes one exchange reads before giving up on a line that never delivers a valid frame.
MAX_REPLY_BYTES = 8 * (2 + 2 * MAX_BODY)

INFO = struct.Struct("<BIHIHHH")  # mode, then the layout in Layout's field order
READ_REQUEST = struct.Struct("<HB")  # offset, count
STAGE_OFFSET = struct.Struct("<H")
ROM_IDENTITY_SIZE = 32  # native main ROM identity at 0x720
PERSONALITY_IDENTITY_SIZE = 12  # personality identity at 0x10d1c0
IMAGE_CRCS = struct.Struct("<II")  # main 0..0x7ffff, personality 0x100000..0x10ffff
IDENTITY_SIZE = ROM_IDENTITY_SIZE + PERSONALITY_IDENTITY_SIZE + IMAGE_CRCS.size
FIRST_INSTALL = b"FIRST-INSTALL"

# How long a whole reply may take to arrive. IDENTITY hashes both whole ROMs, seconds
# at 16 MHz; INIT writes and reads back the record and the persistence. Not hardware qualified.
REPLY_TIMEOUT = 2.0
IDENTITY_TIMEOUT = 60.0
INIT_TIMEOUT = 20.0

RECORD_MAGIC = b"S6CI"
RECORD_VERSION = 1
RECORD_ORIGINAL_CRC = 8  # CRC-32 of the original persistence
RECORD_ORIGINAL = 16  # the original persistence
RECORD_READY, RECORD_DONE = 0xA55A, 0x5AA5
MARKER = struct.Struct("<H")

ARCHIVE_FORMAT = "s6000-commissioning-archive"
ARCHIVE_SCHEMA = 1
MAX_ARCHIVE_SIZE = 64 * 1024
CAPTURE_METHOD = "S6CM v1 INFO, IDENTITY, two identical READ passes, INFO; raw 19200 8N1"


class Op(enum.IntEnum):
    INFO = 0
    READ = 1
    STAGE = 2
    INIT = 3
    IDENTITY = 4


class Mode(enum.IntEnum):
    UNKNOWN = 0  # a first-install candidate, not proof of virgin storage
    READY = 1
    DONE = 2
    REFUSED = 3


@dataclass(frozen=True)
class Layout:
    persist_base: int
    persist_size: int
    record_base: int
    record_size: int
    capture_size: int
    max_chunk: int


LAYOUT = Layout(persist_base=0x430000, persist_size=0x600, record_base=0x431000, record_size=0x800,
                capture_size=0xE00, max_chunk=60)
RECORD_CRC = LAYOUT.record_size - 6  # CRC-32 of the record before it
RECORD_MARKER = LAYOUT.record_size - 2


@dataclass(frozen=True)
class Info:
    mode: Mode
    layout: Layout


@dataclass(frozen=True)
class Identity:
    main_rom: bytes
    personality: bytes
    main_crc32: int
    personality_crc32: int

    @classmethod
    def decode(cls, data: bytes) -> Identity:
        rom_end = ROM_IDENTITY_SIZE
        personality_end = rom_end + PERSONALITY_IDENTITY_SIZE
        return cls(data[:rom_end], data[rom_end:personality_end], *IMAGE_CRCS.unpack(data[personality_end:]))

    def report(self) -> dict:
        return {"main_rom_identity": self.main_rom.hex(), "personality_identity": self.personality.hex(),
                "main_crc32": f"{self.main_crc32:08x}", "personality_crc32": f"{self.personality_crc32:08x}"}


class CommissionError(Exception):
    pass


class NoReply(CommissionError):
    """Nothing valid arrived before the line went quiet."""


class BadReply(CommissionError):
    """A valid frame arrived that does not answer the request, or no valid frame within
    MAX_REPLY_BYTES."""


class Refused(CommissionError):
    def __init__(self, op: Op, status: Status):
        super().__init__(f"{op.name}: {status.name}")
        self.op, self.status = op, status


class UncertainOutcome(CommissionError):
    """INIT may have reached the machine and no valid reply came back. It is not repeated."""

    def __init__(self, op: Op, cause: BaseException):
        super().__init__(f"{op.name}: outcome unknown ({type(cause).__name__}: {cause}); not repeated. "
                         "Run initialize again with the same archive to confirm or resume")
        self.op, self.cause = op, cause


class IntentRequired(CommissionError):
    """initialize needs explicit first-install intent."""


class Mismatch(CommissionError):
    """The machine does not match what this host or the archive requires; nothing written."""

    LAYOUT, MODE, IDENTITY, CAPTURE, PERSISTENCE, RECORD = (
        "layout", "mode", "identity", "capture", "persistence", "record")

    def __init__(self, subject: str, message: str):
        super().__init__(f"{subject}: {message}")
        self.subject = subject


class VerifyFailed(CommissionError):
    """INIT answered OK but reading back does not show the completed transaction."""


class ArchiveError(CommissionError):
    """The archive is corrupt, of another schema or layout, or could not be saved intact."""


class ArchiveExists(ArchiveError):
    pass


# Framing


def encode_frame(payload: bytes) -> bytes:
    body = payload + CRC.pack(zlib.crc32(payload))
    if len(body) > MAX_BODY:
        raise ValueError(f"{len(payload)}-byte payload exceeds the frame limit")
    escaped = bytearray((FLAG,))
    for byte in body:
        if byte in (FLAG, ESCAPE):
            escaped += bytes((ESCAPE, byte ^ ESCAPE_XOR))
        else:
            escaped.append(byte)
    escaped.append(FLAG)
    return bytes(escaped)


class FrameDecoder:
    """Payloads of the valid frames in a byte stream. Each FLAG ends the frame before it and
    starts a new one; bytes before the first FLAG, empty frames, oversized frames and frames
    with a bad CRC are dropped."""

    def __init__(self, max_body: int = MAX_BODY):
        self.max_body = max_body
        self.body: bytearray | None = None  # None: dropping bytes until the next FLAG
        self.escaped = False

    def feed(self, data: bytes) -> list[bytes]:
        payloads = []
        for byte in data:
            if byte == FLAG:
                if self.body is not None and not self.escaped and len(self.body) > CRC.size:
                    payload = bytes(self.body[:-CRC.size])
                    if CRC.unpack_from(self.body, len(payload))[0] == zlib.crc32(payload):
                        payloads.append(payload)
                self.body, self.escaped = bytearray(), False
            elif self.body is None:
                continue
            elif byte == ESCAPE and not self.escaped:
                self.escaped = True
            elif len(self.body) == self.max_body:
                self.body = None
            else:
                self.body.append(byte ^ ESCAPE_XOR if self.escaped else byte)
                self.escaped = False
        return payloads


# Transport


class Port(Protocol):
    """A byte line to the commissioning service: the serial adapter, or a test's fake UART."""

    def write(self, data: bytes) -> None: ...

    def read(self, timeout: float) -> bytes:
        """Bytes received so far, waiting up to `timeout` seconds for the first; b"" if none."""
        ...

    def reset_input(self) -> None:
        """Drop received bytes nobody has read, so a stale reply cannot answer a new request."""
        ...

    def close(self) -> None: ...


class SerialPort:
    """Raw 19200 8N1 through pyserial; the SAS 9-bit wakeup line is not used here."""

    def __init__(self, device: str):
        import serial  # only talking to the machine needs pyserial

        self.port = serial.Serial(device, BAUD, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                                  stopbits=serial.STOPBITS_ONE, timeout=0, exclusive=True)

    def write(self, data: bytes) -> None:
        self.port.write(data)
        self.port.flush()

    def read(self, timeout: float) -> bytes:
        fd = self.port.fileno()
        if not select.select([fd], [], [], timeout)[0]:
            return b""
        return os.read(fd, 4096)

    def reset_input(self) -> None:
        self.port.reset_input_buffer()

    def close(self) -> None:
        self.port.close()


class Session:
    """Stop-and-wait commissioning requests over a Port. Each reply must arrive whole within
    its timeout by `clock`, and within MAX_REPLY_BYTES. The caller owns and closes the port."""

    def __init__(self, port: Port, reply_timeout: float = REPLY_TIMEOUT,
                 identity_timeout: float = IDENTITY_TIMEOUT, init_timeout: float = INIT_TIMEOUT,
                 clock: Callable[[], float] = time.monotonic):
        self.port, self.clock = port, clock
        self.reply_timeout, self.identity_timeout, self.init_timeout = (
            reply_timeout, identity_timeout, init_timeout)

    def info(self) -> Info:
        mode, *layout = INFO.unpack(self._request(Op.INFO, b"", INFO.size, self.reply_timeout))
        try:
            return Info(Mode(mode), Layout(*layout))
        except ValueError:
            raise BadReply(f"INFO: unknown mode {mode}") from None

    def read(self, offset: int, count: int) -> bytes:
        return self._request(Op.READ, READ_REQUEST.pack(offset, count), count, self.reply_timeout)

    def stage(self, offset: int, data: bytes) -> None:
        self._request(Op.STAGE, STAGE_OFFSET.pack(offset) + data, 0, self.reply_timeout)

    def identity(self) -> Identity:
        return Identity.decode(self._request(Op.IDENTITY, b"", IDENTITY_SIZE, self.identity_timeout))

    def init(self) -> None:
        """Send INIT exactly once. Once its bytes may have reached the line, anything but a
        valid reply is UncertainOutcome, an interrupt included."""
        try:
            status, _ = self._exchange(Op.INIT, FIRST_INSTALL, 0, self.init_timeout)
        except (NoReply, BadReply, OSError, KeyboardInterrupt) as e:
            raise UncertainOutcome(Op.INIT, e) from e
        if status != Status.OK:
            raise Refused(Op.INIT, status)

    def _request(self, op: Op, payload: bytes, size: int, timeout: float) -> bytes:
        status, data = self._exchange(op, payload, size, timeout)
        if status != Status.OK:
            raise Refused(op, status)
        return data

    def _exchange(self, op: Op, payload: bytes, size: int, timeout: float) -> tuple[Status, bytes]:
        """The status and payload of the reply, which must be `size` bytes for OK and empty
        otherwise."""
        if len(payload) > MAX_PAYLOAD:
            raise ValueError(f"{op.name}: {len(payload)}-byte payload, more than {MAX_PAYLOAD}")
        frame = encode_frame(MAGIC + bytes((VERSION, op)) + payload)
        self.port.reset_input()
        self.port.write(frame)
        reply = self._receive(op, timeout)
        if len(reply) < REPLY_HEADER or reply[:len(MAGIC)] != MAGIC or reply[4] != VERSION or reply[5] != op:
            raise BadReply(f"{op.name}: not a version {VERSION} reply to it: {reply.hex(' ')}")
        try:
            status = Status(reply[6])
        except ValueError:
            raise BadReply(f"{op.name}: unknown status {reply[6]}") from None
        data = reply[REPLY_HEADER:]
        if len(data) != (size if status == Status.OK else 0):
            raise BadReply(f"{op.name}: {status.name} with {len(data)} payload bytes")
        return status, data

    def _receive(self, op: Op, timeout: float) -> bytes:
        decoder = FrameDecoder()
        deadline = self.clock() + timeout
        received = 0
        while received < MAX_REPLY_BYTES:
            remaining = deadline - self.clock()
            chunk = self.port.read(remaining) if remaining > 0 else b""
            if not chunk:
                raise NoReply(f"{op.name}: no valid reply within {timeout} s")
            received += len(chunk)
            frames = decoder.feed(chunk)
            if frames:
                return frames[0]
        raise BadReply(f"{op.name}: no valid frame in {received} bytes")


# The commissioning record


def commission_record(original: bytes, marker: int) -> bytes:
    """The transaction record INIT writes for this original persistence, with `marker`."""
    record = bytearray(LAYOUT.record_size)
    record[:len(RECORD_MAGIC)] = RECORD_MAGIC
    record[len(RECORD_MAGIC)] = RECORD_VERSION
    CRC.pack_into(record, RECORD_ORIGINAL_CRC, zlib.crc32(original))
    record[RECORD_ORIGINAL:RECORD_ORIGINAL + len(original)] = original
    CRC.pack_into(record, RECORD_CRC, zlib.crc32(record[:RECORD_CRC]))
    MARKER.pack_into(record, RECORD_MARKER, marker)
    return bytes(record)


def has_record_trace(record_area: bytes) -> bool:
    """A commissioning record has been started or committed here."""
    return (record_area[:len(RECORD_MAGIC)] == RECORD_MAGIC
            or MARKER.unpack_from(record_area, RECORD_MARKER)[0] in (RECORD_READY, RECORD_DONE))


def zeroed_prefix_of(current: bytes, original: bytes) -> bool:
    """current is whole zeroed words followed by the rest of original, as INIT leaves the
    persistence when it is cut while zeroing."""
    changed = [i for i in range(len(original)) if current[i] != original[i]]
    end = (changed[-1] + 2) & ~1 if changed else 0
    return not any(current[:end]) and current[end:] == original[end:]


# The archive


@dataclass(frozen=True)
class Provenance:
    endpoint: str  # the serial device the capture was read through
    host: str  # the computer that device is attached to
    method: str
    captured_at: str  # ISO 8601, UTC


@dataclass(frozen=True)
class Archive:
    """An original: the storage of a machine in mode UNKNOWN, before any INIT."""

    capture: bytes  # persistence, then the record area, exactly as READ returned them
    identity: Identity
    provenance: Provenance

    @property
    def persistence(self) -> bytes:
        return self.capture[:LAYOUT.persist_size]

    @property
    def capture_sha256(self) -> str:
        return hashlib.sha256(self.capture).hexdigest()

    def encode(self) -> bytes:
        """The canonical archive document: one byte sequence per archive."""
        content = {
            "format": ARCHIVE_FORMAT,
            "schema": ARCHIVE_SCHEMA,
            "protocol": {"magic": MAGIC.decode(), "version": VERSION},
            "layout": asdict(LAYOUT),
            "captured_mode": Mode.UNKNOWN.name,
            "native_identity": {"main_rom": self.identity.main_rom.hex(),
                                "personality": self.identity.personality.hex()},
            "image_crc32": {"main": self.identity.main_crc32, "personality": self.identity.personality_crc32},
            "capture": {"bytes": self.capture.hex(), "sha256": self.capture_sha256},
            "provenance": asdict(self.provenance),
        }
        content["archive_sha256"] = hashlib.sha256(_canonical(content)).hexdigest()
        return _canonical(content)

    @classmethod
    def decode(cls, data: bytes) -> Archive:
        """The archive in `data`, refused unless it is exactly the canonical document of an
        archive of this schema and layout with both digests intact."""
        if len(data) > MAX_ARCHIVE_SIZE:
            raise ArchiveError(f"archive larger than {MAX_ARCHIVE_SIZE} bytes")
        try:
            archive = cls._from_document(json.loads(data.decode("utf-8")))
        except (UnicodeDecodeError, ValueError, KeyError, TypeError, RecursionError) as e:
            raise ArchiveError(f"not a schema {ARCHIVE_SCHEMA} commissioning archive: {e}") from e
        if archive.encode() != data:
            raise ArchiveError("archive digest or canonical form does not match its content")
        return archive

    @classmethod
    def _from_document(cls, document: object) -> Archive:
        top = _fields(document, ("format", "schema", "protocol", "layout", "captured_mode", "native_identity",
                                 "image_crc32", "capture", "provenance", "archive_sha256"))
        if top["format"] != ARCHIVE_FORMAT or top["schema"] != ARCHIVE_SCHEMA:
            raise ValueError("format or schema")
        if top["protocol"] != {"magic": MAGIC.decode(), "version": VERSION} or top["layout"] != asdict(LAYOUT):
            raise ValueError("protocol or layout")
        if top["captured_mode"] != Mode.UNKNOWN.name:
            raise ValueError("only a capture of a machine with no transaction is an original")
        native = _fields(top["native_identity"], ("main_rom", "personality"))
        crcs = _fields(top["image_crc32"], ("main", "personality"))
        capture = _fields(top["capture"], ("bytes", "sha256"))
        provenance = _fields(top["provenance"], tuple(Provenance.__dataclass_fields__))
        identity = Identity(_hex(native["main_rom"], ROM_IDENTITY_SIZE),
                            _hex(native["personality"], PERSONALITY_IDENTITY_SIZE),
                            _crc32(crcs["main"]), _crc32(crcs["personality"]))
        archive = cls(_hex(capture["bytes"], LAYOUT.capture_size), identity,
                      Provenance(**{k: _text(v) for k, v in provenance.items()}))
        if capture["sha256"] != archive.capture_sha256:
            raise ValueError("capture SHA-256")
        return archive


def _canonical(content: dict) -> bytes:
    return (json.dumps(content, sort_keys=True, indent=1) + "\n").encode()


def _fields(value: object, names: tuple) -> dict:
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError(f"expected exactly the fields {sorted(names)}")
    return value


def _hex(value: object, size: int) -> bytes:
    data = bytes.fromhex(_text(value))
    if len(data) != size:
        raise ValueError(f"{len(data)} bytes, not {size}")
    return data


def _crc32(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"CRC-32 {value!r}")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{value!r} is not text")
    return value


def read_archive(stream: BinaryIO) -> Archive:
    """An archive from a stream such as stdin, reading at most one byte past the limit."""
    return Archive.decode(stream.read(MAX_ARCHIVE_SIZE + 1))


def load_archive(path: Path) -> Archive:
    """The archive in the regular file at `path` (never through a symlink), once the file and
    its directory are synced to the medium and a second read gives the same bytes."""
    path = Path(path)
    data = _read_synced(path)
    _sync_directory(path.parent)
    if _read_synced(path) != data:
        raise ArchiveError(f"{path} reads back differently")
    return Archive.decode(data)


def save_archive(archive: Archive, path: Path) -> None:
    """Create `path` holding the archive, never replacing an existing file or symlink: write a
    temporary file beside it, sync it, link it into place, then load it back durably and check
    it is the same archive."""
    path = Path(path)
    data = archive.encode()
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".partial")
    try:
        with os.fdopen(descriptor, "wb") as f:
            f.write(data)
            f.flush()
            _sync(f.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise ArchiveExists(f"{path} exists; archives are never replaced") from None
    finally:
        os.unlink(temporary)
    if load_archive(path) != archive:
        raise ArchiveError(f"{path} does not read back as the archive written")


def _read_synced(path: Path) -> bytes:
    # Non-blocking so that opening a FIFO returns at once and is refused, instead of waiting
    # for a writer.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ArchiveError(f"{path} is not a regular file")
        os.set_blocking(f.fileno(), True)
        data = f.read(MAX_ARCHIVE_SIZE + 1)
        _sync(f.fileno())
    return data


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        _sync(descriptor)
    finally:
        os.close(descriptor)


def _sync(fd: int) -> None:
    """fsync, or on macOS F_FULLFSYNC: fsync there leaves the data in the drive's cache."""
    if hasattr(fcntl, "F_FULLFSYNC"):
        fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
    else:
        os.fsync(fd)


# Workflows


def read_capture(session: Session) -> bytes:
    data = bytearray()
    while len(data) < LAYOUT.capture_size:
        data += session.read(len(data), min(LAYOUT.max_chunk, LAYOUT.capture_size - len(data)))
    return bytes(data)


def describe(session: Session) -> dict:
    """INFO and IDENTITY; reads only."""
    info = session.info()
    return {"mode": info.mode.name, "layout": asdict(info.layout)} | session.identity().report()


def capture(session: Session, endpoint: str) -> Archive:
    """An archive of the machine's current storage. Only a machine with no commissioning
    transaction is captured: after an interrupted INIT, the original archive is the only one
    that can resume it."""
    info = session.info()
    _check_layout(info)
    if info.mode != Mode.UNKNOWN:
        raise Mismatch(Mismatch.MODE, f"{info.mode.name}; only a machine with no transaction is captured")
    identity = session.identity()
    data = read_capture(session)
    if read_capture(session) != data:
        raise Mismatch(Mismatch.CAPTURE, "two reads of the storage differ")
    if session.info() != info:
        raise Mismatch(Mismatch.MODE, "INFO changed during the capture")
    if has_record_trace(data[LAYOUT.persist_size:]):
        raise Mismatch(Mismatch.RECORD, "a commissioning record was started; resume with its original archive")
    # datetime.UTC needs Python 3.11; the stdlib suite runs on the system python3, 3.9 on macOS.
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: UP017
    return Archive(data, identity, Provenance(endpoint, socket.gethostname(), CAPTURE_METHOD, when))


def initialize(session: Session, archive: Archive, first_install: bool) -> dict:
    """Commission the machine from its saved original archive, or confirm or resume a
    transaction started from it. INIT is sent at most once. A record area that differs from
    the archive's while the persistence is unchanged may be an interrupted record creation:
    the firmware checks it is a word prefix of the archive's record."""
    if not first_install:
        raise IntentRequired("initialize writes the persistence; it needs explicit first-install intent")
    info = session.info()
    _check_layout(info)
    if info.mode == Mode.REFUSED:
        raise Mismatch(Mismatch.MODE, "the machine refuses commissioning")
    identity = session.identity()
    if identity != archive.identity:
        raise Mismatch(Mismatch.IDENTITY, "the machine's ROM identities or CRC-32s differ from the archive's")
    current = read_capture(session)
    record_area = current[LAYOUT.persist_size:]
    report = {"capture_sha256": archive.capture_sha256}
    if info.mode == Mode.DONE:
        if record_area != commission_record(archive.persistence, RECORD_DONE):
            raise Mismatch(Mismatch.RECORD, "the completed transaction is not this archive's")
        return report | {"mode": Mode.DONE.name, "init_sent": False}
    if info.mode == Mode.READY:
        if record_area != commission_record(archive.persistence, RECORD_READY):
            raise Mismatch(Mismatch.RECORD, "the transaction in progress is not this archive's")
        if not zeroed_prefix_of(current[:LAYOUT.persist_size], archive.persistence):
            raise Mismatch(Mismatch.PERSISTENCE, "not the archive's original partly zeroed")
    elif current[:LAYOUT.persist_size] != archive.persistence:
        raise Mismatch(Mismatch.PERSISTENCE, "changed since the archive was captured")
    for offset in range(0, LAYOUT.capture_size, LAYOUT.max_chunk):
        session.stage(offset, archive.capture[offset:offset + LAYOUT.max_chunk])
    session.init()
    after = session.info()
    stored = read_capture(session)
    if (after.mode != Mode.DONE or any(stored[:LAYOUT.persist_size])
            or stored[LAYOUT.persist_size:] != commission_record(archive.persistence, RECORD_DONE)):
        raise VerifyFailed(f"INIT answered OK, but the machine reports {after.mode.name} and does not hold "
                           "zeroed persistence with this archive's completed record")
    return report | {"mode": Mode.DONE.name, "init_sent": True}


def _check_layout(info: Info) -> None:
    if info.layout != LAYOUT:
        raise Mismatch(Mismatch.LAYOUT, f"the machine's {info.layout} is not this host's {LAYOUT}")


# Command line. tools/sas runs the serial commands on the Linux host and the archive commands
# on the Mac, joined by pipes, so INIT can start only after the archive is durable on the Mac:
#
#   capture:     ssh: commission-capture | local: archive-save FILE
#   initialize:  local: archive-verify FILE | ssh: commission-initialize --first-install

COMMANDS = {  # name: (values, what it does)
    "commission-info": ("", "print INFO and IDENTITY as JSON; reads only"),
    "commission-capture": ("", "print the canonical original archive on stdout, and nothing else; reads only"),
    "commission-initialize": ("", ("with --first-install, read the original archive from stdin, check it "
                                   "completely, then commission from it, sending INIT at most once")),
    "archive-save": ("FILE", "save the archive on stdin to the new file FILE, synced and read back; offline"),
    "archive-verify": ("FILE", "sync and read back FILE, check it and print it on stdout; offline"),
}
SERIAL = ("commission-info", "commission-capture", "commission-initialize")
USAGE = "\n".join(f"  {name} {values}".rstrip().ljust(32) + about for name, (values, about) in COMMANDS.items())


def main(argv: list[str] | None = None, open_port: Callable[[str], Port] = SerialPort,
         stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> int:
    """Options go before or after the command. Exit status 0 on success, 2 for a usage error
    (nothing opened or sent). A failure exits 1 with one JSON line on stderr naming its
    error_type (UncertainOutcome: INIT may have acted) and, for a Mismatch, its subject."""
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    parser = argparse.ArgumentParser(prog="sas_commission.py", description=__doc__, epilog="commands:\n" + USAGE,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="serial commands, required: the 8N1 serial device")
    parser.add_argument("--first-install", action="store_true",
                        help="commission-initialize: explicit intent to replace the archived persistence")
    parser.add_argument("command", choices=COMMANDS, metavar="command")
    parser.add_argument("values", nargs="*")
    args = parser.parse_intermixed_args(argv)
    usage, _ = COMMANDS[args.command]
    if len(args.values) != len(usage.split()):
        parser.error(f"{args.command} takes {usage or 'no values'}")
    if args.port is not None and args.command not in SERIAL:
        parser.error(f"--port: {args.command} does not use the serial port")
    if args.port is None and args.command in SERIAL:
        parser.error(f"--port: {args.command} needs the serial device")
    if args.first_install != (args.command == "commission-initialize"):
        parser.error("commission-initialize needs --first-install; no other command takes it")
    try:
        output = _run(args.command, args.values, args.port, open_port, stdin)
    except (CommissionError, OSError, KeyboardInterrupt) as e:
        report = {"command": args.command} | failure(e)
        if isinstance(e, Mismatch):
            report["subject"] = e.subject
        print(json.dumps(report), file=sys.stderr, flush=True)
        return 1
    stdout.write(output)
    stdout.flush()
    return 0


def _run(command: str, values: list[str], device: str | None, open_port: Callable[[str], Port],
         stdin: BinaryIO) -> bytes:
    """What the command prints on stdout."""
    if command == "archive-save":
        archive = read_archive(stdin)
        save_archive(archive, Path(values[0]))
        return _report({"file": values[0], "capture_sha256": archive.capture_sha256})
    if command == "archive-verify":
        return load_archive(Path(values[0])).encode()
    archive = read_archive(stdin) if command == "commission-initialize" else None  # before the port opens
    port = open_port(device)
    try:
        session = Session(port)
        if archive is not None:
            return _report(initialize(session, archive, first_install=True))
        if command == "commission-info":
            return _report(describe(session))
        return capture(session, device).encode()
    finally:
        port.close()


def _report(report: dict) -> bytes:
    return json.dumps(report).encode() + b"\n"


if __name__ == "__main__":
    sys.exit(main())
