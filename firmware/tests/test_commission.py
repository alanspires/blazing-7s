"""CPU tests of the compiled commissioning core with deterministic ports, not hardware.

Word-write atomicity, SRAM retention, UART behavior and watchdog timing are physical
qualification items and are not established here.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import pytest

from firmware.tests.cpu import Cpu
from firmware.tests.support import (
    SAFERAM,
    SAFERAM_SIZE,
    STORAGE_WORD,
    build_variant,
    commission_constant,
    u8,
)
from firmware.tests.support import fw_constant as c

PERSIST = c("PROFILE_BASE")
PERSIST_SIZE = c("PERSIST_SIZE")
RECORD = commission_constant("RECORD_BASE")
RECORD_SIZE = commission_constant("RECORD_SIZE")
CAPTURE_SIZE = commission_constant("CAPTURE_SIZE")
STATE = 0x431800
CAPTURE = 0x435000
TEST_PORTS = 0x434000
INSTRUCTIONS = 50_000_000
FIRST_INSTALL = b"FIRST-INSTALL"
# Native-format magics the refusal scan must recognize at their defined locations.
PROFILE_MAGIC = b"S6PF"
REQUEST_MAGIC = b"S6RE"

PORTS = r"""
#include "commission.h"
struct test_ports {
    uint32_t words, budget, drop, bad_write, watchdog, identity;
};
extern struct test_ports test_ports;
void test_reset(void) {
    test_ports.words = 0;
    test_ports.budget = UINT32_MAX;
    test_ports.drop = UINT32_MAX;
    test_ports.bad_write = 0;
    test_ports.watchdog = 0;
    test_ports.identity = 0;
}
void test_fail_after(uint32_t words) { test_ports.words = 0; test_ports.budget = words; }
void test_drop_at(uint32_t word) { test_ports.words = 0; test_ports.drop = word; }
uint32_t test_words(void) { return test_ports.words; }
uint32_t test_bad_write(void) { return test_ports.bad_write; }
uint32_t test_watchdog(void) { return test_ports.watchdog; }
uint32_t test_identity(void) { return test_ports.identity; }
static int inside(uint32_t address, uint32_t length, uint32_t base, uint32_t size) {
    return address >= base && address - base <= size && length <= size - (address - base);
}
static int write_words(uint32_t address, const uint8_t *data, uint32_t length) {
    for (uint32_t i = 0; i < length; i += 2u) {
        if (test_ports.words == test_ports.budget) return 0;
        if (test_ports.words != test_ports.drop)
            *(volatile uint16_t *)(uintptr_t)(address + i) =
                (uint16_t)(((uint16_t)data[i] << 8) | data[i + 1u]);
        ++test_ports.words;
    }
    return 1;
}
int commission_storage_write(uint32_t address, const uint8_t *data, uint32_t length) {
    if ((address & 1u) || (length & 1u) ||
        (!inside(address, length, FW_PROFILE_BASE, FW_PERSIST_SIZE) &&
         !inside(address, length, CM_RECORD_BASE, CM_RECORD_SIZE))) {
        test_ports.bad_write = 1;
        return 0;
    }
    return write_words(address, data, length);
}
int fw_storage_write(uint32_t address, const uint8_t *data, uint32_t length) {
    if ((address & 1u) || (length & 1u) ||
        !inside(address, length, FW_PROFILE_BASE, FW_PERSIST_SIZE)) {
        test_ports.bad_write = 1;
        return 0;
    }
    return write_words(address, data, length);
}
void commission_watchdog(void) { ++test_ports.watchdog; }
void commission_identity(uint8_t *identity) {
    ++test_ports.identity;
    for (uint32_t i = 0; i < CM_IDENTITY_SIZE; ++i) identity[i] = (uint8_t)(0xc3u ^ (i * 29u));
}
/* Commissioning must never reach native profile or action ports. */
uint32_t fw_capabilities(void) { test_ports.bad_write = 1; return 0; }
uint8_t fw_prepare_profile_change(uint8_t *staged, const uint8_t *active) {
    (void)staged; (void)active; test_ports.bad_write = 1; return FW_BUSY;
}
void fw_finish_profile_change(uint8_t status) { (void)status; test_ports.bad_write = 1; }
uint8_t fw_apply_profile(const uint8_t *slot, bool restore) {
    (void)slot; (void)restore; test_ports.bad_write = 1; return FW_FAULT;
}
uint8_t fw_submit(uint8_t op, const uint8_t *args, uint32_t length, uint64_t id) {
    (void)op; (void)args; (void)length; (void)id; test_ports.bad_write = 1; return FW_FAULT;
}
uint8_t fw_current_status(uint64_t id) { (void)id; test_ports.bad_write = 1; return FW_FAULT; }
"""

LINK = f"""
OUTPUT_ARCH(m68k)
ENTRY(commission_boot)
PHDRS {{ rom PT_LOAD FLAGS(5); }}
SECTIONS {{
  .text EXT_ROM_START : {{ *(.text*) *(.rodata*) }} :rom
  .unexpected : {{ *(.data*) *(.bss*) *(COMMON) }} :rom
  /DISCARD/ : {{ *(.comment) *(.note*) }}
  fw_runtime = {c("RUNTIME_BASE")};
  commission_state = {STATE};
  commission_capture = {CAPTURE};
  test_ports = {TEST_PORTS};
  ASSERT(SIZEOF(.unexpected) == 0, "no implicit runtime initialization")
  ASSERT(. <= EXT_ROM_END, "extension overflow")
}}
"""


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return build_variant(
        tmp_path_factory,
        "commission",
        sources=("fw.h", "core.c", "commission.h", "commission.c"),
        files={"ports.c": PORTS},
        link=LINK,
        patches=[],
    )


def sparse(length: int, stride: int, salt: int) -> bytearray:
    """Unformatted bytes no recognizer can match: isolated nonzero bytes."""
    data = bytearray(length)
    for offset in range(1, length, stride):
        data[offset] = (offset * 13 + salt) % 255 + 1
    return data


@pytest.fixture
def original():
    """Nonblank, unrecognized persistence plus a nonblank record area to replace.

    Few nonzero bytes keep the number of distinct cut states small; every write is
    still cut, and identical storage states restart identically."""
    persist = sparse(PERSIST_SIZE, 199, 5)
    record = sparse(RECORD_SIZE, 331, 9)
    marker = commission_constant("RECORD_MARKER_OFFSET")
    record[marker : marker + 2] = b"\x12\x34"
    return bytes(persist), bytes(record)


def record_bytes(persist: bytes, marker: int) -> bytes:
    """The contract's record layout, built independently of the firmware."""
    body = bytearray(RECORD_SIZE)
    body[0:4] = struct.pack("<I", commission_constant("RECORD_MAGIC"))
    body[4] = commission_constant("RECORD_VERSION")
    crc_offset = commission_constant("RECORD_ORIGINAL_CRC_OFFSET")
    body[crc_offset : crc_offset + 4] = struct.pack("<I", zlib.crc32(persist))
    start = commission_constant("RECORD_ORIGINAL_OFFSET")
    body[start : start + PERSIST_SIZE] = persist
    end = commission_constant("RECORD_CRC_OFFSET")
    body[end : end + 4] = struct.pack("<I", zlib.crc32(body[:end]))
    body[commission_constant("RECORD_MARKER_OFFSET") :] = struct.pack("<H", marker)
    return bytes(body)


