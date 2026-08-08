<div align="center">
  <img src="assets/logo.svg" alt="NetOpsAgent logo" width="380">

  <p>
    <a href="LICENSE.md"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
    <img src="https://img.shields.io/github/last-commit/xhqing/NetOpsAgent" alt="Last Commit">
    <img src="https://img.shields.io/badge/Type-AI%20Agent-blueviolet" alt="Type: AI Agent">
  </p>

  <p>
    <a href="README_cn.md">简体中文</a>
    &nbsp;|&nbsp;
    English
  </p>
</div>

## 🛰️ Hermes — Network Operations Agent

**Hermes** (赫尔墨斯) is this project's **Network Operations Agent**. Named after the Greek god of roads, crossings, and messengers, Hermes' overall job is to handle **network connectivity problems** — keeping your connections to the outside world alive and fast, diagnosing and fixing whatever gets in the way. Proxy forwarding is just one slice of that; the same mindset applies to any network issue you'd rather not babysit by hand.

Today Hermes ships with these capabilities:

- **Real-traffic node testing** — verifies whether each proxy node can actually carry traffic, not just whether TCP reaches the server (catches expired credentials, failed handshakes, and nodes that can't reach the internet).
- **Smart node selection** — picks the fastest usable node by latency and bandwidth, with three strategies: latency-only, speed-only, or a hybrid that leads with bandwidth and falls back on latency.
- **Automatic failover** — the moment the current node degrades, Hermes switches to a better one; when all nodes go down, it refreshes subscriptions and retries.
- **Flexible routing** — sends specific domains (GitHub, OpenAI, Google …) through specific nodes while everything else uses the default.

## Project Layout

This repository is the **Hermes Agent project**. **XPilot** is one of the CLI tools Hermes develops — a Python CLI that makes it easy to use [Xray-core](https://github.com/XTLS/Xray-core), invoked as the `xpilot` command. Run the commands yourself, or hand them to your AI assistant — either way, Hermes does the work.

| Path | Purpose |
|------|---------|
| `XPilot/` | The [XPilot](XPilot/README.md) subproject — a CLI tool for Xray-core proxy management (own README, config, tests, Docker, dev tooling) |
| `XPilot/README.md` | XPilot standalone documentation (install, commands, config reference) |

## Getting Started

The tool and all of its documentation live in the `XPilot` subproject:

- **Installation & usage**: see [XPilot/README.md](XPilot/README.md)
- **中文安装与使用文档**：[XPilot/README_cn.md](XPilot/README_cn.md)

```bash
git clone https://github.com/xhqing/NetOpsAgent.git
cd NetOpsAgent/XPilot
pip install -e .
xpilot --help
```

---

## License & Attribution

This project is licensed under the [MIT License](LICENSE.md).

Copyright (c) 2026 All Contributors.

### Attribution

If you reuse or redistribute any part of this project, please:

- Retain the above copyright notice and the MIT license text.
- Credit the project by linking back to its source.

**Project URL:** [https://github.com/xhqing/NetOpsAgent](https://github.com/xhqing/NetOpsAgent)
