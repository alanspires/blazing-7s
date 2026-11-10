# Commissioning implementation contract (offline, not hardware qualified)

User approved a pre-boot serial commissioning service. No live hardware/SSH writes,
ROM installation or Ghidra mutation. Preserve native SafeRAM, credits and all
previously acknowledged extension IDs. Existing or recognizable damaged extension
state must never be formatted; arbitrary unrelated bytes require explicit user
first-install intent, not an inference of virgin storage.

## Boot and hardware-adapter boundary

`commission_reset` initializes an AC-fail guard at the reset entry (`0x800`) before
reproducing the native SR/SSP setup. `commission_gate` replaces the LEA at `0xa58`,
after native scratch/ROM tests, before kernel initialization and native SafeRAM
tests. It saves registers/SR outside the native stack, switches to a private stack,
and either restores the exact native continuation at `0xa5e` or stays in service.
It also resets the guard for native soft-reset paths that bypass `0x800`.

**This is not a read-only whole reset.** Original instructions before `0xa58`
update seed words at `0x43fff8..0x43ffff` and the native reset counter at
`0x4385e4..0x4385e7`. Commissioning itself does not write native SafeRAM or credits.
The entry-boundary tests must distinguish these claims.

J10 SYSTEM is U39/DUART2 B: MR=`FE1031`, SR/CSR=`FE1033`, CR=`FE1035`, data=`FE1037`,
ACR=`FE1029`, IMR=`FE102b`. Schematic AS-03356-0424 sheet14-19 traces U39 through U40
to TXD4/RXD4; sheet14-8 maps them to J10 pins3/5. Native ROM routines at `0x16834`,
`0x13b5e` and `0x13684` establish register pointers, baud selection and 8N1 mode
values. The adapter combines those values into an explicit polled setup; that
combination is new and not hardware qualified. Do not call native serial startup,
which writes native state and creates tasks. Do not drive other DUART output pins.

Ordinary interrupt masking **does not block IPL7 AC_FAIL**. Vector `0x7c` normally
points to `0x275a`, which writes native SafeRAM. The replacement checks the guard:
normal startup continues to the original handler; commissioning goes irreversibly
to a watchdog-serviced halt, never back to a pending write. It discards the private
exception stack on that terminal path. This does not prove electrical power-fail
latency or atomic word stores. Watchdog service uses the native `FF101e` access.

A valid interrupted native-clear journal must retain its original operator-clear
recovery route; early commissioning must not strand it before that branch.

## Memory

Existing persistence: `FW_PROFILE_BASE` 0x430000, `FW_PERSIST_SIZE` 0x600.
Core runtime stays 0x430600 (1024 bytes), ending at0x430a00. Motion runtime uses
[0x430a00,0x431000). Separate commissioning transaction record:
`CM_RECORD_BASE=0x431000`, `CM_RECORD_SIZE=0x800`. This consumes part of the formerly
reserved core expansion; linker/runtime reservations must be explicit.
Commission working context `commission_state=0x431800`, at most 0x800 bytes.
Volatile staged capture `commission_capture=0x435000`, size `CM_CAPTURE_SIZE=0xe00`:
first 0x600 bytes persistence, followed by the pre-write 0x800-byte transaction area.
These are included in the existing reset-preserved reservation 430000..435fff;
working state/capture must explicitly initialize, never trust retained bytes.
Native SafeRAM 438000..43ffff is NEVER written by commissioning.

The parent adapter uses `commission_io_state=0x434100` (at most0xf00 bytes),
`commission_irq_guard=0x434010` and register-save scratch at0x432000. Before kernel
startup only, `0x433000..0x433fff` is the private commissioning stack. The gate
restores native SP before normal startup; the serialized runtime E0 callback then
reuses that stack, while motion state remains below the commissioning record.
The two stack lifetimes cannot overlap. Platform runtime is limited to16 bytes;
E0's saved caller SP at0x434020 is outside the stack, guard and commissioning IO.

Record layout little-endian: magic `S6CI` at0, version1 at4, bytes5..7 zero;
CRC32 of original persistence at8; bytes12..15 zero; original persistence at16
(length FW_PERSIST_SIZE); remaining padding zero; record CRC32 at size-6 covering
[0,size-6); aligned marker at size-2. READY=0xa55a, DONE=0x5aa5.
Only an explicit INIT may publish READY. No persistence word is changed before
record body readback + READY commit. Initialization zeros only FW_PERSIST_SIZE,
verifies it, then atomically replaces READY with DONE. A valid DONE record is never
changed again. Core's existing fw_boot canonical bootstrap creates baseline journals
later: do not implement another journal encoder. READY always blocks normal startup.

For torn record creation, persistence is still its original bytes; restarting may
require the original archive and must validate exact word-prefix compatibility of
the staged-before image and desired record (marker invalidation, body, commit).
For READY resume require original persistence matches the supplied saved archive
exactly, and current persistence is either original or an exact prefix of zeroed
aligned words followed by original words. Never reformat an existing baseline or
acknowledged operation. A bad record CRC with a committed marker fails closed.
A DONE record also fails closed if malformed; no fallback to first install.

