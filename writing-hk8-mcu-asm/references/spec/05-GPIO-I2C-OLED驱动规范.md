# 05 GPIO、I2C 与 OLED 驱动规范

## 1. 只解析当前任务需要的输入契约

先采用资料库中已经确定的 HK64S825 和当前板级规则，不得重复询问。PinContract 只在任务使用 GPIO 时要求；下表字段也只有会改变当前代码、电气安全或协议行为时才成为缺口：

| 类别 | 必需输入 |
|---|---|
| 芯片 | model；revision、clock/OPTION 仅在任务依赖时 |
| 供电 | 仅在新板、外设电平或电气安全无法由 profile 确定时 |
| GPIO | 当前 pin 的 port/bit、方向、drive、默认态、有效电平 |
| I2C | SDA/SCL、外部上拉、目标频率、7-bit address、clock stretching 是否需要 |
| OLED | controller、resolution、addressing mode、orientation、column/page offset |
| 验收 | 编译 release 使用静态/编译断言；实板点位只在用户要求硬件阶段时 |

缺少会影响电气安全或通信行为的字段时，AI 输出状态只能是 `draft`，不得宣称可烧录。

OLED 查表任务在候选生成前必须解析芯片型号、主频、MTP 容量、OLED 分辨率、I2C 地址、SDA/SCL 引脚、上拉/开漏方式、显示方向和是否反色。当前 profile、规范或用户请求已经明确的参数直接采用，不得重复询问；无法从资料库和请求确定的参数必须先按选择题确认，不得猜测。

### OLED/I2C 电气问题必须前置

即使 SDA/SCL 引脚映射已由当前 profile 确定，创建候选源码前仍必须逐引脚确认 PB7/SDA、PB6/SCL 是否设置 `POD`，并确认上拉来自外部电阻、内部 `PB_PPU` 或两者。用户已明确给出某一项时不得重复询问；未明确时使用 Skill 规定的 A/B/C/D 选择题。禁止先写源码或运行门禁，再因 `POD`、上拉来源不明而中止。

PinContract 必须把 PB7 和 PB6 拆开记录。需要 `POD` 的引脚设为开漏并显式置位对应 `PB_POD`；不需要 `POD` 的引脚写 `configure_drive_mode: false`。上拉来源必须与 `PB_PPU` 写法和板外电阻说明一致。传统 I2C 的开漏与上拉模型只能作为提问依据，不能替代当前板确认。

当前项目 E1 profile 仅适用于同一开发板，芯片型号统一为 `HK64S825`：

