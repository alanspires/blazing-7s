# ROM images

The program images of this machine's chips, merged from the author's dumps, with the
evidence for which game is installed. Tools, tests and the firmware build read them as
inputs and test oracles; they are never modified. They are Bally Gaming's copyrighted
programs (the three main programs also embed the VRTX/68000 Rev. 3.12 kernel,
copyright Hunter & Ready), included for reference and as test inputs, and not covered
by this repository's MIT license.

The 68000 bus is 16 bits wide, so each image interleaves two chips: the even chip
supplies the high byte at even addresses. Even/odd order was checked against content:
the main program's vector table (SSP `0x437FFE`, reset `0x800`) and readable strings in
the personality ROMs. The main program runs the machine; the personality ROM holds the
game: its identity strings, payout records and reel stop weights
(`notes/remote-game-config.md`).

| Image | Even chip | Odd chip | Contents |
|---|---|---|---|
| `s6000_main_S6M00002B5.bin` | U28 LeaveInMain | U43 LeaveInMain | main program `S6M00002B5`, ©2004; the 27C4001 dumps hold the 512 KB program twice, one copy kept |
| `s6000_clear_S6M0000CLR07.bin` | U28 Clear | U43 Clear | SafeRAM clear program `S6M0000CLR07`, ©2000 |
| `s6000_printer_S6M0000C00.bin` | U28 WPrinterSupport | U43 WPrinterSupport | main program variant `S6M0000C0007-08`, ©2003; read from the spare "Main W/PRINTER" chips, which were later reprogrammed with the experimental firmware (`firmware/PROGRAMMING.md`) |
| `personality_E595211X-05.bin` | U20 | U18 | installed game: `PRSN:E595211X-05`, `S63U3X-BL7`, `03-04-93`, `90.85% / 89.45%` |
| `personality_E725711X-05.bin` | U20 | U18 | another Blazing 7s: `PRSN:E725711X-05`, `06-09-97`, `89.37% / 83.90%` |

Their SHA-256 hashes (the firmware build pins the main program's, and
`tools/sas_profile.py` the installed personality's):

| Image | SHA-256 |
|---|---|
| `s6000_main_S6M00002B5.bin` | `49c7717171a34111005d60ad39ec70c9408681700a40fe9324207d571886b02a` |
| `s6000_clear_S6M0000CLR07.bin` | `67cbb357fe41f632bdf1106a4b6bc65b66644e2403031312bdcc3ade3d9870ea` |
| `s6000_printer_S6M0000C00.bin` | `c433bccc503b249f8d4f454f6563289479a3e1d4c00f6198d2e14c56caee2036` |
| `personality_E595211X-05.bin` | `7befde1d6ae296122ef2024f9d21e5e3d1c714a4c4d085aabfc6405a4176316f` |
| `personality_E725711X-05.bin` | `576565c7c791382de33cf557e71fd0e566a004be6f7cb4fa1fb8baae69981752` |

## Which game is installed

The installed chips' stickers read "E595211X-05 SMI7156". Bally's game lists in
`vendor-docs/bally-s6000/` identify each game by an SMI number alongside its part number, and
they disagree with that pairing:

| SMI | Part number | Hit freq. | Max-coin % | Top award | Note |
|---|---|---|---|---|---|
| 6892 | E595211X-05 | 14.07 | 90.85 | 1500 | Blazing 7s 3CB; matches the dump's `90.85%` |
| 7156 | E615511X-05 | 13.14 | 98.02 | 1500 | the SMI on the sticker |
| 5962 | E628511X-05 | 13.68 | 92.83 | 1000 | the SMI on the card cage's sales-order label |
| 8221 | E725711X-05 | 12.37 | 89.37 | 1000 | the other dump |

So the installed game is E595211X-05 (SMI 6892). Verified on the machine: its SAS ROM
signature equals the CRC of `s6000_main_S6M00002B5.bin` plus `personality_E595211X-05.bin`
for two seeds, and SAS reports paytable `E59521` at 90.85% (`notes/sas-j10.md`). The
sticker's "SMI7156" looks like a labelling mistake (inferred), and the card cage
originally shipped as SMI 5962 (inferred).

## Chip checksums (16-bit sum of all bytes)

Sums over each chip's own bytes, as read from its dump; the Note column gives the
reference value where one exists.

| Dump file | Sum | Note |
|---|---|---|
| U18 E725711X-05 | `8500` | matches "CS 8500" in its file name |
| U20 E725711X-05 | `8E7B` | matches "CS 8E7B" in its file name |
| U18 "SMI7156" (E595211X-05) | `83D9` | no reference value |
| U20 "SMI7156" (E595211X-05) | `8DF3` | no reference value |
| U28 LeaveInMain | `1B2A` | over the full 512 KB chip |
| U43 LeaveInMain | `3E00` | over the full 512 KB chip |
| U28 Clear / U43 Clear | `9D77` / `8901` | |
| U28 / U43 WPrinterSupport | `4437` / `C900` | |

`DS1265Y(RW)2.BIN` among the original dump files is every byte `0xFD`, a failed read, and is not kept.
