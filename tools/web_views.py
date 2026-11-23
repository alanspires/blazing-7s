"""Server-rendered pages of the web control page, built from one MachineState snapshot.

Pure functions, no I/O. Pages reuse the approved design's markup, class names and
`data-part` names (.plans/web-control-page.design/approved/machine-control/): Play, Odds &
payouts, Activity & meters and Settings. Every element that shows machine state carries a
stable `data-region` id. `regions()` renders those elements alone, for the SSE stream and
GET /regions. static/live.js swaps them in place and leaves focused and edited fields alone.

The view (`View`) is URL state: the open sheet, a hidden side panel, an Add credits amount,
a dismissed result, an Activity page and an odds editor draft. A region therefore renders
for one view, so two people looking at different sheets each get their own markup.

Controls follow `web_state.admit`. A refused control is a real disabled button, and its
second line names the refusal by code (SHORT). Forms POST to /actions/<Act value> and carry,
as hidden fields, the expected profile generation, bet per line and arm that admit compares
(EXPECTED), plus `return_to`. `action_from_form` turns a posted form back into an Action, and
`redirect_after` says where a form post goes next, so the views own every form they render.

Values a machine or a person supplies are escaped with html.escape; markup is only ever
built from this module's own literals.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from functools import lru_cache
from types import MappingProxyType
from urllib.parse import parse_qs, urlencode, urlsplit

import sas_profile as sp
import web_state as ws
from web_state import (
    Act,
    ArmState,
    Code,
    Family,
    Kind,
    Link,
    OutcomeWord,
    ResultState,
    Setting,
)

PAGE_PATHS = MappingProxyType({"/": "play", "/odds": "odds", "/activity": "activity",
                               "/settings": "settings"})
PATHS = MappingProxyType({page: path for path, page in PAGE_PATHS.items()})
SHEETS = frozenset(("next-result", "add-credits", "details"))
ACTIVITY_PAGE = 50  # entries per Activity page; pass one more to learn that earlier ones exist
RAIL_ENTRIES = 5  # recent entries in the Meters & activity panel
AMOUNT_CHIPS = (500, 1000, 5000)  # quick amounts below the machine's limit, which is the last chip
DEFAULT_AMOUNT = 1000
QUICK_ADD = 5000  # the No credits banner's one-tap top-up, capped at the machine's limit
DIFF_LIST = 4  # odds edits named in the notice before "and N more"
STATIC = "/static"

# Hidden form fields.
F_GENERATION = "profile_generation"
F_BET = "bet_per_line"
F_ARM = "arm_id"
F_RETURN = "return_to"
F_AMOUNT = "amount"
F_OUTCOME = "outcome"
F_ON = "on"
F_LINES = "lines"
F_MAX_BET = "max_bet"

# The expected values each action carries because admit compares them against fresh state.
EXPECTED = MappingProxyType({
    Act.SPIN: (F_GENERATION, F_BET, F_ARM),
    Act.ARM: (F_GENERATION, F_BET),
    Act.CANCEL_ARM: (F_ARM,),
    Act.APPLY_PROFILE: (F_GENERATION,),
})

# A refused control's second line, by refusal code. Code.CREDITS adds the total wager.
SHORT = MappingProxyType({
    Code.OFFLINE: "Offline",
    Code.STALE: "Waiting",
    Code.CHECKING: "Paused",
    Code.UNSUPPORTED: "Not available",
    Code.PAUSED: "Paused",
    Code.DOOR_OPEN: "Door open",
    Code.BUSY: "After the spin",
    Code.PENDING: "Waiting",
    Code.OUT_OF_DATE: "Out of date",
    Code.NO_CREDITS: "No credits",
    Code.CREDITS: "Needs",
    Code.ARM_SET: "Next result set",
    Code.ARM_BET: "Next result set",
    Code.ARM_UNSETTLED: "Cancel next result",
    Code.NOTHING_SET: "Nothing set",
    Code.OUTCOME_UNAVAILABLE: "Not possible at this bet",
    Code.AFT_UNAVAILABLE: "Not now",
    Code.OVER_LIMIT: "Over the limit",
    Code.BAD_AMOUNT: "Whole credits only",
    Code.JOURNAL: "Not saved",
    Code.INVALID_PROFILE: "Not valid",
})

ACTION_LABELS = MappingProxyType({
    Act.BET_ONE: "Bet one", Act.MAX_BET: "Max bet", Act.SPIN: "Spin", Act.ARM: "Set next result",
    Act.CANCEL_ARM: "Cancel next result", Act.ADD_CREDITS: "Add credits", Act.CASH_OUT: "Cash out",
    Act.APPLY_PROFILE: "Apply to machine", Act.RESTORE_STOCK: "Restore stock game",
    Act.PLAY: "Play on or off", Act.SOUND: "Sound", Act.BILLS: "Bill acceptor",
    Act.CLOCK_SYNC: "Set the clock",
})
FAMILY_LABELS = MappingProxyType({
    Family.MONEY: "Adding credits and cashing out",
    Family.EXTENSION: "Spins, next results and odds changes",
    Family.PLAY: "Turning play on or off",
})

# Symbol codes to the design's symbol classes; groups draw as one of their members.
SYMBOL_CLASSES = MappingProxyType({0x18: "blank", 0x0A: "bar1", 0x0B: "bar2", 0x0C: "bar3",
                                   0x0D: "seven", 0x0E: "b7", 0x80: "seven", 0x81: "bar1"})
NBSP = "&nbsp;"
SEP = "&nbsp;· "  # the design's separator
WITH_DOT = ' class="with-dot"'

GAME_OVER = frozenset((OutcomeWord.PLAYED, OutcomeWord.HAPPENED))  # a page spin whose game ended
CHECKED = frozenset((OutcomeWord.HAPPENED, OutcomeWord.NOTHING_CHANGED))  # settled after a lost reply
SAID_NO = frozenset((OutcomeWord.REJECTED, OutcomeWord.REFUSED, OutcomeWord.NACKED))
SPIN_STOPPED = frozenset((OutcomeWord.INTERRUPTED, OutcomeWord.FAULT))
NOT_APPLIED = frozenset((OutcomeWord.ABORTED, OutcomeWord.STAGING_ABANDONED, OutcomeWord.REFUSED))


class FormError(ValueError):
    """A posted form that cannot become an action. code is the refusal to answer with, or
    None for a malformed request; either way nothing was sent."""

    def __init__(self, reason: str, code: Code | None = None):
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class View:
    """What one browser looks at, from its URL."""
    page: str = "play"
    sheet: str | None = None  # Play: next-result, add-credits or details
    rail_hidden: bool = False  # Play: the Meters & activity side panel folded away
    amount: str | None = None  # Add credits: the amount entered, as typed
    seen: str | None = None  # the submission ID of a result banner someone dismissed
    before: int | None = None  # Activity: list entries older than this journal seq
    draft: Mapping[str, str] | None = None  # Odds: editor fields of an unapplied draft


@dataclass(frozen=True)
class Draft:
    """An odds editor draft over the active profile: profile is None when a field is not a
    whole number or the profile is one the machine would refuse (problem says why)."""
    fields: Mapping[str, str]
    profile: sp.Profile | None
    problem: str | None


# ---------- Requests: views, forms, redirects ----------

def view_for(page: str | None, query: str) -> View:
    """The view a request asks for. page comes from the path (PAGE_PATHS), or, for /events
    and /regions, from the query's page parameter. ValueError for an unknown page."""
    values = {key: items[-1] for key, items in parse_qs(query, keep_blank_values=True).items()}
    page = page or values.get("page")
    if page not in PATHS:
        raise ValueError(f"unknown page {page!r}")
    sheet = values.get("sheet") if page == "play" else None
    before = values.get("before", "")
    draft = {key: value for key, value in values.items() if _editor_field(key)} if page == "odds" else {}
    return View(page=page, sheet=sheet if sheet in SHEETS else None,
                rail_hidden=page == "play" and values.get("rail") == "hidden",
                amount=values.get(F_AMOUNT) if sheet == "add-credits" else None,
                seen=values.get("seen") or None,
                before=int(before) if page == "activity" and before.isdigit() else None,
                draft=MappingProxyType(draft) if draft else None)


def view_query(view: View) -> str:
    """The query naming view for /events and /regions (static/live.js appends it)."""
    return urlencode({"page": view.page, **_query(view)})


def view_url(view: View) -> str:
    query = urlencode(_query(view))
    return PATHS[view.page] + (f"?{query}" if query else "")


def action_from_form(route: str, submission_id: str, fields: Mapping[str, str],
                     state: ws.MachineState) -> ws.Action:
    """The Action a form posted to /actions/<route> asks for, with the expected values it
    carries. FormError when the route is unknown or a field cannot mean anything."""
    try:
        name = Act(route)
    except ValueError:
        raise FormError(f"unknown action {route!r}") from None
    params: dict[str, object] = {}
    if name is Act.ADD_CREDITS:
        params[F_AMOUNT] = _amount(fields.get(F_AMOUNT, ""))
    elif name is Act.ARM:
        params[F_OUTCOME] = fields.get(F_OUTCOME, "")
    elif name in (Act.PLAY, Act.SOUND, Act.BILLS):
        on = fields.get(F_ON)
        if on not in ("0", "1"):
            raise FormError(f"{F_ON} must be 0 or 1")
        params[F_ON] = on == "1"
    elif name is Act.APPLY_PROFILE:
        draft = parse_draft(state, fields)
        if draft.profile is None:
            raise FormError(draft.problem, Code.INVALID_PROFILE)
        params["profile"] = draft.profile
    return ws.Action(name, submission_id, MappingProxyType(params),
                     profile_generation=_optional_int(fields.get(F_GENERATION)),
                     bet_per_line=_optional_int(fields.get(F_BET)),
                     arm_id=_optional_int(fields.get(F_ARM)))


def redirect_after(return_to: str | None, action: ws.Action, refusal: ws.Refusal | None) -> str:
    """Where a form post answers 303 to: back to the page it came from, or, for a refused
    top-up, to the Add credits sheet showing why with the amount entered."""
    target = return_to if _safe_path(return_to) else "/"
    if refusal is not None and action.name is Act.ADD_CREDITS:
        parts = urlsplit(target)
        view = view_for(PAGE_PATHS.get(parts.path, "play"), parts.query)
        amount = action.params.get(F_AMOUNT)
        return view_url(View(page="play", sheet="add-credits", rail_hidden=view.rail_hidden,
                             amount=f"{amount}" if amount is not None else None))
    return target


def parse_draft(state: ws.MachineState, fields: Mapping[str, str]) -> Draft:
    """The editor's fields over the active profile, validated against the machine's caps.
    Fields the form leaves out keep the active profile's values."""
    fields = MappingProxyType({key: value for key, value in fields.items() if _editor_field(key)})
    base = None if state.profile is None else state.profile.profile
    if base is None:
        return Draft(fields, None, "The machine's game is not known yet")
    try:
        weights = tuple(tuple(_field_int(fields, f"w-{stop}-{reel}", row[reel],
                                         f"Reel {reel + 1} position {stop + 1} weight")
                              for reel in range(sp.REELS))
                        for stop, row in enumerate(base.weights))
        payouts = tuple(_replace_award(payout, _field_int(fields, f"award-{index}", payout.award,
                                                          f"Award {index + 1}"))
                        for index, payout in enumerate(base.payouts))
        profile = sp.Profile(_field_int(fields, F_LINES, base.lines, "Paylines"),
                             _field_int(fields, F_MAX_BET, base.max_bet_per_line, "Max bet per line"),
                             weights, payouts)
        caps = {} if state.ext_info is None else state.ext_info.caps()
        sp.validate(profile, **caps)
    except (ValueError, sp.ProfileError) as error:
        return Draft(fields, None, str(error))
    return Draft(fields, profile, None)


def preview_regions(state: ws.MachineState, fields: Mapping[str, str]) -> dict[str, str]:
    """The odds editor regions a draft changes, for POST /actions/preview."""
    ctx = _Ctx(state, View(page="odds", draft=MappingProxyType(dict(fields))), (), _EPOCH)
    return {"odds-actions": _odds_actions(ctx), "odds-status": _odds_status(ctx),
            "preview": _preview_panel(ctx)}


# ---------- Pages and regions ----------

