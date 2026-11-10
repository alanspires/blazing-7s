# Connecting a Linux PC to the S6000's SAS port (backplane J10)

This note is the practical and evidential record of talking to the Bally ProSlot 6000
over SAS: how the port is wired, what the host software has to do, how to set it up on
your own machine, and what the machine answered when every read-only command, a
non-money write test and a full-balance money test were run on it in October 2026.
It is for anyone who wants to reproduce the connection or check the findings.
**Status:** working since 2026-10-17.

## Terms used here

- **SAS** (Slot Accounting System) is IGT's protocol between a gaming machine and a
  casino host: meters, game configuration, event reporting, enable/disable, credit
  transfers, tickets and ROM signatures. The specification is
  `docs/sas/IGT-SAS-6.02-protocol-specification.pdf`; section (§) and table numbers
  below refer to it. The machine itself reports SAS 6.01.
- The host addresses one machine at a time with a **long poll**: an address byte and a
  one-byte command code (written here in hex, for example `1C`). A type R long poll is
  just those two bytes; type S and M long polls add data and a CRC. Type R long polls
  only read; type S long polls set something or start an action;
  type M long polls address one game of a multi-game machine. A **general poll** asks
  the machine for its next queued **exception** (an event code such as `11`, slot door
  opened); with real-time event reporting on, an event can also take the place of a
  long-poll reply.
- The **wakeup bit** is a ninth data bit, set only on the first byte (the address) of
  each host message, so the machine can find message boundaries.
- A machine that has heard no poll for 5 s **chirps**: it sends its own address byte
  every 200 ms (§4.2).
- **AFT** (Advanced Funds Transfer) moves credits between the machine and a host
  account; **EFT** (Electronic Funds Transfer) is the older scheme that SAS 6.02
  dropped. **TITO** is ticket-in/ticket-out. **TITO@Home** 4.0 is an aftermarket board
  in this cabinet that normally acts as the machine's SAS host (`machine.md`).
- **Meters** are the machine's lifetime accounting counters. **SafeRAM** is the
  battery-backed RAM at `0x438000`–`0x43FFFF` that survives power-off; **work RAM** is
  cleared at power-up. **Options** are the numbered settings of the machine's setup
  menus (setup manual `docs/bally-s6000/MK2-S6MOD-0013_setup-and-operation.pdf`).
- The **SAS host** in this note is the Linux computer next to the machine that owns the
  USB serial adapter. `tools/sas.py` is our SAS host program; `tools/sas` runs it on
  the SAS host from another computer (**SAS host setup**, below).
- Handler names such as `FUN_0002bae4` are function names in the Ghidra project
  `ghidra/proj/bally.gpr`; addresses are CPU addresses in main program S6M00002B5.

**Verified** means checked in a schematic, the specification, source, a test, or on the
machine (including photographs of it taken 2026-10-17, which are not distributed).
**Inferred** means not yet seen on the machine.

## Status (2026-10-17): working

The PC talks to the machine through an FTDI FT4232H adapter on a Linux SAS host, port
`/dev/ttyUSB1`. `tools/sas` runs any of the 118 long polls the firmware accepts. On
2026-10-17 every command that only reads was run (82 invocations); 77 answered (verified):

| Command | Result |
|---|---|
| `info`, `game-config 0` | game ID `BS`, additional ID `105`, $1 denomination, max bet 3, paytable `E59521`, base payback 90.85%, options `0x0168` |
| `version` | SAS 6.01, the machine's serial number (redacted: the captures carry `12345678`) |
| `meters` (and `0F`, `19`, `10`–`17`, `game-meters 0`) | coin in 548, coin out 1562, drop 0, jackpot 6000, games played 183, won 25, lost 158, slot door opened 14, power resets 9 (credits) |
| `credits` | 1014 credits on the machine (`aft-lock` agrees: 101400 cents cashable) |
| `legacy-bonus-meters 0`, `legacy-bonus-win` | 7000 deductible bonus credits; last bonus 1000 |
| `selected-meters`, `extended-meters` | Table C-7 meters, e.g. `7F` weighted average theoretical payback 10255 (hundredths of a percent) |
| `aft-register FF`, `aft-lock FF 0 0` | the machine's asset number (redacted: the captures carry `12345`), AFT not registered (`80`), transfer limit $8985.00 |
| `date-time` | the machine's clock: `10172026` `234738` |
| `reel-stops` | `15 05 0b` (reels 1–3, rest `FF`) |
| `features 0` | `A6 5D 01` (Tables 7.14c–e) |
| bill meters, tickets, EFT, tournament, progressive polls | all zero |
| `rom-signature` | seed `0x0000` → `CCFB`, seed `0x1234` → `0C2B`; both equal the images (below) |
| `poll` | queued events after the reboot: `17` power applied, `11`/`12` slot door opened/closed; idle answer `1F` |