@dataclass(frozen=True)
class Reply:
    status: int
    payload: bytes


class Machine:
    REQUEST = 0x436000
    REPLY = 0x436100
    SENTINEL = bytes((0x5A ^ (i * 7)) & 0xFF for i in range(SAFERAM_SIZE))

    def __init__(self, compiled, persist: bytes, record: bytes, *, boot=True):
        self.symbols = compiled.symbols
        self.cpu = Cpu(compiled.image)
        self.fw_boots = self.cpu.record_visits(self.symbols["fw_boot"])
        self.call("test_reset")
        # Retained working memory is garbage; the core must never trust it.
        self.cpu.write(STATE, b"\xa7" * commission_constant("STATE_SIZE"))
        self.cpu.write(CAPTURE, b"\xa7" * CAPTURE_SIZE)
        self.cpu.write(c("RUNTIME_BASE"), b"\xa7" * c("RUNTIME_SIZE"))
        self.cpu.write(SAFERAM, self.SENTINEL)
        self.cpu.write(PERSIST, persist)
        self.cpu.write(RECORD, record)
        self.boot_status = u8(self.call("commission_boot")) if boot else None

    def call(self, symbol, *args):
        return self.cpu.call(self.symbols[symbol], *args, max_insns=INSTRUCTIONS)

    def persist(self) -> bytes:
        return self.cpu.read(PERSIST, PERSIST_SIZE)

    def record(self) -> bytes:
        return self.cpu.read(RECORD, RECORD_SIZE)

    def storage(self) -> bytes:
        return self.persist() + self.record()

    def assert_contained(self):
        """No native SafeRAM change, no write outside the ports' ranges or native port use."""
        assert self.cpu.read(SAFERAM, SAFERAM_SIZE) == self.SENTINEL
        assert self.call("test_bad_write") == 0

    def raw(self, data: bytes, capacity=None):
        capacity = (
            commission_constant("REPLY_HEADER") + commission_constant("MAX_PAYLOAD") if capacity is None else capacity
        )
        self.cpu.write(self.REQUEST, data)
        length = self.call(
            "commission_process", self.REQUEST, len(data), self.REPLY, capacity
        )
        return self.cpu.read(self.REPLY, length) if length else None

    def request(self, op, payload=b"", *, version=None) -> Reply:
        version = commission_constant("VERSION") if version is None else version
        raw = self.raw(b"S6CM" + bytes((version, op)) + payload)
        assert raw[:6] == b"S6CM" + bytes((commission_constant("VERSION"), op))
        assert len(raw) <= commission_constant("REPLY_HEADER") + commission_constant("MAX_PAYLOAD")
        reply = Reply(raw[6], raw[commission_constant("REPLY_HEADER") :])
        if reply.status != c("OK"):
            assert reply.payload == b""
        return reply

    def info(self) -> dict:
        reply = self.request(commission_constant("OP_INFO"))
        assert reply.status == c("OK")
        assert len(reply.payload) == commission_constant("INFO_SIZE")
        names = (
            "mode",
            "persist_base",
            "persist_size",
            "record_base",
            "record_size",
            "capture_size",
            "max_chunk",
        )
        return dict(zip(names, struct.unpack("<BIHIHHH", reply.payload)))

    def mode(self) -> int:
        return self.info()["mode"]

    def read_all(self) -> bytes:
        result = b""
        for offset in range(0, CAPTURE_SIZE, commission_constant("MAX_CHUNK")):
            count = min(commission_constant("MAX_CHUNK"), CAPTURE_SIZE - offset)
            reply = self.request(commission_constant("OP_READ"), struct.pack("<HB", offset, count))
            assert reply.status == c("OK")
            result += reply.payload
        return result

    def stage(self, offset: int, data: bytes) -> int:
        return self.request(commission_constant("OP_STAGE"), struct.pack("<H", offset) + data).status

    def stage_all(self, capture: bytes, *, skip=None):
        for offset in range(0, CAPTURE_SIZE, commission_constant("MAX_CHUNK")):
            if offset != skip:
                chunk = capture[offset : offset + commission_constant("MAX_CHUNK")]
                assert self.stage(offset, chunk) == c("OK")

    def init(self, literal=FIRST_INSTALL) -> int:
        return self.request(commission_constant("OP_INIT"), literal).status

    def recorded_writes(self, operation):
        """Storage snapshot immediately BEFORE each aligned store, plus the end state."""
        with self.cpu.cut_points(
            self.storage, (PERSIST, PERSIST_SIZE), (RECORD, RECORD_SIZE), store_size=STORAGE_WORD
        ) as snapshots:
            result = operation()
        return result, snapshots


