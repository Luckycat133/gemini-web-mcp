# 贡献指南

感谢你考虑为 Gemini MCP Server 贡献代码。本指南说明开发环境、代码规范、测试要求和提交流程。

> 仓库内部的 [`AGENTS.md`](../AGENTS.md) 是给 AI agent 的精简规范，本文是给人贡献者的展开版，两者一致。

---

## 开发环境

```bash
# 1. 克隆并进入 venv
python -m venv .venv && . .venv/bin/activate

# 2. 安装全量依赖 + 测试
pip install -e ".[all,dev]"

# 3. 验证安装
pytest -q                                   # 测试套件
python -m py_compile src/server.py src/skill_server.py src/client_wrapper.py src/thinking_client.py src/constants.py src/tools/*.py

# 4. 本地跑服务（默认 core 工具面）
GEMINI_TOOLS=core python -m src.server

# 5. 用 MCP Inspector 交互式调试
pip install "mcp[cli]"
mcp dev src/server.py
```

需要 Cookie 才能做端到端验证；纯单元测试不需要。详见 [Cookie 获取指南](./cookie-setup.md)。

---

## 代码风格

| 项 | 约定 |
|---|---|
| Python 版本 | 3.10+（`pyproject.toml` 锁定 `target-version = "py310"`） |
| 缩进 | 4 空格 |
| 行宽 | 120 列（Ruff 配置） |
| 命名 | `snake_case` 函数/变量；工具名前缀 `gemini_` |
| 类型 | 鼓励类型标注；Mypy 配 `python_version = "3.10"` |
| 模块组织 | 按工具域分组（`chat.py` / `media.py` / `file.py` / `research.py` / `manage.py` / `prompts.py`） |
| 共享逻辑 | 放 `src/tools/utils.py`，不要在多个工具里复制粘贴 |

核心运行时代码在 `src/`：
- `server.py` —— 主 MCP 入口
- `skill_server.py` —— 低 token skill surface
- `client_wrapper.py` / `client_manager.py` / `cookie_manager.py` / `session_manager.py` —— 客户端/会话/Cookie
- `src/tools/` —— 工具实现
- `src/tools/manifest_data.py` —— 纯数据（`WEB_UI_CAPABILITIES` / `WEB_FEATURE_PROBES` / `TOOL_MANIFEST`）

更详细的结构看 [技术架构](./architecture.md)。

---

## 测试规范

- 测试文件放 `tests/test_*.py`，名字描述被测行为
- 工具面变更要同时断言**工具注册**和 **MCP annotations**（`readOnlyHint` / `destructiveHint` / `openWorldHint` 等）
- 用户可见能力或安全元数据变更时，同步更新 [evaluations/gemini_web_mcp_contract.xml](../evaluations/gemini_web_mcp_contract.xml)（当前 17 个只读 QA）
- 交付前必跑：

```bash
pytest -q
# 入口或 import 敏感变更额外跑：
python -m py_compile src/server.py src/skill_server.py src/client_wrapper.py src/thinking_client.py src/constants.py src/tools/*.py
```

---

## 提交规范

参考近期历史，用简洁的祈使句 + conventional prefix：

- `refactor: ...` —— 重构，不改行为
- `feat: ...` —— 新功能
- `fix: ...` —— bug 修复
- `chore(deps): ...` —— 依赖/构建
- `docs: ...` —— 文档
- `chore(release): ...` —— 版本发布

**一个 commit 一个逻辑变更**。不要把重构和新功能塞进同一个 commit。

---

## Pull Request 清单

PR 描述请包含：

1. **Summary** —— 改了什么、为什么
2. **Tests run** —— `pytest -q` 结果，必要时附 `py_compile`
3. **Configuration / environment changes** —— 新增/修改的环境变量、依赖、构建步骤
4. **Tool-surface / privacy / destructive implications** ——
   - 是否新增/删除/重命名工具？
   - 是否改变工具参数或 annotations？
   - 是否引入读取私密聊天文本的路径？
   - 是否新增 destructive 操作？
   - 即使是"无变更"也要显式写明
5. **Link issues** —— 关联 issue（若有）

> 工具名、参数、annotations 和用户可见的输出文本应保持稳定。重构时优先"dispatcher + handler"模式，逐字保留输出文本，避免破坏依赖文本匹配的 agent。

---

## 安全注意事项

**绝不提交**：`.env`、`cookies.json`、`prompts.json`、`generated_media/`、`artifacts/`、日志。用 `.env.example` 只记录变量名。

工具安全分层（写新工具时对照）：
- **只读发现**（`readOnlyHint=True`）—— 默认首选
- **读取私密聊天文本**（`READS_PRIVATE_REMOTE`）—— 需显式用户意图
- **远端修改**（`MUTATES_REMOTE`）—— 标注
- **本地修改**（`MUTATES_LOCAL`）
- **破坏性**（`destructiveHint=True`）—— 删除/重置类操作，需二次确认

默认工具面是 `core`（高价值 AI 工具 + 只读 helpers）。需要 account/history/Gems 管理能力时才用 `GEMINI_TOOLS=all`。把读私密文本或删除账号数据的工具默认暴露给普通 agent 是不允许的。

annotations 定义在 [src/tools/annotations.py](../src/tools/annotations.py)。

---

## Skill 与文档

若改动影响 agent 使用流程，同步更新：
- [`.agents/skills/gemini-web-mcp/SKILL.md`](../.agents/skills/gemini-web-mcp/SKILL.md) —— 公开可安装 skill
- [`.codex/skills/gemini-web-mcp/`](../.codex/skills/gemini-web-mcp) —— 本地副本，需与 `.agents` 保持同步
- [docs/tools.md](./tools.md) —— 工具手册
- [docs/changelog.md](./changelog.md) —— 在 `## Unreleased` 段追加条目

Skill 遵循 [agentskills.io](https://agentskills.io) 规范：`references/` 目录（复数）、description 含"何时使用"、`compatibility` 字段、SKILL.md < 500 行渐进式披露。

---

## 版本发布

发布流程（仅维护者）：

1. `pyproject.toml` 的 `version` 和 `src/__init__.py` 的 `__version__` **都要改**（曾经不同步过）
2. `docs/changelog.md`：`## Unreleased` → `## vX.Y.Z (YYYY-MM-DD)`
3. `git tag -a vX.Y.Z -m "vX.Y.Z"` 并推送
4. PR 合并后打 tag

遵循 [SemVer](https://semver.org/)：破坏性工具面变更升 major，新工具/功能升 minor，bug 修复升 patch。

---

## 行为准则

- 逆向工程访问 Gemini Web 仅供研究/教育；贡献的代码不应增加账户被风控的风险
- 不在代码或文档里硬编码个人路径（如 `/Users/xxx/`）或真实 Cookie
- 对破坏性操作保持保守默认（dry-run 优先，destructive 需显式参数）

有问题先开 issue 讨论，避免大改方向跑偏。祝你贡献顺利。
