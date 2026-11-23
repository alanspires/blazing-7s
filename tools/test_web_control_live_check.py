"""web_control_live_check against a scripted page on an ephemeral loopback port, and once end
to end against the real web_app and web_worker serving the combined fake machine."""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import socketserver
import threading
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

import web_control
import web_control_live_check as lc
import web_views as wv
from sas_testing import FakeS6000, TempDirTest
from web_control_live_check import Problem, Step
from web_state import Act, Code, Link, OutcomeWord, ResultState

LOOPBACK = "127.0.0.1"
GUARD = 10.0  # seconds a wait may take before a hung test fails; a working page never needs it
TODAY = "2026-01-02"
CREDITS = 5  # the top-up asked for
CENTS_PER_CREDIT = 5
BET, LINES = 3, 1
WAGER = BET * LINES
PAID = 10
GENERATION = 7
LIMIT = 9999
CANARY = "canary-not-for-the-record"  # page text the record must never carry

RECORD_KEYS = {"date", "passed", "online", "start", "spin", "top_up", "failure"}
NESTED_KEYS = {
    "start": {"credits", "games_played", "coin_in", "coin_out", "in_house_in_cents"},
    "spin": {"outcome", "bet_per_line", "total_wager", "paid", "games_played_delta", "credits_delta"},
    "top_up": {"outcome", "credits", "credits_delta", "in_house_in_cents_delta", "in_house_out_cents_delta"},
    "failure": {"step", "problem", "ambiguous", "code", "http_status"},
}
WORDS = ({step.value for step in Step} | {problem.value for problem in Problem} | {code.value for code in Code}
         | {word.value for word in OutcomeWord})


def allowed(admission_code: Code | None = None) -> dict:
    if admission_code is None:
        return {"allowed": True, "code": None, "reason": None}
    return {"allowed": False, "code": admission_code.value, "reason": CANARY}


def page_state(**changes) -> dict:
    """A /state.json projection in web_state.public_view's shape: online, with a spin and
    the top-up admitted."""
    state = {
        "link": Link.ONLINE.value, "fresh": True, "last_reply_time": "2026-01-02T03:04:05.000+00:00",
        "credits": 1000, "cents_per_credit": CENTS_PER_CREDIT,
        "meters": {"coin_in": 100, "coin_out": 80, "jackpot": 0, "games_played": 20, "games_won": 5,
                   "slot_door_opened": 1, "power_reset": 2, "in_house_in_cents": 5000, "in_house_in_count": 1,
                   "in_house_out_cents": 0, "in_house_out_count": 0},
        "bet_per_line": BET, "lines": LINES, "total_wager": WAGER, "profile_generation": GENERATION,
        "arm": {"state": "none", "arm_id": None, "bet_per_line": None},
        "spin": allowed(), "top_up": {"available": True, "limit_credits": LIMIT, **allowed()},
        "checking": {}, "results": [],
    }
    state.update(changes)
    return state


BLANK_RESULT = {"state": ResultState.DONE.value, "outcome": None, "reason": None, "code": None,
                "credits_delta": None, "in_house_in_cents_delta": None, "in_house_out_cents_delta": None,
                "evidence": None}


def evidence(**changes) -> dict:
    return {"bet_per_line": BET, "total_wager": WAGER, "paid": PAID, "games_played_delta": 1,
            "credits_delta": PAID - WAGER, **changes}


PLAYED = {"outcome": OutcomeWord.PLAYED.value, "credits_delta": PAID - WAGER, "evidence": evidence()}
MOVED = {"outcome": OutcomeWord.MOVED.value, "credits_delta": CREDITS, "in_house_in_cents_delta": CREDITS * CENTS_PER_CREDIT,
         "in_house_out_cents_delta": 0}

Answer = Callable[[dict], "tuple[int, dict] | None"]


@dataclass
class Post:
    route: str
    fields: dict
    origin: str | None
    accept: str | None


