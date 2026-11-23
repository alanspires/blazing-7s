"""Offline codec and nominal-math preview for custom game profiles of the installed game.

A profile is the part of the installed game (personality E595211X-05) a serial-profile
firmware extension is meant to change: lines, maximum bet per line, per-stop weights and
the eleven paytable records. Symbols, strips, symbol groups and line patterns stay the
installed game's and are read from the hash-pinned personality ROM, once per process.
Experimental firmware support is in firmware/INTEGRATION.md. Its limited live checks on
one machine are recorded in notes/firmware-bringup.md; it is not generally
hardware-qualified. Nothing in this module talks to the machine.

The math is the requested extension's, not the native evaluator's
(notes/remote-game-config.md, "Wager and line constraints"): every profile line is
evaluated on each spin, line i uses LINE pattern i whatever the line count, a record's
wager limits compare against the bet per line, and a spin costs bet per line × lines.

Binary layout, PROFILE_SIZE bytes:

    0..3   MAGIC
    4      VERSION
    5      lines, 1..len(line patterns)
    6      max bet per line
    7      RECORD_COUNT, the installed record count
    8..73  STOPS rows of REELS weight bytes
    74..75 zero
    76..   RECORD_COUNT native 16-byte paytable records: award u32 BE; five symbol slots,
           the last two UNUSED_SYMBOL; max bet; min bet; flags; line mask; match count
           (REELS); record index; zero

Bounds enforced by default are field widths, not limits the machine is known to operate
within (see Caps). Callers narrow them to qualified firmware caps.

score() is the one line evaluator: preview() sums it over every stop combination, and
build_catalogue() uses it to find reel stops that land each named outcome.
"""

from __future__ import annotations

import enum
import functools
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from itertools import product
from math import prod
from pathlib import Path
from types import MappingProxyType
from typing import Union

MAGIC = b"S6PF"
VERSION = 1
REELS = 3
STOPS = 22
RECORD_COUNT = 11
RECORD_SIZE = 16
WEIGHTS_AT = 8
RESERVED_AT = WEIGHTS_AT + STOPS * REELS
RECORDS_AT = RESERVED_AT + 2
PROFILE_SIZE = RECORDS_AT + RECORD_COUNT * RECORD_SIZE

BYTE_MAX = 0xFF
AWARD_MAX = 0xFFFF_FFFF  # the firmware accumulates awards in unsigned 32-bit longs
SYMBOL_SLOTS = 5  # a native record has a symbol per possible reel
UNUSED_SYMBOL = 0x24  # fills native symbol slots beyond REELS
GROUP_SYMBOL = 0x80  # a record symbol with this bit names a group of strip symbols
PROGRESSIVE_FLAGS = frozenset((0x10, 0x20, 0x40))  # the installed records' only flag values
FLAGS = frozenset((0,)) | PROGRESSIVE_FLAGS
CENTER_LINE = 0x01  # line mask of line 0, the only line marked on the glass

PERSONALITY = Path(__file__).resolve().parent.parent / "roms" / "personality_E595211X-05.bin"
PERSONALITY_SHA256 = "7befde1d6ae296122ef2024f9d21e5e3d1c714a4c4d085aabfc6405a4176316f"
# Personality file offsets (CPU address - 0x100000); notes/remote-game-config.md.
P_MAX_BET, P_LINES = 0xD0F8, 0xD0F9
P_GROUPS, GROUP_SIZE = 0xD000, 10  # count, then that many member symbols
P_ROW_START, P_ROW_STEP = 0xD16C, 0xD188  # signed longs per symbol row (FUN_000363f8)
P_RECORD_COUNT, P_RECORDS = 0xD716, 0xD718
P_SYMBOLS, P_WEIGHTS, P_ROW = 0xD7D2, 0xD840, 5  # [stop * P_ROW + reel]
LINE_ROWS = 5  # symbol rows 0..4 are the profile's lines; 5 and 6 are off the glass
CENTER = 0  # index of the center line, the line CENTER_LINE masks

