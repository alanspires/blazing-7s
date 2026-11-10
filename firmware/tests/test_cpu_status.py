"""Stopped CPU observations and execution boundaries preserve native behavior."""

import pytest
from unicorn.m68k_const import (
    UC_M68K_REG_A7,
    UC_M68K_REG_D1,
    UC_M68K_REG_D2,
    UC_M68K_REG_PC,
    UC_M68K_REG_SR,
)

from firmware.tests.cpu import STACK_BASE, STACK_TOP, Cpu


@pytest.mark.parametrize("initial", [0x2700, 0x271B])
def test_status_observation_preserves_lazy_flags_and_execution(initial):
    start = 0x100
    # MOVEQ #0,D0; BNE fail; MOVEQ #1,D1; BRA done; fail: MOVEQ #0,D1; done: NOP.
    image = bytes(start) + bytes.fromhex("700066047201600272004e71")
    cpu = Cpu(image)
    scratch = b"\xa5\x5a"
    cpu.write(STACK_BASE, scratch)
    cpu.uc.reg_write(UC_M68K_REG_SR, initial)
    cpu.run(start, start + 2)
    expected = (initial & 0xFFE0) | (initial & 0x10) | 0x04
    assert cpu.read_sr() == expected
    assert cpu.read_sr() == expected
    assert cpu.uc.reg_read(UC_M68K_REG_PC) == start + 2
    assert cpu.read(STACK_BASE, len(scratch)) == scratch
    assert cpu.instructions == 1
    cpu.run(start + 2, start + 10)
    assert cpu.uc.reg_read(UC_M68K_REG_D1) == 1


def test_cached_return_chain_stops_before_the_next_frame_is_popped():
    start, continuation, end = 0x100, 0x200, 0x300
    image = bytearray(end + 2)
    image[start:start + 2] = bytes.fromhex("4e75")  # RTS
    image[continuation:continuation + 4] = bytes.fromhex("241f4e75")  # POP D2; RTS
    image[end:end + 2] = bytes.fromhex("4e40")  # TRAP0 must never execute
    cpu = Cpu(bytes(image))
    frame = b"".join(v.to_bytes(4, "big") for v in (continuation, 0x1234, end))
    sp = STACK_TOP - len(frame)
    cpu.write(sp, frame)
    cpu.uc.reg_write(UC_M68K_REG_A7, sp)
    cpu.run(start, continuation)
    cpu.run(continuation, end)
    assert cpu.uc.reg_read(UC_M68K_REG_D2) == 0x1234

    cpu.uc.reg_write(UC_M68K_REG_D2, 0)
    cpu.uc.reg_write(UC_M68K_REG_A7, sp)
    cpu.run(start, continuation)
    assert cpu.uc.reg_read(UC_M68K_REG_PC) == continuation
    assert cpu.uc.reg_read(UC_M68K_REG_D2) == 0
    assert cpu.uc.reg_read(UC_M68K_REG_A7) == sp + 4
    assert cpu.instructions == 1
