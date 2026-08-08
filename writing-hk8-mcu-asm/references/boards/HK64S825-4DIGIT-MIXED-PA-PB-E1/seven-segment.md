# HK64S825-4DIGIT-MIXED-PA-PB-E1 数码管板级资料

本文件不是 HK64S825 芯片默认值。只有用户明确确认 `board_profile_id=HK64S825-4DIGIT-MIXED-PA-PB-E1` 后才允许读取和采用；未确认开发板时不得用本文件补齐问题。

## 段线

| GPIO | 段 |
|---|---|
| `PB7` | A |
| `PB6` | B |
| `PB5` | C |
| `PB4` | D |
| `PB3` | E |
| `PB2` | F |
| `PB1` | G |
| `PB0` | DP |

## 位选

| GPIO | 逻辑名 | 类型 | 选通 | 关闭 |
|---|---|---|---|---|
| `PA2` | COM0 | 共阳 | high | low |
| `PA3` | COM1 | 共阳 | high | low |
| `PA5` | COM2 | 共阴 | low | high |
| `PA6` | COM3 | 共阴 | low | high |

视觉从左到右为 `COM2, COM3, COM0, COM1`。全部关闭时四个位选目标 bit 的组合值为 `60H`。PB0..PB7 全部由段线独占；PA 只拥有 PA2、PA3、PA5、PA6，必须保留其他 bit。

同目录 `seven-segment.json` 是供受限生成器读取的机器 profile，记录了用户确认的接线、16 MHz OSC、`SCK_PS=34H`、无外部反相、逐段限流和峰值驱动能力。只有用户明确选择本 ID 后才能采用。

该 JSON 还绑定了 2026-08-07 的 E1 回执：用户确认 `11:11` 倒计时正常显示，周期审计为 `2,000,023` cycles、`1,000,011.5 us`、误差 `0.00115%`。此证据范围仅为“正常显示”，不代表烧录、回读、电流裕量或全部硬件验收均已完成。

旧 ID `HK64S825-DEFAULT` 已弃用，由 `references/boards/aliases.json` 映射到本 canonical profile；旧 ID 不再出现在可选板目录列表中。