def page(state: ws.MachineState, view: View, *, now: datetime,
         entries: Sequence[Mapping[str, object]] | None = None) -> str:
    """The whole page. entries: Activity's journal records, newest first, from
    journal.entries(before=view.before, limit=ACTIVITY_PAGE + 1, kinds=ws.ACTIVITY_KINDS);
    without them Activity lists state.log. now is the server's aware current time."""
    ctx = _Ctx(state, view, state.log if entries is None else tuple(entries), now)
    parts = _region_builders(ctx)
    built = {name: build() for name, build in parts.items()}
    main = _MAINS[view.page](ctx, built)
    title = {"play": "Play", "odds": "Odds &amp; payouts", "activity": "Activity",
             "settings": "Settings"}[view.page]
    body_class = ' class="page-play"' if view.page == "play" else ""
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<title>{title} · Blazing 7s</title>\n'
        f'<link rel="stylesheet" href="{STATIC}/tokens.css">\n'
        f'<link rel="stylesheet" href="{STATIC}/styles.css">\n'
        f'<script src="{STATIC}/live.js" defer></script>\n'
        '</head>\n'
        f'<body{body_class} data-view="{_e(view_query(view))}">\n'
        f'{_topbar(ctx, built["status"])}\n{main}\n'
        '<p class="flash" role="status" data-flash hidden></p>\n'
        f'{_unreachable_template()}\n'
        '</body>\n</html>\n')


def regions(state: ws.MachineState, view: View, *, now: datetime,
            entries: Sequence[Mapping[str, object]] | None = None) -> dict[str, str]:
    """Each machine-state element of the page, by its data-region id."""
    ctx = _Ctx(state, view, state.log if entries is None else tuple(entries), now)
    return {name: build() for name, build in _region_builders(ctx).items()}


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class _Ctx:
    """One render: the snapshot, the view and what several regions derive from them."""

    def __init__(self, state: ws.MachineState, view: View, entries: Sequence[Mapping[str, object]],
                 now: datetime):
        self.state, self.view, self.entries, self.now = state, view, entries, now
        self._admitted: dict[tuple, ws.Refusal | None] = {}
        self._catalogue: list[ws.Outcome] | None = None
        self._draft: Draft | None = None

    def admit(self, name: Act, **params: object) -> ws.Refusal | None:
        key = (name, *sorted(params.items()))
        if key not in self._admitted:
            state = self.state
            self._admitted[key] = ws.admit(state, ws.Action(
                name, "", MappingProxyType(params), profile_generation=ws.profile_generation(state),
                bet_per_line=ws.chosen_bet(state), arm_id=ws.arm_identity(state)))
        return self._admitted[key]

    def catalogue(self) -> list[ws.Outcome]:
        if self._catalogue is None:
            self._catalogue = ws.catalogue(self.state)
        return self._catalogue

    def draft(self) -> Draft | None:
        if self.view.draft is None:
            return None
        if self._draft is None:
            self._draft = parse_draft(self.state, self.view.draft)
        return self._draft

    def form(self, act: Act, form_id: str, **extra: object) -> str:
        """A hidden form for act's buttons (form=form_id), with its expected values."""
        state = self.state
        expected = {F_GENERATION: ws.profile_generation(state), F_BET: ws.chosen_bet(state),
                    F_ARM: ws.arm_identity(state)}
        values = {name: expected[name] for name in EXPECTED.get(act, ())}
        values.update(extra)
        values[F_RETURN] = view_url(View(page=self.view.page, sheet=self.view.sheet,
                                         rail_hidden=self.view.rail_hidden))
        inputs = "".join(f'<input type="hidden" name="{_e(name)}" value="{_e("" if value is None else value)}">'
                         for name, value in values.items())
        return f'<form id="{_e(form_id)}" method="post" action="/actions/{act.value}" hidden>{inputs}</form>'


def _region_builders(ctx: _Ctx) -> dict[str, Callable[[], str]]:
    view = ctx.view
    builders: dict[str, Callable[[], str]] = {"status": lambda: _status(ctx)}
    if view.page == "play":
        builders.update({"game": lambda: _game(ctx), "banner": lambda: _banner(ctx),
                         "reels": lambda: _reels(ctx), "last-spin": lambda: _last_spin(ctx),
                         "drawer-pull": lambda: _drawer_pull(ctx), "meters": lambda: _meters(ctx),
                         "deck": lambda: _deck(ctx), "note": lambda: _note(ctx, "mirror__note")})
        if view.sheet == "next-result":
            builders["next-result"] = lambda: _next_result(ctx)
        if view.sheet == "add-credits":
            builders["add-credits"] = lambda: _add_credits(ctx)
        if not view.rail_hidden:
            builders["rail-note"] = lambda: _note(ctx, "rail__note", "rail-note")
            builders["rail-meters"] = lambda: _rail_meters(ctx)
            builders["rail-activity"] = lambda: _rail_activity(ctx)
    elif view.page == "odds":
        builders.update({"odds-head": lambda: _odds_head(ctx), "odds-actions": lambda: _odds_actions(ctx),
                         "odds-status": lambda: _odds_status(ctx), "preview": lambda: _preview_panel(ctx),
                         "editor": lambda: _editor(ctx)})
    elif view.page == "activity":
        builders.update({"log": lambda: _log(ctx), "meters": lambda: _meter_tiles(ctx)})
    else:
        builders.update({"settings": lambda: _settings(ctx), "about": lambda: _about(ctx)})
    return builders


# ---------- Shared pieces ----------

def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _n(value: int) -> str:
    return f"{value:,}"


def _icon(name: str, cls: str = "icon", alt: str = "") -> str:
    return f'<img class="{cls}" src="{STATIC}/icons/{name}.svg" alt="{_e(alt)}">'


def _plural(count: int, word: str, plural: str | None = None) -> str:
    return f"{_n(count)}{NBSP}{word if count == 1 else plural or word + 's'}"


def _dollars(credits: int, cents_per_credit: int | None, *, cents: bool = True) -> str:
    """credits as money, '$5,000.00' (cents) or '$5,000'; empty while the denomination is unknown."""
    if cents_per_credit is None:
        return ""
    total = credits * cents_per_credit
    whole, part = divmod(total, 100)
    return f"${whole:,}.{part:02d}" if cents or part else f"${whole:,}"


def _denomination(cents_per_credit: int | None) -> str:
    if cents_per_credit is None:
        return "denomination not read yet"
    return _dollars(1, cents_per_credit, cents=False) if cents_per_credit >= 100 else f"{cents_per_credit}¢"


def _parse_time(iso: str) -> datetime:
    if iso.endswith("Z"):  # fromisoformat accepts the Z suffix only from Python 3.11
        iso = iso[:-1] + "+00:00"
    return datetime.fromisoformat(iso)


def _local(moment: datetime) -> datetime:
    return moment.astimezone()


def _clock(iso: str | None) -> str:
    if not iso:
        return "an unknown time"
    moment = _local(_parse_time(iso))
    return f"{moment.hour % 12 or 12}:{moment.minute:02d}{NBSP}{'AM' if moment.hour < 12 else 'PM'}"


def _game_name(state: ws.MachineState) -> str:
    if state.profile is None:
        return "Game not read yet"
    if not state.profile.custom:
        return "Stock game"
    return f"Custom profile (generation {state.profile.generation})"


def _symbol(code: int, size: str) -> str:
    cls = SYMBOL_CLASSES.get(code, "blank")
    inner = ""
    if cls in ("b7", "seven"):
        inner = '<span class="sym__seven">7</span>'
    elif cls.startswith("bar"):
        inner = f'<span class="sym__bar">{"BAR" if size == "reel" else ""}</span>' * int(cls[3])
    name = sp.SYMBOL_NAMES.get(code, f"Symbol {code:#04x}")
    return f'<span class="sym sym--{cls} sym--{size}" role="img" aria-label="{_e(name)}">{inner}</span>'


def _symbols_text(codes: Sequence[int]) -> str:
    return SEP.join(_e(sp.SYMBOL_NAMES.get(code, f"{code:#04x}")).replace(" ", NBSP) for code in codes)


def _centre_symbols(stops: Sequence[int]) -> tuple[int, ...]:
    game = sp.installed_game()
    return sp.line_symbols(game, tuple(stops), game.line_patterns[sp.CENTER])


def _short(refusal: ws.Refusal, state: ws.MachineState) -> str:
    if refusal.code is Code.CREDITS:
        return f"{SHORT[Code.CREDITS]} {_n(ws.total_wager(state))}"
    return SHORT[refusal.code]


def _coins(bet: int) -> str:
    return _plural(bet, "coin")


def _wager_text(state: ws.MachineState) -> str:
    bet, total = ws.chosen_bet(state), ws.total_wager(state)
    money = _dollars(total, state.cents_per_credit, cents=False)
    money = f"{SEP}{money}" if money else ""
    if state.profile.lines == 1:
        return f"{_coins(bet)}{money}"
    return f"{_n(bet)}{NBSP}×{NBSP}{_n(state.profile.lines)}{NBSP}lines{money}"


def _url(page: str, **params: object) -> str:
    view = View(page=page, **params)
    return view_url(view)


def _query(view: View) -> dict[str, str]:
    query: dict[str, str] = {}
    if view.sheet:
        query["sheet"] = view.sheet
    if view.rail_hidden:
        query["rail"] = "hidden"
    if view.amount is not None:
        query[F_AMOUNT] = view.amount
    if view.seen:
        query["seen"] = view.seen
    if view.before is not None:
        query["before"] = str(view.before)
    if view.draft:
        query.update(view.draft)
    return query


