"""S6000 setup option decoding from stock SAS replies (1F/A0) and SafeRAM option reads.

Pure domain module, no I/O. Two data sources feed the web UI's Machine Options view:

- `decode_sas_summary(info, features)` decodes what stock SAS `1F` (Send gaming machine ID
  and information, ROM `0x2853A..0x28606`) and `A0` (Send enabled features, SAS 6.02
  Table 7.14) report on any firmware, including EPROMs without `FW_OP_READ_OPTIONS`.
- `decode_setup_options(raw)` decodes the full Bally ProSlot-6000 setup option table
  from the 232-byte SafeRAM window `[0x43E800, 0x43E8E8)` returned by `FW_OP_READ_OPTIONS`.
  Offsets and encodings match the ROM descriptor table at `0x73BB8` (S6M00002B5) and names
  match Bally Option Card M-00500-0130 Rev X (`vendor-docs/bally-s6000/`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sas_tables import DENOMINATIONS

OPTIONS_BASE = 0x43E800
OPTIONS_SIZE = 232  # [0x43E800, 0x43E8E8); matches firmware/src/fw.h FW_OPTIONS_SIZE

GROUP_PROGRESSIVE = "Progressive"
GROUP_GAMEPLAY = "Gameplay & payouts"
GROUP_SOUND = "Sound & cabinet"
GROUP_HOST = "Host & SAS"
GROUP_VOUCHERS = "Vouchers & bill acceptor"
GROUPS = (GROUP_PROGRESSIVE, GROUP_GAMEPLAY, GROUP_SOUND, GROUP_HOST, GROUP_VOUCHERS)


@dataclass(frozen=True)
class OptionRow:
    code: str
    name: str
    raw: str
    meaning: str
    group: str
    editable: bool = False
    value: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    min_val: int | None = None
    max_val: int | None = None
    needs_reboot: bool = False


def decode_bcd(data: bytes) -> int:
    """Packed big-endian BCD integer, matching ROM `0x3A04E`."""
    value = 0
    for byte in data:
        hi, lo = (byte >> 4) & 0x0F, byte & 0x0F
        if hi > 9 or lo > 9:
            return -1
        value = value * 100 + hi * 10 + lo
    return value


def encode_bcd(value: int, width: int) -> bytes:
    """Pack a non-negative integer into `width` big-endian BCD bytes."""
    if value < 0 or value >= 10 ** (2 * width):
        raise ValueError(f"BCD value {value} out of range for {width} bytes")
    out = bytearray(width)
    for index in range(width - 1, -1, -1):
        value, pair = divmod(value, 100)
        out[index] = ((pair // 10) << 4) | (pair % 10)
    return bytes(out)


def _ascii(data: bytes) -> str:
    text = "".join(chr(b) if 0x20 <= b <= 0x7E else "?" for b in data).strip()
    return text or "0000"


def _int_bits(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return None
    return None


def _fmt_denomination(code: object) -> str:
    if isinstance(code, int) and code in DENOMINATIONS:
        dollars = DENOMINATIONS[code]
        return f"${dollars:,.2f}" if dollars < 1 or dollars % 1 else f"${int(dollars):,}"
    return f"Unknown ({code})"


# Option value tables from Bally ProSlot-6000 Option Card M-00500-0130 Rev X.
_BET_MODE = {1: "Credit game", 3: "Coin to credit"}
_REBET = {
    0: "No rebet, no autospin",
    1: "No rebet, autospin at max credits",
    2: "No rebet, autospin at max coins/credits",
    3: "Rebet, no autospin",
    4: "Rebet, autospin at max credits",
    5: "Rebet, autospin at max coins/credits",
}
_BELL = {
    0: "No bell",
    1: "Rings on all wins",
    2: "Rings on wins ≥ 20",
    3: "Rings on wins ≥ 50",
    4: "Rings on wins ≥ 100",
    5: "Rings on wins ≥ 200",
    6: "Rings on wins causing lockup",
}
_DENOM_OPT = {
    0: "$500", 1: "$250", 2: "$100", 3: "$50", 4: "$25", 5: "$20", 6: "$10", 7: "$5",
    8: "$2", 9: "$1", 10: "50¢", 11: "25¢", 12: "20¢", 13: "10¢", 14: "5¢", 15: "1¢",
    16: "$4", 17: "$2.50",
}
_CHANGE_COIN = {0: "Coins from hopper", 1: "Credits to CREDIT meter"}
_PROGRESSIVE_TYPE = {
    0: "OFF (lockup jackpots)",
    1: "SPL — Serial Progressive Link",
    2: "PPL — Parallel Progressive Link",
    3: "MAPS — Multi-Area Progressive",
    4: "S/MPI — Serial/Multiplex (Mikohn CHAMII+)",
    5: "MPI — Multiplex Progressive Interface",
    6: "SAS Progressive v3.xx",
    7: "OTT — Over the Top",
    8: "SAS Progressive v4.xx",
}
_EXT_JACKPOTS = {0: "None", 1: "One", 2: "Two", 3: "Three", 4: "Four"}
_JP4_SIGNAL = {
    0: "Combination of 2 & 3",
    1: "Combination of 1 & 2",
    2: "Combination of 1 & 3",
    3: "Combination of 1, 2 & 3",
    4: "Discrete serial Mikohn",
}
_OFF_ON = {0: "Off", 1: "On"}
_REEL_SOUND = {0: "Sound from speaker", 1: "Handle solenoid click"}
_EVEN_HANDPAY = {
    0: "Off",
    1: "Even by 10s",
    2: "Even by 100s",
    3: "Even by 1,000s",
    4: "Machine pays to next $1.00",
}
_RESIDUAL = {0: "Must be played", 1: "Forced to attendant pay"}
_VALIDATION = {
    0: "Standard validation",
    1: "Host enhanced validation",
    2: "Host secure enhanced validation",
    3: "Host system validation",
}
_PAYMENT_CONFIG = {
    1: "Hopper",
    2: "Seiko PSA printer",
    3: "Seiko PSA printer and hopper",
}
_EVEN_VOUCHER = {
    0: "Off", 1: "By $1", 2: "By $2", 3: "By $5", 4: "By $10", 5: "By $20",
    6: "By $50", 7: "By $100", 8: "By $200", 9: "By $500", 10: "By $1,000",
}
_CREDIT_SNAP = {0: "Wins added incrementally", 1: "Wins > 20 added as sum"}
_EXTRA_COIN = {0: "Returned to player", 1: "Applied to next bet"}
_PRD_SOUND = {0: "Reel stop sound enabled", 1: "Reel stop sound disabled"}
_GAME_SOUNDS = {0: "Internal device", 1: "External sound board (ProSound)"}
_SAS_BONUS = {0: "Disabled", 1: "SAS 5.01 bonusing", 2: "AFT bonusing", 3: "SAS 5.01 & AFT bonusing"}
_HOST_TYPE = {0: "Disabled", 1: "SAS Host", 2: "SDS Host"}
_COIN_ACCEPTOR_TYPE = {0: "CC-16", 1: "IDX X-20 dual denom"}
_SAS_VERSION = {0: "SAS 5.01", 1: "SAS 6.01"}
_AFT_REG_OPT = {0: "Exit", 1: "Initiate EFT registration", 2: "Cancel EFT registration"}
_MISMATCH = {0: "Must divide evenly by denom", 1: "Accept all, print change voucher"}
_RECEIPT = {0: "Redeemable vouchers only", 1: "Vouchers and info receipts"}
_HOST_RELEASE = {0: "Off", 1: "Host may release lockups"}
_HOST_QUEUE = {0: "No queue", 1: "Multiple lockups queued"}
_SOUND_FLAG = {0: "Muted (SAS sound off)", 1: "Enabled"}


@dataclass(frozen=True)
class _Field:
    code: str
    name: str
    offset: int
    kind: str  # "u8", "bcd2", "bcd3", "ascii4"
    group: str
    labels: Mapping[int, str] | None = None
    unit: str = ""
    zero_text: str | None = None
    editable: bool = False
    min_val: int = 0
    max_val: int = 0
    needs_reboot: bool = False


# Every Bally Option Card entry present in the S6M00002B5 descriptor table (0x73BB8),
# ordered by functional group so progressive and host settings surface first.
# Editable fields and their min/max bounds mirror firmware/src/core.c:option_rules[].
_OPTION_FIELDS: tuple[_Field, ...] = (
    # Progressive
    _Field("02", "Progressive type", 0x05, "u8", GROUP_PROGRESSIVE, _PROGRESSIVE_TYPE,
           editable=True, min_val=0, max_val=8, needs_reboot=True),
    _Field("07", "External jackpots", 0x06, "u8", GROUP_PROGRESSIVE, _EXT_JACKPOTS,
           editable=True, min_val=0, max_val=4, needs_reboot=True),
    _Field("78Lo", "Progressive machine ID (S/MPI)", 0x24, "ascii4", GROUP_PROGRESSIVE,
           editable=True, min_val=0, max_val=9999, needs_reboot=True),
    _Field("18", "Jackpot 4 signal", 0x07, "u8", GROUP_PROGRESSIVE, _JP4_SIGNAL,
           editable=True, min_val=0, max_val=4),
    _Field("06", "SAS progressive group ID", 0x3C, "bcd2", GROUP_PROGRESSIVE, zero_text="0 (none)",
           editable=True, min_val=0, max_val=255),
    _Field("63", "Reset progressive amount", 0x1E, "bcd2", GROUP_PROGRESSIVE, unit="credits", zero_text="0",
           editable=True, min_val=0, max_val=9999),
    _Field("70", "MAPS cabinet ID (1)", 0x30, "bcd2", GROUP_PROGRESSIVE),
    _Field("71", "MAPS cabinet ID (2)", 0x32, "bcd2", GROUP_PROGRESSIVE),
    _Field("72", "MAPS cabinet ID (3)", 0x34, "bcd2", GROUP_PROGRESSIVE),
    # Gameplay & payouts
    _Field("27", "Bet mode", 0x00, "u8", GROUP_GAMEPLAY, _BET_MODE),
    _Field("26", "Rebet / autospin", 0x01, "u8", GROUP_GAMEPLAY, _REBET,
           editable=True, min_val=0, max_val=5),
    _Field("10", "Coin denomination", 0x03, "u8", GROUP_GAMEPLAY, _DENOM_OPT),
    _Field("11", "Change coin / credit", 0x04, "u8", GROUP_GAMEPLAY, _CHANGE_COIN,
           editable=True, min_val=0, max_val=1),
    _Field("08", "Credit snap", 0x9C, "u8", GROUP_GAMEPLAY, _CREDIT_SNAP,
           editable=True, min_val=0, max_val=1),
    _Field("51", "Credit top limit", 0x2A, "bcd3", GROUP_GAMEPLAY, unit="credits",
           editable=True, min_val=0, max_val=99999),
    _Field("56", "Credit collect lockup", 0x18, "bcd3", GROUP_GAMEPLAY, unit="credits",
           editable=True, min_val=0, max_val=99999),
    _Field("58", "Win lockup", 0x36, "bcd3", GROUP_GAMEPLAY, unit="credits",
           editable=True, min_val=0, max_val=99999),
    _Field("57", "SAS lockup", 0x3E, "bcd3", GROUP_GAMEPLAY, unit="credits", zero_text="Off",
           editable=True, min_val=0, max_val=99999),
    _Field("12", "Win lockup credit release", 0x42, "bcd3", GROUP_GAMEPLAY, unit="credits", zero_text="Off",
           editable=True, min_val=0, max_val=99999),
    _Field("54", "Coin hopper pay amount", 0x16, "bcd2", GROUP_GAMEPLAY, unit="coins",
           editable=True, min_val=0, max_val=9999),
    _Field("04", "Even handpay (collect)", 0x28, "u8", GROUP_GAMEPLAY, _EVEN_HANDPAY,
           editable=True, min_val=0, max_val=4),
    _Field("05", "Even handpay (win)", 0x9D, "u8", GROUP_GAMEPLAY, _EVEN_HANDPAY,
           editable=True, min_val=0, max_val=4),
    _Field("15", "Residual credits", 0x2F, "u8", GROUP_GAMEPLAY, _RESIDUAL,
           editable=True, min_val=0, max_val=1),
    _Field("20", "Extra coin", 0x9E, "u8", GROUP_GAMEPLAY, _EXTRA_COIN,
           editable=True, min_val=0, max_val=1),
    _Field("22", "Tokenization", 0x2E, "u8", GROUP_GAMEPLAY, _OFF_ON),
    _Field("01", "Tournament minutes", 0x09, "u8", GROUP_GAMEPLAY, unit="min", zero_text="Off"),
    _Field("03", "Tournament seconds", 0x2D, "u8", GROUP_GAMEPLAY, unit="s", zero_text="Off"),
    _Field("62", "Tournament kitty", 0x1C, "bcd2", GROUP_GAMEPLAY, unit="credits", zero_text="0"),
    # Sound & cabinet
    _Field("68", "Game sounds", 0xA0, "u8", GROUP_SOUND, _GAME_SOUNDS,
           editable=True, min_val=0, max_val=1),
    _Field("66", "PRD reel-stop sound", 0x9F, "u8", GROUP_SOUND, _PRD_SOUND,
           editable=True, min_val=0, max_val=1),
    _Field("38", "Reel stop sound", 0x0B, "u8", GROUP_SOUND, _REEL_SOUND,
           editable=True, min_val=0, max_val=1),
    _Field("09", "Jackpot bell", 0x02, "u8", GROUP_SOUND, _BELL,
           editable=True, min_val=0, max_val=6),
    _Field("47", "Attract feature", 0x0A, "u8", GROUP_SOUND, _OFF_ON,
           editable=True, min_val=0, max_val=1),
    _Field("48", "Tower configuration", 0x0C, "u8", GROUP_SOUND,
           editable=True, min_val=0, max_val=7),
    _Field("80", "Test 2 & 10 enable", 0x08, "u8", GROUP_SOUND, _OFF_ON,
           editable=True, min_val=0, max_val=1),
    _Field("Snd", "SAS sound flag (0x43E8E2)", 0xE2, "u8", GROUP_SOUND, _SOUND_FLAG),
    _Field("Reel", "SAS reel-sound flag (0x43E8E3)", 0xE3, "u8", GROUP_SOUND, _SOUND_FLAG),
    # Host & SAS
    _Field("98", "Primary host", 0xA3, "u8", GROUP_HOST, _HOST_TYPE),
    _Field("99", "Secondary host", 0xA4, "u8", GROUP_HOST, _HOST_TYPE),
    _Field("78Hi", "Primary SAS address", 0x20, "ascii4", GROUP_HOST),
    _Field("17", "SAS version", 0xC6, "u8", GROUP_HOST, _SAS_VERSION),
    _Field("92", "SAS bonusing", 0xA2, "u8", GROUP_HOST, _SAS_BONUS),
    _Field("90", "Host remote jackpot release", 0x45, "u8", GROUP_HOST, _HOST_RELEASE),
    _Field("91", "Host queue", 0x46, "u8", GROUP_HOST, _HOST_QUEUE),
    _Field("23", "GRIPS host", 0xA1, "u8", GROUP_HOST, _OFF_ON),
    _Field("29", "Operator AFT registration", 0xAA, "u8", GROUP_HOST, _AFT_REG_OPT),
    _Field("50Hi", "Machine ID (upper)", 0x0E, "ascii4", GROUP_HOST),
    _Field("50Lo", "Machine ID (lower)", 0x12, "ascii4", GROUP_HOST),
    _Field("19Hi", "Asset number (upper)", 0xAC, "ascii4", GROUP_HOST),
    _Field("19Lo", "Asset number (lower)", 0xB0, "ascii4", GROUP_HOST),
    # Vouchers & bill acceptor
    _Field("61", "Payment device", 0x4B, "u8", GROUP_VOUCHERS, _PAYMENT_CONFIG,
           editable=True, min_val=1, max_val=3),
    _Field("73", "Voucher validation level", 0x49, "u8", GROUP_VOUCHERS, _VALIDATION,
           editable=True, min_val=0, max_val=3),
    _Field("76", "Voucher receipts", 0x4A, "u8", GROUP_VOUCHERS, _RECEIPT,
           editable=True, min_val=0, max_val=1),
    _Field("74", "Voucher expiration", 0x4D, "u8", GROUP_VOUCHERS, unit="days", zero_text="No expiration",
           editable=True, min_val=0, max_val=255),
    _Field("64", "Minimum voucher pay", 0x4E, "bcd2", GROUP_VOUCHERS, unit="credits",
           editable=True, min_val=0, max_val=9999),
    _Field("67", "Maximum voucher pay", 0x50, "bcd3", GROUP_VOUCHERS, unit="credits",
           editable=True, min_val=0, max_val=99999),
    _Field("14", "Even voucher pay", 0x53, "u8", GROUP_VOUCHERS, _EVEN_VOUCHER,
           editable=True, min_val=0, max_val=10),
    _Field("95", "Voucher denom mismatch", 0x48, "u8", GROUP_VOUCHERS, _MISMATCH,
           editable=True, min_val=0, max_val=1),
    _Field("81", "Coin acceptor enable", 0x4C, "u8", GROUP_VOUCHERS, _OFF_ON,
           editable=True, min_val=0, max_val=1),
    _Field("69", "Coin acceptor type", 0xA5, "u8", GROUP_VOUCHERS, _COIN_ACCEPTOR_TYPE),
    _Field("75", "Coins to dispense (float)", 0xA8, "bcd2", GROUP_VOUCHERS, unit="coins"),
    _Field("93", "Bank meter transfer", 0xA6, "u8", GROUP_VOUCHERS, _OFF_ON),
)

_BY_CODE: Mapping[str, _Field] = {f.code: f for f in _OPTION_FIELDS}
EDITABLE_CODES = frozenset(f.code for f in _OPTION_FIELDS if f.editable)


def option_name(code: str) -> str:
    field = _BY_CODE.get(code)
    return field.name if field is not None else f"Option {code}"


def encode_option_write(code: str, value: str) -> bytes:
    """Validate and encode a `FW_OP_SET_OPTION` payload (`offset, len, raw_bytes`)."""
    field = _BY_CODE.get(code)
    if field is None or not field.editable:
        raise ValueError(f"Option {code} cannot be edited from the web UI")
    text = value.strip()
    if field.kind == "ascii4":
        if not text.isdigit() or not (1 <= len(text) <= 4):
            raise ValueError(f"Option {code} ({field.name}): enter 0 to 9999")
        raw_bytes = f"{int(text):04d}".encode("ascii")
        return bytes((field.offset, 4)) + raw_bytes
    if not text.isdigit():
        raise ValueError(f"Option {code} ({field.name}): enter a whole number ({field.min_val}–{field.max_val})")
    num = int(text)
    if not (field.min_val <= num <= field.max_val):
        raise ValueError(f"Option {code} ({field.name}): value must be between {field.min_val} and {field.max_val}")
    if field.kind == "u8":
        return bytes((field.offset, 1, num))
    if field.kind == "bcd2":
        return bytes((field.offset, 2)) + encode_bcd(num, 2)
    if field.kind == "bcd3":
        return bytes((field.offset, 3)) + encode_bcd(num, 3)
    raise ValueError(f"unknown option kind {field.kind!r}")


def decode_setup_options(raw: bytes) -> tuple[OptionRow, ...]:
    """Decode all S6000 setup options from the 232-byte SafeRAM block at `0x43E800`."""
    if len(raw) < OPTIONS_SIZE:
        raise ValueError(f"setup options block is {len(raw)} bytes, expected {OPTIONS_SIZE}")
    rows: list[OptionRow] = []
    for f in _OPTION_FIELDS:
        choices = (tuple((str(k), v) for k, v in sorted(f.labels.items()))
                   if f.editable and f.labels is not None else ())
        min_v = f.min_val if f.editable else None
        max_v = f.max_val if f.editable else None
        if f.kind == "ascii4":
            text = _ascii(raw[f.offset:f.offset + 4])
            if f.code == "78Hi":
                meaning = "None" if text == "0000" else f"SAS address {int(text)}" if text.isdigit() else text
            elif f.code == "78Lo":
                meaning = "Not set" if text == "0000" else f"Controller ID {int(text)}" if text.isdigit() else text
            else:
                meaning = text
            rows.append(OptionRow(f.code, f.name, text, meaning, f.group,
                                  editable=f.editable, value=text, choices=choices,
                                  min_val=min_v, max_val=max_v, needs_reboot=f.needs_reboot))
            continue
        if f.kind == "u8":
            val = raw[f.offset]
            raw_str = f"{val:04d}"
        elif f.kind == "bcd2":
            val = decode_bcd(raw[f.offset:f.offset + 2])
            raw_str = f"{val:04d}" if val >= 0 else raw[f.offset:f.offset + 2].hex().upper()
        elif f.kind == "bcd3":
            val = decode_bcd(raw[f.offset:f.offset + 3])
            raw_str = f"{val:06d}" if val >= 0 else raw[f.offset:f.offset + 3].hex().upper()
        else:
            raise ValueError(f"unknown option kind {f.kind!r}")
        if val < 0:
            meaning = f"Invalid BCD ({raw_str})"
        elif f.labels is not None:
            meaning = f.labels.get(val, f"Unknown ({val})")
        elif val == 0 and f.zero_text is not None:
            meaning = f.zero_text
        elif f.unit:
            meaning = f"{val:,} {f.unit}"
        else:
            meaning = str(val)
        rows.append(OptionRow(f.code, f.name, raw_str, meaning, f.group,
                              editable=f.editable, value=str(val) if val >= 0 else "",
                              choices=choices, min_val=min_v, max_val=max_v, needs_reboot=f.needs_reboot))
    return tuple(rows)


def decode_sas_summary(info: Mapping[str, object] | None,
                       features: Mapping[str, object] | None = None) -> tuple[OptionRow, ...]:
    """Decode setup flags exposed by stock SAS `1F` (info) and `A0` (enabled features)."""
    rows: list[OptionRow] = []
    if info is not None:
        opts = _int_bits(info.get("game_options"))
        if opts is not None:
            prog_bits = opts & 0x07
            if prog_bits == 0:
                prog_meaning = "Off (Option 07 = 0 external jackpots)"
            elif prog_bits == 1:
                prog_meaning = "External jackpots set, Option 02 = Off"
            elif prog_bits == 3:
                prog_meaning = "External progressive enabled (e.g. S/MPI, SPL, PPL)"
            elif prog_bits == 7:
                prog_meaning = "SAS progressive enabled"
            else:
                prog_meaning = f"Bits 0–2 = {prog_bits}"
            rows.append(OptionRow("07/02", "Progressive", f"0x{opts:04X}", prog_meaning, GROUP_PROGRESSIVE))
            rows.append(OptionRow("27", "Bet mode", "On" if opts & 0x08 else "Off",
                                  "Credit game" if opts & 0x08 else "Non-credit", GROUP_GAMEPLAY))
            rows.append(OptionRow("09", "Jackpot bell", "On" if opts & 0x20 else "Off",
                                  "Enabled" if opts & 0x20 else "No bell", GROUP_SOUND))
            rows.append(OptionRow("01", "Tournament mode", "On" if opts & 0x80 else "Off",
                                  "Active" if opts & 0x80 else "Off", GROUP_GAMEPLAY))
        if "progressive_group" in info:
            pg = info["progressive_group"]
            rows.append(OptionRow("06", "SAS progressive group", str(pg),
                                  "None" if pg == 0 else f"Group {pg}", GROUP_PROGRESSIVE))
        if "denomination" in info:
            denom = info["denomination"]
            rows.append(OptionRow("10", "Denomination", str(denom), _fmt_denomination(denom), GROUP_GAMEPLAY))
        paytable = str(info.get("paytable") or "").strip()
        base_pct = str(info.get("base_percent") or "").strip()
        if paytable or base_pct:
            pct_text = f"{int(base_pct) / 100:.2f}%" if base_pct.isdigit() else base_pct
            meaning = f"{paytable} ({pct_text})" if paytable and pct_text else (paytable or pct_text)
            rows.append(OptionRow("1F", "Paytable / base payback", paytable, meaning, GROUP_GAMEPLAY))
    if features is not None:
        f1 = _int_bits(features.get("features_1"))
        f2 = _int_bits(features.get("features_2"))
        f3 = _int_bits(features.get("features_3"))
        if f1 is not None and f2 is not None:
            enabled = []
            if f2 & 0x10:
                enabled.append("AFT")
            if f1 & 0x04:
                enabled.append("AFT bonus")
            if f1 & 0x08:
                enabled.append("Legacy bonus")
            if f1 & 0x80:
                enabled.append("Ticket redemption")
            if f1 & 0x20:
                enabled.append("Validation ext")
            if f2 & 0x04:
                enabled.append("Extended meters")
            if f2 & 0x08:
                enabled.append("Component auth")
            if f2 & 0x80:
                enabled.append("Multi SAS progressive")
            if f3 is not None and (f3 & 0x01):
                enabled.append("Enhanced meters")
            raw_hex = f"{f1:02X} {f2:02X}" + (f" {f3:02X}" if f3 is not None else "")
            rows.append(OptionRow("A0", "SAS enabled features", raw_hex,
                                  ", ".join(enabled) if enabled else "None", GROUP_HOST))
    return tuple(rows)


def default_options_block(*, progressive_type: int = 0, external_jackpots: int = 0,
                          sas_address: int = 1, progressive_id: int = 0) -> bytes:
    """Realistic 232-byte S6000 SafeRAM options block for fakes and tests."""
    buf = bytearray(OPTIONS_SIZE)
    buf[0x00] = 1  # Opt 27: Credit game
    buf[0x01] = 1  # Opt 26: No rebet, autospin at max credits
    buf[0x02] = 6  # Opt 09: Bell on lockup
    buf[0x03] = 9  # Opt 10: $1
    buf[0x04] = 1  # Opt 11: Credits to CREDIT meter
    buf[0x05] = progressive_type  # Opt 02
    buf[0x06] = external_jackpots  # Opt 07
    buf[0x0A] = 1  # Opt 47: Attract on
    buf[0x0E:0x16] = b"00000000"  # Opt 50 Machine ID
    buf[0x18:0x1B] = encode_bcd(800, 3)  # Opt 56 Credit Collect Lockup
    buf[0x20:0x28] = f"{sas_address:04d}{progressive_id:04d}".encode("ascii")  # Opt 78
    buf[0x2A:0x2D] = encode_bcd(800, 3)  # Opt 51 Credit Top Limit
    buf[0x36:0x39] = encode_bcd(800, 3)  # Opt 58 Win Lockup
    buf[0x49] = 3  # Opt 73: Host system validation
    buf[0x4B] = 2  # Opt 61: Seiko PSA printer
    buf[0x4C] = 1  # Opt 81: Coin acceptor enabled
    buf[0x50:0x53] = encode_bcd(8000, 3)  # Opt 67 Max Voucher Pay
    buf[0xA0] = 1  # Opt 68: External sound board
    buf[0xA2] = 3  # Opt 92: SAS 5.01 & AFT bonusing
    buf[0xA3] = 1  # Opt 98: SAS Host
    buf[0xA4] = 0  # Opt 99: Disabled
    buf[0xA8:0xAA] = encode_bcd(125, 2)  # Opt 75
    buf[0xAC:0xB4] = b"00012345"  # Opt 19 Asset Number
    buf[0xC6] = 1  # Opt 17: SAS 6.01
    buf[0xE2] = 1  # SAS sound enabled
    buf[0xE3] = 1  # SAS reel sound enabled
    return bytes(buf)