Four commands gave no reply, each as the specification allows: `last-bill` (48; machines
that never send exception 4F may not support it), `pending-cashout` (57, only during a
system-validated cashout), `ticket-data` (70, only with ticket redemption) and
`validation-info` (4D, only with secure enhanced validation). `selected-meters` with no
meter codes is NACKed (it needs at least one). Commands that change the machine are
covered by the write test (below).

`tools/sas dump` reruns every command that only reads, with the argument values that
make it a read (`read=` in `tools/sas_commands.py`; `tools/sas help <command>` shows
them). Its 2026-10-18 output is `tools/fixtures/s6000_dump.jsonl`: 80 commands, 77
answered; `last-bill`, `set-validation-id 0 0` and `validation-info FF` were silent. The
tests decode every reply in it and check that the meters agree across polls.
`pending-cashout`, `ticket-data` and `sas-progressive-win` are left out of the dump:
answering them starts a cashout or ticket exchange, or deletes the queued win once
acknowledged (§15.7, §15.11, §10.4.2).

The ROM signatures match `roms/s6000_main_S6M00002B5.bin` followed by
`roms/personality_E595211X-05.bin` for both requested seeds, so no checksum discrepancy
was detected. This is not proof of byte-for-byte identity or absence of bit rot: CRC-16
can collide, and changing its seed does not provide an independent integrity check.

## Why J10 and not J3

- J3 (the header the schematic labels HHU), with this machine's DS1 switches SW4–SW6
  all ON, runs the same short "C"/"P" message protocol as the progressive link
  (verified: the port-1 task at `0x160EC` falls through to `FUN_00021af0`, the handler
  port 0 uses; it knows about 9 "C" commands and one "P" message). The other DS1
  choices give the bonus-game and secondary-device links (`j3-hhu-protocol.md`). None
  is a general interface.
- The schematic labels J10 SYSTEM "SAS" (MK14-S6MOD-0001 p.14-8).

## The port (verified)

| J10 pin | Signal | Wire (TITO@Home housing) | Source |
|---|---|---|---|
| 1 | GND | black | schematic p.14-8, `docs/bally-s6000/tito-harness-j10-pinout.pdf` |
| 3 | TXD4, machine transmits | orange | schematic p.14-8; chirps seen on it |
| 5 | RXD4, machine receives | purple | schematic p.14-8; polls on it are answered |
| 13 | GND | black | harness pinout |
| 31 | +12 V (TITO@Home power) | red | harness pinout |

- J10 is a 2×20 header: odd pins on the top row, pin 1 at the right end. The TITO@Home
  wires sit in pins 1, 3, 5, 13 and 31 (both observed on 2026-10-17, from photographs
  that are not distributed). The SAS cable reuses that housing
  (`photos/06-j10-sas-cable.jpg`).
- J10 uses RS-232 levels: pin 3 idles at **−12 V** against pin 1 (measured). On this
  board revision (AS-03356-0424) the drawing puts an MC145406 RS-232 driver (U40,
  powered from +12 V and −12 V) on these lines; the -0438 drawing has a DIP shunt there
  instead (TTL levels).
- Line format (§1.2): 19200 baud, 8 data bits, a ninth wakeup bit, 1 stop bit. The host
  sets the wakeup bit on the first byte of each message only. Bytes of one message may
  not be more than 5 ms apart (§2.3.2); the machine answers within 20 ms (§2.3.1); the
  host polls no faster than every 200 ms (§2.3.3).
- There is only one SAS host: options 98 (Primary Host) and 99 (Secondary Host) cannot
  both be SAS (setup manual, options 98/99). The SAS address is 1 (option 78Hi; verified
  by the chirp). While the PC is connected, TITO@Home is unplugged.

## What you need

- A USB-to-RS-232 adapter with real RS-232 levels, a male DB9 and an **FTDI** chip
  (verified with an FT4232H). Not a 3.3 V/5 V logic-level (TTL) adapter: ±12 V can
  damage one. Other chips (CP210x, CH340, PL2303) are untested with the parity timing
  described below.