# Names for the installed game's symbol and group codes. Inferred: notes/reel-control.md,
# "Geometry and symbols", matches the codes' award patterns to the cabinet glass; moving the
# reels has not confirmed them. Group members are the personality's (Game.groups).
SYMBOL_NAMES: Mapping[int, str] = MappingProxyType({
    0x18: "Blank",
    0x0A: "Bar",
    0x0B: "Double bar",
    0x0C: "Triple bar",
    0x0D: "Seven",
    0x0E: "Blazing 7",
    0x80: "Any seven",
    0x81: "Any bar",
})
# Catalogues kept by build_catalogue: a few profiles' worth at each bet per line.
CATALOGUE_CACHE = 16


class Problem(enum.Enum):
    PERSONALITY = "personality"
    SIZE = "size"
    MAGIC = "magic"
    VERSION = "version"
    RECORD_COUNT = "record_count"
    RESERVED = "reserved"
    RECORD_LAYOUT = "record_layout"
    FIELDS = "fields"
    TYPE = "type"
    CAP = "cap"
    LINES = "lines"
    MAX_BET = "max_bet"
    TOTAL_BET = "total_bet"
    WEIGHTS = "weights"
    WEIGHT_TOTAL = "weight_total"
    AWARD = "award"
    SYMBOL = "symbol"
    WAGER = "wager"
    FLAGS = "flags"
    LINE_MASK = "line_mask"
    PROGRESSIVE_LINE = "progressive_line"
    UNCOVERED_BET = "uncovered_bet"
    AWARD_OVERFLOW = "award_overflow"


class ProfileError(ValueError):
    def __init__(self, problem: Problem, detail: str):
        super().__init__(f"{problem.value}: {detail}")
        self.problem = problem


@dataclass(frozen=True)
class Payout:
    """One paytable record. Wager limits are inclusive bets per line.

    A zero award still matches: it ends the line's evaluation without paying, so later
    records cannot pay that line (FUN_000664a0 stops at the first match, 0x669dc).
    """
    award: int
    symbols: tuple[int, ...]  # one per reel; GROUP_SYMBOL codes name symbol groups
    min_bet: int
    max_bet: int
    flags: int
    line_mask: int  # bit i: pays on line i


@dataclass(frozen=True)
class Profile:
    lines: int
    max_bet_per_line: int
    weights: tuple[tuple[int, ...], ...]  # [stop][reel]
    payouts: tuple[Payout, ...]  # evaluated in order; the first match pays a line


@dataclass(frozen=True)
class Game:
    """The installed game's fixed definition, which profiles do not change."""
    strips: tuple[tuple[int, ...], ...]  # symbol at [stop][reel]
    groups: Mapping[int, frozenset[int]]  # group symbol code -> member symbols
    line_patterns: tuple[tuple[int, ...], ...]  # per line, stop offset per reel
    stock: Profile

    @property
    def symbols(self) -> frozenset[int]:
        """Symbol codes a payout may use."""
        return frozenset(s for row in self.strips for s in row) | frozenset(self.groups)


@dataclass(frozen=True)
class Caps:
    """Upper bounds a profile must keep within.

    The defaults are the codec's field widths and line patterns, not limits the machine
    is known to operate within: a spin's award total must fit the firmware's unsigned
    32-bit accumulator, which says nothing about what it can pay safely. Callers pass the
    caps the target firmware has qualified; caps can only narrow the defaults.
    """
    max_bet_per_line: int = BYTE_MAX
    max_lines: int = LINE_ROWS
    max_total_bet: int = BYTE_MAX
    max_award: int = AWARD_MAX

    def __post_init__(self):
        _int(self.max_bet_per_line, 1, BYTE_MAX, Problem.CAP, "max_bet_per_line cap")
        _int(self.max_lines, 1, LINE_ROWS, Problem.CAP, "max_lines cap")
        _int(self.max_total_bet, 1, BYTE_MAX, Problem.CAP, "max_total_bet cap")
        _int(self.max_award, 1, AWARD_MAX, Problem.CAP, "max_award cap")


