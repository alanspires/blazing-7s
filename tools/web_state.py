"""What the web control page knows about the machine, and what it lets people do.

Pure domain module, no I/O. The machine worker publishes a `MachineState` snapshot; the
views and the worker both ask `admit(state, action)` whether an action may go, so a
disabled control and a server-side refusal can never disagree. Display state never
authorizes a write: the worker re-reads the machine and asks `admit` again, against that
fresh state and the values the page rendered, immediately before it sends anything.

`public_view` is the bounded, JSON-ready projection scripts read (`GET /state.json`), and
`catalogue` lists the next results a person can pick by name at the chosen bet, through
`sas_profile.build_catalogue`.

A spin costs the bet per line times the profile's lines, as the extension charges it
(firmware/src/game.c, `game_line_wager`; tools/sas_profile.py module notes).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from types import MappingProxyType

import sas_extension as se
import sas_profile as sp

RESULTS_LIMIT = 32  # newest action results kept in a snapshot and served by /state.json
# Journal kinds people see, in MachineState.log and on Activity: intents and upload steps hold
# request bytes, which include the AFT registration key, and never show.
ACTIVITY_KINDS = ("event", "spin", "outcome", "profile", "page")


class Link(enum.Enum):
    STARTING = "starting"
    ONLINE = "online"
    OFFLINE = "offline"


class ArmState(enum.Enum):
    NONE = "none"
    ARMED = "armed"  # STATUS shows the journaled arm pending
    HELD = "held"  # a bound spin ended INTERRUPTED or FAULT; the firmware keeps it until CANCEL
    UNKNOWN = "unknown"  # STATUS cannot confirm what is armed (after a restart, or unexplained)


class Setting(enum.Enum):
    ON = "on"
    OFF = "off"
    UNKNOWN = "unknown"  # an ACK-only setting whose reply was lost


class Family(enum.Enum):
    """Actions whose reconciliation evidence another action of the family could erase."""
    MONEY = "money"
    EXTENSION = "extension"
    PLAY = "play"


class Act(enum.Enum):
    """Actions a page can submit; the values are the action route names."""
    BET_ONE = "bet-one"
    MAX_BET = "max-bet"
    SPIN = "spin"
    ARM = "arm"
    CANCEL_ARM = "cancel-arm"
    ADD_CREDITS = "add-credits"
    CASH_OUT = "cash-out"
    APPLY_PROFILE = "apply-profile"
    RESTORE_STOCK = "restore-stock"
    PLAY = "play"
    SOUND = "sound"
    BILLS = "bills"
    CLOCK_SYNC = "clock-sync"


FAMILIES = MappingProxyType({
    Act.ADD_CREDITS: Family.MONEY, Act.CASH_OUT: Family.MONEY,
    Act.SPIN: Family.EXTENSION, Act.ARM: Family.EXTENSION, Act.CANCEL_ARM: Family.EXTENSION,
    Act.APPLY_PROFILE: Family.EXTENSION, Act.RESTORE_STOCK: Family.EXTENSION,
    Act.PLAY: Family.PLAY,
})


class Code(enum.Enum):
    """Why admit refused an action."""
    OFFLINE = "offline"
    STALE = "stale"  # online, but the snapshot is not fresh
    CHECKING = "checking"  # the action's family waits on a reconciliation
    UNSUPPORTED = "unsupported"  # no extension capability, no usable ID, or no known profile
    PAUSED = "paused"  # play disabled
    DOOR_OPEN = "door_open"  # a door is known open
    BUSY = "busy"  # another action is in progress
    PENDING = "pending"  # STATUS shows an extension action still ACCEPTED
    OUT_OF_DATE = "out_of_date"  # the page rendered another generation, bet or arm
    NO_CREDITS = "no_credits"
    CREDITS = "credits"  # fewer credits than the total wager
    ARM_SET = "arm_set"  # a next result is set: cancel it first
    ARM_BET = "arm_bet"  # the set next result was armed at another bet
    ARM_UNSETTLED = "arm_unsettled"  # a held or unknown next result: cancel it first
    NOTHING_SET = "nothing_set"
    OUTCOME_UNAVAILABLE = "outcome_unavailable"
    AFT_UNAVAILABLE = "aft_unavailable"
    OVER_LIMIT = "over_limit"
    BAD_AMOUNT = "bad_amount"
    JOURNAL = "journal"  # the intent could not be saved, so nothing was sent
    INVALID_PROFILE = "invalid_profile"  # the draft is outside the machine's caps; nothing was sent


class ResultState(enum.Enum):
    QUEUED = "queued"
    REFUSED = "refused"
    RUNNING = "running"
    CHECKING = "checking"
    DONE = "done"


class OutcomeWord(enum.Enum):
    """How an action went: `ActionResult.outcome`, and as its value the `result` of the
    journaled outcome record. Where the worker passes on a sas_machine result (a transfer's
    status, a reconciliation, an ACK-only setting) the value is sas_machine's word."""
    MOVED = "moved"  # a transfer completed
    REJECTED = "rejected"  # the machine refused a transfer: nothing moved
    PLAYED = "played"  # a page spin's game ended
    INTERRUPTED = "interrupted"  # a page spin's game stopped before it ended
    FAULT = "fault"
    REFUSED = "refused"  # the extension refused the command
    ARMED = "armed"
    CANCELLED = "cancelled"
    COMMITTED_VERIFIED = "committed_verified"  # an upload activated and read back equal
    ABORTED = "aborted"  # an upload's COMMIT never took effect
    STAGING_ABANDONED = "staging_abandoned"  # an upload stopped before COMMIT was sent
    RESTORED = "restored"
    CONFIRMED = "confirmed"  # play or the clock reads back as set
    NOT_CONFIRMED = "not_confirmed"
    ACKED = "acked"  # an ACK-only setting's own ACK
    NACKED = "nacked"
    UNKNOWN = "unknown"  # no attributable answer: the setting may or may not have changed
    BET = "bet"  # Bet one or Max bet chose the bet; nothing was sent
    HAPPENED = "happened"  # checked after a lost reply: it went through
    NOTHING_CHANGED = "nothing_changed"  # checked after a lost reply: it did not
    INCONSISTENT = "inconsistent"  # on a result still checking: the machine's records disagree


