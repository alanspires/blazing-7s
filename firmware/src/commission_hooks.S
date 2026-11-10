    .text
    .set CM_NATIVE_GUARD,0x4e415456

    .globl commission_reset
commission_reset:
    /* Retained RAM must never select the native AC-fail path during commissioning. */
    clr.l commission_irq_guard
    move.w #0x2700,%sr
    movea.l 0x0,%sp
    jmp 0x808

    .globl commission_gate
commission_gate:
    movem.l %d0-%d7/%a0-%a7,commission_saved_regs
    move.w %sr,commission_saved_regs+64
    clr.l commission_irq_guard
    lea commission_stack_end,%sp
    jsr commission_boot
    tst.b %d0
    bne.s 1f
    move.l #CM_NATIVE_GUARD,commission_irq_guard
    movem.l commission_saved_regs,%d0-%d7/%a0-%a7
    move.w commission_saved_regs+64,%sr
    lea 0x43dbae,%a0
    jmp 0xa5e
1:
    jmp commission_service

    .globl commission_ac_fail
commission_ac_fail:
    cmpi.l #CM_NATIVE_GUARD,commission_irq_guard
    bne.s commission_power_failed
    jmp 0x275a

    .globl commission_power_failed
commission_power_failed:
    /* An IPL7 edge bypasses SR's mask. Never return to a pending persistence store. */
    move.w #0x2700,%sr
    lea commission_stack_end,%sp
1:
    clr.b 0xff101e
    bra.s 1b

    .section .note.GNU-stack,"",@progbits
