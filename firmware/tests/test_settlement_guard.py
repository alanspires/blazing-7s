"""Unit tests of the settlement entry boundary, not financial-history witnesses.

Full production images and the real native kernel allocator are used. Native
prologue/call/return instructions provide controlled recursive stack shapes;
financial bodies between those cuts are deliberately not executed. Explicit
IRQ5/TRAP1 exception entry uses the shared architectural helper, not a timing
model. No executable image is changed after a Cpu has been constructed.
"""

from dataclasses import replace
from itertools import pairwise

import pytest
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC

from firmware.tests.cpu import (
    REGISTERS,
    RETURN_ADDRESS,
    Cpu,
    CpuError,
    marked_registers,
)
from firmware.tests.kernel import (
    CREATE,
    KERNEL_START,
    RAM,
    TCB_NEXT,
    WS_CURRENT,
    WS_FREE,
    WS_LIVE,
    Kernel,
)
from firmware.tests.support import (
    COILS,
    MAIN_ROM,
    MOTOR_AND_TIMER_ROUTINES,
    NORMAL_START,
    OUTPUT_SHADOWS,
    TASK6_ENTRY,
    VIA_TIMER_REGISTERS,
    WATCHDOG,
    build_production,
    run_native_fatal,
    trap1_at,
)

ORIGINAL = MAIN_ROM.read_bytes()
ENTRY, CONTINUE, EPILOGUE = 0x589BA, 0x589C2, 0x599B0
RECURSE, CHILD, CHILD_SAVED, CHILD_CALL = 0x5999E, 0x3D294, 0x3D296, 0x3D6DE
CHILD_RETURN, PARENT_RETURN = 0x3D6E4, 0x599A4
# Derive the native frame from LINK's displacement and the MOVEM register mask.
FRAME = 4 - int.from_bytes(ORIGINAL[ENTRY + 2 : ENTRY + 4], "big", signed=True)
SAVED = int.from_bytes(ORIGINAL[ENTRY + 6 : CONTINUE], "big").bit_count() * 4
FRAME += SAVED
PROTECTED = ((0x430000, 0x6000), (0x43C000, 0x4000))


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return build_production(tmp_path_factory)


def machine(compiled, domain="native", slot=0, *, running=False):
    kernel = Kernel(compiled, ())
    cpu = kernel.cpu
    if running:
        assert domain == "native"
        assert kernel.service(CREATE, TASK6_ENTRY, 6, 6) == 0
        kernel.run(KERNEL_START, TASK6_ENTRY)
        owner = cpu.read_u32(kernel.workspace + WS_CURRENT)
    else:
        # Real CREATE supplies saved contexts as well as allocated tops. This
        # distinguishes TCB+0x38 (saved SSP) from the allocation's TCB+0x40 top.
        for ident in range(1, kernel.tasks + 1):
            assert kernel.service(CREATE, TASK6_ENTRY, ident, ident) == 0
        slots = sorted(
            kernel.chain(kernel.workspace + WS_LIVE, TCB_NEXT),
            key=lambda t: kernel.native(t)[1],
        )
        # Unit boundary: select one real allocation without claiming that this
        # is a scheduler transition. Boot deliberately retains nonnull CURRENT.
        owner = slots[slot]
        cpu.write_u32(kernel.workspace + WS_CURRENT, owner)
    bounds = (
        kernel.native(owner)
        if domain == "native"
        else (
            compiled.symbols["SETTLEMENT_BOOT_BASE"],
            compiled.symbols["SETTLEMENT_BOOT_END"],
        )
    )
    return kernel, bounds


def prepare(cpu, sp, sr=0x201F):
    """A settlement call whose return address is at `sp`, with patterned registers."""
    cpu.enter(stack_top=sp + 4, sr=sr, registers=marked_registers())


def state(cpu):
    return cpu.read_registers((*REGISTERS, UC_M68K_REG_A7)), cpu.read_sr()


def protected(cpu):
    return tuple(cpu.read(start, length) for start, length in PROTECTED)


def enter(cpu, compiled, start=ENTRY):
    try:
        cpu.run(start, CONTINUE)
    except CpuError as error:
        assert trap1_at(cpu, error) == compiled.symbols["platform_fatal"]
        return False
    return True


