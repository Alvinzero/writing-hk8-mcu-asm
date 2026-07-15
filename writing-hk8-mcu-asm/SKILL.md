---
name: writing-hk8-mcu-asm
description: 用于生成、修改、审查或编译公司 HK64S825 8 位 MCU 的 ASM，适用于用户要求芯片专属汇编、LED/OLED/数码管功能、证据链代码、静态检查、目标编译通过后输出 ASM 或失败关闭交付时。
---

# HK64S825 ASM 编译闭环 Skill

本 Skill 面向公司唯一 8 位 MCU `HK64S825`。目标是按 `references/spec/` 的规则重新撰写 ASM，并在静态检查和目标编译通过后即可 release。烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件；如用户另行要求，再作为后续验证单独执行和标注。

## 第一条回复

每次调用本 Skill 后，助手第一条回复必须先询问并确认芯片型号，不得从上下文猜测：

```text
请先确认目标芯片型号是否为 HK64S825？
```

如果用户确认的型号不是 `HK64S825`，立即停止并说明暂不支持。选择 `HK64S825` 后，默认使用 `references/spec/` 中的芯片规则、指令集、寄存器、内存、程序布局、LED、OLED 和数码管规范来设计 ASM，不得追问与当前功能无关的输入。

## 必需输入

创建候选源码前，先区分“资料库已知规则”和“用户任务缺口”。默认自动使用 `references/spec/` 中 HK64S825 的指令、SFR、内存、程序布局、LED、OLED、数码管、工具链和检查规则；资料库已经明确的参数不得重复追问用户。

每次只必须确认：

- 目标芯片是否为 `HK64S825`；
- 本次要实现的具体功能，例如 LED、OLED、数码管或组合功能；
- 当前任务中无法从 spec 推断、且会影响代码行为的功能参数，例如显示内容、闪烁频率、计数范围、图片/字模数据、坐标或刷新要求。

默认按 spec 中当前板级规则处理 LED、OLED 和数码管。只有用户说明换板、改接线、改外设型号、改地址、改极性、共享 GPIO，或 spec 无法覆盖当前任务时，才询问对应 board profile 缺口。

缺口问题必须以 A/B/C/D 选择题呈现，用户只需要回复选项字母。一次最多提出 3 个选择题；每题 2 到 4 个选项，默认或推荐选项必须标注“推荐”，并且必须包含“不确定/我不知道”选项。不得要求用户自由填写一长串板级参数；若确实需要非选项数据，例如显示文本、图片字模或真实文件路径，先说明原因，再只收集当前任务必需的最小数据。

示例：

```text
还缺 1 个会影响 LED 实板表现的板级信息，请选 A/B/C：
A. PA0-PA5 高电平点亮（推荐：若 LED 接 GND）
B. PA0-PA5 低电平点亮（若 LED 接 VDD）
C. 不确定/我不知道；先按编译通过生成，并在 open_items 标注需实板确认
```

编译器路径必须来自 profile、config 或 spec 明确配置。禁止扫盘、遍历本机目录或猜测 IDE/CLI 路径；不得使用 Get-ChildItem、os.walk、rglob、where 或全盘搜索寻找编译器。编译配置缺失时，直接报告缺少的配置项，不要自行在用户电脑上搜索。

烧录、回读和硬件验证所需的烧录器、板卡、供电和测试设备信息，只在用户明确要求执行对应后续验证阶段时询问。不得在普通代码生成阶段或编译 release 阶段提前追问无关硬件细节。缺少的信息若不影响当前阶段，可写入 `open_items`；只有缺口会影响安全、电气争用、地址/内存布局或工具链正确性时，才列入 `unresolved_inputs` 并停止升级状态。

## 规则读取策略

只读取当前任务相关规则，不得加载无关 OLED、数码管或分析快照资料。

- 所有任务：读取 `references/spec/AGENTS.md`、`references/spec/rules/asm-rules.json`、`instruction-reference.json`、`register-reference.json`、`register-alias-policy.json` 和 `09-AI智能体生成与审查协议.md`。
- LED/GPIO：再读取 `05-GPIO-I2C-OLED驱动规范.md` 中 GPIO/LED 相关段落和必要 checklist。
- OLED：再读取 `05-GPIO-I2C-OLED驱动规范.md` 中 I2C/OLED 相关段落。
- 数码管：再读取 `06-数码管动态扫描规范.md`。
- 构建/编译：读取 `07-构建-烧录-验收规范.md` 中编译相关段落、profile/config 和 adapter 配置。

禁止复制 templates、example 或 sample ASM 作为候选源码。示例文件只作反例或格式参考，不进入生成上下文；不得把示例改名、删注释、局部替换后当成新代码。必须根据当前需求、规则、寄存器和时序重新撰写候选 ASM。

## LED/GPIO 通用硬门禁