- A DB9 female screw-terminal breakout (`photos/07-db9-breakout.jpg`).
- Three female-to-bare-wire jumpers for 0.1" header pins, or the TITO@Home housing.
- A multimeter.
- A Linux computer next to the machine that owns the adapter (the SAS host). macOS
  cannot be the SAS host: it lacks the CMSPAR mark/space parity setting that supplies
  the wakeup bit. The serial port cannot be forwarded over the network instead, since
  SAS allows only 5 ms between bytes. The SAS host needs Python 3 with pip and your user
  in the `dialout` group. Running `tools/sas.py` there directly also needs pyserial
  (`pip install pyserial`); the `tools/sas` wrapper installs its own copy.

## SAS host setup

`tools/sas` runs from a checkout on another computer (macOS or Linux) and reaches the
SAS host over SSH. Each online command mirrors `tools/` and `roms/` there with rsync,
installs pyserial into the mirror once (`pip install --target`), and runs `sas.py`
remotely. It needs:

- passwordless SSH to the SAS host (the wrapper uses `BatchMode=yes`, so a password
  prompt fails instead of waiting), and `rsync` on both ends;
- these settings in the environment:

  ```sh
  export SAS_HOST=user@sas-host     # SSH destination of the SAS host
  export SAS_PORT=/dev/ttyUSB0      # the adapter's serial device on that host (step 4)
  export SAS_DIR=blazing-7s         # optional; mirror directory, relative to the login
                                    # directory unless absolute (this is the default)
  ```

`SAS_HOST` and `SAS_PORT` have no defaults. Without either, every online command exits
with status 2 before any SSH or rsync call. `tools/sas list`, `tools/sas help`,
`tools/sas profile-stock` and `tools/sas profile-preview` run locally and need neither.
On the SAS host itself, skip the wrapper and pass the port directly:
`python3 tools/sas.py --port /dev/ttyUSB0 <command>` (`sas.py` has no default port).

## Steps

1. **Unplug TITO@Home.** Machine off. Pull the TITO@Home housing off J10 (all five
   wires stay in it). It goes back the same way: wires on the top row, black at the
   pin 1 end.
2. **Check the voltage.** Machine on. Measure J10 pin 3 (orange) against pin 1 (black):
   expect −5 to −12 V (RS-232 idle; this machine reads −12 V). About +5 V or +3.3 V
   means TTL levels: stop, because an RS-232 adapter would need a level shifter there.
3. **Wire it.** Machine off. Connect J10 pin 1 (GND) to breakout pin 5 (GND), then:

   | Breakout plugs | J10 pin 3 (machine TX) | J10 pin 5 (machine RX) |
   |---|---|---|
   | straight into the adapter, or through a straight cable | breakout pin 2 (RXD) | breakout pin 3 (TXD) |
   | through a null-modem cable, such as our gray StarTech one | breakout pin 3 | breakout pin 2 |

   Mind pin 31 (+12 V) and the even row when pushing jumpers on.
4. **Find the port.** Machine on, nothing polling it for 5 s, then run on the SAS host
   `python3 tools/serial_listen.py /dev/ttyUSB0 /dev/ttyUSB1 ...` (it only listens).
   The port that prints `01` every 200 ms is the machine's (the chirp, §4.2). If
   nothing arrives at all, see **If it does not answer**.
5. **Talk to it.** With the settings from **SAS host setup**, `tools/sas` runs
   `sas.py` on the SAS host; on the SAS host itself, run
   `python3 tools/sas.py --port <device> ...`.

   ```
   tools/sas list                      # all 118 commands: code, name, type, values
   tools/sas help aft-lock             # one command's request and reply fields
   tools/sas meters                    # by name, or by hex code: tools/sas 1C
   tools/sas game-meters 0             # values follow in field order
   tools/sas selected-meters 0 0 1 5 0x0C
   tools/sas dump                      # every command that only reads
   tools/sas write-test                # non-money writes; bills/sound on, game delay zero
   tools/sas money-test                # adds two credits, withdraws ALL, restores starting balance
   tools/sas --json info               # replies as JSON
   tools/sas poll                      # live events: doors, bills, game start/end
   tools/sas rom-signature 0x1234 roms/s6000_main_S6M00002B5.bin roms/personality_E595211X-05.bin
   tools/sas find                      # which address answers
   tools/sas raw 1F                    # any bytes after the address, raw reply
   ```

   BCD values are written as digits (`10172026` for a date), binary values as numbers
   (`255` or `0xFF`, so a ROM signature seed is `0x1234`), and a variable-length
   command may leave off trailing values. Nothing asks for confirmation: `shutdown`,
   `legacy-bonus`, `aft-transfer` and the rest act at once.
