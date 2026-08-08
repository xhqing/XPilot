# NetOpsAgent — Agent 项目说明

**拟人名**：Hermes（赫尔墨斯）
**职称**：网络运维管理员（Network Operations Agent）
**fleet 项目名**：NetOpsAgent
**仓库**：xhqing/NetOpsAgent

本项目是 Agent 项目，定位为「网络运维管理员 Hermes」——总体职责是处理**网络连接相关的问题**（代理转发只是众多网络问题中的一部分），当前专职节点真实流量测速、自动选路、故障转移、订阅刷新。项目根目录为 Agent 定位与对外文档；**XPilot** 是仓库内的独立子项目（`XPilot/` 目录），是 Hermes 开发的 CLI 工具之一——一个方便使用 Xray-core 的 Python CLI 工具，通过 `xpilot` 命令提供全部节点管理能力。CLI 命令名保持 `xpilot`（向后兼容，不改命令名与包名）；Agent 化体现在定位、README 人格与 fleet 注册表，不影响工具实际功能。

## 目录结构

- `XPilot/`：XPilot 子项目（xpilot CLI 工具），含独立 README（`XPilot/README.md`）、配置、测试、Docker 与开发工具链。
- 根目录 README：Agent 项目（Hermes）对外说明，指向 XPilot 子项目文档。
- `.github/workflows/release.yml`：发布工作流，在 `XPilot/` 目录内构建并上传 Release 资产。

## commit skill 检测缓存

<!-- commit-skill: readme-standard = ok -->
- README 中英双语 + LOGO + 徽章 + 版权署名：已就绪（2026-07-22 确认；2026-08-07 徽章更新为 Agent 标准 + 加 Hermes 身份节）

<!-- commit-skill: license = ok -->
- LICENSE.md：已存在，无冗余（2026-07-22 确认）

<!-- commit-skill: github-about = ok -->
- GitHub About：已配置于 xhqing/NetOpsAgent（英文 description + topics，2026-07-22）

<!-- commit-skill: attribution-name = ok -->
- 版权人/署名引用名字：已归一为 All Contributors（2026-07-22 确认）

<!-- commit-skill: readme-link-text = ok -->
- 英文版 README 跳转中文版链接文字：已统一为「简体中文」（2026-07-22 确认）

<!-- commit-skill: repo-sponsors = ok -->
- 仓库 Sponsors 按钮：已就绪（xhqing/.github 全局默认 FUNDING.yml，2026-07-22 确认）

## 跳过的检测项及原因

- **automemory（9e）**：按全局 `~/.claude/CLAUDE.md` 约定（2026-07-20 立），AutoMemory 全局已禁用（`autoMemoryEnabled: false`），新建项目一律不配 AutoMemory。故本项目不创建 `.claude/memory/`、不写 `autoMemoryDirectory`、`.gitignore` 不挂 memory 条目，9e 永久跳过。
