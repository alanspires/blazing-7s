#ifndef S6000_MOTION_H
#define S6000_MOTION_H

#include <stdint.h>
#include "fw.h"

/* Diagnostic reel motion inside an already running native Test 10.
 *
 * Wire results are FW_* statuses. Pending/running is FW_ACCEPTED, a verified
 * settled move FW_OK, cancellation before any motor write FW_INTERRUPTED, an
 * inadmissible machine FW_BUSY and every failure after motor setup FW_FAULT.
 * Failures keep ownership and the start inhibit until motion_init() runs at
 * boot/native reinitialization; nothing here clears native fault fields. */

enum motion_phase {
    MOTION_IDLE,
    MOTION_PENDING,   /* published by the SAS worker, not yet claimed by task 7 */
    MOTION_RUNNING,   /* native motor setup published, polling for settled idle */
    MOTION_RELEASING, /* verified result recorded; one more owned Test 10 pass */
    MOTION_ABORTING,  /* failure recorded; waiting for the native motor to drain */
    MOTION_LATCHED    /* failure retained until boot/native reinitialization */
};

enum motion_cause {
    MOTION_CAUSE_NONE,
    MOTION_CAUSE_REJECTED,     /* admission failed when task 7 claimed it */
    MOTION_CAUSE_CANCELLED,    /* Test 10 exited before any motor write */
    MOTION_CAUSE_NATIVE_FAULT, /* native fault fields or fault mode */
    MOTION_CAUSE_CONTEXT,      /* Test 10, mode, door, geometry or selector changed */
    MOTION_CAUSE_EXIT,         /* Test 10 exited while the motor was owned */
    MOTION_CAUSE_TIMEOUT,      /* movement deadline expired */
    MOTION_CAUSE_MISMATCH      /* settled stops differ from the requested stops */
};

/* Boot/native reinitialization only: discards any pending or running request. */
void motion_init(void);
/* Nonzero while a request is pending, owned or a failure is latched. */
int motion_is_busy(void);
/* FW_CAP_POSITION | FW_CAP_JOG only in a qualified build, otherwise zero. */
uint32_t motion_capabilities(void);
/* SAS worker: validates and publishes; never writes motor state or waits. */
uint8_t motion_submit(uint8_t operation, const uint8_t *args, uint32_t length,
                      uint64_t id);
/* FW_BAD_ID unless id is the module's current request. */
uint8_t motion_current_status(uint64_t id);

/* Task 7 only, through motion_hooks.S. A Test 10 loop pass (0x5ac3c) or its
 * exit cleanup (0x5af80): performs bounded work and returns nonzero when the
 * module owns the pass. Requests are admitted only after a loop pass ran. */
uint32_t motion_test10_step(uint32_t exiting);
/* Task 7 only: Test 10 epilogue (0x5af86), reached by every return including
 * those that bypass the loop. Cancels an unclaimed request; ends availability. */
void motion_test10_exit(void);

/* Deadlines in native ticks (long 0x4127f6) for POSITION, JOG and the abort. */
uint32_t motion_timeout_ticks(uint8_t operation);
uint32_t motion_abort_ticks(void);

uint8_t motion_phase(void);
uint8_t motion_cause(void);
int motion_recovery_required(void);

#endif
