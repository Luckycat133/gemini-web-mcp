<p align="center">
  <img src="docs/assets/gemini-web-mcp-banner.svg" alt="Gemini Web MCP" width="100%">
</p>

<h1 align="center">Gemini Web MCP</h1>

<p align="center">
  A layered FastMCP server and Codex skill for Gemini Web workflows.
</p>

<p align="center">
  <a href="https://github.com/Luckycat133/gemini-web-mcp/releases/latest"><img alt="Release" src="https://img.shields.io/github/v/release/Luckycat133/gemini-web-mcp?label=release"></a>
  <a href="https://github.com/Luckycat133/gemini-web-mcp/tree/main/.agents/skills/gemini-web-mcp"><img alt="Codex Skill" src="https://img.shields.io/badge/Codex%20Skill-installable-0B6BFF"></a>
  <a href="https://www.gnu.org/licenses/agpl-3.0.html"><img alt="License" src="https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg"></a>
  <a href="docs/changelog.md"><img alt="Verified" src="https://img.shields.io/badge/tests-70%20passing-1F8A70"></a>
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a>
</p>

> Disclaimer: this project is for technical research and educational use. It uses reverse-engineered Gemini Web behavior, which may violate Google service terms and may put accounts at risk. Use it at your own discretion.

## What It Does

Gemini Web MCP exposes Gemini Web capabilities to MCP-compatible clients such as Codex, Claude Desktop, VS Code MCP clients, and other agent runtimes.

The main design choice is controlled tool layering. Agents should not see every private, account-level, or destructive operation by default. This server ships narrow `GEMINI_TOOLS` profiles, facade tools, MCP annotations, and a public Codex skill that tells agents how to choose the right surface.

## Install The Codex Skill

Install the public skill with the cross-agent `skills` CLI:

```bash
npx skills add https://github.com/Luckycat133/gemini-web-mcp/tree/main/.agents/skills/gemini-web-mcp
```

The CLI can install the skill for Codex, Claude Code, Gemini CLI, Cline, and other supported agents. The skill lives at [.agents/skills/gemini-web-mcp](.agents/skills/gemini-web-mcp); the local development copy at [.codex/skills/gemini-web-mcp](.codex/skills/gemini-web-mcp) is kept byte-for-byte identical by tests.

## Install The MCP Server

Fastest verified path (requires [uv](https://docs.astral.sh/uv/)):

```bash
GEMINI_TOOLS=model uvx \
  --from https://github.com/Luckycat133/gemini-web-mcp/releases/download/v2.1.2/gemini_mcp_server-2.1.2-py3-none-any.whl \
  gemini-mcp-server
```

Minimal MCP client configuration:

```json
{
  "mcpServers": {
    "gemini": {
      "command": "uvx",
      "args": [
        "--from",
        "https://github.com/Luckycat133/gemini-web-mcp/releases/download/v2.1.2/gemini_mcp_server-2.1.2-py3-none-any.whl",
        "gemini-mcp-server"
      ],
      "env": {
        "GEMINI_TOOLS": "core"
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
pip install -e ".[all]"
```

Run the default content workflow surface:

```bash
GEMINI_TOOLS=core python -m src.server
```

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

## Capabilities

| Area | Supported Workflows |
| --- | --- |
| Models | Gemini Web model aliases for Flash-Lite, Flash, Pro, thinking levels, and guided learning modes |
| Chat | One-shot chat, streamed chat, local sessions, temporary chat, saved Gem usage |
| Media | Image generation/editing, Veo video generation, Lyria 3 / Lyria 3 Pro music routing |
| History | List, scan, search, read, export, delete, and cleanup test artifacts |
| Notebooks | List native Gemini notebooks, inspect notebook chats, move chats into notebooks |
| Account Inventory | Public links, usage limits, library capabilities, modes, models, scheduled actions |
| Safety Metadata | MCP annotations, tool manifest, privacy/destructive-operation guidance |
| Distribution | Standalone Codex skill zip, wheel, source distribution, launch kit |

## Release Assets

Latest release: <https://github.com/Luckycat133/gemini-web-mcp/releases/latest>

Each release includes:

- `gemini-web-mcp-skill-*.zip`: standalone Codex skill package
- `gemini_mcp_server-*-py3-none-any.whl`: Python wheel
- `gemini_mcp_server-*.tar.gz`: source distribution with docs, evaluations, and public skill files

Build the same package set locally:

```bash
python scripts/package_release.py --outdir dist
```

## Documentation

- [Quickstart](docs/quickstart.md)
- [Configuration](docs/configuration.md)
- [Tool reference](docs/tools.md)
- [Live UI coverage](docs/live-ui-coverage.md)
- [Architecture](docs/architecture.md)
- [Launch kit](docs/launch-kit.md)
- [Changelog](docs/changelog.md)

## Verification

Maintained baseline:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m py_compile src/tools/annotations.py src/tools/chat.py src/tools/media.py src/tools/file.py src/tools/research.py src/tools/prompts.py src/tools/manage.py src/server.py src/skill_server.py src/client_wrapper.py src/thinking_client.py src/constants.py
git diff --check
```

Skill packaging check:

```bash
for path in .codex/skills/gemini-web-mcp .agents/skills/gemini-web-mcp; do
  ./.venv/bin/python /Users/jack/.codex/skills/.system/skill-creator/scripts/quick_validate.py "$path"
done
```

## Security Notes

Do not commit `.env`, `cookies.json`, `prompts.json`, generated media, logs, or browser cookie material. Prefer `GEMINI_TOOLS=core` or narrower profiles unless the workflow requires account-level tools. Treat private chat text and destructive operations as explicit-user-intent actions.
