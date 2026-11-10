# ROM images

Merged from the author's dumps of the machine's chips. The 68000 bus is 16 bits
wide, so each image interleaves two chips: the even chip supplies the high byte at even
addresses. Even/odd order was checked against content: the main program's vector table
(SSP `0x437FFE`, reset `0x800`) and readable strings in the personality ROMs.

| Image | Even chip | Odd chip | Contents |
|---|---|---|---|
| `s6000_main_S6M00002B5.bin` | U28 LeaveInMain | U43 LeaveInMain | main program `S6M00002B5`, ©2004; the 27C4001 dumps hold the 512 KB program twice, one copy kept |
| `s6000_clear_S6M0000CLR07.bin` | U28 Clear | U43 Clear | SafeRAM clear program `S6M0000CLR07`, ©2000 |
| `s6000_printer_S6M0000C00.bin` | U28 WPrinterSupport | U43 WPrinterSupport | main program variant `S6M0000C0007-08`, ©2003 |
| `personality_E595211X-05.bin` | U20 | U18 | installed game: `PRSN:E595211X-05`, `S63U3X-BL7`, `03-04-93`, `90.85% / 89.45%` |
| `personality_E725711X-05.bin` | U20 | U18 | another Blazing 7s: `PRSN:E725711X-05`, `06-09-97`, `89.37% / 83.90%` |

## Which game is installed

The installed chips' stickers read "E595211X-05 SMI7156". Bally's lists in
`docs/bally-s6000/` disagree with that pairing:

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
