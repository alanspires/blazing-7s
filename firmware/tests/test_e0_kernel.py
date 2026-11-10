"""Original VRTX kernel switching around the private E0 stack bridge.

The fixture builds the full production sources, linker script and patch spec on
the hash-checked original ROM, plus one test-only file of task programs
(TASKS), so the image is not the production image. The bridge and its C body
keep the production build's addresses and bytes. Kernel instructions remain the
original ROM's; only the configured task-stack size may differ. Kernel initialization,
creation, deletion, delay, tick, scheduling, native NACK/transmit and the IRQ5
root all execute as original ROM code.

Model boundaries, the only behavior not executed from the image:
- Unicorn rejects TRAP #0 and RTE. `Kernel` performs their 68000 supervisor
  frame push/pop explicitly, only for those verified opcode/vector pairs.
- IRQ5 is an explicit autovector entry at chosen instruction boundaries, not
  asynchronous interrupt timing.
- Test task programs replace native callers. The owner calls the bridge, which
  the native SAS parent reaches through its callback at 0x1643a. The waker
  supplies the DUART TX-complete event by clearing the native TX-busy word, then
  issues the tick service that IRQ4 issues natively at 0x2698. The reaper uses
  the native delete/create wrappers, as serial reconfiguration does.
Scope: supervisor tasks and valid kernel objects; no user mode, kernel hooks,
cancellation recovery, whole-system IRQ stack bound or hardware timing.
"""

from __future__ import annotations

import pytest
from unicorn.m68k_const import (
    UC_M68K_REG_A2,
    UC_M68K_REG_A3,
    UC_M68K_REG_A4,
    UC_M68K_REG_A5,
    UC_M68K_REG_A6,
    UC_M68K_REG_A7,
    UC_M68K_REG_D2,
    UC_M68K_REG_D3,
    UC_M68K_REG_D4,
    UC_M68K_REG_D5,
    UC_M68K_REG_D6,
    UC_M68K_REG_D7,
    UC_M68K_REG_PC,
)

from firmware.tests.cpu import decode
from firmware.tests.kernel import (
    DELAY_RETURN,
    DELAY_TRAP,
    RAM,
    STATE_DELAYED,
    STATE_DORMANT,
    TCB_DELAY_NEXT,
    TCB_NEXT,
    TCB_SSP,
    TCB_STACK_TOP,
    TCB_STATE,
    WS_CURRENT,
    WS_DELAYED,
    WS_FREE,
    Kernel,
    compile_tasks,
)
from firmware.tests.support import HOST_ADDRESS, OUTPUT_SHADOWS

TASKS = r"""
/* Test-only task programs for test_e0_kernel.py, linked after the unmodified
 * production sources. They replace native callers, not kernel behavior. */
    .text
    .equ CREATE, 0x3c54             /* native sc_tcreate wrapper */
    .equ DELETE, 0x3c8a             /* native sc_tdelete wrapper */
    .equ TICK, 0x12                 /* VRTX tick service, natively from IRQ4 0x2698 */
    .equ TX_BUSY_PORT1, 0x74e0e     /* ROM table: port 1 TX-pending word pointer */

    .globl kernel_test_owner_id, kernel_test_owner_priority
    .globl kernel_test_other_id, kernel_test_other_priority
    .set kernel_test_owner_id, 13
    .set kernel_test_owner_priority, 9
    .set kernel_test_other_id, 20
    .set kernel_test_other_priority, 20

    .macro owner name, depth
    .globl \name, \name\()_call, \name\()_returned
\name:
    .if \depth
    lea -\depth(%sp),%sp
    .endif
    movem.l kernel_test_canaries,%d2-%d7/%a2-%a6
\name\()_call:
    jsr platform_sas_handler
\name\()_returned:
    bra.s \name\()_returned
    .endm

    owner kernel_test_owner, 0
    /* A different native depth makes a stale saved SP distinguishable. */
    owner kernel_test_owner_deep, 64

    .globl kernel_test_waker, kernel_test_waker_idle
kernel_test_waker:
    movea.l TX_BUSY_PORT1,%a0       /* the DUART TX-complete event */
    clr.w (%a0)
    moveq #TICK,%d0
    trap #0
kernel_test_waker_idle:
    bra.s kernel_test_waker_idle

    .globl kernel_test_reaper, kernel_test_reaper_deleted, kernel_test_reaper_idle
kernel_test_reaper:
    clr.w -(%sp)                    /* service status word */
    pea (%sp)
    clr.l -(%sp)
    pea kernel_test_owner_id
    jsr DELETE
    lea 12(%sp),%sp
    tst.w (%sp)
    bne.s kernel_test_failed
kernel_test_reaper_deleted:
    movea.l TX_BUSY_PORT1,%a0       /* the DUART TX-complete event */
    clr.w (%a0)
    moveq #TICK,%d0                 /* the deleted owner's delay must not expire */
    trap #0
    pea (%sp)
    pea kernel_test_owner_priority
    pea kernel_test_owner_id
    pea kernel_test_owner
    jsr CREATE
    lea 16(%sp),%sp
    tst.w (%sp)
    bne.s kernel_test_failed
kernel_test_reaper_idle:
    bra.s kernel_test_reaper_idle
kernel_test_failed:
    illegal

    .align 2
    .globl kernel_test_canaries
kernel_test_canaries:
    .long 0xd2d2d2d2, 0xd3d3d3d3, 0xd4d4d4d4, 0xd5d5d5d5, 0xd6d6d6d6, 0xd7d7d7d7
    .long 0xa2a2a2a2, 0xa3a3a3a3, 0xa4a4a4a4, 0xa5a5a5a5, 0xa6a6a6a6

    .section .note.GNU-stack,"",@progbits
"""
CALLEE_SAVED = (
    UC_M68K_REG_D2, UC_M68K_REG_D3, UC_M68K_REG_D4, UC_M68K_REG_D5, UC_M68K_REG_D6,
    UC_M68K_REG_D7, UC_M68K_REG_A2, UC_M68K_REG_A3, UC_M68K_REG_A4, UC_M68K_REG_A5,
    UC_M68K_REG_A6,
)
IRQ5 = 5
NACK = 0x80
GUARD = 0x10


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return compile_tasks(tmp_path_factory, TASKS)


