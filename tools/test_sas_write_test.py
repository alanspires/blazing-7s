import unittest
from functools import partial

import sas_commands as sc
import sas_protocol as protocol
import sas_write_test
from sas_testing import FakeS6000, run_machine_test

run = partial(run_machine_test, sas_write_test.run)


def sent_commands(machine: FakeS6000) -> list[str]:
    return [sc.COMMANDS[rest[0]].name for first, rest in machine.sent
            if first == FakeS6000.ADDRESS and rest]


class WriteTestTest(unittest.TestCase):
    def test_every_check_passes_and_the_machine_is_left_as_found(self):
        machine = FakeS6000()
        found = machine.state()
        ok, records = run(machine)
        failed = [r for r in records if "check" in r and not r["ok"]]
        self.assertEqual(failed, [])
        self.assertTrue(ok)
        self.assertEqual(machine.state(), found)
        self.assertEqual(machine.bill_mask, machine.ALL_BILLS)
        self.assertEqual(machine.game_delay, 0)

    def test_final_bill_and_delay_commands_are_attempted_when_machine_goes_silent(self):
        for command, restore, values in (("configure-bills", "bills-on", []),
                                         ("delay-game", "delay-game", ["0"])):
            with self.subTest(command=command):
                machine = FakeS6000(silent_after=command)
                ok, _ = run(machine)
                self.assertFalse(ok)
                target = sc.BY_NAME[restore]
                expected = protocol.with_crc(bytes((machine.ADDRESS, target.code))
                                             + sc.encode_request(target, values))
                self.assertIn(expected, machine.messages())

    def test_startup_is_still_attempted_when_the_machine_goes_silent_after_shutdown(self):
        machine = FakeS6000(silent_after="shutdown")
        ok, records = run(machine)
        self.assertFalse(ok)
        after = sent_commands(machine)[sent_commands(machine).index("shutdown"):]
        self.assertIn("startup", after)
        self.assertTrue(any(not r["ok"] for r in records if "check" in r))
        self.assertEqual(machine.shutdown_flag, 2)

    def test_shutdown_is_not_sent_unless_the_probe_shows_disabled_play(self):
        machine = FakeS6000(shows_disable=False)
        ok, _ = run(machine)
        self.assertFalse(ok)
        self.assertNotIn("shutdown", sent_commands(machine))
        self.assertTrue(machine.game_enabled)


if __name__ == "__main__":
    unittest.main()