def split(storage: bytes) -> tuple[bytes, bytes]:
    return storage[:PERSIST_SIZE], storage[PERSIST_SIZE:]


def commissioned(compiled, original) -> Machine:
    machine = Machine(compiled, *original)
    machine.stage_all(b"".join(original))
    assert machine.init() == c("OK")
    return machine


def dense(seed: int) -> tuple[bytes, bytes]:
    """Every byte nonzero and varied; checked unrecognized by the boot classifier."""
    state = seed
    data = bytearray()
    while len(data) < CAPTURE_SIZE:
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        data.append((state >> 16) % 255 + 1)
    return split(bytes(data))


# Ordered plan from the contract: marker invalidation, record body (CRC last),
# READY commit, zeroed persistence, DONE.
RECORD_WRITES = RECORD_SIZE // 2 + 1
TOTAL_WRITES = RECORD_WRITES + PERSIST_SIZE // 2 + 1


def marker(record: bytes) -> int:
    return struct.unpack_from("<H", record, commission_constant("RECORD_MARKER_OFFSET"))[0]


def test_protocol_info_read_stage_identity_without_writes(compiled, original):
    machine = Machine(compiled, *original)
    assert machine.boot_status == commission_constant("BOOT_SERVICE")
    assert len(machine.fw_boots) == 1
    assert machine.info() == {
        "mode": commission_constant("MODE_UNKNOWN"),
        "persist_base": PERSIST,
        "persist_size": PERSIST_SIZE,
        "record_base": RECORD,
        "record_size": RECORD_SIZE,
        "capture_size": CAPTURE_SIZE,
        "max_chunk": commission_constant("MAX_CHUNK"),
    }
    assert machine.read_all() == b"".join(original)
    boundary = PERSIST_SIZE - 3
    reply = machine.request(commission_constant("OP_READ"), struct.pack("<HB", boundary, 7))
    assert reply == Reply(c("OK"), b"".join(original)[boundary : boundary + 7])
    for offset, count in (
        (0, 0),
        (0, commission_constant("MAX_CHUNK") + 1),
        (CAPTURE_SIZE - 1, 2),
        (CAPTURE_SIZE, 1),
    ):
        reply = machine.request(commission_constant("OP_READ"), struct.pack("<HB", offset, count))
        assert reply.status == c("BAD_REQUEST")
    assert machine.request(commission_constant("OP_READ"), b"\0\0").status == c("BAD_REQUEST")

    reply = machine.request(commission_constant("OP_IDENTITY"))
    assert reply.status == c("OK")
    assert reply.payload == bytes(
        0xC3 ^ (i * 29 & 0xFF) for i in range(commission_constant("IDENTITY_SIZE"))
    )
    assert machine.call("test_identity") == 1
    assert machine.request(commission_constant("OP_IDENTITY"), b"\0").status == c("BAD_REQUEST")
    assert machine.request(commission_constant("OP_INFO"), b"\0").status == c("BAD_REQUEST")
    assert machine.request(commission_constant("OP_IDENTITY") + 1).status == c("UNSUPPORTED")
    assert machine.request(commission_constant("OP_INFO"), version=commission_constant("VERSION") + 1).status == c(
        "BAD_VERSION"
    )
    header = b"S6CM" + bytes((commission_constant("VERSION"), commission_constant("OP_INFO")))
    for request in (
        header[:5],
        b"S6RE" + header[4:],
        header + b"\0" * (commission_constant("MAX_PAYLOAD") + 1),
    ):
        raw = machine.raw(request)
        assert raw[6] == c("BAD_REQUEST") and len(raw) == commission_constant("REPLY_HEADER")
    assert (
        machine.raw(header, capacity=commission_constant("REPLY_HEADER") + commission_constant("MAX_PAYLOAD") - 1) is None
    )

    chunk = b"".join(original)[100 : 100 + commission_constant("MAX_CHUNK")]
    assert machine.stage(100, chunk) == c("OK")
    assert machine.stage(100, chunk) == c("OK")
    assert machine.stage(130, chunk[30:]) == c("OK")
    conflicting = bytearray(chunk)
    conflicting[-1] ^= 1
    assert machine.stage(100, bytes(conflicting)) == c("BAD_REQUEST")
    assert machine.stage(0, b"") == c("BAD_REQUEST")
    assert machine.stage(0, b"\0" * (commission_constant("MAX_CHUNK") + 1)) == c("BAD_REQUEST")
    assert machine.stage(CAPTURE_SIZE - 1, b"\0\0") == c("BAD_REQUEST")
    assert machine.cpu.read(CAPTURE + 100, len(chunk)) == chunk
    assert machine.call("test_words") == 0
    assert machine.storage() == b"".join(original)
    assert machine.mode() == commission_constant("MODE_UNKNOWN")
    machine.assert_contained()


