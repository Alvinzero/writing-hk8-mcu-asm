# 实板功能验收清单

> 只有完成适用项并保存 E1 证据，状态才可升级为 `hardware_verified`。

## A. 会话身份

- [ ] board ID/revision、chip/revision 已记录。
- [ ] source/build/bin hashes 与 flash session 一致。
- [ ] programmer/port/verify result 已记录。
- [ ] clock、OPTION、supply、环境条件已记录。
- [ ] 照片、波形、日志使用唯一 session ID 命名。

## B. 通用启动与安全

- [ ] 上电电压、电流在预期范围，无异常发热。
- [ ] reset/startup 行为符合预期。
- [ ] GPIO 在初始化前后均无危险毛刺或总线争用。
- [ ] WDT/主循环 heartbeat 符合设计。
- [ ] 掉电重启和重复上电可恢复。

## C. I2C（适用时）

- [ ] idle SDA/SCL 均为 high。
- [ ] START/STOP 边沿正确。
- [ ] 7-bit address 与 wire byte 对应正确。
- [ ] byte 为 MSB first。
- [ ] 第 9 clock 前 SDA output driver 已释放。
- [ ] 真实 ACK 为 low；NACK 路径有可观察处置。
- [ ] tLOW/tHIGH/setup/hold 和总线频率实测合规。
- [ ] 无持续争用、异常电平或波形过冲。

## D. SSD1306/OLED（适用时）

- [ ] 强制全亮层通过。
- [ ] 全屏 range + 1024×FF GDDRAM 全白通过。
- [ ] 00/FF 条纹通过。
- [ ] 非对称 marker 顺序通过。
- [ ] 单列 bit pattern 确认 bit0 top 和方向。
- [ ] 小字符通过。
- [ ] 目标图片/字库通过。
- [ ] 大表每块 table/sender 与 MAP 同页。
- [ ] 长时间刷新无偶发 NACK/花屏。

## E. 四位数码管（适用时）

- [ ] all-off 确认无任何位亮。
- [ ] 单独开启 COM0..COM3，记录物理位置和极性。
- [ ] A..G/DP 逐段 mapping 正确。
- [ ] 固定 `1234` 从左到右正确。
- [ ] 共阴/共阳两套段码全部数字 0..9 正确。
- [ ] 扫描无明显串影、全亮或亮度不均。
- [ ] frame rate/duty/current 实测合规。
- [ ] `0009->0010`、`0099->0100`、`0999->1000`、`9999->wrap` 正确。
- [ ] 长期运行无 `0101/0202` 类状态串扰。

## F. 异常与恢复

- [ ] NACK/外设缺失时程序进入定义状态。
- [ ] WDT/reset 后能恢复。
- [ ] 电压边界或快速掉电重启按产品要求测试。
- [ ] 用户操作/输入边界不会破坏 SRAM 或锁死扫描。
- [ ] 失败项有最小复现、规则 ID、波形/日志和 owner。

## G. 状态判定

```yaml
status: hardware_verified
session_id:
board_profile:
source_sha256:
bin_sha256:
passed_items: []
failed_items: []
deviations: []
evidence_files: []
reviewer:
date:
```

若有任何适用 failed item 或未批准 deviation，`status` 不能填写 `hardware_verified`。
