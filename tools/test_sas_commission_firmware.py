"""Host archive + real frame/UART adapter + compiled core, on its private stack.

The UART is register memory and byte delivery, not electrical timing or interrupts.
No native tasks are scheduled; the native SafeRAM image stays unchanged.
"""

import struct
import zlib

import sas_commission as cm
from sas_testing import pattern

from firmware.tests.support import PERSONALITY, commission_constant, e0_request, u8
from firmware.tests.support import fw_constant as c
from firmware.tests.test_commission_io import (
    Machine,
    compiled,  # noqa: F401 -- pytest discovers the imported build fixture.
)


class CpuPort:
    def __init__(self, machine):
        self.machine = machine
        self.pending = b""
        self.ops = []
        self.closed = False

    def write(self, data):
        for payload in cm.FrameDecoder().feed(data):
            self.ops.append(payload[5])
        self.pending = self.machine.wire(data)

    def read(self, timeout):
        result, self.pending = self.pending, b""
        return result

    def reset_input(self):
        self.pending = b""

    def close(self):
        self.closed = True


def test_real_archive_initialize_and_replay_cannot_erase_acknowledged_ids(request, tmp_path):
    built = request.getfixturevalue("compiled")
    original = pattern(c("PERSIST_SIZE"), 3)
    machine = Machine(built, original)
    assert u8(machine.call("commission_boot")) == commission_constant("BOOT_SERVICE")
    machine.call("commission_io_init")
    port = CpuPort(machine)
    session = cm.Session(port)
    archive = cm.capture(session, "/dev/emulated-uart")
    assert archive.persistence == original
    assert archive.identity.main_rom == built.image[0x720:0x740]
    assert commission_constant("MAIN_SIZE") == len(built.image)
    assert commission_constant("PERSONALITY_SIZE") == len(PERSONALITY)
    assert archive.identity.main_crc32 == zlib.crc32(built.image)
    assert archive.identity.personality_crc32 == zlib.crc32(PERSONALITY)
    local = tmp_path / "original.json"
    cm.save_archive(archive, local)
    saved = cm.load_archive(local)
    result = cm.initialize(session, saved, first_install=True)
    assert result["init_sent"]
    assert session.info().mode == cm.Mode.DONE
    assert port.ops.count(cm.Op.INIT) == 1
    machine.assert_safe()

    # A lost reply can be reconciled before reboot without repeating INIT.
    assert not cm.initialize(session, saved, first_install=True)["init_sent"]
    assert port.ops.count(cm.Op.INIT) == 1

    # Normal boot owns the canonical journal encoder; commissioning never duplicates it.
    assert u8(machine.call("commission_boot")) == commission_constant("BOOT_CONTINUE")
    assert u8(machine.call("fw_boot")) == c("OK")
    request = e0_request(c("OP_BEGIN_PROFILE"), 1, struct.pack("<HI", c("PROFILE_SIZE"), 0))
    request_at, reply_at = 0x435E00, 0x435F00
    machine.cpu.write(request_at, request)
    length = machine.call("fw_process", request_at, len(request), reply_at, c("REPLY_HEADER") + c("MAX_PAYLOAD"))
    assert length == c("REPLY_HEADER")
    assert machine.cpu.read_u8(reply_at + 6) == c("OK")
    acknowledged = machine.cpu.read(c("PROFILE_BASE"), c("PERSIST_SIZE"))
    assert acknowledged != bytes(c("PERSIST_SIZE"))
    assert u8(machine.call("commission_boot")) == commission_constant("BOOT_CONTINUE")
    # Even a direct service invocation cannot turn this completed record into a formatter.
    assert not cm.initialize(session, saved, first_install=True)["init_sent"]
    assert port.ops.count(cm.Op.INIT) == 1
    assert machine.cpu.read(c("PROFILE_BASE"), len(acknowledged)) == acknowledged
    machine.assert_safe()
