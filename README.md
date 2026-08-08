<p align="center">
  <img src="docs/assets/gemini-web-mcp-banner.svg" alt="Gemini Web MCP" width="100%">
</p>

<h1 align="center">Gemini Web MCP</h1>

<p align="center">
  An agent-first MCP Python SDK v2 gateway and skills for Gemini Web workflows.
</p>

<p align="center">
  <a href="https://github.com/Luckycat133/gemini-web-mcp/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Luckycat133/gemini-web-mcp/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/Luckycat133/gemini-web-mcp/tree/main/.agents/skills/gemini-web-mcp"><img alt="Codex Skill" src="https://img.shields.io/badge/Codex%20Skill-installable-0B6BFF"></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0.html"><img alt="License" src="https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg"></a>
  <a href="docs/changelog.md"><img alt="Verified" src="https://img.shields.io/badge/tests-CI%20verified-1F8A70"></a>
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a>
</p>

> **Status: Active Development — Flagship Public Project**

> Disclaimer: this project is for technical research and educational use. It uses reverse-engineered Gemini Web behavior, which may violate Google service terms and may put accounts at risk. Use it at your own discretion.

## What It Does

Gemini Web MCP exposes Gemini Web capabilities to MCP-compatible clients such as Codex, Claude Desktop, VS Code MCP clients, and other agent runtimes.

The main design choice is controlled tool layering. Agents should not see every private, account-level, or destructive operation by default. This server ships narrow `GEMINI_TOOLS` profiles, facade tools, MCP annotations, and a public Codex skill that tells agents how to choose the right surface.

## MCP Protocol Compatibility

This server delegates MCP protocol behavior — discovery, version negotiation, JSON-RPC framing, the legacy `initialize` handshake, result validation, and structured protocol errors — to the official `mcp` Python SDK v2 through a dedicated `MCPServer` adapter. It contains no custom protocol stack; the codes in `error_handler.py` (`NO_COOKIE`, `INVALID_COOKIE`, `SESSION_NOT_FOUND`, …) are application-level errors returned in tool content, not JSON-RPC protocol errors.

The supported runtime is `mcp>=2,<3` plus `mcp-types>=2,<3`. CI exercises both current `server/discover` clients on protocol `2026-07-28` and compatibility clients using the `2025-11-25` initialize path. Every tool advertises an `outputSchema` and returns validated `structuredContent` while retaining its existing text. See the explicit [SDK/client compatibility and v1 end-of-support policy](docs/mcp-sdk-compatibility.md).

## Install The Runtime Skill

Install the `0.1.x` preview from ClawHub:

```bash
clawhub install gemini-web-mcp
```

Or install the current repository copy with the cross-agent `skills` CLI:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp \
  --agent codex --copy --yes
```

This runtime skill teaches an agent how to operate the installed tools safely. Repository contributors should install the separate development skill:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

The two roles are intentionally separate: `gemini-web-mcp` is for tool use; `gemini-web-mcp-development` owns implementation, tests, packaging, compatibility, and releases. `.agents/skills` is the single repository source so clients that scan both `.agents` and `.codex` do not discover duplicate names.

The three-file runtime skill bundle is released on ClawHub under MIT-0. The MCP server source and the repository-development skill remain AGPL-3.0-only.

## Install The MCP Server

One-command, credential-free proof (requires [uv](https://docs.astral.sh/uv/)):

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding
```

This installs into an isolated environment, starts the real stdio server with the `model` profile, and calls the static text manifest without forwarding Gemini Cookies or making a Gemini request. Pin `@main` to a reviewed commit SHA for immutable installs.

Minimal MCP client configuration:

```json
{
  "mcpServers": {
    "gemini": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Luckycat133/gemini-web-mcp@main",
        "gemini-mcp-server"
      ],
      "env": {
        "GEMINI_TOOLS": "model"
      }
    }
  }
}
```

For local development from source:

```bash
git clone https://github.com/Luckycat133/gemini-web-mcp.git
cd gemini-web-mcp
python -m venv .venv
. .venv/bin/activate
pip install -e ".[all,dev]"
```

Run the default content workflow surface:

```bash
GEMINI_TOOLS=core python -m src.server
# equivalent installed console entrypoint
GEMINI_TOOLS=core gemini-mcp-server
```

Run the compact, low-token facade:

```bash
gemini-mcp-skill-server
```

See [copyable Codex, Claude Desktop, Claude Code, and VS Code configurations plus verified text/image walkthroughs](docs/client-examples.md). Live examples require explicit account opt-in; no live Gemini request is part of PR CI.

## Tool Profiles

