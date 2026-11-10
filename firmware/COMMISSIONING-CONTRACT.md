# Commissioning implementation contract (offline, not hardware qualified)

This is the implementation contract for the pre-boot commissioning service described in
[PROVISIONING.md](PROVISIONING.md): its boot hooks, serial adapter, memory map, core
API, request/reply payloads, framing, host archive rules and the evidence the tests must
provide. It is for readers checking the firmware (`src/commission.c`,
`src/commission_io.c`, `src/commission_hooks.S`) or the host client
(`../tools/sas_commission.py`) against the intended design.

**Status: implemented and CPU-tested offline; not hardware qualified.** The owner
approved a pre-boot serial commissioning service. The implementation work involved no
live hardware or SSH writes, ROM installation or changes to the Ghidra project. The
service must preserve native SafeRAM (the battery-backed native accounting RAM),
credits and all previously acknowledged extension request IDs. Existing or recognizably
damaged extension state must never be formatted; arbitrary unrelated bytes require the
owner's explicit first-install intent, not an inference that the storage is virgin.

## Boot and hardware-adapter boundary

`commission_reset` initializes an AC-fail guard at the reset entry (`0x800`) before
reproducing the native SR/SSP setup. `commission_gate` replaces the LEA at `0xa58`,
after the native scratch and ROM tests, before kernel initialization and the native
SafeRAM tests. It saves registers and SR outside the native stack, switches to a
private stack, and either restores the exact native continuation at `0xa5e` or stays
in service. It also resets the guard for native soft-reset paths that bypass `0x800`.

**This is not a read-only whole reset.** Original instructions before `0xa58`
update the seed words at `0x43fff8..0x43ffff` and the native reset counter at
`0x4385e4..0x4385e7`. Commissioning itself does not write native SafeRAM or credits.
The entry-boundary tests must distinguish these two claims.

J10 SYSTEM is U39/DUART2 B: MR=`FE1031`, SR/CSR=`FE1033`, CR=`FE1035`, data=`FE1037`,
ACR=`FE1029`, IMR=`FE102b`. Schematic AS-03356-0424 sheet 14-19 traces U39 through U40
to TXD4/RXD4; sheet 14-8 maps them to J10 pins 3/5. Native ROM routines at `0x16834`,
`0x13b5e` and `0x13684` establish the register pointers, baud selection and 8N1 mode
values. The adapter combines those values into an explicit polled setup; that
combination is new and not hardware qualified. Do not call native serial startup,
which writes native state and creates tasks. Do not drive other DUART output pins.

Ordinary interrupt masking **does not block IPL7 AC_FAIL** (the power-fail interrupt).
Vector `0x7c` normally points to `0x275a`, which writes native SafeRAM. The
replacement checks the guard: normal startup continues to the original handler;
commissioning goes irreversibly to a watchdog-serviced halt, never back to a pending
write. On that terminal path it discards the private exception stack. This does not
prove electrical power-fail latency or atomic word stores. Watchdog service uses the
native `FF101e` access.

A valid interrupted native-clear journal must retain its original operator-clear
recovery route; early commissioning must not strand it before that branch.

## Memory

Existing persistence: `FW_PROFILE_BASE` 0x430000, `FW_PERSIST_SIZE` 0x600.
Core runtime stays at 0x430600 (1024 bytes), ending at 0x430a00. Motion runtime uses
[0x430a00, 0x431000). The separate commissioning transaction record is
`CM_RECORD_BASE=0x431000`, `CM_RECORD_SIZE=0x800`; it consumes part of the formerly
reserved core expansion, and the linker and runtime reservations must be explicit.
The commissioning working context `commission_state=0x431800` is at most 0x800 bytes.
The volatile staged capture is `commission_capture=0x435000`, size
`CM_CAPTURE_SIZE=0xe00`: the first 0x600 bytes are persistence, followed by the
pre-write 0x800-byte transaction area. All of these lie inside the existing
reset-preserved reservation `430000..435fff`. Working state and capture must be
explicitly initialized; retained bytes are never trusted. Native SafeRAM
`438000..43ffff` is NEVER written by commissioning.

