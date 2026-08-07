---
name: writing-hk8-mcu-asm
description: 用于生成、修改、审查或编译公司 HK64S825 8 位 MCU 的 ASM，适用于芯片专属汇编、LED/OLED/数码管功能、静态检查、内置编译模块编译通过后输出 ASM 或失败关闭交付时。
---

# HK64S825 ASM 编译闭环 Skill

本 Skill 面向公司唯一 8 位 MCU `HK64S825`。默认使用 Skill 内置 HK64S825 编译模块完成静态检查和目标编译，通过后即可 release；烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件。失败时只返回诊断，不展示候选 ASM。

## 第一条回复

先从用户请求中解析目标芯片型号：

- 若用户请求已经明确包含 `HK64S825`，或显式调用 `$writing-hk8-mcu-asm`/`/writing-hk8-mcu-asm`，视为型号已确认；不得再要求用户回复“是/否”或重复确认型号，直接进入需求解析和缺口检查。芯片已确认不等于开发板已确认；存在板级缺口时必须先询问，不得生成候选源码。
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

已注册 board profile 只在用户明确选择对应 ID 后读取。需要向用户推荐已注册 profile 时，必须先以本 `SKILL.md` 所在目录为根，只枚举 `references/boards/` 的一级非隐藏子目录名，按字典序完整列出为当前已有的 `board_profile_id`。枚举目录名只用于展示 ID，不等于读取或采用板级资料；用户选择前不得打开、解析或概述任何 profile 文件，也不得根据目录名猜测适用性、状态或接线。目录中没有 ID 时，明确说明当前没有已注册 profile，不得继续把“选择已注册 profile”标为推荐。用户选择自定义开发板时，只收集当前功能需要的最小板级参数，不要求无关硬件信息。

数码管板级信息未明确且存在已注册 ID 时，第一题必须把实际枚举结果填入以下模板，不得只写“选择已注册 board profile”而省略 ID：

```text
当前已有的 board_profile_id：
- `<实际 ID 1>`
- `<实际 ID 2>`

A. 选择已注册 board profile（推荐；有多个 ID 时回复 `A <board_profile_id>`）
B. 我提供本板数码管接线表
C. 先做逐段逐位硬件探测程序
D. 不确定 / 我不知道
```

用户也可以直接回复列表中的完整 `board_profile_id`。只有一个 ID 时，选项 A 必须直接写出该 ID，用户只回复 `A` 即视为明确选择；有多个 ID 时，用户只回复 `A` 仍不构成板级确认，必须原样重列 ID 并只追问具体 ID，不得默认选择第一项，也不得重复询问已经回答的分类问题。

用户选择提供接线表时，熟悉硬件的用户可在一次回复中按下列格式提供当前任务所需字段，不必先回答分类问卷：

```text
驱动：GPIO 动态扫描 / 驱动芯片型号
段线：A=..., B=..., C=..., D=..., E=..., F=..., G=..., DP=...
视觉位序：左起 COM 引脚、共阳/共阴、MCU 选通电平
反相：段线与位选是否有外部反相
时钟：OSC=..., SCK_PS=...
电气：共享 GPIO、限流方式、峰值驱动能力是否确认
```

GPIO 直驱时还必须确认 A-G/DP 逐段引脚、从左到右的逐位 COM 引脚、每位有效电平、外部三极管/MOS 是否反相、OSC/SCK_PS、共享 GPIO 和限流/驱动方式。任一项未知时不得生成正式显示 ASM；用户明确要求硬件探测时，可另建逐段逐位 probe，探测结论经用户确认后再建立 board profile。

OLED/I2C 任务必须在候选生成前读取 `references/workflows/oled.md`，并按其中问卷确认逐引脚 POD、上拉、地址、方向和资产输入。普通 GPIO/数码管任务不得加载该文件。

缺口问题必须以 A/B/C/D 选择题呈现；除多个 board profile ID 的选择题外，用户只需要回复选项字母。一次最多提出 3 个选择题；每题 2 到 4 个选项，默认或推荐选项必须标注“推荐”，并且必须包含“不确定/我不知道”选项。不得要求用户自由填写一长串板级参数；若确实需要非选项数据，例如 `board_profile_id`、显示文本、图片字模或真实文件路径，先说明原因，再只收集当前任务必需的最小数据。

