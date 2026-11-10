#!/usr/bin/env python3
"""Talk SAS to the S6000 from Linux through a USB-RS232 adapter on backplane J10.

Every long poll the machine's firmware accepts is a command, by name or hex code; values
follow in field order. Commands that talk to the machine need --port, the serial device
(for example --port /dev/ttyUSB0); list, help, profile-stock and profile-preview do not.
From another computer, `tools/sas` runs this over SSH on the Linux host named by SAS_HOST.

  sas.py list                          every command, its code and the values it takes
  sas.py help aft-transfer             one command's fields, request and reply
  sas.py meters                        a long poll by name (or: sas.py 1C)
  sas.py game-meters 0                 values in field order (here: game number)
  sas.py set-date-time 10172026 233000
  sas.py dump                          every command that only reads
  sas.py write-test                    non-money writes; ends with bills/sound on, delay zero
  sas.py money-test                    add two credits, withdraw ALL, restore starting balance
  sas.py poll                          print events until Ctrl-C
  sas.py rom-signature 0x1234 roms/main.bin roms/game.bin
                                       ROM signature, compared with ROM images if given
  sas.py find                          which address answers
  sas.py raw 21 00 00 --crc            any bytes after the address, raw reply
  sas.py selftest                      adapter loopback (DB9 pins 2 and 3 joined)

The firmware extension (firmware/INTEGRATION.md) answers its own private long poll, which is
not in `list`. Each change reads INFO first and is sent once under the next mutation ID; when
no valid reply comes back the ID is reported with status unknown and nothing is repeated.
Stops and reels are zero-based. ACCEPTED means taken on, not finished: see extension-status.

  sas.py extension-info                capabilities, limits and the next mutation ID
  sas.py extension-status              the last request and its state, the active profile
  sas.py profile-stock > stock.json    the installed game as a profile (offline)
  sas.py profile-preview FILE [BET]    nominal return and hit frequency per bet (offline)
  sas.py profile-download > p.json     the active profile, read back and CRC-checked
  sas.py profile-upload FILE           stage, commit and read back a JSON profile
  sas.py profile-restore               return to the stock game
  sas.py outcome-arm S0 S1 S2          the next normal game stops there
  sas.py outcome-cancel                drop an armed outcome no game has taken yet
  sas.py game-spin BET [S0 S1 S2]      bet BET per enabled line and spin, optionally to stops
  sas.py reels-position S0 S1 S2       diagnostic: move the reels to stops
  sas.py reels-jog REEL                diagnostic: step one reel
  sas.py --id N game-spin 1            repeat request N unchanged: the machine answers with
                                       its recorded state instead of acting again

The tools/sas wrapper separately provides pre-boot commissioning (not normal SAS):
  tools/sas commission-info
  tools/sas commission-capture ORIGINAL.json
  tools/sas commission-initialize ORIGINAL.json --first-install
See firmware/PROVISIONING.md. This path is offline-tested, not hardware-qualified.

Numbers are decimal or 0x.. hex; BCD values are digits (a date is 10172026).
Wiring and checks: notes/sas-j10.md. Needs pyserial (`pip install pyserial`).
"""

from __future__ import annotations

import argparse
import json
import os
import select
import sys
import time

import sas_commands as sc
import sas_extension as se
import sas_money_test
import sas_profile as sp
import sas_protocol as sas
import sas_write_test
from sas_client import failure, poll
from sas_tables import DENOMINATIONS, EXCEPTIONS, IDLE_EXCEPTIONS, METERS

