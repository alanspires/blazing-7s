from __future__ import annotations

import json
import unittest
from dataclasses import replace
from types import MappingProxyType

import sas_extension as se
import sas_profile as sp
import web_state as ws
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
    ResultState,
)

ALL_CAPABILITIES = int(se.Capability.PROFILE | se.Capability.OUTCOME | se.Capability.SPIN)
NEXT_ID = 40
GENERATION = 7
LIMIT = 9999
DOOR = "Slot door"
METERS = ws.Meters(coin_in=300, coin_out=120, jackpot=0, games_played=100, games_won=20,
                   slot_door_opened=4, power_reset=9, in_house_in_cents=500000,
                   in_house_in_count=1, in_house_out_cents=0, in_house_out_count=0)


def info(capabilities: int = ALL_CAPABILITIES, next_id: int = NEXT_ID) -> se.Info:
    return se.Info(capabilities, next_id, sp.PROFILE_SIZE, 48, 5, 3, 15, sp.STOPS, 1500)


def status(last_id: int = NEXT_ID - 1, op: se.Op = se.Op.BET_SPIN,
           result: se.Status = se.Status.OK) -> se.MachineStatus:
    return se.MachineStatus(last_id, op, result, False, GENERATION, 0, False)


def online(profile: sp.Profile | None = None, **changes) -> ws.MachineState:
    """A fresh online machine with credits, both transfer directions, closed doors and play on."""
    state = ws.MachineState(
        link=Link.ONLINE, fresh=True, last_reply_time="2026-11-22T12:00:00Z", credits=100,
        cents_per_credit=100, meters=METERS, aft=ws.Aft(True, True, False, LIMIT),
        doors={DOOR: False}, play_enabled=True,
        profile=ws.ProfileState(False, GENERATION, profile or sp.stock_profile()),
        ext_info=info(), ext_status=status())
    return replace(state, **changes)


def action(state: ws.MachineState, name: Act, **params) -> Action:
    """name with the expected values a page rendered from state."""
    return Action(name, "submission-1", MappingProxyType(params),
                  profile_generation=state.profile.generation, bet_per_line=ws.chosen_bet(state),
                  arm_id=ws.arm_identity(state))


def code(state: ws.MachineState, name: Act, **params) -> Code | None:
    refusal = ws.admit(state, action(state, name, **params))
    return None if refusal is None else refusal.code


def armed(state: ws.MachineState, arm_state: ArmState = ArmState.ARMED, bet: int | None = None,
          last: se.MachineStatus | None = None) -> ws.MachineState:
    """state with a next result armed as the extension's id NEXT_ID."""
    arm = Arm(arm_state, NEXT_ID, 0, "armed", (1, 2, 3), ws.chosen_bet(state) if bet is None else bet)
    return replace(state, arm=arm,
                   ext_status=last or status(NEXT_ID, se.Op.ARM_OUTCOME, se.Status.ACCEPTED))


def shadowed_profile() -> sp.Profile:
    """Stock with its any-seven record first, so the 3 Blazing 7s record (now 3) never wins."""
    payouts = list(sp.stock_profile().payouts)
    payouts[0], payouts[3] = payouts[3], payouts[0]
    return replace(sp.stock_profile(), payouts=tuple(payouts))


