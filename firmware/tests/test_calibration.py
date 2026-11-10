"""Native calibration cuts and fatal sink, not peripheral/timing qualification.

The guarded executor refuses TRAP1. The fatal-path test explicitly supplies its
68000 supervisor exception frame, then executes the real ROM handler and leaves.
No other exception, timer, writer, display or recovery routine is substituted.
"""

import pytest
from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_MEM_WRITE
from unicorn.m68k_const import (
    UC_M68K_REG_A7,
    UC_M68K_REG_D0,
    UC_M68K_REG_D5,
)

from firmware.tests import test_platform
from firmware.tests.cpu import PAGE_SIZE, REGISTERS, STACK_TOP, Cpu, marked_registers
from firmware.tests.kernel import CREATE_QUEUE
from firmware.tests.support import (
    COILS,
    DEVICE_PAGE,
    MAIN_ROM,
    MOTOR_AND_TIMER_ROUTINES,
    PERSONALITY,
    PORT_PAGE,
    T2_ENABLE,
    VIA_IER,
    VIA_TIMER_REGISTERS,
    WATCHDOG,
    run_native_fatal,
    run_to_platform_fatal,
)

compiled = test_platform.compiled

BASE = MAIN_ROM.read_bytes()
BOOT = 0x1906
CANDIDATE, STORED = 0x5DF1A, 0x5DF30
T2_LOADED = 0x5D7A2
# The immediate in the unchanged native ADDI.W, not a second policy limit.
BIAS = int.from_bytes(BASE[0x5DF1E:0x5DF20], "big")
IO_SPACE = (DEVICE_PAGE, 0x1000000 - DEVICE_PAGE)  # to the top of the 24-bit bus


def prepare(cpu, calibration, value, sr=0x251F, delay=0):
    cpu.map_io(DEVICE_PAGE)
    cpu.map_io(PORT_PAGE)
    cpu.write_u16(calibration, value)
    cpu.write_u16(COILS, 0xA55A)
    cpu.write_u32(0x410218, 0x12AB34CD)
    registers = marked_registers() | {UC_M68K_REG_D0: 0xA3215678, UC_M68K_REG_D5: 0xB0000000 | delay}
    cpu.set_state(STACK_TOP, sr=sr, registers=registers)


def observed(cpu, calibration):
    """The block's I/O accesses and accesses starting at the calibration word."""
    return cpu.accesses(IO_SPACE, (calibration, 1), kind=UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE)


def snapshot(cpu, calibration):
    return (
        cpu.read_registers((*REGISTERS, UC_M68K_REG_A7)),
        cpu.read_sr(),
        cpu.read_u16(calibration),
        cpu.read(DEVICE_PAGE, PAGE_SIZE),
        cpu.read(PORT_PAGE, PAGE_SIZE),
    )


@pytest.mark.parametrize("sr", [0x2000, 0x251F, 0x2700])
@pytest.mark.parametrize("boundary", ["CALIBRATION_MIN", "CALIBRATION_MAX"])
@pytest.mark.parametrize("cut", ["candidate", "t2"])
def test_allowed_paths_match_original_registers_flags_stack_and_writes(
    compiled, sr, boundary, cut
):
    calibration = compiled.symbols["CALIBRATION_ADDRESS"]
    value = compiled.symbols[boundary]
    start, until = (CANDIDATE, STORED) if cut == "candidate" else (T2_ENABLE, T2_LOADED)
    results, writes = [], []
    for image in (BASE, compiled.image):
        cpu = Cpu(image, PERSONALITY)
        prepare(cpu, calibration, value, sr, value - BIAS)
        with observed(cpu, calibration) as accesses:
            cpu.run(start, until)
        results.append(snapshot(cpu, calibration))
        writes.append([a for a in accesses if a.kind == UC_MEM_WRITE])
        assert cpu.read_u16(calibration) == value
    assert results[0] == results[1]
    assert writes[0] == writes[1]
    if cut == "candidate":
        assert writes[1][-1] == (UC_MEM_WRITE, calibration, 2, value)
    else:
        assert writes[1] == [
            (UC_MEM_WRITE, VIA_IER, 1, 0xE0),
            (UC_MEM_WRITE, 0xFE10D0, 4, 0x12AB34CD),
        ]


