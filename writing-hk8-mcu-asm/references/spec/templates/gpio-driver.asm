; ========================================================
; TEMPLATE: gpio-driver.asm
; STATUS: draft; PA2 ownership, polarity and clock must be confirmed
; CHIP: HK64S8101 baseline
; TOOLCHAIN: company_ide or python_source_module_cli (no DB)
; BOARD CONTRACT: this probe owns all PA configuration registers and PA2
; FUNCTION: blink PA2 for pin/polarity/timing validation
; RULES: HK-GPIO-001, HK-MEM-001, HK-MEM-004, HK-SYN-007
;
; SRAM ALLOCATION
; 80H GPIO_DELAY_OUTER scratch owner=GPIO_DELAY lifetime=one call
; 81H GPIO_DELAY_INNER scratch owner=GPIO_DELAY lifetime=one call
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

  MOV A,#00H
  MOV PA_PIO,A           ; preload safe low before enabling PA2
  MOV A,#04H
  MOV PA_POE,A

MAIN_LOOP:
  BSET PA_PIO,2
  CALL GPIO_DELAY
  BCLR PA_PIO,2
  CALL GPIO_DELAY
  JMP MAIN_LOOP

; GPIO_DELAY
; IN: none
; OUT: none
; CLOBBERS: A, 80H, 81H, flags
; REENTRANT: no
; TIMING: must be measured for the selected clock/OPTION
GPIO_DELAY:
  MOV A,#20H
  MOV 80H,A
GPIO_DELAY_OUTER:
  MOV A,#FFH
  MOV 81H,A
GPIO_DELAY_INNER:
  CLRWDT
  DECSZR 81H
  JMP GPIO_DELAY_INNER
  DECSZR 80H
  JMP GPIO_DELAY_OUTER
  RET

END
