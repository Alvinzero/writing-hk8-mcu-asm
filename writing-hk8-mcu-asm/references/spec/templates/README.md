# ASM 模板说明

这些模板是“合规骨架”，不是无需确认即可量产的固件。

| 文件 | 定位 | 默认工具链 | 使用前必须修改/确认 |
|---|---|---|---|
| `minimal-main.asm` | 最小向量/主循环 | `builtin_compiler` | chip/vector/WDT |
| `gpio-driver.asm` | 独占 PA2 的 GPIO probe | `builtin_compiler` | pin ownership、极性、clock |
| `i2c-bitbang.asm` | 当前 PB7/PB6 地址 probe | `builtin_compiler` | address、电压、上拉、时序 |
| `ssd1306-table-paged.asm` | 两页 DB + 同页 sender 骨架 | `builtin_compiler`；company IDE 可选交叉验证 | 替换 consumer、MAP pair、资产 hash |
| `seven-segment-scan.asm` | 当前板固定 1234 scan | `builtin_compiler` | 同一板 mapping/电流/刷新率 |
| `hkproj.example` | company IDE project JSON | company IDE | 绝对 source/build path |
| `board-profile.example.json` | board 输入契约 | AI/CI | 所有 UNRESOLVED 字段 |
| `ai-task-request.example.json` | AI 任务输入 | AI orchestrator | task/source/acceptance |
| `ai-review-output.example.json` | AI 输出结构 | AI orchestrator | 只能填真实证据 |

使用流程：

1. 复制模板到新项目，不在规范目录直接开发。
2. 填写 board profile 和文件头；清除所有 `UNRESOLVED`。
3. 运行 `checklists/pre-generation.md`。
4. 运行 `tools/asm_static_check.py`。
5. 使用目标工具链构建，0 warnings。
6. 含 DB 时使用 `builtin_compiler` 并用 MAP/`--table-pair` 审计同页；company IDE 仅在用户明确要求交叉验证或正式工件时使用。
7. 完成烧录和实板 checklist 后，才可把状态改为 `hardware_verified`。

禁止把模板中的当前板接线升级为 HK64S825 全家族默认值。