class AdmitTest(unittest.TestCase):
    def test_fresh_online_machine_admits_its_actions(self):
        state = online()
        for name, params in ((Act.BET_ONE, {}), (Act.MAX_BET, {}), (Act.SPIN, {}),
                             (Act.ARM, {"outcome": "0"}), (Act.ADD_CREDITS, {"amount": 10}),
                             (Act.CASH_OUT, {}), (Act.APPLY_PROFILE, {}), (Act.RESTORE_STOCK, {}),
                             (Act.PLAY, {"on": False}), (Act.SOUND, {"on": True}),
                             (Act.BILLS, {"on": True}), (Act.CLOCK_SYNC, {})):
            with self.subTest(name):
                self.assertIsNone(code(state, name, **params))

    def test_offline_or_stale_refuses_everything(self):
        for state, expected in ((online(link=Link.OFFLINE), Code.OFFLINE),
                                (online(link=Link.STARTING), Code.OFFLINE),
                                (online(fresh=False), Code.STALE)):
            for name in Act:
                with self.subTest(state.link, fresh=state.fresh, name=name):
                    refusal = ws.admit(state, action(state, name, amount=1))
                    self.assertEqual(refusal.code, expected)
                    self.assertEqual(refusal.submission_id, "submission-1")

    def test_checking_family_refuses_only_its_own_actions(self):
        money = online(checking=MappingProxyType({Family.MONEY: Hold("lost reply", 12)}))
        self.assertEqual(code(money, Act.ADD_CREDITS, amount=10), Code.CHECKING)
        self.assertEqual(code(money, Act.CASH_OUT), Code.CHECKING)
        self.assertIsNone(code(money, Act.SPIN))
        extension = online(checking=MappingProxyType({Family.EXTENSION: Hold("lost reply", 13)}))
        for name in (Act.SPIN, Act.ARM, Act.APPLY_PROFILE, Act.RESTORE_STOCK):
            self.assertEqual(code(extension, name), Code.CHECKING)
        self.assertIsNone(code(extension, Act.BET_ONE))
        self.assertIsNone(code(extension, Act.ADD_CREDITS, amount=10))

    def test_small_balance_at_default_max_bet_keeps_bet_one_usable(self):
        profile = sp.stock_profile()
        state = online(credits=profile.max_bet_per_line * profile.lines - 1)
        self.assertEqual(ws.chosen_bet(state), profile.max_bet_per_line)
        self.assertEqual(code(state, Act.SPIN), Code.CREDITS)
        self.assertIsNone(code(state, Act.BET_ONE))
        self.assertIsNone(code(state, Act.MAX_BET))
        smaller = replace(state, bet_per_line=ws.next_bet(state, Act.BET_ONE))
        self.assertEqual(ws.chosen_bet(smaller), 1)
        self.assertIsNone(code(smaller, Act.SPIN))
        self.assertEqual(ws.next_bet(smaller, Act.MAX_BET), profile.max_bet_per_line)

    def test_no_credits_blocks_spin_and_cash_out_but_not_bet_selection(self):
        state = online(credits=0)
        self.assertEqual(code(state, Act.SPIN), Code.NO_CREDITS)
        self.assertEqual(code(state, Act.CASH_OUT), Code.NO_CREDITS)
        self.assertIsNone(code(state, Act.BET_ONE))
        self.assertIsNone(code(state, Act.ADD_CREDITS, amount=10))

    def test_multi_line_profile_needs_bet_per_line_times_lines(self):
        profile = replace(sp.stock_profile(), lines=3)
        wager = profile.max_bet_per_line * profile.lines
        short = online(profile, credits=wager - 1)
        self.assertEqual(ws.total_wager(short), wager)
        self.assertEqual(code(short, Act.SPIN), Code.CREDITS)
        self.assertIsNone(code(online(profile, credits=wager), Act.SPIN))
        lower = replace(short, bet_per_line=profile.max_bet_per_line - 1)
        self.assertIsNone(code(lower, Act.SPIN))

    def test_spin_needs_capability_play_and_no_action_in_progress(self):
        no_spin = online(ext_info=info(ALL_CAPABILITIES & ~se.Capability.SPIN))
        self.assertEqual(code(no_spin, Act.SPIN), Code.UNSUPPORTED)
        self.assertEqual(code(no_spin, Act.BET_ONE), Code.UNSUPPORTED)
        no_ids = online(ext_info=info(next_id=0))
        self.assertEqual(code(no_ids, Act.SPIN), Code.UNSUPPORTED)
        self.assertIsNone(code(no_ids, Act.BET_ONE))  # selecting a bet sends nothing
        paused = online(play_enabled=False)
        self.assertEqual(code(paused, Act.SPIN), Code.PAUSED)
        self.assertEqual(code(paused, Act.MAX_BET), Code.PAUSED)
        busy = online(in_progress=Action(Act.SPIN, "submission-0"))
        for name in (Act.SPIN, Act.BET_ONE, Act.CASH_OUT, Act.APPLY_PROFILE):
            self.assertEqual(code(busy, name), Code.BUSY)
        self.assertIsNone(code(busy, Act.ADD_CREDITS, amount=10))

    def test_a_game_started_at_the_machine_holds_play_and_cash_out_like_a_page_spin(self):
        running = online(game_running=True)
        for name in (Act.SPIN, Act.BET_ONE, Act.MAX_BET, Act.CASH_OUT, Act.APPLY_PROFILE):
            with self.subTest(name):
                self.assertEqual(code(running, name), Code.BUSY)
        self.assertIsNone(code(running, Act.ADD_CREDITS, amount=10))

    def test_doors_block_play_only_once_known_open(self):
        self.assertIsNone(code(online(doors=None), Act.SPIN))  # no baseline: unknown, not open
        self.assertIsNone(code(online(doors={DOOR: False}), Act.SPIN))
        door_open = online(doors={DOOR: True})
        for name in (Act.SPIN, Act.BET_ONE, Act.MAX_BET, Act.ARM):
            self.assertEqual(code(door_open, name), Code.DOOR_OPEN)
        # AFT's own availability decides transfers; an open door only names the reason.
        self.assertIsNone(code(door_open, Act.ADD_CREDITS, amount=10))
        unavailable = ws.Aft(False, False, False, LIMIT)
        refusal = ws.admit(replace(door_open, aft=unavailable), action(door_open, Act.ADD_CREDITS))
        self.assertEqual(refusal.code, Code.DOOR_OPEN)
        self.assertIn(DOOR, refusal.reason)
        self.assertEqual(code(online(aft=unavailable), Act.ADD_CREDITS), Code.AFT_UNAVAILABLE)
        self.assertEqual(code(online(doors=None, aft=unavailable), Act.CASH_OUT), Code.AFT_UNAVAILABLE)

    def test_page_with_another_view_is_out_of_date(self):
        state = online()
        current = action(state, Act.SPIN)
        for stale in (replace(current, profile_generation=GENERATION + 1),
                      replace(current, bet_per_line=ws.chosen_bet(state) - 1),
                      replace(current, arm_id=NEXT_ID)):
            self.assertEqual(ws.admit(state, stale).code, Code.OUT_OF_DATE)
        apply = replace(action(state, Act.APPLY_PROFILE), profile_generation=GENERATION - 1)
        self.assertEqual(ws.admit(state, apply).code, Code.OUT_OF_DATE)
        # A spin rendered without the arm another page set would bind to that arm.
        self.assertEqual(ws.admit(armed(state), current).code, Code.OUT_OF_DATE)

    def test_add_credits_within_the_machine_limit(self):
        state = online()
        self.assertIsNone(code(state, Act.ADD_CREDITS, amount=LIMIT))
        self.assertEqual(code(state, Act.ADD_CREDITS, amount=LIMIT + 1), Code.OVER_LIMIT)
        self.assertEqual(code(state, Act.ADD_CREDITS, amount=0), Code.BAD_AMOUNT)
        self.assertIsNone(code(state, Act.ADD_CREDITS))  # without an amount: top-ups possible

    def test_deposit_only_availability(self):
        state = online(aft=ws.Aft(True, False, False, LIMIT))
        self.assertIsNone(code(state, Act.ADD_CREDITS, amount=10))
        self.assertEqual(code(state, Act.CASH_OUT), Code.AFT_UNAVAILABLE)
        locked = online(aft=ws.Aft(True, True, True, LIMIT))
        self.assertEqual(code(locked, Act.ADD_CREDITS, amount=10), Code.AFT_UNAVAILABLE)
        self.assertEqual(code(locked, Act.CASH_OUT), Code.AFT_UNAVAILABLE)

    def test_armed_next_result_hands_off_only_to_cancel_and_its_spin(self):
        state = armed(online())
        self.assertIsNone(code(state, Act.SPIN))
        self.assertIsNone(code(state, Act.CANCEL_ARM))
        for name in (Act.ARM, Act.APPLY_PROFILE, Act.RESTORE_STOCK):
            self.assertEqual(code(state, name), Code.ARM_SET)
        other_bet = armed(online(), bet=ws.chosen_bet(online()) - 1)
        self.assertEqual(code(other_bet, Act.SPIN), Code.ARM_BET)

    def test_unsettled_next_result_can_only_be_cancelled(self):
        unknown = armed(online(), ArmState.UNKNOWN)
        held = armed(online(), ArmState.HELD,
                     last=status(NEXT_ID + 1, se.Op.BET_SPIN, se.Status.INTERRUPTED))
        uncorrelated = replace(armed(online()), arm=Arm())  # STATUS shows an arm the page never set
        for state in (unknown, held):
            self.assertEqual(code(state, Act.SPIN), Code.ARM_UNSETTLED)
            self.assertIsNone(code(state, Act.CANCEL_ARM))
        self.assertEqual(code(uncorrelated, Act.SPIN), Code.ARM_UNSETTLED)
        self.assertEqual(code(online(), Act.CANCEL_ARM), Code.NOTHING_SET)

    def test_accepted_spin_blocks_extension_mutations(self):
        state = online(ext_status=status(NEXT_ID, se.Op.BET_SPIN, se.Status.ACCEPTED))
        for name in (Act.SPIN, Act.ARM, Act.APPLY_PROFILE):
            self.assertEqual(code(state, name), Code.BUSY)
        self.assertIsNone(code(state, Act.ADD_CREDITS, amount=10))

    def test_arm_needs_an_available_catalogue_entry(self):
        state = online(shadowed_profile())
        self.assertEqual(code(state, Act.ARM, outcome="3"), Code.OUTCOME_UNAVAILABLE)
        self.assertEqual(code(state, Act.ARM, outcome="no-such-outcome"), Code.OUTCOME_UNAVAILABLE)
        self.assertIsNone(code(state, Act.ARM, outcome="0"))
        self.assertIsNone(code(state, Act.ARM, outcome=ws.PATTERNS[0][0]))


