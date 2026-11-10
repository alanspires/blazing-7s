#include "commission.h"

/* Byte-checked by fw_validate_profile and fw_process; stored in this order. */
#define PROFILE_MAGIC 0x46503653u /* "S6PF" */
#define REQUEST_MAGIC 0x45523653u /* "S6RE" */
#define HEADER_SIZE CM_RECORD_ORIGINAL_OFFSET
#define PADDING_OFFSET (CM_RECORD_ORIGINAL_OFFSET + FW_PERSIST_SIZE)
#define WATCHDOG_CHUNK 256u

struct commission_context {
    uint8_t mode;
    uint8_t coverage[CM_CAPTURE_SIZE / 8u];
};

/* Absolute RAM symbols supplied by link.ld; retained bytes are never trusted. */
extern struct commission_context commission_state;
extern uint8_t commission_capture[CM_CAPTURE_SIZE];
#define state commission_state

_Static_assert(sizeof(struct commission_context) <= CM_STATE_SIZE, "context reservation");
_Static_assert(FW_RUNTIME_BASE + FW_RUNTIME_SIZE <= CM_RECORD_BASE,
               "core runtime precedes commissioning record");
_Static_assert(CM_CAPTURE_SIZE == FW_PERSIST_SIZE + CM_RECORD_SIZE, "capture layout");
_Static_assert(CM_RECORD_CRC_OFFSET == CM_RECORD_SIZE - 6u, "record CRC position");
_Static_assert(CM_RECORD_MARKER_OFFSET == CM_RECORD_SIZE - 2u, "record marker position");
_Static_assert(PADDING_OFFSET <= CM_RECORD_CRC_OFFSET, "record capacity");
_Static_assert(CM_READY == FW_COMMIT_MARKER, "READY is the core commit marker");
_Static_assert(CM_REQUEST_HEADER + 2u + CM_MAX_CHUNK <= CM_REQUEST_HEADER + CM_MAX_PAYLOAD,
               "stage request capacity");
_Static_assert(CM_IDENTITY_SIZE <= CM_MAX_PAYLOAD, "identity reply capacity");

static const uint8_t zeros[2];
static const uint8_t first_install[] = "FIRST-INSTALL";
_Static_assert(sizeof(first_install) == CM_FIRST_INSTALL_SIZE + 1u, "INIT literal");

static const uint8_t *mapped(uint32_t address)
{
    return (const uint8_t *)(uintptr_t)address;
}

static void zero(uint8_t *data, uint32_t length)
{
    while (length-- != 0u)
        *data++ = 0;
}

static int equal(const uint8_t *a, const uint8_t *b, uint32_t length)
{
    while (length-- != 0u)
        if (*a++ != *b++)
            return 0;
    return 1;
}

static int blank(const uint8_t *data, uint32_t length)
{
    while (length-- != 0u)
        if (*data++ != 0u)
            return 0;
    return 1;
}