def _safe_path(target: str | None) -> bool:
    """A same-site path: absolute, not protocol-relative, without a scheme or host."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc


def _editor_field(key: str) -> bool:
    return key in (F_LINES, F_MAX_BET) or bool(re.fullmatch(r"w-\d+-\d+|award-\d+", key))


def _amount(text: str) -> int | str:
    """A typed amount as whole credits, or the text as typed (admit refuses it as BAD_AMOUNT)."""
    digits = text.replace(",", "").replace(" ", "").strip()
    return int(digits) if digits.isdigit() else text


def _optional_int(text: str | None) -> int | None:
    return int(text) if text is not None and text.strip().isdigit() else None


def _field_int(fields: Mapping[str, str], name: str, default: int, what: str) -> int:
    if name not in fields:
        return default
    text = fields[name].replace(",", "").strip()
    if not text.isdigit():
        raise ValueError(f"{what}: enter a whole number")
    return int(text)


def _replace_award(payout: sp.Payout, award: int) -> sp.Payout:
    return sp.Payout(award, payout.symbols, payout.min_bet, payout.max_bet, payout.flags, payout.line_mask)


@lru_cache(maxsize=8)
def _preview_rows(encoding: bytes) -> tuple[sp.PreviewRow, ...]:
    return sp.preview(sp.decode(encoding))


def _preview(profile: sp.Profile) -> tuple[sp.PreviewRow, ...]:
    return _preview_rows(sp.encode(profile))


def _odds(profile: sp.Profile, bet: int, index: int) -> str:
    """How often paytable record index pays at bet with profile's weights, as '1 in 4,096';
    empty when it never does."""
    row = next((row for row in _preview(profile) if row.bet_per_line == bet), None)
    if row is None or not row.record_masses[index]:
        return ""
    return f"1{NBSP}in{NBSP}{_n(round(Fraction(row.outcome_mass, row.record_masses[index])))}"


def _percent(fraction: object, places: int) -> str:
    return f"{float(fraction) * 100:.{places}f}%"


# ---------- Top bar ----------

def _topbar(ctx: _Ctx, status: str) -> str:
    view, state = ctx.view, ctx.state
    links = []
    for page, part, icon, label in (("play", "nav-play", "gamepad-2", "Play"),
                                    ("odds", "nav-odds", "sliders-horizontal", "Odds &amp; payouts"),
                                    ("activity", "nav-activity", "history", "Activity"),
                                    ("settings", "nav-settings", "settings", "Settings")):
        current = ' is-current" aria-current="page' if page == view.page else ""
        links.append(f'<a class="nav__link{current}" href="{PATHS[page]}" data-part="{part}">'
                     f'{_icon(icon)}<span class="nav__label">{label}</span></a>')
    return (
        '<header class="topbar"><div class="topbar__inner">'
        '<a class="brand" href="/" data-part="brand-home"><span class="brand__name">Blazing 7s</span>'
        f'<span class="brand__sub">Bally ProSlot 6000{SEP}{_e(_denomination(state.cents_per_credit))}'
        f'{SEP}3 reels</span></a>'
        f'<nav class="nav" aria-label="Pages">{"".join(links)}</nav>{status}</div></header>')


def _status(ctx: _Ctx) -> str:
    state = ctx.state
    door = ws.open_door(state)
    if state.link is Link.OFFLINE:
        tone, dot, main = "danger", "danger", "Offline"
        since = _clock(state.last_reply_time)
        short, detail = f"· since {since}", f"· Last seen {since}"
    elif state.link is Link.STARTING:
        tone, dot, main, short, detail = "warn", "warn", "Connecting", "", "· reading the machine"
    elif not state.fresh:
        tone, dot, main, short, detail = "warn", "warn", "Catching up", "", "· reading the machine"
    elif state.checking:
        tone, dot = "warn", "warn"
        main = 'Checking<span class="status-pill__rest"> what happened</span>'
        short = detail = ""
    elif door is not None:
        tone, dot, main = "warn", "warn", "Door open"
        short, detail = "· Play stopped", f"· Online{SEP}{_e(door)} open"
    elif not state.play_enabled:
        tone, dot, main, short, detail = "warn", "warn", "Play off", "· Online", f"· Online{SEP}play off"
    else:
        tone, dot, main = "", "ok", "Online"
        doors = "doors not read yet" if state.doors is None else "doors closed"
        short, detail = "· Play enabled", f"· Play enabled{SEP}{doors}"
    cls = f" status-pill--{tone}" if tone else ""
    short_html = f'<span class="status-pill__short">{short}</span>' if short else ""
    detail_html = f'<span class="status-pill__detail">{detail}</span>' if detail else ""
    return (f'<p class="status-pill{cls}" id="machine-status" data-region="status">'
            f'<span class="dot dot--{dot}"></span><span class="status-pill__main">{main}</span>'
            f'{short_html}{detail_html}</p>')


def _unreachable_template() -> str:
    """Markup live.js shows while the page cannot reach its server."""
    return (
        '<template id="unreachable">'
        '<p class="status-pill status-pill--danger" id="machine-status" data-region="status">'
        '<span class="dot dot--danger"></span><span class="status-pill__main">No server</span>'
        '<span class="status-pill__detail">· this page lost its server</span></p>'
        '<div class="armed-tag armed-tag--danger armed-tag--wrap" id="play-status" data-region="banner" '
        'data-banner="unreachable" data-tone="danger">'
        f'{_icon("unplug")}<p class="armed-tag__text"><strong>Can\'t reach this page\'s server.</strong> '
        'Every control is off until it answers again. This page reconnects by itself.</p>'
        '<span class="armed-tag__cancel is-busy" aria-disabled="true">'
        f'{_icon("loader-circle", "icon icon--s icon--turning")}Reconnecting</span></div>'
        '</template>')


# ---------- Play ----------

def _play_main(ctx: _Ctx, built: Mapping[str, str]) -> str:
    view = ctx.view
    drawer_open = view.sheet == "next-result"
    window_cls = "reel-window is-drawer-open" if drawer_open else "reel-window"
    mirror = (
        '<section class="mirror" aria-label="The machine">'
        f'{built["game"]}'
        f'<div class="window-stack"><div class="{window_cls}" id="reel-window">'
        f'{built["banner"]}{built["reels"]}'
        f'<div class="reel-window__foot">{built["last-spin"]}{built["drawer-pull"]}</div></div>'
        f'{built.get("next-result", "")}</div>'
        '<div class="dock">'
        f'<a class="dock__handle" href="{_url("play", sheet="details", rail_hidden=view.rail_hidden)}" '
        f'data-part="details-open">{_icon("chevron-up", "icon icon--s")}Meters &amp; activity</a>'
        f'{built["meters"]}{built["deck"]}{built.get("add-credits", "")}</div>'
        f'{built["note"]}</section>')
    if view.rail_hidden:
        rail = (f'<a class="rail-tab" href="{_url("play", sheet=view.sheet)}" data-part="details-expand">'
                f'{_icon("panel-right-open")}<span class="rail-tab__label">Meters &amp; activity</span></a>')
    else:
        sheet = " sheet sheet--tall" if view.sheet == "details" else ""
        rail = (
            f'<aside class="rail{sheet}" id="details" aria-labelledby="details-title">'
            '<div class="rail__head"><h2 class="rail__title" id="details-title">Meters &amp; activity</h2>'
            f'<a class="icon-btn icon-btn--quiet rail__collapse" href="{_url("play", rail_hidden=True)}" '
            f'data-part="details-collapse">{_icon("panel-right-close", alt="Hide meters and activity")}</a>'
            f'<a class="icon-btn rail__close" href="{_url("play")}" data-part="details-close">'
            f'{_icon("x", alt="Close")}</a></div>'
            f'{built["rail-note"]}{built["rail-meters"]}{built["rail-activity"]}</aside>')
    scrim = ""
    if view.sheet:
        part = {"next-result": "next-result-scrim", "add-credits": "add-credits-scrim",
                "details": "details-scrim"}[view.sheet]
        scrim = (f'<a class="scrim" href="{_url("play", rail_hidden=view.rail_hidden)}" data-part="{part}" '
                 'aria-label="Close"></a>')
    play_cls = "play play--collapsed" if view.rail_hidden else "play"
    return f'<main class="main main--play"><div class="{play_cls}">{mirror}{rail}</div>{scrim}</main>'


def _game(ctx: _Ctx) -> str:
    state = ctx.state
    profile = state.profile
    game = _e(_game_name(state))
    spec = ""
    if profile is not None:
        if profile.custom:
            top = _preview(profile.profile)[-1]
            game += f"{SEP}return {_percent(top.nominal_return, 1)} at {_coins(top.bet_per_line)}"
        money = (f"{_dollars(1, state.cents_per_credit, cents=False)} per credit{SEP}"
                 if state.cents_per_credit else "")
        spec = (f'<p class="mirror__spec">{money}{_plural(profile.lines, "line")}{SEP}'
                f'Bet 1–{_n(profile.max_bet_per_line)} coins</p>')
    return (f'<div class="mirror__head" data-region="game"><p class="mirror__game">{game}</p>{spec}</div>')


def _note(ctx: _Ctx, cls: str, region: str = "note") -> str:
    state = ctx.state
    if state.profile is None:
        text = "This machine can only cash out the whole balance."
    else:
        per_line = " per line" if state.profile.lines > 1 else ""
        lines = f" on {_n(state.profile.lines)} lines" if state.profile.lines > 1 else ""
        coin = (f" Each coin is one credit ({_dollars(1, state.cents_per_credit, cents=False)})."
                if state.cents_per_credit else "")
        text = (f"Bet 1 to {_n(state.profile.max_bet_per_line)} coins{per_line}{lines}.{coin} "
                "This machine can only cash out the whole balance.")
    return f'<p class="{cls}" data-region="{region}">{text}</p>'


def _banner(ctx: _Ctx) -> str:
    """The one spot above the reels that says what is going on, most urgent first."""
    for build in (_banner_link, _banner_checking, _banner_door, _banner_paused, _banner_running,
                  _banner_arm, _banner_result, _banner_no_credits):
        banner = build(ctx)
        if banner is not None:
            return banner
    return '<div id="play-status" data-region="banner" data-banner="none" hidden></div>'


def _tag(kind: str, tone: str, icon: str, text: str, action: str = "", wrap: bool = False) -> str:
    cls = "armed-tag" + ("" if tone == "warn" else f" armed-tag--{tone}") + (" armed-tag--wrap" if wrap else "")
    return (f'<div class="{cls}" id="play-status" data-region="banner" data-banner="{kind}" '
            f'data-tone="{tone}">{icon}<p class="armed-tag__text">{text}</p>{action}</div>')


def _busy_mark(part: str, label: str) -> str:
    return (f'<span class="armed-tag__cancel is-busy" aria-disabled="true" data-part="{part}">'
            f'{_icon("loader-circle", "icon icon--s icon--turning")}{label}</span>')


def _ok_link(ctx: _Ctx, part: str, submission_id: str) -> str:
    view = ctx.view
    href = view_url(View(page="play", sheet=view.sheet, rail_hidden=view.rail_hidden, seen=submission_id))
    return f'<a class="armed-tag__cancel" href="{_e(href)}" data-part="{part}">OK</a>'


def _banner_link(ctx: _Ctx) -> str | None:
    state = ctx.state
    if state.link is Link.OFFLINE:
        return _tag("offline", "danger", _icon("unplug"),
                    f"<strong>Machine offline.</strong> The machine stopped answering at "
                    f"{_clock(state.last_reply_time)}. Check that it's switched on and the cable is "
                    "plugged in. This page reconnects by itself.",
                    _busy_mark("reconnect-progress", "Reconnecting"), wrap=True)
    if state.link is Link.STARTING:
        return _tag("starting", "quiet", _icon("loader-circle", "icon icon--turning"),
                    "<strong>Connecting to the machine…</strong> Controls come on once the page has "
                    "read it.")
    if not state.fresh:
        return _tag("stale", "warn", _icon("loader-circle", "icon icon--turning"),
                    "<strong>Catching up with the machine…</strong> Controls come back once the page "
                    "has read it again.")
    return None


def _banner_checking(ctx: _Ctx) -> str | None:
    state = ctx.state
    if not state.checking:
        return None
    families = [family for family in Family if family in state.checking]
    waiting = next((r for r in state.results if r.state is ResultState.CHECKING), None)
    if waiting is not None:
        detail = (f"The machine didn't answer “{_e(ACTION_LABELS[waiting.action])}”. Nothing will be "
                  "sent again. Reading the machine to see whether it went through…")
    else:
        reason = state.checking[families[0]].reason
        detail = f"{_e(reason)}. Nothing will be sent again. Reading the machine to see what happened…"
    paused = " and ".join(FAMILY_LABELS[family] for family in families)
    return _tag("checking", "warn", '<span class="dot dot--warn"></span>',
                f"<strong>Checking what happened.</strong> {detail}"
                f'<span class="armed-tag__more">{paused} wait until this is settled.</span>',
                _busy_mark("check-progress", "Checking"), wrap=True)


def _banner_door(ctx: _Ctx) -> str | None:
    door = ws.open_door(ctx.state)
    if door is None:
        return None
    blocked = [ACTION_LABELS[name] for name in (Act.SPIN, Act.BET_ONE, Act.ADD_CREDITS, Act.CASH_OUT)
               if (refusal := ctx.admit(name)) is not None and refusal.code is Code.DOOR_OPEN]
    waits = (f" {_join(blocked)} wait until it's closed: the machine refuses them while a door is open."
             if blocked else "")
    return _tag("door", "warn", _icon("door-open"),
                f"<strong>{_e(door)} open at the machine.</strong>{waits}"
                f'<span class="armed-tag__more">Play also stops whenever Play is off in '
                f'<a class="armed-tag__link" href="/settings#setting-play" data-part="locked-settings">'
                "Settings</a>.</span>")


def _banner_paused(ctx: _Ctx) -> str | None:
    if ctx.state.play_enabled:
        return None
    return _tag("paused", "warn", _icon("power"),
                "<strong>Play is off.</strong> Spin and Bet wait until Play is turned on in "
                '<a class="armed-tag__link" href="/settings#setting-play" data-part="locked-settings">'
                "Settings</a>.")


def _banner_running(ctx: _Ctx) -> str | None:
    state = ctx.state
    action = state.in_progress
    if action is None:
        return None
    if action.name is Act.SPIN:
        bet = action.bet_per_line or ws.chosen_bet(state)
        return _tag("spinning", "quiet", _icon("loader-circle", "icon icon--turning"),
                    f"<strong>Spinning… {_coins(bet)}.</strong> Bet and Cash out wait until the reels stop.")
    amount = action.params.get(F_AMOUNT)
    text = {Act.ADD_CREDITS: f"Adding {_n(amount)} credits…" if isinstance(amount, int) else "Adding credits…",
            Act.CASH_OUT: "Cashing out the whole balance…",
            Act.ARM: "Setting the next result…",
            Act.CANCEL_ARM: "Cancelling the next result…",
            Act.APPLY_PROFILE: "Applying the odds to the machine…",
            Act.RESTORE_STOCK: "Restoring the stock game…"}.get(action.name, "Sending to the machine…")
    return _tag("busy", "quiet", _icon("loader-circle", "icon icon--turning"), f"<strong>{text}</strong>")


def _banner_arm(ctx: _Ctx) -> str | None:
    state = ctx.state
    arm = state.arm
    if arm.state is ArmState.NONE:
        return None
    refusal = ctx.admit(Act.CANCEL_ARM)
    cancel = (f'<button class="armed-tag__cancel" type="submit" form="act-cancel-arm" data-part="armed-cancel"'
              f'{_disabled(refusal)}>Cancel</button>{ctx.form(Act.CANCEL_ARM, "act-cancel-arm")}')
    name = _e(arm.name or "a combination")
    if arm.state is ArmState.ARMED:
        pays = ""
        if arm.bet_per_line == ws.chosen_bet(state) and arm.outcome_id is not None:
            entry = ws.find_outcome(ctx.catalogue(), str(arm.outcome_id))
            if entry is not None and entry.total_award is not None:
                pays = (f" (pays{NBSP}{_n(entry.total_award)} at{NBSP}{_coins(arm.bet_per_line)})"
                        if entry.total_award else f" (loses at{NBSP}{_coins(arm.bet_per_line)})")
        elif arm.bet_per_line is not None:
            pays = f" (set for a bet of {_coins(arm.bet_per_line)})"
        text = f"Next result: <strong>{name}</strong>{pays}. Waiting for the next spin."
        kind = "armed"
    elif arm.state is ArmState.HELD:
        text = (f"Next result <strong>{name}</strong> is held: the spin that would take it didn't "
                "finish. Cancel it to play on.")
        kind = "held"
    else:
        text = ("<strong>A next result may be set</strong> on the machine, but the page can't confirm "
                "which. Cancel it to play on.")
        kind = "arm-unknown"
    return _tag(kind, "warn", '<span class="dot dot--warn"></span>', text, cancel)


def _banner_result(ctx: _Ctx) -> str | None:
    state = ctx.state
    if not state.results:
        return None
    result = state.results[0]
    if result.submission_id == ctx.view.seen or result.state is not ResultState.DONE:
        return None
    money = state.cents_per_credit
    paid = None if result.evidence is None else result.evidence.paid
    if result.action is Act.SPIN and result.outcome in GAME_OVER and paid:
        name = _spin_name(state)
        dollars = _dollars(paid, money, cents=False)
        dollars = f" ({dollars})" if dollars else ""
        return _tag("win", "win", _icon("party-popper"),
                    f'<strong class="armed-tag__big">{_e(name)} · You won {_n(paid)}!</strong> '
                    f"{_n(paid)} credits{dollars} went onto the machine.")
    if result.outcome in CHECKED:
        return _checked(ctx, result)
    delta = abs(result.credits_delta or 0)
    dollars = _dollars(delta, money, cents=False)
    dollars = f" ({dollars})" if dollars else ""
    balance = "" if state.credits is None else f"{SEP}balance now {_n(state.credits)}"
    if result.action is Act.ADD_CREDITS and result.outcome is OutcomeWord.MOVED:
        return _tag("credits-added", "ok", _icon("circle-check"),
                    f"<strong>Added {_n(delta)} credits</strong>{dollars}{balance}")
    if result.action is Act.CASH_OUT and result.outcome is OutcomeWord.MOVED:
        return _tag("cashed-out", "ok", _icon("banknote-arrow-up"),
                    f"<strong>Cashed out {_n(delta)} credits</strong>{dollars}{balance}. "
                    "The whole balance left the machine.",
                    _ok_link(ctx, "cashed-out-dismiss", result.submission_id))
    if result.outcome in SAID_NO:
        reason = f" {_e(result.reason)}." if result.reason else ""
        return _tag("refused", "danger", _icon("circle-alert"),
                    f"<strong>The machine said no to “{_e(ACTION_LABELS[result.action])}”.</strong>"
                    f"{reason} Nothing changed.",
                    _ok_link(ctx, "refused-dismiss", result.submission_id), wrap=True)
    if result.outcome in SPIN_STOPPED:
        return _tag("interrupted", "warn", _icon("triangle-alert"),
                    "<strong>The spin didn't finish.</strong> The machine stopped the game; the "
                    "credits and meters show what it counted.",
                    _ok_link(ctx, "interrupted-dismiss", result.submission_id))
    return None


def _checked(ctx: _Ctx, result: ws.ActionResult) -> str:
    state = ctx.state
    label = _e(ACTION_LABELS[result.action])
    family = ws.FAMILIES.get(result.action)
    again = f" {FAMILY_LABELS[family]} work again." if family is not None else ""
    if result.outcome is OutcomeWord.HAPPENED:
        delta = result.credits_delta
        if result.action is Act.ADD_CREDITS and delta is not None and state.credits is not None:
            what = f"credits went from {_n(state.credits - delta)} to {_n(state.credits)}."
        elif result.action is Act.CASH_OUT and delta is not None:
            what = f"{_n(abs(delta))} credits left the machine."
        else:
            what = f"the machine did “{label}”."
        head = f"<strong>It went through:</strong> {what}"
    else:
        credits = "" if state.credits is None else f" Credits are still {_n(state.credits)}."
        head = f"<strong>Nothing changed:</strong> the machine didn't do “{label}”.{credits} You can try again."
    return _tag("checked", "ok", _icon("circle-check"),
                f'{head}<span class="armed-tag__more">Checked on the machine.{again}</span>',
                _ok_link(ctx, "checked-dismiss", result.submission_id))


def _banner_no_credits(ctx: _Ctx) -> str | None:
    state = ctx.state
    if state.credits != 0:
        return None
    quick = ""
    if state.aft is not None:
        amount = min(QUICK_ADD, state.aft.limit_credits)
        if amount > 0 and ctx.admit(Act.ADD_CREDITS, amount=amount) is None:
            quick = (f'<button class="armed-tag__cancel" type="submit" form="act-quick-add" '
                     f'data-part="quick-add-{amount}">Add {_n(amount)}</button>'
                     f'{ctx.form(Act.ADD_CREDITS, "act-quick-add", amount=amount)}')
    return _tag("no-credits", "quiet", _icon("coins"),
                "<strong>No credits.</strong> Spin and Cash out need credits on the machine. "
                "Add credits to play.", quick)


def _spin_name(state: ws.MachineState) -> str:
    """The newest spin's result name, as the worker journaled it, or its centre-line symbols."""
    record = next((r for r in state.log if r.get("kind") == "spin"), None)
    if record is not None and record.get("name"):
        return str(record["name"])
    if state.last_spin is not None:
        return " · ".join(sp.SYMBOL_NAMES.get(code, f"{code:#04x}")
                          for code in _centre_symbols(state.last_spin.stops))
    return "This spin"