简单 LED/GPIO 不得套用端口全量初始化模板。生成前先判断任务需要哪些电气属性，再决定写哪些寄存器；默认只写当前功能必需的 `PIO` 和 `POE`，例如先预装安全输出值，再打开对应输出使能位。

不得为了显得完整而批量清写 `PPU/PPD/POD/INS/IOS`。只有 board profile 或当前功能明确要求上拉、下拉、开漏、输入通道或特殊功能选择时，才写对应寄存器，并在注释中说明依据；共享端口必须使用不会破坏其他 pin 的 read-modify-write 或集中式端口初始化。

WDT 未明确关闭时，任何可见延时、长忙等或周期循环必须插入 `CLRWDT`。`CLRWDT` 要放在忙等循环内部或足够短的循环层级内，不能只在初始化或主循环入口偶尔执行；如果确认 WDT 已关闭，必须在文件头写明 OPTION/WDT 依据。

## OLED 任务硬门禁

当用户要求依据 `D:\hk64s8x-cli` 项目经验生成或修正 OLED/SSD1306 ASM 时，先读取该仓库的 `docs/hk_asm_compiler_extract.md`、`asmc/SKILL.md` 和当前任务相关的已验证结论，再读取本 Skill 的 `05-GPIO-I2C-OLED驱动规范.md`。项目经验、旧示例和模板冲突时，以当前 `HK64S825` 目标、已编译证据和实板验证结论为准；带旧芯片型号的文件只能作为反例或历史线索，不得作为候选源码模板。

生成候选 ASM 前先写 OLED 契约测试，并至少断言以下行为；测试必须先失败，再写候选源码：

- 目标芯片为 `HK64S825`，不得出现旧芯片标注。
- PB6/PB7 初始化必须覆盖 `PB_PPU/PB_POD/PB_INS/PB_PIO/PB_POE`，并先预装 idle high 再打开输出；需要清下拉和特殊选择时同步写明。
- I2C 第 9 个时钟前释放 SDA，采样 ACK 后必须立即检查；NACK 必须 STOP 并进入安全状态或明确错误路径，不能无条件继续。
- `I2C_DELAY` 不得退回 2 个 `NOP` 的旧超速写法；16 MHz 目标下必须给出保守延时或实测频率依据。
- SSD1306 初始化必须包含 charge pump `8D/14`，并设置 column/page range 后进入 `0x40` 数据模式。
- 可见亮屏不得只用 `A5H/AFH` 或裸 `AFH/AEH` 证明亮灭；必须先写入 1024 字节 `0xFF` 到 GDDRAM，闪烁可在此后用 `AFH/AEH` 开关显示输出，或用精确 1024 byte 的 `FF/00` 重刷实现。
- 除复位/中断向量等必要位置外，避免无意义 `ORG` 空洞；编译后检查 code size、warning 和 hash。

## 快速路径

简单 LED/GPIO 任务使用快速路径：

1. 确认芯片为 `HK64S825`，确认一句话功能需求。
2. 读取通用规则和 GPIO/LED 相关规范，不读取 OLED、数码管、analysis 或模板 ASM。
3. 根据规则新写候选 ASM 到隔离运行目录，不向用户展示候选源码。
4. 运行静态检查和目标编译。
5. 只有 `release` 返回 `RELEASED` 后，才输出 ASM 和编译凭据。

复杂任务按涉及模块增量读取资料；不要先加载整个 spec 目录。

## 闭环命令

运行环境要求 Python 3.10+。`hk8asm.py` 和内置 adapter 只依赖标准库；真实 ASMC 编译由外部 `D:\hk64s8x-cli\asmc\scripts\asmc_compile.py` 和公司 `HK_ASM_Compiler` 源码环境提供，若该环境需要额外依赖，必须在 config 中用 `--python` 指向已安装依赖的解释器。稳定命令入口如下：

```powershell
python scripts/hk8asm.py doctor --profile profile.json --config local-config.json
python scripts/hk8asm.py new-run --profile profile.json --config local-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id
python scripts/hk8asm.py close-loop --run-dir .hk8asm/run-id
python scripts/hk8asm.py release --run-dir .hk8asm/run-id --output verified.asm
```

`doctor` 只探测显式配置的 compiler adapter 和批准工具版本；若额外配置了 programmer/verifier adapter，也只做可选探测。`new-run` 把输入快照到隔离运行目录。`close-loop` 只执行静态检查和目标编译，并保存 source/artifact/evidence hash。`release` 是唯一允许释放已编译 ASM 的命令。

资料包内置 `scripts/compiler_adapter.py` 作为真实 ASMC wrapper，但资料包不内置公司 `HK_ASM_Compiler` 源码、`D:\hk64s8x-cli` 项目或本机工具链路径。`compiler_adapter.py` 必须显式配置 `--asmc-cli`、`--compiler-source-root`、`--compiler-mcu-type` 和批准的 `--tool-version`；`--compiler-mcu-type` 是公司编译器源码实际接受的工程型号，不是对外任务芯片名的替代。`probe` 会真实编译一个最小 ASM，`run` 会调用 ASMC 生成产物并绑定 source/artifact hash。

