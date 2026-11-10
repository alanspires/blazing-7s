# Reference documents

Copies of the documents relevant to our ProSlot 6000, renamed for clarity. The Source
column names each file as found in the author's collection of vendor and forum downloads.

## bally-s6000/

| File | What it is | Source |
|---|---|---|
| `MK2-S6MOD-0013_setup-and-operation.pdf` | S6000 setup and operation (444 pp): DIP switches DS1–DS3 (p. 2-11), options, diagnostics, exception codes | `Bally S6000/Bally_6000.pdf` |
| `MK14-S6MOD-0001_schematics.pdf` | S6000 schematics with searchable text: memory map, DUARTs, J3 HHU, backplanes | `Bally S6000/MK14-S6MOD-0001_SCHEMATICS.pdf` |
| `MK6-S6MOD-0002_monte-carlo.pdf` | Monte Carlo bonus top; describes the HHU-port bonus wheel and its exception codes | `Bally S6000/MK6-S6MOD-0002_MonteCarlo.pdf` |
| `option-card-blazing-7-settings-11d.pdf` | ProSlot 6000 option card M-00500-0130 (Test 11d options) | `Bally S6000/Bally S6000 Blazing 7 Setting 11d.pdf` |
| `tito-option-settings-printer-no-hopper.pdf` | option values for TITO with printer, no hopper (98 Primary Host 1, 92 SAS Bonusing 3, …) | `Bally S6000/TITO Menu settings Bally 6000 printer-no hopper.pdf` |
| `tito-harness-j10-pinout.pdf` | J10 SYSTEM pins for TITO: 1 GND, 3 TX, 5 RX, 13 GND, 31 +12 V | `Bally S6000/Bally S6000 TITO harness Pinout J10 .pdf` |
| `tito-harness-j10-photo.jpeg` | photo of that J10 harness (same wire colours as ours) | `Bally S6000/875529C7-…jpeg` |
| `tito-at-home-forum-fix-option-92-19hi.png` | forum fix: option 92 SAS Bonusing = 2 and 19HI = 0 made TITO@Home print | `Bally S6000/TITO Settings.PNG` |
| `mikohn-settings-blazing-7.pdf` | Mikohn PSP jackpot screens that worked on an S6000 (M-04/MS08 or M-08/MS00); the "Blazing 8" copy is identical and was left out | `Bally S6000/Mikohn setting for Bally S6000 Blazing 7.pdf` |
| `game-list-2008-09-30.xls` | Bally S6000 game list | `Bally S6000/Bally series 6000 game list as of Sept 30 2008.xls` |
| `games-by-smi-part-number.xls` | games by SMI part number (look up SMI7156) | `Bally S6000/Bally_by_Game_Name_SMI_Part_Number(1) (1).xls` |

## sas/

| File | What it is | Source |
|---|---|---|
| `IGT-SAS-6.02-protocol-specification.pdf` | the SAS protocol (190 pp); the copy in `Project/` is identical | `SAS Specifications.pdf` |
| `montana-sas-implementation-guide-1.2.1.pdf` | Montana's SAS implementation guide | `sasimplementationguide.pdf` |
| `sastest-simulator/`, `sastest-simulator-2021/` | Windows SAS host test tool (`sastest.exe`, `sastest2021.exe`) with its config and help | `SAS Simulator/`, `SAS Simulator 2/` |

## tito-at-home/

TITO@Home 4.0 guides for the board in our cabinet: `bally-s6000-tito-settings-v4.pdf`,
`tito-dip-switches.pdf` (free-play switches), `tito-flash-instructions.pdf`
(from `TITO @ Home Install Info/TITO 4.0 instructions/`).

## bill-acceptor-jcm-wba/

JCM WBA series (ours is a WBA-12-SS): quick reference card, flip-book manual,
exploded views, harness pinout (from `Bill Validators/WBA/` and
`Bill Validators/JCM WBA Quick Reference Card.pdf`).

## progressive/

The top sign and link progressive (J14 LINK 485). Our top box holds a Mikohn CHAMII+
logic board (label 341-075-05 "CSTLR STND ALONE(BLY)", observed on 2026-10-17 from
photographs that are not distributed), so the CHAMII+ manual and PSP software below apply.

| File | What it is | Source |
|---|---|---|
| `mikohn-chamii-plus-user-manual-990-241-38B.pdf` | CHAMII+ controller / display driver | `CHAMII+ User Manual 990-241-38 Rev B.pdf` |
| `mikohn-pocket-guide-990-241-39B.pdf` | Mikohn configuration menus, error codes, machine types and serial settings | `Mikohn Pocket Guide 990-241-39 REVB.pdf` |
| `chamii-plus-bally-harness-311-205.pdf` | Bally-to-CHAMII+ harness (CHAMII+ manual App A) | `Bally Interface Cable.pdf` |
| `psp-rs232-cable.pdf` | PC-to-Mikohn RS-232 cable for PSP | `PSP_RS232.pdf` |
| `mikohn-psp-2.0-technical-manual.pdf` | Progressive System Programmer 2.0 manual | `PSP_v2[1].0.pdf` |
| `prolink-progressive-controller-manual.pdf` | ProLINK progressive controller | `ROMS/ProLink.pdf` |
| `mikohn-progressive-system.pdf`, `mikohn-progressive-sult.pdf` | Mikohn progressive system write-ups | `267868487-Mikohn-Progressive-System.pdf`, `Mikohn Progressive.pdf` |
| `mikohn-display-manual-ii.pdf`, `mikohn-display-manual-scan.pdf` | Mikohn display user manual (two different copies) | `Mikohn Display Manual.pdf`, `305240725-Display-Mikohn.pdf` |
| `r273-chamii-psp-notes/` | a hobbyist's (r273) CHAMII+, PSP and progressive pinout notes | `r273CHAMII+PSPSettings/` |
| `psp-software/` | PSP DOS software, PSP 3 zip, EZTextToHex | `Bally S6000/psp/`, `PSP_Version3.zip`, `EZTextToHex.exe` |

## peripherals/

`MK8-SVMOD-0001_peripherals.pdf`: Bally peripherals module (2001), written for the S5500
but covering the PSA-66-ST printer, JCM WBA and the accounting interface
(from a vendor download of the S5500 set).

## components/

- `components/m27c4001-st.pdf`: ST M27C4001 parallel EPROM datasheet; programmer
  evidence and remaining checks are in `../firmware/PROGRAMMING.md`.
- `components/g65sc22-cmd.pdf`: full CMD G65SC22 VIA datasheet matching the
  U41 model on the MPU (`../photos/05-mpu-eproms.jpg`); timer/interrupt analysis is
  in `../firmware/IRQ-AUDIT.md`.

Provenance and hashes are in `components/README.md`.

## Left out

Bally Alpha/S9000, the rest of the S5500 set (a different model), IGT backup notes,
other bill validators (DBV, UBA, EB-100, MEI, Rowe), TITO guides for other platforms,
Paltronics, BSK100, the empty `Supreme Display User Manual.pdf`, the Mikohn LED
replacement project, and the GQ-4X chip list. The ROM dumps live in `../roms/`.
