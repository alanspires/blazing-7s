import json
import unittest

import sas
import sas_commands as sc
import sas_protocol as protocol
from sas_tables import IDLE_EXCEPTIONS
from sas_testing import (
    REPO,
    ROM_SIGNATURES,
    FakeLine,
    FakeS6000,
    run_cli,
    sample_values,
)

ADDRESS = 1


def sample_reply(command: sc.Command) -> tuple[bytes, object]:
    """A valid reply message to `command` and the reply the CLI should report for it."""
    if command.reply is None:
        return bytes((ADDRESS,)), "ACK"
    texts, fields = sample_values(command.reply)
    data = sc.encode(command.reply, texts, length_byte=command.reply_length)
    return protocol.with_crc(bytes((ADDRESS, command.code)) + data), json.loads(json.dumps(dict(fields)))


def request_values(command: sc.Command, message: bytes) -> list[tuple[str, object]]:
    """The values a long poll message carries, checking its address, command and CRC."""
    assert message[:2] == bytes((ADDRESS, command.code)), message.hex(" ")
    data = protocol.check_crc(message)[2:] if command.crc else message[2:]
    return sc.decode(command.request, data, length_byte=command.request_length)


class EchoLine(FakeLine):
    """An adapter with its transmit and receive pins joined."""

    def read_message(self, timeout):
        return self.messages()[-1]


class EveryCommandTest(unittest.TestCase):
    def test_each_long_poll_sends_its_values_and_reports_the_reply(self):
        for command in sc.COMMANDS.values():
            if command.name in sas.SPECIAL:
                continue
            with self.subTest(command.name):
                texts, expected_request = sample_values(command.request)
                message, expected_reply = sample_reply(command)
                line = FakeLine([message])
                code, out, _ = run_cli(["--json", command.name, *texts], line)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out)["reply"], expected_reply)
                self.assertEqual(request_values(command, line.messages()[-1]), expected_request)

    def test_each_command_has_help(self):
        for command in sc.COMMANDS.values():
            with self.subTest(command.name):
                code, out, _ = run_cli(["help", command.name])
                self.assertEqual(code, 0)
                self.assertTrue(out)

    def test_list_shows_every_command_once(self):
        _, out, _ = run_cli(["list"])
        self.assertEqual([int(line.split()[0], 16) for line in out.splitlines()], list(sc.COMMANDS))


class SpecialCommandTest(unittest.TestCase):
    def test_dump_sends_every_read_command_with_its_read_values(self):
        reads = [c for c in sc.COMMANDS.values() if c.read is not None]
        line = FakeLine([sample_reply(c)[0] for c in reads])
        code, out, _ = run_cli(["--json", "dump"], line)
        self.assertEqual(code, 0)
        results = [json.loads(r) for r in out.splitlines()]
        self.assertEqual([(r["command"], r["values"]) for r in results], [(c.name, list(c.read)) for c in reads])
        self.assertTrue(all("reply" in r for r in results))
        polls = [m for m in line.messages() if m[0] == ADDRESS]
        for command, message in zip(reads, polls):
            self.assertEqual(request_values(command, message),
                             sc.decode(command.request, sc.encode_request(command, list(command.read)),
                                       length_byte=command.request_length))

    def test_dump_fails_only_when_nothing_answers(self):
        self.assertEqual(run_cli(["dump"], FakeLine())[0], 1)

    def test_machine_tests_report_every_record_and_pass_on_a_faithful_machine(self):
        for argv in (["--json", "write-test"], ["write-test"], ["--json", "money-test"], ["money-test"]):
            with self.subTest(argv=argv):
                code, out, _ = run_cli(argv, FakeS6000())
                self.assertEqual(code, 0)
                self.assertTrue(out)
                if "--json" in argv:
                    records = [json.loads(line) for line in out.splitlines()]
                    self.assertTrue(all(r["ok"] for r in records if "check" in r))

    def test_poll_prints_every_event_but_idle_until_interrupted(self):
        events = [bytes((0x11,)), bytes((min(IDLE_EXCEPTIONS),)), bytes((0x12,)),
                  protocol.with_crc(bytes((ADDRESS, protocol.EVENT, 0x7E)))]
        code, out, _ = run_cli(["poll"], FakeLine(events, then=KeyboardInterrupt()))
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), len(events) - 1)

    def test_find_reports_the_answering_address(self):
        code, out, _ = run_cli(["find"], FakeLine([bytes((0x1F,))]))
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1)

    def test_raw_sends_the_bytes_with_a_crc_on_request(self):
        line = FakeLine([bytes((ADDRESS,))])
        code, _, _ = run_cli(["raw", "21", "00", "00", "--crc"], line)
        self.assertEqual(code, 0)
        self.assertEqual(line.messages()[-1], protocol.with_crc(bytes((ADDRESS, 0x21, 0, 0))))

    def test_rom_signature_matches_the_images_as_the_machine_reported(self):
        fixture = json.loads(ROM_SIGNATURES.read_text())
        images = [str(REPO / path) for path in fixture["images"]]
        for reported in fixture["signatures"]:
            with self.subTest(seed=reported["seed"]):
                signature = int(reported["signature"], 0).to_bytes(2, "little")
                result = protocol.with_crc(bytes((ADDRESS, protocol.LONG_POLL_ROM_SIGNATURE)) + signature)
                line = FakeLine([bytes((ADDRESS,)), b"", bytes((min(IDLE_EXCEPTIONS),)), result])
                self.assertEqual(run_cli(["rom-signature", reported["seed"], *images], line)[0], 0)
                request = next(m for m in line.messages() if m[0] == ADDRESS)
                command = sc.find("rom-signature")
                self.assertEqual(request_values(command, request), [("seed", int(reported["seed"], 0))])

    def test_rom_signature_mismatch_fails(self):
        fixture = json.loads(ROM_SIGNATURES.read_text())
        reported = fixture["signatures"][0]
        wrong = protocol.with_crc(bytes((ADDRESS, protocol.LONG_POLL_ROM_SIGNATURE)) + bytes(2))
        images = [str(REPO / path) for path in fixture["images"]]
        self.assertEqual(run_cli(["21", reported["seed"], *images], FakeLine([bytes((ADDRESS,)), wrong]))[0], 1)

    def test_selftest_passes_only_on_its_own_echo(self):
        self.assertEqual(run_cli(["selftest"], FakeLine())[0], 1)
        self.assertEqual(run_cli(["selftest"], EchoLine())[0], 0)


class ErrorTest(unittest.TestCase):
    def test_unknown_command_and_bad_values_are_usage_errors(self):
        for argv in (["no-such-command"], ["help", "no-such-command"], ["set-date-time", "1017"],
                     ["delay-game", "70000"], ["raw", "zz"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as exit_:
                run_cli(argv, FakeLine())
            self.assertEqual(exit_.exception.code, 2)

    def test_serial_commands_without_a_port_are_usage_errors_before_opening_one(self):
        for argv in (["meters"], ["dump"], ["selftest"], ["extension-info"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as exit_:
                run_cli(argv)  # no line: no --port, and opening a port fails the test
            self.assertEqual(exit_.exception.code, 2)

    def test_silent_machine_fails(self):
        code, out, _ = run_cli(["--json", "meters"], FakeLine())
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(out))

    def test_nack_fails(self):
        self.assertEqual(run_cli(["shutdown"], FakeLine([bytes((ADDRESS | protocol.NACK_FLAG,))]))[0], 1)


if __name__ == "__main__":
    unittest.main()