@dataclass(frozen=True)
class Action:
    """A submitted action with the expected values of the page that rendered it.

    params by action: ADD_CREDITS `amount` (int credits; without it admit answers whether
    top-ups are possible at all), ARM `outcome` (a catalogue `Outcome.id` as text),
    PLAY/SOUND/BILLS `on` (bool), APPLY_PROFILE `profile` (sas_profile.Profile).
    """
    name: Act
    submission_id: str
    params: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    profile_generation: int | None = None
    bet_per_line: int | None = None
    arm_id: int | None = None  # extension id of the arm the page showed; None: no arm


@dataclass(frozen=True)
class Refusal:
    code: Code
    reason: str
    submission_id: str


@dataclass(frozen=True)
class Meters:
    """Lifetime meters: 1C in credits, in-house AFT meters in cents and counts."""
    coin_in: int
    coin_out: int
    jackpot: int
    games_played: int
    games_won: int
    slot_door_opened: int
    power_reset: int
    in_house_in_cents: int
    in_house_in_count: int
    in_house_out_cents: int
    in_house_out_count: int


@dataclass(frozen=True)
class Aft:
    to_machine: bool  # the available-transfers bit for in-house transfers to the machine
    to_host: bool
    locked: bool
    limit_credits: int  # the reported transfer limit, cents converted exactly to credits


@dataclass(frozen=True)
class ProfileState:
    custom: bool  # False: the stock game
    generation: int
    profile: sp.Profile

    @property
    def lines(self) -> int:
        return self.profile.lines

    @property
    def max_bet_per_line(self) -> int:
        return self.profile.max_bet_per_line


@dataclass(frozen=True)
class Arm:
    """The next result: armed or not comes from STATUS, the journal only names it."""
    state: ArmState = ArmState.NONE
    arm_id: int | None = None  # ARM_OUTCOME's extension id
    outcome_id: int | str | None = None  # the catalogue key it was picked as
    name: str | None = None
    stops: tuple[int, ...] | None = None
    bet_per_line: int | None = None  # the bet it was armed at; pays are promised only at it


