<div align="center">
  <img src="assets/logo.svg" alt="XPilot logo" width="380">

  <p>
    <a href="LICENSE.md"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
    <img src="https://img.shields.io/badge/Version-v0.5.1-blue" alt="Version: 0.5.1">
    <img src="https://img.shields.io/badge/Type-Python%20CLI-blue" alt="Type: Python CLI">
  </p>

  <p>
    <a href="README.md">English</a>
    &nbsp;|&nbsp;
    简体中文
  </p>
</div>

一个方便使用 [Xray](https://github.com/XTLS/Xray-core) 的 Python 命令行框架，把节点管理、服务控制、健康检查、自动切换和路由规则等日常操作封装成一条条简单命令，免去直接手写 Xray 配置的繁琐。（xpilot 即「x-ray pilot」，Xray 的驾驶工具。）

## 特性

- 支持 VMess、VLESS、Trojan、Shadowsocks 协议
- 智能节点健康检测（延迟/连通性）
- 自动切换到延迟最低的可用节点（基于真实流量）
- 订阅自动导入（Base64/JSON/Clash 格式）
- macOS 系统代理集成
- 灵活的路由规则管理（代理/直连/拦截）
- 常用命令（`start` / `restart` / `stop` / `status`）后台运行、立即返回，运行日志自动落盘，便于排查问题

## 安装

> **说明**：本工具是一个 Python CLI，需要先安装 [Xray-core](https://github.com/XTLS/Xray-core) 作为代理后端。整个安装过程可交给你的 AI 助手完成——把本章节内容发给它，并告知你的操作系统，它应能端到端帮你装好。安装完成后，工具的配置默认与 Xray 官方安装路径一致，无需手动改任何配置即可使用。

### 环境要求

| 依赖 | 版本要求 | 说明 |
|------|----------|------|
| Python | 3.8+ | 运行 CLI 工具本体 |
| **Xray-core** | 推荐 `v26.3.27` | 代理后端（其他较新版本通常也能用） |

> **关于 Xray 版本**：本工具基于 `v26.3.27` 验证通过，推荐使用该版本。Xray-core 的协议实现与配置字段在版本间总体稳定，其他较新版本通常也能正常工作；若遇到兼容问题，再回到 `v26.3.27` 即可。

### 第一步：安装 Xray-core（固定版本）

Xray 官方提供安装脚本，支持通过 `--version` 参数安装指定版本。在终端执行：

```bash
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install --version v26.3.27
```

安装完成后：

- 二进制文件位于 `/usr/local/bin/xray`（macOS / Linux，官方脚本默认路径）。
- 验证安装是否成功：

```bash
xray version
# 应输出类似：Xray 26.3.27 (Xray, Penetrates Everything.)
```

<details>
<summary>macOS 用户：若没装 Homebrew 的 curl/cert，或上述命令失败，可改用以下方式</summary>

```bash
# 1. 下载安装脚本
curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh -o /tmp/install-release.sh

# 2. 用固定版本执行安装
bash /tmp/install-release.sh install --version v26.3.27

# 3. 清理
rm /tmp/install-release.sh
```

Windows 用户：官方脚本不支持 Windows，请从 [Xray-core Releases](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27) 下载对应 `windows.zip`，解压后将 `xray.exe` 放到某个目录（如 `C:\xray\xray.exe`），并在安装本工具后把 `settings.json` 里的 `xray_bin` 指向该路径。
</details>

### 第二步：安装 xpilot

从源码安装（开发模式）：

```bash
git clone https://github.com/xhqing/XPilot.git
cd XPilot
pip install -e .
```

或仅安装依赖后直接以模块方式运行：

```bash
pip install click 'requests[socks]' pyyaml
python3 -m xpilot.cli --help
```

安装完成后，会得到 `xpilot` 命令：

```bash
xpilot --help
```

> **默认配置无需改动**：`xpilot init` 生成的默认配置中，`xray_bin` 已指向 `/usr/local/bin/xray`，与上一步官方脚本的安装路径一致。若你把 xray 装在了其他位置（例如 Windows 或自定义路径），修改 `~/.config/xpilot/settings.json`（或项目 `config/settings.json`）里的 `xray_bin` 字段指向实际路径即可。

> **后续升级**：安装完成后，随时执行 `xpilot --update` 即可自动从 GitHub Release 检查并升级到最新版，无需手动下载或重新 `pip install`。

### 第三步：交给 AI 助手安装（可选）

如果你不想手动执行上述命令，可以直接把本章节内容复制给你的 AI 助手（Claude、ChatGPT、Gemini 等），并附上：

- 你的操作系统（macOS / Windows / Linux 发行版）和芯片架构（Apple Silicon / Intel / x64 / arm64）
- 你已有的代理节点信息（如有），或让助手引导你用 `xpilot node add` 添加

AI 助手应能：识别并安装固定版本 `v26.3.27` 的 Xray-core → 安装本工具 → `xpilot init` 初始化 → 协助你添加节点并启动代理。

## 快速开始

```bash
# 1. 初始化配置
xpilot init

# 2. 添加节点
xpilot node add --name "My Node" --protocol vmess --address server.example.com --port 443 --uuid your-uuid

# 3. 启动代理（后台运行，自动选择最快节点，命令立即返回）
xpilot start

# 4. 查看状态
xpilot status

# 5. 测试所有节点连通性
xpilot test --all-nodes

# 6. 停止代理
xpilot stop
```

> `start` / `restart` 会在后台启动 xray 代理进程与自动切换监控守护进程，命令本身立即返回，不会阻塞终端。代理进程与运行日志在后台持续存在，可用 `status` 查看状态、查看日志排查问题。

## 常用命令

日常使用最频繁的命令：

| 命令 | 说明 | 示例 |
|------|------|------|
| `xpilot start` | 后台启动代理（自动选择最快节点），立即返回 | `xpilot start` |
| `xpilot stop` | 停止代理服务与后台守护进程 | `xpilot stop` |
| `xpilot status` | 查看代理运行状态 | `xpilot status -v` |
| `xpilot restart` | 后台重启代理（自动选择最快节点），立即返回 | `xpilot restart` |
| `xpilot test --all-nodes` | 测试所有节点延迟和连通性 | `xpilot test -a` |

---

## 命令详解

### 基础命令

#### `init` - 初始化配置文件

在 `~/.config/xpilot/` 目录下创建默认配置文件（`nodes.json`、`routing.json`、`settings.json`）。

```bash
xpilot init
```

强制覆盖已有配置：

```bash
xpilot init -f
```

---

#### `start` - 启动代理服务

后台启动 xray 代理进程并设置系统代理，命令立即返回。自动切换监控守护进程会在后台随之运行。

使用默认节点启动（自动选择最快节点）：

```bash
xpilot start
```

使用指定节点启动：

```bash
xpilot start my_node
```

---

#### `stop` - 停止代理服务

停止 xray 进程、后台监控守护进程并关闭系统代理。

```bash
xpilot stop
```

---

#### `restart` - 重启代理服务

后台先停止再启动，自动选择最快节点，命令立即返回。

```bash
xpilot restart
```

---

#### `status` - 查看代理状态

显示代理是否运行、当前节点、端口、当前节点的真实流量延迟（与直连延迟并排对比）。默认还会测经节点网速和直连网速。

```bash
xpilot status
```

跳过网速测试（只看延迟，秒回）：

```bash
xpilot status --no-speed
```

---

#### `switch` - 切换节点

切换到指定节点并重启代理服务。

```bash
xpilot switch another_node
```

---

#### `--update` - 自动升级到最新版

从项目的 [GitHub Release](https://github.com/xhqing/XPilot/releases) 检查最新版本；若比当前版本新，则自动下载并安装（优先用 Release 附带的 wheel，其次 sdist 压缩包，两者都没有时回退到从对应 tag 的源码安装）。

```bash
xpilot --update
```

升级源是 GitHub Release（本项目未上架 PyPI）。已是最新版本时会提示并直接退出。

---

#### `rollback` - 回滚到旧版本

安装一个更早的 [GitHub Release](https://github.com/xhqing/XPilot/releases)。不带 `--version` 时，回滚到严格早于当前版本的最近一个 Release；带 `--version X.Y.Z` 时，安装指定的那个版本。安装复用与 `--update` 相同的资产（优先 wheel，其次 sdist 压缩包，两者都没有时回退到对应 tag 的源码）。

```bash
# 回滚到上一个版本
xpilot rollback

# 回滚到指定版本
xpilot rollback --version 0.1.1
```

若目标版本与当前版本相同，会提示并直接退出，不做任何改动。

---

### 节点管理命令

#### `node list` - 列出所有节点

显示所有已保存的节点信息（ID、名称、协议、地址、延迟）。

```bash
xpilot node list
```

按分组筛选：

```bash
xpilot node list -g work
```

---

#### `node add` - 添加节点

向配置中添加一个新的代理节点。

添加 VMess 节点：

```bash
xpilot node add --name "日本节点" --protocol vmess --address jp.example.com --port 443 --uuid xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx --tls --servername jp.example.com
```

添加 Trojan 节点：

```bash
xpilot node add --name "美国节点" --protocol trojan --address us.example.com --port 443 --password yourpassword --tls
```

添加 Shadowsocks 节点：

```bash
xpilot node add --name "SS节点" --protocol ss --address ss.example.com --port 8388 --password ss_password --security chacha20-ietf-poly1305
```

添加 VLESS 节点：

```bash
xpilot node add --name "VLESS节点" --protocol vless --address vless.example.com --port 443 --uuid xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx --tls --network ws --servername vless.example.com
```

带分组添加：

```bash
xpilot node add --name "工作节点" --protocol vmess --address work.example.com --port 443 --uuid work-uuid --group work
```

**参数说明：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `--name` | str | 是 | 节点名称 |
| `--protocol` | str | 是 | 协议类型（vmess/vless/trojan/ss） |
| `--address` | str | 是 | 服务器地址 |
| `--port` | int | 是 | 服务器端口 |
| `--uuid` | str | 部分 | UUID（VMess/VLESS 必填） |
| `--password` | str | 部分 | 密码（Trojan/SS 必填） |
| `--alter-id` | int | 否 | VMess alterId，默认 0 |
| `--security` | str | 否 | 加密方式，默认 auto |
| `--network` | str | 否 | 传输层（tcp/ws/h2/grpc），默认 tcp |
| `--tls` | flag | 否 | 是否启用 TLS |
| `--servername` | str | 否 | TLS 服务器名称（SNI） |
| `--group` | str | 否 | 分组名称，默认 default |

---

#### `node remove` - 删除节点

从配置中删除指定节点。

```bash
xpilot node remove my_node
```

---

#### `node edit` - 编辑节点

修改已有节点的配置信息。

修改节点名称：

```bash
xpilot node edit my_node --name "新名称"
```

修改服务器地址：

```bash
xpilot node edit my_node --address new.example.com
```

修改端口：

```bash
xpilot node edit my_node --port 8443
```

修改分组：

```bash
xpilot node edit my_node --group work
```

修改 UUID：

```bash
xpilot node edit my_node --uuid new-uuid-here
```

修改 TLS 设置：

```bash
xpilot node edit my_node --tls --servername new.example.com
```

---

#### `node import` - 从订阅导入节点

从订阅链接解析并批量导入节点。支持 Base64、JSON、Clash 格式。

```bash
xpilot node import "https://example.com/subscription/link"
```

---

#### `node export` - 导出节点配置

将当前所有节点导出为 JSON 或 YAML 格式。

导出为 JSON：

```bash
xpilot node export
```

导出为 YAML：

```bash
xpilot node export -f yaml
```

---

### 测试命令

#### `test` - 测试节点连通性

默认执行**真实流量**检测：为每个节点临时起一个 xray 实例，经它访问 `generate_204`，结果反映节点是否真能代理流量，而不只是 TCP 能否连到服务器。这能揪出「TCP 通但代理不可用」（密钥失效、协议握手失败）这类常见故障。输出同时展示 TCP 可达性与代理可用性。

测试指定节点：

```bash
xpilot test --node my_node
```

测试所有节点：

```bash
xpilot test --all-nodes
```

使用简写：

```bash
xpilot test -a
```

测试当前默认节点：

```bash
xpilot test --current
```

测试指定分组的所有节点：

```bash
xpilot test --group work
```

仅做快速 TCP 检测（不起临时 xray、不走真实流量，秒级返回）：

```bash
xpilot test --all-nodes --tcp
```

#### `test speed` - 测试网速

经 Cloudflare `__down` 端点下载固定大小文件（默认每节点 10MB）测速率。`--all-nodes` 并发测所有节点经代理网速，`--direct` 测直连网速（不走代理），`--current` 测当前节点。

测所有节点经代理网速（并发）：

```bash
xpilot test speed --all-nodes
```

测直连网速：

```bash
xpilot test speed --direct
```

测当前节点网速，或调整下载大小（MB）：

```bash
xpilot test speed --current
xpilot test speed --all-nodes --size 20
```

---

### 路由规则命令

#### `routing list` - 查看路由规则

列出当前所有代理、直连、拦截规则。

```bash
xpilot routing list
```

---

#### `routing add` - 添加路由规则

添加新的路由规则到代理、直连或拦截列表。

添加代理规则：

```bash
xpilot routing add proxy "geosite:google"
```

添加直连规则：

```bash
xpilot routing add direct "geoip:private"
```

添加拦截规则：

```bash
xpilot routing add block "geosite:ads"
```

---

#### `routing remove` - 删除路由规则

从所有规则列表中移除指定规则。

```bash
xpilot routing remove "geosite:google"
```

---

### 域名路由命令（特定网站走特定节点）

通过域名路由功能，可以让指定的域名使用特定的代理节点。例如让 GitHub 走专门的节点，而其他流量走默认节点。

#### `routing domain add` - 添加域名路由规则

将一组域名指向指定的代理节点。

让 GitHub 相关域名走 `github_node` 节点：

```bash
xpilot routing domain add -d github.com -d '*.github.io' -d api.github.com -n github_node --desc "GitHub"
```

让 OpenAI 走另一个节点：

```bash
xpilot routing domain add -d openai.com -d '*.openai.com' -d chatgpt.com -n openai_node --desc "OpenAI"
```

让 Google 服务走专属节点：

```bash
xpilot routing domain add -d google.com -d '*.google.com' -d youtube.com -d '*.youtube.com' -n google_node --desc "Google & YouTube"
```

**参数说明：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `-d, --domains` | str | 是 | 域名模式，可多次指定 |
| `-n, --node` | str | 是 | 目标节点的 ID |
| `--desc` | str | 否 | 规则描述，默认使用域名列表 |

---

#### `routing list` - 查看路由规则（含域名路由）

列出当前所有代理、直连、拦截规则以及域名到节点的映射规则。

```bash
xpilot routing list
```

输出示例：

```
Proxy rules:
  [PROXY] geosite:google

Direct rules:
  [DIRECT] geoip:private

Domain-to-node rules:
  [0] GitHub -> github_node
  [1] OpenAI -> openai_node
```

其中 `[0]`、`[1]` 是规则的索引编号，删除时使用。

---

#### `routing domain remove` - 删除域名路由规则

通过索引删除指定的域名规则。

删除索引为 0 的规则：

```bash
xpilot routing domain remove 0
```

---

#### `routing domain clear` - 清空所有域名路由规则

删除所有域名到节点的映射规则。

```bash
xpilot routing domain clear
```

强制清空（不确认）：

```bash
xpilot routing domain clear -f
```

---

### 订阅管理命令

#### `subscription add` - 添加订阅源

保存一个订阅链接供后续更新使用。

```bash
xpilot subscription add "https://example.com/subscription" --name "My Subscription"
```

---

#### `subscription update` - 更新订阅

从已保存的订阅源刷新节点：按节点名匹配已有节点，用订阅里的最新连接字段（地址、端口、UUID、密码、加密方式等）覆盖旧值，保留 id、分组与自定义名；同时导入新增节点。仅追加、不刷新已有节点请用 `node import`。

更新所有订阅源：

```bash
xpilot subscription update
```

更新指定订阅源：

```bash
xpilot subscription update "My Subscription"
```

---

#### `subscription list` - 列出订阅源

显示所有已保存的订阅源。

```bash
xpilot subscription list
```

---

#### `subscription remove` - 删除订阅源

移除一个已保存的订阅源。

```bash
xpilot subscription remove "My Subscription"
```

---

### 配置管理命令

#### `config show` - 查看当前配置

显示 `settings.json` 的全部内容。

```bash
xpilot config show
```

---

#### `config set` - 设置配置项

修改设置文件中的配置值，支持点符号路径访问嵌套配置。

修改日志级别：

```bash
xpilot config set log_level debug
```

修改 SOCKS 端口：

```bash
xpilot config set socks_port 7890
```

启用自动切换：

```bash
xpilot config set auto_switch.enabled true
```

调整切换迟滞（仅当更优节点比当前快该比例以上才切换；0 = 总是切到延迟最低的节点，即默认）：

```bash
xpilot config set auto_switch.hysteresis 0.2
```

启用系统代理：

```bash
xpilot config set system_proxy.enabled true
```

---

#### `config reset` - 重置配置

将所有配置文件恢复为默认值。

```bash
xpilot config reset
```

强制重置（不确认）：

```bash
xpilot config reset -f
```

---

## 配置文件

配置文件位于 `~/.config/xpilot/`：

| 文件 | 用途 |
|------|------|
| `nodes.json` | 节点配置（地址、协议、UUID 等） |
| `routing.json` | 路由规则（代理/直连/拦截列表） |
| `settings.json` | 全局设置（端口、xray 路径、自动切换等） |

### 配置方法

- **初始化**：运行 `xpilot init` 会在 `~/.config/xpilot/` 生成上述三个默认配置文件；加 `-f` 可强制覆盖已有配置。
- **目录位置**：遵循 XDG 规范——优先使用 `$XDG_CONFIG_HOME/xpilot`，未设置时回退到 `~/.config/xpilot`（Windows 用 `%APPDATA%/xpilot`）。也可用环境变量 `PROXY_TOOLKIT_CONFIG_DIR` 整体覆盖配置目录（多套配置切换、测试时常用）。
- **配置不放进项目目录**：本仓库 `config/` 下只保留 `*.example.json` 模板供参考，**不含任何实际配置**；真实配置（含节点凭据）只存在于用户目录，从源头上避免凭据进入代码仓库。

```bash
pip install -e .            # 安装 CLI
xpilot init                 # 在 ~/.config/xpilot/ 初始化默认配置
xpilot node add ...         # 添加节点（写入用户目录的 nodes.json）
xpilot config show          # 查看当前配置
```

- **开发用隔离代理**：`dev/isolated_proxy.py`（端口 2080/2087、完全不碰系统代理）同样读取用户目录下的 `nodes.json`，与主工具共用一份节点配置，无需重复维护。

### settings.json 字段说明

```json
{
  "xray_bin": "/usr/local/bin/xray",
  "socks_port": 1080,
  "http_port": 1087,
  "log_level": "warning",
  "log_file": "/tmp/xpilot.log",
  "auto_switch": {
    "enabled": true,
    "interval": 300,
    "strategy": "latency",
    "hysteresis": 0
  },
  "watchdog": {
    "enabled": true,
    "interval": 30,
    "max_retries": 3,
    "retry_delay": 5
  },
  "subscription": {
    "auto_update": false,
    "update_interval": 3600
  },
  "system_proxy": {
    "enabled": true,
    "bypass_local": true
  }
}
```

> **watchdog 与 auto_switch 的区别**：`watchdog` 默认开启，负责在 xray 进程意外退出时自动重新拉起（保活），与是否启用 `auto_switch` 无关；`auto_switch` 默认开启，每 interval 秒在所有真实流量可用的节点中选**延迟最低**的，最优不是当前节点就切换（`hysteresis` 可防止两个节点延迟接近时来回抖动），全部节点不通时自动拉取订阅按名字刷新。两者相互独立，可单独开关。

### routing.json 字段说明

```json
{
  "proxy_all": true,
  "proxy_list": [
    "domain:google.com",
    "domain:github.com"
  ],
  "direct_list": [
    "geoip:private"
  ],
  "block_list": [],
  "domain_rules": [],
  "rules": []
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `proxy_all` | `true` | **全局代理模式（默认）**：除 `direct_list` / `block_list` 明确列出的流量外，其余全部走代理——未匹配的流量在规则末尾显式兜底到代理，即使规则写漏 / 写错也不会意外直连；`false` 为**白名单分流模式**：只有 `proxy_list` 列出的流量走代理，其余全部直连 |
| `proxy_list` | 常用境外域名 | 走代理的规则列表（支持 `geosite:` / `domain:` 前缀或裸域名；裸域名会按域名规则处理并在日志中提示补前缀） |
| `direct_list` | `["geoip:private"]` | 直连的规则列表（支持 `geosite:` / `geoip:` 前缀、裸 IP 与 CIDR） |
| `block_list` | `[]` | 拦截的规则列表 |
| `domain_rules` | `[]` | 域名→指定节点映射规则（用 `routing domain add` 管理） |
| `rules` | `[]` | 自定义高级规则（完整 Xray field 规则，type 可为 `proxy` / `direct` / `block`） |

> **注意**：全局代理模式下，若 `rules` 里残留全量直连兜底规则（如 `ip: ["0.0.0.0/0"]` 且 `type: "direct"`），它会先于代理兜底把全部流量放行直连——xpilot 启动日志会打 warning 提示该冲突，需删除该规则或改用 `proxy_all: false`。

## 运行日志

xpilot 会把运行日志写入文件，便于在代理异常时定位问题。日志分两类：

| 日志文件 | 内容 | 说明 |
|----------|------|------|
| `/tmp/xpilot.log` | xpilot 自身日志 | 记录代理启停、自动切换、健康检查等；后台守护进程的输出也写在这里。路径由 `settings.json` 的 `log_file` 字段控制 |
| `/tmp/xpilot-xray-stdout.log` | xray 进程标准输出 | 排查 xray 启动、协议握手等问题 |
| `/tmp/xpilot-xray-stderr.log` | xray 进程标准错误 | xray 报错最先出现在这里 |

查看日志：

```bash
cat /tmp/xpilot.log                  # xpilot 自身日志
tail -f /tmp/xpilot-xray-stderr.log  # 实时跟踪 xray 报错
```

需要更详细的信息时，把日志级别调到 `debug`：

```bash
xpilot config set log_level debug
```

## 常见问题

### Q: 启动代理失败？

- 确认 xray 已正确安装：`which xray`
- 检查端口是否被占用
- 查看 xray 报错日志：`cat /tmp/xpilot-xray-stderr.log`
- 查看 xpilot 自身日志：`cat /tmp/xpilot.log`

### Q: 系统代理无法设置？

- macOS 上设置系统代理需要网络权限
- 可以在系统设置 → 网络 → 高级 → 代理中手动检查

### Q: 健康检查超时？

- 检查网络连接是否正常
- 确认节点配置是否正确
- 增加测试超时时间

---

## 版权与署名

本项目基于 [MIT 协议](LICENSE.md) 开源。

Copyright (c) 2026 All Contributors。

### 署名方式

如果你复用或再分发本项目的任何部分，请：

- 保留上方版权声明与 MIT 协议文本。
- 通过链接回项目原始来源的方式注明出处。

**项目地址：** [https://github.com/xhqing/XPilot](https://github.com/xhqing/XPilot)
