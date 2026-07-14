# HK64S825 ASM 公司级规范包

> 版本：1.1.0
> 基线日期：2026-07-10
> 已实证芯片/板级基线：HK64S825 及本项目所用 OLED、四位数码管开发板
> 来源项目：`D:\hk64s8x-cli`

本目录不是“语法摘抄”，而是从 53 组 ASM/MAP/BIN/HEX、34 个工程文件、公司编译器源码、自动编译探针和实板结论中提炼出的**可执行约束包**。它同时面向人工开发、代码评审、CI 静态检查和其他 AI 智能体二次开发。

## 一眼先看四条 BLOCKER

1. **当前 Python 源码模块 CLI 不生成 `DB` 机器码。** 含 `DB` 的源码即使返回 `0 error(s)`，其 BIN 也不能作为烧录件。见 `HK-TOOLCHAIN-DB-001`。
2. **OLED/字库表按原始字节写 `DB B0,B1`，运行时 `TABL` 后 `TABH`。** 不得按 HxD/BIN 的物理排列做 nibble swap 补偿。
3. **`TABL/TABH` 与目标表 word 必须位于同一 256-word 页。** 大表按页拆块，每块旁边放自己的发送函数。
4. **ORG、标签、MAP、PC、JMP、CALL 都是 word 地址。** HK64S825 当前程序空间为 `0x0000..0x03FF`，即 1024 words / 2048 bytes。

## 快速接入

### 给人工开发者

按顺序阅读：

1. [00-规范适用范围与证据等级.md](00-规范适用范围与证据等级.md)
2. [01-HK64S825-ASM编码规范.md](01-HK64S825-ASM编码规范.md)
3. 与任务相关的专题文档
4. [07-构建-烧录-验收规范.md](07-构建-烧录-验收规范.md)
5. `checklists/` 下对应清单

### 给 AI 智能体

最小上下文集合：

- [AGENTS.md](AGENTS.md)
- [rules/asm-rules.json](rules/asm-rules.json)
- [rules/instruction-reference.json](rules/instruction-reference.json)
- [rules/register-reference.json](rules/register-reference.json)
- [rules/register-alias-policy.json](rules/register-alias-policy.json)
- [09-AI智能体生成与审查协议.md](09-AI智能体生成与审查协议.md)
- 目标硬件的 `board_profile`、原理图或已确认接线

AI 必须输出使用过的规则 ID、未确认输入、SRAM 分配、程序布局、目标工具链和验收步骤；不能只返回一段 ASM。

### 给 CI / 自动审查

    python tools/validate_spec.py .
    python -m unittest discover -s tools/tests -v
    python tools/asm_static_check.py path/to/main.asm --toolchain company_ide

含 `DB` 时若选择 `--toolchain python_source_module_cli`，检查器必须返回 BLOCKER。

## 目录

| 路径 | 用途 |
|---|---|
| `00..10-*.md` | 人类可读的规范、专题和证据说明 |
| `rules/asm-rules.json` | 70 条机器可读规则；AI/CI 的主入口 |
| `rules/asm-rules.schema.json` | 规则集 JSON Schema |
| `rules/instruction-metadata.json` | 2026-07 公司 instruction metadata 原始结构化快照 |
| `rules/instruction-reference.json` | 65 个指令变体的修正版参考与逐变体编译探针 |
| `rules/register-reference.json` | 2026-07 register metadata：407 行明细、96 个聚合寄存器及完整位描述 |
| `rules/register-alias-policy.json` | 官方 SFR 名、内存空间和 CLI 内部别名策略 |
| `checklists/` | 生成、构建、烧录、实板验收门禁 |
| `templates/` | 最小主程序、GPIO、I2C、分页查表、数码管模板 |
| `analysis/project-inventory.json` | 53 个 ASM 的逐文件统计和工件哈希 |
| `analysis/asm-inventory.csv` | 便于 Excel/BI 使用的清单 |
| `analysis/evidence-matrix.json` | 证据等级、结论来源与 OPEN 项 |
| `analysis/probe-results.json` | 65/65 指令编译探针及 DB/JMP 专项探针 |
| `analysis/source-manifest.json` | 关键输入文件 SHA256，锁定本规范基线 |
| `analysis/template-validation.json` | 4 个无 DB 模板的隔离编译结果、工件哈希及 DB 模板 BLOCKER 验证 |
| `tools/` | 规范自检、ASM 静态检查、分析快照生成脚本及回归测试 |

