# Gemini Web MCP Launch Kit

Use this evergreen kit when announcing or redistributing Gemini Web MCP. Do not present an older GitHub release as the current source line without checking its tag, wheel filename, and source version.

## Canonical links

- Repository and current source: https://github.com/Luckycat133/gemini-web-mcp
- Release history: https://github.com/Luckycat133/gemini-web-mcp/releases
- Runtime skill: https://github.com/Luckycat133/gemini-web-mcp/tree/main/.agents/skills/gemini-web-mcp
- ClawHub runtime skill: https://clawhub.ai/skills/gemini-web-mcp
- Development skill: https://github.com/Luckycat133/gemini-web-mcp/tree/main/.agents/skills/gemini-web-mcp-development
- Client examples: https://github.com/Luckycat133/gemini-web-mcp/blob/main/docs/client-examples.md

## Verified install snippets

Install in an isolated environment, start the real MCP stdio server, and call an auth-free text tool:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding
```

Pin `@main` to a reviewed commit SHA for an immutable install. The command above does not forward Gemini Cookies or call Gemini.

Install the runtime skill for agents that operate the server:

```bash
clawhub install gemini-web-mcp
```

The ClawHub release starts at `0.1.0`; `0.1.1` is the first security-audit wording and disclosure patch. To install directly from the repository instead:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp \
  --agent codex --copy --yes
```

Install the development skill for contributors changing the repository:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

For local development:

```bash
git clone https://github.com/Luckycat133/gemini-web-mcp.git
cd gemini-web-mcp
python -m venv .venv
. .venv/bin/activate
pip install -e ".[all,dev]"
GEMINI_TOOLS=model python -m src.server
```

## Product summary

Gemini Web MCP is an agent-first MCP Python SDK v2 gateway and paired skill set for Gemini Web text, media, files, URLs, Deep Research, history, notebooks, scheduled actions, and account inventory. `model` is the narrow text starting point, `core` adds content and multimodal workflows, the compact server offers a fixed low-token facade, and `all` is reserved for maintenance verification.

The runtime skill and development skill are deliberately separate. Shared history services keep primary and compact typed results aligned, destructive results distinguish accepted requests from positive read-back evidence, and media results preserve requested, effective, and observed backend fields while treating a deliverable as local only after file/MIME/size metadata verification.

The three-file ClawHub runtime bundle is MIT-0. The MCP server source and repository-development skill remain AGPL-3.0-only.

## Copy templates

### Short English

Gemini Web MCP gives Codex, Claude, VS Code, and other MCP clients a layered Gemini Web interface. Start with an auth-free one-command MCP preflight, select `model` for text or `core` for multimodal work, and install the separate runtime skill when an agent needs workflow guidance.

Repo: https://github.com/Luckycat133/gemini-web-mcp

### 简体中文

Gemini Web MCP 为 Codex、Claude、VS Code 等 MCP 客户端提供分层的 Gemini Web 接口。先用无需账号的一条命令完成真实 MCP 预检；文本从 `model` 开始，多模态使用 `core`，agent 操作指南与仓库开发指南分别由两个 skill 提供。

Repo: https://github.com/Luckycat133/gemini-web-mcp

### Show HN body

```text
I built Gemini Web MCP, an agent-first MCP Python SDK v2 gateway plus separate runtime and development skills for Gemini Web workflows.

The practical design is tool-surface control: model for text, core for multimodal content, a fixed compact facade when token cost matters, and all only for maintenance. Structured media results distinguish expected routing from observed backend evidence and verify local artifacts independently from response prose.

Credential-free MCP preflight:
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding

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
