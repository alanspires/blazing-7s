# The machine: Bally ProSlot 6000 "Blazing 7s"

What this privately owned slot machine is, board by board: the game, the MPU (main
processor unit) board and its chips, the DIP switches, the peripherals and the
backplane connectors, and what that means for talking to it over a serial port. It is
the hardware reference the other notes build on.

Observed on the machine on 2026-10-17, from photographs taken that day that are not
distributed with this repository. **Verified** = read on the machine in those
photographs; **inferred** = deduced from those observations plus manuals or firmware.
The later photographs in `photos/` are cited only where they show the item.

It is a **ProSlot 6000 (S6000)**, not an S5500: the MPU silkscreen reads "S6000 MPU" and
the door label is "ProSlot-6000 Exception Codes". The S6000 manuals in
`docs/bally-s6000/` apply; the S5500 manuals do not.

## Game

| Item | Value | Evidence |
|---|---|---|
| Game | Blazing 7s, 3 reels, 1 payline, 3 coins max | observed 2026-10-17; `photos/01-front.jpg` |
| Denomination | $1 | observed 2026-10-17; `photos/01-front.jpg` |
| Top award | 3rd-coin three Blazing 7s "PAYS PROGRESSIVE" | observed 2026-10-17; `photos/01-front.jpg` |
| Personality chips | U20/U18 stickers "E595211X-05 SMI7156 (1-256)"; Bally's lists put E595211X-05 at SMI 6892 (see `roms/README.md`) | observed 2026-10-17; `photos/05-mpu-eproms.jpg` |
| Personality strings | `S63U3X-BL7`, `PRSN:E595211X-05`, `03-04-93`, `90.85% / 89.45%` | `roms/personality_E595211X-05.bin` |
| Meters on 2026-10-17 | WIN PAID 10, CREDIT 1014, COIN IN 3 | observed 2026-10-17 |

The personality strings come from the chip dump, whose part number matches the chip
labels; that the dump was read from these exact chips is inferred. Bally's game list
gives E595211X-05 (SMI 6892) a 14.07 % hit frequency, 90.85 % max-coin payback and a
1500 top award.

## MPU board (observed 2026-10-17; `photos/04-mpu-in-card-cage.jpg`, `photos/05-mpu-eproms.jpg`)

| Item | Value |
|---|---|
| Assembly | S6000 MPU AS-03356-0424 **Rev G**, ©1997, date code 0308 |
| CPU | Motorola MC68HC000FN16 (U31, PLCC) |
| Serial | 3 × Philips SCN68681C1N40 DUARTs (dual UARTs: two serial channels each) |
| Parallel I/O | 2 × CMD G65SC22 VIAs, U41/U42 (versatile interface adapters: parallel ports and timers); U41 reads **G65SC22PI-2**, marked `0304` (both VIAs in `photos/05-mpu-eproms.jpg`) |
| Sound | Yamaha YM2149F (U52) |
| Main program | U28 / U43 "BALLY 6000 LEAVE-IN MAIN" → `roms/s6000_main_S6M00002B5.bin` (labels match). The later `photos/05-mpu-eproms.jpg` shows chips labelled "BALLY 6000 MAIN W/PRINTER" in these sockets (inferred: the reprogrammed spares of `firmware/PROGRAMMING.md`) |
| Personality | U20 / U18 "E595211X-05 SMI7156" → `roms/personality_E595211X-05.bin` |
| RAM | Samsung K6T1008C2E (128K×8) plus U30, a 32-pin part under a torn Microtec Research software copyright label (both in `photos/05-mpu-eproms.jpg`) |
| Battery | Tadiran lithium, BATT1 |
| Logic | PALs (programmable logic chips) E-01055-0041, -0044, -0045, -0047 |
| DIP switches | DS1 next to U24/RP20; DS2 and DS3 end to end below the YM2149F; settings below |
| J3 (HHU) | 10-pin header on the front edge; U70 next to it is a Maxim MAX1406CPE (date 0301), a 16-pin RS-232 transceiver that replaces the MC145406 in the drawing |
| Second MAX1406CPE | above SN75ALS195N / SN75ALS194N (RS-422 receivers/drivers) near DS1 |
| Card cage | AS-06605-0001 Rev G, UDT 0312-4; a site and sales-order label (the original casino order, inferred; not reproduced here, pixelated in the photo); TEST, COIN MECH, PSEUDO COIN, RESET (`photos/04-mpu-in-card-cage.jpg`) |

U30 (the torn software label) is not firmware: the schematic (p.14-12 and p.14-17) draws
it as an 841001 128K×8 SRAM holding data bits D8–D15, the partner of U45 (D0–D7),
together the scratch RAM and SafeRAM (the battery-backed RAM area).

Memory map (schematic MK14-S6MOD-0001, applied in the Ghidra project): MAINS
`000000-0FFFFF`, PERSONALITY `100000-11FFFF`, scratch RAM `400000-437FFF`,
SafeRAM `438000-43FFFF`, I/O `FE0000-FFFFFF`.

## DIP switches (observed 2026-10-17)

Read on 2026-10-17 from photographs that are not distributed, decoded with MK2-S6MOD-0013
p.2-11 to 2-13. Which physical bank is DS3 is inferred: the bank next to DS2 decodes to
$1 and JCM WBA only with this assignment, matching the glass and the bill acceptor.

| Bank | ON | OFF | Meaning |
|---|---|---|---|
| DS1 | 1–7 | 8 | SW1 4-digit message center; SW2, SW7 reserved ON; SW3 no bonusing; SW4–6 ON/ON/ON no J3 secondary protocol; SW8 SafeRAM clear procedure off |
| DS2 | 2, 3, 5, 7, 8 | 1, 4, 6 | SW1–4 = 0009 $1.00; SW5 ON SW6 OFF JCM WBA; SW7 normal reel spin; SW8 no diverter optic |
| DS3 | 4, 6, 7, 8 | 1, 2, 3, 5 | market code 07 ("4.00 Denomination"; unusual for a $1 game); SW5 OFF release to 99 code; SW6 ON no tokenization |

