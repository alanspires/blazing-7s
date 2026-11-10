"""Every long poll the S6000 firmware accepts, with what it sends and what comes back.

The command list is the firmware's own: the table at 0x744CA in S6M00002B5 that `sas_rx_byte`
looks commands up in (test_sas_commands checks it against the ROM). Layouts come from IGT SAS
6.02 (section or table cited per command) or, for the EFT polls 6.02 dropped, from SASTest's
command definitions in docs/sas/sastest-simulator-2021/sastest.ini.

Encodings follow §2.2.3: BCD and ASCII most significant byte first, binary least significant
byte first. A variable-length message has one binary length byte after the command.
"""

from __future__ import annotations

import string
from dataclasses import dataclass

from sas_tables import METERS

BCD = "bcd"  # number, packed BCD
DIGITS = "digits"  # BCD kept as its digit string: dates, times, validation numbers
BIN = "bin"  # unsigned integer, LSB first
BITS = "bits"  # binary flags, shown in hex
ASCII = "ascii"
HEX = "hex"  # opaque bytes
TEXT = "text"  # ASCII, or bytes given as 0x... hex

COUNTED = "counted"  # size: a 1-byte length, then that many bytes
REST = "rest"  # size: everything left in the message
TABLE_C7 = "c7"  # size: Table C-7's minimum size for the preceding meter code (long poll 2F)


@dataclass(frozen=True)
class Field:
    name: str
    size: int | str
    enc: str
    help: str = ""


@dataclass(frozen=True)
class Repeat:
    """The fields inside, repeated to the end of the message."""
    fields: tuple[Field, ...]


@dataclass(frozen=True)
class Command:
    code: int
    name: str
    title: str
    kind: str  # §2.2.2: R (no data, no CRC), S, M (game number), G
    request: tuple = ()
    reply: tuple | None = ()  # None: the machine ACKs or NACKs
    request_length: bool = False  # variable length: a length byte follows the command
    reply_length: bool = False
    source: str = ""
    # Values that make the command only read, or None when it changes the machine; R polls
    # whose answer starts a process or is deleted once acknowledged are None too.
    read: tuple[str, ...] | None = None

    @property
    def crc(self) -> bool:
        return self.kind != "R"


F = Field  # a short name keeps the command table readable
GAME = F("game", 2, BCD, "game number, 0 = whole machine")
ACK = None  # Command.reply of a poll the machine only ACKs or NACKs
READ_METERS = ("0", "0", "1", "5", "0x0C")  # game 0: coin in, coin out, games played, credits


def meter4(name):
    return F(name, 4, BCD)


INFO_FIELDS = (
    F("game_id", 2, ASCII), F("additional_id", 3, ASCII), F("denomination", 1, BIN),
    F("max_bet", 1, BIN), F("progressive_group", 1, BIN), F("game_options", 2, BITS),
    F("paytable", 6, ASCII), F("base_percent", 4, ASCII),
)
EFT_LOAD = (F("transaction", 1, BIN, "transaction number"), F("ack", 1, BIN, "0 = request, 1 = confirm"),
            F("credits", 4, BCD))
EFT_LOAD_REPLY = (F("transaction", 1, BIN), F("ack", 1, BIN), F("status", 1, BIN), F("credits", 4, BCD))
EFT_CASHOUT = EFT_LOAD[:2]
EFT_CASHOUT_REPLY = EFT_LOAD_REPLY[:3] + (F("credits", 5, BCD),)
SASTEST = "SASTest sastest.ini (EFT, not in SAS 6.02)"

_BILLS = {0x31: 1, 0x32: 2, 0x33: 5, 0x34: 10, 0x35: 20, 0x36: 50, 0x37: 100, 0x38: 500,
          0x39: 1000, 0x3A: 200, 0x3B: 25, 0x3C: 2000, 0x3E: 2500, 0x3F: 5000, 0x40: 10000,
          0x41: 20000, 0x42: 25000, 0x43: 50000, 0x44: 100000, 0x45: 250}