@dataclass(frozen=True)
class Hold:
    reason: str
    intent_seq: int | None = None  # the journal intent the family waits on


@dataclass(frozen=True)
class LastSpin:
    time: str  # UTC ISO
    source: str  # "page" or "machine"
    bet_per_line: int
    total_bet: int
    stops: tuple[int, ...]
    paid: int
    credits: int  # balance after the game


# The ACK-only settings' `Settings` field names, which journal records also use.
SETTING_NAMES = MappingProxyType({Act.SOUND: "sound", Act.BILLS: "bills"})


@dataclass(frozen=True)
class Settings:
    sound: Setting = Setting.UNKNOWN
    bills: Setting = Setting.UNKNOWN
    clock_offset: float | None = None  # machine clock minus server clock, seconds


@dataclass(frozen=True)
class Counters:
    """Money moved by this page, from the journal."""
    credits_added: int = 0
    top_ups: int = 0
    credits_cashed_out: int = 0
    cash_outs: int = 0


@dataclass(frozen=True)
class SpinEvidence:
    """A page spin's result, correlated from the game's end read."""
    bet_per_line: int
    total_wager: int
    paid: int  # coin-out meter delta across the game
    games_played_delta: int
    credits_delta: int


@dataclass(frozen=True)
class ActionResult:
    submission_id: str
    action: Act
    state: ResultState
    outcome: OutcomeWord | None = None
    reason: str | None = None
    code: Code | None = None  # a refusal's code
    credits_delta: int | None = None  # the net balance change, play at the machine included
    transfer_credits: int | None = None  # a completed transfer's amount, as the in-house meters verified it
    in_house_in_cents_delta: int | None = None
    in_house_out_cents_delta: int | None = None
    evidence: SpinEvidence | None = None
    generation: int | None = None  # a verified profile change's committed generation
    machine_status: se.Status | None = None  # the extension's own non-OK reply to the action


# Extension statuses the firmware answers before any side effect (firmware/src/core.c
# process_mutation, execute and commit_profile; game.c validate_change): a mutation refused
# with one of them changed nothing. Any other, FAULT above all, can follow a partial
# profile transition (game.c game_finish_profile_change), so only STATUS says what is active.
UNCHANGED_REFUSALS = frozenset((se.Status.BAD_REQUEST, se.Status.BAD_VERSION, se.Status.BAD_ID,
                                se.Status.BUSY, se.Status.INVALID_PROFILE, se.Status.INCOMPLETE,
                                se.Status.UNSUPPORTED))


def changed_nothing(result: ActionResult) -> bool:
    """Whether a result the machine said no to is known to have changed nothing: a correlated
    transfer rejection or settings NACK, or an extension refusal answered before any side
    effect."""
    if result.outcome in (OutcomeWord.REJECTED, OutcomeWord.NACKED):
        return True
    return result.outcome is OutcomeWord.REFUSED and result.machine_status in UNCHANGED_REFUSALS


@dataclass(frozen=True)
class MachineState:
    link: Link = Link.STARTING
    fresh: bool = False  # tracked apart from link: online with an outdated snapshot is not fresh
    last_reply_time: str | None = None  # UTC ISO of the last valid reply
    credits: int | None = None
    cents_per_credit: int | None = None
    meters: Meters | None = None
    aft: Aft | None = None
    doors: Mapping[str, bool] | None = None  # None until a baseline; door name -> open
    play_enabled: bool = False
    profile: ProfileState | None = None
    ext_info: se.Info | None = None
    ext_status: se.MachineStatus | None = None  # the extension's last STATUS
    arm: Arm = Arm()
    bet_per_line: int | None = None  # the shared chosen bet; None: the profile's maximum
    in_progress: Action | None = None
    game_running: bool = False  # the machine reported a game start (7E) whose end is not yet read
    checking: Mapping[Family, Hold] = field(default_factory=lambda: MappingProxyType({}))
    last_spin: LastSpin | None = None
    settings: Settings = Settings()
    counters: Counters = Counters()
    log: tuple[Mapping[str, object], ...] = ()  # newest ACTIVITY_KINDS records, newest first
    results: tuple[ActionResult, ...] = ()  # newest first, at most RESULTS_LIMIT