def _join(items: Sequence[str]) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _reels(ctx: _Ctx) -> str:
    state = ctx.state
    spin = state.last_spin
    stops = spin.stops if spin is not None else (0,) * sp.REELS
    game = sp.installed_game()
    spinning = state.in_progress is not None and state.in_progress.name is Act.SPIN
    cls = "reels"
    newest = state.results[0] if state.results else None
    if state.link is not Link.ONLINE:
        cls += " reels--stale"
    elif (not spinning and newest is not None and newest.evidence is not None and newest.evidence.paid
          and newest.submission_id != ctx.view.seen):
        cls += " reels--win"
    reels = []
    for reel, stop in enumerate(stops):
        cells = []
        for offset, row in ((-1, "edge"), (0, "line"), (1, "edge")):
            code = game.strips[(stop + offset) % sp.STOPS][reel]
            cells.append(f'<span class="reel__cell reel__cell--{row}">{_symbol(code, "reel")}</span>')
        spin_cls, label = (" reel--spinning", f"Reel {reel + 1}, spinning") if spinning else ("", f"Reel {reel + 1}")
        reels.append(f'<div class="reel{spin_cls}" aria-label="{label}">{"".join(cells)}</div>')
    return (f'<div class="{cls}" data-region="reels"><span class="payline" aria-hidden="true">'
            '<span class="payline__mark">1</span><span class="payline__mark">1</span></span>'
            f'{"".join(reels)}</div>')


def _last_spin(ctx: _Ctx) -> str:
    state = ctx.state
    if state.link is Link.OFFLINE:
        credits = "credits not read" if state.credits is None else f"{_n(state.credits)} credits"
        return (f'<p class="last-spin" data-region="last-spin"><span class="last-spin__label">Last known</span> '
                f"{credits}{SEP}{_e(_game_name(state))}</p>")
    spin = state.last_spin
    if spin is None:
        return ('<p class="last-spin" data-region="last-spin"><span class="last-spin__label">Last spin</span> '
                "none yet</p>")
    result = (f'<span class="won">won{NBSP}{_n(spin.paid)}</span>' if spin.paid
              else f"lost{NBSP}{_n(spin.total_bet)}")
    return (f'<p class="last-spin" data-region="last-spin"><span class="last-spin__label">Last spin</span> '
            f"{_clock(spin.time)}{SEP}{_coins(spin.bet_per_line)}"
            f'<span class="last-spin__reels">{SEP}{_symbols_text(_centre_symbols(spin.stops))}</span>'
            f"{SEP}{result}</p>")


def _drawer_pull(ctx: _Ctx) -> str:
    view, state = ctx.view, ctx.state
    if state.link is not Link.ONLINE or state.profile is None:
        return ('<button class="drawer-pull" type="button" disabled aria-disabled="true" '
                f'data-part="next-result-toggle" data-region="drawer-pull">{_icon("crosshair")}Next result '
                f'{_icon("chevron-down", "icon on-wide")}{_icon("chevron-up", "icon on-phone")}</button>')
    if view.sheet == "next-result":
        return (f'<a class="drawer-pull is-open" href="{_url("play", rail_hidden=view.rail_hidden)}" '
                f'data-part="next-result-toggle" data-region="drawer-pull">Next result {_icon("chevron-up")}</a>')
    return (f'<a class="drawer-pull" href="{_url("play", sheet="next-result", rail_hidden=view.rail_hidden)}" '
            f'data-part="next-result-toggle" data-region="drawer-pull">{_icon("crosshair")}Next result '
            f'{_icon("chevron-down", "icon on-wide")}{_icon("chevron-up", "icon on-phone")}</a>')


def _meters(ctx: _Ctx) -> str:
    state = ctx.state
    offline = state.link is Link.OFFLINE
    spinning = state.in_progress is not None and state.in_progress.name is Act.SPIN
    paid = 0 if state.last_spin is None or spinning else state.last_spin.paid
    cls = "meters" + (" meters--stale" if offline else "") + (" meters--big-paid" if paid >= 1000 else "")
    if state.credits is None:
        credit_value, credit_cls, credit_sub = "—", " meter__value--zero", "not read yet"
    else:
        credit_value = _n(state.credits)
        credit_cls = " meter__value--zero" if state.credits == 0 else ""
        credit_sub = (f"as of {_clock(state.last_reply_time)}" if offline
                      else _dollars(state.credits, state.cents_per_credit))
    if state.profile is None:
        bet_value, bet_sub = "—", "coins"
    else:
        bet = ws.chosen_bet(state)
        total = _dollars(ws.total_wager(state), state.cents_per_credit, cents=False)
        bet_value = _n(bet)
        lines = f"{NBSP}×{NBSP}{_n(state.profile.lines)}{NBSP}lines" if state.profile.lines > 1 else ""
        bet_sub = f"coin{'s' if bet != 1 else ''}{lines}" + (f"{SEP}{total}" if total else "")
    paid_cls = " meter__value--win" if paid else (" meter__value--zero" if not offline else "")
    return (
        f'<div class="{cls}" data-region="meters">'
        f'<div class="meter meter--credit" id="credit-meter"><span class="meter__label">Credit</span>'
        f'<span class="meter__value{credit_cls}">{credit_value}</span><span class="meter__sub">{credit_sub}</span></div>'
        f'<div class="meter" id="bet-meter"><span class="meter__label">Bet</span>'
        f'<span class="meter__value">{bet_value}</span><span class="meter__sub">{bet_sub}</span></div>'
        f'<div class="meter" id="paid-meter"><span class="meter__label">Paid</span>'
        f'<span class="meter__value{paid_cls}">{_n(paid)}</span>'
        f'<span class="meter__sub">{"this spin" if spinning else "last spin"}</span></div></div>')


def _disabled(refusal: ws.Refusal | None) -> str:
    return "" if refusal is None else f' disabled title="{_e(refusal.reason)}"'


def _key(ctx: _Ctx, part: str, cls: str, icon: str, label: str, sub: str, refusal: ws.Refusal | None,
         form: str) -> str:
    sub = sub if refusal is None else _e(_short(refusal, ctx.state))
    return (f'<button class="key {cls}" type="submit" form="{form}" data-part="{part}"'
            f'{_disabled(refusal)}>{icon}<span class="key__label">{label}</span>'
            f'<span class="key__sub">{sub}</span></button>')


def _deck(ctx: _Ctx) -> str:
    state, view = ctx.state, ctx.view
    profile = state.profile
    top = None if profile is None else profile.max_bet_per_line
    bet = ws.chosen_bet(state)
    bet_one = ctx.admit(Act.BET_ONE)
    max_bet = ctx.admit(Act.MAX_BET)
    spin = ctx.admit(Act.SPIN)
    cash_out = ctx.admit(Act.CASH_OUT)
    add = ctx.admit(Act.ADD_CREDITS)
    keys = [
        _key(ctx, "bet-one", "key--bet", _icon("coins"), "<span>Bet</span> <span>one</span>",
             "Back to 1 coin" if bet is not None and bet == top else "+1 coin", bet_one, "act-bet-one"),
        _key(ctx, "max-bet", "key--max", _icon("chevrons-up"), "<span>Max</span> <span>bet</span>",
             _coins(top) if top else "", max_bet, "act-max-bet"),
    ]
    if state.in_progress is not None and state.in_progress.name is Act.SPIN:
        keys.append('<button class="key key--spin is-busy" type="button" disabled aria-busy="true" '
                    f'data-part="spin">{_icon("loader-circle", "icon icon--turning")}'
                    f'<span class="key__label">Spinning</span><span class="key__sub">{_wager_text(state)}</span></button>')
    else:
        keys.append(_key(ctx, "spin", "key--spin", _icon("flame", "icon icon--dark icon--spin"), "Spin",
                         _wager_text(state) if profile is not None else "", spin, "act-spin"))
    keys.append(_key(ctx, "cash-out", "key--money key--cash", _icon("banknote-arrow-up"),
                     "<span>Cash</span> <span>out</span>", "Whole balance", cash_out, "act-cash-out"))
    limit = "" if state.aft is None else f"Up to {_n(state.aft.limit_credits)}"
    add_label = "<span>Add</span> <span>credits</span>"
    if add is not None:
        keys.append(f'<button class="key key--money key--add" type="button" disabled title="{_e(add.reason)}" '
                    f'data-part="add-credits">{_icon("plus")}<span class="key__label">{add_label}</span>'
                    f'<span class="key__sub">{_e(_short(add, state))}</span></button>')
    else:
        main = spin is not None and spin.code in (Code.NO_CREDITS, Code.CREDITS)
        is_open = view.sheet == "add-credits"
        cls = "key key--money key--add" + (" key--main" if main else "") + (" is-open" if is_open else "")
        href = _url("play", rail_hidden=view.rail_hidden) if is_open else _url(
            "play", sheet="add-credits", rail_hidden=view.rail_hidden)
        keys.append(f'<a class="{cls}" href="{href}" data-part="add-credits">'
                    f'{_icon("plus", "icon icon--dark" if main else "icon")}<span class="key__label">{add_label}</span>'
                    f'<span class="key__sub">{limit}</span></a>')
    forms = (ctx.form(Act.BET_ONE, "act-bet-one") + ctx.form(Act.MAX_BET, "act-max-bet")
             + ctx.form(Act.SPIN, "act-spin") + ctx.form(Act.CASH_OUT, "act-cash-out"))
    return f'<div class="deck" aria-label="Buttons" data-region="deck">{"".join(keys)}{forms}</div>'