The platform adapter (`src/commission_io.c`) uses `commission_io_state=0x434100` (at
most 0xf00 bytes), `commission_irq_guard=0x434010` and register-save scratch at
0x432000. Before kernel startup only, `0x433000..0x433fff` is the private
commissioning stack. The gate restores the native SP before normal startup; the
serialized runtime E0 callback then reuses that stack, while motion state remains
below the commissioning record. The two stack lifetimes cannot overlap. Platform
runtime is limited to 16 bytes; E0's saved caller SP at 0x434020 is outside the
stack, the guard and commissioning IO.

The record layout is little-endian:

| Offset | Contents |
| --- | --- |
| 0 | magic `S6CI` |
| 4 | version 1 |
| 5..7 | zero |
| 8 | CRC32 of the original persistence |
| 12..15 | zero |
| 16 | original persistence (length `FW_PERSIST_SIZE`) |
| after that | zero padding |
| size-6 | record CRC32, covering [0, size-6) |
| size-2 | aligned marker: READY=0xa55a, DONE=0x5aa5 |

Only an explicit INIT may publish READY. No persistence word is changed before the
record body readback and the READY commit. Initialization zeros only
`FW_PERSIST_SIZE`, verifies it, then atomically replaces READY with DONE. A valid DONE
record is never changed again. Core's existing `fw_boot` canonical bootstrap creates
the baseline journals later; do not implement another journal encoder. READY always
blocks normal startup.

For torn record creation, persistence still holds its original bytes. Restarting may
require the original archive and must validate exact word-prefix compatibility of the
staged-before image and the desired record (marker invalidation, body, commit).
Resuming from READY requires that the original persistence matches the supplied saved
archive exactly, and that current persistence is either original or an exact prefix of
zeroed aligned words followed by original words. Never reformat an existing baseline or
acknowledged operation. A bad record CRC with a committed marker fails closed. A DONE
record also fails closed if malformed; there is no fallback to first install.

First-install refusal detection covers recognizable current records: slot magic,
profile magic, request magic, journal magic, either commit marker, and near-magic
(single-byte-damaged) values at their defined locations. Detection is conservative,
not proof against arbitrary simultaneous corruption. There is no force option.

## Core ports and API

Statuses are the `fw.h` codes. Commissioning uses `FW_OK`, `FW_BAD_REQUEST`,
`FW_BAD_VERSION`, `FW_UNSUPPORTED`, `FW_INCOMPLETE`, `FW_INVALID_PROFILE` and
`FW_FAULT`; `src/commission.h` documents when each is returned.

`uint8_t commission_boot(void)` initializes its volatile context. It returns
`CM_BOOT_CONTINUE=0` only for a valid DONE record or no commissioning transaction plus
a successful existing `fw_boot`, or for a journaled interrupted native clear
(`fw_clear_pending`), on which `fw_boot` faults but which only normal startup's
operator-clear branch can complete. Otherwise it returns `CM_BOOT_SERVICE=1`. It must
not apply a profile, submit an action, write native state or start tasks. Calls to
`fw_boot` must be excluded while a READY or recognizably invalid commissioning record
exists.

`uint32_t commission_process(const uint8_t *request, uint32_t length,
 uint8_t *reply, uint32_t capacity)` consumes and produces the payloads below.
`int commission_storage_write(uint32_t address,const uint8_t*,uint32_t length)`
permits aligned words ONLY in persistence or the transaction record, with readback.
`void commission_watchdog(void)` services the hardware watchdog during long loops.
`void commission_identity(uint8_t *out)` supplies the 52-byte identity payload.
The platform adapter supplies these ports. The core uses `fw_crc32_update` in
watchdog-bounded chunks. There are no implicit globals.

## Request/reply payload and state

Request: ASCII `S6CM`, version 1, op byte, payload (at most 64 bytes).
Reply: ASCII `S6CM`, version 1, same op, `fw` status byte, payload (at most 64 bytes).
There is no SAS envelope and no SAS mutation ID. The transport is stop-and-wait raw
19200 8N1 on J10, only while commissioning. The outer framing is described below.