First-install refusal detection covers recognizable current records: slot magic,
profile magic, request magic, journal magic, either commit marker, and near-magic
(single-byte-damaged) values at their defined locations. Detection is conservative,
not proof against arbitrary simultaneous corruption. No-force policy.

## Core ports and API

Use fw.h statuses (FW_OK, FAULT, BAD_REQUEST, BUSY, etc).
`uint8_t commission_boot(void)` initializes its volatile context. Returns
`CM_BOOT_CONTINUE=0` only for valid DONE or no commissioning transaction plus
successful existing fw_boot, or a journaled interrupted native clear
(`fw_clear_pending`), which fw_boot faults on but only normal startup's operator
clear branch can complete; `CM_BOOT_SERVICE=1` otherwise. Must not apply a profile,
submit an action, write native state or start tasks. Calls to fw_boot must be
excluded while READY or recognizable invalid commissioning record exists.

`uint32_t commission_process(const uint8_t *request, uint32_t length,
 uint8_t *reply, uint32_t capacity)` consumes/produces payload below.
`int commission_storage_write(uint32_t address,const uint8_t*,uint32_t length)`
permits aligned words ONLY in persistence or transaction record, with readback.
`void commission_watchdog(void)` services hardware watchdog during long loops.
`void commission_identity(uint8_t *out)` supplies the 52-byte identity payload.
Parent supplies these ports. Core uses fw_crc32_update for watchdog-bounded chunks.
No implicit globals.

## Request/reply payload and state

Request: ASCII S6CM, version1, op byte, payload (at most64 bytes).
Reply: ASCII S6CM, version1, same op, fw status byte, payload (at most64 bytes).
No SAS envelope, no SAS mutation ID. Stop-and-wait raw 19200 8N1 on J10 while
commissioning only. Outer framing described below.

Ops:
- 0 INFO empty: payload `<B I H I H H H` (17 bytes): mode, persist_base,
  persist_size, record_base, record_size, capture_size, max_chunk. Modes
  UNKNOWN=0 (candidate only, NOT proof of virgin state), READY=1, DONE=2,
  REFUSED=3. max_chunk=60. INFO has no writes.
- 1 READ: offset:u16le,count:u8 (1..60), returns exact bytes from combined
  persistence+record capture. READ has no writes. DONE/REFUSED readable too.
- 2 STAGE: offset:u16le,data (1..60): stage original archived capture in volatile
  memory with coverage. Conflicting overlapping bytes refused; same bytes accepted.
  Staging does not touch any persistent region.
- 3 INIT: literal ASCII `FIRST-INSTALL` (13 bytes). Requires full staged coverage,
  explicit candidate eligibility, exact original/current comparison or qualified
  transaction-resume comparison above. Only this op modifies persistence. Refuse
  DONE and REFUSED; never overwrite a completed transaction. Success stays in
  service and reports DONE; requires a physical restart for normal boot.
- 4 IDENTITY empty: parent adapter provides exact native ROM identity 32 bytes
  from 0x720 and personality identity 12 bytes from0x10d1c0, followed by CRC32 of
  actual main 0..0x7ffff and personality 0x100000..0x10ffff (52 bytes total).
  Watchdog serviced throughout hashing; cache within service if needed.

READ during torn transaction returns CURRENT bytes. Host must retain original
archive and use it for resume; recapturing current state is not a replacement for
an interrupted original archive. INFO/READ after INIT confirm DONE and zeroed
persistence; no automatic INIT retries after uncertain transport results.

## Framing (parent and host)

Delimiter 0x7e, escape0x7d followed by byte XOR0x20 (escape delimiters/escapes).
Delimited unescaped body is request/reply payload followed by CRC32 little-endian
of that payload. New delimiter closes the previous frame and starts a fresh one;
empty delimiters ignored. Strict max body limits; overflow or bad CRC discarded
and next delimiter resynchronizes. No wall-clock/inter-byte timing framing.
Watchdog always serviced; UART error resets partial frame. Only one bounded TX
frame at a time; RX overrun resets framing. Missing response is uncertain, not
permission to retry INIT. Explicit resume uses the original on-disk archive.

## Host archive contract

Two explicit commands/phases: capture to a NEW local archive; initialize/resume
from that archive with `--first-install`. Archive includes exact capture binary,
SHA256, protocol layout, native identities and actual ROM/personality CRC32,
provenance (serial endpoint/capture method), never pretends CRC32 proves identity.
Must save, fsync and read back before any INIT. Mac wrapper must NOT perform INIT
on remote Linux before the verified archive exists on the Mac: use explicit
separate capture then initialize phases, or a persistent client channel serving
serial on Linux while archive/approval stays local. No silent overwrite, no force.
Hash/metadata/layout mismatches fail before INIT. Exact saved capture is staged.
Existing transaction resume needs same archived original, not a new capture.

## Evidence needed

Compiled CPU state-machine tests cut each modeled aligned persistent write,
including record creation, zeroing and DONE. Show every prefix either can resume
from original archive or faults without changing native bytes/IDs. Test READY never
allows normal startup, DONE never reformats, corrupt/acknowledged records refused,
readback failure and missing/full/conflicting stage coverage. Integration test
real host+framing+compiled core/adapter including boot diversion and UART fakes.
Physical retention, atomicity, electrical UART mapping, timing and full native boot
remain hardware qualification and install_ready must stay false.
