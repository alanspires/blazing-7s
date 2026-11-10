#include "fw.h"

#define PROFILE_STOCK 0u
#define PROFILE_ACTIVE 1u
#define PROFILE_CLEARING 2u
#define PROFILE_RESTORING 3u /* stock, from the baseline of a recorded source slot */

struct fw_state {
    uint64_t last_id;
    uint32_t operation_sequence;
    uint32_t generation;
    uint32_t profile_crc;
    uint32_t upload_crc;
    const uint8_t *active;
    const uint8_t *restore_source; /* while a journaled restoration is pending */
    uint8_t last_op;
    uint8_t last_status;
    uint8_t operation_index;
    uint8_t profile_index;
    uint8_t slot;
    uint8_t fault;
    uint8_t ready;
    uint8_t uploading;
    uint8_t profile_owned;
    uint8_t profile_pending;
    uint8_t restore_pending;
    uint8_t request_length;
    uint8_t request[FW_MAX_REQUEST];
    uint8_t upload[FW_PROFILE_SIZE];
    uint8_t coverage[(FW_PROFILE_SIZE + 7u) / 8u];
    uint8_t slot_data[FW_SLOT_SIZE];
};

/* Absolute RAM symbol supplied by link.ld; no initialized C runtime is assumed. */
extern struct fw_state fw_runtime;
#define state fw_runtime
_Static_assert(sizeof(struct fw_state) <= FW_RUNTIME_SIZE, "runtime reservation");
_Static_assert(FW_PROFILE_BASE + FW_PERSIST_SIZE <= FW_RUNTIME_BASE,
               "runtime follows persistence");
_Static_assert(FW_MAX_REQUEST == FW_REQUEST_HEADER + FW_MAX_PAYLOAD, "request capacity");
_Static_assert(FW_OPERATION_REQUEST_OFFSET + FW_MAX_REQUEST <= FW_JOURNAL_CRC_OFFSET,
               "operation journal capacity");
_Static_assert(FW_SLOT_CONTEXT_OFFSET + 4u == FW_SLOT_BASELINE_OFFSET &&
               FW_SLOT_BASELINE_OFFSET + FW_SLOT_BASELINE_SIZE <= FW_SLOT_FORMAT_OFFSET &&
               FW_SLOT_FORMAT_OFFSET + 4u <= FW_SLOT_MAGIC_OFFSET, "slot layout");

struct journal {
    const uint8_t *data;
    uint32_t sequence;
    uint8_t index;
};

static void zero(uint8_t *data, uint32_t length)
{
    while (length-- != 0u)
        *data++ = 0;
}

static void copy(uint8_t *to, const uint8_t *from, uint32_t length)
{
    while (length-- != 0u)
        *to++ = *from++;
}

