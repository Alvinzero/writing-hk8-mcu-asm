# HK64S825 板级参数确认门禁设计

## 目标

让 Skill 在只确认芯片型号时只采用 HK64S825 芯片级事实。GPIO 接线、外设型号、极性、驱动反相、物理位序、时钟和共享关系等板级事实，必须由用户直接提供或由用户明确选择已注册 board profile 后才能进入候选 ASM 生成和 `new-run`。

## 边界

- 芯片级事实：指令集、SFR、程序空间、RAM、复位寄存器语义和内置编译器能力，可从 `references/spec/` 与芯片 profile 自动使用。
- 板级事实：GPIO 映射、外设地址、共阳/共阴、有效电平、晶体管反相、物理顺序、OSC 来源、共享 GPIO 和限流/驱动方式，不得从“默认板”静默推断。
- 功能事实：显示内容、计数范围和刷新目标，可从用户请求解析；无法可靠解析时继续询问。

## 请求契约

硬件相关请求增加 `input_provenance`：

```json
{
  "input_provenance": {
    "board": "user_confirmed_profile",
    "pins": "user_confirmed_profile",
    "clock": "user_provided"
  }
}
```

`board` 与 GPIO `pins` 只接受 `user_provided` 或 `user_confirmed_profile`。使用时钟时，`clock` 也必须来自这两个来源。缺少板级确认返回 `BOARD_PROFILE_UNCONFIRMED`；缺少引脚或时钟来源返回 `BOARD_INPUT_UNCONFIRMED`。来源字段是交付声明，Skill 明确禁止模型代替用户填写确认。

内置编译配置只描述工具链，不再绑定 `HK64S825-4DIGIT-MIXED-PA-PB-E1`。外部烧录/回读配置仍可带 `board_id`，此时必须与请求板号一致。

## 数码管流程

用户没有明确 board profile 时，第一轮最多询问三项：驱动方式、是否有原理图/引脚表、数码管位数与极性是否已知。GPIO 直驱后继续收集 A-G/DP、每个 COM 与物理顺序、有效电平/外部反相、时钟和共享/限流信息。

未知接线不得生成正式显示 ASM。用户明确要求硬件探测时，可以另建逐段逐位 probe；probe 结果经用户确认后形成新的 board profile。

## 资料隔离

`references/spec/06-数码管动态扫描规范.md` 只保留通用算法和必需输入。当前板映射迁入 `references/boards/HK64S825-4DIGIT-MIXED-PA-PB-E1/seven-segment.md`，仅在用户明确选择该 profile 后读取。删除与常见用户请求完全重合的固定 `1234` ASM 模板，避免评测答案泄漏。

## 验证

- 未确认开发板的数码管请求不能创建 run。
- 已确认 profile 或用户完整提供板级参数时可以创建 run。
- 缺少 pins/clock 来源时失败关闭。
- 内置 config 可用于任意已确认 board id。
- Skill 文本、eval 和规范共同要求先澄清后生成。
- 全量 Python 测试、Skill 结构校验和 `git diff --check` 全部通过。