@dataclass(frozen=True)
class PreviewRow:
    """Nominal results at one bet per line, over every stop combination.

    Masses are weight products: outcome_mass is all outcomes, hit_mass those where a line
    pays, award_mass the weighted sum of the awards they pay, and record_masses[i] those
    where paytable record i pays at least one line (it is the first match there and its
    award is not zero), so a record's odds are record_masses[i] / outcome_mass. All of it is
    nominal: records flagged progressive count at their record award, and
    progressive_award_mass is that part of award_mass, not what an external progressive
    controller would actually pay. External bonuses are excluded too.
    """
    bet_per_line: int
    total_bet: int
    outcome_mass: int
    hit_mass: int
    award_mass: int
    progressive_award_mass: int
    record_masses: tuple[int, ...]  # by paytable record index

    @property
    def nominal_return(self) -> Fraction:
        return Fraction(self.award_mass, self.outcome_mass * self.total_bet)

    @property
    def hit_frequency(self) -> Fraction:
        return Fraction(self.hit_mass, self.outcome_mass)


@dataclass(frozen=True)
class SpinScore:
    """Nominal result of one spin's stops at one bet per line.

    lines holds, per enabled line in line order, the index of the first paytable record
    that matched the line and that record's award, or (None, 0) when none matched. A
    zero-award match keeps its index with award 0: it ended the line unpaid. Awards are
    nominal: progressive records count at their record award.
    """
    lines: tuple[tuple[int | None, int], ...]
    total_award: int
    total_bet: int  # bet per line x lines


@dataclass(frozen=True)
class StopChoice:
    """Reel stops that land a catalogue entry, with the whole spin's score at that bet."""
    stops: tuple[int, ...]
    score: SpinScore


# Paytable record index, or a named pattern's name, to the stops chosen for it. An absent
# key has no stops that land it at that profile and bet.
Catalogue = Mapping[Union[int, str], StopChoice]  # noqa: UP007 -- a runtime alias; int | str needs 3.10 and the stdlib suites run on 3.9


def load_game(personality: bytes) -> Game:
    """The installed game from the original personality image, verified by hash."""
    digest = hashlib.sha256(personality).hexdigest()
    if digest != PERSONALITY_SHA256:
        raise ProfileError(Problem.PERSONALITY, f"sha256 {digest} is not the installed personality")
    p = personality
    strips = tuple(tuple(p[P_SYMBOLS + s * P_ROW:P_SYMBOLS + s * P_ROW + REELS]) for s in range(STOPS))
    weights = tuple(tuple(p[P_WEIGHTS + s * P_ROW:P_WEIGHTS + s * P_ROW + REELS]) for s in range(STOPS))
    if p[P_RECORD_COUNT] != RECORD_COUNT:
        raise ProfileError(Problem.PERSONALITY, f"{p[P_RECORD_COUNT]} paytable records")
    payouts = tuple(_unpack_record(p[P_RECORDS + i * RECORD_SIZE:P_RECORDS + (i + 1) * RECORD_SIZE], i)
                    for i in range(RECORD_COUNT))

    def signed_long(at: int) -> int:
        return int.from_bytes(p[at:at + 4], "big", signed=True)

    patterns = []
    for row in range(LINE_ROWS):
        start, step = signed_long(P_ROW_START + 4 * row), signed_long(P_ROW_STEP + 4 * row)
        patterns.append(tuple(start + step * (reel + 1) for reel in range(REELS)))
    # Only groups the installed records use are known to be configured groups.
    codes = {s for payout in payouts for s in payout.symbols if s & GROUP_SYMBOL}
    groups = {}
    for code in sorted(codes):
        at = P_GROUPS + (code & ~GROUP_SYMBOL) * GROUP_SIZE
        groups[code] = frozenset(p[at + 1:at + 1 + p[at]])
    stock = Profile(p[P_LINES], p[P_MAX_BET], weights, payouts)
    game = Game(strips, MappingProxyType(groups), tuple(patterns), stock)
    _validate(stock, game, Caps())
    return game


@functools.cache
def installed_game() -> Game:
    return load_game(PERSONALITY.read_bytes())


def stock_profile() -> Profile:
    """The installed personality's own settings as a profile."""
    return installed_game().stock


def validate(profile: Profile, **caps: int) -> None:
    """Raise ProfileError unless profile is valid within caps, keyword fields of Caps."""
    _validate(profile, installed_game(), Caps(**caps))


