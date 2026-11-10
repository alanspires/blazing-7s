# Bally ProSlot 6000: the J3 "HHU" serial protocol

Reverse engineered from the S6000 main program `S6M00002B5` (2004), merged from the
U28 (even bytes) and U43 (odd bytes) EPROM dumps of the author's chips.
The chips in our machine carry the matching labels (see `machine.md`).
The 1 MB dump is the 512 KB program twice; `roms/s6000_main_S6M00002B5.bin` keeps one copy.
Ghidra project: `ghidra/proj/bally.gpr`; function names below are the ones set there.

Status markers: **verified** = checked against the ROM bytes or schematic;
**inferred** = read from decompiled code, not yet seen on a real wire.

## Hardware (verified, MK14-S6MOD-0001 schematics)

- CPU MC68HC000 (16 MHz). Three MC68681 DUARTs at `FE1000`, `FE1020`, `FE1060`, clocked
  at 3.6864 MHz. MC68681 register n sits at `base + 2n + 1` (RS1..RS4 on A1..A4, odd byte lane).
- DUART1 channel A: link progressive (RS-232 through U27, or RS-485 through U24/U25,
  picked by JW8; OP2 drives the RS-485 transmit enable through JW9).
- DUART1 channel B: **J3 "HHU (ON BOARD)"**, a 10-pin header wired DB9-style:

  | J3 pin | Signal | MPU direction |
  |---|---|---|
  | 2 | RXD2 | in |
  | 3 | TXD2 | out |
  | 4 | DTR2 | out |
  | 5 | GND | |
  | 6 | DSR2 | in |
  | 7 | RTS2 | out |
  | 8 | CTS2 | in |
  | 1, 9, 10 | not connected | |

  An IDC10-to-DB9 ribbon turns it into a PC-style (DTE) DB9, so a PC needs a null modem.
- Line levels depend on U70: the AS-03356-0424 drawing fits an MC145406 (RS-232 levels);
  the AS-03356-0438 drawing shows a DIP shunt there (TTL levels). Our board is
  AS-03356-0424 Rev G and U70 is a Maxim MAX1406CPE, an RS-232 transceiver that replaces
  the MC145406 (observed on 2026-10-17, from photographs that are not distributed), so J3
  is **RS-232 levels**.
- J3 sits on the MPU's front edge and is reachable through the card cage front panel
  (observed on 2026-10-17, from photographs that are not distributed).
- DUART2 B and DUART3 B are configured for 9-bit multidrop mode at 19200 baud: the SAS
  host ports (options 98/99 Primary/Secondary Host). `sas_rx_byte` is the SAS receiver.

## Serial setup (verified, `duart1_init_9600_8n1`)

Both DUART1 channels: MR1 `0x33`, MR2 `0x07`, CSR `0xBB`, ACR `0x00`
→ **9600 baud, 8 data bits, no parity, 1 stop bit**. IVR `0x41`, IMR `0x33`.

## Which protocol runs on J3 (verified, `select_j3_protocol_from_ds1`, `duart1_b_rx_j3`)

`read_ds1_switches` assembles DS1 from the DUART input ports into `0x43E8CA`.
`select_j3_protocol_from_ds1` turns DS1 SW4..SW6 into configuration word `0x43E8D2`,
matching the "DS1 - Secondary Device Protocol" table in MK2-S6MOD-0013 p.2-11
(an OFF switch reads as 1):

| SW4 | SW5 | SW6 | Manual | Bit set | J3 receiver |
|---|---|---|---|---|---|
| OFF | ON | OFF | Bally Secondary Game (BSG) | `0x80` | `bsg_rx_byte` |
| ON | OFF | ON | Secondary Device (SED, needs wheel control chip) | `0x100` | `sed_rx_byte` |
| OFF | ON | ON | Anchor 1.6 | none here | generic ring (not traced) |
| ON | ON | ON | none | none | generic ring |

The word picks the receiver:

- bit `0x80` → flag `0x41D875` → `bsg_rx_byte` (this document; the Bally Secondary Game link,
  inferred from the Monte Carlo manual: a lost link on the HHU port shows 91nC).
- bit `0x100` → flag `0x41D858` → `sed_rx_byte` (same framing; a different command set,
  dispatched through a 12×12 state/command table at `0x69902`; its fixed-state
  handlers were checked for upload capability in `remote-game-config.md`).
- neither → bytes go into the generic receive ring for port 1 (`0x411D3E` to `0x411DBE`).

