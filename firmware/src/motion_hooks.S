| Task-7 Test 10 hooks for diagnostic motion (main ROM S6M00002B5).
|
| motion_test10_poll, JSR at 0x5ac3c replacing `move.w 0xff1014,%d0`
| (30 39 00 ff 10 14). Entry: (%sp) = 0x5ac42, stack otherwise balanced.
|   Native path: every register restored, then the original word load runs so
|   D0.w = FF1014, the upper word of D0 is unchanged and N/Z/V/C come from that load
|   with X unchanged from entry; returns to 0x5ac42.
|   Owned path: all registers restored, return address replaced by 0x5ac2a (the
|   two-tick queue wait of the loop); no physical selector, jog or payout code.
|
| motion_test10_cleanup, JSR at 0x5af80 replacing `jsr 0x5b5fe`
| (4e b9 00 05 b5 fe). Entry: (%sp) = 0x5af86; no caller arguments outstanding.
|   Native path: all registers restored and `jmp 0x5b5fe` with the same stack,
|   so 0x5b5fe returns to 0x5af86 exactly as the original JSR.
|   Owned path: native delay 0x3a1e(2) (stack long tick count, caller pops), so
|   an invalid queue cannot spin, then all registers restored and return to
|   0x5ac2a. CCR is not preserved: 0x5ac2a starts with PEA/JSR.

    .text
    .equ TEST10_LOOP, 0x5ac2a
    .equ NATIVE_CLEANUP, 0x5b5fe
    .equ NATIVE_DELAY, 0x3a1e
    .equ INPUTS, 0xff1014

    .globl motion_test10_poll
motion_test10_poll:
    move.w %sr,-(%sp)
    movem.l %d0-%d1/%a0-%a1,-(%sp)
    clr.l -(%sp)
    jsr motion_test10_step
    addq.l #4,%sp
    tst.l %d0
    bne.s 1f
    movem.l (%sp)+,%d0-%d1/%a0-%a1
    move.w (%sp)+,%ccr
    move.w INPUTS,%d0
    rts
1:  movem.l (%sp)+,%d0-%d1/%a0-%a1
    addq.l #2,%sp
    move.l #TEST10_LOOP,(%sp)
    rts

    .globl motion_test10_cleanup
motion_test10_cleanup:
    movem.l %d0-%d1/%a0-%a1,-(%sp)
    pea 1.w
    jsr motion_test10_step
    addq.l #4,%sp
    tst.l %d0
    bne.s 1f
    movem.l (%sp)+,%d0-%d1/%a0-%a1
    jmp NATIVE_CLEANUP
1:  pea 2.w
    jsr NATIVE_DELAY
    addq.l #4,%sp
    movem.l (%sp)+,%d0-%d1/%a0-%a1
    move.l #TEST10_LOOP,(%sp)
    rts

| motion_test10_return, JMP at 0x5af86 replacing the Test 10 epilogue
| `movem.l (%sp)+,%d2-%d6; rts` (4c df 00 7c 4e 75). Every Test 10 return
| passes here: the loop exits through 0x5af80 and the branches at 0x5abb6
| (initial queue wait) and 0x5aefe bypass both other hooks. Entry: (%sp) = saved
| D2-D6, then the caller return address. D0-D1/A0-A1 and the entry CCR are
| restored before the original epilogue runs, so registers, CCR and stack leave
| exactly as the original two instructions leave them.

    .globl motion_test10_return
motion_test10_return:
    move.w %sr,-(%sp)
    movem.l %d0-%d1/%a0-%a1,-(%sp)
    jsr motion_test10_exit
    movem.l (%sp)+,%d0-%d1/%a0-%a1
    move.w (%sp)+,%ccr
    movem.l (%sp)+,%d2-%d6
    rts