def encode(profile: Profile, **caps: int) -> bytes:
    validate(profile, **caps)
    weights = bytes(w for row in profile.weights for w in row)
    records = b"".join(_pack_record(payout, i) for i, payout in enumerate(profile.payouts))
    header = MAGIC + bytes((VERSION, profile.lines, profile.max_bet_per_line, RECORD_COUNT))
    return header + weights + bytes(RECORDS_AT - RESERVED_AT) + records


def decode(data: bytes, **caps: int) -> Profile:
    if len(data) != PROFILE_SIZE:
        raise ProfileError(Problem.SIZE, f"{len(data)} bytes, not {PROFILE_SIZE}")
    if data[:len(MAGIC)] != MAGIC:
        raise ProfileError(Problem.MAGIC, data[:len(MAGIC)].hex())
    version, lines, max_bet, count = data[len(MAGIC):WEIGHTS_AT]
    if version != VERSION:
        raise ProfileError(Problem.VERSION, str(version))
    if count != RECORD_COUNT:
        raise ProfileError(Problem.RECORD_COUNT, str(count))
    if any(data[RESERVED_AT:RECORDS_AT]):
        raise ProfileError(Problem.RESERVED, data[RESERVED_AT:RECORDS_AT].hex())
    weights = tuple(tuple(data[WEIGHTS_AT + s * REELS:WEIGHTS_AT + (s + 1) * REELS]) for s in range(STOPS))
    payouts = tuple(_unpack_record(data[RECORDS_AT + i * RECORD_SIZE:RECORDS_AT + (i + 1) * RECORD_SIZE], i)
                    for i in range(RECORD_COUNT))
    profile = Profile(lines, max_bet, weights, payouts)
    validate(profile, **caps)
    return profile


PROFILE_FIELDS = ("version", "lines", "max_bet_per_line", "weights", "payouts")
PAYOUT_FIELDS = ("award", "symbols", "min_bet", "max_bet", "flags", "line_mask")


def to_json(profile: Profile) -> dict:
    return {
        "version": VERSION,
        "lines": profile.lines,
        "max_bet_per_line": profile.max_bet_per_line,
        "weights": [list(row) for row in profile.weights],
        "payouts": [{"award": p.award, "symbols": list(p.symbols), "min_bet": p.min_bet,
                     "max_bet": p.max_bet, "flags": p.flags, "line_mask": p.line_mask}
                    for p in profile.payouts],
    }


def from_json(obj: object, **caps: int) -> Profile:
    fields = _object(obj, PROFILE_FIELDS, "profile")
    if _int(fields["version"], 0, None, Problem.VERSION, "version") != VERSION:
        raise ProfileError(Problem.VERSION, str(fields["version"]))
    payouts = []
    for i, item in enumerate(_list(fields["payouts"], "payouts")):
        record = _object(item, PAYOUT_FIELDS, f"payout {i}")
        payouts.append(Payout(record["award"], tuple(_list(record["symbols"], f"payout {i} symbols")),
                              record["min_bet"], record["max_bet"], record["flags"], record["line_mask"]))
    profile = Profile(fields["lines"], fields["max_bet_per_line"],
                      tuple(tuple(_list(row, "weights row")) for row in _list(fields["weights"], "weights")),
                      tuple(payouts))
    validate(profile, **caps)
    return profile


def write_json(profile: Profile, path: Path, **caps: int) -> None:
    validate(profile, **caps)
    Path(path).write_text(json.dumps(to_json(profile), indent=2) + "\n")


def read_json(path: Path, **caps: int) -> Profile:
    return from_json(json.loads(Path(path).read_text(), object_pairs_hook=_unique_keys,
                                parse_constant=_reject_constant), **caps)


def score(profile: Profile, stops: tuple[int, ...], bet_per_line: int, game: Game | None = None) -> SpinScore:
    """Score the reels stopped at stops (one zero-based stop per reel) at bet_per_line.

    Each enabled line reads its pattern's symbols and takes the first paytable record, in
    order, whose line mask includes the line, whose wager limits include bet_per_line and
    whose symbols match; a zero-award match ends the line unpaid.
    """
    game = installed_game() if game is None else game
    _validate(profile, game, Caps())
    _int(bet_per_line, 1, profile.max_bet_per_line, Problem.WAGER, "bet per line")
    stops = tuple(stops)
    if len(stops) != REELS or any(type(stop) is not int or not 0 <= stop < STOPS for stop in stops):
        raise ValueError(f"stops {stops} are not {REELS} stops in 0..{STOPS - 1}")
    return _scorer(profile, bet_per_line, game)(stops)


