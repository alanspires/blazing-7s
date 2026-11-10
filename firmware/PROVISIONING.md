# First-install SRAM commissioning

**Commissioning service implemented offline; not hardware-qualified.** The user
selected an embedded pre-boot service rather than a temporary ROM pair or external
powered SRAM programmer. The later [limited live bring-up](../notes/firmware-bringup.md)
reached normal runtime without commissioning INIT. It did not qualify this service's
physical capture/initialization transport or electrical failure behavior.

## Why a pre-boot service is necessary

The original extension boot hook at `0x155c` calls `platform_boot` and `fw_boot`.
Unrecognized persistence faults into native `0x38a06(0x0f)`, then the permanent
watchdog-serviced indication loop at `0x3864a`. It never reaches native serial
initialization at `0x1aae`. A normal private E0 command therefore cannot repair
this dependency. Direct calls to INFO in a CPU test are not evidence of a reachable
serial recovery interface on the machine.

The new `commission_gate` is earlier, at `0xa58`: after scratch/ROM tests, before
kernel initialization and native SafeRAM tests. It either resumes the original
startup exactly or stays in a limited polled J10 service. No gameplay tasks run
inside that service, and completing initialization does **not** start gameplay.
Normal startup requires a restart. A valid interrupted native-clear journal keeps
its original operator-clear recovery route; commissioning does not replace it.

This is **not a read-only whole reset**. Original code before the gate updates the
seed words at `0x43fff8..0x43ffff` and the reset counter at `0x4385e4..0x4385e7`.
Commissioning's no-native-writes boundary starts at the gate; it does not undo or
claim to eliminate those existing reset writes.

## What is archived and initialized

The exact capture consists of:

- Existing extension persistence: `0x430000..0x4305ff` (profiles and request journals).
- Commissioning record area: `0x431000..0x4317ff`, formerly reserved expansion.

It is not a full-machine SRAM backup. Native credit, accounting and configuration
bytes in SafeRAM are outside both ranges. Working context and the private stack
are also outside the archived ranges.

The host reads the capture twice and requires identical bytes. An archive includes
these bytes, SHA-256 digests, format/layout, native ROM/personality identity bytes,
the two actual image CRC32s and capture provenance. CRC32 is change detection,
not cryptographic proof of image or physical-machine identity.

The `tools/sas` wrapper has **two separate phases**. Capture writes a new local archive,
fully syncs it, and reads it back; existing files and symlinks are never replaced.
Initialize reads and verifies that same archive locally, then feeds it through
SSH. The remote process parses it completely before opening the serial port.
There is no remote-only archive followed by an implicit initialization.

Commands for a later, separately approved hardware qualification:

```text
tools/sas commission-info
tools/sas commission-capture ORIGINAL.json
tools/sas commission-initialize ORIGINAL.json --first-install
```