class PublicViewTest(unittest.TestCase):
    def test_projection_carries_what_a_script_needs(self):
        profile = replace(sp.stock_profile(), lines=3)
        bet = profile.max_bet_per_line - 1
        state = armed(online(profile, bet_per_line=bet))
        state = replace(state, checking=MappingProxyType({Family.MONEY: Hold("lost reply", 12)}))
        evidence = ws.SpinEvidence(bet, bet * profile.lines, 20, 1, 20 - bet * profile.lines)
        state = ws.with_result(state, ActionResult("spin-1", Act.SPIN, ResultState.QUEUED))
        state = ws.with_result(state, ActionResult("refused-1", Act.CASH_OUT, ResultState.REFUSED,
                                                   reason="Checking what happened", code=Code.CHECKING))
        state = ws.with_result(state, ActionResult("spin-1", Act.SPIN, ResultState.DONE, outcome=OutcomeWord.PLAYED,
                                                   credits_delta=evidence.credits_delta, evidence=evidence))
        view = json.loads(json.dumps(ws.public_view(state)))  # JSON-ready
        self.assertEqual((view["link"], view["fresh"]), (Link.ONLINE.value, True))
        self.assertEqual(view["credits"], state.credits)
        self.assertEqual(view["meters"]["games_played"], METERS.games_played)
        self.assertEqual(view["meters"]["in_house_in_cents"], METERS.in_house_in_cents)
        self.assertEqual((view["bet_per_line"], view["lines"]), (bet, profile.lines))
        self.assertEqual(view["total_wager"], bet * profile.lines)
        self.assertEqual(view["profile_generation"], GENERATION)
        self.assertEqual(view["arm"]["arm_id"], NEXT_ID)
        self.assertEqual(view["spin"], {"allowed": True, "code": None, "reason": None})
        self.assertTrue(view["top_up"]["available"])
        self.assertEqual(view["top_up"]["limit_credits"], LIMIT)
        self.assertEqual(view["top_up"]["code"], Code.CHECKING.value)
        self.assertEqual(view["checking"][Family.MONEY.value]["intent_seq"], 12)
        results = {r["submission_id"]: r for r in view["results"]}
        self.assertEqual(list(results), ["refused-1", "spin-1"])
        self.assertEqual(results["refused-1"]["code"], Code.CHECKING.value)
        self.assertEqual(results["spin-1"]["state"], ResultState.DONE.value)
        self.assertEqual(results["spin-1"]["outcome"], OutcomeWord.PLAYED.value)
        self.assertEqual(results["spin-1"]["evidence"]["total_wager"], evidence.total_wager)
        self.assertEqual(results["spin-1"]["evidence"]["games_played_delta"], 1)

    def test_a_pending_restore_exposes_the_machines_fault_while_its_hold_stays(self):
        pending = se.MachineStatus(NEXT_ID, se.Op.RESTORE_STOCK, se.Status.ACCEPTED, True, GENERATION, 0, True)
        held = MappingProxyType({Family.EXTENSION: Hold("checking", 5)})
        state = online(ext_status=pending, checking=held)
        view = json.loads(json.dumps(ws.public_view(state)))
        self.assertIs(view["extension_fault"], True)
        self.assertEqual(view["checking"][Family.EXTENSION.value]["intent_seq"], 5)  # still pending
        self.assertIs(ws.public_view(online(checking=held))["extension_fault"], False)

    def test_a_machine_refusal_carries_the_extensions_own_status(self):
        refused = ActionResult("restore-1", Act.RESTORE_STOCK, ResultState.DONE, OutcomeWord.REFUSED,
                               machine_status=se.Status.FAULT)
        view = ws.public_view(ws.with_result(online(), refused))
        self.assertEqual(view["results"][0]["machine_status"], se.Status.FAULT.name)
        self.assertFalse(ws.changed_nothing(refused))  # a FAULT can follow a partial transition
        self.assertTrue(ws.changed_nothing(replace(refused, machine_status=se.Status.BUSY)))

    def test_refused_spin_shows_its_code(self):
        view = ws.public_view(online(link=Link.OFFLINE))
        self.assertFalse(view["spin"]["allowed"])
        self.assertEqual(view["spin"]["code"], Code.OFFLINE.value)
        self.assertFalse(view["top_up"]["allowed"])

    def test_results_ring_is_bounded_and_keyed_by_submission_id(self):
        state = online()
        for n in range(ws.RESULTS_LIMIT + 5):
            state = ws.with_result(state, ActionResult(f"s{n}", Act.BET_ONE, ResultState.DONE))
        self.assertEqual(len(state.results), ws.RESULTS_LIMIT)
        older = state.results[5].submission_id
        state = ws.with_result(state, ActionResult(older, Act.BET_ONE, ResultState.REFUSED))
        self.assertEqual(len(state.results), ws.RESULTS_LIMIT)
        self.assertEqual([r.submission_id for r in state.results].count(older), 1)
        self.assertEqual(state.results[5].state, ResultState.REFUSED)


