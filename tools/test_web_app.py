"""web_app's routes over a real server on an ephemeral loopback port, serving a web_worker
Worker on the combined fake machine that the test steps itself, and web_control's settings
and start-up."""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import socket
import threading
import unittest
from collections.abc import Callable
from datetime import datetime, timezone
from types import MappingProxyType
from urllib.parse import urlencode

import web_app
import web_control
import web_state as ws
import web_views as wv
from sas_testing import FakeClock, FakeS6000, TempDirTest
from web_journal import Journal
from web_state import Act, Code, ResultState
from web_worker import Worker

LOOPBACK = "127.0.0.1"
# Seconds a wait may block before a hung test fails. A working server answers far sooner, but
# a journal fsync on a busy disk has taken over ten seconds.
GUARD = 60.0
STEP_LIMIT = 20000
PROBE_LIMIT = 5000
SHORT_TIMEOUT = 0.2  # a server socket timeout short enough to reach in a test


# The wire contract browsers rely on, restated rather than read from web_app.
STATIC_TYPES = MappingProxyType({".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                                 ".svg": "image/svg+xml", ".woff2": "font/woff2"})


def now() -> datetime:
    return datetime.now(timezone.utc)


class Site:
    """A Worker on a FakeS6000 with a fake clock, stepped only by the test, behind a real
    web_app.Server on LOOPBACK."""

    def __init__(self, test: TempDirTest, *, start: bool = True, **options):
        self.machine = FakeS6000(clock=FakeClock())
        self.clock = self.machine.clock
        # Durability is test_web_journal's subject; here an fsync is only disk latency.
        self.journal = Journal.open(str(test.dir / "journal"), fsync=lambda fd: None)
        test.addCleanup(self.journal.close)
        self.worker = Worker(lambda: self.machine, self.journal, clock=self.clock, address=FakeS6000.ADDRESS)
        if start:
            self.drive(lambda: self.worker.snapshot().fresh)
        self.logged: list[str] = []
        self.server = web_app.Server((LOOPBACK, 0), self.worker, self.journal, log=self.logged.append, **options)
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        test.addCleanup(thread.join)
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)
        self.host = f"{LOOPBACK}:{self.server.port}"
        self.origin = f"http://{self.host}"

    @property
    def state(self) -> ws.MachineState:
        return self.worker.snapshot()

    def drive(self, until: Callable[[], bool]) -> None:
        for _ in range(STEP_LIMIT):
            if until():
                return
            delay = self.worker.step()
            if delay > 0:
                self.clock.advance(delay)
        raise AssertionError("the worker never got there")

    def result(self, submission_id: str) -> ws.ActionResult | None:
        return next((r for r in self.state.results if r.submission_id == submission_id), None)

    def connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(LOOPBACK, self.server.port, timeout=GUARD)

    def request(self, method: str, target: str, body: bytes | None = None,
                headers: dict | None = None) -> tuple[int, http.client.HTTPResponse, bytes]:
        connection = self.connection()
        try:
            connection.request(method, target, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response, response.read()
        finally:
            connection.close()

    def get(self, target: str, **headers) -> tuple[int, http.client.HTTPResponse, bytes]:
        return self.request("GET", target, headers=headers)

    def post(self, route: str, fields: dict, *, as_json: bool = True,
             headers: dict | None = None) -> tuple[int, http.client.HTTPResponse, bytes]:
        sent = {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin}
        if as_json:
            sent["Accept"] = "application/json"
        sent.update(headers or {})
        return self.request("POST", f"/actions/{route}", urlencode(fields).encode(),
                            {name: value for name, value in sent.items() if value is not None})

    def public(self) -> dict:
        status, _, body = self.get("/state.json")
        assert status == 200, status
        return json.loads(body)

    def stream(self, page: str = "play", timeout: float = GUARD) -> http.client.HTTPResponse:
        connection = http.client.HTTPConnection(LOOPBACK, self.server.port, timeout=timeout)
        connection.request("GET", f"/events?page={page}")
        return connection.getresponse()

    def stream_status(self, page: str = "play") -> http.client.HTTPResponse:
        """Open a stream and close it at once, keeping only the answer's status and headers."""
        response = self.stream(page)
        response.close()
        return response


def message(stream: http.client.HTTPResponse) -> tuple[str, object]:
    """The next event-stream message: ("data", the decoded JSON) or ("comment", its text)."""
    lines = []
    while True:
        line = stream.readline()
        if not line:
            raise AssertionError("the stream ended")
        if line == b"\n":
            break
        lines.append(line.decode().rstrip("\n"))
    if lines[0].startswith(":"):
        return "comment", lines[0][1:].strip()
    data = "".join(line[len("data: "):] for line in lines if line.startswith("data: "))
    return "data", json.loads(data)


class AppTest(TempDirTest):
    def site(self, **options) -> Site:
        return Site(self, **options)


class PagesTest(AppTest):
    def test_every_page_renders_with_its_regions(self):
        site = self.site()
        for path, page in wv.PAGE_PATHS.items():
            with self.subTest(path=path):
                status, response, body = site.get(path)
                self.assertEqual(status, 200)
                self.assertEqual(response.getheader("Content-Type"), "text/html; charset=utf-8")
                for region in wv.regions(site.state, wv.view_for(page, ""), now=now()):
                    self.assertIn(f'data-region="{region}"'.encode(), body)
        newest = site.journal.entries(limit=1)[0]["seq"]
        self.assertEqual(site.get(f"/activity?before={newest}")[0], 200)

    def test_regions_answer_json_for_the_named_page(self):
        site = self.site()
        for page in wv.PATHS:
            with self.subTest(page=page):
                status, response, body = site.get(f"/regions?page={page}")
                self.assertEqual((status, response.getheader("Content-Type")), (200, "application/json"))
                expected = wv.regions(site.state, wv.view_for(None, f"page={page}"), now=now())
                self.assertEqual(set(json.loads(body)["regions"]), set(expected))
        self.assertEqual(site.get("/regions")[0], 400)
        self.assertEqual(site.get("/regions?page=nowhere")[0], 400)

    def test_state_json_serves_the_public_view_of_the_snapshot(self):
        site = self.site()
        expected = json.loads(json.dumps(ws.public_view(site.state)))
        self.assertEqual(site.public(), expected)

    def test_other_routes_are_not_found(self):
        site = self.site()
        self.assertEqual(site.get("/admin")[0], 404)
        self.assertEqual(site.post("nowhere", {})[0], 404)
        self.assertEqual(site.request("POST", "/odds", b"", {"Origin": site.origin,
                                                              "Content-Length": "0"})[0], 404)


class StaticTest(AppTest):
    def test_static_files_are_served_with_their_types(self):
        site = self.site()
        for suffix, content_type in STATIC_TYPES.items():
            file = next(path for path in sorted(web_app.STATIC_ROOT.rglob(f"*{suffix}")))
            target = wv.STATIC + "/" + file.relative_to(web_app.STATIC_ROOT).as_posix()
            with self.subTest(target=target):
                status, response, body = site.get(target)
                self.assertEqual((status, response.getheader("Content-Type")), (200, content_type))
                self.assertEqual(body, file.read_bytes())

    def test_nothing_outside_the_static_files_is_served(self):
        root = self.dir / "static"
        (root / "icons").mkdir(parents=True)
        (root / "shown.css").write_text("shown")
        (root / "notes.txt").write_text("not a served type")
        (self.dir / "secret.css").write_text("outside the static files")
        (root / "link.css").symlink_to(self.dir / "secret.css")
        self.addCleanup(setattr, web_app, "STATIC_ROOT", web_app.STATIC_ROOT)
        web_app.STATIC_ROOT = root.resolve()
        site = self.site()
        status, _, body = site.get("/static/shown.css")
        self.assertEqual((status, body), (200, b"shown"))
        for target in ("/static/../secret.css", "/static/%2e%2e/secret.css", "/static/..%2fsecret.css",
                       "/static/link.css", "/static/notes.txt", "/static/icons", "/static/missing.css",
                       "/static/"):
            with self.subTest(target=target):
                self.assertEqual(site.get(target)[0], 404)


class ActionsTest(AppTest):
    def test_an_action_is_queued_for_the_worker_and_its_result_is_found_by_submission_id(self):
        site = self.site()
        sent = len(site.machine.sent)
        status, _, body = site.post("add-credits", {wv.F_AMOUNT: "25", wv.F_RETURN: "/"})
        self.assertEqual(status, 202)
        submission_id = json.loads(body)["submission_id"]
        queued = {r["submission_id"]: r for r in site.public()["results"]}[submission_id]
        self.assertEqual(queued["state"], ResultState.QUEUED.value)
        self.assertEqual(len(site.machine.sent), sent)  # HTTP threads never reach the line
        site.drive(lambda: site.result(submission_id).state is ResultState.DONE)
        done = {r["submission_id"]: r for r in site.public()["results"]}[submission_id]
        self.assertEqual((done["state"], done["credits_delta"]), (ResultState.DONE.value, 25))

    def test_a_refusal_answers_409_with_its_code_and_where_to_look(self):
        site = self.site()
        over = site.state.aft.limit_credits + 1
        status, _, body = site.post("add-credits", {wv.F_AMOUNT: str(over), wv.F_RETURN: "/"})
        answer = json.loads(body)
        self.assertEqual((status, answer["code"]), (409, Code.OVER_LIMIT.value))
        action = ws.Action(Act.ADD_CREDITS, answer["submission_id"], {wv.F_AMOUNT: over})
        refusal = ws.Refusal(Code.OVER_LIMIT, answer["reason"], answer["submission_id"])
        self.assertEqual(answer["location"], wv.redirect_after("/", action, refusal))
        self.assertEqual(site.result(answer["submission_id"]).state, ResultState.REFUSED)

    def test_a_form_the_views_refuse_with_a_code_answers_409_without_reaching_the_worker(self):
        site = self.site()
        results = site.state.results
        status, _, body = site.post("apply-profile", {"award-0": "many", wv.F_RETURN: "/odds"})
        answer = json.loads(body)
        self.assertEqual((status, answer["code"]), (409, Code.INVALID_PROFILE.value))
        self.assertNotIn("location", answer)
        self.assertEqual(site.state.results, results)

    def test_a_refusal_while_the_machine_is_unreachable_answers_503(self):
        site = self.site(start=False)
        status, _, body = site.post("add-credits", {wv.F_AMOUNT: "25", wv.F_RETURN: "/"})
        self.assertEqual((status, json.loads(body)["code"]), (503, Code.OFFLINE.value))

    def test_a_malformed_form_answers_400_and_submits_nothing(self):
        site = self.site()
        results = site.state.results
        self.assertEqual(site.post("play", {wv.F_ON: "maybe"})[0], 400)
        self.assertEqual(site.state.results, results)

    def test_a_plain_form_post_answers_303_to_where_it_came_from(self):
        site = self.site()
        bet = ws.chosen_bet(site.state)
        status, response, _ = site.post("bet-one", {wv.F_RETURN: "/settings"}, as_json=False)
        self.assertEqual((status, response.getheader("Location")), (303, "/settings"))
        self.assertNotEqual(ws.chosen_bet(site.state), bet)
        over = site.state.aft.limit_credits + 1
        status, response, _ = site.post("add-credits", {wv.F_AMOUNT: str(over), wv.F_RETURN: "/"}, as_json=False)
        refused = ws.Action(Act.ADD_CREDITS, "", {wv.F_AMOUNT: over})
        expected = wv.redirect_after("/", refused, ws.Refusal(Code.OVER_LIMIT, "", ""))
        self.assertEqual((status, response.getheader("Location")), (303, expected))

    def test_preview_answers_the_draft_regions_and_submits_nothing(self):
        site = self.site()
        results = site.state.results
        fields = {"award-10": "3"}
        status, _, body = site.post(web_app.PREVIEW, fields)
        self.assertEqual(status, 200)
        self.assertEqual(set(json.loads(body)["regions"]), set(wv.preview_regions(site.state, fields)))
        self.assertEqual(site.state.results, results)


class EdgeTest(AppTest):
    def test_requests_must_name_the_listen_address_or_an_allowed_host(self):
        site = self.site(allow_hosts=("slots.example",))
        self.assertEqual(site.get("/state.json")[0], 200)
        self.assertEqual(site.get("/state.json", Host=f"Slots.Example:{site.server.port}")[0], 200)
        for host in (f"rebound.example:{site.server.port}", f"{LOOPBACK}:{site.server.port + 1}", ""):
            with self.subTest(host=host):
                self.assertEqual(site.get("/state.json", Host=host)[0], 403)

    def test_posts_from_another_origin_are_refused_before_anything_is_submitted(self):
        site = self.site()
        results = site.state.results
        for headers in ({"Origin": "http://other.example"}, {"Origin": "null"},
                        {"Origin": None, "Referer": "http://other.example/"}, {"Origin": None}):
            with self.subTest(headers=headers):
                self.assertEqual(site.post("bet-one", {}, headers=headers)[0], 403)
        self.assertEqual(site.state.results, results)
        status, _, _ = site.post("bet-one", {}, headers={"Origin": None, "Referer": f"{site.origin}/settings"})
        self.assertEqual(status, 202)

    def test_bodies_are_bounded(self):
        site = self.site()
        for length, status in ((str(web_app.MAX_BODY + 1), 413), (None, 411)):
            with self.subTest(length=length):
                connection = site.connection()
                connection.putrequest("POST", "/actions/bet-one")
                connection.putheader("Origin", site.origin)
                if length is not None:
                    connection.putheader("Content-Length", length)
                connection.endheaders()
                self.assertEqual(connection.getresponse().status, status)
                connection.close()

    def test_a_stalled_request_frees_its_handler_at_the_socket_timeout(self):
        site = self.site(handlers=1, socket_timeout=SHORT_TIMEOUT)
        stalled = socket.create_connection((LOOPBACK, site.server.port), timeout=GUARD)
        self.addCleanup(stalled.close)
        stalled.sendall(b"GET / HTTP/1.0\r\n")  # and never the rest
        over_cap = socket.create_connection((LOOPBACK, site.server.port), timeout=GUARD)
        self.addCleanup(over_cap.close)
        self.assertTrue(read_all(over_cap).startswith(b"HTTP/1.0 503 "))
        self.assertEqual(stalled.recv(1), b"")  # the server closed it at the timeout
        self.assertEqual(site.get("/state.json")[0], 200)


def read_all(connection: socket.socket) -> bytes:
    data = b""
    while True:
        chunk = connection.recv(4096)
        if not chunk:
            return data
        data += chunk


class EventsTest(AppTest):
    def test_a_stream_starts_with_every_region_and_then_sends_what_changed(self):
        site = self.site()
        stream = site.stream("play")
        self.addCleanup(stream.close)
        self.assertEqual(stream.getheader("Content-Type"), "text/event-stream")
        kind, first = message(stream)
        everything = set(wv.regions(site.state, wv.view_for(None, "page=play"), now=now()))
        self.assertEqual((kind, set(first["regions"])), ("data", everything))
        self.assertEqual(site.post("bet-one", {})[0], 202)
        kind, update = message(stream)
        self.assertEqual(kind, "data")
        self.assertTrue(update["regions"])
        self.assertLessEqual(set(update["regions"]), everything)
        for name, markup in update["regions"].items():
            self.assertNotEqual(markup, first["regions"][name])

    def test_a_quiet_stream_sends_keepalive_comments(self):
        site = self.site(keepalive=0.01)
        stream = site.stream()
        self.addCleanup(stream.close)
        self.assertEqual(message(stream)[0], "data")
        self.assertEqual(message(stream)[0], "comment")

    def test_streams_over_the_cap_get_503_and_regions_still_answer(self):
        site = self.site(subscribers=1)
        stream = site.stream()
        self.addCleanup(stream.close)
        self.assertEqual(message(stream)[0], "data")
        over = site.stream_status()
        self.assertEqual(over.status, 503)
        self.assertIsNotNone(over.getheader("Retry-After"))
        self.assertEqual(site.get("/regions?page=play")[0], 200)

    def test_a_stream_that_stops_reading_frees_its_slot_at_the_socket_timeout(self):
        site = self.site(subscribers=1, socket_timeout=SHORT_TIMEOUT)
        stalled = socket.socket()
        self.addCleanup(stalled.close)
        stalled.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        stalled.settimeout(GUARD)
        stalled.connect((LOOPBACK, site.server.port))
        stalled.sendall(f"GET /events?page=play HTTP/1.0\r\nHost: {site.host}\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:  # streaming, so it holds the one subscriber slot
            head += stalled.recv(1)
        self.assertTrue(head.startswith(b"HTTP/1.0 200 "))
        for _ in range(PROBE_LIMIT):  # each bet change is one more message it does not read
            self.assertEqual(site.post("bet-one", {})[0], 202)
            status = site.stream_status().status
            if status == 200:
                return
            self.assertEqual(status, 503)
        self.fail("the stalled stream kept its slot")

    def test_closing_the_server_ends_open_streams_well_before_a_keepalive(self):
        site = self.site()
        stream = site.stream(timeout=web_app.KEEPALIVE / 3)  # an end at the keepalive times out
        self.addCleanup(stream.close)
        self.assertEqual(message(stream)[0], "data")
        site.server.shutdown()
        site.server.server_close()
        self.assertEqual(stream.readline(), b"")


ARGS = {"--port": "serial-device", "--listen": f"{LOOPBACK}:0", "--journal": None}


class ControlTest(TempDirTest):
    def argv(self, **changes) -> list[str]:
        values = {**ARGS, "--journal": str(self.dir / "journal"), **changes}
        return [part for flag, value in values.items() if value is not None for part in (flag, value)]

    def test_missing_or_malformed_settings_are_usage_errors(self):
        cases = [{flag: None} for flag in ARGS]
        cases += [{"--listen": value} for value in ("nowhere", f"{LOOPBACK}:", ":8080", "::1:8080",
                                                    f"{LOOPBACK}:65536")]
        cases.append({"--address": "0"})
        for changes in cases:
            # main parses before it opens anything; parse alone cannot hang when it is wrong.
            with self.subTest(changes=changes), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    web_control.parse(self.argv(**changes))
                self.assertNotEqual(raised.exception.code, 0)

    def test_settings_come_from_the_arguments(self):
        settings = web_control.parse(self.argv(**{"--listen": "[::1]:8080", "--address": "3"})
                                     + ["--allow-host", "slots.example", "--allow-host", "other.example"])
        self.assertEqual(settings, web_control.Settings("serial-device", "::1", 8080, str(self.dir / "journal"),
                                                        ("slots.example", "other.example"), 3))

    def test_start_serves_the_machine_on_the_line_it_opens_and_stops_cleanly(self):
        opened = []

        def open_line(device: str) -> FakeS6000:
            opened.append(device)
            return FakeS6000()

        running = web_control.start(web_control.parse(self.argv()), open_line=open_line)
        self.addCleanup(running.stop)
        subscription = running.worker.subscribe()
        while not running.worker.snapshot().fresh:
            self.assertIsNotNone(subscription.wait(GUARD), "the worker never reached the machine")
        connection = http.client.HTTPConnection(LOOPBACK, running.server.port, timeout=GUARD)
        connection.request("GET", "/state.json")
        self.assertEqual(json.loads(connection.getresponse().read())["link"], ws.Link.ONLINE.value)
        connection.close()
        running.stop()
        self.assertFalse(running.failed)
        self.assertEqual(set(opened), {ARGS["--port"]})
        Journal.open(str(self.dir / "journal")).close()  # stop released the journal

    def test_a_worker_that_dies_marks_the_page_failed(self):
        def broken(device: str) -> FakeS6000:
            raise RuntimeError("the worker hit a bug")

        failures = []
        hook = threading.excepthook
        threading.excepthook = failures.append
        self.addCleanup(setattr, threading, "excepthook", hook)
        running = web_control.start(web_control.parse(self.argv()), open_line=broken)
        self.addCleanup(running.stop)
        self.assertTrue(running.done.wait(GUARD))
        running.stop()
        self.assertTrue(running.failed)
        self.assertEqual([type(f.exc_value) for f in failures], [RuntimeError])


if __name__ == "__main__":
    unittest.main()
