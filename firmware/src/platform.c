#include "platform.h"
#include "game.h"
#include "motion.h"

#define SAS_COMMAND 0xe0u
#define SAS_PORTS 6u
#define SAS_RX 0x4106f8u
#define SAS_RX_STRIDE 0x340u
#define SAS_TX 0x414234u
#define SAS_TX_STRIDE 0x15f6u
#define SAS_FRAME_SIZE (FW_REQUEST_HEADER + FW_MAX_PAYLOAD + 5u)

struct platform_runtime {
    uint8_t ready;
    uint8_t operation;
    uint8_t booting; /* from platform_boot (0x155c) through platform_boot_restore (0x1906) */
    uint8_t closed;
    uint8_t active;
};

extern struct platform_runtime platform_state;
extern uint32_t sas_drain_mailbox;
_Static_assert(sizeof(struct platform_runtime) <= 0x10u, "platform reservation before IRQ guard");
_Static_assert(__builtin_offsetof(struct platform_runtime, ready) == 0u,
               "ext_reset_runtime clears ready before C initialization");

static uint32_t host_port(void)
{
    return MMIO8(0x43e8d6u);
}

static void native_send(uint32_t no_crc)
{
    ((void (*)(uint32_t))(uintptr_t)0x278feu)(no_crc);
}

static void native_nack(void)
{
    ((void (*)(uint32_t))(uintptr_t)0x27536u)(1u);
}

static uint16_t native_crc(const uint8_t *bytes, uint32_t length)
{
    return (uint16_t)((uint32_t (*)(const uint8_t *, uint32_t, uint32_t))
                     (uintptr_t)0x185acu)(bytes, length, 0u);
}

uint16_t platform_irq_save(void)
{
    uint16_t status;
    __asm__ volatile ("move.w %%sr,%0\n\tmove.w #0x2700,%%sr"
                      : "=d" (status) : : "cc", "memory");
    return status;
}

void platform_irq_restore(uint16_t status)
{
    __asm__ volatile ("move.w %0,%%sr" : : "d" (status) : "cc", "memory");
}

void platform_safe_u8(uint32_t address, uint32_t value)
{
    ((void (*)(uint32_t, uint32_t))(uintptr_t)0x400u)(address, value);
}

void platform_safe_u16(uint32_t address, uint32_t value)
{
    ((void (*)(uint32_t, uint32_t))(uintptr_t)0x426u)(address, value);
}

void platform_safe_u32(uint32_t address, uint32_t value)
{
    ((void (*)(uint32_t, uint32_t))(uintptr_t)0x44cu)(address, value);
}

int platform_geometry_ok(void)
{
    static const uint8_t identity[] = "E595211X-05  ";
    uint32_t i;
    for (i = 0; i < sizeof(identity) - 1u; ++i)
        if (MMIO8(0x10d1c0u + i) != identity[i])
            return 0;
    return MMIO8(0x10d0fau) == FW_STOPS && MMIO8(0x10d0fbu) == FW_REELS;
}

int platform_native_fault(void)
{
    return MMIO8(0x438030u) == 0x14u || MMIO8(0x43c31cu) != 0u ||
           MMIO32(0x4101eeu) != 0u || MMIO8(0x43c2e4u) != 0u ||
           MMIO8(0x41d9c1u) != 0u;
}

int platform_timer2_active(void)
{
    /* Never read T2-low (0xfe10d1): that acknowledges the timer 2 flag. */
    return ((MMIO8(0xfe10dbu) | MMIO8(0xfe10ddu)) & 0x20u) != 0u;
}

uint32_t platform_ticks(void)
{
    return MMIO32(0x4127f6u);
}

int platform_write_words(uint32_t address, const uint8_t *bytes, uint32_t length)
{
    uint32_t i;
    if ((address & 1u) != 0u || (length & 1u) != 0u)
        return 0;
    for (i = 0; i < length; i += 2u) {
        uint16_t value = (uint16_t)(((uint16_t)bytes[i] << 8) | bytes[i + 1u]);
        uint16_t status = platform_irq_save();
        MMIO16(address + i) = value;
        platform_irq_restore(status);
        if (MMIO16(address + i) != value)
            return 0;
    }
    return 1;
}

