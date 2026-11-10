#ifndef S6000_COMMISSION_IO_H
#define S6000_COMMISSION_IO_H

#include "commission.h"

#define CM_MAIN_BASE 0u
#define CM_MAIN_SIZE 0x80000u
#define CM_PERSONALITY_BASE 0x100000u
#define CM_PERSONALITY_SIZE 0x10000u

#define CM_UART_MR 0xfe1031u
#define CM_UART_SR 0xfe1033u
#define CM_UART_CR 0xfe1035u
#define CM_UART_DATA 0xfe1037u
#define CM_UART_ACR 0xfe1029u
#define CM_UART_IMR 0xfe102bu
#define CM_DELIMITER 0x7eu
#define CM_ESCAPE 0x7du
#define CM_FRAME_BODY (CM_REPLY_HEADER + CM_MAX_PAYLOAD + 4u)
#define CM_FRAME_ENCODED (2u * CM_FRAME_BODY + 2u)

void commission_io_init(void);
void commission_io_step(void);
_Noreturn void commission_service(void);

#endif
