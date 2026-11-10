"""Native pool allocation under the production configuration, not timing proof.

SC48 initialization and queue/task services execute unchanged kernel instructions.
The six boot-queue sizes are fixture inputs from the audited native creation
sites. Task entry addresses are real native programs but are not scheduled here;
blocking, resumption and deletion while running are covered by test_e0_kernel.
"""

from itertools import pairwise

import pytest

from firmware.tests.kernel import (
    CONFIG_POINTER,
    CONFIG_POOL_SIZE,
    CREATE,
    CREATE_QUEUE,
    DELETE,
    TCB_ID,
    TCB_NEXT,
    TCB_STACK_TOP,
    WS_FREE,
    WS_HEAP_END,
    WS_HEAP_FREE,
    WS_LIVE,
    Kernel,
    compile_tasks,
)
from firmware.tests.support import TASK6_ENTRY

BOOT_QUEUES = (64, 64, 64, 32, 32, 32)
QUEUE_HEADER_BYTES, QUEUE_ENTRY_BYTES = 14, 4  # native SC41 layout
REPLACEMENT_ENTRY = 0xB080
POOL_GUARD_BYTES = 64


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    return compile_tasks(tmp_path_factory, '.text\n.section .note.GNU-stack,"",@progbits\n')


def test_pool_allocates_queues_and_all_slots_then_recycles_without_growth(compiled):
    kernel = Kernel(compiled, ())
    cpu = kernel.cpu
    config = cpu.read_u32(CONFIG_POINTER)
    pool_end = kernel.workspace + cpu.read_u32(config + CONFIG_POOL_SIZE)
    slots = kernel.chain(kernel.workspace + WS_FREE, TCB_NEXT)
    assert len(slots) == kernel.tasks
    ranges = sorted(kernel.native(tcb) for tcb in slots)
    assert ranges[-1][1] == pool_end
    assert all(high - low == kernel.task_stack for low, high in ranges)
    assert all(left[1] == right[0] for left, right in pairwise(ranges))
    initial_free = cpu.read_u32(kernel.workspace + WS_HEAP_FREE)
    assert cpu.read_u32(kernel.workspace + WS_HEAP_END) + initial_free == ranges[0][0]
    kernel.plant(pool_end, pool_end + POOL_GUARD_BYTES)

    for ident, entries in enumerate(BOOT_QUEUES, 1):
        assert kernel.service(CREATE_QUEUE, ident, entries) == 0
    remaining = cpu.read_u32(kernel.workspace + WS_HEAP_FREE)
    assert remaining == initial_free - sum(
        QUEUE_HEADER_BYTES + QUEUE_ENTRY_BYTES * entries for entries in BOOT_QUEUES
    )
    heap_end = cpu.read_u32(kernel.workspace + WS_HEAP_END)
    assert heap_end + remaining == ranges[0][0]
    assert remaining > 0

    # Reinitializing an existing queue must not grow the bump allocation.
    assert kernel.service(CREATE_QUEUE, 1, BOOT_QUEUES[0]) != 0
    assert cpu.read_u32(kernel.workspace + WS_HEAP_END) == heap_end
    assert cpu.read_u32(kernel.workspace + WS_HEAP_FREE) == remaining

    identifiers = set(range(1, kernel.tasks + 1))
    for ident in sorted(identifiers):
        assert kernel.service(CREATE, TASK6_ENTRY, ident, ident) == 0
    live = kernel.chain(kernel.workspace + WS_LIVE, TCB_NEXT)
    assert {cpu.read_u8(tcb + TCB_ID) for tcb in live} == identifiers
    assert len(live) == kernel.tasks
    assert sorted(kernel.native(tcb) for tcb in live) == ranges
    assert not kernel.chain(kernel.workspace + WS_FREE, TCB_NEXT)

    replacement_id = kernel.tasks + 1
    assert kernel.service(CREATE, TASK6_ENTRY, replacement_id, replacement_id) != 0
    assert set(kernel.chain(kernel.workspace + WS_LIVE, TCB_NEXT)) == set(live)
    victim = next(tcb for tcb in live if cpu.read_u8(tcb + TCB_ID) == kernel.tasks)
    old_top = cpu.read_u32(victim + TCB_STACK_TOP)
    assert kernel.service(DELETE, kernel.tasks, 0) == 0
    assert kernel.service(CREATE, REPLACEMENT_ENTRY, replacement_id, replacement_id) == 0
    replacement = kernel.chain(kernel.workspace + WS_LIVE, TCB_NEXT)
    assert victim in replacement
    assert cpu.read_u8(victim + TCB_ID) == replacement_id
    assert cpu.read_u32(victim + TCB_STACK_TOP) == old_top
    assert {cpu.read_u8(tcb + TCB_ID) for tcb in replacement} == (
        identifiers - {kernel.tasks} | {replacement_id}
    )
    assert sorted(kernel.native(tcb) for tcb in replacement) == ranges
    assert cpu.read_u32(kernel.workspace + WS_HEAP_END) == heap_end
    assert cpu.read_u32(kernel.workspace + WS_HEAP_FREE) == remaining
    assert kernel.intact(pool_end, pool_end + POOL_GUARD_BYTES)