BAUD = 19200  # §1.2
FRAME_TIME = 11 / BAUD  # start, 8 data, wakeup and stop bits
MARK, SPACE = "M", "S"  # pyserial's PARITY_MARK and PARITY_SPACE
# A reply has ended once the line has been quiet this long: the machine may pause 5 ms between
# bytes (§2.3.2) and the FTDI chip forwards received bytes every 16 ms by default.
MESSAGE_GAP = 0.05
INTER_BYTE_LIMIT = 0.005  # §2.3.2
POLL_INTERVAL = 0.25  # §2.3.3: no faster than one poll per 200 ms per machine
FIND_TIMEOUT = 0.1  # find polls 127 addresses, each up to 3 times
# The S6000 stops answering polls for about 8 s while it computes a signature; the result
# came 18 s after the request (notes/sas-j10.md, ROM signature).
ROM_SIGNATURE_POLLS = int(60 / POLL_INTERVAL)


class SerialLine:
    """The 9th (wakeup) bit is sent as mark parity, every other byte with space parity.
    Linux exposes both through CMSPAR; macOS does not. pyserial leaves input parity
    checking off, so replies read the same whichever parity is set."""

    def __init__(self, device: str):
        import serial  # only talking to the machine needs pyserial; list and help do not

        self.port = serial.Serial(device, BAUD, bytesize=serial.EIGHTBITS, parity=SPACE,
                                  stopbits=serial.STOPBITS_ONE, timeout=0, exclusive=True)
        self.last_gap = 0.0  # seconds from writing the wakeup byte to writing the next

    def send(self, first: int, rest: bytes) -> None:
        port = self.port
        port.reset_input_buffer()
        port.parity = MARK
        port.write(bytes((first,)))
        start = time.monotonic()
        port.flush()
        if rest:
            # The wakeup byte must leave the adapter before the parity changes, or it goes
            # out with the wrong 9th bit. tcdrain only waits until the adapter holds it: on
            # the FT4232H it returned 0.25 ms after the write, inside the 0.57 ms frame. One
            # more frame was always enough; two keep a margin and the gap to the next byte
            # near 1.4 ms, inside the 5 ms of §2.3.2.
            time.sleep(2 * FRAME_TIME)
            port.parity = SPACE
            port.write(rest)
            self.last_gap = time.monotonic() - start
            port.flush()

    def read_message(self, timeout: float) -> bytes:
        # select on the descriptor instead of pyserial's timeout property, whose setter
        # rewrites the whole termios state on every call.
        fd = self.port.fileno()
        deadline = time.monotonic() + timeout
        data = bytearray()
        while True:
            wait = MESSAGE_GAP if data else deadline - time.monotonic()
            if wait <= 0 or not select.select([fd], [], [], wait)[0]:
                return bytes(data)
            data += os.read(fd, 4096)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)


def describe(reply: int | sas.RomSignature | sas.RealTimeEvent) -> str:
    if isinstance(reply, sas.RomSignature):
        return f"ROM signature {reply.signature:04X}"
    if isinstance(reply, sas.RealTimeEvent):
        name = EXCEPTIONS.get(reply.exception, "unknown exception")
        return f"event {reply.exception:02X} {name} {reply.data.hex(' ')}".rstrip()
    return f"exception {reply:02X} {EXCEPTIONS.get(reply, 'unknown exception')}"


def annotate(name: str, value: object) -> str:
    if name == "denomination" and value in DENOMINATIONS:
        return f"{value} (${DENOMINATIONS[value]:g})"
    if name == "meter" and value in METERS:
        return f"{value:04X} {METERS[value][0]}"
    return str(value)


def print_result(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result), flush=True)
        return
    if "error" in result:
        print(f"{result['command']}: {result['error']}", file=sys.stderr, flush=True)
        for name in ("id", "status"):  # an extension request's ID and the machine's answer
            if name in result:
                print(f"{name}: {result[name]}", file=sys.stderr, flush=True)
        if result.get("data"):
            print(f"raw reply data: {result['data']}", file=sys.stderr, flush=True)
        return
    reply = result["reply"]
    if reply == "ACK":
        print("ACK")
        return
    for name, value in reply.items():
        if name != "items":
            print(f"{name}: {annotate(name, value)}")
            continue
        for item in value:
            print("  " + ", ".join(f"{k} {annotate(k, v)}" for k, v in item.items()))


