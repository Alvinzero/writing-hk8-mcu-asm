; ========================================================
; TEMPLATE: ssd1306-table-paged.asm
; STATUS: draft; the CONSUME_BYTE stub must be replaced and hardware-tested
; CHIP: HK64S825 baseline
; TOOLCHAIN: company_ide ONLY because this source contains DB
; DATA RULE: raw consumer byte order; TABL then TABH; reload A before TABH
; RULES: HK-TOOLCHAIN-DB-001, HK-TABLE-003, HK-TABLE-004,
;        HK-TABLE-005, HK-TABLE-006, HK-TABLE-007, HK-BUILD-006
;
; SRAM ALLOCATION
; 88H TABLE_INDEX scratch owner=SEND_TABLE0/SEND_TABLE1 lifetime=one call
; 89H TABLE_WORDS scratch owner=SEND_TABLE0/SEND_TABLE1 lifetime=one call
;
; TABLE_PAIR: TABLE0,SEND_TABLE0
; TABLE_PAIR: TABLE1,SEND_TABLE1
; ========================================================

ORG 0x0000
  JMP INIT

ORG 0x0008
  RETI

; Page 0: table and sender are both in 0x00xx.
ORG 0x0020
TABLE0:
  DB 12H,34H,56H,78H,9AH,BCH,DEH,F0H
  DB 01H,23H,45H,67H,89H,ABH,CDH,EFH

ORG 0x0040
; SEND_TABLE0
; OUT: 16 source bytes delivered in DB order
; CLOBBERS: A, 88H, 89H, CONSUME_BYTE clobbers
SEND_TABLE0:
  MOV A,#20H
  MOV 88H,A
  MOV A,#08H            ; 8 words = 16 source bytes
  MOV 89H,A
SEND_TABLE0_LOOP:
  MOV A,88H
  TABL
  CALL CONSUME_BYTE
  MOV A,88H             ; mandatory reload after TABL
  TABH
  CALL CONSUME_BYTE
  INCR 88H
  DECSZR 89H
  JMP SEND_TABLE0_LOOP
  RET

; Page 1: independent table and same-page sender.
ORG 0x0100
TABLE1:
  DB 80H,01H,40H,02H,20H,04H,10H,08H
  DB 08H,10H,04H,20H,02H,40H,01H,80H

ORG 0x0120
; SEND_TABLE1
; OUT: 16 source bytes delivered in DB order
; CLOBBERS: A, 88H, 89H, CONSUME_BYTE clobbers
SEND_TABLE1:
  MOV A,#00H
  MOV 88H,A
  MOV A,#08H
  MOV 89H,A
SEND_TABLE1_LOOP:
  MOV A,88H
  TABL
  CALL CONSUME_BYTE
  MOV A,88H
  TABH
  CALL CONSUME_BYTE
  INCR 88H
  DECSZR 89H
  JMP SEND_TABLE1_LOOP
  RET

ORG 0x0200
INIT:
  CALL SEND_TABLE0
  CALL SEND_TABLE1
MAIN_HOLD:
  CLRWDT
  JMP MAIN_HOLD

; CONSUME_BYTE
; IN: A=one table byte
; OUT: implementation-specific
; CLOBBERS: implementation-specific
; This draft stub discards A. Replace it with an audited OLED/I2C consumer.
CONSUME_BYTE:
  RET

END