@pytest.mark.parametrize("record_area", ["blank", "unrelated"])
def test_unrelated_record_area_does_not_block_valid_extension(
    compiled, original, record_area
):
    record = bytes(RECORD_SIZE) if record_area == "blank" else original[1]
    machine = Machine(compiled, bytes(PERSIST_SIZE), record)
    assert machine.boot_status == commission_constant("BOOT_CONTINUE") and len(machine.fw_boots) == 1
    baseline = machine.persist()
    assert baseline != bytes(PERSIST_SIZE)
    restarted = Machine(compiled, baseline, record)
    assert restarted.boot_status == commission_constant("BOOT_CONTINUE") and len(restarted.fw_boots) == 1
    assert restarted.storage() == baseline + record
    restarted.assert_contained()


def test_init_requires_literal_and_complete_conflict_free_archive(compiled, original):
    machine = Machine(compiled, *original)
    capture = b"".join(original)
    machine.stage_all(capture, skip=commission_constant("MAX_CHUNK") * 3)
    assert machine.init() == c("INCOMPLETE")
    for literal in (
        b"",
        FIRST_INSTALL[:-1],
        FIRST_INSTALL.lower(),
        FIRST_INSTALL + b"\0",
    ):
        assert machine.init(literal) == c("BAD_REQUEST")
    machine.stage_all(capture)
    wrong = bytes((capture[0] ^ 0xFF,))
    assert machine.stage(0, wrong) == c("BAD_REQUEST")
    assert machine.call("test_words") == 0
    assert machine.storage() == capture
    assert machine.mode() == commission_constant("MODE_UNKNOWN")
    assert machine.init() == c("OK")
    machine.assert_contained()


