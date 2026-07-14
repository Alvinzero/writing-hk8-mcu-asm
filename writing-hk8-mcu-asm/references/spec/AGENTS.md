# AGENTS.md — HK64S8x ASM 智能体约束

本文件用于任何生成、修改、审查、构建或诊断 HK64S8x ASM 的 AI 智能体。除非上级系统指令另有规定，以下 `MUST` 规则具有交付阻断效力。

## 1. 启动协议

开始工作前必须读取：

1. `rules/asm-rules.json`
2. `rules/instruction-reference.json`
3. `rules/register-reference.json`
4. `rules/register-alias-policy.json`
5. 与任务相关的专题文档和 checklist
6. 用户提供的芯片型号、板级接线、供电、时钟、外设地址/极性、目标工具链

若缺少任何会影响硬件安全或行为的参数，不得猜测。把它列入 `unresolved_inputs`；如果会影响烧录结果，停止在“可审查草案”，不得宣称可烧录。

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

- 禁止用当前 `python_source_module_cli` 构建含 `DB` 的可烧录工件。
- 禁止对 OLED/字库 `DB` 做 nibble swap、word swap 或依据 BIN 的补偿。
- 禁止让一个跨页通用 `TABL/TABH` 函数读取多个 256-word 页。
- 禁止 `JMP/CALL` 直接使用数字字面量；使用标签或 `EQU` 符号。
- 禁止接受任何地址覆盖、跳转截断或未知指令 warning。
- 禁止把 `PA_PU/PB_OE` 等 Python 模块内部名写进公司 IDE 交付源码；使用 `REG825.INC` 的 `PA_PPU/PB_POE` 等正式名。
- 禁止自行把 metadata 的 `LVD1/LVD2/LVD3` 当作 company IDE 正式符号；`LVD/LVD1` 冲突和 `LVD2/LVD3` 缺失仍为 OPEN。
- 禁止把 SFR 二次 `EQU` 成业务别名来规避解析问题。
- 禁止重用同一 SRAM 地址同时保存长期状态和 scratch。
- 禁止把 `RET A,#K`、`CPL/CPLR` 用于正式关键逻辑，除非有新 E1 证据并升级规则。
- 禁止仅以“编译成功”“MTP verify 成功”或“模拟器通过”宣称功能完成。
- 禁止直接复制 `probe/check/sanity` 文件作为正式模板。
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

## 5. 生成工作流

1. 输出 `resolved_inputs` 和 `unresolved_inputs`。
2. 选择目标工具链；若含 `DB`，目标构建器必须是 `company_ide`。
3. 列出适用规则 ID，至少包含所有 BLOCKER。
4. 先设计：向量、连续代码、ORG 段、DB 块、同页 sender、SRAM 分区。
5. 生成最小非对称 probe；验证后再扩展完整功能。
6. 运行静态检查：

       python tools/asm_static_check.py main.asm --toolchain company_ide

7. 构建后检查 0 warning、MAP、BIN size/hash、DB marker、表/函数同页。
8. 烧录后按硬件 checklist 验收。

## 6. 修改工作流

修改现有代码前：

- 判定文件角色：`verified`、`probe`、`example`、`production`。
- 对 `verified` 文件优先复制为新实验文件，不在原文件试错。
- 对 DB、ORG、ACK 序列、COM 映射的修改必须给出 before/after、规则 ID、影响地址和回归步骤。
- 不得顺手格式化或重排大表，除非任务明确要求且 hash/byte count 回归通过。

## 7. 审查输出格式

AI 最终输出至少包含：

    status: draft | buildable | flash_candidate | hardware_verified
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
- `buildable`：静态/编译通过，但未完成工件审计。
- `flash_candidate`：工件审计通过，可以在受控环境烧录。
- `hardware_verified`：完成规定实板验收并保存证据。

不得从 `buildable` 直接跳到 `hardware_verified`。

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
