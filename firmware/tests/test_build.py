from __future__ import annotations

import json
import struct

import pytest
from unicorn.m68k_const import UC_M68K_REG_A2, UC_M68K_REG_D2, UC_M68K_REG_D3

from firmware.build import (
    CHECKSUM_RANGES,
    CHIP_SIZE,
    MAIN_SHA256,
    MAIN_SIZE,
    PROGRAMMING_DEVICE,
    TARGET_FLAGS,
    BuildError,
    Segment,
    apply_patch,
    build,
    checksum,
    load_segments,
    run,
    sha256,
    split_chips,
    tool,
    validate_spec,
)
from firmware.tests.cpu import DECODE_CHUNK_BYTES, Cpu, CpuError
from firmware.tests.support import MAIN_ROM, ROOT

BASE = MAIN_ROM.read_bytes()
EXT_START = 0x79200
EXT_END = MAIN_SIZE
PATCH_OFFSET = 0x100


def specification():
    return {
        "base": {"path": str(MAIN_ROM), "sha256": MAIN_SHA256},
        "extension": {
            "start": hex(EXT_START),
            "end": hex(EXT_END),
            "expect_fill": "00",
        },
        "patches": [
            {
                "name": "test-call",
                "offset": PATCH_OFFSET,
                "expected": BASE[PATCH_OFFSET : PATCH_OFFSET + 6].hex(),
                "symbol": "arithmetic",
                "kind": "jsr",
            },
            {
                "name": "test-return",
                "offset": PATCH_OFFSET + 6,
                "expected": BASE[PATCH_OFFSET + 6 : PATCH_OFFSET + 8].hex(),
                "replacement": "4e75",
            },
        ],
    }


def write_project(root):
    root.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "patches.json").write_text(json.dumps(specification(), indent=2) + "\n")
    (root / "link.ld").write_text("""
OUTPUT_ARCH(m68k)
ENTRY(arithmetic)
PHDRS { rom PT_LOAD FLAGS(5); }
SECTIONS {
  .text EXT_ROM_START : { *(.text*) *(.rodata*) } :rom
  /DISCARD/ : { *(.comment) *(.note*) }
  ASSERT(. <= EXT_ROM_END, "extension exceeds its declared space")
}
""")
    (root / "src/core.c").write_text("""
typedef unsigned long u32;
_Static_assert(sizeof(u32) == 4, "32-bit ABI");
u32 arithmetic(u32 x, u32 y) { return x * y + 7; }
u32 divide(u32 x, u32 y) { return x / y; }
u32 weighted_sum(volatile u32 *dst, const unsigned char *p, u32 n) {
    u32 total = 0;
    for (u32 i = 0; i < n; ++i) total += (u32)p[i] * (i + 1);
    *dst = total;
    return total;
}
u32 io_read(void) { return *(volatile unsigned short *)0xff1000; }
void endless(void) { for (;;) { __asm__ volatile ("nop"); } }
""")
    (root / "src/hooks.S").write_text("""
    .text
    .globl preserve
preserve:
    movem.l %d2-%d3/%a2,-(%sp)
    move.l #0x12345678,%d2
    move.l #0x87654321,%d3
    movea.l #0x400000,%a2
    move.l %d2,(%a2)
    cmp.l %d3,%d2
    bhi.s unexpected
    moveq #1,%d0
    bra.s done
unexpected:
    moveq #0,%d0
done:
    movem.l (%sp)+,%d2-%d3/%a2
    rts
    .globl unaligned
unaligned:
    move.w 0x400001,%d0
    rts
    .section .note.GNU-stack,"",@progbits
""")
    return root / "patches.json"


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    root = tmp_path_factory.mktemp("build-fixture") / "one"
    spec = write_project(root)
    return build(spec)


def interleave(even: bytes, odd: bytes) -> bytes:
    return bytes(byte for pair in zip(even, odd, strict=True) for byte in pair)


def test_original_checksum_and_physical_lane_reconstruction():
    assert sha256(BASE) == MAIN_SHA256
    assert checksum(BASE) == 0
    chips = split_chips(BASE)
    assert {name: len(data) for name, data in chips.items()} == {
        "U28_even.bin": CHIP_SIZE,
        "U43_odd.bin": CHIP_SIZE,
    }
    assert interleave(chips["U28_even.bin"], chips["U43_odd.bin"]) == BASE


@pytest.mark.parametrize("size", [CHIP_SIZE, MAIN_SIZE - 2, MAIN_SIZE + 2])
def test_chip_split_rejects_other_main_sizes(size):
    with pytest.raises(BuildError):
        split_chips(bytes(size))


