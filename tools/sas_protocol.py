"""SAS host side for the S6000's J10 SAS port, independent of the serial hardware.

Everything here follows the IGT SAS 6.02 spec in vendor-docs/sas/ (section numbers cited inline).
The serial line is injected (see `Line`), so the protocol logic runs without a machine.
What each long poll carries is in `sas_commands`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

T = TypeVar("T")

GENERAL_POLL_FLAG = 0x80  # §2.2.1: general poll = address | 0x80, wakeup bit set
# §3.3: a poll to address 0 resynchronizes the machine's poll-state counter; §3.1: it is also
# an implied ACK of the machine's last reply.
SYNC_POLL = 0 | GENERAL_POLL_FLAG
POLL_TRIES = 3  # §3.2: the third consecutive identical poll is the final NACK
# §2.3.1: the machine answers within 20 ms. A global poll gets no answer, but the S6000 missed
# polls sent right after one; waiting out the window first made every poll answer.
SYNC_SETTLE = 0.02

LONG_POLL_ROM_SIGNATURE = 0x21
NACK_FLAG = 0x80  # Table 7.4b: a type S poll is ACKed with the address, NACKed with address | 0x80
BUSY = 0x00  # §4.1: "address, 00" means busy; the host polls again later
EVENT = 0xFF  # §12.5: with real-time event reporting on, "address, FF, exception, data, CRC"

def crc16(data: bytes, crc: int = 0) -> int:
    """§5.1 routine (CRC-16/KERMIT); seed 0 for messages (§5.2)."""
    for c in data:
        q = (crc ^ c) & 0o17
        crc = (crc >> 4) ^ (q * 0o10201)
        q = (crc ^ (c >> 4)) & 0o17
        crc = (crc >> 4) ^ (q * 0o10201)
    return crc


def with_crc(message: bytes) -> bytes:
    """Append the CRC low byte first, the order this machine uses (its 1F reply)."""
    crc = crc16(message)
    return message + bytes((crc & 0xFF, crc >> 8))


class ProtocolError(Exception):
    pass


class ReplyError(ProtocolError):
    """The reply was missing or garbled; the host repeats the poll (§3.2)."""


class NoResponse(ReplyError):
    pass


class CrcError(ReplyError):
    pass


class Busy(ReplyError):
    pass


class EventReply(ReplyError):
    """§12.3: a real-time event came back instead of the long poll's reply; Host.events has it."""


class Nacked(ProtocolError):
    """The machine refused a type S or M poll (Table 7.4b)."""


def check_crc(message: bytes) -> bytes:
    """Return the message without its CRC, or raise CrcError."""
    body = message[:-2]
    if len(message) < 4 or with_crc(body) != message:
        raise CrcError(f"CRC mismatch: {message.hex(' ')}")
    return body


@dataclass(frozen=True)
class RomSignature:
    """§6: the reply to the first general poll after a ROM signature calculation finishes."""
    signature: int


@dataclass(frozen=True)
class RealTimeEvent:
    """§12.5: a general poll reply while real-time event reporting is on."""
    exception: int
    data: bytes


class Line(Protocol):
    def send(self, first: int, rest: bytes) -> None:
        """Send `first` with the wakeup bit set and `rest` with it clear (§1.2.1)."""

    def read_message(self, timeout: float) -> bytes:
        """Return one reply: wait up to `timeout` for its first byte, then read until the line
        goes quiet. Empty if nothing arrived."""

    def wait(self, seconds: float) -> None:
        """Let `seconds` pass without sending."""


