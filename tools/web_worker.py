"""The web control page's machine worker: the one thread that talks to the machine.

Only this module touches the serial `Line`, the SAS `Host` and the extension `Extension`
(decision-1 r2). The HTTP threads use three calls: `snapshot()` for the immutable
`web_state.MachineState` it publishes, `subscribe()` for a waiter that wakes on each new
snapshot, and `submit(action)`, which admits an action at once against the snapshot and
either queues it (bounded) or refuses it. Publishing swaps the snapshot under a lock and
notifies waiters; it never waits on a subscriber (decision-6 r5).

Wire pacing and schedule (decision-1 r4, decision-10 r2). `PacedLine` spaces every actual
send POLL_INTERVAL after the previous one, whoever makes it: general polls, long polls and
the host's own syncs, repeats and busy retries. A general poll is due GENERAL_POLL_GAP after
the previous one; the slots between belong to the running operation's long polls. Due polls
run in `pace()`, which operations call between their host calls and never inside one (the
`Extension` is built with it, so every extension query and mutation yields there), so a
retransmission sequence is never split and the gap between general polls stays within
MAX_GENERAL_POLL_GAP.

Commands. The worker re-reads what admission needs, asks `web_state.admit` again against
that fresh state and the values the page rendered, and only then runs the operation. The
operation's `record(intent)` callback is the last step before every send: it re-admits the
action after whatever the yields in `pace()` reported, appends the intent to the journal
(fsynced, with the submission ID) and raises when either fails, so nothing is sent. For an
upload's steps it checks instead that this upload may continue. A lost reply holds the
action's family as checking until a reconciler has read what happened; nothing is resent
(.rules/sas-host-machine-tests.md, .rules/firmware-remote-control.md). Only a failed intent
append proves nothing was sent; any later failed append keeps the intent open and its
family held until the record is durable (decision-6 r4).

Link (decision-10 r1, r3). A run of unanswered general polls lasting OFFLINE_AFTER seconds,
with no other validated reply in it, marks the
machine offline while the line stays open; an OSError from the line itself (SerialClosed
included), which `PacedLine` raises as `LineLost`, closes the handle and reopens it through
`line_factory` with backoff. Any other OSError is not a lost line and ends the worker
(fail-fast; web_control exits non-zero). Whenever replies
return, at start-up, after a reopen and after exception 17, a full refresh and the
reconciliation of every open intent run before the snapshot is fresh and actions are
admitted again.

The journal records the worker writes are the contract the views read (web_views): outcome
{action, result, transfer, deltas, outcome_name, settings, play_enabled, clock_offset,
generation, evidence, code}, where `result` is the value of the `web_state.OutcomeWord` the
action's result carries, spin {bet_per_line,
total_bet, stops, paid, credits, games, name}, event {code, event, door, name, arm},
profile {intent, change, generation, returns} and page {event, cause}. A verified apply or
restore journals its profile record, naming its intent, before that intent's outcome.
"""

from __future__ import annotations

import collections
import datetime as dt
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from types import MappingProxyType

import sas_extension as se
import sas_machine as sm
import sas_profile as sp
import sas_protocol as sas
import web_state as ws
from sas_tables import EXCEPTIONS, IDLE_EXCEPTIONS
from web_journal import NO_ARM, JournalError
from web_state import (
    Act,
    Action,
    ActionResult,
    Arm,
    ArmState,
    Code,
    Family,
    Hold,
    Link,
    OutcomeWord,
    Refusal,
    ResultState,
    Setting,
)

POLL_INTERVAL = sas.POLL_INTERVAL
GENERAL_POLL_GAP = 4 * POLL_INTERVAL  # one general poll, then three slots for long polls
# One host call at its longest: every try with a sync before it, or one profile switch.
LONGEST_HOST_CALL = max(
    sas.POLL_TRIES * (2 * POLL_INTERVAL + sas.SYNC_SETTLE + sas.Host.RESPONSE_TIMEOUT),
    2 * POLL_INTERVAL + sas.SYNC_SETTLE + se.PROFILE_SWITCH_REPLY_TIMEOUT)
MAX_GENERAL_POLL_GAP = GENERAL_POLL_GAP + POLL_INTERVAL + LONGEST_HOST_CALL
OFFLINE_AFTER = 2.0  # seconds of unanswered general polls
REFRESH_INTERVAL = 5.0  # periodic credits, meters, aft-lock and STATUS read
REOPEN_BACKOFF = (0.5, 1.0, 2.0, 4.0, 8.0)  # seconds before each reopen of a failed line
QUEUE_LIMIT = 4
LOG_LIMIT = 20

POWER_APPLIED, POWER_LOST = 0x17, 0x18
GAME_STARTED, GAME_ENDED = 0x7E, 0x7F
DISTURBING = range(0x11, 0x1F)  # Table A-1 doors, power and cashbox: an upload stops
DOOR_OPENED = (0x11, 0x13, 0x15, 0x19, 0x1D)  # each door's close code is one more
DOORS = MappingProxyType({**{code: (EXCEPTIONS[code].split(" was ")[0], True) for code in DOOR_OPENED},
                          **{code + 1: (EXCEPTIONS[code].split(" was ")[0], False) for code in DOOR_OPENED}})
EVENT_NAMES = MappingProxyType({POWER_APPLIED: "power_up", POWER_LOST: "power_down", 0x1B: "cashbox_removed",
                                0x1C: "cashbox_installed", 0x51: "handpay", 0x52: "handpay_reset",
                                0x66: "cash_out_button", 0x69: "aft_complete", GAME_STARTED: "game_start"})
MONEY_EVENTS = frozenset((0x51, 0x52, 0x66, 0x69))  # credits or meters may have moved

MONEY_OPS = frozenset((sm.OP_DEPOSIT, sm.OP_CASH_OUT))
EXTENSION_OPS = frozenset(op.name for op in (se.Op.BET_SPIN, se.Op.ARM_OUTCOME, se.Op.CANCEL_OUTCOME,
                                             se.Op.RESTORE_STOCK))
OP_ACTS = MappingProxyType({
    sm.OP_DEPOSIT: Act.ADD_CREDITS, sm.OP_CASH_OUT: Act.CASH_OUT, sm.OP_SET_PLAY: Act.PLAY,
    sm.OP_SET_SOUND: Act.SOUND, sm.OP_SET_BILLS: Act.BILLS, sm.OP_SET_CLOCK: Act.CLOCK_SYNC,
    se.UPLOAD_OP: Act.APPLY_PROFILE,
    se.Op.RESTORE_STOCK.name: Act.RESTORE_STOCK, se.Op.BET_SPIN.name: Act.SPIN,
    se.Op.ARM_OUTCOME.name: Act.ARM, se.Op.CANCEL_OUTCOME.name: Act.CANCEL_ARM,
})
BET_ACTS = frozenset((Act.BET_ONE, Act.MAX_BET))

CHECKING = "Checking what happened"
INCONSISTENT = "The machine's records disagree: this stays blocked"

# What a failed read raises; nothing of the request it guards was sent.
_READ_ERRORS = (sm.ReadFailed, sas.ProtocolError, se.ExtensionError)
_JOURNAL_ERRORS = (OSError, JournalError)

Clock = Callable[[], float]
LineFactory = Callable[[], sas.Line]


@dataclass(frozen=True)
class Submitted:
    submission_id: str


