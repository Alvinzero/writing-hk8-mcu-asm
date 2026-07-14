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

## 快速路径

简单 LED/GPIO 任务使用快速路径：

1. 确认芯片为 `HK64S825`，确认一句话功能需求。
2. 读取通用规则和 GPIO/LED 相关规范，不读取 OLED、数码管、analysis 或模板 ASM。
3. 根据规则新写候选 ASM 到隔离运行目录，不向用户展示候选源码。
4. 运行静态检查和目标编译。
5. 只有 `release` 返回 `RELEASED` 后，才输出 ASM 和编译凭据。

复杂任务按涉及模块增量读取资料；不要先加载整个 spec 目录。

## 闭环命令

运行环境要求 Python 3.10+，脚本只依赖标准库。稳定命令入口如下：

```powershell
python scripts/hk8asm.py doctor --profile profile.json --config local-config.json
python scripts/hk8asm.py new-run --profile profile.json --config local-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id
python scripts/hk8asm.py close-loop --run-dir .hk8asm/run-id
python scripts/hk8asm.py release --run-dir .hk8asm/run-id --output verified.asm
```

`doctor` 只探测显式配置的 compiler adapter 和批准工具版本；若额外配置了 programmer/verifier adapter，也只做可选探测。`new-run` 把输入快照到隔离运行目录。`close-loop` 只执行静态检查和目标编译，并保存 source/artifact/evidence hash。`release` 是唯一允许释放已编译 ASM 的命令。

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
