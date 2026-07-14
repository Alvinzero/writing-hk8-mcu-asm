---
name: writing-hk8-mcu-asm
description: 用于生成、修改、审查、编译、烧录或硬件验证公司 HK64S825 8 位 MCU 的 ASM，适用于用户要求芯片专属汇编、LED/OLED/数码管功能、证据链代码、自编译/自烧录/自验证闭环或失败关闭交付时。
---

# HK64S825 ASM 闭环 Skill

本 Skill 只允许通过证据绑定闭环交付公司规范的 HK64S825 MCU ASM。必须失败关闭：在静态检查、真实构建、受控烧录、回读校验和功能验证全部通过，并且 `release` 成功之前，不得向用户展示候选 ASM。

## 第一条回复

每次调用本 Skill 后，助手第一条回复必须先询问并确认芯片型号，不得从上下文猜测。

建议直接这样问：

```text
请先确认目标芯片型号是否为 HK64S825？
```

如果用户确认的型号不是 `HK64S825`，立即停止，并说明该芯片暂未被本 Skill 支持。本 Skill 当前只面向这一款 8 位 MCU。

选择 `HK64S825` 后，默认使用 `references/spec/` 中的 HK64S825 规则、指令集、寄存器、内存、程序布局、LED、OLED 和数码管规范来设计 ASM。不得追问与当前功能无关的输入；只在缺少会影响安全、烧录、时序、地址、资源分配或机器验收的必要信息时才暂停并列入 `unresolved_inputs`。

## 必需输入

创建任何候选源码前，必须收集并验证当前任务真正需要的信息：

- 芯片型号为 `HK64S825`；
- 当前功能需要的 board profile、板卡 ID、烧录器序列号、供电电压、时钟、OPTION/WDT 策略；
- 当前功能涉及的引脚归属、有效极性、上拉、电流限制和禁止争用条件；
- 当前功能涉及的外设、时序容差、ROM/RAM 限制、中断和 SRAM 约束；
- 目标工具链及批准版本；
- 可机器观测的验收条件，例如逻辑分析仪、串口测试治具、电流/电压测量、回读/CRC 或自动化测试夹具证据。

对于 LED、OLED、数码管任务，先读取 `references/spec/05-GPIO-I2C-OLED驱动规范.md`、`references/spec/06-数码管动态扫描规范.md` 和相关 checklist，直接采用规范中已经给出的接线、时序、布局和验收规则。不得追问与当前功能无关的输入。只要安全、时序、地址、内存、工具链或硬件观测相关输入仍未解析，就只返回诊断和 `unresolved_inputs`。不得生成或泄露 ASM。

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

禁止把模板直接复制成量产代码。模板只是可审查骨架，仍然必须补齐 board profile、工具链、构建、烧录和 E1 实板证据。

## 闭环命令

运行环境要求 Python 3.10+，脚本只依赖标准库。稳定命令入口如下：

```powershell
python scripts/hk8asm.py doctor --profile profile.json --config local-config.json
python scripts/hk8asm.py new-run --profile profile.json --config local-config.json --request request.json --source candidate.asm --run-dir .hk8asm/run-id
python scripts/hk8asm.py close-loop --run-dir .hk8asm/run-id
python scripts/hk8asm.py release --run-dir .hk8asm/run-id --output verified.asm
```

`doctor` 检查本机 adapter、批准工具版本、烧录器序列号、器件 ID 和电压。`new-run` 把输入快照到隔离运行目录。`close-loop` 依次执行静态检查、编译器、烧录器、回读和功能验证器。`release` 是唯一允许释放最终 ASM 的命令。

Adapter 命令必须配置为字符串数组，并按以下协议调用：

```text
<command...> <role> <probe|run> --input input.json --output output.json
```

Adapter 可以把 JSON 结果写入 `--output`，也可以在 stdout 输出单个 JSON 对象。禁止把 adapter command 写成 shell 字符串。

## 硬门禁

- 候选 ASM 在 release 前只能存在于隔离运行目录中。
- Profile 提供 `spec_root` 和 `static_check` 时，静态检查必须使用内置规范检查器。
- 编译 warning 一律视为失败，除非明确列入 `allowed_warnings`。
- 自动烧录默认且最高只能尝试 3 次。
- 回读/CRC 只证明传输成功，不代表功能正确；功能验证必须满足 request 中的 acceptance contract。
- 默认禁止修改 fuse、lock、security bit、OPTION、保护位或其他非易失配置，除非另有批准流程。
- 验证后源码或 evidence 发生任何变化，release 必须失效。
- 任一门禁失败时，只返回诊断和 evidence 路径，不得展示候选 ASM。

## Release 后最终回复

只有 `release` 返回 `RELEASED` 后，才可以向用户交付：

- 用户要求的已验证 ASM 内容或文件路径；
- 芯片/型号和 run ID；
- source、artifact 和 evidence hash；
- 简短验证凭据：静态检查结果、编译器版本、烧录器序列号、device ID、电压、回读 hash 和功能测试名称。

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
