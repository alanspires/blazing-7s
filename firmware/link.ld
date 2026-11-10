OUTPUT_ARCH(m68k)
ENTRY(ext_boot)
PHDRS { rom PT_LOAD FLAGS(5); }

fw_runtime = 0x430600;
fw_runtime_end = 0x430a00;
motion_state = fw_runtime_end;
commission_record_start = 0x431000;
motion_state_end = commission_record_start;
game_state = 0x432000;
platform_state = 0x434000;
commission_state = 0x431800;
commission_capture = 0x435000;
commission_io_state = 0x434100;
commission_irq_guard = 0x434010;
commission_saved_regs = game_state;
commission_stack_start = game_state + 0x1000;
commission_stack_end = platform_state;
/* Commissioning cannot resume after kernel startup; E0 then owns this stack. */
sas_stack_start = commission_stack_start;
sas_stack_end = commission_stack_end;
sas_saved_sp = 0x434020;
sas_drain_mailbox = sas_saved_sp + 4;

SECTIONS {
  .text EXT_ROM_START : {
    *(.text*)
    . = ALIGN(4);
    *(.rodata*)
  } :rom
  .data : { *(.data*) *(.sdata*) } :rom
  .bss (NOLOAD) : { *(.bss*) *(.sbss*) *(COMMON) } :rom
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
  ASSERT(SIZEOF(.data) == 0, "initialized mutable globals need explicit RAM startup")
  ASSERT(SIZEOF(.bss) == 0, "working state must use explicit absolute RAM symbols")
  ASSERT(. <= EXT_ROM_END, "extension exceeds its declared ROM region")
  ASSERT(motion_state_end - motion_state == 0x600, "motion reservation size")
  ASSERT(motion_state_end <= commission_record_start, "motion overlaps commissioning record")
  ASSERT(sas_stack_end - sas_stack_start == 0x1000, "private E0 stack size")
  ASSERT(platform_state + 0x10 <= commission_irq_guard, "platform overlaps IRQ guard")
  ASSERT(commission_irq_guard + 4 <= sas_saved_sp, "saved SP overlaps IRQ guard")
  ASSERT(sas_saved_sp + 4 <= sas_drain_mailbox, "saved SP overlaps drain mailbox")
  ASSERT((sas_drain_mailbox & 3) == 0, "drain mailbox alignment")
  ASSERT(sas_drain_mailbox + 4 <= commission_io_state, "drain mailbox overlaps commissioning IO")
}