def test_archive_must_match_current_storage(compiled, original):
    for index in (5, PERSIST_SIZE + 7):
        machine = Machine(compiled, *original)
        capture = bytearray(b"".join(original))
        capture[index] ^= 0x40
        machine.stage_all(bytes(capture))
        assert machine.init() == c("INVALID_PROFILE")
        assert machine.call("test_words") == 0
        assert machine.storage() == b"".join(original)
        machine.assert_contained()


def test_every_cut_of_first_install_resumes_from_original_archive(compiled, original):
    capture = b"".join(original)
    final = bytes(PERSIST_SIZE) + record_bytes(original[0], commission_constant("DONE"))
    machine = Machine(compiled, *original)
    machine.stage_all(capture)
    status, snapshots = machine.recorded_writes(machine.init)
    assert status == c("OK")
    assert machine.call("test_words") == TOTAL_WRITES == len(snapshots) - 1
    assert snapshots[-1] == final
    assert machine.mode() == commission_constant("MODE_DONE")
    machine.assert_contained()

    distinct = list(dict.fromkeys(snapshots[:-1]))
    previous_mode = commission_constant("MODE_UNKNOWN")
    for snapshot in distinct:
        persist, record = split(snapshot)
        ready = marker(record) == commission_constant("READY")
        if not ready:
            # No persistence word changes before the READY commit.
            assert persist == original[0]
        restarted = Machine(compiled, persist, record)
        assert restarted.boot_status == commission_constant("BOOT_SERVICE")
        assert restarted.storage() == snapshot
        mode = restarted.mode()
        assert mode == (commission_constant("MODE_READY") if ready else commission_constant("MODE_UNKNOWN"))
        assert mode >= previous_mode
        previous_mode = mode
        if ready or record[:4] == struct.pack("<I", commission_constant("RECORD_MAGIC")):
            assert len(restarted.fw_boots) == 0
        restarted.stage_all(capture)
        assert restarted.init() == c("OK")
        assert restarted.storage() == final
        restarted.assert_contained()
    assert previous_mode == commission_constant("MODE_READY")


def test_done_record_runs_core_bootstrap_at_every_cut_and_never_reformats(
    compiled, original
):
    capture = b"".join(original)
    done = commissioned(compiled, original)
    assert done.init() == c("INVALID_PROFILE")
    machine = Machine(compiled, *split(done.storage()), boot=False)

    def boot():
        return u8(machine.call("commission_boot"))

    status, snapshots = machine.recorded_writes(boot)
    assert status == commission_constant("BOOT_CONTINUE") and len(machine.fw_boots) == 1
    baseline = machine.storage()
    assert split(baseline)[1] == record_bytes(original[0], commission_constant("DONE"))
    for snapshot in dict.fromkeys(snapshots):
        restarted = Machine(compiled, *split(snapshot))
        assert restarted.boot_status == commission_constant("BOOT_CONTINUE") and len(restarted.fw_boots) == 1
        assert restarted.storage() == baseline
        assert restarted.mode() == commission_constant("MODE_DONE")
        restarted.stage_all(capture)
        assert restarted.init() == c("INVALID_PROFILE")
        assert restarted.storage() == baseline
        restarted.assert_contained()


