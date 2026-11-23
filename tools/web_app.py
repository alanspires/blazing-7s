"""The web control page's HTTP adapter: a fixed-route threaded HTTP server.

The adapter never touches the serial line. It reads the machine worker's
immutable snapshots (`worker.snapshot()`), waits for new ones (`worker.subscribe()`), hands
actions to it (`worker.submit()`) and pages the journal's activity (`journal.entries()`).
Rendering is web_views'; the routes are:

    GET  /, /odds, /activity, /settings   a whole page for the URL's view
    GET  /events?page=...                 Server-Sent Events: {"regions": {id: html}}, the
                                          whole set first, then the regions each newer
                                          snapshot changed; a keepalive comment with a
                                          "heartbeat" event {"deadline": seconds} at the
                                          start and whenever the stream has been quiet for
                                          the keepalive period
    GET  /regions?page=...                the whole region set as JSON, for pages /events
                                          turned away over its subscriber cap, and for the
                                          Add credits sheet at the amount being typed
    GET  /state.json                      web_state.public_view, for scripts
    GET  /static/<path>                   the design's stylesheets, fonts, icons and live.js,
                                          and the licence and source notices beside them
    POST /actions/preview                 the odds editor regions a draft changes; sends nothing
    POST /actions/<Act value>             submit an action under a new submission ID

An action answers a JSON request (Accept: application/json, as live.js asks) with 202
{submission_id}, or with 409, or 503 while the machine cannot be reached, and
{submission_id, code, reason, location?}; a plain form post answers 303 to the page it came
from. The submission ID is the handle for the result in /state.json: nothing is resent when
an answer goes missing.

Admission at the edge: every request's Host header must name
the listen address or an allowed host name at the listening port, and every POST's Origin
(or, without one, its Referer) must be this same origin, which refuses cross-site and
DNS-rebinding posts without logins or confirmations. Bodies are bounded. Every
connection has a socket timeout, so a stalled request or a client that stops reading its
event stream frees its handler at that deadline. Handler threads are capped (an over-cap
connection gets 503 and no thread), and so, separately, are event streams.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import socketserver
import sys
import threading
import time
import uuid
from collections.abc import Callable, Collection, Mapping
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import web_state as ws
import web_views as wv
from web_state import Act, Code

STATIC_ROOT = Path(__file__).resolve().parent / "web" / "static"
# The notices (.md, .txt) ship with the fonts and icons they cover: the URW font's licence
# requires its source directions beside the served file. Plain text, so a browser shows them.
CONTENT_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".svg": "image/svg+xml", ".woff2": "font/woff2",
                 ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
ACTIONS = "/actions/"
PREVIEW = "preview"

MAX_HANDLERS = 64  # concurrent connections with a handler thread
MAX_SUBSCRIBERS = 32  # open event streams, fewer than the handlers so pages still load
SOCKET_TIMEOUT = 20.0  # seconds any one read or write on a connection may block
KEEPALIVE = 15.0  # seconds without a write before an event stream sends a heartbeat
# A page counts its stream lost after this many keepalive periods without a message, so one
# late heartbeat never trips it; the heartbeat tells the page the resulting deadline.
HEARTBEAT_PERIODS = 2
# Each stream's kernel send buffer: bounds what a client that stops reading pins, and makes
# its writes block, and time out, after that much rather than after the system maximum.
SSE_SEND_BUFFER = 64 * 1024
MAX_BODY = 32 * 1024  # a POST body; the largest form, the odds editor's, is a few KiB
MAX_FIELDS = 512
RETRY_AFTER = "5"  # seconds, on answers to requests over a cap
UNAVAILABLE = frozenset((Code.OFFLINE, Code.STALE))  # refusals answered 503 instead of 409
OVER_CAP = (b"HTTP/1.0 503 Service Unavailable\r\nContent-Type: text/plain; charset=utf-8\r\n"
            b"Content-Length: 5\r\nRetry-After: " + RETRY_AFTER.encode() + b"\r\n"
            b"Connection: close\r\n\r\nBusy\n")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(text: str) -> None:
    sys.stderr.write(text + "\n")


class Server(ThreadingHTTPServer):
    """Serves `worker` (snapshot, subscribe, submit) and `journal` (entries) at `address`,
    a (host, port) pair. Requests must name host, or one of allow_hosts, at the bound port."""

    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address: tuple[str, int], worker, journal, *, allow_hosts: Collection[str] = (),
                 handlers: int = MAX_HANDLERS, subscribers: int = MAX_SUBSCRIBERS,
                 socket_timeout: float = SOCKET_TIMEOUT, keepalive: float = KEEPALIVE,
                 log: Callable[[str], None] = _log):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.worker = worker
        self.journal = journal
        self.allowed = frozenset(_host_name(name) for name in (address[0], *allow_hosts))
        self.socket_timeout = socket_timeout
        self.keepalive = keepalive
        self.log = log
        self.closing = threading.Event()  # set by server_close: event streams end
        self._handlers = threading.BoundedSemaphore(handlers)
        self.subscribers = threading.BoundedSemaphore(subscribers)
        self._streams: set[socket.socket] = set()
        self._streams_lock = threading.Lock()
        super().__init__(address, Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    def server_bind(self) -> None:
        # HTTPServer.server_bind also resolves the host's fully qualified name, which can
        # wait on DNS and is never used here.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request: socket.socket, client_address) -> None:
        if not self._handlers.acquire(blocking=False):
            try:
                request.settimeout(self.socket_timeout)
                request.sendall(OVER_CAP)
            except OSError:
                pass  # the client already left; its connection is closed either way
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._handlers.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address) -> None:
        try:
            self.finish_request(request, client_address)  # an error's traceback goes to threading.excepthook
        finally:
            # Released before the connection closes, so a client that sees it close can
            # count on the slot being free again.
            self._handlers.release()
            self.shutdown_request(request)

    def add_stream(self, connection: socket.socket) -> bool:
        """Track an open event stream so server_close can end it; False once closing."""
        with self._streams_lock:
            if self.closing.is_set():
                return False
            self._streams.add(connection)
            return True

    def remove_stream(self, connection: socket.socket) -> None:
        with self._streams_lock:
            self._streams.discard(connection)

    def server_close(self) -> None:
        # A stream's handler waits on the worker's snapshots, which nothing here can wake,
        # so its connection is shut down instead: the page sees the stream end at once, and
        # the handler finds `closing` set when its wait returns and ends without writing.
        with self._streams_lock:
            self.closing.set()
            streams = list(self._streams)
        for connection in streams:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # the client already closed it
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    server: Server
    server_version = "web_control"
    sys_version = ""

    def setup(self) -> None:
        self.timeout = self.server.socket_timeout
        super().setup()

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionError, socket.timeout):  # noqa: UP041 -- socket.timeout is no TimeoutError before 3.10
            pass  # the client left or stalled past the socket timeout; nothing is owed to it

    def log_request(self, code="-", size="-") -> None:
        pass  # no line per request: pages that poll /regions would flood the log

    def log_message(self, format: str, *args) -> None:
        self.server.log(f"{self.address_string()} {format % args}")

    # Routes

    def do_GET(self) -> None:
        if not self._host_allowed():
            return
        target = urlsplit(self.path)
        path, query = target.path, target.query
        if path in wv.PAGE_PATHS:
            self._page(wv.PAGE_PATHS[path], query)
        elif path == "/events":
            self._events(query)
        elif path == "/regions":
            self._regions(query)
        elif path == "/state.json":
            self._json(200, ws.public_view(self.server.worker.snapshot()))
        elif path.startswith(wv.STATIC + "/"):
            self._static(path[len(wv.STATIC) + 1:])
        else:
            self._text(404, "Not found")

    def do_POST(self) -> None:
        # The bounded body is read before any refusal: closing a connection with unread
        # input resets it, and the client can lose the answer.
        fields = self._form()
        if fields is None or not self._host_allowed() or not self._same_origin():
            return
        path = urlsplit(self.path).path
        route = path[len(ACTIONS):] if path.startswith(ACTIONS) else None
        if route != PREVIEW and route not in _ROUTES:
            self._text(404, "Not found")
            return
        if route == PREVIEW:
            state = self.server.worker.snapshot()
            self._json(200, {"regions": wv.preview_regions(state, fields)})
        else:
            self._action(route, fields)

    def _page(self, page: str, query: str) -> None:
        view = wv.view_for(page, query)
        markup = wv.page(self.server.worker.snapshot(), view, now=_now(), entries=self._entries(view))
        self._send(200, markup.encode(), "text/html; charset=utf-8")

    def _regions(self, query: str) -> None:
        view = self._live_view(query)
        if view is not None:
            self._json(200, {"regions": self._render(self.server.worker.snapshot(), view)})

    def _events(self, query: str) -> None:
        view = self._live_view(query)
        if view is None:
            return
        server = self.server
        if not server.subscribers.acquire(blocking=False):
            self._text(503, "Too many open pages", {"Retry-After": RETRY_AFTER})
            return
        if not server.add_stream(self.connection):
            server.subscribers.release()
            self._text(503, "Shutting down")
            return
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SSE_SEND_BUFFER)
            subscription = server.worker.subscribe()  # before the first render: no update is missed
            shown = self._render(server.worker.snapshot(), view)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self._event(shown)
            self._heartbeat()
            written = time.monotonic()
            while True:
                state = subscription.wait(max(written + server.keepalive - time.monotonic(), 0.0))
                if server.closing.is_set():
                    break
                if state is not None:
                    # Coalesced: the newest snapshot only, never a backlog of older ones.
                    regions = self._render(state, view)
                    changed = {name: markup for name, markup in regions.items() if shown.get(name) != markup}
                    if changed:
                        self._event(changed)
                        shown = regions
                        written = time.monotonic()
                if time.monotonic() - written >= server.keepalive:
                    self._heartbeat()
                    written = time.monotonic()
        finally:
            server.remove_stream(self.connection)
            server.subscribers.release()

    def _event(self, regions: Mapping[str, str]) -> None:
        self._frame(b"data: " + json.dumps({"regions": regions}, separators=(",", ":")).encode() + b"\n\n")

    def _heartbeat(self) -> None:
        # A keepalive comment and an event in one frame: EventSource never hands comments to
        # the page, which needs to see the stream is alive to notice a connection lost without
        # a close.
        deadline = self.server.keepalive * HEARTBEAT_PERIODS
        self._frame(b": keepalive\nevent: heartbeat\ndata: " + json.dumps({"deadline": deadline}).encode() + b"\n\n")

    def _frame(self, frame: bytes) -> None:
        self.wfile.write(frame)

    def _action(self, route: str, fields: Mapping[str, str]) -> None:
        worker = self.server.worker
        submission_id = uuid.uuid4().hex
        try:
            action = wv.action_from_form(route, submission_id, fields, worker.snapshot())
        except wv.FormError as error:
            if error.code is None:
                self._answer_bad(error.reason)
                return
            action = ws.Action(Act(route), submission_id)
            answer = ws.Refusal(error.code, error.reason, submission_id)  # nothing was submitted
        else:
            answer = worker.submit(action)
        refusal = answer if isinstance(answer, ws.Refusal) else None
        return_to = fields.get(wv.F_RETURN)
        location = wv.redirect_after(return_to, action, refusal)
        if not self._wants_json():
            self._send(303, b"", "text/plain; charset=utf-8", {"Location": location})
        elif refusal is None:
            self._json(202, {"submission_id": submission_id})
        else:
            body = {"submission_id": submission_id, "code": refusal.code.value, "reason": refusal.reason}
            if location != return_to:
                body["location"] = location
            self._json(503 if refusal.code in UNAVAILABLE else 409, body)

    def _static(self, relative: str) -> None:
        parts = unquote(relative).split("/")
        if any(part in ("", ".", "..") or "\\" in part or "\0" in part for part in parts):
            self._text(404, "Not found")
            return
        file = STATIC_ROOT.joinpath(*parts).resolve()
        content_type = CONTENT_TYPES.get(file.suffix)
        if content_type is None or STATIC_ROOT not in file.parents or not file.is_file():
            self._text(404, "Not found")
            return
        self._send(200, file.read_bytes(), content_type, {"Cache-Control": "no-cache"})

    # Helpers

    def _live_view(self, query: str) -> wv.View | None:
        try:
            return wv.view_for(None, query)
        except ValueError:
            self._text(400, "Name the page: page=play, odds, activity or settings")
            return None

    def _entries(self, view: wv.View) -> list[dict] | None:
        """Activity's journal page for view; other pages list none."""
        if view.page != "activity":
            return None
        return self.server.journal.entries(before=view.before, limit=wv.ACTIVITY_PAGE + 1,
                                           kinds=ws.ACTIVITY_KINDS)

    def _render(self, state: ws.MachineState, view: wv.View) -> dict[str, str]:
        return wv.regions(state, view, now=_now(), entries=self._entries(view))

    def _host_allowed(self) -> bool:
        host = _authority(self.headers.get("Host", ""))
        if host is not None and host[0] in self.server.allowed and host[1] == self.server.port:
            return True
        self._text(403, "Unknown host name: open the page at its listen address or an allowed name")
        return False

    def _same_origin(self) -> bool:
        source = self.headers.get("Origin")
        if source is None:
            source = self.headers.get("Referer")
        parts = urlsplit(source or "")
        if parts.scheme == "http" and _authority(parts.netloc) == _authority(self.headers.get("Host", "")):
            return True
        self._text(403, "Actions are taken only from this page")
        return False

    def _form(self) -> dict[str, str] | None:
        """The POST body's form fields (the last value of each), or None once refused."""
        length = self.headers.get("Content-Length")
        if "Transfer-Encoding" in self.headers or length is None:
            self._text(411, "Content-Length required")
            return None
        if not length.isdigit():
            self._text(400, "Bad Content-Length")
            return None
        if int(length) > MAX_BODY:
            self._text(413, "Too large")
            return None
        try:
            text = self.rfile.read(int(length)).decode()
            values = parse_qs(text, keep_blank_values=True, max_num_fields=MAX_FIELDS)
        except ValueError:  # UnicodeDecodeError included
            self._text(400, "Not a form")
            return None
        return {name: items[-1] for name, items in values.items()}

    def _wants_json(self) -> bool:
        return "application/json" in self.headers.get("Accept", "")

    def _answer_bad(self, reason: str) -> None:
        if self._wants_json():
            self._json(400, {"reason": reason})
        else:
            self._text(400, reason)

    def _json(self, status: int, body: object) -> None:
        self._send(status, json.dumps(body, separators=(",", ":")).encode(), "application/json")

    def _text(self, status: int, text: str, headers: Mapping[str, str] | None = None) -> None:
        self._send(status, (text + "\n").encode(), "text/plain; charset=utf-8", headers)

    def _send(self, status: int, body: bytes, content_type: str, headers: Mapping[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        headers = dict(headers or {})
        headers.setdefault("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


_ROUTES = frozenset(act.value for act in Act)


def _host_name(text: str) -> str:
    """A host as compared: an IP literal in its canonical spelling (browsers compress IPv6
    addresses, so "[0:0::1]" arrives as "[::1]"), any other name in lower case."""
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return text.lower()


def _authority(text: str) -> tuple[str, int] | None:
    """The host (as _host_name compares it) and port an authority ("name:port", "[v6]:port")
    names, port 80 when it gives none; None for anything else."""
    parts = urlsplit("//" + text)
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname or parts.username is not None or parts.path or parts.query or parts.fragment:
        return None
    return _host_name(parts.hostname), 80 if port is None else port
