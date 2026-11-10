/* Native game hook adapters. Every routine documents its patch site, exact
 * original bytes, entry/exit stack and register/CCR contract. Three hooks
 * need no adapter because their original callee is itself a C-ABI call with
 * the same stack shape (JSR directly to the C symbol):
 *
 *   0x0a53e 4eb90000ae6a  JSR game_next_event   (no args; D0.w event count)
 *   0x4324e 4eb9000549c8  JSR game_repeat_wager (caller's pushed counter id)
 *   0x65168 4eb900035fdc  JSR game_evaluate_wager (no args)
 *
 * C callees clobber only D0/D1/A0/A1 and CCR, exactly like the replaced
 * native C callees, and preserve D2-D7/A2-A6. */

    .text

/* 0x42fac 4eb90004341c -> JSR game_bet_one_entry (credit BET ONE handler
 * 0x42f08, inside its 0x3d82 lock taken at 0x42fa6). Entry SP: return 0x42fb2
 * above the dispatcher's return; 0x42f08 has no frame or saved registers.
 * Accepted: performs whole-line BET ONE and returns to 0x42fb2. Refused:
 * releases the lock (0x3d88, as the native exit at 0x4307a does) and returns
 * to the native refusal 0x42f9e (JSR 0x35f5a; RTS), so no state change.
 * D0/D1/A0/A1/CCR clobbered as by 0x4341c; all other registers preserved. */
    .globl game_bet_one_entry
game_bet_one_entry:
    jsr     game_bet_one
    tst.l   %d0
    bne.s   1f
    jsr     0x3d88
    move.l  #0x42f9e,(%sp)
1:  rts

/* 0x61172 4e56fffc48e7 -> JMP game_start_entry; pad 0x61178 3c00 -> 4e71
 * (never executed). Entry of FUN_00061172, the normal game start, reached from
 * the event dispatcher and directly from native alternate-transaction and
 * special-game paths. Entry stack: caller's return address only. Refused:
 * returns to the caller with D0 = 0 and no frame, like the native early exit
 * 0x61920 (callers ignore D0). Permitted: replays LINK A6,#-4 and
 * MOVEM.L D2-D5,-(SP) exactly, then continues at 0x6117a, so 4(A6) remains the
 * caller's return address. D0/D1/A0/A1/CCR are scratch at a C entry. */
    .globl game_start_entry
game_start_entry:
    jsr     game_start_permitted
    tst.l   %d0
    bne.s   1f
    rts
1:  link.w  %a6,#-4
    movem.l %d2-%d5,-(%sp)
    jmp     0x6117a

/* 0x3fc34 23c0004103b0 (MOVE.L D0,0x4103b0) -> JSR game_diag_wager_entry,
 * returns to 0x3fc3a. D0 is the native-capped aggregate diagnostic wager.
 * Stores the per-line wager instead; preserves every register including D0.
 * The entry CCR is restored after the C call (which may change X), then TST.L
 * reproduces MOVE's flags: N/Z of D0, V=C=0, X unchanged. */
    .globl game_diag_wager_entry
game_diag_wager_entry:
    move.w  %sr,-(%sp)
    movem.l %d0-%d1/%a0-%a1,-(%sp)
    move.l  %d0,-(%sp)
    jsr     game_line_wager
    addq.l  #4,%sp
    move.l  %d0,0x4103b0
    movem.l (%sp)+,%d0-%d1/%a0-%a1
    move.w  (%sp)+,%ccr
    tst.l   %d0
    rts

/* Line-order selection. Original 8 bytes 0c3900020043ca1f (CMPI.B #2,0x43ca1f)
 * become JMP + one NOP (4e71, never executed). Sets A2 to the row-order table
 * and jumps to the native continuation; no stack effect. A custom profile uses
 * the native identity table 0x6ac60 (center, top, bottom, two diagonals for
 * lines 0..4); otherwise the native choice (0x6ac58 for 2 or 4 lines) is
 * reproduced. Only A2 and CCR change; the continuation's MOVEQ redefines CCR.
 *   0x65184 -> JMP game_line_order_entry,      continue 0x651a6 (normal)
 *   0x3fbcc -> JMP game_diag_line_order_entry, continue 0x3fbee (Test 10) */
    .macro LINE_ORDER resume
    tst.b   game_state              /* game_state.identity_order, offset 0 */
    bne.s   2f
    cmpi.b  #2,0x43ca1f
    beq.s   1f
    cmpi.b  #4,0x43ca1f
    bne.s   2f
1:  movea.l #0x6ac58,%a2
    jmp     \resume
2:  movea.l #0x6ac60,%a2
    jmp     \resume
    .endm

    .globl game_line_order_entry
