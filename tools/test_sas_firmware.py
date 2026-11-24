"""Real host codec ↔ compiled platform/core/game ↔ native configuration routines.

Only the UART/RTOS-facing leaves use the CPU fixture's fakes. This is not a
physical serial, scheduler, accounting or motor test.
"""

import pytest
import sas_extension as se
import sas_options as so
import sas_profile as sp
from sas_protocol import with_crc

from firmware.tests.support import (
    HOST_ADDRESS,
    HOST_PORT,
    NATIVE_TRANSMIT,
    receive,
    transmitted,
)
from firmware.tests.support import fw_constant as c
from firmware.tests.test_game import (
    LINES,
    MAX_WAGER,
    MODE,
    RECORDS_PTR,
    Machine,
    compiled,  # noqa: F401 -- pytest registers this imported fixture by name.
    config,
    profile,
)

SAS_ADDRESS = 1


def no_record(intent):
    """These tests run the firmware, not a journal."""


class CpuSasHost:
    """The transport boundary ends at native UART-send entry, before device I/O. The handler
    runs to completion before its reply is read, so `retry` and `timeout` have no effect."""

    address = SAS_ADDRESS

    def __init__(self, machine):
        self.machine = machine

    def long_poll(self, command, data, *, retry=True, timeout=None):
        cpu = self.machine.cpu
        cpu.write_u8(HOST_ADDRESS, self.address)
        receive(cpu, with_crc(bytes((self.address, command)) + data))
        cpu.enter()
        cpu.run(self.machine.symbols["platform_sas_handler"], NATIVE_TRANSMIT)
        _, reply = transmitted(cpu)
        assert reply[:2] == bytes((self.address, command))
        assert reply == with_crc(reply[:-2])
        return reply[2:-2]


@pytest.fixture
def connected(request):
    machine = Machine(request.getfixturevalue("compiled"))
    machine.call("platform_boot")
    machine.call("platform_boot_restore")
    return machine, se.Extension(CpuSasHost(machine), pace=lambda: None)


def test_host_upload_native_activation_reboot_and_stock_restore(connected):
    machine, ext = connected
    stock = config(machine)
    data = profile(lines=3)
    result = se.upload(ext, sp.decode(data), record=no_record)
    decoded, status = se.download(ext)
    assert sp.encode(decoded) == data
    assert status.active and not status.fault
    assert status.last_id == result["id"]
    assert machine.cpu.read_u8(LINES) == decoded.lines
    assert machine.cpu.read_u8(MAX_WAGER) == decoded.lines * decoded.max_bet_per_line
    assert machine.cpu.read_u32(RECORDS_PTR) == machine.call("fw_active_profile") + c("PROFILE_RECORD_OFFSET")
    assert machine.config_sum() == 0
    assert not machine.busy()

    stops = bytes((4, 5, 6))
    armed = se.act(ext, se.Op.ARM_OUTCOME, stops, record=no_record)
    assert ext.status().last_status == se.Status.ACCEPTED
    assert machine.busy()
    machine.call("platform_boot")
    machine.call("platform_boot_restore")
    assert ext.status().last_status == se.Status.INTERRUPTED
    assert ext.mutate(se.Op.ARM_OUTCOME, armed["id"], stops, record=no_record) == se.Status.INTERRUPTED
    assert not machine.busy()
    assert sp.encode(se.download(ext)[0]) == data

    se.restore(ext, record=no_record)
    assert not ext.status().active
    assert config(machine) == stock  # the recorded original configuration, exactly
    assert machine.cpu.read_u8(MAX_WAGER) == sp.stock_profile().max_bet_per_line
    assert not machine.busy()


def test_host_busy_commit_never_changes_native_wager_or_configuration(connected):
    machine, ext = connected
    data = profile(lines=3)
    original = ext.status()
    machine.env(wager=1)
    machine.cpu.write_u8(MODE, 2)
    with pytest.raises(se.Refused) as refused:
        se.upload(ext, sp.decode(data), record=no_record)
    assert refused.value.status == se.Status.BUSY
    assert refused.value.op == se.Op.COMMIT_PROFILE
    assert not ext.status().active
    assert ext.status().generation == original.generation
    assert machine.read_env("wager") == 1
    assert machine.cpu.read_u8(LINES) == sp.stock_profile().lines
    assert machine.config_sum() == 0

    machine.env(wager=0)
    machine.cpu.write_u8(MODE, 0)
    assert ext.mutate(se.Op.COMMIT_PROFILE, refused.value.id, record=no_record) == se.Status.BUSY
    assert ext.mutate(se.Op.COMMIT_PROFILE, ext.info().next_id, record=no_record) == se.Status.OK
    assert sp.encode(se.download(ext)[0]) == data
    assert not machine.busy()


def test_host_reads_setup_options_from_saferam(connected):
    machine, ext = connected
    info = ext.info()
    assert info.capabilities & se.Capability.OPTIONS
    block = bytearray(so.default_options_block(progressive_type=4, external_jackpots=1, sas_address=1, progressive_id=1))
    # receive() and CpuSasHost write HOST_PORT (0x43E8D6) and HOST_ADDRESS (0x43E8DC).
    block[HOST_PORT - c("OPTIONS_BASE")] = 1
    block[HOST_ADDRESS - c("OPTIONS_BASE")] = SAS_ADDRESS
    machine.cpu.write(c("OPTIONS_BASE"), bytes(block))
    raw = se.read_options(ext, info)
    assert raw == bytes(block)
    rows = {row.code: row for row in so.decode_setup_options(raw)}
    assert rows["02"].raw == "0004"
    assert rows["07"].raw == "0001"
    assert rows["78Lo"].raw == "0001"


def test_host_sets_allowlisted_options_and_rejects_host_link_options(connected):
    machine, ext = connected
    info = ext.info()
    assert info.capabilities & se.Capability.SET_OPTION
    block = bytearray(so.default_options_block(progressive_type=0, external_jackpots=0, progressive_id=0))
    block[HOST_PORT - c("OPTIONS_BASE")] = 1
    block[HOST_ADDRESS - c("OPTIONS_BASE")] = SAS_ADDRESS
    machine.cpu.write(c("OPTIONS_BASE"), bytes(block))

    for code, val in (("02", "4"), ("07", "1"), ("78Lo", "1"), ("56", "1200")):
        res = se.set_option(ext, so.encode_option_write(code, val), record=no_record)
        assert res["status"] == se.Status.OK.name
    rows = {row.code: row for row in so.decode_setup_options(res["options_raw"])}
    assert rows["02"].raw == "0004"
    assert rows["07"].raw == "0001"
    assert rows["78Lo"].raw == "0001"
    assert rows["56"].raw == "001200"

    # Disallowed Primary Host option 98 (offset 0xA3) must be refused by compiled firmware
    with pytest.raises(se.Refused) as refused:
        se.set_option(ext, bytes((0xA3, 1, 0)), record=no_record)
    assert refused.value.status == se.Status.BAD_REQUEST