@pytest.mark.parametrize("failure", ["stop", "readback"])
@pytest.mark.parametrize(
    "cut",
    [
        0,
        1,
        9,
        500,
        RECORD_WRITES - 3,
        RECORD_WRITES - 1,
        RECORD_WRITES,
        RECORD_WRITES + 400,
        TOTAL_WRITES - 1,
    ],
)
def test_dense_capture_write_failure_latches_and_resumes_after_restart(
    compiled, cut, failure
):
    original = dense(cut + 1)
    capture = b"".join(original)
    final = bytes(PERSIST_SIZE) + record_bytes(original[0], commission_constant("DONE"))
    machine = Machine(compiled, *original)
    assert machine.mode() == commission_constant("MODE_UNKNOWN")
    machine.stage_all(capture)
    machine.call("test_fail_after" if failure == "stop" else "test_drop_at", cut)
    assert machine.init() == c("FAULT")
    assert machine.mode() == commission_constant("MODE_REFUSED")
    torn = machine.storage()
    machine.call("test_reset")
    assert machine.init() == c("INVALID_PROFILE")
    assert machine.call("test_words") == 0 and machine.storage() == torn
    machine.assert_contained()

    restarted = Machine(compiled, *split(torn))
    assert restarted.boot_status == commission_constant("BOOT_SERVICE")
    record = split(torn)[1]
    ready = marker(record) == commission_constant("READY")
    assert restarted.mode() == (commission_constant("MODE_READY") if ready else commission_constant("MODE_UNKNOWN"))
    if ready or record[:4] == struct.pack("<I", commission_constant("RECORD_MAGIC")):
        # A recognizable record needs the original archive, not a recapture.
        assert len(restarted.fw_boots) == 0
        restarted.stage_all(restarted.read_all())
        assert restarted.init() == c("INVALID_PROFILE")
        restarted = Machine(compiled, *split(torn))
    restarted.stage_all(capture)
    assert restarted.init() == c("OK")
    assert restarted.storage() == final
    restarted.assert_contained()


def cut_install(compiled, original, words: int) -> bytes:
    """Storage after the first install's port stops before write number `words`."""
    machine = Machine(compiled, *original)
    machine.stage_all(b"".join(original))
    machine.call("test_fail_after", words)
    assert machine.init() == c("FAULT")
    return machine.storage()


def test_ready_resume_requires_original_archive_and_zero_prefix(compiled, original):
    capture = b"".join(original)
    zeroed = 3
    ready = cut_install(compiled, original, RECORD_WRITES + zeroed)
    persist, record = split(ready)
    assert marker(record) == commission_constant("READY")
    nonzero = next(
        i for i in range(0, PERSIST_SIZE, 2) if persist[i : i + 2] != b"\0\0"
    )

    def refused(storage: bytes, archive: bytes):
        machine = Machine(compiled, *split(storage))
        assert machine.boot_status == commission_constant("BOOT_SERVICE") and len(machine.fw_boots) == 0
        assert machine.mode() == commission_constant("MODE_READY")
        machine.stage_all(archive)
        assert machine.init() == c("INVALID_PROFILE")
        assert machine.call("test_words") == 0 and machine.storage() == storage
        machine.assert_contained()

    refused(ready, ready)  # a recapture is not the original archive
    other = bytearray(capture)
    other[nonzero] ^= 0x10
    refused(ready, bytes(other))
    changed = bytearray(ready)
    changed[nonzero] ^= 0x10  # persistence changed beyond the zeroed prefix
    refused(bytes(changed), capture)
    gap = bytearray(ready)
    later = next(
        i for i in range(nonzero + 2, PERSIST_SIZE, 2) if ready[i : i + 2] != b"\0\0"
    )
    gap[later : later + 2] = b"\0\0"  # zeroed word after an original word
    refused(bytes(gap), capture)


# (offset in persistence, magic bytes) of every recognizable native-format field.
SLOT_FIELDS = [
    (slot * c("SLOT_SIZE") + offset, magic)
    for slot in range(2)
    for offset, magic in (
        (0, PROFILE_MAGIC),
        (c("SLOT_MAGIC_OFFSET"), struct.pack("<I", c("SLOT_MAGIC"))),
    )
]
JOURNAL_FIELDS = [
    (base - PERSIST + index * c("JOURNAL_SIZE") + offset, magic)
    for base, journal_magic in (
        (c("PROFILE_JOURNAL"), c("JOURNAL_PROFILE_MAGIC")),
        (c("OPERATION_JOURNAL"), c("JOURNAL_OPERATION_MAGIC")),
    )
    for index in range(2)
    for offset, magic in (
        ((0, struct.pack("<I", journal_magic)),)
        + (
            ((c("OPERATION_REQUEST_OFFSET"), REQUEST_MAGIC),)
            if base == c("OPERATION_JOURNAL")
            else ()
        )
    )
]
MARKERS = [slot * c("SLOT_SIZE") + c("SLOT_MARKER_OFFSET") for slot in range(2)] + [
    c("PROFILE_JOURNAL")
    - PERSIST
    + index * c("JOURNAL_SIZE")
    + c("JOURNAL_MARKER_OFFSET")
    for index in range(4)
]


