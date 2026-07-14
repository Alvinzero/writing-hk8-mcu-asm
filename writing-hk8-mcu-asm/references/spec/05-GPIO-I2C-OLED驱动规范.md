# 05 GPIO、I2C 与 OLED 驱动规范

## 1. 先建立 board profile，再写一条指令

GPIO 和外设代码不能只依赖芯片型号。生成/修改前必须明确：

| 类别 | 必需输入 |
|---|---|
| 芯片 | model、revision、program capacity、clock/OPTION |
| 供电 | VDD、外设电压、电平兼容、上电顺序 |
| GPIO | port/bit、输入输出、上下拉、开漏、默认态、有效电平 |
| I2C | SDA/SCL、外部上拉、目标频率、7-bit address、clock stretching 是否需要 |
| OLED | controller、resolution、addressing mode、orientation、column/page offset |
| 验收 | 示波器/逻辑分析仪点位、可见图案、容许时序 |

缺少会影响电气安全或通信行为的字段时，AI 输出状态只能是 `draft`，不得宣称可烧录。

当前项目 E1 profile 仅适用于同一开发板：

```json
{
  "chip": "HK64S8101",
  "i2c": {
    "sda": "PB7",
    "scl": "PB6",
    "ssd1306_address_7bit": "0x3C",
    "write_byte": "0x78"
  },
  "display": {
    "controller": "SSD1306",
    "width": 128,
    "height": 64,
    "pages": 8
  }
}
```

适用规则：`HK-GPIO-001`、`HK-I2C-001..004`、`HK-OLED-001..004`。

## 2. GPIO 初始化顺序

初始化必须同时考虑 output data 和 output enable，避免上电毛刺。推荐顺序按板级电气需要明确写出：

1. 配置 pull-up/pull-down/open-drain/special selection。
2. 在输出未使能时预装安全的 `PIO` 值。
3. 最后设置 `POE` 打开输出。
4. 对总线先建立 idle state，再开始外设事务。

当前 PB6/PB7 I2C 基线：

```asm
MOV A,#0xC0
MOV PB_PPU,A           ; PB6/PB7 内部上拉；外部上拉仍按原理图确认
MOV A,#0xC0
MOV PB_PIO,A           ; SDA/SCL idle high
MOV A,#0xC0
MOV PB_POE,A           ; 输出使能
```

若同一 PB 还有其他模块，以上整寄存器写会覆盖其配置，必须改为集中式端口初始化或经验证的 read-modify-write 方案。

## 3. GPIO 驱动契约

每个 GPIO 操作函数至少声明：

```asm
; FUNCTION: SDA_RELEASE
; BOARD:    SDA=PB7
; EFFECT:   PB7 output disabled; external/device pull controls line
; CLOBBERS: flags only
; SAFETY:   caller must restore PB7 output data high before re-enable
```

不允许用“set high”同时表达两种不同电气动作：

- `BSET PB_PIO,7`：输出数据 latch 为高。
- `BCLR PB_POE,7`：释放 output driver，让总线由上拉/从机控制。

I2C ACK 阶段需要的是后者。

## 4. I2C 地址表示

文档和代码必须同时标明：

```text
7-bit slave address = 0x3C
wire write byte     = (0x3C << 1) | 0 = 0x78
wire read byte      = (0x3C << 1) | 1 = 0x79
```

禁止只写“地址 0x78”而不说明它是 8-bit wire byte；这会导致其他库按 7-bit 地址再次左移。

## 5. bit-bang I2C 电气模型

I2C 标准模型是 wired-AND/open-drain。当前项目代码通过 `POE` 在 ACK 期间释放 SDA。无论具体输出结构如何，主机不得在从机 ACK 下拉时继续主动驱动高电平。

### START

```asm
I2C_START:
  BSET PB_PIO,7        ; SDA high
  BSET PB_PIO,6        ; SCL high
  NOP
  NOP
  BCLR PB_PIO,7        ; SDA high -> low while SCL high
  NOP
  NOP
  BCLR PB_PIO,6
  RET
```

### STOP

```asm
I2C_STOP:
  BCLR PB_PIO,7
  BCLR PB_PIO,6
  NOP
  BSET PB_PIO,6
  NOP
  NOP
  BSET PB_PIO,7        ; SDA low -> high while SCL high
  RET
```

NOP 数只是当前实验代码的占位时序，不能跨 clock/OPTION 复制。交付前应测量 tLOW、tHIGH、tSU;DAT、tHD;STA、tSU;STO。

## 6. 发送 8 位与 ACK 的强制序列

当前已验证核心：

```asm
I2C_SEND:
  MOV 80H,A            ; shift byte
  MOV A,#8
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

  BCLR PB_POE,7        ; 关键：第 9 个时钟前释放 SDA
  NOP
  BSET PB_PIO,6
  NOP
  NOP
  MOV A,PB_PIO
  AND A,#80H           ; ACK=0, NACK=1
  MOV 80H,A
  BCLR PB_PIO,6
  BSET PB_POE,7        ; 恢复主机驱动
  BSET PB_PIO,7        ; idle data high
  RET
```

必须关注：

- `TABL`、shift 和 `CALL` 都可能覆盖 A；输入字节先保存。
- 第 9 个 clock 之前释放 SDA，不是采样之后才释放。
- ACK level 必须定义输出契约；不能采样后立即丢失却宣称有错误处理。
- NACK 的处理策略要明确：重试、STOP、错误计数、进入安全状态，不能无条件继续。
- 如目标器件会 clock stretching，还需释放/读取 SCL；当前模板没有证明支持该能力。

## 7. I2C 波形验收

至少捕获一条完整事务并确认：

