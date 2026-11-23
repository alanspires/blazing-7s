"""Machine operations shared by the web control page's worker, MoneyTest, WriteTest and sas.py.

Every state-changing operation here is sent once (`retry=False`) and calls the injected
`record(intent)` with its identity and exact request bytes immediately before the bytes
leave; when `record` raises, nothing is sent. Operations over a `Host` also take
`pace()`, called between their host calls and never inside one, so a caller can give other
polls their turn without splitting a retransmission sequence (the CLI passes a wait of
POLL_INTERVAL). Operations over an `Extension` take `record` only: the Extension was built
with the pace. `observe`, where given, receives every poll result as `sas_client.poll`
returns it, for the machine tests' logs.

A missing, unparsable or uncorrelated reply after a send raises `Uncertain` carrying the
recorded intent, and so does a line failure or interrupt after it, kept as the Uncertain's
`__cause__` (an OSError there means the link broke): whether the machine acted is unknown,
and the caller reconciles by reading (`reconcile_transfer`, `reconcile_extension`, the 6E
probe), never by resending (.rules/sas-host-machine-tests.md,
.rules/firmware-remote-control.md).

Money follows SAS 6.02 §8 (Tables 8.1, 8.2b, 8.3d and 8.3e) and the live findings in
notes/sas-j10.md, "Money test": in-house transfers work at registration status 80, and this
machine cashes out only the whole balance.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

import sas_commands as sc
import sas_extension as se
import sas_profile as sp
import sas_protocol as sas
from sas_client import poll
from sas_tables import DENOMINATIONS

Record = Callable[[dict], None]
Pace = Callable[[], None]
Observe = Callable[[dict], None]

# Table C-7 meters the money operations compare, by the names results use.
MONEY_METERS = {"credits": 0x0C, "in_cents": 0xA0, "in_count": 0xA1, "out_cents": 0xB8,
                "out_count": 0xB9}
IN_HOUSE = ("in_cents", "in_count", "out_cents", "out_count")
GAME_METERS = ("coin_in", "coin_out", "drop", "jackpot", "games_played", "games_won",
               "slot_door_opened", "power_reset")  # 1C, Table 7.2c
IN_HOUSE_TO_MACHINE, IN_HOUSE_TO_HOST = 0x00, 0x80  # Table 8.3d
FULL_TRANSFER, PEEK, INTERROGATE = 0x00, 0xFE, 0xFF  # 72 transfer codes
TRANSFER_OK, TRANSFER_PENDING = 0x00, 0x40  # Table 8.3e
REJECTED = range(0x80, 0xC2)  # Table 8.3e: 80-9F, C0 and C1 refuse the transfer
RECEIPT_PENDING = 0x20
NOT_LOCKED = 0xFF  # 74 lock status
AFT_IN_HOUSE_ENABLED = 0x90  # Table 8.2b AFT status, bits 7 and 4
TO_MACHINE, TO_HOST = 0x01, 0x02  # Table 8.2b available transfers, bits 0 and 1
TRANSACTION_ID_LENGTH = 20  # SAS transaction IDs allow at most 20 ASCII bytes
REGISTRATION_KEY_BYTES = 20
COMPLETION_POLLS = 40  # bounded, paced status reads; never repeat an initiating command
HANDLER_POLLS = 8  # reads, 2 s at POLL_INTERVAL, for a handler's effect to show
BOUNDARY_READS = 3  # 1A reads, each with the host's own repeats, for one validated reply

# 6E action 2 (authenticate) answers C5 while play is enabled, before it looks at the
# component; with play disabled it looks the component up, and index FF does not exist
# (FUN_0002f28e takes 1-3), so the answer is 80 and nothing starts (FUN_0002f598).
CANNOT_AUTHENTICATE_NOW, NO_SUCH_COMPONENT = 0xC5, 0x80
PROBE_COMPONENT = "0xFF"

CLOCK_FORMAT = "%m%d%Y%H%M%S"
CLOCK_TOLERANCE = dt.timedelta(seconds=3)  # 7E has whole seconds; a read follows the set

# The `op` of each intent the operations here record
OP_DEPOSIT, OP_CASH_OUT, OP_SET_PLAY = "deposit", "cash_out", "set_play"
OP_SET_SOUND, OP_SET_BILLS, OP_SET_CLOCK = "set_sound", "set_bills", "set_clock"

# Reconciled results
HAPPENED, NOTHING_CHANGED, PENDING, INCONSISTENT = "happened", "nothing_changed", "pending", "inconsistent"
COMMITTED_VERIFIED, ABORTED, STAGING_ABANDONED = "committed_verified", "aborted", "staging_abandoned"
RESTORED, INTERRUPTED, FAULT = "restored", "interrupted", "fault"  # RESTORE_STOCK only
# ACK-only settings
ACKED, NACKED, UNKNOWN = "acked", "nacked", "unknown"
# TransferOutcome.status
TRANSFER_MOVED, TRANSFER_REJECTED = "moved", "rejected"
# An upload step record's `result`, as the caller journals it for reconcile_extension
STEP_ACKED, STEP_REFUSED, STEP_LOST = "acked", "refused", "lost"


class MachineError(Exception):
    pass


class ReadFailed(MachineError):
    """A read did not return the data it asks for. `result` is the poll result, if any."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = result


