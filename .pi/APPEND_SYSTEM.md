# XPilot · 项目硬约束

身份与角色见本仓库根 `AGENTS.md`；团队通用规则见全局 `~/.pi/agent/AGENTS.md`，此处不重述。

## 本项目是什么

Python 命令行框架，把 [Xray](https://github.com/XTLS/Xray-core) 的日常运维包成简单命令：节点管理、服务控制、健康检测、自动切换、路由规则、订阅自动导入。支持 VMess / VLESS / Trojan / Shadowsocks；macOS 系统代理集成。

## 🚨 绝对不要做的事

**不要自行关闭 / 重启 / 卸载用户正在运行的 xpilot 实例。**

- xpilot 是本机全局出口，关掉它 = 本机所有走代理的流量一起断（包括用户浏览器与其它程序的网络）。
- 禁止：`xpilot stop` / `xpilot restart`、`pkill` / `killall` 指向 xpilot、`launchctl unload` / `bootout` 它的守护项。
- 允许的只读 / 非破坏操作：`xpilot status`、`xpilot node list`、`xpilot test`（含 `--node <id>`）、`xpilot config show`、`xpilot switch`（只改配置、不影响运行实例）。
- 确实需要停 / 重开时，先在汇报里说明理由与影响、由 Leader 向用户取得当次授权，**Worker 不得自行执行**。

## 测试与检查

- `make test`——跑 pytest（`python -m pytest tests/ -v`）。
- `make lint`——`ruff check xpilot/ tests/`；格式化 `make fmt`（`ruff check --fix`）。
- 涉及真实网络 / 节点测速的用例会走代理，**不要在本机对着生产配置跑**；用测试替身或 `xpilot test` 的临时实例能力。
- 改代理行为后必须验证「配置不匹配时的自愈重启」与「全部节点连续失败后的退避」两条路径没被破坏——这两条是防重启风暴的关键。

## 版本与文档

- 版本号以 `VERSION` 为唯一权威（与 `pyproject.toml` / README 徽章一致）。
- README 中英双版（`README.md` / `README_cn.md`）**必须同步改**：信息点、列举项、数字不能少、不能多、不能变。
- 改动记 `CHANGELOG.md`；待办记 `TODO.md`（不写进别处）。

## 仓库规矩

- main 锁定、一切改动走 PR + CI；`feat/` / `fix/` / `chore/` 分支，一个分支只做一件事。
- 临时产物放项目 `tmp/`（已忽略）；`__pycache__` / `.pytest_cache` / `.ruff_cache` 不要提交。

## 详细说明

`README.md`（功能与安装）、`TODO.md`（待办）；通用工作规则见全局 `~/.pi/agent/AGENTS.md`。