class Host:
    # §2.3.1 gives the machine 20 ms to start answering; the rest covers USB-serial latency.
    RESPONSE_TIMEOUT = 0.25

    def __init__(self, line: Line, address: int, timeout: float = RESPONSE_TIMEOUT):
        if not 1 <= address <= 0x7F:
            raise ValueError("SAS addresses are 1-127 (§2.1)")
        self.line = line
        self.address = address
        self.timeout = timeout
        self._last_poll: tuple[int, bytes] | None = None
        self._sends = 0  # consecutive sends of _last_poll
        self._answered = False
        # §12.3: events the machine sent instead of a long poll's reply; general polls do not
        # return them again.
        self.events: list[RealTimeEvent] = []

    def send(self, first: int, rest: bytes = b"") -> None:
        """Send a poll, keeping the machine's poll-state counter in step (§3.1-3.3).

        Repeating a poll asks the machine to resend its reply (implied NACK), so a repeat of
        an answered poll is preceded by a sync poll as its ACK. The machine ignores polls
        for itself until it sees a sync after startup or a loop break, and gives up after
        the final NACK, so the first poll and a poll tried POLL_TRIES times also get one."""
        poll = (first, rest)
        repeat = poll == self._last_poll
        if self._last_poll is None or (repeat and (self._answered or self._sends >= POLL_TRIES)):
            self.line.send(SYNC_POLL, b"")
            self.line.wait(SYNC_SETTLE)
            repeat = False
        self.line.send(first, rest)
        self._sends = self._sends + 1 if repeat else 1
        self._last_poll, self._answered = poll, False

    def general_poll(self) -> int | RomSignature | RealTimeEvent:
        """Return the next exception code (§2.2.1), a finished ROM signature (§6.1), or a
        real-time event (§12.5)."""
        return self._poll(self.address | GENERAL_POLL_FLAG, b"", self._read_general_reply)

    def long_poll(self, command: int, data: bytes = b"", crc: bool = True, *,
                  retry: bool = True, timeout: float | None = None) -> bytes | None:
        """Send a long poll (§2.2.2) and return the reply's data, between the command byte
        and the CRC, or None for an ACK. Type R polls carry no CRC; the others do.
        With retry=False, send once: callers must reconcile an uncertain write by reading.
        `timeout` replaces self.timeout as the wait for each reply to this call only."""
        message = bytes((self.address, command)) + data
        if crc:
            message = with_crc(message)
        wait = self.timeout if timeout is None else timeout
        return self._poll(message[0], message[1:], lambda: self._read_long_reply(command, wait),
                          retry=retry)

    def request_rom_signature(self, seed: int) -> None:
        """§6.2: start a signature calculation; general_poll returns the result when done."""
        if self.long_poll(LONG_POLL_ROM_SIGNATURE, seed.to_bytes(2, "little")) is not None:
            raise ProtocolError("ROM signature request answered with data instead of an ACK")

    def _poll(self, first: int, rest: bytes, read_reply: Callable[[], T], *, retry: bool = True) -> T:
        """Send and read, optionally repeating missing or garbled replies per §3.2."""
        attempts = POLL_TRIES if retry else 1
        for attempt in range(1, attempts + 1):
            self.send(first, rest)
            try:
                reply = read_reply()
            except EventReply:
                # §12.4: the poll counts as NACKed and is sent again; the sync before the
                # repeat acknowledges the event.
                self._answered = True
                if attempt == attempts:
                    raise
                continue
            except ReplyError:
                if attempt == attempts:
                    raise
                continue
            except Nacked:
                self._answered = True  # a NACK is an answer: repeating the poll needs a sync first
                raise
            self._answered = True
            return reply

    def _read(self, timeout: float) -> bytes:
        message = self.line.read_message(timeout)
        if not message:
            raise NoResponse(f"no reply from address {self.address}")
        if message == bytes((self.address, BUSY)):
            raise Busy(f"address {self.address} busy")
        return message

    def _read_general_reply(self) -> int | RomSignature | RealTimeEvent:
        message = self._read(self.timeout)
        # Addresses 0x11-0x7F are also exception codes; only the bytes that follow tell a
        # longer message from an exception.
        if len(message) == 1:
            return message[0]
        body = check_crc(message)
        if body[0] != self.address:
            raise ReplyError(f"reply from another address: {message.hex(' ')}")
        if body[1] == EVENT and len(body) >= 3:
            return RealTimeEvent(body[2], body[3:])
        if body[1] == LONG_POLL_ROM_SIGNATURE and len(body) == 4:
            return RomSignature(int.from_bytes(body[2:4], "little"))
        raise ReplyError(f"unexpected reply to general poll: {message.hex(' ')}")

    def _read_long_reply(self, command: int, timeout: float) -> bytes | None:
        """Data of the reply, or None for an ACK."""
        message = self._read(timeout)
        if len(message) == 1:
            if message[0] == self.address:
                return None
            if message[0] == self.address | NACK_FLAG:
                raise Nacked(f"NACK to long poll {command:02X}")
            raise ReplyError(f"unexpected one-byte reply to {command:02X}: {message.hex()}")
        body = check_crc(message)
        if body[0] == self.address and body[1] == EVENT and len(body) >= 3:
            self.events.append(RealTimeEvent(body[2], body[3:]))
            raise EventReply(f"event {body[2]:02X} instead of the reply to {command:02X}")
        if body[0] != self.address or body[1] != command:
            raise ReplyError(f"unexpected reply to {command:02X}: {message.hex(' ')}")
        return body[2:]