Ops:

- **0 INFO**, empty request. Payload `<B I H I H H H` (17 bytes): mode, persist_base,
  persist_size, record_base, record_size, capture_size, max_chunk. Modes are
  UNKNOWN=0 (candidate only, NOT proof of virgin state), READY=1, DONE=2,
  REFUSED=3. max_chunk=60. INFO performs no writes.
- **1 READ**: offset:u16le, count:u8 (1..60). Returns the exact bytes from the combined
  persistence+record capture. READ performs no writes; DONE and REFUSED state are
  readable too.
- **2 STAGE**: offset:u16le, data (1..60). Stages the original archived capture in
  volatile memory with coverage tracking. Conflicting overlapping bytes are refused;
  identical bytes are accepted. Staging does not touch any persistent region.
- **3 INIT**: the literal ASCII `FIRST-INSTALL` (13 bytes). Requires full staged
  coverage, explicit candidate eligibility, and the exact original/current comparison
  or the qualified transaction-resume comparison above. Only this op modifies
  persistence. It refuses DONE and REFUSED and never overwrites a completed
  transaction. Success stays in service and reports DONE; normal boot requires a
  physical restart.
- **4 IDENTITY**, empty request. The platform adapter provides the exact native ROM
  identity, 32 bytes from 0x720, and the personality identity, 12 bytes from
  0x10d1c0, followed by the CRC32 of the actual main ROM 0..0x7ffff and of the
  personality 0x100000..0x10ffff (52 bytes total). The watchdog is serviced
  throughout hashing; the result may be cached within the service.

READ during a torn transaction returns the CURRENT bytes. The host must retain the
original archive and use it for resume; recapturing current state is not a replacement
for an interrupted original archive. INFO/READ after INIT confirm DONE and zeroed
persistence; there are no automatic INIT retries after uncertain transport results.

## Framing (platform adapter and host)

Delimiter 0x7e; escape 0x7d followed by the byte XOR 0x20 (delimiters and escapes are
escaped). The delimited, unescaped body is the request/reply payload followed by the
little-endian CRC32 of that payload. A new delimiter closes the previous frame and
starts a fresh one; empty frames between delimiters are ignored. Body size limits are
strict; an overflowing or bad-CRC frame is discarded and the next delimiter
resynchronizes. Framing never depends on wall-clock or inter-byte timing. The watchdog
is always serviced; a UART error resets the partial frame. Only one bounded TX frame
exists at a time; an RX overrun resets framing. A missing response is uncertain, not
permission to retry INIT. Explicit resume uses the original on-disk archive.

## Host archive contract

There are two explicit commands, or phases: capture to a NEW local archive, then
initialize/resume from that archive with `--first-install`. The archive includes the
exact capture binary, its SHA256, the protocol layout, the native identities and the
actual ROM/personality CRC32s, and provenance (serial endpoint and capture method); it
never pretends that CRC32 proves identity. It must be saved, fsynced and read back
before any INIT. The `tools/sas` wrapper on the local computer must NOT perform INIT on
the remote Linux SAS host before the verified archive exists locally: use explicitly
separate capture and initialize phases, or a persistent client channel that serves
serial on Linux while the archive and approval stay local. There is no silent
overwrite and no force option. Hash, metadata or layout mismatches fail before INIT.
The exact saved capture is staged. Resuming an existing transaction needs the same
archived original, not a new capture.

## Evidence needed

Compiled CPU state-machine tests cut each modeled aligned persistent write,
including record creation, zeroing and DONE. They show that every prefix either can
resume from the original archive or faults without changing native bytes or IDs. Tests
show that READY never allows normal startup, DONE is never reformatted, corrupt or
acknowledged records are refused, and readback failures and missing, full or
conflicting stage coverage are handled. An integration test runs the real host,
framing and compiled core/adapter, including boot diversion and UART fakes. Physical
retention, atomicity, electrical UART mapping, timing and full native boot remain
hardware qualification, and `install_ready` must stay false.
