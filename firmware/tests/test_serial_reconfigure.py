"""Real VRTX drain, deletion and unchanged native serial reinitialization.

Full production build plus test-only task callsites; no core/storage/kernel or
UART-transmit leaves substituted. The owner calls the actual SAS bridge, and
configuration task 7 enters the patched 0xc1a8 three-call block through 0xc1ba.
The original suspend/resume wrappers and 16614 execute before the drain gate;
this bounded fixture does not execute the communication-test UI at 132ee.

Kernel's explicit TRAP0/RTE model and test-only IRQ/service shims supply chosen
scheduling events, not physical timing. One shim advances the native tick;
another changes owner priority through native SC4 to exercise post-before-pend.
These priorities/events are test inputs, not a claimed machine chronology.
Ports are disabled; the native initialized-config marker avoids virgin RTC
initialization. Native 15836/152ec run, but no enabled-port tasks are created.
Test callers supply received packets and TX-complete events. No TCB/context is
repaired by the host, and no reboot occurs after cancellation/draining.
"""

from __future__ import annotations

import zlib

import pytest
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.m68k_const import UC_M68K_REG_D0, UC_M68K_REG_D1

from firmware.tests.cpu import CpuError
from firmware.tests.kernel import (
    HOST_ID,
    IPL,
    PEND,
    POST,
    STATE_DELAYED,
    STATE_DORMANT,
    STATUS,
    TCB_DELAY_NEXT,
    TCB_NEXT,
    TCB_STACK_TOP,
    TCB_STATE,
    WS_CURRENT,
    WS_DELAYED,
    WS_FREE,
    Kernel,
    compile_tasks,
)
from firmware.tests.support import (
    CONFIG_INITIALIZED,
    CONFIG_MAGIC,
    CREATE_PARENTS,
    DELETE_PARENTS,
    HOST_ADDRESS,
    HOST_PORT,
    INIT_PORTS,
    PERSONALITY,
    RECONFIGURE,
    RECONFIGURED,
    RX,
    e0_poll,
    e0_request,
    platform_constant,
    rx_buffer,
)
from firmware.tests.support import fw_constant as c

RESUME_PARENTS = 0x16614
SUSPEND, PEND_RETURN = 0x3CB0, 0x3A62
POST_RETURN, POST_TRAP = 0x3A42, 0x3A40
PORT_TASK_REGISTRY = 0x412D50
PORT_ENABLE = (0x412418, 0x41241C, 0x412420)
MAILBOX_WAIT, SUSPENDED = 2, 1
WS_LOCK, WS_USER_LOCK = 0x28, 0x29

TASKS = r"""
    .text
    .globl owner_id, owner_priority, config_id, config_priority
    .globl observer_id, observer_priority, idle_id, idle_priority
    .set owner_id, 13
    .set owner_priority, 20
    .set config_id, 7
    .set config_priority, 9
    .set observer_id, 21
    .set observer_priority, 5
    .set idle_id, 25
    .set idle_priority, 30
    .globl info_reply, status_reply, retry_reply
    .globl info_length, status_length, retry_length
    .set info_reply, 0xe00200
    .set status_reply, 0xe00300
    .set retry_reply, 0xe00400
    .set info_length, 0xe00600
    .set status_length, 0xe00604
    .set retry_length, 0xe00608

    .macro delay ticks
    pea \ticks
    jsr 0x3a1e
    addq.l #4,%sp
    .endm
    .macro request input, length, reply, output_length
    pea REPLY_CAPACITY
    pea \reply
    pea \length
    pea \input
    jsr fw_process
    lea 16(%sp),%sp
    move.l %d0,\output_length
    .endm
    .macro submit packet, length
    move.b #1,HOST_PORT
    move.b #HOST_ID,HOST_ADDRESS
    lea \packet,%a0
    lea RX,%a1
    move.w #\length-1,%d0
1:  move.b (%a0)+,(%a1)+
    dbra %d0,1b
    movea.l 0x74e0e,%a0
    clr.w (%a0)                    /* supplied UART TX-complete event */
    jsr platform_sas_handler
    .endm

    .globl owner, owner_returned, owner_delayed
owner:
    jsr platform_sas_handler
owner_returned:
    delay 1
owner_delayed:
    bra.s owner_delayed

    .globl config_task, config_resumed_parents
config_task:
    jsr 0x16614                    /* communication-test cleanup before case 11 */
config_resumed_parents:
    delay 1
    jmp 0xc1a8                    /* actual patched 18-byte block */

    .globl observer_queries, observer_done
observer_queries:
    delay 2
    request info_request, INFO_LENGTH, info_reply, info_length
    request status_request, STATUS_LENGTH, status_reply, status_length
    request begin_request, BEGIN_LENGTH, retry_reply, retry_length
    jsr platform_reconfigure      /* a second, inactive drain must not reuse a token */
    submit newer_frame, NEWER_FRAME_LENGTH
    bra.w observer_done

    .globl observer_closed, closed_probe_done
observer_closed:
    delay 2
    submit begin_frame, BEGIN_FRAME_LENGTH
closed_probe_done:
    delay 1
    jsr platform_reconfigure      /* repeated inactive drain */
    submit begin_frame, BEGIN_FRAME_LENGTH
    request info_request, INFO_LENGTH, info_reply, info_length
    request status_request, STATUS_LENGTH, status_reply, status_length
observer_done:
    bra.s observer_done

    .globl idle
idle:
    bra.s idle

    .macro irq_enter
    move.l %d0,-(%sp)
    moveq #0x16,%d0
    trap #0
    movem.l %d1-%d7/%a0-%a6,-(%sp)
    .endm
    .macro irq_exit
    movem.l (%sp)+,%d1-%d7/%a0-%a6
    moveq #0x11,%d0
    trap #0
    illegal
    .endm

    .globl test_tick_entry, test_promote_entry
test_tick_entry:
    irq_enter
    moveq #0x12,%d0
    trap #0
    irq_exit

test_promote_entry:
    irq_enter
    moveq #owner_id,%d1
    moveq #6,%d2                   /* test-only priority above config, below observer */
    moveq #4,%d0                   /* original SC4 changes the priority */
    trap #0
    irq_exit
    .section .note.GNU-stack,"",@progbits
"""