class FakePage:
    """A scripted page on LOOPBACK: GET /state.json answers `state`; each POST /actions/<route>
    is recorded in `posts` and answered by `answers[route]` (None: the connection closes
    with no answer). Results posted as queued settle at `advance()`."""

    def __init__(self, test: unittest.TestCase):
        self._ids = itertools.count()
        self._settling: dict[str, dict] = {}
        self.reset()
        server = _Server((LOOPBACK, 0), _Handler)
        server.page = self
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        test.addCleanup(thread.join)
        test.addCleanup(server.server_close)
        test.addCleanup(server.shutdown)
        self.url = f"http://{LOOPBACK}:{server.server_address[1]}"

    def reset(self, answers: dict[str, Answer] | None = None, **state: object) -> None:
        """A new page: no posts or results, state changed by `state`, and every action
        settled at once as passing unless `answers` says otherwise."""
        self.state = page_state(**state)
        self.posts: list[Post] = []
        self.answers: dict[str, Answer] = {Act.SPIN.value: self.submitted(PLAYED),
                                           Act.ADD_CREDITS.value: self.submitted(MOVED), **(answers or {})}
        self.unreadable_after_post = False

    @property
    def routes(self) -> list[str]:
        return [post.route for post in self.posts]

    def submitted(self, final: dict, *, queued: bool = False, listed: bool = True) -> Answer:
        """An answer that accepts the action under a new submission ID, with its result in
        the ring already settled as final, or queued until advance()."""
        def answer(fields: dict) -> tuple[int, dict]:
            submission_id = f"submission-{next(self._ids)}"
            result = {"submission_id": submission_id, "action": self.posts[-1].route, **BLANK_RESULT, **final}
            if queued:
                self._settling[submission_id] = result
                result = {**result, **BLANK_RESULT, "state": ResultState.QUEUED.value}
            if listed:
                self.state["results"].insert(0, result)
            return 202, {"submission_id": submission_id}
        return answer

    @staticmethod
    def refused(status: int, code: str) -> Answer:
        return lambda fields: (status, {"submission_id": "refused", "code": code, "reason": CANARY})

    def advance(self) -> None:
        self.state["results"] = [self._settling.pop(r["submission_id"], r) for r in self.state["results"]]


class _Server(HTTPServer):
    page: FakePage

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)  # HTTPServer's would also look up the host's name


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        page = self.server.page
        if self.path != lc.STATE_PATH or (page.unreadable_after_post and page.posts):
            self._send(500, {})
        else:
            self._send(200, page.state)

    def do_POST(self):
        page = self.server.page
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        route = self.path[len(lc.ACTIONS_PATH):]
        page.posts.append(Post(route, {name: values[-1] for name, values in parse_qs(body).items()},
                               self.headers.get("Origin"), self.headers.get("Accept")))
        answer = page.answers[route](page.posts[-1].fields)
        if answer is None:
            self.close_connection = True  # nothing written: the client sees the connection close
            return
        self._send(*answer)

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeTime:
    """The check's clock and pause: time passes only in pause, which lets the page move on."""

    def __init__(self, page: FakePage):
        self.page = page
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def pause(self, seconds: float) -> None:
        self.now += seconds
        self.page.advance()


