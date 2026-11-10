"""Shared inputs and images for firmware tests; derive limits from the production ABI header."""

import json
import re
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest
from unicorn.m68k_const import UC_M68K_REG_A7

from firmware.build import BuildResult, build
from firmware.tests.cpu import ENTRY_SR, TRAP, Cpu, CpuError
from tools.sas_protocol import with_crc

ROOT = Path(__file__).resolve().parents[2]
MAIN_ROM = ROOT / "roms/s6000_main_S6M00002B5.bin"
PERSONALITY = (ROOT / "roms/personality_E595211X-05.bin").read_bytes()
SOURCE = ROOT / "firmware/src"
PRODUCTION_SPEC = ROOT / "firmware/patches.json"
PRODUCTION_LINK = ROOT / "firmware/link.ld"
# Native SAS receive buffers, shared with platform.c and original-ROM framing.
RX, RX_STRIDE = 0x4106F8, 0x340
# Native SAS port state read by the production C body and native transmit.
HOST_PORT, HOST_ADDRESS = 0x43E8D6, 0x43E8DC
# Native SAS transmit entry; its stack holds (port, frame pointer, kind) above the return.
NATIVE_TRANSMIT = 0x14C66
E0_POLL = 0xE0
# fw.h platform ports: an aligned two-byte storage write is atomic; a power cut leaves it whole.
STORAGE_WORD = 2
# Native fatal sink (IRQ-AUDIT.md, SETTLEMENT-GUARD.md): TRAP #1 vectors to FATAL_HANDLER, whose
# diagnostic path records the stage, the trapping PC and a count, then spins in the halt loop.
TRAP1 = TRAP | 1
FATAL_HANDLER = 0xFC4
FATAL_PATH = frozenset((FATAL_HANDLER, 0x13EE, 0x1274, 0x1246))
FATAL_HALT, FATAL_HALT_NEXT = 0x123C, 0x1242
FATAL_STAGE, FATAL_RETURN_PC, FATAL_COUNT = 0x4385B2, 0x4385BA, 0x4385AC
FATAL_STAGE_TRAP = 0xFF
T2_ENABLE = 0x5D790  # native IRQ6 calibration site that enables timer 2
# Native routines with motor or timer side effects a fatal halt must never reach: motor
# fault, motor phase writer, motor initialization, timer 2 enable (IRQ-AUDIT.md,
# INITIAL-ROOTS.md).
MOTOR_AND_TIMER_ROUTINES = frozenset((0x5FF76, 0x60254, 0x5CF64, T2_ENABLE))
NORMAL_START = 0x61172  # native normal game start, FUN_00061172
TASK6_ENTRY = 0x9F2A
# Configuration task 7's serial reconfiguration block and the three native calls in it.
RECONFIGURE, RECONFIGURED = 0xC1A8, 0xC1BA
DELETE_PARENTS, INIT_PORTS, CREATE_PARENTS = 0x16682, 0x15836, 0x152EC
CONFIG_INITIALIZED, CONFIG_MAGIC = 0x43C332, 0x55AA
SAFERAM, SAFERAM_SIZE = 0x438000, 0x8000
# I/O pages (S6000Setup.java, IRQ-AUDIT.md): DEVICE_PAGE holds the DUARTs and the VIA
# timers; PORT_PAGE the output, coil and input ports and the watchdog.
DEVICE_PAGE, PORT_PAGE = 0xFE1000, 0xFF1000
VIA_IFR, VIA_IER = 0xFE10DB, 0xFE10DD
VIA_TIMER_REGISTERS = range(0xFE10C8, VIA_IER + 1)  # T1 counter through IER
COILS, WATCHDOG = 0xFF100C, 0xFF101E
# IRQ5 root 0x26e4 copies each RAM shadow to its output port.
OUTPUT_SHADOWS = ((0x412426, PORT_PAGE), (0x412428, PORT_PAGE + 2))


def _lookup(*headers: str, prefix: str) -> Callable[[str], int]:
    """The numeric #define constants of firmware/src `headers`, looked up by their name
    without `prefix`, the namespace the headers share."""
    constants = {
        key: int(value, 0)
        for header in headers
        for key, value in re.findall(
            r"^#define (\w+) (0x[0-9a-fA-F]+|[0-9]+)u?$", (SOURCE / header).read_text(), re.MULTILINE
        )
    }

    def constant(name: str) -> int:
        return constants[prefix + name]

    return constant


fw_constant = _lookup("fw.h", prefix="FW_")
commission_constant = _lookup("commission.h", "commission_io.h", prefix="CM_")
platform_constant = _lookup("platform.h", prefix="PLATFORM_")


def u8(result: int) -> int:
    """A uint8_t or bool C result from D0: GCC leaves the upper bytes unspecified."""
    return result & 0xFF


def e0_request(op: int, ident: int = 0, payload: bytes = b"") -> bytes:
    """Core's request (fw.h): magic, VERSION, op, id (u64 LE), payload."""
    return b"S6RE" + bytes((fw_constant("VERSION"), op)) + ident.to_bytes(8, "little") + payload


