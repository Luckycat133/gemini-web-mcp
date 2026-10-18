---
name: gemini-account
description: "Manage requested Gemini history, Web Notebooks and sources, schedules, native Skills, Canvas, Library and sharing, plus Gems, local Prompts and cleanup jobs."
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
| History | `gemini_history` | list, search, read, export, update, delete, task_status |
| Notebooks | `gemini_notebooks` | list, get, create, update, delete, sources, add_source, remove_source, chats, move |
| Scheduled actions | `gemini_scheduled` | list, get, create, update, pause, resume, delete; legacy create_daily |
| Gems | `gemini_gems` | list, create, update, delete |
| Local Prompts | `gemini_prompts` | list, categories, get, render, create, update, delete |
| Account resources | `gemini_account` | capabilities, status, models, features, links, usage, library, modes; nested skills, sharing, library_assets, canvas |
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

## Resource workflows

Notebook writes support title and instructions; description is not writable.
Source ingestion can remain processing after acceptance. These Gemini Web
Notebooks have separate IDs from NotebookLM attachments used by assistance.

Use `backend="remy"` for native schedules and preserve it with the returned ID.
Native create/update supports hourly, daily, weekly, monthly, yearly and one-off
triggers; pause/resume changes the existing schedule. Legacy daily actions keep
their separate backend.

Native Skills use the nested `gemini_account` request with `action="skills"`,
including ZIP upload. Gems are reusable personas; Prompts live in the server's local library
and render variables without submitting to Gemini. Updates preserve omitted fields.

For Library assets, follow page tokens and use returned identities. A snapshot
ID is not a native ID; deletion requires an unambiguous supported media target.
Canvas/document removal from Library is unavailable. Sharing
creates a public link or deletes one exact token within the requested scope.

Canvas uses a nested account request to create, get, list versions, edit or
export. Edit replaces a plain document's entire text and formatting on the same
artifact; it does not create a new version. Attachments and citations are outside
this edit scope. Export writes UTF-8 text to a new absolute output path.

## Cleanup and setup

Address registered work by `job_id`: `status` observes jobs, `run` retries or
executes due work, and `cancel` withdraws eligible work. It cannot undo an
already in-flight deletion. `test_artifacts` is a separate bounded marker scan;
preview its default dry run and ensure deletion matches the authorized targets.

Use a checkout or installed version providing these tools and parameters.
For a checkout:

```bash
uvx --from /absolute/path/to/gemini-web-mcp gemini-mcp-account
```

Use the server's private authentication setup. Load [examples.md](references/examples.md)
for calls and action-specific details. Assistance and media generation use
`$gemini-assist` and `$gemini-create`; repository changes use `$gemini-web-mcp-development`.
