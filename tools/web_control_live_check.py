#!/usr/bin/env python3
"""Check a running web control page against its machine: read, one remote spin, one top-up.

Run it only with the owner's approval for this operation: it plays one real game and adds
--credits to the machine, and both stay in the machine's lifetime meters.

It talks to the page alone, reading only GET /state.json and posting two actions, in this
order, with no retries and nothing undone afterwards:

  1. Preflight, from /state.json alone: the page is online and fresh, nothing is being
     checked, a spin is admitted with credits covering the reported total wager, and a
     top-up is available with --credits within the reported limit. A failed preflight
     sends nothing.
  2. The same read is the starting state and meters.
  3. One remote spin at the page's chosen bet, carrying the profile generation, bet per
     line and next-result identity that read showed. The check waits, reading only, for
     the result with the spin's submission ID and verifies its evidence: one game played,
     the bet charged equal to the reported total wager, and the credit change equal to
     what it paid minus that bet.
  4. Only after a verified spin, one top-up of --credits. The machine stops taking transfers
     for a moment after a game, so the check first waits, reading only, until the page is
     online and fresh and admits a top-up again with --credits within the reported limit.
     The top-up is verified from the result with its submission ID: credits and the
     in-house-in meter moved by exactly that amount.

An action whose POST gets no usable answer is never posted again: the check stops and
reports it as ambiguous, as it does when a result does not settle in time. Reconcile on
the page before running it again.

Output: one JSON record on stdout holding a date and semantic fields only (whether the page
was online, the starting credits and meters, each action's credit and meter changes, the
spin's result, and why the check stopped). It never holds the URL, raw HTTP, the page's
messages, exception text, the environment or paths. Exit status 0 when every check passed,
1 when one failed or stopped, 2 for a usage error.
"""

from __future__ import annotations

import argparse
import enum
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import web_views as wv
from web_state import Act, Code, Link, OutcomeWord, ResultState

HTTP_TIMEOUT = 10.0  # seconds for one request
RESULT_TIMEOUT = 120.0  # seconds an action's result may take to settle
POLL_INTERVAL = 0.5  # seconds between reads while a result settles
MAX_BODY = 1 << 20  # the page's answers are bounded; anything larger is not the page
STATE_PATH = "/state.json"
ACTIONS_PATH = "/actions/"
SETTLING = frozenset((ResultState.QUEUED.value, ResultState.RUNNING.value, ResultState.CHECKING.value))
REJECTED_BEFORE_SUBMIT = frozenset((400, 403, 404, 411, 413))  # web_app answers these before submitting
REFUSED = frozenset((409, 503))  # admission refused it under a submission ID; nothing was sent
CODES = frozenset(code.value for code in Code)
OUTCOMES = frozenset(word.value for word in OutcomeWord)
START_METERS = ("games_played", "coin_in", "coin_out", "in_house_in_cents")  # recorded from the starting read


class Step(enum.Enum):
    PREFLIGHT = "preflight"
    SPIN = "spin"
    TOP_UP = "top_up"


class Problem(enum.Enum):
    """Why the check stopped."""
    STATE_UNREADABLE = "state_unreadable"
    STATE_INCOMPLETE = "state_incomplete"
    OFFLINE = "offline"
    STALE = "stale"
    CHECKING = "checking"
    SPIN_NOT_ALLOWED = "spin_not_allowed"
    CREDITS_SHORT = "credits_short"
    TOP_UP_UNAVAILABLE = "top_up_unavailable"
    OVER_LIMIT = "over_limit"
    POST_REJECTED = "post_rejected"  # refused at the HTTP edge, before submission
    REFUSED = "refused"  # refused by admission, at once or once queued; nothing was sent
    NO_ANSWER = "no_answer"  # the POST got no usable answer: it may or may not have been submitted
    RESULT_MISSING = "result_missing"  # the submission ID is not in the results ring
    RESULT_TIMEOUT = "result_timeout"
    RESULT_MALFORMED = "result_malformed"
    NOT_PLAYED = "not_played"  # the spin settled without a game's evidence
    MISMATCH = "mismatch"  # the action settled, but its evidence is not what was asked for