class NotAdmitted(MachineError):
    """The machine's fresh state rules the operation out; nothing was sent."""

    def __init__(self, check: Check):
        super().__init__(f"{check.label}: {check.detail}" if check.detail != "" else check.label)
        self.check = check


class Uncertain(MachineError):
    """A state-changing request went to the line and no reply settled what it did."""

    def __init__(self, intent: dict, reason: str):
        super().__init__(f"{intent.get('op')}: outcome unknown ({reason}); not repeated, reconcile first")
        self.intent = intent
        self.reason = reason


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: object = ""


@dataclass(frozen=True)
class AftState:
    """What admits a transfer, read fresh: the replies of 1F, 74, 73, 72 FE, 1A and 6F."""
    cents_per_credit: int  # 0 when the denomination is not a whole number of cents
    lock: dict
    registration: dict
    latest: dict
    credits: int
    meters: dict

    @property
    def asset(self) -> int:
        return self.lock["asset"]

    @property
    def key(self) -> str:
        return self.registration.get("registration_key", "").replace(" ", "")


@dataclass(frozen=True)
class TransferOutcome:
    status: str  # TRANSFER_MOVED or TRANSFER_REJECTED
    code: int  # the machine's Table 8.3e status, reported as is
    transaction_id: str
    cents: int
    deltas: dict  # money meter name -> change across the transfer


@dataclass(frozen=True)
class Reconciled:
    result: str  # HAPPENED, NOTHING_CHANGED, PENDING, INCONSISTENT; uploads: COMMITTED_VERIFIED, ...;
    # RESTORE_STOCK: RESTORED, INTERRUPTED, FAULT instead of HAPPENED
    evidence: dict


@dataclass(frozen=True)
class PlayResult:
    confirmed: bool  # the 6E probe shows the target
    acked: bool
    play_enabled: bool  # as the last probe read it


@dataclass(frozen=True)
class ClockResult:
    confirmed: bool
    read: dt.datetime  # zone-less, as SAS carries it


@dataclass(frozen=True)
class MachineReading:
    cents_per_credit: int
    credits: int
    meters: dict  # 1C, by GAME_METERS name
    money_meters: dict  # by MONEY_METERS name
    aft: dict  # 74 interrogation
    registration: dict  # 73 read
    latest_transfer: dict  # 72 FE peek
    play_enabled: bool
    machine_time: dt.datetime
    extension_info: se.Info
    extension_status: se.MachineStatus
    reel_stops: tuple


# Reads


def read(host: sas.Host, name: str, *values: object, retry: bool = True, observe: Observe | None = None) -> dict:
    """The decoded reply of one read, or ReadFailed (no reply, NACK, bare ACK, undecodable)."""
    result = _send(host, sc.BY_NAME[name], list(values), retry=retry, observe=observe)
    if "error" in result:
        raise ReadFailed(f"{name}: {result['error']}", result)
    if not isinstance(result["reply"], dict):
        raise ReadFailed(f"{name}: expected data, not a bare ACK", result)
    return result["reply"]