def _next_result(ctx: _Ctx) -> str:
    state = ctx.state
    bet = ws.chosen_bet(state)
    groups = {Kind.WIN: [], Kind.NEAR_MISS: [], Kind.LOSS: []}
    for outcome in ctx.catalogue():
        groups[outcome.kind].append(_combo(ctx, outcome))
    hint = ("Pick how the next spin lands. It stays set until someone spins."
            + (f" Pays shown at your bet of {_coins(bet)}." if bet else ""))
    wins = "".join(groups[Kind.WIN]) or '<li class="combo-group__meta">No wins pay at this bet.</li>'
    meta = f' <span class="combo-group__meta">pays at {_coins(bet)}</span>' if bet else ""
    return (
        '<section class="tray tray--drawer sheet sheet--tall" id="next-result-drawer" '
        'aria-labelledby="drawer-title" data-region="next-result"><div class="tray__head"><div>'
        f'<h2 class="tray__title" id="drawer-title">Next result</h2><p class="tray__hint">{hint}</p></div>'
        f'<a class="icon-btn" href="{_url("play", rail_hidden=ctx.view.rail_hidden)}" '
        f'data-part="next-result-close">{_icon("x", alt="Close")}</a></div>'
        '<div class="combo-groups"><div class="combo-group combo-group--wins">'
        f'<h3 class="combo-group__title">Wins{meta}</h3><ul class="combo-list">{wins}</ul></div>'
        '<div class="combo-group"><h3 class="combo-group__title">Near-misses</h3>'
        f'<ul class="combo-list">{"".join(groups[Kind.NEAR_MISS])}</ul>'
        '<h3 class="combo-group__title">Losses</h3>'
        f'<ul class="combo-list">{"".join(groups[Kind.LOSS])}</ul></div></div>'
        f'{ctx.form(Act.ARM, "act-arm")}</section>')


def _combo(ctx: _Ctx, outcome: ws.Outcome) -> str:
    refusal = ctx.admit(Act.ARM, outcome=str(outcome.id))
    strip = "".join(_symbol(code, "mini") for code in outcome.symbols)
    sub = ""
    if not outcome.available:
        sub = f'<span class="combo__sub">{SHORT[Code.OUTCOME_UNAVAILABLE]}</span>'
    elif outcome.kind is Kind.WIN and outcome.name.startswith("Any"):
        sub = f'<span class="combo__sub">{_symbols_text(outcome.symbols)}</span>'
    elif refusal is not None:
        sub = f'<span class="combo__sub">{_e(_short(refusal, ctx.state))}</span>'
    if outcome.available and outcome.kind is Kind.WIN:
        odds = _odds(ctx.state.profile.profile, ws.chosen_bet(ctx.state), outcome.id)
        sub += f'<span class="combo__sub">{odds}</span>' if odds else ""
    if outcome.total_award:
        pays = f'<span class="combo__pays">{_n(outcome.total_award)}</span>'
    else:
        pays = f'<span class="combo__pays combo__pays--none">lost {_n(outcome.total_bet)}</span>'
    part = "combo-" + re.sub(r"[^a-z0-9]+", "-", outcome.name.lower()).strip("-")
    # Keep each symbol name whole and let lines break after a separator, as the design does.
    name = SEP.join(_e(piece).replace(" ", NBSP) for piece in outcome.name.split(" · "))
    return (f'<li><button class="combo" type="submit" form="act-arm" name="{F_OUTCOME}" '
            f'value="{_e(outcome.id)}" data-part="{_e(part)}"{_disabled(refusal)}>'
            f'<span class="strip">{strip}</span><span class="combo__name">{name}{sub}</span>{pays}</button></li>')


def _add_credits(ctx: _Ctx) -> str:
    state, view = ctx.state, ctx.view
    limit = 0 if state.aft is None else state.aft.limit_credits
    money = state.cents_per_credit
    typed = view.amount
    amount = _amount(typed) if typed is not None else min(DEFAULT_AMOUNT, limit) or DEFAULT_AMOUNT
    refusal = ctx.admit(Act.ADD_CREDITS, amount=amount)
    invalid = refusal is not None and refusal.code in (Code.OVER_LIMIT, Code.BAD_AMOUNT)
    shown = f"{_n(amount)}" if isinstance(amount, int) else str(amount)
    unit = (f"credits = {_dollars(amount, money)}" if isinstance(amount, int) and money else "credits")
    limit_money = _dollars(limit, money, cents=False)
    limit_money = f" ({limit_money})" if limit_money else ""
    error = ""
    label_cls = "amount"
    input_extra = ""
    if invalid:
        label_cls += " is-invalid"
        input_extra = ' aria-invalid="true" aria-describedby="amount-error"'
        error = f'<p class="field-error" id="amount-error">{_icon("circle-alert")}{_e(refusal.reason)}.</p>'
    chips = []
    for chip in (*(c for c in AMOUNT_CHIPS if c < limit), limit):
        if chip <= 0:
            continue
        on = " is-on" if chip == amount else ""
        text = f"{_n(chip)} max" if chip == limit else _n(chip)
        chips.append(f'<button class="chip{on}" type="button" data-part="amount-{chip}" '
                     f'data-amount="{chip}">{text}</button>')
    submit_text = f"Add {_e(shown)} credits"
    return (
        '<section class="tray tray--credits sheet sheet--short" id="add-credits-tray" '
        'aria-labelledby="credits-title" data-region="add-credits"><div class="tray__head"><div>'
        '<h2 class="tray__title" id="credits-title">Add credits</h2>'
        f'<p class="tray__hint">Up to {_n(limit)} credits{limit_money} per top-up. Goes straight onto the '
        'machine.</p></div>'
        f'<a class="icon-btn" href="{_url("play", rail_hidden=view.rail_hidden)}" data-part="add-credits-close">'
        f'{_icon("x", alt="Close")}</a></div>'
        f'<form class="credit-form" method="post" action="/actions/{Act.ADD_CREDITS.value}" '
        f'data-part="add-credits-form" data-limit="{limit}">'
        f'<input type="hidden" name="{F_RETURN}" value="{_e(_url("play", rail_hidden=view.rail_hidden))}">'
        f'<label class="{label_cls}" for="credit-amount"><span class="amount__label">Amount</span>'
        f'<input class="amount__input" id="credit-amount" name="{F_AMOUNT}" inputmode="numeric" '
        f'autocomplete="off" value="{_e(shown)}"{input_extra}>'
        f'<span class="amount__unit">{unit}</span></label>{error}'
        f'<div class="chips" aria-label="Quick amounts">{"".join(chips)}</div>'
        f'<button class="btn btn--ink btn--wide" type="submit" data-part="add-credits-submit"'
        f'{_disabled(refusal)}>{_icon("plus", "icon icon--dark")}{submit_text}</button></form></section>')


def _rail_meters(ctx: _Ctx) -> str:
    state = ctx.state
    meters = state.meters
    title = "Meters" if state.link is not Link.OFFLINE else f"Meters{SEP}last known {_clock(state.last_reply_time)}"
    rows = []
    if meters is not None:
        for label, value, _ in _game_meters(meters):
            rows.append(f'<div class="kv__row"><dt>{label}</dt><dd>{_n(value)}</dd></div>')
    counters = state.counters
    rows.append('<div class="kv__row kv__row--stack"><dt>Credits added from this page</dt>'
                f"<dd>{_counter(counters.credits_added, counters.top_ups, 'top-up')}</dd></div>")
    rows.append('<div class="kv__row kv__row--stack"><dt>Cashed out to this page</dt>'
                f"<dd>{_counter(counters.credits_cashed_out, counters.cash_outs, 'cash-out')}</dd></div>")
    return (f'<section class="rail-block" id="rail-meters" data-region="rail-meters">'
            f'<h3 class="rail-block__title">{title}</h3><dl class="kv">{"".join(rows)}</dl>'
            f'<a class="text-link" href="/activity#meters" data-part="rail-all-meters">All meters '
            f'{_icon("chevron-right", "icon icon--s")}</a></section>')


def _game_meters(meters: ws.Meters) -> tuple[tuple[str, int, str], ...]:
    """The game meters both meter lists show: label, value and unit."""
    return (("Games played", meters.games_played, "games"), ("Games won", meters.games_won, "games"),
            ("Games lost", meters.games_played - meters.games_won, "games"),
            ("Coin in", meters.coin_in, "credits"), ("Coin out", meters.coin_out, "credits"),
            ("Jackpots", meters.jackpot, "credits"))


def _counter(credits: int, count: int, word: str) -> str:
    if not count:
        return f"0 credits{SEP}none yet"
    return f"{_n(credits)} credits in {_plural(count, word)}"


def _rail_activity(ctx: _Ctx) -> str:
    items = []
    for record in [r for r in ctx.state.log if _shown(r)][:RAIL_ENTRIES]:
        _, what, where = _describe(record)
        items.append(f'<li class="mini-log__item"><span class="mini-log__time">{_entry_time(record)}</span>'
                     f'<span class="mini-log__what">{what}</span>'
                     f'<span class="mini-log__where">{where}</span></li>')
    return (f'<section class="rail-block" id="rail-activity" data-region="rail-activity">'
            f'<h3 class="rail-block__title">Recent activity</h3><ol class="mini-log">{"".join(items)}</ol>'
            f'<a class="text-link" href="/activity" data-part="rail-all-activity">Open Activity '
            f'{_icon("chevron-right", "icon icon--s")}</a></section>')


# ---------- Activity records ----------

_EVENT_TEXT = MappingProxyType({
    "door_open": ("door-open", "{door} opened"),
    "door_closed": ("door-closed", "{door} closed"),
    "power_up": ("power", "Machine powered up"),
    "power_down": ("power", "Machine powered down"),
    "handpay": ("hand-coins", "Handpay"),
    "handpay_reset": ("hand-coins", "Handpay reset"),
    "cash_out_button": ("banknote-arrow-up", "Cash out button pressed"),
    "aft_complete": ("circle-check", "Transfer complete"),
    "cashbox_removed": ("banknote", "Cashbox removed"),
    "cashbox_installed": ("banknote", "Cashbox put back"),
})
_HIDDEN_EVENTS = frozenset(("game_start",))
_ACTION_ICONS = MappingProxyType({
    Act.ADD_CREDITS.value: "circle-plus", Act.CASH_OUT.value: "banknote-arrow-up",
    Act.ARM.value: "crosshair", Act.CANCEL_ARM.value: "crosshair", Act.SPIN.value: "rotate-cw",
    Act.APPLY_PROFILE.value: "sliders-horizontal", Act.RESTORE_STOCK.value: "sliders-horizontal",
    Act.PLAY.value: "power", Act.SOUND.value: "volume-2", Act.BILLS.value: "banknote",
    Act.CLOCK_SYNC.value: "clock-3",
})
_SETTING_LABELS = MappingProxyType({"sound": "Sound", "bills": "Bill acceptor"})


def _shown(record: Mapping[str, object]) -> bool:
    """Whether Activity lists a journal record: never intents or upload steps (they hold
    request bytes), nor records another entry already tells."""
    kind = record.get("kind")
    if kind not in ws.ACTIVITY_KINDS:
        return False
    if kind == "event":
        return record.get("event") not in _HIDDEN_EVENTS
    if kind == "outcome":
        action, result = record.get("action"), _recorded_outcome(record)
        if action in (Act.BET_ONE.value, Act.MAX_BET.value):
            return False
        if action == Act.SPIN.value and result in GAME_OVER:
            return False  # the spin record tells the game
        if action in (Act.APPLY_PROFILE.value, Act.RESTORE_STOCK.value) and result in (
                OutcomeWord.COMMITTED_VERIFIED, OutcomeWord.RESTORED):
            return False  # the profile record tells the change
    return True


