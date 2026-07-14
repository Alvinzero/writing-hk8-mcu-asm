# 烧录前检查清单

> 适用：已完成构建和工件审计，准备连接真实板卡。任何身份、供电、镜像或 OPTION 不确定时停止。

## A. Flash candidate 证据

- [ ] 状态已达到 `flash_candidate`。
- [ ] build 为 0 errors / 0 warnings。
- [ ] source/project/MAP/BIN/HEX/build log 已归档。
- [ ] source 到 artifact 的 manifest 完整。
- [ ] MAP range、ORG、holes、table pages 审计通过。
- [ ] DB marker、byte count、BIN/HEX hash 审计通过。

## B. 目标与安全

- [ ] target board/revision/serial/工位已确认。
- [ ] chip model/revision 与 project 一致。
- [ ] VDD、GND、接口方向、线序和限流正确。
- [ ] 外设和负载处于安全状态。
- [ ] 有断电/恢复步骤和已知 good 镜像。
- [ ] 不会误烧录其他连接设备。

## C. 镜像身份

- [ ] 将要烧录的 BIN **绝对路径** 已显示。
- [ ] BIN size 与 manifest 一致且不超过 2048 bytes（当前 HK64S8101 基线）。
- [ ] BIN SHA256 与审批值逐字符一致。
- [ ] state.last_build 不指向旧 workspace/旧 source。
- [ ] build ID、toolchain version、时间和操作人已记录。

## D. OPTION 与烧录器

- [ ] OPTION profile 由独立流程确认。
- [ ] 明确本次烧录是否包含/不包含 OPTION。
- [ ] oscillator/WDT/LVR 等不会造成不可恢复或危险状态。
- [ ] programmer、firmware、COM port、baudrate 已记录。
- [ ] verify/readback 的范围和算法已明确。

## E. 烧录后计划

- [ ] 不把 verify success 当功能完成。
- [ ] 已打开 hardware acceptance checklist。
- [ ] 已准备逻辑分析仪/示波器/照片或日志采集。
- [ ] 先执行最小安全 probe，再执行完整业务。
- [ ] 失败时只改变一个变量并保留失败证据。

## 签核

```yaml
board_id:
chip:
bin_path:
bin_size:
bin_sha256:
option_profile:
programmer:
port:
operator:
approved_to_flash: false
```