_TOGGLES = {  # Table 7.4a: type S, no data, ACK or NACK
    0x01: ("shutdown", "Shutdown (lock out play)"),
    0x02: ("startup", "Startup (enable play)"),
    0x03: ("sound-off", "Sound off (all sounds disabled)"),
    0x04: ("sound-on", "Sound on (all sounds enabled)"),
    0x05: ("reel-sounds-off", "Reel spin or game play sounds disabled"),
    0x06: ("bills-on", "Enable bill acceptor"),
    0x07: ("bills-off", "Disable bill acceptor"),
}

_SINGLE_METERS = {  # Table 7.1b: one 4-byte BCD meter
    0x10: ("cancelled-credits", "Total cancelled credits meter"),
    0x11: ("coin-in", "Total coin in meter"),
    0x12: ("coin-out", "Total coin out meter"),
    0x13: ("drop", "Total drop meter"),
    0x14: ("jackpot", "Total jackpot meter"),
    0x15: ("games-played", "Games played meter"),
    0x16: ("games-won", "Games won meter"),
    0x17: ("games-lost", "Games lost meter"),
    0x1A: ("credits", "Current credits"),
    0x20: ("bill-dollars", "Total dollar value of bills meter"),
    0x2A: ("true-coin-in", "True coin in (coins)"),
    0x2B: ("true-coin-out", "True coin out (coins)"),
    0x2C: ("hopper-level", "Current hopper level (coins)"),
    0x46: ("bill-credits", "Credit amount of all bills accepted"),
    0x49: ("stacker-bills", "Number of bills currently in the stacker"),
    0x4A: ("stacker-credits", "Total credit amount of all bills currently in the stacker"),
}

_GAME_METERS = {  # type M: game number in, game number and 4-byte BCD meters out
    0x2D: ("handpaid-cancelled", "Total hand paid cancelled credits", ("handpaid_credits",), "Table 7.6.2"),
    0x52: ("game-meters", "Game n meters", ("coin_in", "coin_out", "jackpot", "games_played"), "Table 7.6.4"),
    0x83: ("progressive-wins", "Cumulative progressive wins", ("progressive_wins",), "Table 10.6"),
    0x95: ("tournament-games-played", "Tournament games played", ("games",), "§11"),
    0x96: ("tournament-games-won", "Tournament games won", ("games",), "§11"),
    0x97: ("tournament-credits-wagered", "Tournament credits wagered", ("credits",), "§11"),
    0x98: ("tournament-credits-won", "Tournament credits won", ("credits",), "§11"),
    0x99: ("tournament-meters", "Tournament meters 95 through 98",
           ("games_played", "games_won", "credits_wagered", "credits_won"), "§11"),
    0x9A: ("legacy-bonus-meters", "Legacy bonus meters", ("deductible", "non_deductible", "wager_match"),
           "Table 13.6"),
}