game_line_order_entry:
    LINE_ORDER 0x651a6

    .globl game_diag_line_order_entry
game_diag_line_order_entry:
    LINE_ORDER 0x3fbee

/* 0x615ce 207c004101dd12300000 (MOVEA.L #0x4101dd,A0; MOVE.B 0(A0,D0.W),D1)
 * -> JMP game_candidate_entry + two NOPs (4e714e71, never executed); no stack
 * effect. Inside FUN_00061172's copy loop: D2 = reel index, D0.w = 4*D2,
 * A6 = 61172's frame (4(A6) is its caller's return address). Replaces only
 * D1's low byte with the stop to commit, leaves A0 = 0x4101dd as the original
 * did, preserves all other registers and the entry CCR (so X is unchanged, as
 * after the original MOVE.B), and jumps to 0x615d8, whose MOVE.L redefines
 * N/Z/V/C; X is next written by 0x615ec ADDQ.L, with 0x400 only saving and
 * restoring SR in between. */
    .globl game_candidate_entry
game_candidate_entry:
    move.w  %sr,-(%sp)
    movem.l %d0-%d1/%a1,-(%sp)
    move.l  4(%a6),-(%sp)
    move.l  %d2,-(%sp)
    jsr     game_candidate
    addq.l  #8,%sp
    move.b  %d0,7(%sp)              /* low byte of the saved D1 */
    movem.l (%sp)+,%d0-%d1/%a1
    move.w  (%sp)+,%ccr
    movea.l #0x4101dd,%a0
    jmp     0x615d8

/* 0x615fa 4a390043cd59 (TST.B 0x43cd59) -> JSR game_commit_entry, returns to
 * 0x61600. Runs after the copy loop's SafeRAM writes (or after the native
 * guards skipped them). Preserves every register; restores the entry CCR after
 * the C call and reproduces the original TST.B (N/Z, V=C=0, X unchanged) for
 * the BNE at 0x61600. */
    .globl game_commit_entry
game_commit_entry:
    move.w  %sr,-(%sp)
    movem.l %d0-%d1/%a0-%a1,-(%sp)
    jsr     game_commit
    movem.l (%sp)+,%d0-%d1/%a0-%a1
    move.w  (%sp)+,%ccr
    tst.b   0x43cd59
    rts

/* 0x589ba 4e56ffb048e73f00 -> JMP game_settlement_entry + NOP.
 * Entry SP includes the native caller's return. Admit only with the proved
 * extension-live reserve (TASK-STACK-AUDIT.md); this is not a bound on all
 * legacy side callees. IRQ/private-E0 domains do not synchronously enter here.
 * Bootstrap uses its own arena even after CURRENT becomes nonnull. Resumed
 * native tasks use their allocated top, not the saved context SP at TCB+0x38.
 * Both exits restore every register and full SR. Success replays the original
 * 108-byte prologue, then tail-enters 0x589c2; refusal leaves the original
 * stack shape for platform_fatal's TRAP1 exception, with no accounting writes.
 */
    .equ SETTLEMENT_RESERVE, 2232
    .equ SETTLEMENT_SCRATCH, 10
    .equ SETTLEMENT_BOOT_BASE, 0x436000
    .equ SETTLEMENT_BOOT_END, 0x438000

    .globl game_settlement_entry
game_settlement_entry:
    move.w  %sr,-(%sp)
    movem.l %d0/%a0,-(%sp)
    move.l  %sp,%d0
    addi.l  #SETTLEMENT_SCRATCH,%d0
    cmpi.l  #SETTLEMENT_BOOT_BASE,%d0
    bcs.s   1f
    cmpi.l  #SETTLEMENT_BOOT_END,%d0
    bcc.s   1f
    movea.l #SETTLEMENT_BOOT_BASE+SETTLEMENT_RESERVE,%a0
    bra.s   2f
1:  movea.l 0x400024,%a0
    movea.l 0x40(%a0),%a0
    moveq   #0,%d0
    move.w  0x27b6,%d0
    suba.l  %d0,%a0
    lea     SETTLEMENT_RESERVE(%a0),%a0
    move.l  %sp,%d0
    addi.l  #SETTLEMENT_SCRATCH,%d0
2:  cmpa.l  %d0,%a0
    bhi.s   3f
    movem.l (%sp)+,%d0/%a0
    move.w  (%sp)+,%sr
    link.w  %a6,#-80
    movem.l %d2-%d7,-(%sp)
    jmp     0x589c2
3:  movem.l (%sp)+,%d0/%a0
    move.w  (%sp)+,%sr
    jmp     platform_fatal

    .section .note.GNU-stack,"",@progbits
