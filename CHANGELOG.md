# 更新日志

本项目所有重要变更均会记录在此文件中。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.5.1] - 未发布

### 新增

- `routing.json` 新增 `proxy_all` 字段并**默认启用全局代理**（`true`）：除 `direct_list` / `block_list` 明确列出的流量外，其余全部走代理——生成 Xray 路由规则时在末尾显式加「未匹配兜底走代理」规则（带 `network: tcp,udp` 条件；Xray 拒绝无有效字段的规则，实测 26.1.18 报 `this rule has no effective fields` 直接拒绝启动），不再依赖 Xray「未匹配走第一个 outbound」的默认行为，从机制上杜绝「以为走了代理、实际直连」的泄漏。**为什么**：2026-08-14 排查老虎证券实盘下单被 code=1200 拒（监管按指令网络环境判定、需境外出口），发现路由配置是白名单分流且 `rules` 里有一条 `0.0.0.0/0 → direct` 的兜底直连规则，任何漏配 / 写错的规则都会让流量直连；用户以为白名单已生效、实际流量一直走境内出口。`proxy_all: false` 为白名单分流模式：只有 `proxy_list` 列出的走代理，未匹配流量兜底直连。
- 路由规则格式识别容错：`proxy_list` / `direct_list` / `block_list` 里的**裸域名（无 `geosite:` / `domain:` 前缀）不再被静默丢弃**——自动按 domain 规则生效（Xray 裸域名即主域名匹配），并在日志打 warning 提示补前缀。**为什么**：此前裸域名两个前缀分支都不进、规则静默消失，正是老虎域名规则「从未落地」的根因之一；顺带把同因的 `geoip:` / 裸 IP / CIDR 识别为 ip 字段（此前会被误放进 domain 或当 ip 处理）。
- 全局代理模式下的冲突检测：`rules` 自定义规则里残留全量直连兜底（`ip: ["0.0.0.0/0"]` 且 `type: "direct"`）时，xpilot 启动日志打 warning 提示该规则会使全部流量直连、与 `proxy_all` 冲突。

### 变更

- `xpilot/config.py` 默认 routing 配置（`xpilot init` 生成的）新增 `proxy_all: true`；`config/routing.example.json` 模板 `proxy_all` 由 `false` 改为 `true`。
- `xpilot/routing_manager.py`：`generate_xray_routing_rules` 读取 `proxy_all`（缺字段时按 `true` 处理，老配置自动获得全局代理语义）并生成对应兜底规则。
- `README.md` / `README_cn.md`：新增「routing.json 字段说明」章节，含 `proxy_all` 两种模式语义、各列表支持的规则格式、以及全局代理模式与自定义直连兜底规则的冲突提示。
- `xpilot/cli.py`：monitor 守护进程清理从「只按 PID 文件杀最后一个」改为「按命令行匹配清理全部」（新增 `_kill_all_monitors`，`_stop_monitor_daemon` 与 `_spawn_monitor_daemon` 均走它）。**为什么**：`start` / `restart` 每次都 spawn 新 monitor、`stop` 只杀 PID 文件记录的一个，旧 monitor 多代残留——8-02、8-07 两代旧 monitor 用旧代码反复写 xray 配置、拉起 xray，覆盖新配置导致 2026-08-14 全局代理改动不生效，手动全清后才生效。修复后 spawn 前 / stop 时一律清掉所有现存 monitor（pgrep 按 `xpilot.cli monitor` 命令行匹配，排除自身，SIGTERM + SIGKILL 兜底），杜绝多代并存。新增 `tests/test_cli.py` 覆盖清理逻辑（多代全清、空场景、pgrep 失败降级、不误杀自身）。
- 撤销 2026-08-08 的 Agent 化重构（原记录于 0.6.0 未发布条目，该条目已移除），项目恢复为独立的 **XPilot 工具项目**。**为什么**：Agent 化重构把工具塞进 `XPilot/` 子目录、仓库与目录改名 `NetOpsAgent`，实际使用中发现工具独立成库更合适——恢复原目录布局与仓库名，Agent 定位（Hermes / NetOpsAgent）拆到独立仓库 `xhqing/NetOpsAgent`。工具功能、命令名（仍为 `xpilot`）、包名均不受影响。
- 目录布局恢复：`XPilot/` 子目录内容全部移回仓库根目录（代码、配置、测试、Docker、README / CHANGELOG / VERSION / LICENSE、`.gitignore`）；删除根目录 Agent 层文档（Hermes 定位 README、`.claude/`、`.codebuddy/`、Agent 版 CHANGELOG / VERSION / assets logo）。
- `README.md` / `README_cn.md`：去除 Hermes / 子项目表述，恢复独立工具文档；GitHub 链接、徽章 URL 改回 `xhqing/XPilot`。
- `xpilot/updater.py` 的 `REPO_NAME` 改回 `XPilot`（`--update` / `rollback` 从改回后的仓库拉 Release）；`tests/test_updater.py` 的 pip 安装期望值同步改回。
- `.github/workflows/release.yml`：去掉 `XPilot` 子目录相关配置（`working-directory`、`XPilot/dist/*`），恢复在仓库根目录构建并上传 Release 资产。
- `CLAUDE.md` 恢复工具项目说明（含 agent-persona / agent-llm 跳过项）。