def line_symbols(game: Game, stops: tuple[int, ...], pattern: tuple[int, ...]) -> tuple[int, ...]:
    """Symbols a line with pattern's stop offsets shows when the reels stop at stops."""
    return tuple(game.strips[(stop + offset) % STOPS][reel]
                 for reel, (stop, offset) in enumerate(zip(stops, pattern)))


def preview(profile: Profile) -> tuple[PreviewRow, ...]:
    """Nominal results for each bet per line, 1..profile.max_bet_per_line."""
    game = installed_game()
    validate(profile)
    totals = [sum(row[reel] for row in profile.weights) for reel in range(REELS)]
    outcome_mass = prod(totals)
    # (stops, mass) for every stop combination that can occur
    outcomes = []
    for stops in product(range(STOPS), repeat=REELS):
        mass = prod(profile.weights[stop][reel] for reel, stop in enumerate(stops))
        if mass:
            outcomes.append((stops, mass))
    progressive = [payout.flags in PROGRESSIVE_FLAGS for payout in profile.payouts]
    rows = []
    for low, high in _bet_ranges(profile):
        score_stops = _scorer(profile, low, game)  # every bet in the range scores alike
        hit_mass = award_mass = progressive_mass = 0
        record_masses = [0] * len(profile.payouts)
        for stops, mass in outcomes:
            result = score_stops(stops)
            hit_mass += mass if result.total_award else 0  # a zero-award match is not a hit
            award_mass += mass * result.total_award
            progressive_mass += mass * sum(award for index, award in result.lines
                                           if index is not None and progressive[index])
            for index in {index for index, award in result.lines if award}:
                record_masses[index] += mass
        rows.extend(PreviewRow(bet, bet * profile.lines, outcome_mass, hit_mass, award_mass, progressive_mass,
                               tuple(record_masses))
                    for bet in range(low, high))
    return tuple(rows)


def build_catalogue(profile: Profile, bet_per_line: int,
                    patterns: tuple[tuple[str, tuple[int, ...]], ...]) -> Catalogue:
    """Stops for every paytable record that can win at bet_per_line, and for each pattern.

    patterns are (name, codes) pairs with unique names, codes one symbol or group code per
    reel for the center line. Every stop combination is scored once, whatever its weight,
    since an armed outcome need not be one the weights can produce. A record is keyed by its index when it wins a line with
    a nonzero award, so a record shadowed by an earlier one, a zero award or a record whose
    wager limits exclude the bet has no entry, and a record off the center line is found on
    the line it wins. A pattern is keyed by its name when the center line shows it, by the
    paytable's match rule. Of the stops that land a key, the choice is the first by: no line
    paying other than the keyed line (the record's winning line, or the center line for a
    pattern), then the lowest total award, then the lowest stops.

    Catalogues are cached per profile encoding, bet and patterns.
    """
    return _catalogue(encode(profile), bet_per_line, tuple((name, tuple(codes)) for name, codes in patterns))


@functools.lru_cache(maxsize=CATALOGUE_CACHE)
def _catalogue(encoding: bytes, bet_per_line: int,
               patterns: tuple[tuple[str, tuple[int, ...]], ...]) -> Catalogue:
    game = installed_game()
    profile = decode(encoding)
    _int(bet_per_line, 1, profile.max_bet_per_line, Problem.WAGER, "bet per line")
    for name, codes in patterns:
        if type(name) is not str:
            raise TypeError(f"pattern name {name!r} is not a str")
        if len(codes) != REELS or any(code not in game.symbols for code in codes):
            raise ValueError(f"pattern {name!r} is not {REELS} of the game's symbol or group codes")
    if len({name for name, _ in patterns}) != len(patterns):
        raise ValueError(f"pattern names {[name for name, _ in patterns]} repeat")
    score_stops = _scorer(profile, bet_per_line, game)
    center = game.line_patterns[CENTER]
    best: dict[int | str, tuple[tuple, StopChoice]] = {}

    def offer(key: int | str, line: int, paying: set[int], choice: StopChoice) -> None:
        rank = (bool(paying - {line}), choice.score.total_award, choice.stops)
        if key not in best or rank < best[key][0]:
            best[key] = rank, choice

    for stops in product(range(STOPS), repeat=REELS):
        choice = StopChoice(stops, score_stops(stops))
        paying = {line for line, (_, award) in enumerate(choice.score.lines) if award}
        for line in paying:
            offer(choice.score.lines[line][0], line, paying, choice)
        shown = line_symbols(game, stops, center)
        for name, codes in patterns:
            if _matches(game, codes, shown):
                offer(name, CENTER, paying, choice)
    keys = [*range(len(profile.payouts)), *(name for name, _ in patterns)]
    return MappingProxyType({key: best[key][1] for key in keys if key in best})