def _recorded_outcome(record: Mapping[str, object]) -> OutcomeWord | None:
    """An outcome record's result word, None when it is not one."""
    try:
        return OutcomeWord(record.get("result"))
    except ValueError:
        return None


def _describe(record: Mapping[str, object]) -> tuple[str, str, str]:
    """(icon, what happened as escaped HTML, where) for one journal record."""
    kind = str(record.get("kind"))
    where = "at the machine" if record.get("source") == "machine" else "from this page"
    if kind == "spin":
        return "rotate-cw", _describe_spin(record), where
    if kind == "event":
        icon, what = _describe_event(record)
        return icon, what, where
    if kind == "outcome":
        return _ACTION_ICONS.get(str(record.get("action")), "circle-check"), _describe_outcome(record), where
    if kind == "profile":
        if record.get("change") == "restored":
            return "sliders-horizontal", "Odds &amp; payouts: Stock game restored", where
        what = "Odds &amp; payouts: custom profile applied"
        returns = record.get("returns")
        if isinstance(returns, Mapping) and returns:
            bet = max(returns, key=lambda key: int(key))
            what += f"{SEP}return {float(returns[bet]):.2f}% at {_coins(int(bet))}"
        return "sliders-horizontal", what, where
    if kind == "page":
        event = record.get("event")
        text = {"started": "Page started", "machine_found": "Machine found",
                "machine_lost": ("Machine stopped answering" if record.get("cause") == "silent"
                                 else "Machine lost: the serial line closed"),
                "journal_torn_tail": "Log: an unfinished last entry was dropped"}.get(str(event), f"Page: {event}")
        return "plug", _e(text), "on this page's server"
    return "history", _e(kind.capitalize()), where


def _describe_spin(record: Mapping[str, object]) -> str:
    bet = int(record.get("bet_per_line") or 0)
    paid = int(record.get("paid") or 0)
    name = record.get("name")
    if name:
        result = _e(name).replace(" ", NBSP)
    else:
        stops = record.get("stops")
        result = _symbols_text(_centre_symbols(tuple(stops))) if isinstance(stops, (list, tuple)) else ""
    total = int(record.get("total_bet") or bet)
    outcome = f'<span class="won">won{NBSP}{_n(paid)}</span>' if paid else f"lost{NBSP}{_n(total)}"
    parts = [f"Spin{SEP}{_coins(bet)}"] + ([result] if result else []) + [outcome]
    return SEP.join(parts)


def _describe_event(record: Mapping[str, object]) -> tuple[str, str]:
    event = str(record.get("event"))
    if event == "arm":
        arm = record.get("arm") if isinstance(record.get("arm"), Mapping) else {}
        name = f" ({_e(arm['name'])})" if arm.get("name") else ""
        text = {ArmState.ARMED.value: "Next result set", ArmState.HELD.value: "Next result held",
                ArmState.NONE.value: "Next result cleared", ArmState.UNKNOWN.value: "Next result not confirmed",
                }.get(str(arm.get("state")), "Next result changed")
        return "crosshair", f"{text}{name}"
    if event in _EVENT_TEXT:
        icon, text = _EVENT_TEXT[event]
        return icon, _e(text.format(door=record.get("door") or "A door"))
    name = record.get("name")
    code = record.get("code")
    if name:
        return "circle-alert", _e(name)
    return "circle-alert", _e(f"Machine event {code:02X}" if isinstance(code, int) else f"Machine event {event}")


def _describe_outcome(record: Mapping[str, object]) -> str:
    action, result = str(record.get("action")), str(record.get("result"))
    word = _recorded_outcome(record)
    checked = " (checked after the machine didn't answer)" if word in CHECKED else ""
    transfer = record.get("transfer") if isinstance(record.get("transfer"), Mapping) else None
    if action in (Act.ADD_CREDITS.value, Act.CASH_OUT.value):
        verb = "Added" if action == Act.ADD_CREDITS.value else "Cashed out"
        if transfer is not None and word in (OutcomeWord.MOVED, OutcomeWord.HAPPENED):
            return f"{verb} {_n(int(transfer.get('credits', 0)))} credits{_e(checked)}"
        label = ACTION_LABELS[Act(action)]
        if word is OutcomeWord.NOTHING_CHANGED:
            return _e(f"{label}: nothing changed{checked}")
        if word is OutcomeWord.REJECTED:
            code = record.get("code")
            return _e(f"{label}: the machine refused" + (f" (code {code:02X})" if isinstance(code, int) else ""))
        return _e(f"{label}: {result.replace('_', ' ')}")
    if action == Act.ARM.value and word is OutcomeWord.ARMED:
        name = record.get("outcome_name")
        return "Next result set" + (f" ({_e(name)})" if name else "")
    if action == Act.CANCEL_ARM.value and word is OutcomeWord.CANCELLED:
        name = record.get("outcome_name")
        return "Next result cancelled" + (f" ({_e(name)})" if name else "")
    if action == Act.PLAY.value and word is OutcomeWord.CONFIRMED:
        return "Play turned on" if record.get("play_enabled") else "Play turned off"
    if action in (Act.SOUND.value, Act.BILLS.value):
        setting = ws.SETTING_NAMES[Act(action)]
        label = _SETTING_LABELS[setting]
        value = record.get("settings", {}).get(setting) if isinstance(record.get("settings"), Mapping) else None
        if word is OutcomeWord.ACKED and value in (Setting.ON.value, Setting.OFF.value):
            return _e(f"{label} {value}")
        if word is OutcomeWord.UNKNOWN:
            return _e(f"{label}: not confirmed, the machine didn't answer")
        if word is OutcomeWord.NACKED:
            return _e(f"{label}: the machine refused")
    if action == Act.CLOCK_SYNC.value:
        return "Machine clock set" if word is OutcomeWord.CONFIRMED else "Machine clock: not confirmed"
    try:
        label = ACTION_LABELS[Act(action)]
    except ValueError:
        label = action
    return _e(f"{label}: {result.replace('_', ' ')}{checked}")


def _entry_time(record: Mapping[str, object]) -> str:
    time = record.get("time")
    if not isinstance(time, str):
        return ""
    return _clock(time)


# ---------- Odds & payouts ----------

def _odds_main(ctx: _Ctx, built: Mapping[str, str]) -> str:
    return ('<main class="main main--tool"><div class="tool"><div class="tool-head">'
            '<div class="tool-head__text"><h1 class="tool-head__title">Odds &amp; payouts</h1>'
            f'{built["odds-head"]}</div>{built["odds-actions"]}</div>'
            f'{built["odds-status"]}{built["preview"]}{built["editor"]}</div></main>')


def _odds_head(ctx: _Ctx) -> str:
    return (f'<p class="tool-head__sub" data-region="odds-head">On the machine now: '
            f'<strong>{_e(_game_name(ctx.state))}</strong></p>')


def _changed_draft(ctx: _Ctx) -> sp.Profile | None:
    draft = ctx.draft()
    state = ctx.state
    if draft is None or draft.profile is None or state.profile is None:
        return None
    return None if draft.profile == state.profile.profile else draft.profile


def _odds_actions(ctx: _Ctx) -> str:
    state = ctx.state
    custom = state.profile is not None and state.profile.custom
    restore = ctx.admit(Act.RESTORE_STOCK) if custom else None
    apply = ctx.admit(Act.APPLY_PROFILE) if _changed_draft(ctx) is not None else None
    restore_off = f' disabled title="{_e(restore.reason)}"' if restore else ("" if custom else " disabled")
    apply_off = (f' disabled title="{_e(apply.reason)}"' if apply
                 else ("" if _changed_draft(ctx) is not None else " disabled"))
    return (
        '<div class="tool-head__actions" data-region="odds-actions" data-draft>'
        f'<button class="btn btn--quiet" type="submit" form="act-restore-stock" data-part="restore-stock"'
        f'{restore_off}>{_icon("rotate-ccw")}Restore stock game</button>'
        '<noscript><button class="btn btn--quiet" type="submit" form="odds-editor" formmethod="get" '
        f'formaction="/odds" data-part="preview-odds">{_icon("refresh-cw")}Preview</button></noscript>'
        f'<button class="btn btn--flame" type="submit" form="odds-editor" data-part="apply-odds"{apply_off}>'
        f'{_icon("upload", "icon icon--dark")}Apply to machine</button>'
        f'{ctx.form(Act.RESTORE_STOCK, "act-restore-stock")}</div>')


def _notice(tone: str, text: str) -> str:
    mark = (f'<span class="dot dot--{tone}"></span>' if tone == "warn"
            else _icon("circle-alert" if tone == "danger" else "circle-check"))
    cls = "notice notice--warn" if tone == "danger" else f"notice notice--{tone}"
    return (f'<div class="{cls}" id="odds-status" data-region="odds-status" data-draft data-tone="{tone}">'
            f'{mark}<p class="notice__text">{text}</p></div>')


def _odds_status(ctx: _Ctx) -> str:
    state = ctx.state
    if Family.EXTENSION in state.checking:
        return _notice("warn", "<strong>Checking what happened</strong>"
                       f"{SEP}{_e(state.checking[Family.EXTENSION].reason)}. Odds changes wait until it is settled.")
    draft = ctx.draft()
    if draft is not None and draft.problem is not None:
        return _notice("danger", f"<strong>The machine would refuse this</strong>{SEP}{_e(draft.problem)}. "
                       "Nothing was sent.")
    changed = _changed_draft(ctx)
    if changed is not None:
        blocked = ctx.admit(Act.APPLY_PROFILE)
        why = f" Can't apply now: {_e(blocked.reason)}." if blocked else ""
        return _notice("warn", f"<strong>Edited (not applied)</strong>{SEP}"
                       f"{_diff_text(state.profile.profile, changed, 'was')}.{why}")
    result = state.results[0] if state.results else None
    if result is not None and result.state is ResultState.DONE and result.submission_id != ctx.view.seen:
        if result.action is Act.APPLY_PROFILE and result.outcome is OutcomeWord.COMMITTED_VERIFIED and state.profile:
            top = _preview(state.profile.profile)[-1]
            return _notice("ok", f"<strong>Applied to the machine</strong> and read back to check{SEP}"
                           f"return {_percent(top.nominal_return, 2)} at {_coins(top.bet_per_line)}")
        if result.action is Act.RESTORE_STOCK and result.outcome is OutcomeWord.RESTORED:
            return _notice("ok", "<strong>Stock game restored</strong> and checked on the machine.")
        if result.action is Act.APPLY_PROFILE and result.outcome in NOT_APPLIED:
            return _notice("danger", "<strong>Not applied</strong>"
                           f"{SEP}the machine kept the profile it had. Nothing changed.")
    if state.profile is None:
        return _notice("quiet", "<strong>Game not read yet.</strong> The editor fills in once the page has "
                       "read the machine.")
    if state.profile.custom:
        return _notice("quiet", f"<strong>A custom profile is on the machine</strong>{SEP}"
                       f"{_diff_text(sp.stock_profile(), state.profile.profile, 'stock')}. "
                       "Restore stock game puts the stock game back.")
    return _notice("quiet", "<strong>Stock game, nothing changed.</strong> There is nothing to apply or "
                   "restore. Change a weight or an award and its effect shows in the preview.")


def _diff_text(old: sp.Profile, new: sp.Profile, word: str) -> str:
    game = sp.installed_game()
    changes = []
    if old.lines != new.lines:
        changes.append(f"paylines {old.lines} → {new.lines}")
    if old.max_bet_per_line != new.max_bet_per_line:
        changes.append(f"max bet per line {old.max_bet_per_line} → {new.max_bet_per_line}")
    for stop, (before, after) in enumerate(zip(old.weights, new.weights)):
        for reel in range(sp.REELS):
            if before[reel] != after[reel]:
                symbol = sp.SYMBOL_NAMES.get(game.strips[stop][reel], "symbol")
                changes.append(f"reel {reel + 1} position {stop + 1} {symbol} weight {before[reel]} → {after[reel]}")
    for index, (before, after) in enumerate(zip(old.payouts, new.payouts)):
        if before.award != after.award:
            name = ws.outcome_name(before.symbols, before.symbols, Kind.WIN)
            changes.append(f"{name} at {_bets(before)} award {before.award:,} → {after.award:,}")
    if not changes:
        return "no differences" if word == "was" else "the same as stock"
    shown = changes[:DIFF_LIST]
    more = f" and {len(changes) - DIFF_LIST} more" if len(changes) > DIFF_LIST else ""
    text = ", ".join(shown) + more
    return _e(text[0].upper() + text[1:])