def chosen_bet(state: MachineState) -> int | None:
    """The shared bet per line, clamped to the active profile's 1..maximum."""
    if state.profile is None:
        return None
    top = state.profile.max_bet_per_line
    bet = top if state.bet_per_line is None else state.bet_per_line
    return min(max(bet, 1), top)


def total_wager(state: MachineState) -> int | None:
    bet = chosen_bet(state)
    return None if bet is None else bet * state.profile.lines


def profile_generation(state: MachineState) -> int | None:
    return None if state.profile is None else state.profile.generation


def arm_identity(state: MachineState) -> int | None:
    """The arm a page shows and a spin binds to: its extension id, or None."""
    return None if state.arm.state is ArmState.NONE else state.arm.arm_id


def open_door(state: MachineState) -> str | None:
    """A door known to be open; unknown doors (no baseline yet) are not open."""
    if state.doors is None:
        return None
    return next((name for name, is_open in sorted(state.doors.items()) if is_open), None)


def next_bet(state: MachineState, name: Act) -> int:
    """The chosen bet after Bet one (up by one, back to 1 after the maximum) or Max bet."""
    top = state.profile.max_bet_per_line
    if name is Act.MAX_BET:
        return top
    if name is Act.BET_ONE:
        return chosen_bet(state) % top + 1
    raise ValueError(f"{name.value} does not select a bet")


def with_result(state: MachineState, result: ActionResult) -> MachineState:
    """state with result in its ring, replacing the entry with the same submission ID."""
    results = list(state.results)
    at = next((i for i, r in enumerate(results) if r.submission_id == result.submission_id), None)
    if at is None:
        results.insert(0, result)
    else:
        results[at] = result
    return replace(state, results=tuple(results[:RESULTS_LIMIT]))


def admit(state: MachineState, action: Action) -> Refusal | None:
    """None when the action may go now, otherwise why not."""
    refused = _refusal(state, action)
    return None if refused is None else Refusal(refused[0], refused[1], action.submission_id)


def _refusal(state: MachineState, action: Action) -> tuple[Code, str] | None:
    if state.link is not Link.ONLINE:
        return Code.OFFLINE, "Offline"
    if not state.fresh:
        return Code.STALE, "Waiting for the machine"
    family = FAMILIES.get(action.name)
    if family in state.checking:
        return Code.CHECKING, "Checking what happened"
    return _RULES[action.name](state, action)


def _bet_selection(state: MachineState, action: Action) -> tuple[Code, str] | None:
    # Selecting a bet sends nothing and never depends on credits, so a smaller affordable
    # bet stays reachable.
    return _play_gate(state, se.Capability.SPIN, mutation=False)


def _spin(state: MachineState, action: Action) -> tuple[Code, str] | None:
    return (_play_gate(state, se.Capability.SPIN) or _pending(state, action.name)
            or _out_of_date(state, action, generation=True, bet=True, arm=True)
            or _spin_arm(state) or _credits(state))


def _arm(state: MachineState, action: Action) -> tuple[Code, str] | None:
    return (_play_gate(state, se.Capability.OUTCOME) or _pending(state, action.name)
            or _out_of_date(state, action, generation=True, bet=True)
            or _nothing_armed(state) or _credits(state) or _outcome(state, action))


def _cancel_arm(state: MachineState, action: Action) -> tuple[Code, str] | None:
    refused = _extension(state, se.Capability.OUTCOME) or _pending(state, action.name)
    if refused:
        return refused
    if state.arm.state is ArmState.NONE:
        return Code.NOTHING_SET, "No next result is set"
    return _out_of_date(state, action, arm=True)


def _profile_change(state: MachineState, action: Action) -> tuple[Code, str] | None:
    if (refused := _extension(state, se.Capability.PROFILE)):
        return refused
    if (refused := _busy(state)):
        return refused
    return (_pending(state, action.name) or _nothing_armed(state)
            or _out_of_date(state, action, generation=action.name is Act.APPLY_PROFILE))


