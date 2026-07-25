# HK64S825-DEFAULT 数码管板级资料

本文件不是 HK64S825 芯片默认值。只有用户明确确认 `board_profile_id=HK64S825-DEFAULT` 后才允许读取和采用；未确认开发板时不得用本文件补齐问题。

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

该 profile 记录了接线、逻辑极性和物理位序，不自动证明当前客户板的限流、电流能力、OSC 或外部驱动器与它相同。用户选择该 profile 后，仍需确认当前任务依赖但 profile 没有记录的板级字段。