def recognizable_cases():
    for offset, magic in SLOT_FIELDS + JOURNAL_FIELDS:
        yield pytest.param(offset, magic, id=f"magic-{offset:#x}")
        damaged = bytearray(magic)
        damaged[offset % 4] ^= 0x01
        yield pytest.param(offset, bytes(damaged), id=f"near-{offset:#x}")
    for offset in MARKERS:
        for value in (commission_constant("READY"), commission_constant("DONE")):
            yield pytest.param(
                offset, struct.pack("<H", value), id=f"marker-{offset:#x}-{value:#x}"
            )


@pytest.mark.parametrize(("offset", "field"), list(recognizable_cases()))
def test_recognizable_extension_state_refuses_first_install(
    compiled, original, offset, field
):
    persist = bytearray(original[0])
    persist[offset : offset + len(field)] = field
    machine = Machine(compiled, bytes(persist), original[1])
    assert machine.boot_status == commission_constant("BOOT_SERVICE")
    assert machine.mode() == commission_constant("MODE_REFUSED")
    storage = machine.storage()
    machine.stage_all(storage)
    assert machine.init() == c("INVALID_PROFILE")
    assert machine.call("test_words") == 0 and machine.storage() == storage
    machine.assert_contained()


def test_faulted_existing_extension_is_refused(compiled):
    installed = Machine(compiled, bytes(PERSIST_SIZE), bytes(RECORD_SIZE))
    assert installed.boot_status == commission_constant("BOOT_CONTINUE")
    persist = bytearray(installed.persist())
    crc = c("OPERATION_JOURNAL") - PERSIST + c("JOURNAL_CRC_OFFSET")
    persist[crc] ^= 0x01  # committed journal with a bad CRC faults fw_boot
    machine = Machine(compiled, bytes(persist), bytes(RECORD_SIZE))
    assert machine.boot_status == commission_constant("BOOT_SERVICE") and len(machine.fw_boots) == 1
    assert machine.mode() == commission_constant("MODE_REFUSED")
    storage = machine.storage()
    machine.stage_all(storage)
    assert machine.init() == c("INVALID_PROFILE")
    assert machine.call("test_words") == 0 and machine.storage() == storage
    machine.assert_contained()


def malformed_record(persist: bytes, kind: str, value: int) -> bytes:
    record = bytearray(record_bytes(persist, value))
    end = commission_constant("RECORD_CRC_OFFSET")
    if kind == "body":
        record[commission_constant("RECORD_ORIGINAL_OFFSET") + 1] ^= 0x01
        return bytes(record)
    if kind == "uncommitted":
        # A complete header whose marker is neither cleared nor committed.
        record[commission_constant("RECORD_MARKER_OFFSET") :] = b"\x12\x34"
        return bytes(record)
    record[end - 1 if kind == "padding" else 3] ^= 0x01
    record[end : end + 4] = struct.pack("<I", zlib.crc32(record[:end]))
    return bytes(record)


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (kind, commission_constant(name))
        for kind in ("body", "padding", "magic")
        for name in ("READY", "DONE")
    ]
    + [("uncommitted", commission_constant("READY"))],
)
def test_committed_or_damaged_record_fails_closed(compiled, original, kind, value):
    record = malformed_record(original[0], kind, value)
    machine = Machine(compiled, original[0], record)
    assert machine.boot_status == commission_constant("BOOT_SERVICE")
    assert len(machine.fw_boots) == 0
    assert machine.mode() == commission_constant("MODE_REFUSED")
    machine.stage_all(b"".join(original))
    assert machine.init() == c("INVALID_PROFILE")
    assert machine.call("test_words") == 0
    assert machine.storage() == original[0] + record
    machine.assert_contained()


def test_torn_creation_resumes_only_as_an_exact_prefix_of_the_archive(
    compiled, original
):
    capture = b"".join(original)
    header_words = commission_constant("RECORD_ORIGINAL_OFFSET") // 2
    torn = cut_install(compiled, original, 1 + header_words)
    persist, record = split(torn)
    assert persist == original[0] and marker(record) == 0
    assert (
        record[: commission_constant("RECORD_ORIGINAL_OFFSET")]
        == record_bytes(original[0], 0)[: commission_constant("RECORD_ORIGINAL_OFFSET")]
    )
    late = RECORD_SIZE - 0x100

    def attempt(storage: bytes, archive: bytes) -> int:
        machine = Machine(compiled, *split(storage))
        assert machine.boot_status == commission_constant("BOOT_SERVICE") and len(machine.fw_boots) == 0
        assert machine.mode() == commission_constant("MODE_UNKNOWN")
        machine.stage_all(archive)
        status = machine.init()
        if status != c("OK"):
            assert machine.call("test_words") == 0 and machine.storage() == storage
        machine.assert_contained()
        return status

    wrong = bytearray(capture)
    wrong[PERSIST_SIZE + late] ^= 0x01  # archive record area differs beyond the prefix
    assert attempt(torn, bytes(wrong)) == c("INVALID_PROFILE")
    committed = bytearray(capture)
    committed[PERSIST_SIZE + commission_constant("RECORD_MARKER_OFFSET") :] = struct.pack(
        "<H", commission_constant("READY")
    )
    assert attempt(torn, bytes(committed)) == c("INVALID_PROFILE")
    hole = bytearray(torn)
    hole[PERSIST_SIZE + late] ^= 0x01  # a changed word after the written prefix
    assert attempt(bytes(hole), capture) == c("INVALID_PROFILE")
    assert attempt(torn, capture) == c("OK")


