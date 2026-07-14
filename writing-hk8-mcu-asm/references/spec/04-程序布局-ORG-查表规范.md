# 04 程序布局、ORG 与查表规范

## 1. 地址单位：统一按 program word

HK64S825 当前基线程序空间：

```text
0x0000..0x03FF program words
1024 words
1 word = 16 bits = 2 physical bytes
maximum BIN = 2048 bytes
```

以下全部是 **word address**：

- `ORG` 参数。
- 源码 label。
- MAP 地址和 PC。
- `JMP/CALL` 目标。
- `TABL/TABH` 所在地址与表 word 地址。

只有 BIN/HxD offset 是 byte offset：

```text
MAP word 0x0100 -> BIN byte offset 0x0200
MAP word 0x03FF -> BIN byte offsets 0x07FE..0x07FF
```

适用规则：`HK-LAYOUT-001..008`、`HK-TOOLCHAIN-DB-001`、`HK-TABLE-002..010`。

## 2. 每类源码项占用多少 word

| 源码项 | program word 占用 |
|---|---:|
| 普通机器指令 | 1 |
| label / `EQU` / `END` | 0 |
| `ORG` | 0，但改变当前 word address |
| `DB B0,B1` | 1 |
| `DB` 的 N 个字节 | `ceil(N/2)`；本规范要求 N 显式为偶数 |

当前 Python 源码模块的实际缺陷是：普通指令才使 address 增加，`DB` 既不正确推进地址也不写机器码。因此含 `DB` 时不能用该模块的 BIN 做布局或烧录依据。

## 3. ORG 的工程约束

`ORG` 只用于：

1. reset/interrupt vector。
2. 为 `TABL/TABH` 建立受控 256-word page 布局。
3. 经过容量计算的协议固定位置或 boot interface。

不允许仅为了“章节看起来整齐”任意分段。

每个 `ORG` 前必须写：

```asm
; ORG PURPOSE: page-1 table and same-page sender
; RESERVED: 0x0100..0x01FF words
; CONTENT: TABLE1 + SEND_TABLE1
ORG 0x0100
```

## 4. 空洞、BIN 大小与 NOP 幻觉

公司输出按最高写入 word 地址形成镜像。`ORG` 跳过的区域通常以 `0x0000` 填充；`0x0000` 又是 `NOP` 的机器字。因此：

- 一个只有几十条指令但最后 `ORG 0x03F0` 的程序，BIN 仍接近 2048 bytes。
- “代码行很少”不等于“镜像很小”。
- 空洞如果因意外跳入，可能表现为长串 NOP，而不是立即故障。

容量公式：

```text
highest_written_word = max(all occupied words)
image_bytes = (highest_written_word + 1) * 2
hole_words = words inside image that were never explicitly occupied
```

构建报告必须同时给出 occupied words、highest word、hole words 和 image bytes。

## 5. 地址覆盖是 BLOCKER

当前源码模块存在“地址重复时仅 warning，后写值覆盖先写值”的危险路径。公司规范提升为 BLOCKER：

```asm
ORG 0x0020
TABLE0:
  DB 01H,02H          ; 占 0x0020

ORG 0x0020            ; 错误：覆盖前面的表
NOP
```

不允许通过“最终 BIN 看起来还能运行”豁免。重叠会使 label、MAP、源码意图和真实机器字失去一致性。

## 6. 范围与跳转截断

- `ORG`、occupied word、label、`JMP/CALL` target 必须位于 `0x0000..0x03FF`。
- 当前编译器可能对超 10-bit jump target warning 后执行 `target & 0x3FF`。
- 所有 range/overlap/truncation warning 在交付中都是 BLOCKER。
- `JMP/CALL` 使用 label 或 `EQU`，不能直接写数字 target；详见 [02-指令与操作数规范.md](02-指令与操作数规范.md)。

## 7. 公司 IDE 的 DB 物理编码事实

专项源码：

```asm
DB 12H,34H,56H,78H,9AH,BCH,DEH,F0H
```

公司 IDE 生成的相应 BIN bytes：

```text
34 21 78 65 BC A9 F0 ED
```

