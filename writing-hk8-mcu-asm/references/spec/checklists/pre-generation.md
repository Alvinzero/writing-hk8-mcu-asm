# ASM 生成前检查清单

> 适用：人工或 AI 新建/大改 HK64S825 ASM；HK64S8101 按已确认 HK64S825 别名处理。任一 BLOCKER 未确认时，输出状态保持 `draft`。

## A. 任务与证据

- [ ] 任务 ID、功能目标、成功标准已写明。
- [ ] 已读取 `AGENTS.md` 相关段落，并只按 mnemonic、SFR、rule ID 和当前功能章节结构化检索 `rules/*.json`。
- [ ] 不得整份加载约 892 KB 的 `register-reference.json`，未把大型规则 JSON 整包注入上下文。
- [ ] 已列出全部适用 BLOCKER/ERROR rule IDs。
- [ ] 已区分 E1/E2/E3/E4、注释和推断。
- [ ] 已识别 source 文件角色：verified / production / probe / generated asset。
- [ ] protected 文件和 DB hash 已锁定。

## B. 芯片与工具链

- [ ] 第一条回复已确认芯片型号为 `HK64S825`，或已确认别名 `HK64S8101` 并按 HK64S825 规则继续。
- [ ] program capacity、SRAM range、vector 地址已确认。
- [ ] 默认使用 Skill 内置编译器；只有用户明确要求时才选外部 ASMC。
- [ ] 含 DB 时不得使用 `python_source_module_cli`；默认 `builtin_compiler` 支持 DB，可完成编译 release。
- [ ] company IDE 仅在用户明确要求交叉验证或公司正式工件时使用，不阻断默认 release。
- [ ] 未扫盘、遍历本机目录或猜测 IDE/CLI 路径。
- [ ] ClockContract 只在任务依赖时序、WDT 或低功耗时要求；所需字段已由 profile 解析或列为缺口。

## C. 条件式 PinContract

- [ ] PinContract 只在任务使用 GPIO 时要求。
- [ ] 已优先采用资料库中的当前板级 pin、极性和外设规则，没有重复询问。
- [ ] 每个输出 pin 的 port/bit、方向、drive、默认态、有效电平已解析。
- [ ] port 共享和 ownership 已定义。
- [ ] 推挽清目标 `POD`、开漏置目标 `POD`，安全 `PIO` 先于 `POE`。
- [ ] 不会用整寄存器写破坏其他模块 pin，也没有批量初始化无关属性。

## D. 外设参数

### I2C/OLED

- [ ] SDA/SCL、外部上拉、目标频率已确认。
- [ ] 7-bit address 与 wire write/read byte 均已记录。
- [ ] controller、resolution、window offset、orientation 已确认。
- [ ] OLED 亮屏默认使用已验证最小初始化：`PB_PPU`、`PB_POE`、`PB_PIO`；额外 `PB_POD/PB_INS/PB_PPD/PB_PSL` 只有明确板级证据才加入。
- [ ] ACK 采样必须读 `PB_INS`，不得读 `PB_PIO` 输出锁存。
- [ ] 第一条 OLED 命令或数据事务前已有上电稳定延时。
- [ ] 已复核 `BTSZ R,b` 是 bit=0 跳过下一条，I2C bit 发送分支方向未反写。
- [ ] GDDRAM 全屏填充为 1024 字节；8 位计数器实现时低字节 `00H` 配合高计数 `04H` 或等价计数已说明。
- [ ] clock stretching、NACK/retry、bus recovery 策略已决定。

### 数码管

- [ ] A..G/DP 段线 mapping 已确认。
- [ ] 每个 COM pin、极性、物理位序已确认。
- [ ] all-off 电平和值已确认。
- [ ] 限流、电流和扫描 duty 约束已确认。

## E. 资源设计

- [ ] program layout 已画出：vector、连续 code、ORG、DB、same-page sender。
- [ ] 所有地址单位标明 word/byte。
- [ ] SRAM allocation 已区分 persistent/scratch/handoff/isr。
- [ ] 每个函数有 IN/OUT/CLOBBERS/REENTRANT/TIMING 草案。
- [ ] 最大嵌套 CALL/ISR 影响已评审。
- [ ] DB 原始资产、格式、byte count/hash 和转换参数已定义。

## F. 编译 release 与可选后续阶段

- [ ] 已定义静态检查和 compiler 命令。
- [ ] 已定义 MAP/BIN/HEX 审计项。
- [ ] 编译 release 不要求烧录、回读或实板验收。
- [ ] 只有用户明确要求硬件阶段时，才收集板卡、供电、烧录器和测量条件。
- [ ] 未确认项已进入 `unresolved_inputs`，没有静默猜测。

## 签核

```yaml
status: draft
owner:
reviewer:
date:
constraints_used: []
unresolved_inputs: []
approved_to_generate: false
```