The manual says jumper and DIP settings take effect only after a Complete SafeRAM
Clear. Test 12 shows what the game reads (121d DS1, 122d DS2, 123d DS3, hex in CREDIT,
first digit = switches 5–8, OFF = 1): these readings predict **80**, **29** and **17**.

## Peripherals

| Item | Value | Evidence |
|---|---|---|
| Ticket printer | PSA-66-ST, "4MBIT" sticker | observed 2026-10-17; in place in `photos/02-door-open.jpg` |
| Bill acceptor | JCM WBA-12-SS, ID-003 protocol, software WBA-12/13-SS/SU/SH/UH-USA V3.34-21i (1185) | observed 2026-10-17; in place in `photos/02-door-open.jpg` |
| Bill bezel | accepts $1, $5, $10, $20, $50, $100 | observed 2026-10-17; `photos/01-front.jpg` |
| Top sign | tri-colour LED dot-matrix, scrolling | observed 2026-10-17; `photos/01-front.jpg` |
| Progressive controller | Mikohn CHAMII+ logic board, label "341-075-05 CSTLR STND ALONE(BLY)", PCB 321-075-00 Rev D, in a locked black box in the top box. NEC V40, Xilinx XC3030A, SCN2681 DUART, MAX232 (U13) to the J5 DB9 PC port, AT29C010A flash (U5), HM628128 RAM (U4), EPROM U19 "CH2 STA/V1.66 (C)MK99 K2:5FH7 ©2000 BALLY(BB3F)", GAL "CSSA V1.3" | observed 2026-10-17 |
| Progressive display | Mikohn "CHAM2 12 CELL MINI" Rev A, PCB 321-007-60 (©1989,90), part 341-010-10, four UCN5832EP drivers (U1–U4), taped behind the top glass | observed 2026-10-17 |
| Mikohn 341-010-30 board | four UDN2944W quad high-current source drivers (QP1–QP4), SN74LS122N, a 74HC32-type gate, 10-pin headers at both ends, power plug J4; purpose not confirmed (a lamp driver, inferred from its parts) | observed 2026-10-17 |
| Mikohn PCB, solder side | silkscreen "... CHAM2 ... ©1989,90 by MIKOHN Inc.", only partly legible | observed 2026-10-17 |
| Electromechanical meters | TOTAL IN 1524511, TOTAL OUT 6198504, DROP 3619331 (bracket AS4225-064, label LBL-180-3 Rev A) | observed 2026-10-17 |
| TITO@Home 4.0 | aftermarket ticket-in, ticket-out (TITO) board on the cabinet floor; five wires (red, orange, purple and two black) in one housing on backplane J10 SYSTEM | observed 2026-10-17; the housing, now reused for the SAS cable, in `photos/06-j10-sas-cable.jpg` |
| Aux board | small board on the left wall, silkscreen "BM-3" and "MEXICO", label "01-00-2607 S"; relay K1, U1 under the label, LED1, regulator-style U2, connectors P1–P3; purpose unknown | observed 2026-10-17 |
| Power | Bally low-leakage supply, Pacific Transformer E122-21xx (03-01), breaker box with 2.5/2.5/5/5 A push-to-reset breakers and ON/OFF switch | observed 2026-10-17; breaker box and supply label in `photos/02-door-open.jpg` |

## Backplane (observed 2026-10-17; `photos/03-interface-board.jpg`)

Readable connectors: J7 LINE IN, J8 LINE OUT (audio), J9 SPARE I/O, J10 SYSTEM,
J12 (modular jack), J14 LINK 485 (cable tagged "S6000 LINK 485"), J16 HOPPER,
J18 SPARE 232, J19 LINK 232, power test pins (+5 V, +12 V, +24 V, 7.5 VAC),
JACKPOT 1–3 RELAY (ISO1–ISO3) and TOTAL IN RELAY (ISO4), plus a 20-pin header.
Red tap splices sit on the jackpot-relay and harness wires (aftermarket).
The backplane assembly number sits under the card cage lip ("AS-0…"), not readable.
J10 SYSTEM is the SAS port; TITO@Home's wires sit in pins 1 (black), 3 (orange), 5 (purple),
13 (black) and 31 (red); see `sas-j10.md`. Top box harness label: AS-6851-02FM Rev H, Mar 2001.

## What this means for the serial work

- The S6000 chip dumps' labels and SAS-reported identity match this machine;
  the J3 findings in `j3-hhu-protocol.md` concern that identified firmware.
- J3 is RS-232 (U70 is a MAX1406CPE) and reachable from the card cage front, but with DS1 as
  set it only runs the narrow progressive-link message set.
- SAS on J10 gives the most access and works: `sas-j10.md` has the wiring, results and host
  timing, `tools/sas.py` the host. Its ROM signature matches `roms/s6000_main_S6M00002B5.bin`
  plus `roms/personality_E595211X-05.bin` for two seeds. No checksum discrepancy was
  detected, but this is not proof of byte-for-byte identity: CRC-16 can collide, and
  changing its seed is not an independent integrity check (`sas-j10.md`).
- The CHAMII+ J5 DB9 is a second RS-232 port, for Mikohn's PSP software (progressive settings).
- The LINK 485 cable feeds the CHAMII+ machine port J7 (inferred from the Bally-to-CHAMII+
  harness drawing: coin-in/jackpot on J7 pin 3, serial return on pins 9/10).
