from __future__ import annotations

import hashlib
import json
import re
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path
from types import MappingProxyType

import sas_extension as se
import sas_profile as sp
import web_state as ws
import web_views as wv
from web_state import (
    Act,
    ActionResult,
    Arm,
    ArmState,
    Code,
    Family,
    Hold,
    Link,
    OutcomeWord,
    ResultState,
)

STATIC = Path(__file__).resolve().parent / "web" / "static"
NOW = datetime(2026, 11, 22, 20, 0, tzinfo=timezone.utc)
GENERATION = 7
NEXT_ID = 40
LIMIT = 9999
DOOR = "Slot door"
CAPABILITIES = int(se.Capability.PROFILE | se.Capability.OUTCOME | se.Capability.SPIN)
METERS = ws.Meters(coin_in=300, coin_out=120, jackpot=0, games_played=100, games_won=20,
                   slot_door_opened=4, power_reset=9, in_house_in_cents=500000,
                   in_house_in_count=1, in_house_out_cents=0, in_house_out_count=0)
DECK = ("bet-one", "max-bet", "spin", "cash-out", "add-credits")
OLD_MAX_BET_FIELD = "max_bet"  # the editor's max bet field before decision-18 made it a fixed reading
DECK_ACTS = MappingProxyType({"bet-one": Act.BET_ONE, "max-bet": Act.MAX_BET, "spin": Act.SPIN,
                              "cash-out": Act.CASH_OUT, "add-credits": Act.ADD_CREDITS})


def online(**changes) -> ws.MachineState:
    """A fresh online machine with credits, both transfer directions, closed doors and play on."""
    state = ws.MachineState(
        link=Link.ONLINE, fresh=True, last_reply_time="2026-11-22T19:59:00+00:00", credits=5000,
        cents_per_credit=100, meters=METERS, aft=ws.Aft(True, True, False, LIMIT),
        doors={DOOR: False}, play_enabled=True,
        profile=ws.ProfileState(False, GENERATION, sp.stock_profile()),
        ext_info=se.Info(CAPABILITIES, NEXT_ID, sp.PROFILE_SIZE, 48, 5, 3, 15, sp.STOPS, 1500),
        ext_status=se.MachineStatus(NEXT_ID - 1, se.Op.BET_SPIN, se.Status.OK, False, GENERATION, 0, False))
    return replace(state, **changes)


def armed(state: ws.MachineState) -> ws.MachineState:
    arm = Arm(ArmState.ARMED, NEXT_ID, 0, "3 Blazing 7s", (1, 2, 3), ws.chosen_bet(state))
    status = se.MachineStatus(NEXT_ID, se.Op.ARM_OUTCOME, se.Status.ACCEPTED, False, GENERATION, 0, False)
    return replace(state, arm=arm, ext_status=status)


def result(action: Act, outcome: OutcomeWord, **fields) -> ActionResult:
    return ActionResult("sub-1", action, ResultState.DONE, outcome=outcome, **fields)


def spin_action(state: ws.MachineState) -> ws.Action:
    return ws.Action(Act.SPIN, "sub-1", profile_generation=GENERATION, bet_per_line=ws.chosen_bet(state))


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None], parent: Node | None):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node | str] = []

    def text(self) -> str:
        return "".join(child if isinstance(child, str) else child.text() for child in self.children)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def find(self, tag: str | None = None, **attrs: str) -> list[Node]:
        """Descendants (and self) with this tag, if given, and these attributes; data_part
        stands for data-part."""
        return [node for node in self.walk() if (tag is None or node.tag == tag)
                and all(node.attrs.get(name.replace("_", "-")) == value for name, value in attrs.items())]

    def one(self, tag: str | None = None, **attrs: str) -> Node:
        found = self.find(tag, **attrs)
        if len(found) != 1:
            raise AssertionError(f"{len(found)} elements match {attrs}")
        return found[0]


