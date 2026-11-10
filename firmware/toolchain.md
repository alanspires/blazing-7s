# Offline 68000 build and CPU-test toolchain

This builds **experimental offline artifacts**, never installation-ready images.
It neither connects to hardware nor changes an original ROM. CPU execution cannot
qualify unused ROM/RAM, interrupts, scheduling, SafeRAM recovery, optical sensors,
motor behavior, identity compatibility, or actual machine operation.

## Native tools and isolated Python

The verified native installation is Homebrew `m68k-elf-gcc` 16.2.0 and
`m68k-elf-binutils` 2.47.20260726, with [`uv`](https://docs.astral.sh/uv/) creating
the Python environment. Install once (network required):

```sh
brew install m68k-elf-gcc m68k-elf-binutils uv
uv venv --python 3.12 firmware/.venv
uv pip install --python firmware/.venv/bin/python --require-hashes -r firmware/requirements.txt
```

After dependencies are installed, compilation, packaging and tests are offline.
No target OS, libc, startup files, original compiler, or remote executor is
required. The dev container (`.devcontainer/`, see `GETTING-STARTED.md`) carries the
same pinned compiler and binutils bottles and Python dependencies, as an alternative
to installing them natively. `requirements.txt` pins all Python dependencies and package hashes;
`requirements.in` records direct dependencies and the lock-generation command.
The builder itself uses only the Python standard library.

```sh
firmware/.venv/bin/python firmware/build.py firmware/patches.json
firmware/.venv/bin/python -m pytest firmware/tests/test_build.py
```

Compilation uses `-m68000 -ffreestanding`, explicit no-runtime link flags and
warnings as errors. Soft float is already the default for this CPU. Explicit
`-msoft-float` incorrectly selects the default (68020) multilib in GCC 16.2;
compile, link and library queries therefore share only the `-m68000` target flag,
and the build requires the selected multilib directory to be `m68000`.
The **68000 multilib** static `libgcc.a` supplies
compiler arithmetic helpers; unresolved references fail. It is not a target
runtime dependency. The build records its hash and tool versions. No initialized
C runtime is synthesized: BSS and ROM-to-RAM relocation segments are rejected.
State must use explicitly allocated linker symbols and explicit initialization.

## Explicit input contract

`build(patch_file, out_dir=None)` returns `BuildResult(image, symbols, manifest,
directory)`. The default output is `build/` beside the patch file. Paths are
relative to that patch file. Its directory contains `link.ld` and `src/*.c` /
`src/*.S`; `.h` files under `src/` are copied and hashed too.

```json
{
  "base": {
    "path": "../roms/s6000_main_S6M00002B5.bin",
    "sha256": "49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a"
  },
  "extension": {"start": "0x79100", "end": "0x80000", "expect_fill": "00"},
  "patches": [
    {"name": "example", "offset": "0x1234", "expected": "001122334455", "symbol": "example_entry", "kind": "jsr"}
  ]
}
```

**The example addresses are not qualified placements or a usable patch.** Choosing
a region remains the patch author's explicit responsibility. In particular,
`0x79042..0x7ffff` being zero-filled does not prove that it is unused.

Every original extension byte must match the one-byte `expect_fill`. Extension
start/end must be even; end is exclusive. The last word is reserved for checksum
compensation. The builder passes `EXT_ROM_START` and `EXT_ROM_END` to the linker;
the latter is the exclusive **code** end, two bytes before the extension end.
ELF load segments must fit the declared code region, with matching physical and
virtual addresses and no overlaps. Specify `OUTPUT_ARCH(m68k)` and an explicit
`ENTRY` in `link.ld`; link warnings are errors.

Each patch has unique `name`, `offset`, exact original `expected` hex bytes, and:

- `replacement`: exactly the same number of hex bytes; or
- `symbol` plus `kind`: `jsr` → `4eb9` + BE32 target, `jmp` → `4ef9` + BE32 target,
  `pointer` → BE32 symbol address; `jsr-nop` → the six-byte call followed by NOPs
  over the explicitly verified whole region.

Ordinary symbolic branches require six expected bytes and an even loaded
destination; pointers require four. `jsr-nop` requires an even-sized region of
at least eight bytes. Patch ranges may not overlap each other or the extension.
Unknown schema fields fail rather than silently ignoring an expected-byte safeguard.

## Outputs and reproducibility

- `ext.elf`, `ext.map`, `ext.dis`: executable, linker map and 68000 disassembly.
- `ext.stack`: deterministic GCC `-fstack-usage` reports for C functions, with
  source location, byte count and static/dynamic qualifiers. These are not total
  call-chain stack bounds: assembly, libgcc, original native callees and interrupt
  frames need separate accounting. The conditional native 3 KiB stack proof is
  recorded in `TASK-STACK-AUDIT.md`; compiler reports alone do not establish it.
- `main.bin`: exactly 512 KiB; original SHA-256 and original-byte expectations are
  checked before compiling. The originals are read-only inputs.
- `U28_even.bin`, `U43_odd.bin`: CPU even bytes → U28 and odd bytes → U43. Each
  file is exactly 256 KiB, without repetition or padding, for the reported Winbond
  W27C020/W27C02 DIP32 spares. There is no legacy 512 KiB chip-output mode.
- `manifest.json`: deterministic input hashes (ROM, patch specification, linker,
  sources, headers, builder and libgcc), versions/commands, symbol addresses,
  all actual patch ranges with original/replacement bytes, checksum ranges and
  compensation address, output hashes/sizes and chip byte sums. Format
  `s6000-offline-build/2` has one `programming_device` record for the fixed Winbond
  target (part/alias, DIP32, capacity, manufacturerDA/device85) and filename-to-lane
  `chips` records without `repetitions`. These are target specifications, not
  verification of physical parts. `PROGRAMMING.md` records the offline packaging gate.

Boot checksum is the 16-bit big-endian **word sum**, not the chip byte sum, over
`[0, 0x7f0)` and `[0x800, 0x80000)`. The builder compensates at the allocated
extension's last word, leaving original `0x7ee` untouched unless explicitly
patched. The boot checksum is retained, not disabled.

No timestamps, checkout paths or output-directory paths enter build products.
Sources are compiled with stable relative names in a private temporary directory;
`SOURCE_DATE_EPOCH=0`, C locale and UTC are fixed. Byte-for-byte reproducibility is
tested across different input/output directories using identical inputs and the
same toolchain. `main.bin` and the two chip images reproduce with gcc 16.2.0 and
binutils 2.47 from the Homebrew bottles on macOS and on Linux (the dev container,
arm64 and amd64). `ext.elf`, `ext.map` and `manifest.json` differ between those
toolchain builds even at the same versions: the bottles carry different `libgcc.a`
builds, whose hash the manifest records and whose linked layout shows in the ELF, the
map and the manifest's symbol table. `ext.dis` and `ext.stack` matched. Different compiler/binutils
versions are not promised identical output; manifests identify those versions. Hashes establish provenance, not
hardware safety. Every manifest sets `install_ready: false`.

## CPU executor

`firmware.tests.cpu.Cpu` uses Unicorn's real M68K execution engine with model
`UC_CPU_M68K_M68000`. `call(address, *u32_args)` supplies GCC stack arguments,
returns D0 and verifies the return stack. `run(start, until, max_insns=...)`
executes until the exact address or fails on the instruction budget. SR is set to
supervisor mode **before** initializing SP. The test-only stack at `0xe00000` and
return sentinel at `0xf00000` are not asserted machine allocations.

Main ROM is mapped at zero, optional personality at `0x100000`, default RAM at
`0x400000..0x43ffff`. ROM is read/execute only; RAM is read/write only. I/O is
unmapped until a test explicitly calls `map_io(page_address, size)`; that creates
plain test memory, not a DUART or motor model. The `.uc` property permits explicit
register/hook setup by tests; never use it to change executable image bytes.
Construct a fresh `Cpu` when image bytes change. Use `Cpu.read_sr()` when stopped
to observe effective condition codes: Unicorn's direct `reg_read(SR)` returns stale
lazy flags and can change subsequent branches. The helper executes a real MOVE
from SR in test-only memory and restores the observation scratch and PC.

Observed Unicorn limitations: selecting M68000 still permits some later-CPU
instructions and odd-address word reads. Therefore the harness checks executed
opcodes with native `objdump -m m68k:68000` and rejects unrecognized/illegal code,
adds word/long alignment guards, and fails on all unhandled CPU exceptions and
unmapped/protected memory accesses. It never replaces firmware behavior with a
Python interpreter. Tests exercise compiled arithmetic (including 68000 libgcc),
buffer loops, branches, memory effects and assembly register preservation, plus
unsupported opcodes, traps, unmapped I/O and bounded nontermination. These guards
are not an independently certified 68000 implementation; untested instruction,
interrupt and timing semantics remain emulator limitations.