| Profile | Use When | Surface |
| --- | --- | --- |
| `model` / `chat` | The agent only needs Gemini model calls | Smallest model-call surface |
| `history` | The agent is organizing or searching chat history | `gemini_history` facade plus safe helpers |
| `history-organize` | The agent can move chats into native Gemini notebooks | History facade, notebook facade, explicit move tool |
| `account-read` | The agent needs read-only account inventory | `gemini_account_inventory` facade |
| `scheduled-read` | The agent only needs to inspect scheduled actions | Read-only scheduled actions |
| `scheduled-admin` | The user explicitly authorized scheduled-action create/delete | Scheduled mutation tools |
| `core` | General content workflows | Chat, media, files, research, manifest/cookie helpers |
| `all` | Maintainers are verifying the full surface | Full maintenance surface |

Use `model` as the primary starting profile for text-only work, `core` for multimodal/content workflows, and `gemini-mcp-skill-server` when a fixed eleven-tool facade is more valuable than the primary schemas. `all` is not a general default.

## Capabilities

| Area | Supported Workflows |
| --- | --- |
| Models | Gemini Web model aliases for Flash-Lite, Flash, Pro, thinking levels, and guided learning modes |
| Chat | One-shot chat, normalized collection of Gemini upstream streams, local sessions, temporary chat, saved Gem usage |
| Media | Image generation/editing, Veo video generation, Lyria 3 / Lyria 3 Pro music routing |
| History | List, scan, search, read, export, delete, and cleanup test artifacts |
| Notebooks | List native Gemini notebooks, inspect notebook chats, move chats into notebooks |
| Account Inventory | Public links, usage limits, library capabilities, modes, models, scheduled actions |
| Safety Metadata | MCP annotations, tool manifest, privacy/destructive-operation guidance |
| Distribution | Standalone Codex skill zip, wheel, source distribution, launch kit |

## Development Status

The maintained baseline is usable, but the development skill is not a completed feature checklist. Primary and compact
history list/search/read/export/delete now share typed results; a chat deletion is only called verified after positive
absence evidence from a complete fresh history-metadata read-back. An explicitly authorized targeted live run on
2026-08-08 validated Cookie initialization, temporary and retained text, multi-turn context, primary/compact typed history,
and verified deletion of every created chat. It was not a dedicated-account full canary and did not cover media, files,
URLs, Deep Research, or account mutations. Remaining work includes that broader live baseline, typed results for other
management actions, durable cleanup, and a shared long-operation job contract. The source version remains `1.3.0`; the next
public release line requires an explicit owner decision because higher historical tags already exist.

See [Development status and next steps](docs/development-status.md) for the implemented, partial, deferred, and owner-decision
boundaries. Offline CI or package success is not presented as current live Gemini behavior.

## Distribution Assets

The current supported one-command path installs the reviewed `main` source (or a pinned commit) through `uvx`. GitHub release history may contain older independent version lines; use a wheel only when its tag and filename match the source version you intend to run.

The tag release workflow builds:

- `gemini-web-mcp-skill-*.zip`: standalone Codex skill package
- `gemini_mcp_server-*-py3-none-any.whl`: Python wheel
- `gemini_mcp_server-*.tar.gz`: source distribution with docs, evaluations, and public skill files

Build the same package set locally:

```bash
python scripts/package_release.py --outdir dist
```

## Documentation

- [Quickstart](docs/quickstart.md)
- [Client installation and verified onboarding](docs/client-examples.md)
- [Configuration](docs/configuration.md)
- [Tool reference](docs/tools.md)
- [Live UI coverage](docs/live-ui-coverage.md)
- [Development status and next steps](docs/development-status.md)
- [Architecture](docs/architecture.md)
- [MCP SDK and client compatibility](docs/mcp-sdk-compatibility.md)
- [Opt-in live compatibility canary](docs/live-canary.md)
- [Launch kit](docs/launch-kit.md)
- [Changelog](docs/changelog.md)

## Verification

Maintained baseline:

```bash
./.venv/bin/python -m ruff check src tests scripts
./.venv/bin/python -m mypy src scripts
./.venv/bin/python -m pytest -q
./.venv/bin/python scripts/run_contract_checklist.py
./.venv/bin/python scripts/smoke_profiles.py
./.venv/bin/python scripts/smoke_mcp_protocol.py
git diff --check
```

Skill packaging check:

```bash
for path in \
  .agents/skills/gemini-web-mcp-development \
  .agents/skills/gemini-web-mcp; do
  skills-ref validate "$path"
done
```

## Security Notes

Do not commit `.env`, `cookies.json`, `prompts.json`, generated media, logs, or browser cookie material. Prefer `GEMINI_TOOLS=core` or narrower profiles unless the workflow requires account-level tools. Treat private chat text and destructive operations as explicit-user-intent actions. On macOS, browser-cookie access is bounded by `GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` (15 seconds by default) so an unanswered Keychain request returns a sanitized error instead of hanging the MCP process.
