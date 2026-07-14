; ========================================================
; TEMPLATE: minimal-main.asm
; STATUS: draft until chip/vector/OPTION/hardware acceptance are confirmed
; CHIP: HK64S8101 baseline
; TOOLCHAIN: company_ide or python_source_module_cli (no DB)
; RULES: HK-GOV-002, HK-GOV-003, HK-LAYOUT-001, HK-LAYOUT-002
;
; SRAM ALLOCATION: none
; ========================================================

ORG 0x0000
  JMP INIT

ORG 0x0008
  RETI

ORG 0x0010
INIT:
  ; Insert only board-profile-approved initialization here.

MAIN_LOOP:
  CLRWDT
  JMP MAIN_LOOP

END
