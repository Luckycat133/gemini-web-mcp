# Client installation and verified onboarding

These examples use the current `main` source through `uvx`, which creates an isolated tool environment. Pin `@main` to a reviewed commit SHA when you need an immutable installation. Do not substitute `releases/latest` blindly: a release tag and wheel filename must both match the source version you intend to run.

## One-command offline proof

With [uv](https://docs.astral.sh/uv/) installed, this command installs the current source, starts its real stdio server, negotiates MCP, and calls `gemini_get_tool_manifest`:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding
```

The command removes Gemini Cookie variables from the child process and reports `mode=offline`, `credentials_accessed=false`, the negotiated protocol version, server version, profile, and enabled-tool count. It makes no Gemini request.

## Authentication

The authenticated local runtime currently requires macOS/Linux POSIX file
protections. Windows local authentication is unsupported until a secure
private-file/ACL implementation is accepted. Desktop configuration examples
describe client formats; they do not establish server support on that OS.

Live model calls use Gemini Web Cookies and therefore carry account and Terms-of-Service risk. Export secrets in the client host or use that client's secret-input facility; never pass Cookies as command-line arguments or commit them.

```bash
export GEMINI_PSID='your __Secure-1PSID value'
export GEMINI_PSIDTS='your __Secure-1PSIDTS value'  # optional but recommended
```

See [Cookie setup](cookie-setup.md) and run the offline preflight before a live call.
Browser-based discovery on macOS bounds Keychain access to 15 seconds by default. Override
`GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` only when the host needs a different local timeout; a timeout is a diagnostic,
not evidence that no matching browser profile exists.

## Copyable client configurations

- [Codex `config.toml`](../examples/clients/codex.config.toml) uses the documented `mcp_servers` table and forwards locally exported Cookie variables.
- [Claude Desktop JSON](../examples/clients/claude-desktop.json) uses `mcpServers`; replace its Cookie placeholders locally and restrict file permissions.
- [Claude Code `.mcp.json`](../examples/clients/claude-code.mcp.json) uses Claude Code's `${VAR}` environment expansion.
- [VS Code `mcp.json`](../examples/clients/vscode.mcp.json) uses the `servers` key and password input variables instead of committed secrets.

Authoritative configuration references: [Codex MCP](https://developers.openai.com/codex/mcp/), [Claude Code MCP](https://code.claude.com/docs/en/mcp), and [VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration).

Focused configurations for all three capability lanes are also available:
[Codex](../examples/clients/codex.focused.config.toml),
[Claude Desktop](../examples/clients/claude-desktop.focused.json),
[Claude Code](../examples/clients/claude-code.focused.mcp.json), and
[VS Code](../examples/clients/vscode.focused.mcp.json).
Connect only the lanes needed for the task. Share `GEMINI_STATE_DB_PATH` when
multiple local processes should recover the same operations and cleanup jobs.

## Pick one surface

| Surface | Select it when | Trade-off |
| --- | --- | --- |
| `gemini-mcp-assist` | Second opinion, sourced search, image/mixed-input understanding, Research | Five focused tools; explicit Research actions recover a saved handle |
| `gemini-mcp-create` | Image generation/editing, video/music operations | Seven focused tools; verified files and restart recovery |
| `gemini-mcp-account` | Explicit Gemini history/admin/cleanup | Seven typed action facades; account reads/mutations require user intent |
| Primary + `GEMINI_TOOLS=model` | Text/model calls are the goal | Recommended starting point; smallest primary model surface |
| Primary + `GEMINI_TOOLS=core` | Images, music, files, URLs, or Deep Research are needed | Broader content surface and longer tool timeouts; use Gemini Web's dedicated Videos page for video until its MCP route returns a verified artifact |
| `gemini-mcp-skill-server` | The client benefits from a fixed, low-token facade | Eleven workflow tools; compact presentation, shared service semantics |
| Primary + `GEMINI_TOOLS=all` | A maintainer is verifying account/history/admin coverage | Largest and most sensitive surface; not a general default |

Change `GEMINI_TOOLS` from `model` to `core` in a copied client configuration for multimodal work. To use the compact facade, change only the final argument from `gemini-mcp-server` to `gemini-mcp-skill-server`; `GEMINI_TOOLS` does not resize the compact surface.

## Explicit live examples

Live examples require the flag as well as Cookie configuration. Text uses a temporary chat:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main \
  gemini-mcp-onboarding chat \
  --allow-live-account \
  --prompt 'Reply with exactly: Gemini MCP is connected'
```

The image example installs the image-verification extra, asks the primary `core` surface to save the result, and independently rejects response prose, a remote-only URI, a missing/empty file, MIME mismatch, missing dimensions, or an unverified artifact:

```bash
uvx \
  --from 'gemini-mcp-server[image] @ git+https://github.com/Luckycat133/gemini-web-mcp@main' \
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
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp \
  --agent codex --copy --yes
```

Contributors changing this repository should install the separate development workflow:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

The runtime skill teaches safe tool use. The development skill owns architecture, tests, packaging, compatibility, and release gates; neither installs the MCP server itself.

## Client and OS evidence

The initial supported runtime is Python 3.11+ with MCP SDK v2 over local stdio,
installed from a built wheel or pinned source with uvx. Media images require
`[image]` (or `[all]`) for independent decoding; audio/video verification needs
working ffprobe/ffmpeg binaries in the server's PATH.

| Combination | Evidence | Boundary |
| --- | --- | --- |
| macOS arm64, Python SDK v2 stdio client | Modern/legacy protocol, wheel/uvx, bounded Chrome media tests | Primary, compact and focused surfaces; signed-in coverage is modality-specific |
| Linux, supported CI Python matrix | Offline tests, package and real stdio gates | CI does not access a Gemini account |
| Codex, Claude Desktop/Code, VS Code configs | Parsed copyable configurations | Desktop-host end-to-end behavior requires a separate run |
| Windows | Authenticated local runtime unsupported; no runtime/ACL acceptance | Missing POSIX protections fail with a safe storage error before writing state; configuration templates are format examples only |

An agent-use handoff can exercise a Skill with an existing verified file without
making another generation request. This proves task routing/use of that file;
it does not replace fresh upstream acceptance for another modality.
