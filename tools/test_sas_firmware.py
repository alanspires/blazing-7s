"""Real host codec ↔ compiled platform/core/game ↔ native configuration routines.

Only the UART/RTOS-facing leaves use the CPU fixture's fakes. This is not a
physical serial, scheduler, accounting or motor test.
"""

import json

import pytest
import sas_extension as se
import sas_profile as sp
from sas_protocol import with_crc

from firmware.tests.support import HOST_ADDRESS, NATIVE_TRANSMIT, receive, transmitted
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


class CpuSasHost:
    """The transport boundary ends at native UART-send entry, before device I/O. The handler
    runs to completion before its reply is read, so `retry` and `timeout` have no effect."""

    def __init__(self, machine):
        self.machine = machine

    def long_poll(self, command, data, *, retry=True, timeout=None):
        cpu = self.machine.cpu
        cpu.write_u8(HOST_ADDRESS, 1)
        receive(cpu, with_crc(bytes((1, command)) + data))
        cpu.enter()
        cpu.run(self.machine.symbols["platform_sas_handler"], NATIVE_TRANSMIT)
        _, reply = transmitted(cpu)
        assert reply[:2] == bytes((1, command))
        assert reply == with_crc(reply[:-2])
        return reply[2:-2]


@pytest.fixture
def connected(request):
    machine = Machine(request.getfixturevalue("compiled"))
    machine.call("platform_boot")
    machine.call("platform_boot_restore")
    return machine, se.Extension(CpuSasHost(machine))


def profile_file(tmp_path, data):
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(sp.to_json(sp.decode(data))))
    return str(path)


def test_host_upload_native_activation_reboot_and_stock_restore(connected, tmp_path):
    machine, ext = connected
    stock = config(machine)
    data = profile(lines=3)
    result = se.upload(ext, profile_file(tmp_path, data))
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
    armed = se.act(ext, se.Op.ARM_OUTCOME, stops)
    assert ext.status().last_status == se.Status.ACCEPTED
    assert machine.busy()
    machine.call("platform_boot")
    machine.call("platform_boot_restore")
    assert ext.status().last_status == se.Status.INTERRUPTED
    assert ext.mutate(se.Op.ARM_OUTCOME, armed["id"], stops) == se.Status.INTERRUPTED
    assert not machine.busy()
    assert sp.encode(se.download(ext)[0]) == data

    se.restore(ext)
    assert not ext.status().active
    assert config(machine) == stock  # the recorded original configuration, exactly
    assert machine.cpu.read_u8(MAX_WAGER) == sp.stock_profile().max_bet_per_line
    assert not machine.busy()


def test_host_busy_commit_never_changes_native_wager_or_configuration(connected, tmp_path):
    machine, ext = connected
    data = profile(lines=3)
    original = ext.status()
    machine.env(wager=1)
    machine.cpu.write_u8(MODE, 2)
    with pytest.raises(se.Refused) as refused:
        se.upload(ext, profile_file(tmp_path, data))
    assert refused.value.status == se.Status.BUSY
    assert refused.value.op == se.Op.COMMIT_PROFILE
    assert not ext.status().active
    assert ext.status().generation == original.generation
    assert machine.read_env("wager") == 1
    assert machine.cpu.read_u8(LINES) == sp.stock_profile().lines
    assert machine.config_sum() == 0

    machine.env(wager=0)
    machine.cpu.write_u8(MODE, 0)
    assert ext.mutate(se.Op.COMMIT_PROFILE, refused.value.id) == se.Status.BUSY
    assert ext.mutate(se.Op.COMMIT_PROFILE, ext.info().next_id) == se.Status.OK
    assert sp.encode(se.download(ext)[0]) == data
    assert not machine.busy()
