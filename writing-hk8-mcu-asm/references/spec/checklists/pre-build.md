# ASM 构建前检查清单

> 适用：进入 target compiler 前。BLOCKER/ERROR 或 warning 未处置时不得形成 flash candidate。

## A. 源码身份

- [ ] 绝对 source path 已打印并核对。
- [ ] source SHA256 已保存。
- [ ] project/include 解析到预期文件，无旧副本串用。
- [ ] 文件角色不是 probe/check/sanity，或已明确仅做实验构建。
- [ ] `verified` baseline 未被覆盖；修改发生在新 revision。

## B. 静态规则

- [ ] 已运行 `tools/asm_static_check.py`。
- [ ] 无 `#0x..H`。
- [ ] `JMP/CALL` 只用 label/`EQU`，target 在 10-bit 范围。
- [ ] 无 Python 内部 SFR 名。
- [ ] 无未经批准的 `RET A,#K`、`CPL/CPLR`、`IDLE/STOP`。
- [ ] SRAM allocation 和 clobbers 与实际地址一致。
- [ ] ORG/ranges 无重叠、越界和无说明的大空洞。

## C. DB/TABLE 门禁

- [ ] 已扫描 source/include 是否含 `DB`。
- [ ] 含 DB 时 target toolchain 为 `company_ide`。
- [ ] 每条 DB 为显式偶数字节。
- [ ] DB 是消费者原始顺序，无 nibble/word swap 补偿。
- [ ] 每个 table 有 table/sender pair。
- [ ] 每个 `TABH` 前重新装载 A/index。
- [ ] 预计 table/sender 在同一 256-word page。
- [ ] 原始资产和生成 DB byte count/hash 匹配。

## D. Project settings

- [ ] `mcu_type` 正确。
- [ ] warning level 显示全部 warning。
- [ ] output format 包含 BIN + HEX + MAP。
- [ ] build path 唯一、可追溯，避免跨工程残留。
- [ ] compiler/IDE/source module version 已记录。
- [ ] clock/OPTION profile 与 source 假设一致。

## E. 构建预期

- [ ] 预计 highest word 和 BIN size 已计算。
- [ ] vector、INIT、关键 labels 的预计 MAP 地址已列出。
- [ ] DB marker 的预计源码/物理工件模式已列出。
- [ ] 要求 0 errors / 0 warnings。
- [ ] 构建失败时不会自动继续 flash。

## 签核

```yaml
source_sha256:
target_toolchain:
toolchain_version:
static_check_exit_code:
blockers: []
errors: []
warnings: []
approved_to_build: false
```
