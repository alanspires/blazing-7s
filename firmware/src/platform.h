#ifndef S6000_PLATFORM_H
#define S6000_PLATFORM_H

#include <stdint.h>
#include "fw.h"

#define MMIO8(address) (*(volatile uint8_t *)(uintptr_t)(address))
#define MMIO16(address) (*(volatile uint16_t *)(uintptr_t)(address))
#define MMIO32(address) (*(volatile uint32_t *)(uintptr_t)(address))

#define PLATFORM_SAS_QUIESCED 0x73534153u

_Noreturn void platform_fatal(void);
void platform_reconfigure(void);
void platform_sas_handler(void);
/* Only the assembly entry calls this body, after selecting the private stack. */
void platform_sas_process(void);

uint16_t platform_irq_save(void);
void platform_irq_restore(uint16_t status);
/* Callers enforce their owned address range before invoking this word writer. */
int platform_write_words(uint32_t address, const uint8_t *bytes, uint32_t length);
void platform_safe_u8(uint32_t address, uint32_t value);
void platform_safe_u16(uint32_t address, uint32_t value);
void platform_safe_u32(uint32_t address, uint32_t value);
int platform_geometry_ok(void);
int platform_native_fault(void);
/* VIA timer 2 interrupt flag or enable is set (IFR/IER bit 5). Two byte reads
 * that acknowledge nothing; callers mask interrupts for a coherent snapshot. */
int platform_timer2_active(void);
uint32_t platform_ticks(void);

#endif
