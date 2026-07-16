# AGENTS.md — HK64S825 ASM 智能体约束

本文件用于任何生成、修改、审查、构建或诊断 HK64S825 ASM 的 AI 智能体。除非上级系统指令另有规定，以下 `MUST` 规则具有交付阻断效力。

## 1. 启动协议

开始工作前必须读取 `AGENTS.md` 的相关段落，并挂载以下可检索资源：

1. `rules/asm-rules.json`
2. `rules/instruction-reference.json`
3. `rules/register-reference.json`
4. `rules/register-alias-policy.json`
5. 与任务相关的专题文档和 checklist
6. 用户已确认的芯片型号和当前功能中无法从资料库解析的必要缺口

不得整份加载约 892 KB 的 `register-reference.json`，也不得把上述大型 JSON 整包注入上下文。只按 mnemonic、SFR、rule ID 和当前功能章节进行结构化检索；静态检查器负责执行全部适用门禁。

资料库已知参数不得重复追问。PinContract 只在任务使用 GPIO 时要求；ClockContract 只在任务依赖时序时要求。缺口以 A/B/C/D 选项询问；会影响当前源码、电气安全或编译正确性时列入 `unresolved_inputs`，否则留作可选硬件阶段的 `open_items`。

## 2. 证据裁决

冲突时严格使用：

    实板 E1
    > 公司 IDE 真实产物 E2
    > 当前编译器源码 E3
    > 自动测试/编译探针 E4
    > 模拟器
    > 项目注释 E5 / 推断 E6

不得因为某个 BIN 看起来“更符合常规字节序”而推翻实板结论。

## 3. 绝对禁止

- 禁止用已退休的 `python_source_module_cli` 构建含 `DB` 的工件；`builtin_compiler` 支持 `DB` 并可完成编译 release。
- 禁止对 OLED/字库 `DB` 做 nibble swap、word swap 或依据 BIN 的补偿。
- 禁止让一个跨页通用 `TABL/TABH` 函数读取多个 256-word 页。
- 禁止 `JMP/CALL` 直接使用数字字面量；使用标签或 `EQU` 符号。
- 禁止接受任何地址覆盖、跳转截断或未知指令 warning。
- 禁止把 `PA_PU/PB_OE` 等 Python 模块内部名写进公司 IDE 交付源码；使用 `REG825.INC` 的 `PA_PPU/PB_POE` 等正式名。
- 禁止自行把 metadata 的 `LVD1/LVD2/LVD3` 当作 company IDE 正式符号；`LVD/LVD1` 冲突和 `LVD2/LVD3` 缺失仍为 OPEN。
- 禁止把 SFR 二次 `EQU` 成业务别名来规避解析问题。
- 禁止定义未使用的业务 `EQU`，或定义后继续使用同值魔数形成两个来源。
- 禁止重用同一 SRAM 地址同时保存长期状态和 scratch。
- 禁止把 `DECSZ/INCSZ` 当作写回 R 的持久计数指令；写回目标必须来自指令表。
- 禁止省略输出 pin 的 `POD` drive 配置，或批量初始化与任务无关的端口属性。
- 禁止把 OSC 频率直接当作实际 SCK 计算精确延时。
- 禁止把 `RET A,#K`、`CPL/CPLR` 用于正式关键逻辑，除非有新 E1 证据并升级规则。
- 禁止仅以“编译成功”“MTP verify 成功”或“模拟器通过”宣称功能完成。
- 禁止直接复制 `probe/check/sanity` 文件作为正式模板。
- 禁止复制 templates/example/sample ASM 作为候选源码；必须根据规则新写。
- 禁止扫盘、遍历本机目录或猜测 IDE/CLI 路径；默认使用 Skill 内置编译器。
- 禁止静默修改文件名含 `verified` 的基线或大块 DB 数据。

## 4. 固定语义

### 4.1 地址

- 程序空间：`0x0000..0x03FF` words。
- `ORG`、标签、MAP、PC、JMP、CALL：word 地址。
- BIN：每 word 2 bytes；普通机器字按小端输出。
- MAP `0x0100` 对应 BIN byte offset `0x0200`。

### 4.2 DB 与查表

对源码：

    DB B0,B1

运行时必须：

    MOV A,index
    TABL
    CALL CONSUME_BYTE
    MOV A,index
    TABH
    CALL CONSUME_BYTE

`TABL/TABH` 指令地址和目标表 word 地址必须满足：

    (instruction_word_address >> 8) == (table_word_address >> 8)