def cmd_long_poll(line, args, command: sc.Command) -> int:
    result = poll(sas.Host(line, args.address), command, args.values)
    print_result(result, args.json)
    return 1 if "error" in result else 0


def cmd_dump(line, args) -> int:
    """Every command that only reads, with the values that make it a read. Some get no
    answer on a machine without the feature, so only a dump where nothing answers fails."""
    host = sas.Host(line, args.address)
    answered = 0
    for command in sc.COMMANDS.values():
        if command.read is None:
            continue
        result = poll(host, command, list(command.read))
        if not args.json:
            print(f"== {command.code:02X} {command.name} {' '.join(command.read)}".rstrip())
        print_result(result, args.json)
        answered += "error" not in result
        line.wait(POLL_INTERVAL)
    return 0 if answered else 1


def cmd_write_test(line, args) -> int:
    return 0 if sas_write_test.run(sas.Host(line, args.address), machine_test_reporter(args)) else 1


def cmd_money_test(line, args) -> int:
    return 0 if sas_money_test.run(sas.Host(line, args.address), machine_test_reporter(args)) else 1


def machine_test_reporter(args):
    """Print a machine test's records as they come: steps, polls, exceptions and checks."""
    def report(record: dict) -> None:
        if args.json:
            print(json.dumps(record), flush=True)
        elif "step" in record:
            print(f"== {record['step']}: {record['about']}", flush=True)
        elif "check" in record:
            verdict = "PASS" if record["ok"] else "FAIL"
            print(f"{verdict} {record['check']}" + (f": {record['detail']}" if record["detail"] else ""),
                  flush=True)
        elif "exception" in record:
            code = int(record["exception"], 16)
            print(f"   exception {code:02X} {EXCEPTIONS.get(code, 'unknown exception')}"
                  + (" (real-time event)" if record["real_time"] else ""), flush=True)
        else:
            outcome = record.get("error") or ("ACK" if record["reply"] == "ACK" else record["data"])
            print(f"   {record['code']} {record['command']} {' '.join(record['values'])} -> {outcome}",
                  flush=True)
    return report


def cmd_list(args) -> int:
    for c in sc.COMMANDS.values():
        values = " ".join(f.name for f in _flat(c.request))
        read = "read" if c.read is not None else "    "
        print(f"{c.code:02X} {c.name:28} {c.kind} {read} {c.title}" + (f"  [{values}]" if values else ""))
    return 0


def cmd_help(args) -> int:
    if not args.values:
        print(__doc__)
        return 0
    if args.values[0] in EXTENSION_COMMANDS:
        usage, _, about = EXTENSION_COMMANDS[args.values[0]]
        print(f"{args.values[0]} {usage}".rstrip() + f": {about}")
        if args.values[0] in ID_COMMANDS:
            print("--id N: repeat request N unchanged instead of sending a new one")
        return 0
    c = sc.find(args.values[0])
    print(f"{c.code:02X} {c.name}: {c.title} (type {c.kind}; {c.source})")
    print("request:" if c.request else "request: no values")
    for f in _flat(c.request):
        print(f"  {f.name} ({_shape(f)}){': ' + f.help if f.help else ''}")
    if c.request_length:
        print("  trailing values may be left off")
    print("reply: ACK or NACK" if c.reply is None else "reply:")
    for f in _flat(c.reply or ()):
        print(f"  {f.name} ({_shape(f)})")
    if c.read is not None:
        print(f"only reads with: {c.name} {' '.join(c.read)}".rstrip())
    return 0


def _flat(fields: tuple):
    for f in fields:
        if isinstance(f, sc.Repeat):
            yield from (sc.Field(x.name + " ...", x.size, x.enc, x.help) for x in f.fields)
        else:
            yield f


def _shape(f: sc.Field) -> str:
    size = {sc.COUNTED: "length-prefixed", sc.REST: "rest of message", sc.TABLE_C7: "Table C-7 size"}
    return f"{size.get(f.size, f'{f.size} byte' + 's' * (f.size != 1))} {f.enc}"