def _scorer(profile: Profile, bet_per_line: int, game: Game) -> Callable[[tuple[int, ...]], SpinScore]:
    """score() for one valid profile and bet, remembering each line's first match per symbols."""
    patterns = game.line_patterns[:profile.lines]
    eligible = [[(index, payout) for index, payout in enumerate(profile.payouts)
                 if payout.line_mask >> line & 1 and payout.min_bet <= bet_per_line <= payout.max_bet]
                for line in range(profile.lines)]
    first_match: dict[tuple[int, tuple[int, ...]], tuple[int | None, int]] = {}  # few distinct symbols
    total_bet = bet_per_line * profile.lines

    def score_stops(stops: tuple[int, ...]) -> SpinScore:
        lines = []
        for line, pattern in enumerate(patterns):
            symbols = line_symbols(game, stops, pattern)
            key = line, symbols
            if key not in first_match:
                first_match[key] = next(((index, payout.award) for index, payout in eligible[line]
                                         if _matches(game, payout.symbols, symbols)), (None, 0))
            lines.append(first_match[key])
        return SpinScore(tuple(lines), sum(award for _, award in lines), total_bet)

    return score_stops


def _validate(profile: Profile, game: Game, caps: Caps) -> None:
    if not isinstance(profile, Profile):
        raise ProfileError(Problem.TYPE, f"profile is {type(profile).__name__}")
    lines = _int(profile.lines, 1, min(caps.max_lines, len(game.line_patterns)), Problem.LINES, "lines")
    max_bet = _int(profile.max_bet_per_line, 1, caps.max_bet_per_line, Problem.MAX_BET, "max_bet_per_line")
    if max_bet * lines > caps.max_total_bet:
        raise ProfileError(Problem.TOTAL_BET, f"{max_bet} x {lines} lines exceeds {caps.max_total_bet}")
    _tuple(profile.weights, STOPS, Problem.WEIGHTS, "weights")
    for row in profile.weights:
        for weight in _tuple(row, REELS, Problem.WEIGHTS, "weights row"):
            _int(weight, 0, BYTE_MAX, Problem.WEIGHTS, "weight")
    for reel in range(REELS):
        if not any(row[reel] for row in profile.weights):
            raise ProfileError(Problem.WEIGHT_TOTAL, f"reel {reel} weights are all zero")
    symbols = game.symbols
    for i, payout in enumerate(_tuple(profile.payouts, RECORD_COUNT, Problem.RECORD_COUNT, "payouts")):
        if not isinstance(payout, Payout):
            raise ProfileError(Problem.TYPE, f"payout {i} is {type(payout).__name__}")
        _int(payout.award, 0, caps.max_award, Problem.AWARD, f"payout {i} award")
        for symbol in _tuple(payout.symbols, REELS, Problem.SYMBOL, f"payout {i} symbols"):
            if _int(symbol, 0, BYTE_MAX, Problem.SYMBOL, f"payout {i} symbol") not in symbols:
                raise ProfileError(Problem.SYMBOL, f"payout {i} symbol {symbol:#04x} is not the game's")
        low = _int(payout.min_bet, 1, max_bet, Problem.WAGER, f"payout {i} min_bet")
        _int(payout.max_bet, low, max_bet, Problem.WAGER, f"payout {i} max_bet")
        if _int(payout.flags, 0, BYTE_MAX, Problem.FLAGS, f"payout {i} flags") not in FLAGS:
            raise ProfileError(Problem.FLAGS, f"payout {i} flags {payout.flags:#04x}")
        _int(payout.line_mask, 1, (1 << lines) - 1, Problem.LINE_MASK, f"payout {i} line_mask")
        if payout.flags in PROGRESSIVE_FLAGS and payout.line_mask != CENTER_LINE:
            raise ProfileError(Problem.PROGRESSIVE_LINE, f"payout {i} is progressive off the center line")
    # Bounds a spin's total award, which the firmware accumulates in an unsigned long.
    if max(p.award for p in profile.payouts) * lines > AWARD_MAX:
        raise ProfileError(Problem.AWARD_OVERFLOW, f"{lines} lines of the largest award exceed {AWARD_MAX}")
    for low, _ in _bet_ranges(profile):
        if not any(p.min_bet <= low <= p.max_bet for p in profile.payouts):
            raise ProfileError(Problem.UNCOVERED_BET, f"no payout accepts a bet of {low} per line")