大表按页拆块。64×64 / 512-byte 已验证布局为 224 + 224 + 64 bytes，但新布局仍必须由 MAP 验证。

### 4.3 数据空间

- SFR：`0x00..0x7F`。
- SRAM：`0x80..0xBF`。
- OPTION：独立配置/烧录空间，不是运行时 RAM。
- 正式文件头必须有 SRAM allocation table 和每个子程序的 clobbers。
- 位字段语义查 `register-reference.json`；交付源码名称查 `REG825.INC`，两者冲突时停止并上报。

### 4.4 GPIO、循环与时钟

- 推挽输出清目标 `POD`，开漏输出置目标 `POD`；随后预装安全 `PIO`，最后打开 `POE`。
- `DECSZ/INCSZ` 写 A；`DECSZR/INCSZR` 写回 R。循环必须证明状态进展，`CLRWDT` 不得掩盖死循环。
- 精确延时从 OSC、SCK_PS、实际 SCK 和指令 cycles 推导。默认 `SCK_PS=34H` 时，16 MHz OSC 对应 2 MHz SCK。

## 5. 生成工作流

1. 输出 `resolved_inputs` 和 `unresolved_inputs`。
2. 默认选择 `builtin_compiler`；只有用户明确要求公司 ASMC 交叉验证时使用外部 adapter，不得在本机搜索工具。若目标能力无法覆盖当前语法，失败关闭并报告。
3. 按 rule ID/scope/tags 结构化查询并列出当前任务适用的 active BLOCKER/ERROR；不得整包加载规则 JSON。
4. 先设计：向量、连续代码、ORG 段、DB 块、同页 sender、SRAM 分区。
5. 根据规则新写候选源码；禁止把示例或 probe 改名后复用。
6. 运行静态检查：

       python scripts/hk8asm.py close-loop --run-dir .hk8asm/run-id

7. 静态检查和内置目标编译 0 warning 后执行 release，并保存源码/产物/evidence hash。
8. 编译 release 不要求烧录、回读或实板验收；只有用户明确要求时才继续硬件 checklist。

## 6. 修改工作流

修改现有代码前：

- 判定文件角色：`verified`、`probe`、`example`、`production`。
- 对 `verified` 文件优先复制为新实验文件，不在原文件试错。
- 对 DB、ORG、ACK 序列、COM 映射的修改必须给出 before/after、规则 ID、影响地址和回归步骤。
- 不得顺手格式化或重排大表，除非任务明确要求且 hash/byte count 回归通过。

## 7. 审查输出格式

AI 最终输出至少包含：

    status: draft | released | hardware_verified
    target_chip:
    board_profile:
    target_toolchain:
    source_files:
    constraints_used: [HK-...]
    unresolved_inputs: []
    sram_allocation:
    program_layout:
    build_result:
    artifact_manifest:
    warnings: []
    hardware_acceptance:
    open_items: []

状态含义：

- `draft`：仍有阻断输入。
- `released`：静态检查、批准编译器和 hash 门禁通过，允许交付 ASM；不代表实板通过。
- `hardware_verified`：完成规定实板验收并保存证据。

不得把 `released` 直接描述为 `hardware_verified`。

## 8. 专项规则

### I2C / SSD1306

- 明确 7-bit 地址与线上的 8-bit write/read byte。
- 第 9 个 ACK 时钟前释放 SDA 输出使能。
- “全亮”链路测试必须真正向 GDDRAM 写 1024 个 `FFH`；`A5H/AFH` 不是唯一证据。
- SSD1306 数据为 8 pages × 128 columns；每 byte 纵向 8 pixels，LSB 在页顶部。

### 当前四位数码管板

- 段：`PB7=A ... PB1=G, PB0=DP`。
- COM：`PA2=COM0, PA3=COM1, PA5=COM2, PA6=COM3`。
- COM0/1 共阳高有效；COM2/3 共阴低有效。
- 左到右：`COM2, COM3, COM0, COM1`。
- 全关：`PA_PIO=60H`。
- 扫描：全关 → 写段码 → 开当前 COM → 延时 → 全关。

以上只是当前板级 profile；换板必须重新确认。

## 9. 自检

完成前运行：

    python tools/validate_spec.py <spec-root>
    python tools/asm_static_check.py <asm> --toolchain <id> --map <map-if-available>

如果检查器和人工结论冲突，不得静默忽略；按证据等级分析并记录例外。