These use raw 19200 8N1 on J10 **only while the commissioning service is active**,
not normal SAS wakeup-bit framing. They are implemented by
`../tools/sas_commission.py`. The wrapper requires `SAS_HOST` (SSH destination of
the Linux SAS host) and `SAS_PORT` (the adapter's serial device there), and exits
before any SSH call without them; `SAS_DIR` optionally names the remote mirror
(default `blazing-7s`). Setup: [`notes/sas-j10.md`](../notes/sas-j10.md),
**SAS host setup**. Run directly on the SAS host, `sas_commission.py` needs
`--port <device>`; it has no default. No special password is added, but explicit first-install intent
and state checks are mandatory.

## Transaction and refusal policy

Only INIT after complete staging of the archived capture can alter persistence.
It checks exact current/archived bytes, refuses existing or recognizable damaged
extension state, and then:

1. Writes and verifies a separate transaction record containing the original
   persistence. It commits READY with one aligned marker word.
2. Zeros only the extension persistence, checking each aligned word.
3. Commits DONE with one aligned marker word. It remains in the serial service.

READY blocks normal startup. After a restart, the original archive can resume an
exact interrupted record-write or zeroing prefix. Mismatched bytes, corrupt
committed records and unrelated changes fail closed. The canonical baseline
journals are created by the existing `fw_boot` implementation, not a second encoder.
A completed record is never reformatted; later acknowledged request IDs are retained.

A missing INIT reply means **uncertain**, not permission to send another INIT
with a new archive. No automatic retry occurs. An explicit invocation with the
same original archive confirms DONE without another INIT, or resumes the verified
transaction. Keep the original file: a new capture of partially modified storage
is not its replacement. REFUSED requires investigation, not a force flag or a
machine-wide clear.

Recognition is deliberately conservative: known/near-matching magic fields and
commit markers are evidence against first-install formatting. Missing magic or
bad CRC alone cannot prove that no older request ID ever existed. Wholly
unrecognized bytes still require established first-install intent; the software
cannot recover history destroyed by arbitrary prior corruption.

## AC-fail, watchdog and serial limits

`SR=2700` does not mask edge-triggered IPL7 AC_FAIL. The stock handler writes native
SafeRAM, so the replacement vector uses an explicitly initialized guard. During
commissioning it stops irreversibly in a watchdog-serviced halt and never returns
to a pending persistence write. Outside commissioning it retains the native path.
This is a software boundary, not proof of electrical write inhibition.

J10 is U39/DUART2 B through U40: schematic AS-03356-0424 sheets14-19 and14-8 trace
TXD4/RXD4 to pins3/5. The adapter uses explicit byte-register initialization, no
RTOS calls, no ordinary serial startup and no unrelated DUART output changes.
Frames have delimiter/escape encoding, bounded buffers and CRC32; UART errors
invalidate partial input. The host imposes a total reply deadline and byte limit.
READ does not echo its offset: delayed same-op replies are a residual transport
limitation. Two capture passes plus firmware's exact preimage check prevent such
a malformed archive from authorizing a different destructive initialization.

## Existing mechanisms are not substitutes

- Initialized original reset clears only `0x400000..0x412871`, leaving the reserved
  region untouched. Uninitialized original reset clears through `0x437fff` and
  takes the native initialization path. Manipulating initialization markers or
  reverting ROMs is not a qualified credits-preserving provisioning procedure.
- Native SafeRAM-clear routines at `0x387b6`, `0x38850` and `0x388e4` begin at
  `0x438000`/`0x438002`. They miss the reservation and alter native SafeRAM.
- The existing bounded J3/SAS audit established no arbitrary SRAM capture/write
  command in the stock firmware. See `../notes/j3-hhu-protocol.md` and the decoded
  command catalogue in `../tools/sas_commands.py`. This is not an exhaustive proof
  against every undiscovered interface.
- The all-FD `DS1265Y(RW)2.BIN` described in `../roms/README.md` is a failed read,
  not a usable SRAM archive. Removing volatile SRAM or disconnecting its power is
  not an archive method.

## Evidence and remaining qualification

`COMMISSIONING-CONTRACT.md` defines the memory, protocol and boot boundary.
`tests/test_commission.py` exercises compiled initialization and interruption
behavior; `tests/test_commission_io.py` covers the actual hooks and adapter against
register-memory UART inputs. `../tools/test_sas_commission_firmware.py` joins the
real host archive workflow to compiled firmware. Host filesystem and SSH-boundary
tests are in `../tools/test_sas_commission.py` and
`../tools/test_sas_commission_wrapper.py`. Recorded gate results belong in
`README.md`; test existence alone is not a pass claim.

Remaining physical qualification, not closed by the limited live bring-up:

- [ ] Installed JW4 electrical selection, battery retention and SRAM address decoding.
  The schematic's shared VBJ/VBB supply is not an installed-board measurement.
- [ ] Real word-write atomicity, AC-fail timing/write inhibition, watchdog margins and
  J10's proposed 8N1 initialization/transport behavior.
- [ ] Whole native boot/accounting/recovery and game behavior; offline CPU tests are
  not a complete machine simulator. Normal-game stack and ISR assumptions remain
  separate from the pre-kernel commissioning stack. Approved calibration checks
  use the stock fatal CPU halt, not reel de-energization; fault-latch behavior and
  a credits/request-history-preserving recovery procedure still need qualification.
- [ ] Actual T48/blank-chip bench qualification. [PROGRAMMING.md](PROGRAMMING.md)
  records the user's Winbond W27C02/DA85 spares and matching advertised support,
  plus the original dump's ST reference, exact 256 KiB packaging and successful
  full-lane readback comparisons after user programming. Actual markings/speed,
  authenticity, installed algorithm and electrical/timing margins remain unverified.
  Preserve original chips and dumps; do not erase originals.

The commissioning implementation removes a missing software path, not these
physical qualification requirements. Build manifests must remain
`install_ready: false`.
