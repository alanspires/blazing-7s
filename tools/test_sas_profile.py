import json
import unittest
from dataclasses import replace
from fractions import Fraction
from math import prod

import sas_profile as sp
from sas_profile import Payout, Problem, ProfileError
from sas_testing import TempDirTest

# notes/remote-game-config.md, "Offline math cross-check": an independent enumeration of
# the installed game. Bet -> (award mass, hit mass) over OUTCOME_MASS.
OUTCOME_MASS = 32768
STOCK_MASSES = {1: (29312, 4394), 2: (58912, 4610), 3: (89312, 4610)}
# The requested lines, in line order (center, above, below, two diagonals).
REQUESTED_LINES = ((0, 0, 0), (-1, -1, -1), (1, 1, 1), (-1, 0, 1), (1, 0, -1))
# Native record layout: symbol slots from 4, then max, min, flags, mask, match count, index, zero.
SLOTS, MATCH_COUNT, INDEX, PAD = 4, 13, 14, 15


def game() -> sp.Game:
    return sp.installed_game()


def stock() -> sp.Profile:
    return sp.stock_profile()


def landing(stops: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    """Weights under which every spin lands on stops."""
    return tuple(tuple(int(stops[reel] == stop) for reel in range(sp.REELS)) for stop in range(sp.STOPS))


def all_group_stop() -> tuple[int, int]:
    """A stop and a group symbol such that every row within one stop of it is that group's."""
    g = game()
    for code, members in g.groups.items():
        for stop in range(sp.STOPS):
            if all(g.strips[(stop + offset) % sp.STOPS][reel] in members
                   for offset in (-1, 0, 1) for reel in range(sp.REELS)):
                return stop, code
    raise AssertionError("the strips have no three rows of one group")


def filler(max_bet: int) -> Payout:
    """A payout for a symbol in no group, so it never pays where all_group_stop lands."""
    g = game()
    plain = min(s for row in g.strips for s in row if not any(s in m for m in g.groups.values()))
    return Payout(1, (plain,) * sp.REELS, 1, max_bet, 0, sp.CENTER_LINE)


def with_payout(profile: sp.Profile, index: int, **changes) -> sp.Profile:
    payouts = list(profile.payouts)
    payouts[index] = replace(payouts[index], **changes)
    return replace(profile, payouts=tuple(payouts))


class StockTest(unittest.TestCase):
    def test_personality_must_be_the_installed_image(self):
        image = bytearray(sp.PERSONALITY.read_bytes())
        image[sp.P_WEIGHTS] ^= 1
        with self.assertRaises(ProfileError) as caught:
            sp.load_game(bytes(image))
        self.assertIs(caught.exception.problem, Problem.PERSONALITY)

    def test_line_patterns_are_the_requested_lines(self):
        self.assertEqual(REQUESTED_LINES, game().line_patterns)

    def test_stock_records_and_weights_encode_as_the_roms(self):
        image = sp.PERSONALITY.read_bytes()
        encoded = sp.encode(stock())
        records = sp.RECORD_COUNT * sp.RECORD_SIZE
        self.assertEqual(image[sp.P_RECORDS:sp.P_RECORDS + records], encoded[sp.RECORDS_AT:])
        rom_weights = b"".join(image[sp.P_WEIGHTS + s * sp.P_ROW:][:sp.REELS] for s in range(sp.STOPS))
        self.assertEqual(rom_weights, encoded[sp.WEIGHTS_AT:sp.RESERVED_AT])
        header = sp.MAGIC + bytes((sp.VERSION, image[sp.P_LINES], image[sp.P_MAX_BET], sp.RECORD_COUNT))
        self.assertEqual(header, encoded[:sp.WEIGHTS_AT])

    def test_stock_preview_reproduces_the_documented_returns(self):
        rows = sp.preview(stock())
        self.assertEqual(sorted(STOCK_MASSES), [row.bet_per_line for row in rows])
        for row in rows:
            award_mass, hit_mass = STOCK_MASSES[row.bet_per_line]
            self.assertEqual(OUTCOME_MASS, row.outcome_mass)
            self.assertEqual(row.bet_per_line, row.total_bet)
            self.assertEqual(Fraction(award_mass, OUTCOME_MASS * row.bet_per_line), row.nominal_return)
            self.assertEqual(Fraction(hit_mass, OUTCOME_MASS), row.hit_frequency)

    def test_progressive_awards_are_counted_at_their_nominal_award(self):
        p = stock()
        progressive = {i for i, payout in enumerate(p.payouts) if payout.flags in sp.PROGRESSIVE_FLAGS}
        only = replace(p, payouts=tuple(replace(payout, award=1) if i not in progressive else payout
                                        for i, payout in enumerate(p.payouts)))
        for row, cheap in zip(sp.preview(p), sp.preview(only)):
            self.assertEqual(row.progressive_award_mass, cheap.progressive_award_mass)
            self.assertLessEqual(row.progressive_award_mass, row.award_mass)


class RoundTripTest(TempDirTest):
    def multiline(self) -> sp.Profile:
        p = replace(stock(), lines=sp.LINE_ROWS)
        return with_payout(p, len(p.payouts) - 1, line_mask=(1 << sp.LINE_ROWS) - 1)

    def test_binary(self):
        for profile in (stock(), self.multiline()):
            self.assertEqual(profile, sp.decode(sp.encode(profile)))
            self.assertEqual(sp.PROFILE_SIZE, len(sp.encode(profile)))

    def test_json(self):
        for profile in (stock(), self.multiline()):
            self.assertEqual(profile, sp.from_json(json.loads(json.dumps(sp.to_json(profile)))))
            self.assertEqual(sorted(sp.PROFILE_FIELDS), sorted(sp.to_json(profile)))

    def test_json_file(self):
        path = self.dir / "profile.json"
        sp.write_json(self.multiline(), path)
        self.assertEqual(self.multiline(), sp.read_json(path))


class MathTest(unittest.TestCase):
    def test_lines_pay_independently_in_record_order_and_cost_bet_times_lines(self):
        stop, group = all_group_stop()
        center = tuple(game().strips[stop])
        every_line = (1 << sp.LINE_ROWS) - 1
        exact = Payout(500, center, 1, 2, 0x10, sp.CENTER_LINE)
        for mask, paying_lines in ((every_line, sp.LINE_ROWS - 1), (0b10, 1)):
            with self.subTest(mask=mask):
                group_pay = Payout(7, (group,) * sp.REELS, 1, 2, 0, mask)
                profile = sp.Profile(sp.LINE_ROWS, 2, landing((stop,) * sp.REELS),
                                     (exact, group_pay) + (filler(2),) * (sp.RECORD_COUNT - 2))
                per_spin = exact.award + paying_lines * group_pay.award
                for row in sp.preview(profile):
                    self.assertEqual(row.bet_per_line * sp.LINE_ROWS, row.total_bet)
                    self.assertEqual(1, row.outcome_mass)
                    self.assertEqual(1, row.hit_frequency)
                    self.assertEqual(per_spin, row.award_mass)
                    self.assertEqual(exact.award, row.progressive_award_mass)
                    self.assertEqual(Fraction(per_spin, row.total_bet), row.nominal_return)

    def test_payouts_pay_only_bets_within_their_limits(self):
        stop, _ = all_group_stop()
        center = tuple(game().strips[stop])
        high = Payout(90, center, 2, 3, 0, sp.CENTER_LINE)
        low = Payout(20, center, 1, 1, 0, sp.CENTER_LINE)
        profile = sp.Profile(1, 3, landing((stop,) * sp.REELS),
                             (high, low) + (filler(3),) * (sp.RECORD_COUNT - 2))
        awards = {row.bet_per_line: row.award_mass for row in sp.preview(profile)}
        self.assertEqual({1: low.award, 2: high.award, 3: high.award}, awards)
        # An earlier eligible payout takes the line from a later one.
        widened = with_payout(profile, 0, min_bet=1)
        self.assertEqual({high.award}, {row.award_mass for row in sp.preview(widened)})

    def test_outcome_mass_is_the_product_of_reel_weight_totals(self):
        p = stock()
        heavier = replace(p, weights=tuple((2 * row[0],) + row[1:] for row in p.weights))
        totals = [sum(row[reel] for row in heavier.weights) for reel in range(sp.REELS)]
        for row, base in zip(sp.preview(heavier), sp.preview(p)):
            self.assertEqual(prod(totals), row.outcome_mass)
            self.assertEqual(base.nominal_return, row.nominal_return)
            self.assertEqual(base.hit_frequency, row.hit_frequency)


def center_mass(profile: sp.Profile, *symbol_sets: frozenset[int]) -> int:
    """Weight mass of center-line outcomes whose symbol on reel r is in symbol_sets[r]."""
    g = game()
    return prod(sum(row[reel] for stop, row in enumerate(profile.weights) if g.strips[stop][reel] in wanted)
                for reel, wanted in enumerate(symbol_sets))


class ZeroAwardTest(unittest.TestCase):
    def test_zero_awards_round_trip(self):
        p = with_payout(stock(), 0, award=0)
        self.assertEqual(p, sp.decode(sp.encode(p)))
        self.assertEqual(p, sp.from_json(json.loads(json.dumps(sp.to_json(p)))))

    def test_an_earlier_zero_match_blocks_a_later_payout_and_is_no_hit(self):
        stop, group = all_group_stop()
        center = tuple(game().strips[stop])
        zero = Payout(0, center, 1, 1, 0, sp.CENTER_LINE)
        paying = Payout(9, (group,) * sp.REELS, 1, 1, 0, sp.CENTER_LINE)
        rest = (filler(1),) * (sp.RECORD_COUNT - 2)
        weights = landing((stop,) * sp.REELS)
        (blocked,) = sp.preview(sp.Profile(1, 1, weights, (zero, paying) + rest))
        self.assertEqual((1, 0, 0), (blocked.outcome_mass, blocked.hit_mass, blocked.award_mass))
        self.assertEqual(0, blocked.nominal_return)
        (paid,) = sp.preview(sp.Profile(1, 1, weights, (paying, zero) + rest))
        self.assertEqual((1, 1, paying.award), (paid.outcome_mass, paid.hit_mass, paid.award_mass))

    def test_zeroing_a_stock_record_removes_exactly_what_it_and_later_records_paid(self):
        # Stock records 6-9 pay three-of-a-kind for each bar, then any three bars.
        p = stock()
        triples = range(6, 9)
        any_bar = p.payouts[9]
        (group,) = set(any_bar.symbols)
        bars = game().groups[group]
        each = {i: center_mass(p, *(frozenset({s}) for s in p.payouts[i].symbols)) for i in triples}
        self.assertEqual(bars, {p.payouts[i].symbols[0] for i in triples})
        all_bars = center_mass(p, *(bars,) * sp.REELS)
        mixed = all_bars - sum(each.values())
        # The last triple's record becomes a zero award for any three bars: it no longer
        # pays its own triple, and it blocks the any-bar record from paying mixed bars.
        last = triples[-1]
        zeroed = with_payout(p, last, award=0, symbols=any_bar.symbols)
        for before, after in zip(sp.preview(p), sp.preview(zeroed)):
            with self.subTest(bet=before.bet_per_line):
                lost = p.payouts[last].award * each[last] + any_bar.award * mixed
                self.assertEqual(before.outcome_mass, after.outcome_mass)
                self.assertEqual(before.award_mass - lost, after.award_mass)
                self.assertEqual(before.hit_mass - each[last] - mixed, after.hit_mass)
                self.assertEqual(Fraction(after.award_mass, after.outcome_mass * after.total_bet),
                                 after.nominal_return)


class ValidationTest(TempDirTest):
    def assertProblem(self, problem: Problem, call, *args, **kwargs):
        with self.assertRaises(ProfileError) as caught:
            call(*args, **kwargs)
        self.assertIs(problem, caught.exception.problem)

    def test_profiles(self):
        p = stock()
        lines2 = replace(p, lines=2)
        weights = [list(row) for row in p.weights]
        no_reel = tuple((0,) + row[1:] for row in p.weights)
        cases = {
            "bool lines": (replace(p, lines=True), Problem.TYPE),
            "no lines": (replace(p, lines=0), Problem.LINES),
            "too many lines": (replace(p, lines=sp.LINE_ROWS + 1), Problem.LINES),
            "float max bet": (replace(p, max_bet_per_line=3.0), Problem.TYPE),
            "zero max bet": (replace(p, max_bet_per_line=0), Problem.MAX_BET),
            "max bet over a byte": (replace(p, max_bet_per_line=sp.BYTE_MAX + 1), Problem.MAX_BET),
            "total over a byte": (replace(p, lines=sp.LINE_ROWS, max_bet_per_line=sp.BYTE_MAX),
                                  Problem.TOTAL_BET),
            "missing stop": (replace(p, weights=p.weights[:-1]), Problem.WEIGHTS),
            "short row": (replace(p, weights=(p.weights[0][:-1],) + p.weights[1:]), Problem.WEIGHTS),
            "weight over a byte": (replace(p, weights=((sp.BYTE_MAX + 1,) * sp.REELS,) + p.weights[1:]),
                                   Problem.WEIGHTS),
            "negative weight": (replace(p, weights=((-1,) * sp.REELS,) + p.weights[1:]), Problem.WEIGHTS),
            "list weights": (replace(p, weights=weights), Problem.TYPE),
            "reel never stops": (replace(p, weights=no_reel), Problem.WEIGHT_TOTAL),
            "missing record": (replace(p, payouts=p.payouts[:-1]), Problem.RECORD_COUNT),
            "extra record": (replace(p, payouts=p.payouts + p.payouts[:1]), Problem.RECORD_COUNT),
            "negative award": (with_payout(p, 0, award=-1), Problem.AWARD),
            "award over 32 bits": (with_payout(p, 0, award=sp.AWARD_MAX + 1), Problem.AWARD),
            "bool award": (with_payout(p, 0, award=True), Problem.TYPE),
            "unused symbol": (with_payout(p, 0, symbols=(sp.UNUSED_SYMBOL,) * sp.REELS), Problem.SYMBOL),
            "unconfigured group": (with_payout(p, 0, symbols=(max(game().groups) + 1,) * sp.REELS),
                                   Problem.SYMBOL),
            "two symbols": (with_payout(p, 0, symbols=p.payouts[0].symbols[:2]), Problem.SYMBOL),
            "zero min bet": (with_payout(p, 6, min_bet=0), Problem.WAGER),
            "min above max": (with_payout(p, 6, min_bet=p.payouts[6].max_bet + 1), Problem.WAGER),
            "max above profile": (with_payout(p, 6, max_bet=p.max_bet_per_line + 1), Problem.WAGER),
            "unknown flag": (with_payout(p, 6, flags=0x01), Problem.FLAGS),
            "combined flags": (with_payout(p, 6, flags=0x30), Problem.FLAGS),
            "no line": (with_payout(p, 6, line_mask=0), Problem.LINE_MASK),
            "inactive line": (with_payout(p, 6, line_mask=0b10), Problem.LINE_MASK),
            "progressive off center": (with_payout(lines2, 0, line_mask=0b11), Problem.PROGRESSIVE_LINE),
            "bet nothing pays": (replace(p, max_bet_per_line=4), Problem.UNCOVERED_BET),
            "spin overflows": (with_payout(lines2, 6, award=sp.AWARD_MAX // 2 + 1), Problem.AWARD_OVERFLOW),
            "not a profile": (sp.to_json(p), Problem.TYPE),
        }
        for name, (profile, problem) in cases.items():
            with self.subTest(name):
                self.assertProblem(problem, sp.validate, profile)

    def test_caps_narrow_the_field_widths(self):
        p = stock()
        multiline = replace(p, lines=sp.LINE_ROWS)
        largest = max(payout.award for payout in p.payouts)
        sp.validate(p, max_bet_per_line=p.max_bet_per_line, max_lines=p.lines,
                    max_total_bet=p.max_bet_per_line * p.lines, max_award=largest)
        cases = {
            "bet per line": ({"max_bet_per_line": p.max_bet_per_line - 1}, p, Problem.MAX_BET),
            "lines": ({"max_lines": sp.LINE_ROWS - 1}, multiline, Problem.LINES),
            "total bet": ({"max_total_bet": p.max_bet_per_line * sp.LINE_ROWS - 1}, multiline,
                          Problem.TOTAL_BET),
            "award": ({"max_award": largest - 1}, p, Problem.AWARD),
            "cap wider than a field": ({"max_total_bet": sp.BYTE_MAX + 1}, p, Problem.CAP),
            "cap wider than the lines": ({"max_lines": sp.LINE_ROWS + 1}, p, Problem.CAP),
            "zero cap": ({"max_award": 0}, p, Problem.CAP),
            "bool cap": ({"max_bet_per_line": True}, p, Problem.TYPE),
        }
        for name, (caps, profile, problem) in cases.items():
            with self.subTest(name):
                self.assertProblem(problem, sp.validate, profile, **caps)
                if problem is not Problem.CAP and problem is not Problem.TYPE:
                    self.assertProblem(problem, sp.decode, sp.encode(profile), **caps)
                    self.assertProblem(problem, sp.from_json, sp.to_json(profile), **caps)

    def test_binary(self):
        good = sp.encode(stock())

        def patched(at: int, value: int) -> bytes:
            data = bytearray(good)
            data[at] = value
            return bytes(data)

        version_at = len(sp.MAGIC)
        last = sp.RECORDS_AT + (sp.RECORD_COUNT - 1) * sp.RECORD_SIZE
        cases = {
            "short": (good[:-1], Problem.SIZE),
            "long": (good + b"\0", Problem.SIZE),
            "magic": (patched(0, 0), Problem.MAGIC),
            "version": (patched(version_at, sp.VERSION + 1), Problem.VERSION),
            "lines": (patched(version_at + 1, 0), Problem.LINES),
            "record count": (patched(version_at + 3, sp.RECORD_COUNT + 1), Problem.RECORD_COUNT),
            "reserved": (patched(sp.RESERVED_AT + 1, 1), Problem.RESERVED),
            "unused slot": (patched(last + SLOTS + sp.REELS, 0), Problem.RECORD_LAYOUT),
            "match count": (patched(last + MATCH_COUNT, sp.REELS - 1), Problem.RECORD_LAYOUT),
            "index": (patched(last + INDEX, 0), Problem.RECORD_LAYOUT),
            "pad": (patched(last + PAD, 1), Problem.RECORD_LAYOUT),
            "unused symbol": (patched(last + SLOTS, sp.UNUSED_SYMBOL), Problem.SYMBOL),
        }
        for name, (data, problem) in cases.items():
            with self.subTest(name):
                self.assertProblem(problem, sp.decode, data)

    def test_json(self):
        good = sp.to_json(stock())

        def changed(**fields) -> dict:
            return {**good, **fields}

        def payout_changed(**fields) -> dict:
            return changed(payouts=[{**good["payouts"][0], **fields}] + good["payouts"][1:])

        reduced = dict(good)
        del reduced["lines"]
        cases = {
            "extra field": (changed(denomination=1), Problem.FIELDS),
            "missing field": (reduced, Problem.FIELDS),
            "extra payout field": (payout_changed(index=0), Problem.FIELDS),
            "version": (changed(version=sp.VERSION + 1), Problem.VERSION),
            "bool version": (changed(version=True), Problem.TYPE),
            "weights object": (changed(weights={}), Problem.TYPE),
            "float award": (payout_changed(award=1.0), Problem.TYPE),
            "string symbol": (payout_changed(symbols=["0e"] * sp.REELS), Problem.TYPE),
            "not an object": ([good], Problem.TYPE),
        }
        for name, (obj, problem) in cases.items():
            with self.subTest(name):
                self.assertProblem(problem, sp.from_json, obj)

    def test_json_text(self):
        text = json.dumps(sp.to_json(stock()))
        cases = {
            "duplicate key": (text.replace('"lines": ', '"lines": 1, "lines": ', 1), Problem.FIELDS),
            "NaN": (text.replace('"lines": 1', '"lines": NaN', 1), Problem.TYPE),
        }
        for name, (content, problem) in cases.items():
            with self.subTest(name):
                self.assertNotEqual(text, content)
                path = self.dir / "profile.json"
                path.write_text(content)
                self.assertProblem(problem, sp.read_json, path)


if __name__ == "__main__":
    unittest.main()