def begin(ident=1):
    return e0_request(c("OP_BEGIN_PROFILE"), ident, c("PROFILE_SIZE").to_bytes(2, "little") + bytes(4))


def frame(request):
    return rx_buffer(e0_poll(request, HOST_ID))


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    inputs = {"info_request": e0_request(c("OP_INFO")), "status_request": e0_request(c("OP_STATUS")),
              "begin_request": begin(), "begin_frame": frame(begin()), "newer_frame": frame(begin(2))}
    definitions = f".equ REPLY_CAPACITY, {c('REPLY_HEADER') + c('MAX_PAYLOAD')}\n"
    definitions += f".equ HOST_PORT, {HOST_PORT}\n.equ HOST_ADDRESS, {HOST_ADDRESS}\n.equ HOST_ID, {HOST_ID}\n.equ RX, {RX}\n"
    data = ".section .rodata\n"
    for name, value in inputs.items():
        length_name = name.removesuffix("_request").upper() + "_LENGTH"
        definitions += f".equ {length_name}, {len(value)}\n"
        data += f"{name}:\n.byte " + ",".join(map(str, value)) + "\n"
    return compile_tasks(tmp_path_factory, definitions + TASKS + data)


def task(entry, who):
    return entry, who + "_id", who + "_priority"


def setup(compiled, *, observer="observer_queries", active=True, stuck_tx=False):
    tasks = [task("config_task", "config"), task(observer, "observer"), task("idle", "idle")]
    if active:
        tasks.append(task("owner", "owner"))
    k = Kernel(compiled, tasks, tx_busy=int(stuck_tx), personality=PERSONALITY)
    k.cpu.call(k.symbol("platform_boot"))
    assert k.cpu.call(k.symbol("fw_is_faulted")) == 0
    assert k.cpu.call(k.symbol("fw_capabilities")) & c("CAP_PROFILE")
    k.cpu.write_u16(CONFIG_INITIALIZED, CONFIG_MAGIC)
    for address in PORT_ENABLE:
        assert k.cpu.read_u32(address) == 0
    if active:
        k.cpu.write_u8(PORT_TASK_REGISTRY, k.symbol("owner_id"))
        k.call(SUSPEND, k.symbol("owner_id"), 0, STATUS)
        assert k.cpu.read_u16(STATUS) == 0
        assert k.cpu.read_u8(k.live("owner_id") + TCB_STATE) == SUSPENDED
    k.cpu.write(RX, frame(begin()))
    return k


