# USER-4DIGIT-MIXED-PA-PB 数码管板级草案

此 profile 只记录用户已经提供的接线、视觉顺序和时钟。状态为 `draft`，不得作为 `user_confirmed_profile` 自动补齐正式请求。

已记录内容：

- A-G/DP 对应 PB7-PB0，PB 全口由段线独占。
- 视觉左起依次为 PA5、PA6、PA2、PA3。
- PA5/PA6 为共阴、低电平选通；PA2/PA3 为共阳、高电平选通。
- OSC 为 16 MHz，SCK_PS 为 34H。

升级为 `ready` 前仍须确认：

- 段线是否存在外部反相。
- 位选是否存在外部反相；若有，逐位记录。
- 限流方式及阻值/峰值电流约束。
- HK64S825 GPIO 和外部驱动路径是否满足峰值驱动能力。

确认后将 JSON 中对应状态改为 `confirmed`，清空 `unresolved_inputs`，再由用户明确选择 `board_profile_id=USER-4DIGIT-MIXED-PA-PB`。
