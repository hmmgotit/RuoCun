# 01 可选受管 Shell 沙箱

Type: task
Status: claimed
Blocked by: none
用户体验验收: pending

## 验收

- 合成启动验证默认关闭、`--sandbox` 开启、项目和旧环境开关无法改变选择，以及 `/doctor` 与运行时一致。
- 验证关闭时无 MXC 也能运行，开启时缺后端拒绝实际启动。Bash 前后台与 Monitor 保留启动确认与取消，子代理继承现行模式。
- 已分离 Windows 执行按签名接回，保留工具记录和输出。隔离状态按原计划显示，旧取消与未决状态可追溯。
- 两条线独立构包及干净安装，核对制品版本和摘要。正式线发布前确认原生 Windows 与 POSIX 适配结果。

## Comments

2026-11-26 用户确认[规格](../spec.md)，正式实施分支为 `codex/release-v2.2.0`。