def cents_per_credit(host: sas.Host, *, observe: Observe | None = None) -> int:
    """Whole cents per credit from 1F's denomination, or 0 when it is not whole cents."""
    dollars = DENOMINATIONS.get(read(host, "info", observe=observe).get("denomination"))
    cents = Decimal(str(dollars)) * 100 if dollars is not None else Decimal(0)
    return int(cents) if cents == cents.to_integral_value() else 0


def current_credits(host: sas.Host, *, observe: Observe | None = None) -> int:
    return _int_field(read(host, "credits", observe=observe), "value", "credits")


def game_meters(host: sas.Host, *, observe: Observe | None = None) -> dict:
    reply = read(host, "meters", observe=observe)
    return {name: _int_field(reply, name, "meters") for name in GAME_METERS}


def money_meters(host: sas.Host, *, observe: Observe | None = None) -> dict:
    """Credits and the in-house meters by name, every one present."""
    reply = read(host, "extended-meters", 0, *(f"0x{code:02X}" for code in MONEY_METERS.values()),
                 observe=observe)
    by_code = {item.get("meter"): item.get("value") for item in reply.get("items", [])}
    if any(not isinstance(by_code.get(code), int) for code in MONEY_METERS.values()):
        raise ReadFailed(f"extended-meters: not every money meter answered: {reply}")
    return {name: by_code[code] for name, code in MONEY_METERS.items()}


def aft_lock(host: sas.Host, *, observe: Observe | None = None) -> dict:
    reply = read(host, "aft-lock", 0xFF, 0, 0, observe=observe)
    _require_fields(reply, ("asset", "lock_status", "available_transfers", "aft_status", "cashable_cents",
                            "restricted_cents", "nonrestricted_cents", "transfer_limit_cents"), "aft-lock")
    return reply


def registration(host: sas.Host, *, observe: Observe | None = None) -> dict:
    return read(host, "aft-register", 0xFF, observe=observe)


def latest_transfer(host: sas.Host, *, observe: Observe | None = None) -> dict:
    """72 FE: the latest transfer, observed without completing its exchange."""
    reply = read(host, "aft-transfer", PEEK, 0, observe=observe)
    _require_fields(reply, ("status",), "aft-transfer")
    return reply


def play_disabled(host: sas.Host, *, observe: Observe | None = None) -> bool:
    """The play-disabled bit (bit 7 of 0x412D38), read with the 6E probe."""
    status = read(host, "authentication", 2, 0, PROBE_COMPONENT, observe=observe).get("status")
    if status not in (CANNOT_AUTHENTICATE_NOW, NO_SUCH_COMPONENT):
        raise ReadFailed(f"6E probe answered {status}")
    return status == NO_SUCH_COMPONENT


def machine_time(host: sas.Host, *, observe: Observe | None = None) -> dt.datetime:
    reply = read(host, "date-time", observe=observe)
    try:
        stamp = reply["date"] + reply["time"]
        return dt.datetime.strptime(stamp, CLOCK_FORMAT)  # noqa: DTZ007 -- SAS time is zone-less
    except (KeyError, TypeError, ValueError) as e:
        raise ReadFailed(f"date-time: not a date and time: {reply}") from e


def reel_stops(host: sas.Host, *, observe: Observe | None = None) -> tuple:
    """8F: the zero-based stop of each reel."""
    raw = bytes.fromhex(str(read(host, "reel-stops", observe=observe).get("stops", "")))
    if len(raw) < sp.REELS or any(stop >= sp.STOPS for stop in raw[:sp.REELS]):
        raise ReadFailed(f"reel-stops: {raw.hex(' ')}")
    return tuple(raw[:sp.REELS])


def read_aft_state(host: sas.Host, *, pace: Pace, observe: Observe | None = None) -> AftState:
    readers = (cents_per_credit, aft_lock, registration, latest_transfer, current_credits, money_meters)
    values = []
    for index, reader in enumerate(readers):
        if index:
            pace()
        values.append(reader(host, observe=observe))
    return AftState(*values)


