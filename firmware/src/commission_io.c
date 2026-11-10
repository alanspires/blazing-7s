#include "commission_io.h"
#include "platform.h"

struct commission_io_runtime {
    uint16_t received;
    uint16_t transmitting;
    uint16_t sent;
    uint8_t collecting;
    uint8_t escaped;
    uint8_t identity_ready;
    uint8_t identity[CM_IDENTITY_SIZE];
    uint8_t rx[CM_FRAME_BODY];
    uint8_t tx[CM_FRAME_ENCODED];
};

extern struct commission_io_runtime commission_io_state;
#define io commission_io_state
_Static_assert(sizeof(io) <= 0xf00u, "commission IO reservation");

void commission_watchdog(void)
{
    MMIO8(0xff101eu) = 0u;
}

static int within(uint32_t address, uint32_t length, uint32_t base, uint32_t size)
{
    return address >= base && address <= base + size && length <= base + size - address;
}

int commission_storage_write(uint32_t address, const uint8_t *data, uint32_t length)
{
    if ((address & 1u) != 0u || (length & 1u) != 0u ||
        (!within(address, length, FW_PROFILE_BASE, FW_PERSIST_SIZE) &&
         !within(address, length, CM_RECORD_BASE, CM_RECORD_SIZE)))
        return 0;
    for (uint32_t i = 0; i < length; i += 2u) {
        commission_watchdog();
        if (!platform_write_words(address + i, data + i, 2u))
            return 0;
    }
    return 1;
}

static void put32(uint8_t *p, uint32_t value)
{
    for (uint32_t i = 0; i < 4u; ++i)
        p[i] = (uint8_t)(value >> (8u * i));
}

static uint32_t get32(const uint8_t *p)
{
    uint32_t value = 0;
    for (uint32_t i = 0; i < 4u; ++i)
        value |= (uint32_t)p[i] << (8u * i);
    return value;
}

static uint32_t image_crc(uint32_t address, uint32_t length)
{
    uint32_t crc = UINT32_MAX;
    for (uint32_t offset = 0; offset < length; offset += 128u) {
        uint32_t count = length - offset;
        if (count > 128u)
            count = 128u;
        commission_watchdog();
        crc = fw_crc32_update(crc, (const uint8_t *)(uintptr_t)(address + offset), count);
    }
    return ~crc;
}

void commission_identity(uint8_t *identity)
{
    if (!io.identity_ready) {
        for (uint32_t i = 0; i < 32u; ++i)
            io.identity[i] = MMIO8(0x720u + i);
        for (uint32_t i = 0; i < 12u; ++i)
            io.identity[32u + i] = MMIO8(0x10d1c0u + i);
        put32(io.identity + 44u, image_crc(CM_MAIN_BASE, CM_MAIN_SIZE));
        put32(io.identity + 48u, image_crc(CM_PERSONALITY_BASE, CM_PERSONALITY_SIZE));
        io.identity_ready = 1u;
    }
    for (uint32_t i = 0; i < CM_IDENTITY_SIZE; ++i)
        identity[i] = io.identity[i];
}

static void encoded(uint8_t byte)
{
    if (byte == CM_DELIMITER || byte == CM_ESCAPE) {
        io.tx[io.transmitting++] = CM_ESCAPE;
        io.tx[io.transmitting++] = byte ^ 0x20u;
    } else {
        io.tx[io.transmitting++] = byte;
    }
}

static void complete_frame(void)
{
    uint8_t reply[CM_FRAME_BODY];
    uint32_t length;
    if (io.escaped || io.received < CM_REQUEST_HEADER + 4u ||
        io.received > CM_REQUEST_HEADER + CM_MAX_PAYLOAD + 4u ||
        get32(io.rx + io.received - 4u) != fw_crc32(io.rx, io.received - 4u))
        return;
    length = commission_process(io.rx, io.received - 4u, reply, CM_FRAME_BODY - 4u);
    if (length == 0u || length > CM_FRAME_BODY - 4u)
        return;
    put32(reply + length, fw_crc32(reply, length));
    io.sent = 0;
    io.transmitting = 0;
    io.tx[io.transmitting++] = CM_DELIMITER;
    for (uint32_t i = 0; i < length + 4u; ++i)
        encoded(reply[i]);
    io.tx[io.transmitting++] = CM_DELIMITER;
}

void commission_io_init(void)
{
    uint8_t *p = (uint8_t *)&io;
    for (uint32_t i = 0; i < sizeof(io); ++i)
        p[i] = 0u;
    MMIO8(CM_UART_IMR) = 0u;
    MMIO8(CM_UART_CR) = 0x0au;
    MMIO8(CM_UART_CR) = 0x2au;
    MMIO8(CM_UART_CR) = 0x3au;
    MMIO8(CM_UART_CR) = 0x4au;
    MMIO8(CM_UART_CR) = 0x1au;
    MMIO8(CM_UART_MR) = 0x33u;
    MMIO8(CM_UART_MR) = 0x07u;
    MMIO8(CM_UART_ACR) = 0x80u;
    MMIO8(CM_UART_SR) = 0xccu;
    MMIO8(CM_UART_CR) = 0x05u;
}

void commission_io_step(void)
{
    uint8_t status, byte;
    commission_watchdog();
    status = MMIO8(CM_UART_SR);
    if ((status & 0xf0u) != 0u) {
        MMIO8(CM_UART_CR) = 0x2au;
        MMIO8(CM_UART_CR) = 0x4au;
        MMIO8(CM_UART_CR) = 0x05u;
        io.collecting = 0u;
        io.received = 0u;
        io.escaped = 0u;
        return;
    }
    if (io.sent < io.transmitting) {
        if ((status & 1u) != 0u) {
            (void)MMIO8(CM_UART_DATA);
            io.collecting = 0u;
            io.received = 0u;
            io.escaped = 0u;
        }
        if ((status & 4u) != 0u)
            MMIO8(CM_UART_DATA) = io.tx[io.sent++];
        return;
    }
    if ((status & 1u) == 0u)
        return;
    byte = MMIO8(CM_UART_DATA);
    if (byte == CM_DELIMITER) {
        if (io.collecting && io.received != 0u)
            complete_frame();
        io.collecting = 1u;
        io.received = 0u;
        io.escaped = 0u;
    } else if (io.collecting) {
        if (!io.escaped && byte == CM_ESCAPE) {
            io.escaped = 1u;
            return;
        }
        if (io.escaped) {
            byte ^= 0x20u;
            io.escaped = 0u;
        }
        if (io.received == sizeof(io.rx)) {
            io.collecting = 0u;
            io.received = 0u;
        } else {
            io.rx[io.received++] = byte;
        }
    }
}

_Noreturn void commission_service(void)
{
    commission_io_init();
    for (;;)
        commission_io_step();
}
