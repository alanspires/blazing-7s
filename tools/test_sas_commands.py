import json
import unittest

import sas_commands as sc
import sas_protocol as sas
from sas_tables import DENOMINATIONS, METERS
from sas_testing import (
    FIXTURES,
    MAIN_ROM,
    REPO,
    ROM_SIGNATURES,
    read_jsonl,
    sample_values,
)

TABLE = 0x744CA  # sas_rx_byte's table, indexed by command: code, bytes following, 00, handler
VARIABLE, UNSUPPORTED = 0xFE, 0xFF
DUMP = FIXTURES / "s6000_dump.jsonl"  # `tools/sas --json dump` (notes/sas-j10.md)
WRITE_TEST = FIXTURES / "s6000_write_test.jsonl"  # `tools/sas --json write-test`
MONEY_TEST = FIXTURES / "s6000_money_test.jsonl"  # `tools/sas --json money-test`


def firmware_commands() -> dict[int, int]:
    rom = MAIN_ROM.read_bytes()
    entries = (rom[TABLE + 6 * code:TABLE + 6 * code + 2] for code in range(256))
    return {code: following for code, following in entries if following != UNSUPPORTED}


def to_bcd(value: int, size: int) -> bytes:
    return bytes.fromhex(f"{value:0{2 * size}d}")


class TableTest(unittest.TestCase):
    def test_commands_are_the_firmwares(self):
        self.assertEqual(set(sc.COMMANDS), set(firmware_commands()))

    def test_request_lengths_match_the_firmware(self):
        for code, following in firmware_commands().items():
            command = sc.COMMANDS[code]
            expected = VARIABLE if command.request_length else (
                sum(f.size for f in command.request) + 2 * command.crc)
            self.assertEqual(expected, following, command.name)

    def test_read_values_are_valid_requests(self):
        for command in sc.COMMANDS.values():
            if command.read is not None:
                with self.subTest(command.name):
                    sc.encode_request(command, list(command.read))


class EveryCommandTest(unittest.TestCase):
    def test_request_values_round_trip(self):
        for command in sc.COMMANDS.values():
            with self.subTest(command.name):
                texts, expected = sample_values(command.request)
                data = sc.encode_request(command, texts)
                self.assertEqual(sc.decode(command.request, data, command.request_length), expected)

    def test_reply_fields_round_trip(self):
        for command in sc.COMMANDS.values():
            if command.reply is None:
                continue
            with self.subTest(command.name):
                texts, expected = sample_values(command.reply)
                data = sc.encode(command.reply, texts, length_byte=command.reply_length)
                self.assertEqual(sc.decode_reply(command, data), expected)


class CodecTest(unittest.TestCase):
    def test_selected_meters_sizes_come_from_table_c7(self):
        codes = (0x00, 0x0D)  # a 4-byte and a 5-byte meter
        data = to_bcd(0, 2) + b"".join(bytes((c,)) + to_bcd(c + 7, METERS[c][1]) for c in codes)
        fields = dict(sc.decode_reply(sc.find("2F"), bytes((len(data),)) + data))
        self.assertEqual(fields["items"], [{"meter": c, "value": c + 7} for c in codes])

    def test_short_variable_length_reply_ends_early(self):
        command = sc.find("hopper-status")  # level is only sent when the machine knows it
        fields = sc.decode_reply(command, bytes((2, 0x00, 0xFF)))
        self.assertEqual([name for name, _ in fields], [f.name for f in command.reply[:2]])

    def test_bytes_beyond_the_layout_are_kept(self):
        data = bytes(range(len(sc.encode_request(sc.find("set-date-time"), ["0", "0"])))) + b"\x99"
        self.assertIn("extra", dict(sc.decode(sc.find("set-date-time").request, data)))

    def test_variable_length_request_may_leave_off_trailing_fields(self):
        self.assertEqual(sc.encode_request(sc.find("aft-transfer"), ["0xFF", "0"]), bytes((2, 0xFF, 0)))

    def test_fixed_length_request_needs_every_field(self):
        with self.assertRaises(sc.ArgumentError):
            sc.encode_request(sc.find("set-date-time"), ["10172026"])

    def test_value_too_big_for_its_field_is_rejected(self):
        with self.assertRaises(sc.ArgumentError):
            sc.encode_request(sc.find("delay-game"), [str(1 << 16)])

    def test_commands_are_found_by_name_or_hex_code(self):
        self.assertIs(sc.find("1c"), sc.find("meters"))
        self.assertIs(sc.find("0x1C"), sc.find("meters"))
        with self.assertRaises(sc.UnknownCommand):
            sc.find("no-such-command")


