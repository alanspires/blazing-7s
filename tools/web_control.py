#!/usr/bin/env python3
"""Serve the web control page for the machine on one SAS serial line.

The page's server owns the serial line while it runs: stop anything else using the port
first, and do not run tools/sas against the machine at the same time. Every setting comes
from the arguments; there are no defaults for the serial device, the listen address or the
journal.

SIGTERM or SIGINT stops it cleanly. It exits non-zero when the machine worker or the HTTP
server stops on its own, so a service manager restarts it.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import sas
import sas_protocol
import web_app
from web_journal import Journal, JournalError
from web_worker import Worker


@dataclass(frozen=True)
class Settings:
    device: str
    host: str
    listen_port: int
    journal: str
    allow_hosts: tuple[str, ...]
    address: int


def _listen(text: str) -> tuple[str, int]:
    host, colon, port = text.rpartition(":")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    elif ":" in host:
        raise argparse.ArgumentTypeError(f"{text!r}: write an IPv6 address in brackets, [address]:port")
    if not colon or not host or not port.isdigit() or int(port) > 65535:
        raise argparse.ArgumentTypeError(f"{text!r} is not ADDRESS:PORT")
    return host, int(port)


def _address(text: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= 127:
        raise argparse.ArgumentTypeError(f"{text!r} is not a SAS address from 1 to 127")
    return int(text)


def parse(argv: list[str] | None = None) -> Settings:
    """The settings argv gives; a usage error exits (status 2) before anything is opened."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, metavar="DEVICE",
                        help="the serial device the machine's SAS cable is on (a /dev/serial/by-id/ path "
                             "survives reboots and replugging)")
    parser.add_argument("--listen", required=True, type=_listen, metavar="ADDRESS:PORT",
                        help="the address and TCP port to serve on, [address]:port for IPv6; the page "
                             "answers only requests naming this address or an --allow-host name")
    parser.add_argument("--journal", required=True, metavar="FILE",
                        help="the activity log and command journal, kept across restarts; its directory "
                             "must exist and be writable")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="another host name people open the page by (repeatable)")
    parser.add_argument("--address", type=_address, default=1, metavar="N",
                        help="the machine's SAS address, machine option 78Hi (default 1)")
    args = parser.parse_args(argv)
    host, listen_port = args.listen
    return Settings(args.port, host, listen_port, args.journal, tuple(args.allow_host), args.address)


class Running:
    """A started page: the journal, the worker on its thread and the server on its thread.
    `done` is set once stop is due: asked for, or a thread ended on its own (`failed`)."""

    def __init__(self, journal: Journal, worker: Worker, server: web_app.Server):
        self.journal, self.worker, self.server = journal, worker, server
        self.done = threading.Event()
        self.failed = False
        self._stopping = threading.Event()
        self._threads = [threading.Thread(target=self._watch, args=(worker.run,), name="machine-worker"),
                         threading.Thread(target=self._watch, args=(server.serve_forever,), name="http")]
        for thread in self._threads:
            thread.start()

    def _watch(self, target: Callable[[], None]) -> None:
        try:
            target()
        finally:
            if not self._stopping.is_set():
                self.failed = True  # an exception's traceback goes to stderr through threading.excepthook
            self.done.set()

    def stop(self) -> None:
        """Stop both threads and close the journal; calling it again does nothing more."""
        self._stopping.set()
        self.done.set()
        self.worker.stop()
        self.server.shutdown()
        self.server.server_close()
        for thread in self._threads:
            thread.join()
        self.journal.close()


def start(settings: Settings, *, open_line: Callable[[str], sas_protocol.Line] = sas.SerialLine,
          clock: Callable[[], float] = time.monotonic) -> Running:
    """Open the journal, bind the server and start the worker and server threads. The worker
    opens the line with open_line(settings.device), and reopens it the same way after a
    failure."""
    journal = Journal.open(settings.journal)
    try:
        worker = Worker(lambda: open_line(settings.device), journal, clock=clock, address=settings.address)
        server = web_app.Server((settings.host, settings.listen_port), worker, journal,
                                allow_hosts=settings.allow_hosts)
    except BaseException:
        journal.close()
        raise
    return Running(journal, worker, server)


def main(argv: list[str] | None = None) -> int:
    settings = parse(argv)
    try:
        running = start(settings)
    except (OSError, JournalError) as error:
        print(f"web_control: {error}", file=sys.stderr)
        return 1
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: running.done.set())
    running.done.wait()
    failed = running.failed
    running.stop()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