编译器默认来自资料包内置配置：`scripts/builtin_compiler.py`，批准版本为 `builtin-hk64s825-assembler-2`。默认不需要用户提供本机 IDE、外部 ASMC 或 HK_ASM_Compiler 路径。禁止扫盘、遍历本机目录或猜测 IDE/CLI 路径；不得使用 Get-ChildItem、os.walk、rglob、where 或全盘搜索寻找编译器。

烧录、回读和硬件验证所需的硬件环境信息，只在用户明确要求执行对应后续验证阶段时询问。不得在普通代码生成阶段或编译 release 阶段提前追问无关硬件细节。缺少的信息若不影响当前阶段，可写入 `open_items`；只有缺口会影响安全、电气争用、地址/内存布局或编译正确性时，才列入 `unresolved_inputs` 并停止。

## 规则读取策略

只读取当前任务相关规则，不得加载无关 OLED、数码管或 analysis 快照资料。不得把大型规则 JSON 整份载入上下文；使用文本搜索或结构化解析，只检索候选源码实际使用的 mnemonic、SFR、rule ID 和当前功能章节。

- 通用生成任务：读取 `references/spec/AGENTS.md` 和 `09-AI智能体生成与审查协议.md` 的相关段落；从 `asm-rules.json`、`instruction-reference.json`、`register-reference.json`、`register-alias-policy.json` 定向查询实际使用项。命中下述“已确认 E1 profile 的倒计时快速路径”时不重复读取这些文件，由受限生成器和完整 `quick-release` 门禁执行已有规则。
- LED/GPIO：再读取 `05-GPIO-I2C-OLED驱动规范.md` 中 GPIO/LED 相关段落和必要 checklist。
- OLED：读取 `references/workflows/oled.md`，再读取 `05-GPIO-I2C-OLED驱动规范.md` 中 I2C/OLED 相关段落。
- OLED 字形出现“窗口尺寸正确但字形不完整、像多个字符拼接”时，再读取 `08-踩坑案例与症状诊断手册.md` 的 OLED 查表索引案例。
- 数码管：板级缺口提问前只允许枚举 `references/boards/` 的一级目录名并向用户展示全部 ID。用户明确选择后，若存在 `references/boards/<board_profile_id>/seven-segment.json`，优先读取机器 profile；命中倒计时快速路径时不再读取通用规范。其他数码管任务再读取 `06-数码管动态扫描规范.md` 和所选 profile 的 `seven-segment.md`；不得试读未选择 profile 的文件来替用户选板。
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

## OLED 专项路由

OLED/SSD1306/I2C 显示任务必须读取并执行 `references/workflows/oled.md`；主 Skill 不再重复载入其资产、方向和字体细则。

## 快速路径

简单 LED/GPIO 任务使用快速路径：

1. 确认芯片为 `HK64S825`，确认一句话功能需求。
2. 读取通用规则和 GPIO/LED 相关规范，不读取 OLED、数码管、analysis 或模板 ASM。
3. 简单任务不创建设计文档、计划文档、probe 工程或额外说明文件；候选 ASM 只写入隔离运行目录。
4. 一次完成需求解析、候选生成、静态检查、编译和 release；失败时只修订候选并重跑门禁。
5. 只有 `release` 返回 `RELEASED` 后，才输出已编译 ASM 和编译凭据。

复杂任务按涉及模块增量读取资料；不要先加载整个 spec 目录。

## 已确认 E1 profile 的倒计时快速路径

同时满足以下条件时直接使用此路径：用户已明确选择一个 `seven-segment.json` 机器 profile；profile 为 `status=ready`、`unresolved_inputs=[]`，并记录 `evidence.level=E1` 与 `evidence.status=user_confirmed_normal_display`；功能是四位 `MM:SS` 逐秒倒计时，终点为 `00:00` 保持，分隔符为 profile 默认的视觉第二位 DP 或不显示。

