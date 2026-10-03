---
name: gemini-assist
description: "Use Gemini for second opinions, sourced web search, image/file/URL understanding, and Deep Research. Media generation and account management have separate focused Skills."
license: MIT-0
metadata:
  compatibility: "Requires a connected gemini-mcp-assist server (Python 3.11+); live calls use the server's private Gemini Web authentication."
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
---

# Gemini Assist

Use the connected `gemini-mcp-assist` server (`gemini_assist_mcp`) for the
analysis or research needed by the user's task. Combine tools when the task
needs several steps.

| Task | Tool | Result to use |
| --- | --- | --- |
| Second opinion, critique, code or design review | `gemini_ask` | answer with optional context |
| Current-web search | `gemini_search` | answer, observed sources and grounding state |
| One image or screenshot | `gemini_understand_image` | analysis of the supplied image |
| Files, URLs or mixed inputs | `gemini_understand` | synthesis with per-input IDs and outcomes |
| Deep Research | `gemini_research` | operation handle, then report |

Read structured `ok`, `data`, `error` and `meta`. Incorporate the information
into the requested work. For search, `grounded` requires observed `sources`;
`answer_only`, `unavailable` and `failed` preserve their reported meaning.
Keep input identities and failures visible when interpreting mixed evidence.

## Research

Start with a question; the default asynchronous call returns `operation_id`
before the report is ready. Preserve the handle and any upstream IDs. Use
`gemini_research(action="status"|"result"|"cancel", operation_id=...)`
for continuation, including after reconnect. An optional opaque
`idempotency_key` lets a repeated start lookup the same run after response loss.
A timeout alone does not justify starting a second run.

A report is complete when its artifact is ready. `cancel_requested` is best
effort; confirmed provider cancellation and `local_cancelled_before_start`
have distinct evidence. Availability comes from the actual tool result.

## Connection and routing

The MCP server must be connected independently of this Skill. If setup is
needed, the source entrypoint is:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-assist
```

Use the server's private authentication setup. For generated media use
`$gemini-create`; for requested account work use `$gemini-account`.
Repository changes use `$gemini-web-mcp-development`.