class MachineRepliesTest(unittest.TestCase):
    """Replies the S6000 sent to `tools/sas --json dump`, decoded with today's layouts."""

    @classmethod
    def setUpClass(cls):
        cls.results = {result["command"]: result for result in read_jsonl(DUMP)}
        cls.fields = {name: dict(sc.decode_reply(sc.BY_NAME[name], bytes.fromhex(r["data"])))
                      for name, r in cls.results.items() if r.get("data")}

    def test_dump_covers_every_read_command(self):
        reads = {c.name: list(c.read) for c in sc.COMMANDS.values() if c.read is not None}
        self.assertEqual({name: r["values"] for name, r in self.results.items()}, reads)

    def test_every_answer_fits_its_layout(self):
        for name, fields in self.fields.items():
            with self.subTest(name):
                self.assertNotIn("extra", fields)
                bcd = [f.name for f in sc.BY_NAME[name].reply if isinstance(f, sc.Field) and f.enc == sc.BCD]
                self.assertFalse([n for n in bcd if str(fields.get(n, "")).startswith("0x")])

    def test_meters_agree_across_polls(self):
        f = self.fields
        selected = {i["meter"]: i["value"] for i in f["selected-meters"]["items"]}
        extended = {i["meter"]: i["value"] for i in f["extended-meters"]["items"]}
        # Table C-7 code, the single-meter poll, and other polls' fields holding the same meter
        for code, single, others in (
                (0x00, "coin-in", (("meters", "coin_in"), ("meters-10-15", "coin_in"), ("game-meters", "coin_in"))),
                (0x01, "coin-out", (("meters", "coin_out"), ("game-meters", "coin_out"))),
                (0x05, "games-played", (("meters", "games_played"), ("game-meters", "games_played"))),
                (0x0C, "credits", ())):
            with self.subTest(meter=code):
                value = f[single]["value"]
                self.assertEqual((selected[code], extended[code]), (value, value))
                for poll, field in others:
                    self.assertEqual(f[poll][field], value, poll)

    def test_credits_agree_with_the_aft_cashable_amount(self):
        credits = self.fields["credits"]["value"]
        dollars = DENOMINATIONS[self.fields["info"]["denomination"]]
        self.assertEqual(self.fields["aft-lock"]["cashable_cents"], round(credits * dollars * 100))

    def test_every_machine_test_reply_fits_its_layout(self):
        for fixture in (WRITE_TEST, MONEY_TEST):
            records = read_jsonl(fixture)
            for r in (r for r in records if r.get("data")):
                with self.subTest(fixture=fixture.name, command=r["command"], values=r["values"]):
                    fields = dict(sc.decode_reply(sc.BY_NAME[r["command"]], bytes.fromhex(r["data"])))
                    self.assertNotIn("extra", fields)
                    self.assertEqual(fields, r["reply"])

    def test_live_money_round_trip_balances_and_transfer_ids_match(self):
        records = read_jsonl(MONEY_TEST)
        credits = [r["reply"]["value"] for r in records if r.get("command") == "credits"]
        self.assertEqual(credits[0], credits[-1])
        self.assertIn(0, credits)
        denomination = next(r["reply"]["denomination"] for r in records if r.get("command") == "info")
        cents_per_credit = round(DENOMINATIONS[denomination] * 100)
        bonus = sum(int(r["values"][0]) for r in records if r.get("command") == "legacy-bonus")
        requests = [r for r in records if r.get("command") == "aft-transfer" and int(r["values"][0]) == 0]
        completions = {r["reply"]["transaction_id"]: r["reply"] for r in records
                       if r.get("command") == "aft-transfer" and int(r["values"][0]) == 0xFF
                       and r["reply"]["status"] == 0}
        net_cents = bonus * cents_per_credit
        for r in requests:
            c = sc.BY_NAME[r["command"]]
            request = dict(sc.decode(c.request, sc.encode_request(c, r["values"]), c.request_length))
            reply = completions[request["transaction_id"]]
            self.assertEqual((reply["type"], reply["cashable_cents"]),
                             (request["type"], request["cashable_cents"]))
            net_cents += reply["cashable_cents"] * (1 if reply["type"] == 0 else -1)
        self.assertEqual(net_cents, (credits[-1] - credits[0]) * cents_per_credit)
        meters = [r["reply"]["deductible"] for r in records if r.get("command") == "legacy-bonus-meters"]
        self.assertEqual(meters[-1] - meters[0], bonus)

    def test_rom_signatures_match_the_images(self):
        fixture = json.loads(ROM_SIGNATURES.read_text())
        images = [(REPO / path).read_bytes() for path in fixture["images"]]
        for reported in fixture["signatures"]:
            with self.subTest(seed=reported["seed"]):
                signature = int(reported["seed"], 0)
                for image in images:
                    signature = sas.crc16(image, signature)
                self.assertEqual(signature, int(reported["signature"], 0))


if __name__ == "__main__":
    unittest.main()
