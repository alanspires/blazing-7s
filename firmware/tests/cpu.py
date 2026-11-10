"""Execute real compiled M68000 code, not a model of the extension's behavior.

Unicorn does not fully enforce the M68000 ISA/alignment. Native objdump decodes
executed opcodes as 68000 and memory hooks reject odd word/long accesses. Neither
these checks nor this harness model peripherals, asynchronous IRQs or timing.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple, TypeVar

from unicorn import (
    UC_ARCH_M68K,
    UC_HOOK_CODE,
    UC_HOOK_INTR,
    UC_HOOK_MEM_READ,
    UC_HOOK_MEM_WRITE,
    UC_MODE_BIG_ENDIAN,
    UC_PROT_EXEC,
    UC_PROT_READ,
    UC_PROT_WRITE,
    Uc,
    UcError,
)
from unicorn.m68k_const import (
    UC_CPU_M68K_M68000,
    UC_M68K_REG_A0,
    UC_M68K_REG_A1,
    UC_M68K_REG_A2,
    UC_M68K_REG_A3,
    UC_M68K_REG_A4,
    UC_M68K_REG_A5,
    UC_M68K_REG_A6,
    UC_M68K_REG_A7,
    UC_M68K_REG_D0,
    UC_M68K_REG_D1,
    UC_M68K_REG_D2,
    UC_M68K_REG_D3,
    UC_M68K_REG_D4,
    UC_M68K_REG_D5,
    UC_M68K_REG_D6,
    UC_M68K_REG_D7,
    UC_M68K_REG_PC,
    UC_M68K_REG_SR,
)

REGISTERS = (
    UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3,
    UC_M68K_REG_D4, UC_M68K_REG_D5, UC_M68K_REG_D6, UC_M68K_REG_D7,
    UC_M68K_REG_A0, UC_M68K_REG_A1, UC_M68K_REG_A2, UC_M68K_REG_A3,
    UC_M68K_REG_A4, UC_M68K_REG_A5, UC_M68K_REG_A6,
)


def marked_registers(registers: Iterable[int] = REGISTERS) -> dict[int, int]:
    """A distinct, recognizable value for each register, to prove code preserves them."""
    return {register: 0xA00000 + index * 0x1234 for index, register in enumerate(registers)}


PAGE_SIZE = 4096
STACK_BASE = 0xE00000
STACK_SIZE = 0x10000
STACK_TOP = STACK_BASE + STACK_SIZE
ENTRY_SR = 0x2700  # supervisor, every interrupt level masked
SUPERVISOR, TRACE = 0x2000, 0x8000
TRAP, TRAP_VECTOR_BASE = 0x4E40, 32  # TRAP #n is TRAP | n, vectored through TRAP_VECTOR_BASE + n
RETURN_ADDRESS = 0xF00000
DEFAULT_MAX_INSNS = 1_000_000
DECODE_CHUNK_BYTES = 256
MAX_INSTRUCTION_BYTES = 10

T = TypeVar("T")


class Access(NamedTuple):
    """One bus access a memory hook saw."""

    kind: int  # UC_MEM_READ or UC_MEM_WRITE
    address: int
    size: int
    value: int  # the stored value, masked to `size`; Unicorn reports reads as 0

    @property
    def addresses(self) -> range:
        return range(self.address, self.address + self.size)


class Visit(NamedTuple):
    """One arrival of execution at an instruction, before it executes."""

    pc: int
    sp: int


class CpuError(RuntimeError):
    def __init__(self, code: str, address: int, detail: str = ""):
        self.code = code
        self.address = address
        super().__init__(f"{code} at 0x{address:08x}: {detail}")


@lru_cache(maxsize=4096)
def decode(code: bytes, address: int) -> dict[int, str]:
    objdump = shutil.which("m68k-elf-objdump")
    if objdump is None:
        raise RuntimeError("m68k-elf-objdump is required for strict M68000 execution")
    with tempfile.TemporaryDirectory(prefix="s6000-decode-") as directory:
        path = Path(directory) / "code.bin"
        path.write_bytes(code)
        result = subprocess.run(
            [
                objdump,
                "-D",
                "-z",
                "-b",
                "binary",
                "-m",
                "m68k:68000",
                "--no-show-raw-insn",
                f"--adjust-vma={address}",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    return {
        int(match[1], 16): match[2]
        for line in result.stdout.splitlines()
        if (match := re.match(r"\s*([0-9a-f]+):\s+(\S+)", line))
        and int(match[1], 16) < address + DECODE_CHUNK_BYTES
    }


class Cpu:
    def __init__(
        self,
        image: bytes,
        personality: bytes | None = None,
        ram: tuple[tuple[int, int], ...] = ((0x400000, 0x40000),),
    ):
        if not image:
            raise ValueError("An executable image is required")
        self.uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_M68K_M68000)
        self._code = [(0, bytes(image))]
        if personality is not None:
            self._code.append((0x100000, bytes(personality)))
        for address, data in self._code:
            self.uc.mem_map(
                address,
                (len(data) + PAGE_SIZE - 1) // PAGE_SIZE * PAGE_SIZE,
                UC_PROT_READ | UC_PROT_EXEC,
            )
            self.uc.mem_write(address, data)
        for address, size in ram:
            self.uc.mem_map(address, size, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(STACK_BASE, STACK_SIZE, UC_PROT_READ | UC_PROT_WRITE)
        self.uc.mem_map(RETURN_ADDRESS, PAGE_SIZE, UC_PROT_READ | UC_PROT_EXEC)
        status_reader = b"\x4e\x71\x40\xf9" + STACK_BASE.to_bytes(4, "big")
        self.uc.mem_write(RETURN_ADDRESS, status_reader)
        self._code.append((RETURN_ADDRESS, status_reader))
        self._decoded: dict[int, str] = {}
        self._fault: CpuError | None = None
        self._until: int | None = None
        self.instructions = 0
        self.uc.hook_add(UC_HOOK_CODE, self._instruction)
        self.uc.hook_add(UC_HOOK_INTR, self._interrupt)
        self.uc.hook_add(UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE, self._memory)
        self.uc.reg_write(UC_M68K_REG_SR, ENTRY_SR)
        self.uc.reg_write(UC_M68K_REG_A7, STACK_TOP)

    def _fail(self, code: str, address: int, detail: str = "") -> None:
        if self._fault is None:
            self._fault = CpuError(code, address, detail)
        self.uc.emu_stop()

    def _instruction(self, uc: Uc, address: int, size: int, data: object) -> None:
        # Unicorn can chain cached return blocks past emu_start's until address.
        if address == self._until:
            uc.emu_stop()
            return
        self.instructions += 1
        if address % 2:
            self._fail("alignment", address)
            return
        if address not in self._decoded:
            for start, code in self._code:
                if start <= address < start + len(code):
                    offset = address - start
                    # Decode past the cache boundary so a split final instruction
                    # is not cached as an invalid opcode by objdump.
                    limit = offset + DECODE_CHUNK_BYTES + MAX_INSTRUCTION_BYTES
                    self._decoded.update(decode(code[offset:limit], address))
                    break
        instruction = self._decoded.get(address)
        if (
            instruction is None
            or instruction.startswith(".")
            or instruction == "illegal"
        ):
            self._fail("instruction", address, str(instruction))

    def _interrupt(self, uc: Uc, vector: int, data: object) -> None:
        self._fail("exception", uc.reg_read(UC_M68K_REG_PC), str(vector))

    def _memory(
        self, uc: Uc, access: int, address: int, size: int, value: int, data: object
    ) -> None:
        if size > 1 and address % 2:
            self._fail("alignment", address)

    def push_supervisor_exception(self, sr: int, pc: int, new_sr: int) -> int:
        """Explicit exception entry; Unicorn stops instead of supplying this frame."""
        frame = sr.to_bytes(2, "big") + pc.to_bytes(4, "big")
        sp = self.uc.reg_read(UC_M68K_REG_A7) - len(frame)
        self.write(sp, frame)
        self.uc.reg_write(UC_M68K_REG_A7, sp)
        self.uc.reg_write(UC_M68K_REG_SR, new_sr)
        return sp

    def enter_trap(self, address: int) -> int:
        """Exception entry for the TRAP #n stopped at `address`: push SR and the next PC,
        enter supervisor mode without trace. Returns the handler from the vector table."""
        opcode = self.read_u16(address)
        if opcode & 0xFFF0 != TRAP:
            raise ValueError(f"No TRAP at 0x{address:08x}")
        sr = self.read_sr()
        self.push_supervisor_exception(sr, address + 2, (sr | SUPERVISOR) & ~TRACE)
        return self.read_u32((TRAP_VECTOR_BASE + (opcode & 0xF)) * 4)

    @contextmanager
    def hook(
        self, kind: int, callback: Callable[..., object], *, begin: int = 1, end: int = 0
    ) -> Iterator[None]:
        """`callback` hooked for the block only, removed even when the block raises.
        Unicorn's default range (begin 1, end 0) covers every address."""
        handle = self.uc.hook_add(kind, callback, begin=begin, end=end)
        try:
            yield
        finally:
            self.uc.hook_del(handle)

    @contextmanager
    def accesses(
        self, *ranges: tuple[int, int], kind: int = UC_HOOK_MEM_WRITE
    ) -> Iterator[list[Access]]:
        """The block's bus accesses of hook `kind`, in order: every address, or only
        those starting inside `ranges` ((start, size) pairs)."""
        handle, log = self._record(ranges, kind)
        try:
            yield log
        finally:
            self.uc.hook_del(handle)

    def record_accesses(
        self, *ranges: tuple[int, int], kind: int = UC_HOOK_MEM_WRITE
    ) -> list[Access]:
        """`accesses` for the rest of this Cpu's life, for a harness whose checks cover
        every access its machine makes. Not a context manager: a generator left open
        would remove its hook whenever it is garbage-collected."""
        return self._record(ranges, kind)[1]

    @contextmanager
    def visits(self, *addresses: int) -> Iterator[list[Visit]]:
        """The block's arrivals at `addresses`, in order: at every instruction without
        addresses."""
        handles, log = self._visit(addresses)
        try:
            yield log
        finally:
            for handle in handles:
                self.uc.hook_del(handle)

    def record_visits(self, *addresses: int) -> list[Visit]:
        """`visits` for the rest of this Cpu's life; not a context manager, as for
        `record_accesses`."""
        return self._visit(addresses)[1]

    def _visit(self, addresses: tuple[int, ...]) -> tuple[list[int], list[Visit]]:
        log: list[Visit] = []

        def record(uc: Uc, pc: int, size: int, data: object) -> None:
            log.append(Visit(pc, uc.reg_read(UC_M68K_REG_A7)))

        bounds = [(address, address) for address in addresses] or [(1, 0)]
        return [self.uc.hook_add(UC_HOOK_CODE, record, begin=begin, end=end) for begin, end in bounds], log

    def _record(self, ranges: tuple[tuple[int, int], ...], kind: int) -> tuple[int, list[Access]]:
        log: list[Access] = []
        begin, end = (
            (min(start for start, _ in ranges), max(start + size for start, size in ranges) - 1)
            if ranges else (1, 0)
        )

        def record(uc: Uc, access: int, address: int, size: int, value: int, data: object) -> None:
            if not ranges or any(start <= address < start + length for start, length in ranges):
                # Unicorn may report a sign-extended register value for a narrower store.
                log.append(Access(access, address, size, value & ((1 << (size * 8)) - 1)))

        return self.uc.hook_add(kind, record, begin=begin, end=end), log

    @contextmanager
    def written(self, *ranges: tuple[int, int]) -> Iterator[list[int]]:
        """Every byte address the block stores to, in store order, filled when the block
        ends; `ranges` as for `accesses`."""
        addresses: list[int] = []
        with self.accesses(*ranges) as stores:
            yield addresses
        addresses.extend(address for store in stores for address in store.addresses)

    @contextmanager
    def cut_points(
        self,
        snapshot: Callable[[], T],
        *ranges: tuple[int, int],
        store_size: int | None = None,
    ) -> Iterator[list[T]]:
        """Power-cut images of the block: `snapshot()` immediately before each store
        into `ranges` ((start, size) pairs), then once more when the block completes.
        With `store_size`, the memory's atomic unit, any other store there fails the run."""
        images: list[T] = []

        def store(uc: Uc, access: int, address: int, size: int, value: int, data: object) -> None:
            if any(start <= address < start + length for start, length in ranges):
                if store_size is not None and (size != store_size or address % store_size):
                    self._fail("store", address, f"{size}-byte store, not one aligned {store_size}")
                images.append(snapshot())

        with self.hook(UC_HOOK_MEM_WRITE, store):
            yield images
        images.append(snapshot())

    def map_io(self, address: int, size: int = PAGE_SIZE) -> None:
        """Map explicitly requested test memory, not a simulated peripheral."""
        if address % PAGE_SIZE or size <= 0 or size % PAGE_SIZE:
            raise ValueError("I/O mappings must be page-aligned")
        self.uc.mem_map(address, size, UC_PROT_READ | UC_PROT_WRITE)

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def write(self, address: int, data: bytes | bytearray | memoryview) -> None:
        data = bytes(data)
        if any(
            address < start + len(code) and start < address + len(data)
            for start, code in self._code
        ):
            raise ValueError(
                "Executable images are immutable; construct a new Cpu for new bytes"
            )
        self.uc.mem_write(address, data)

    def read_registers(self, registers: Iterable[int] = REGISTERS) -> dict[int, int]:
        return {register: self.uc.reg_read(register) for register in registers}

    def read_sr(self) -> int:
        """Observe effective CCR while stopped; Unicorn's SR read corrupts lazy flags."""
        pc = self.uc.reg_read(UC_M68K_REG_PC)
        scratch = self.read(STACK_BASE, 2)
        instructions = self.instructions
        try:
            self.run(RETURN_ADDRESS + 2, RETURN_ADDRESS + 8, max_insns=1)
            return self.read_u16(STACK_BASE)
        finally:
            self.write(STACK_BASE, scratch)
            self.uc.reg_write(UC_M68K_REG_PC, pc)
            self.instructions = instructions

    def read_u8(self, address: int) -> int:
        return int.from_bytes(self.read(address, 1), "big")

    def read_u16(self, address: int) -> int:
        return int.from_bytes(self.read(address, 2), "big")

    def read_u32(self, address: int) -> int:
        return int.from_bytes(self.read(address, 4), "big")

    def write_u8(self, address: int, value: int) -> None:
        self.write(address, value.to_bytes(1, "big"))

    def write_u16(self, address: int, value: int) -> None:
        self.write(address, value.to_bytes(2, "big"))

    def write_u32(self, address: int, value: int) -> None:
        self.write(address, value.to_bytes(4, "big"))

    def run(
        self, start: int, until: int, *, max_insns: int = DEFAULT_MAX_INSNS
    ) -> None:
        if start % 2 or until % 2:
            raise CpuError("alignment", start if start % 2 else until)
        if max_insns <= 0:
            raise ValueError("Instruction budget must be positive")
        self._fault = None
        self._until = until
        self.instructions = 0
        try:
            self.uc.emu_start(start, until, count=max_insns)
        except UcError as exc:
            raise self._fault or CpuError(
                "unicorn", self.uc.reg_read(UC_M68K_REG_PC), str(exc)
            ) from exc
        finally:
            self._until = None
        if self._fault is not None:
            raise self._fault
        pc = self.uc.reg_read(UC_M68K_REG_PC)
        if pc != until:
            raise CpuError("instruction-budget", pc)

    def enter(
        self,
        *args: int,
        stack_top: int = STACK_TOP,
        sr: int = ENTRY_SR,
        registers: dict[int, int] | None = None,
    ) -> int:
        """Set up a 68000 GCC ABI call without running it: RETURN_ADDRESS and the
        32-bit stack arguments end at `stack_top`, then SR and preset registers.
        Returns the entry SP, which addresses RETURN_ADDRESS."""
        frame = b"".join(
            (value & 0xFFFFFFFF).to_bytes(4, "big") for value in (RETURN_ADDRESS, *args)
        )
        sp = stack_top - len(frame)
        self.write(sp, frame)
        self.set_state(sp, sr=sr, registers=registers)
        return sp

    def set_state(
        self, sp: int, *, sr: int = ENTRY_SR, registers: dict[int, int] | None = None
    ) -> None:
        """SP, SR and preset registers for a run that starts at any instruction."""
        self.uc.reg_write(UC_M68K_REG_SR, sr)
        self.uc.reg_write(UC_M68K_REG_A7, sp)
        for register, value in (registers or {}).items():
            self.uc.reg_write(register, value)

    def call(
        self,
        address: int,
        *args: int,
        stack_top: int = STACK_TOP,
        max_insns: int = DEFAULT_MAX_INSNS,
    ) -> int:
        """A complete C call through `enter`: D0 result; no initialized C runtime."""
        sp = self.enter(*args, stack_top=stack_top)
        self.run(address, RETURN_ADDRESS, max_insns=max_insns)
        if self.uc.reg_read(UC_M68K_REG_A7) != sp + 4:
            raise CpuError("stack", self.uc.reg_read(UC_M68K_REG_A7))
        return self.uc.reg_read(UC_M68K_REG_D0)