## 工具链能力矩阵

| 能力 | company_ide | python_source_module_cli | simulator | hardware |
|---|---:|---:|---:|---:|
| 普通指令编译 | 是 | 是，65/65 代表探针通过 | 执行子集 | 最终语义 |
| `DB` 生成 | **是，E2** | **否，BLOCKER** | 依赖 ROM 输入 | 最终读取语义 |
| `TABL/TABH` 页 0 | 可构建 | 指令可构建 | 可模拟 | 已验证 |
| `TABL/TABH` 跨页 | 可构建 | 指令可构建 | **模型错误：固定页 0** | 已验证“同页函数”方案 |
| BIN | 每 word 小端，DB 有特殊物理编码 | 每 word 小端，DB 缺失 | 不适用 | 烧录/回读 |
| 交付依据 | 真实产物 | 无 DB 项目的辅助工具 | 只作低等级辅助证据 | 冲突时最高优先级 |

## 数据基线摘要

- ASM / MAP / BIN / HEX：各 53 个
- `.hkproj`：34 个
- ASM 源码总行数：15,735
- 识别到的指令出现次数：10,667
- 含 `DB` 的 ASM：22 个；源码 DB 字节总数：4,796
- 使用 `TABL` 的 ASM：30 个；使用 `TABH` 的 ASM：25 个
- 指令元数据：65 个变体、56 个 mnemonic；规范化后 65/65 代表编译探针通过
- 2026-07 指令元数据已修复 `BTSZ/BTSNZ` 拼写；`XOR A.#K` 和 `MOV A,#K` operand type 仍由 reference 规范化
- 2026-07 寄存器元数据：407 行、96 个聚合定义；8 个 GPIO 名已统一为公司正式名
- `REG825.INC` 与寄存器元数据存在 4 个显式 OPEN：`STATUS`、`LVD/LVD1`、`LVD2`、`LVD3`
- 4 个无 DB 模板已用当前 Python source module 隔离编译：4/4 为 0 errors / 0 warnings；详见 `analysis/template-validation.json`
- 实板明确标记的最终基线：
  - `ssd1306_oled_heello_db_raw_verified.asm`
  - `ssd1306_oled_avatar_64x64_db_raw_verified.asm`

完整数字与每个文件 SHA256 见 `analysis/project-inventory.json`。

## 采用建议

- 将整个目录以只读依赖方式嵌入 AI 工程，不要只复制某一个模板。
- CI 至少执行 `tools/validate_spec.py` 和 `tools/asm_static_check.py`。
- 每块新硬件建立独立 `board_profile`，不要把本项目 OLED/数码管接线升级为整个 HK64S825 家族的默认值。
- 工具链、XLSX、IDE 或芯片版本变化时，重跑基准探针并升级本规范版本。

## 已知 OPEN

- IDE DB 半字节物理变换与芯片 ROM 数据总线之间的底层原因。
- `RET A,#K` 的正式硬件语义。
- `CPL/CPLR` 的确切语义。
- 单个 `DB` 指令为奇数字节时的 IDE 填充规则。
- `REG825.INC` 的 `LVD@24H` 与 metadata 的 `LVD1@24H/LVD2@26H/LVD3@27H` 的正式映射。

这些 OPEN 不阻塞已验证路径，但禁止 AI 自行补全结论。详见 [10-证据索引与待确认事项.md](10-证据索引与待确认事项.md)。