def cmd_selftest(line, args) -> int:
    message = bytes((0x01, 0x1F, 0x55, 0xAA, 0x00, 0xFF))
    line.send(message[0], message[1:])
    echo = line.read_message(0.5)
    print(f"sent {message.hex(' ')}\ngot  {echo.hex(' ') or '(nothing)'}")
    print(f"wakeup byte to next byte: about {line.last_gap * 1000:.1f} ms, host-side estimate "
          f"(SAS allows {INTER_BYTE_LIMIT * 1000:.0f} ms)")
    if echo != message:
        print("FAIL: join DB9 pins 2 and 3 on the breakout, nothing else connected")
        return 1
    print("OK: adapter, driver and mark/space switching work")
    return 0


def cmd_find(line, args) -> int:
    found = 0
    for address in range(1, 0x80):
        try:
            reply = sas.Host(line, address, FIND_TIMEOUT).general_poll()
        except sas.NoResponse:
            continue
        found += 1
        print(f"address {address}: {describe(reply)}")
    if not found:
        print("no address answered")
        return 1
    return 0


def cmd_poll(line, args) -> int:
    host = sas.Host(line, args.address)
    answering = True
    while True:
        try:
            reply = host.general_poll()
            answering = True
            if reply not in IDLE_EXCEPTIONS:
                print(f"{time.strftime('%H:%M:%S')} {describe(reply)}", flush=True)
        except sas.NoResponse:
            if answering:
                print(f"no reply from address {args.address}", file=sys.stderr, flush=True)
            answering = False
        line.wait(POLL_INTERVAL)


def cmd_rom_signature(line, args) -> int:
    """§6: the seed, then optionally the ROM images in the order the machine hashes them."""
    seed_field = sc.BY_NAME["rom-signature"].request[0]
    seed = int.from_bytes(sc.encode((seed_field,), args.values[:1] or ["0"]), "little")
    images = args.values[1:]
    expected = seed
    for path in images:  # §6.1: each ROM's signature seeds the next
        with open(path, "rb") as f:
            expected = sas.crc16(f.read(), expected)
    host = sas.Host(line, args.address)
    host.request_rom_signature(seed)
    for _ in range(ROM_SIGNATURE_POLLS):
        line.wait(POLL_INTERVAL)
        try:
            reply = host.general_poll()
        except sas.NoResponse:
            continue  # the machine is still computing
        if not isinstance(reply, sas.RomSignature):
            if reply not in IDLE_EXCEPTIONS:
                print(f"meanwhile: {describe(reply)}")
            continue
        print(f"ROM signature for seed {seed:04X}: {reply.signature:04X}")
        if not images:
            return 0
        print(f"images give {expected:04X}: {'MATCH' if reply.signature == expected else 'MISMATCH'}")
        return 0 if reply.signature == expected else 1
    print(f"no ROM signature after {ROM_SIGNATURE_POLLS} polls", file=sys.stderr)
    return 1


def cmd_raw(line, args) -> int:
    try:
        body = bytes((args.address,)) + bytes.fromhex("".join(args.values))
    except ValueError as e:
        raise sc.ArgumentError(f"raw: {e}") from e
    if args.crc:
        body = sas.with_crc(body)
    sas.Host(line, args.address).send(body[0], body[1:])
    reply = line.read_message(sas.Host.RESPONSE_TIMEOUT)
    print(f"sent {body.hex(' ')}\ngot  {reply.hex(' ') or '(nothing)'}")
    return 0 if reply else 1