## [0.5.0] - 2026-08-02

### 新增

- `auto_switch` 新增选优策略 `strategy`（默认 `latency`，行为不变）：`hybrid` 排除经节点延迟超过 `latency_threshold_ms`（默认 1500ms）的节点后，在剩余节点里按带宽降序选最优——带宽主导、延迟兜底；`speed` 为纯带宽（延迟不参与）。**为什么**：原纯延迟选优会把「延迟尚可、带宽极差」的假优节点（实测 219ms/0.5Mbps 的节点）当最优切过去，YouTube 等视频场景必卡；延迟与带宽无相关性（实测最优延迟节点的带宽垫底）。带宽数据用缓存（`speed_cache_ttl` 默认 1 小时整组重测、`speed_test_size` 默认 5MB），避免每轮测速烧套餐流量；5MB 是实测收敛点——2MB 对快节点只有 0.6s 下载窗口、TCP 慢启动未到峰、严重低估（27Mbps 节点测成 0.4Mbps），5MB 以上才稳定区分档位。`hysteresis` 在 hybrid/speed 下语义变为带宽比例（当前带宽 ≥ 最优带宽的 (1-hysteresis) 时不切，防抖）。为此在 `xpilot/auto_switch.py` 新增 `_pick_best`（按策略选优，hybrid 全超限时回退全部「宁可慢不可断」）、`_refresh_speed_cache`（带宽缓存整组刷新，失败不阻塞选优）、`_sort_by_speed`（带宽降序、缺失排最后、同带宽按延迟兜底）、`_fmt_speed`。
- `status` 命令默认不再测延迟与网速（原来每次 status 要下载 10MB 测速、等 10-20 秒）：只显示运行状态、PID、当前节点、端口，以及「当前节点连通性」——经当前在跑的代理访问 `generate_204`，通即报 OK、不通报 FAIL，几秒内返回。`-v/--verbose` 才额外显示经节点与直连延迟；`--no-speed` 选项废弃（保留声明、隐藏，无效果）。

### 变更

- `xpilot/config.py` 默认 settings 的 `auto_switch` 新增 `strategy`（默认 `latency`）、`latency_threshold_ms`（1500）、`speed_cache_ttl`（3600）、`speed_test_size`（5000000）四项配置；原文档语义随策略扩展。

## [0.4.0] - 2026-08-02

### 新增