def _add_credits(state: MachineState, action: Action) -> tuple[Code, str] | None:
    if (refused := _transfers(state, to_machine=True)):
        return refused
    if "amount" not in action.params:
        return None
    amount = action.params["amount"]
    if type(amount) is not int or amount < 1:
        return Code.BAD_AMOUNT, "Enter a whole number of credits"
    limit = state.aft.limit_credits
    if amount > limit:
        return Code.OVER_LIMIT, (f"{amount:,} is more than this machine takes at once: up to "
                                 f"{limit:,} credits per top-up. Nothing was sent")
    return None


def _cash_out(state: MachineState, action: Action) -> tuple[Code, str] | None:
    if (refused := _busy(state)):
        return refused
    if (refused := _transfers(state, to_machine=False)):
        return refused
    if not state.credits:
        return Code.NO_CREDITS, "No credits"
    return None


def _setting(state: MachineState, action: Action) -> tuple[Code, str] | None:
    return None


_RULES = MappingProxyType({
    Act.BET_ONE: _bet_selection, Act.MAX_BET: _bet_selection, Act.SPIN: _spin, Act.ARM: _arm,
    Act.CANCEL_ARM: _cancel_arm, Act.APPLY_PROFILE: _profile_change,
    Act.RESTORE_STOCK: _profile_change, Act.ADD_CREDITS: _add_credits, Act.CASH_OUT: _cash_out,
    Act.PLAY: _setting, Act.SOUND: _setting, Act.BILLS: _setting, Act.CLOCK_SYNC: _setting,
})


def _extension(state: MachineState, capability: se.Capability,
               mutation: bool = True) -> tuple[Code, str] | None:
    info = state.ext_info
    if state.profile is None or info is None or not info.capabilities & capability:
        return Code.UNSUPPORTED, "Not available on this machine"
    if mutation and info.next_id == 0:
        return Code.UNSUPPORTED, "The extension takes no commands"
    return None


def _play_gate(state: MachineState, capability: se.Capability,
               mutation: bool = True) -> tuple[Code, str] | None:
    if (refused := _extension(state, capability, mutation)):
        return refused
    if not state.play_enabled:
        return Code.PAUSED, "Paused"
    if open_door(state) is not None:
        return Code.DOOR_OPEN, "Door open"
    return _busy(state)


def _busy(state: MachineState) -> tuple[Code, str] | None:
    """An action of this page, or a game started at the machine, is still running."""
    if state.in_progress is not None or state.game_running:
        return Code.BUSY, "After the spin"
    return None


def _pending(state: MachineState, name: Act) -> tuple[Code, str] | None:
    """An ACCEPTED extension action blocks new mutations, except the firmware's handoff: a
    pending arm can be cancelled, and taken by the spin bound to it when it is the arm this
    page journaled (firmware/src/core.c)."""
    status = state.ext_status
    if status is None or status.last_status != se.Status.ACCEPTED:
        return None
    if status.last_op == se.Op.ARM_OUTCOME:
        correlated = state.arm.state is ArmState.ARMED and state.arm.arm_id == status.last_id
        if name is Act.CANCEL_ARM or (name is Act.SPIN and correlated):
            return None
        if name is Act.SPIN:
            return Code.ARM_UNSETTLED, "Cancel the next result first"
        return Code.ARM_SET, "Cancel the next result first"
    if status.last_op == se.Op.BET_SPIN:
        return Code.BUSY, "After the spin"
    return Code.PENDING, "Waiting for the machine"


def _out_of_date(state: MachineState, action: Action, *, generation: bool = False,
                 bet: bool = False, arm: bool = False) -> tuple[Code, str] | None:
    if ((generation and action.profile_generation != state.profile.generation)
            or (bet and action.bet_per_line != chosen_bet(state))
            or (arm and action.arm_id != arm_identity(state))):
        return Code.OUT_OF_DATE, "This page is out of date"
    return None


def _spin_arm(state: MachineState) -> tuple[Code, str] | None:
    arm = state.arm
    if arm.state in (ArmState.HELD, ArmState.UNKNOWN):
        return Code.ARM_UNSETTLED, "Cancel the next result first"
    if arm.state is ArmState.ARMED and arm.bet_per_line != chosen_bet(state):
        return Code.ARM_BET, f"The next result is set for a bet of {arm.bet_per_line}"
    return None