`asm_static_check.py` 只是静态检查器，不是编译器；`fake_adapter.py` 只能用于自动化测试，不能用于 release。`local-adapter.example.json` 中的 `REPLACE_WITH...` 是占位符，不是遗漏文件；配置中出现 `REPLACE_WITH` 占位符时必须停止，报告缺少真实 compiler adapter 或真实工具链配置，不得把静态检查通过伪装成目标编译通过。

当 `doctor` 返回 `PROFILE_NOT_READY`、`INVALID_CONFIG` 或发现 `REPLACE_WITH...` 时，回复必须给出可执行的本机配置指引，而不是只让用户“提供 profile/config 路径”。必须说明：

- 模板文件在 Skill 内：`references/profiles/HK64S825.profile.example.json` 和 `references/configs/local-adapter.example.json`。
- 先复制为工作文件，例如 `profiles/local-HK64S825.profile.json` 和 `configs/local-adapter.json`；不得直接把 `.example.json` 当作 release 配置。
- `configs/local-adapter.json` 中的 `<ABSOLUTE_SKILL_ROOT>\scripts\compiler_adapter.py` 必须替换为当前安装 Skill 根目录下的 `scripts/compiler_adapter.py` 绝对路径，例如 Codex 用户安装通常在 `%USERPROFILE%\.agents\skills\writing-hk8-mcu-asm\scripts\compiler_adapter.py`，Claude Code 用户安装通常在 `%USERPROFILE%\.claude\skills\writing-hk8-mcu-asm\scripts\compiler_adapter.py`。
- 必须替换 `--asmc-cli`、`--compiler-source-root`、`--compiler-mcu-type`、`--tool-version`；其中 `--compiler-mcu-type` 是公司编译器源码接受的工程型号，不一定等于对外芯片名 `HK64S825`。
- `profiles/local-HK64S825.profile.json` 中 `status` 必须改为就绪状态，并把 `approved_tool_versions.compiler` 改为与 config 中 `--tool-version` 完全一致的批准版本。
- 配好后先运行：`python scripts/hk8asm.py doctor --profile profiles/local-HK64S825.profile.json --config configs/local-adapter.json`。只有 doctor 通过后，才继续 `new-run -> close-loop -> release`。

`local-adapter.example.json` 的 compiler 命令形态如下；复制后必须把占位符替换为真实绝对路径，因为 adapter 在隔离 run 目录执行，不能依赖当前目录：

```json
{
  "command": [
    "python",
    "<ABSOLUTE_SKILL_ROOT>\\scripts\\compiler_adapter.py",
    "--asmc-cli",
    "D:\\hk64s8x-cli\\asmc\\scripts\\asmc_compile.py",
    "--compiler-source-root",
    "<ABSOLUTE_HK_ASM_COMPILER_SOURCE_ROOT>",
    "--compiler-mcu-type",
    "<COMPILER_MCU_TYPE_ACCEPTED_BY_COMPILER_SOURCE>",
    "--tool-version",
    "<APPROVED_COMPILER_VERSION>"
  ]
}
```

Adapter 命令必须配置为字符串数组，并按以下协议调用：

```text
<command...> <role> <probe|run> --input input.json --output output.json
```

Adapter 可以把 JSON 结果写入 `--output`，也可以在 stdout 输出单个 JSON 对象。禁止把 adapter command 写成 shell 字符串。

## 硬门禁

- 候选 ASM 在 release 前只能存在于隔离运行目录中。
- Profile 提供 `spec_root` 和 `static_check` 时，静态检查必须使用内置规范检查器。
- 编译 warning 一律视为失败，除非明确列入 `allowed_warnings`。
- 目标编译必须使用批准版本的真实工具链；源码、产物和 evidence 必须通过 hash 绑定。
- 最终 release 的 ASM 中，说明性注释必须使用中文。寄存器名、指令名、标号、宏名、文件名和英文专有名词可以原样保留，但不得使用英文句子作为 ASM 注释。
- 烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件；若用户后续要求执行，必须单独记录结果，且不得把仅编译通过描述为实板验证通过。
- 默认禁止修改 fuse、lock、security bit、OPTION、保护位或其他非易失配置，除非另有批准流程。
- 编译后源码、产物或 evidence 发生任何变化，release 必须失效。
- 任一门禁失败时，只返回诊断和 evidence 路径，不得展示候选 ASM。

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

Codex 可用 `$writing-hk8-mcu-asm` 显式调用；Claude Code 可用 `/writing-hk8-mcu-asm` 显式调用。描述匹配时也可以隐式触发。
