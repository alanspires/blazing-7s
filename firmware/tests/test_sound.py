"""SAS sound-control hooks at 0x609ec (sound task latch) and 0x27ae6 (SAS 03 handler).

Runs the real compiled production image against untouched S6M00002B5 in the CPU
emulator across the native sound task remap block (0x609ec..0x60b06), the native
external-sound-board latch driver (0x60d3e..0x60d6a) and the SAS 03 handler
(0x27ad6..0x27af6).
"""

import pytest
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_D2

from firmware.tests.cpu import REGISTERS, STACK_TOP, Cpu, marked_registers
from firmware.tests.support import (
    DEVICE_PAGE,
    MAIN_ROM,
    PERSONALITY,
    PORT_PAGE,
    build_production,
)

BASE = MAIN_ROM.read_bytes()


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return build_production(tmp_path_factory)

# Native sound addresses (roms/s6000_main_S6M00002B5.bin)
SOUND_LATCH_SITE = 0x609EC
SOUND_AFTER_REMAP = 0x60B06
DRIVER_ENTRY = 0x60D3E
DRIVER_AFTER_LATCH = 0x60D6A
SAS_03_ENTRY = 0x27AD6
SAS_03_REPLY = 0x27AF6

CONFIG_WORD = 0x43E8D2  # bit 1 (0x0002): SAS active
SOUND_ALL = 0x43E8E2  # 0 after SAS 03 sound-off, 1 after SAS 04 sound-on
SOUND_REEL = 0x43E8E3  # 0 after SAS 05 reel-sounds-off, 1 after SAS 04 sound-on
REMAP_FLAGS = 0x43CA30
EXT_LATCH_VAR = 0x4102A7
LAST_REEL_SOUND = 0x41021C
PORT_SHADOW = 0x41242C
EXT_PORT = 0xFF100A  # low byte = active-low external sound board command bus
SAS_PORT_IDX = 0x43E8D6
RX_BASE = 0x4105E0  # port 1 RX slot (0x2749a computes 0x4105e0 + (idx-1)*0x340)


def _crc16_kermit(data: bytes) -> int:
    crc = 0
    for b in data:
        q = (crc ^ b) & 0x0F
        crc = (crc >> 4) ^ (q * 0x1081)
        q = (crc ^ (b >> 4)) & 0x0F
        crc = (crc >> 4) ^ (q * 0x1081)
    return crc


def _setup_sound(cpu: Cpu, code: int, *, sas_active: bool, sound_all: int, sound_reel: int,
                 remap: int = 0x1F, upper_shadow: int = 0xA500) -> None:
    cpu.map_io(PORT_PAGE)
    cpu.write_u16(CONFIG_WORD, 0x0022 if sas_active else 0x0000)
    cpu.write_u8(SOUND_ALL, sound_all)
    cpu.write_u8(SOUND_REEL, sound_reel)
    cpu.write_u8(REMAP_FLAGS, remap)
    cpu.write_u8(EXT_LATCH_VAR, 0)
    cpu.write_u32(LAST_REEL_SOUND, 0xCAFEBABE)
    cpu.write_u16(PORT_SHADOW, upper_shadow | 0x00FF)
    cpu.write_u16(EXT_PORT, upper_shadow | 0x00FF)
    regs = marked_registers() | {UC_M68K_REG_D2: code}
    cpu.set_state(STACK_TOP, sr=0x2015, registers=regs)


def _reg(cpu: Cpu, reg: int) -> int:
    return cpu.read_registers((reg,))[reg]


def _snapshot(cpu: Cpu) -> tuple:
    return (
        cpu.read_registers((*REGISTERS, UC_M68K_REG_A7)),
        cpu.read_sr(),
        cpu.read_u8(EXT_LATCH_VAR),
        cpu.read_u32(LAST_REEL_SOUND),
        cpu.read_u16(PORT_SHADOW),
        cpu.read_u16(EXT_PORT),
    )


@pytest.mark.parametrize("code", [2, 4, 5, 10, 17, 50, 75])
@pytest.mark.parametrize("sas_active,sound_all,sound_reel", [(True, 1, 1), (False, 0, 0)])
def test_unmuted_or_sas_inactive_matches_stock_exactly(compiled, code, sas_active, sound_all, sound_reel):
    patched = compiled.image
    results = []
    for image in (BASE, patched):
        cpu = Cpu(image, PERSONALITY)
        _setup_sound(cpu, code, sas_active=sas_active, sound_all=sound_all, sound_reel=sound_reel)
        cpu.run(SOUND_LATCH_SITE, SOUND_AFTER_REMAP)
        after_task = _snapshot(cpu)
        cpu.set_state(STACK_TOP, sr=0x2000)
        cpu.run(DRIVER_ENTRY, DRIVER_AFTER_LATCH)
        results.append((after_task, _snapshot(cpu)))
    assert results[1] == results[0]