- `test` 命令默认改用真实流量检测：为每个节点起临时 xray 实例（独立 socks 端口、仅绑 127.0.0.1），经它访问 `generate_204`，把「TCP 可达」与「代理可用」分开判定——解决此前只测 TCP 握手、导致「TCP 通但代理流量不通」（密钥失效、协议握手失败、对端无法出网）被误判为可用的问题。输出分 TCP 与代理两列；新增 `--tcp` 保留秒级 TCP-only 快速检测。为此在 `xpilot/health_checker.py` 新增 `check_real_traffic`（单节点）、`batch_check_real`（并发批量）、`_curl_through_socks`（走 socks5h 实测，避免本地 DNS 污染误判）、`_find_free_port`、`_kill_proc`。
- `auto_switch` 重设计为「主动选优」：每 interval 秒对全部节点做真实流量检测，在能用的节点中选延迟最低的为最优，最优不是当前节点就切换（不再是「当前不通才切」的兜底）。`hysteresis`（迟滞比例，默认 0）可防止节点延迟接近时来回抖动；当前节点不可用时无视迟滞直接切到最优；全部节点不通时自动拉订阅按名字刷新（带 cooldown 退避）后重测并切换。配置项 `threshold` 由 `hysteresis` 取代。为此在 `xpilot/auto_switch.py` 重写 `_check_and_switch`，新增 `_try_refresh_subscription`（带退避的订阅刷新）、`_switch_to`（切换逻辑，`manual_switch` 复用）、`_log_usable_status`（可用节点不足时记录日志）。
- 订阅导入支持按名字刷新：`NodeManager.import_from_subscription` 新增 `update_existing` 参数，为 True 时按节点名匹配已有节点并覆盖其连接字段（地址、端口、UUID、密码、加密方式、传输、TLS、SNI），保留 id、分组与自定义名——机场轮换密钥或 IP 后能真正刷新同名节点，而非像旧逻辑那样加后缀创建重复项（`jms-c56s1` → `jms-c56s1_1`）。新增 `SUBSCRIPTION_REFRESH_FIELDS` 常量与 `_refresh_node_fields` 方法。
- `status` 命令在 `auto_switch` 未开启时给出醒目提示，提醒当前节点失效时不会自动切换。
- VLESS+Reality 协议支持：`_parse_vless_link` 解析 reality 参数（`pbk` 公钥、`sid` 短 ID、`fp` 指纹、`flow` 流控）并对 fragment 节点名做 URL 解码（修复订阅导入后节点名出现 `%40` `%3A` 等乱码）；`ProxyManager._generate_outbound` 为 reality 节点生成 `realitySettings`、把 flow（如 `xtls-rprx-vision`）写到 vless user；`NodeManager` 的存储字段白名单（`add_node` / `update_node` / 订阅刷新字段）纳入 reality 字段。
- 订阅节点自动清理与 source 标记：`import_from_subscription` 新增 `source` 参数，给导入/刷新的节点打订阅源标记（如 `source: 'JMS'`），并自动清理该订阅下「订阅不再返回」的旧节点——订阅改名（如 `JMS-c56s1` → `JMS-1336028@c56s1`）或移除节点时，失效旧节点会被自动清掉，本地始终保持订阅返回的最新节点集。带骤降保护（返回数 < 已有的 50% 时跳过清理，避免订阅临时故障误删）。手动 `node add` 的节点无 source、永不参与清理。`subscription update` 与 auto_switch 自动刷新均启用此模式。
- 可用节点数异常监控：auto_switch 每次真实流量检测后，若可用节点少于总数，记录 warning 日志列出不通节点名与可能原因（写入 `log_file`，默认 `/tmp/xpilot.log`）；订阅导入后若该 source 节点数与订阅返回数不符，同样记录原因（含新增/刷新/失败/清理计数），让用户知道「为什么可用节点少了」。
- `xpilot test speed` 网速测试：经代理或直连下载 Cloudflare `__down` 端点的固定大小文件（默认 10MB）测速率。`--all-nodes` 并发测所有节点经代理网速，`--direct` 测直连网速（不走代理），`--current` 测当前节点，`--size` 调下载大小。为此在 `xpilot/health_checker.py` 抽出 `_start_probe`（临时 xray 探测的公共逻辑，连通性检测与测速复用），新增 `check_speed` / `check_direct_speed` / `batch_check_speed` / `_curl_download`。
- `test` 命令改为 group（`invoke_without_command` 兼容原有 `xpilot test --all-nodes` / `--tcp` / `--current` / `--group`），以承载 `speed` 子命令。**不兼容变更**：单节点测试由 positional 参数（`xpilot test my_node`）改为 `--node` 选项（`xpilot test --node my_node`），因 group 的 positional 会与子命令名冲突。
- `status` 命令默认展示当前节点的真实流量延迟、经节点网速、直连网速三项指标（经当前在跑的代理与直连各下载 5MB 测速）；新增 `--no-speed` 选项跳过测速只看延迟。
- 延迟对比：凡显示「经节点延迟」处都配套显示「直连延迟」——`status` 并排展示经节点延迟与直连延迟；`test` 连通性汇总显示直连延迟基准，用于和各节点经节点延迟对比。新增 `HealthChecker.check_direct_latency`（直连访问 generate_204，墙内自动回退 cloudflare 端点）。
- 新增单元测试：订阅按名字刷新与默认追加行为对比、source 自动清理与骤降保护（`tests/test_node_manager.py`）、真实流量检测失败路径与端口探测（`tests/test_health_checker.py`）、auto_switch 真实流量切换决策与订阅刷新退避（`tests/test_auto_switch.py`）、VLESS+Reality 解析与出站配置生成（`tests/test_subscription.py`、`tests/test_proxy_manager.py`）。

### 变更

- `subscription update` 命令由「只追加新节点」改为「按名字刷新已有节点 + 导入新增节点」，符合 update 语义；仅追加新节点请用 `node import`。
- `xpilot/config.py` 默认 settings 的 `auto_switch` 新增 `auto_update_subscription`（默认 True）与 `subscription_refresh_cooldown`（默认 3600）两项配置。
- `auto_switch.enabled` 默认值由 False 改为 True——新装或 `xpilot init` 重置即开箱启用真实流量自动切换，无需手动 `config set`。

### 修复

- `subscription.fetch` 改用 `trust_env=False` 的 session 强制直连，绕过环境变量与 macOS 系统代理。此前 `xpilot start` 会把系统代理指向本地 xray，当代理本身故障时，拉订阅的请求也被坏代理截断，陷入「代理坏 → 拉不到订阅 → 无法恢复代理」的死循环。
- `_parse_ss_link` 重写为手动切分解析，修复 SIP002 格式（`ss://base64(method:password)@host:port#name`）SS 链接解析失败的问题：userinfo 里的 base64 字符（`+` `/` `=`）会让 urlparse 把 userinfo 误当成 hostname/port，导致 SS 节点无法解析、订阅更新时被静默跳过（旧节点因此长期得不到刷新）。新增 `_b64decode_loose` 兼容 websafe base64。

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
