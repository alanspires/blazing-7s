import random
import struct
import unittest

import bsg_frame
from sas_testing import MAIN_ROM

# Nibble tables used by crc16_modbus (FUN_00016e34) in the main program.
LOW_NIBBLE_TABLE = 0x73AEC
HIGH_NIBBLE_TABLE = 0x73B2C


def firmware_crc(rom: bytes, data: bytes) -> int:
    lo = [struct.unpack_from(">H", rom, LOW_NIBBLE_TABLE + 4 * i)[0] for i in range(16)]
    hi = [struct.unpack_from(">H", rom, HIGH_NIBBLE_TABLE + 4 * i)[0] for i in range(16)]
    crc = 0xFFFF
    for b in data:
        x = (crc ^ b) & 0xFF
        crc = hi[x >> 4] ^ lo[x & 0xF] ^ (crc >> 8)
    return crc


class FrameTest(unittest.TestCase):
    def test_crc_matches_firmware_tables(self):
        rom = MAIN_ROM.read_bytes()
        rng = random.Random(0)
        for _ in range(500):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 14)))
            self.assertEqual(bsg_frame.crc16_modbus(data), firmware_crc(rom, data))

    def test_round_trip_escapes_reserved_bytes(self):
        payload = bytes((0x84, 0x05, 0x01, 0xFE, 0xFF))
        wire = bsg_frame.encode(payload)
        self.assertEqual(wire[-1], bsg_frame.END)
        self.assertNotIn(bsg_frame.END, wire[:-1])
        self.assertEqual(bsg_frame.decode(wire), payload)

    def test_corrupted_frame_is_rejected(self):
        wire = bytearray(bsg_frame.encode(bytes((0x11, 0x03))))
        wire[0] ^= 0x01
        with self.assertRaises(bsg_frame.CrcError):
            bsg_frame.decode(bytes(wire))


if __name__ == "__main__":
    unittest.main()
