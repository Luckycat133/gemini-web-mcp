# Gemini Web MCP Launch Kit

Use this evergreen kit when announcing or redistributing Gemini Web MCP. Do not present an older GitHub release as the current source line without checking its tag, wheel filename, and source version.

## Canonical links

- Repository: https://github.com/Luckycat133/gemini-web-mcp
- Development source: https://github.com/Luckycat133/gemini-web-mcp/tree/codex/gemini-tools-polish-20261101
- Release history: https://github.com/Luckycat133/gemini-web-mcp/releases
- Runtime skill: https://github.com/Luckycat133/gemini-web-mcp/tree/codex/gemini-tools-polish-20261101/.agents/skills/gemini-web-mcp
- ClawHub runtime skill: https://clawhub.ai/skills/gemini-web-mcp
- Development skill: https://github.com/Luckycat133/gemini-web-mcp/tree/codex/gemini-tools-polish-20261101/.agents/skills/gemini-web-mcp-development
- Client examples: https://github.com/Luckycat133/gemini-web-mcp/blob/codex/gemini-tools-polish-20261101/docs/client-examples.md

## Verified install snippets

Install in an isolated environment, start the real MCP stdio server, and call an auth-free text tool:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 gemini-mcp-onboarding
```

Replace the branch after `@` with a reviewed commit SHA for an immutable install. The command checks `profile=function`, `enabled_tools=17` and `text_tool=gemini_ask` without forwarding Gemini Cookies or calling Gemini. See [source integration notes](source-update-integration.md) for the differences from the published 0.2.2 release.

Install the runtime skill for agents that operate the server:

```bash
clawhub install gemini-web-mcp
```

This development branch keeps the Python package and public Skills at `0.2.1`. Check the ClawHub listing for the version it actually distributes. To install the development branch's copy:

```bash
npx --yes skills@1.5.21 add \
  "https://github.com/Luckycat133/gemini-web-mcp#codex/gemini-tools-polish-20261101" \
  --skill gemini-web-mcp \
  --agent codex --copy --yes
```

Install the development skill for contributors changing the repository:

```bash
npx --yes skills@1.5.21 add \
  "https://github.com/Luckycat133/gemini-web-mcp#codex/gemini-tools-polish-20261101" \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

For local development:

```bash
git clone --branch codex/gemini-tools-polish-20261101 https://github.com/Luckycat133/gemini-web-mcp.git
cd gemini-web-mcp
python -m venv .venv
. .venv/bin/activate
pip install -e ".[all,dev]"
gemini-mcp-function
```

## Product summary

Gemini Web MCP is an agent-first MCP Python SDK v2 gateway and paired skill set. The development branch exposes two entrypoints: `gemini-mcp-function` for Chat, Task, media and research, and `gemini-mcp-account` for explicit management of chats, Notebooks, native Skills, preferences and schedules. Each offers 17 tools.

The runtime skill and development skill are deliberately separate. Shared history services keep primary and compact typed results aligned, destructive results distinguish accepted requests from positive read-back evidence, and media results preserve requested, effective, and observed backend fields while treating a deliverable as local only after file/MIME/size metadata verification.

The ClawHub runtime bundle is MIT-0. The MCP server source and repository-development skill remain AGPL-3.0-only.

## Copy templates

### Short English

Gemini Web MCP gives Codex, Claude, VS Code, and other MCP clients access to Gemini Web. Start with the auth-free preflight for `gemini-mcp-function` and add `gemini-mcp-account` when you want to manage account data.

Repo: https://github.com/Luckycat133/gemini-web-mcp

### 简体中文

Gemini Web MCP 让 Codex、Claude、VS Code 等 MCP 客户端使用 Gemini Web。先运行无需账号的功能面预检；有明确的账号管理需求时，再添加 `gemini-mcp-account`。

Repo: https://github.com/Luckycat133/gemini-web-mcp

### Show HN body

```text
I built Gemini Web MCP, an agent-first MCP Python SDK v2 gateway plus separate runtime and development skills for Gemini Web workflows.

The development source separates Chat, Task, media and research from explicit account administration. Structured media results distinguish expected routing from observed backend evidence and verify local artifacts independently from response prose.

Credential-free MCP preflight:
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 gemini-mcp-onboarding

The project uses reverse-engineered Gemini Web behavior and carries explicit account/Terms-of-Service risk. PR checks stay offline; live behavior is only claimed when separately observed with an opted-in dedicated account.

Repo: https://github.com/Luckycat133/gemini-web-mcp
```

## Distribution checklist

- The source or commit being announced passed CI and CodeQL.
- The one-command onboarding smoke installed the built wheel in a clean `uvx` environment and called the auth-free text tool.
- Codex, Claude Desktop, Claude Code, and VS Code example files still parse and use the intended profile.
- Runtime and development skills both validate from the single `.agents/skills` source, and the development skill installs directly from the repository.
- The ClawHub runtime listing version, source commit, MIT-0 bundle license, categories, topics, and security scan state were checked before announcement.
- A tag release, if announced, matches `pyproject.toml` and its wheel/sdist/skill asset names.
- Expected routing is not described as observed Gemini backend behavior.
- Live account behavior is reported separately from offline CI evidence.