class _WatchedHost(sas.Host):
    """A Host that reports each long poll the machine answered validly (CRC, address and
    command checked, or the machine's ACK): proof the machine answers, like a general poll's."""

    def __init__(self, line: sas.Line, address: int, answered: Callable[[], None]):
        super().__init__(line, address)
        self._on_answer = answered

    def long_poll(self, command: int, data: bytes = b"", crc: bool = True, *,
                  retry: bool = True, timeout: float | None = None) -> bytes | None:
        reply = super().long_poll(command, data, crc, retry=retry, timeout=timeout)
        self._on_answer()
        return reply


class LineLost(OSError):
    """The serial line itself failed (SerialClosed, an unplugged adapter); the OSError the
    line raised is the cause. An OSError subclass, so sas_machine and sas_extension keep it
    as an uncertain outcome's cause like any line failure."""


@contextmanager
def _line_errors() -> Iterator[None]:
    try:
        yield
    except LineLost:
        raise
    except OSError as e:
        raise LineLost(f"{type(e).__name__}: {e}") from e


class PacedLine:
    """Waits, on the injected clock and the line's own wait, until POLL_INTERVAL has passed
    since the previous actual send before each send (SAS §2.3.3). Every OSError the line
    raises comes out as LineLost, so only the line's own failures read as a lost line."""

    def __init__(self, line: sas.Line, clock: Clock):
        self.line = line
        self._clock = clock
        self._last: float | None = None

    def send(self, first: int, rest: bytes) -> None:
        with _line_errors():
            if self._last is not None:
                wait = self._last + POLL_INTERVAL - self._clock()
                if wait > 0:
                    self.line.wait(wait)
            self._last = self._clock()
            self.line.send(first, rest)

    def read_message(self, timeout: float) -> bytes:
        with _line_errors():
            return self.line.read_message(timeout)

    def wait(self, seconds: float) -> None:
        with _line_errors():
            self.line.wait(seconds)

    def close(self) -> None:
        with _line_errors():
            self.line.close()


class Subscription:
    """Wakes on each new snapshot; a slow reader gets the newest one, never a backlog."""

    def __init__(self, worker: Worker, version: int):
        self._worker = worker
        self._seen = version

    def wait(self, timeout: float) -> ws.MachineState | None:
        """The newest snapshot once it is newer than the last one returned, None on timeout."""
        newer = self._worker._newer(self._seen, timeout)
        if newer is None:
            return None
        self._seen, state = newer
        return state


class _Refused(Exception):
    """record: the action is no longer admissible; nothing was sent."""

    def __init__(self, refusal: Refusal):
        super().__init__(refusal.reason)
        self.refusal = refusal


class _NotRecorded(Exception):
    """record: the journal append failed; the request it guarded was not sent."""


class _Invalidated(Exception):
    """record: the open upload may not continue; this step was neither recorded nor sent."""


class _Open:
    """An intent with no durable outcome yet: the journal record and an upload's steps."""

    def __init__(self, record: dict, steps: list[dict], *, profile_told: bool = False):
        self.record = record
        self.steps = steps
        self.profile_told = profile_told  # its verified profile change's record is journaled or queued
        self.act = OP_ACTS[record["op"]]
        self.family = ws.FAMILIES.get(self.act)
        self.inconsistent = False  # re-checked only at start-up and reconnect
        self.closing = False  # its outcome is waiting to be saved

    @property
    def seq(self) -> int:
        return self.record["seq"]

    @property
    def submission_id(self) -> str | None:
        return self.record.get("submission_id")


@dataclass
class _Later:
    """A record to append, in order, once the journal takes it."""
    record: dict
    entry: _Open | None = None  # an outcome that closes this intent
    result: ActionResult | None = None  # shown once durable; CHECKING until then


@dataclass
class _Upload:
    seq: int
    previous: dict
    disturbances: int