static uint16_t get16(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t get32(const uint8_t *p)
{
    return (uint32_t)get16(p) | ((uint32_t)get16(p + 2) << 16);
}

static void put16(uint8_t *p, uint16_t value)
{
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void put32(uint8_t *p, uint32_t value)
{
    put16(p, (uint16_t)value);
    put16(p + 2, (uint16_t)(value >> 16));
}

static uint32_t crc32(const uint8_t *data, uint32_t length)
{
    uint32_t crc = UINT32_MAX;
    while (length != 0u) {
        uint32_t count = length < WATCHDOG_CHUNK ? length : WATCHDOG_CHUNK;
        commission_watchdog();
        crc = fw_crc32_update(crc, data, count);
        data += count;
        length -= count;
    }
    return ~crc;
}

/* Exact or single-byte-damaged magic. */
static int near(const uint8_t *p, uint32_t magic)
{
    uint32_t matches = 0;
    for (uint32_t i = 0; i < 4u; ++i)
        matches += p[i] == (uint8_t)(magic >> (8u * i));
    return matches >= 3u;
}

static int committed(const uint8_t *p)
{
    return get16(p) == CM_READY || get16(p) == CM_DONE;
}

/* Conservative detection of existing or damaged extension persistence; absence is
 * not proof that no request ID was ever acknowledged. */
static int recognizable(const uint8_t *persist)
{
    for (uint32_t i = 0; i < 2u; ++i) {
        const uint8_t *slot = persist + i * FW_SLOT_SIZE;
        if (near(slot, PROFILE_MAGIC) || near(slot + FW_SLOT_MAGIC_OFFSET, FW_SLOT_MAGIC) ||
            committed(slot + FW_SLOT_MARKER_OFFSET))
            return 1;
    }
    for (uint32_t i = 0; i < 4u; ++i) {
        uint32_t offset = FW_PROFILE_JOURNAL - FW_PROFILE_BASE + i * FW_JOURNAL_SIZE;
        const uint8_t *journal = persist + offset;
        int operation = offset >= FW_OPERATION_JOURNAL - FW_PROFILE_BASE;
        if (near(journal, operation ? FW_JOURNAL_OPERATION_MAGIC : FW_JOURNAL_PROFILE_MAGIC) ||
            (operation && near(journal + FW_OPERATION_REQUEST_OFFSET, REQUEST_MAGIC)) ||
            committed(journal + FW_JOURNAL_MARKER_OFFSET))
            return 1;
    }
    return 0;
}

static int record_present(const uint8_t *record)
{
    return near(record, CM_RECORD_MAGIC) || committed(record + CM_RECORD_MARKER_OFFSET);
}

static void build_header(uint8_t *header, const uint8_t *original)
{
    zero(header, HEADER_SIZE);
    put32(header, CM_RECORD_MAGIC);
    header[4] = CM_RECORD_VERSION;
    put32(header + CM_RECORD_ORIGINAL_CRC_OFFSET, crc32(original, FW_PERSIST_SIZE));
}

/* Offset of the first word before the record CRC that differs from the record
 * built from header + original + zero padding. */
static uint32_t record_prefix(const uint8_t *record, const uint8_t *header,
                              const uint8_t *original)
{
    uint32_t i;
    for (i = 0; i < CM_RECORD_CRC_OFFSET; i += 2u) {
        const uint8_t *expected = i < HEADER_SIZE ? header + i
                                  : i < PADDING_OFFSET ? original + (i - HEADER_SIZE)
                                                       : zeros;
        if (i % WATCHDOG_CHUNK == 0u)
            commission_watchdog();
        if (!equal(record + i, expected, 2u))
            break;
    }
    return i;
}

static int record_valid(const uint8_t *record)
{
    uint8_t header[HEADER_SIZE];
    build_header(header, record + CM_RECORD_ORIGINAL_OFFSET);
    return record_prefix(record, header, record + CM_RECORD_ORIGINAL_OFFSET) ==
               CM_RECORD_CRC_OFFSET &&
           get32(record + CM_RECORD_CRC_OFFSET) == crc32(record, CM_RECORD_CRC_OFFSET);
}

/* A journaled native clear faults fw_boot until the native operator-clear branch,
 * reached only through normal startup, completes it. */
static uint8_t normal_boot(void)
{
    return fw_boot() == FW_OK || fw_clear_pending() ? CM_BOOT_CONTINUE : CM_BOOT_SERVICE;
}

uint8_t commission_boot(void)
{
    const uint8_t *record = mapped(CM_RECORD_BASE);
    uint16_t marker = get16(record + CM_RECORD_MARKER_OFFSET);
    zero((uint8_t *)&state, sizeof(state));
    zero(commission_capture, CM_CAPTURE_SIZE);
    if (committed(record + CM_RECORD_MARKER_OFFSET)) {
        /* A committed marker over a bad record is corruption, never a first install. */
        state.mode = !record_valid(record) ? CM_MODE_REFUSED
                     : marker == CM_READY  ? CM_MODE_READY
                                           : CM_MODE_DONE;
        return state.mode == CM_MODE_DONE ? normal_boot() : CM_BOOT_SERVICE;
    }
    if (near(record, CM_RECORD_MAGIC)) {
        /* Creation clears the marker first: only that torn state may resume. */
        if (marker != 0u || recognizable(mapped(FW_PROFILE_BASE)))
            state.mode = CM_MODE_REFUSED;
        return CM_BOOT_SERVICE;
    }
    if (normal_boot() == CM_BOOT_CONTINUE) {
        /* Storage the core accepts is existing extension state, never a first install. */
        state.mode = CM_MODE_REFUSED;
        return CM_BOOT_CONTINUE;
    }
    if (recognizable(mapped(FW_PROFILE_BASE)))
        state.mode = CM_MODE_REFUSED;
    return CM_BOOT_SERVICE;
}

/* Writes data, or zeros when data is null, verifying each word before the next so
 * a silently lost word still leaves an exact restartable prefix. */
static int store(uint32_t address, const uint8_t *data, uint32_t length)
{
    for (uint32_t i = 0; i < length; i += 2u) {
        const uint8_t *source = data != 0 ? data + i : zeros;
        commission_watchdog();
        if (!commission_storage_write(address + i, source, 2u) ||
            !equal(mapped(address + i), source, 2u))
            return 0;
    }
    return 1;
}

/* Current persistence is zeroed aligned words followed by original words. */
static int zeroing_prefix(const uint8_t *persist, const uint8_t *original)
{
    uint32_t i = 0;
    while (i < FW_PERSIST_SIZE && get16(persist + i) == 0u)
        i += 2u;
    return equal(persist + i, original + i, FW_PERSIST_SIZE - i);
}

/* Current record area is the staged area after the ordered creation writes were
 * cut: marker cleared, then ascending body words including CRC, never the commit. */
static int creation_prefix(const uint8_t *record, const uint8_t *before,
                           const uint8_t *header, const uint8_t *original)
{
    uint8_t crc[4];
    uint32_t i;
    if (get16(record + CM_RECORD_MARKER_OFFSET) != 0u)
        return equal(record, before, CM_RECORD_SIZE);
    i = record_prefix(record, header, original);
    if (i == CM_RECORD_CRC_OFFSET) {
        put32(crc, crc32(record, CM_RECORD_CRC_OFFSET));
        while (i < CM_RECORD_MARKER_OFFSET &&
               equal(record + i, crc + (i - CM_RECORD_CRC_OFFSET), 2u))
            i += 2u;
    }
    return equal(record + i, before + i, CM_RECORD_MARKER_OFFSET - i);
}

static int create_record(const uint8_t *header, const uint8_t *original)
{
    const uint8_t *record = mapped(CM_RECORD_BASE);
    uint8_t word[4];
    if (!store(CM_RECORD_BASE + CM_RECORD_MARKER_OFFSET, 0, 2u) ||
        !store(CM_RECORD_BASE, header, HEADER_SIZE) ||
        !store(CM_RECORD_BASE + CM_RECORD_ORIGINAL_OFFSET, original, FW_PERSIST_SIZE) ||
        !store(CM_RECORD_BASE + PADDING_OFFSET, 0, CM_RECORD_CRC_OFFSET - PADDING_OFFSET) ||
        record_prefix(record, header, original) != CM_RECORD_CRC_OFFSET)
        return 0;
    put32(word, crc32(record, CM_RECORD_CRC_OFFSET));
    if (!store(CM_RECORD_BASE + CM_RECORD_CRC_OFFSET, word, 4u))
        return 0;
    put16(word, CM_READY);
    return store(CM_RECORD_BASE + CM_RECORD_MARKER_OFFSET, word, 2u);
}

static uint8_t initialize(const uint8_t *p, uint32_t length)
{
    const uint8_t *persist = mapped(FW_PROFILE_BASE);
    const uint8_t *record = mapped(CM_RECORD_BASE);
    const uint8_t *original = commission_capture;
    const uint8_t *before = commission_capture + FW_PERSIST_SIZE;
    uint8_t header[HEADER_SIZE];
    uint8_t word[2];
    if (length != CM_FIRST_INSTALL_SIZE || !equal(p, first_install, CM_FIRST_INSTALL_SIZE))
        return FW_BAD_REQUEST;
    if (state.mode != CM_MODE_UNKNOWN && state.mode != CM_MODE_READY)
        return FW_INVALID_PROFILE;
    for (uint32_t i = 0; i < sizeof(state.coverage); ++i)
        if (state.coverage[i] != 0xffu)
            return FW_INCOMPLETE;
    /* The archive must be the unrecognized pre-write original, never a recapture.
     * Checked here as well as at boot so INIT alone never formats known state. */
    if (recognizable(original) || record_present(before))
        return FW_INVALID_PROFILE;
    if (state.mode == CM_MODE_READY) {
        /* Boot validated the record, including its stored original CRC. */
        if (!equal(record + CM_RECORD_ORIGINAL_OFFSET, original, FW_PERSIST_SIZE) ||
            !zeroing_prefix(persist, original))
            return FW_INVALID_PROFILE;
        state.mode = CM_MODE_REFUSED;
    } else {
        build_header(header, original);
        if (!equal(persist, original, FW_PERSIST_SIZE) ||
            !creation_prefix(record, before, header, original))
            return FW_INVALID_PROFILE;
        /* Any failure from here leaves uncertain storage for this session. */
        state.mode = CM_MODE_REFUSED;
        if (!create_record(header, original))
            return FW_FAULT;
    }
    put16(word, CM_DONE);
    if (!store(FW_PROFILE_BASE, 0, FW_PERSIST_SIZE) || !blank(persist, FW_PERSIST_SIZE) ||
        !store(CM_RECORD_BASE + CM_RECORD_MARKER_OFFSET, word, 2u))
        return FW_FAULT;
    state.mode = CM_MODE_DONE;
    return FW_OK;
}

static uint8_t stage(const uint8_t *p, uint32_t length)
{
    uint32_t offset, count, i;
    if (length < 3u || length - 2u > CM_MAX_CHUNK)
        return FW_BAD_REQUEST;
    offset = get16(p);
    count = length - 2u;
    if (offset >= CM_CAPTURE_SIZE || count > CM_CAPTURE_SIZE - offset)
        return FW_BAD_REQUEST;
    /* Conflicting overlaps reject the entire chunk, independent of ordering. */
    for (i = 0; i < count; ++i)
        if ((state.coverage[(offset + i) / 8u] & (1u << ((offset + i) % 8u))) != 0u &&
            commission_capture[offset + i] != p[2u + i])
            return FW_BAD_REQUEST;
    for (i = 0; i < count; ++i) {
        commission_capture[offset + i] = p[2u + i];
        state.coverage[(offset + i) / 8u] |= (uint8_t)(1u << ((offset + i) % 8u));
    }
    return FW_OK;
}

uint32_t commission_process(const uint8_t *request, uint32_t length, uint8_t *reply,
                            uint32_t capacity)
{
    uint8_t *out = reply + CM_REPLY_HEADER;
    const uint8_t *p = request + CM_REQUEST_HEADER;
    uint8_t op = length >= CM_REQUEST_HEADER ? request[5] : 0u;
    uint8_t status = FW_OK;
    uint32_t size = 0, n = length - CM_REQUEST_HEADER;
    if (capacity < CM_REPLY_HEADER + CM_MAX_PAYLOAD)
        return 0;
    if (length < CM_REQUEST_HEADER || n > CM_MAX_PAYLOAD || request[0] != 'S' ||
        request[1] != '6' || request[2] != 'C' || request[3] != 'M') {
        status = FW_BAD_REQUEST;
    } else if (request[4] != CM_VERSION) {
        status = FW_BAD_VERSION;
    } else if (op == CM_OP_INFO) {
        if (n != 0u) {
            status = FW_BAD_REQUEST;
        } else {
            out[0] = state.mode;
            put32(out + 1, FW_PROFILE_BASE);
            put16(out + 5, FW_PERSIST_SIZE);
            put32(out + 7, CM_RECORD_BASE);
            put16(out + 11, CM_RECORD_SIZE);
            put16(out + 13, CM_CAPTURE_SIZE);
            put16(out + 15, CM_MAX_CHUNK);
            size = CM_INFO_SIZE;
        }
    } else if (op == CM_OP_READ) {
        uint32_t offset = n == 3u ? get16(p) : 0u;
        size = n == 3u ? p[2] : 0u;
        if (size == 0u || size > CM_MAX_CHUNK || offset >= CM_CAPTURE_SIZE ||
            size > CM_CAPTURE_SIZE - offset) {
            status = FW_BAD_REQUEST;
        } else {
            for (uint32_t i = 0; i < size; ++i, ++offset)
                out[i] = offset < FW_PERSIST_SIZE
                             ? mapped(FW_PROFILE_BASE)[offset]
                             : mapped(CM_RECORD_BASE)[offset - FW_PERSIST_SIZE];
        }
    } else if (op == CM_OP_STAGE) {
        status = stage(p, n);
    } else if (op == CM_OP_INIT) {
        status = initialize(p, n);
    } else if (op == CM_OP_IDENTITY) {
        if (n != 0u) {
            status = FW_BAD_REQUEST;
        } else {
            commission_identity(out);
            size = CM_IDENTITY_SIZE;
        }
    } else {
        status = FW_UNSUPPORTED;
    }
    if (status != FW_OK)
        size = 0;
    reply[0] = 'S';
    reply[1] = '6';
    reply[2] = 'C';
    reply[3] = 'M';
    reply[4] = CM_VERSION;
    reply[5] = op;
    reply[6] = status;
    return CM_REPLY_HEADER + size;
}
