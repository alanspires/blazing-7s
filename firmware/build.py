#!/usr/bin/env python3
"""Deterministic, offline-only S6000 68000 patch compilation and image packaging."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAIN_SIZE = 512 * 1024
# The 16-bit bus splits main.bin into one byte lane per EPROM.
CHIP_SIZE = MAIN_SIZE // 2
# Matches the user's reported spares; physical parts are not verified here.
PROGRAMMING_DEVICE = {
    "manufacturer": "Winbond",
    "part": "W27C020",
    "aliases": ["W27C02"],
    "package": "DIP32",
    "capacity": CHIP_SIZE,
    "manufacturer_id": 0xDA,
    "device_id": 0x85,
}
MAIN_SHA256 = "49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a"
CHECKSUM_RANGES = ((0, 0x7F0), (0x800, MAIN_SIZE))  # Exclusive ends.
# 68000 already defaults to soft float. Explicit -msoft-float incorrectly selects
# the default (68020) libgcc multilib in the installed GCC 16.2 distribution.
TARGET_FLAGS = ("-m68000",)
CFLAGS = (
    *TARGET_FLAGS,
    "-Os",
    "-ffreestanding",
    "-fno-builtin",
    "-fno-pic",
    "-fno-pie",
    "-fno-common",
    "-fno-stack-protector",
    "-fno-unwind-tables",
    "-fno-asynchronous-unwind-tables",
    "-ffunction-sections",
    "-fdata-sections",
    "-fno-ident",
    "-fno-tree-loop-distribute-patterns",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-Wdate-time",
    "-ffile-prefix-map=.=.",
    "-fstack-usage",
)
LDFLAGS = (
    *TARGET_FLAGS,
    "-nostdlib",
    "-nostartfiles",
    "-nodefaultlibs",
    "-Wl,--build-id=none,--fatal-warnings,--no-undefined",
    # libgcc assembly lacks GNU-stack notes; no generated code executes on stack.
    "-Wl,-z,noexecstack",
)


class BuildError(ValueError):
    pass


@dataclass(frozen=True)
class Segment:
    address: int
    data: bytes


@dataclass(frozen=True)
class BuildResult:
    image: bytes
    symbols: dict[str, int]
    manifest: dict[str, Any]
    directory: Path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def number(value: int | str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise BuildError("Addresses must be integers or hexadecimal strings")
    try:
        return int(value, 0) if isinstance(value, str) else value
    except ValueError as exc:
        raise BuildError("Invalid integer") from exc


def hex_bytes(value: str) -> bytes:
    if not isinstance(value, str):
        raise BuildError("Expected a hex string")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise BuildError("Invalid hexadecimal bytes") from exc


def keys(
    obj: dict, required: set[str], optional: set[str] | frozenset[str] = frozenset()
) -> None:
    if (
        not isinstance(obj, dict)
        or set(obj) - required - optional
        or required - set(obj)
    ):
        raise BuildError(
            f"Invalid fields; required={sorted(required)}, optional={sorted(optional)}"
        )


def checksum(image: bytes) -> int:
    if len(image) != MAIN_SIZE:
        raise BuildError("Main image must be exactly 512 KiB")
    return (
        sum(
            word[0]
            for start, end in CHECKSUM_RANGES
            for word in struct.iter_unpack(">H", image[start:end])
        )
        & 0xFFFF
    )


def split_chips(image: bytes) -> dict[str, bytes]:
    if len(image) != MAIN_SIZE:
        raise BuildError("Main image must be exactly 512 KiB")
    return {"U28_even.bin": image[::2], "U43_odd.bin": image[1::2]}


def tool(name: str) -> str:
    found = shutil.which("m68k-elf-" + name)
    if not found:
        raise BuildError(f"Missing native m68k-elf-{name}; see firmware/toolchain.md")
    return found


def run(command: list[str], cwd: Path) -> str:
    env = {**os.environ, "LC_ALL": "C", "TZ": "UTC", "SOURCE_DATE_EPOCH": "0"}
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise BuildError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stdout}{result.stderr}"
        )
    if result.stderr:
        raise BuildError(
            f"Unexpected tool diagnostics: {' '.join(command)}\n{result.stderr}"
        )
    return result.stdout


def load_segments(elf: bytes) -> list[Segment]:
    if len(elf) < 52 or elf[:7] != b"\x7fELF\x01\x02\x01":
        raise BuildError("Expected a 32-bit big-endian ELF")
    header = struct.unpack_from(">HHIIIIIHHHHHH", elf, 16)
    kind, machine, version, _, phoff, _, flags, ehsize, phsize, phnum, _, _, _ = header
    if (kind, machine, version, ehsize) != (2, 4, 1, 52) or flags != 0x01000000:
        raise BuildError("Expected a linked EM_68K / M68000 ELF")
    if phsize != 32 or phoff + phsize * phnum > len(elf):
        raise BuildError("Invalid ELF program header table")
    segments = []
    for index in range(phnum):
        typ, offset, vaddr, paddr, size, memsize, _, _ = struct.unpack_from(
            ">IIIIIIII", elf, phoff + index * phsize
        )
        if typ != 1:
            continue
        if size > memsize or offset + size > len(elf):
            raise BuildError("Invalid ELF load segment")
        if not size:
            if memsize:
                raise BuildError(
                    "Implicit BSS initialization is unsupported; use explicit RAM symbols"
                )
            continue
        if vaddr != paddr or memsize != size:
            raise BuildError(
                "RAM relocation/BSS requires an explicit startup implementation"
            )
        segments.append(Segment(paddr, elf[offset : offset + size]))
    if not segments:
        raise BuildError("ELF contains no loadable bytes")
    return segments


def validate_spec(spec: dict, base: bytes) -> tuple[int, int]:
    keys(spec, {"base", "extension", "patches"})
    keys(spec["base"], {"path", "sha256"})
    if spec["base"]["sha256"] != MAIN_SHA256 or sha256(base) != MAIN_SHA256:
        raise BuildError("Base ROM SHA-256 does not match the owned S6M00002B5 image")
    if len(base) != MAIN_SIZE or checksum(base):
        raise BuildError("Invalid base ROM size/checksum")
    ext = spec["extension"]
    keys(ext, {"start", "end", "expect_fill"})
    start, end = number(ext["start"]), number(ext["end"])
    if start % 2 or end % 2 or not 0 <= start < end - 2 <= MAIN_SIZE - 2:
        raise BuildError(
            "Extension must be even-aligned and contain code plus a checksum word"
        )
    if not any(a <= end - 2 and end <= b for a, b in CHECKSUM_RANGES):
        raise BuildError("Compensation word must be in a boot-checksummed range")
    fill = hex_bytes(ext["expect_fill"])
    if len(fill) != 1 or base[start:end] != fill * (end - start):
        raise BuildError("Extension does not match expected original fill bytes")
    if not isinstance(spec["patches"], list):
        raise BuildError("patches must be a list")
    occupied = [(start, end)]
    names = set()
    for patch in spec["patches"]:
        keys(patch, {"name", "offset", "expected"}, {"replacement", "symbol", "kind"})
        if (
            not isinstance(patch["name"], str)
            or not patch["name"]
            or patch["name"] in names
        ):
            raise BuildError("Patch names must be unique nonempty strings")
        names.add(patch["name"])
        if set(patch) - {"name", "offset", "expected"} not in (
            {"replacement"},
            {"symbol", "kind"},
        ):
            raise BuildError("Specify replacement OR symbol+kind")
        expected = hex_bytes(patch["expected"])
        offset = number(patch["offset"])
        limit = offset + len(expected)
        if not expected or not 0 <= offset < limit <= MAIN_SIZE:
            raise BuildError("Patch is empty or out of bounds")
        if any(offset < b and a < limit for a, b in occupied):
            raise BuildError("Overlapping patch ranges")
        occupied.append((offset, limit))
        if base[offset:limit] != expected:
            raise BuildError(f"Original-byte mismatch for {patch['name']}")
        if "replacement" in patch:
            if len(hex_bytes(patch["replacement"])) != len(expected):
                raise BuildError("Replacement length differs from expected bytes")
        elif patch["kind"] not in {"jsr", "jsr-nop", "jmp", "pointer"}:
            raise BuildError("Unknown symbolic patch kind")
        elif offset % 2 or (
            (len(expected) < 8 or len(expected) % 2)
            if patch["kind"] == "jsr-nop"
            else len(expected) != (4 if patch["kind"] == "pointer" else 6)
        ):
            raise BuildError("Symbolic patch has incorrect size/alignment")
    return start, end


def apply_patch(
    base: bytes, spec: dict, segments: list[Segment], symbols: dict[str, int]
) -> tuple[bytes, list[dict]]:
    start, end = validate_spec(spec, base)
    image = bytearray(base)
    ranges = []

    def put(name: str, offset: int, data: bytes) -> None:
        ranges.append(
            {
                "name": name,
                "offset": offset,
                "length": len(data),
                "expected": base[offset : offset + len(data)].hex(),
                "replacement": data.hex(),
            }
        )
        image[offset : offset + len(data)] = data

    previous_end = start
    for index, segment in enumerate(sorted(segments, key=lambda item: item.address)):
        limit = segment.address + len(segment.data)
        if not segment.data or not previous_end <= segment.address < limit <= end - 2:
            raise BuildError(
                "ELF bytes overlap or exceed the explicitly declared extension"
            )
        put(f"extension-{index}", segment.address, segment.data)
        previous_end = limit
    if not segments:
        raise BuildError("No firmware code to package")
    for patch in spec["patches"]:
        if "replacement" in patch:
            replacement = hex_bytes(patch["replacement"])
        else:
            target = symbols.get(patch["symbol"])
            if target is None or not 0 <= target < 0x1000000:
                raise BuildError(f"Unknown/out-of-bus-range symbol: {patch['symbol']}")
            kind = patch["kind"]
            if kind != "pointer" and (
                target % 2
                or not any(
                    s.address <= target < s.address + len(s.data) for s in segments
                )
            ):
                raise BuildError(
                    "Branch destination must be loaded, even-aligned extension code"
                )
            opcode = {
                "pointer": b"",
                "jsr": b"\x4e\xb9",
                "jsr-nop": b"\x4e\xb9",
                "jmp": b"\x4e\xf9",
            }[kind]
            replacement = opcode + target.to_bytes(4, "big")
            if kind == "jsr-nop":
                replacement += b"\x4e\x71" * (
                    (len(hex_bytes(patch["expected"])) - 6) // 2
                )
        put(patch["name"], number(patch["offset"]), replacement)
    image[end - 2 : end] = b"\x00\x00"
    put(
        "checksum-compensation",
        end - 2,
        ((-checksum(image)) & 0xFFFF).to_bytes(2, "big"),
    )
    if checksum(image):
        raise BuildError("Internal checksum compensation failure")
    return bytes(image), sorted(ranges, key=lambda item: item["offset"])


def compile_elf(
    root: Path, work: Path, start: int, end: int
) -> tuple[dict, dict, list[str]]:
    sources = sorted([*root.glob("src/*.c"), *root.glob("src/*.S")])
    if not sources or not (root / "link.ld").is_file():
        raise BuildError(
            "Expected link.ld and src/*.c and/or src/*.S beside the patch manifest"
        )
    gcc, nm, objdump, assembler, linker = (
        tool(n) for n in ("gcc", "nm", "objdump", "as", "ld")
    )
    libgcc_path = Path(
        run([gcc, *TARGET_FLAGS, "-print-libgcc-file-name"], root).strip()
    )
    if (
        not libgcc_path.is_file()
        or run([gcc, *TARGET_FLAGS, "-print-multi-directory"], root).strip() != "m68000"
    ):
        raise BuildError("Compiler did not select the M68000 libgcc multilib")
    versions = {
        name: run([binary, "--version"], root).splitlines()[0]
        for name, binary in (
            ("gcc", gcc),
            ("as", assembler),
            ("ld", linker),
            ("nm", nm),
            ("objdump", objdump),
        )
    }
    inputs = {
        "link.ld": sha256((root / "link.ld").read_bytes()),
        "libgcc.a": sha256(libgcc_path.read_bytes()),
    }
    commands = []
    objects = []
    # Relative names and a private stable layout keep checkout/output paths out of artifacts.
    shutil.copyfile(root / "link.ld", work / "link.ld")
    for header in sorted((root / "src").rglob("*.h")):
        relative = header.relative_to(root)
        (work / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(header, work / relative)
        inputs[relative.as_posix()] = sha256(header.read_bytes())
    for index, source in enumerate(sources):
        relative = source.relative_to(root)
        (work / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, work / relative)
        inputs[relative.as_posix()] = sha256(source.read_bytes())
        obj = f"obj/{index:03}.o"
        (work / "obj").mkdir(exist_ok=True)
        command = [gcc, *CFLAGS, "-Isrc", "-c", relative.as_posix(), "-o", obj]
        run(command, work)
        commands.append(["m68k-elf-gcc", *command[1:]])
        objects.append(obj)
    shutil.copyfile(libgcc_path, work / "libgcc.a")
    command = [
        gcc,
        *LDFLAGS,
        f"-Wl,--defsym=EXT_ROM_START={start},--defsym=EXT_ROM_END={end - 2}",
        "-Wl,-T,link.ld,-Map,ext.map",
        *objects,
        "libgcc.a",
        "-o",
        "ext.elf",
    ]
    run(command, work)
    commands.append(["m68k-elf-gcc", *command[1:]])
    undefined = run([nm, "-u", "ext.elf"], work).strip()
    if undefined:
        raise BuildError(f"Unresolved runtime dependencies: {undefined}")
    symbols = {}
    for line in run(
        [nm, "--defined-only", "--format=posix", "ext.elf"], work
    ).splitlines():
        name, _, address, *_ = line.split()
        symbols[name] = int(address, 16)
    disassembly = run([objdump, "-d", "-m", "m68k:68000", "ext.elf"], work)
    (work / "ext.dis").write_text(disassembly)
    (work / "ext.stack").write_text(
        "".join(path.read_text() for path in sorted((work / "obj").glob("*.su")))
    )
    return (
        symbols,
        {"versions": versions, "commands": commands, "inputs": inputs},
        objects,
    )


def build(patch_file: Path | str, out_dir: Path | str | None = None) -> BuildResult:
    patch_file = Path(patch_file).resolve()
    root = patch_file.parent
    out = Path(out_dir).resolve() if out_dir is not None else root / "build"
    spec_bytes = patch_file.read_bytes()
    spec = json.loads(spec_bytes)
    keys(spec, {"base", "extension", "patches"})
    keys(spec["base"], {"path", "sha256"})
    base_path = (root / spec["base"]["path"]).resolve()
    base = base_path.read_bytes()
    start, end = validate_spec(spec, base)
    if out == root or out == root / "src" or out == base_path.parent:
        raise BuildError("Output directory must be separate from inputs")
    output_names = {
        "ext.elf",
        "ext.map",
        "ext.dis",
        "ext.stack",
        "main.bin",
        "U28_even.bin",
        "U43_odd.bin",
        "manifest.json",
    }
    if any(
        (out / name).resolve() in {base_path, patch_file, root / "link.ld"}
        for name in output_names
    ):
        raise BuildError("Output would overwrite an input")
    with tempfile.TemporaryDirectory(prefix="s6000-build-") as temporary:
        work = Path(temporary)
        symbols, compilation, _ = compile_elf(root, work, start, end)
        segments = load_segments((work / "ext.elf").read_bytes())
        image, patches = apply_patch(base, spec, segments, symbols)
        outputs = {"main.bin": image, **split_chips(image)}
        outputs.update(
            {
                name: (work / name).read_bytes()
                for name in ("ext.elf", "ext.map", "ext.dis", "ext.stack")
            }
        )
        manifest = {
            "format": "s6000-offline-build/2",
            "install_ready": False,
            "qualification": "Offline CPU tests cannot establish ROM/RAM placement safety, motor/sensor behavior or hardware readiness.",
            "base": {"sha256": sha256(base), "size": len(base)},
            "inputs": {
                patch_file.name: sha256(spec_bytes),
                **compilation.pop("inputs"),
                "build.py": sha256(Path(__file__).read_bytes()),
            },
            "toolchain": compilation,
            "stack_usage": {
                "report": "ext.stack",
                "scope": "Compiler per-function usage only; excludes callees, assembly, native code and interrupts.",
            },
            "extension": {
                "start": start,
                "end": end,
                "expect_fill": spec["extension"]["expect_fill"],
            },
            "patches": patches,
            "symbols": dict(sorted(symbols.items())),
            "checksum": {
                "ranges_end_exclusive": CHECKSUM_RANGES,
                "word": end - 2,
                "sum": checksum(image),
            },
            "programming_device": PROGRAMMING_DEVICE,
            "chips": {
                "U28_even.bin": {"lane": "even"},
                "U43_odd.bin": {"lane": "odd"},
            },
            "outputs": {
                name: {
                    "sha256": sha256(data),
                    "size": len(data),
                    **(
                        {"byte_sum16": sum(data) & 0xFFFF}
                        if name.endswith(".bin")
                        else {}
                    ),
                }
                for name, data in sorted(outputs.items())
            },
        }
        outputs["manifest.json"] = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode()
        out.mkdir(parents=True, exist_ok=True)
        # Never follow an existing output symlink (especially one pointing at an original ROM).
        for name, data in outputs.items():
            destination = out / name
            if destination.is_symlink():
                raise BuildError(f"Refusing output symlink: {destination}")
            with tempfile.NamedTemporaryFile(dir=out, delete=False) as pending:
                pending.write(data)
                pending_path = Path(pending.name)
            pending_path.replace(destination)
    return BuildResult(image, symbols, manifest, out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = build(args.manifest, args.out)
    print(
        json.dumps(
            {
                "directory": str(result.directory),
                "sha256": sha256(result.image),
                "install_ready": False,
            }
        )
    )


if __name__ == "__main__":
    main()
