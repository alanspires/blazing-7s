"""CPU tests of the production core with deterministic platform ports, not hardware."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import pytest

from firmware.tests.cpu import Cpu
from firmware.tests.support import (
    PERSONALITY,
    STORAGE_WORD,
    build_variant,
    commission_constant,
    production_link,
    u8,
)
from firmware.tests.support import fw_constant as c

PORTS = r"""
#include "fw.h"
struct test_ports {
    uint32_t words, budget, submitted, applied, profile, idle, caps;
    uint32_t submit_status, current_status, apply_status, bad_write, drop;
    uint32_t prepared, prepare_status, finished, owned, context, restore, prepare_active;
    uint32_t finish_status, finish_recorded_status, apply_recorded_status;
    uint32_t watch_profile_writes, bad_order, prepare_staged, prepare_dirty;
    uint64_t last_id;
};
extern struct test_ports test_ports;
void test_reset(void) {
    uint8_t *p = (uint8_t *)&test_ports;
    for (uint32_t i = 0; i < sizeof(test_ports); ++i) p[i] = 0;
    test_ports.budget = UINT32_MAX;
    test_ports.drop = UINT32_MAX;
    test_ports.idle = 1;
    test_ports.caps = FW_CAP_PROFILE | FW_CAP_OUTCOME | FW_CAP_SPIN |
                      FW_CAP_POSITION | FW_CAP_JOG;
    test_ports.submit_status = FW_ACCEPTED;
    test_ports.current_status = FW_ACCEPTED;
    test_ports.context = 0x12345678u; /* CONTEXT */
}
void test_fail_after(uint32_t words) { test_ports.words = 0; test_ports.budget = words; }
void test_drop_at(uint32_t word) { test_ports.words = 0; test_ports.drop = word; }
void test_idle(uint32_t idle) { test_ports.idle = idle; }
void test_status(uint32_t status) { test_ports.current_status = status; }
void test_submit_status(uint32_t status) { test_ports.submit_status = status; }
void test_apply_status(uint32_t status) { test_ports.apply_status = status; }
void test_prepare_status(uint32_t status) { test_ports.prepare_status = status; }
void test_watch_profile_writes(uint32_t watch) { test_ports.watch_profile_writes = watch; }
void test_capabilities(uint32_t caps) { test_ports.caps = caps; }
uint32_t test_submitted(void) { return test_ports.submitted; }
uint32_t test_applied(void) { return test_ports.applied; }
uint32_t test_profile(void) { return test_ports.profile; }
uint32_t test_words(void) { return test_ports.words; }
uint32_t test_bad_write(void) { return test_ports.bad_write; }
uint32_t test_bad_order(void) { return test_ports.bad_order; }
uint32_t test_prepared(void) { return test_ports.prepared; }
uint32_t test_finished(void) { return test_ports.finished; }
uint32_t test_owned(void) { return test_ports.owned; }
uint32_t test_context(void) { return test_ports.context; }
uint32_t test_restore(void) { return test_ports.restore; }
uint32_t test_prepare_active(void) { return test_ports.prepare_active; }
uint32_t test_prepare_staged(void) { return test_ports.prepare_staged; }
uint32_t test_prepare_dirty(void) { return test_ports.prepare_dirty; }
uint32_t test_finish_status(void) { return test_ports.finish_status; }
uint32_t test_finish_recorded_status(void) { return test_ports.finish_recorded_status; }
uint32_t test_apply_recorded_status(void) { return test_ports.apply_recorded_status; }
uint32_t test_id_high(void) { return (uint32_t)(test_ports.last_id >> 32); }
uint32_t test_id_low(void) { return (uint32_t)test_ports.last_id; }
int fw_storage_write(uint32_t address, const uint8_t *data, uint32_t length) {
    if (test_ports.watch_profile_writes && address < FW_OPERATION_JOURNAL && !test_ports.owned)
        test_ports.bad_order = 1;
    if ((address & 1u) || (length & 1u) || address < FW_PROFILE_BASE ||
        address + length > FW_PROFILE_BASE + FW_PERSIST_SIZE) {
        test_ports.bad_write = 1;
        return 0;
    }
    for (uint32_t i = 0; i < length; i += 2u) {
        if (test_ports.words == test_ports.budget) return 0;
        if (test_ports.words != test_ports.drop)
            *(volatile uint16_t *)(uintptr_t)(address + i) =
                (uint16_t)(((uint16_t)data[i] << 8) | data[i + 1u]);
        ++test_ports.words;
    }
    return 1;
}
uint32_t fw_capabilities(void) { return test_ports.caps; }
static uint32_t le32(const uint8_t *p) {
    return p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static uint8_t durable_status(void) {
    uint32_t latest = 0;
    uint8_t status = FW_FAULT;
    for (uint32_t i = 0; i < 2u; ++i) {
        const uint8_t *p = (const uint8_t *)(uintptr_t)(FW_OPERATION_JOURNAL + i * FW_JOURNAL_SIZE);
        if ((p[FW_JOURNAL_MARKER_OFFSET] | ((uint16_t)p[FW_JOURNAL_MARKER_OFFSET + 1u] << 8)) ==
            FW_COMMIT_MARKER && le32(p + FW_JOURNAL_CRC_OFFSET) == fw_crc32(p, FW_JOURNAL_CRC_OFFSET) &&
            le32(p + 4) > latest) {
            latest = le32(p + 4);
            status = p[17];
        }
    }
    return status;
}
/* Context and baseline stand-ins: the context value, then a pattern from it. */
uint8_t fw_prepare_profile_change(uint8_t *staged, const uint8_t *active) {
    ++test_ports.prepared;
    test_ports.prepare_active = (uint32_t)(uintptr_t)active;
    test_ports.prepare_staged = (uint32_t)(uintptr_t)staged;
    test_ports.prepare_dirty = 0;
    if (staged)
        for (uint32_t i = FW_PROFILE_SIZE; i < FW_SLOT_SIZE; ++i)
            if (staged[i] != 0u) test_ports.prepare_dirty = 1;
    if (!test_ports.idle) return FW_BUSY;
    if (test_ports.prepare_status != FW_OK) return (uint8_t)test_ports.prepare_status;
    test_ports.owned = 1;
    if (staged)
        for (uint32_t i = 0; i < 4u + FW_SLOT_BASELINE_SIZE; ++i)
            staged[FW_SLOT_CONTEXT_OFFSET + i] = (uint8_t)(test_ports.context >> (8u * (i % 4u))) ^ (uint8_t)(i / 4u);
    return FW_OK;
}
void fw_finish_profile_change(uint8_t status) {
    if (!test_ports.owned) test_ports.bad_order = 1;
    ++test_ports.finished;
    test_ports.finish_status = status;
    test_ports.finish_recorded_status = durable_status();
    test_ports.owned = 0;
}
uint8_t fw_apply_profile(const uint8_t *profile, bool restore) {
    ++test_ports.applied;
    test_ports.profile = (uint32_t)(uintptr_t)profile;
    test_ports.restore = restore;
    test_ports.apply_recorded_status = durable_status();
    return (uint8_t)test_ports.apply_status;
}
uint8_t fw_submit(uint8_t op, const uint8_t *args, uint32_t length, uint64_t id) {
    (void)op; (void)args; (void)length;
    ++test_ports.submitted;
    test_ports.last_id = id;
    return (uint8_t)test_ports.submit_status;
}
uint8_t fw_current_status(uint64_t id) {
    if (id != test_ports.last_id) return FW_BAD_ID;
    return (uint8_t)test_ports.current_status;
}
"""

@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return build_variant(
        tmp_path_factory,
        "core",
        sources=("fw.h", "core.c"),
        files={"ports.c": PORTS},
        link=production_link("fw_boot", test_ports=0x434000),
        patches=[],
    )


@pytest.fixture(scope="module")
def profile():
    result = bytearray(b"S6PF")
    result.extend(
        (c("VERSION"), PERSONALITY[0xD0F9], PERSONALITY[0xD0F8], PERSONALITY[0xD716])
    )
    for stop in range(c("STOPS")):
        start = 0xD840 + stop * 5
        result.extend(PERSONALITY[start : start + c("REELS")])
    result.extend(b"\0\0")
    start = 0xD718
    result.extend(
        PERSONALITY[
            start : start + c("PROFILE_RECORD_COUNT") * c("PROFILE_RECORD_SIZE")
        ]
    )
    assert len(result) == c("PROFILE_SIZE")
    return bytes(result)


# The prepare fake's context and baseline stand-in (see PORTS).
CONTEXT = 0x12345678
METADATA = bytes(((CONTEXT >> 8 * (i % 4)) & 0xFF) ^ (i // 4)
                 for i in range(4 + c("SLOT_BASELINE_SIZE")))


@dataclass(frozen=True)
class Reply:
    status: int
    payload: bytes


class Machine:
    REQUEST = 0x435000
    REPLY = 0x435100

    def __init__(self, compiled, persistent=None, *, boot=True):
        self.symbols = compiled.symbols
        self.cpu = Cpu(compiled.image)
        self.call("test_reset")
        self.cpu.write(self.symbols["fw_runtime"], b"\xa7" * c("RUNTIME_SIZE"))
        if persistent is not None:
            self.cpu.write(c("PROFILE_BASE"), persistent)
        self.boot_status = u8(self.call("fw_boot")) if boot else None
        if boot:
            self.call("test_watch_profile_writes", 1)

    def call(self, symbol, *args):
        return self.cpu.call(self.symbols[symbol], *args)

    def persistent(self):
        return self.cpu.read(c("PROFILE_BASE"), c("PERSIST_SIZE"))

    def request(self, op, payload=b"", *, ident=0, version=None, capacity=None):
        data = b"S6RE" + bytes((c("VERSION") if version is None else version, op))
        data += struct.pack("<Q", ident) + payload
        self.cpu.write(self.REQUEST, data)
        length = self.call(
            "fw_process",
            self.REQUEST,
            len(data),
            self.REPLY,
            c("REPLY_HEADER") + c("MAX_PAYLOAD") if capacity is None else capacity,
        )
        if not length:
            return None
        raw = self.cpu.read(self.REPLY, length)
        assert raw[:6] == b"S6RE" + bytes((c("VERSION"), op))
        assert int.from_bytes(raw[7 : c("REPLY_HEADER")], "little") == ident
        assert length <= c("REPLY_HEADER") + c("MAX_PAYLOAD")
        return Reply(raw[6], raw[c("REPLY_HEADER") :])

    def info(self):
        reply = self.request(c("OP_INFO"))
        assert reply.status == c("OK")
        assert len(reply.payload) == c("INFO_SIZE")
        fields = struct.unpack("<IQHBBBBBI", reply.payload)
        names = (
            "caps",
            "next_id",
            "profile_size",
            "max_chunk",
            "max_lines",
            "max_bet",
            "max_total_bet",
            "stops",
            "max_award",
        )
        return dict(zip(names, fields))

    def status(self):
        reply = self.request(c("OP_STATUS"))
        assert reply.status == c("OK")
        assert len(reply.payload) == c("STATUS_SIZE")
        fields = struct.unpack("<QBBBIIB", reply.payload)
        return dict(
            zip(("id", "op", "status", "active", "generation", "crc", "fault"), fields)
        )

    def mutate(self, op, payload=b""):
        return self.request(op, payload, ident=self.info()["next_id"])

    def begin(self, profile, crc=None):
        return self.mutate(
            c("OP_BEGIN_PROFILE"),
            struct.pack(
                "<HI", len(profile), zlib.crc32(profile) if crc is None else crc
            ),
        )

    def stage(self, profile, *, reverse=False):
        assert self.begin(profile).status == c("OK")
        offsets = list(range(0, len(profile), c("MAX_CHUNK")))
        for offset in reversed(offsets) if reverse else offsets:
            data = struct.pack("<H", offset) + profile[offset : offset + c("MAX_CHUNK")]
            assert self.mutate(c("OP_PROFILE_CHUNK"), data).status == c("OK")

    def activate(self, profile):
        self.stage(profile)
        assert self.mutate(c("OP_COMMIT_PROFILE")).status == c("OK")
        return self.status()

    def active(self):
        pointer = self.call("fw_active_profile")
        return self.cpu.read(pointer, c("PROFILE_SIZE")) if pointer else None

    def write_checkpoints(self, operation):
        """Snapshot immediately BEFORE each atomic store, plus completed state."""
        with self.cpu.cut_points(
            self.persistent, (c("PROFILE_BASE"), c("PERSIST_SIZE")), store_size=STORAGE_WORD
        ) as snapshots:
            result = operation()
        assert self.call("test_bad_write") == 0
        return result, snapshots


def test_discovery_boot_restore_and_bounds(compiled):
    machine = Machine(compiled)
    assert machine.boot_status == c("OK")
    assert machine.call("test_applied") == 0
    assert u8(machine.call("fw_restore")) == c("OK")
    assert machine.call("test_profile") == 0
    info = machine.info()
    for key in (
        "profile_size",
        "max_chunk",
        "max_lines",
        "max_bet",
        "max_total_bet",
        "stops",
        "max_award",
    ):
        assert info[key] == c(key.upper())
    assert info["next_id"] == 1
    assert info["caps"] == sum(
        c("CAP_" + name) for name in ("PROFILE", "OUTCOME", "SPIN", "POSITION", "JOG")
    )
    assert machine.request(c("OP_INFO"), ident=1).status == c("BAD_ID")
    assert machine.request(c("OP_INFO"), version=c("VERSION") + 1).status == c(
        "BAD_VERSION"
    )
    assert machine.request(c("OP_INFO"), b"\0").status == c("BAD_REQUEST")
    assert machine.request(c("OP_INFO"), capacity=c("REPLY_HEADER")) is None
    assert machine.request(
        c("OP_BEGIN_PROFILE"), b"\0" * (c("MAX_PAYLOAD") + 1), ident=1
    ).status == c("BAD_REQUEST")
    assert (
        machine.request(c("OP_BEGIN_PROFILE"), ident=1, capacity=c("REPLY_HEADER") - 1)
        is None
    )
    assert machine.info()["next_id"] == info["next_id"]


@pytest.mark.parametrize("fill", [0, 0xFF])
@pytest.mark.parametrize("first", ["boot", "mutation", "clear"])
def test_virgin_baselines_and_first_operation_restart_at_every_word(
    compiled, profile, fill, first
):
    machine = Machine(compiled, bytes([fill]) * c("PERSIST_SIZE"), boot=False)

    def operation():
        if first == "clear":
            return u8(machine.call("fw_invalidate"))
        assert u8(machine.call("fw_boot")) == c("OK")
        if first == "mutation":
            return machine.begin(profile).status
        return c("OK")

    result, checkpoints = machine.write_checkpoints(operation)
    assert result == c("OK")
    previous_id = 0
    clearing = False
    for checkpoint in checkpoints:
        restarted = Machine(compiled, checkpoint)
        status = restarted.status()
        assert status["id"] >= previous_id
        previous_id = status["id"]
        assert restarted.active() is None
        assert restarted.call("test_submitted") == 0
        assert restarted.call("test_applied") == 0
        if restarted.boot_status == c("FAULT"):
            assert first == "clear"
            clearing = True
            restarted.call("test_watch_profile_writes", 0)
            assert u8(restarted.call("fw_clear_complete")) == c("OK")
            assert u8(restarted.call("fw_boot")) == c("OK")
        else:
            assert restarted.boot_status == c("OK")
            assert not clearing
        if first != "mutation":
            assert restarted.info()["next_id"] == 1
    assert previous_id == (1 if first == "mutation" else 0)
    assert clearing == (first == "clear")


@pytest.mark.parametrize("offset", [0, c("OPERATION_JOURNAL") - c("PROFILE_BASE") + 6])
def test_nonblank_unformatted_storage_requires_offline_provisioning(compiled, offset):
    persistent = bytearray(c("PERSIST_SIZE"))
    persistent[offset] = 0xA7
    machine = Machine(compiled, persistent)
    assert machine.boot_status == c("FAULT")
    assert machine.persistent() == bytes(persistent)
    assert u8(machine.call("fw_invalidate")) == c("FAULT")
    assert machine.persistent() == bytes(persistent)
    assert machine.info()["next_id"] == 0


def test_crc_collision_is_not_an_identical_latest_request(compiled, profile):
    machine = Machine(compiled)
    assert machine.begin(profile).status == c("OK")
    ident = machine.info()["next_id"]
    first = bytes.fromhex("08009fc3c332f13410ac")
    collision = bytes.fromhex("0800b4dfd34b139d04ad")
    envelope = (
        b"S6RE"
        + bytes((c("VERSION"), c("OP_PROFILE_CHUNK")))
        + struct.pack("<Q", ident)
    )
    assert first != collision
    assert zlib.crc32(envelope + first) == zlib.crc32(envelope + collision)
    assert machine.request(c("OP_PROFILE_CHUNK"), first, ident=ident).status == c("OK")
    writes = machine.call("test_words")
    assert machine.request(c("OP_PROFILE_CHUNK"), collision, ident=ident).status == c(
        "BAD_ID"
    )
    assert machine.call("test_words") == writes
    restarted = Machine(compiled, machine.persistent())
    assert restarted.boot_status == c("OK")
    assert restarted.request(c("OP_PROFILE_CHUNK"), first, ident=ident).status == c(
        "OK"
    )
    assert restarted.request(c("OP_PROFILE_CHUNK"), collision, ident=ident).status == c(
        "BAD_ID"
    )
    assert restarted.request(
        c("OP_PROFILE_CHUNK"), first[:-1], ident=ident
    ).status == c("BAD_ID")


def test_upload_out_of_order_overlap_duplicate_readback_and_restore(compiled, profile):
    machine = Machine(compiled)
    assert machine.begin(profile).status == c("OK")
    chunk = struct.pack("<H", 0) + profile[: c("MAX_CHUNK")]
    ident = machine.info()["next_id"]
    assert machine.request(c("OP_PROFILE_CHUNK"), chunk, ident=ident).status == c("OK")
    writes = machine.call("test_words")
    assert machine.request(c("OP_PROFILE_CHUNK"), chunk, ident=ident).status == c("OK")
    assert machine.call("test_words") == writes
    changed = chunk[:-1] + bytes((chunk[-1] ^ 1,))
    assert machine.request(c("OP_PROFILE_CHUNK"), changed, ident=ident).status == c(
        "BAD_ID"
    )
    assert machine.request(c("OP_PROFILE_CHUNK"), chunk, ident=ident - 1).status == c(
        "BAD_ID"
    )
    assert machine.mutate(c("OP_PROFILE_CHUNK"), changed).status == c("BAD_REQUEST")
    assert machine.mutate(c("OP_PROFILE_CHUNK"), chunk).status == c("OK")
    for offset in reversed(range(c("MAX_CHUNK"), len(profile), c("MAX_CHUNK"))):
        assert machine.mutate(
            c("OP_PROFILE_CHUNK"),
            struct.pack("<H", offset) + profile[offset : offset + c("MAX_CHUNK")],
        ).status == c("OK")
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("OK")
    status = machine.status()
    assert status["active"] == 1 and status["crc"] == zlib.crc32(profile)
    pointer = machine.call("fw_active_profile")
    assert pointer == c("PROFILE_BASE")
    assert machine.cpu.read(pointer + len(profile), c("PROFILE_GUARD_SIZE")) == bytes(
        c("PROFILE_GUARD_SIZE")
    )
    readback = bytearray()
    for offset in range(0, len(profile), c("MAX_PAYLOAD")):
        count = min(c("MAX_PAYLOAD"), len(profile) - offset)
        reply = machine.request(c("OP_READ_PROFILE"), struct.pack("<HB", offset, count))
        assert reply.status == c("OK")
        readback.extend(reply.payload)
    assert bytes(readback) == profile
    restarted = Machine(compiled, machine.persistent())
    assert restarted.active() == profile
    assert restarted.call("test_applied") == 0
    assert u8(restarted.call("fw_restore")) == c("OK")
    assert restarted.call("test_profile") == pointer
    assert restarted.mutate(c("OP_RESTORE_STOCK")).status == c("OK")
    assert restarted.active() is None
    assert (restarted.call("test_profile"), restarted.call("test_restore")) == (pointer, 1)
    stock = Machine(compiled, restarted.persistent())
    assert stock.boot_status == c("OK") and stock.active() is None
    assert stock.status()["generation"] > status["generation"]


def test_commit_requires_complete_digest_valid_profile_and_idle(compiled, profile):
    machine = Machine(compiled)
    assert machine.begin(profile).status == c("OK")
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("INCOMPLETE")
    machine.stage(profile)
    machine.call("test_idle", 0)
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("BUSY")
    machine.call("test_idle", 1)
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("OK")
    prior = machine.active()
    invalid = bytearray(profile)
    invalid[c("PROFILE_RECORD_OFFSET") + 12] = 0
    machine.stage(invalid)
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("INVALID_PROFILE")
    assert machine.active() == prior
    assert machine.begin(profile, zlib.crc32(profile) ^ 1).status == c("OK")
    for offset in range(0, len(profile), c("MAX_CHUNK")):
        assert machine.mutate(
            c("OP_PROFILE_CHUNK"),
            struct.pack("<H", offset) + profile[offset : offset + c("MAX_CHUNK")],
        ).status == c("OK")
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("INVALID_PROFILE")
    restarted = Machine(compiled, machine.persistent())
    assert restarted.active() == prior
    assert restarted.mutate(c("OP_COMMIT_PROFILE")).status == c("INCOMPLETE")


def test_profile_preparation_context_and_durable_finish_order(compiled, profile):
    machine = Machine(compiled)
    invalid = bytearray(profile)
    invalid[c("PROFILE_RECORD_OFFSET") + 12] = 0
    machine.stage(invalid)
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("INVALID_PROFILE")
    assert machine.call("test_prepared") == 0
    machine.stage(profile)
    before = machine.cpu.read(
        c("PROFILE_BASE"), c("OPERATION_JOURNAL") - c("PROFILE_BASE")
    )
    machine.call("test_prepare_status", c("BUSY"))
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("BUSY")
    assert machine.cpu.read(c("PROFILE_BASE"), len(before)) == before
    assert machine.call("test_applied") == 0
    assert machine.call("test_finished") == 0
    machine.call("test_prepare_status", c("OK"))
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("OK")
    assert machine.call("test_prepare_active") == 0  # stock: first capture
    slot = machine.call("fw_active_profile")
    assert machine.cpu.read(slot + c("SLOT_CONTEXT_OFFSET"), len(METADATA)) == METADATA
    assert machine.cpu.read(slot + c("SLOT_FORMAT_OFFSET"), 4) == struct.pack(
        "<I", c("SLOT_FORMAT")
    )
    assert machine.call("test_apply_recorded_status") == c("ACCEPTED")
    assert machine.call("test_finish_recorded_status") == c("OK")
    assert machine.call("test_finish_status") == c("OK")
    assert machine.call("test_finished") == 1
    assert machine.call("test_owned") == 0
    assert machine.call("test_bad_order") == 0
    assert machine.call("test_restore") == 0
    assert machine.mutate(c("OP_RESTORE_STOCK")).status == c("OK")
    assert machine.call("test_prepare_active") == slot
    assert (machine.call("test_profile"), machine.call("test_restore")) == (slot, 1)
    assert machine.call("test_finish_recorded_status") == c("OK")
    assert machine.call("test_finished") == 2
    assert machine.call("test_bad_order") == 0


def test_boot_defers_stock_completion_until_successful_authorized_restore(
    compiled, profile
):
    machine = Machine(compiled)
    machine.activate(profile)
    journal_words = c("JOURNAL_SIZE") // 2 + 1
    ident = machine.info()["next_id"]
    machine.call("test_fail_after", journal_words * 2)
    assert machine.request(c("OP_RESTORE_STOCK"), ident=ident).status == c("FAULT")
    assert machine.call("test_finish_status") == c("FAULT")
    assert machine.call("test_finish_recorded_status") == c("ACCEPTED")
    pending = Machine(compiled, machine.persistent())
    assert pending.boot_status == c("OK")
    assert pending.status()["status"] == c("ACCEPTED")
    assert pending.call("test_applied") == 0
    assert pending.call("test_prepared") == 0
    pending.call("test_apply_status", c("FAULT"))
    assert u8(pending.call("fw_restore")) == c("FAULT")
    assert pending.call("test_restore") == 1
    assert pending.call("test_profile") == c("PROFILE_BASE")
    assert pending.status()["status"] == c("ACCEPTED")
    recovered = Machine(compiled, pending.persistent())
    assert u8(recovered.call("fw_restore")) == c("OK")
    assert recovered.call("test_restore") == 1
    assert recovered.call("test_profile") == c("PROFILE_BASE")
    assert recovered.call("test_apply_recorded_status") == c("ACCEPTED")
    assert recovered.status()["status"] == c("OK")
    assert recovered.call("test_prepared") == 0
    assert recovered.call("test_finished") == 0
    completed = Machine(compiled, recovered.persistent())
    assert u8(completed.call("fw_restore")) == c("OK")
    assert (completed.call("test_profile"), completed.call("test_restore")) == (0, 0)
    assert completed.call("test_apply_recorded_status") == c("OK")


def test_profile_validator_native_fields_and_limits(compiled, profile):
    machine = Machine(compiled)

    def validate(data):
        machine.cpu.write(machine.REQUEST, data)
        return u8(machine.call("fw_validate_profile", machine.REQUEST))

    assert validate(profile) == c("OK")
    record = c("PROFILE_RECORD_OFFSET")
    invalid_fields = {
        4: c("VERSION") + 1,
        5: c("MAX_LINES") + 1,
        6: c("MAX_BET") + 1,
        7: c("PROFILE_RECORD_COUNT") - 1,
        74: 1,
        75: 1,
        record + 4: 0x24,
        record + 7: 0x0A,
        record + 8: 0x0B,
        record + 9: 0,
        record + 10: 0,
        record + 11: 0x30,
        record + 12: 0,
        record + 13: c("REELS") - 1,
        record + 14: 1,
        record + 15: 1,
    }
    for offset, value in invalid_fields.items():
        invalid = bytearray(profile)
        invalid[offset] = value
        assert validate(invalid) == c("INVALID_PROFILE"), (offset, value)
    valid = bytearray(profile)
    valid[record : record + 4] = bytes(4)
    assert validate(valid) == c("OK")
    for award in (c("MAX_AWARD") + 1, 0xFFFFFFFF):
        invalid = bytearray(profile)
        invalid[record : record + 4] = award.to_bytes(4, "big")
        assert validate(invalid) == c("INVALID_PROFILE")
    invalid = bytearray(profile)
    invalid[8 : 8 + c("STOPS") * c("REELS") : c("REELS")] = bytes(c("STOPS"))
    assert validate(invalid) == c("INVALID_PROFILE")
    invalid = bytearray(profile)
    for i in range(c("PROFILE_RECORD_COUNT")):
        invalid[record + i * c("PROFILE_RECORD_SIZE") + 10] = c("MAX_BET")
    assert validate(invalid) == c("INVALID_PROFILE")
    for flags in (0, 0x10, 0x20, 0x40):
        valid = bytearray(profile)
        valid[5] = c("MAX_LINES")
        valid[record + 11] = flags
        assert validate(valid) == c("OK")
        valid[record + 12] = (1 << c("MAX_LINES")) - 1
        assert validate(valid) == (c("INVALID_PROFILE") if flags else c("OK"))
    for symbol in (0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x18, 0x80, 0x81):
        valid = bytearray(profile)
        valid[record + 4] = symbol
        assert validate(valid) == c("OK")


def test_accepted_id_is_durable_duplicates_do_not_execute_and_boot_interrupts(compiled):
    machine = Machine(compiled)
    ident = (1 << 40) + 7
    stops = bytes(range(c("REELS")))
    reply = machine.request(c("OP_POSITION"), stops, ident=ident)
    assert reply.status == c("ACCEPTED")
    assert machine.call("test_submitted") == 1
    assert machine.call("test_id_high") == ident >> 32
    assert machine.call("test_id_low") == ident & 0xFFFFFFFF
    assert machine.request(c("OP_POSITION"), stops, ident=ident).status == c("ACCEPTED")
    assert machine.call("test_submitted") == 1
    assert machine.request(c("OP_JOG"), b"\0", ident=ident + 1).status == c("BUSY")
    restarted = Machine(compiled, machine.persistent())
    assert restarted.boot_status == c("OK")
    assert restarted.status()["status"] == c("INTERRUPTED")
    assert restarted.request(c("OP_POSITION"), stops, ident=ident).status == c(
        "INTERRUPTED"
    )
    assert restarted.call("test_submitted") == 0
    assert restarted.info()["next_id"] == ident + 1
    assert restarted.mutate(c("OP_JOG"), b"\0").status == c("ACCEPTED")
    restarted.call("test_status", c("OK"))
    assert restarted.status()["status"] == c("OK")
    terminal = Machine(compiled, restarted.persistent())
    assert terminal.status()["status"] == c("OK")
    assert terminal.call("test_submitted") == 0


def test_native_ports_own_action_admission_and_cancel_pending_arm(compiled):
    machine = Machine(compiled)
    machine.call("test_idle", 0)
    assert machine.mutate(c("OP_POSITION"), bytes(c("REELS"))).status == c("ACCEPTED")
    machine.call("test_status", c("OK"))
    assert machine.status()["status"] == c("OK")
    assert machine.mutate(c("OP_ARM_OUTCOME"), bytes(c("REELS"))).status == c(
        "ACCEPTED"
    )
    machine.call("test_status", c("ACCEPTED"))
    submitted = machine.call("test_submitted")
    assert machine.mutate(c("OP_CANCEL_OUTCOME")).status == c("ACCEPTED")
    assert machine.call("test_submitted") == submitted + 1


@pytest.mark.parametrize("native_status", [c("ACCEPTED"), c("BUSY")])
def test_pending_arm_handoff_to_new_spin_id_is_single_execution(
    compiled, native_status
):
    machine = Machine(compiled)
    arm_id = machine.info()["next_id"]
    stops = bytes(range(c("REELS")))
    assert machine.request(c("OP_ARM_OUTCOME"), stops, ident=arm_id).status == c(
        "ACCEPTED"
    )
    spin_id = machine.info()["next_id"]
    submitted = machine.call("test_submitted")
    assert machine.request(c("OP_POSITION"), stops, ident=spin_id).status == c("BUSY")
    assert machine.call("test_submitted") == submitted
    machine.call("test_submit_status", native_status)
    bet = bytes((c("MAX_BET"),))
    assert machine.request(c("OP_BET_SPIN"), bet, ident=spin_id).status == native_status
    assert machine.call("test_submitted") == submitted + 1
    assert machine.call("test_id_low") == spin_id
    assert machine.info()["next_id"] == spin_id + 1
    assert machine.request(c("OP_BET_SPIN"), bet, ident=spin_id).status == native_status
    assert machine.request(c("OP_ARM_OUTCOME"), stops, ident=arm_id).status == c(
        "BAD_ID"
    )
    assert machine.call("test_submitted") == submitted + 1
    if native_status == c("BUSY"):
        machine.call("test_submit_status", c("ACCEPTED"))
        assert machine.mutate(c("OP_CANCEL_OUTCOME")).status == c("ACCEPTED")
        assert machine.call("test_submitted") == submitted + 2
    else:
        assert machine.mutate(c("OP_CANCEL_OUTCOME")).status == c("BUSY")
        assert machine.call("test_submitted") == submitted + 1


def test_action_validation_capabilities_and_exhausted_id(compiled):
    machine = Machine(compiled)
    for op, payload in (
        ("POSITION", bytes([c("STOPS")]) * c("REELS")),
        ("BET_SPIN", bytes([c("MAX_BET") + 1])),
        ("JOG", bytes([c("REELS")])),
    ):
        assert machine.mutate(c("OP_" + op), payload).status == c("BAD_REQUEST")
    assert machine.call("test_submitted") == 0
    machine.call("test_capabilities", c("CAP_PROFILE"))
    assert machine.mutate(c("OP_JOG"), b"\0").status == c("UNSUPPORTED")
    maximum = (1 << 64) - 1
    assert machine.request(c("OP_BEGIN_PROFILE"), ident=maximum).status == c(
        "BAD_REQUEST"
    )
    assert machine.info()["next_id"] == 0
    assert machine.request(c("OP_BEGIN_PROFILE"), ident=1).status == c("BAD_ID")


def test_failure_to_reserve_id_prevents_any_side_effect(compiled):
    machine = Machine(compiled)
    machine.call("test_fail_after", 1)
    assert machine.request(c("OP_POSITION"), bytes(c("REELS")), ident=1).status == c(
        "FAULT"
    )
    assert machine.call("test_submitted") == 0
    assert machine.call("fw_is_faulted") == 1
    restarted = Machine(compiled, machine.persistent())
    assert restarted.call("test_submitted") == 0


def test_write_verification_rejects_silently_dropped_marker_clear(compiled, profile):
    machine = Machine(compiled)
    assert machine.begin(profile).status == c("OK")
    ident = machine.info()["next_id"]
    machine.call("test_drop_at", 0)
    assert machine.request(
        c("OP_POSITION"), bytes(c("REELS")), ident=ident
    ).status == c("FAULT")
    assert machine.call("test_submitted") == 0
    restarted = Machine(compiled, machine.persistent())
    assert restarted.boot_status == c("OK")
    assert restarted.info()["next_id"] == ident


def test_profile_and_activation_journal_power_cuts_at_every_atomic_write(
    compiled, profile
):
    machine = Machine(compiled)
    machine.activate(profile)
    replacement = bytearray(profile)
    replacement[8] += 1
    machine.stage(replacement)
    ident = machine.info()["next_id"]
    result, snapshots = machine.write_checkpoints(
        lambda: machine.request(c("OP_COMMIT_PROFILE"), ident=ident)
    )
    assert result.status == c("OK")
    seen = set()
    committed = False
    for checkpoint in snapshots:
        restarted = Machine(compiled, checkpoint)
        assert restarted.boot_status == c("OK")
        active = restarted.active()
        assert active in (profile, bytes(replacement))
        seen.add(active)
        if committed:
            assert active == bytes(replacement)
        committed |= active == bytes(replacement)
        assert restarted.call("test_submitted") == 0
        assert restarted.call("test_applied") == 0
        if active == bytes(replacement):
            assert restarted.status()["status"] in (c("ACCEPTED"), c("OK"))
            assert u8(restarted.call("fw_restore")) == c("OK")
            assert restarted.status()["status"] == c("OK")
    assert seen == {profile, bytes(replacement)}
    assert committed


def test_action_reservation_power_cuts_never_reexecute_on_boot(compiled, profile):
    machine = Machine(compiled)
    assert machine.begin(profile).status == c("OK")
    ident = machine.info()["next_id"]
    stops = bytes(range(c("REELS")))
    result, snapshots = machine.write_checkpoints(
        lambda: machine.request(c("OP_POSITION"), stops, ident=ident)
    )
    assert result.status == c("ACCEPTED")
    assert machine.call("test_submitted") == 1
    seen_reserved = False
    for checkpoint in snapshots:
        restarted = Machine(compiled, checkpoint)
        assert restarted.boot_status == c("OK")
        status = restarted.status()
        if status["id"] == ident:
            seen_reserved = True
            assert status["status"] == c("INTERRUPTED")
            assert restarted.request(c("OP_POSITION"), stops, ident=ident).status == c(
                "INTERRUPTED"
            )
        assert restarted.call("test_submitted") == 0
    assert seen_reserved


@pytest.mark.parametrize("phase", ["slot", "journal", "result"])
def test_storage_port_failure_after_partial_write(compiled, profile, phase):
    machine = Machine(compiled)
    machine.activate(profile)
    replacement = bytearray(profile)
    replacement[8] += 1
    machine.stage(replacement)
    ident = machine.info()["next_id"]
    journal_words = c("JOURNAL_SIZE") // 2 + 1
    slot_words = c("SLOT_SIZE") // 2 + 1
    budget = {
        "slot": journal_words + slot_words // 2,
        "journal": journal_words + slot_words + journal_words // 2,
        "result": journal_words + slot_words + journal_words,
    }[phase]
    machine.call("test_fail_after", budget)
    assert machine.request(c("OP_COMMIT_PROFILE"), ident=ident).status == c("FAULT")
    assert machine.call("test_words") == budget
    restarted = Machine(compiled, machine.persistent())
    assert restarted.boot_status == c("OK")
    assert restarted.active() == (bytes(replacement) if phase == "result" else profile)
    assert restarted.status()["status"] == (
        c("ACCEPTED") if phase == "result" else c("INTERRUPTED")
    )
    assert machine.call("test_finish_status") == c("FAULT")
    assert machine.call("test_finished") == machine.call("test_prepared")
    assert machine.call("test_bad_order") == 0
    if phase == "result":
        assert u8(restarted.call("fw_restore")) == c("OK")
        assert restarted.status()["status"] == c("OK")


def test_restore_stock_power_cuts_never_resurrect_after_commit(compiled, profile):
    machine = Machine(compiled)
    machine.activate(profile)
    result, snapshots = machine.write_checkpoints(
        lambda: machine.mutate(c("OP_RESTORE_STOCK"))
    )
    assert result.status == c("OK")
    stock_committed = False
    for checkpoint in snapshots:
        restarted = Machine(compiled, checkpoint)
        assert restarted.boot_status == c("OK")
        active = restarted.active()
        assert active in (profile, None)
        if stock_committed:
            assert active is None
        stock_committed |= active is None
        pending = restarted.status()["status"] == c("ACCEPTED")
        assert u8(restarted.call("fw_restore")) == c("OK")
        # Stock with a pending restoration replays the recorded source baseline.
        expected = (c("PROFILE_BASE"), 0) if active else (
            (c("PROFILE_BASE"), 1) if pending else (0, 0))
        assert (restarted.call("test_profile"), restarted.call("test_restore")) == expected
        assert restarted.status()["status"] in (c("OK"), c("INTERRUPTED"))
    assert stock_committed


def test_clear_handshake_pre_runtime_and_interrupted_completion(compiled, profile):
    machine = Machine(compiled)
    machine.activate(profile)
    before_clear = Machine(compiled, machine.persistent(), boot=False)
    result, snapshots = before_clear.write_checkpoints(
        lambda: u8(before_clear.call("fw_invalidate"))
    )
    assert result == c("OK")
    for checkpoint in snapshots:
        restarted = Machine(compiled, checkpoint)
        assert (restarted.boot_status, restarted.active()) in (
            (c("OK"), profile),
            (c("FAULT"), None),
        )
    interrupted = Machine(compiled, before_clear.persistent())
    assert interrupted.boot_status == c("FAULT")
    assert u8(interrupted.call("fw_restore")) == c("FAULT")
    assert interrupted.call("test_applied") == 0
    completing = Machine(compiled, before_clear.persistent(), boot=False)
    result, snapshots = completing.write_checkpoints(
        lambda: u8(completing.call("fw_clear_complete"))
    )
    assert result == c("OK")
    for checkpoint in snapshots:
        restarted = Machine(compiled, checkpoint)
        assert restarted.active() is None
        assert restarted.boot_status in (c("FAULT"), c("OK"))
    completed = Machine(compiled, completing.persistent())
    assert completed.boot_status == c("OK") and completed.active() is None
    assert completed.info()["next_id"] == machine.info()["next_id"]


@pytest.mark.parametrize(
    "corruption",
    ["profile", "guard", "context", "baseline", "format", "journal", "id_journal"],
)
def test_corruption_faults_instead_of_silent_stock_or_rollback(
    compiled, profile, corruption
):
    machine = Machine(compiled)
    machine.activate(profile)
    persistent = bytearray(machine.persistent())
    if corruption == "profile":
        offset = 8
    elif corruption == "guard":
        offset = c("PROFILE_SIZE")
    elif corruption == "context":
        offset = c("SLOT_CONTEXT_OFFSET")
    elif corruption == "baseline":
        offset = c("SLOT_BASELINE_OFFSET") + c("SLOT_BASELINE_SIZE") - 1
    else:
        base = c("PROFILE_JOURNAL" if corruption == "journal" else "OPERATION_JOURNAL")
        offset = base - c("PROFILE_BASE") + 12
    if corruption == "format":  # an earlier format without a baseline, CRC intact
        start = c("SLOT_BASELINE_OFFSET")
        persistent[start:c("SLOT_MAGIC_OFFSET")] = bytes(c("SLOT_MAGIC_OFFSET") - start)
        resign_slot(persistent)
    else:
        persistent[offset] ^= 1
    restarted = Machine(compiled, persistent)
    assert restarted.boot_status == c("FAULT")
    assert u8(restarted.call("fw_restore")) == c("FAULT")
    assert restarted.call("test_applied") == 0
    assert restarted.info()["next_id"] == 0
    assert restarted.status()["fault"] == 1
    assert restarted.request(
        c("OP_RESTORE_STOCK"), ident=machine.info()["next_id"]
    ).status == c("FAULT")


def test_native_apply_failure_latches_fault_without_erasing_commit(compiled, profile):
    machine = Machine(compiled)
    machine.stage(profile)
    machine.call("test_apply_status", c("FAULT"))
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("FAULT")
    assert machine.call("fw_is_faulted") == 1
    restarted = Machine(compiled, machine.persistent())
    assert restarted.boot_status == c("OK")
    assert restarted.active() == profile
    assert u8(restarted.call("fw_restore")) == c("OK")


def resign_slot(persistent):
    persistent[c("SLOT_CRC_OFFSET"):c("SLOT_CRC_OFFSET") + 4] = struct.pack(
        "<I", zlib.crc32(persistent[:c("SLOT_CRC_OFFSET")]))


def test_restore_when_already_stock_is_a_native_noop(compiled):
    machine = Machine(compiled)
    profiles = machine.cpu.read(c("PROFILE_BASE"), c("OPERATION_JOURNAL") - c("PROFILE_BASE"))
    generation = machine.status()["generation"]
    assert machine.mutate(c("OP_RESTORE_STOCK")).status == c("OK")
    assert machine.call("test_prepared") == machine.call("test_applied") == 0
    assert machine.call("test_finished") == 0
    assert machine.cpu.read(c("PROFILE_BASE"), len(profiles)) == profiles
    assert machine.status()["generation"] == generation
    restarted = Machine(compiled, machine.persistent())
    assert restarted.status()["status"] == c("OK")


def test_custom_to_custom_commit_inherits_from_the_active_slot(compiled, profile):
    machine = Machine(compiled)
    machine.activate(profile)
    first = machine.call("fw_active_profile")
    replacement = bytearray(profile)
    replacement[8] += 1
    machine.activate(bytes(replacement))
    assert machine.call("test_prepare_active") == first
    assert machine.call("fw_active_profile") != first


def pending_restore(compiled, profile):
    """Persistent state of a restoration journaled and applied, whose durable
    completion was cut by power loss."""
    machine = Machine(compiled)
    machine.activate(profile)
    journal_words = c("JOURNAL_SIZE") // 2 + 1
    machine.call("test_fail_after", journal_words * 2)
    assert machine.mutate(c("OP_RESTORE_STOCK")).status == c("FAULT")
    assert machine.call("test_restore") == 1
    return bytearray(machine.persistent())


@pytest.mark.parametrize("damage", ["profile", "baseline", "generation", "format"])
def test_pending_restore_refuses_a_damaged_or_substituted_source(compiled, profile, damage):
    persistent = pending_restore(compiled, profile)
    if damage == "profile":
        persistent[8] ^= 1
    elif damage == "baseline":
        persistent[c("SLOT_BASELINE_OFFSET")] ^= 1
    elif damage == "generation":  # a different, internally valid commit
        persistent[c("SLOT_GENERATION_OFFSET")] ^= 1
        resign_slot(persistent)
    else:
        persistent[c("SLOT_FORMAT_OFFSET")] ^= 1
        resign_slot(persistent)
    restarted = Machine(compiled, bytes(persistent))
    assert restarted.boot_status == c("FAULT")
    assert u8(restarted.call("fw_restore")) == c("FAULT")
    assert restarted.call("test_applied") == 0
    assert restarted.persistent() == bytes(persistent)


def test_failed_restore_application_faults_boot_and_keeps_its_source(compiled, profile):
    machine = Machine(compiled)
    machine.activate(profile)
    source = machine.cpu.read(c("PROFILE_BASE"), c("SLOT_SIZE"))
    machine.call("test_apply_status", c("FAULT"))  # e.g. partially restored, then refused
    assert machine.mutate(c("OP_RESTORE_STOCK")).status == c("FAULT")
    assert machine.call("test_restore") == 1
    restarted = Machine(compiled, machine.persistent())
    assert restarted.boot_status == c("FAULT")
    assert restarted.status()["status"] == c("FAULT")
    assert u8(restarted.call("fw_restore")) == c("FAULT")
    assert restarted.call("test_applied") == 0
    assert restarted.cpu.read(c("PROFILE_BASE"), c("SLOT_SIZE")) == source


def test_completed_restore_lets_a_new_commit_reuse_the_source_slot(compiled, profile):
    machine = Machine(compiled)
    machine.activate(profile)
    assert machine.mutate(c("OP_RESTORE_STOCK")).status == c("OK")
    replacement = bytearray(profile)
    replacement[8] += 1
    machine.stage(bytes(replacement))
    ident = machine.info()["next_id"]
    result, snapshots = machine.write_checkpoints(
        lambda: machine.request(c("OP_COMMIT_PROFILE"), ident=ident)
    )
    assert result.status == c("OK")
    assert machine.call("test_prepare_active") == 0  # stock again: first capture
    assert machine.call("fw_active_profile") == c("PROFILE_BASE")  # the old source
    for checkpoint in snapshots:
        restarted = Machine(compiled, checkpoint)
        assert restarted.boot_status == c("OK")
        assert restarted.active() in (None, bytes(replacement))
        assert u8(restarted.call("fw_restore")) == c("OK")
        assert restarted.call("test_restore") == 0  # the old baseline is never read


def drop_operation(persistent, ident):
    """Clear the commit marker of the operation record holding ident, as if its
    reservation never committed (corruption: profile journals follow it)."""
    for index in range(2):
        record = c("OPERATION_JOURNAL") - c("PROFILE_BASE") + index * c("JOURNAL_SIZE")
        if struct.unpack_from("<Q", persistent, record + 8)[0] == ident:
            marker = record + c("JOURNAL_MARKER_OFFSET")
            persistent[marker:marker + 2] = bytes(2)
            return bytes(persistent)
    raise AssertionError("no operation record holds the ID")


def pending_commit(compiled, profile):
    """A committed ACTIVE journal whose durable completion was cut by power loss."""
    machine = Machine(compiled)
    machine.activate(profile)
    replacement = bytearray(profile)
    replacement[8] += 1
    machine.stage(bytes(replacement))
    journal_words = c("JOURNAL_SIZE") // 2 + 1
    machine.call("test_fail_after", 2 * journal_words + c("SLOT_SIZE") // 2 + 1)
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("FAULT")
    return bytearray(machine.persistent())


@pytest.mark.parametrize("record", [pending_restore, pending_commit])
def test_profile_journal_newer_than_every_operation_faults_boot(compiled, profile, record):
    persistent = record(compiled, profile)
    intact = Machine(compiled, bytes(persistent)).status()
    assert intact["status"] == c("ACCEPTED")  # pending before the damage
    persistent = drop_operation(persistent, intact["id"])
    restarted = Machine(compiled, persistent)
    assert restarted.boot_status == c("FAULT")
    assert u8(restarted.call("fw_restore")) == c("FAULT")
    assert restarted.call("test_applied") == 0
    assert restarted.info()["next_id"] == 0
    assert restarted.persistent() == persistent


def test_profile_preparation_buffer_is_reserved_runtime(compiled, profile):
    machine = Machine(compiled)
    machine.activate(profile)
    staged = machine.call("test_prepare_staged")
    runtime = machine.symbols["fw_runtime"]
    assert runtime == c("RUNTIME_BASE")
    assert runtime <= staged and staged + c("SLOT_SIZE") <= runtime + c("RUNTIME_SIZE")
    assert (
        machine.call("test_profile")
        == machine.call("fw_active_profile")
        == c("PROFILE_BASE")
    )


def test_slot_scratch_reuse_preserves_upload_retry_and_persistent_publication(
    compiled, profile
):
    machine = Machine(compiled)
    machine.stage(profile)
    machine.call("test_prepare_status", c("BUSY"))
    ident = machine.info()["next_id"]
    assert machine.request(c("OP_COMMIT_PROFILE"), ident=ident).status == c("BUSY")
    scratch = machine.call("test_prepare_staged")
    assert machine.call("test_prepare_dirty") == 0
    assert machine.call("test_applied") == machine.call("test_finished") == 0
    assert machine.active() is None
    prepared, writes = machine.call("test_prepared"), machine.call("test_words")
    assert machine.request(c("OP_COMMIT_PROFILE"), ident=ident).status == c("BUSY")
    assert (machine.call("test_prepared"), machine.call("test_words")) == (
        prepared,
        writes,
    )

    machine.cpu.write(scratch, b"\xa7" * c("SLOT_SIZE"))
    machine.call("test_prepare_status", c("OK"))
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("OK")
    first = machine.call("fw_active_profile")
    assert machine.active() == profile
    assert machine.call("test_profile") == first == c("PROFILE_BASE")
    assert machine.call("test_prepare_dirty") == 0
    first_slot = machine.cpu.read(first, c("SLOT_SIZE"))

    replacement = bytearray(profile)
    replacement[8] += 1
    machine.stage(bytes(replacement))
    machine.cpu.write(scratch, b"\x96" * c("SLOT_SIZE"))
    assert machine.mutate(c("OP_COMMIT_PROFILE")).status == c("OK")
    second = machine.call("fw_active_profile")
    assert second == c("PROFILE_BASE") + c("SLOT_SIZE")
    assert machine.call("test_profile") == second
    assert machine.call("test_prepare_active") == first
    assert machine.call("test_prepare_staged") == scratch
    assert machine.call("test_prepare_dirty") == machine.call("test_bad_order") == 0
    assert machine.cpu.read(first, len(first_slot)) == first_slot

    machine.cpu.write(scratch, b"\x5a" * c("SLOT_SIZE"))
    assert machine.active() == bytes(replacement)
    assert u8(machine.call("fw_restore")) == c("OK")
    assert machine.call("test_profile") == second != scratch
    assert machine.cpu.read(
        second + c("PROFILE_SIZE"), c("PROFILE_GUARD_SIZE")
    ) == bytes(c("PROFILE_GUARD_SIZE"))


@pytest.mark.parametrize("transition", ["settled", "commit", "restore"])
def test_boot_ignores_poisoned_slot_scratch_and_preserves_reservation_neighbors(
    compiled, profile, transition
):
    original = Machine(compiled)
    original.activate(profile)
    scratch = original.call("test_prepare_staged")
    pointer, restore = original.call("fw_active_profile"), 0
    if transition != "settled":
        journal_words = c("JOURNAL_SIZE") // 2 + 1
        budget = 2 * journal_words
        if transition == "commit":
            replacement = bytearray(profile)
            replacement[8] += 1
            original.stage(bytes(replacement))
            budget += c("SLOT_SIZE") // 2 + 1
            pointer += c("SLOT_SIZE")
        else:
            restore = 1
        original.call("test_fail_after", budget)
        op = c("OP_COMMIT_PROFILE" if transition == "commit" else "OP_RESTORE_STOCK")
        assert original.mutate(op).status == c("FAULT")
    persistent = original.persistent()
    restarted = Machine(compiled, persistent, boot=False)
    runtime, end = c("RUNTIME_BASE"), c("RUNTIME_BASE") + c("RUNTIME_SIZE")
    assert restarted.symbols["fw_runtime"] == runtime
    assert c("PROFILE_BASE") + c("PERSIST_SIZE") <= runtime
    assert end <= commission_constant("RECORD_BASE")
    neighbors = {
        c("PROFILE_BASE") - 16: b"\xc3" * 16,
        end: b"\x5a" * (commission_constant("RECORD_BASE") + commission_constant("RECORD_SIZE") - end),
    }
    for address, data in neighbors.items():
        restarted.cpu.write(address, data)
    assert restarted.cpu.read(scratch, c("SLOT_SIZE")) == b"\xa7" * c("SLOT_SIZE")
    assert u8(restarted.call("fw_boot")) == c("OK")
    assert restarted.cpu.read(scratch, c("SLOT_SIZE")) == bytes(c("SLOT_SIZE"))
    assert restarted.persistent() == persistent
    assert restarted.call("test_prepared") == restarted.call("test_applied") == 0
    assert u8(restarted.call("fw_restore")) == c("OK")
    assert (restarted.call("test_profile"), restarted.call("test_restore")) == (
        pointer,
        restore,
    )
    assert restarted.status()["status"] == c("OK")
    assert restarted.call("fw_active_profile") == (0 if restore else pointer)
    assert restarted.cpu.read(scratch, c("SLOT_SIZE")) == bytes(c("SLOT_SIZE"))
    for address, data in neighbors.items():
        assert restarted.cpu.read(address, len(data)) == data
