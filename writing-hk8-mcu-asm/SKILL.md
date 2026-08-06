---
name: writing-hk8-mcu-asm
description: 用于生成、修改、审查或编译公司 HK64S825 8 位 MCU 的 ASM，适用于芯片专属汇编、LED/OLED/数码管功能、静态检查、内置编译模块编译通过后输出 ASM 或失败关闭交付时。
---

# HK64S825 ASM 编译闭环 Skill

本 Skill 面向公司唯一 8 位 MCU `HK64S825`。默认使用 Skill 内置 HK64S825 编译模块完成静态检查和目标编译，通过后即可 release；烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件。失败时只返回诊断，不展示候选 ASM。

## 第一条回复

先从用户请求中解析目标芯片型号：

- 若用户请求已经明确包含 `HK64S825`，例如“已确认 HK64S825”“HK64S825 ASM 闭环”“HK64S825 OLED”或等价表述，视为型号已确认；不得再要求用户回复“是/否”或重复确认型号，直接进入需求解析和缺口检查。芯片已确认不等于开发板已确认；存在板级缺口时必须先询问，不得生成候选源码。
- 若用户请求明确写出其他芯片型号，立即停止并说明暂不支持，不得猜测架构、寄存器或指令集。
- 若用户请求没有提供目标型号，第一条回复只询问并确认芯片型号，不得输出 ASM：

```text
请先确认目标芯片型号是否为 HK64S825？
```

如果用户确认的型号不是 `HK64S825`，立即停止并说明暂不支持。选择、确认或解析为 `HK64S825` 后，使用 `references/spec/` 中的芯片规则、指令集、寄存器、内存、程序布局、LED、OLED 和数码管规范来设计 ASM，不得追问与当前功能无关的输入。

## 必需输入

创建候选源码前，先区分“资料库已知规则”和“用户任务缺口”，并把输入分成三类：

- 芯片级事实：HK64S825 指令、SFR、程序空间、RAM、复位寄存器语义和编译器能力；默认从 `references/spec/` 使用，资料库已经明确的参数不得重复追问用户。
- 板级事实：GPIO 映射、外设型号/地址、共阳共阴、有效电平、外部反相、物理位序、OSC 来源、共享 GPIO 和限流/驱动方式；不得默认采用任何开发板，必须由用户直接提供或由用户明确选择已注册 board profile。
- 功能事实：显示内容、闪烁频率、计数范围、图片/字模、坐标和刷新要求；可从当前请求可靠解析，无法从 spec 推断时作为用户任务缺口。

创建候选源码前只必须确认或从请求中可靠解析：

- 目标芯片为 `HK64S825`；
- 本次要实现的具体功能，例如 LED、OLED、数码管或组合功能；
- 当前任务中无法从 spec 推断、且会影响代码行为的功能参数，例如显示内容、闪烁频率、计数范围、图片/字模数据、坐标或刷新要求。

开发板只在以下任一条件成立时视为已确认：用户明确给出全部当前任务所需板级参数；或用户明确说出并选择一个已注册 `board_profile_id`。请求没有提开发板、只说“默认”、只确认芯片型号，均不构成板级确认。不得从旧代码、示例、模板、最近一次任务或 spec 中的已注册板参数补齐缺口。

在候选源码生成之前，把确认来源写入 `request.input_provenance`：`board`、GPIO 任务的 `pins`、时序任务的 `clock` 只能取 `user_provided` 或 `user_confirmed_profile`。这些值是用户确认记录，智能体不得代替用户填写。缺少 board 来源时列入 `unresolved_inputs` 并以 `BOARD_PROFILE_UNCONFIRMED` 停止；缺少 pins/clock 来源时以 `BOARD_INPUT_UNCONFIRMED` 停止。不得创建候选 ASM、`new-run` 或编译后再补来源。

已注册 board profile 只在用户明确选择对应 ID 后读取。用户选择自定义开发板时，只收集当前功能需要的最小板级参数，不要求无关硬件信息。

数码管板级信息未明确时，第一批最多询问三题：

