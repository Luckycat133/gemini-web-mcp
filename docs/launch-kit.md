# Gemini Web MCP Launch Kit

Use this kit when announcing or redistributing Gemini Web MCP.

## Canonical Links

- Repository: https://github.com/Luckycat133/gemini-web-mcp
- Latest release: https://github.com/Luckycat133/gemini-web-mcp/releases/latest
- Public Codex skill: https://github.com/Luckycat133/gemini-web-mcp/tree/main/.agents/skills/gemini-web-mcp
- Standalone skill zip: use the `gemini-web-mcp-skill-*.zip` asset from the latest release

## Install Snippets

Install the skill directly from GitHub for Codex, Claude Code, Gemini CLI, Cline, and other supported agents:

```bash
npx skills add https://github.com/Luckycat133/gemini-web-mcp/tree/main/.agents/skills/gemini-web-mcp
```

Install and run the MCP server locally:

```bash
git clone https://github.com/Luckycat133/gemini-web-mcp.git
cd gemini-web-mcp
python -m venv .venv
. .venv/bin/activate
pip install -e ".[all]"
GEMINI_TOOLS=core python -m src.server
```

Build release artifacts:

```bash
python scripts/package_release.py --outdir dist
```

## One-Liners

- English: Gemini Web MCP gives coding agents a safer, layered MCP interface for Gemini Web chat, media, history, notebooks, scheduled actions, and account inventory.
- Chinese: Gemini Web MCP 给各类 AI agent 提供一个分层、安全、可验证的 Gemini Web MCP 接口，覆盖模型调用、媒体生成、历史整理、Notebook、定时任务和账号只读盘点。

## X / Twitter Posts

### English

I just released Gemini Web MCP v2.2.0.

It gives Codex, Claude Desktop, VS Code, and other MCP-capable agents a layered Gemini Web interface:

- `GEMINI_TOOLS=model` for model calls only
- `history` for chat history work
- `account-read` for read-only inventory
- `scheduled-admin` only when you explicitly need mutations
- standalone Codex skill package included

Repo: https://github.com/Luckycat133/gemini-web-mcp

### Chinese

我发布了 Gemini Web MCP v2.2.0。

它把 Gemini Web 封装成更适合 AI agent 使用的 MCP/Skill 工具层：模型调用、媒体生成、历史对话整理、Notebook、定时任务、账号只读盘点都做了分层。

默认不是把所有工具一股脑暴露给 agent，而是按意图启用：

- `model` 只调用模型
- `history` 整理历史
- `account-read` 只读盘点
- `scheduled-admin` 才允许定时任务写操作

Repo: https://github.com/Luckycat133/gemini-web-mcp

## LinkedIn / Longform Post

I released Gemini Web MCP v2.2.0, an MCP server and Codex skill for using Gemini Web from AI coding agents.

The main design goal is not just "more tools." It is safer tool layering:

- narrow profiles for model-only, history-only, and account-read workflows
- facade tools for chat history and account inventory
- explicit boundaries for private chat text and destructive operations
- a standalone Codex skill under `.agents/skills/gemini-web-mcp`
- release assets for the MCP server wheel, source distribution, and skill zip

This makes it easier for agents like Codex, Claude Desktop, VS Code MCP clients, and other MCP-compatible tools to use Gemini Web without exposing an oversized tool surface by default.

GitHub: https://github.com/Luckycat133/gemini-web-mcp

## Reddit / Hacker News Style

I built Gemini Web MCP, a Python FastMCP server plus Codex skill for Gemini Web workflows.

The interesting bit is the tool layering: instead of exposing every account/history/admin function to every agent, it has narrow `GEMINI_TOOLS` profiles such as `model`, `history`, `account-read`, `history-organize`, `scheduled-read`, and `scheduled-admin`.

Release includes a standalone Codex skill zip and direct GitHub skill install path.

Repo: https://github.com/Luckycat133/gemini-web-mcp

## Show HN

Title:

```text
Show HN: Gemini Web MCP - Layered Gemini Web tools for AI agents
```

Body:

```text
I built Gemini Web MCP, a Python FastMCP server and agent skill for using Gemini Web from MCP-compatible clients.

The practical problem it solves is tool-surface control. An agent that only needs model calls can start with GEMINI_TOOLS=model instead of receiving private history, account, and admin tools. Separate profiles cover history organization, read-only account inventory, media workflows, and explicitly enabled scheduled-action administration.

Install the agent skill:
npx skills add https://github.com/Luckycat133/gemini-web-mcp/tree/main/.agents/skills/gemini-web-mcp

Run the smallest MCP profile:
GEMINI_TOOLS=model uvx --from https://github.com/Luckycat133/gemini-web-mcp/releases/download/v2.2.0/gemini_mcp_server-2.2.0-py3-none-any.whl gemini-mcp-server

The project uses reverse-engineered Gemini Web behavior, so it includes an explicit account-risk disclaimer and keeps private/destructive tools outside the default narrow profiles.

Repo: https://github.com/Luckycat133/gemini-web-mcp
```

## Hashtags

`#MCP` `#Codex` `#AIAgents` `#Gemini` `#OpenSource` `#Python`

## Distribution Checklist

- GitHub Release with wheel, source distribution, and standalone skill zip
- Public skill path at `.agents/skills/gemini-web-mcp`
- README install commands for both skill and MCP server
- Repository topics: `codex-skills`, `agent-skills`, `mcp`, `mcp-server`, `gemini`, `gemini-web`
- Submission to MCP/skill directories where a public submission path exists
