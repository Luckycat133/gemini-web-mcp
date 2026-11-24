# Client installation and verified onboarding

These examples use the [development branch from PR #39](source-update-integration.md) through `uvx`, which creates an isolated tool environment. For an immutable install, replace the branch after `@` with a reviewed commit SHA. The published 0.2.2 release uses different server entrypoints and account schemas.

## One-command offline proof

With uv installed, run this command to start `gemini-mcp-function` from the development source, check its six-tool catalog and verify the typed `INVALID_ARGUMENT` response to an empty `gemini_ask` prompt. [uv installation](https://docs.astral.sh/uv/)

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 gemini-mcp-onboarding
```

The command removes Gemini Cookie variables from the child process and makes no Gemini request. Check for `mode=offline`, `credentials_accessed=false`, `profile=function`, `enabled_tools=6`, and `text_tool=gemini_ask`.

## Authentication

Live model calls can discover Cookies from a signed-in browser automatically. When supplying Cookies explicitly, export secrets in the client host or use the client's secret-input facility; never pass Cookies as command-line arguments or commit them.

For browser discovery, install the browser dependencies too. A full desktop entry can use the following arguments, with no Cookie placeholders in `env`:

```json
{
  "command": "uvx",
  "args": ["--from", "gemini-mcp-server[all] @ git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101", "gemini-mcp-function"]
}
```

The base package leaves browser, image, and browser-control dependencies optional. A supplied PSID takes priority and constrains refresh to that credential lineage. Leave the Cookie variables unset for automatic discovery.

```bash
export GEMINI_PSID='your __Secure-1PSID value'
export GEMINI_PSIDTS='your __Secure-1PSIDTS value'  # optional but recommended
```

See [Cookie setup](cookie-setup.md) and run the offline preflight before a live call.
Browser-based discovery on macOS bounds Keychain access to 15 seconds by default. Override
`GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` only when the host needs a different local timeout; a timeout is a diagnostic,
not evidence that no matching browser profile exists.

## Copyable client configurations

These files are for manually supplied credentials. For Chrome autoload, use the `[all]` arguments above and omit the Cookie variables. For image generation or editing with manual credentials, also use those arguments so the server can decode the returned images.

- [Codex `config.toml`](../examples/clients/codex.config.toml) uses the documented `mcp_servers` table and forwards locally exported Cookie variables.
- [Claude Desktop JSON](../examples/clients/claude-desktop.json) uses `mcpServers`; replace its Cookie placeholders locally and restrict file permissions.
- [Claude Code `.mcp.json`](../examples/clients/claude-code.mcp.json) uses Claude Code's `${VAR}` environment expansion.
- [VS Code `mcp.json`](../examples/clients/vscode.mcp.json) uses the `servers` key and password input variables instead of committed secrets.

Authoritative configuration references: [Codex MCP](https://developers.openai.com/codex/mcp/), [Claude Code MCP](https://code.claude.com/docs/en/mcp), and [VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

## Choose a surface

The supplied client configurations connect only `gemini-mcp-function` by default. Its six tools cover Chat, Task, media, Research, status and result retrieval. Use `gemini_ask` for ordinary questions, current web information and mixed inputs. Name an available app naturally when needed. Flash is the default. Notebook IDs start background Chat, while `gemini_run_task` starts Task. Keep the original operation handle for either workflow. [Notebook, Chat and Task workflows](notebook-task-workflows.md)

For explicitly requested account management, configure a separate server entry. This optional entry is not enabled by the supplied default files. For Codex, copy the account-only TOML as a standalone entry. [Optional Codex account config](../examples/clients/codex.account.config.toml)

```json
{
  "mcpServers": {
    "gemini-account": {
      "command": "uvx",
      "args": ["--from", "gemini-mcp-server[all] @ git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101", "gemini-mcp-account"]
    }
  }
}
```

Configure the account entry's authentication as you do for the function entry, or use browser discovery. Its six tools cover history, chat organization and deletion, Notebooks, and read-only native Task metadata and progress. You can connect just this entry for conversation management. Enable the function entry for generation. Host controls for enabling servers vary by client.

Use `gemini-mcp-server` with the server entry's `env.GEMINI_TOOLS` set to `function-full` or `account-full` when a workflow needs the corresponding prior 17-tool catalog. The latter includes schedules, Skills, preferences and explicit browser inputs. Its `gemini_cleanup` acts only on MCP-owned ledger records, never title matches.

The older `gemini-mcp-assist` and `gemini-mcp-skill-server` entrypoints remain compatibility options. Explicit `GEMINI_TOOLS=model` or `GEMINI_TOOLS=core` still selects the corresponding legacy profile when running `gemini-mcp-server`. Do not set that variable for the default function entry.

## Explicit live examples

Live examples require the flag as well as Cookie configuration. Text uses the function assistance tool:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 \
  gemini-mcp-onboarding chat \
  --allow-live-account \
  --prompt 'Reply with exactly: Gemini MCP is connected'
```

The image example installs the image-verification extra, starts the function surface background task, retrieves the same operation handle, and independently rejects response prose, a remote-only URI, a missing/empty file, MIME mismatch, missing dimensions, or an unverified artifact:

```bash
uvx \
  --from 'gemini-mcp-server[image] @ git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101' \
  gemini-mcp-onboarding image \
  --allow-live-account \
  --prompt 'A two-color geometric cat icon on a plain background' \
  --output-dir ./gemini-artifacts \
  --filename onboarding-cat
```

Successful JSON contains `local_path`, non-zero `size_bytes`, an `image/*` MIME type, positive `width`/`height`, and `verification=verified`.

## Expected routing versus observation

Media results deliberately keep four fields separate:

- `requested_model`: the user's alias, such as `flash` or `pro`;
- `request_model`: the model identifier sent by the adapter;
- `effective_backend`: the repository's expected routing label;
- `observed_backend`: a backend marker actually returned by the current upstream response, or `null` when none was reported.

An `effective_backend` label is not evidence that the live Gemini Web backend used that implementation. The onboarding client reports `observed_backend_status=not_reported` instead of promoting an expectation to an observation. Local artifact verification proves the file, not the upstream generator identity.

## Runtime skill versus development skill

Install the runtime workflow skill when an agent should operate the MCP tools:

```bash
npx --yes skills@1.5.21 add \
  "https://github.com/Luckycat133/gemini-web-mcp#codex/gemini-tools-polish-20261101" \
  --skill gemini-web-mcp \
  --agent codex --copy --yes
```

Contributors changing this repository should install the separate development workflow:

```bash
npx --yes skills@1.5.21 add \
  "https://github.com/Luckycat133/gemini-web-mcp#codex/gemini-tools-polish-20261101" \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

The runtime skill teaches safe tool use. The development skill owns architecture, tests, packaging, compatibility, and release gates; neither installs the MCP server itself.