```text
1. 数码管通过什么方式驱动？
A. HK64S825 GPIO 直接动态扫描
B. 使用驱动芯片或移位寄存器
C. 不确定/我不知道

2. 你能提供哪种板级资料？
A. 明确的 board profile ID
B. 原理图或段线/位选引脚表
C. 不确定/我不知道

3. 位数和极性是否已知？
A. 已知位数，且知道各位共阳/共阴与有效电平
B. 只知道位数
C. 不确定/我不知道
```

GPIO 直驱时还必须确认 A-G/DP 逐段引脚、从左到右的逐位 COM 引脚、每位有效电平、外部三极管/MOS 是否反相、OSC/SCK_PS、共享 GPIO 和限流/驱动方式。任一项未知时不得生成正式显示 ASM；用户明确要求硬件探测时，可另建逐段逐位 probe，探测结论经用户确认后再建立 board profile。

OLED/I2C 的 `POD` 与上拉是附加硬门禁：board/SDA/SCL 确认后、创建候选源码前必须确认 SDA、SCL 各自是否配置 `POD`，并且候选源码前必须确认 I2C 上拉来源。只有用户已在当前请求中逐引脚明确说明，才可跳过对应问题；不得从“传统 I2C”、旧代码或默认模板猜测。不得先生成候选、运行静态检查或编译后，再以 POD 或上拉缺口为由中止。

OLED 查表显示还必须在候选生成前解析芯片型号、主频、MTP 容量、分辨率、I2C 地址、SDA/SCL、上拉/开漏方式、显示方向和是否反色。当前已验证板级参数或用户已明确给出的值直接采用，不得重复询问；资料库和请求都没有的参数才作为缺口，按一次最多三题的选择题规则分批确认。

未明确时依次询问以下 A/B/C/D 选择题，一次最多三题：

```text
1. 已确认的 SDA 引脚是否设置 POD？
A. 设置 POD
B. 不设置 POD
C. 不确定/我不知道

2. 已确认的 SCL 引脚是否设置 POD？
A. 不设置 POD
B. 设置 POD
C. 不确定/我不知道

3. I2C 上拉来源是什么？
A. 外部上拉电阻（推荐）
B. 芯片内部 PB_PPU
C. 外部上拉与内部 PB_PPU 同时使用
D. 不确定/我不知道
```

两根线都要在 PinContract 中分别记录 `configure_drive_mode`；选择设置 POD 的引脚按开漏显式置位对应 `PB_POD`，选择不设置的引脚写 `configure_drive_mode: false`。上拉选项必须落实到 `PB_PPU` 初始化或外部上拉说明中。若用户选择“不确定/我不知道”且当前已验证 board profile 也没有明确答案，将其列入 `unresolved_inputs`，停在候选生成之前。

缺口问题必须以 A/B/C/D 选择题呈现，用户只需要回复选项字母。一次最多提出 3 个选择题；每题 2 到 4 个选项，默认或推荐选项必须标注“推荐”，并且必须包含“不确定/我不知道”选项。不得要求用户自由填写一长串板级参数；若确实需要非选项数据，例如显示文本、图片字模或真实文件路径，先说明原因，再只收集当前任务必需的最小数据。

编译器默认来自资料包内置配置：`scripts/builtin_compiler.py`，批准版本为 `builtin-hk64s825-assembler-2`。默认不需要用户提供本机 IDE、外部 ASMC 或 HK_ASM_Compiler 路径。禁止扫盘、遍历本机目录或猜测 IDE/CLI 路径；不得使用 Get-ChildItem、os.walk、rglob、where 或全盘搜索寻找编译器。

烧录、回读和硬件验证所需的硬件环境信息，只在用户明确要求执行对应后续验证阶段时询问。不得在普通代码生成阶段或编译 release 阶段提前追问无关硬件细节。缺少的信息若不影响当前阶段，可写入 `open_items`；只有缺口会影响安全、电气争用、地址/内存布局或编译正确性时，才列入 `unresolved_inputs` 并停止。

## 规则读取策略