static int equal(const uint8_t *a, const uint8_t *b, uint32_t length)
{
    while (length-- != 0u)
        if (*a++ != *b++)
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

static uint64_t get64(const uint8_t *p)
{
    return (uint64_t)get32(p) | ((uint64_t)get32(p + 4) << 32);
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

static void put64(uint8_t *p, uint64_t value)
{
    put32(p, (uint32_t)value);
    put32(p + 4, (uint32_t)(value >> 32));
}

uint32_t fw_crc32_update(uint32_t crc, const uint8_t *data, uint32_t length)
{
    uint32_t bit;
    while (length-- != 0u) {
        crc ^= *data++;
        for (bit = 0; bit < 8u; ++bit)
            crc = (crc >> 1) ^ ((0u - (crc & 1u)) & 0xedb88320u);
    }
    return crc;
}

uint32_t fw_crc32(const uint8_t *data, uint32_t length)
{
    return ~fw_crc32_update(UINT32_MAX, data, length);
}

static int valid_symbol(uint8_t symbol)
{
    return (symbol >= 0x0au && symbol <= 0x0eu) || symbol == 0x18u ||
           symbol == 0x80u || symbol == 0x81u;
}

uint8_t fw_validate_profile(const uint8_t *p)
{
    uint32_t reel, row, i, bet, largest = 0;
    uint8_t lines = p[5], maximum = p[6];
    if (p[0] != 'S' || p[1] != '6' || p[2] != 'P' || p[3] != 'F' ||
        p[4] != FW_VERSION || lines == 0u || lines > FW_MAX_LINES ||
        maximum == 0u || maximum > FW_MAX_BET ||
        (uint32_t)lines * maximum > FW_MAX_TOTAL_BET ||
        p[7] != FW_PROFILE_RECORD_COUNT || p[74] != 0u || p[75] != 0u)
        return FW_INVALID_PROFILE;
    for (reel = 0; reel < FW_REELS; ++reel) {
        uint32_t total = 0;
        for (row = 0; row < FW_STOPS; ++row)
            total += p[8u + row * FW_REELS + reel];
        if (total == 0u || total > UINT16_MAX)
            return FW_INVALID_PROFILE;
    }
    for (i = 0; i < FW_PROFILE_RECORD_COUNT; ++i) {
        const uint8_t *r = p + FW_PROFILE_RECORD_OFFSET + i * FW_PROFILE_RECORD_SIZE;
        uint32_t award = ((uint32_t)r[0] << 24) | ((uint32_t)r[1] << 16) |
                         ((uint32_t)r[2] << 8) | r[3];
        uint8_t flags = r[11], mask = r[12];
        if (award > FW_MAX_AWARD || !valid_symbol(r[4]) || !valid_symbol(r[5]) ||
            !valid_symbol(r[6]) || r[7] != 0x24u || r[8] != 0x24u ||
            r[10] == 0u || r[10] > r[9] || r[9] > maximum ||
            (flags != 0u && flags != 0x10u && flags != 0x20u && flags != 0x40u) ||
            mask == 0u || (mask >> lines) != 0u || (flags != 0u && mask != 1u) ||
            r[13] != FW_REELS || r[14] != i || r[15] != 0u)
            return FW_INVALID_PROFILE;
        if (award > largest)
            largest = award;
    }
    if (largest > UINT32_MAX / lines)
        return FW_INVALID_PROFILE;
    for (bet = 1; bet <= maximum; ++bet) {
        for (i = 0; i < FW_PROFILE_RECORD_COUNT; ++i) {
            const uint8_t *r = p + FW_PROFILE_RECORD_OFFSET + i * FW_PROFILE_RECORD_SIZE;
            if (r[10] <= bet && bet <= r[9])
                break;
        }
        if (i == FW_PROFILE_RECORD_COUNT)
            return FW_INVALID_PROFILE;
    }
    return FW_OK;
}

static const uint8_t *mapped(uint32_t address)
{
    return (const uint8_t *)(uintptr_t)address;
}

static int blank(const uint8_t *p, uint32_t length)
{
    uint8_t value = p[0];
    uint32_t i;
    if (value != 0u && value != 0xffu)
        return 0;
    for (i = 1; i < length; ++i)
        if (p[i] != value)
            return 0;
    return 1;
}

static uint8_t select_journal(uint32_t base, uint32_t magic, struct journal *out)
{
    uint32_t i;
    out->data = 0;
    out->sequence = 0;
    out->index = 1;
    for (i = 0; i < 2u; ++i) {
        const uint8_t *p = mapped(base + i * FW_JOURNAL_SIZE);
        uint16_t marker = get16(p + FW_JOURNAL_MARKER_OFFSET);
        if (blank(p, FW_JOURNAL_SIZE))
            continue;
        if (marker == 0u)
            continue;
        /* An intact commit marker with bad contents is corruption, not a torn
         * write. Never silently roll back an acknowledged configuration or ID. */
        if (marker != FW_COMMIT_MARKER || get32(p) != magic ||
            get32(p + 4) == 0u ||
            get32(p + FW_JOURNAL_CRC_OFFSET) != fw_crc32(p, FW_JOURNAL_CRC_OFFSET))
            return FW_FAULT;
        if (out->data != 0 && get32(p + 4) == out->sequence)
            return FW_FAULT;
        if (get32(p + 4) > out->sequence) {
            out->data = p;
            out->sequence = get32(p + 4);
            out->index = (uint8_t)i;
        }
    }
    return out->data == 0 ? FW_FAULT : FW_OK;
}

static uint8_t write_committed(uint32_t address, uint8_t *data, uint32_t size)
{
    uint8_t marker[2] = {0, 0};
    uint32_t end = size - 2u;
    if (!fw_storage_write(address + end, marker, 2u) ||
        get16(mapped(address + end)) != 0u ||
        !fw_storage_write(address, data, end) || !equal(mapped(address), data, end))
        return FW_FAULT;
    put16(marker, FW_COMMIT_MARKER);
    if (!fw_storage_write(address + end, marker, 2u) ||
        get16(mapped(address + end)) != FW_COMMIT_MARKER)
        return FW_FAULT;
    return FW_OK;
}

static void baseline(uint8_t *record, uint32_t magic)
{
    zero(record, FW_JOURNAL_SIZE);
    put32(record, magic);
    put32(record + 4, 1u);
    put32(record + FW_JOURNAL_CRC_OFFSET, fw_crc32(record, FW_JOURNAL_CRC_OFFSET));
    put16(record + FW_JOURNAL_MARKER_OFFSET, FW_COMMIT_MARKER);
}

static int erased(const uint8_t *p, uint32_t length, uint8_t fill)
{
    while (length-- != 0u)
        if (*p++ != fill)
            return 0;
    return 1;
}

static int baseline_state(const uint8_t *p, const uint8_t *expected, uint8_t fill)
{
    int suffix = 0;
    if (equal(p, expected, FW_JOURNAL_SIZE))
        return 2;
    if (erased(p, FW_JOURNAL_SIZE, fill))
        return 1;
    if (get16(p + FW_JOURNAL_MARKER_OFFSET) != 0u)
        return 0;
    for (uint32_t i = 0; i < FW_JOURNAL_MARKER_OFFSET; i += 2u) {
        if (!equal(p + i, expected + i, 2u))
            suffix = 1;
        if (suffix && !erased(p + i, 2u, fill))
            return 0;
    }
    return 1;
}

static uint8_t bootstrap(void)
{
    uint8_t operation[FW_JOURNAL_SIZE], profile[FW_JOURNAL_SIZE];
    baseline(operation, FW_JOURNAL_OPERATION_MAGIC);
    baseline(profile, FW_JOURNAL_PROFILE_MAGIC);
    /* Only this exact virgin write sequence is restartable without a committed
     * predecessor. An acknowledged operation always has a durable predecessor. */
    for (uint32_t i = 0; i < 2u; ++i) {
        uint8_t fill = i == 0u ? 0u : 0xffu;
        int operation_state, profile_state;
        if (!erased(mapped(FW_PROFILE_BASE), 2u * FW_SLOT_SIZE, fill) ||
            !erased(mapped(FW_PROFILE_JOURNAL + FW_JOURNAL_SIZE), FW_JOURNAL_SIZE, fill) ||
            !erased(mapped(FW_OPERATION_JOURNAL + FW_JOURNAL_SIZE), FW_JOURNAL_SIZE, fill))
            continue;
        operation_state = baseline_state(mapped(FW_OPERATION_JOURNAL), operation, fill);
        profile_state = baseline_state(mapped(FW_PROFILE_JOURNAL), profile, fill);
        if (operation_state == 0 || profile_state == 0 ||
            (operation_state != 2 && !erased(mapped(FW_PROFILE_JOURNAL), FW_JOURNAL_SIZE, fill)))
            continue;
        if (operation_state != 2 &&
            write_committed(FW_OPERATION_JOURNAL, operation, sizeof(operation)) != FW_OK)
            return FW_FAULT;
        if (profile_state != 2 &&
            write_committed(FW_PROFILE_JOURNAL, profile, sizeof(profile)) != FW_OK)
            return FW_FAULT;
        break;
    }
    return FW_OK;
}

/* Profile record: kind, slot, profile CRC, request ID and, for a restoration,
 * the source slot's generation. */
static uint8_t write_profile_journal(const struct journal *previous, uint8_t kind,
                                     uint8_t slot, uint32_t crc, uint64_t id,
                                     uint32_t source_generation)
{
    uint8_t record[FW_JOURNAL_SIZE];
    if (previous->sequence == UINT32_MAX)
        return FW_FAULT;
    zero(record, sizeof(record));
    put32(record, FW_JOURNAL_PROFILE_MAGIC);
    put32(record + 4, previous->sequence + 1u);
    record[8] = kind;
    record[9] = slot;
    put32(record + 12, crc);
    put64(record + 16, id);
    put32(record + 24, source_generation);
    put32(record + FW_JOURNAL_CRC_OFFSET, fw_crc32(record, FW_JOURNAL_CRC_OFFSET));
    return write_committed(FW_PROFILE_JOURNAL +
                           (uint32_t)(previous->index ^ 1u) * FW_JOURNAL_SIZE,
                           record, sizeof(record));
}

static uint8_t record_operation(const uint8_t *request, uint32_t length, uint8_t status)
{
    uint8_t record[FW_JOURNAL_SIZE];
    uint8_t index = state.operation_index ^ 1u;
    if (state.operation_sequence == UINT32_MAX)
        return FW_FAULT;
    zero(record, sizeof(record));
    put32(record, FW_JOURNAL_OPERATION_MAGIC);
    put32(record + 4, state.operation_sequence + 1u);
    put64(record + 8, get64(request + 6));
    record[16] = (uint8_t)length;
    record[17] = status;
    copy(record + FW_OPERATION_REQUEST_OFFSET, request, length);
    put32(record + FW_JOURNAL_CRC_OFFSET, fw_crc32(record, FW_JOURNAL_CRC_OFFSET));
    if (write_committed(FW_OPERATION_JOURNAL + (uint32_t)index * FW_JOURNAL_SIZE,
                        record, sizeof(record)) != FW_OK)
        return FW_FAULT;
    state.last_id = get64(request + 6);
    state.request_length = (uint8_t)length;
    state.last_op = request[5];
    copy(state.request, request, length);
    state.last_status = status;
    state.operation_index = index;
    ++state.operation_sequence;
    return FW_OK;
}

static uint8_t finish_operation(uint8_t status)
{
    if (status > FW_UNSUPPORTED ||
        record_operation(state.request, state.request_length, status) != FW_OK) {
        state.fault = 1;
        return FW_FAULT;
    }
    return status;
}

static int action(uint8_t op)
{
    return op >= FW_OP_ARM_OUTCOME && op <= FW_OP_JOG;
}

static int mutation(uint8_t op)
{
    return (op >= FW_OP_BEGIN_PROFILE && op <= FW_OP_RESTORE_STOCK) || action(op);
}

/* The committed slot a profile record names, or NULL if it is not exactly the
 * current-format slot that record committed. */
static const uint8_t *record_slot(const uint8_t *record, uint32_t generation)
{
    const uint8_t *p = mapped(FW_PROFILE_BASE + (uint32_t)record[9] * FW_SLOT_SIZE);
    if (get16(p + FW_SLOT_MARKER_OFFSET) != FW_COMMIT_MARKER ||
        get32(p + FW_SLOT_MAGIC_OFFSET) != FW_SLOT_MAGIC ||
        get32(p + FW_SLOT_FORMAT_OFFSET) != FW_SLOT_FORMAT ||
        get32(p + FW_SLOT_GENERATION_OFFSET) != generation ||
        get32(p + FW_SLOT_CRC_OFFSET) != fw_crc32(p, FW_SLOT_CRC_OFFSET) ||
        get32(record + 12) != fw_crc32(p, FW_PROFILE_SIZE) ||
        fw_validate_profile(p) != FW_OK)
        return 0;
    /* Native matcher reads two extra records; guard bytes are part of slot CRC. */
    for (uint32_t i = FW_PROFILE_SIZE; i < FW_PROFILE_SIZE + FW_PROFILE_GUARD_SIZE; ++i)
        if (p[i] != 0u)
            return 0;
    return p;
}

static uint8_t load_profile(const struct journal *journal)
{
    const uint8_t *p = journal->data;
    state.profile_index = journal->index;
    state.generation = journal->sequence;
    if (p == 0)
        return FW_OK;
    if (p[8] == PROFILE_STOCK)
        return p[9] == 0u && get32(p + 12) == 0u && get32(p + 24) == 0u ? FW_OK : FW_FAULT;
    /* A restoration's source is validated only while that restoration is
     * pending; once complete, its slot may be reused by a later commit. */
    if (p[8] == PROFILE_RESTORING)
        return p[9] <= 1u && get64(p + 16) != 0u ? FW_OK : FW_FAULT;
    if (p[8] != PROFILE_ACTIVE || p[9] > 1u || get32(p + 24) != 0u)
        return FW_FAULT;
    state.slot = p[9];
    state.active = record_slot(p, journal->sequence);
    state.profile_crc = get32(p + 12);
    return state.active == 0 ? FW_FAULT : FW_OK;
}

uint8_t fw_boot(void)
{
    struct journal profile, operation;
    uint8_t profile_status, operation_status;
    zero((uint8_t *)&state, sizeof(state));
    state.ready = 1;
    state.operation_index = 1;
    if (bootstrap() != FW_OK) {
        state.fault = 1;
        return FW_FAULT;
    }
    profile_status = select_journal(FW_PROFILE_JOURNAL, FW_JOURNAL_PROFILE_MAGIC, &profile);
    if (profile_status != FW_OK || load_profile(&profile) != FW_OK)
        state.fault = 1;
    operation_status = select_journal(FW_OPERATION_JOURNAL, FW_JOURNAL_OPERATION_MAGIC, &operation);
    if (operation_status != FW_OK) {
        state.fault = 1;
    } else if (operation.data != 0) {
        const uint8_t *p = operation.data;
        state.operation_sequence = operation.sequence;
        state.operation_index = operation.index;
        state.last_id = get64(p + 8);
        state.request_length = p[16];
        state.last_status = p[17];
        if (state.last_id == 0u) {
            uint8_t expected[FW_JOURNAL_SIZE];
            baseline(expected, FW_JOURNAL_OPERATION_MAGIC);
            if (operation.index != 0u || !equal(p, expected, sizeof(expected)))
                state.fault = 1;
        } else if (state.request_length < FW_REQUEST_HEADER || state.request_length > FW_MAX_REQUEST ||
                   state.last_status > FW_UNSUPPORTED) {
            state.fault = 1;
        } else {
            copy(state.request, p + FW_OPERATION_REQUEST_OFFSET, state.request_length);
            state.last_op = state.request[5];
            if (state.request[0] != 'S' || state.request[1] != '6' ||
                state.request[2] != 'R' || state.request[3] != 'E' ||
                state.request[4] != FW_VERSION || !mutation(state.last_op) ||
                get64(state.request + 6) != state.last_id)
                state.fault = 1;
        }
        /* Every profile record's request was reserved in the operation
         * journal first: a newer one means that journal lost a committed record. */
        if (!state.fault && get64(profile.data + 16) > state.last_id)
            state.fault = 1;
        if (!state.fault && state.last_status == FW_ACCEPTED) {
            if (get64(profile.data + 16) != state.last_id) {
                (void)finish_operation(FW_INTERRUPTED);
            } else if (state.last_op == FW_OP_COMMIT_PROFILE &&
                       profile.data[8] == PROFILE_ACTIVE) {
                state.profile_pending = 1;
            } else if (state.last_op == FW_OP_RESTORE_STOCK &&
                       profile.data[8] == PROFILE_RESTORING) {
                /* Replay exactly the recorded baseline, never a substitute. */
                state.restore_source = record_slot(profile.data, get32(profile.data + 24));
                state.profile_pending = 1;
                state.restore_pending = 1;
                if (state.restore_source == 0)
                    state.fault = 1;
            } else {
                state.fault = 1;
            }
        } else if (!state.fault && profile.data[8] == PROFILE_RESTORING &&
                   get64(profile.data + 16) == state.last_id && state.last_status != FW_OK) {
            /* Only a durable OK proves the recorded baseline was restored; a
             * failed restoration keeps its source for investigation. */
            state.fault = 1;
        }
    }
    return state.fault ? FW_FAULT : FW_OK;
}

static uint8_t apply_profile(const uint8_t *slot, bool restore)
{
    if (fw_apply_profile(slot, restore) != FW_OK) {
        state.fault = 1;
        return FW_FAULT;
    }
    return FW_OK;
}

uint8_t fw_restore(void)
{
    if (!state.ready || state.fault ||
        apply_profile(state.restore_pending ? state.restore_source : state.active,
                      state.restore_pending != 0u) != FW_OK)
        return FW_FAULT;
    if (state.profile_pending) {
        if (finish_operation(FW_OK) != FW_OK)
            return FW_FAULT;
        state.profile_pending = 0;
        state.restore_pending = 0;
        state.restore_source = 0;
    }
    return FW_OK;
}

int fw_is_faulted(void)
{
    return !state.ready || state.fault;
}

const uint8_t *fw_active_profile(void)
{
    return state.ready && !state.fault ? state.active : 0;
}

uint8_t fw_invalidate(void)
{
    struct journal journal;
    struct journal operation;
    if (bootstrap() != FW_OK ||
        select_journal(FW_OPERATION_JOURNAL, FW_JOURNAL_OPERATION_MAGIC, &operation) != FW_OK ||
        select_journal(FW_PROFILE_JOURNAL, FW_JOURNAL_PROFILE_MAGIC, &journal) != FW_OK)
        return FW_FAULT;
    return write_profile_journal(&journal, PROFILE_CLEARING, 0, 0, 0, 0);
}

int fw_clear_pending(void)
{
    struct journal profile, operation;
    if (select_journal(FW_PROFILE_JOURNAL, FW_JOURNAL_PROFILE_MAGIC, &profile) != FW_OK ||
        select_journal(FW_OPERATION_JOURNAL, FW_JOURNAL_OPERATION_MAGIC, &operation) != FW_OK)
        return 0;
    return profile.data[8] == PROFILE_CLEARING && profile.data[9] == 0u &&
           get32(profile.data + 12) == 0u && get64(profile.data + 16) == 0u;
}

uint8_t fw_clear_complete(void)
{
    struct journal journal;
    if (select_journal(FW_PROFILE_JOURNAL, FW_JOURNAL_PROFILE_MAGIC, &journal) != FW_OK ||
        journal.data == 0 || journal.data[8] != PROFILE_CLEARING)
        return FW_FAULT;
    return write_profile_journal(&journal, PROFILE_STOCK, 0, 0, 0, 0);
}

static uint8_t commit_profile(void)
{
    struct journal previous;
    uint32_t address;
    uint8_t slot, status;
    if (!state.uploading)
        return FW_INCOMPLETE;
    for (uint32_t i = 0; i < FW_PROFILE_SIZE; ++i)
        if ((state.coverage[i / 8u] & (1u << (i % 8u))) == 0u)
            return FW_INCOMPLETE;
    if (fw_crc32(state.upload, FW_PROFILE_SIZE) != state.upload_crc ||
        fw_validate_profile(state.upload) != FW_OK)
        return FW_INVALID_PROFILE;
    if (state.generation == UINT32_MAX)
        return FW_FAULT;
    zero(state.slot_data, sizeof(state.slot_data));
    copy(state.slot_data, state.upload, FW_PROFILE_SIZE);
    /* Fills context and the original-configuration baseline, inherited from
     * the active slot or first captured from stock. */
    status = fw_prepare_profile_change(state.slot_data, state.active);
    if (status != FW_OK)
        return status;
    state.profile_owned = 1;
    slot = state.active == 0 ? 0 : state.slot ^ 1u;
    address = FW_PROFILE_BASE + (uint32_t)slot * FW_SLOT_SIZE;
    put32(state.slot_data + FW_SLOT_FORMAT_OFFSET, FW_SLOT_FORMAT);
    put32(state.slot_data + FW_SLOT_MAGIC_OFFSET, FW_SLOT_MAGIC);
    put32(state.slot_data + FW_SLOT_GENERATION_OFFSET, state.generation + 1u);
    put32(state.slot_data + FW_SLOT_CRC_OFFSET, fw_crc32(state.slot_data, FW_SLOT_CRC_OFFSET));
    if (write_committed(address, state.slot_data, sizeof(state.slot_data)) != FW_OK)
        return FW_FAULT;
    previous.data = 0;
    previous.index = state.profile_index;
    previous.sequence = state.generation;
    if (write_profile_journal(&previous, PROFILE_ACTIVE, slot, state.upload_crc,
                              state.last_id, 0) != FW_OK)
        return FW_FAULT;
    state.profile_index ^= 1u;
    ++state.generation;
    state.slot = slot;
    state.active = mapped(address);
    state.profile_crc = state.upload_crc;
    state.uploading = 0;
    return apply_profile(state.active, false);
}

static uint8_t restore_stock(void)
{
    struct journal previous;
    const uint8_t *source = state.active;
    uint8_t status;
    if (source == 0)
        return FW_OK; /* already stock: nothing native to change */
    status = fw_prepare_profile_change(0, source);
    if (status != FW_OK)
        return status;
    state.profile_owned = 1;
    previous.data = 0;
    previous.index = state.profile_index;
    previous.sequence = state.generation;
    if (write_profile_journal(&previous, PROFILE_RESTORING, state.slot, state.profile_crc,
                              state.last_id,
                              get32(source + FW_SLOT_GENERATION_OFFSET)) != FW_OK)
        return FW_FAULT;
    state.profile_index ^= 1u;
    ++state.generation;
    state.active = 0;
    state.profile_crc = 0;
    state.uploading = 0;
    return apply_profile(source, true);
}

static uint32_t capability(uint8_t op)
{
    if (op >= FW_OP_BEGIN_PROFILE && op <= FW_OP_RESTORE_STOCK)
        return FW_CAP_PROFILE;
    if (op == FW_OP_ARM_OUTCOME || op == FW_OP_CANCEL_OUTCOME)
        return FW_CAP_OUTCOME;
    if (op == FW_OP_BET_SPIN)
        return FW_CAP_SPIN;
    return op == FW_OP_POSITION ? FW_CAP_POSITION : FW_CAP_JOG;
}

static uint8_t execute(uint8_t op, const uint8_t *p, uint32_t length)
{
    uint32_t offset, i;
    if ((fw_capabilities() & capability(op)) == 0u)
        return FW_UNSUPPORTED;
    switch (op) {
    case FW_OP_BEGIN_PROFILE:
        if (length != 6u || get16(p) != FW_PROFILE_SIZE)
            return FW_BAD_REQUEST;
        state.upload_crc = get32(p + 2);
        zero(state.coverage, sizeof(state.coverage));
        zero(state.upload, sizeof(state.upload));
        state.uploading = 1;
        return FW_OK;
    case FW_OP_PROFILE_CHUNK:
        if (length < 3u)
            return FW_BAD_REQUEST;
        if (!state.uploading)
            return FW_INCOMPLETE;
        offset = get16(p);
        if (offset >= FW_PROFILE_SIZE || length - 2u > FW_PROFILE_SIZE - offset)
            return FW_BAD_REQUEST;
        /* Conflicting overlaps reject the entire chunk, independent of ordering. */
        for (i = 0; i < length - 2u; ++i)
            if ((state.coverage[(offset + i) / 8u] & (1u << ((offset + i) % 8u))) != 0u &&
                state.upload[offset + i] != p[2u + i])
                return FW_BAD_REQUEST;
        for (i = 0; i < length - 2u; ++i) {
            state.upload[offset + i] = p[2u + i];
            state.coverage[(offset + i) / 8u] |= (uint8_t)(1u << ((offset + i) % 8u));
        }
        return FW_OK;
    case FW_OP_COMMIT_PROFILE:
        return length == 0u ? commit_profile() : FW_BAD_REQUEST;
    case FW_OP_RESTORE_STOCK:
        return length == 0u ? restore_stock() : FW_BAD_REQUEST;
    case FW_OP_CANCEL_OUTCOME:
        if (length != 0u)
            return FW_BAD_REQUEST;
        break;
    case FW_OP_ARM_OUTCOME:
    case FW_OP_POSITION:
        if (length != FW_REELS)
            return FW_BAD_REQUEST;
        for (i = 0; i < FW_REELS; ++i)
            if (p[i] >= FW_STOPS)
                return FW_BAD_REQUEST;
        break;
    case FW_OP_BET_SPIN:
        if ((length != 1u && length != 1u + FW_REELS) || p[0] == 0u ||
            p[0] > (state.active == 0 ? FW_MAX_BET : state.active[6]))
            return FW_BAD_REQUEST;
        for (i = 1; i < length; ++i)
            if (p[i] >= FW_STOPS)
                return FW_BAD_REQUEST;
        break;
    case FW_OP_JOG:
        if (length != 1u || p[0] >= FW_REELS)
            return FW_BAD_REQUEST;
        break;
    default:
        return FW_UNSUPPORTED;
    }
    return fw_submit(op, p, length, state.last_id);
}

static void poll_operation(void)
{
    uint8_t status;
    if (state.last_status != FW_ACCEPTED || !action(state.last_op) || state.fault)
        return;
    status = fw_current_status(state.last_id);
    if (status != FW_ACCEPTED)
        (void)finish_operation(status);
}

static uint8_t process_mutation(const uint8_t *data, uint32_t length, uint64_t id)
{
    uint8_t op = data[5], status;
    if (id == 0u || id < state.last_id)
        return FW_BAD_ID;
    if (id == state.last_id) {
        if (length != state.request_length || !equal(data, state.request, length))
            return FW_BAD_ID;
        poll_operation();
        return state.fault ? FW_FAULT : state.last_status;
    }
    if (state.fault)
        return FW_FAULT;
    poll_operation();
    if (state.fault)
        return FW_FAULT;
    if (state.last_status == FW_ACCEPTED &&
        !(state.last_op == FW_OP_ARM_OUTCOME &&
          (op == FW_OP_CANCEL_OUTCOME || op == FW_OP_BET_SPIN)))
        return FW_BUSY;
    if (record_operation(data, length, FW_ACCEPTED) != FW_OK) {
        state.fault = 1;
        return FW_FAULT;
    }
    status = execute(op, data + FW_REQUEST_HEADER, length - FW_REQUEST_HEADER);
    if (status == FW_FAULT)
        state.fault = 1;
    status = status == FW_ACCEPTED ? status : finish_operation(status);
    if (state.profile_owned) {
        fw_finish_profile_change(status);
        state.profile_owned = 0;
    }
    return status;
}

uint32_t fw_process(const uint8_t *data, uint32_t length, uint8_t *reply,
                    uint32_t capacity)
{
    uint8_t payload[FW_MAX_PAYLOAD];
    uint8_t op = length >= 6u ? data[5] : 0u;
    uint64_t id = length >= FW_REQUEST_HEADER ? get64(data + 6) : 0;
    uint8_t status = FW_OK;
    uint32_t size = 0, count;
    if (capacity < FW_REPLY_HEADER)
        return 0;
    if (length < FW_REQUEST_HEADER || length > FW_REQUEST_HEADER + FW_MAX_PAYLOAD ||
        data[0] != 'S' || data[1] != '6' || data[2] != 'R' || data[3] != 'E') {
        status = FW_BAD_REQUEST;
    } else if (data[4] != FW_VERSION) {
        status = FW_BAD_VERSION;
    } else if (!state.ready) {
        status = FW_FAULT;
    } else if (mutation(op)) {
        status = process_mutation(data, length, id);
    } else if (id != 0u) {
        status = FW_BAD_ID;
    } else if (op == FW_OP_INFO || op == FW_OP_STATUS) {
        size = op == FW_OP_INFO ? FW_INFO_SIZE : FW_STATUS_SIZE;
        if (length != FW_REQUEST_HEADER) {
            size = 0;
            status = FW_BAD_REQUEST;
        } else if (capacity < FW_REPLY_HEADER + size) {
            return 0;
        } else if (op == FW_OP_INFO) {
            put32(payload, fw_capabilities());
            put64(payload + 4, state.fault || state.last_id == UINT64_MAX ? 0 : state.last_id + 1u);
            put16(payload + 12, FW_PROFILE_SIZE);
            payload[14] = FW_MAX_CHUNK;
            payload[15] = FW_MAX_LINES;
            payload[16] = FW_MAX_BET;
            payload[17] = FW_MAX_TOTAL_BET;
            payload[18] = FW_STOPS;
            put32(payload + 19, FW_MAX_AWARD);
        } else {
            poll_operation();
            put64(payload, state.last_id);
            payload[8] = state.last_op;
            payload[9] = state.last_status;
            payload[10] = state.active != 0;
            put32(payload + 11, state.generation);
            put32(payload + 15, state.profile_crc);
            payload[19] = state.fault;
        }
    } else if (op == FW_OP_READ_PROFILE) {
        if (length != FW_REQUEST_HEADER + 3u) {
            status = FW_BAD_REQUEST;
        } else if (state.fault) {
            status = FW_FAULT;
        } else {
            uint32_t offset = get16(data + FW_REQUEST_HEADER);
            count = data[FW_REQUEST_HEADER + 2u];
            if (count == 0u || count > FW_MAX_PAYLOAD || offset >= FW_PROFILE_SIZE ||
                count > FW_PROFILE_SIZE - offset) {
                status = FW_BAD_REQUEST;
            } else if (state.active == 0) {
                status = FW_INCOMPLETE;
            } else if (capacity < FW_REPLY_HEADER + count) {
                return 0;
            } else {
                size = count;
                copy(payload, state.active + offset, size);
            }
        }
    } else {
        status = FW_UNSUPPORTED;
    }
    reply[0] = 'S';
    reply[1] = '6';
    reply[2] = 'R';
    reply[3] = 'E';
    reply[4] = FW_VERSION;
    reply[5] = op;
    reply[6] = status;
    put64(reply + 7, id);
    copy(reply + FW_REPLY_HEADER, payload, size);
    return FW_REPLY_HEADER + size;
}