def clearing(machine: Machine) -> bytes:
    """Journal a native clear that never completed, as fw_invalidate leaves it."""
    assert machine.boot_status == commission_constant("BOOT_CONTINUE")
    assert u8(machine.call("fw_invalidate")) == c("OK")
    return machine.persist()


@pytest.mark.parametrize("record_state", ["absent", "done"])
def test_interrupted_native_clear_continues_to_native_recovery(
    compiled, original, record_state
):
    if record_state == "absent":
        record = bytes(RECORD_SIZE)
        persist = clearing(Machine(compiled, bytes(PERSIST_SIZE), record))
    else:
        done = commissioned(compiled, original)
        record = done.record()
        persist = clearing(Machine(compiled, done.persist(), record))
    machine = Machine(compiled, persist, record)
    assert machine.boot_status == commission_constant("BOOT_CONTINUE") and len(machine.fw_boots) == 1
    assert machine.storage() == persist + record
    machine.assert_contained()

    corrupt = bytearray(persist)
    for index in range(2):  # damage whichever profile journal holds the clear
        corrupt[
            c("PROFILE_JOURNAL")
            - PERSIST
            + index * c("JOURNAL_SIZE")
            + c("JOURNAL_CRC_OFFSET")
        ] ^= 0x01
    machine = Machine(compiled, bytes(corrupt), record)
    assert machine.boot_status == commission_constant("BOOT_SERVICE")
    assert machine.mode() == (
        commission_constant("MODE_DONE") if record_state == "done" else commission_constant("MODE_REFUSED")
    )
    assert machine.storage() == bytes(corrupt) + record
    machine.assert_contained()


def test_ready_record_never_takes_the_native_clear_allowance(compiled):
    persist = clearing(Machine(compiled, bytes(PERSIST_SIZE), bytes(RECORD_SIZE)))
    record = record_bytes(persist, commission_constant("READY"))
    machine = Machine(compiled, persist, record)
    assert machine.boot_status == commission_constant("BOOT_SERVICE") and len(machine.fw_boots) == 0
    assert machine.mode() == commission_constant("MODE_READY")
    machine.assert_contained()


@pytest.mark.parametrize("known", ["virgin", "baseline", "clearing", "done"])
def test_init_after_normal_continue_never_formats_known_state(
    compiled, original, known
):
    """A direct INIT after CONTINUE, with an archive matching current storage."""
    record = bytes(RECORD_SIZE)
    persist = bytes(PERSIST_SIZE)
    if known == "baseline":
        persist = Machine(compiled, persist, record).persist()
    elif known == "clearing":
        persist = clearing(Machine(compiled, persist, record))
    elif known == "done":
        done = commissioned(compiled, original)
        persist, record = split(done.storage())
    machine = Machine(compiled, persist, record)
    assert machine.boot_status == commission_constant("BOOT_CONTINUE")
    expected = commission_constant("MODE_DONE") if known == "done" else commission_constant("MODE_REFUSED")
    assert machine.mode() == expected
    storage = machine.storage()
    words = machine.call("test_words")  # the core's own bootstrap may have written
    machine.stage_all(machine.read_all())
    assert machine.init() == c("INVALID_PROFILE")
    assert machine.call("test_words") == words and machine.storage() == storage
    assert machine.mode() == expected
    # INIT's own archive scan must refuse even if the session mode were lost
    # (mode is the context's first byte).
    machine.cpu.write_u8(STATE, commission_constant("MODE_UNKNOWN"))
    assert machine.init() == c("INVALID_PROFILE")
    assert machine.call("test_words") == words and machine.storage() == storage
    machine.assert_contained()