def assert_boundary(compiled, domain, slot, delta, sr=0x201F):
    kernel, (low, _) = machine(compiled, domain, slot)
    cpu = kernel.cpu
    sp = low + compiled.symbols["SETTLEMENT_RESERVE"] + delta
    prepare(cpu, sp, sr)
    before, unchanged = state(cpu), protected(cpu)
    with cpu.written() as writes:
        allowed = enter(cpu, compiled)
    assert allowed == (delta >= 0)
    depth = FRAME if allowed else compiled.symbols["SETTLEMENT_SCRATCH"]
    assert writes and all(sp - depth <= address < sp for address in writes)
    if not allowed:
        assert min(writes) == sp - depth
    assert protected(cpu) == unchanged
    if not allowed:
        assert state(cpu) == before
        assert cpu.read_u32(sp) == RETURN_ADDRESS
        return
    original = Cpu(ORIGINAL)
    prepare(original, sp, sr)
    original.run(ENTRY, CONTINUE)
    assert state(cpu) == state(original)
    assert cpu.uc.reg_read(UC_M68K_REG_A7) == sp - FRAME
    # Uninitialized locals are not an ABI contract; saved registers/FP/return are.
    for address, length in ((sp - FRAME, SAVED), (sp - 4, 8)):
        assert cpu.read(address, length) == original.read(address, length)
    for executor in (cpu, original):
        executor.run(EPILOGUE, RETURN_ADDRESS)
    assert state(cpu) == state(original)
    assert state(cpu) == (before[0] | {UC_M68K_REG_A7: sp + 4}, before[1])


@pytest.mark.parametrize(
    "domain,slot", [("native", 0), ("native", 8), ("native", -1), ("boot", 0)]
)
@pytest.mark.parametrize("delta", [-2, 0, 2])
@pytest.mark.parametrize("sr", [0x2000, 0x201F, 0x2415, 0x270F])
def test_threshold_and_original_prologue_return_identity(
    compiled, domain, slot, delta, sr
):
    assert_boundary(compiled, domain, slot, delta, sr)


@pytest.mark.parametrize("domain", ["native", "boot"])
def test_first_repeated_admissions_and_recursive_refusal_use_actual_stack(
    compiled, domain
):
    kernel, (low, high) = machine(compiled, domain)
    cpu = kernel.cpu
    first = high - 4
    prepare(cpu, first)
    unchanged = protected(cpu)
    visits = cpu.record_visits(ENTRY)
    assert enter(cpu, compiled)
    while True:
        cpu.run(RECURSE, CHILD)
        cpu.run(CHILD, CHILD_SAVED)
        # This is a native linkage cut, not a claim that a financial state takes
        # CHILD_CALL. The actual JSR, guard and return shapes are executed.
        if not enter(cpu, compiled, CHILD_CALL):
            break
    entries = [visit.sp for visit in visits]
    assert entries[0] == first
    growth = FRAME + 4 + 4 + 4  # parent JSR, child's D2 save, child JSR
    assert all(a - b == growth for a, b in pairwise(entries))
    threshold = low + compiled.symbols["SETTLEMENT_RESERVE"]
    assert entries[-1] < threshold <= entries[-2]
    assert cpu.uc.reg_read(UC_M68K_REG_A7) == entries[-1]
    assert cpu.read_u32(entries[-1]) == CHILD_RETURN
    assert protected(cpu) == unchanged

    # A separate admitted chain exercises actual native epilogues. There is no
    # attempted recovery/unwind after the rejecting TRAP1 above.
    kernel, (low, high) = machine(compiled, domain)
    cpu = kernel.cpu
    prepare(cpu, high - 4)
    initial = state(cpu)
    assert enter(cpu, compiled)
    for _ in entries[1:-1]:
        cpu.run(RECURSE, CHILD)
        cpu.run(CHILD, CHILD_SAVED)
        assert enter(cpu, compiled, CHILD_CALL)
    for _ in entries[1:-1]:
        cpu.run(EPILOGUE, CHILD_RETURN)
        cpu.run(CHILD_RETURN, PARENT_RETURN)
    cpu.run(EPILOGUE, RETURN_ADDRESS)
    registers, sr = state(cpu)
    assert registers == initial[0] | {UC_M68K_REG_A7: high}
    # The genuine native D2 push/pop sets CCR; only guard entry promises its
    # preservation. System bits must remain unchanged throughout this chain.
    assert sr & ~0x1F == initial[1] & ~0x1F
    # No stateless guard residue can deny a later shallow invocation.
    prepare(cpu, high - 4)
    assert enter(cpu, compiled)


