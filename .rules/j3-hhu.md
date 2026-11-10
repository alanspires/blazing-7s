---
paths:
  - "tools/bsg_frame.py"
  - "tools/test_bsg_frame.py"
  - "notes/j3-hhu-protocol.md"
---

# J3 (HHU) link

The MPU's J3 header (DUART1 channel B) and its Bally Secondary Game (BSG) framing, reverse engineered from S6M00002B5. `notes/j3-hhu-protocol.md` is the record; `tools/bsg_frame.py` is the frame codec.

## Codec invariants

- The codec mirrors the ROM's `bsg_send_frame` / `bsg_rx_byte`: payload `[cmd][seq][data...]`, CRC-16/MODBUS appended low byte first, every byte `>= 0xFE` escaped as `0xFE, b & 0x7F`, terminated by `0xFF`. Do not "fix" the escape into a conventional XOR scheme; the receiver sets bit 7 of the byte after `0xFE`.
- `test_bsg_frame` checks `crc16_modbus` against the CRC nibble tables in `roms/s6000_main_S6M00002B5.bin`. Keep that ROM-derived oracle; do not replace it with hard-coded check values.
- Direction is bit 7 of the command: MPU→device clear, device→MPU set; the ROM rejects incoming frames without it.

## Gotchas

- On this machine DS1 SW4–SW6 are all ON, so neither the BSG nor the SED receiver is selected: J3 carries the generic progressive-style `0x01`-framed protocol, not BSG. A BSG frame sent to this machine as configured will not be answered. Use SAS on J10 for general access (`.rules/sas-host.md`).
- J3 is 9600 8N1 at RS-232 levels on this board (U70 is a MAX1406); the IDC10-to-DB9 ribbon makes it a DTE, so a PC needs a null modem.
- Link-layer timing and the queued application frames are inferred from code, not seen on a wire. Keep the note's **verified** / **inferred** markers accurate when adding findings.