@pytest.mark.parametrize("code", [2, 4, 5, 10, 17, 50, 75])
def test_sas_sound_off_silences_external_latch_and_ym2149_code(compiled, code):
    # Stock ROM leaves the unmuted/remapped code in 0x4102a7 and clocks it to 0xff100a.
    stock = Cpu(BASE, PERSONALITY)
    _setup_sound(stock, code, sas_active=True, sound_all=0, sound_reel=1)
    stock.run(SOUND_LATCH_SITE, SOUND_AFTER_REMAP)
    assert stock.read_u8(EXT_LATCH_VAR) != 1

    cpu = Cpu(compiled.image, PERSONALITY)
    _setup_sound(cpu, code, sas_active=True, sound_all=0, sound_reel=1, upper_shadow=0xA500)
    cpu.run(SOUND_LATCH_SITE, SOUND_AFTER_REMAP)
    assert _reg(cpu, UC_M68K_REG_D2) == 1
    assert cpu.read_u8(EXT_LATCH_VAR) == 1
    assert _reg(cpu, UC_M68K_REG_A7) == STACK_TOP

    cpu.set_state(STACK_TOP, sr=0x2000)
    cpu.run(DRIVER_ENTRY, DRIVER_AFTER_LATCH)
    assert cpu.read_u16(EXT_PORT) == 0xA5FE  # upper byte preserved, low byte = ~1 (STOP)
    assert cpu.read_u8(EXT_LATCH_VAR) == 0


@pytest.mark.parametrize("code,muted", [(4, True), (17, True), (5, False), (50, False)])
def test_sas_reel_sounds_off_silences_only_reel_codes_and_preserves_41021c(compiled, code, muted):
    cpu = Cpu(compiled.image, PERSONALITY)
    _setup_sound(cpu, code, sas_active=True, sound_all=1, sound_reel=0, upper_shadow=0xA500)
    cpu.run(SOUND_LATCH_SITE, SOUND_AFTER_REMAP)
    if muted:
        assert _reg(cpu, UC_M68K_REG_D2) == 1
        assert cpu.read_u8(EXT_LATCH_VAR) == 1
        assert cpu.read_u32(LAST_REEL_SOUND) == code
        cpu.set_state(STACK_TOP, sr=0x2000)
        cpu.run(DRIVER_ENTRY, DRIVER_AFTER_LATCH)
        assert cpu.read_u16(EXT_PORT) == 0xA5FE
    else:
        stock = Cpu(BASE, PERSONALITY)
        _setup_sound(stock, code, sas_active=True, sound_all=1, sound_reel=0, upper_shadow=0xA500)
        stock.run(SOUND_LATCH_SITE, SOUND_AFTER_REMAP)
        assert cpu.read_u8(EXT_LATCH_VAR) == stock.read_u8(EXT_LATCH_VAR)
        assert _reg(cpu, UC_M68K_REG_D2) == _reg(stock, UC_M68K_REG_D2)


@pytest.mark.parametrize("good_crc", [True, False])
def test_sas_03_handler_latches_stop_on_valid_crc_only(compiled, good_crc):
    cpu = Cpu(compiled.image, PERSONALITY)
    cpu.map_io(DEVICE_PAGE)
    cpu.map_io(PORT_PAGE)
    cpu.write_u8(SAS_PORT_IDX, 1)
    cpu.write_u8(SOUND_ALL, 1)
    cpu.write_u8(EXT_LATCH_VAR, 0)
    payload = bytes((0x01, 0x03))
    crc = _crc16_kermit(payload) ^ (0 if good_crc else 0xFFFF)
    frame = payload + crc.to_bytes(2, "little")
    cpu.write_u8(RX_BASE + 280, len(frame))
    cpu.write(RX_BASE + 281, frame)
    cpu.set_state(STACK_TOP, sr=0x2000, registers=marked_registers())

    cpu.run(SAS_03_ENTRY, SAS_03_REPLY)

    assert cpu.read_u32(_reg(cpu, UC_M68K_REG_A7)) == (0 if good_crc else 1)  # ACK vs NACK arg
    if good_crc:
        assert cpu.read_u8(SOUND_ALL) == 0
        assert cpu.read_u8(EXT_LATCH_VAR) == 1
    else:
        assert cpu.read_u8(SOUND_ALL) == 1
        assert cpu.read_u8(EXT_LATCH_VAR) == 0