_COMMANDS = [
    *(Command(code, name, title, "S", reply=ACK, source="Table 7.4") for code, (name, title) in _TOGGLES.items()),
    Command(0x08, "configure-bills", "Configure bill denominations", "S",
            (F("bills", 4, BITS, "bitmap, bit 0 = $1 (Table 7.5)"),
             F("action", 1, BITS, "bit 0: 1 = keep acceptor enabled after each bill")), ACK, source="Table 7.5"),
    Command(0x09, "enable-game", "Enable/disable game n", "M",
            (GAME, F("enable", 1, BIN, "1 = enable, 0 = disable")), ACK, source="Table 7.6.1"),
    Command(0x0E, "real-time-events", "Enable/disable real time event reporting", "S",
            (F("enable", 1, BIN, "1 = enable, 0 = disable"),), ACK, source="Table 12.1"),
    Command(0x0F, "meters-10-15", "Send meters 10 through 15", "R",
            read=(), reply=tuple(map(meter4, ("cancelled_credits", "coin_in", "coin_out", "drop", "jackpot",
                                     "games_played"))), source="Table 7.2a"),
    *(Command(code, name, title, "R", read=(), reply=(meter4("value"),), source="Table 7.1")
      for code, (name, title) in _SINGLE_METERS.items()),
    Command(0x18, "games-since", "Games since last power up and last slot door closure", "R",
            read=(), reply=(F("since_power_up", 2, BCD), F("since_door_closed", 2, BCD)), source="Table 7.7"),
    Command(0x19, "meters-11-15", "Send meters 11 through 15", "R",
            read=(), reply=tuple(map(meter4, ("coin_in", "coin_out", "drop", "jackpot", "games_played"))),
            source="Table 7.2b"),
    Command(0x1B, "handpay-info", "Send handpay information", "R",
            read=(), reply=(F("progressive_group", 1, BIN), F("level", 1, BIN), F("amount", 5, BCD),
                   F("partial_pay", 2, BCD), F("reset_id", 1, BIN), F("unused", 10, HEX)),
            source="Table 7.8"),
    Command(0x1C, "meters", "Send meters", "R",
            read=(), reply=tuple(map(meter4, ("coin_in", "coin_out", "drop", "jackpot", "games_played",
                                     "games_won", "slot_door_opened", "power_reset"))),
            source="Table 7.2c"),
    Command(0x1D, "eft-meters", "EFT cumulative meters (credits)", "R",
            read=(), reply=tuple(map(meter4, ("promotional", "non_cashable", "uploaded", "cashable"))),
            source=SASTEST),
    Command(0x1E, "bill-meters", "Send total bill meters (number of bills)", "R",
            read=(), reply=tuple(map(meter4, ("bills_1", "bills_5", "bills_10", "bills_20", "bills_50",
                                     "bills_100"))), source="Table 7.2d"),
    Command(0x1F, "info", "Send gaming machine ID and information", "R", read=(), reply=INFO_FIELDS,
            source="Table 7.10"),
    Command(0x21, "rom-signature", "ROM signature verification", "S",
            (F("seed", 2, BIN, "CRC seed"),), ACK, source="Table 6.2"),
    Command(0x27, "eft-promo-credits", "EFT current restricted promotional credits", "R",
            read=(), reply=(meter4("credits"),), source=SASTEST),
    Command(0x28, "eft-log", "EFT transaction log, newest first", "R",
            read=(), reply=tuple(F(f"transaction_{i}", 9, HEX) for i in range(1, 6)), source=SASTEST),
    Command(0x2E, "delay-game", "Delay game", "S",
            (F("delay", 2, BIN, "units of 100 ms"),), ACK, source="Table 13.7"),
    Command(0x2F, "selected-meters", "Send selected meters for game n", "M",
            (GAME, Repeat((F("meter", 1, BIN, "Table C-7 meter code"),))),
            (GAME, Repeat((F("meter", 1, BIN), F("value", TABLE_C7, BCD)))),
            request_length=True, reply_length=True, read=READ_METERS, source="Table 7.3"),
    *(Command(code, f"bills-{value}", f"Send ${value:,} bills in meter", "R", read=(), reply=(meter4("bills"),),
              source="Table 7.1") for code, value in _BILLS.items()),
    Command(0x3D, "cashout-ticket", "Send cash out ticket information", "R",
            read=(), reply=(F("validation_number", 4, DIGITS), F("amount_cents", 5, BCD)), source="Table 15.5"),
    Command(0x48, "last-bill", "Send last accepted bill information", "R",
            read=(), reply=(F("country", 1, BCD), F("denomination", 1, BCD), meter4("count")), source="Table 7.11"),
    Command(0x4C, "set-validation-id", "Set secure enhanced validation ID", "S",
            (F("machine_id", 3, BIN), F("sequence", 3, BIN)),
            (F("machine_id", 3, BIN), F("sequence", 3, BIN)), read=("0", "0"), source="Table 15.6"),
    Command(0x4D, "validation-info", "Send enhanced validation information", "S",
            (F("function", 1, BIN, "0 = current, 1-31 = buffer index, 255 = look ahead"),),
            (F("type", 1, BIN), F("index", 1, BIN), F("date", 4, DIGITS), F("time", 3, DIGITS),
             F("validation_number", 8, DIGITS), F("amount_cents", 5, BCD), F("ticket_number", 2, BIN),
             F("system_id", 1, BCD), F("expiration", 4, DIGITS), F("pool_id", 2, BIN)),
            read=("255",), source="Table 15.10"),
    Command(0x4F, "hopper-status", "Send current hopper status", "R",
            read=(), reply=(F("status", 1, BIN), F("percent_full", 1, BIN), F("level", 4, BCD)),
            reply_length=True, source="Table 7.19"),
    Command(0x50, "validation-meters", "Send validation meters", "S",
            (F("type", 1, BIN, "validation type, Table 15.13c"),),
            (F("type", 1, BIN), F("count", 4, BCD), F("amount_cents", 5, BCD)), read=("0",), source="Table 15.13"),
    Command(0x51, "game-count", "Send total number of games implemented", "R",
            read=(), reply=(F("games", 2, BCD),), source="Table 7.6.3"),
    Command(0x53, "game-config", "Send game n configuration", "M", (GAME,), (GAME,) + INFO_FIELDS,
            read=("0",), source="Table 7.6.5"),
    Command(0x54, "version", "Send SAS version ID and gaming machine serial number", "R",
            read=(), reply=(F("sas_version", 3, ASCII), F("serial_number", REST, ASCII)), reply_length=True,
            source="Table 7.15"),
    Command(0x55, "selected-game", "Send selected game number", "R", read=(), reply=(GAME,), source="Table 7.6.6"),
    Command(0x56, "enabled-games", "Send enabled game numbers", "R",
            read=(), reply=(F("count", 1, BIN), Repeat((GAME,))), reply_length=True, source="Table 7.6.7"),
    Command(0x57, "pending-cashout", "Send pending cashout information", "R",
            reply=(F("type", 1, BIN), F("amount_cents", 5, BCD)), source="Table 15.7"),
    Command(0x58, "validation-number", "Receive validation number", "S",
            (F("system_id", 1, BCD, "0 = validation denied"), F("validation_number", 8, DIGITS)),
            (F("status", 1, BIN),), source="Table 15.8"),
    Command(0x62, "eft-load-noncashable", "EFT load non-cashable credits", "S", EFT_LOAD, EFT_LOAD_REPLY,
            source=SASTEST),
    Command(0x63, "eft-load-promo", "EFT load promotional credits", "S", EFT_LOAD, EFT_LOAD_REPLY,
            source=SASTEST),
    Command(0x64, "eft-cashout-to-host", "EFT force cash out to host", "S", EFT_CASHOUT, EFT_CASHOUT_REPLY,
            source=SASTEST),
    Command(0x65, "eft-cashout-from-machine", "EFT force cash out from gaming machine", "S", EFT_CASHOUT,
            EFT_CASHOUT_REPLY, source=SASTEST),
    Command(0x66, "eft-cashout-amount", "EFT request cash out credit amount", "S", EFT_CASHOUT[1:],
            EFT_LOAD_REPLY, source=SASTEST),
    Command(0x67, "eft-cashout-win", "EFT cash out win to host", "S", EFT_CASHOUT, EFT_CASHOUT_REPLY,
            source=SASTEST),
    Command(0x69, "eft-load-cashable", "EFT load cashable credits", "S", EFT_LOAD, EFT_LOAD_REPLY,
            source=SASTEST),
    Command(0x6A, "eft-available", "EFT available transfers", "R",
            read=(), reply=(F("reserved", 3, HEX), F("available", 1, BITS)), source=SASTEST),
    Command(0x6B, "eft-promo-cashout", "EFT force promotional cash out to host", "S", EFT_CASHOUT,
            EFT_CASHOUT_REPLY, source=SASTEST),
    Command(0x6E, "authentication", "Send authentication info", "S",
            (F("action", 1, BIN, "0 = count, 1 = status, 2 = authenticate, 3 = auth status"),
             F("addressing", 1, BIN, "0 = by index, 1 = by name"),
             F("component", COUNTED, TEXT, "index as 0x.. or name"),
             F("method", 4, BITS, "Table 17.1c"), F("seed", COUNTED, TEXT), F("offset", COUNTED, TEXT)),
            (F("list_crc", 2, BIN), F("status", 1, BIN), F("name", COUNTED, ASCII),
             F("size", COUNTED, BIN), F("available_methods", 4, BITS), F("method", 4, BITS),
             F("data", COUNTED, HEX)),
            request_length=True, reply_length=True, read=("0",), source="Table 17.1"),
    *(Command(code, name, "Send extended meters for game n" + alt, "M",
              (GAME, Repeat((F("meter", 2, BIN, "Table C-7 meter code"),))),
              (GAME, Repeat((F("meter", 2, BIN), F("value", COUNTED, BCD)))),
              request_length=True, reply_length=True, source="Table 7.21", read=READ_METERS + ("0x7F",))
      for code, name, alt in ((0x6F, "extended-meters", ""), (0xAF, "extended-meters-alt", " (alternate)"))),
    Command(0x70, "ticket-data", "Send ticket validation data", "R",
            reply=(F("status", 1, BIN), F("amount_cents", 5, BCD), F("parsing_code", 1, BIN),
                   F("validation_data", REST, HEX)), reply_length=True, source="Table 15.11"),
    Command(0x71, "redeem-ticket", "Redeem ticket", "S",
            (F("transfer_code", 1, BIN, "Table 15.12c"), F("amount_cents", 5, BCD),
             F("parsing_code", 1, BIN), F("validation_data", 9, DIGITS, "18 digits for parsing code 0"),
             F("expiration", 4, DIGITS, "MMDDYYYY or 0000NNNN days"), F("pool_id", 2, BIN)),
            (F("status", 1, BIN), F("amount_cents", 5, BCD), F("parsing_code", 1, BIN),
             F("validation_data", REST, HEX)),
            request_length=True, reply_length=True, source="Table 15.12"),
    Command(0x72, "aft-transfer", "AFT transfer funds (transfer code FF interrogates)", "S",
            (F("transfer_code", 1, BIN, "0 full, 1 partial, 80 cancel, FE/FF interrogate"),
             F("index", 1, BIN, "0 for a new transfer"), F("type", 1, BIN, "Table 8.3d"),
             F("cashable_cents", 5, BCD), F("restricted_cents", 5, BCD), F("nonrestricted_cents", 5, BCD),
             F("flags", 1, BITS), F("asset", 4, BIN), F("registration_key", 20, HEX),
             F("transaction_id", COUNTED, ASCII), F("expiration", 4, DIGITS), F("pool_id", 2, BIN),
             F("receipt_data", COUNTED, HEX), F("lock_timeout", 2, BCD, "hundredths of a second")),
            (F("position", 1, BIN), F("status", 1, BIN), F("receipt_status", 1, BIN), F("type", 1, BIN),
             F("cashable_cents", 5, BCD), F("restricted_cents", 5, BCD), F("nonrestricted_cents", 5, BCD),
             F("flags", 1, BITS), F("asset", 4, BIN), F("transaction_id", COUNTED, ASCII),
             F("date", 4, DIGITS), F("time", 3, DIGITS), F("expiration", 4, DIGITS), F("pool_id", 2, BIN),
             F("cumulative_cashable", COUNTED, BCD), F("cumulative_restricted", COUNTED, BCD),
             F("cumulative_nonrestricted", COUNTED, BCD)),
            request_length=True, reply_length=True, read=("0xFE", "0"), source="Table 8.3"),
    Command(0x73, "aft-register", "AFT register gaming machine (code FF reads)", "S",
            (F("code", 1, BIN, "0 init, 1 register, 40 ack, 80 unregister, FF read"),
             F("asset", 4, BIN), F("registration_key", 20, HEX), F("pos_id", 4, BIN)),
            (F("status", 1, BIN), F("asset", 4, BIN), F("registration_key", 20, HEX), F("pos_id", 4, BIN)),
            request_length=True, reply_length=True, read=("0xFF",), source="Table 8.1"),
    Command(0x74, "aft-lock", "AFT game lock and status request (lock code FF reads)", "S",
            (F("lock_code", 1, BIN, "0 lock, 80 cancel, FF interrogate"), F("condition", 1, BITS),
             F("timeout", 2, BCD, "hundredths of a second")),
            (F("asset", 4, BIN), F("lock_status", 1, BIN), F("available_transfers", 1, BITS),
             F("host_cashout_status", 1, BITS), F("aft_status", 1, BITS), F("max_history_index", 1, BIN),
             F("cashable_cents", 5, BCD), F("restricted_cents", 5, BCD), F("nonrestricted_cents", 5, BCD),
             F("transfer_limit_cents", 5, BCD), F("restricted_expiration", 4, DIGITS),
             F("restricted_pool_id", 2, BIN)),
            reply_length=True, read=("0xFF", "0", "0"), source="Table 8.2"),
    Command(0x75, "aft-receipt-data", "Set AFT receipt data", "S",
            (Repeat((F("code", 1, BIN, "Table 8.11.1b"), F("text", COUNTED, ASCII))),), ACK,
            request_length=True, source="Table 8.11.1"),
    Command(0x7B, "validation-status", "Extended validation status", "S",
            (F("control_mask", 2, BITS), F("control_states", 2, BITS),
             F("cashable_expiration", 2, BCD, "days, 0 = no change"),
             F("restricted_expiration", 2, BCD, "days, 0 = no change")),
            (F("asset", 4, BIN), F("status", 2, BITS), F("cashable_expiration", 2, BCD),
             F("restricted_expiration", 2, BCD)),
            request_length=True, reply_length=True, source="Table 15.2"),
    Command(0x7C, "extended-ticket-data", "Set extended ticket data", "S",
            (Repeat((F("code", 1, BIN, "Table 15.3c"), F("data", COUNTED, ASCII))),),
            (F("ticket_data_valid", 1, BIN),), request_length=True, source="Table 15.3"),
    Command(0x7D, "ticket-data-set", "Set ticket data", "S",
            (F("host_id", 2, BIN), F("expiration_days", 1, BIN), F("location", COUNTED, ASCII),
             F("address_1", COUNTED, ASCII), F("address_2", COUNTED, ASCII)),
            (F("ticket_data_valid", 1, BIN),), request_length=True, source="Table 15.4"),
    Command(0x7E, "date-time", "Send current date and time", "R",
            read=(), reply=(F("date", 4, DIGITS), F("time", 3, DIGITS)), source="Table 7.18"),
    Command(0x7F, "set-date-time", "Receive date and time", "S",
            (F("date", 4, DIGITS, "MMDDYYYY"), F("time", 3, DIGITS, "HHMMSS")), ACK, source="Table 7.17"),
    Command(0x80, "progressive-level", "Receive progressive amount", "S",
            (F("group", 1, BIN), F("level", 1, BIN), F("amount_cents", 5, BCD)), ACK, source="Table 10.1a"),
    Command(0x84, "progressive-win", "Send progressive win amount", "R",
            read=(), reply=(F("group", 1, BIN), F("level", 1, BIN), F("amount_cents", 5, BCD)), source="Table 10.4"),
    Command(0x85, "sas-progressive-win", "Send SAS progressive win amount", "R",
            reply=(F("group", 1, BIN), F("level", 1, BIN), F("amount_cents", 5, BCD)), source="Table 10.4"),
    Command(0x86, "progressive-levels", "Receive multiple progressive levels", "S",
            (F("group", 1, BIN), Repeat((F("level", 1, BIN), F("amount_cents", 5, BCD)))), ACK,
            request_length=True, source="Table 10.1b"),
    Command(0x8A, "legacy-bonus", "Initiate a legacy bonus pay", "S",
            (F("amount", 4, BCD, "SAS accounting denom units"),
             F("tax_status", 1, BIN, "0 deductible, 1 non-deductible, 2 wager match")), ACK,
            source="Table 13.3"),
    Command(0x8B, "multiplied-jackpot", "Initiate multiplied jackpot mode (obsolete)", "S",
            (F("minimum_win", 4, BCD), F("maximum_win", 4, BCD),
             F("multiplier", 1, BIN, "1-10, bit 7 = non-deductible"), F("disable", 1, BIN, "0 enable, 1 disable"),
             F("wager_type", 1, BIN, "0 all wagers, 1 max bet only")), ACK, source="Table 13.4"),
    Command(0x8C, "tournament", "Enter/exit tournament mode", "M",
            (GAME, F("time", 2, DIGITS, "MMSS"), F("credits", 4, BCD),
             F("pulses", 1, BIN, "1 = tournament pulses enabled")), ACK, source="Table 11.1"),
    Command(0x8F, "reel-stops", "Send physical reel stop information", "R",
            read=(), reply=(F("stops", 9, HEX),), source="Table 7.13"),
    Command(0x90, "legacy-bonus-win", "Send legacy bonus win amount", "R",
            read=(), reply=(F("multiplier", 1, BIN), F("multiplied_win", 4, BCD), F("tax_status", 1, BIN),
                   F("bonus", 4, BCD)), source="Table 13.5"),
    Command(0x94, "reset-handpay", "Remote handpay reset", "S", reply=(F("reset_code", 1, BIN),),
            source="Table 7.9"),
    *(Command(code, name, title, "M", (GAME,), (GAME,) + tuple(map(meter4, meters)), source=source,
              read=("0",))
      for code, (name, title, meters, source) in _GAME_METERS.items()),
    Command(0xA0, "features", "Send enabled features", "M", (GAME,),
            (GAME, F("features_1", 1, BITS), F("features_2", 1, BITS), F("features_3", 1, BITS),
             F("reserved", 3, HEX)), read=("0",), source="Table 7.14"),
    Command(0xA4, "cashout-limit", "Send cash out limit", "M", (GAME,),
            (GAME, F("limit", 2, BCD)), read=("0",), source="Table 7.16"),
    Command(0xA8, "handpay-reset-method", "Enable jackpot handpay reset method", "S",
            (F("method", 1, BIN, "0 standard handpay, 1 reset to the credit meter"),),
            (F("ack_code", 1, BIN),), source="Table 14.2"),
    Command(0xAA, "auto-rebet", "Enable/disable game auto rebet", "S",
            (F("enable", 1, BIN, "1 = enable, 0 = disable"),), ACK, source="Table 7.20"),
]