class Stop(Exception):
    """The check cannot go on. ambiguous: the machine's state after it is not known from
    the page's answers, so nothing may follow until a person reconciles."""

    def __init__(self, problem: Problem, *, ambiguous: bool, code: object = None, http_status: int | None = None):
        super().__init__(problem.value)
        self.problem = problem
        self.ambiguous = ambiguous
        self.code = code if isinstance(code, str) and code in CODES else None  # never the page's free text
        self.http_status = http_status


class Page:
    """The page's HTTP surface for scripts: GET /state.json and POST /actions/<name>, with
    the same-origin headers web_app requires. Environment proxies are ignored and
    redirects are not followed, so every request goes to the page alone, once. urllib sends
    the URL's authority as the Host header, which the Origin header repeats."""

    def __init__(self, url: str, *, timeout: float = HTTP_TIMEOUT):
        parts = urlsplit(url)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def state(self) -> dict:
        """The /state.json projection. Stop (not ambiguous: reading changes nothing) when it
        cannot be read."""
        request = urllib.request.Request(self._origin + STATE_PATH, headers={"Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ValueError("too large")
            state = json.loads(body)
        except (OSError, http.client.HTTPException, ValueError):
            raise Stop(Problem.STATE_UNREADABLE, ambiguous=False) from None
        if not isinstance(state, dict):
            raise Stop(Problem.STATE_UNREADABLE, ambiguous=False)
        return state

    def post(self, act: Act, fields: Mapping[str, str]) -> str:
        """Submit act once; its submission ID. Stop when the page refused it, and as
        ambiguous when the answer is missing or unusable: it is never posted again."""
        request = urllib.request.Request(self._origin + ACTIONS_PATH + act.value, data=urlencode(fields).encode(),
                                         method="POST", headers={
                                             "Origin": self._origin, "Accept": "application/json",
                                             "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                status, body = response.status, json.loads(response.read(MAX_BODY))
        except urllib.error.HTTPError as error:
            status = error.code
            if status in REJECTED_BEFORE_SUBMIT:
                raise Stop(Problem.POST_REJECTED, ambiguous=False, http_status=status) from None
            if status not in REFUSED:
                raise Stop(Problem.NO_ANSWER, ambiguous=True, http_status=status) from None
            try:
                body = json.loads(error.read(MAX_BODY))
            except (OSError, http.client.HTTPException, ValueError):
                raise Stop(Problem.NO_ANSWER, ambiguous=True, http_status=status) from None
            if not isinstance(body, dict) or not _text(body.get("submission_id")):
                raise Stop(Problem.NO_ANSWER, ambiguous=True, http_status=status) from None
            raise Stop(Problem.REFUSED, ambiguous=False, code=body.get("code"), http_status=status) from None
        except (OSError, http.client.HTTPException, ValueError):
            raise Stop(Problem.NO_ANSWER, ambiguous=True) from None
        if status != 202 or not isinstance(body, dict) or not _text(body.get("submission_id")):
            raise Stop(Problem.NO_ANSWER, ambiguous=True, http_status=status)
        return body["submission_id"]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # urllib raises the 3xx as an HTTPError instead of requesting elsewhere


class LiveCheck:
    """One run of the steps in the module notes against page. clock and pause (monotonic
    seconds, and a wait of the given seconds) bound and space the reads while a result
    settles or the top-up waits to be admitted."""

    def __init__(self, page: Page, credits: int, *, clock: Callable[[], float] = time.monotonic,
                 pause: Callable[[float], object] = time.sleep, poll_interval: float = POLL_INTERVAL,
                 timeout: float = RESULT_TIMEOUT, today: str | None = None):
        self._page = page
        self._credits = credits
        self._clock = clock
        self._pause = pause
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._step = Step.PREFLIGHT
        self.record: dict = {"date": today or datetime.now(timezone.utc).date().isoformat(), "passed": False,
                             "online": None, "start": None, "spin": None, "top_up": None, "failure": None}

    def run(self) -> dict:
        """The record; record["passed"] only when every step verified."""
        try:
            start = self._preflight()
            self._step = Step.SPIN
            self._spin(start)
            self._step = Step.TOP_UP
            self._top_up(start)
        except Stop as stop:
            self.record["failure"] = {"step": self._step.value, "problem": stop.problem.value,
                                      "ambiguous": stop.ambiguous, "code": stop.code,
                                      "http_status": stop.http_status}
            return self.record
        self.record["passed"] = True
        return self.record

    def _preflight(self) -> dict:
        state = self._page.state()
        link, fresh = state.get("link"), state.get("fresh")
        self.record["online"] = link == Link.ONLINE.value and fresh is True
        if link != Link.ONLINE.value:
            raise Stop(Problem.OFFLINE, ambiguous=False)
        if fresh is not True:
            raise Stop(Problem.STALE, ambiguous=False)
        try:
            checking, spin, top_up = state["checking"], state["spin"], state["top_up"]
            if not isinstance(checking, dict):
                raise TypeError("checking is not a mapping")
            if checking:
                raise Stop(Problem.CHECKING, ambiguous=False)
            if spin["allowed"] is not True:
                raise Stop(Problem.SPIN_NOT_ALLOWED, ambiguous=False, code=spin["code"])
            if top_up["allowed"] is not True or top_up["available"] is not True:
                raise Stop(Problem.TOP_UP_UNAVAILABLE, ambiguous=False, code=top_up["code"])
            meters = state["meters"]
            start = {"credits": _count(state["credits"]), "cents_per_credit": _count(state["cents_per_credit"]),
                     "bet_per_line": _count(state["bet_per_line"]), "total_wager": _count(state["total_wager"]),
                     "profile_generation": _count(state["profile_generation"]),
                     "arm_id": _optional(state["arm"]["arm_id"]), "limit_credits": _count(top_up["limit_credits"]),
                     **{name: _count(meters[name]) for name in START_METERS}}
        except (KeyError, TypeError, ValueError):
            raise Stop(Problem.STATE_INCOMPLETE, ambiguous=False) from None
        self.record["start"] = {name: start[name] for name in ("credits", *START_METERS)}
        if start["credits"] < start["total_wager"]:
            raise Stop(Problem.CREDITS_SHORT, ambiguous=False)
        if self._credits > start["limit_credits"]:
            raise Stop(Problem.OVER_LIMIT, ambiguous=False)
        return start

    def _spin(self, start: dict) -> None:
        fields = {wv.F_GENERATION: str(start["profile_generation"]), wv.F_BET: str(start["bet_per_line"])}
        if start["arm_id"] is not None:
            fields[wv.F_ARM] = str(start["arm_id"])
        result = self._act(Act.SPIN, fields)
        evidence = result.get("evidence")
        if evidence is None:
            self.record["spin"] = {"outcome": _word(result.get("outcome"))}
            raise Stop(Problem.NOT_PLAYED, ambiguous=False)
        try:
            spin = {"outcome": _word(result.get("outcome")),
                    **{name: _number(evidence[name]) for name in (
                        "bet_per_line", "total_wager", "paid", "games_played_delta", "credits_delta")}}
        except (KeyError, TypeError, ValueError):
            raise Stop(Problem.RESULT_MALFORMED, ambiguous=True) from None
        self.record["spin"] = spin
        if (spin["games_played_delta"] != 1 or spin["bet_per_line"] != start["bet_per_line"]
                or spin["total_wager"] != start["total_wager"]
                or spin["credits_delta"] != spin["paid"] - spin["total_wager"]):
            raise Stop(Problem.MISMATCH, ambiguous=False)

    def _await_top_up(self) -> None:
        """Wait, reading only and bounded, until the page admits a top-up of --credits."""
        deadline = self._clock() + self._timeout
        while True:
            state = self._page.state()
            try:
                top_up = state["top_up"]
                ready = (state["link"] == Link.ONLINE.value and state["fresh"] is True
                         and top_up["allowed"] is True and top_up["available"] is True)
                limit = _count(top_up["limit_credits"]) if ready else None
                code = top_up["code"]
            except (KeyError, TypeError, ValueError):
                raise Stop(Problem.STATE_INCOMPLETE, ambiguous=False) from None
            if ready:
                if self._credits > limit:
                    raise Stop(Problem.OVER_LIMIT, ambiguous=False)
                return
            if self._clock() >= deadline:
                raise Stop(Problem.TOP_UP_UNAVAILABLE, ambiguous=False, code=code)
            self._pause(self._poll_interval)

    def _top_up(self, start: dict) -> None:
        amount = self._credits
        self._await_top_up()
        result = self._act(Act.ADD_CREDITS, {wv.F_AMOUNT: str(amount)})
        try:
            top_up = {"outcome": _word(result.get("outcome")), "credits": amount,
                      **{name: _optional(result.get(name)) for name in (
                          "credits_delta", "in_house_in_cents_delta", "in_house_out_cents_delta")}}
        except (TypeError, ValueError):
            raise Stop(Problem.RESULT_MALFORMED, ambiguous=True) from None
        self.record["top_up"] = top_up
        if (top_up["credits_delta"] != amount
                or top_up["in_house_in_cents_delta"] != amount * start["cents_per_credit"]
                or top_up["in_house_out_cents_delta"] != 0):
            raise Stop(Problem.MISMATCH, ambiguous=False)

    def _act(self, act: Act, fields: Mapping[str, str]) -> dict:
        """Post act once and wait, reading only, for its settled result."""
        submission_id = self._page.post(act, fields)
        deadline = self._clock() + self._timeout
        while True:
            try:
                results = self._page.state()["results"]
                result = next((r for r in results if r["submission_id"] == submission_id), None)
            except Stop:
                raise Stop(Problem.STATE_UNREADABLE, ambiguous=True) from None
            except (KeyError, TypeError):
                raise Stop(Problem.RESULT_MALFORMED, ambiguous=True) from None
            if result is None:
                raise Stop(Problem.RESULT_MISSING, ambiguous=True)
            state = result.get("state")
            if state == ResultState.DONE.value:
                return result
            if state == ResultState.REFUSED.value:
                raise Stop(Problem.REFUSED, ambiguous=False, code=result.get("code"))
            if not isinstance(state, str) or state not in SETTLING:
                raise Stop(Problem.RESULT_MALFORMED, ambiguous=True)
            if self._clock() >= deadline:
                raise Stop(Problem.RESULT_TIMEOUT, ambiguous=True)
            self._pause(self._poll_interval)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _number(value: object) -> int:
    if type(value) is not int:
        raise ValueError("not an integer")
    return value


def _count(value: object) -> int:
    if _number(value) < 0:
        raise ValueError("negative")
    return value


def _optional(value: object) -> int | None:
    return None if value is None else _number(value)


def _word(value: object) -> str | None:
    """A result's outcome word as the record may carry it; anything else is dropped."""
    return value if isinstance(value, str) and value in OUTCOMES else None


def check(url: str, credits: int, **options) -> dict:
    """Run the live check against the page at url; options as LiveCheck takes them."""
    return LiveCheck(Page(url), credits, **options).run()


def _url(text: str) -> str:
    # The messages never repeat the URL: it is never printed.
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise argparse.ArgumentTypeError("that is not a URL with a numeric port") from None
    if parts.scheme != "http" or not parts.hostname or port == 0:
        raise argparse.ArgumentTypeError("give the page's address as http://HOST:PORT")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username is not None:
        raise argparse.ArgumentTypeError("give the page's address alone, with no path, query or user")
    return text


def _credits(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("give a whole number of credits from 1")
    return int(text)


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, type=_url, metavar="URL",
                        help="the page's address, http://HOST:PORT; never recorded or printed")
    parser.add_argument("--credits", required=True, type=_credits, metavar="N",
                        help="the small top-up to add after the spin, in credits")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    record = check(args.url, args.credits)
    print(json.dumps(record, sort_keys=True))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
