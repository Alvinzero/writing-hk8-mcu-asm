# ASM 生成前检查清单

> 适用：人工或 AI 新建/大改 HK64S825 ASM。任一 BLOCKER 未确认时，输出状态保持 `draft`。

## A. 任务与证据

- [ ] 任务 ID、功能目标、成功标准已写明。
- [ ] 已读取 `AGENTS.md` 和 `rules/*.json`。
- [ ] 已列出全部适用 BLOCKER/ERROR rule IDs。
- [ ] 已区分 E1/E2/E3/E4、注释和推断。
- [ ] 已识别 source 文件角色：verified / production / probe / generated asset。
- [ ] protected 文件和 DB hash 已锁定。

## B. 芯片与工具链

- [ ] chip family/model/revision 已确认。
- [ ] program capacity、SRAM range、vector 地址已确认。
- [ ] target toolchain 已显式选择。
- [ ] 若会使用 `DB`，toolchain 为 `company_ide`。
- [ ] compiler/IDE/version 与规范基线一致；否则已安排重跑探针。
- [ ] OPTION/clock/WDT/LVR 配置已确认或列为阻断输入。

## C. Board profile

- [ ] board/revision/serial or fixture 已确认。
- [ ] supply voltage、level compatibility、上电顺序已确认。
- [ ] 每个使用 pin 的 port/bit、方向、上下拉、开漏、默认态、有效电平已确认。
- [ ] port 共享和 ownership 已定义。
- [ ] 不会用整寄存器写破坏其他模块 pin。

## D. 外设参数

### I2C/OLED

- [ ] SDA/SCL、外部上拉、目标频率已确认。
- [ ] 7-bit address 与 wire write/read byte 均已记录。
- [ ] controller、resolution、window offset、orientation 已确认。
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

## F. 最小 probe 与验收

- [ ] 已设计一个非对称最小 probe。
- [ ] 已定义静态检查和 compiler 命令。
- [ ] 已定义 MAP/BIN/HEX 审计项。
- [ ] 已定义烧录前安全检查。
- [ ] 已定义实板 expected/actual 和测量证据。
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