# name: (values, how many values it takes, what it does)
EXTENSION_COMMANDS = {
    "extension-info": ("", (0,), "the extension's capabilities, limits and next mutation ID"),
    "extension-status": ("", (0,), ("the last request and its state, the active profile's "
                                    "generation and CRC")),
    "profile-stock": ("", (0,), "print the installed game as a JSON profile; offline"),
    "profile-preview": ("FILE [BET]", (1, 2), ("nominal return and hit frequency of a JSON profile "
                                              "for each bet per line, or BET; offline")),
    "profile-download": ("", (0,), ("print the active profile as JSON, read back within INFO's "
                                    "limits and checked against its CRC")),
    "profile-upload": ("FILE", (1,), ("validate a JSON profile within INFO's limits, stage and "
                                      "commit it, and read the committed profile back")),
    "profile-restore": ("", (0,), "return to the stock game, confirmed by STATUS"),
    "outcome-arm": ("S0 S1 S2", (3,), "the next normal game stops at these zero-based stops"),
    "outcome-cancel": ("", (0,), "drop an armed outcome no game has taken yet"),
    "game-spin": ("BET [S0 S1 S2]", (1, 4), ("bet BET credits on each enabled line and spin, "
                                            "optionally to zero-based stops")),
    "reels-position": ("S0 S1 S2", (3,), "diagnostic: move the reels to these zero-based stops"),
    "reels-jog": ("REEL", (1,), "diagnostic: step one reel, zero-based"),
}
ACTIONS = {"outcome-arm": se.Op.ARM_OUTCOME, "outcome-cancel": se.Op.CANCEL_OUTCOME,
           "game-spin": se.Op.BET_SPIN, "reels-position": se.Op.POSITION, "reels-jog": se.Op.JOG}
ID_COMMANDS = frozenset(ACTIONS) | {"profile-restore"}  # one request each, so --id names it


def extension_values(args) -> list[str]:
    """The command's values once their count and --id are right for it."""
    usage, counts, _ = EXTENSION_COMMANDS[args.command]
    if len(args.values) not in counts:
        raise sc.ArgumentError(f"{args.command} takes {usage or 'no values'}")
    if args.id is not None and args.command not in ID_COMMANDS:
        raise sc.ArgumentError(f"--id: {args.command} does not take an ID")
    if args.id is not None and not 1 <= args.id <= se.ID_MAX:
        raise sc.ArgumentError(f"--id {args.id}: mutation IDs are 1..{se.ID_MAX}")
    return args.values


def byte_values(name: str, values: list[str]) -> bytes:
    try:
        return bytes(int(v, 0) for v in values)
    except ValueError as e:
        raise sc.ArgumentError(f"{name}: values are numbers 0-255: {e}") from e


def prepare_extension(args):
    """Check the values before anything is sent; return the request run as ext -> reply."""
    values = extension_values(args)
    if args.command in ACTIONS:
        op, payload = ACTIONS[args.command], byte_values(args.command, values)
        return lambda ext: se.act(ext, op, payload, args.id)
    if args.command == "profile-upload":
        return lambda ext: se.upload(ext, values[0])
    if args.command == "profile-restore":
        return lambda ext: se.restore(ext, args.id)
    return {"extension-info": se.info, "extension-status": se.status,
            "profile-download": se.download}[args.command]


def cmd_extension(line, args, request) -> int:
    """profile-download prints only the profile on stdout, so it can be redirected to a file;
    its read-back state and any error go to stderr."""
    result: dict = {"command": args.command}
    try:
        reply = request(se.Extension(sas.Host(line, args.address)))
    except se.UncertainOutcome as e:
        result |= failure(e) | {"id": e.id, "status": "unknown"}
    except se.Refused as e:
        result |= failure(e) | {"id": e.id, "status": e.status.name}
    except (se.ExtensionError, sp.ProfileError, sas.ProtocolError, OSError, json.JSONDecodeError) as e:
        result |= failure(e)
    except KeyboardInterrupt as e:  # an unfinished request, e.g. an unverified upload, is not success
        result |= failure(e) | {"error": "interrupted"}  # an interrupt's own message is empty
    else:
        if args.command != "profile-download":
            print_result(result | {"reply": reply}, args.json)
            return 0
        profile, machine = reply
        print(json.dumps(sp.to_json(profile), indent=2), flush=True)
        for name, value in machine.report().items():
            print(f"{name}: {value}", file=sys.stderr, flush=True)
        return 0
    print_result(result, args.json and args.command != "profile-download")
    return 1