def e0_poll(data: bytes, address: int = 1) -> bytes:
    """The private long poll carrying `data` (a request or reply) as the native SAS
    receiver and transmitter frame it: address, E0, length, data, CRC."""
    return with_crc(bytes((address, E0_POLL, len(data))) + data)


def rx_buffer(poll: bytes) -> bytes:
    """A native receive buffer holding `poll`: its length, then the poll."""
    return bytes((len(poll),)) + poll


def receive(cpu, poll: bytes, port: int = 1) -> None:
    """Place `poll` in the native receive buffer of `port` as the native receiver does."""
    cpu.write_u8(HOST_PORT, port)
    cpu.write(RX + (port - 1) * RX_STRIDE, rx_buffer(poll))


def transmitted(cpu) -> tuple[int, bytes]:
    """Stopped at NATIVE_TRANSMIT's entry: the port and the frame it was asked to send."""
    sp = cpu.uc.reg_read(UC_M68K_REG_A7)
    port, pointer, kind = (cpu.read_u32(sp + offset) for offset in (4, 8, 12))
    assert kind == 2
    return port, cpu.read(pointer + 1, cpu.read_u8(pointer))


def trap1_at(cpu: Cpu, error: CpuError) -> int:
    """The address of the TRAP #1 the guarded CPU refused with `error`."""
    assert error.code == "exception"
    assert cpu.read_u16(error.address) == TRAP1
    return error.address


def run_to_platform_fatal(cpu: Cpu, compiled: BuildResult, start: int, until: int) -> int:
    """Run from `start` towards `until`; the guarded CPU must refuse the extension's TRAP #1
    at platform_fatal first. Returns the trap's address."""
    with pytest.raises(CpuError) as failure:
        cpu.run(start, until)
    trap = trap1_at(cpu, failure.value)
    assert trap == compiled.symbols["platform_fatal"]
    return trap


def run_native_fatal(cpu: Cpu, trap: int) -> set[int]:
    """Take the TRAP #1 stopped at `trap` into the real native handler, run its diagnostic
    to the halt loop, check the record it leaves with interrupts masked, and go once around
    the loop. Returns every address executed."""
    handler = cpu.enter_trap(trap)
    assert handler == FATAL_HANDLER
    with cpu.visits() as visits:
        cpu.run(handler, FATAL_HALT)
        assert FATAL_PATH <= {visit.pc for visit in visits}
        assert cpu.read_u16(FATAL_STAGE) == FATAL_STAGE_TRAP
        assert cpu.read_u32(FATAL_RETURN_PC) == trap + 2
        assert cpu.read_u32(FATAL_COUNT) == 1
        assert cpu.read_sr() & ENTRY_SR == ENTRY_SR
        cpu.run(FATAL_HALT, FATAL_HALT_NEXT)
        cpu.run(FATAL_HALT_NEXT, FATAL_HALT)
    return {visit.pc for visit in visits}


def production_patches() -> list[dict]:
    return json.loads(PRODUCTION_SPEC.read_text())["patches"]


def production_link(entry: str = "ext_boot", **symbols: int) -> str:
    """firmware/link.ld entered at `entry`, plus test-only absolute symbols."""
    link = PRODUCTION_LINK.read_text()
    assert "ENTRY(ext_boot)" in link
    link = link.replace("ENTRY(ext_boot)", f"ENTRY({entry})")
    return link + "".join(f"{name} = {value:#x};\n" for name, value in symbols.items())


def build_production(tmp_path_factory) -> BuildResult:
    """The image firmware/patches.json publishes, built from the checkout itself."""
    return build(PRODUCTION_SPEC, tmp_path_factory.mktemp("production"))


def build_variant(
    tmp_path_factory,
    name: str,
    *,
    sources: Iterable[str] | None = None,
    files: Mapping[str, str] | None = None,
    link: str | None = None,
    patches: list[dict] | None = None,
) -> BuildResult:
    """An image the production builder makes from a staged copy of firmware/src.

    `sources` are the firmware/src files copied, all of them when None; `files` are
    test-only or rewritten sources by name. `link` replaces firmware/link.ld and
    `patches` the production patch list; `patches=[]` links the extension into the
    unpatched main ROM."""
    root = tmp_path_factory.mktemp(f"{name}-source")
    src = root / "src"
    if sources is None:
        shutil.copytree(SOURCE, src)
    else:
        src.mkdir()
        for source in sources:
            shutil.copyfile(SOURCE / source, src / source)
    for file, text in (files or {}).items():
        (src / file).write_text(text)
    (root / "link.ld").write_text(production_link() if link is None else link)
    spec = json.loads(PRODUCTION_SPEC.read_text())
    spec["base"]["path"] = str(MAIN_ROM)
    if patches is not None:
        spec["patches"] = patches
    (root / "patches.json").write_text(json.dumps(spec))
    return build(root / "patches.json", tmp_path_factory.mktemp(f"{name}-build"))