每对源码字节 `X,Y` 的观测模型：

```text
program_word = nibble_swap(X) << 8 | Y
BIN little-endian bytes = [Y, nibble_swap(X)]
```

对应工件 SHA256：

```text
11b59d7692016d7c053d5243f2e73a0020027a063ffc57f5eb8a443afa8fffa1
```

这是一条 E2 **物理工件审计规则**，不是源码数据补偿规则。

## 8. 运行时 DB 规则：原始字节，不做补偿

E1 实板确认的唯一 active 路径：

```asm
TABLE0:
  DB B0,B1

; A = table word index
MOV A,TABLE_INDEX
TABL                    ; 产生源码第一个字节 B0
CALL CONSUME_BYTE

MOV A,TABLE_INDEX       ; TABL 已覆盖 A，必须重新装载
TABH                    ; 产生源码第二个字节 B1
CALL CONSUME_BYTE
```

禁止：

- nibble swap 源字节。
- 交换 `B0/B1`。
- 按 HxD 可见顺序重排 DB。
- 先 `TABH` 后 `TABL`，除非另有新 E1 证据且建立独立格式。

历史 `hello_db_tabl_tabh_hxd_probe.asm` 属于被排除的补偿假设，不得作为模板。

## 9. Python CLI 的 DB BLOCKER

公司源码 `assembler.py` 当前行为和项目探针共同证明：

- 第一遍 `DB` 不按实际 word 数推进地址。
- 第二遍 `DB` 不写 program words。
- `asmc_compile.py` 直接调用该模块。
- `probe_db_table.asm` 在 `ORG 0x0080` 后含 DB，但 CLI BIN 只有 46 bytes，表数据不存在。

因此：

```text
source contains DB + target_toolchain=python_source_module_cli
=> BLOCKER HK-TOOLCHAIN-DB-001
```

即使 CLI 返回 `0 error(s)`，也不得进入 flash candidate。含 DB 的交付件必须由已证明支持 DB 的 company IDE 构建，并审计 MAP/BIN/HEX。

## 10. TABL/TABH 的 256-word page 约束

运行时表 word 和执行 `TABL/TABH` 的具体 instruction word 必须满足：

```text
(table_word_address >> 8) == (tabl_instruction_address >> 8)
(table_word_address >> 8) == (tabh_instruction_address >> 8)
```

也就是：

- 表在 `0x00xx`，读取它的 `TABL/TABH` 也在 `0x00xx`。
- 表在 `0x01xx`，读取函数也放在 `0x01xx`。
- 不能把一个通用 sender 放在 `0x02xx` 去读取 `0x00xx` 和 `0x01xx` 的表。

`CALL I2C_SEND` 可以跳到其他 page；受限的是 `TABL/TABH` 指令本身与目标表。

## 11. 分页布局算法

对 N 个源字节：

1. 要求 N 为偶数；word count = `N / 2`。
2. 为每个 256-word page 预留 sender、循环、可能的 vector/code 空间。
3. 每块数据不得占满整页后把 sender 推到下一页。
4. 每个块建立 `TABLE_PAIR: DATA_LABEL,SENDER_LABEL` 注释或构建检查参数。
5. 构建后以 MAP 而不是源码目测确认地址。

已验证的 64×64 / 512-byte 头像布局：

| 块 | 源字节 | words | table | sender | page |
|---|---:|---:|---:|---:|---:|
| 0 | 224 | 112 | `0x0020` | `0x0090` | `0x00` |
| 1 | 224 | 112 | `0x0100` | `0x0170` | `0x01` |
| 2 | 64 | 32 | `0x0200` | `0x0220` | `0x02` |

`224+224+64` 是当前 1K MTP 布局的已验证实例，不是所有项目的固定分块大小。新项目必须按该页剩余代码重新计算。

## 12. sender 模板契约