def read_state(host: sas.Host, ext: se.Extension, *, pace: Pace, observe: Observe | None = None) -> MachineReading:
    """Everything the page shows, read once: a full refresh."""
    values = []
    for reader in (cents_per_credit, current_credits, game_meters, money_meters, aft_lock, registration,
                   latest_transfer):
        if values:
            pace()
        values.append(reader(host, observe=observe))
    pace()
    values.append(not play_disabled(host, observe=observe))
    pace()
    values.append(machine_time(host, observe=observe))
    values += [ext.info(), ext.status()]
    pace()
    values.append(reel_stops(host, observe=observe))
    return MachineReading(*values)


# Money


def transfer_checks(state: AftState) -> list:
    """The checks both directions need (decision-3 r8, decision-7 r4). Registration need not
    be enrolled: the cabinet runs at status 80 and transfers there with the key it reports."""
    lock, reg, latest = state.lock, state.registration, state.latest
    cpc = state.cents_per_credit
    key = state.key
    registration_valid = (all(name in reg for name in ("status", "asset", "registration_key", "pos_id"))
                          and "extra" not in reg and _is_hex(key, REGISTRATION_KEY_BYTES))
    return [
        Check("whole-cent denomination", cpc >= 1, cpc),
        Check("registration query answered in full", registration_valid, reg),
        Check("consistent asset number", lock["asset"] != 0 and reg.get("asset") == lock["asset"],
              {"aft-lock": lock["asset"], "aft-register": reg.get("asset")}),
        Check("machine unlocked", lock["lock_status"] == NOT_LOCKED, lock["lock_status"]),
        Check("in-house AFT enabled", _bits(lock["aft_status"]) & AFT_IN_HOUSE_ENABLED == AFT_IN_HOUSE_ENABLED,
              lock["aft_status"]),
        Check("cashable credits only", lock["restricted_cents"] == lock["nonrestricted_cents"] == 0,
              {"restricted": lock["restricted_cents"], "nonrestricted": lock["nonrestricted_cents"]}),
        Check("no existing transfer or receipt pending",
              latest["status"] != TRANSFER_PENDING and latest.get("receipt_status") != RECEIPT_PENDING, latest),
        Check("credit meters agree",
              state.credits == state.meters["credits"] and state.credits * cpc == lock["cashable_cents"],
              {"credits": state.credits, "meter": state.meters["credits"], "cashable_cents": lock["cashable_cents"]}),
    ]


def deposit(host: sas.Host, credits: int, *, record: Record, pace: Pace,
            observe: Observe | None = None) -> TransferOutcome:
    """Add `credits` by in-house AFT: refused before sending unless the machine takes it now
    and the amount is within its reported transfer limit, converted exactly."""
    if credits < 1:
        raise ValueError(f"deposit of {credits} credits")
    state = read_aft_state(host, pace=pace, observe=observe)
    cents = credits * state.cents_per_credit
    _admit(transfer_checks(state) + [
        Check("transfers to the machine available", bool(_bits(state.lock["available_transfers"]) & TO_MACHINE),
              state.lock["available_transfers"]),
        Check("amount within the transfer limit", cents <= state.lock["transfer_limit_cents"],
              {"cents": cents, "limit": state.lock["transfer_limit_cents"]}),
    ])
    return _transfer(host, state, IN_HOUSE_TO_MACHINE, cents, record=record, pace=pace, observe=observe)


def cash_out(host: sas.Host, *, record: Record, pace: Pace, observe: Observe | None = None) -> TransferOutcome:
    """Move the whole cashable balance to the host. No cap is applied: the 74 transfer limit is
    the incoming one (Table 8.2b), and a refusal such as 84 is reported, never followed by a
    partial attempt (decision-7 r6)."""
    state = read_aft_state(host, pace=pace, observe=observe)
    _admit(transfer_checks(state) + [
        Check("transfers to the host available", bool(_bits(state.lock["available_transfers"]) & TO_HOST),
              state.lock["available_transfers"]),
        Check("credits to cash out", state.lock["cashable_cents"] > 0, state.lock["cashable_cents"]),
    ])
    return _transfer(host, state, IN_HOUSE_TO_HOST, state.lock["cashable_cents"], record=record, pace=pace,
                     observe=observe)


