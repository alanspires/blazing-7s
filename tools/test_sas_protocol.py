import random
import unittest

import sas_protocol as sas
from sas_testing import FakeLine

ADDRESS = 1


def crc_bitwise(data: bytes) -> int:
    """Independent CRC-16/KERMIT: reflected polynomial 0x1021, seed 0."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


class CrcTest(unittest.TestCase):
    def test_spec_routine_is_crc16_kermit(self):
        self.assertEqual(sas.crc16(b"123456789"), 0x2189)  # CRC-16/KERMIT check value
        rng = random.Random(0)
        for _ in range(500):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
            self.assertEqual(sas.crc16(data), crc_bitwise(data))

    def test_check_crc_rejects_a_bad_crc(self):
        good = sas.with_crc(bytes((ADDRESS, 0x1F, 0x41)))
        self.assertEqual(sas.check_crc(good), good[:-2])
        swapped = good[:-2] + good[-1:] + good[-2:-1]
        with self.assertRaises(sas.CrcError):
            sas.check_crc(swapped)


GENERAL_POLL = (ADDRESS | sas.GENERAL_POLL_FLAG, b"")
SYNC = (sas.SYNC_POLL, b"")
METERS = 0x1C
METERS_POLL = (ADDRESS, bytes((METERS,)))  # type R: no CRC
METERS_REPLY = sas.with_crc(bytes((ADDRESS, METERS)) + bytes(range(32)))


class HostTest(unittest.TestCase):
    def test_general_poll_sets_wakeup_on_the_flagged_address(self):
        line = FakeLine([bytes((0x11,))])
        code = sas.Host(line, ADDRESS).general_poll()
        self.assertEqual(line.sent[-1], GENERAL_POLL)
        self.assertEqual(code, 0x11)

    def test_sync_before_first_poll_and_before_repeating_an_answered_poll(self):
        line = FakeLine([bytes((0x11,)), bytes((0x12,))])
        host = sas.Host(line, ADDRESS)
        codes = [host.general_poll(), host.general_poll()]
        self.assertEqual(codes, [0x11, 0x12])
        self.assertEqual(line.sent, [SYNC, GENERAL_POLL, SYNC, GENERAL_POLL])

    def test_unanswered_poll_is_repeated_then_resynced(self):
        line = FakeLine()
        host = sas.Host(line, ADDRESS)
        for _ in range(2):
            with self.assertRaises(sas.NoResponse):
                host.general_poll()
        self.assertEqual(line.sent, ([SYNC] + [GENERAL_POLL] * sas.POLL_TRIES) * 2)

    def test_long_poll_returns_the_data_between_command_and_crc(self):
        line = FakeLine([METERS_REPLY])
        self.assertEqual(sas.Host(line, ADDRESS).long_poll(METERS, crc=False), METERS_REPLY[2:-2])
        self.assertEqual(line.sent[-1], METERS_POLL)

    def test_garbled_or_busy_reply_is_polled_again_without_ack(self):
        garbled = METERS_REPLY[:-1] + bytes((METERS_REPLY[-1] ^ 0xFF,))
        line = FakeLine([garbled, bytes((ADDRESS, sas.BUSY)), METERS_REPLY])
        self.assertEqual(sas.Host(line, ADDRESS).long_poll(METERS, crc=False), METERS_REPLY[2:-2])
        self.assertEqual(line.sent, [SYNC] + [METERS_POLL] * 3)

    def test_type_s_poll_carries_a_crc_and_an_ack_returns_none(self):
        line = FakeLine([bytes((ADDRESS,))])
        self.assertIsNone(sas.Host(line, ADDRESS).long_poll(0x01))
        first, rest = line.sent[-1]
        self.assertEqual(sas.check_crc(bytes((first,)) + rest), bytes((ADDRESS, 0x01)))

    def test_event_instead_of_a_long_poll_reply_is_kept_and_the_poll_resent(self):
        event = sas.with_crc(bytes((ADDRESS, sas.EVENT, 0x6F)))
        line = FakeLine([event, sas.with_crc(bytes((ADDRESS, 0x1A, 0, 0, 0, 7)))])
        host = sas.Host(line, ADDRESS)
        self.assertEqual(host.long_poll(0x1A, crc=False), bytes((0, 0, 0, 7)))
        self.assertEqual(host.events, [sas.RealTimeEvent(0x6F, b"")])
        poll = (ADDRESS, bytes((0x1A,)))
        self.assertEqual(line.sent[-3:], [poll, SYNC, poll])  # the sync acknowledges the event

    def test_nack_raises_and_counts_as_an_answer(self):
        line = FakeLine([bytes((ADDRESS | sas.NACK_FLAG,)), bytes((ADDRESS,))])
        host = sas.Host(line, ADDRESS)
        with self.assertRaises(sas.Nacked):
            host.long_poll(0x01)
        host.long_poll(0x01)  # the repeat is acknowledged with a sync, not sent as a NACK
        self.assertEqual(line.sent[0], SYNC)
        self.assertEqual(line.sent[2], SYNC)

    def test_single_send_does_not_repeat_uncertain_replies(self):
        replies = (b"", METERS_REPLY[:-1] + bytes((METERS_REPLY[-1] ^ 0xFF,)),
                   bytes((ADDRESS, sas.BUSY)), sas.with_crc(bytes((ADDRESS, sas.EVENT, 0x6F))))
        for reply in replies:
            with self.subTest(reply=reply):
                line = FakeLine([reply])
                with self.assertRaises(sas.ReplyError):
                    sas.Host(line, ADDRESS).long_poll(METERS, crc=False, retry=False)
                self.assertEqual(line.sent, [SYNC, METERS_POLL])

    def test_a_per_call_timeout_covers_every_try_of_that_call_only(self):
        for configured in (None, 3 * sas.Host.RESPONSE_TIMEOUT):
            with self.subTest(configured=configured):
                line = FakeLine([b"", METERS_REPLY, METERS_REPLY, bytes((0x11,))])
                host = (sas.Host(line, ADDRESS) if configured is None
                        else sas.Host(line, ADDRESS, timeout=configured))
                default = host.timeout
                override = 2 * default
                host.long_poll(METERS, crc=False, timeout=override)
                host.long_poll(METERS, crc=False)
                host.general_poll()
                self.assertEqual(line.timeouts, [override, override, default, default])
                self.assertEqual(host.timeout, configured or sas.Host.RESPONSE_TIMEOUT)

    def test_silence_raises_no_response(self):
        with self.assertRaises(sas.NoResponse):
            sas.Host(FakeLine(), ADDRESS).general_poll()

    def test_rom_signature_is_requested_then_answers_a_general_poll(self):
        seed, signature = 0x1234, 0xCCFB
        result = sas.with_crc(bytes((ADDRESS, sas.LONG_POLL_ROM_SIGNATURE)) + signature.to_bytes(2, "little"))
        line = FakeLine([bytes((ADDRESS,)), bytes((0x1F,)), result])
        host = sas.Host(line, ADDRESS)
        host.request_rom_signature(seed)
        request = line.sent[-1]
        self.assertEqual(sas.check_crc(bytes((request[0],)) + request[1]),
                         bytes((ADDRESS, sas.LONG_POLL_ROM_SIGNATURE)) + seed.to_bytes(2, "little"))
        self.assertEqual(host.general_poll(), 0x1F)
        self.assertEqual(host.general_poll(), sas.RomSignature(signature))

    def test_real_time_event_answers_a_general_poll(self):
        event = sas.with_crc(bytes((ADDRESS, sas.EVENT, 0x4F, 0x00, 0x05, 0x00, 0x00, 0x00, 0x12)))
        reply = sas.Host(FakeLine([event]), ADDRESS).general_poll()
        self.assertEqual(reply, sas.RealTimeEvent(0x4F, event[3:-2]))

    def test_reply_from_another_address_is_rejected(self):
        reply = sas.with_crc(bytes((ADDRESS + 1, METERS)) + bytes(32))
        with self.assertRaises(sas.ProtocolError):
            sas.Host(FakeLine([reply]), ADDRESS).long_poll(METERS, crc=False)


if __name__ == "__main__":
    unittest.main()