@pytest.mark.parametrize("domain", ["native", "boot"])
@pytest.mark.parametrize("delta", [-2, 0])
def test_irq5_at_every_executed_guard_instruction_boundary(compiled, domain, delta):
    def setup():
        kernel, bounds = machine(compiled, domain, running=domain == "native")
        low, _ = bounds
        prepare(kernel.cpu, low + compiled.symbols["SETTLEMENT_RESERVE"] + delta)
        for shadow, _ in OUTPUT_SHADOWS:
            kernel.cpu.write_u16(shadow, shadow & 0xFFFF)
        return kernel, bounds

    baseline, _ = setup()
    entry = compiled.symbols["game_settlement_entry"]
    end = min(a for a in compiled.symbols.values() if a > entry)
    with baseline.cpu.visits() as executed:
        assert enter(baseline.cpu, compiled) == (delta >= 0)
    cuts = [visit.pc for visit in executed if entry <= visit.pc < end]
    expected = state(baseline.cpu)
    assert cuts and len(cuts) == len(set(cuts))
    for cut in cuts:
        kernel, (low, _) = setup()
        cpu = kernel.cpu
        allocations = [
            kernel.native(tcb)
            for head in (WS_LIVE, WS_FREE)
            for tcb in kernel.chain(kernel.workspace + head, TCB_NEXT)
        ]
        allocations += [
            kernel.private,
            (
                compiled.symbols["SETTLEMENT_BOOT_BASE"],
                compiled.symbols["SETTLEMENT_BOOT_END"],
            ),
        ]
        cpu.run(ENTRY, cut)
        before, sp = state(cpu), cpu.uc.reg_read(UC_M68K_REG_A7)
        before_ram = kernel.ram()
        pushed = len(kernel.pushed)
        with cpu.written() as writes:
            kernel.interrupt(5)
        assert state(cpu) == before, hex(cut)
        assert cpu.uc.reg_read(UC_M68K_REG_PC) == cut
        for shadow, output in OUTPUT_SHADOWS:
            assert cpu.read_u16(output) == cpu.read_u16(shadow)
        # Classify writes on EVERY allocated stack, not only the selected one:
        # writes into an adjacent allocation/private arena must fail too.
        stack_writes = {
            a for a in writes if any(lo <= a < hi for lo, hi in allocations)
        } | set(kernel.pushed[pushed:])
        assert stack_writes and all(low <= a < sp for a in stack_writes)
        after_ram = kernel.ram()
        changed = {
            RAM[0] + i
            for i, (old, new) in enumerate(zip(before_ram, after_ram, strict=True))
            if old != new
        }
        # Balanced kernel counters may be written but must return to their old
        # state; heap, TCB, accounting and other non-stack corruption is visible.
        assert changed <= stack_writes
        assert enter(cpu, compiled, cut) == (delta >= 0)
        assert state(cpu) == expected, hex(cut)


@pytest.mark.parametrize("domain", ["native", "boot"])
def test_rejection_runs_native_trap1_diagnostic_and_halt_without_accounting_clear(
    compiled, domain
):
    kernel, (low, _) = machine(compiled, domain)
    cpu = kernel.cpu
    prepare(cpu, low + compiled.symbols["SETTLEMENT_RESERVE"] - 2)
    # Real credit47 BCD plus pending host amounts; no financial behavior is run.
    cpu.write(0x43D546, bytes.fromhex("12345678"))
    cpu.write_u32(0x43CD92, 37)
    cpu.write_u32(0x43D476, 91)
    cpu.write_u16(COILS, 0xA55A)
    unchanged = protected(cpu)
    assert not enter(cpu, compiled)
    with cpu.written() as writes:
        visited = run_native_fatal(cpu, compiled.symbols["platform_fatal"])
    assert not ({CONTINUE, NORMAL_START} | MOTOR_AND_TIMER_ROUTINES) & visited
    assert protected(cpu) == unchanged
    assert cpu.read_u16(COILS) == 0xA55A
    assert not set(VIA_TIMER_REGISTERS) & set(writes)
    assert not {COILS, COILS + 1} & set(writes)
    assert WATCHDOG in writes


def mutated(compiled, old, new):
    assert len(old) == len(new)
    start = compiled.symbols["game_settlement_entry"]
    end = min(a for a in compiled.symbols.values() if a > start)
    code = compiled.image[start:end]
    assert code.count(old) == 1
    image = compiled.image[:start] + code.replace(old, new) + compiled.image[end:]
    return replace(compiled, image=image)