def expected_deltas(kind: int, cents: int) -> dict:
    """The in-house meter changes one completed transfer of this kind makes."""
    moved = dict.fromkeys(IN_HOUSE, 0)
    prefix = "in" if kind == IN_HOUSE_TO_MACHINE else "out"
    moved.update({f"{prefix}_cents": cents, f"{prefix}_count": 1})
    return moved


def reconcile_transfer(host: sas.Host, intent: dict, *, pace: Pace, observe: Observe | None = None) -> Reconciled:
    """What the recorded transfer did, from the 72 FE peek and the money meters against the
    intent's baseline. A peek and meters that disagree are INCONSISTENT (decision-3 r4)."""
    baseline, transaction_id = intent["baseline"], intent["transaction_id"]
    moved = expected_deltas(intent["type"], intent["cents"])
    unmoved = dict.fromkeys(IN_HOUSE, 0)
    for attempt in range(COMPLETION_POLLS + 1):
        if attempt:
            pace()
        latest = latest_transfer(host, observe=observe)
        pace()
        meters = money_meters(host, observe=observe)
        deltas = {name: meters[name] - baseline[name] for name in MONEY_METERS}
        in_house = {name: deltas[name] for name in IN_HOUSE}
        evidence = {"latest": latest, "deltas": deltas}
        if latest.get("transaction_id") != transaction_id:
            return Reconciled(NOTHING_CHANGED if in_house == unmoved else INCONSISTENT, evidence)
        status = latest["status"]
        if status == TRANSFER_PENDING:
            continue
        if status == TRANSFER_OK:
            same = (latest.get("type"), latest.get("cashable_cents")) == (intent["type"], intent["cents"])
            return Reconciled(HAPPENED if same and in_house == moved else INCONSISTENT, evidence)
        if status in REJECTED:
            return Reconciled(NOTHING_CHANGED if in_house == unmoved else INCONSISTENT, evidence)
        return Reconciled(INCONSISTENT, evidence)
    return Reconciled(PENDING, evidence)


def _transfer(host: sas.Host, state: AftState, kind: int, cents: int, *, record: Record, pace: Pace,
              observe: Observe | None) -> TransferOutcome:
    """Send once, interrogate pending status with paced FF reads, and acknowledge completion
    with a different read (the meters). Exception 69 is optional if status is retrieved first
    (SAS §8.3)."""
    command = sc.BY_NAME["aft-transfer"]
    transaction_id = uuid4().hex[:TRANSACTION_ID_LENGTH]
    values = [FULL_TRANSFER, 0, kind, cents, 0, 0, 0, state.asset, state.key, transaction_id, "00000000", 0,
              "", 0]
    intent = {"op": OP_DEPOSIT if kind == IN_HOUSE_TO_MACHINE else OP_CASH_OUT, "transaction_id": transaction_id,
              "type": kind, "cents": cents, "asset": state.asset,
              "request_hex": _message(host, command, values).hex(), "baseline": dict(state.meters)}
    pace()
    record(intent)
    with _uncertain_after_send(intent):
        return _complete(host, intent, command, values, pace, observe)


def _complete(host: sas.Host, intent: dict, command: sc.Command, values: list, pace: Pace,
              observe: Observe | None) -> TransferOutcome:
    kind, cents = intent["type"], intent["cents"]
    identity = (kind, intent["asset"], intent["transaction_id"])
    result = _send(host, command, values, retry=False, observe=observe)
    for attempt in range(COMPLETION_POLLS + 1):
        reply = result.get("reply")
        if "error" in result or not isinstance(reply, dict):
            raise Uncertain(intent, result.get("error", "no data reply"))
        status = reply.get("status")
        if (reply.get("type"), reply.get("asset"), reply.get("transaction_id")) != identity:
            raise Uncertain(intent, f"reply to another transfer: {reply}")
        amounts = (reply.get("cashable_cents"), reply.get("restricted_cents"), reply.get("nonrestricted_cents"))
        if status in REJECTED:
            if amounts != (0, 0, 0):
                raise Uncertain(intent, f"refusal {status:02X} reports an amount: {reply}")
            return _settle(host, intent, TRANSFER_REJECTED, status, dict.fromkeys(IN_HOUSE, 0), pace, observe)
        if status not in (TRANSFER_PENDING, TRANSFER_OK) or amounts != (cents, 0, 0):
            raise Uncertain(intent, f"unexpected transfer reply: {reply}")
        if status == TRANSFER_OK:
            if reply.get("receipt_status") == RECEIPT_PENDING:
                raise Uncertain(intent, "completed with a receipt pending")
            return _settle(host, intent, TRANSFER_MOVED, status, expected_deltas(kind, cents), pace, observe)
        if attempt < COMPLETION_POLLS:
            pace()
            result = _send(host, command, [INTERROGATE, 0], retry=True, observe=observe)
    raise Uncertain(intent, f"still pending after {COMPLETION_POLLS} interrogations")


