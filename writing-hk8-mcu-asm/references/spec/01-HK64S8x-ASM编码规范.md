# 01 HK64S8x ASM 编码规范

## 1. 文件结构

正式 ASM 推荐固定顺序：

1. 文件头：功能、芯片型号、板级 profile、工具链、证据状态。
2. 规则声明：关键 rule IDs、OPEN 项。
3. SFR：直接使用 `REG825.INC` 官方名，不重复定义。
4. 常量 `EQU`。
5. SRAM allocation table。
6. 复位/中断向量。
7. 受控 ORG 数据页和同页 sender。
8. 主程序。
9. 驱动/业务子程序。
10. 延时等底层子程序。
11. `END`。

文件头示例：

    ; CHIP: HK64S8101
    ; TOOLCHAIN: company_ide; hardware acceptance required
    ; BOARD: board_oled_revA
    ; EVIDENCE: based on verified OLED DB raw-order baseline
    ; RULES: HK-TOOLCHAIN-DB-001, HK-TABLE-003..008
    ; OPEN: none blocking flash
    ;
    ; SRAM:
    ; 80H I2C_SHIFT   scratch   owner=I2C_SEND
    ; 81H I2C_COUNT   scratch   owner=I2C_SEND
    ; 88H TABLE_INDEX scratch   owner=SEND_CHUNKx
    ; 89H TABLE_WORDS scratch   owner=SEND_CHUNKx

## 2. 命名

- 标签和 `EQU` 推荐大写蛇形：`OLED_INIT`、`SEND_CHUNK0_LOOP`。
- 标签表达职责，不使用 `L1`、`TEMP2` 等无语义名字。
- 循环/退出标签带父级前缀，避免全局符号冲突。
- 表和同页函数命名成对：`AVATAR_DATA0` / `SEND_AVATAR_CHUNK0`。
- SRAM 直接地址允许用于短小、已列分配表的 scratch；若编译器跨工具链别名行为未验证，不要为了“可读性”强行 EQU SRAM。

## 3. 数值与操作数

允许：

    MOV A,#0x80
    MOV A,#80H
    MOV A,#128        ; 十进制 128
    MOV 80H,A
    ORG 0x0100
    COUNT EQU 128

禁止：

    MOV A,#0x80H      ; 0x 与 H 淭搭
    JMP 03FFH         ; 直接数字目标兼容性差

建议：

- 位掩码、寄存器地址、协议字节使用十六进制。
- 人类数量、尺寸可用十进制并配名称。
- 每个立即数必须满足 k8 0..255；bit 0..7；k10 0..1023。
- `JMP/CALL` 使用代码标签；固定目标用 `TARGET EQU 03FFH` 后引用符号。

## 4. 指令拼写

使用修正形式：

    XOR A,#0xFF
    BTSZ 80H,3
    BTSNZ 80H,3
    MOV A,#0x80

2026-07 元数据已修复 `BTSZ R,b/BTSNZ R,b`，但仍保留 `XOR A.#K` 和 `MOV A,#K` operand type 错误。不得假定原始表已全部干净；完整参考见 `rules/instruction-reference.json`。

## 5. A、标志和 skip 指令

A 是隐式共享资源。任何子程序默认可能破坏 A，除非契约明确保证。

连续比较必须重载：

    MOV A,90H
    SE #1
    JMP HANDLE_1

    MOV A,90H
    SE #2
    JMP HANDLE_2

不要假设第一次 `SE` 后 A 或 flags 保持可用于下一次比较。所有 skip 类指令还要人工检查“跳过下一条”的布局，禁止在其后放会因后续插入而改变含义的隐式多指令宏。

## 6. 子程序契约

每个可复用子程序入口前声明：

    ; I2C_SEND
    ; IN:       A = byte
    ; OUT:      none
    ; CLOBBERS: A, 80H, 81H
    ; FLAGS:    not preserved
    ; REENTRANT:no
    ; TIMING:   depends on CPU clock and NOP count
    ; ERROR:    ACK sampled into 80H bit7 (if caller needs it)

规则：

- 调用方只能依赖契约中声明的输出。
- scratch 所有权不能跨可嵌套调用冲突。
- 中断可调用的子程序必须专门设计保存现场；默认函数不可重入。
- `CALL` 深度、硬件栈容量若没有正式资料，必须作为 OPEN 并用最小调用深度设计。

## 7. 复位与中断

当前 HK64S8101 项目基线：

    ORG 0x0000
      JMP INIT

    ORG 0x0008
      RETI

这不是整个家族的无条件常量。`hkproj` 的 `mcu_type` 改变时必须重新核对向量。

中断入口应：

- 尽量短。
- 明确保存/恢复 A、状态和共享 SRAM。
- 不调用未证明可重入的 I2C/OLED/扫描函数。
- 清中断标志的时机按寄存器资料和实板测试确定。

## 8. ORG 和连续布局

- 不必要的 `ORG` 会产生零填充空洞并扩大 BIN。
- 每个 `ORG` 注释目的、起止和容量。
- 任何地址被写入两次都是 BLOCKER，即使编译器只 warning 并让后值覆盖前值。
- 容量按最高写入 word 地址计算，不只按指令数量。
- DB 每两个源字节占一个程序 word；当前规范要求每条 DB 显式偶数字节。

详细规则见 [04-程序布局-ORG-查表规范.md](04-程序布局-ORG-查表规范.md)。

## 9. 注释

注释写“为什么”和硬件契约，不重复显而易见的机器动作。

好：

    BCLR PB_POE,7      ; 释放 SDA，避免与 OLED ACK 下拉争用

差：

    BCLR PB_POE,7      ; 清 bit7

重要魔数必须解释协议含义、单位和来源：

    MOV A,#0x78        ; SSD1306 7-bit 0x3C 的 write byte
    MOV A,#0x70        ; 112 table words = 224 source bytes

## 10. 禁用或受限特性

- `RET A,#K`：编译器可生成 `0xA1KK`，硬件语义未确认，量产禁用。
- `CPL/CPLR`：文档语义有疑点，关键逻辑禁用直至实板真值表确认。
- `IDLE/STOP`：必须验证唤醒、WDT、GPIO 和外设状态。
- simulator 跨页 `TABL/TABH`：不可作为验收证据。

## 11. 代码评审最小项

- [ ] 文件头有 chip、board、toolchain、rules、SRAM 表。
- [ ] 无 `#0x..H`、数字 JMP/CALL、内部 SFR 名。
- [ ] 所有子程序有 clobber 契约。
- [ ] skip 指令下一条语义明确。
- [ ] ORG 区间无重叠、无无意义大空洞。
- [ ] warnings 为 0。
- [ ] 有 DB 时公司 IDE 构建且按查表规范验收。
