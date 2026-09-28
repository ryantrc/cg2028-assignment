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
@ Write Student 2's Name here: Chong Kai Jie (A0306749U)
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
@   R0  = new_data on entry; filtered result on return
@   R1  = old_output; intermediate multiplied by (100-alpha)
@   R2  = alpha_percent (unchanged)
@   R3  = constant 100
    R12 = 100 - alpha
@
@ Write your program from here.
.thumb_func
ewma_filter:

    MOV R3, #100
    SUB R12, R4, R2
    MUL R1, R3, R1
    MLA R0, R0, R2, R1
    SDIV R0, R0, R4

    BX LR

.size ewma_filter, .-ewma_filter
