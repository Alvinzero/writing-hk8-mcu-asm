; ========================================================
; TEMPLATE: i2c-bitbang.asm
; STATUS: draft until voltage, pull-ups, address and waveform are confirmed
; CHIP: HK64S825 baseline
; TOOLCHAIN: company_ide or python_source_module_cli (no DB)
; BOARD PROFILE EXAMPLE: SDA=PB7, SCL=PB6, 7-bit addr=3CH, write byte=78H
; RULES: HK-GPIO-001, HK-I2C-001, HK-I2C-002, HK-I2C-003, HK-I2C-005, HK-I2C-006, HK-OLED-005
;
; SRAM ALLOCATION
; 80H I2C_SHIFT scratch owner=I2C_SEND lifetime=one call
; 81H I2C_COUNT scratch owner=I2C_SEND lifetime=one call
; 82H I2C_ACK   handoff owner=I2C_SEND lifetime=until next send (0=ACK)
; 83H DELAY_OUT scratch owner=PROBE_DELAY lifetime=one call
; 84H DELAY_IN  scratch owner=PROBE_DELAY lifetime=one call
; ========================================================

ORG 0x0000
  JMP INIT

ORG 0x0008
  RETI

ORG 0x0010
INIT:
  MOV A,#C0H
  MOV PB_PPU,A
  MOV A,#C0H
  MOV PB_PIO,A           ; preload idle high
  MOV A,#C0H
  MOV PB_POE,A

MAIN_LOOP:
  CALL PROBE_DELAY
  CALL I2C_START
  MOV A,#78H             ; 7-bit 3CH, write byte 78H
  CALL I2C_SEND
  CALL I2C_STOP
  CALL PROBE_DELAY
  JMP MAIN_LOOP

; I2C_START
; CLOBBERS: flags
I2C_START:
  BSET PB_PIO,7
  BSET PB_PIO,6
  NOP
  NOP
  BCLR PB_PIO,7
  NOP
  NOP
  BCLR PB_PIO,6
  NOP
  RET

; I2C_STOP
; CLOBBERS: flags
I2C_STOP:
  BCLR PB_PIO,7
  BCLR PB_PIO,6
  NOP
  BSET PB_PIO,6
  NOP
  NOP
  BSET PB_PIO,7
  NOP
  RET

; I2C_SEND
; IN: A=byte, MSB first
; OUT: 82H bit7 contains sampled ACK level (0=ACK, nonzero=NACK)
; CLOBBERS: A, 80H, 81H, 82H, flags
; REENTRANT: no
I2C_SEND:
  MOV 80H,A
  MOV A,#08H
  MOV 81H,A
I2C_SEND_LOOP:
  BTSZ 80H,7
  JMP I2C_SEND_ONE
  BCLR PB_PIO,7
  JMP I2C_SEND_CLOCK
I2C_SEND_ONE:
  BSET PB_PIO,7
I2C_SEND_CLOCK:
  BSET PB_PIO,6
  NOP
  NOP
  BCLR PB_PIO,6
  RLR 80H
  DECSZR 81H
  JMP I2C_SEND_LOOP

  BCLR PB_POE,7          ; release SDA before ninth clock
  NOP
  BSET PB_PIO,6
  NOP
  NOP
  MOV A,PB_INS
  AND A,#80H
  MOV 82H,A
  BCLR PB_PIO,6
  BSET PB_POE,7
  BSET PB_PIO,7
  RET

; PROBE_DELAY
; CLOBBERS: A, 83H, 84H, flags
PROBE_DELAY:
  MOV A,#20H
  MOV 83H,A
PROBE_DELAY_OUTER:
  MOV A,#FFH
  MOV 84H,A
PROBE_DELAY_INNER:
  CLRWDT
  DECSZR 84H
  JMP PROBE_DELAY_INNER
  DECSZR 83H
  JMP PROBE_DELAY_OUTER
  RET

END