def _bet_ranges(profile: Profile) -> list[tuple[int, int]]:
    """Half-open ranges of bets per line within which the same payouts are eligible."""
    edges = {1, profile.max_bet_per_line + 1}
    for payout in profile.payouts:
        edges.update((payout.min_bet, payout.max_bet + 1))
    edges = sorted(edge for edge in edges if edge <= profile.max_bet_per_line + 1)
    return list(zip(edges, edges[1:]))  # noqa: RUF007 -- itertools.pairwise needs 3.10; the stdlib suites run on macOS's python3 3.9


def _matches(game: Game, wanted: tuple[int, ...], seen: tuple[int, ...]) -> bool:
    return all(seen_symbol in game.groups[want] if want & GROUP_SYMBOL else seen_symbol == want
               for want, seen_symbol in zip(wanted, seen))


def _pack_record(payout: Payout, index: int) -> bytes:
    slots = bytes(payout.symbols) + bytes((UNUSED_SYMBOL,)) * (SYMBOL_SLOTS - REELS)
    tail = bytes((payout.max_bet, payout.min_bet, payout.flags, payout.line_mask, REELS, index, 0))
    return payout.award.to_bytes(4, "big") + slots + tail


def _unpack_record(raw: bytes, index: int) -> Payout:
    unused = raw[4 + REELS:4 + SYMBOL_SLOTS]
    if (any(s != UNUSED_SYMBOL for s in unused) or raw[13] != REELS or raw[14] != index
            or raw[15] != 0):
        raise ProfileError(Problem.RECORD_LAYOUT, f"record {index}: {raw.hex(' ')}")
    return Payout(int.from_bytes(raw[:4], "big"), tuple(raw[4:4 + REELS]), raw[10], raw[9], raw[11], raw[12])


def _int(value: object, low: int, high: int | None, problem: Problem, what: str) -> int:
    if type(value) is not int:  # rejects bool, a subclass of int
        raise ProfileError(Problem.TYPE, f"{what} is {type(value).__name__}, not int")
    if value < low or (high is not None and value > high):
        raise ProfileError(problem, f"{what} {value} is outside {low}..{high}")
    return value


def _tuple(value: object, length: int, problem: Problem, what: str) -> tuple:
    if type(value) is not tuple:
        raise ProfileError(Problem.TYPE, f"{what} is {type(value).__name__}, not tuple")
    if len(value) != length:
        raise ProfileError(problem, f"{what} has {len(value)} entries, not {length}")
    return value


def _list(value: object, what: str) -> list:
    if type(value) is not list:
        raise ProfileError(Problem.TYPE, f"{what} is {type(value).__name__}, not a list")
    return value


def _object(value: object, fields: tuple[str, ...], what: str) -> dict:
    if type(value) is not dict:
        raise ProfileError(Problem.TYPE, f"{what} is {type(value).__name__}, not an object")
    if set(value) != set(fields):
        raise ProfileError(Problem.FIELDS, f"{what} fields {sorted(value)} are not {sorted(fields)}")
    return value


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ProfileError(Problem.FIELDS, f"duplicate keys in {[key for key, _ in pairs]}")
    return result


def _reject_constant(name: str) -> None:
    raise ProfileError(Problem.TYPE, f"{name} is not a JSON number")