def _settle(host: sas.Host, intent: dict, status: str, code: int, expected: dict, pace: Pace,
            observe: Observe | None) -> TransferOutcome:
    pace()
    meters = money_meters(host, observe=observe)
    deltas = {name: meters[name] - intent["baseline"][name] for name in MONEY_METERS}
    if {name: deltas[name] for name in IN_HOUSE} != expected:
        raise Uncertain(intent, f"{status} with code {code:02X}, but the in-house meters moved by {deltas}")
    return TransferOutcome(status, code, intent["transaction_id"], intent["cents"], deltas)


# Settings


def set_play(host: sas.Host, enabled: bool, *, record: Record, pace: Pace,
             observe: Observe | None = None) -> PlayResult:
    """09 for game 1 once, then paced 6E probes until play shows the target. The probe alone
    settles a lost reply; a probe that never answers leaves the outcome Uncertain."""
    command = sc.BY_NAME["enable-game"]
    values = [1, int(enabled)]
    intent = {"op": OP_SET_PLAY, "enabled": enabled, "request_hex": _message(host, command, values).hex()}
    pace()
    record(intent)
    with _uncertain_after_send(intent):
        acked = _send(host, command, values, retry=False, observe=observe).get("reply") == "ACK"
        shown = None
        for _ in range(HANDLER_POLLS):
            pace()
            try:
                shown = not play_disabled(host, observe=observe)
            except ReadFailed:
                continue
            if shown == enabled:
                break
    if shown is None:
        raise Uncertain(intent, "the 6E probe never answered")
    return PlayResult(shown == enabled, acked, shown)


def set_sound(host: sas.Host, on: bool, *, record: Record, observe: Observe | None = None) -> str:
    """04 or 03 once: ACKED, NACKED or UNKNOWN. No poll reads the sound flags back."""
    return _ack_only(host, "sound-on" if on else "sound-off", {"op": OP_SET_SOUND, "on": on}, record, observe)


def set_bills(host: sas.Host, on: bool, *, record: Record, observe: Observe | None = None) -> str:
    """06 or 07 once: ACKED, NACKED or UNKNOWN. No poll reads the bill mask back."""
    return _ack_only(host, "bills-on" if on else "bills-off", {"op": OP_SET_BILLS, "on": on}, record, observe)


def ack_boundary(host: sas.Host, *, pace: Pace, observe: Observe | None = None) -> int:
    """A harmless 1A read whose reply must be a CRC-valid credits reply from this address,
    never a bare ACK or any one-byte reply. The machine answers in order, so once it arrives
    any late ACK of an earlier command came before it. A reply failing validation (a stale
    byte in front of it included) is discarded and the read repeated, within a bound; an
    OSError from the line propagates. Returns the credits."""
    for attempt in range(BOUNDARY_READS):
        if attempt:
            pace()
        result = _send(host, sc.BY_NAME["credits"], [], retry=True, observe=observe)
        reply = result.get("reply")
        if isinstance(reply, dict) and "extra" not in reply and isinstance(reply.get("value"), int):
            return reply["value"]
    raise ReadFailed(f"no validated 1A reply in {BOUNDARY_READS} reads")