1. 只读取用户已选择的 `seven-segment.json`，从当前请求解析 `MM:SS`、终点行为和分隔符；不要重新询问 profile 已确认的接线、时钟、反相、限流或驱动能力。
2. 不读取 analysis、templates、示例 ASM、检查器源码或通用数码管规范，不手工重做段码表和周期推导。运行 `scripts/generate_countdown.py`，由受限生成器校验机器 profile、求解扫描周期并生成完整 request 与候选 ASM。
3. 生成后直接运行一次 `quick-release`。不要预先单独运行 `doctor` 或 `lint`；`quick-release` 已包含 doctor、静态检查、真实内置编译和 release。失败时依据聚合诊断修订后再重跑一次完整门禁。
4. 只消费 `quick-release` 的 JSON 回执；其中已直接包含 evidence hash、编译器版本、warning、HEX/BIN/MAP 路径与 hash、代码规模、静态摘要和 timing audit，无需再逐个读取这些文件。
5. E1 状态只表示用户确认该倒计时正常显示，不得扩大为全部硬件验收通过，也不得把内置编译 release 描述为公司编译器兼容。

```powershell
python scripts/generate_countdown.py --profile references/profiles/HK64S825.profile.json --board-profile references/boards/<board_profile_id>/seven-segment.json --start 11:11 --source candidate.asm --output-request request.json
python scripts/hk8asm.py quick-release --profile references/profiles/HK64S825.profile.json --config references/configs/builtin-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id --output verified.asm
```

`--terminal-behavior` 当前只支持 `hold`。`--separator` 可取 `profile`、`visual-digit-2-dp` 或 `none`。生成器是受限程序化生成器，不复制 template/example，也不替代其他数码管功能的通用生成流程。

## 闭环命令

运行环境要求 Python 3.7+。`hk8asm.py`、`scripts/builtin_compiler.py` 和其他内置脚本只依赖标准库，不要求 Python 3.8 或 Python 3.10。默认 profile/config 已使用可移植占位符：`$PYTHON` 会展开为当前运行 `hk8asm.py` 的 Python，`$SKILL_ROOT` 会展开为当前 Skill 根目录。若机器默认 `python` 不可用或低于 3.7，应改用系统中可用的 `python3`、`py -3.7`、`py -3.8` 或智能体自带 Python 运行闭环命令；不得因为缺少 Python 3.10 而阻断 ASM 编译 release。

稳定命令入口如下：

```powershell
python scripts/hk8asm.py doctor --profile references/profiles/HK64S825.profile.json --config references/configs/builtin-config.json
python scripts/hk8asm.py lint --profile references/profiles/HK64S825.profile.json --config references/configs/builtin-config.json --request request.json --source candidate.asm
python scripts/hk8asm.py new-run --profile references/profiles/HK64S825.profile.json --config references/configs/builtin-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id
python scripts/hk8asm.py close-loop --run-dir .hk8asm/run-id
python scripts/hk8asm.py release --run-dir .hk8asm/run-id --output verified.asm
python scripts/hk8asm.py quick-release --profile references/profiles/HK64S825.profile.json --config references/configs/builtin-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id --output verified.asm
```

`lint` 一次聚合中文注释和语义问题，不创建持久 run；`quick-release` 在一次命令中执行 doctor、快照、检查、编译和 release，并直接返回完整发布凭据。`release` 仍是唯一允许释放已编译 ASM 的状态转换。

完整板级契约的“全部段亮/全灭循环”可先运行 `scripts/generate_seven_segment.py`，由生成器求解扫描延时和帧数，并把 `HOLD_ON/HOLD_OFF` 写成完整周期审计目标。生成器只支持其声明的受限功能，不替代通用 ASM 生成。

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

- release 以 `new-run` 内的源码快照为唯一候选；工作区源文件只可作为 `lint/new-run/quick-release` 输入，门禁通过前不得向用户展示或交付。
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

开发源的完整规范包使用 `validate_spec.py` 默认模式自检；精简安装副本不携带 analysis、templates 和开发测试，必须使用 `validate_spec.py --runtime-only`：

```powershell
python references/spec/tools/validate_spec.py references/spec --runtime-only
```

默认使用 `copy` 生成精简的可移植安装副本；`symlink` 仅用于本仓库开发调试，会暴露测试和开发资料，不用于分发。Codex 可用 `$writing-hk8-mcu-asm` 显式调用；Claude Code 可用 `/writing-hk8-mcu-asm` 显式调用。描述匹配时也可以隐式触发。
