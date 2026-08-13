# xpilot — 项目说明

## commit skill 检测缓存

<!-- commit-skill: readme-standard = ok -->
- README 中英双语 + LOGO + 徽章 + 版权署名：已就绪（2026-07-22 确认）

<!-- commit-skill: license = ok -->
- LICENSE.md：已存在，无冗余（2026-07-22 确认）

<!-- commit-skill: github-about = ok -->
- GitHub About：已配置于 xhqing/XPilot（英文 description + topics，2026-07-22）

<!-- commit-skill: attribution-name = ok -->
- 版权人/署名引用名字：已归一为 All Contributors（2026-07-22 确认）

<!-- commit-skill: readme-link-text = ok -->
- 英文版 README 跳转中文版链接文字：已统一为「简体中文」（2026-07-22 确认）

<!-- commit-skill: repo-sponsors = ok -->
- 仓库 Sponsors 按钮：已就绪（xhqing/.github 全局默认 FUNDING.yml，2026-07-22 确认）

## 跳过的检测项及原因

- **agent-persona / agent-llm（9d / 9f）**：本项目目录名 `XPilot` 不以 `Agent` 结尾，是一个 Python CLI 工具（非开源 Agent 能力的项目），故跳过拟人名与大脑型号检测。
- **automemory（9e）**：按全局 `~/.claude/CLAUDE.md` 约定（2026-07-20 立），AutoMemory 全局已禁用（`autoMemoryEnabled: false`），新建项目一律不配 AutoMemory。故本项目不创建 `.claude/memory/`、不写 `autoMemoryDirectory`、`.gitignore` 不挂 memory 条目，9e 永久跳过。
