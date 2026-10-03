---
name: gemini-web-mcp
description: "Use available Gemini Web MCP tools for assistance, media creation, and account tasks. Select a focused Skill for its connected server; use this router for primary or compact compatibility tools."
license: MIT-0
metadata:
  compatibility: "Requires a connected Gemini Web MCP server (Python 3.11+); live calls use the server's private Gemini Web authentication."
  version: "0.2.2"
  openclaw:
    emoji: "♊️"
    homepage: https://github.com/Luckycat133/gemini-web-mcp
    requires:
      bins:
        - uvx
    primaryEnv: GEMINI_PSID
    envVars:
      - name: GEMINI_PSID
        required: false
        description: Optional __Secure-1PSID Cookie for authenticated Gemini Web calls.
      - name: GEMINI_PSIDTS
        required: false
        description: Optional matching __Secure-1PSIDTS Cookie recommended for session stability.
      - name: GEMINI_PSIDCC
        required: false
        description: Optional __Secure-1PSIDCC Cookie forwarded when configured.
      - name: GEMINI_PROXY
        required: false
        description: Optional HTTP or HTTPS proxy used by the MCP server.
      - name: GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS
        required: false
        description: Optional bounded macOS browser-credential authorization wait for Cookie discovery.
      - name: GEMINI_TOOLS
        required: false
        description: Optional primary-server tool profile such as model, core, history, or account-read.
---

# Gemini Web MCP

Choose a connected surface for the step the user's task needs. Focused and
compatibility tools can be composed; a Skill does not itself connect a server.

| Task | Focused route | Compatibility route |
| --- | --- | --- |
| Advice, search, image/file/URL understanding, Research | `$gemini-assist` / `gemini-mcp-assist` | compact chat or primary text/file/URL/Research tools |
| Generate/edit images, video or music | `$gemini-create` / `gemini-mcp-create` | compact create/edit or primary media tools |
| Requested history, Notebooks, Scheduled, Gems, Prompts, inventory, cleanup | `$gemini-account` / `gemini-mcp-account` | compact account facades or an appropriate primary profile |

Use the available tool's schema; focused and compatibility arguments differ.
Consult a manifest or diagnostics when discovery or recovery is needed.
Read the result's structured state, use returned information or verified
artifacts in the requested work, and preserve operation/source IDs for
continuation. A timeout or accepted mutation is not proof of completion.
Account access remains within the user's requested scope. Repository changes
use `$gemini-web-mcp-development`.

## Load details as needed

- [workflows.md](references/workflows.md): task routes and representative calls.
- [artifacts.md](references/artifacts.md): result envelopes, output verification
  and artifact handoff.
- [operations.md](references/operations.md): async starts, timeout/restart
  recovery, cancellation and source cleanup.
- [tool_surface.md](references/tool_surface.md): compact facades, primary
  profiles and account/authentication controls.
- [recovery.md](references/recovery.md): missing tools, authentication failures
  and upstream drift.