int fw_storage_write(uint32_t address, const uint8_t *bytes, uint32_t length)
{
    if (address < FW_PROFILE_BASE || address > FW_PROFILE_BASE + FW_PERSIST_SIZE ||
        length > FW_PROFILE_BASE + FW_PERSIST_SIZE - address)
        return 0;
    return platform_write_words(address, bytes, length);
}

uint32_t fw_capabilities(void)
{
    if (!platform_geometry_ok())
        return 0;
    return FW_CAP_PROFILE | FW_CAP_OUTCOME | FW_CAP_SPIN | motion_capabilities();
}

uint8_t fw_prepare_profile_change(uint8_t *staged, const uint8_t *active)
{
    return game_prepare_profile(staged, active);
}

void fw_finish_profile_change(uint8_t status)
{
    game_finish_profile_change(status);
}

uint8_t fw_apply_profile(const uint8_t *slot, bool restore)
{
    return game_apply_profile(slot, restore, platform_state.booting != 0u);
}

uint8_t fw_submit(uint8_t operation, const uint8_t *args, uint32_t length, uint64_t id)
{
    uint8_t result;
    if (operation == FW_OP_POSITION || operation == FW_OP_JOG)
        result = motion_submit(operation, args, length, id);
    else
        result = game_submit(operation, args, length, id);
    if (result == FW_ACCEPTED || result == FW_OK)
        platform_state.operation = operation;
    return result;
}

uint8_t fw_current_status(uint64_t id)
{
    if (platform_state.operation == FW_OP_POSITION || platform_state.operation == FW_OP_JOG)
        return motion_current_status(id);
    return game_current_status(id);
}

_Noreturn void platform_startup_fault(void)
{
    /* The stock startup fault loop maintains the watchdog and never starts tasks. */
    ((void (*)(uint32_t))(uintptr_t)0x38a06u)(0x0fu);
    __builtin_trap();
}

void platform_boot(void)
{
    platform_state.ready = 0u;
    platform_state.operation = 0u;
    platform_state.booting = 1u;
    platform_state.closed = 0u;
    platform_state.active = 0u;
    sas_drain_mailbox = 0u;
    game_init();
    motion_init();
    if (fw_boot() != FW_OK)
        platform_startup_fault();
    platform_state.ready = 1u;
}

/* Native loader tail (0x37dde): during startup's optional load (0x1730) or a
 * runtime reload (0x63db8, 0x64120, 0x6430a). */
void platform_restore(void)
{
    if (platform_state.ready && fw_restore() != FW_OK)
        platform_startup_fault();
}

/* Startup's last step before native queues, tasks, VIA initialization (0x24ae)
 * and the kernel start: FUN_0000154e, called only from reset at 0xbca. */
void platform_boot_restore(void)
{
    platform_restore();
    platform_state.booting = 0u;
}

void platform_before_clear(void)
{
    platform_state.ready = 0u;
    if (fw_invalidate() != FW_OK)
        platform_startup_fault();
}

void platform_after_clear(void)
{
    if (fw_clear_complete() != FW_OK)
        platform_startup_fault();
}

static int sas_core_begin(void)
{
    uint16_t irq = platform_irq_save();
    if (platform_state.closed) {
        platform_irq_restore(irq);
        return 0;
    }
    if (platform_state.active || sas_drain_mailbox != 0u)
        platform_fatal();
    platform_state.active = 1u;
    platform_irq_restore(irq);
    return 1;
}

static void sas_core_end(void)
{
    uint16_t error = UINT16_MAX;
    uint16_t irq = platform_irq_save();
    uint8_t notify;
    if (!platform_state.active || sas_drain_mailbox != 0u)
        platform_fatal();
    platform_state.active = 0u;
    notify = platform_state.closed;
    platform_irq_restore(irq);
    if (notify) {
        /* SC8 may wake task 7, which can delete this task before the wrapper
         * returns. All shared cleanup must already be complete. */
        ((void (*)(uint32_t *, uint32_t, uint16_t *))(uintptr_t)0x3a32u)
            (&sas_drain_mailbox, PLATFORM_SAS_QUIESCED, &error);
        if (error != 0u)
            platform_fatal();
    }
}

