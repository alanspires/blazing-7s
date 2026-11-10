"""Frame codec for the Bally S6000 J3 (HHU) link, as implemented by bsg_send_frame and
bsg_rx_byte in the S6M00002B5 main program. See notes/j3-hhu-protocol.md."""

END = 0xFF
ESCAPE = 0xFE


def crc16_modbus(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def encode(payload: bytes) -> bytes:
    """payload = cmd, seq, data...; returns the bytes to put on the wire."""
    crc = crc16_modbus(payload)
    body = payload + bytes((crc & 0xFF, crc >> 8))
    out = bytearray()
    for b in body:
        out += bytes((ESCAPE, b & 0x7F)) if b >= ESCAPE else bytes((b,))
    out.append(END)
    return bytes(out)


class CrcError(ValueError):
    pass


def decode(frame: bytes) -> bytes:
    """frame = wire bytes up to and including the 0xFF terminator; returns the payload."""
    if not frame or frame[-1] != END:
        raise ValueError("frame must end with 0xFF")
    body = bytearray()
    high = 0
    for b in frame[:-1]:
        if b == ESCAPE:
            high = 0x80
            continue
        body.append(b | high)
        high = 0
    payload, got = bytes(body[:-2]), body[-2] | body[-1] << 8
    if crc16_modbus(payload) != got:
        raise CrcError(f"crc {got:04x} != {crc16_modbus(payload):04x}")
    return payload
