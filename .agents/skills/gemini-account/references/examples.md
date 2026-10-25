# Account Examples And Routing Cases

Use exact IDs provided by the user or returned by authorized reads. Every tool takes a typed request object.

```json
{"tool":"gemini_history","arguments":{"request":{"action":"list","limit":20}}}
{"tool":"gemini_history","arguments":{"request":{"action":"read","chat_id":"<observed chat_id>"}}}
{"tool":"gemini_history","arguments":{"request":{"action":"update","chat_id":"<observed chat_id>","title":"New title","pinned":false}}}
{"tool":"gemini_history","arguments":{"request":{"action":"task_status","chat_id":"<observed Task chat_id>"}}}
{"tool":"gemini_notebooks","arguments":{"request":{"action":"create","title":"Reading notes","instructions":"Prefer the attached sources."}}}
{"tool":"gemini_notebooks","arguments":{"request":{"action":"add_source","notebook_id":"<observed notebook_id>","display_name":"Notes","text":"Source text supplied by the user."}}}
{"tool":"gemini_notebooks","arguments":{"request":{"action":"move","chat_id":"<observed chat_id>","notebook_id":"<observed notebook_id>"}}}
{"tool":"gemini_scheduled","arguments":{"request":{"action":"list","backend":"remy"}}}
{"tool":"gemini_scheduled","arguments":{"request":{"action":"create","backend":"remy","title":"Morning briefing","instructions":"Summarize current engineering news","trigger":{"type":"daily","hour":9},"timezone_name":"Asia/Shanghai","enabled":false}}}
{"tool":"gemini_scheduled","arguments":{"request":{"action":"resume","backend":"remy","action_id":"<observed native schedule id>"}}}
{"tool":"gemini_scheduled","arguments":{"request":{"action":"create_daily","title":"Morning briefing","instructions":"Summarize current engineering news","hour":9,"timezone_name":"Asia/Shanghai"}}}
{"tool":"gemini_gems","arguments":{"request":{"action":"update","gem_id":"<observed gem_id>","description":"Concise engineering critique"}}}
{"tool":"gemini_prompts","arguments":{"request":{"action":"create","name":"Review","content":"Review {code}"}}}
{"tool":"gemini_account","arguments":{"request":{"action":"capabilities"}}}
{"tool":"gemini_account","arguments":{"request":{"action":"skills","request":{"action":"create","name":"engineering-review","description":"Review engineering proposals","instructions":"Identify concrete risks and alternatives."}}}}
{"tool":"gemini_account","arguments":{"request":{"action":"skills","request":{"action":"upload","file_path":"/absolute/engineering-review.zip"}}}}
{"tool":"gemini_account","arguments":{"request":{"action":"skills","request":{"action":"disable","skill_id":"<observed skill_id>"}}}}
{"tool":"gemini_account","arguments":{"request":{"action":"library_assets","request":{"action":"list","section":"documents","page_size":30}}}}
{"tool":"gemini_account","arguments":{"request":{"action":"sharing","request":{"action":"delete","token":"<observed share token>"}}}}
{"tool":"gemini_account","arguments":{"request":{"action":"canvas","request":{"action":"create","prompt":"Create a Canvas document from these supplied notes."}}}}
{"tool":"gemini_account","arguments":{"request":{"action":"canvas","request":{"action":"edit","artifact_id":"<observed artifact_id>","text":"Complete replacement document text."}}}}
{"tool":"gemini_account","arguments":{"request":{"action":"canvas","request":{"action":"export","artifact_id":"<observed artifact_id>","output_path":"/absolute/new-document.md"}}}}
{"tool":"gemini_cleanup","arguments":{"request":{"action":"status","limit":20}}}
{"tool":"gemini_cleanup","arguments":{"request":{"action":"run","job_id":"<registered job_id>"}}}
```

Positive triggers: “查我的 Gemini 会话”; “Add this source to my Notebook”; “Pause this Gemini schedule”; “Upload this native Skill ZIP”; “Replace this Canvas document”; “Delete this public link”; “Retry this pending cleanup job”.