def _nothing_armed(state: MachineState) -> tuple[Code, str] | None:
    if state.arm.state is not ArmState.NONE:
        return Code.ARM_SET, "Cancel the next result first"
    return None


def _credits(state: MachineState) -> tuple[Code, str] | None:
    total = total_wager(state)
    if not state.credits:
        return Code.NO_CREDITS, "No credits"
    if state.credits < total:
        return Code.CREDITS, f"Not enough credits for this bet ({total:,} credits)"
    return None


def _outcome(state: MachineState, action: Action) -> tuple[Code, str] | None:
    if "outcome" not in action.params:
        return None
    wanted = find_outcome(catalogue(state), str(action.params["outcome"]))
    if wanted is None or not wanted.available:
        return Code.OUTCOME_UNAVAILABLE, "Not possible at this bet"
    return None


def _transfers(state: MachineState, *, to_machine: bool) -> tuple[Code, str] | None:
    aft = state.aft
    if aft is not None and not aft.locked and (aft.to_machine if to_machine else aft.to_host):
        return None
    door = open_door(state)
    if door is not None:
        return Code.DOOR_OPEN, f"{door} open"
    if to_machine:
        return Code.AFT_UNAVAILABLE, "The machine is not taking credits now"
    return Code.AFT_UNAVAILABLE, "The machine is not cashing out now"


def public_view(state: MachineState) -> dict:
    """The bounded JSON-ready projection GET /state.json serves."""
    spin = admit(state, Action(Act.SPIN, "", profile_generation=profile_generation(state),
                               bet_per_line=chosen_bet(state), arm_id=arm_identity(state)))
    top_up = admit(state, Action(Act.ADD_CREDITS, ""))
    return {
        "link": state.link.value,
        "fresh": state.fresh,
        "last_reply_time": state.last_reply_time,
        "credits": state.credits,
        "cents_per_credit": state.cents_per_credit,
        "meters": None if state.meters is None else asdict(state.meters),
        "bet_per_line": chosen_bet(state),
        "lines": None if state.profile is None else state.profile.lines,
        "total_wager": total_wager(state),
        "profile_generation": profile_generation(state),
        "arm": {"state": state.arm.state.value, "arm_id": arm_identity(state),
                "bet_per_line": state.arm.bet_per_line},
        "spin": _admission(spin),
        "top_up": {"available": state.aft is not None and state.aft.to_machine,
                   "limit_credits": None if state.aft is None else state.aft.limit_credits,
                   **_admission(top_up)},
        "checking": {family.value: {"reason": hold.reason, "intent_seq": hold.intent_seq}
                     for family, hold in state.checking.items()},
        "extension_fault": extension_fault(state),
        "results": [_result(r) for r in state.results[:RESULTS_LIMIT]],
    }


def extension_fault(state: MachineState) -> bool | None:
    """The fault flag of the extension's last STATUS; None before STATUS was read. A pending
    restore can stay ACCEPTED with it set: it is shown, never resolved."""
    return None if state.ext_status is None else state.ext_status.fault


def _admission(refusal: Refusal | None) -> dict:
    if refusal is None:
        return {"allowed": True, "code": None, "reason": None}
    return {"allowed": False, "code": refusal.code.value, "reason": refusal.reason}


def _result(result: ActionResult) -> dict:
    evidence = result.evidence
    return {
        "submission_id": result.submission_id,
        "action": result.action.value,
        "state": result.state.value,
        "outcome": None if result.outcome is None else result.outcome.value,
        "reason": result.reason,
        "code": None if result.code is None else result.code.value,
        "credits_delta": result.credits_delta,
        "in_house_in_cents_delta": result.in_house_in_cents_delta,
        "in_house_out_cents_delta": result.in_house_out_cents_delta,
        "evidence": None if evidence is None else asdict(evidence),
        "machine_status": None if result.machine_status is None else result.machine_status.name,
    }


class Kind(enum.Enum):
    WIN = "win"
    NEAR_MISS = "near-miss"
    LOSS = "loss"