**Our machine** has DS1 SW4–SW6 ON/ON/ON (observed on 2026-10-17; `machine.md`), so
neither bit is set. The port-1 task at `0x160EC` then calls `FUN_00016442(1)`, which runs
`FUN_00021af0(1, ...)`: the same handler the progressive link (port 0) uses. Its frames start
with `0x01`, carry a length (at most `0x80`) and a type letter: `C` (subcommands `1`, `2`,
`4`, `5`, `6`, `9`, `A`, `B`, `F`, handled in `FUN_00022d02`) or `P` (`PL` + 10 ASCII digits
sets the progressive amount at `0x43CD0E`, `FUN_00022df8`). The serial loopback test
(`FUN_000132ee`, sends `'A'+port` on every port) also reads this ring. For general access
use SAS on J10 instead (`sas-j10.md`).

## Framing (verified against the code and ROM tables)

Transmit (`bsg_send_frame`):

1. Payload `P` = `[cmd][seq][data...]`.
2. Append CRC-16/MODBUS of `P` (poly `0xA001` reflected, init `0xFFFF`, no final XOR),
   low byte first. The ROM's nibble tables at `0x73AEC`/`0x73B2C` match CRC-16/MODBUS on
   2000 random inputs and give the standard check value `0x4B37` for `"123456789"`.
3. Escape every byte `>= 0xFE` as `0xFE, b & 0x7F`.
4. End the frame with `0xFF`.

Receive (`bsg_rx_byte`): `0xFF` closes a frame into a 4-slot ring (16 bytes per slot,
slot byte 0 = count); `0xFE` sets bit 7 of the next byte; more than 13 bytes restarts the slot.
`bsg_check_crc` recomputes the CRC over all but the last two bytes.

Direction: MPU→device commands have bit 7 clear; device→MPU commands have bit 7 set
(`bsg_handle_frame` rejects incoming frames without it, then masks it off).

## Link layer (inferred from code)

- **Link reset**: MPU sends `[10 00]` three times (`bsg_link_reset`), then a status frame `0x14`.
- **Sequence numbers**: MPU's transmit sequence `0x41D877` increments per data frame; the
  receive sequence `0x41D876` must advance by exactly one. A repeat of the last sequence is
  re-acknowledged and dropped.
- **ACK**: MPU acks with `[20 seq]` (`bsg_send_ack_20`); the device acks with `[A0 seq]`.
- **Retransmit**: unacknowledged frame resent after > 20 task ticks, up to 3 tries, then link reset.
- **Keepalive**: after > 100 idle ticks the MPU sends `[11 seq]` once linked, or resets the
  link if not. The task sleeps 2 ticks per loop (tick length not measured).
- **Device reset**: an incoming `0x90` sets the expected receive sequence to 0.

## Messages

MPU → device:

| Cmd | Function | Contents |
|---|---|---|
| `10` | `bsg_link_reset` | link reset, sent ×3 |
| `11` | `bsg_send_keepalive_11` | keepalive |
| `14` | `bsg_send_status_14` | `[14 seq id flagA flagB byte bcd bcd bcd (extra)]`; id = `0x43D472`; the three BCD bytes are digits 1–6 of the game chip's part number at `0x10D1C1` (our machine's `E595211X-05` → `59 52 11`); the extra byte only when config `0x43DCD0` bit `0x10` is set |
| `20` | `bsg_send_ack_20` | ack `[20 seq]` |
| `33` | `bsg_link_reset_then_33` | sent after a reset when config `0x43DCD0` bit `0x20` is set |
| queued | `bsg_send_next_queued` | application frames from the port-1 message queue |

Device → MPU (bit 7 shown set):

| Cmd | Function | Effect |
|---|---|---|
| `90` | `bsg_rx_link_reset_90` | reset receive sequence |
| `A0` | `bsg_rx_a0_ack` | ack of the MPU's pending frame; acking `0x14` arms the hello |
| `95` | `bsg_rx_95_device_hello` | accepted only before the link is up and after `0x14` was acked; brings the link up; when byte 3 equals id `0x43D472` it also flushes the queue and posts game events |
| `91` | `bsg_rx_91_keepalive` | acked |
| `92` | `bsg_rx_92_fault_state` | byte 3 = 0 clears fault flag `0x10`, nonzero sets it |
| `84` | `bsg_rx_84_bonus_award` | byte 3 = id; bytes 4–5 = 16-bit big-endian amount, stored at `0x43D468` when the game is in state 6, then signalled to the game |
| `B0` | `bsg_rx_b0` | acked; signals the game in states `0x12`/`0x1C` |

## Open questions

- Complete wire layouts and meanings of the `C` subcommands on the default
  J3/progressive-link protocol. One candidate is now resolved: `CF8`
  (`FUN_00022bbc`) posts to queue 4, whose consumer `FUN_000609b6` controls sound,
  not buttons or reels. See `reel-control.md`, **Serial and deployment**.
- Task tick length (to turn the 20/100-tick timeouts into milliseconds).
- Contents of the queued application frames.
- Detailed SED message layouts behind table `0x69902`, and whether Anchor 1.6 is
  handled anywhere. The checked SED dispatch does not expose an identified loader;
  `remote-game-config.md` records that boundary and the nonfunctional `CF2` download
  candidate.
