# Task Routes and Calls

Read when choosing between connected focused and compatibility tools. Use each
server's registered schema: identical tool names can have different arguments.

## Assistance and Understanding

| Task | Focused call | Compatibility call |
| --- | --- | --- |
| Advice or critique | `gemini_ask(prompt=..., context=...)` | compact `chat(message=...)` or primary `gemini_chat(message=...)` |
| Current-web sources | `gemini_search(query=...)` | compact/primary chat with a sourcing request |
| One image | `gemini_understand_image(image=..., task=...)` | compact `chat(message=..., image_path=...)` or primary `gemini_chat(message=..., image_paths=[...])` |
| A local file | `gemini_understand(task=..., inputs=[...])` | primary `gemini_upload_file(file_path=..., analysis_prompt=...)` |
| A URL | `gemini_understand(task=..., inputs=[...])` | primary `gemini_analyze_url(url=..., analysis_prompt=...)` |

Search returns `answer`, `sources`, `observed_at` and
`grounding_state=grounded|answer_only|unavailable|failed`. Only observed structured
source URLs support `grounded`; Markdown links in the answer are leads to verify
independently. `recency` and `domains` express prompt preferences, not enforced
retrieval filters. Use Research when the task calls for a broader investigation.

Mixed-input calls preserve a unique caller-supplied ID for each input:

```json
{
  "task": "Compare the implementation with the design",
  "inputs": [
    {"id": "design", "kind": "image", "path": "/absolute/design.png"},
    {"id": "code", "kind": "file", "path": "/absolute/app.py"},
    {"id": "docs", "kind": "url", "url": "https://example.org/spec"},
    {"id": "notes", "kind": "text", "text": "Check the error state."}
  ]
}
```

The focused schema permits at most 16 inputs. Local files are uploaded; remote
image/file URLs and web URLs are referenced in the prompt. Inspect each outcome:
`accepted` means submitted, `analyzed` means the completed analysis acknowledged
that input, and `skipped`/`failed` retain their reasons. An input URI or file
identity alone does not prove its contents were fetched or analyzed. For primary
fallback calls, retain source identities when combining their analyses.

## Deep Research

```text
focused: gemini_research(query=..., idempotency_key=<new opaque token>)
primary: gemini_deep_research(query=..., wait_for_completion=false, retain_chat=true,
                            idempotency_key=<new opaque token>)
```

Focused starts return immediately. Primary Research otherwise defaults to waiting
for completion. Preserve the opaque `operation_id` and any observed upstream IDs;
use the same tool's `action="status"|"result"|"cancel"` with that handle. Result
recovery saves a verified report under `generated_reports/` on the server.
Read the report and use its evidence in the requested deliverable.

If an older compatibility result provides only a known chat ID, compact
`history(action="read", chat_id=...)` or primary
`gemini_history(action="read", chat_id=...)` can inspect that source. Such reads
may truncate turns or expose only a completion notice. Primary
`gemini_create_from_research_report(chat_id=..., artifact_type="webpage")` can
attempt report extraction; an empty or failed extraction remains inconclusive.
Recovery stays within this requested research source. See
[operations.md](operations.md) for restart, timeout and cancellation semantics.

## Media Creation

```text
focused: gemini_generate_image(prompt=..., output_dir=...)
focused: gemini_edit_image(image_path=..., prompt=..., output_dir=...)
focused: gemini_generate_video(prompt=..., idempotency_key=<new opaque token>)
focused: gemini_generate_music(prompt=..., idempotency_key=<new opaque token>)
compact: create(prompt=..., type="image", image_path=<optional reference>)
compact: edit(image_path=..., prompt=...)
primary: gemini_generate_media(prompt=..., media_type="image",
                               image_path=<optional reference>, output_dir=...)
primary: gemini_generate_music(prompt=..., output_dir=...)
```

Compatibility type/media_type also accepts "video" or "music".
Image generation/editing is synchronous. Focused video/music returns durable
handles for `gemini_get_operation_status`, `gemini_get_operation_result` and
`gemini_cancel_operation`. Compatibility media may wait or return queued output
without a durable handle. Recover an existing run through its returned handle or
known source rather than repeating generation.

Native feature selection and model aliases do not identify the observed media
backend or prove generation succeeded. Accept matching verified output files;
text and search-reference images cannot satisfy creation. Use
[artifacts.md](artifacts.md) for verification and the requested handoff.

## Requested Account Work

Focused account tools take `request={"action": ..., ...}`. Compact and primary
tools usually take flat arguments. For example:

```text
focused: gemini_history(request={"action": "delete", "chat_id": <observed ID>})
compact: history(action="delete", chat_id=<observed ID>)
primary: gemini_delete_chat(chat_id=<observed ID>)
```

Primary `gemini_history` is read-only and cannot perform the focused facade's
delete action. Similarly, focused `gemini_notebooks` supports move, while primary
uses `gemini_move_chat_to_notebook`. Compact Scheduled creation is
`scheduled(action="create", ...)`; focused uses `request.action="create_daily"`.
Compact Prompts identifies targets by name; focused Prompts uses `prompt_id`.

Resolve exact targets through the reads needed for the authorized task, respect
pagination and content-read scope, then inspect mutation read-back. Account,
cleanup, authentication and profile details are in
[tool_surface.md](tool_surface.md).