def set_clock(host: sas.Host, when: dt.datetime, *, record: Record, pace: Pace,
              observe: Observe | None = None) -> ClockResult:
    """7F once (this firmware never answers it, not even with an ACK), then a 7E read:
    confirmed when it is within CLOCK_TOLERANCE of `when`. SAS time carries no zone."""
    if when.tzinfo is not None:
        raise ValueError("SAS time is zone-less; give a naive datetime")
    command = sc.BY_NAME["set-date-time"]
    stamp = when.strftime(CLOCK_FORMAT)
    values = [stamp[:8], stamp[8:]]
    # target_time, not time: a journal assigns its own record time (web_journal.Journal.append).
    intent = {"op": OP_SET_CLOCK, "target_time": when.isoformat(),
              "request_hex": _message(host, command, values).hex()}
    pace()
    record(intent)
    with _uncertain_after_send(intent):
        _send(host, command, values, retry=False, observe=observe)
        pace()
        got = machine_time(host, observe=observe)
    return ClockResult(abs(got - when) <= CLOCK_TOLERANCE, got)


# Extension


def spin(ext: se.Extension, bet: int, stops: Sequence[int] | None = None, *, record: Record) -> dict:
    """BET_SPIN at `bet` per line; with stops it lands there, without them it binds to an
    armed outcome or plays a random game."""
    payload = bytes((bet,)) + (b"" if stops is None else _stops(stops))
    return _act(ext, se.Op.BET_SPIN, payload, record)


def arm(ext: se.Extension, stops: Sequence[int], *, record: Record) -> dict:
    return _act(ext, se.Op.ARM_OUTCOME, _stops(stops), record)


def cancel(ext: se.Extension, *, record: Record) -> dict:
    return _act(ext, se.Op.CANCEL_OUTCOME, b"", record)


def reconcile_extension(ext: se.Extension, intent: dict, *, steps: Sequence[dict] = ()) -> Reconciled:
    """What a recorded mutation did, from STATUS and, for an upload, the CRC-bracketed
    read-back; nothing is ever resent. `steps` are an upload's step records with their journaled
    `result` (STEP_ACKED, STEP_REFUSED or STEP_LOST) where one is known.

    A RESTORE_STOCK the machine took is RESTORED only on the proof an acknowledged restore
    needs (`sas_extension.stock_restored`); a matching INTERRUPTED or FAULT is that terminal
    result, a matching ACCEPTED is PENDING whatever the fault flag says, and an OK that STATUS
    contradicts (a profile active or a fault) is INCONSISTENT."""
    if intent.get("op") == se.UPLOAD_OP:
        return _reconcile_upload(ext, intent, steps)
    op = se.Op[intent["op"]]
    try:
        status = ext.status()
    except (sas.ProtocolError, se.ExtensionError) as e:
        return Reconciled(PENDING, {"error": f"{type(e).__name__}: {e}"})
    evidence = {"status": status.report()}
    if status.last_id < intent["id"]:
        return Reconciled(NOTHING_CHANGED, evidence)  # IDs only grow: the machine never took this one
    if (status.last_id, status.last_op) != (intent["id"], op):
        return Reconciled(INCONSISTENT, evidence)
    if op == se.Op.RESTORE_STOCK:
        return Reconciled(_restore_result(status, intent["id"]), evidence)
    taken = status.last_status in (se.Status.OK, se.Status.ACCEPTED, se.Status.INTERRUPTED, se.Status.FAULT)
    return Reconciled(HAPPENED if taken else NOTHING_CHANGED, evidence)


def _restore_result(status: se.MachineStatus, id: int) -> str:
    """What STATUS, naming RESTORE_STOCK `id` as its last request, says that restore did."""
    if se.stock_restored(status, id):
        return RESTORED
    return {se.Status.OK: INCONSISTENT, se.Status.ACCEPTED: PENDING, se.Status.INTERRUPTED: INTERRUPTED,
            se.Status.FAULT: FAULT}.get(status.last_status, NOTHING_CHANGED)