只读取当前任务相关规则，不得加载无关 OLED、数码管或 analysis 快照资料。不得把大型规则 JSON 整份载入上下文；使用文本搜索或结构化解析，只检索候选源码实际使用的 mnemonic、SFR、rule ID 和当前功能章节。

- 所有任务：读取 `references/spec/AGENTS.md` 和 `09-AI智能体生成与审查协议.md` 的相关段落；从 `asm-rules.json`、`instruction-reference.json`、`register-reference.json`、`register-alias-policy.json` 定向查询实际使用项。
- LED/GPIO：再读取 `05-GPIO-I2C-OLED驱动规范.md` 中 GPIO/LED 相关段落和必要 checklist。
- OLED：再读取 `05-GPIO-I2C-OLED驱动规范.md` 中 I2C/OLED 相关段落。
- OLED 字形出现“窗口尺寸正确但字形不完整、像多个字符拼接”时，再读取 `08-踩坑案例与症状诊断手册.md` 的 OLED 查表索引案例。
- 数码管：先读取通用 `06-数码管动态扫描规范.md`；只有用户明确选择已注册 profile 后，才读取 `references/boards/<board_profile_id>/seven-segment.md`，不得枚举或试读其他板级目录来替用户选板。
- 构建/编译：读取 `07-构建-烧录-验收规范.md` 中编译相关段落、profile/config 和 adapter 配置。

禁止复制 templates、example 或 sample ASM 作为候选源码。示例文件只作反例或格式参考，不进入生成上下文；不得把示例改名、删注释、局部替换后当成新代码。不得搜索与用户显示内容相同的现成答案。必须根据已确认的当前需求、板级契约、芯片规则、寄存器和时序重新撰写候选 ASM。

## 实板验证显示驱动保持规则

用户提供并确认正常显示的驱动程序属于该板的 E1 证据。修改其倒计时、显示内容或其他业务层时：

- 保持已验证的 Timer0 驱动模式、时基参数、IW1F 清除位置、查表顺序与位选顺序。
- 不得把已验证的轮询驱动擅自改成中断驱动，也不得把中断驱动擅自改成轮询；只有逐项实板 probe 已确认向量、总中断、flag 和时基后才能替换。
- 不得同时替换计时器模式、段码变换、段口写法与位选写法；每次只改变一个未经验证的变量，并保留实板对比路径。
- 数码管倒计时在完整四位扫描后才更新业务状态，避免同一帧混用两个时刻的数字。

对当前 HK64S825 四位精确动态扫描任务，用户确认正常的 E1 版本还证明了运行时结构约束：

- 请求同时声明 `seven_segment` 和 `timing.precision=precise` 时，必须显式执行 `MOV SCK_PS,A`，并在程序字地址 `008H` 保留 `RETI` 中断入口。
- SRAM 读或读改写指令之后，下一次 SRAM 访问前必须有 `NOP` 或其他明确的非 SRAM 指令；纯写入 SRAM 后不强制额外间隔。skip 指令跳过下一条后若会直接落到 SRAM 访问，必须在被跳过的位置放 `NOP`。
- 正常版仍可保留多层 `CALL`。不能把“减少 CALL 深度”当作默认修复方向；先检查上述 SRAM 间隔、skip 路径、`SCK_PS` 和中断入口。
- 这组约束属于当前 E1 运行时基线，不是普通 GPIO 或硬件 probe 的通用模板；静态检查器只在精确数码管请求中启用。

## 端口独占整字节写规则

当 PinContract 明确某端口全部相关 bit 都由当前数码管模块独占时，优先使用整字节写入，不得把固定字节值无意义地展开为八条 BSET/BCLR。例如所有段线关闭为低电平时：

    MOV A,#00H
    MOV PB_PIO,A

00H 与 0FFH 分别表示整口低电平和高电平；在写入前必须由 board contract 判断哪一个是安全段线状态。动态段码已经在 A 中且整个 PB7..PB0 都由段线独占时，也优先使用 MOV PB_PIO,A。端口存在共享 bit、未确认独占权或写段码时仍有位选打开时，改用所有权保留的 RMW 或逐 bit 写法。

## LED/GPIO 通用硬门禁