```asm
; TABLE_PAIR: IMAGE_DATA0,SEND_IMAGE_DATA0
; IMAGE_DATA0: even byte count, raw consumer order
ORG 0x0020
IMAGE_DATA0:
  DB 80H,35H,EEH,00H

ORG 0x0030
; IN: none
; OUT: bytes sent in DB source order
; CLOBBERS: A, 88H, 89H, I2C_SEND clobbers
SEND_IMAGE_DATA0:
  MOV A,#20H           ; table low-word address
  MOV 88H,A
  MOV A,#02H           ; 2 program words = 4 source bytes
  MOV 89H,A
SEND_IMAGE_DATA0_LOOP:
  MOV A,88H
  TABL
  CALL I2C_SEND
  MOV A,88H
  TABH
  CALL I2C_SEND
  INCR 88H
  DECSZR 89H
  JMP SEND_IMAGE_DATA0_LOOP
  RET
```

若通过 `ADD A,#TABLE_LABEL` 计算偏移，同样必须保证结果的低 8 位和 instruction page 符合目标芯片查表语义，并以实板小 marker 证明。

## 13. MAP 审计

每个含表项目必须保存 pair 列表：

```text
TABLE0:SEND_TABLE0
TABLE1:SEND_TABLE1
TABLE2:SEND_TABLE2
```

审计步骤：

1. 从 MAP 读取 table 和 sender 地址。
2. 定位 sender 内具体 `TABL/TABH` 的 word 地址。
3. 比较高 8 位 page。
4. 检查 sender 自身没有跨 `xxFF -> (xx+1)00` 边界。
5. 记录每块 bytes、words、起止地址和剩余 page 容量。

命令示例：

```powershell
python tools/asm_static_check.py main.asm `
  --toolchain company_ide `
  --map build/main.map `
  --table-pair TABLE0:SEND_TABLE0 `
  --table-pair TABLE1:SEND_TABLE1
```

## 14. marker 工件检查

DB 工件不能只看“BIN 变大了”。至少在每个表块开头放非对称 marker，并在资产生成记录中保存其预期：

```asm
DB 12H,34H,56H,78H
```

审计同时区分：

- 源码逻辑顺序：`12 34 56 78`。
- company IDE 物理 BIN 观测顺序。
- 实板 `TABL -> TABH` 消费顺序。

三者不应被压缩成一个“字节序”概念。

## 15. simulator 的已知模型缺陷

当前 `simulator.py` 的 `TABL/TABH` 路径使用：

```python
addr = self.acc & 0xFF
self.rom[addr]
```

它固定读取 page 0，无法正确证明 `0x01xx/0x02xx` 表。处置：

- page 0 小表可将 simulator 作为低等级辅助证据。
- 跨页表不得用 simulator 通过来替代 MAP 和实板。
- simulator 与 E1/E2 冲突时，按证据等级舍弃 simulator 结论。

## 16. DB 资产可追溯性

图片、字库或协议表必须记录：

- 原始资产文件名和 SHA256。
- 转换脚本和参数。
- width/height、page/row/column format、bit order。
- 生成 DB byte count 和 SHA256。
- 分块边界和每块 marker。
- 不允许 AI 静默格式化、重排或“优化”已验证 DB。

## 17. 奇数字节 DB

公司 IDE 对单条奇数 DB 的末尾填充规则仍是 `OPEN-DB-ODD`。当前强制策略：

- 每条 `DB` 显式包含偶数字节。
- 若逻辑数据为奇数，资产层显式增加有定义的 pad byte，并在 byte count 中记录。
- 不依赖编译器隐式补零、沿用下一行首字节或其他未证实行为。

## 18. 布局审查清单

- [ ] 所有地址均明确是 word 还是 byte。
- [ ] `ORG` 只用于向量或受控分页，并注明容量。
- [ ] occupied range 不越过 `0x03FF`。
- [ ] 无重叠、无 jump truncation warning。
- [ ] 报告 highest word、image bytes 和 hole words。
- [ ] 含 DB 时使用 company IDE，不使用 Python CLI 工件。
- [ ] DB 为原始消费者顺序，每条偶数字节。
- [ ] 每次 `TABH` 前重新装载 A/index。
- [ ] 每个 table/sender pair 在 MAP 中同一 256-word page。
- [ ] simulator 未被用来证明跨页查表。
- [ ] 原始资产、转换参数、byte count、hash 可追溯。