class ScriptedPageTest(unittest.TestCase):
    def setUp(self):
        self.page = FakePage(self)
        self.time = FakeTime(self.page)

    def check(self) -> dict:
        return lc.check(self.page.url, CREDITS, clock=self.time.clock, pause=self.time.pause, today=TODAY)

    def assert_stopped(self, record: dict, step: Step, problem: Problem, *, ambiguous: bool) -> None:
        self.assertFalse(record["passed"])
        failure = record["failure"]
        self.assertEqual((failure["step"], failure["problem"], failure["ambiguous"]),
                         (step.value, problem.value, ambiguous))

    def test_one_verified_spin_then_one_top_up_passes_with_the_pages_expected_values(self):
        for arm in ({"state": "none", "arm_id": None, "bet_per_line": None},
                    {"state": "armed", "arm_id": 42, "bet_per_line": BET}):
            with self.subTest(arm=arm["state"]):
                self.page.reset({Act.SPIN.value: self.page.submitted(PLAYED, queued=True),
                                 Act.ADD_CREDITS.value: self.page.submitted(MOVED, queued=True)}, arm=arm)
                record = self.check()
                self.assertTrue(record["passed"], record["failure"])
                self.assertEqual(self.page.routes, [Act.SPIN.value, Act.ADD_CREDITS.value])
                spin, top_up = self.page.posts
                expected = {wv.F_GENERATION: str(GENERATION), wv.F_BET: str(BET)}
                if arm["arm_id"] is not None:
                    expected[wv.F_ARM] = str(arm["arm_id"])
                self.assertEqual(spin.fields, expected)
                self.assertEqual(top_up.fields, {wv.F_AMOUNT: str(CREDITS)})
                for post in self.page.posts:
                    self.assertEqual(post.origin, self.page.url)
                    self.assertIn("application/json", post.accept)
                self.assertEqual(record["spin"], {"outcome": OutcomeWord.PLAYED.value, **evidence()})
                self.assertEqual(record["top_up"], {"credits": CREDITS, **MOVED})
                self.assertTrue(record["online"])
                self.assertEqual(record["start"]["credits"], self.page.state["credits"])

    def test_a_failed_preflight_sends_nothing(self):
        checking = {"money": {"reason": CANARY, "intent_seq": 3}}
        cases = [
            ({"link": Link.OFFLINE.value}, Problem.OFFLINE, None),
            ({"fresh": False}, Problem.STALE, None),
            ({"checking": checking}, Problem.CHECKING, None),
            ({"spin": allowed(Code.DOOR_OPEN)}, Problem.SPIN_NOT_ALLOWED, Code.DOOR_OPEN),
            ({"credits": WAGER - 1}, Problem.CREDITS_SHORT, None),
            ({"top_up": {"available": False, "limit_credits": LIMIT, **allowed(Code.AFT_UNAVAILABLE)}},
             Problem.TOP_UP_UNAVAILABLE, Code.AFT_UNAVAILABLE),
            ({"top_up": {"available": True, "limit_credits": CREDITS - 1, **allowed()}}, Problem.OVER_LIMIT, None),
            ({"meters": None}, Problem.STATE_INCOMPLETE, None),
        ]
        for changes, problem, code in cases:
            with self.subTest(problem=problem.value):
                self.page.reset(**changes)
                record = self.check()
                self.assert_stopped(record, Step.PREFLIGHT, problem, ambiguous=False)
                self.assertEqual(record["failure"]["code"], None if code is None else code.value)
                self.assertEqual(self.page.posts, [])

    def test_the_top_up_follows_only_a_verified_spin(self):
        cases = [
            (self.page.submitted({**PLAYED, "evidence": evidence(games_played_delta=2)}), Problem.MISMATCH),
            (self.page.submitted({**PLAYED, "evidence": evidence(credits_delta=PAID - WAGER + 1)}),
             Problem.MISMATCH),
            (self.page.submitted({**PLAYED, "evidence": evidence(total_wager=WAGER + 1)}), Problem.MISMATCH),
            (self.page.submitted({"outcome": OutcomeWord.INTERRUPTED.value}), Problem.NOT_PLAYED),
            (self.page.submitted({"state": ResultState.REFUSED.value, "code": Code.BUSY.value}, queued=True),
             Problem.REFUSED),
            (FakePage.refused(409, Code.OUT_OF_DATE.value), Problem.REFUSED),
        ]
        for answer, problem in cases:
            with self.subTest(problem=problem.value):
                self.page.reset({Act.SPIN.value: answer})
                record = self.check()
                self.assert_stopped(record, Step.SPIN, problem, ambiguous=False)
                self.assertEqual(self.page.routes, [Act.SPIN.value])
                self.assertIsNone(record["top_up"])

    def test_after_the_game_the_top_up_waits_reading_only_until_the_page_admits_it(self):
        record = self.check_after_a_busy_game(ready_after_pauses=2)
        self.assertTrue(record["passed"], record["failure"])
        self.assertEqual(self.page.routes, [Act.SPIN.value, Act.ADD_CREDITS.value])

        record = self.check_after_a_busy_game(ready_after_pauses=None)
        self.assert_stopped(record, Step.TOP_UP, Problem.TOP_UP_UNAVAILABLE, ambiguous=False)
        self.assertEqual(record["failure"]["code"], Code.AFT_UNAVAILABLE.value)
        self.assertEqual(self.page.routes, [Act.SPIN.value])

    def check_after_a_busy_game(self, *, ready_after_pauses: int | None) -> dict:
        """The check where the page stops admitting a top-up once the spin is posted, and
        admits it again after that many of the check's pauses (None: never)."""
        ready = self.page.state["top_up"]
        busy = {**ready, "available": False, **allowed(Code.AFT_UNAVAILABLE)}
        played = self.page.submitted(PLAYED)

        def spin(fields: dict) -> tuple[int, dict]:
            self.page.state["top_up"] = busy  # the machine is finishing its game
            return played(fields)

        self.page.reset({Act.SPIN.value: spin})
        pauses = itertools.count(1)

        def pause(seconds: float) -> None:
            self.time.pause(seconds)
            if next(pauses) == ready_after_pauses:
                self.page.state["top_up"] = ready

        return lc.check(self.page.url, CREDITS, clock=self.time.clock, pause=pause, today=TODAY)

    def test_an_ambiguous_spin_stops_before_the_top_up(self):
        never_settles = self.page.submitted({"state": ResultState.CHECKING.value})
        unlisted = self.page.submitted(PLAYED, listed=False)
        cases = [(never_settles, False, Problem.RESULT_TIMEOUT), (unlisted, False, Problem.RESULT_MISSING),
                 (self.page.submitted(PLAYED), True, Problem.STATE_UNREADABLE)]
        for answer, unreadable, problem in cases:
            with self.subTest(problem=problem.value):
                self.page.reset({Act.SPIN.value: answer})
                self.page.unreadable_after_post = unreadable
                record = self.check()
                self.assert_stopped(record, Step.SPIN, problem, ambiguous=True)
                self.assertEqual(self.page.routes, [Act.SPIN.value])

    def test_waiting_for_a_result_is_bounded(self):
        self.page.reset({Act.SPIN.value: self.page.submitted({"state": ResultState.RUNNING.value})})
        record = self.check()
        self.assert_stopped(record, Step.SPIN, Problem.RESULT_TIMEOUT, ambiguous=True)
        self.assertGreaterEqual(self.time.now, lc.RESULT_TIMEOUT)
        self.assertLess(self.time.now, lc.RESULT_TIMEOUT + lc.POLL_INTERVAL)

    def test_a_post_without_an_answer_is_never_posted_again(self):
        def no_answer(fields: dict) -> None:
            return None

        cases = [
            (Act.SPIN, no_answer, None),
            (Act.SPIN, lambda fields: (202, {"queued": True}), 202),
            (Act.SPIN, lambda fields: (500, {}), 500),
            (Act.ADD_CREDITS, no_answer, None),
        ]
        for act, answer, http_status in cases:
            with self.subTest(act=act.value, http_status=http_status):
                self.page.reset({act.value: answer})
                record = self.check()
                step = Step.SPIN if act is Act.SPIN else Step.TOP_UP
                self.assert_stopped(record, step, Problem.NO_ANSWER, ambiguous=True)
                self.assertEqual(record["failure"]["http_status"], http_status)
                self.assertEqual(self.page.routes.count(act.value), 1)
                self.assertEqual(self.page.routes[-1], act.value)

    def test_the_record_holds_allowlisted_fields_only_and_never_the_url(self):
        cases = [
            ("passed", {}),
            ("refused with page text", {Act.SPIN.value: FakePage.refused(409, CANARY)}),
            ("no answer", {Act.ADD_CREDITS.value: lambda fields: None}),
        ]
        for name, answers in cases:
            with self.subTest(name):
                self.page.reset(answers)
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = lc.main(["--url", self.page.url, "--credits", str(CREDITS)])
                output = stdout.getvalue()
                self.assertEqual(stderr.getvalue(), "")
                self.assertEqual(output.count("\n"), 1)
                record = json.loads(output)
                self.assertEqual(status, 0 if record["passed"] else 1)
                self.assertEqual(record["passed"], name == "passed")
                self.assert_allowlisted(record)
                for secret in (self.page.url, LOOPBACK, CANARY):
                    self.assertNotIn(secret, output)

    def assert_allowlisted(self, record: dict) -> None:
        self.assertEqual(set(record), RECORD_KEYS)
        self.assertRegex(record["date"], r"\A\d{4}-\d{2}-\d{2}\Z")
        for name, keys in NESTED_KEYS.items():
            part = record[name]
            if part is None:
                continue
            self.assertLessEqual(set(part), keys, name)
            for value in part.values():
                self.assertTrue(value is None or type(value) in (bool, int) or value in WORDS, (name, value))

    def test_the_url_is_never_printed_on_a_usage_error(self):
        secret = "page.example"
        for url in (f"https://{secret}:8080", f"http://{secret}:8080/private", f"http://{secret}:port",
                    f"http://user@{secret}:8080", f"http://[{secret}"):
            with self.subTest(url=url), contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as raised:
                    lc.main(["--url", url, "--credits", str(CREDITS)])
                self.assertEqual(raised.exception.code, 2)
                self.assertNotIn(secret, stderr.getvalue())


