# HK64S825 ASM 语义与时序门禁设计

日期：2026-07-16
状态：已由用户确认方案，待书面审阅后实施

## 1. 背景与问题证据

另一台电脑生成的 LED 闪烁源码同时包含四类问题：

1. 源码声明 PA0、PA3、PA5 为推挽输出，却没有显式清除对应 `PA_POD` 位。
2. 定义了五个 `EQU`，后续仍使用同值字面量，所有定义均未被引用。
3. 延时循环使用 `DECSZ`。该指令执行 `A ← R-1`，不会写回计数寄存器；循环因此不前进。
4. 延时按 16 MHz OSC 直接计算，没有应用 `SCK_PS=34H` 的默认 `/8` 分频。

现有闭环对该源码给出以下结果：

- `asm_static_check.py`：0 blocker、0 error、0 warning；
- `builtin_compiler.py`：`pass`，生成 30 words；
- 因此当前 release 门禁只能证明语法和编码成立，不能证明 GPIO 电气模式、循环数据流或延时时间正确。

若仅把三条 `DECSZ` 改为 `DECSZR`，原三层计数约为 8,032,131 cycles。在默认 `SCK=16 MHz/8=2 MHz` 下约为 4.016 秒，而不是 500 ms。相同循环外层计数改为 4 时约为 502 ms，说明原参数实际是按 16 MHz 指令时钟倒推的。

## 2. 目标

- 简单 LED/GPIO 源码继续保持短小，不恢复全端口初始化模板。
- release 前自动阻止 GPIO 输出模式遗漏、无用 `EQU`、无进展循环和错误时钟域延时。
- 使用资料包内置规则与编译模块，不扫描用户磁盘，不要求外部 IDE/CLI。
- 静态语义检查和内置编译在普通 LED 任务中保持秒级完成。
- 烧录、回读、逻辑分析仪和实板验证仍不作为当前 ASM 输出前置条件。

## 3. 非目标

- 不把内置编译通过描述为实板功能通过。
- 不模拟整个 HK64S825 指令集或任意复杂控制流。
- 不自动修改 OPTION、fuse、lock 或其他非易失配置。
- 不依赖示例 ASM 生成业务源码。
- 不为短小 SRAM scratch 强制增加 `EQU` 别名。

## 4. 总体架构

闭环调整为：

```text
用户需求
  -> request.json：PinContract + ClockContract + TimingContract
  -> 智能体按规则新写候选 ASM
  -> asm_static_check.py：结构、GPIO、符号、数据流、循环与周期证明
  -> builtin_compiler.py：指令编码和工件生成
  -> hk8asm.py：哈希绑定与 release
```

编译器继续负责“能否编码”；静态检查器新增“代码是否兑现请求契约”的职责。`hk8asm.py` 必须把隔离运行目录中的 `request.json` 和 `profile.json` 传给静态检查器，不能只传 ASM 与 toolchain。

## 5. 输入契约

### 5.1 GPIO PinContract

新生成的 GPIO 请求使用结构化 pin 定义：

```json
{
  "pins": {
    "led_outputs": {
      "port": "PA",
      "bits": [0, 3, 5],
      "direction": "output",
      "drive": "push_pull",
      "active_level": "high",
      "initial_state": "off",
      "preserve_unowned_bits": true
    }
  }
}
```

支持的 `drive` 首版为 `push_pull` 和 `open_drain`。GPIO 任务缺少真正影响代码的字段时，Skill 仍按现有规则用 A/B/C/D 选项询问；资料库或用户需求已经明确的字段不得重复询问。

### 5.2 ClockContract

不再用含义模糊的单一时钟字段表达所有时钟域：

```json
{
  "clock": {
    "osc_hz": 16000000,
    "sck_ps": "reset"
  }
}
```

- `osc_hz` 表示 OSC 振荡频率。
- `sck_ps="reset"` 使用 Profile 中 `SCK_PS` 复位值 `34H`。
- Profile 保存分频映射并派生 `sck_hz`。
- 旧 `clock_hz` 在迁移期继续接受，但只解释为 `osc_hz`，不得解释为 CPU/SCK 频率。

### 5.3 TimingContract