6. **Leave the CLI connected.** Keep TITO@Home disconnected while the PC is the SAS
   host. If reverting to TITO@Home later, power the machine off before swapping the
   J10 harness.

## If it does not answer

- **No chirp on any port.** The machine's TX does not reach the adapter's RX. Run
  `python3 tools/serial_txd_toggle.py /dev/ttyUSBn` (all ports at once is fine) and
  measure breakout pins 2 and 3 against GND: the adapter's transmit line swings between
  about −6 V and +6 V every 3 s. If it lands on the machine's TX wire (that wire then
  swings between −12 V and about +1.5 V as the two drivers fight), the cable crosses 2
  and 3: swap the wires on the breakout. RS-232 drivers are current-limited, so this
  short fight does no harm (inferred, typical of RS-232 driver ICs). This is how we
  found that the gray StarTech cable is a null-modem cable.
- **Chirps continue while polling.** The machine does not take the polls as address
  bytes: check the RX wire, then the address (option 78Hi) and Primary Host (option 98).
- **Adapter self-test.** With nothing else connected, join breakout pins 2 and 3 and run
  `python3 tools/sas.py --port /dev/ttyUSBn selftest`: it must print `OK`.
- The machine ignores a poll whose bytes arrive more than 5 ms apart.

## What the host must do (verified on this machine)

- **Wakeup bit timing.** The wakeup byte goes out with mark parity, the rest with space
  parity, so the parity bit serves as the ninth bit. `tcdrain` returns once the FTDI
  chip holds the byte, 0.25 ms after the write, before the 0.57 ms frame has left;
  switching parity then re-frames the wakeup byte and the machine ignores the poll
  (0 of 5 answered). Waiting one more frame after `tcdrain` worked 5 of 5; `sas.py`
  waits two, a gap of about 1.4 ms.
- **Sync and acknowledge (§3.1–3.3).** After startup or 5 s without polls the machine
  ignores polls for itself until it sees a poll to address 0 (`80`). Repeating a poll
  asks it to resend (implied NACK); on the third identical poll it stops answering. So
  the host sends `80` before its first poll, and as the ACK before repeating a poll it
  got an answer to. Polling `81` alone got three replies and then silence.
- **20 ms after the sync.** A poll sent right after `80` was often missed; 20 ms later
  (the §2.3.1 response window) every poll was answered.
- **First poll after idle.** Even after the sync, the first poll after 5 s of silence
  was ignored once in three tries. `sas.py` repeats a poll whose reply is missing or
  garbled, up to 3 times (§3.2).
- **CRC low byte first**, both ways: the machine's replies check that way, and it ACKs
  our ROM signature request sent that way.