def _bets(payout: sp.Payout) -> str:
    if payout.min_bet == payout.max_bet:
        return f"{payout.min_bet} coin{'s' if payout.min_bet != 1 else ''}"
    return f"{payout.min_bet}–{payout.max_bet} coins"


def _preview_panel(ctx: _Ctx) -> str:
    state = ctx.state
    rows: list[tuple[str, tuple[sp.PreviewRow, ...], bool]] = [("Stock", _preview(sp.stock_profile()), False)]
    if state.profile is not None and state.profile.custom:
        rows.append(("On the machine", _preview(state.profile.profile), True))
    changed = _changed_draft(ctx)
    if changed is not None:
        rows.append(("Edited", _preview(changed), True))
    top = max(row.bet_per_line for _, previews, _ in rows for row in previews)
    bets = range(1, top + 1)
    head = "".join(f'<th scope="col">{_coins(bet)}</th>' for bet in bets)
    meta = ("Stock game, per bet" if len(rows) == 1
            else "Stock vs " + " vs ".join(name.lower() for name, _, _ in rows[1:]) + ", per bet")
    hot: set[int] = set()

    def table(caption: str, value: Callable[[sp.PreviewRow], str], is_hot: Callable[[sp.PreviewRow], bool]) -> str:
        body = []
        for name, previews, edited in rows:
            by_bet = {row.bet_per_line: row for row in previews}
            cells = []
            for bet in bets:
                row = by_bet.get(bet)
                if row is None:
                    cells.append("<td>—</td>")
                    continue
                if len(rows) == 1:
                    cls = "preview__value"
                elif edited:
                    cls = "preview__edited" + (" is-hot" if is_hot(row) else "")
                    if is_hot(row):
                        hot.add(bet)
                else:
                    cls = ""
                cells.append(f'<td class="{cls}">{value(row)}</td>' if cls else f"<td>{value(row)}</td>")
            row_cls = ' class="preview__stock"' if not edited and len(rows) > 1 else ""
            body.append(f'<tr{row_cls}><th scope="row">{_e(name)}</th>{"".join(cells)}</tr>')
        return (f'<table class="preview"><caption class="preview__caption">{caption}</caption>'
                f'<thead><tr><td></td>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>')

    returns = table("Return", lambda row: _percent(row.nominal_return, 2), lambda row: row.nominal_return > 1)
    hits = table("Hit rate", lambda row: _percent(row.hit_frequency, 1), lambda row: False)
    warning = ""
    if hot:
        warning = (f'<p class="warning">{_icon("triangle-alert", alt="Warning")}Pays back more than it takes at '
                   f'{_join([str(bet) for bet in sorted(hot)])} coin{"s" if max(hot) > 1 else ""}.</p>')
    return (
        '<section class="panel panel--preview" aria-labelledby="preview-title" data-region="preview" data-draft>'
        '<div class="panel__head"><h2 class="panel__title" id="preview-title">Live preview</h2>'
        f'<p class="panel__meta">{meta}</p></div><div class="preview-grid">{returns}{hits}</div>{warning}</section>')


def _editor(ctx: _Ctx) -> str:
    state = ctx.state
    if state.profile is None:
        return '<div class="tool-grid" data-region="editor"></div>'
    base = state.profile.profile
    stock = sp.stock_profile()
    draft = ctx.draft()
    values = {} if draft is None else dict(draft.fields)
    game = sp.installed_game()
    rows = []
    totals = [0] * sp.REELS
    totals_known = True
    for stop in range(sp.STOPS):
        cells = []
        for reel in range(sp.REELS):
            name = f"w-{stop}-{reel}"
            shown = values.get(name, str(base.weights[stop][reel]))
            if shown.strip().isdigit():
                totals[reel] += int(shown)
            else:
                totals_known = False
            mark = _mark(shown, base.weights[stop][reel], stock.weights[stop][reel], draft is not None,
                         state.profile.custom)
            symbol = game.strips[stop][reel]
            cells.append(
                f'<td class="reelcell{" is-changed" if mark else ""}"><span class="reelcell__in">'
                f'<span class="reelcell__sym">{_symbol(symbol, "mini")}<span class="reelcell__name">'
                f'{_e(sp.SYMBOL_NAMES.get(symbol, "Symbol"))}</span></span><span class="weight">'
                f'<input class="weight__input" form="odds-editor" name="{name}" aria-label="Reel {reel + 1} position {stop + 1} weight" '
                f'inputmode="numeric" autocomplete="off" value="{_e(shown)}" data-base="{base.weights[stop][reel]}" '
                f'data-reel="{reel}">{mark}</span></span></td>')
        rows.append(f'<tr><th scope="row" class="reeltable__pos">{stop + 1}</th>{"".join(cells)}</tr>')
    foot = []
    for reel in range(sp.REELS):
        base_total = sum(row[reel] for row in base.weights)
        stock_total = sum(row[reel] for row in stock.weights)
        total = totals[reel] if totals_known else None
        mark = ""
        if total is not None and draft is not None and total != base_total:
            mark = f' <span class="weight__was">was {base_total}</span>'
        elif draft is None and state.profile.custom and base_total != stock_total:
            mark = f' <span class="weight__was">stock {stock_total}</span>'
        foot.append(f'<td class="reeltable__total{" is-changed" if mark else ""}" data-total-reel="{reel}">'
                    f'{"—" if total is None else total}{mark}</td>')
    reels = (
        '<section class="panel" aria-labelledby="reels-title"><div class="panel__head">'
        '<h2 class="panel__title" id="reels-title">Reels</h2>'
        f'<p class="panel__meta">{sp.STOPS} positions × {sp.REELS} reels</p></div>'
        f'<p class="panel__about">Each reel has {sp.STOPS} positions. A position\'s weight is how likely the '
        "reel is to stop there; higher weight = more often. Symbols on the reels can't be changed, only "
        'weights.</p><table class="reeltable"><thead><tr><th scope="col" class="reeltable__pos">Pos</th>'
        + "".join(f'<th scope="col">Reel {reel + 1}</th>' for reel in range(sp.REELS))
        + f'</tr></thead><tbody>{"".join(rows)}</tbody><tfoot><tr><th scope="row" class="reeltable__pos">Total</th>'
        f'{"".join(foot)}</tr></tfoot></table></section>')
    return (f'<div class="tool-grid" data-region="editor">'
            f'<form id="odds-editor" class="tool-grid__form" method="post" '
            f'action="/actions/{Act.APPLY_PROFILE.value}" hidden>'
            f'<input type="hidden" name="{F_GENERATION}" value="{_e(ws.profile_generation(state))}">'
            f'<input type="hidden" name="{F_RETURN}" value="/odds"></form>'
            f'{reels}'
            f'<div class="tool-stack">{_paytable(ctx, base, stock, values, draft is not None)}'
            f'{_limits(ctx, base, values)}</div></div>')


def _mark(shown: str, base: int, stock: int, drafting: bool, custom: bool) -> str:
    if drafting and shown.strip() != str(base):
        return f'<span class="weight__was">was {base:,}</span>'
    if not drafting and custom and base != stock:
        return f'<span class="weight__was">stock {stock:,}</span>'
    return ""


def _paytable(ctx: _Ctx, base: sp.Profile, stock: sp.Profile, values: Mapping[str, str], drafting: bool) -> str:
    bets = range(1, base.max_bet_per_line + 1)
    combos: dict[tuple[int, ...], list[int]] = {}
    for index, payout in enumerate(base.payouts):
        combos.setdefault(payout.symbols, []).append(index)
    custom = ctx.state.profile.custom
    shown_profile = _changed_draft(ctx) or base  # the odds of what the editor shows
    rows = []
    for symbols, indexes in combos.items():
        name = ws.outcome_name(symbols, symbols, Kind.WIN)
        # Each bet column pays from the first record (in paytable order) for these symbols
        # whose wager limits include it; a record covering several bets is one field.
        owner = [next((i for i in indexes if base.payouts[i].min_bet <= bet <= base.payouts[i].max_bet), None)
                 for bet in bets]
        cells = []
        at = 0
        while at < len(owner):
            index = owner[at]
            span = 1
            while at + span < len(owner) and owner[at + span] == index:
                span += 1
            colspan = f' colspan="{span}"' if span > 1 else ""
            first, last = at + 1, at + span
            bet_text = (f"{first} coin{'s' if first != 1 else ''}" if span == 1 else f"{first}–{last} coins")
            if index is None:
                cells.append(f'<td{colspan}><input class="award" aria-label="{_e(name)} at {bet_text}" '
                             'value="" placeholder="—" disabled></td>')
            else:
                field = f"award-{index}"
                award = base.payouts[index].award
                shown = values.get(field, f"{award:,}")
                changed = (drafting and shown.replace(",", "").strip() != str(award)) or (
                    not drafting and custom and award != stock.payouts[index].award)
                cls = ' class="is-changed"' if changed else ""
                cells.append(f'<td{colspan}{cls}><input class="award" form="odds-editor" name="{field}" '
                             f'aria-label="{_e(name)} at {bet_text}" '
                             f'inputmode="numeric" autocomplete="off" value="{_e(shown)}" data-base="{award}"></td>')
            at += span
        strip = "".join(_symbol(code, "mini") for code in symbols)
        top = max((bet for bet, index in zip(bets, owner) if index is not None), default=None)
        odds = "" if top is None else _odds(shown_profile, top, owner[top - 1])
        odds = f'<span class="paycombo__odds">{odds}</span>' if odds else ""
        rows.append(f'<tr><th scope="row"><span class="paycombo"><span class="strip">{strip}</span>'
                    f'<span class="paycombo__name">{_e(name)}{odds}</span></span></th>{"".join(cells)}</tr>')
    head = "".join(f'<th scope="col">{_coins(bet)}</th>' for bet in bets)
    return ('<section class="panel" aria-labelledby="pay-title"><div class="panel__head">'
            '<h2 class="panel__title" id="pay-title">Paytable</h2><p class="panel__meta">Credits won per line</p></div>'
            '<p class="panel__about">Awards are credits won on a line at that bet. A dash means that '
            "combination does not pay at that bet. An award spanning several bets is one paytable record: "
            'changing it changes every bet it covers.</p>'
            f'<table class="paytable"><thead><tr><th scope="col">Combination</th>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></section>')


def _limits(ctx: _Ctx, base: sp.Profile, values: Mapping[str, str]) -> str:
    info = ctx.state.ext_info
    max_lines = info.max_lines if info is not None else sp.LINE_ROWS
    max_bet = info.max_bet_per_line if info is not None else sp.BYTE_MAX
    max_total = info.max_total_bet if info is not None else sp.BYTE_MAX
    lines = values.get(F_LINES, str(base.lines))
    bet = values.get(F_MAX_BET, str(base.max_bet_per_line))
    total = (f"{int(lines) * int(bet):,} credits" if lines.isdigit() and bet.isdigit() else "—")
    return ('<section class="panel" aria-labelledby="limits-title"><div class="panel__head">'
            '<h2 class="panel__title" id="limits-title">Paylines &amp; bet limits</h2></div><div class="fields">'
            f'<label class="field"><span class="field__label">Paylines<span class="field__hint">up to {max_lines}'
            f'</span></span><input class="field__input" form="odds-editor" name="{F_LINES}" inputmode="numeric" '
            f'autocomplete="off" value="{_e(lines)}" data-base="{base.lines}"></label>'
            f'<label class="field"><span class="field__label">Max bet per line<span class="field__hint">coins, 1 to '
            f'{max_bet}</span></span><input class="field__input" form="odds-editor" name="{F_MAX_BET}" '
            f'inputmode="numeric" autocomplete="off" value="{_e(bet)}" data-base="{base.max_bet_per_line}"></label>'
            f'<div class="field"><span class="field__label">Max total bet<span class="field__hint">up to '
            f'{max_total}</span></span><span class="field__value">{total}</span></div></div></section>')


# ---------- Activity & meters ----------

def _activity_main(ctx: _Ctx, built: Mapping[str, str]) -> str:
    return f'<main class="main main--tool"><div class="tool tool--split">{built["log"]}{built["meters"]}</div></main>'