精确延时由标签、目标时间和容差绑定：

```json
{
  "timing": {
    "delay_targets": [
      {
        "label": "DELAY_500MS",
        "target_us": 500000,
        "tolerance_percent": 1.0
      }
    ]
  }
}
```

只要求“肉眼可见、无需精确”的任务可以声明 `precision="approximate"`；此时仍必须使用正确 SCK 域和有进展的循环，但不要求严格落入 1% 容差。

## 6. GPIO 最小充分初始化

“最小初始化”定义为最少但足以建立确定电气状态的操作，而不是固定只允许 `PIO/POE`。

对每个输出 pin，顺序必须为：

1. 只对所拥有 pin 显式建立 drive mode：
   - `push_pull`：清对应 `POD` 位；
   - `open_drain`：置对应 `POD` 位。
2. 根据 `initial_state` 与 `active_level` 预装安全 `PIO` 值。
3. 最后置对应 `POE` 位。

即使 `PA_POD` 复位值当前为 0，正式生成代码也不得把该复位态当作推挽模式的唯一依据。这样可覆盖非冷启动入口、代码复用和初始化重新执行，同时只增加目标 pin 所需的位操作。

`PPU/PPD/INS/IOS/PSL` 仍只在 PinContract 明确要求时配置。共享端口必须使用位操作或 read-modify-write 保留非本任务 pin。

## 7. 指令副作用与循环进展

静态检查器从 `instruction-reference.json` 的 `raw_notes`、`cycles`、`semantic_status` 和 `delivery_policy` 建立内部 `InstructionEffect`：

- 读取和写入目标：A、R、flags；
- 是否 skip 下一条；
- 普通与 skip-taken cycle 数；
- 是否受限或仅有编码证据。

首版重点审计单基本块和嵌套倒计数循环：

- `DECSZ/INCSZ` 后紧跟向后 `JMP`，且被当作持久计数器时阻断；
- `DECSZR/INCSZR` 可作为写回计数器；
- 每条循环回边必须能证明至少一个 loop-carried 状态在所有继续路径上更新；
- skip 指令后的下一条必须是一条明确机器指令；
- 含 `CLRWDT` 的循环仍必须证明有界退出，或明确声明为永久服务循环。

无法分析的复杂精确延时循环不得默认通过；返回结构化 `semantic_analysis_unresolved`，要求使用更易审计的循环结构。

## 8. EQU 单一来源检查

静态检查器保存每个 `EQU` 的定义位置、值和源码引用次数：

- 业务掩码、延时计数、尺寸和枚举定义后必须至少被代码引用一次；
- 未使用的 `EQU` 产生 warning；默认 `strict_warnings=true` 会阻断 release；
- 若定义了常量又在对应代码中重复使用同值魔数，可增加定向 warning；
- 官方 SFR 仍禁止二次 `EQU`；短小 SRAM scratch 继续允许直接地址。

## 9. 时钟派生与周期审计

Profile 新增 HK64S825 时钟模型：

```json
{
  "clock_model": {
    "sck_ps_register": "SCK_PS",
    "sck_ps_reset": "0x34",
    "instruction_clock": "SCK",
    "divider_by_sckps": {
      "0x4": 8
    }
  }
}
```

实际实现保存完整有效分频表，而非只保存 `0x4`。

周期审计器从延时标签开始执行受支持的抽象解释：

- 支持立即数装载、SRAM 写入、`NOP`、`CLRWDT`、写回增减、skip、向后 `JMP` 和 `RET`；
- 支持由常量初始化的嵌套计数循环；
- cycle 数来自 `instruction-reference.json`；
- skip 未触发按 1 cycle，触发并跳过下一条按 2 cycles；
- 报告总 cycles、派生 `sck_hz`、实际微秒、目标、误差百分比；
- 超出 TimingContract 容差时阻断 release。

若源码显式写 `SCK_PS`，检查器只接受可静态解析的写入并以该值重算；动态或冲突写入在精确时序任务中失败关闭。

## 10. 规则与错误输出

新增或规范化以下机器规则：