简单 LED/GPIO 不得套用端口全量初始化模板。最小初始化是最少但足以建立确定电气状态的操作：只配置当前 PinContract 真正需要的寄存器，但每个输出 pin 的电气模式必须显式建立。推挽输出必须显式清除目标 `POD` 位，开漏输出必须显式置位目标 `POD` 位；先预装安全 `PIO`，最后开启 `POE`。不得依赖 `POD` 复位值代替正式初始化。

不得批量清写无关 `PPU/PPD/INS/IOS/PSL`。只有 PinContract 或当前功能明确要求上拉、下拉、输入通道或特殊功能选择时，才写对应寄存器；共享端口必须使用保留非本任务 bit 的 read-modify-write 或集中式端口初始化。

所有循环计数指令都必须先核对 `instruction-reference.json.raw_notes` 的写回目标。`DECSZ/INCSZ` 的结果写入 A，不能作为原位更新的 SRAM 计数器；不得把 `DECSZ` 当作写回计数寄存器的倒计数指令，需要写回时使用规则允许的 `DECSZR/INCSZR`。含 `CLRWDT` 的循环仍必须证明会进展并退出。

精确延时必须从 OSC、SCK_PS 和实际 SCK 推导，并通过 cycle audit；只写“16 MHz”不构成延时依据。HK64S825 默认 `SCK_PS=34H` 时，16 MHz OSC 派生的实际 SCK 为 2 MHz。未使用的业务 `EQU` 必须删除或真正引用，不能定义后继续在代码中散落同值魔数。

WDT 未明确关闭时，任何可见延时、长忙等或周期循环必须插入 `CLRWDT`。`CLRWDT` 要放在忙等循环内部或足够短的循环层级内，不能只在初始化或主循环入口偶尔执行；如果确认 WDT 已关闭，必须在文件头写明 OPTION/WDT 依据。

## OLED 任务硬门禁

生成 OLED/SSD1306 ASM 时，读取本 Skill 的 `05-GPIO-I2C-OLED驱动规范.md`。项目经验、旧示例和模板冲突时，以当前 `HK64S825` 目标、已编译证据和实板验证结论为准；带其他旧芯片型号的文件只能作为反例或历史线索，不得作为候选源码模板。

至少保证：