```json
{
  "chip": "HK64S825",
  "aliases": [],
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

当前已验证显示基线：

- SSD1306 128x64。
- 7-bit 地址 `3CH`，写地址 `78H`。
- `PB7=SDA`，`PB6=SCL`。
- 命令模式控制字节 `00H`。
- 数据模式控制字节 `40H`。
- 正常显示命令 `A6H`。
- 当前板验证方向为 `A0H + C0H`，用于修正上下左右镜像。
- 换板时必须重新确认显示方向，不得把当前板方向无条件用于其他模组。

适用规则：`HK-GPIO-001`、`HK-I2C-001..006`、`HK-OLED-001..005`。

## 2. GPIO 初始化顺序

初始化必须同时考虑 drive mode、output data 和 output enable，避免上电毛刺。推荐顺序按板级电气需要明确写出：

1. 显式建立目标 pin 的 drive mode：推挽清 `POD`，开漏置 `POD`。
2. 在输出未使能时预装安全的 `PIO` 值。
3. 最后设置 `POE` 打开输出。
4. 对总线先建立 idle state，再开始外设事务。

### 简单 LED/GPIO 最小初始化原则

简单 LED/GPIO 任务先判断当前功能真正需要的电气属性，不要从模板惯性写完整端口初始化序列。最小充分顺序为：

1. 推挽输出清目标 `POD` 位；开漏输出置目标 `POD` 位。
2. 用 `PIO` 预装安全输出电平，避免打开输出瞬间毛刺。
3. 用 `POE` 打开目标 bit 的输出使能。
4. 用 read-modify-write 保留同一端口上不属于本任务的 bit。

`POD` 是每个输出 pin 的 drive mode，不可省略；`PPU/PPD/INS/IOS/PSL` 只有 PinContract 明确要求时才写。不得为了“初始化完整”而清写整套寄存器，这会让简单 LED 代码变重，也可能覆盖其他模块的端口配置。

### 长延时与 WDT

WDT 未明确关闭时，任何可见延时、长忙等或周期循环都必须喂狗。`CLRWDT` 必须放在忙等循环内部或足够短的循环层级内，使最坏情况下的两次喂狗间隔小于当前 WDT 超时；不得只在初始化阶段执行一次。

若确认 WDT 已关闭，文件头必须写明 OPTION/WDT 依据。若 WDT 状态未知，按开启处理。

### PB6/PB7 SSD1306 安全基线

当前 PB6/PB7 I2C/OLED 的引脚和地址采用已验证基线；`POD` 与上拉来源按任务开始阶段的用户确认生成。未要求 `POD` 的引脚只建立所确认的上拉、`PB_PIO` idle high 和 `PB_POE` 输出使能。

当当前板级依据或用户明确确认 PB6/PB7 不配置 `PB_POD` 时，结构化 PinContract 必须写入 `configure_drive_mode: false`；静态检查仍须验证 `PIO` 安全值先于 `POE`、仅修改 PB6/PB7，并继续检查 ACK 释放。该字段只适用于已有明确板级依据的外设基线，不得用于绕过普通 GPIO 的模式初始化。

```asm
MOV A,#0C0H
MOV PB_PPU,A           ; PB6/PB7 内部上拉；外部上拉仍按原理图确认
MOV PB_POE,A           ; 输出使能
MOV PB_PIO,A           ; SDA/SCL idle high
```

不得从“端口初始化完整”的惯性出发，无证批量写 `PB_POD/PB_INS/PB_PPD/PB_PSL`。这些寄存器只有在 board profile、E1 实板证据、原理图或用户明确要求证明当前板需要时才加入；加入时还必须说明它改变的电气含义，并重新验证 ACK、idle、START/STOP。

特别注意：`PB_INS` 在本基线中是 ACK 真实输入采样来源，不是必须写入的初始化模板项。若同一 PB 还有其他模块，整寄存器写会覆盖其配置，必须改为集中式端口初始化或经验证的 read-modify-write 方案。

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

NOP 数只是当前实验代码的占位时序，不能跨 clock/OPTION 复制。普通编译 release 必须完成 clock/cycle audit；只有用户明确要求后续硬件验收时，才测量 tLOW、tHIGH、tSU;DAT、tHD;STA、tSU;STO。

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
  MOV A,PB_INS
  AND A,#80H           ; ACK=0, NACK=1
  MOV 80H,A
  BCLR PB_PIO,6
  BSET PB_POE,7        ; 恢复主机驱动
  BSET PB_PIO,7        ; idle data high
  RET
```

必须关注：

- `TABL`、shift 和 `CALL` 都可能覆盖 A；输入字节先保存。
- `BTSZ R,b` 是 bit=0 跳过下一条；上面布局表示 bit7=1 时执行 `JMP I2C_SEND_ONE` 并 `BSET PB_PIO,7`，bit7=0 时跳过 `JMP` 并执行 `BCLR PB_PIO,7`。反写会把地址和数据按位取反，`78H` 就不会真实出现在总线上。
- 第 9 个 clock 之前释放 SDA，不是采样之后才释放。
- ACK 采样必须读 `PB_INS`，不得读 `PB_PIO`；PB_PIO 可能是输出锁存，不保证代表真实引脚电平，容易把 ACK 误判成 NACK。
- 亮屏最小路径可以采样保存 ACK 但不立即停机；这只能声明“采样记录”，不能宣称完整 NACK 处理。
- 若实现 NACK 错误路径，必须先保证 ACK 读法来自真实输入且不会 false NACK，再选择重试、STOP、错误计数或进入安全状态。
- 如目标器件会 clock stretching，还需释放/读取 SCL；当前模板没有证明支持该能力。

