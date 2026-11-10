---
paths:
  - "firmware/**"
  - "notes/firmware-*"
  - "notes/remote-game-config.md"
  - "notes/reel-control.md"
  - "tools/sas_extension.py"
  - "tools/sas_profile.py"
  - "tools/*commission*.py"
  - "tools/test_sas_extension.py"
  - "tools/test_sas_profile.py"
  - "tools/test_sas_firmware.py"
  - "tools/fixtures/s6000_profile_reply_timing.json"
---
# Experimental firmware

## Scope and qualification boundary

- Target only S6M00002B5 main plus E595211X-05 personality. Stock firmware cannot
  service the private extension commands. Every build retains `install_ready: false`;
  reproducibility, CPU tests and successful chip readback are not installation qualification.
- Slice-specific contracts live in `firmware-remote-control.md` (protocol, profiles,
  persistence and platform lifecycle), `firmware-game.md` (native gameplay),
  `firmware-motion.md` (diagnostic reels), `firmware-commissioning.md` (first install),
  and `firmware-native-guards.md` (stack, IRQ, settlement and serial-drain proofs).
- `notes/firmware-bringup.md` and its evidence distinguish limited live success from
  offline-era blockers: two stock games, idle-restart retention, stock-equivalent
  profile persistence and normal CLI upload/readback/restore were observed. They
  do not qualify changed-profile gameplay, other award classes or interrupted writes.
- Modified-firmware live use needs separate approval of the bounded procedure;
  prior spin/profile approvals do not authorize another game, transfer, SRAM clear,
  commissioning initialization, reflash, fault injection or power interruption.
  Missing measurement equipment leaves electrical/timing gates unverified, not waived.
  Do not infer installed image identity from an E0 version or SAS signature alone.
- The reply-deadline correction was host-only and required no reflash. Historical
  probe text is evidence using an obsolete host interface, not a reusable live tool.
  Consult the live record for last-observed state; never use recorded IDs/meters as
  a fresh preflight. Standalone positioning/jogging remain disabled.

## Patch and link contract

- `build.py:validate_spec` pins the base hash, size and boot checksum before compile.
  Preserve exact original-byte expectations, unique patch names, equal replacement
  lengths and nonoverlap with other patches and the extension. Unknown fields fail.
- `patches.json` reserves `[0x79100, 0x80000)` with verified zero fill. Fill does not
  prove placement safety. The final word is compensation, not code; linker
  `EXT_ROM_END` is the exclusive code end, two bytes before the declared end.
- Symbolic JSR/JMP patches cover six bytes, pointers four; `jsr-nop` covers an even
  region of at least eight bytes. Verify complete displaced instructions and every
  continuation, not just encoding length. Branch targets must be even loaded code.
- `link.ld` owns explicit absolute RAM reservations. No startup runtime initializes
  globals: `.data` and `.bss` must stay empty; explicitly initialize reserved state.
  ELF load segments must not overlap, must fit the code region, and must have equal
  physical/virtual addresses and file/memory sizes (no implicit BSS or RAM relocation).
- Retain the boot checksum: 16-bit big-endian word sum over `[0, 0x7f0)` and
  `[0x800, 0x80000)`, compensated at the extension's last word. Chip byte sums are
  different. Original `0x7ee` stays untouched unless explicitly patched.