/* Caller holds the saved-SR section; the single task-7 drainer owns closed. */
static void sas_require_drained(void)
{
    if (!platform_state.closed || platform_state.active || sas_drain_mailbox != 0u)
        platform_fatal();
}

void platform_reconfigure(void)
{
    uint16_t error = UINT16_MAX;
    uint16_t irq = platform_irq_save();
    uint8_t must_wait;
    if (platform_state.closed || sas_drain_mailbox != 0u)
        platform_fatal();
    platform_state.closed = 1u;
    must_wait = platform_state.active;
    platform_irq_restore(irq);
    if (must_wait) {
        /* Consume even if completion posted before we pend. Rechecking active
         * instead would leave that token behind for the next configuration. */
        uint32_t token = ((uint32_t (*)(uint32_t *, uint32_t, uint16_t *))
                          (uintptr_t)0x3a52u)(&sas_drain_mailbox, 0u, &error);
        if (error != 0u || token != PLATFORM_SAS_QUIESCED)
            platform_fatal();
    }
    irq = platform_irq_save();
    sas_require_drained();
    platform_irq_restore(irq);
    ((void (*)(void))(uintptr_t)0x16682u)();
    ((void (*)(void))(uintptr_t)0x15836u)();
    ((void (*)(void))(uintptr_t)0x152ecu)();
    irq = platform_irq_save();
    sas_require_drained();
    platform_state.closed = 0u;
    platform_irq_restore(irq);
}

void platform_sas_process(void)
{
    uint8_t frame[SAS_FRAME_SIZE];
    uint8_t reply[FW_REPLY_HEADER + FW_MAX_PAYLOAD];
    uint32_t port = host_port(), received, length, i, tx;
    uint16_t status, crc;
    if (port == 0u || port > SAS_PORTS)
        return;
    status = platform_irq_save();
    received = MMIO8(SAS_RX + (port - 1u) * SAS_RX_STRIDE);
    if (received >= 5u && received <= sizeof(frame))
        for (i = 0; i < received; ++i)
            frame[i] = MMIO8(SAS_RX + (port - 1u) * SAS_RX_STRIDE + 1u + i);
    platform_irq_restore(status);
    if (received < 5u || received > sizeof(frame)) {
        native_nack();
        return;
    }
    crc = native_crc(frame, received - 2u);
    if (frame[0] != (MMIO8(0x43e8dcu) & 0x7fu) || frame[1] != SAS_COMMAND ||
        (uint32_t)frame[2] + 5u != received ||
        frame[received - 2u] != (uint8_t)crc || frame[received - 1u] != (uint8_t)(crc >> 8)) {
        native_nack();
        return;
    }
    if (!sas_core_begin()) {
        native_nack();
        return;
    }
    length = fw_process(frame + 3u, frame[2], reply, sizeof(reply));
    sas_core_end();
    if (length == 0u) {
        native_nack();
        return;
    }
    tx = SAS_TX + (port - 1u) * SAS_TX_STRIDE;
    MMIO8(tx) = (uint8_t)(length + 3u);
    MMIO8(tx + 1u) = frame[0];
    MMIO8(tx + 2u) = SAS_COMMAND;
    MMIO8(tx + 3u) = (uint8_t)length;
    for (i = 0; i < length; ++i)
        MMIO8(tx + 4u + i) = reply[i];
    native_send(0u);
}

void platform_sas_identity(void)
{
    static const uint8_t label[] = "CUSTOM";
    uint32_t port = host_port(), tx, offset, i;
    if (port == 0u || port > SAS_PORTS ||
        (fw_active_profile() == 0 && !fw_is_faulted()))
        return;
    tx = SAS_TX + (port - 1u) * SAS_TX_STRIDE;
    if (MMIO8(tx + 2u) == 0x1fu && MMIO8(tx) == 22u)
        offset = 13u;
    else if (MMIO8(tx + 2u) == 0x53u && MMIO8(tx) == 24u)
        offset = 15u;
    else
        return;
    for (i = 0; i < sizeof(label) - 1u; ++i)
        MMIO8(tx + offset + i) = label[i];
    for (i = 0; i < 4u; ++i)
        MMIO8(tx + offset + sizeof(label) - 1u + i) = ' ';
}