def guards(kernel: Kernel) -> bytes:
    low, high = kernel.private
    return kernel.cpu.read(low - GUARD, GUARD) + kernel.cpu.read(high, GUARD)


def canaries(compiled) -> tuple[int, ...]:
    table = compiled.symbols["kernel_test_canaries"]
    return tuple(
        int.from_bytes(compiled.image[table + 4 * i:table + 4 * i + 4], "big")
        for i in range(len(CALLEE_SAVED))
    )


def assert_returned_natively(kernel: Kernel, tcb: int, top: int, caller_sp: int) -> None:
    """The bridge restored the caller's native SP and callee-saved registers."""
    cpu = kernel.cpu
    assert cpu.uc.reg_read(UC_M68K_REG_A7) == caller_sp
    assert cpu.read_u32(kernel.symbol("sas_saved_sp")) == caller_sp - 4
    assert tuple(cpu.read_registers(CALLEE_SAVED).values()) == canaries(kernel.compiled)
    assert cpu.read_u32(tcb + TCB_STACK_TOP) == top
    assert cpu.read_u32(kernel.workspace + WS_CURRENT) == tcb
    # The native transmit after the wait queued the NACK for this address.
    assert cpu.read_u16(kernel.tx_busy) != 0
    assert cpu.read_u8(kernel.tx_buffer) == cpu.read_u8(HOST_ADDRESS) & 0x7F | NACK


OWNER = ("kernel_test_owner", "kernel_test_owner_id", "kernel_test_owner_priority")
DEEP_OWNER = ("kernel_test_owner_deep", "kernel_test_owner_id", "kernel_test_owner_priority")
WAKER = ("kernel_test_waker", "kernel_test_other_id", "kernel_test_other_priority")
REAPER = ("kernel_test_reaper", "kernel_test_other_id", "kernel_test_other_priority")


def test_delay_inside_bridge_saves_and_resumes_the_private_stack(compiled):
    kernel = Kernel(compiled, (OWNER, WAKER), tx_busy=1)
    cpu, private = kernel.cpu, kernel.private
    tcb = kernel.live("kernel_test_owner_id")
    native_low, top = kernel.native(tcb)
    initial_ssp = cpu.read_u32(tcb + TCB_SSP)
    kernel.plant(native_low, initial_ssp)
    kernel.plant(*private)
    outside = guards(kernel)
    calls = kernel.cpu.record_visits(kernel.symbol("kernel_test_owner_call"))
    delays = kernel.cpu.record_visits(DELAY_TRAP)

    kernel.start("kernel_test_waker")
    ((_, caller_sp),) = calls
    ((_, blocked_sp),) = delays
    # Blocked in native sc_delay: the scheduler saved the actual private SSP.
    assert tcb in kernel.chain(kernel.workspace + WS_DELAYED, TCB_DELAY_NEXT)
    assert cpu.read_u8(tcb + TCB_STATE) & STATE_DELAYED
    saved = cpu.read_u32(tcb + TCB_SSP)
    assert private[0] <= saved < blocked_sp <= private[1]
    assert cpu.read_u32(tcb + TCB_STACK_TOP) == top
    assert cpu.read_u32(kernel.symbol("sas_saved_sp")) == caller_sp - 4
    assert kernel.intact(native_low, initial_ssp)  # no deep native frames
    frames = cpu.read(saved, private[1] - saved)

    kernel.resume(DELAY_RETURN)
    assert cpu.read_u32(kernel.workspace + WS_CURRENT) == tcb
    assert cpu.uc.reg_read(UC_M68K_REG_A7) == blocked_sp
    assert cpu.read(saved, private[1] - saved) == frames  # the waker never touched it

    kernel.resume("kernel_test_owner_returned")
    assert_returned_natively(kernel, tcb, top, caller_sp)
    assert kernel.intact(native_low, initial_ssp)
    assert not kernel.intact(*private)
    assert guards(kernel) == outside