def cmd_profile_stock(args) -> int:
    extension_values(args)
    print(json.dumps(sp.to_json(sp.stock_profile()), indent=2))
    return 0


def cmd_profile_preview(args) -> int:
    path, *bet = extension_values(args)
    try:
        profile = sp.read_json(path)
    except (sp.ProfileError, OSError, json.JSONDecodeError) as e:
        print_result({"command": args.command} | failure(e), args.json)
        return 1
    rows = sp.preview(profile)
    if bet:
        wanted = byte_values(args.command, bet)[0]
        if not 1 <= wanted <= profile.max_bet_per_line:
            raise sc.ArgumentError(f"{args.command}: BET is 1..{profile.max_bet_per_line} for this profile")
        rows = tuple(row for row in rows if row.bet_per_line == wanted)
    items = [{"bet_per_line": row.bet_per_line, "total_bet": row.total_bet,
              "return_percent": float(row.nominal_return * 100), "hit_percent": float(row.hit_frequency * 100),
              "nominal_return": str(row.nominal_return), "hit_frequency": str(row.hit_frequency),
              "outcome_mass": row.outcome_mass, "hit_mass": row.hit_mass, "award_mass": row.award_mass,
              "progressive_award_mass": row.progressive_award_mass} for row in rows]
    print_result({"command": args.command, "reply": {"items": items}}, args.json)
    return 0


OFFLINE = {"list": cmd_list, "help": cmd_help, "profile-stock": cmd_profile_stock,
           "profile-preview": cmd_profile_preview}
SPECIAL = {"selftest": cmd_selftest, "find": cmd_find, "poll": cmd_poll, "raw": cmd_raw,
           "dump": cmd_dump, "write-test": cmd_write_test, "money-test": cmd_money_test,
           "rom-signature": cmd_rom_signature}


def main(argv: list[str] | None = None, open_line=SerialLine) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="the serial device; every command but the offline ones needs it")
    parser.add_argument("--address", type=int, default=1, help="SAS address, machine option 78Hi")
    parser.add_argument("--json", action="store_true", help="print long poll results as JSON")
    parser.add_argument("--crc", action="store_true", help="raw: append the message CRC")
    parser.add_argument("--id", type=int, help="extension: repeat this request ID unchanged")
    parser.add_argument("command", help="list, help, a command name or hex code, or a special command")
    parser.add_argument("values", nargs="*", help="the command's values, in field order")
    args = parser.parse_intermixed_args(argv)

    def serial_line():
        if args.port is None:
            parser.error(f"--port: {args.command} talks to the machine; give the serial device")
        return open_line(args.port)

    try:
        if args.id is not None and args.command not in EXTENSION_COMMANDS:
            raise sc.ArgumentError(f"--id: {args.command} does not take an ID")
        if args.command in OFFLINE:
            return OFFLINE[args.command](args)
        if args.command in EXTENSION_COMMANDS:
            request = prepare_extension(args)
            return cmd_extension(serial_line(), args, request)
        command = None if args.command in SPECIAL else sc.find(args.command)
        special = SPECIAL.get(command.name if command else args.command)
        line = serial_line()
        return special(line, args) if special else cmd_long_poll(line, args, command)
    except sc.UnknownCommand as e:
        parser.error(f"unknown command {e}; `list` shows them all")
    except sc.ArgumentError as e:
        parser.error(str(e))
    except sas.NoResponse as e:
        print(f"{e}: check wiring, TX/RX swap, address (option 78Hi) and Primary Host (option 98)",
              file=sys.stderr)
    except (sas.ProtocolError, OSError) as e:
        print(e, file=sys.stderr)
    except KeyboardInterrupt:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