class CatalogueTest(unittest.TestCase):
    def test_stock_catalogue_lands_each_entry_at_each_bet(self):
        game = sp.installed_game()
        profile = sp.stock_profile()
        for bet in range(1, profile.max_bet_per_line + 1):
            outcomes = ws.catalogue(online(bet_per_line=bet))
            wins = [o for o in outcomes if o.kind is ws.Kind.WIN]
            self.assertEqual([o.id for o in wins],
                             [i for i, p in enumerate(profile.payouts)
                              if p.award and p.min_bet <= bet <= p.max_bet])
            self.assertEqual([o.id for o in outcomes if o.kind is not ws.Kind.WIN],
                             [key for key, _ in ws.PATTERNS])
            for outcome in outcomes:
                with self.subTest(bet=bet, outcome=outcome.id):
                    self.assertTrue(outcome.available)
                    score = sp.score(profile, outcome.stops, bet)
                    self.assertEqual(outcome.total_award, score.total_award)
                    self.assertEqual(outcome.total_bet, bet * profile.lines)
                    if outcome.kind is ws.Kind.WIN:
                        self.assertIn(outcome.id, [r for r, award in score.lines if award])
                    else:
                        center = sp.line_symbols(game, outcome.stops, game.line_patterns[0])
                        self.assertEqual(center, dict(ws.PATTERNS)[outcome.id])
                        self.assertEqual(outcome.symbols, center)

    def test_record_names_come_from_symbol_names(self):
        outcomes = ws.catalogue(online())
        top = ws.find_outcome(outcomes, "0")
        self.assertIn(sp.SYMBOL_NAMES[sp.stock_profile().payouts[0].symbols[0]], top.name)

    def test_entry_no_stops_land_is_unavailable(self):
        outcomes = ws.catalogue(online(shadowed_profile()))
        shadowed = ws.find_outcome(outcomes, "3")
        self.assertFalse(shadowed.available)
        self.assertIsNone(shadowed.stops)
        self.assertTrue(ws.find_outcome(outcomes, "0").available)

    def test_no_profile_no_catalogue(self):
        self.assertEqual(ws.catalogue(replace(online(), profile=None)), [])


if __name__ == "__main__":
    unittest.main()