def journal(cpu):
    valid = []
    for index in range(2):
        data = cpu.read(c("OPERATION_JOURNAL") + index * c("JOURNAL_SIZE"), c("JOURNAL_SIZE"))
        crc = c("JOURNAL_CRC_OFFSET")
        if (int.from_bytes(data[c("JOURNAL_MARKER_OFFSET"):], "little") == c("COMMIT_MARKER")
                and int.from_bytes(data[:4], "little") == c("JOURNAL_OPERATION_MAGIC")
                and int.from_bytes(data[crc:crc + 4], "little") == zlib.crc32(data[:crc])):
            valid.append((int.from_bytes(data[4:8], "little"), int.from_bytes(data[8:16], "little"), data[17]))
    assert valid
    return max(valid)


class AfterRestore:
    """Stop AFTER the next SR restore, optionally armed by a journal commit.

    Never read SR inside an active hook. Journal cuts select durable status;
    configuration cuts select the first restored critical section, before the
    must_wait branch. Neither uses fixed PCs or instruction/occurrence counts.
    """
    def __init__(self, kernel, commit_status):
        self.k, self.status = kernel, commit_status
        self.markers = {c("OPERATION_JOURNAL") + i*c("JOURNAL_SIZE") + c("JOURNAL_MARKER_OFFSET")
                        for i in range(2)}
        self.armed, self.restoring = commit_status is None, False
        self.pc = None

    def write(self, uc, access, address, size, value, data):
        marker = int.from_bytes(c("COMMIT_MARKER").to_bytes(2, "little"), "big")
        if self.status is not None and address in self.markers and size == 2 and value == marker:
            self.armed = self.k.cpu.read_u8(address - c("JOURNAL_MARKER_OFFSET") + 17) == self.status

    def code(self, uc, pc, size, data):
        if self.restoring:
            self.pc = pc
            uc.emu_stop()
        elif self.armed and self.k.cpu.read_u16(pc) & 0xFFF8 == 0x46C0:
            self.restoring = True  # MOVE.W Dn,SR; stop on the next boundary


def after_restore(k, until, *, commit_status=None):
    cut = AfterRestore(k, commit_status)
    with (k.cpu.hook(UC_HOOK_MEM_WRITE, cut.write), k.cpu.hook(UC_HOOK_CODE, cut.code),
          pytest.raises(CpuError) as stopped):
        k.resume(until)
    assert stopped.value.code == "instruction-budget" and stopped.value.address == cut.pc
    assert k.cpu.read_sr() & IPL == 0
    if commit_status is not None:
        assert journal(k.cpu)[1:] == (1, commit_status)


def event(k, until, entry="test_tick_entry"):
    k.interrupt(4, entry=k.symbol(entry), until=until if isinstance(until, int) else k.symbol(until))


def mailbox(k):
    return k.cpu.read_u32(k.symbol("sas_drain_mailbox"))


def forbid(k, address):
    """Fail the test if execution reaches `address` within the block."""
    def fail(uc, pc, size, data):
        pytest.fail(f"unexpected execution at {pc:#x}")
    return k.cpu.hook(UC_HOOK_CODE, fail, begin=address, end=address)


def pend_results(k):
    results = []
    k.cpu.uc.hook_add(UC_HOOK_CODE,
        lambda uc, pc, size, data: results.append((uc.reg_read(UC_M68K_REG_D0), uc.reg_read(UC_M68K_REG_D1))),
        begin=PEND_RETURN, end=PEND_RETURN)
    return results


def assert_gate_context(k):
    assert k.cpu.read_u32(k.workspace + WS_CURRENT) == k.live("config_id")
    assert k.cpu.read_sr() & IPL == 0
    # Standard kernel entry increments 28; its final decrement leaves ff outside a service.
    assert k.cpu.read_u8(k.workspace + WS_LOCK) == 0xFF
    assert k.cpu.read_u8(k.workspace + WS_USER_LOCK) == 0


def response(k, name):
    length = k.cpu.read_u32(k.symbol(name + "_length"))
    assert c("REPLY_HEADER") <= length <= c("REPLY_HEADER") + c("MAX_PAYLOAD")
    return k.cpu.read(k.symbol(name + "_reply"), length)


def assert_published(k):
    info, status = response(k, "info"), response(k, "status")
    assert info[6] == status[6] == c("OK")
    info, status = info[c("REPLY_HEADER"):], status[c("REPLY_HEADER"):]
    assert int.from_bytes(info[4:12], "little") == 2
    assert int.from_bytes(status[:8], "little") == 1
    assert status[9] == c("OK") and status[19] == 0