def test_emitted_chip_images_match_device_and_main_image(compiled):
    manifest = json.loads((compiled.directory / "manifest.json").read_text())
    main = (compiled.directory / "main.bin").read_bytes()
    assert main == compiled.image
    assert manifest["outputs"]["main.bin"]["size"] == MAIN_SIZE
    assert manifest["programming_device"] == PROGRAMMING_DEVICE
    assert PROGRAMMING_DEVICE["capacity"] == CHIP_SIZE
    assert (
        PROGRAMMING_DEVICE["manufacturer_id"],
        PROGRAMMING_DEVICE["device_id"],
    ) == (0xDA, 0x85)
    assert set(manifest["chips"]) == set(split_chips(main))
    lanes = {}
    for name, chip in manifest["chips"].items():
        assert list(chip) == ["lane"]
        data = (compiled.directory / name).read_bytes()
        assert manifest["outputs"][name] == {
            "sha256": sha256(data),
            "size": PROGRAMMING_DEVICE["capacity"],
            "byte_sum16": sum(data) & 0xFFFF,
        }
        lanes[chip["lane"]] = data
    assert interleave(lanes.pop("even"), lanes.pop("odd")) == main
    assert not lanes


def test_checksum_includes_only_documented_ranges():
    data = bytearray(MAIN_SIZE)
    for start, end in CHECKSUM_RANGES:
        data[start : start + 2] = b"\x12\x34"
        data[end - 2 : end] = b"\x00\x01"
    data[0x7F0:0x800] = b"\xff" * (0x800 - 0x7F0)
    assert checksum(data) == len(CHECKSUM_RANGES) * (0x1234 + 1)


@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "size",
        "fill",
        "expected",
        "overlap",
        "length",
        "range",
        "unaligned",
        "unknown-field",
    ],
)
def test_rejects_unverified_or_ambiguous_inputs(fault):
    spec = specification()
    base = BASE
    if fault == "hash":
        spec["base"]["sha256"] = "0" * len(MAIN_SHA256)
    elif fault == "size":
        base = base[:-1]
    elif fault == "fill":
        spec["extension"]["expect_fill"] = "ff"
    elif fault == "expected":
        expected = bytearray.fromhex(spec["patches"][0]["expected"])
        expected[0] ^= 1
        spec["patches"][0]["expected"] = expected.hex()
    elif fault == "overlap":
        another = {**spec["patches"][0], "name": "overlap"}
        spec["patches"].append(another)
    elif fault == "length":
        spec["patches"][1]["replacement"] = "4e714e71"
    elif fault == "range":
        spec["extension"]["end"] = MAIN_SIZE + 2
    elif fault == "unaligned":
        spec["extension"]["start"] = EXT_START + 1
    elif fault == "unknown-field":
        spec["extension"]["safe"] = True
    with pytest.raises(BuildError):
        validate_spec(spec, base)


@pytest.mark.parametrize(
    "segments,symbols",
    [
        ([Segment(EXT_START - 2, b"\x4e\x75")], {"arithmetic": EXT_START}),
        ([Segment(EXT_END - 2, b"\x4e\x75")], {"arithmetic": EXT_START}),
        (
            [Segment(EXT_START, b"\x4e\x75"), Segment(EXT_START, b"\x4e\x75")],
            {"arithmetic": EXT_START},
        ),
        ([Segment(EXT_START, b"\x4e\x75")], {}),
        ([Segment(EXT_START, b"\x4e\x75")], {"arithmetic": EXT_START + 1}),
        ([Segment(EXT_START, b"\x4e\x75")], {"arithmetic": EXT_START + 2}),
    ],
)
def test_rejects_outside_or_overlapping_loads_and_bad_symbols(segments, symbols):
    with pytest.raises(BuildError):
        apply_patch(BASE, specification(), segments, symbols)


def test_symbolic_patches_and_compensation_preserve_other_bytes(compiled):
    image = compiled.image
    assert len(image) == MAIN_SIZE and checksum(image) == 0
    assert image[PATCH_OFFSET : PATCH_OFFSET + 6] == b"\x4e\xb9" + compiled.symbols[
        "arithmetic"
    ].to_bytes(4, "big")
    assert image[0x7EE:0x7F0] == BASE[0x7EE:0x7F0]
    reconstructed = bytearray(BASE)
    for patch in compiled.manifest["patches"]:
        start = patch["offset"]
        end = start + patch["length"]
        assert reconstructed[start:end] == bytes.fromhex(patch["expected"])
        reconstructed[start:end] = bytes.fromhex(patch["replacement"])
    assert bytes(reconstructed) == image
    assert MAIN_ROM.read_bytes() == BASE
    assert compiled.manifest["install_ready"] is False
    for name, record in compiled.manifest["outputs"].items():
        data = (compiled.directory / name).read_bytes()
        assert len(data) == record["size"]
        assert sha256(data) == record["sha256"]


