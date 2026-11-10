#ifndef S6000_COMMISSION_H
#define S6000_COMMISSION_H

#include "fw.h"

/* Pre-boot first-install commissioning core. See COMMISSIONING-CONTRACT.md. */

#define CM_VERSION 1u
#define CM_REQUEST_HEADER 6u
#define CM_REPLY_HEADER 7u
#define CM_MAX_PAYLOAD 64u
#define CM_MAX_CHUNK 60u
#define CM_INFO_SIZE 17u
#define CM_IDENTITY_SIZE 52u
#define CM_FIRST_INSTALL_SIZE 13u

#define CM_RECORD_BASE 0x431000u
#define CM_RECORD_SIZE 0x800u
#define CM_STATE_SIZE 0x800u
#define CM_CAPTURE_SIZE 0xe00u
#define CM_RECORD_MAGIC 0x49433653u
#define CM_RECORD_VERSION 1u
#define CM_RECORD_ORIGINAL_CRC_OFFSET 8u
#define CM_RECORD_ORIGINAL_OFFSET 16u
#define CM_RECORD_CRC_OFFSET 0x7fau
#define CM_RECORD_MARKER_OFFSET 0x7feu
#define CM_READY 0xa55au
#define CM_DONE 0x5aa5u

#define CM_BOOT_CONTINUE 0u
#define CM_BOOT_SERVICE 1u

#define CM_MODE_UNKNOWN 0u
#define CM_MODE_READY 1u
#define CM_MODE_DONE 2u
#define CM_MODE_REFUSED 3u

#define CM_OP_INFO 0u
#define CM_OP_READ 1u
#define CM_OP_STAGE 2u
#define CM_OP_INIT 3u
#define CM_OP_IDENTITY 4u

/* Request: "S6CM", version, op, payload. Reply: "S6CM", version, op, status,
 * payload; every non-OK reply has an empty payload. Statuses (fw.h):
 *   FW_BAD_REQUEST     malformed request, range, INIT literal or staging conflict
 *   FW_BAD_VERSION     unsupported request version
 *   FW_UNSUPPORTED     unknown op
 *   FW_INCOMPLETE      INIT without full staged coverage
 *   FW_INVALID_PROFILE INIT refused, nothing written: mode DONE/REFUSED, the staged
 *                      archive holds recognizable extension state, or it does not
 *                      match current storage / an exact restartable prefix
 *   FW_FAULT           INIT write/readback failed after writes may have begun; the
 *                      session reports REFUSED until a physical restart, then resume
 *                      only with the same original archive
 *   FW_OK              INIT completed: mode DONE, service continues until restart
 * INFO LE payload: mode:u8, persist_base:u32, persist_size:u16, record_base:u32,
 * record_size:u16, capture_size:u16, max_chunk:u16. READ offset:u16 count:u8
 * returns current persistence+record bytes. STAGE offset:u16 data. INIT payload is
 * ASCII "FIRST-INSTALL". IDENTITY payload is commission_identity's 52 bytes. */

/* Call before any request. CONTINUE only with a valid DONE record or no recognizable
 * record, when fw_boot succeeds or a journaled native clear is pending; without a
 * record the session mode is then REFUSED, so INIT never formats it. Never applies
 * a profile, submits an action, writes native state or starts tasks; it calls fw_boot
 * only without a READY or recognizable invalid commissioning record. */
uint8_t commission_boot(void);
/* Returns the reply length, or zero when capacity is below a maximum reply. */
uint32_t commission_process(const uint8_t *request, uint32_t length, uint8_t *reply,
                            uint32_t capacity);

/* Platform ports. Storage writes aligned words in ascending address order, only in
 * persistence or the commissioning record, may stop after a prefix (each word atomic)
 * and returns nonzero only after writing and reading back everything. */
int commission_storage_write(uint32_t address, const uint8_t *data, uint32_t length);
void commission_watchdog(void);
/* ROM identity 0x720 (32), personality identity 0x10d1c0 (12), CRC32 main 0..0x7ffff
 * and personality 0x100000..0x10ffff (u32le each). */
void commission_identity(uint8_t *identity);

#endif