- Compare artifacts across different input/output directories with identical inputs
  and toolchain; manifests track provenance, not safety. Across toolchain builds of the
  same gcc/binutils versions (macOS and the dev container's Linux bottles) only
  `main.bin` and the chip images must match the published hash; `ext.elf`, `ext.map`
  and `manifest.json` follow the bottle's `libgcc.a`. Compiler-version changes
  need new evidence. Per-function `ext.stack` is not a call-chain bound: include
  assembly, libgcc, native callees and interrupt frames in the owning guard proof.

## Hook adapters and toolchain traps

- For each `*_hooks.S` change, establish entry/exit SP, return/continuation address,
  displaced instructions, register widths and CCR/SR behavior at the native site.
  A tail jump and a JSR do not have interchangeable stack shapes. Compare permitted
  paths against native execution, including flags and stack, not merely C results.
- GCC C calls may clobber D0/D1/A0/A1 and CCR; D2–D7/A2–A6 are callee-saved.
  Mid-instruction-stream adapters must preserve additional live registers/flags or
  reproduce displaced effects. Preserve X when required; restore full SR only where
  the contract requires it. Test `bool`/`uint8_t` results in D0's low byte only.
- Use native `m68k-elf-*` tools and `-m68000 -ffreestanding` without target libc or
  startup files. Do not add `-msoft-float`: in the documented GCC distribution it
  selects the wrong multilib. Compile, link and libgcc queries share target flags;
  require the `m68000` multilib. Unresolved references and tool diagnostics fail.

## CPU-test obligations

- Run real compiled code through `firmware.tests.cpu.Cpu`, not a Python rewrite of
  firmware behavior. Run native ROM routines where practical; explicit UART, RTOS,
  physical-input and accounting-leaf fakes are boundary fixtures, not machine proof.
  Use real kernel fixtures when testing kernel behavior (see native-guards rule).
- `map_io` supplies plain memory, not peripheral semantics. ROM is immutable RX,
  RAM RW; construct a fresh `Cpu` for changed executable bytes, including variants.
  Never bypass immutability with `.uc.mem_write`. Test stack/sentinel addresses are
  harness allocations, not evidence of available machine RAM.
- Set supervisor SR before SP. Observe stopped flags with `Cpu.read_sr()`, never
  direct Unicorn SR reads: lazy flags can be stale and reading them can change
  later branches. Preserve exact stop boundaries even through cached return blocks.
- Keep strict objdump M68000 decoding, alignment guards, exception/access failures
  and bounded execution. Unicorn's CPU selector alone permits later opcodes and odd
  word accesses. These guards still do not certify IRQ, scheduling or timing fidelity.
- Derive ABI limits from production headers via `firmware.tests.support` helpers;
  stage variants through the production builder/linker rather than duplicating them.
  Word-atomic power-cut fixtures assert a premise, not physical SRAM qualification.

## EPROM packaging and bench boundary

- The builder targets reported Winbond W27C020/W27C02 DIP32 spares: exactly 256 KiB
  per lane, no padding, repetition or truncation. Program `U28_even.bin`/`U43_odd.bin`,
  never merged `main.bin`. The old mirrored 512 KiB outputs are not spare-chip files.
- Follow `firmware/PROGRAMMING.md` only under separate approval: preserve originals
  and full spare backups; identify physical parts and use the matching Winbond
  algorithm, not the ST M27C4001 profile inferred from old dump names. DA/85 and
  successful readback do not establish suffix, authenticity or board timing margins.
- Require fresh full-device raw readback equality to the intended lane, then complete
  interleaving equality and boot checksum. Do not accept partial verification, byte
  swapping, silent truncation or an assumed export trailer; unexplained ID/write/
  length mismatches stop the procedure. Prior readbacks do not authorize more flashing.

## Verification commands

Run from the repository root. Use Python 3.12 in `firmware/.venv`, with the
hash-locked `firmware/requirements.txt` (direct inputs in `requirements.in`); the dev
container (`.devcontainer/`) carries the same toolchain and runs these commands with
`python` in place of `firmware/.venv/bin/python`.
Select affected tests rather than treating a historical test count as a gate:

```sh
firmware/.venv/bin/python firmware/build.py firmware/patches.json
firmware/.venv/bin/python -m pytest firmware/tests/test_build.py firmware/tests/test_cpu_status.py
firmware/.venv/bin/python -m pytest -v firmware/tests tools/test_sas_firmware.py tools/test_sas_commission_firmware.py
ruff check firmware tools/sas_profile.py tools/test_sas_profile.py tools/sas_extension.py tools/test_sas_extension.py tools/test_sas_firmware.py tools/sas.py
ruff check tools/sas_commission.py tools/test_sas_commission.py tools/test_sas_commission_wrapper.py tools/test_sas_commission_firmware.py
```