_CODES = {name: code for code, name in sp.SYMBOL_NAMES.items()}
# The design's named near-misses and losses, as centre-line symbols in reel order.
_NAMED = (
    (Kind.NEAR_MISS, ("Blazing 7", "Blazing 7", "Blank")),
    (Kind.NEAR_MISS, ("Blazing 7", "Blazing 7", "Seven")),
    (Kind.NEAR_MISS, ("Triple bar", "Triple bar", "Blank")),
    (Kind.LOSS, ("Blank", "Bar", "Blank")),
    (Kind.LOSS, ("Seven", "Blank", "Double bar")),
)


def _pattern_id(names: tuple[str, ...]) -> str:
    return "-".join(names).lower().replace(" ", "-")  # "blazing-7-blazing-7-blank"


# build_catalogue's (key, codes) patterns: the same tuple every call, so its cache hits.
PATTERNS = tuple((_pattern_id(names), tuple(_CODES[name] for name in names)) for _, names in _NAMED)
PATTERN_KINDS = MappingProxyType({_pattern_id(names): kind for kind, names in _NAMED})


@dataclass(frozen=True)
class Outcome:
    """One next result a person can pick, at the chosen bet.

    id is the catalogue key: a paytable record index, or a PATTERNS id. symbols are the
    symbols the chosen stops land on the keyed line (the record's winning line, a pattern's
    centre line), or the entry's own codes when it is unavailable. total_award is the whole
    spin's nominal total at these stops, other lines included.
    """
    id: int | str
    name: str
    kind: Kind
    symbols: tuple[int, ...]
    stops: tuple[int, ...] | None
    total_award: int | None
    total_bet: int
    available: bool


def catalogue(state: MachineState) -> list[Outcome]:
    """Every paytable record that pays at the chosen bet, then the named near-misses and
    losses; an entry no stops land at this bet and profile is unavailable."""
    bet = chosen_bet(state)
    if bet is None:
        return []
    profile = state.profile.profile
    choices = sp.build_catalogue(profile, bet, PATTERNS)
    game = sp.installed_game()
    total_bet = bet * profile.lines
    outcomes = []
    for index, payout in enumerate(profile.payouts):
        if payout.award and payout.min_bet <= bet <= payout.max_bet:
            choice = choices.get(index)
            line = None if choice is None else next(
                at for at, (record, award) in enumerate(choice.score.lines) if record == index and award)
            outcomes.append(_outcome_entry(index, payout.symbols, Kind.WIN, choice, line, game, total_bet))
    for key, pattern in PATTERNS:
        outcomes.append(_outcome_entry(key, pattern, PATTERN_KINDS[key], choices.get(key), 0, game,
                                       total_bet))
    return outcomes


def find_outcome(outcomes: list[Outcome], outcome_id: str) -> Outcome | None:
    """The entry whose id, as text (a form field), is outcome_id."""
    return next((o for o in outcomes if str(o.id) == outcome_id), None)


def _outcome_entry(key: int | str, wanted: tuple[int, ...], kind: Kind, choice: sp.StopChoice | None,
                   line: int | None, game: sp.Game, total_bet: int) -> Outcome:
    if choice is None:
        return Outcome(key, outcome_name(wanted, wanted, kind), kind, wanted, None, None, total_bet, False)
    landed = sp.line_symbols(game, choice.stops, game.line_patterns[line])
    return Outcome(key, outcome_name(wanted, landed, kind), kind, landed, choice.stops,
                   choice.score.total_award, total_bet, True)


def outcome_name(wanted: tuple[int, ...], landed: tuple[int, ...], kind: Kind) -> str:
    """'3 Blazing 7s', 'Any 3 sevens (mixed)', or the symbols in reel order."""
    names = sp.SYMBOL_NAMES
    if kind is Kind.WIN and len(set(wanted)) == 1:
        symbol = wanted[0]
        if not symbol & sp.GROUP_SYMBOL:
            return f"{len(wanted)} {names[symbol]}s"
        group = names[symbol].split(" ", 1)[1]  # "Any seven" -> "seven"
        mixed = " (mixed)" if len(set(landed)) > 1 else ""
        return f"Any {len(wanted)} {group}s{mixed}"
    return " · ".join(names[symbol] for symbol in wanted)
