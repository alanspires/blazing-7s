"""Unit tests for tools/sas_options.py (stock SAS 1F/A0 summary and SafeRAM setup options)."""

from __future__ import annotations

import unittest

import sas_options as so


class SasOptionsTests(unittest.TestCase):
    def test_bcd_roundtrip_and_invalid_nibbles(self):
        for width, value in ((2, 0), (2, 125), (2, 9999), (3, 800), (3, 8000), (3, 999999)):
            packed = so.encode_bcd(value, width)
            self.assertEqual(len(packed), width)
            self.assertEqual(so.decode_bcd(packed), value)
        self.assertEqual(so.decode_bcd(b"\xAB\x00"), -1)
        with self.assertRaises(ValueError):
            so.encode_bcd(10000, 2)

    def test_decode_setup_options_maps_progressive_host_and_bcd_fields(self):
        raw = so.default_options_block(progressive_type=4, external_jackpots=1, sas_address=1, progressive_id=1)
        rows = {row.code: row for row in so.decode_setup_options(raw)}
        self.assertEqual(rows["02"].raw, "0004")
        self.assertIn("S/MPI", rows["02"].meaning)
        self.assertEqual(rows["07"].raw, "0001")
        self.assertEqual(rows["07"].meaning, "One")
        self.assertEqual(rows["78Hi"].raw, "0001")
        self.assertEqual(rows["78Lo"].raw, "0001")
        self.assertEqual(rows["56"].raw, "000800")
        self.assertEqual(rows["67"].raw, "008000")
        self.assertEqual(rows["98"].raw, "0001")
        self.assertEqual(rows["98"].meaning, "SAS Host")
        self.assertEqual(rows["68"].raw, "0001")
        self.assertEqual(rows["Snd"].raw, "0001")
        self.assertEqual(rows["Reel"].raw, "0001")
        with self.assertRaises(ValueError):
            so.decode_setup_options(raw[:-1])

    def test_decode_sas_summary_reflects_game_options_progressive_bits_and_features(self):
        base_info = {"denomination": 6, "progressive_group": 0, "game_options": "0x0168",
                     "paytable": "E59521", "base_percent": "9085"}
        features = {"features_1": "0xA6", "features_2": "0x5D", "features_3": "0x01"}
        rows = {row.code: row for row in so.decode_sas_summary(base_info, features)}
        self.assertEqual(rows["07/02"].raw, "0x0168")
        self.assertIn("Option 07 = 0", rows["07/02"].meaning)
        self.assertEqual(rows["27"].raw, "On")
        self.assertEqual(rows["09"].raw, "On")
        self.assertEqual(rows["10"].meaning, "$1")
        self.assertIn("AFT", rows["A0"].meaning)
        self.assertIn("Enhanced meters", rows["A0"].meaning)

        ext_prog = {**base_info, "game_options": "0x016B"}
        ext_rows = {row.code: row for row in so.decode_sas_summary(ext_prog)}
        self.assertIn("External progressive", ext_rows["07/02"].meaning)

        sas_prog = {**base_info, "game_options": "0x016F"}
        sas_rows = {row.code: row for row in so.decode_sas_summary(sas_prog)}
        self.assertIn("SAS progressive", sas_rows["07/02"].meaning)

        custom_info = {**base_info, "paytable": "CUSTOM", "base_percent": "    "}
        custom_rows = {row.code: row for row in so.decode_sas_summary(custom_info)}
        self.assertEqual(custom_rows["1F"].meaning, "CUSTOM")

    def test_encode_option_write_allowlist_and_validation(self):
        self.assertEqual(so.encode_option_write("02", "4"), bytes((0x05, 1, 4)))
        self.assertEqual(so.encode_option_write("07", "1"), bytes((0x06, 1, 1)))
        self.assertEqual(so.encode_option_write("78Lo", "1"), bytes((0x24, 4)) + b"0001")
        self.assertEqual(so.encode_option_write("06", "12"), bytes((0x3C, 2, 0x00, 0x12)))
        self.assertEqual(so.encode_option_write("56", "800"), bytes((0x18, 3, 0x00, 0x08, 0x00)))
        for bad_code, bad_val in (
            ("98", "0"), ("99", "1"), ("78Hi", "0002"), ("17", "0"), ("27", "1"), ("10", "9"),
            ("02", "9"), ("07", "5"), ("61", "0"), ("78Lo", "10000"), ("78Lo", "abcd"), ("56", "-1"),
        ):
            with self.subTest(code=bad_code, val=bad_val), self.assertRaises(ValueError):
                so.encode_option_write(bad_code, bad_val)


if __name__ == "__main__":
    unittest.main()
