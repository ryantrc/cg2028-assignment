/*
 * mov_avg.s
 *
 * CG2028 Assignment starter file.
 */
.syntax unified
.cpu cortex-m4
.thumb
.global ewma_filter
.type ewma_filter, %function

.text
.align 2

@ CG2028 Assignment
@ (c) ECE NUS
@ Write Student 1's Name here: Ryan Tan Rong Chang (A0306841H)
@ Write Student 2's Name here: WXYZ (A0000007X)
@
@ Function prototype:
@   int ewma_filter(int new_data, int old_output, int alpha_percent);
@
@ ARM calling convention:
@   R0 = new_data       (signed integer sensor sample)
@   R1 = old_output     (previous filtered output)
@   R2 = alpha_percent  (integer from 0 to 100)
@   Return R0 = (alpha_percent * new_data
@                + (100 - alpha_percent) * old_output) / 100
@
@ Notes:
@ - Use signed integer arithmetic.
@ - Integer division must truncate towards zero, matching C integer division.
@ - Preserve all callee-saved registers that you use (R4-R11).
@ - Do not call a C helper function and do not use floating-point instructions.
@
@ Register table:
@   R0 = new_data on entry; filtered result on return
@   R1 = old_output (unchanged)
@   R2 = alpha_percent (unchanged)
@   R3 = weighted new_data, then the complete numerator
@   R4 = constant 100
@   R5 = (100 - alpha_percent), then weighted old_output
@   R6-R7 = unused; preserved by the starter PUSH/POP
@
@ Write your program from here.
.thumb_func
ewma_filter:
    PUSH {r4-r7, lr}

    @ Weight the new reading by alpha_percent.
    MUL  r3, r2, r0          @ R3 = alpha_percent * new_data

    @ Weight the previous result by the remaining percentage.
    MOV  r4, #100            @ R4 = 100 (also the final divisor)
    SUB  r5, r4, r2          @ R5 = 100 - alpha_percent
    MUL  r5, r5, r1          @ R5 = (100 - alpha_percent) * old_output

    @ Add first, then divide once to match the C reference's rounding.
    ADD  r3, r3, r5          @ R3 = complete weighted sum
    SDIV r0, r3, r4          @ R0 = sum / 100, truncated towards zero

    POP  {r4-r7, pc}

.size ewma_filter, .-ewma_filter