def _log(ctx: _Ctx) -> str:
    view = ctx.view
    records = [record for record in ctx.entries if _shown(record)]
    more = len(ctx.entries) > ACTIVITY_PAGE
    records = records[:ACTIVITY_PAGE] if more else records
    meta = "Earlier entries" if view.before is not None else "Newest first"
    parts = [(
        '<section class="panel panel--log" id="activity-log" aria-labelledby="log-title" data-region="log">'
        '<div class="panel__head"><h1 class="panel__title panel__title--page" id="log-title">Activity</h1>'
        f'<p class="panel__meta">{meta}{SEP}kept across restarts</p></div>'
        '<p class="panel__about">Everything that happens to the machine, in the order it happened, and '
        "whether it was done at the machine or from this page.</p>")]
    if view.before is not None:
        parts.append(f'<a class="btn btn--quiet btn--wide" href="/activity" data-part="newer-entries">'
                     f'{_icon("arrow-up")}Back to newest entries</a>')
    days: list[tuple[datetime | None, list[Mapping[str, object]]]] = []
    for record in records:
        time = record.get("time")
        moment = _local(_parse_time(time)) if isinstance(time, str) else None
        if not days or (days[-1][0] and days[-1][0].date()) != (moment and moment.date()):
            days.append((moment, []))
        days[-1][1].append(record)
    for moment, day in days:
        entries = []
        for record in day:
            icon, what, where = _describe(record)
            page_side = record.get("source") != "machine"
            where_cls = "where where--page" if page_side else "where"
            where_icon = "monitor-smartphone" if page_side else "hand"
            entries.append(f'<li class="log__entry"><span class="log__time">{_entry_time(record)}</span>'
                           f'<span class="log__icon">{_icon(icon)}</span><span class="log__what">{what}</span>'
                           f'<span class="{where_cls}">{_icon(where_icon, "icon icon--s")}{where}</span></li>')
        parts.append(f'<h2 class="log__day">{_day_heading(moment, ctx.now)}</h2><ol class="log">{"".join(entries)}</ol>')
    if view.before is None and all(record.get("kind") == "page" for record in records):
        parts.append(
            f'<div class="log-empty"><span class="log-empty__icon">{_icon("history")}</span>'
            '<h2 class="log-empty__title">Nothing else yet</h2><p class="log-empty__text">Spins, credits added '
            "and cashed out, Next result picks, Odds &amp; payouts changes and door openings show up here as "
            'they happen, at the machine or from this page.</p>'
            f'<a class="btn" href="/" data-part="empty-go-play">{_icon("gamepad-2")}Go to Play</a></div>')
    if more and records:
        parts.append(f'<a class="btn btn--quiet btn--wide" href="/activity?before={int(records[-1]["seq"])}" '
                     f'data-part="load-earlier">{_icon("history")}Load earlier entries</a>')
    parts.append("</section>")
    return "".join(parts)


def _day_heading(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "Undated"
    today = _local(now).date()
    date = f'<span class="log__date">{moment.strftime("%a %b")} {moment.day}</span>'
    if moment.date() == today:
        return f"Today {date}"
    if moment.date() == today - timedelta(days=1):
        return f"Yesterday {date}"
    if today - moment.date() < timedelta(days=7):
        return f'{moment.strftime("%A")} <span class="log__date">{moment.strftime("%b")} {moment.day}</span>'
    return f'{moment.strftime("%a %b")} {moment.day} <span class="log__date">{moment.year}</span>'


def _meter_tiles(ctx: _Ctx) -> str:
    state = ctx.state
    meters = state.meters
    tiles = []
    if meters is not None:
        for label, value, unit in _game_meters(meters):
            tiles.append(f'<div class="meter"><span class="meter__label">{label}</span>'
                         f'<span class="meter__value">{_n(value)}</span><span class="meter__sub">{unit}</span></div>')
    counters = state.counters
    for label, credits, count, word in (("Credits added from this page", counters.credits_added, counters.top_ups,
                                         "top-up"),
                                        ("Cashed out to this page", counters.credits_cashed_out, counters.cash_outs,
                                         "cash-out")):
        zero = " meter__value--zero" if not count else ""
        sub = f"credits{SEP}none yet" if not count else f"credits in {_plural(count, word)}"
        tiles.append(f'<div class="meter meter--wide"><span class="meter__label">{label}</span>'
                     f'<span class="meter__value{zero}">{_n(credits)}</span><span class="meter__sub">{sub}</span></div>')
    if meters is not None:
        tiles.append(f'<div class="meter"><span class="meter__label">Door openings</span>'
                     f'<span class="meter__value">{_n(meters.slot_door_opened)}</span><span class="meter__sub">times</span></div>')
        tiles.append(f'<div class="meter"><span class="meter__label">Power resets</span>'
                     f'<span class="meter__value">{_n(meters.power_reset)}</span><span class="meter__sub">times</span></div>')
    stale = (f"{SEP}last known {_clock(state.last_reply_time)}" if state.link is Link.OFFLINE else "")
    return ('<section class="panel panel--meters" id="meters" aria-labelledby="meters-title" data-region="meters">'
            '<div class="panel__head"><h2 class="panel__title" id="meters-title">Meters</h2>'
            f'<p class="panel__meta">Lifetime totals{stale}</p></div>'
            '<p class="panel__about">Meters never go back: every credit in and out stays counted, even after a '
            "cash-out. Games, coin, jackpot, door and power meters come from the machine and count everything "
            "since it was set up; the two rows for this page count only what this page has done.</p>"
            f'<div class="mtiles">{"".join(tiles)}</div></section>')


# ---------- Settings ----------

def _settings_main(ctx: _Ctx, built: Mapping[str, str]) -> str:
    return (f'<main class="main main--tool"><div class="tool tool--split tool--settings">'
            f'{built["settings"]}{built["about"]}</div></main>')


def _settings(ctx: _Ctx) -> str:
    state = ctx.state
    running = None if state.in_progress is None else state.in_progress.name
    rows = [
        _switch_row(ctx, "play", "power", "Play",
                    "When off, nobody can play at the machine or from this page, and top-ups are refused.",
                    Act.PLAY, Setting.ON if state.play_enabled else Setting.OFF,
                    ("Enabled", "Off"), running is Act.PLAY),
        _switch_row(ctx, "sound", "volume-2", "Sound", "Reel, button and win sounds at the machine.",
                    Act.SOUND, state.settings.sound, ("On", "Off"), running is Act.SOUND),
        _switch_row(ctx, "bills", "banknote", "Bill acceptor", "Lets the machine take paper bills as credits.",
                    Act.BILLS, state.settings.bills, ("All bills accepted", "Off"), running is Act.BILLS),
        _clock_row(ctx, running is Act.CLOCK_SYNC),
    ]
    return ('<section class="panel" aria-labelledby="settings-title" data-region="settings"><div class="panel__head">'
            '<h1 class="panel__title panel__title--page" id="settings-title">Settings</h1>'
            '<p class="panel__meta">Changes apply at once</p></div>'
            '<p class="panel__about">Each row shows what the machine reports right now. A tap changes it on the '
            f'machine straight away.</p><div class="settings">{"".join(rows)}</div></section>')


def _switch_row(ctx: _Ctx, key: str, icon: str, label: str, desc: str, act: Act, value: Setting,
                words: tuple[str, str], saving: bool) -> str:
    form_id = f"act-{act.value}"
    extra_desc = ""
    if saving:
        target = ctx.state.in_progress.params.get(F_ON)
        doing = "Turning on…" if target else "Turning off…"
        control = (f'<span class="setting__state setting__state--pending">'
                   f'{_icon("loader-circle", "icon icon--s icon--turning")}{doing}</span>'
                   f'<button class="switch is-pending" type="button" aria-pressed="{"true" if target else "false"}" '
                   f'aria-busy="true" aria-label="{label}" disabled data-part="{key}-toggle">'
                   '<span class="switch__knob"></span></button>')
        extra_desc = ('<p class="setting__desc"><span class="dot dot--warn"></span>Sent to the machine. Waiting '
                      "for it to answer; this row shows the machine's new state as soon as it does.</p>")
    elif value is Setting.UNKNOWN:
        on = ctx.admit(act, on=True)
        off = ctx.admit(act, on=False)
        control = ('<span class="setting__state">Not confirmed</span>'
                   f'<button class="btn btn--quiet" type="submit" form="{form_id}" name="{F_ON}" value="1" '
                   f'data-part="{key}-on"{_disabled(on)}>On</button>'
                   f'<button class="btn btn--quiet" type="submit" form="{form_id}" name="{F_ON}" value="0" '
                   f'data-part="{key}-off"{_disabled(off)}>Off</button>')
        if act is not Act.PLAY:
            extra_desc = (f'<p class="setting__desc"><span class="dot dot--warn"></span>The machine didn\'t answer '
                          f"the last change, so {label.lower()} may be on or off. It has no way to read it back; "
                          "a tap sends your choice again.</p>")
    else:
        is_on = value is Setting.ON
        refusal = ctx.admit(act, on=not is_on)
        control = (f'<span class="setting__state">{words[0] if is_on else words[1]}</span>'
                   f'<button class="switch{" is-on" if is_on else ""}" type="submit" form="{form_id}" '
                   f'name="{F_ON}" value="{"0" if is_on else "1"}" aria-pressed="{"true" if is_on else "false"}" '
                   f'aria-label="{label}" data-part="{key}-toggle"{_disabled(refusal)}>'
                   '<span class="switch__knob"></span></button>')
    return (f'<div class="setting" id="setting-{key}"><span class="setting__icon">{_icon(icon)}</span>'
            f'<div class="setting__text"><h2 class="setting__label">{label}</h2><p class="setting__desc">{desc}</p>'
            f'{extra_desc}</div><div class="setting__control">{control}{ctx.form(act, form_id)}</div></div>')


def _clock_row(ctx: _Ctx, saving: bool) -> str:
    state = ctx.state
    offset = state.settings.clock_offset
    if offset is None:
        shown, desc = "Not read yet", ""
    else:
        machine = _local(ctx.now + timedelta(seconds=offset))
        shown = (f'{machine.strftime("%a %b")} {machine.day}, {machine.hour % 12 or 12}:{machine.minute:02d}:'
                 f'{machine.second:02d} {"AM" if machine.hour < 12 else "PM"}')
        seconds = round(abs(offset))
        if seconds < 1:
            desc = '<p class="setting__desc"><span class="dot dot--ok"></span>Matches this computer</p>'
        else:
            side = "behind" if offset < 0 else "ahead of"
            desc = (f'<p class="setting__desc"><span class="dot dot--warn"></span>{_plural(seconds, "s", "s")} '
                    f"{side} this computer</p>")
    refusal = ctx.admit(Act.CLOCK_SYNC)
    if saving:
        control = (f'<span class="setting__state setting__state--pending">'
                   f'{_icon("loader-circle", "icon icon--s icon--turning")}Setting…</span>')
    else:
        control = (f'<button class="btn btn--quiet" type="submit" form="act-clock-sync" data-part="clock-sync"'
                   f'{_disabled(refusal)}>{_icon("refresh-cw")}Set to this computer</button>')
    return ('<div class="setting setting--clock" id="setting-clock">'
            f'<span class="setting__icon">{_icon("clock-3")}</span><div class="setting__text">'
            f'<h2 class="setting__label">Machine clock</h2><p class="setting__clock">{shown}</p>{desc}</div>'
            f'<div class="setting__control">{control}{ctx.form(Act.CLOCK_SYNC, "act-clock-sync")}</div></div>')


def _about(ctx: _Ctx) -> str:
    state = ctx.state
    door = ws.open_door(state)
    if state.link is Link.OFFLINE:
        status, dot = f"Offline{SEP}last seen {_clock(state.last_reply_time)}", "danger"
    elif state.link is Link.STARTING:
        status, dot = "Connecting", "warn"
    else:
        play = "Play enabled" if state.play_enabled else "Play off"
        doors = "doors not read yet" if state.doors is None else (f"{_e(door)} open" if door else "doors closed")
        status, dot = f"Online{SEP}{play}{SEP}{doors}", "ok" if state.play_enabled and not door else "warn"
    rows = [("Machine", f"Bally ProSlot 6000{SEP}{_e(_denomination(state.cents_per_credit))}{SEP}3 reels"),
            ("Status", f'<span class="dot dot--{dot}"></span>{status}'),
            ("Game", _e(_game_name(state))),
            ("Credit value", (f"{_dollars(1, state.cents_per_credit, cents=False)} per credit"
                              if state.cents_per_credit else "Not read yet"))]
    if state.profile is not None:
        coin = (f" Each coin is one credit ({_dollars(1, state.cents_per_credit, cents=False)})."
                if state.cents_per_credit else "")
        rows.append(("Bet", f"Bet 1 to {_n(state.profile.max_bet_per_line)} coins.{coin}"))
    if state.aft is not None:
        money = _dollars(state.aft.limit_credits, state.cents_per_credit, cents=False)
        rows.append(("Top-ups", f"Up to {_n(state.aft.limit_credits)} credits" + (f" ({money})" if money else "")
                     + " per top-up"))
    rows.append(("Cash out", "This machine can only cash out the whole balance."))
    body = "".join(f'<div class="kv__row kv__row--stack"><dt>{label}</dt>'
                   f'<dd{WITH_DOT if label == "Status" else ""}>{value}</dd></div>'
                   for label, value in rows)
    return ('<section class="panel" aria-labelledby="about-title" data-region="about"><div class="panel__head">'
            f'<h2 class="panel__title" id="about-title">This machine</h2></div><dl class="kv kv--about">{body}</dl>'
            '</section>')


_MAINS: Mapping[str, Callable[[_Ctx, Mapping[str, str]], str]] = MappingProxyType({
    "play": _play_main, "odds": _odds_main, "activity": _activity_main, "settings": _settings_main})