@pytest.mark.parametrize("status", [c("ACCEPTED"), c("OK")], ids=["reservation", "final"])
@pytest.mark.parametrize("order", ["pend-first", "post-first"])
def test_active_core_finishes_before_native_deletion(compiled, status, order):
    k = setup(compiled)
    owner = k.live("owner_id")
    top = k.cpu.read_u32(owner + TCB_STACK_TOP)
    resumed = k.cpu.record_visits(RESUME_PARENTS)
    k.start("owner")
    assert resumed and k.cpu.read_u8(owner + TCB_STATE) == 0
    after_restore(k, "owner_returned", commit_status=status)
    event(k, RECONFIGURE)
    assert_gate_context(k)
    returns = pend_results(k)
    with forbid(k, DELETE_PARENTS):
        if order == "post-first":
            after_restore(k, PEND)
            assert mailbox(k) == 0
            event(k, POST_RETURN, "test_promote_entry")
            assert mailbox(k) == platform_constant("SAS_QUIESCED")
            assert k.cpu.read_u32(k.workspace + WS_CURRENT) == owner
            assert returns == []
        else:
            k.resume(PEND)
            assert mailbox(k) == 0
            k.resume(POST_TRAP)
            config = k.live("config_id")
            assert k.cpu.read_u8(config + TCB_STATE) == MAILBOX_WAIT
            assert config not in k.chain(k.workspace + WS_DELAYED, TCB_DELAY_NEXT)
            assert mailbox(k) == 0
        assert journal(k.cpu)[1:] == (1, c("OK"))
    k.resume(DELETE_PARENTS)
    assert returns == [(0, platform_constant("SAS_QUIESCED"))]
    assert mailbox(k) == 0
    retired = k.cpu.record_visits(k.symbol("owner_returned"))
    k.resume(RECONFIGURED)
    assert k.live("owner_id") is None
    assert owner in k.chain(k.workspace + WS_FREE, TCB_NEXT)
    assert k.cpu.read_u8(owner + TCB_STATE) & STATE_DORMANT
    assert k.cpu.read_u32(owner + TCB_STACK_TOP) == top
    event(k, "observer_done")
    assert retired == []
    assert_published(k)
    assert response(k, "retry")[6] == c("OK")
    assert mailbox(k) == 0
    assert len(returns) == 1  # repeated inactive drain did not consume a stale token
    assert journal(k.cpu)[1:] == (2, c("OK"))  # new E0 admitted after reopening


@pytest.mark.parametrize("cut", [INIT_PORTS, CREATE_PARENTS], ids=["initialize", "recreate"])
def test_closed_admission_and_repeated_inactive_drain(compiled, cut):
    k = setup(compiled, observer="observer_closed", active=False)
    services = k.cpu.record_visits(PEND, POST)
    core = k.cpu.record_visits(k.symbol("fw_process"))
    before = journal(k.cpu)
    k.start("idle")
    event(k, RECONFIGURE)
    assert_gate_context(k)
    k.resume(cut)
    event(k, "closed_probe_done")
    assert core == [] and journal(k.cpu) == before
    assert k.cpu.read_u8(k.tx_buffer) == k.cpu.read_u8(HOST_ADDRESS) | 0x80
    k.resume(RECONFIGURED)
    event(k, "observer_done")
    assert_published(k)
    assert journal(k.cpu)[1:] == (1, c("OK"))
    assert core  # valid E0 reached core after reopening
    assert services == [] and mailbox(k) == 0


def test_stuck_post_core_transmit_does_not_block_reconfiguration(compiled):
    k = setup(compiled, stuck_tx=True)
    owner = k.live("owner_id")
    services = k.cpu.record_visits(PEND, POST)
    tx_wait = k.cpu.record_visits(0x14CAA)  # native TX-busy loop's sc_delay call
    returned = k.cpu.record_visits(k.symbol("owner_returned"))
    k.start("owner")
    k.resume("idle")
    assert tx_wait and returned == []
    assert owner in k.chain(k.workspace + WS_DELAYED, TCB_DELAY_NEXT)
    assert k.cpu.read_u8(owner + TCB_STATE) == STATE_DELAYED
    assert journal(k.cpu)[1:] == (1, c("OK"))
    assert k.cpu.read_u16(k.tx_busy) != 0
    with forbid(k, PEND):
        event(k, RECONFIGURED)
    assert k.live("owner_id") is None
    assert owner in k.chain(k.workspace + WS_FREE, TCB_NEXT)
    event(k, "observer_done")
    assert_published(k)
    assert response(k, "retry")[6] == c("OK")
    assert services == [] and mailbox(k) == 0