def _reconcile_upload(ext: se.Extension, intent: dict, steps: Sequence[dict]) -> Reconciled:
    commit = next((step for step in steps if step["op"] == se.Op.COMMIT_PROFILE.name), None)
    if commit is None:
        # Only COMMIT activates, and staging does not survive a boot: nothing to read or resume.
        return Reconciled(STAGING_ABANDONED, {"upload_id": intent["upload_id"]})
    previous = intent["previous"]
    try:
        status = ext.status()
    except (sas.ProtocolError, se.ExtensionError) as e:
        return Reconciled(PENDING, {"error": f"{type(e).__name__}: {e}"})
    evidence = {"status": status.report()}
    last = (status.last_id, status.last_op)
    seen_ok = last == (commit["id"], se.Op.COMMIT_PROFILE) and status.last_status == se.Status.OK
    if commit.get("result") == STEP_ACKED or seen_ok:
        if status.fault or not status.active or status.profile_crc != intent["profile_crc"]:
            return Reconciled(INCONSISTENT, evidence)
        try:
            stored, bracket = se.read_active(ext, ext.info())
        except (sas.ProtocolError, se.ExtensionError) as e:
            return Reconciled(PENDING, evidence | {"error": f"{type(e).__name__}: {e}"})
        verified = stored == bytes.fromhex(intent["profile_hex"]) and bracket.profile_crc == intent["profile_crc"]
        return Reconciled(COMMITTED_VERIFIED if verified else INCONSISTENT, evidence)
    refused = commit.get("result") == STEP_REFUSED or (
        last == (commit["id"], se.Op.COMMIT_PROFILE) and status.last_status not in (se.Status.OK, se.Status.ACCEPTED))
    if refused or status.last_id < commit["id"]:
        as_before = (status.active, status.profile_crc, status.generation) == (
            previous["active"], previous["profile_crc"], previous["generation"])
        return Reconciled(ABORTED if as_before and not status.fault else INCONSISTENT, evidence)
    return Reconciled(INCONSISTENT, evidence)


def _act(ext: se.Extension, op: se.Op, payload: bytes, record: Record) -> dict:
    recorded: list = []

    def keep(intent: dict) -> None:
        record(intent)
        recorded.append(intent)

    try:
        return se.act(ext, op, payload, record=keep)
    except se.UncertainOutcome as e:
        raise Uncertain(recorded[-1], str(e)) from e


def _stops(stops: Sequence[int]) -> bytes:
    if len(stops) != sp.REELS:
        raise ValueError(f"{len(stops)} stops for {sp.REELS} reels")
    return bytes(stops)


# Helpers


def _ack_only(host: sas.Host, name: str, intent: dict, record: Record, observe: Observe | None) -> str:
    command = sc.BY_NAME[name]
    intent["request_hex"] = _message(host, command, []).hex()
    record(intent)
    with _uncertain_after_send(intent):
        result = _send(host, command, [], retry=False, observe=observe)
    if result.get("reply") == "ACK":
        return ACKED
    return NACKED if result.get("error_type") == sas.Nacked.__name__ else UNKNOWN


@contextmanager
def _uncertain_after_send(intent: dict) -> Iterator[None]:
    """After a send, a failed read, a line failure or an interrupt leaves the outcome unknown.
    The error stays the Uncertain's __cause__, so a caller can tell a broken link (OSError)."""
    try:
        yield
    except (ReadFailed, OSError, KeyboardInterrupt) as e:
        raise Uncertain(intent, f"{type(e).__name__}: {e}") from e


def _send(host: sas.Host, command: sc.Command, values: list, *, retry: bool, observe: Observe | None) -> dict:
    result = poll(host, command, [str(v) for v in values], retry=retry)
    if observe is not None:
        observe(result)
    return result


def _message(host: sas.Host, command: sc.Command, values: list) -> bytes:
    """The exact long poll as it goes on the wire."""
    message = bytes((host.address, command.code)) + sc.encode_request(command, [str(v) for v in values])
    return sas.with_crc(message) if command.crc else message


def _admit(checks: list) -> None:
    for check in checks:
        if not check.ok:
            raise NotAdmitted(check)


def _require_fields(reply: dict, names: tuple, command: str) -> None:
    missing = [name for name in names if name not in reply]
    if missing:
        raise ReadFailed(f"{command}: reply without {', '.join(missing)}: {reply}")


def _int_field(reply: dict, name: str, command: str) -> int:
    value = reply.get(name)
    if not isinstance(value, int):
        raise ReadFailed(f"{command}: {name} is {value!r}, not a number")
    return value


def _bits(value: str) -> int:
    return int(value, 16)


def _is_hex(text: str, size: int) -> bool:
    try:
        return len(bytes.fromhex(text)) == size
    except ValueError:
        return False
