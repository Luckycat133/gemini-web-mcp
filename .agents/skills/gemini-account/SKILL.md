---
name: gemini-account
description: "Manage Gemini history, Notebooks, Scheduled actions, Gems, local Prompts, account inventory, and registered cleanup jobs when the user requests account work."
license: MIT-0
metadata:
  compatibility: "Requires a connected gemini-mcp-account server (Python 3.11+); live calls use the server's private Gemini Web authentication."
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

Use the connected `gemini-mcp-account` server (`gemini_account_mcp`) for the
requested account task. All seven tools take a typed `request` object whose
`action` selects the operation.

| Task | Tool | Available actions |
| --- | --- | --- |
| History | `gemini_history` | list, search, read, export, delete |
| Notebooks | `gemini_notebooks` | list, chats, move |
| Scheduled actions | `gemini_scheduled` | list, get, create_daily, delete |
| Gems | `gemini_gems` | list, create, update, delete |
| Local Prompts | `gemini_prompts` | list, categories, get, render, create, update, delete |
| Account inventory | `gemini_account` | capabilities, status, models, features, links, usage, library, modes |
| Cleanup jobs | `gemini_cleanup` | status, run, cancel, test_artifacts |

## Select and verify the target

Keep private reads and mutations within the user's requested scope. Use
provided or observed IDs; list/search only as needed to resolve a target.
Pagination and incomplete coverage matter: omission from one page does not
prove absence. Preserve existing authorization instead of asking again.

Perform the action through its MCP facade and inspect structured `ok`, `data`,
`error` and `meta`. Claim a mutation completed only from positive read-back;
accepted, unavailable, rejected or mismatched results retain their reported
state. Features and usage report observations, not inferred entitlements.

Prompts live in the server's local `prompts.json` library. Rendering a Prompt
substitutes variables and does not submit it to Gemini. Partial Gem/Prompt
updates preserve omitted fields.

## Cleanup and setup

Address registered work by `job_id`: `status` observes jobs, `run` retries or
executes due work, and `cancel` withdraws eligible work. It cannot undo an
already in-flight deletion. `test_artifacts` is a separate bounded marker scan;
preview its default dry run and ensure deletion matches the authorized targets.

If setup is needed, the source entrypoint is:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-account
```

Use an installed version or checkout that provides this entrypoint and the
server's private authentication setup. Load [examples.md](references/examples.md)
for calls and action-specific details. Assistance and media generation use
`$gemini-assist` and `$gemini-create`; repository changes use `$gemini-web-mcp-development`.