COMMANDS = {c.code: c for c in sorted(_COMMANDS, key=lambda c: c.code)}
BY_NAME = {c.name: c for c in COMMANDS.values()}
if not len(_COMMANDS) == len(COMMANDS) == len(BY_NAME):
    raise ValueError("two commands share a code or a name")


class ArgumentError(ValueError):
    pass


class DecodeError(ValueError):
    pass


class UnknownCommand(LookupError):
    pass


def find(name_or_code: str) -> Command:
    """A command by name, or by hex code such as 1C or 0x1c."""
    if name_or_code in BY_NAME:
        return BY_NAME[name_or_code]
    try:
        return COMMANDS[int(name_or_code, 16)]
    except (ValueError, KeyError):
        raise UnknownCommand(name_or_code) from None


def encode_request(command: Command, values: list[str]) -> bytes:
    """The bytes after the command byte, without the CRC. A variable-length command may leave
    off trailing fields."""
    return encode(command.request, values, length_byte=command.request_length, name=command.name)


def decode_reply(command: Command, data: bytes) -> list[tuple[str, object]]:
    """Fields of a reply's data (after the command byte, before the CRC)."""
    return decode(command.reply or (), data, length_byte=command.reply_length)


def encode(fields: tuple, values: list[str], length_byte: bool = False, name: str = "") -> bytes:
    """Encode values given as text (digits, numbers, 0x.. hex, ASCII) in field order. With a
    length byte the message is variable length, so trailing fields may be left off."""
    data = _encode(fields, list(values), partial=length_byte, name=name)
    if length_byte:
        if len(data) > 0xFF:
            raise ArgumentError(f"{name}: {len(data)} bytes, more than a length byte holds")
        data = bytes((len(data),)) + data
    return data


