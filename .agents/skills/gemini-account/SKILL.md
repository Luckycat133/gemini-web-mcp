---
name: gemini-account
description: "Use this skill when the user explicitly asks to read, search, export, organize or delete Gemini account history, manage Notebooks, Scheduled actions, Gems, the local Prompt library, inspect account inventory, or observe/run/cancel registered cleanup jobs. Prefer this focused skill over gemini-web-mcp for explicit account work. Do not use as a prerequisite to media creation, general search or understanding, or repository development; route those to gemini-create, gemini-assist or gemini-web-mcp-development."
license: MIT-0
metadata:
  version: "0.2.2"
  compatibility: "Requires Python 3.11+, the gemini-mcp-account MCP server and authenticated Gemini Web Cookies for live requests. Install with uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-account."
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
        description: Optional authenticated Gemini Web Cookie, configured privately.
      - name: GEMINI_PSIDTS
        required: false
        description: Optional matching session Cookie recommended for stability.
      - name: GEMINI_PSIDCC
        required: false
        description: Optional matching Cookie forwarded when configured.
      - name: GEMINI_PROXY
        required: false
        description: Optional HTTP or HTTPS proxy.
      - name: GEMINI_STATE_DB_PATH
        required: false
        description: Optional private SQLite metadata location for restart recovery.
---

# Gemini Account

Use this Skill to complete the user's explicit Gemini account task. Do not use for repository implementation, general assistance or media generation.

## Choose The Facade

All calls take `request={"action": ..., ...}`. Action-specific schemas reject irrelevant fields and require mutation targets.

| Task | Tool | Read actions | Mutation actions |
| --- | --- | --- | --- |
| Conversation history | `gemini_history` | list, search, read, export | delete |
| Notebook organization | `gemini_notebooks` | list, chats | move |
| Scheduled actions | `gemini_scheduled` | list, get | create_daily, delete |
| Gems | `gemini_gems` | list | create, update, delete |
| Local Prompt library | `gemini_prompts` | list, categories, get, render | create, update, delete |
| Account inventory | `gemini_account` | capabilities, status, models, features, links, usage, library, modes | none |
| Registered cleanup | `gemini_cleanup` | status, test_artifacts with dry_run=true | run, cancel, test_artifacts with dry_run=false |

The catalog is exactly these seven tools. The entrypoint is `gemini-mcp-account`; the MCP name is `gemini_account_mcp`. `GEMINI_TOOLS` does not change this catalog.

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-account
```

## Explicit Scope, Exact Targets

Perform account mutations through MCP tools. Prefer the host's connected tools;
use a real MCP stdio client when verifying an installation or a surface the
host has not connected. Browser deletion and direct agent-side SDK/RPC calls
are separate diagnostic paths and do not prove that MCP deletion works.

Use private history reads or deletion only when the user asked for that account task. General search, image creation or a cleanup failure does not authorize browsing private conversations. User authorization already given for a specific task persists; do not ask for it repeatedly.

1. Select the requested facade/action. Inspect `meta.actionSemantics` and the discriminated request schema.
2. List/search metadata only as necessary to resolve exact IDs. Read/export private turns only when requested. Respect pagination and incomplete coverage; absence from a bounded list does not prove deletion.
3. For move/update/delete, use the observed chat_id/notebook_id/action_id/gem_id/prompt_id, never a guessed target.
4. Perform the authorized mutation once and read its structured evidence.
5. Report completed only when the requested state was verified. `accepted`, missing read-back, rejected RPC, changed shape and read errors are not success.

Scheduled create_daily requires title, instructions, hour 0–23 and timezone_name. Partial Gem/Prompt updates preserve omitted fields. Prompts use the local `prompts.json` library in the working directory and never call Gemini; render supplies variables without generating content.

## Cleanup

`status` lists only registered jobs in the current authentication scope. `run` with job_id retries that job; without it processes due registered jobs. `cancel` requires job_id and is best effort: it cannot undo deletion already in flight. A cleanup run result with `meta.verification_status=verified_absent` confirms deletion. Failed/pending states remain recoverable; do not convert them into a successful cleanup claim.

`test_artifacts` is a separate explicit, bounded marker scan. Supply the user's/test run's marker, preview with default dry_run=true, inspect exact matches, then delete only if that target set is authorized. Do not treat a broad default marker as proof of ownership or run a whole-account sweep.

Operation metadata survives restart for seven days; pending cleanup authority can be renewed until resolved. Private SQLite stores authentication-scoped IDs, states, deadlines, artifact locators and verification status; it stores no prompts, chat/report contents, cookies, raw responses or generated bytes. Local output files and the Prompt library are separate stores.

## Evidence And Boundaries

Read direct structured `ok`, `error`, `data`, `meta`. Input/Pydantic validation errors require corrected arguments; auth failures require the existing private account setup. Features reports RPC reachability, not account entitlement. Model names/usage are observed data, not inferred quota or marketing model versions.

Use `$gemini-assist` for search/critique/understanding/Research and `$gemini-create` for media creation. A user asking for a new image does not imply permission to read their Gemini history. Repository code/Skill development belongs to `$gemini-web-mcp-development`.

The Skill supplies routing instructions; an MCP server must also be connected. Keep authentication values out of conversation, command arguments and agent memory. Load [examples.md](references/examples.md) for calls and positive/near-miss routing cases.
