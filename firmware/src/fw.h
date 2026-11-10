#ifndef S6000_FW_H
#define S6000_FW_H

#include <stdbool.h>
#include <stdint.h>

#define FW_VERSION 1u
#define FW_MAX_PAYLOAD 64u
#define FW_REQUEST_HEADER 14u
#define FW_REPLY_HEADER 15u
#define FW_PROFILE_SIZE 252u
#define FW_PROFILE_GUARD_SIZE 32u
#define FW_PROFILE_RECORD_OFFSET 76u
#define FW_PROFILE_RECORD_SIZE 16u
#define FW_PROFILE_RECORD_COUNT 11u
#define FW_MAX_CHUNK 62u
#define FW_MAX_LINES 5u
#define FW_MAX_BET 3u
#define FW_MAX_TOTAL_BET 15u
#define FW_MAX_AWARD 1500u
#define FW_STOPS 22u
#define FW_REELS 3u
#define FW_INFO_SIZE 23u
#define FW_STATUS_SIZE 20u

#define FW_PROFILE_BASE 0x430000u
#define FW_SLOT_SIZE 512u
#define FW_PROFILE_JOURNAL 0x430400u
#define FW_OPERATION_JOURNAL 0x430500u
#define FW_JOURNAL_SIZE 128u
#define FW_PERSIST_SIZE 0x600u
#define FW_RUNTIME_BASE 0x430600u
#define FW_RUNTIME_SIZE 1024u
#define FW_SLOT_CONTEXT_OFFSET 284u
#define FW_SLOT_BASELINE_OFFSET 288u
#define FW_SLOT_BASELINE_SIZE 177u
#define FW_SLOT_FORMAT_OFFSET 492u
#define FW_SLOT_FORMAT 2u
#define FW_SLOT_MAGIC_OFFSET 496u
#define FW_SLOT_GENERATION_OFFSET 500u
#define FW_SLOT_CRC_OFFSET 506u
#define FW_SLOT_MARKER_OFFSET 510u
#define FW_JOURNAL_CRC_OFFSET 122u
#define FW_JOURNAL_MARKER_OFFSET 126u
#define FW_OPERATION_REQUEST_OFFSET 20u
#define FW_MAX_REQUEST 78u
#define FW_COMMIT_MARKER 0xa55au
#define FW_SLOT_MAGIC 0x4c533653u
#define FW_JOURNAL_PROFILE_MAGIC 0x4a503653u
#define FW_JOURNAL_OPERATION_MAGIC 0x4a493653u

#define FW_CAP_PROFILE 0x01u
#define FW_CAP_OUTCOME 0x02u
#define FW_CAP_SPIN 0x04u
#define FW_CAP_POSITION 0x08u
#define FW_CAP_JOG 0x10u

#define FW_OP_INFO 0u
#define FW_OP_STATUS 1u
#define FW_OP_READ_PROFILE 2u
#define FW_OP_BEGIN_PROFILE 16u
#define FW_OP_PROFILE_CHUNK 17u
#define FW_OP_COMMIT_PROFILE 18u
#define FW_OP_RESTORE_STOCK 19u
#define FW_OP_ARM_OUTCOME 32u
#define FW_OP_CANCEL_OUTCOME 33u
#define FW_OP_BET_SPIN 34u
#define FW_OP_POSITION 35u
#define FW_OP_JOG 36u

#define FW_OK 0u
#define FW_ACCEPTED 1u
#define FW_BAD_REQUEST 2u
#define FW_BAD_VERSION 3u
#define FW_BAD_ID 4u
#define FW_BUSY 5u
#define FW_FAULT 6u
#define FW_INVALID_PROFILE 7u
#define FW_INCOMPLETE 8u
#define FW_INTERRUPTED 9u
#define FW_UNSUPPORTED 10u

/* Requests/replies exclude the outer SAS length byte. Return zero if no reply fits. */
uint32_t fw_process(const uint8_t *data, uint32_t length, uint8_t *reply,
                    uint32_t capacity);
/* Only uniform erased SRAM or exact interrupted virgin-baseline prefixes may
 * initialize automatically. Arbitrary nonblank SRAM requires offline provisioning. */
uint8_t fw_boot(void);
uint8_t fw_restore(void);
int fw_is_faulted(void);
uint8_t fw_validate_profile(const uint8_t *profile);
/* Incremental CRC uses an uncomplemented accumulator; start at UINT32_MAX. */
uint32_t fw_crc32_update(uint32_t crc, const uint8_t *data, uint32_t length);
uint32_t fw_crc32(const uint8_t *data, uint32_t length);
const uint8_t *fw_active_profile(void);

/* Call before native clear, even before BSS initialization; abort clear on failure.
 * Only call clear_complete after native clear successfully finishes. Interrupted
 * clear boots faulted, never selecting the old profile or clearing native faults. */
uint8_t fw_invalidate(void);
uint8_t fw_clear_complete(void);
/* Read-only: allow the native operator-clear branch to finish a journaled clear. */
int fw_clear_pending(void);

/* Platform ports. Writes may fail after a prefix; a final aligned two-byte write
 * is atomic. The core reads mapped SRAM but never writes it except via this port.
 * Ports must not reenter request processing or boot/restore. Caller serializes
 * requests and native transitions; pure validation and state accessors are allowed. */
int fw_storage_write(uint32_t address, const uint8_t *data, uint32_t length);
uint32_t fw_capabilities(void);
/* Successful preparation owns native exclusion until finish, including durable
 * operation completion. Boot restoration instead relies on pre-task exclusion.
 * staged: the slot image being committed (wire profile at offset 0); preparation
 * fills its context and original-configuration baseline. NULL prepares a stock
 * restoration from active. active: the committed slot now in effect, or NULL. */
uint8_t fw_prepare_profile_change(uint8_t *staged, const uint8_t *active);
void fw_finish_profile_change(uint8_t status);
/* (slot, false) applies a committed profile; (slot, true) restores the original
 * configuration recorded in that slot; (NULL, false) checks settled stock. */
uint8_t fw_apply_profile(const uint8_t *slot, bool restore);
/* Native ports own action-specific admission, including diagnostic mode and cancel. */
uint8_t fw_submit(uint8_t operation, const uint8_t *args, uint32_t length,
                  uint64_t id);
uint8_t fw_current_status(uint64_t id);

/* INFO LE payload: capabilities:u32, next_id:u64, profile_size:u16,
 * max_chunk:u8, max_lines:u8, max_bet:u8, max_total_bet:u8, stops:u8, max_award:u32.
 * STATUS LE payload: last_id:u64, last_op:u8, last_status:u8, active:u8,
 * generation:u32, profile_crc:u32, fault:u8. READ_PROFILE returns raw bytes. */

#endif