@pytest.mark.parametrize(
    "fault",
    [
        "bypass",
        "threshold-low",
        "threshold-high",
        "saved-sp-owner",
        "unsigned-size",
        "boot-owner",
        "register-restore",
        "sr-restore",
        "prologue",
        "fatal-call",
    ],
)
def test_negative_controls_are_detected_by_boundary_contract(compiled, fault):
    reserve = compiled.symbols["SETTLEMENT_RESERVE"]
    domain, delta = "native", -2
    if fault == "bypass":
        image = (
            compiled.image[:ENTRY]
            + ORIGINAL[ENTRY:CONTINUE]
            + compiled.image[CONTINUE:]
        )
        broken = replace(compiled, image=image)
    elif fault.startswith("threshold"):
        adjustment = -2 if fault == "threshold-low" else 2
        broken = mutated(
            compiled,
            b"\x41\xe8" + reserve.to_bytes(2, "big"),
            b"\x41\xe8" + (reserve + adjustment).to_bytes(2, "big"),
        )
        delta = -2 if adjustment < 0 else 0
    elif fault == "saved-sp-owner":
        broken = mutated(compiled, bytes.fromhex("20680040"), bytes.fromhex("20680038"))
    elif fault == "unsigned-size":
        broken = mutated(
            compiled, bytes.fromhex("7000303827b6"), bytes.fromhex("4e71303827b6")
        )
    elif fault == "boot-owner":
        # The first conditional branch becomes unconditional; CURRENT wrongly
        # wins even when the actual stack belongs to bootstrap.
        start = compiled.symbols["game_settlement_entry"]
        code = compiled.image[start : start + 40]
        branch = next(i for i in range(0, len(code), 2) if code[i] == 0x65)
        old = code[branch : branch + 2]
        broken = mutated(compiled, old, b"\x60" + old[1:])
        domain = "boot"
    elif fault == "register-restore":
        broken = mutated(
            compiled,
            bytes.fromhex("4cdf010146df4e56"),
            bytes.fromhex("4cdf010246df4e56"),
        )
        delta = 0
    elif fault == "sr-restore":
        broken = mutated(
            compiled, bytes.fromhex("46df4e56ffb0"), bytes.fromhex("548f4e56ffb0")
        )
        delta = 0
    elif fault == "prologue":
        broken = mutated(compiled, bytes.fromhex("4e56ffb0"), bytes.fromhex("4e56ffae"))
        delta = 0
    else:
        target = compiled.symbols["platform_fatal"].to_bytes(4, "big")
        broken = mutated(compiled, b"\x4e\xf9" + target, b"\x4e\xb9" + target)
    with pytest.raises(AssertionError):
        assert_boundary(broken, domain, 0, delta)


def test_final_image_bounded_dispatch_loads_do_not_bypass_guard(compiled):
    # Positive byte-selector producer domains from the range-specific ingress
    # audit. a5e0's overlapping rows extend INTO extension ROM, so this must use
    # the final linked image, not the stock/frozen table or a literal census.
    domains = {
        "a5e0": {
            0x77CC6 + 120 * event + 4 * mode
            for event in range(256)
            for mode in range(256)
        },
        "a660": {0x78F86 + 4 * mode for mode in range(256)},
        "ad3a": {0x77CC6 + 4 * mode for mode in range(256)},
        "ad72": {0x77DB6 + 4 * mode for mode in range(256)},
        "serial": {0x744CC + 6 * index for index in range(256)},
        "protocol": {0x74AF2 + 4 * index for index in range(15)},
        "sas-state": {
            0x69902 + 48 * row + 4 * state for row in range(12) for state in range(256)
        },
    }
    guard = compiled.symbols["game_settlement_entry"]
    guard_end = min(a for a in compiled.symbols.values() if a > guard)
    for producer, addresses in domains.items():
        found = []
        for address in sorted(addresses):
            assert address + 4 <= len(compiled.image)
            target = (
                int.from_bytes(compiled.image[address : address + 4], "big") & 0xFFFFFF
            )
            assert not (
                ENTRY < target < 0x599BA
                or CHILD < target < 0x3D794
                or guard <= target < guard_end
            ), (producer, hex(address), hex(target))
            if target in (ENTRY, CHILD):
                found.append((address, target))
        assert found == ([(0x7837E, CHILD)] if producer == "a5e0" else [])
