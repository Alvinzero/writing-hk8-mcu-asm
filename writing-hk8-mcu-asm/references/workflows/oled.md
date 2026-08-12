# OLED/SSD1306/SH1106 专项工作流

只在当前任务涉及 OLED、SSD1306、SH1106 或 I2C 显示时读取本文件，并同时读取 `references/spec/05-GPIO-I2C-OLED驱动规范.md` 的相关章节。普通 LED 和数码管任务不得加载本文件。

OLED 控制器没有模块默认值，必须来自用户输入或用户明确选择的 `references/boards/<board_profile_id>/oled.json`。推荐 profile 前只按 `oled.json` 是否存在筛选 board 目录，不读取未选择 profile 的内容。用户明确选择后，profile 中已确认的 controller、resolution、地址、SDA/SCL、POD、上拉、时钟、方向和 offset 直接采用，不重复提问。

## 候选生成前输入

必须解析芯片、主频、MTP 容量、分辨率、I2C 地址、SDA/SCL、逐引脚 POD 选择、上拉来源、显示方向和反色设置。创建候选源码前必须确认 SDA、SCL 各自是否配置 `POD`，并确认 I2C 上拉来源；资料库或用户已确认的值不得重复询问。不得先生成候选、运行静态检查或编译后，再以 POD 或上拉缺口为由中止。

POD/上拉未知时，一次询问：

```text
1. SDA 是否设置 POD？
A. 设置 POD
B. 不设置 POD
C. 不确定/我不知道

2. SCL 是否设置 POD？
A. 不设置 POD
B. 设置 POD
C. 不确定/我不知道

3. I2C 上拉来源是什么？
A. 外部上拉电阻（推荐）
B. 芯片内部 PB_PPU
C. 外部与内部同时使用
D. 不确定/我不知道
```

两根线分别在 PinContract 中记录 `configure_drive_mode`。设置 POD 的引脚按开漏显式置位对应位；不设置时写 `configure_drive_mode: false`。上拉选择必须落实到 `PB_PPU` 初始化或外部上拉说明。不确定且 profile 无答案时列入 `unresolved_inputs`，不得生成候选。

## 总线与初始化门禁

- 只初始化已确认 PinContract 使用的 PPU/POE/PIO/POD 位；安全 PIO 必须先于 POE。
- 第 9 个时钟前释放 SDA；ACK 读 `PB_INS`，不得读输出锁存 `PB_PIO`。
- 第一条事务前执行上电稳定延时。
- 按 `BTSZ R,b` 为 bit=0 跳过下一条复核 MSB-first 分支。
- SSD1306 初始化包含 charge pump `8D/14`，按其 addressing mode 设置窗口后再进入数据模式。
- SH1106 不得照搬 SSD1306 的 `20H/21H/22H` 水平窗口路径；使用 `B0H..B7H` 页地址和每页低/高列地址。
- 可见全亮链路向 GDDRAM 写入精确 1024 个 `FFH`；不得只用 `A5H/AFH` 代替。

## 显示资产门禁

- 正式文字、Logo、头像、图片和多 page 字模使用 `DB + TABL/TABH`，请求声明 `source_encoding=db`、`orientation_profile`、`source_label` 和 `table_sender`，源码声明精确 `TABLE_PAIR`。
- 无文本总线/方向 probe 才可使用 `inline_i2c_send`，且最多 8 bytes。
- 自定义或多 page 资产先运行 `scripts/ssd1306_page_bitmap.py`；记录尺寸、字符块顺序、源格式、镜像参数、byte count 和源/输出 SHA256。
- 标准文字先用 `scripts/plan_text_line.py` 规划，再由 `scripts/bdf_to_ssd1306.py` 从已登记 BDF 字体生成。正式文字必须具有 Unicode codepoint、字体 SHA256、生成器版本和逐字 glyph SHA256。
- `new-run` 和 `close-loop` 从 ASM DB 重建并核对 manifest；最终 MAP 证明 table/sender 同一 256-word page。

## 当前已验证方向边界

只有用户明确选择 `HK64S825-4DIGIT-MIXED-PA-PB-E1` 时，才可使用 `hk64s825-4digit-mixed-pa-pb-e1-a1-c0-page-lsb-top-v1`：SSD1306、`A1H + C0H`、`ssd1306-page-lsb-top`、`mirror_x_within_glyphs=false`、`mirror_y=true`。

只有用户明确选择 `HK64S825-SH1106-1P3-I2C-PB6-PB7-E1` 时，才可使用 `hk64s825-sh1106-1p3-pb6-pb7-a1-c8-page-lsb-top-v1`：SH1106、`A1H + C8H`、`ssd1306-page-lsb-top`、`mirror_x_within_glyphs=false`、`mirror_y=false`。该板 SH1106 内部 132 列，可见 128 列从控制器列 2 开始；可见列 `x` 必须寻址到 `x+2`。

## 当前 1.3 英寸 SH1106 已验证边界

- `PB7=SDA`、`PB6=SCL`；两根线均不设置 `PB_POD`，使用内部 `PB_PPU`，且 PB6/PB7 由 OLED 独占。
- `OSC=16MHz`、`SCK_PS=34H`、实际 SCK=2MHz；7-bit 地址 `3CH`，write/read byte 为 `78H/79H`。
- 命令/数据控制字节为 `00H/40H`；I2C byte 使用 `RLR` 实现 MSB first，不得改用 `RLC`。
- 已验证初始化为 `AE D5 80 A8 3F D3 00 40 AD 8B A1 C8 DA 12 81 80 D9 22 DB 35 A4 A6`；GDDRAM 写完后发送 `AFH`。
- 每页发送 `B0H..B7H`，可见列 0 发送低列 `02H`、高列 `10H`；page byte 的 bit0 在顶部。
- 正式文字路径把 DB 表与 sender 放在 program page 0 后实板正常。page 1 版本的全黑结果只形成当前应用的保守布局和 `OPEN-SH1106-TABLE-PAGE1`，不能升级为 HK64S825 全局查表规则。
- 查表诊断 probe 应在查表前发送 `AFH`，并显示一个已验证的立即数 marker，避免把“显示未开启”和“查表失败”都误判为同一种全黑。

该结论不能跨板或跨源格式推广。5x7 单 page、16 像素多 page 和整屏方向必须分开判断；水平修正只作用于单个 glyph，垂直镜像同时交换 page 并反转 byte bit。完整细节和字体授权边界以 `references/spec/05-GPIO-I2C-OLED驱动规范.md`、`09-AI智能体生成与审查协议.md` 为准。