def _encode(fields: tuple, values: list[str], partial: bool, name: str) -> bytes:
    out = bytearray()
    meter = None  # the last meter code, for TABLE_C7 sizes
    for i, f in enumerate(fields):
        if isinstance(f, Repeat):
            group = len(f.fields)
            if len(values) % group:
                raise ArgumentError(f"{name}: give {', '.join(x.name for x in f.fields)} in groups of {group}")
            while values:
                out += _encode(f.fields, values[:group], partial=False, name=name)
                del values[:group]
            continue
        if not values:
            if partial:
                break
            raise ArgumentError(f"{name}: missing {', '.join(x.name for x in fields[i:])}")
        value = values.pop(0)
        out += _encode_field(f, value, meter)
        if f.name == "meter":
            meter = int(value, 0)
    if values:
        raise ArgumentError(f"{name}: unexpected extra values {values}")
    return bytes(out)


def _encode_field(f: Field, value: str, meter: int | None) -> bytes:
    size = _size(f, meter)
    try:
        raw = _encode_value(f, value, size)
    except OverflowError:
        raise ArgumentError(f"{f.name}: {value} does not fit in {size} bytes") from None
    except ValueError as e:
        raise ArgumentError(f"{f.name}: {e}") from e
    if f.size == COUNTED:
        if len(raw) > 0xFF:
            raise ArgumentError(f"{f.name}: longer than 255 bytes")
        return bytes((len(raw),)) + raw
    if size is not None and len(raw) != size:
        raise ArgumentError(f"{f.name}: needs {size} bytes, got {len(raw)}")
    return raw