def test_deleted_blocked_owner_never_resumes_and_recreation_uses_its_native_stack(compiled):
    kernel = Kernel(compiled, (DEEP_OWNER, REAPER), tx_busy=1)
    cpu, private = kernel.cpu, kernel.private
    tcb = kernel.live("kernel_test_owner_id")
    native_low, top = kernel.native(tcb)
    calls = kernel.cpu.record_visits(kernel.symbol("kernel_test_owner_deep_call"))
    kernel.start("kernel_test_reaper")
    ((_, stale_caller),) = calls
    stale = cpu.read_u32(kernel.symbol("sas_saved_sp"))
    assert stale == stale_caller - 4
    assert private[0] <= cpu.read_u32(tcb + TCB_SSP) < private[1]
    assert cpu.read_u8(tcb + TCB_STATE) & STATE_DELAYED

    kernel.resume("kernel_test_reaper_deleted")
    assert kernel.live("kernel_test_owner_id") is None
    assert kernel.chain(kernel.workspace + WS_FREE, TCB_NEXT)[0] == tcb
    assert cpu.read_u8(tcb + TCB_STATE) & STATE_DORMANT
    assert kernel.chain(kernel.workspace + WS_DELAYED, TCB_DELAY_NEXT) == []
    assert cpu.read_u32(tcb + TCB_STACK_TOP) == top
    assert cpu.read_u32(kernel.symbol("sas_saved_sp")) == stale

    # Only the deleted activation's caller can reach this marker.
    old = kernel.cpu.record_visits(kernel.symbol("kernel_test_owner_deep_returned"))
    calls = kernel.cpu.record_visits(kernel.symbol("kernel_test_owner_call"))
    kernel.resume("kernel_test_owner")
    # Same TCB, entered fresh on its allocated native stack, not the stale one.
    assert kernel.live("kernel_test_owner_id") == tcb
    assert cpu.uc.reg_read(UC_M68K_REG_A7) == top
    assert native_low <= cpu.read_u32(tcb + TCB_SSP) < top

    kernel.resume("kernel_test_owner_returned")
    ((_, caller_sp),) = calls
    assert caller_sp == top != stale_caller
    assert_returned_natively(kernel, tcb, top, caller_sp)
    assert old == []


def bridge_cuts(compiled) -> list[int]:
    """Every bridge instruction boundary, the C body's entry and the caller's return."""
    start = compiled.symbols["platform_sas_handler"]
    decoded = decode(compiled.image[start:start + 64], start)
    cuts = []
    for address in sorted(decoded):
        cuts.append(address)
        if decoded[address] == "rts":
            break
    return [*cuts, compiled.symbols["platform_sas_process"],
            compiled.symbols["kernel_test_owner_returned"]]


def irq_kernel(compiled) -> Kernel:
    kernel = Kernel(compiled, (OWNER,), tx_busy=0)
    for shadow, _ in OUTPUT_SHADOWS:  # distinct values make the root's output writes visible
        kernel.cpu.write_u16(shadow, shadow & 0xFFFF)
    return kernel


def test_irq5_at_every_bridge_boundary_stays_on_the_active_guarded_stack(compiled):
    baseline = irq_kernel(compiled)
    baseline.start("kernel_test_owner_returned")
    expected_registers, expected_ram = baseline.registers(), baseline.ram()
    kinds = set()
    for cut in bridge_cuts(compiled):
        kernel = irq_kernel(compiled)
        cpu, private = kernel.cpu, kernel.private
        native = kernel.native(kernel.live("kernel_test_owner_id"))
        kernel.start("kernel_test_owner")
        kernel.resume(cut)
        sp, sr, registers, before = (
            cpu.uc.reg_read(UC_M68K_REG_A7), cpu.read_sr(), kernel.registers(), kernel.ram())
        stacks = {"private": private, "native": native}
        (kind,) = [k for k, (low, high) in stacks.items() if low < sp <= high]
        kinds.add(kind)
        low, _ = stacks[kind]
        pushed = len(kernel.pushed)
        with cpu.written() as writes:
            kernel.interrupt(IRQ5)

        assert (cpu.uc.reg_read(UC_M68K_REG_PC), kernel.registers(), cpu.read_sr()) == (
            cut, registers, sr), hex(cut)
        for shadow, output in OUTPUT_SHADOWS:
            assert cpu.read_u16(output) == cpu.read_u16(shadow)
        writes += kernel.pushed[pushed:]
        frame = {a for a in writes if any(lo <= a < hi for lo, hi in stacks.values())}
        assert frame and all(low <= a < sp for a in frame), hex(cut)
        after = kernel.ram()
        changed = {RAM[0] + i for i in range(RAM[1]) if before[i] != after[i]}
        assert changed <= frame, hex(cut)

        kernel.resume("kernel_test_owner_returned")
        assert kernel.registers() == expected_registers, hex(cut)
        final = kernel.ram()
        assert {RAM[0] + i for i in range(RAM[1]) if final[i] != expected_ram[i]} <= frame
    assert kinds == {"private", "native"}
