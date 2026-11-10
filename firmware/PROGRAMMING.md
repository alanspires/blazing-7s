# T48 / main-program EPROM qualification

This document covers putting the experimental firmware on EPROMs: which chips the
images are for, how they relate to the original dumps, the verified image hashes,
the readback record of the spares the owner programmed with an XGecu T48 programmer,
and a bench procedure for any future programming. It is for readers who want to
reproduce or check the chip images.

**Status: research and a future procedure, not authorization to program or install.**
The experimental images remain `install_ready: false`. Preserve the original
U28/U43 chips intact; do not erase them. Initial research was offline. The owner
subsequently programmed spares; full readbacks were compared as recorded below,
and limited machine checks are in [the live record](../notes/firmware-bringup.md).
Neither activity closes all physical qualification gates.

## Available spares: Winbond W27C02

The owner reports spare chips containing different firmware, labeled
“Main W/PRINTER”; the T48 detects `W27C02` and reports device ID `DA 85`.
The saved `U28_WPrinterSupport` / `U43_WPrinterSupport` dump filenames also
identify `W27C020@DIP32`. These are spares, not authorization to erase them.

Use the matching **WINBOND → W27C02** standard DIP entry, not ST M27C4001.
The mirrored T48 V13.16 catalogue includes both `W27C02` and `W27C020`;
PLCC/TSOP entries are separate. Winbond's
[W27C020 datasheet, Table of Operating Modes](https://www.alldatasheet.com/html-pdf/47650/WINBOND/W27C020/748/3/W27C020.html)
explicitly gives manufacturer `DA` and device `85`. Its **General Description**
identifies 262,144 × 8 bits (256 KiB), 5 V reading and electrical chip erasure.
No UV eraser is needed for this family. Erase/program voltages differ from the
ST device; let the exact Winbond algorithm control them.

Each merged-image byte lane is exactly 256 KiB, so capacity is sufficient.
The builder now emits one exact 262,144-byte lane per Winbond spare, without
mirroring, padding or manual truncation. Old 512 KiB mirrored outputs are **not
files for these spares**; do not rely on a programmer silently truncating them.
The verified packaging and local-publication record is below.
Read and preserve each spare's existing complete firmware before any erase.
Actual speed suffix, authenticity and electrical/timing compatibility remain
unverified. Successful image/readback comparisons are recorded below. The ID does not distinguish every related voltage/package
variant; retain the full physical markings in the bench record.

## Original dump and build evidence

The original `LeaveInMain` dump names identify the selected device as
`M27C4001@DIP32`. Each dump is exactly 524,288 bytes, with two identical 262,144-byte
halves. Interleaving the first halves, U28 even/high byte and U43 odd/low byte,
reproduces the pinned main ROM exactly. The original full-dump SHA-256 values are:

- U28: `9ac36aeeb0a7bf5b82e53ba897059e8af62543ea5430581ee4590d4fb60f872a`.
- U43: `24d113de6821c1d4aa7425523ee20378f316e150c504b23f9b0a603a21edbe4b`.

Sources: `../roms/README.md`, the author's original chip dump files, and the
socket labels (observed on 2026-10-17, from photographs that are not distributed).
The photographs establish socket labels and package shape, but the stickers obscure
the EPROM manufacturer/speed markings. A programmer-selected name is not an
independent identification of the silicon or of replacement blanks.

The published Winbond bundle's two 262,144-byte chip files were checked against both
`manifest.json` and `main.bin`: each is precisely its complete CPU byte lane.
Use `U28_even.bin` for U28 (even/high byte) and `U43_odd.bin` for U43 (odd/low
byte), not the merged `main.bin`. This verifies file layout, not the installed
board's address decoding or electrical compatibility.

### Verified offline Winbond packaging

**An independent packaging review approved all six files in its scope. The exact
eight verified artifacts were published locally in `firmware/build/` (not committed;
a fresh build regenerates `main.bin` and the chip images with the hashes below);
copied bytes and all 20 inputs were rechecked. No programming or installation is
authorized.**

| File | Bytes | SHA-256 |
| --- | ---: | --- |
| `U28_even.bin` | 262144 | `5e45a82f45ad1d482a6989ecd89c0198bd1ce517c574beb7cbbb811ba04eff0c` |
| `U43_odd.bin` | 262144 | `7f6230d139033cabd60f464a15d4cb0e871770d7370769c67b069cc2635165d9` |

The merged 524,288-byte `main.bin` remains
`06bef89137d206e8113bbe096a06d30dcb45e960f764d9350b777033b83e7197`.
The packaging manifest is
`ef1728ca2cb342666e6b31ebee1be8fdc411322b36f7c509c67dfb296b5f7b01`.
Its format is `s6000-offline-build/2`: one common `programming_device` identifies
Winbond W27C020, alias W27C02, DIP32, capacity 262144, manufacturer ID 218 (DAh)
and device ID 133 (85h). `chips` maps each filename to its lane; there is no
repetition field or legacy mirrored-output option. This is the intended target
matching the owner's report, **not a physical identification or compatibility
measurement**. `install_ready` remains false.

All 53 builder tests passed, including exact full-lane reconstruction, emitted
file/metadata/hash consistency, invalid main-size rejection and reproducibility.
The packaging tests reject the old mirrored splitter. Two independent production
builds from different checkout/output directories reproduced all eight artifacts.
`main.bin`, `ext.elf`, `ext.map`, `ext.dis` and `ext.stack` are byte-identical to
the stack-qualified build. Both builds used the same toolchain installation; across
toolchain builds only `main.bin` and the chip images are expected to reproduce
(`toolchain.md`, **Outputs and reproducibility**), so the manifest hash above is
this record's, not one a fresh build must match. All 20 input hashes were checked;
only `build.py` changed. Native patches, symbols, compiler commands, checksum and original ROMs
are unchanged. Thus this packaging revision does not change the previously
reviewed runtime code or stack proof.

### Owner-programmed spare readback checks

The owner chose to proceed without additional chip-marking and Xgpro screenshots.
Do not treat that waiver as physical identification or algorithm qualification.
The owner operated the programmer directly; the readback files below are what it
produced.

- The U28 readback file: exactly 262,144 bytes; full equality to `U28_even.bin`,
  SHA-256 `5e45a82f45ad1d482a6989ecd89c0198bd1ce517c574beb7cbbb811ba04eff0c`.
  This readback passed first but was no longer present at the final check.
- The initial U43 readback file failed: it matched the old printer firmware's odd
  lane, not the intended image. Installation was paused; that pair failed the
  reconstructed-image checksum. No truncation or byte swapping was accepted.
- A fresh U43 readback file: exactly 262,144 bytes; full equality to
  `U43_odd.bin`, SHA-256
  `7f6230d139033cabd60f464a15d4cb0e871770d7370769c67b069cc2635165d9`.
- The final reconstruction used the passing fresh U43 readback and the published
  U28 reference matching the earlier readback hash. It equaled `main.bin` exactly
  and had boot word sum zero. This was not a second read of the physical U28 chip.

No further flashing was needed on this evidence. The owner then reported installing
the pair and a normal idle display. Readback equality does not establish installed
address decoding, operating margins or complete machine behavior.

### Published T48 support

XGecu's [download page](http://www.xgecu.com/EN/download.html) lists Xgpro V13.16
for the T48. The [mirrored V13.16 device list](https://github.com/Kreeblah/XGecu_Software/blob/master/SupportLists/T48_List.txt)
has header date `17.3.2026`, 37,103 devices, and explicitly lists
`M27C4001 @DIP32` under both `[ ST ]` and `[ SGS-THOMSON ]`.
The complete downloaded list's SHA-256 is
`bfd9e2836334b5954197da18c7a82b0ef0cc41a428bce5279cbbd8294999edc1`.
The publisher's direct list could not be fetched; the list evidence is a mirror,
not a measurement or inspection of the owner's installed programmer algorithm.

This establishes advertised support. It does **not** establish the owner's Xgpro
version, selected profile, actual output voltages, device authenticity or successful
programming. The T48 settings from an earlier, unrelated motherboard flash-chip job
(a Winbond SPI profile, a SOIC adapter and that chip's voltage) are not applicable
to these parallel EPROMs.

### ST device requirements

The retained [ST M27C4001 datasheet](../vendor-docs/components/m27c4001-st.pdf), Rev 4,
provides these requirements:

- **Summary description / DIP Connections:** 524,288 × 8 bits (512 KiB total),
  32-pin DIP for the socketed variant. Select `M27C4001`, not `M27C4002` or a
  smaller part.
- **Programming / PRESTO II:** VPP `12.75 V ± 0.25 V`, VCC `6.25 V ± 0.25 V`,
  nominal 100 µs programming pulses and verify. These are programmer-mode values,
  not voltages to apply to the board. Use the exact manufacturer's device profile;
  do not override voltages or select a different part to force a failed write.
- **Read Mode / DC characteristics:** nominal 5 V operation. Confirm the exact
  suffix's supply tolerance and speed against the board's requirements.
- **Electronic Signature:** ST manufacturer `20h`, device `41h`. Signature mode
  itself uses elevated voltage on A9; use it only through the matching programmer
  profile, out of circuit. Do not disable ID checks to bypass an unexplained mismatch.
- **Summary description / Part numbering:** `F` is windowed UV-erasable FDIP32W;
  `B` is plastic OTP PDIP32. Both are distinct from PLCC/TSOP packages. A T48 does
  not electrically erase an ST UV EPROM; OTP cannot be erased for reuse.

This ST reference explains the device named by the original dump files. It is
**not the profile to use for the owner's Winbond spares**. Windowed ST `F` parts
would need a proper enclosed UV eraser for reuse; blank OTP `B` parts are one-use.
Neither a physical ST part nor its speed/authenticity has been qualified here.
Device identification, not a firmware sticker or old dump filename, determines
the programmer algorithm.

## Future bench procedure

Only after separate approval and identification of the actual spare chips:

1. Record each blank's complete markings, package and pin-1 orientation, Xgpro
   version and exact device selection. Confirm its datasheet, board voltage/timing
   requirements, and the software's socket-placement diagram. Do not reuse the
   motherboard job's SOIC adapter or infer ZIF placement from its center.
2. Keep originals separately labeled U28/U43. Program spares out of circuit;
   do not use an in-circuit clip or move board jumpers for this procedure.
3. Preserve fresh full-device reads of the Winbond spares before a separately
   approved electrical erase, then require a successful blank check. This does
   not authorize erasing the original leave-in main chips. A different UV part
   needs a proper UV erasure procedure; OTP cannot be made blank for reuse.
   Stop on unexplained ID, pin-contact, blank-check or voltage errors.
4. Load the matching full chip image at offset zero, binary/raw, without byte
   swapping, partial-range programming or padding. Record its manifest SHA-256.
   Run the matching device algorithm and full verify; a successful write alone
   does not count.
5. After ending the programming operation, obtain a fresh full-chip read into a
   new raw file. For the W27C02 spares require exactly 262,144 bytes and
   byte-for-byte equality to the correctly packaged lane image, independently
   of the application's verify message. A separately selected 4-Mbit ST part
   instead requires its full 524,288-byte image/readback.
   This builder targets only the Winbond spares; it does not emit a 512 KiB ST
   programming image. Do not pad or repeat these files for a different device.
   Record both image and readback SHA-256, socket label, chip marking and result.
6. Label the verified spares, and cover a UV window with an opaque label. Do not
   install them while firmware/board qualification remains incomplete.

The existing slot-chip dumps contain **no export trailer**. A different-length
readback is not accepted or silently truncated. Select a documented raw export
or first establish the export format; the 1,040-byte trailer encountered in the
motherboard job is not a general T48 rule.

## Remaining gates

- [x] T48 catalogue selections located; ST requirements retained; original/build
  image organization verified. Owner-reported W27C02/DA85 spares matched to the
  Winbond identification table and 256 KiB capacity.
- [x] Prepare and verify exact 256 KiB images for the Winbond spares offline.
- [x] Independent packaging review and exact-artifact local publication; no hardware approval.
- [ ] Actual spare part markings, suffix, authenticity and board timing compatibility.
- [x] Owner-provided full-chip readbacks independently compared with both intended
  256 KiB lane images; initial U43 mismatch resolved as recorded above.
- [ ] Installed Xgpro/device algorithm, placement, blank-check and physical part
  identity independently verified; the owner waived the additional screenshot check.
- [ ] Remaining firmware, SRAM, power-fail, watchdog, interrupt and machine-behavior
  gates in [PROVISIONING.md](PROVISIONING.md); limited bring-up is not closure.

Advertised support and successful readbacks are documented. They are not a general
installation qualification or permission for additional programming.
