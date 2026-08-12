# HK64S825-SH1106-1P3-I2C-PB6-PB7-E1 OLED 板级资料

本文件不是 HK64S825 或所有 1.3 英寸 OLED 的默认值。只有用户明确选择
`board_profile_id=HK64S825-SH1106-1P3-I2C-PB6-PB7-E1` 后才允许读取和采用；机器可读契约以同目录 `oled.json` 为准。

## 实板连接与电气

- OLED 为 1.3 英寸、SH1106、可见分辨率 128x64。
- `PB7=SDA`，`PB6=SCL`，两根线只供 OLED 总线使用。
- 两根线均不设置 `PB_POD`；使用内部 `PB_PPU` 上拉。
- 7-bit 地址为 `3CH`，线上写字节为 `78H`，读字节为 `79H`。
- 命令控制字节为 `00H`，数据控制字节为 `40H`。
- `OSC=16MHz`、`SCK_PS=34H`，实际 SCK 为 2MHz。
- I2C 必须 MSB first；当前实板正确移位指令是 `RLR`，不得替换为 `RLC`。
- 第 9 个时钟前释放 PB7，ACK 读取 `PB_INS`，不能读取 `PB_PIO` 输出锁存。

## SH1106 显存寻址

SH1106 内部有 132 列，当前面板只显示中间 128 列，因此可见列 `x` 对应控制器列 `x+2`。每页开始必须发送：

```text
page:        B0H..B7H
column low:  02H
column high: 10H
```

页内 byte 的 bit0 位于顶部。不得照搬 SSD1306 的 `20H/21H/22H` 水平窗口路径；每一页都要重新设置 page、低列地址和高列地址。

## 已验证初始化与方向

```text
AE D5 80 A8 3F D3 00 40 AD 8B
A1 C8 DA 12 81 80 D9 22 DB 35 A4 A6
```

方向 profile 为 `hk64s825-sh1106-1p3-pb6-pb7-a1-c8-page-lsb-top-v1`：控制器使用 `A1H+C8H`，page 源格式为 `ssd1306-page-lsb-top`，软件不做字块水平镜像，也不做垂直镜像。写完 GDDRAM 后再发送 `AFH` 开启显示。

## 正式文字的已验证布局边界

2026-08-12 用户确认 102x24、306-byte 正式文字版显示正常。该版本的 `TEXT_DATA=0x0020`，sender=`0x00C0`，`TABL/TABH=0x00D2/0x00D5`，表和 sender 都位于程序 page 0。

此前表和 sender 位于 page 1 的版本编译通过但实板全黑。这个结果只支持“本板、当前内置工具链、当前正式文字应用优先把表和 sender 放 page 0”的保守布局，不能推导成 HK64S825 所有 page 1 或跨页查表都失效。该问题以 `OPEN-SH1106-TABLE-PAGE1` 保留。

诊断查表路径时，应先发送 `AFH`，并同时写入一个已验证的立即数 marker；否则清屏后查表失败和显示未开启都会只表现为全黑，无法区分根因。