def test_target_selection_and_stack_report(compiled):
    assert (
        run([tool("gcc"), *TARGET_FLAGS, "-print-multi-directory"], ROOT).strip()
        == "m68000"
    )
    entries = (
        (compiled.directory / compiled.manifest["stack_usage"]["report"])
        .read_text()
        .splitlines()
    )
    frames = {}
    for entry in entries:
        location, size, qualifiers = entry.split("\t")
        frames[location.rsplit(":", 1)[1]] = int(size)
        assert qualifiers in {"static", "dynamic,bounded"}
    assert {"arithmetic", "divide", "weighted_sum"} <= frames.keys()
    assert all(size >= 0 for size in frames.values())


def test_pointer_and_jump_encoding():
    spec = specification()
    spec["patches"] = [
        {
            "name": "jump",
            "offset": PATCH_OFFSET,
            "expected": BASE[PATCH_OFFSET : PATCH_OFFSET + 6].hex(),
            "symbol": "fn",
            "kind": "jmp",
        },
        {
            "name": "pointer",
            "offset": PATCH_OFFSET + 6,
            "expected": BASE[PATCH_OFFSET + 6 : PATCH_OFFSET + 10].hex(),
            "symbol": "ram",
            "kind": "pointer",
        },
    ]
    symbols = {"fn": EXT_START, "ram": 0x400000}
    image, _ = apply_patch(BASE, spec, [Segment(EXT_START, b"\x4e\x75")], symbols)
    assert image[PATCH_OFFSET : PATCH_OFFSET + 10] == b"\x4e\xf9" + struct.pack(
        ">II", symbols["fn"], symbols["ram"]
    )


def test_reproducible_across_directories(compiled, tmp_path):
    other = build(
        write_project(tmp_path / "different-root"), tmp_path / "different-output"
    )
    assert other.image == compiled.image
    for name in (*compiled.manifest["outputs"], "manifest.json"):
        assert (other.directory / name).read_bytes() == (
            compiled.directory / name
        ).read_bytes()


def test_elf_identity_and_runtime_requirements(compiled):
    elf = (compiled.directory / "ext.elf").read_bytes()
    assert load_segments(elf)
    wrong_cpu = bytearray(elf)
    struct.pack_into(">I", wrong_cpu, 36, 0)
    with pytest.raises(BuildError):
        load_segments(wrong_cpu)
    with pytest.raises(BuildError):
        load_segments(elf[:40])
    with_bss = bytearray(elf)
    phoff = struct.unpack_from(">I", elf, 28)[0]
    filesz = struct.unpack_from(">I", elf, phoff + 16)[0]
    struct.pack_into(">I", with_bss, phoff + 20, filesz + 4)
    with pytest.raises(BuildError):
        load_segments(with_bss)


def test_unresolved_dependencies_fail_without_emitting_images(tmp_path):
    spec = write_project(tmp_path / "unresolved")
    (spec.parent / "src/missing.c").write_text(
        "extern void missing_runtime(void); void fail(void) { missing_runtime(); }\n"
    )
    with pytest.raises(BuildError):
        build(spec)
    assert not (spec.parent / "build/main.bin").exists()


def test_output_symlink_cannot_overwrite_original(compiled, tmp_path):
    spec = write_project(tmp_path / "symlink-output")
    output = spec.parent / "build"
    output.mkdir()
    (output / "main.bin").symlink_to(MAIN_ROM)
    with pytest.raises(BuildError):
        build(spec)
    assert MAIN_ROM.read_bytes() == BASE


@pytest.mark.parametrize("x,y", [(7, 6), (0x123456, 123), (0xFEDCBA98, 0x12345)])
def test_compiled_arithmetic_and_68000_libgcc(compiled, x, y):
    cpu = Cpu(compiled.image)
    assert cpu.call(compiled.symbols["arithmetic"], x, y) == (x * y + 7) & 0xFFFFFFFF
    assert cpu.call(compiled.symbols["divide"], x, y) == x // y


def test_compiled_buffer_loop_and_native_register_preservation(compiled):
    cpu = Cpu(compiled.image)
    data = bytes([3, 17, 0, 255, 22])
    source, destination = 0x400100, 0x400200
    cpu.write(source, data)
    expected = sum(byte * (i + 1) for i, byte in enumerate(data))
    assert (
        cpu.call(compiled.symbols["weighted_sum"], destination, source, len(data))
        == expected
    )
    assert cpu.read_u32(destination) == expected
    sentinels = {
        UC_M68K_REG_D2: 0x11223344,
        UC_M68K_REG_D3: 0x55667788,
        UC_M68K_REG_A2: 0x400300,
    }
    for register, value in sentinels.items():
        cpu.uc.reg_write(register, value)
    assert cpu.call(compiled.symbols["preserve"]) == 1
    assert cpu.read_registers(sentinels) == sentinels
    assert cpu.read_u32(0x400000) == 0x12345678


