---
name: writing-hk8-mcu-asm
description: 用于生成、修改、审查或编译公司 HK64S825 8 位 MCU 的 ASM，适用于芯片专属汇编、LED/OLED/数码管功能、静态检查、内置编译模块编译通过后输出 ASM 或失败关闭交付时。
---

# HK64S825 ASM 编译闭环 Skill

本 Skill 面向公司唯一 8 位 MCU `HK64S825`。默认使用 Skill 内置 HK64S825 编译模块完成静态检查和目标编译，通过后即可 release；烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件。失败时只返回诊断，不展示候选 ASM。

## 第一条回复

先从用户请求中解析目标芯片型号：

- 若用户请求已经明确包含 `HK64S825`，例如“已确认 HK64S825”“HK64S825 ASM 闭环”“HK64S825 OLED”或等价表述，视为型号已确认；不得再要求用户回复“是/否”或重复确认型号，直接进入需求解析、规则读取、候选生成、静态检查、编译和 release。
- 若用户请求明确写出其他芯片型号，立即停止并说明暂不支持，不得猜测架构、寄存器或指令集。
- 若用户请求没有提供目标型号，第一条回复只询问并确认芯片型号，不得输出 ASM：

```text
请先确认目标芯片型号是否为 HK64S825？
```

如果用户确认的型号不是 `HK64S825`，立即停止并说明暂不支持。选择、确认或解析为 `HK64S825` 后，使用 `references/spec/` 中的芯片规则、指令集、寄存器、内存、程序布局、LED、OLED 和数码管规范来设计 ASM，不得追问与当前功能无关的输入。

## 必需输入

创建候选源码前，先区分“资料库已知规则”和“用户任务缺口”。默认自动使用 `references/spec/` 中 HK64S825 的指令、SFR、内存、程序布局、LED、OLED、数码管、工具链和检查规则；资料库已经明确的参数不得重复追问用户。

创建候选源码前只必须确认或从请求中可靠解析：

- 目标芯片为 `HK64S825`；
- 本次要实现的具体功能，例如 LED、OLED、数码管或组合功能；
- 当前任务中无法从 spec 推断、且会影响代码行为的功能参数，例如显示内容、闪烁频率、计数范围、图片/字模数据、坐标或刷新要求。

默认按 spec 中当前板级规则处理 LED、OLED 和数码管。只有用户说明换板、改接线、改外设型号、改地址、改极性、共享 GPIO，或 spec 无法覆盖当前任务时，才询问对应 board profile 缺口。

OLED/I2C 的 `POD` 与上拉是例外：芯片确认后、创建候选源码前必须确认 SDA、SCL 各自是否配置 `POD`，并且候选源码前必须确认 I2C 上拉来源。只有用户已在当前请求中逐引脚明确说明，才可跳过对应问题；不得从“传统 I2C”、旧代码或默认模板猜测。不得先生成候选、运行静态检查或编译后，再以 POD 或上拉缺口为由中止。

OLED 查表显示还必须在候选生成前解析芯片型号、主频、MTP 容量、分辨率、I2C 地址、SDA/SCL、上拉/开漏方式、显示方向和是否反色。当前已验证板级参数或用户已明确给出的值直接采用，不得重复询问；资料库和请求都没有的参数才作为缺口，按一次最多三题的选择题规则分批确认。

未明确时依次询问以下 A/B/C/D 选择题，一次最多三题：

```text
1. PB7（SDA）是否设置 POD？
A. 设置 POD（当前板推荐）
B. 不设置 POD
C. 不确定/我不知道

2. PB6（SCL）是否设置 POD？
A. 不设置 POD（当前板推荐）
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
- 数码管：再读取 `06-数码管动态扫描规范.md`。
- 构建/编译：读取 `07-构建-烧录-验收规范.md` 中编译相关段落、profile/config 和 adapter 配置。

禁止复制 templates、example 或 sample ASM 作为候选源码。示例文件只作反例或格式参考，不进入生成上下文；不得把示例改名、删注释、局部替换后当成新代码。必须根据当前需求、规则、寄存器和时序重新撰写候选 ASM。

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
- 在创建候选源码前完成 PB7/SDA、PB6/SCL 的逐引脚 `POD` 选择和 I2C 上拉来源确认；缺少任一项不得开始静态检查或编译。
- PB6/PB7 OLED 亮屏默认优先已验证最小初始化：只写 `PB_PPU`、`PB_POE`、`PB_PIO`，建立上拉、输出使能和 SDA/SCL idle high。不得为了“完整初始化”无证批量写 `PB_POD/PB_INS/PB_PPD/PB_PSL`；只有 board profile、E1 证据或用户明确要求证明需要时才加。
- 用户或当前板级依据明确确认 PB6/PB7 不配置 `PB_POD` 时，结构化 PinContract 写 `configure_drive_mode: false`，但仍必须通过 `PIO` 先于 `POE`、位所有权和 ACK 释放检查；不得把该例外用于普通 GPIO。
- I2C 第 9 个时钟前释放 SDA；ACK 采样必须读 `PB_INS`，不得读 `PB_PIO`，因为 PB_PIO 可能是输出锁存而不是真实引脚电平。亮屏最小路径可以采样记录 ACK 但不直接停机；若实现 NACK 错误路径，必须确认读法真实且不会 false NACK 后再 STOP/重试/进安全状态。
- OLED 上电后必须先执行上电稳定延时，例如 `DELAY_100MS`，再发送 `0xAE`、初始化命令或数据事务。
- I2C 发送 bit 前必须复核 `BTSZ` 语义：`BTSZ R,b` 是 bit=0 跳过下一条；MSB-first 发送 bit7 的已验证布局是 `BTSZ 80H,7` 后 bit7=1 跳到 `BSET PB_PIO,7`，bit7=0 走 `BCLR PB_PIO,7`，不得把 0/1 分支反写。
- I2C 时序不得靠随机增删 `NOP` 猜测修复；普通编译 release 给出 clock/cycle 依据，硬件阶段再测 SCL/timing。
- SSD1306 初始化必须包含 charge pump `8D/14`，并设置 column/page range 后进入 `0x40` 数据模式。
- 当前已验证显示基线为 SSD1306 128x64、7-bit 地址 `3CH`、写地址 `78H`、PB7=SDA、PB6=SCL、命令模式控制字节 `00H`、数据模式控制字节 `40H`、正常显示命令 `A6H`。当前板反馈证明 `A0H + C0H` 只交换两个汉字的位置，不能修正单字内部镜像；单字左右镜像必须在每个 16 列字模块内单独逆序。`A1H + C0H` 配合单字列逆序在实板复验前只能标为候选；换板时必须重新确认显示方向。
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