@pytest.mark.parametrize("value_kind", ["zero", "min", "max"])
def test_real_boot_hook_accepts_virgin_or_bounded_calibration(compiled, value_kind):
    calibration = compiled.symbols["CALIBRATION_ADDRESS"]
    value = {
        "zero": 0,
        "min": compiled.symbols["CALIBRATION_MIN"],
        "max": compiled.symbols["CALIBRATION_MAX"],
    }[value_kind]
    reference = Cpu(BASE, PERSONALITY)
    prepare(reference, calibration, value)
    reference.run(BOOT, CREATE_QUEUE)
    cpu = Cpu(compiled.image, PERSONALITY)
    cpu.call(compiled.symbols["test_reset"])
    cpu.call(compiled.symbols["platform_boot"])
    prepare(cpu, calibration, value)
    cpu.run(BOOT, CREATE_QUEUE)
    assert snapshot(cpu, calibration) == snapshot(reference, calibration)
    assert cpu.call(compiled.symbols["test_restores"]) == 1
    assert cpu.call(compiled.symbols["test_boot"]) == 1
    cpu.call(compiled.symbols["platform_restore"])
    assert cpu.call(compiled.symbols["test_boot"]) == 0


@pytest.mark.parametrize("value_kind", ["zero", "low", "high", "word_max"])
@pytest.mark.parametrize("cut", ["boot", "t2"])
def test_bad_retained_calibration_never_reaches_side_effects(compiled, cut, value_kind):
    low, high = compiled.symbols["CALIBRATION_MIN"], compiled.symbols["CALIBRATION_MAX"]
    value = {"zero": 0, "low": low - 1, "high": high + 1, "word_max": 0xFFFF}[
        value_kind
    ]
    if cut == "boot" and value == 0:
        # Virgin zero is tested on the boot success path, not an invalid case.
        value = 1
    calibration = compiled.symbols["CALIBRATION_ADDRESS"]
    cpu = Cpu(compiled.image, PERSONALITY)
    if cut == "boot":
        cpu.call(compiled.symbols["test_reset"])
        cpu.call(compiled.symbols["platform_boot"])
    prepare(cpu, calibration, value)
    start, until = (BOOT, CREATE_QUEUE) if cut == "boot" else (T2_ENABLE, T2_LOADED)
    with observed(cpu, calibration) as accesses:
        run_to_platform_fatal(cpu, compiled, start, until)
    assert cpu.read_u16(calibration) == value
    assert cpu.read_u16(COILS) == 0xA55A
    assert all(
        a.address == calibration and a.size == 2 and a.kind != UC_MEM_WRITE for a in accesses
    )
    if cut == "boot":
        assert cpu.call(compiled.symbols["test_restores"]) == 0


@pytest.mark.parametrize(
    "delay_kind", ["high", "last_no_wrap", "first_wrap", "last_wrap"]
)
def test_invalid_candidate_including_word_overflow_is_not_stored(compiled, delay_kind):
    calibration = compiled.symbols["CALIBRATION_ADDRESS"]
    low, high = compiled.symbols["CALIBRATION_MIN"], compiled.symbols["CALIBRATION_MAX"]
    delay = {
        "high": high - BIAS + 1,
        "last_no_wrap": 0xFFFF - BIAS,
        "first_wrap": 0x10000 - BIAS,
        "last_wrap": 0xFFFF,
    }[delay_kind]
    cpu = Cpu(compiled.image, PERSONALITY)
    prepare(cpu, calibration, low, delay=delay)
    with observed(cpu, calibration) as accesses:
        run_to_platform_fatal(cpu, compiled, CANDIDATE, STORED)
    assert cpu.read_u16(calibration) == low
    assert not accesses


@pytest.mark.parametrize("cut", ["boot", "candidate", "t2"])
def test_rejection_enters_real_native_fatal_sink_without_timer_recovery_or_coil_off(
    compiled, cut
):
    calibration = compiled.symbols["CALIBRATION_ADDRESS"]
    high = compiled.symbols["CALIBRATION_MAX"]
    cpu = Cpu(compiled.image, PERSONALITY)
    prepare(cpu, calibration, high + 1, delay=high - BIAS + 1)
    start, until = {
        "boot": (BOOT, CREATE_QUEUE),
        "candidate": (CANDIDATE, STORED),
        "t2": (T2_ENABLE, T2_LOADED),
    }[cut]
    with observed(cpu, calibration) as accesses:
        trap = run_to_platform_fatal(cpu, compiled, start, until)
        visited = run_native_fatal(cpu, trap)
    assert not (MOTOR_AND_TIMER_ROUTINES & visited)
    assert cpu.read_u16(calibration) == high + 1
    assert cpu.read_u16(COILS) == 0xA55A
    assert not any(
        a.address in VIA_TIMER_REGISTERS
        or a.kind == UC_MEM_WRITE
        and a.address in (COILS, calibration)
        for a in accesses
    )
    assert (UC_MEM_WRITE, WATCHDOG, 1, 0) in accesses
