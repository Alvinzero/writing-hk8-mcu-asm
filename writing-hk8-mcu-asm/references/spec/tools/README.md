# 工具使用说明

本目录提供规范包自检、ASM 静态审查和分析快照重建工具。普通 ASM 由 Skill 内置编译器完成编译 release；company IDE 工件审计、受控烧录和实板 E1 验收只在用户明确要求相应后续阶段时执行。

## 1. 规范包自检

```powershell
python tools/validate_spec.py <spec-root>
python tools/validate_spec.py <spec-root> --json
```

检查范围包括：必需文件、UTF-8、JSON/Schema、82 条规则、65 个指令探针、65 条 instruction metadata、407 行/96 个 register metadata、INC/metadata OPEN、Markdown 相对链接、模板源码哈希，以及模板静态检查的正反例。

退出码：

- `0`：规范包自检无错误。
- `2`：缺文件、结构错误、链接错误、规则/探针异常、模板证据过期或静态检查异常。

## 2. ASM 静态检查

```powershell
python tools/asm_static_check.py main.asm --toolchain company_ide
python tools/asm_static_check.py main.asm `
  --toolchain company_ide `
  --map build/main.map `
  --table-pair TABLE0:SEND_TABLE0 `
  --json
```

表项目也可在源码中声明：

```asm
; TABLE_PAIR: TABLE0,SEND_TABLE0
```

检查器比较的是表地址与 sender 内实际 `TABL/TABH` 指令地址，而不只比较 sender 起始标签；因此能发现 sender 从页尾跨页的情况。MAP 符号行支持：

```text
TABLE0             0x0020    32
SEND_TABLE0        0x0090    144
```

退出码：

- `0`：无 BLOCKER/ERROR；默认允许 WARNING。
- `1`：仅有 WARNING 且使用了 `--strict-warnings`。
- `2`：存在 BLOCKER 或 ERROR。

含 `DB` 源码选择 `python_source_module_cli` 时必须返回 `HK-TOOLCHAIN-DB-001` 和退出码 `2`。

## 3. 重建分析快照

```powershell
python tools/build_analysis_snapshot.py `
  --repo <authorized-toolchain-repo> `
  --compiler-root <authorized-compiler-root> `
  --instruction-metadata <instruction-metadata.json> `
  --register-metadata <register-metadata.json> `
  --spec-root <spec-root> `
  --generated-at 2026-07-10T15:00:00+08:00
```

`--repo` 和 `--compiler-root` 必填。两个 metadata 参数省略时优先使用规范包内快照，再回退源仓库；更新公司 JSON 时应显式传入新路径。`--spec-root` 默认是脚本上一级目录；`--generated-at` 省略时使用当前本地时区时间。运行前应归档已发布规范，因为该工具会更新指定规范根目录下的分析快照和机器参考文件。

## 4. 回归测试

```powershell
python -m unittest discover -s tools/tests -v
```

测试覆盖 DB 工具链阻断、地址重叠/越界、十六进制混搭、数字跳转、内部 SFR 名、`RET A,#K`、`TABH` 重载 A、实际查表指令跨页、规则重复、metadata 回退、GPIO 正式名、链接断裂和模板证据过期。

规则含义和证据边界以 [../README.md](../README.md)、[../AGENTS.md](../AGENTS.md) 和 [../rules/asm-rules.json](../rules/asm-rules.json) 为准。