class Worker:
    """See the module notes. `clock` returns monotonic seconds; `line_factory` opens the
    serial line (a `sas.Line` with close()); `journal` is an open web_journal.Journal."""

    def __init__(self, line_factory: LineFactory, journal, *, clock: Clock, address: int):
        sp.installed_game()  # the pinned personality's game, read once: missing, nothing starts
        self._line_factory = line_factory
        self._journal = journal
        self._clock = clock
        self._address = address
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._queued = threading.Condition(self._lock)
        self._version = 0
        self._queue: collections.deque[Action] = collections.deque()
        self._stopping = False

        self._line: PacedLine | None = None
        self._host: sas.Host | None = None
        self._ext: se.Extension | None = None
        self._started = False
        self._reopen_at = clock()
        self._backoff = 0
        self._replying = False
        self._recovering = True
        self._recovery = 0  # bumped whenever a full refresh is needed again
        self._recover_at = clock()
        self._silent_since: float | None = None  # when the current run of unanswered general polls began
        self._last_general_poll: float | None = None
        self._next_refresh = clock()
        self._retry_at = clock()
        self._needs: set[str] = set()
        self._disturbances = 0

        self._open: dict[int, _Open] = {}
        self._holds: dict[Family, Hold] = {}
        self._later: list[_Later] = []
        self._current: int | None = None  # seq of the running action's own intent
        self._upload: _Upload | None = None
        self._page_spin: _Open | None = None  # a page spin whose game has not ended
        self._spin_action: Action | None = None  # its action, in progress until the game ends
        self._spin_baseline: dict | None = None
        self._games_played: int | None = None
        self._game_meters: dict | None = None
        self._profile_status: se.MachineStatus | None = None  # the STATUS the profile was read under
        self._arm_record: dict = journal.arm()
        settings = journal.settings()
        self._boundary_needed = Setting.UNKNOWN.value in settings.values()
        self._state = self._initial_state(settings)
        for intent in journal.open_intents():
            entry = _Open(intent.record, list(intent.steps), profile_told=intent.profile is not None)
            self._open[entry.seq] = entry
            self._hold(entry, CHECKING)
            if entry.submission_id:
                self._set_result(ActionResult(entry.submission_id, entry.act, ResultState.CHECKING,
                                              reason=CHECKING))

    # The HTTP threads' surface

    def snapshot(self) -> ws.MachineState:
        with self._lock:
            return self._state

    def subscribe(self) -> Subscription:
        with self._lock:
            return Subscription(self, self._version)

    def submit(self, action: Action) -> Submitted | Refusal:
        """Admit at once: refused, or queued (Bet one and Max bet are done at once; they send
        nothing). Either way the action's submission ID enters the results ring."""
        refusal = None if action.name in BET_ACTS else ws.admit(self.snapshot(), action)
        with self._lock:
            state = self._state
            if refusal is None and action.name in BET_ACTS:
                refusal = ws.admit(state, action)
                if refusal is None:
                    state = replace(state, bet_per_line=ws.next_bet(state, action.name))
                    self._publish(ws.with_result(state, ActionResult(
                        action.submission_id, action.name, ResultState.DONE, outcome=OutcomeWord.BET)))
                    return Submitted(action.submission_id)
            if refusal is None and len(self._queue) >= QUEUE_LIMIT:
                refusal = Refusal(Code.BUSY, "Busy: try again in a moment", action.submission_id)
            if refusal is not None:
                self._publish(ws.with_result(state, _refused(action, refusal)))
                return refusal
            self._queue.append(action)
            self._publish(ws.with_result(state, ActionResult(action.submission_id, action.name,
                                                             ResultState.QUEUED)))
            self._queued.notify_all()
            return Submitted(action.submission_id)

    # The machine thread

    def run(self) -> None:
        """Work until stop(); the thread's target."""
        try:
            while True:
                with self._lock:
                    if self._stopping:
                        return
                delay = self.step()
                if delay > 0:
                    with self._lock:
                        if not self._queue and not self._stopping:
                            self._queued.wait(delay)
        finally:
            if self._line is not None:
                try:
                    self._line.close()
                except LineLost:
                    pass  # stopping anyway; a failing close must not hide why the loop ended

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._queued.notify_all()

    def step(self) -> float:
        """Do the next unit of work; 0, or the seconds until something is due."""
        if self._host is None:
            if self._queue:
                self._run(self._take())
                return 0.0
            wait = self._reopen_at - self._clock()
            if wait > 0:
                return wait
            self._open_line()
            return 0.0
        try:
            return self._step()
        except LineLost as e:
            self._lose_line(e)
            return 0.0

    def _step(self) -> float:
        if self._general_poll_due():
            self._general_poll()
            return 0.0
        if self._queue:
            self._run(self._take())
            return 0.0
        until_poll = self._last_general_poll + GENERAL_POLL_GAP - self._clock()
        if not self._replying:
            return until_poll
        now = self._clock()
        if self._recovering:
            if now >= self._recover_at:
                self._recover()
                return 0.0
            return min(until_poll, self._recover_at - now)
        if self._needs and now >= self._retry_at:
            self._targeted_refresh()
            return 0.0
        if now >= self._next_refresh:
            self._refresh()
            return 0.0
        due = [until_poll, self._next_refresh - now]
        if self._needs:
            due.append(self._retry_at - now)
        return max(min(due), 0.0)

    def _take(self) -> Action:
        with self._lock:
            return self._queue.popleft()

    # Publication

    def _publish(self, state: ws.MachineState) -> None:
        """Swap the snapshot and wake subscribers; the caller holds the lock."""
        self._state = state
        self._version += 1
        self._changed.notify_all()

    def _update(self, **changes: object) -> None:
        with self._lock:
            self._publish(replace(self._state, **changes))

    def _set_result(self, result: ActionResult) -> None:
        with self._lock:
            self._publish(ws.with_result(self._state, result))

    def _newer(self, seen: int, timeout: float) -> tuple[int, ws.MachineState] | None:
        with self._changed:
            if not self._changed.wait_for(lambda: self._version > seen, timeout):
                return None
            return self._version, self._state

    def _initial_state(self, settings: dict) -> ws.MachineState:
        journal = self._journal
        spins = journal.entries(limit=1, kinds=("spin",))
        return ws.MachineState(
            arm=_arm(self._arm_record, unconfirmed=True),
            settings=ws.Settings(sound=Setting(settings.get("sound", Setting.UNKNOWN.value)),
                                 bills=Setting(settings.get("bills", Setting.UNKNOWN.value))),
            counters=journal.counters(),
            log=tuple(MappingProxyType(r) for r in journal.entries(limit=LOG_LIMIT, kinds=ws.ACTIVITY_KINDS)),
            last_spin=_last_spin(spins[0]) if spins else None)

    # The line

    def _open_line(self) -> None:
        if not self._started:
            self._started = True
            self._save({"kind": "page", "source": "page", "event": "started"})
        try:
            line = self._line_factory()
        except OSError:  # the device cannot be opened (unplugged, busy): the line, retried
            self._reopen_at = self._clock() + self._next_backoff()
            return
        self._line = PacedLine(line, self._clock)
        # A new Host sends a sync before its first poll: the resync after every (re)open.
        self._host = _WatchedHost(self._line, self._address, self._machine_answered)
        self._ext = se.Extension(self._host, pace=self._pace)
        self._replying = False
        self._need_recovery()
        self._silent_since = None
        self._last_general_poll = None

    def _lose_line(self, error: LineLost) -> None:
        line = self._line
        self._line = self._host = self._ext = None
        if line is not None:
            try:
                line.close()
            except LineLost:
                pass  # the device may already be gone; the handle is discarded either way
        self._reopen_at = self._clock() + self._next_backoff()
        self._went_offline("serial")

    def _next_backoff(self) -> float:
        delay = REOPEN_BACKOFF[min(self._backoff, len(REOPEN_BACKOFF) - 1)]
        self._backoff += 1
        return delay

    def _went_offline(self, cause: str) -> None:
        was = self._state.link
        self._replying = False
        self._need_recovery()
        self._update(link=Link.OFFLINE, fresh=False, doors=None, game_running=False)
        if was is not Link.OFFLINE:
            self._save({"kind": "page", "source": "page", "event": "machine_lost", "cause": cause})

    def _need_recovery(self) -> None:
        """A full refresh and reconciliation must run before the snapshot is fresh again; one
        already running when this happens does not count."""
        self._recovering = True
        self._recovery += 1

    def _machine_answered(self) -> None:
        self._silent_since = None

    def _general_poll_due(self) -> bool:
        return self._last_general_poll is None or self._clock() - self._last_general_poll >= GENERAL_POLL_GAP

    def _pace(self) -> None:
        """The yield between host calls: a due general poll runs here."""
        if self._host is not None and self._general_poll_due():
            self._general_poll()

    def _general_poll(self) -> None:
        self._last_general_poll = self._clock()
        try:
            reply = self._host.general_poll()
        except sas.ProtocolError:
            # One unanswered poll is noise; only a run of them with no reply at all is silence.
            if self._silent_since is None:
                self._silent_since = self._last_general_poll
            if self._clock() - self._silent_since >= OFFLINE_AFTER and self._state.link is not Link.OFFLINE:
                self._went_offline("silent")
            return
        self._machine_answered()
        if not self._replying:
            self._replying = True
            self._update(link=Link.ONLINE, last_reply_time=_utc_now())
        if isinstance(reply, sas.RealTimeEvent):
            reply = reply.exception
        if isinstance(reply, int):
            self._on_exception(reply)

    def _on_exception(self, code: int) -> None:
        if code in IDLE_EXCEPTIONS:
            return
        if code in DISTURBING:
            self._disturbances += 1
        if code == GAME_ENDED:
            self._needs.add("game")  # journaled as the game's spin record
            self._update(game_running=False)
            return
        if code == GAME_STARTED:
            self._update(game_running=True)  # a game at the machine shows from its start (R2)
        event = {"kind": "event", "source": "machine", "code": code}
        if code in DOORS:
            door, is_open = DOORS[code]
            doors = dict(self._state.doors or {})
            doors[door] = is_open
            self._update(doors=MappingProxyType(doors))
            event.update(event="door_open" if is_open else "door_closed", door=door)
            if self._state.arm.state is ArmState.ARMED:
                self._needs.add("status")  # a door can clear an arm: STATUS says before it is shown
        elif code in EVENT_NAMES:
            event["event"] = EVENT_NAMES[code]
        else:
            event.update(event="exception", name=EXCEPTIONS.get(code, "Unknown exception"))
        if code == POWER_APPLIED:
            self._need_recovery()
            self._update(fresh=False, doors=None, game_running=False)
        if code in MONEY_EVENTS:
            self._needs.add("money")
        self._save(event)

    # Reads

    def _read(self, reader: Callable[..., object]) -> object:
        self._pace()
        return reader(self._host)

    def _read_meters(self) -> tuple[int, dict, dict]:
        return self._read(sm.current_credits), self._read(sm.game_meters), self._read(sm.money_meters)

    def _recover(self) -> None:
        """Full refresh, then reconcile every open intent, then fresh."""
        recovery = self._recovery
        try:
            reading = sm.read_state(self._host, self._ext, pace=self._pace)
            self._apply_status(reading.extension_status, reading.extension_info)
        except _READ_ERRORS:
            self._recover_at = self._clock() + GENERAL_POLL_GAP
            return
        cpc = reading.cents_per_credit
        self._update(cents_per_credit=cpc, play_enabled=reading.play_enabled,
                     aft=_aft(reading.aft, cpc), last_reply_time=_utc_now(),
                     settings=replace(self._state.settings, clock_offset=_clock_offset(reading.machine_time)))
        self._sync_meters(reading.credits, reading.meters, reading.money_meters)
        for entry in list(self._open.values()):
            if entry is not self._page_spin and not entry.closing:
                self._reconcile(entry)
        self._flush()
        if recovery != self._recovery:
            return  # offline or powered up while it read: what it read may be outdated
        self._recovering = False
        self._backoff = 0
        self._next_refresh = self._clock() + REFRESH_INTERVAL
        self._update(link=Link.ONLINE, fresh=True)
        self._save({"kind": "page", "source": "page", "event": "machine_found"})

    def _refresh(self) -> None:
        """The periodic read, then a retry of what is still unsettled."""
        try:
            credits, game, money = self._read_meters()
            lock = self._read(sm.aft_lock)
            self._apply_status(self._ext.status())
        except _READ_ERRORS:
            self._stale()
            return
        self._update(aft=_aft(lock, self._state.cents_per_credit), last_reply_time=_utc_now())
        self._sync_meters(credits, game, money)
        for entry in list(self._open.values()):
            if entry is not self._page_spin and not entry.inconsistent and not entry.closing:
                self._reconcile(entry)
        self._flush()
        self._next_refresh = self._clock() + REFRESH_INTERVAL
        if not self._state.fresh:
            self._update(fresh=True)

    def _targeted_refresh(self) -> None:
        needs, self._needs = self._needs, set()
        try:
            if needs & {"game", "money"}:
                self._sync_meters(*self._read_meters())
                if "money" in needs:
                    self._update(aft=_aft(self._read(sm.aft_lock), self._state.cents_per_credit))
            if needs & {"game", "status"}:
                self._apply_status(self._ext.status())
        except _READ_ERRORS:
            self._needs |= needs
            self._stale()

    def _stale(self) -> None:
        """A refresh failed: the snapshot may be outdated until one succeeds."""
        self._retry_at = self._next_refresh = self._clock() + GENERAL_POLL_GAP
        self._update(fresh=False)

    def _sync_meters(self, credits: int, game: dict, money: dict) -> None:
        if self._games_played is not None and game["games_played"] > self._games_played:
            self._game_ended(credits, game)
        self._games_played = game["games_played"]
        self._game_meters = game
        self._update(credits=credits, meters=_meters(game, money))

    def _apply_status(self, status: se.MachineStatus, info: se.Info | None = None) -> None:
        """STATUS read fresh: the profile it names, the arm and a page spin's end."""
        changes: dict = {"ext_status": status}
        if info is not None:
            changes["ext_info"] = info
        known = self._profile_status
        if status.fault:
            changes["profile"] = None
            self._profile_status = None
        elif known is None or (known.active, known.generation, known.profile_crc) != (
                status.active, status.generation, status.profile_crc):
            profile, under = se.download(self._ext)
            changes["profile"] = ws.ProfileState(under.active, under.generation, profile)
            self._profile_status = under
        self._update(**changes)
        self._derive_arm(status)
        spin = self._page_spin
        if (spin is not None and status.last_id == spin.record["id"]
                and status.last_status in (se.Status.INTERRUPTED, se.Status.FAULT)):
            word = OutcomeWord.INTERRUPTED if status.last_status == se.Status.INTERRUPTED else OutcomeWord.FAULT
            self._page_spin = None
            self._close(spin, word)
            self._update(in_progress=None)

    def _derive_arm(self, status: se.MachineStatus) -> None:
        """Armed or not comes from STATUS; the journal only names the arm (decision-8 r5)."""
        record = self._arm_record
        state = record.get("state", ArmState.NONE.value)
        arm_id = record.get("arm_id")
        op, result, last = status.last_op, status.last_status, status.last_id
        # An unknown arm stays unknown until STATUS settles it: taken, cleared or cancelled.
        tracked = state != ArmState.NONE.value and arm_id is not None
        if op == se.Op.ARM_OUTCOME and result == se.Status.ACCEPTED:
            if tracked and arm_id == last:
                named = record.get("name") is not None  # journaled by this page when it armed it
                new = {**record, "state": ArmState.ARMED.value if named else state}
            else:
                new = {"state": ArmState.UNKNOWN.value, "arm_id": last}  # no journaled intent explains it
        elif not tracked:
            new = dict(NO_ARM)
        elif last == arm_id and op == se.Op.ARM_OUTCOME and result in (se.Status.INTERRUPTED, se.Status.OK):
            new = dict(NO_ARM)  # cleared by a door or reset, or taken
        elif last > arm_id and op == se.Op.BET_SPIN:
            if result == se.Status.ACCEPTED:
                new = record  # the bound game is running
            elif result in (se.Status.INTERRUPTED, se.Status.FAULT):
                new = {**record, "state": ArmState.HELD.value}  # kept until CANCEL
            elif result == se.Status.OK:
                new = dict(NO_ARM)  # consumed
            else:
                new = {**record, "state": ArmState.UNKNOWN.value}  # refused: STATUS no longer shows the arm
        elif last > arm_id and op == se.Op.CANCEL_OUTCOME and result == se.Status.OK:
            new = dict(NO_ARM)
        else:
            new = {**record, "state": ArmState.UNKNOWN.value}
        if new != record:
            self._set_arm(new)
            self._save({"kind": "event", "source": "page", "event": "arm", "arm": new})
        elif self._state.arm != _arm(record):
            self._update(arm=_arm(record))

    def _set_arm(self, record: dict) -> None:
        self._arm_record = dict(record)
        self._update(arm=_arm(record))

    def _game_ended(self, credits: int, game: dict) -> None:
        """Games were played since the last read: journal them as one spin, the page's if
        a page spin is waiting for its game."""
        stops = self._read(sm.reel_stops)
        spin = self._page_spin
        if spin is not None:
            base_game, base_credits = spin.record["baseline_game"], spin.record["baseline_credits"]
        else:
            base_game, base_credits = self._game_meters, self._state.credits
        games = game["games_played"] - base_game["games_played"]
        total_bet = game["coin_in"] - base_game["coin_in"]
        paid = game["coin_out"] - base_game["coin_out"]
        lines = self._state.profile.lines if self._state.profile is not None else 1
        bet_per_line = total_bet // (lines * games) if games > 0 else 0
        source = "page" if spin is not None else "machine"
        record = {"kind": "spin", "source": source, "bet_per_line": bet_per_line, "total_bet": total_bet,
                  "stops": list(stops), "paid": paid, "credits": credits, "games": games,
                  "name": self._winning_name(stops, bet_per_line) if paid else None}
        if spin is not None and spin.submission_id:
            record["submission_id"] = spin.submission_id
        self._save(record, entry=None)
        self._update(last_spin=ws.LastSpin(_utc_now(), source, bet_per_line, total_bet, tuple(stops), paid,
                                           credits))
        if spin is not None:
            self._page_spin = None
            evidence = ws.SpinEvidence(bet_per_line, total_bet, paid, games, credits - base_credits)
            word = OutcomeWord.HAPPENED if spin.record.get("reconciled") else OutcomeWord.PLAYED
            self._close(spin, word, {"evidence": asdict(evidence)}, evidence=evidence,
                        credits_delta=credits - base_credits)
            self._update(in_progress=None)

    def _winning_name(self, stops: tuple, bet_per_line: int) -> str | None:
        state = self._state
        if state.profile is None or not 1 <= bet_per_line <= state.profile.max_bet_per_line:
            return None
        score = sp.score(state.profile.profile, tuple(stops), bet_per_line)
        index = next((record for record, award in score.lines if award), None)
        if index is None:
            return None
        found = ws.find_outcome(ws.catalogue(replace(state, bet_per_line=bet_per_line)), str(index))
        return None if found is None else found.name

    # Actions

    def _run(self, action: Action) -> None:
        refusal = self._readmit(action, self._state)
        if refusal is None and self._host is None:
            refusal = Refusal(Code.OFFLINE, "Offline", action.submission_id)
        if refusal is None:
            try:
                self._prepare(action)
            except _READ_ERRORS as e:
                refusal = Refusal(Code.STALE, f"Could not read the machine: {e}", action.submission_id)
            else:
                refusal = self._readmit(action, self._state)
        if refusal is not None:
            self._set_result(_refused(action, refusal))
            return
        self._current = None
        self._upload = None
        self._set_result(ActionResult(action.submission_id, action.name, ResultState.RUNNING))
        self._update(in_progress=action)
        try:
            self._execute(action)
        except _Refused as e:
            self._set_result(_refused(action, e.refusal))
        except _NotRecorded as e:
            if self._current is None:
                self._set_result(_refused(action, Refusal(
                    Code.JOURNAL, f"Could not save to the log, so nothing was sent: {e}", action.submission_id)))
            else:
                self._settle_failed(action, None)
        except _Invalidated as e:
            self._settle_failed(action, None, f"Stopped before the profile was activated: {e}")
        except sm.NotAdmitted as e:
            code = Code.AFT_UNAVAILABLE if ws.FAMILIES.get(action.name) is Family.MONEY else Code.UNSUPPORTED
            self._set_result(_refused(action, Refusal(code, str(e), action.submission_id)))
        except sp.ProfileError as e:
            self._set_result(_refused(action, Refusal(Code.INVALID_PROFILE, str(e), action.submission_id)))
        except se.Unavailable as e:
            self._set_result(_refused(action, Refusal(Code.UNSUPPORTED, str(e), action.submission_id)))
        except sm.Uncertain as e:
            self._settle_failed(action, e.__cause__)
        except se.UncertainOutcome as e:
            self._settle_failed(action, e.cause, failure=e)
        except se.Refused as e:
            if self._current is None:  # a query refused before anything was sent
                self._set_result(_refused(action, Refusal(Code.UNSUPPORTED, str(e), action.submission_id)))
            elif self._upload is not None:
                self._settle_failed(action, None, failure=e)
            else:
                entry = self._open[self._current]
                reason = f"The machine refused it ({e.status.name})"
                if action.name is Act.RESTORE_STOCK:
                    # A refusal other than ws.UNCHANGED_REFUSALS can follow a partial profile
                    # transition: STATUS, read next, says which game is active.
                    self._profile_status = None
                    self._needs.add("status")
                self._close(entry, OutcomeWord.REFUSED, {"code": e.status.name}, reason=reason,
                            machine_status=e.status)
        except _READ_ERRORS as e:
            if self._current is None:
                self._set_result(_refused(action, Refusal(Code.STALE, f"Could not read the machine: {e}",
                                                          action.submission_id)))
            else:
                self._settle_failed(action, None)
        except LineLost:
            if self._current is None:
                self._set_result(_refused(action, Refusal(Code.OFFLINE, "Offline", action.submission_id)))
            else:
                entry = self._open[self._current]
                self._hold(entry, CHECKING)
                self._set_result(ActionResult(action.submission_id, action.name, ResultState.CHECKING,
                                              reason=CHECKING))
            raise
        finally:
            self._update(in_progress=self._spin_action if self._page_spin is not None else None)
            self._current = None
            self._upload = None

    def _readmit(self, action: Action, state: ws.MachineState) -> Refusal | None:
        """admit, and no second action of a family whose intent is still open."""
        refusal = ws.admit(state, action)
        family = ws.FAMILIES.get(action.name)
        if refusal is None and family is not None and any(e.family is family for e in self._open.values()):
            refusal = Refusal(Code.PENDING, "Waiting for the machine", action.submission_id)
        return refusal

    def _prepare(self, action: Action) -> None:
        """Read fresh what admission needs (decision-3 r8, decision-6 r3)."""
        name = action.name
        if ws.FAMILIES.get(name) is Family.MONEY:
            credits, lock = self._read(sm.current_credits), self._read(sm.aft_lock)
            self._update(credits=credits, aft=_aft(lock, self._state.cents_per_credit))
        elif ws.FAMILIES.get(name) is Family.EXTENSION:
            info = self._ext.info()
            self._apply_status(self._ext.status(), info)
            if name in (Act.SPIN, Act.ARM):
                credits, game = self._read(sm.current_credits), self._read(sm.game_meters)
                play = not self._read(sm.play_disabled)
                self._update(credits=credits, play_enabled=play)
                self._spin_baseline = {"baseline_game": game, "baseline_credits": credits}

    def _execute(self, action: Action) -> None:
        name = action.name
        self._pace()
        if name is Act.ADD_CREDITS:
            self._transfer(action, sm.deposit(self._host, action.params["amount"], record=self._recorder(action),
                                              pace=self._pace))
        elif name is Act.CASH_OUT:
            self._transfer(action, sm.cash_out(self._host, record=self._recorder(action), pace=self._pace))
        elif name is Act.SPIN:
            self._spin(action)
        elif name is Act.ARM:
            self._arm(action)
        elif name is Act.CANCEL_ARM:
            reply = sm.cancel(self._ext, record=self._recorder(action))
            self._after_mutation(reply, se.Op.CANCEL_OUTCOME)
            cancelled = self._arm_record.get("name")
            self._set_arm(dict(NO_ARM))
            self._close(self._open[self._current], OutcomeWord.CANCELLED,
                        {"outcome_name": cancelled, "arm": dict(NO_ARM)})
        elif name is Act.APPLY_PROFILE:
            self._apply_profile(action)
        elif name is Act.RESTORE_STOCK:
            reply = se.restore(self._ext, record=self._recorder(action))
            self._profile_verified(self._open[self._current], OutcomeWord.RESTORED, reply["generation"],
                                   False, sp.stock_profile())
        elif name is Act.PLAY:
            result = sm.set_play(self._host, bool(action.params["on"]), record=self._recorder(action),
                                 pace=self._pace)
            word = OutcomeWord.CONFIRMED if result.confirmed else OutcomeWord.NOT_CONFIRMED
            self._update(play_enabled=result.play_enabled)
            self._close(self._open[self._current], word, {"play_enabled": result.play_enabled})
        elif name in ws.SETTING_NAMES:
            self._ack_only(action)
        elif name is Act.CLOCK_SYNC:
            result = sm.set_clock(self._host, _local_now(), record=self._recorder(action), pace=self._pace)
            word = OutcomeWord.CONFIRMED if result.confirmed else OutcomeWord.NOT_CONFIRMED
            offset = _clock_offset(result.read)
            self._update(settings=replace(self._state.settings, clock_offset=offset))
            self._close(self._open[self._current], word, {"clock_offset": offset})
        else:
            raise ValueError(f"{name.value} is not a machine action")

    def _recorder(self, action: Action, extra: dict | None = None) -> Callable[[dict], None]:
        """The operation's record callback: the last step before each of its sends."""
        def record(intent: dict) -> None:
            if intent.get("kind") == "step":
                self._record_step(action, intent)
                return
            refusal = self._readmit(action, replace(self._state, in_progress=None))
            if refusal is not None:
                raise _Refused(refusal)
            fields = {key: value for key, value in intent.items() if key != "kind"}
            stored = self._append_now({"kind": "intent", "source": "page", "submission_id": action.submission_id,
                                       **fields, **(extra or {})})
            entry = _Open(stored, [])
            self._open[entry.seq] = entry
            self._current = entry.seq
            if fields.get("op") == se.UPLOAD_OP:
                self._upload = _Upload(entry.seq, fields["previous"], self._disturbances)
        return record

    def _record_step(self, action: Action, step: dict) -> None:
        """Before each upload step: this upload may continue (decision-6 r3), then the
        previous step's acknowledgement and this step are journaled."""
        upload = self._upload
        state = self._state
        status = state.ext_status
        previous = upload.previous
        if state.link is not Link.ONLINE or not state.fresh:
            raise _Invalidated("the machine stopped answering or needs a full read")
        if status is None or (status.active, status.profile_crc, status.generation) != (
                previous["active"], previous["profile_crc"], previous["generation"]):
            raise _Invalidated("the active profile changed")
        if self._disturbances != upload.disturbances:
            raise _Invalidated("the machine reported a door, power or reset event")
        entry = self._open[upload.seq]
        common = {"kind": "step", "source": "page", "intent": entry.seq, "submission_id": action.submission_id}
        if entry.steps:
            last = entry.steps[-1]
            entry.steps.append(self._append_now({**common, "upload_id": last["upload_id"], "op": last["op"],
                                                 "id": last["id"], "result": sm.STEP_ACKED}))
        fields = {key: value for key, value in step.items() if key != "kind"}
        entry.steps.append(self._append_now({**common, **fields}))

    def _append_now(self, record: dict) -> dict:
        """Append before a send: any failure means the send does not happen."""
        try:
            stored = self._journal.append(record)
        except _JOURNAL_ERRORS as e:
            raise _NotRecorded(f"{type(e).__name__}: {e}") from e
        self._appended(stored)
        return stored

    def _transfer(self, action: Action, outcome: sm.TransferOutcome) -> None:
        entry = self._open[self._current]
        word = OutcomeWord(outcome.status)
        fields = {"code": outcome.code, "deltas": outcome.deltas}
        result = _deltas(outcome.deltas)
        if word is OutcomeWord.MOVED:
            fields["transfer"] = self._transfer_field(entry.record)
            result["transfer_credits"] = fields["transfer"]["credits"]
        self._update(credits=entry.record["baseline"]["credits"] + outcome.deltas["credits"])
        self._close(entry, word, fields, **result)
        self._needs.add("money")

    def _transfer_field(self, intent: dict) -> dict:
        """A completed transfer's direction and amount. Only called once the in-house meters
        moved by exactly the intent's cents (moved, happened), so the amount is verified; the
        credits meter is not, since play at the machine can move it too."""
        cpc = self._state.cents_per_credit or 1
        return {"direction": "in" if intent["op"] == sm.OP_DEPOSIT else "out", "credits": intent["cents"] // cpc}

    def _spin(self, action: Action) -> None:
        bet = ws.chosen_bet(self._state)
        reply = sm.spin(self._ext, bet, record=self._recorder(action, self._spin_baseline))
        self._page_spin, self._spin_action = self._open[self._current], action
        self._after_mutation(reply, se.Op.BET_SPIN)
        self._set_result(ActionResult(action.submission_id, action.name, ResultState.RUNNING))

    def _arm(self, action: Action) -> None:
        state = self._state
        outcome = ws.find_outcome(ws.catalogue(state), str(action.params["outcome"]))
        pick = {"outcome_id": outcome.id, "name": outcome.name, "stops": list(outcome.stops),
                "bet_per_line": ws.chosen_bet(state)}
        reply = sm.arm(self._ext, outcome.stops, record=self._recorder(action, {"pick": pick}))
        self._after_mutation(reply, se.Op.ARM_OUTCOME)
        arm = {"state": ArmState.ARMED.value, "arm_id": reply["id"], **pick}
        self._set_arm(arm)
        self._close(self._open[self._current], OutcomeWord.ARMED, {"outcome_name": outcome.name, "arm": arm})

    def _apply_profile(self, action: Action) -> None:
        profile = action.params["profile"]
        reply = se.upload(self._ext, profile, record=self._recorder(action))
        entry = self._open[self._current]
        commit = entry.steps[-1]
        self._save({"kind": "step", "source": "page", "intent": entry.seq, "submission_id": action.submission_id,
                    "upload_id": commit["upload_id"], "op": commit["op"], "id": commit["id"], "result": sm.STEP_ACKED})
        status = self._state.ext_status
        self._update(ext_status=replace(status, last_id=reply["id"], last_op=se.Op.COMMIT_PROFILE,
                                        last_status=se.Status.OK, active=True, generation=reply["generation"],
                                        profile_crc=int(reply["profile_crc"], 16)))
        self._profile_verified(entry, OutcomeWord.COMMITTED_VERIFIED, reply["generation"], True, profile)

    def _profile_verified(self, entry: _Open, word: OutcomeWord, generation: int, custom: bool,
                          profile: sp.Profile, *, reason: str | None = None) -> None:
        """Close a verified apply or restore, acknowledged or reconciled, with the same records:
        the profile record that Activity shows, naming the intent, goes to the journal before
        the outcome, so a restart between the two finds it on the open intent and Activity
        tells the change once."""
        self._profile_changed(generation, custom, profile)
        if not entry.profile_told:
            entry.profile_told = True
            returns = {str(row.bet_per_line): round(float(row.nominal_return) * 100, 4)
                       for row in sp.preview(profile)}
            self._save({"kind": "profile", "source": "page", "intent": entry.seq,
                        "change": "applied" if custom else "restored", "generation": generation,
                        "returns": returns})
        self._close(entry, word, {"generation": generation}, reason=reason, generation=generation)

    def _profile_changed(self, generation: int, custom: bool, profile: sp.Profile) -> None:
        self._profile_status = None  # read it again under the next STATUS
        self._update(profile=ws.ProfileState(custom, generation, profile))
        self._needs.add("status")

    def _after_mutation(self, reply: dict, op: se.Op) -> None:
        """What the reply showed, until the next STATUS read confirms it."""
        state = self._state
        changes = {}
        if state.ext_status is not None:
            changes["ext_status"] = replace(state.ext_status, last_id=reply["id"], last_op=op,
                                            last_status=se.Status[reply["status"]])
        if state.ext_info is not None:
            changes["ext_info"] = replace(state.ext_info, next_id=reply["id"] + 1)
        self._update(**changes)
        self._needs.add("status")

    def _ack_only(self, action: Action) -> None:
        """Sound and bills have no read-back: only an ACK attributable to this command
        confirms it (decision-5). After a reply was lost, the fresh command waits for a
        validated data reply (sas_machine.ack_boundary), so any late ACK has gone by."""
        name = ws.SETTING_NAMES[action.name]
        on = bool(action.params["on"])
        attributable = True
        if self._boundary_needed:
            self._pace()
            try:
                credits = sm.ack_boundary(self._host, pace=self._pace)
            except sm.ReadFailed:
                attributable = False  # the tap still sends once; its ACK cannot confirm it
            else:
                self._boundary_needed = False
                self._update(credits=credits)
        self._pace()
        operation = sm.set_sound if action.name is Act.SOUND else sm.set_bills
        try:
            result = operation(self._host, on, record=self._recorder(action))
        except sm.Uncertain as e:
            self._settled_setting(name, sm.UNKNOWN, on)
            if isinstance(e.__cause__, LineLost):
                self._lose_line(e.__cause__)
            return
        if result == sm.ACKED and not attributable:
            result = sm.UNKNOWN
        self._settled_setting(name, result, on)

    def _settled_setting(self, name: str, result: str, on: bool) -> None:
        state = self._state.settings
        if result == sm.ACKED:
            value = Setting.ON if on else Setting.OFF
        elif result == sm.NACKED:
            value = getattr(state, name)
        else:
            value = Setting.UNKNOWN
            self._boundary_needed = True
        self._update(settings=replace(state, **{name: value}))
        self._close(self._open[self._current], OutcomeWord(result), {"settings": {name: value.value}})

    # Lost replies and reconciliation

    def _settle_failed(self, action: Action, cause: BaseException | None, reason: str | None = None,
                       failure: Exception | None = None) -> None:
        """The action's intent is open and its outcome unsettled: hold its family, read what
        happened, never resend."""
        entry = self._open[self._current]
        if self._upload is not None:
            self._note_last_step(entry, failure)
        self._hold(entry, CHECKING)
        self._set_result(ActionResult(action.submission_id, action.name, ResultState.CHECKING,
                                      reason=reason or CHECKING))
        if entry is self._page_spin:
            self._page_spin = None
        if isinstance(cause, LineLost):
            self._lose_line(cause)  # reconciled after the reopen
            return
        self._reconcile(entry, reason)

    def _note_last_step(self, entry: _Open, failure: Exception | None) -> None:
        """The result of the last journaled upload step, as the failure shows it."""
        if not entry.steps:
            return
        last = entry.steps[-1]
        if "result" in last:
            return
        if isinstance(failure, se.UncertainOutcome) and failure.id == last["id"]:
            result = sm.STEP_LOST
        elif isinstance(failure, se.Refused) and failure.id == last["id"]:
            result = sm.STEP_REFUSED
        else:
            result = sm.STEP_ACKED  # a step's failure would have raised at it; a later one came after
        step = {"kind": "step", "source": "page", "intent": entry.seq, "upload_id": last["upload_id"],
                "op": last["op"], "id": last["id"], "result": result}
        if entry.submission_id:
            step["submission_id"] = entry.submission_id
        entry.steps.append(dict(step))
        self._save(step)

    def _reconcile(self, entry: _Open, reason: str | None = None) -> None:
        """Read what an open intent did and close it, or keep its family held."""
        op = entry.record["op"]
        try:
            self._pace()
            if op in MONEY_OPS:
                found = sm.reconcile_transfer(self._host, entry.record, pace=self._pace)
                self._reconciled_money(entry, found)
            elif op == se.UPLOAD_OP:
                steps = _merged(entry.steps)
                found = sm.reconcile_extension(self._ext, entry.record, steps=steps)
                self._reconciled_upload(entry, found, reason)
            elif op in EXTENSION_OPS:
                found = sm.reconcile_extension(self._ext, entry.record)
                self._reconciled_extension(entry, found)
            elif op == sm.OP_SET_PLAY:
                shown = not self._read(sm.play_disabled)
                word = OutcomeWord.HAPPENED if shown == entry.record["enabled"] else OutcomeWord.NOTHING_CHANGED
                self._update(play_enabled=shown)
                self._close(entry, word, {"play_enabled": shown})
            elif entry.act in ws.SETTING_NAMES:
                name = ws.SETTING_NAMES[entry.act]
                self._boundary_needed = True
                self._update(settings=replace(self._state.settings, **{name: Setting.UNKNOWN}))
                self._close(entry, OutcomeWord.UNKNOWN, {"settings": {name: Setting.UNKNOWN.value}})
            else:
                self._close(entry, OutcomeWord.UNKNOWN)
        except _READ_ERRORS:
            self._hold(entry, CHECKING)  # still unknown: read again at the next refresh

    def _reconciled_money(self, entry: _Open, found: sm.Reconciled) -> None:
        if found.result == sm.PENDING:
            self._hold(entry, CHECKING)
            return
        if found.result == sm.INCONSISTENT:
            self._inconsistent(entry)
            return
        deltas = found.evidence["deltas"]
        fields = {"deltas": deltas}
        result = _deltas(deltas)
        if found.result == sm.HAPPENED:
            fields["transfer"] = self._transfer_field(entry.record)
            result["transfer_credits"] = fields["transfer"]["credits"]
        self._close(entry, OutcomeWord(found.result), fields, **result)
        self._needs.add("money")

    def _reconciled_upload(self, entry: _Open, found: sm.Reconciled, reason: str | None) -> None:
        if found.result == sm.PENDING:
            self._hold(entry, "Checking whether the profile was applied")
            return
        if found.result == sm.INCONSISTENT:
            self._inconsistent(entry)
            return
        if found.result == sm.COMMITTED_VERIFIED:
            profile = sp.decode(bytes.fromhex(entry.record["profile_hex"]))  # the bytes the read-back equalled
            self._profile_verified(entry, OutcomeWord.COMMITTED_VERIFIED, found.evidence["status"]["generation"],
                                   True, profile, reason=reason)
            return
        self._profile_status = None
        self._needs.add("status")
        self._close(entry, OutcomeWord(found.result), reason=reason)

    def _reconciled_extension(self, entry: _Open, found: sm.Reconciled) -> None:
        if found.result == sm.PENDING:
            self._hold(entry, CHECKING)
            if "status" in found.evidence:
                self._needs.add("status")  # what it read, a fault included, shows while it waits
            return
        if found.result == sm.INCONSISTENT:
            self._inconsistent(entry)
            return
        if found.result == sm.RESTORED:
            self._profile_verified(entry, OutcomeWord.RESTORED, found.evidence["status"]["generation"],
                                   False, sp.stock_profile())
            return
        op = se.Op[entry.record["op"]]
        self._needs.add("status")
        fields: dict = {}
        if found.result == sm.HAPPENED and op == se.Op.BET_SPIN:
            # The game's end closes it, with the spin's evidence.
            entry.record = {**entry.record, "reconciled": True}
            self._release(entry)
            self._page_spin, self._spin_action = entry, Action(Act.SPIN, entry.submission_id or "")
            self._update(in_progress=self._spin_action)
            if entry.submission_id:
                self._set_result(ActionResult(entry.submission_id, Act.SPIN, ResultState.RUNNING))
            if self._games_played is not None and self._games_played > entry.record["baseline_game"]["games_played"]:
                self._game_ended(self._state.credits, self._game_meters)  # counted before it was known
            else:
                self._needs.add("game")
            return
        if found.result == sm.HAPPENED and op == se.Op.ARM_OUTCOME:
            pick = entry.record.get("pick", {})
            fields["arm"] = {"state": ArmState.ARMED.value, "arm_id": entry.record["id"], **pick}
            fields["outcome_name"] = pick.get("name")
            self._set_arm(fields["arm"])  # STATUS, read next, says whether it is still armed
        elif found.result == sm.HAPPENED and op == se.Op.CANCEL_OUTCOME:
            fields["arm"] = dict(NO_ARM)
            self._set_arm(fields["arm"])
        elif op == se.Op.RESTORE_STOCK:
            self._profile_status = None  # interrupted, fault or nothing changed: read what is active
        self._close(entry, OutcomeWord(found.result), fields)

    def _inconsistent(self, entry: _Open) -> None:
        entry.inconsistent = True
        self._hold(entry, INCONSISTENT)
        if entry.submission_id:
            self._set_result(ActionResult(entry.submission_id, entry.act, ResultState.CHECKING,
                                          outcome=OutcomeWord.INCONSISTENT, reason=INCONSISTENT))

    def _hold(self, entry: _Open, reason: str) -> None:
        if entry.family is None:
            return
        self._holds[entry.family] = Hold(reason, entry.seq)
        self._update(checking=MappingProxyType(dict(self._holds)))

    def _release(self, entry: _Open) -> None:
        hold = self._holds.get(entry.family) if entry.family is not None else None
        if hold is not None and hold.intent_seq == entry.seq:
            del self._holds[entry.family]
            self._update(checking=MappingProxyType(dict(self._holds)))

    # The journal after a send

    def _close(self, entry: _Open, outcome: OutcomeWord, fields: dict | None = None, *,
               reason: str | None = None, **result: object) -> None:
        """Journal the intent's outcome, with fields beside its result; the action is done only
        once it is durable."""
        entry.closing = True
        record = {"kind": "outcome", "source": "page", "intent": entry.seq, "action": entry.act.value,
                  "result": outcome.value, **(fields or {})}
        done = None
        if entry.submission_id:
            record["submission_id"] = entry.submission_id
            done = ActionResult(entry.submission_id, entry.act, ResultState.DONE, outcome=outcome, reason=reason,
                                **result)
        self._save(record, entry=entry, result=done)

    def _save(self, record: dict, *, entry: _Open | None = None, result: ActionResult | None = None) -> None:
        """Append now if the journal takes it, else keep it, in order, for the next retry."""
        self._later.append(_Later(record, entry, result))
        self._flush()

    def _flush(self) -> None:
        while self._later:
            later = self._later[0]
            try:
                stored = self._journal.append(later.record)
            except _JOURNAL_ERRORS as e:
                self._not_saved(e)
                return
            self._later.pop(0)
            self._appended(stored)
            if later.entry is not None:
                self._open.pop(later.entry.seq, None)
                self._release(later.entry)
            if later.result is not None:
                self._set_result(later.result)

    def _not_saved(self, error: BaseException) -> None:
        """After a possible send, a failed append is never 'done' and never 'nothing sent':
        every record still queued waits behind it, so each one's family stays held until it
        is durable (a profile record goes before its intent's outcome)."""
        reason = f"Could not save to the log ({type(error).__name__}: {error}); checking"
        for later in self._later:
            if later.entry is not None:
                self._hold(later.entry, reason)
            if later.result is not None:
                self._set_result(replace(later.result, state=ResultState.CHECKING, reason=reason))
        self._next_refresh = min(self._next_refresh, self._clock() + REFRESH_INTERVAL)

    def _appended(self, stored: dict) -> None:
        changes: dict = {}
        if stored["kind"] in ws.ACTIVITY_KINDS:
            changes["log"] = (MappingProxyType(stored), *self._state.log)[:LOG_LIMIT]
        if stored["kind"] == "outcome" and "transfer" in stored:
            changes["counters"] = self._journal.counters()
        if changes:
            self._update(**changes)


def _refused(action: Action, refusal: Refusal) -> ActionResult:
    return ActionResult(action.submission_id, action.name, ResultState.REFUSED, reason=refusal.reason,
                        code=refusal.code)


def _merged(steps: list[dict]) -> list[dict]:
    """One record per upload step, its later result records folded in."""
    by_id: dict[int, dict] = {}
    for step in steps:
        by_id.setdefault(step["id"], {}).update(step)
    return list(by_id.values())


def _arm(record: dict, *, unconfirmed: bool = False) -> Arm:
    state = ArmState(record.get("state", ArmState.NONE.value))
    if state is ArmState.NONE:
        return Arm()
    if unconfirmed:
        state = ArmState.UNKNOWN  # until STATUS confirms it
    stops = record.get("stops")
    return Arm(state, record.get("arm_id"), record.get("outcome_id"), record.get("name"),
               None if stops is None else tuple(stops), record.get("bet_per_line"))


def _aft(lock: dict, cents_per_credit: int | None) -> ws.Aft:
    available = int(lock["available_transfers"], 16)
    enabled = int(lock["aft_status"], 16) & sm.AFT_IN_HOUSE_ENABLED == sm.AFT_IN_HOUSE_ENABLED
    limit = lock["transfer_limit_cents"] // cents_per_credit if cents_per_credit else 0
    return ws.Aft(to_machine=enabled and bool(available & sm.TO_MACHINE),
                  to_host=enabled and bool(available & sm.TO_HOST),
                  locked=lock["lock_status"] != sm.NOT_LOCKED, limit_credits=limit)


def _meters(game: dict, money: dict) -> ws.Meters:
    return ws.Meters(coin_in=game["coin_in"], coin_out=game["coin_out"], jackpot=game["jackpot"],
                     games_played=game["games_played"], games_won=game["games_won"],
                     slot_door_opened=game["slot_door_opened"], power_reset=game["power_reset"],
                     in_house_in_cents=money["in_cents"], in_house_in_count=money["in_count"],
                     in_house_out_cents=money["out_cents"], in_house_out_count=money["out_count"])


def _deltas(deltas: dict) -> dict:
    return {"credits_delta": deltas["credits"], "in_house_in_cents_delta": deltas["in_cents"],
            "in_house_out_cents_delta": deltas["out_cents"]}


def _last_spin(record: dict) -> ws.LastSpin:
    return ws.LastSpin(record["time"], record["source"], record["bet_per_line"], record["total_bet"],
                       tuple(record["stops"]), record["paid"], record["credits"])


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _local_now() -> dt.datetime:
    """The server's local time without a zone, as SAS carries time."""
    return dt.datetime.now().replace(microsecond=0)  # noqa: DTZ005 -- SAS time is zone-less local time


def _clock_offset(machine_time: dt.datetime) -> float:
    return (machine_time - _local_now()).total_seconds()
