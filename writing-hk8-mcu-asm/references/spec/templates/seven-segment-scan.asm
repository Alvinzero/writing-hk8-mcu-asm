; ========================================================
; TEMPLATE: seven-segment-scan.asm
; STATUS: board-specific draft; validates fixed visual pattern 1234
; CHIP: HK64S8101 baseline
; TOOLCHAIN: company_ide or python_source_module_cli (no DB)
; CURRENT BOARD ONLY:
; PB7=A PB6=B PB5=C PB4=D PB3=E PB2=F PB1=G PB0=DP
; PA2=COM0(CA), PA3=COM1(CA), PA5=COM2(CC), PA6=COM3(CC)
; visual order=COM2,COM3,COM0,COM1; all-off PA_PIO=60H
; RULES: HK-7SEG-001, HK-7SEG-002, HK-7SEG-003,
;        HK-7SEG-004, HK-7SEG-005
;
; SRAM ALLOCATION
; 80H SCAN_DELAY_OUT scratch owner=SCAN_DELAY lifetime=one call
; 81H SCAN_DELAY_IN  scratch owner=SCAN_DELAY lifetime=one call
; ========================================================

ORG 0x0000
  JMP INIT

ORG 0x0008
  RETI

ORG 0x0010
INIT:
  MOV A,#00H
  MOV PA_PPU,A
  MOV PA_PPD,A
  MOV PA_POD,A
  MOV PA_PSL,A
  MOV PB_PPU,A
  MOV PB_PPD,A
  MOV PB_POD,A
  MOV PB_PSL,A

  MOV A,#FFH
  MOV PB_PIO,A           ; safe for common-anode digits before enable
  MOV A,#60H
  MOV PA_PIO,A           ; all COM off
  MOV A,#FFH
  MOV PB_POE,A
  MOV A,#6CH
  MOV PA_POE,A

MAIN_LOOP:
  CALL DISPLAY_1234_ONCE
  JMP MAIN_LOOP

; DISPLAY_1234_ONCE
; CLOBBERS: A, 80H, 81H, PA_PIO, PB_PIO, flags
DISPLAY_1234_ONCE:
  ; Visual 1: COM2, common cathode, digit 1 = 60H
  MOV A,#60H
  MOV PA_PIO,A
  MOV A,#60H
  MOV PB_PIO,A
  MOV A,#40H
  MOV PA_PIO,A
  CALL SCAN_DELAY

  ; Visual 2: COM3, common cathode, digit 2 = DAH
  MOV A,#60H
  MOV PA_PIO,A
  MOV A,#DAH
  MOV PB_PIO,A
  MOV A,#20H
  MOV PA_PIO,A
  CALL SCAN_DELAY

  ; Visual 3: COM0, common anode, digit 3 = 0DH
  MOV A,#60H
  MOV PA_PIO,A
  MOV A,#0DH
  MOV PB_PIO,A
  MOV A,#64H
  MOV PA_PIO,A
  CALL SCAN_DELAY

  ; Visual 4: COM1, common anode, digit 4 = 99H
  MOV A,#60H
  MOV PA_PIO,A
  MOV A,#99H
  MOV PB_PIO,A
  MOV A,#68H
  MOV PA_PIO,A
  CALL SCAN_DELAY

  MOV A,#60H
  MOV PA_PIO,A
  RET

; SCAN_DELAY
; CLOBBERS: A, 80H, 81H, flags
; TIMING: measure and tune for selected clock/current limits
SCAN_DELAY:
  MOV A,#02H
  MOV 80H,A
SCAN_DELAY_OUTER:
  MOV A,#FFH
  MOV 81H,A
SCAN_DELAY_INNER:
  CLRWDT
  DECSZR 81H
  JMP SCAN_DELAY_INNER
  DECSZR 80H
  JMP SCAN_DELAY_OUTER
  RET

END