def _size(f: Field, meter: int | None) -> int | None:
    """The field's size in bytes, or None when the value sets it (COUNTED, REST)."""
    if f.size == TABLE_C7:
        if meter not in METERS:
            raise ArgumentError(f"{f.name}: meter {meter} is not in Table C-7")
        return METERS[meter][1]
    return f.size if isinstance(f.size, int) else None


def _encode_value(f: Field, value: str, size: int | None) -> bytes:
    if f.enc in (BCD, DIGITS):
        if not value.isdigit():
            raise ValueError(f"digits only, got {value!r}")
        width = 2 * size if size else len(value) + len(value) % 2
        if len(value) > width:
            raise ValueError(f"at most {width} digits")
        return bytes.fromhex(value.zfill(width))
    if f.enc in (BIN, BITS):
        number = int(value, 0)
        return number.to_bytes(size or max(1, (number.bit_length() + 7) // 8), "little")
    if f.enc == HEX:
        if size and int(value, 16) == 0:
            return bytes(size)  # "0" for an all-zero key or padding
        return bytes.fromhex(value.removeprefix("0x"))
    if f.enc == TEXT and value.startswith("0x"):
        return bytes.fromhex(value[2:])
    return value.encode("ascii")


def decode(fields: tuple, data: bytes, length_byte: bool = False) -> list[tuple[str, object]]:
    """Decode data laid out as `fields`. A short message ends early, as variable-length ones
    may; bytes beyond the layout come back as "extra"."""
    extra = b""
    if length_byte:
        if not data or data[0] > len(data) - 1:
            raise DecodeError(f"length byte does not match the message: {data.hex(' ')}")
        extra, data = data[1 + data[0]:], data[1:1 + data[0]]
    out, rest = _decode(fields, data)
    if rest + extra:
        out.append(("extra", (rest + extra).hex(" ")))
    return out


def _decode(fields: tuple, data: bytes) -> tuple[list[tuple[str, object]], bytes]:
    out: list[tuple[str, object]] = []
    for f in fields:
        if not data:
            break
        if isinstance(f, Repeat):
            items = []
            while data:
                item, rest = _decode(f.fields, data)
                if rest == data:
                    break
                items.append(dict(item))
                data = rest
            out.append(("items", items))
            continue
        meter = out[-1][1] if out and out[-1][0] == "meter" else None
        if f.size == TABLE_C7 and meter not in METERS:
            return out, data  # unknown size: leave the rest undecoded
        header, size = (1, data[0]) if f.size == COUNTED else (0, _size(f, meter) or len(data))
        if len(data) < header + size:
            return out, data
        out.append((f.name, decode_value(f, data[header:header + size])))
        data = data[header + size:]
    return out, data


def decode_value(f: Field, raw: bytes) -> object:
    if f.enc in (BCD, DIGITS):
        digits = raw.hex()
        if digits and not digits.isdigit():
            return "0x" + digits  # not BCD: show what came
        return digits if f.enc == DIGITS else int(digits or "0")
    if f.enc == BIN:
        return int.from_bytes(raw, "little")
    if f.enc == BITS:
        return f"0x{int.from_bytes(raw, 'little'):0{2 * len(raw)}X}"
    if f.enc in (ASCII, TEXT) and all(chr(b) in string.printable for b in raw):
        return raw.decode("ascii")
    return raw.hex(" ")