- 目标芯片为 `HK64S825`，不得出现旧芯片型号标注。
- 在创建候选源码前完成用户已确认 SDA/SCL 的逐引脚 `POD` 选择和 I2C 上拉来源确认；缺少任一项不得开始静态检查或编译。
- OLED 亮屏只初始化已确认 PinContract 所在端口的目标 PPU/POE/PIO 位，建立上拉、输出使能和 SDA/SCL idle high。不得为了“完整初始化”无证批量写目标端口的 POD/INS/PPD/PSL；只有用户确认的 board profile、E1 证据或用户明确要求证明需要时才加。
- 用户或已确认板级依据明确某个 SDA/SCL 引脚不配置 POD 时，结构化 PinContract 写 `configure_drive_mode: false`，但仍必须通过 `PIO` 先于 `POE`、位所有权和 ACK 释放检查；不得把该例外用于普通 GPIO。
- I2C 第 9 个时钟前释放 SDA；ACK 采样必须读 `PB_INS`，不得读 `PB_PIO`，因为 PB_PIO 可能是输出锁存而不是真实引脚电平。亮屏最小路径可以采样记录 ACK 但不直接停机；若实现 NACK 错误路径，必须确认读法真实且不会 false NACK 后再 STOP/重试/进安全状态。
- OLED 上电后必须先执行上电稳定延时，例如 `DELAY_100MS`，再发送 `0xAE`、初始化命令或数据事务。
- I2C 发送 bit 前必须复核 `BTSZ` 语义：`BTSZ R,b` 是 bit=0 跳过下一条；MSB-first 发送 bit7 的已验证布局是 `BTSZ 80H,7` 后 bit7=1 跳到 `BSET PB_PIO,7`，bit7=0 走 `BCLR PB_PIO,7`，不得把 0/1 分支反写。
- I2C 时序不得靠随机增删 `NOP` 猜测修复；普通编译 release 给出 clock/cycle 依据，硬件阶段再测 SCL/timing。
- SSD1306 初始化必须包含 charge pump `8D/14`，并设置 column/page range 后进入 `0x40` 数据模式。
- 只有用户明确选择 `HK64S825-DEFAULT` 后，才可采用其 SSD1306 128x64、地址、SDA/SCL、控制字节和 `A1H + C0H` 等注册板基线；其他 board 必须从用户资料或新 probe 建立自己的参数。“资产原始列顺序”只对已经记录来源和实板结果的同一种字模格式成立，不能跨字库生成器套用。
- 当前板 5x7 ASCII 数字/斜杠已用 `2026/7/24` 实板验证：字符按文本顺序发送，每个字符保持标准 5 列加 1 空列的原始列顺序；标准 5x7 列字节必须先做 bit 顺序反转，再作为 SSD1306 page byte 发送。该结论只覆盖单 page 的标准 5x7 常量，禁止直接推广到 8x16、16x16、汉字、Logo 或其他多 page 资产。
- 自定义、多 page 或混合字符宽度的字模在创建候选源码前，必须建立资产清单并运行 `python scripts/ssd1306_page_bitmap.py <asset-manifest.json>`。清单必须固定宽高、按文本顺序排列的字符块及各自宽度、源格式 `ssd1306-page-lsb-top`、逐字符水平变换、垂直变换、源/输出 byte count 和 SHA256；转换器输出的点阵预览必须保持文本块顺序。只有用户明确选择 `HK64S825-DEFAULT` 时，正式资产才声明 `orientation_profile: "hk64s825-default-a1-c0-page-lsb-top-v1"`。
- 水平和垂直修正必须按像素坐标定义：`mirror_x_within_glyphs` 只反转每个字符块内部的列，不得反转整行；多 page 的 `mirror_y` 必须反转全部像素行，等价于交换 page 顺序并反转每个 byte 的 bit 顺序，不能只交换 page 或只做 bit 反转。
- 汉字、ASCII 字母、Logo、头像、图片及多 page 字模的正式显示数据默认且强制使用 `DB + TABL/TABH` 查表，不得展开成连续 `MOV A,#xxH / CALL I2C_SEND`。请求中的 `display.asset` 必须写 `source_encoding: "db"`、匹配当前 board 的 `orientation_profile`、DB 的 `source_label` 和查表函数 `table_sender`；源码必须有精确的 `; 查表配对 TABLE_PAIR: TABLE,SENDER`。`inline_i2c_send` 仅允许用户明确要求的无文本总线/方向探针，须声明 `role: "probe"` 且最多 8 bytes，不能作为正式文字或图片 release。
- 上述显示资产还必须提供相对 manifest 路径、byte count 和源/输出 SHA256。`new-run` 必须校验方向 profile 与请求 board 匹配，并校验 manifest 的 `source.format` 和两个镜像参数与 profile 完全一致；再从指定 DB 重新提取实际字节并核对转换输出，检查 sender 含 `TABL -> 重载索引 -> TABH`。`close-loop` 必须重复资产审计，并在编译后使用最终 MAP 证明每个 table/sender pair 同一 256-word page；最终静态 evidence 为 0 warning，manifest 作为 run 快照参与 release hash 门禁。显式无文本 probe 豁免方向 profile。
- 排查方向错误时，分别判断控制器整屏列/行映射、同一行中字块排列顺序、单个字模内部列方向，不得把三者混为一次整行翻转。使用左右和上下均不对称的测试图，每次只改变一个变量并记录实板结果；字块位置正确但每个字块左右镜像时，不得交换字块顺序。
- 当前板 16 像素高 `2026年8月1号` 已实板确认的唯一基线是 `A1H + C0H`、源格式 `ssd1306-page-lsb-top`、`mirror_x_within_glyphs=false`、`mirror_y=true`。其中 `mirror_y=true` 对 8 像素高资产等价于逐 byte bit 反转，对 16 像素高资产等价于交换上下 page 并反转每个 byte 的 bit；不得再由“上下左右都反了”的照片症状直接推导 `true/true`。换板或换源格式时使用无文本非对称 probe，每次只改一个轴，实板确认后建立新的方向 profile。
- 标准中文和 ASCII 字符默认使用 `scripts/bdf_to_ssd1306.py` 从 BDF 源字模生成，再经 `ssd1306_page_bitmap.py` 审计并写入 `DB + TABL/TABH`。随 Skill 提供的字库登记在 `scripts/bdf_to_ssd1306.py` 的 `APPROVED_FONTS` 中：`wenquanyi_bitmap_song_16px_ascii_date_cn.bdf`（可打印 ASCII 与“年、月、号、中、国、￥”，101 字）和 `wenquanyi_bitmap_song_16px_gb2312.bdf`（可打印 ASCII、全角标点与 GB2312 一级二级，7539 字）。文字资产只能来自已登记字库，新增字库必须先登记 SHA256；字库体积与 MTP 无关，它只是取模查询源，写入芯片的永远只有当前文本用到的字，禁止把完整字库写入 HK64S825 的 1K MTP。
- 每行文本在生成资产前先跑 `python scripts/plan_text_line.py --text "<本行文本>"`，取其给出的 `--text`、`--widths`、字节数和居中列范围。字模按 word 存放、发送器每轮发一次 `TABL` 再一次 `TABH`，因此每行字节数必须为偶数；行宽为奇数时该工具在行尾追加一列 1 像素空白凑偶数。不得为了凑偶数加宽字形：格宽超过字形 `DWIDTH` 时 `crop_glyph_cell` 会拒绝，release 门禁也用同一函数逐字形重建比对。
- 基线由 `layout_baseline` 按本行实际字形推导，不接受人工传入的任意基线。只用 `cell_height - 3` 会让 `y_offset` 为 `-3` 的字形（`g j p q y` 与 `( ) / J ] _ { | }` 等）在任何 `cell_height` 下都恰好溢出一行；纯汉字资产的推导结果与该公式相同，故既有资产不受影响。
- 正式文字资产必须把 `layout[].kind` 固定为 `text`，并由同一确定性转换器记录每个字符的 Unicode codepoint、字符宽度、逐字 glyph SHA256、生成器版本、字体 ID 和固定字体 SHA256。`new-run` 与 `close-loop` 必须从 Skill 内置固定字体逐字节重建源字模，再核对 manifest 和 ASM DB；禁止复用只有 label 和自填 SHA256 的旧字模。字体缺字、宽度不符、来源字段缺失、不同 Unicode codepoint 得到相同 glyph 或重建结果不一致时必须失败关闭，错误码为 `DISPLAY_GLYPH_PROVENANCE_MISMATCH`，不得回退到旧清单、临时手绘或模型猜测的点阵。
- SHA256 只能证明某组字节未变化，不能证明这些字节代表 label 声称的字符；点阵预览只用于人工诊断，不能代替 Unicode 语义门禁。标准文字的一致性定义是“相同文本 + 相同字体 SHA256 + 相同生成器版本 + 相同尺寸/方向 profile => 相同逐字 glyph SHA256 和最终 DB”。
- 使用文泉驿子集时必须保留 `references/fonts/NOTICE-wenquanyi-bitmap-song.txt`。两个子集都从上游 `bdf/wenquanyi_12pt.bdf`（SHA256 `b4bc0413...4247a28`，6759714 字节）按 `ENCODING` 机械提取，点阵字节、`DWIDTH`、`BBX` 与上游逐字节一致；核对上游哈希前先核对字节数，截断的下载会得到不同哈希并静默丢掉后段字形。该字体源文件标注为 GPL v2 with font embedding exception；`u8g2_wqy` 的 MIT 包装许可证不替代字体本身的许可证。U8g2 的 `u8g2_font_*` 数组是专用 RLE 格式，不能直接发送给 SSD1306。`fontDisplay` 的二进制字库和取模 EXE 未提供清晰再分发授权，禁止纳入正式 Skill 或交付 ASM。
- 为保留已实板验证的纯图片/Logo，可继续使用带来源证据的 image manifest。正式文字不得通过 `--base-manifest` 保留未重建字块；若使用该模式升级历史文字资产，必须把所有 `layout[].kind=text` 字块列入 `--replace-label`，使每个字符都由固定字体重建。
- 每个 GDDRAM 数据字节必须使用 SSD1306 page 格式：bit0 是该 page 顶部像素，bit7 是该 page 底部像素；禁止把字模按普通横向行扫描直接发送。
- 多字符、汉字、Logo、头像或位图必须先设置与数据量一致的水平寻址窗口，再严格按 page → 当前行字块/图片块 → 列发送。两个 16x16 汉字必须依次发送 page0 的第 1 个字 16 列、page0 的第 2 个字 16 列、page1 的第 1 个字 16 列、page1 的第 2 个字 16 列，不得按“完整第 1 字两页后再完整第 2 字两页”的顺序发送。
- 可见亮屏不得只用 `A5H/AFH` 或裸 `AFH/AEH` 证明亮灭；必须先写入 1024 字节 `0xFF` 到 GDDRAM。8 位计数器实现 1024 字节时，必须审查低字节 `00H` 配合高计数 `04H` 的 4×256 结构，闪烁可在此后用 `AFH/AEH` 开关显示输出，或用精确 1024 byte 的 `FF/00` 重刷实现。
- 除复位/中断向量等必要位置外，避免无意义 `ORG` 空洞；编译后检查 code size、warning 和 hash。