def test_compiled_io_is_unmapped_until_explicitly_requested(compiled):
    cpu = Cpu(compiled.image)
    with pytest.raises(CpuError) as failure:
        cpu.call(compiled.symbols["io_read"])
    assert failure.value.code == "unicorn"
    cpu = Cpu(compiled.image)
    cpu.map_io(0xFF1000)
    expected = 0xCAFE
    cpu.write_u16(0xFF1000, expected)
    assert cpu.call(compiled.symbols["io_read"]) == expected


def test_alignment_and_bounded_execution_fail_closed(compiled):
    cpu = Cpu(compiled.image)
    with pytest.raises(CpuError) as failure:
        cpu.call(compiled.symbols["unaligned"])
    assert failure.value.code == "alignment"
    cpu = Cpu(compiled.image)
    with pytest.raises(CpuError) as failure:
        cpu.call(compiled.symbols["endless"], max_insns=100)
    assert failure.value.code == "instruction-budget"


def test_decoder_lookahead_does_not_reject_instruction_crossing_chunk_end():
    expected = 0x12345678
    nops = (DECODE_CHUNK_BYTES - 2) // 2
    image = (
        bytes(0x100)
        + bytes.fromhex("4e71") * nops
        + bytes.fromhex("203c")
        + expected.to_bytes(4, "big")
        + bytes.fromhex("4e75")
    )
    assert Cpu(image).call(0x100) == expected


@pytest.mark.parametrize(
    "opcode,code",
    [
        (
            "4c0100004e75",
            "instruction",
        ),  # MULU.L is 68020-only; Unicorn would execute it.
        ("4afc", "instruction"),
        ("4e43", "exception"),  # TRAP #3 is not silently simulated.
    ],
)
def test_cpu_rejects_unsupported_isa_and_unhandled_exceptions(opcode, code):
    image = bytes(0x100) + bytes.fromhex(opcode)
    cpu = Cpu(image)
    with pytest.raises(CpuError) as failure:
        cpu.call(0x100)
    assert failure.value.code == code


@pytest.mark.parametrize("length", [8, 10, 18])
def test_whole_instruction_jsr_with_nop_padding(length):
    spec = specification()
    patch = spec["patches"][0]
    patch.update(
        kind="jsr-nop", expected=BASE[PATCH_OFFSET : PATCH_OFFSET + length].hex()
    )
    spec["patches"] = [patch]
    image, ranges = apply_patch(
        BASE, spec, [Segment(EXT_START, b"\x4e\x75")], {"arithmetic": EXT_START}
    )
    assert image[PATCH_OFFSET : PATCH_OFFSET + length] == (
        b"\x4e\xb9" + EXT_START.to_bytes(4, "big") + b"\x4e\x71" * ((length - 6) // 2)
    )
    assert next(r for r in ranges if r["name"] == patch["name"])["length"] == length


@pytest.mark.parametrize(
    "kind,length",
    [
        ("pointer", 6),
        ("pointer", 8),
        ("jsr", 8),
        ("jmp", 8),
        ("jsr-nop", 4),
        ("jsr-nop", 6),
        ("jsr-nop", 7),
        ("jsr-nop", 9),
    ],
)
def test_symbolic_patch_width_contract(kind, length):
    spec = specification()
    patch = spec["patches"][0]
    patch.update(kind=kind, expected=BASE[PATCH_OFFSET : PATCH_OFFSET + length].hex())
    spec["patches"] = [patch]
    with pytest.raises(BuildError):
        validate_spec(spec, BASE)


@pytest.mark.parametrize("fault", ["expected", "overlap", "alignment", "target"])
def test_padded_call_retains_verification(fault):
    spec = specification()
    patch = spec["patches"][0]
    patch.update(kind="jsr-nop", expected=BASE[PATCH_OFFSET : PATCH_OFFSET + 8].hex())
    spec["patches"] = [patch]
    target = EXT_START
    if fault == "expected":
        patch["expected"] = "00" * 8
    elif fault == "overlap":
        spec["patches"].append(
            {
                "name": "overlap",
                "offset": PATCH_OFFSET + 6,
                "expected": BASE[PATCH_OFFSET + 6 : PATCH_OFFSET + 8].hex(),
                "replacement": "4e71",
            }
        )
    elif fault == "alignment":
        patch["offset"] += 1
        patch["expected"] = BASE[PATCH_OFFSET + 1 : PATCH_OFFSET + 9].hex()
    else:
        target += 1
    with pytest.raises(BuildError):
        apply_patch(
            BASE, spec, [Segment(EXT_START, b"\x4e\x75")], {"arithmetic": target}
        )