class EndToEndTest(TempDirTest):
    def test_the_check_passes_on_the_real_page_over_the_fake_machine(self):
        machine = FakeS6000()
        argv = ["--port", "serial-device", "--listen", f"{LOOPBACK}:0", "--journal", str(self.dir / "journal")]
        running = web_control.start(web_control.parse(argv), open_line=lambda device: machine)
        self.addCleanup(running.stop)
        subscription = running.worker.subscribe()
        while not running.worker.snapshot().fresh:
            self.assertIsNotNone(subscription.wait(GUARD), "the worker never reached the machine")
        game, in_house = dict(machine.game_meters), machine.meters[0xA0]

        record = lc.check(f"http://{LOOPBACK}:{running.server.port}", CREDITS,
                          pause=lambda seconds: subscription.wait(GUARD), timeout=3 * GUARD)

        self.assertTrue(record["passed"], record["failure"])
        spin = record["spin"]
        self.assertEqual(record["start"]["games_played"], game["games_played"])
        self.assertEqual(machine.game_meters["games_played"], game["games_played"] + 1)
        self.assertEqual(machine.game_meters["coin_in"] - game["coin_in"], spin["total_wager"])
        self.assertEqual(machine.game_meters["coin_out"] - game["coin_out"], spin["paid"])
        self.assertEqual(machine.meters[0xA0] - in_house, CREDITS * FakeS6000.CENTS_PER_CREDIT)
        running.stop()
        self.assertFalse(running.failed)


if __name__ == "__main__":
    unittest.main()
