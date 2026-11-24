    .text
    .equ CALIBRATION_ADDRESS, 0x43862a
    .equ CALIBRATION_MIN, 0x0060
    .equ CALIBRATION_MAX, 0x0800

    .macro require_calibration operand
    cmpi.w #CALIBRATION_MIN,\operand
    bcs.s platform_fatal
    cmpi.w #CALIBRATION_MAX,\operand
    bhi.s platform_fatal
    .endm
    .macro save_native
    move.w %sr,-(%sp)
    movem.l %d0-%d7/%a0-%a6,-(%sp)
    .endm

    .macro restore_native
    movem.l (%sp)+,%d0-%d7/%a0-%a6
    move.w (%sp)+,%sr
    .endm

    .globl platform_sas_handler
platform_sas_handler:
    /* Native child 17 has one live owner. Deletion retires the old continuation;
     * a replacement overwrites this saved SP and any abandoned private frames. */
    move.l %sp,sas_saved_sp
    lea sas_stack_end,%sp
    jsr platform_sas_process
    movea.l sas_saved_sp,%sp
    rts

    .globl ext_preserve_sram
ext_preserve_sram:
    /* Reset is clearing the supervisor stack too: no JSR or stack use here. */
    cmpa.l #0x430000,%a0
    bne.s 1f
    movea.l #0x436000,%a0
1:
    cmpa.l #0x438000,%a0
    jmp 0x9c2

    .globl ext_reset_runtime
ext_reset_runtime:
    /* Before any native clear/reload: retained working RAM is not initialized. */
    clr.b platform_state
    move.l #0xa5a5a5a5,0x412872
    jmp 0x9e2

    .globl ext_boot
ext_boot:
    jsr 0x25b0
    save_native
    jsr platform_boot
    restore_native
    rts

    .globl ext_restore_before_queues
ext_restore_before_queues:
    /* Only reset reaches 0x1906; a cleared machine has no calibration yet. */
    move.w %sr,-(%sp)
    tst.w CALIBRATION_ADDRESS
    beq.s 1f
    require_calibration CALIBRATION_ADDRESS
1:
    move.w (%sp)+,%sr
    save_native
    jsr platform_boot_restore
    restore_native
    jmp 0x3b56

    .globl ext_calibration_candidate
ext_calibration_candidate:
    /* Native ADDI.W #0x60 overflow wraps below MIN. Check the actual word
     * argument, not D0's unrelated upper half, before the protected store. */
    move.w %sr,-(%sp)
    require_calibration 12(%sp)
    move.w (%sp)+,%sr
    jmp 0x426

    .globl ext_calibration_timer2
ext_calibration_timer2:
    move.w %sr,-(%sp)
    require_calibration CALIBRATION_ADDRESS
    move.w (%sp)+,%sr
    move.b #0xe0,0xfe10dd
    rts

    .globl platform_fatal
platform_fatal:
    /* Native CPU fatal halt; intentionally not motor recovery or coil-off. */
    trap #1

    .globl ext_reload
ext_reload:
    jsr 0x54a88
    save_native
    jsr platform_restore
    restore_native
    rts

    .globl ext_clear_begin
ext_clear_begin:
    save_native
    jsr platform_before_clear
    restore_native
    jmp 0x35cf4

    .globl ext_clear_complete
ext_clear_complete:
    save_native
    jsr platform_after_clear
    restore_native
    jmp 0x3864a

    .globl ext_report_1f
ext_report_1f:
    save_native
    jsr platform_sas_identity
    restore_native
    /* The replaced CLR/BSR leaves its zero argument for the native UNLK. */
    move.l (%sp),-(%sp)
    clr.l 4(%sp)
    jmp 0x278fe

    .globl ext_report_53
ext_report_53:
    save_native
    jsr platform_sas_identity
    restore_native
    jmp 0x278fe

    .globl sound_latch_entry
sound_latch_entry:
    /* 0x609ec 13c2004102a7 (MOVE.B D2,0x4102a7) -> JSR sound_latch_entry.
     * Stock S6M00002B5 checked SAS sound-off (0x43e8e2) and reel-sounds-off
     * (0x43e8e3) only at 0x60b34/0x60b76, after 0x609ec..0x60b02 had already
     * written the unmuted code to 0x4102a7 (external sound board latch ->
     * 0xff100a). Apply both SAS mute gates before writing 0x4102a7 so both
     * the external sound board (code 1 = STOP) and the onboard YM2149F
     * (table[1] = 0 -> 0x60cd4) are silenced.
     * Preserves all registers except D2 (set to 1 when muted) and CCR on the
     * muted/reel-off paths; the unmuted path preserves every register and SR.
     * Stack cost: 4-byte JSR return only. */
    btst #1,0x43e8d3
    beq.s 3f
    tst.b 0x43e8e2
    beq.s 2f
    tst.b 0x43e8e3
    bne.s 3f
    cmpi.l #4,%d2
    beq.s 1f
    cmpi.l #17,%d2
    bne.s 3f
1:
    move.l %d2,0x41021c
2:
    moveq #1,%d2
3:
    move.b %d2,0x4102a7
    rts

    .globl sound_off_entry
sound_off_entry:
    /* 0x27ae6 4eb900000400 (JSR 0x400) -> JSR sound_off_entry inside SAS 03
     * (sound-off) handler 0x27ad6. Immediately latches code 1 (STOP) for the
     * external sound board (0x60d3e -> 0xff100a), then tail-jumps to 0x400
     * (SafeRAM byte write) with the caller's exact stack frame. Zero extra
     * stack bytes; preserves all registers. */
    move.b #1,0x4102a7
    jmp 0x400

    .section .note.GNU-stack,"",@progbits
