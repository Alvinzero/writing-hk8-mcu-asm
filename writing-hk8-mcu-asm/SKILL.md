---
name: writing-hk8-mcu-asm
description: 用于生成、修改、审查或编译公司 HK64S825 8 位 MCU 的 ASM，适用于用户要求芯片专属汇编、LED/OLED/数码管功能、证据链代码、静态检查、目标编译通过后输出 ASM 或失败关闭交付时。
---

# HK64S825 ASM 闭环 Skill

本 Skill 只允许通过证据绑定的编译闭环交付公司规范的 HK64S825 MCU ASM。必须失败关闭：静态检查和目标编译通过后即可 release；在 `release` 成功之前，不得向用户展示候选 ASM。烧录、回读、逻辑分析仪或其他实板验证暂不作为输出 ASM 的前置条件，可作为后续可选验证项记录。

## 第一条回复

每次调用本 Skill 后，助手第一条回复必须先询问并确认芯片型号，不得从上下文猜测。

建议直接这样问：

```text
请先确认目标芯片型号是否为 HK64S825？
```

如果用户确认的型号不是 `HK64S825`，立即停止，并说明该芯片暂未被本 Skill 支持。本 Skill 当前只面向这一款 8 位 MCU。

选择 `HK64S825` 后，默认使用 `references/spec/` 中的 HK64S825 规则、指令集、寄存器、内存、程序布局、LED、OLED 和数码管规范来设计 ASM。不得追问与当前功能无关的输入；只在缺少会影响安全、烧录、时序、地址、资源分配或机器验收的必要信息时才暂停并列入 `unresolved_inputs`。

## 必需输入

创建候选源码前，先区分“资料库已知规则”和“用户任务缺口”。默认自动使用 `references/spec/` 中 HK64S825 的指令、SFR、内存、程序布局、LED、OLED、数码管、工具链和检查规则；资料库已经明确的参数不得重复追问用户。

每次只必须向用户确认：

- 目标芯片是否为 `HK64S825`；
- 本次要实现的具体功能，例如 LED、OLED、数码管或组合功能；
- 当前任务中无法从 spec 推断、且会影响代码行为的功能参数，例如显示内容、闪烁频率、计数范围、图片/字模数据、坐标或刷新要求。

默认按 spec 中当前板级规则处理 LED、OLED 和数码管。只有用户说明换板、改接线、改外设型号、改地址、改极性、共享 GPIO，或 spec 无法覆盖当前任务时，才询问对应 board profile 缺口。

编译所需的工具链信息只在本地配置无法自动解析时询问。烧录、回读和硬件验证所需的烧录器、板卡、供电和测试设备信息，只在用户明确要求执行对应后续验证阶段时询问。不得在普通代码生成阶段或编译 release 阶段提前追问无关硬件细节。

缺少的信息若不影响当前阶段，可写入 `open_items`，继续生成可审查草案；只有缺口会影响安全、电气争用、地址/内存布局、工具链正确性或机器验收时，才列入 `unresolved_inputs` 并停止升级状态。

## 资源导航

按任务需要加载资料，但以下资源始终视为权威：

- `references/spec/AGENTS.md`
- `references/spec/rules/asm-rules.json`
- `references/spec/rules/instruction-reference.json`
- `references/spec/rules/register-reference.json`
- `references/spec/rules/register-alias-policy.json`
- `references/spec/09-AI智能体生成与审查协议.md`
- `references/spec/` 下与任务相关的专题文档和 checklist

常用示例：

- `references/profiles/HK64S825.profile.example.json`
- `references/configs/local-adapter.example.json`
- `references/requests/gpio-request.example.json`
- `references/spec/templates/`

禁止把模板直接复制成量产代码。模板只是可审查骨架，仍然必须补齐当前任务所需的功能参数、board profile 缺口、目标工具链、静态检查和构建证据；烧录和 E1 实板证据作为后续验证项，不阻断编译通过后的 ASM 输出。

## 闭环命令

运行环境要求 Python 3.10+，脚本只依赖标准库。稳定命令入口如下：

```powershell
python scripts/hk8asm.py doctor --profile profile.json --config local-config.json
python scripts/hk8asm.py new-run --profile profile.json --config local-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id
python scripts/hk8asm.py close-loop --run-dir .hk8asm/run-id
python scripts/hk8asm.py release --run-dir .hk8asm/run-id --output verified.asm
```

`doctor` 检查本机 compiler adapter 和批准工具版本；若额外配置了 programmer/verifier adapter，也可做可选探测。`new-run` 把输入快照到隔离运行目录。`close-loop` 只执行静态检查和目标编译，并保存 source/artifact/evidence hash。`release` 是唯一允许释放已编译 ASM 的命令。

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