- idle 为 SDA=1、SCL=1。
- START/STOP 边沿正确。
- 每个 byte MSB first。
- SCL high 时数据稳定，START/STOP 例外。
- 第 9 个 clock 主机 SDA driver 已释放。
- ACK 确实为 low，而不是主机自己保持 low。
- 实测频率满足目标器件和 board profile。
- 无长时间双向争用、过冲或电平不足。

“屏幕偶尔能亮”不能替代波形验证。

## 8. SSD1306 事务分层

建议把接口拆成：

1. `I2C_START/STOP/SEND`：字节总线层。
2. `OLED_CMD`：地址 `0x78` + control `0x00` + command。
3. `OLED_DATA_BEGIN/END`：地址 `0x78` + control `0x40` + burst data。
4. `OLED_SET_RANGE`：column/page window。
5. `OLED_FILL`、`OLED_WRITE_TABLE`：像素数据层。

`OLED_CMD` 需保存输入 A，因为内部 `I2C_SEND` 会破坏 A：

```asm
OLED_CMD:
  MOV 82H,A
  CALL I2C_START
  MOV A,#78H
  CALL I2C_SEND
  MOV A,#00H
  CALL I2C_SEND
  MOV A,82H
  CALL I2C_SEND
  CALL I2C_STOP
  RET
```

`82H` 必须出现在 SRAM allocation 和 clobber 契约中。

## 9. 初始化序列不是通用常量

项目已验证序列包含 `AE/D5/80/A8/3F/.../A4/A6/AF`，但以下字段可能因模组改变：

- multiplex ratio。
- COM pin configuration。
- segment remap / COM scan direction。
- charge pump。
- contrast、pre-charge、VCOMH。
- panel column offset。

迁移时以模组/controller datasheet 与实板为准，不得仅因“都是 128×64”复制全部命令。

## 10. 可见全亮必须真正写 GDDRAM

`A5H` 是 entire display ON mode，`AFH` 是 display ON；它们不能单独证明寻址、数据事务和 GDDRAM 写路径都正确。

可靠链路测试：

```text
set column range 0..127
set page range   0..7
enter data mode  0x40
send 128 * 8 = 1024 bytes of 0xFF
```

循环计数要注意 8-bit 计数器溢出。项目基线使用低字节 `00H` 配合高计数 `04H` 发送 1024 bytes；审查必须确认 exact count，而不是只看循环名字。

## 11. SSD1306 page 数据格式

128×64 基线：

```text
8 pages × 128 columns = 1024 bytes
one byte = vertical 8 pixels at one column
bit0 = top pixel within current page
bit7 = bottom pixel within current page
```

窗口发送量：

```text
bytes = (column_end - column_start + 1)
      * (page_end - page_start + 1)
```

例如 64×64 区域：64 columns × 8 pages = 512 bytes。若发送 512 bytes 但窗口是 48×2 pages，显示必然 wrap/错位。

## 12. 图片/字库转换要求

资产转换必须固定：

- 原图宽高和裁剪区域。
- threshold/dithering 方式。
- X/Y 方向、mirror/rotate。
- SSD1306 page packing。
- bit0 top。
- byte sequence 与窗口遍历顺序。
- 输出 byte count/hash。

DB 源码按上述逻辑原始 byte sequence 写入，不得根据 BIN 物理排列做 nibble/word 补偿。分页查表见 [04-程序布局-ORG-查表规范.md](04-程序布局-ORG-查表规范.md)。

## 13. OLED 验证阶梯

按最小可定位路径逐级推进：

1. **供电/复位/总线 idle**：电压和静态电平正确。
2. **强制全亮模式**：确认面板供电和基本命令链路。
3. **GDDRAM 全白**：写 1024×`FFH`，验证 data path。
4. **`00/FF` 条纹**：验证 byte 节奏和窗口 wrap。
5. **非对称 marker**：如 `12 34 56 78`，验证 byte order/TABL/TABH。
6. **小字符**：验证 page packing 和方向。
7. **目标图片**：最后导入大 DB 与分页 sender。

一旦某层失败，回到上一层，不要同时更改初始化、查表、图片转换和时序。

## 14. 常见症状

| 症状 | 优先检查 | 不应先做 |
|---|---|---|
| 无 ACK | 地址、SDA 是否释放、上拉、电压、SCL 波形 | 先重排图片 DB |
| 能全亮但不能写图 | control byte、窗口、GDDRAM data path | 认定 OLED 损坏 |
| 周期性乱码 | page window、发送 byte count、表跨页 | 根据 HxD 做补偿 |
| 字符轮廓对但细节错 | SSD1306 page/bit order、源资产转换 | 改 I2C 地址 |
| page 0 正常、后续块错 | `TABL/TABH` 同页、simulator page-0 缺陷 | 写一个跨页通用 sender |
| 偶发花屏 | NACK 处理、时序、供电、共享 SRAM | 只增加随机 NOP |

## 15. 交付清单

- [ ] board profile 完整，SDA/SCL/地址/电压/clock 已确认。
- [ ] 使用 `REG825.INC` 正式 SFR 名。
- [ ] 初始化不会覆盖其他模块的 port bits。
- [ ] 第 9 个时钟前释放 SDA。
- [ ] ACK/NACK 有明确输出和处置。
- [ ] 逻辑分析仪确认时序和无总线争用。
- [ ] OLED window 与发送 byte count 一致。
- [ ] 全亮验收真正写入 GDDRAM。
- [ ] 资产为 SSD1306 page format，bit0 top。
- [ ] DB 原始顺序、company IDE 构建、MAP 同页审计完成。
- [ ] 最终目标图经过分层实板验收。