- `HK-GPIO-002`：输出 pin 的 `POD/PIO/POE` 模式与顺序必须兑现 PinContract；
- `HK-SYN-012`：指令写回目标必须匹配状态用途，循环必须有进展；
- `HK-SYN-013`：业务 `EQU` 必须成为单一有效来源；
- `HK-CLOCK-001`：精确时序必须区分 OSC、SCK_PS 和 SCK；
- `HK-TIME-001`：延时 cycle 结果必须落入目标容差；
- `HK-WDT-002`：喂狗循环不得掩盖无进展死循环。

现有检查器发出的规则 ID 必须全部存在于 `asm-rules.json`。规范校验器增加覆盖校验：自动化规则必须绑定测试，检查器不得发出未注册 ID。

## 11. 错误处理

- PinContract 缺失且无法从资料库确认：列入 `unresolved_inputs`，不生成候选 ASM。
- GPIO 模式或初始化顺序不符：`STATIC_CHECK_FAILED`，不进入编译。
- 无用 `EQU`：默认严格 warning，阻断 release。
- 循环无进展或指令写回目标错误：BLOCKER。
- SCK 无法派生、周期无法分析或延时超差：精确时序任务 BLOCKER。
- 编译失败：保持现有失败关闭行为，不泄露候选源码。

所有诊断必须包含 rule ID、源码行、实际证据、风险和所需修复。

## 12. 测试设计

严格执行 RED-GREEN-REFACTOR。

### RED：真实反例

把 `有问题led亮灯.txt` 的关键结构转成回归测试。在修改实现前确认当前检查器错误返回 0 findings、内置编译器能够编码。

### 单元测试

- push-pull 缺 `POD`：失败；显式清目标位且顺序正确：通过。
- open-drain 缺或错误清 `POD`：失败；显式置位：通过。
- 共享端口整口破坏：失败；位操作/RMW：通过。
- 定义未使用 `EQU`：严格模式失败；真实引用：通过。
- `DECSZ + backward JMP` 和 `INCSZ + backward JMP`：失败。
- `DECSZR/INCSZR` 写回循环：通过。
- 有 `CLRWDT` 但循环无进展：失败。
- `OSC=16 MHz`、`SCK_PS=34H` 派生 `SCK=2 MHz`。
- 原计数约 4.016 秒，对 500 ms 目标失败。
- 重算计数约 502 ms，在 1% 容差内通过。

### 集成测试

- `close-loop` 必须把 request/profile 传入静态检查器。
- 任一语义或时序 finding 阻断编译与 release。
- 合规 LED 候选完成 `doctor -> new-run -> close-loop -> release`。
- release evidence 保存 GPIO、loop 和 timing 审计摘要。

### Skill 前向测试

在全新智能体上下文中分别请求：

- 多个任意 PA 位的高有效推挽 LED 闪烁；
- 低有效 LED；
- open-drain GPIO；
- 不同 OSC 与目标延时；
- 故意要求使用 `DECSZ` 作为计数器。

成功标准是根据规则重新生成而非复制示例，源码注释使用中文，并在数秒级本地门禁后交付或失败关闭。

## 13. 迁移与兼容

- `request.json` schema 首版继续保持版本 1，新增结构化字段。
- 旧 `clock_hz` 暂时兼容并解释为 OSC；新请求和 Skill 文档只生成 `clock.osc_hz`。
- 旧字符串 pin 仅用于不需要电气证明的兼容场景；GPIO 输出 release 必须使用结构化 PinContract。
- 默认 profile/config 继续使用内置编译器和 `$PYTHON/$SKILL_ROOT`，不引入本地工具链路径。

## 14. 验收标准

- 问题源码不能再得到 0 findings 或 release。
- 合规推挽 LED 只增加必要 `POD/PIO/POE` 操作，不出现全量端口初始化。
- `DECSZ` 与 `DECSZR` 的写回差异由自动检查证明，不依赖模型记忆。
- 500 ms 结论必须显示 OSC、SCK_PS、SCK、cycles 和误差。
- 所有自动测试、规范校验、Skill 校验和闭环烟测通过。
- Codex 与 Claude Code 安装副本同步后，前向测试不再复现四类问题。
- 最终提交推送到 GitHub `main`。