## 7. I2C 波形验收（仅后续硬件阶段）

仅在用户明确要求烧录和硬件验收时，至少捕获一条完整事务并确认：

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

也就是 bit0 是该 page 顶部像素，bit7 是该 page 底部像素。在 `A6H` 正常显示模式下，通常 bit=1 为亮点、bit=0 为黑点。禁止把字模按普通横向行扫描直接发送；ASCII、16x16 汉字、Logo、头像和其他位图都必须先转换成 page 格式。

窗口发送量：

```text
bytes = (column_end - column_start + 1)
      * (page_end - page_start + 1)
```

例如 64×64 区域：64 columns × 8 pages = 512 bytes。若发送 512 bytes 但窗口是 48×2 pages，显示必然 wrap/错位。

### 多字符、汉字和图片块的统一发送顺序

设置窗口后使用水平寻址模式，遍历顺序必须是：

```text
for page in pages:
  for glyph_or_image_block in row:
    for col in width:
      send one page-format byte
```

例如显示两个 16x16 汉字，窗口宽 32 列、高 2 页，必须：

1. 先发送 page0 的第 1 个字 16 列。
2. 再发送 page0 的第 2 个字 16 列。
3. 再发送 page1 的第 1 个字 16 列。
4. 再发送 page1 的第 2 个字 16 列。

禁止先发送第 1 个汉字的两个 page，再发送第 2 个汉字的两个 page；该顺序与 SSD1306 水平寻址窗口的列/page 自动递增不一致，会造成字形错位或重排。

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

## 13. OLED 验证阶梯（仅后续硬件阶段）

仅在用户明确要求烧录和实板验收时，按最小可定位路径逐级推进：

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

### 普通编译 release（必需）

- [ ] board profile 完整，SDA/SCL/地址/电压/clock 已确认。
- [ ] 使用 `REG825.INC` 正式 SFR 名。
- [ ] 初始化不会覆盖其他模块的 port bits。
- [ ] OLED 亮屏默认采用已验证最小初始化：`PB_PPU`、`PB_POE`、`PB_PIO`；额外 `PB_POD/PB_INS/PB_PPD/PB_PSL` 有明确证据。
- [ ] 第 9 个时钟前释放 SDA。
- [ ] ACK 采样读 `PB_INS`；若实现 NACK 错误路径，已排除 false NACK。
- [ ] 第一条 OLED 命令或数据事务前有上电稳定延时。
- [ ] I2C bit 发送的 `BTSZ` 分支方向已按 bit=0 跳过下一条复核。
- [ ] OLED window 与发送 byte count 一致。
- [ ] 全屏 1024 字节填充循环已确认低字节 `00H` 配合高计数 `04H` 或等价 1024 次结构。
- [ ] 全亮路径会真正写入 GDDRAM，而不是只发送 `A5H/AFH`。
- [ ] 资产为 SSD1306 page format，bit0 top。
- [ ] 多字符/汉字/图片块按 page → 字块/图片块 → 列发送；两个 16x16 汉字的 64 字节顺序为 page0 字1、page0 字2、page1 字1、page1 字2。
- [ ] DB 原始顺序、`builtin_compiler` 构建、MAP 同页审计完成。
- [ ] 静态检查和目标编译 0 error / 0 warning，`release` 返回 `RELEASED`。

### 后续硬件验收（仅用户明确要求时）

- [ ] ASM 已烧录到目标板，镜像 hash 与 release 证据一致。
- [ ] 逻辑分析仪确认时序和无总线争用。
- [ ] 实板全亮验收真正写入 GDDRAM。
- [ ] 最终目标图经过分层实板验收。