## 快速路径

简单 LED/GPIO 任务使用快速路径：

1. 确认芯片为 `HK64S825`，确认一句话功能需求。
2. 读取通用规则和 GPIO/LED 相关规范，不读取 OLED、数码管、analysis 或模板 ASM。
3. 简单任务不创建设计文档、计划文档、probe 工程或额外说明文件；候选 ASM 只写入隔离运行目录。
4. 一次完成需求解析、候选生成、静态检查、编译和 release；失败时只修订候选并重跑门禁。
5. 只有 `release` 返回 `RELEASED` 后，才输出已编译 ASM 和编译凭据。

复杂任务按涉及模块增量读取资料；不要先加载整个 spec 目录。

## 闭环命令

运行环境要求 Python 3.7+。`hk8asm.py`、`scripts/builtin_compiler.py` 和其他内置脚本只依赖标准库，不要求 Python 3.8 或 Python 3.10。默认 profile/config 已使用可移植占位符：`$PYTHON` 会展开为当前运行 `hk8asm.py` 的 Python，`$SKILL_ROOT` 会展开为当前 Skill 根目录。若机器默认 `python` 不可用或低于 3.7，应改用系统中可用的 `python3`、`py -3.7`、`py -3.8` 或智能体自带 Python 运行闭环命令；不得因为缺少 Python 3.10 而阻断 ASM 编译 release。

