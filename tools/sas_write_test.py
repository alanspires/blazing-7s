"""Non-money write tests, with read-back where SAS provides it.

The expected behavior comes from the firmware handlers (notes/sas-j10.md, "Write test").
Play, clock and registration are checked after restoration. Sound and bills end enabled,
game delay ends at zero; those commands can only be checked by their ACKs.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Callable

import sas_commands as sc
import sas_protocol as sas
from sas_client import poll
from sas_tables import IDLE_EXCEPTIONS

POLL_INTERVAL = 0.25  # §2.3.3
HANDLER_POLLS = 8  # reads, 2 s, for a handler's effect to show
MAIN_LOOP_POLLS = 120  # reads, 30 s, for the main loop to act on shutdown or startup
DRAIN_POLLS = 40  # general polls for the machine to run out of exceptions to report

# 6E action 2 (authenticate) answers C5 while play is enabled, before it looks at the
# component; with play disabled it looks the component up, and index FF does not exist
# (FUN_0002f28e takes 1-3), so the answer is 80 and nothing starts (FUN_0002f598).
CANNOT_AUTHENTICATE_NOW, NO_SUCH_COMPONENT = 0xC5, 0x80
PROBE_COMPONENT = "0xFF"
LOCKED, NOT_LOCKED = 0x00, 0xFF  # 74 lock status
REGISTRATION_READY, REGISTERED, NOT_REGISTERED = 0x00, 0x01, 0x80  # 73 status
AFT_REGISTERED_BIT = 0x08  # 74 AFT status while registered (FUN_0003070a)
GAME_LOCKED, REGISTRATION_CANCELLED, VALIDATION_ID_NOT_CONFIGURED = 0x6F, 0x6E, 0x3F
NOT_IN_HANDPAY = 0x02  # 94 reset code and A8 ack code
NOT_VALIDATING = 0x80  # 58 status with no cashout waiting for a validation number
ONE_DOLLAR_BILLS, KEEP_ENABLED = 0x01, 0x01  # 08 bill bitmap and action (Table 7.5)
TEST_DELAY = 10  # 2E, in units of 100 ms
TEST_KEY = bytes(range(1, 21)).hex()
TEST_POS_ID = 1
LOCK_TIMEOUT = 1000  # hundredths of a second: an AFT lock left behind lapses after 10 s
CLOCK_FORMAT = "%m%d%Y%H%M%S"
CLOCK_TOLERANCE = dt.timedelta(seconds=3)  # 7E has whole seconds; 7F is sent up to 3 times


class StepFailed(Exception):
    pass


def acked(result: dict) -> bool:
    return result.get("reply") == "ACK"


def silent(result: dict) -> bool:
    return result.get("error_type") == sas.NoResponse.__name__


class WriteTest:
    def __init__(self, host: sas.Host, report: Callable[[dict], None]):
        self.host = host
        self.line = host.line
        self.report = report
        self.failures = 0

    def send(self, name: str, *values, retry: bool = True) -> dict:
        self.line.wait(POLL_INTERVAL)
        result = poll(self.host, sc.BY_NAME[name], [str(v) for v in values], retry=retry)
        self.report(result)
        return result

    def read(self, name: str, *values, retry: bool = True) -> dict:
        result = self.send(name, *values, retry=retry)
        if "error" in result:
            raise StepFailed(f"{name}: {result['error']}")
        if not isinstance(result["reply"], dict):
            raise StepFailed(f"{name}: expected data, not a bare ACK")
        return result["reply"]

    def check(self, label: str, ok: bool, detail: object = "") -> bool:
        self.report({"check": label, "ok": bool(ok), "detail": str(detail)})
        self.failures += not ok
        return bool(ok)

    def restore(self, label: str, action: Callable[[], bool], advice: str = "") -> None:
        """Run an undo; a failure is reported, not raised, so the other undos still run."""
        try:
            ok, detail = action(), advice
        except (sas.ProtocolError, StepFailed) as e:
            ok, detail = False, f"{e}. {advice}".strip()
        self.check(f"restored: {label}", ok, "" if ok else detail)

    def drain(self) -> list[tuple[int, bool]]:
        """General polls until the machine has nothing left to report: the exceptions it
        reported, each with whether it came as a real-time event."""
        seen = [(e.exception, True) for e in self.host.events]
        self.host.events.clear()
        for _ in range(DRAIN_POLLS):
            self.line.wait(POLL_INTERVAL)
            reply = self.host.general_poll()
            if isinstance(reply, sas.RomSignature):
                continue
            event = isinstance(reply, sas.RealTimeEvent)
            code = reply.exception if event else reply
            if code in IDLE_EXCEPTIONS:
                break
            seen.append((code, event))
        for code, event in seen:
            self.report({"exception": f"{code:02X}", "real_time": event})
        return seen

    def wait_for(self, read: Callable[[], object], want: object, reads: int) -> object:
        """Read until `read()` returns `want`, at most `reads` times; the last value read."""
        for _ in range(reads):
            value = read()
            if value == want:
                break
        return value

    def snapshot(self) -> tuple:
        """What the steps change that a poll reads back: whether play is disabled, the AFT
        status, the registration."""
        self.drain()
        return self.play_disabled(), self.aft_status(), self.registration()

    def play_disabled(self) -> bool:
        """The play-disabled bit (bit 7 of 0x412D38), read with the 6E probe."""
        status = self.read("authentication", 2, 0, PROBE_COMPONENT)["status"]
        if status not in (CANNOT_AUTHENTICATE_NOW, NO_SUCH_COMPONENT):
            raise StepFailed(f"6E probe answered {status:02X}")
        return status == NO_SUCH_COMPONENT

    def aft_status(self) -> dict:
        return self.read("aft-lock", 0xFF, 0, 0)

    def registration(self) -> dict:
        return self.read("aft-register", 0xFF)

    def machine_time(self) -> dt.datetime:
        reply = self.read("date-time")
        return dt.datetime.strptime(reply["date"] + reply["time"], CLOCK_FORMAT)

    def set_machine_time(self, when: dt.datetime) -> dict:
        stamp = when.strftime(CLOCK_FORMAT)
        return self.send("set-date-time", stamp[:8], stamp[8:])


def enable_game(t: WriteTest) -> bool:
    """09 sets and clears the play-disabled bit (bit 7 of 0x412D38) in its handler. Whether
    the 6E probe shows it decides the shutdown step."""
    try:
        t.check("09 disable game 1: ACK", acked(t.send("enable-game", 1, 0)))
        shown = t.wait_for(t.play_disabled, True, HANDLER_POLLS)
        t.check("6E probe shows play disabled", shown)
    finally:
        t.restore("game 1 enabled", lambda: acked(t.send("enable-game", 1, 1))
                  and t.wait_for(t.play_disabled, False, HANDLER_POLLS) is False)
    return shown


def shutdown(t: WriteTest) -> None:
    """01 sets the shutdown flag (0x43D150, SAFERAM) to 1; the main loop, once the machine is
    idle, disables play and sets it to 2. 02 acts only on 2 (it sets 3, and the main loop
    re-enables play). So startup waits for the 6E probe to show play disabled, and is
    repeated until play is back: a startup sent too early would be ignored and leave the
    machine shut down."""
    try:
        t.check("01 shutdown: ACK", acked(t.send("shutdown")))
        t.check("6E probe shows play disabled", t.wait_for(t.play_disabled, True, MAIN_LOOP_POLLS))
    finally:
        t.restore("play enabled", lambda: start_up(t),
                  "If it stays shut down: once `tools/sas authentication 2 0 0xFF` answers "
                  "status 128, run `tools/sas startup`")


def start_up(t: WriteTest) -> bool:
    """Send startup until the probe shows play enabled. The machine has left the poll after a
    startup unanswered (its main loop waits on 0x74E1A's word while re-enabling play), so a
    silent probe here means wait, not fail."""
    for _ in range(MAIN_LOOP_POLLS // HANDLER_POLLS):
        t.send("startup")
        for _ in range(HANDLER_POLLS):
            try:
                if not t.play_disabled():
                    return True
            except StepFailed:
                continue
    return False


def sound(t: WriteTest) -> None:
    """03 and 05 clear the sound flags in SAFERAM (0x43E8E2, 0x43E8E3); 04 sets both to 1,
    their RAM-clear value (FUN_0005603a). No poll reads them, so only the ACKs show."""
    try:
        for name in ("sound-off", "reel-sounds-off"):
            t.check(f"{sc.BY_NAME[name].code:02X} {name}: ACK", acked(t.send(name)))
    finally:
        t.restore("sound on", lambda: acked(t.send("sound-on")))


def clock(t: WriteTest) -> None:
    """7F writes the clock chip and sends no reply at all (FUN_0002eaac never ACKs, unlike
    Table 7.17); 7E reads it back. The test time is an hour off the machine's, and the
    restore adds the time that passed."""
    start, started = t.machine_time(), time.monotonic()
    test = start - dt.timedelta(hours=1) if start.hour else start + dt.timedelta(hours=1)
    try:
        t.check("7F gets no reply", silent(t.set_machine_time(test)))
        got = t.machine_time()
        t.check("7E reads the test time", abs(got - test) <= CLOCK_TOLERANCE, got)
    finally:
        def put_back() -> bool:
            t.set_machine_time(start + dt.timedelta(seconds=time.monotonic() - started))
            expected = start + dt.timedelta(seconds=time.monotonic() - started)
            return abs(t.machine_time() - expected) <= CLOCK_TOLERANCE
        t.restore("clock", put_back)


def aft_lock(t: WriteTest) -> None:
    """74 lock code 00 locks play (bit 30 of 0x412D38) and posts 6F, 80 cancels it. With
    real-time events on (0E, a flag in work RAM, off at power-up), 6F comes as an event."""
    try:
        t.check("0E real-time events on: ACK", acked(t.send("real-time-events", 1)))
        try:
            lock = t.read("aft-lock", 0, 0, LOCK_TIMEOUT)
            t.check("74 lock: locked", lock["lock_status"] == LOCKED, lock["lock_status"])
            t.check("6F game locked comes as an event", (GAME_LOCKED, True) in t.drain())
        finally:
            t.restore("AFT lock cancelled",
                      lambda: t.read("aft-lock", 0x80, 0, 0)["lock_status"] == NOT_LOCKED)
        t.drain()
    finally:
        t.restore("real-time events off", lambda: acked(t.send("real-time-events", 0)))
    t.drain()


def aft_registration(t: WriteTest) -> None:
    """73 code 00 stores a key and POS ID (status 00); 01 registers (01) once a non-zero key
    is stored; 80 unregisters (80) and posts 6E but keeps the key and POS ID, so the restore
    stores the ones found with 00 and unregisters again (FUN_0002fee4)."""
    before = t.registration()
    if not t.check("found unregistered", before["status"] == NOT_REGISTERED, before["status"]):
        return
    asset = before["asset"]
    try:
        ready = t.read("aft-register", 0, asset, TEST_KEY, TEST_POS_ID)
        t.check("73 00: registration ready", ready["status"] == REGISTRATION_READY, ready["status"])
        done = t.read("aft-register", 1, asset, TEST_KEY, TEST_POS_ID)
        t.check("73 01: registered with the test key and POS ID",
                (done["status"], done["registration_key"].replace(" ", ""), done["pos_id"])
                == (REGISTERED, TEST_KEY, TEST_POS_ID), done)
        t.check("74 shows AFT registered", int(t.aft_status()["aft_status"], 16) & AFT_REGISTERED_BIT)
        gone = t.read("aft-register", 0x80)
        t.check("73 80: unregistered", gone["status"] == NOT_REGISTERED, gone["status"])
        t.check("6E registration cancelled",
                any(code == REGISTRATION_CANCELLED for code, _ in t.drain()))
    finally:
        def put_back() -> bool:
            t.read("aft-register", 0, asset, before["registration_key"].replace(" ", ""),
                   before["pos_id"])
            t.read("aft-register", 0x80)
            t.drain()
            return t.registration() == before
        t.restore("registration as found", put_back)


def bills_and_delay(t: WriteTest) -> None:
    """07 clears the bill mask (0x43CDAA, SAFERAM), 08 replaces it and 06 turns every bill on
    (0x2A42C, FUN_0002b94e, 0x2A3B8); 2E writes the game delay (0x43D312, SAFERAM) and
    answers only with option 92 on (FUN_0002af0e). No poll reads either, so only the ACKs
    show. End with all bills enabled and no game delay, not their unknown original values."""
    try:
        t.check("07 bills off: ACK", acked(t.send("bills-off")))
        t.check("08 $1 bills only: ACK", acked(t.send("configure-bills", ONE_DOLLAR_BILLS, KEEP_ENABLED)))
    finally:
        t.restore("all bills on", lambda: acked(t.send("bills-on")))
    try:
        t.check("2E game delay: ACK", acked(t.send("delay-game", TEST_DELAY)))
    finally:
        t.restore("no game delay", lambda: acked(t.send("delay-game", 0)))


def status_only(t: WriteTest) -> None:
    """With nothing in progress these answer with a status, or stay silent where an option
    is off: AA 00 with auto rebet off, 94 and A8 with no handpay (only with their options
    on), 58 with no cashout waiting for a validation number (only with system validation),
    an empty 75, 4C with ID 0 (only with secure enhanced validation), and 7B, 7C and 7D with
    nothing to set. Only the last three change anything: 7B posts 3F and clears the
    validation-ID flag (0x41D992, set only by 4C); 7C and 7D clear their "ticket data
    received" flags (0x43D442 in SAFERAM, 0x41D988) after answering."""
    t.check("AA 00 auto rebet off: ACK", acked(t.send("auto-rebet", 0)))
    for name, values, field, want in (
            ("reset-handpay", (), "reset_code", NOT_IN_HANDPAY),
            ("handpay-reset-method", (0,), "ack_code", NOT_IN_HANDPAY),
            ("validation-number", (0, 0), "status", NOT_VALIDATING)):
        result = t.send(name, *values)
        t.check(f"{result['code']} {name}: {want:02X} or no reply",
                silent(result) or result.get("reply", {}).get(field) == want, result.get("reply"))
    t.check("75 empty receipt data: ACK", acked(t.send("aft-receipt-data")))
    t.send("set-validation-id", 0, 0)
    status = t.send("validation-status", 0, 0, 0, 0)
    t.check("7B answers", "reply" in status, status.get("error"))
    t.check("7B posts 3F", any(code == VALIDATION_ID_NOT_CONFIGURED for code, _ in t.drain()))
    for name in ("extended-ticket-data", "ticket-data-set"):
        result = t.send(name)
        t.check(f"{result['code']} {name} with no data answers", "reply" in result, result.get("error"))


def run(host: sas.Host, report: Callable[[dict], None]) -> bool:
    """Run the non-money steps; True when all read-backs and ACK checks pass."""
    t = WriteTest(host, report)
    try:
        found = t.snapshot()
    except (sas.ProtocolError, StepFailed) as e:
        t.check("machine answers", False, e)
        return False
    play_shown = False
    for step in (enable_game, shutdown, sound, clock, aft_lock, aft_registration, bills_and_delay,
                 status_only):
        report({"step": step.__name__, "about": step.__doc__.split(". ")[0]})
        if step is shutdown and not play_shown:
            t.check("shutdown not run: the 6E probe did not show play disabled", False)
            continue
        try:
            result = step(t)
        except (sas.ProtocolError, StepFailed) as e:
            t.check(f"{step.__name__} ran to the end", False, e)
            continue
        if step is enable_game:
            play_shown = result
    report({"step": "as_found", "about": "play, the AFT status and the registration read as before"})
    t.restore("machine as found", lambda: t.snapshot() == found)
    return t.failures == 0