Near misses: “Look up current web sources” -> gemini-assist; “Generate a logo” -> gemini-create, without history reads; “Review the account service code” -> development. “Clean up the generated files in this local project” is local filesystem work, not Gemini account deletion.

A cancellation request never establishes that an already submitted deletion was undone. A bounded history page that does not contain an ID never establishes verified absence.

## Action details

### Notebooks and sources

`create` takes `title` and optional `instructions`; `update` changes either on
the exact `notebook_id`. Description writes are unavailable. Use `get`/`sources`
for current state. `add_source` takes `display_name` and exactly one of `text`,
`url`, `file_path` or an already observed `upload_reference`; it may remain
processing. `remove_source` takes the returned `source_name`. Notebook deletion
defaults to `delete_chats=false`; notebook absence does not prove chat deletion.

### Schedules

Native `create`, `update`, `pause`, `resume` and `delete` use `backend="remy"`.
List/get/delete default to the separate `legacy` backend, so retain the backend
alongside each ID. Legacy `create_daily` keeps title/instructions/hour semantics.
Supply the intended IANA `timezone_name`; omission uses `Asia/Shanghai`.

| Native trigger `type` | Fields |
| --- | --- |
| `hourly` | optional minute (0–59) |
| `daily` | hour (0–23), optional minute |
| `weekly` | days_of_week (1=Monday through 7=Sunday), hour, optional minute |
| `monthly` | days_of_month (1–31), hour, optional minute |
| `yearly` | month, day, hour, optional minute |
| `one_off` | unix_seconds |

### Native Skills, Gems and Prompts

Nested `skills` supports list/get/create/update/enable/disable/delete/upload.
Create uses name, description and instructions. Upload takes an absolute ZIP
`file_path` with a root `SKILL.md`; adding `skill_id` replaces that exact Skill.
Native Skills are the Web `/customize/skills` resources. Gem/Prompt updates
leave omitted fields unchanged; a rendered local Prompt is not submitted.

### Library, sharing and Canvas

Nested `library_assets` supports list/get/delete. Continue list with its returned
`next_page_token`, preserving `section`; `all` excludes purchases. Get/delete
use an exact returned `asset_id`. `id_kind="content_snapshot"` is a local snapshot
identity; removal still needs a unique source target. Incomplete scans, missing
source IDs and ambiguous grouped assets cannot establish safe removal. Current
Library deletion supports media kinds; Canvas/documents are rejected before a
mutation. Refresh snapshot identities after editing an asset.

Nested `sharing` supports list/create/delete. Create takes an observed `chat_id`,
title, optional `response_id` and `full_conversation`; it publishes a public link.
Delete takes one returned `token`, then verifies fresh absence.

Nested `canvas` supports create/get/chat_artifacts/versions/edit/export. Recover
creation by the returned `chat_id` through `chat_artifacts`, rather than repeating
it after a timeout. Get/versions/edit/export take the observed `artifact_id`.
Edit takes the complete replacement `text`, discards existing rich formatting
and updates that artifact without generating a new version. Only plain document
content without attachments/citations is supported. Export takes a new absolute
`output_path` and writes UTF-8 text; it is not a native PDF or Google Docs export.

### Cleanup

Cleanup `status` addresses jobs registered in the current authentication scope.
`run` with `job_id` targets that job; omission executes due registered jobs.
Only positive `verified_absent` evidence confirms deletion. The separate
`test_artifacts` action scans bounded metadata by marker and defaults to a dry
run; a broad marker is not proof that every match belongs to the requested task.

Operation metadata and cleanup jobs use the server's private local database;
IDs, states and locators persist there, while credentials and conversation
contents are excluded. Output files and the local Prompt library are separate
stores. Read the connected action schema when options or coverage differ.

Reproducible offline call/schema and expected-route cases live in evaluations/focused_skill_cases.json. These fixtures do not claim measured LLM trigger accuracy or live account mutations.

History `update` preserves omitted fields; `pinned=false` unpins. `task_status`
reads an existing native Task without resubmitting or deleting its source.
