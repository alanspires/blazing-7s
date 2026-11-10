---
paths:
  - "firmware/src/commission*"
  - "firmware/tests/test_commission*.py"
  - "tools/*commission*.py"
  - "firmware/COMMISSIONING-CONTRACT.md"
  - "firmware/PROVISIONING.md"
---
# Pre-boot commissioning

See `firmware.md` for firmware-wide constraints and `firmware-remote-control.md`
for runtime E0 and canonical persistence journals. This service is first-install
recovery, not a runtime formatter or a substitute for native operator clear.

## Reachability and safety boundary

- Keep `commission_gate` before kernel/SafeRAM initialization: the later extension
  boot fault occurs before native serial startup, so E0 cannot repair it.
  The commissioning patches in `firmware/patches.json` wire reset, gate and IPL7;
  direct calls to `commission_process` do not prove boot-time reachability.
- Restore saved registers, SR, native SP and the displaced LEA on continuation.
  Service mode starts no gameplay tasks, applies no profile and submits no action.
  Successful INIT stays in service; normal startup requires a restart.
- Commissioning never writes native SafeRAM or credits. This is not a read-only
  whole reset: native seed/reset-counter writes precede the gate.
- READY and recognizable invalid commissioning records must exclude `fw_boot`.
  Otherwise preserve `normal_boot`'s `fw_clear_pending` allowance so a valid
  interrupted native clear can reach its original operator recovery branch.
- Explicitly initialize retained working state and IRQ guard. The private stack
  is reused by E0 only after commissioning has ended; its lifetimes cannot overlap.
  Preserve the reservation boundaries in `firmware/link.ld` when changing state.
- SR interrupt masking cannot block IPL7 AC_FAIL. In commissioning, the guarded
  replacement handler discards the exception stack and irreversibly watchdog-halts;
  it must never return to a pending write. Normal startup retains the native handler.
- Use polled J10 U39/DUART2 B setup, not native serial startup or RTOS tasks;
  do not drive unrelated DUART outputs. Service the watchdog in polling, writes
  and bounded CRC/hash work, including the terminal power-fail path.

## Archive before mutation

- Keep capture and initialize/resume separate explicit commands. UNKNOWN is only
  a candidate, never proof of virgin storage; initialization requires established
  first-install intent (`--first-install` and the firmware INIT literal).
- Capture extension persistence plus the pre-write transaction area, not whole
  SRAM. Require two identical READ passes and stable INFO. Preserve exact bytes,
  SHA-256 integrity, protocol/layout, native identities, actual image CRC32s and
  provenance. CRC32 detects change; it does not authenticate a physical machine.
- Never replace an archive, including a symlink. Publish a new file without
  clobbering, sync file and directory, and read it back. Loading requires a regular
  non-symlink file, durable rereads and canonical schema/digest validation.
- In `tools/sas`, keep remote capture piped to local archive-save and local
  archive-verify piped to remote initialize. The remote CLI parses the entire
  archive before opening serial, even if SSH starts while local verification fails.
  Keep SSH streams free of a PTY; rsync the current host modules before running.
  Never initialize remotely and only afterward fetch the archive to the local computer.
- Stage the exact saved capture with full coverage. Equal overlaps are accepted;
  a conflicting overlap rejects the whole chunk without partial staging.
  INFO/READ are nonmutating and STAGE changes volatile memory only.

## Transaction, refusal and recovery

- Only INIT may publish READY. Invalidate the record marker, write and read back
  the original-persistence record body and CRC, then commit READY with an aligned
  word. No persistence word changes before READY. Zero only `FW_PERSIST_SIZE`,
  verify it, then commit DONE. Check every written word before advancing.
- Never rewrite valid DONE or erase acknowledged request history. Existing or
  recognizable damaged slot/profile/request/journal magic (including near-magic)
  and commit markers refuse first-install formatting. Bad committed records fail
  closed, including DONE. No force flag or fallback to formatting on bad CRC.
  Recognition is conservative, not proof against arbitrary prior corruption.
- Torn creation requires unchanged original persistence and exact ordered
  word-prefix compatibility with the archived transaction-area preimage.
  READY resume requires the record's original to match the archive exactly and
  persistence to be zeroed aligned words followed by untouched original words.
  Unrelated changes refuse; a current READ is not a replacement original archive.
- Write/readback failure latches REFUSED for that session. Restart and use the same
  original archive for qualified recovery; never silently retry INIT.
- Missing or malformed INIT responses are uncertain outcomes. Explicit subsequent
  initialize may confirm matching DONE without sending INIT again. Do not require
  DONE persistence to remain zero: later `fw_boot` and acknowledged operations
  legitimately change it. Immediately after a successful INIT, verify DONE,
  zeroed persistence and the exact completed record.
- Let `fw_boot` create baseline journals; do not introduce another baseline encoder.

## Wire compatibility and evidence

- Keep host and firmware layout, status, record and protocol validation in lockstep.
  Commissioning is raw 19200 8N1 stop-and-wait S6CM, not SAS wakeup framing or E0
  mutation IDs. Frames use delimiter/escape encoding and little-endian CRC32.
- Bound RX/TX; discard bad CRC, overflow and UART-error partial frames and
  resynchronize at a delimiter, never inter-byte timing. Keep one bounded TX frame.
  Host replies have a total deadline and byte budget. READ does not echo offsets:
  delayed same-op replies remain a limitation, mitigated by double capture and
  firmware exact-preimage checks, not by speculative retries.
- Changes must retain compiled cut-point coverage of every modeled aligned write,
  readback failures, staging conflicts/incompleteness, READY boot exclusion,
  damaged-state refusal and DONE/history preservation. Exercise actual gate/IRQ
  hooks and UART adapter, real host-to-compiled-firmware flow, filesystem durability
  and wrapper SSH/rsync boundaries, not just a Python service model.
- Register-memory UART, atomic-word cut models and already-taken exceptions do not
  qualify electrical mapping/timing, SRAM retention, write atomicity, AC-fail
  latency, watchdog margins or whole native boot. Limited normal-runtime bring-up
  did not qualify commissioning INIT.