稳定命令入口如下：

```powershell
python scripts/hk8asm.py doctor --profile references/profiles/HK64S825.profile.json --config references/configs/builtin-config.json
python scripts/hk8asm.py new-run --profile references/profiles/HK64S825.profile.json --config references/configs/builtin-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id
python scripts/hk8asm.py close-loop --run-dir .hk8asm/run-id
python scripts/hk8asm.py release --run-dir .hk8asm/run-id --output verified.asm
```

`doctor` 探测 compiler adapter 和批准工具版本；`new-run` 把输入快照到隔离运行目录；`close-loop` 执行静态检查和目标编译，并保存 source/artifact/evidence hash；`release` 是唯一允许释放已编译 ASM 的命令。

内置编译器说明：

- `scripts/builtin_compiler.py` 是默认目标编译模块，读取 `instruction-reference.json` 与 `register-reference.json`，输出 HEX/BIN/MAP。
- 支持资料包中 65 条指令变体、标签、`ORG`、`EQU`、`DB`、`DW` 和 `END`。
- 不支持或无法确定的语法必须 fail closed，不能伪装编译通过。
- `asm_static_check.py` 只是静态检查器，不是替代编译器；`fake_adapter.py` 只能用于自动化测试，不能用于 release。
- 内置编译 release 只证明源码通过当前 Skill 内置编译器，不证明公司 IDE/ASMC 的符号分类、头文件或工程环境兼容。未经公司编译器交叉验证，不得宣称公司编译器兼容，不得使用 `company compatible`、`官方编译通过` 或同义措辞命名文件或描述状态。
- 已知兼容性反例：公司编译器可能把 `BTSZ STATUS,b` / `BTSNZ STATUS,b` 中的 `STATUS` 分类为常量 `K`，从而拒绝要求 `[R,b]` 的指令。可移植交付源码禁止该形式；应直接测试业务寄存器位，或使用已经过公司编译器交叉验证的等价序列。

