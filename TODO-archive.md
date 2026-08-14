# TODO 归档

已完成 / 已更新的待办条目归档于此，保留供日后回溯排查（当初打算做什么、做没做、什么时候做的、后来改没改主意）。

## 代码 / 机制

- ✅**已完成** routing 规则裸域名被静默丢弃，add 时应自动补 `domain:` 前缀或报错提示（完成：2026-08-14 18:00）
  - 现象：`routing.json` 的 `proxy_list` 里手写成裸域名（如 `openapi.tigerfintech.com`，无 `domain:` 前缀）时，`routing_manager.py` 生成 Xray 路由规则的循环只认 `geosite:` / `domain:` 两种前缀——裸域名两个分支都不进，**规则被静默丢弃、无任何警告**，流量落到兜底规则直连。
  - 实际踩坑：2026-08-14 排查老虎证券实盘下单被 code=1200 拒（监管按指令网络环境判定、需境外出口），用户已往 `proxy_list` 加了裸域名老虎域名、以为白名单已生效，实际规则从未落地、老虎流量一直走直连（境内出口），排查绕了一大圈才定位到是这里丢弃。
  - 完成情况：按期望修复方向②实现并加强——`generate_xray_routing_rules` 生成循环对无前缀规则不再静默跳过：裸域名自动按 domain 规则生效（Xray 裸域名即主域名匹配，规则真正落地）+ 打 warning 提示补前缀；`geoip:` / 裸 IP / CIDR 正确识别为 ip 字段（此前裸域名会被误当 ip 字段）。方向①（add 入口自动补前缀）未做——生成时规则已生效，无需入口补丁；顺带在同次改动中新增 `proxy_all` 全局代理默认模式（详见 CHANGELOG 0.5.1）。

- ✅**已完成** auto_switch monitor 守护进程多代积累，旧进程用旧代码干扰新配置（完成：2026-08-14 18:27）
  - 现象：`xpilot start` / `restart` 每次都 spawn 新 monitor（`python -m xpilot.cli monitor`），`stop` 只按 PID 文件杀一个，旧 monitor 残留不清理——2026-08-14 本机发现 8-02、8-07 两代旧 monitor 一直在跑，用**旧代码**反复写 xray 配置、拉起 xray，覆盖新配置导致改动不生效；手动全清（杀所有 monitor + 删 PID 文件）后才生效。
  - 完成情况：`xpilot/cli.py` 新增 `_kill_all_monitors()`，按命令行（pgrep 匹配 `xpilot.cli monitor`）清理**全部** monitor 进程（排除自身，SIGTERM + SIGKILL 兜底，异常静默降级），并清理 PID 文件；`_stop_monitor_daemon` 与 `_spawn_monitor_daemon` 均改走它。新增 `tests/test_cli.py` 4 个用例（多代全清、空场景、pgrep 失败降级、不误杀自身）。本机实测：spawn 出 3 个并存 monitor 后 `xpilot stop` 全部清掉、`start` 后仅 1 个，代理正常（详见 CHANGELOG 0.5.1）。