VOID = frozenset(("area", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"))


class Tree(HTMLParser):
    def __init__(self, markup: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.at = self.root
        self.feed(markup)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.at)
        self.at.children.append(node)
        if tag not in VOID:
            self.at = node

    def handle_startendtag(self, tag, attrs):
        self.at.children.append(Node(tag, dict(attrs), self.at))

    def handle_endtag(self, tag):
        node = self.at
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.at = node.parent

    def handle_data(self, data):
        self.at.children.append(data)


def render(state: ws.MachineState, view: wv.View | None = None, **kwargs) -> Node:
    return Tree(wv.page(state, view or wv.View(), now=NOW, **kwargs)).root


def banner(root: Node) -> Node:
    """The page's banner region (the unreachable template's copy lives inside <template>)."""
    return next(node for node in root.find(data_region="banner") if node.parent.tag != "template")


def key_sub(root: Node, part: str) -> str:
    key = root.one(data_part=part)
    return next(node for node in key.walk() if node.attrs.get("class") == "key__sub").text()


class BannerTest(unittest.TestCase):
    """Each approved Play state's banner, from the snapshot alone."""

    def assert_banner(self, state: ws.MachineState, kind: str, tone_class: str | None,
                      view: wv.View | None = None):
        node = banner(render(state, view))
        self.assertEqual(node.attrs.get("data-banner"), kind)
        classes = node.attrs.get("class", "").split()
        self.assertIn("armed-tag", classes)
        tones = {c for c in classes if c in ("armed-tag--quiet", "armed-tag--ok", "armed-tag--win",
                                              "armed-tag--danger")}
        self.assertEqual(tones, set() if tone_class is None else {tone_class})

    def test_each_mock_state_has_its_banner_and_tone(self):
        state = online()
        spinning = replace(state, in_progress=spin_action(state))
        evidence = ws.SpinEvidence(3, 3, 1500, 1, 1497)
        cases = {
            "spinning": (spinning, "spinning", "armed-tag--quiet"),
            "win": (replace(state, results=(result(Act.SPIN, OutcomeWord.PLAYED, evidence=evidence),)), "win",
                    "armed-tag--win"),
            "no credits": (replace(state, credits=0), "no-credits", "armed-tag--quiet"),
            "credits added": (replace(state, results=(result(Act.ADD_CREDITS, OutcomeWord.MOVED, credits_delta=1000,
                                                              transfer_credits=1000),)),
                              "credits-added", "armed-tag--ok"),
            "cashed out": (replace(state, credits=0,
                                   results=(result(Act.CASH_OUT, OutcomeWord.MOVED, credits_delta=-5000,
                                                   transfer_credits=5000),)),
                           "cashed-out", "armed-tag--ok"),
            "checking": (replace(state, checking=MappingProxyType({Family.MONEY: Hold("no reply", 3)}),
                                 results=(ActionResult("sub-1", Act.ADD_CREDITS, ResultState.CHECKING),)),
                         "checking", None),
            "checked": (replace(state, results=(result(Act.ADD_CREDITS, OutcomeWord.HAPPENED, credits_delta=5000),)),
                        "checked", "armed-tag--ok"),
            "offline": (replace(state, link=Link.OFFLINE, fresh=False), "offline", "armed-tag--danger"),
            "door open": (replace(state, doors={DOOR: True}), "door", None),
            "armed": (armed(state), "armed", None),
        }
        for name, (case, kind, tone) in cases.items():
            with self.subTest(name):
                self.assert_banner(case, kind, tone)

    def test_held_and_unknown_next_results_offer_only_cancel(self):
        state = armed(online())
        held = replace(state, arm=replace(state.arm, state=ArmState.HELD))
        unknown = replace(state, arm=Arm(ArmState.UNKNOWN))
        for case, kind in ((held, "held"), (unknown, "arm-unknown")):
            with self.subTest(kind):
                root = render(case)
                self.assertEqual(banner(root).attrs["data-banner"], kind)
                self.assertNotIn("disabled", root.one(data_part="armed-cancel").attrs)
                self.assertIn("disabled", root.one(data_part="spin").attrs)

    def test_a_dismissed_result_leaves_the_banner_empty(self):
        state = replace(online(), results=(result(Act.CASH_OUT, OutcomeWord.MOVED, credits_delta=-5000,
                                                  transfer_credits=5000),))
        node = banner(render(state, wv.View(seen="sub-1")))
        self.assertEqual(node.attrs.get("data-banner"), "none")
        self.assertIn("hidden", node.attrs)

    def test_a_game_started_at_the_machine_spins_the_reels_until_it_ends(self):
        running = online(game_running=True)
        self.assertIsNone(running.in_progress)
        for state, spinning in ((running, True), (replace(running, game_running=False), False)):
            with self.subTest(spinning=spinning):
                root = render(state)
                reels = root.one(data_region="reels").find("div")
                self.assertEqual({"reel--spinning" in (node.attrs.get("class") or "").split() for node in reels
                                  if "reel" in (node.attrs.get("class") or "").split()}, {spinning})
                self.assertEqual(banner(root).attrs.get("data-banner") == "spinning", spinning)
                self.assertEqual("is-busy" in root.one(data_part="spin").attrs["class"].split(), spinning)
                self.assertEqual("disabled" in root.one(data_part="cash-out").attrs, spinning)

    def test_a_transfer_banner_shows_the_transfer_not_the_net_balance_change(self):
        played = 3  # credits played at the machine while the transfer ran
        for action, kind, amount, delta in ((Act.ADD_CREDITS, "credits-added", 1000, 1000 - played),
                                            (Act.CASH_OUT, "cashed-out", 5000, -5000 - played)):
            state = online(results=(result(action, OutcomeWord.MOVED, credits_delta=delta, transfer_credits=amount),))
            node = banner(render(state))
            with self.subTest(action):
                self.assertEqual(node.attrs.get("data-banner"), kind)
                headline = node.one("strong").text()
                self.assertIn(f"{amount:,}", headline)
                self.assertNotIn(f"{abs(delta):,}", headline)

    def test_a_refusal_after_admission_shows_its_code_until_dismissed(self):
        refused = ActionResult("sub-1", Act.SPIN, ResultState.REFUSED, reason="This page is out of date",
                               code=Code.OUT_OF_DATE)
        state = online(results=(refused,))
        node = banner(render(state))
        self.assertEqual((node.attrs.get("data-banner"), node.attrs.get("data-code"), node.attrs.get("data-submission")),
                         ("not-sent", Code.OUT_OF_DATE.value, refused.submission_id))
        self.assertIn("armed-tag--danger", node.attrs["class"].split())
        dismiss = node.one(data_part="not-sent-dismiss")
        seen = wv.view_for("play", dismiss.attrs["href"].split("?", 1)[1]).seen
        self.assertEqual(seen, refused.submission_id)
        self.assertEqual(banner(render(state, wv.View(seen=seen))).attrs.get("data-banner"), "none")

    def test_a_refusal_behind_a_newer_result_is_listed_for_the_page_that_submitted_it(self):
        spin = ActionResult("spin-1", Act.SPIN, ResultState.REFUSED, reason="This page is out of date",
                            code=Code.OUT_OF_DATE)
        bet = ActionResult("bet-1", Act.BET_ONE, ResultState.DONE, outcome=OutcomeWord.BET)
        state = online(results=(bet, spin))  # another page's bet came after the spin it outdated
        root = render(state)
        self.assertNotEqual(banner(root).attrs.get("data-banner"), "not-sent")  # results[0] is the bet
        status = next(node for node in root.find(data_region="status") if node.parent.tag != "template")
        listed = json.loads(status.attrs["data-refused"])
        self.assertEqual([(entry["id"], entry["code"]) for entry in listed],
                         [(spin.submission_id, Code.OUT_OF_DATE.value)])
        self.assertTrue(listed[0]["text"])
        self.assertEqual(json.loads(Tree(wv.regions(state, wv.View(page="settings"), now=NOW)["status"])
                                    .root.children[0].attrs["data-refused"]), listed)  # on every page

    def test_a_reconciled_transfer_shows_the_verified_amount_and_the_live_balance(self):
        played = 3  # credits played at the machine while the reply was lost
        credits = 5000
        for action, amount, delta in ((Act.ADD_CREDITS, 1000, 1000 - played), (Act.CASH_OUT, 4000, -4000 - played)):
            state = online(credits=credits, results=(result(action, OutcomeWord.HAPPENED, credits_delta=delta,
                                                            transfer_credits=amount),))
            node = banner(render(state))
            numbers = {whole(n) for n in re.findall(r"\d[\d,]*", node.text())}
            with self.subTest(action):
                self.assertEqual(node.attrs.get("data-banner"), "checked")
                self.assertIn(amount, numbers)
                self.assertIn(credits, numbers)  # the live balance, shown apart from the transfer
                self.assertNotIn(credits - delta, numbers)  # never a balance made up from the delta

    def test_a_machine_refusal_claims_nothing_changed_only_when_answered_before_any_change(self):
        for status, kind in ((se.Status.BUSY, "refused"), (se.Status.FAULT, "refused-unconfirmed")):
            refused = result(Act.RESTORE_STOCK, OutcomeWord.REFUSED, reason="why", machine_status=status)
            node = banner(render(online(results=(refused,))))
            with self.subTest(status.name):
                self.assertEqual((node.attrs.get("data-banner"), node.attrs.get("data-status")),
                                 (kind, status.name))

    def test_only_a_spin_that_stopped_shows_the_spin_interrupted_banner(self):
        for act, outcome, kind in ((Act.SPIN, OutcomeWord.INTERRUPTED, "interrupted"),
                                   (Act.SPIN, OutcomeWord.FAULT, "interrupted"),
                                   (Act.RESTORE_STOCK, OutcomeWord.INTERRUPTED, "none"),
                                   (Act.RESTORE_STOCK, OutcomeWord.FAULT, "none")):
            with self.subTest(act, outcome=outcome):
                self.assertEqual(banner(render(online(results=(result(act, outcome),)))).attrs.get("data-banner"), kind)

    def test_server_unreachable_banner_ships_for_live_js(self):
        root = render(online())
        template = root.one("template", id="unreachable")
        regions = {node.attrs.get("data-region"): node for node in template.walk() if "data-region" in node.attrs}
        self.assertEqual(set(regions), {"status", "banner"})
        self.assertIn("armed-tag--danger", regions["banner"].attrs["class"].split())
        self.assertEqual(regions["banner"].attrs["data-banner"], "unreachable")


class DeckTest(unittest.TestCase):
    def assert_deck_follows_admit(self, state: ws.MachineState):
        root = render(state)
        for part in DECK:
            refusal = ws.admit(state, ws.Action(DECK_ACTS[part], "", profile_generation=GENERATION,
                                                bet_per_line=ws.chosen_bet(state),
                                                arm_id=ws.arm_identity(state)))
            key = root.one(data_part=part)
            with self.subTest(part):
                if refusal is None:
                    self.assertNotIn("disabled", key.attrs)
                else:
                    self.assertEqual(key.tag, "button")
                    self.assertIn("disabled", key.attrs)
                    if "is-busy" in key.attrs["class"].split():
                        continue  # Spin while spinning shows the wager in play, as designed
                    expected = wv.SHORT[refusal.code]
                    self.assertTrue(key_sub(root, part).startswith(expected), key_sub(root, part))

    def test_keys_and_reasons_follow_admit_in_each_state(self):
        state = online()
        for name, case in {
            "ready": state,
            "offline": replace(state, link=Link.OFFLINE, fresh=False),
            "door open": replace(state, doors={DOOR: True}, aft=ws.Aft(False, False, False, LIMIT)),
            "no credits": replace(state, credits=0),
            "spinning": replace(state, in_progress=spin_action(state)),
            "game at the machine": replace(state, game_running=True),
            "checking money": replace(state, checking=MappingProxyType({Family.MONEY: Hold("no reply")})),
            "play off": replace(state, play_enabled=False),
        }.items():
            with self.subTest(name):
                self.assert_deck_follows_admit(case)

    def test_no_credits_keeps_bet_selection_and_makes_add_credits_the_main_key(self):
        root = render(online(credits=0))
        for part in ("bet-one", "max-bet"):
            self.assertNotIn("disabled", root.one(data_part=part).attrs)
        self.assertIn("disabled", root.one(data_part="spin").attrs)
        self.assertIn("key--main", root.one(data_part="add-credits").attrs["class"].split())

    def test_add_credits_shows_the_machines_own_limit(self):
        limit = 2500
        state = online(aft=ws.Aft(True, True, False, limit))
        self.assertIn(f"{limit:,}", key_sub(render(state), "add-credits"))

    def test_a_game_at_the_machine_spins_without_a_wager_even_before_the_game_is_read(self):
        starting = ws.MachineState(game_running=True)  # a 7E polled before the first full refresh
        self.assertIsNone(starting.profile)
        for view in RegionsTest.VIEWS:
            with self.subTest(page=view.page, sheet=view.sheet):
                wv.regions(starting, view, now=NOW)
                render(starting, view)
        self.assertEqual(key_sub(render(starting), "spin"), "")
        cabinet = online(bet_per_line=2, game_running=True)
        self.assertEqual(key_sub(render(cabinet), "spin"), "")  # the cabinet's wager is not the page's bet
        page_spin = replace(cabinet, game_running=False, in_progress=spin_action(cabinet))
        self.assertIn(str(ws.chosen_bet(cabinet)), key_sub(render(page_spin), "spin"))
        recovered = replace(page_spin, in_progress=ws.Action(Act.SPIN, "sub-1"))  # journal names no bet
        self.assertEqual(key_sub(render(recovered), "spin"), "")

    def test_every_refusal_code_has_a_short_reason(self):
        self.assertEqual(set(wv.SHORT), set(Code))


def whole(text: str) -> int:
    return int(text.replace(",", ""))


def odds_text(profile: sp.Profile, bet: int, index: int) -> str:
    row = next(row for row in sp.preview(profile) if row.bet_per_line == bet)
    return f"1\xa0in\xa0{round(Fraction(row.outcome_mass, row.record_masses[index])):,}"


def percent(text: str) -> tuple[Fraction, Fraction]:
    """A shown percentage as a fraction, with half its last digit's unit: its rounding bound."""
    number = text.strip().rstrip("%")
    places = len(number.partition(".")[2])
    return Fraction(number) / 100, Fraction(1, 2 * 10 ** places) / 100


class OddsLinesTest(unittest.TestCase):
    """The design's '1 in N' lines: how often each paytable record pays, from preview()."""

    def test_next_result_shows_the_whole_spin_total_on_a_multi_line_profile(self):
        stock = sp.stock_profile()
        every_line = (1 << sp.LINE_ROWS) - 1
        profile = replace(stock, lines=sp.LINE_ROWS, payouts=tuple(
            payout if payout.flags in sp.PROGRESSIVE_FLAGS else replace(payout, line_mask=every_line)
            for payout in stock.payouts))
        state = online(profile=ws.ProfileState(True, GENERATION, profile))
        bet = ws.chosen_bet(state)
        root = render(state, wv.View(sheet="next-result"))
        other_lines_pay = []
        for outcome in ws.catalogue(state):
            if not outcome.available:
                continue
            total = sp.score(profile, outcome.stops, bet).total_award
            combo = next(node for node in root.find("button") if node.attrs.get("value") == str(outcome.id))
            pays = next(node for node in combo.walk() if "combo__pays" in (node.attrs.get("class") or "").split())
            with self.subTest(outcome.name):
                if total:
                    self.assertEqual(whole(pays.text()), total)
                else:
                    self.assertIn("combo__pays--none", pays.attrs["class"].split())
            if outcome.kind is ws.Kind.WIN and total != profile.payouts[outcome.id].award:
                other_lines_pay.append(outcome.id)
        self.assertTrue(other_lines_pay)  # some choice's stops pay on more than its own line

    def test_next_result_wins_show_their_odds_at_the_chosen_bet(self):
        state = online(bet_per_line=2)
        root = render(state, wv.View(sheet="next-result"))
        wins = [o for o in ws.catalogue(state) if o.kind is ws.Kind.WIN and o.available]
        self.assertTrue(wins)
        for outcome in wins:
            combo = next(node for node in root.find("button") if node.attrs.get("value") == str(outcome.id))
            with self.subTest(outcome.name):
                self.assertIn(odds_text(state.profile.profile, 2, outcome.id), combo.text())

    def test_paytable_rows_show_the_odds_of_their_top_bet_record(self):
        state = online()
        profile = state.profile.profile
        root = render(state, wv.View(page="odds"))
        listed = [node.text() for node in root.walk() if node.attrs.get("class") == "paycombo__odds"]
        top = profile.max_bet_per_line
        expected = [odds_text(profile, top, index) for index, payout in enumerate(profile.payouts)
                    if payout.min_bet <= top <= payout.max_bet and payout.award]
        self.assertEqual(expected, listed)


class DisabledControlsTest(unittest.TestCase):
    def test_disabled_controls_are_real_disabled_buttons(self):
        state = online()
        for case in (replace(state, link=Link.OFFLINE, fresh=False),
                     replace(state, doors={DOOR: True}, aft=ws.Aft(False, False, False, LIMIT)),
                     replace(state, checking=MappingProxyType({Family.MONEY: Hold("no reply")}))):
            for view in (wv.View(), wv.View(sheet="next-result"), wv.View(page="odds"),
                         wv.View(page="settings")):
                root = render(case, view)
                with self.subTest(link=case.link, view=view.page, sheet=view.sheet):
                    for node in root.walk():
                        if "disabled" in node.attrs:
                            self.assertIn(node.tag, ("button", "input"))
                        if node.tag == "a":
                            self.assertNotIn("aria-disabled", node.attrs)

    def test_offline_disables_every_machine_control(self):
        root = render(online(link=Link.OFFLINE, fresh=False), wv.View(sheet="next-result"))
        controls = [node for node in root.find("button") if node.attrs.get("type") == "submit"]
        self.assertTrue(controls)
        for node in controls + [root.one(data_part="add-credits"), root.one(data_part="next-result-toggle")]:
            self.assertEqual(node.tag, "button")
            self.assertIn("disabled", node.attrs, node.attrs.get("data-part"))


class FormsTest(unittest.TestCase):
    def hidden_fields(self, root: Node, form_id: str) -> dict[str, str]:
        form = next(node for node in root.walk() if node.tag == "form" and node.attrs.get("id") == form_id)
        self.assertEqual(form.attrs["method"], "post")
        return {node.attrs["name"]: node.attrs["value"] for node in form.walk()
                if node.tag == "input" and node.attrs.get("type") == "hidden"}

    def test_forms_carry_the_expected_state_admit_compares(self):
        state = armed(online(bet_per_line=2))
        root = render(state, wv.View(sheet="next-result"))
        expected = {wv.F_GENERATION: str(GENERATION), wv.F_BET: str(ws.chosen_bet(state)),
                    wv.F_ARM: str(NEXT_ID)}
        for act, form_id in ((Act.SPIN, "act-spin"), (Act.ARM, "act-arm"), (Act.CANCEL_ARM, "act-cancel-arm")):
            with self.subTest(act):
                fields = self.hidden_fields(root, form_id)
                for name in wv.EXPECTED[act]:
                    self.assertEqual(fields[name], expected[name])

    def test_forms_post_to_the_action_routes(self):
        root = render(armed(online()), wv.View(sheet="next-result"))
        routes = {node.attrs["action"] for node in root.walk() if node.tag == "form" and "action" in node.attrs}
        self.assertTrue(routes)
        for route in routes:
            self.assertTrue(route.startswith("/actions/"), route)
            Act(route[len("/actions/"):])

    def test_a_rendered_spin_form_is_admitted_until_another_page_changes_the_bet(self):
        state = online()
        fields = self.hidden_fields(render(state), "act-spin")
        action = wv.action_from_form(Act.SPIN.value, "sub-9", fields, state)
        self.assertIsNone(ws.admit(state, action))
        changed = replace(state, bet_per_line=ws.next_bet(state, Act.BET_ONE))
        self.assertEqual(ws.admit(changed, action).code, Code.OUT_OF_DATE)

    def test_action_from_form_reads_amount_and_switch_targets(self):
        state = online()
        top_up = wv.action_from_form(Act.ADD_CREDITS.value, "s", {wv.F_AMOUNT: "1,000"}, state)
        self.assertEqual(top_up.params[wv.F_AMOUNT], 1000)
        self.assertIs(wv.action_from_form(Act.SOUND.value, "s", {wv.F_ON: "0"}, state).params[wv.F_ON], False)
        with self.assertRaises(wv.FormError):
            wv.action_from_form(Act.SOUND.value, "s", {wv.F_ON: "maybe"}, state)
        with self.assertRaises(wv.FormError):
            wv.action_from_form("launch", "s", {}, state)

    def test_over_limit_amount_shows_the_error_and_disables_the_submit(self):
        state = online()
        over = LIMIT + 1
        root = render(state, wv.View(sheet="add-credits", amount=str(over)))
        self.assertTrue(root.find("p", **{"class": "field-error"}))
        submit = root.one(data_part="add-credits-submit")
        self.assertIn("disabled", submit.attrs)
        refusal = ws.admit(state, ws.Action(Act.ADD_CREDITS, "", MappingProxyType({"amount": over})))
        self.assertEqual(refusal.code, Code.OVER_LIMIT)
        self.assertEqual(root.one("input", id="credit-amount").attrs["aria-invalid"], "true")

    def test_a_refused_top_up_returns_to_the_sheet_with_its_amount(self):
        state = online()
        action = wv.action_from_form(Act.ADD_CREDITS.value, "s", {wv.F_AMOUNT: str(LIMIT + 1)}, state)
        refusal = ws.admit(state, action)
        target = wv.redirect_after("/?rail=hidden", action, refusal)
        view = wv.view_for("play", target.split("?", 1)[1])
        self.assertEqual((view.sheet, view.amount, view.rail_hidden), ("add-credits", str(LIMIT + 1), True))
        self.assertEqual(wv.redirect_after("/settings", action, None), "/settings")
        for unsafe in ("https://elsewhere.example/", "//elsewhere.example/", None):
            self.assertEqual(wv.redirect_after(unsafe, action, None), "/")


class EscapingTest(unittest.TestCase):
    def test_machine_and_journal_values_are_escaped(self):
        hostile = '<script>alert("x")</script>'
        state = online(doors={hostile: True}, aft=ws.Aft(False, False, False, LIMIT),
                       log=({"seq": 1, "time": "2026-11-22T19:00:00+00:00", "kind": "event", "source": "machine",
                             "event": "exception", "code": 99, "name": hostile},),
                       checking=MappingProxyType({Family.PLAY: Hold(hostile)}))
        for view in (wv.View(), wv.View(page="activity"), wv.View(page="settings")):
            with self.subTest(view.page):
                markup = wv.page(state, view, now=NOW)
                self.assertNotIn("<script>", markup)
        markup = wv.page(online(), wv.View(sheet="add-credits", amount=hostile), now=NOW)
        self.assertNotIn("<script>", markup)


class RegionsTest(unittest.TestCase):
    VIEWS = (wv.View(), wv.View(sheet="next-result"), wv.View(sheet="add-credits"), wv.View(sheet="details"),
             wv.View(rail_hidden=True), wv.View(page="odds"), wv.View(page="activity"), wv.View(page="settings"))

    def page_regions(self, root: Node) -> dict[str, Node]:
        found = {}
        for node in root.walk():
            if "data-region" in node.attrs and not any(p.tag == "template" for p in self.parents(node)):
                self.assertNotIn(node.attrs["data-region"], found)
                found[node.attrs["data-region"]] = node
        return found

    @staticmethod
    def parents(node: Node):
        while node.parent is not None:
            node = node.parent
            yield node

    def test_regions_match_the_page_and_stay_stable_across_states(self):
        states = (online(), online(link=Link.OFFLINE, fresh=False), online(credits=0),
                  replace(online(), in_progress=spin_action(online())), armed(online()))
        for view in self.VIEWS:
            ids = None
            for state in states:
                regions = wv.regions(state, view, now=NOW)
                with self.subTest(page=view.page, sheet=view.sheet, link=state.link):
                    self.assertEqual(set(regions), set(self.page_regions(render(state, view))))
                    for name, markup in regions.items():
                        element = Tree(markup).root.children[0]
                        self.assertEqual(element.attrs.get("data-region"), name)
                    ids = set(regions) if ids is None else ids
                    self.assertEqual(set(regions), ids)

    def test_view_round_trips_through_its_query(self):
        drafted = wv.View(page="odds", draft=MappingProxyType({"award-10": "3"}), generation=GENERATION - 1)
        for view in self.VIEWS + (wv.View(page="activity", before=40), wv.View(seen="sub-1"), drafted):
            with self.subTest(view):
                self.assertEqual(wv.view_for(None, wv.view_query(view)), view)
        with self.assertRaises(ValueError):
            wv.view_for("admin", "")


class ActivityTest(unittest.TestCase):
    SECRET = "c0ffee5ec2e7"  # stands in for request bytes an intent carries

    def entries(self) -> list[dict]:
        base = {"time": "2026-11-22T19:30:00+00:00", "source": "page"}
        return [
            {**base, "seq": 6, "kind": "spin", "bet_per_line": 3, "total_bet": 3, "stops": [0, 1, 2],
             "paid": 1500, "credits": 6497, "name": "3 Blazing 7s"},
            {**base, "seq": 5, "kind": "outcome", "intent": 4, "action": Act.ADD_CREDITS.value, "result": OutcomeWord.MOVED.value,
             "transfer": {"direction": "in", "credits": 5000}},
            {**base, "seq": 4, "kind": "intent", "action": "add-credits", "request_hex": self.SECRET},
            {**base, "seq": 3, "kind": "step", "upload_id": "u", "op": "BEGIN_PROFILE", "request_hex": self.SECRET},
            {**base, "seq": 2, "kind": "event", "source": "machine", "event": "door_open", "door": DOOR, "code": 17},
            {**base, "seq": 1, "kind": "page", "event": "started"},
        ]

    def test_activity_lists_records_but_never_intents_or_steps(self):
        self.assertNotIn("intent", ws.ACTIVITY_KINDS)
        self.assertNotIn("step", ws.ACTIVITY_KINDS)
        markup = wv.page(online(), wv.View(page="activity"), now=NOW, entries=self.entries())
        self.assertNotIn(self.SECRET, markup)
        root = Tree(markup).root
        listed = [node for node in root.walk() if node.attrs.get("class") == "log__entry"]
        self.assertEqual(len(listed), len([e for e in self.entries() if e["kind"] in ws.ACTIVITY_KINDS]))
        self.assertFalse(root.find(data_part="load-earlier"))

    def test_a_full_page_offers_earlier_entries_from_the_last_shown_seq(self):
        records = [{"seq": seq, "time": "2026-11-22T19:00:00+00:00", "kind": "event", "source": "machine",
                    "event": "door_closed", "door": DOOR} for seq in range(wv.ACTIVITY_PAGE + 1, 0, -1)]
        root = render(online(), wv.View(page="activity"), entries=records)
        earlier = root.one(data_part="load-earlier")
        self.assertEqual(wv.view_for("activity", earlier.attrs["href"].split("?", 1)[1]).before,
                         records[wv.ACTIVITY_PAGE - 1]["seq"])

    def test_empty_log_shows_the_empty_state(self):
        root = render(online(), wv.View(page="activity"), entries=[])
        self.assertTrue(root.find(data_part="empty-go-play"))

    def test_rail_shows_recent_activity_from_the_snapshot_log(self):
        root = render(online(log=tuple(self.entries())))
        items = [node for node in root.walk() if node.attrs.get("class") == "mini-log__item"]
        self.assertEqual(len(items), min(wv.RAIL_ENTRIES, 4))


class OddsTest(unittest.TestCase):
    def test_stock_game_without_edits_disables_apply_and_restore(self):
        root = render(online(), wv.View(page="odds"))
        for part in ("apply-odds", "restore-stock"):
            self.assertIn("disabled", root.one(data_part=part).attrs)
        self.assertEqual(root.one(data_region="odds-status").attrs["data-tone"], "quiet")

    def test_a_draft_marks_its_changes_and_enables_apply(self):
        stock = sp.stock_profile()
        field = "w-7-0"
        changed = stock.weights[7][0] + 1
        # A draft carries the generation its form was rendered with, as the editor's form does.
        root = render(online(), wv.View(page="odds", draft=MappingProxyType({field: str(changed)}),
                                        generation=GENERATION))
        self.assertNotIn("disabled", root.one(data_part="apply-odds").attrs)
        weight = next(node for node in root.walk() if node.attrs.get("name") == field)
        self.assertEqual(weight.attrs["value"], str(changed))
        self.assertIn("is-changed", weight.parent.parent.parent.attrs["class"].split())
        preview = root.one(data_region="preview")
        self.assertIn("Edited", preview.text())

    def test_a_draft_the_machine_would_refuse_is_flagged_and_not_sent(self):
        state = online()
        too_many = {wv.F_LINES: str(state.ext_info.max_lines + 1)}
        root = render(state, wv.View(page="odds", draft=MappingProxyType(too_many)))
        self.assertEqual(root.one(data_region="odds-status").attrs["data-tone"], "danger")
        self.assertIn("disabled", root.one(data_part="apply-odds").attrs)
        with self.assertRaises(wv.FormError) as caught:
            wv.action_from_form(Act.APPLY_PROFILE.value, "s", too_many, state)
        self.assertIs(caught.exception.code, Code.INVALID_PROFILE)

    def test_max_bet_per_line_is_a_fixed_reading_of_the_active_profile(self):
        state = online()
        top = state.profile.max_bet_per_line
        limits = render(state, wv.View(page="odds")).one(data_part="max-bet-per-line")
        self.assertNotEqual(limits.tag, "input")
        self.assertIn(str(top), limits.text())
        editor = Tree(wv.regions(state, wv.View(page="odds"), now=NOW)["editor"]).root
        named = {node.attrs["name"] for node in editor.find("input") if node.attrs.get("name")}
        # weights and awards, plus only these: no field for the max bet
        self.assertEqual({name for name in named if not re.fullmatch(r"w-\d+-\d+|award-\d+", name)},
                         {wv.F_GENERATION, wv.F_RETURN, wv.F_LINES})

    def test_a_posted_max_bet_never_changes_the_draft(self):
        state = online()
        top = state.profile.max_bet_per_line
        fields = {OLD_MAX_BET_FIELD: str(top - 1), "award-10": "3", wv.F_GENERATION: str(GENERATION)}
        action = wv.action_from_form(Act.APPLY_PROFILE.value, "s", fields, state)
        self.assertEqual(action.params["profile"].max_bet_per_line, top)
        self.assertIsNone(ws.admit(state, action))
        self.assertEqual(wv.parse_draft(state, fields).profile.max_bet_per_line, top)
        self.assertNotIn(OLD_MAX_BET_FIELD, wv.view_for("odds", f"{OLD_MAX_BET_FIELD}={top - 1}&award-10=3").draft)

    def test_max_total_bet_follows_the_paylines_at_the_fixed_max_bet(self):
        state = online()
        top = state.profile.max_bet_per_line
        for lines in (1, state.ext_info.max_lines):
            with self.subTest(lines=lines):
                root = render(state, wv.View(page="odds", draft=MappingProxyType({wv.F_LINES: str(lines)})))
                total = root.one(data_part="max-total-bet")
                self.assertEqual(whole(total.text().split()[0]), lines * top)
                self.assertEqual(int(total.attrs["data-per-line"]), top)  # what live.js multiplies

    def test_applied_draft_becomes_the_apply_action_profile(self):
        state = online()
        fields = {"award-10": "3", wv.F_GENERATION: str(GENERATION)}
        action = wv.action_from_form(Act.APPLY_PROFILE.value, "s", fields, state)
        self.assertEqual(action.params["profile"].payouts[10].award, 3)
        self.assertIsNone(ws.admit(state, action))

    def test_preview_regions_follow_the_draft(self):
        regions = wv.preview_regions(online(), {"award-10": "3"})
        self.assertEqual(set(regions), {"odds-actions", "odds-status", "preview"})
        self.assertIn("Edited", Tree(regions["preview"]).root.text())

    def test_preview_figures_are_the_drafts_own_preview(self):
        stock = sp.stock_profile()
        stop, reel = 7, 0
        weight = min(stock.weights[stop][reel] + 50, sp.BYTE_MAX)
        weights = tuple(tuple(weight if (s, r) == (stop, reel) else w for r, w in enumerate(row))
                        for s, row in enumerate(stock.weights))
        edited = replace(stock, weights=weights)
        regions = wv.preview_regions(online(), {f"w-{stop}-{reel}": str(weight), wv.F_GENERATION: str(GENERATION)})
        panel = Tree(regions["preview"]).root
        figures = {"return": lambda row: row.nominal_return, "hit-rate": lambda row: row.hit_frequency}
        for figure, exact in figures.items():
            cells = [node.text() for node in panel.one("table", data_figure=figure).walk()
                     if "preview__edited" in (node.attrs.get("class") or "").split()]
            rows = sp.preview(edited)
            self.assertEqual(len(cells), len(rows))
            for text, row, stock_row in zip(cells, rows, sp.preview(stock)):
                with self.subTest(figure, bet=row.bet_per_line):
                    shown, half_unit = percent(text)
                    self.assertLessEqual(abs(shown - exact(row)), half_unit)
                    self.assertGreater(abs(shown - exact(stock_row)), half_unit)  # not the stock figure

    def test_a_draft_kept_across_a_profile_change_shows_apply_out_of_date(self):
        drafted_at = online()
        now = online(profile=ws.ProfileState(True, GENERATION + 1, sp.stock_profile()))
        fields = {"award-10": "3", wv.F_GENERATION: str(ws.profile_generation(drafted_at))}
        apply = Tree(wv.preview_regions(now, fields)["odds-actions"]).root.one(data_part="apply-odds")
        self.assertIn("disabled", apply.attrs)
        self.assertEqual(apply.attrs.get("data-code"), Code.OUT_OF_DATE.value)
        # the apply POST itself is refused the same way
        action = wv.action_from_form(Act.APPLY_PROFILE.value, "s", fields, now)
        self.assertIs(ws.admit(now, action).code, Code.OUT_OF_DATE)
        rebased = {**fields, wv.F_GENERATION: str(ws.profile_generation(now))}
        apply = Tree(wv.preview_regions(now, rebased)["odds-actions"]).root.one(data_part="apply-odds")
        self.assertNotIn("disabled", apply.attrs)

    def test_the_editor_names_the_verified_apply_that_committed_the_active_profile(self):
        verified = ActionResult("sub-2", Act.APPLY_PROFILE, ResultState.DONE, OutcomeWord.COMMITTED_VERIFIED,
                                generation=GENERATION)
        others = (ActionResult("sub-3", Act.APPLY_PROFILE, ResultState.DONE, OutcomeWord.ABORTED),
                  ActionResult("sub-4", Act.APPLY_PROFILE, ResultState.RUNNING))
        state = online(results=(*others, verified))
        editor = render(state, wv.View(page="odds")).one(data_region="editor")
        self.assertEqual(editor.attrs["data-applied"], verified.submission_id)

    def test_an_own_apply_another_profile_replaced_is_not_named_so_the_draft_stays_out_of_date(self):
        mine = ActionResult("sub-2", Act.APPLY_PROFILE, ResultState.DONE, OutcomeWord.COMMITTED_VERIFIED,
                            generation=GENERATION)
        theirs = ActionResult("sub-5", Act.APPLY_PROFILE, ResultState.DONE, OutcomeWord.COMMITTED_VERIFIED,
                              generation=GENERATION + 1)
        newer = online(profile=ws.ProfileState(True, GENERATION + 1, sp.stock_profile()), results=(theirs, mine))
        editor = render(newer, wv.View(page="odds")).one(data_region="editor")
        self.assertNotIn(mine.submission_id, editor.attrs["data-applied"].split())  # live.js keeps its draft
        draft = {"award-10": "3", wv.F_GENERATION: str(mine.generation)}  # the kept draft's form
        apply = Tree(wv.preview_regions(newer, draft)["odds-actions"]).root.one(data_part="apply-odds")
        self.assertIn("disabled", apply.attrs)
        self.assertEqual(apply.attrs.get("data-code"), Code.OUT_OF_DATE.value)

    def test_a_no_script_preview_of_an_old_draft_keeps_its_generation_and_shows_apply_out_of_date(self):
        drafted_at = GENERATION
        now = online(profile=ws.ProfileState(True, GENERATION + 1, sp.stock_profile()))
        # The no-script Preview submits the editor form by GET to /odds, hidden generation included.
        view = wv.view_for("odds", f"award-10=3&{wv.F_GENERATION}={drafted_at}&{wv.F_RETURN}=%2Fodds")
        for root in (render(now, view), Tree("".join(wv.regions(now, view, now=NOW).values())).root):
            apply = root.one(data_part="apply-odds")
            form = root.one("form", id="odds-editor")
            generation = next(node for node in form.find("input") if node.attrs.get("name") == wv.F_GENERATION)
            with self.subTest(whole_page=root.find("html") != []):
                self.assertIn("disabled", apply.attrs)
                self.assertEqual(apply.attrs.get("data-code"), Code.OUT_OF_DATE.value)
                self.assertEqual(generation.attrs["value"], str(drafted_at))
        current = wv.view_for("odds", f"award-10=3&{wv.F_GENERATION}={ws.profile_generation(now)}")
        self.assertNotIn("disabled", render(now, current).one(data_part="apply-odds").attrs)

    def test_a_restore_the_machine_stopped_is_shown_as_unfinished(self):
        custom = ws.ProfileState(True, GENERATION, sp.stock_profile())
        for outcome in (OutcomeWord.INTERRUPTED, OutcomeWord.FAULT):
            state = online(profile=custom, results=(result(Act.RESTORE_STOCK, outcome),))
            status = render(state, wv.View(page="odds")).one(data_region="odds-status")
            with self.subTest(outcome):
                self.assertEqual(status.attrs["data-tone"], "danger")
        restored = online(results=(result(Act.RESTORE_STOCK, OutcomeWord.RESTORED),))
        self.assertEqual(render(restored, wv.View(page="odds")).one(data_region="odds-status").attrs["data-tone"], "ok")

    def test_an_unfinished_restore_is_told_with_an_odds_draft_open_and_the_draft_kept(self):
        custom = ws.ProfileState(True, GENERATION, sp.stock_profile())
        state = online(profile=custom, results=(result(Act.RESTORE_STOCK, OutcomeWord.INTERRUPTED),))
        drafts = {"changed": {"award-10": "3"}, "invalid": {wv.F_LINES: str(state.ext_info.max_lines + 1)}}
        for name, draft in drafts.items():
            fields = {**draft, wv.F_GENERATION: str(GENERATION)}
            view = wv.View(page="odds", draft=MappingProxyType(draft), generation=GENERATION)
            page_root = render(state, view)
            preview = wv.preview_regions(state, fields)  # live.js's path while odds-status is protected
            for where, status, actions in (
                    ("page", page_root.one(data_region="odds-status"), page_root),
                    ("preview", Tree(preview["odds-status"]).root.children[0], Tree(preview["odds-actions"]).root)):
                with self.subTest(name, where=where):
                    self.assertEqual(status.attrs.get("data-result"), "restore-unfinished")
                    # the draft is kept: Apply still follows it
                    self.assertEqual("disabled" in actions.one(data_part="apply-odds").attrs, name == "invalid")
            kept = {node.attrs["name"]: node.attrs["value"] for node in page_root.find("input")
                    if node.attrs.get("name") in draft}
            self.assertEqual(kept, draft)

    def test_a_restore_the_machine_said_no_to_shows_its_status_and_claims_nothing_after_a_fault(self):
        custom = ws.ProfileState(True, GENERATION, sp.stock_profile())
        for status, kind in ((se.Status.BUSY, "restore-refused"), (se.Status.FAULT, "restore-unfinished")):
            refused = result(Act.RESTORE_STOCK, OutcomeWord.REFUSED, reason="why", machine_status=status)
            notice = render(online(profile=custom, results=(refused,)), wv.View(page="odds")).one(
                data_region="odds-status")
            with self.subTest(status.name):
                self.assertEqual((notice.attrs.get("data-result"), notice.attrs.get("data-status")),
                                 (kind, status.name))
                self.assertEqual(notice.attrs["data-tone"], "danger")

    def test_a_pending_restore_shows_the_fault_the_machine_reports_while_checking(self):
        held = MappingProxyType({Family.EXTENSION: Hold("checking", 5)})
        pending = se.MachineStatus(NEXT_ID, se.Op.RESTORE_STOCK, se.Status.ACCEPTED, True, GENERATION, 0, True)
        waiting = (ActionResult("sub-1", Act.RESTORE_STOCK, ResultState.CHECKING),)
        for fault in (True, False):
            state = online(checking=held, results=waiting,
                           ext_status=replace(pending, fault=fault))
            odds = render(state, wv.View(page="odds")).one(data_region="odds-status")
            play = banner(render(state))
            with self.subTest(fault=fault):
                self.assertEqual(play.attrs.get("data-banner"), "checking")  # still pending, not terminal
                self.assertEqual(odds.attrs["data-tone"], "warn")
                self.assertEqual("data-fault" in odds.attrs, fault)
                self.assertEqual("data-fault" in play.attrs, fault)

    def test_a_refused_apply_or_restore_shows_not_sent_with_its_code(self):
        draft = MappingProxyType({"award-10": "3"})
        for act, code in ((Act.APPLY_PROFILE, Code.JOURNAL), (Act.RESTORE_STOCK, Code.CHECKING)):
            refused = ActionResult("sub-1", act, ResultState.REFUSED, reason="why", code=code)
            state = online(results=(refused,))
            for view in (wv.View(page="odds"), wv.View(page="odds", draft=draft)):
                status = render(state, view).one(data_region="odds-status")
                with self.subTest(act, drafting=view.draft is not None):
                    self.assertEqual((status.attrs.get("data-result"), status.attrs.get("data-code")),
                                     ("not-sent", code.value))
                    self.assertEqual(status.attrs["data-tone"], "danger")
        state = online(results=(ActionResult("sub-1", Act.SPIN, ResultState.REFUSED, code=Code.BUSY),))
        self.assertNotIn("data-result", render(state, wv.View(page="odds")).one(data_region="odds-status").attrs)


class SettingsTest(unittest.TestCase):
    def test_unknown_setting_offers_both_targets(self):
        state = online(settings=ws.Settings(sound=ws.Setting.UNKNOWN, bills=ws.Setting.ON))
        root = render(state, wv.View(page="settings"))
        on, off = root.one(data_part="sound-on"), root.one(data_part="sound-off")
        self.assertEqual((on.attrs["value"], off.attrs["value"]), ("1", "0"))
        bills = root.one(data_part="bills-toggle")
        self.assertEqual(bills.attrs["value"], "0")  # a tap turns the known-on setting off

    def test_a_refused_setting_shows_not_sent_with_its_code_on_its_row(self):
        rows = {Act.PLAY: "setting-play", Act.SOUND: "setting-sound", Act.BILLS: "setting-bills",
                Act.CLOCK_SYNC: "setting-clock"}
        for act, row in rows.items():
            refused = ActionResult("sub-1", act, ResultState.REFUSED, reason="why", code=Code.JOURNAL)
            root = render(online(results=(refused,)), wv.View(page="settings"))
            with self.subTest(act):
                marked = root.find(data_result="not-sent")
                self.assertEqual(len(marked), 1)
                self.assertEqual(marked[0].attrs.get("data-code"), Code.JOURNAL.value)
                self.assertIn(marked[0], list(root.one("div", id=row).walk()))

    def test_a_setting_being_applied_is_pending(self):
        state = online(settings=ws.Settings(sound=ws.Setting.ON),
                       in_progress=ws.Action(Act.SOUND, "s", MappingProxyType({"on": False})))
        switch = render(state, wv.View(page="settings")).one(data_part="sound-toggle")
        self.assertIn("is-pending", switch.attrs["class"].split())
        self.assertIn("disabled", switch.attrs)


class StaticAssetsTest(unittest.TestCase):
    def rendered_icons(self) -> set[str]:
        states = (online(), online(link=Link.OFFLINE, fresh=False), online(credits=0), armed(online()),
                  online(doors={DOOR: True}), online(settings=ws.Settings(sound=ws.Setting.UNKNOWN)))
        views = RegionsTest.VIEWS
        icons = set()
        for state in states:
            for view in views:
                icons.update(re.findall(r'/static/icons/([a-z0-9-]+)\.svg', wv.page(state, view, now=NOW)))
        return icons

    def test_every_icon_a_page_uses_ships_with_its_licence_notice(self):
        for name in self.rendered_icons():
            with self.subTest(name):
                svg = (STATIC / "icons" / f"{name}.svg").read_text()
                self.assertTrue(svg.startswith("<!-- @license lucide-static"), name)
        self.assertIn("ISC License", (STATIC / "icons" / "LICENSE-lucide.txt").read_text())

    def test_stylesheet_fonts_ship_with_licences_pinned_to_their_bytes(self):
        css = (STATIC / "styles.css").read_text()
        fonts = re.findall(r'url\("fonts/([^"]+\.woff2)"\)', css)
        self.assertTrue(fonts)
        notice = (STATIC / "fonts" / "README.md").read_text()
        for name in fonts:
            with self.subTest(name):
                digest = hashlib.sha256((STATIC / "fonts" / name).read_bytes()).hexdigest()
                self.assertRegex(notice, rf"`{re.escape(name)}` \| `{digest}`")
        ofl = (STATIC / "fonts" / "OFL.txt").read_text()
        self.assertIn('Reserved Font Name "Lato"', ofl)
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", ofl)
        self.assertIn("As a special exception", (STATIC / "fonts" / "LICENSE-URW-base35.txt").read_text())
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", (STATIC / "fonts" / "COPYING-AGPL-3.0.txt").read_text())

    def test_page_assets_exist(self):
        markup = wv.page(online(), wv.View(), now=NOW)
        for asset in re.findall(r'(?:href|src)="/static/([^"]+)"', markup):
            with self.subTest(asset):
                self.assertTrue((STATIC / asset).is_file())


if __name__ == "__main__":
    unittest.main()