外部编译器说明：

- `scripts/compiler_adapter.py` 是可选外部 ASMC 适配器。只有用户明确要求使用公司官方 ASMC，或需要与官方 IDE/ASMC 做交叉验证时才使用。
- 外部 ASMC 模式必须显式配置 `--asmc-cli`、`--compiler-source-root`、`--compiler-mcu-type` 和 `--tool-version`；其中 `--compiler-mcu-type` 是公司编译器源码接受的工程型号，不一定等于对外芯片名 `HK64S825`。
- 外部 adapter 命令必须配置为字符串数组，并按 `<command...> <role> <probe|run> --input input.json --output output.json` 协议调用。禁止写成 shell 字符串。

## 硬门禁

- release 模式下，候选 ASM 在 release 前只能存在于隔离运行目录中。
- Profile 提供 `spec_root` 和 `static_check` 时，静态检查必须使用内置规范检查器。
- 编译 warning 一律视为失败，除非明确列入 `allowed_warnings`。
- 目标编译必须使用批准版本的内置编译模块或用户明确配置的外部 ASMC；源码、产物和 evidence 必须通过 hash 绑定。
- 只有 evidence 明确记录公司编译器交叉验证成功时，才可声明公司编译器兼容；只有内置编译 evidence 时，状态必须写成“内置编译 release”。
- 最终 release 的 ASM 中，说明性注释必须使用中文。寄存器名、指令名、标号、宏名、文件名和英文专有名词可以原样保留，但不得使用英文句子作为 ASM 注释。
- 烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件；若用户后续要求执行，必须单独记录结果，且不得把仅编译通过描述为实板验证通过。
- 默认禁止修改 fuse、lock、security bit、OPTION、保护位或其他非易失配置，除非另有批准流程。
- 编译后源码、产物或 evidence 发生任何变化，release 必须失效。
- 精确四位数码管任务必须通过 `HK-7SEG-008..010`：SRAM 读/RMW 后的访问间隔、skip 落点间隔、显式 `SCK_PS` 与 `008H/RETI` 入口；任何一项失败都不得 release。
- release 门禁失败时，只返回诊断和 evidence 路径，不得展示 release 候选 ASM。

## Release 后最终回复

只有 `release` 返回 `RELEASED` 后，才可以向用户交付：

- 用户要求的已编译 ASM 内容或文件路径；
- 芯片/型号和 run ID；
- source、artifact 和 evidence hash；
- 简短编译凭据：静态检查结果、编译器版本、warning 策略、产物 hash。若未执行烧录/回读/实板验证，必须明确标注为“未执行，暂不作为本次输出前置条件”。

如果 release 没有成功，只说明失败门禁和下一步所需输入/动作。不得包含未 release 的源码。

## 安装

可用以下命令安装本 Skill：

```powershell
python scripts/install.py --target codex-user --mode copy
python scripts/install.py --target claude-user --mode copy
python scripts/install.py --target codex-project --project-dir <project> --mode copy
python scripts/install.py --target claude-project --project-dir <project> --mode copy
```

默认使用 `copy` 生成精简的可移植安装副本；`symlink` 仅用于本仓库开发调试，会暴露测试和开发资料，不用于分发。Codex 可用 `$writing-hk8-mcu-asm` 显式调用；Claude Code 可用 `/writing-hk8-mcu-asm` 显式调用。描述匹配时也可以隐式触发。
