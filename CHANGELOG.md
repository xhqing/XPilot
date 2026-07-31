# 更新日志

本项目所有重要变更均会记录在此文件中。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-07-31

### 新增

- `xpilot rollback` 版本回滚命令：安装一个更早的 GitHub Release——不带 `--version` 时自动回滚到严格早于当前版本的最近一个 Release，带 `--version X.Y.Z` 时安装指定版本；安装复用 `--update` 的资产选择逻辑（优先 wheel、其次 sdist 压缩包、两者都没有时回退到对应 tag 的源码）。为此在 `xpilot/updater.py` 新增 `fetch_releases`（拉取全部 Release 列表）、`pick_previous_release`（取上一版）、`find_release_by_version`（按版本号定位）、`perform_rollback`（编排回滚），并把 `--update` 与 rollback 共用的「选资产 + 调 pip 安装」步骤提取为内部函数 `_install_release`；配套补充 `tests/test_updater.py` 单元测试。

## [0.2.0] - 2026-07-31

### 新增

- `xpilot --update` 自动升级命令：检查项目的 GitHub Release，若存在比当前版本更新的版本则自动安装——优先用 Release 附带的 wheel，其次 sdist 压缩包，两者都没有时回退到从对应 tag 的源码安装；已是最新版本时提示并退出。xpilot 仅经 GitHub Release 分发（未上架 PyPI），因此更新源指向 GitHub Release，而非 `pip install --upgrade xpilot`。新增 `xpilot/updater.py` 模块承载「取最新 Release / 版本比较 / 选资产 / 调 pip 安装」等逻辑，并配套 `tests/test_updater.py` 单元测试。

### 变更

- 修正版本号基线不一致：`xpilot/__init__.py` 的 `__version__` 与 `cli.py` 的 `--version` 输出由 `0.1.0` 同步至与 `VERSION` 一致的 `0.1.1`，使 `--update` 能基于准确的当前版本判断是否需要升级。

## [0.1.1] - 2026-07-28

### 新增

- `status` 命令展示当前节点的实时延迟：对当前节点做一次 TCP 探测获取实时延迟，替代此前读取缓存延迟字段的做法，使状态输出反映节点当下的可达性。

## [0.1.0] - 2026-07-07

首个发布版本。一个纯 Python 的命令行代理工具包，以 [Xray-core](https://github.com/XTLS/Xray-core)（v26.3.27）作为后端，提供节点管理、代理服务控制、健康检查与自动切换等功能。

### 新增

- 支持协议：VMess、VLESS、Trojan、Shadowsocks。
- 节点管理模块：节点的增删改查与导入。
- 智能节点健康检测：基于延迟与连通性的健康检查。
- 自动节点切换：依据延迟阈值在节点间自动切换。
- 订阅自动导入：支持 Base64、JSON、Clash 三种订阅格式。
- macOS 系统代理集成：一键开启与关闭系统代理。
- 灵活的路由规则管理：支持代理、直连、拦截三类规则配置。
- 命令行入口 `xpilot`（基于 Click）。
- 配套单元测试、Docker 部署文件、开发工具链与发布工作流。
- MIT 开源协议。