- **Idle answer.** With nothing queued, general polls get `1F` ("no activity and waiting
  for player input"), not `00`.

## Write test (2026-10-18, verified)

`tools/sas write-test` (`tools/sas_write_test.py`) exercises the commands that change
the machine without moving money, with read-back where available. Play, clock and
registration are checked after they are restored. Sound and bills end enabled and game
delay at zero; those settings have no SAS read-back, so only their ACKs are verified.
What each step expects comes from the firmware handler named in the table below.

The earlier passing run is `tools/fixtures/s6000_write_test.jsonl`; the tests decode
every reply in it. Its step descriptions are those of the code at the time: the
`status_only` one predates finding that `7B`, `7C` and `7D` change state (table below),
although its recorded replies already show `3F` posted. The extended run, including
bills and game delay, also passed and is preserved in
`tools/fixtures/s6000_write_test_bills_delay.txt`. The owner approved its final bill and
delay settings rather than restoring their unknown starting values.
`tools/test_sas_write_test.py` exercises the routine against `FakeS6000`
(`tools/sas_testing.py`), a scripted model of these handlers.

| Poll | Handler | What it does on this firmware | Seen on the machine |
|---|---|---|---|
| `09` enable/disable game | `FUN_0002bae4` | sets or clears the play-disabled bit (bit 7 of `0x412D38`, work RAM) | ACK; probe below flips `C5`→`80`→`C5` |
| `01` shutdown, `02` startup | `FUN_000279f0`, `0x27A52` | `01` sets the flag at `0x43D150` (SAFERAM, survives power-off) to 1; the main loop at `0x7F32`, once the machine is idle, sets bit 7 and the flag to 2. `02` acts only on flag 2 (sets 3; the main loop clears bit 7 and the flag). No cashout on either path | shut down within one poll; after `02` the machine skipped one whole poll, then play was back |
| `03`, `05`, `04` sound | `0x27AD6`, `0x27B34`, `0x27AFE` | sound flags `0x43E8E2`, `0x43E8E3` (SAFERAM); `04` sets both to 1, their RAM-clear value | ACK (no poll reads them) |
| `07`, `08`, `06` bills | `0x2A42C`, `FUN_0002b94e`, `0x2A3B8` | clear, replace, then enable the bill mask at `0x43CDAA` (SAFERAM) | ACK for off, $1-only with acceptor kept enabled, then all bills on; no physical bill test or setting read-back |
| `2E` game delay | `FUN_0002af0e` | writes `0x43D312` (SAFERAM), only with option 92 on | ACK for 10 × 100 ms, then zero; no setting read-back |
| `7F` set date/time | `FUN_0002eaac` | writes the clock chip; **sends no reply at all** (Table 7.17 says ACK) | silent; `7E` read the new time; restored to the second |
| `0E` real-time events | `FUN_0002ac26` | flag in work RAM (off at power-up, §12) | with it on, `6F` came as an event `01 FF 6F` |
| `74` AFT lock / cancel | `FUN_0003056c`, `FUN_000306be` | lock sets bit 30 of `0x412D38` and posts `6F`; cancel clears it | lock status `00`, then `FF` |
| `73` AFT registration | `FUN_0002fee4` | `00` stores key and POS ID (status `00`); `01` registers (needs a non-zero stored key); `80` unregisters and posts `6E` but keeps key and POS ID | `00`→`01` (AFT status bit 3 set)→`80`; restored to status `80`, zero key, POS 0 |
| `AA 00` auto rebet off | `FUN_0002b740` | no change while auto rebet is off (`AA 01` would make the machine play on its own) | ACK |
| `94`, `A8` handpay | `FUN_0002cd6c`, `FUN_0002ceb8` | answer only with options `0x20`/`0x1F` on; `02` with no handpay | silent: both options are off |
| `58` validation number | `FUN_0002d732` | handled only when `0x41D376` (option `0x24`) is 3 | silent |
| `4C` validation ID | `FUN_0002d0e8` | handled only when option `0x24` is 1 or 2; ID 0 only reads | silent |
| `75` receipt data, empty | `FUN_0003172e` | sets nothing | ACK |
| `7B` validation status, mask 0 | `FUN_0002e2de` | answers, but also posts `3F` and clears the validation-ID flag `0x41D992` (set only by `4C`); so not a read | the asset number, status `003F`, expirations 0; `3F` posted, and again before later runs |
| `7C`, `7D` with no data | `FUN_0002e492`, `FUN_0002e744` | answer whether ticket text was received, then clear that flag; so not reads | both `00` |

A handler given as a bare address (`0x27A52` …) is the entry point the ROM's command
table lists for that poll.

**Seeing play disabled.** `6A` does not show it here (it would only with `0x43E8E4`
set). `6E` action 2 (authenticate) does: it answers `C5` while play is enabled before
looking at the component, and with play disabled looks the component up; index `FF`
does not exist (`FUN_0002f28e` takes 1-3), so the answer is `80` and nothing starts
(`tools/sas authentication 2 0 0xFF`).

**Validation type: open.** `58` and `4C` stayed silent, so option `0x24` (`0x41D376`)
is neither 3 (system validation) nor 1 or 2 (secure enhanced validation), although `A0`
reports system validation (Features1 bits 5-6). How TITO@Home validated tickets is still
to be traced.

**Not run.** EFT `62`–`6B`, AFT bonus/debit/ticket transfers, and restricted/promotional
transfers. Legacy bonus and cashable in-house AFT transfers are covered in **Money test**.
`71` redeem: on this firmware any code outside its list, `FF` included, sets the stored
redemption status (`0x43D7B7`) to `82` and calls `FUN_000346dc(3)` before answering
(SAS 6.01 has no `FF` interrogation), so it would overwrite the last TITO redemption
record. `80`/`86` progressive levels: with SAS progressives on, they set jackpot
amounts. `8B` multiplied jackpot and `8C` tournament (enter writes options `0x43E81C`,
`0x43E809`, `0x43E82D`; exit depends on the tournament state at `0x43C350`, which no
poll reads).

**The machine restarted between two write-test runs (verified; cause unknown).** Run 3
began with `8E` "component list changed", and the component list CRC in `6E` replies
changed from `30B8` to `2461`. Component 1 is "ROM+SMI BALLY S6000 ... <signature>"; the
old CRC is exactly that string ending in `0C2B` (our seed `0x1234` request), the new one
ending in `CCFB` (seed `0x0000`), with the same bill acceptor string
`BVJCM12V380-32AEA1`. A seed-0 signature is computed only by `21`, by `6E`
authentication of a real component (both call `FUN_00028b44`; a byte search finds no
other callers), or by the power-up initializer `FUN_0000154e` (reached only from the
reset vector, via `0xBCA`). None of those polls was sent between the runs, and nothing
was polling. `legacy-bonus-win` (work RAM) also went from 1000 to 0. The power reset
meter stayed at 9 and no `17` "AC power applied" was reported, so it looks like a CPU
restart rather than a power loss. It came within about a minute of run 2, the first run
with `01`/`02`.

## Money test

**Verified on 2026-10-18:** `tools/sas --json money-test` passed on the machine, with
the complete request/reply/check log in `tools/fixtures/s6000_money_test.jsonl`. It runs
`tools/sas_money_test.py`. This is a **full-balance test**, not just a two-credit round
trip: it adds one legacy bonus credit and 100 cents by AFT, withdraws the whole balance,
and transfers the starting balance back in.

The preflight (`tools/fixtures/s6000_money_preflight.jsonl`) reported AFT status `F0`:
in-house transfers enabled, but bit 1 clear, so withdrawals of less than the full
available amount are not supported (SAS Table 8.2b). We did not attempt a partial
withdrawal. The owner explicitly approved withdrawing the entire balance and restoring
the original credits after this limitation was explained.

| Action | Verified credit balance | Other evidence |
|---|---:|---|
| Starting state | 1014 | `1A`, `6F` meter `0C`, and `74` cashable amount agree; $1 denomination |
| `8A 1 0`: one deductible legacy bonus | 1015 | ACK, exception `7C`, `90` last bonus 1, `9A` deductible meter 7000 → 7001 |
| `72` type `00`: 100 cents in | 1016 | status `40` → `00`; transaction ID and amount match; meter `A0` +100 cents, `A1` +1 |
| `72` type `80`: 101600 cents out | 0 | status `40` → `00`; meter `B8` +101600 cents, `B9` +1 |
| `72` type `00`: 101400 cents back in | 1014 | status `40` → `00`; meter `A0` +101400 cents, `A1` +1 |
| Final checks | 1014 | `1A`, `6F`, `74` agree; play enabled, no AFT lock, registration unchanged (`80`, zero key, POS 0) |

Meters are permanent, so the final lifetime totals retain the test: deductible legacy
bonus **7001 credits**; in-house cashable in **101500 cents in 2 transfers**; out
**101600 cents in 1 transfer**. The starting credit balance was restored, not the
accounting history. No receipt, physical cashout or forced-handpay flags were
requested. No restart-detector or ROM signature request was sent during this money test.

**Firmware basis.** `FUN_0002a9d6` handles legacy bonus (`8A`): option 92 must be 1 or
3, play enabled, and the amount and tax status valid; it queues the payment.
`FUN_000309e0` dispatches AFT (`72`); `FUN_00030ca2` validates the transfer and the
asset number, registration key and transaction ID before starting it. In-house types
`00` and `80` worked without AFT registration. The EFT load handler at `0x2B338` calls
its legacy path only when `FUN_0002eb2e` says AFT is off; EFT movement was not
live-tested.

**Failure handling.** The test records each AFT transaction ID, direction and amount
before sending its initiating poll exactly once (`retry=False`). It polls `72 FF 0`
while the transfer is pending, checks status, ID, asset, amounts and meter changes, then
acknowledges completion with a different read. It does not require exception `69`:
SAS §8.3 says it may be absent if completion is retrieved first. Any failure stops
further money writes, even during restoration; the balance may therefore be zero or
contain test credits after a failure.

Do **not** rerun a failed money test as recovery. Preserve its output; read `credits`,
`extended-meters 0 0x0C 0xA0 0xA1 0xB8 0xB9`, `legacy-bonus-meters 0`, and
`aft-transfer 0xFE 0`, then reconcile them with the last recorded intent. `FE` observes
without completing the transfer exchange; `FF` retrieves completion for acknowledgment.
Never issue a new money movement while the previous outcome remains uncertain.

`tools/test_sas_money_test.py` covers the round trip, immediate and delayed completion,
completion on the last permitted poll, refusal, persistent pending status, mismatched
transaction IDs, unexpected credits, lost money replies, and interrupted withdrawal.
The captured live replies also exercise the command decoder and accounting checks.

## ROM signature (long poll 21)

- The handler (`FUN_00028b70`, from the table entry for `21`) stores the seed (sent low
  byte first) and sets a request flag; the task `FUN_0000f5d6` runs the §5 CRC with the
  seed over `0x000000–0x07FFFF` (main program) and continues it over
  `0x100000–0x10FFFF` (personality ROM, the chip pair holding the game's paytable and
  reel data). The result answers the next general poll, low byte first. (When the byte
  at `0x43E8CE` is 6 it uses a different CRC loop; not the case here.)
- **Caution.** While computing, for about 8 s, the machine answers no SAS polls at all,
  which §6.1 forbids. In the first capture (seed `0000`) polls went unanswered from
  about 2 s to 11 s after the request, and the signature arrived 18 s after it.
  Afterwards `FUN_0002540a` stores a component string ("ROM+SMI BALLY S6000 ...
  <signature>") and, when it changed, raises `8E` "component list changed".
- After our two requests the machine also reported `31`, which SAS defines as "CMOS RAM
  error (data recovered from EEPROM)". The meters were unchanged and the machine showed
  no fault afterwards (checked on its displays the same night). In this firmware `31`
  comes from 14 call sites that look like lockup handlers (they call `FUN_00034d22`
  with `0x0C` or `0x10`), not RAM checks, and none of them touch the component list.
  Whether the 8 s stall caused it is unknown.

## Long polls this firmware accepts (verified, ROM table)

`tools/sas_commands.py` has every one of them with its request and reply layout, from
SAS 6.02 (table cited per command) or, for the EFT polls 6.02 dropped (`1D`, `27`, `28`,
`62`–`6B`), from the SASTest simulator's definitions
(`docs/sas/sastest-simulator-2021/sastest.ini`). `tools/test_sas_commands.py` checks the
list and every fixed request length against this ROM table. The EFT reply layouts are
SASTest's and unconfirmed on the machine, except `1D`, `27`, `28` and `6A`, which
answered.

`sas_rx_byte` (`0x19732`) looks each command up in a 256-entry table at `0x744CA`, 6
bytes per command: code, number of bytes that follow the command (`00` none, `FE`
variable length, `FF` not supported), `00`, and the handler address (24-bit). 118
commands are supported (code:bytes following):

```
01:02 02:02 03:02 04:02 05:02 06:02 07:02 08:07 09:05 0E:03 0F:00 10-1F:00 20:00 21:04
27:00 28:00 2A-2C:00 2D:04 2E:04 2F:FE 31-3F:00 40-46:00 48-4A:00 4C:08 4D:03 4F:00
50:03 51:00 52:04 53:04 54-57:00 58:0B 62:08 63:08 64:04 65:04 66:03 67:04 69:08 6A:00
6B:04 6E:FE 6F:FE 70:00 71:FE 72:FE 73:FE 74:06 75:FE 7B:FE 7C:FE 7D:FE 7E:00 7F:09
80:09 83:04 84:00 85:00 86:FE 8A:07 8B:0D 8C:0B 8F:00 90:00 94:02 95-9A:04 A0:04 A4:04
A8:03 AA:03 AF:FE
```

Type S polls such as `01` list 2 following bytes: the CRC. Among them: meters
(`10`–`2F`, `6F`, `AF`), ROM signature (`21`), legacy bonus (`8A`), tickets (`70`, `71`,
`7B`–`7D`), AFT (`72`–`74`) and features (`A0`). There are no `B0`–`B5` multi-denomination
polls.
