# Task Workflows

Load this reference only after the `gemini-web-mcp` Skill has selected the user's capability lane.

The current 0.2.x runtime has a low-token compatibility server plus narrow primary profiles. The dedicated `gemini-assist` MCP server (`gemini-mcp-assist`) now implements the assistance workflows below; `gemini-create` and `gemini-account` remain pending. All of them share the same services instead of duplicating business logic.

## 1. Ask Gemini for a Second Opinion

Use for critique, alternative reasoning, code review, design review, or comparing model opinions.

Current routes, in order:

```text
focused: gemini_ask(prompt=...)
compact: chat(message=..., model="flash"|"pro", thinking_level="standard"|"extended")
primary: gemini_chat(message=..., model=..., thinking_level=..., temporary=true)
```

Process:

1. Give Gemini the relevant context, not the entire unrelated conversation.
2. Ask a concrete question or request a concrete critique.
3. Separate Gemini's answer from the calling agent's own conclusion.
4. Resolve disagreements or present them explicitly.
5. Continue the user's task; do not stop at “Gemini said…”.

## 2. Quick Web Search

Use for current facts, documentation discovery, comparison, or a small number of sources.

Use focused `gemini_search` when connected. On compatibility servers, use `chat` or `gemini_chat` with an explicit request to search current sources and return source URLs.

Only label the result grounded when its structured `grounding_state` is `grounded` and `sources[]` contains citation URLs observed in the upstream response. Markdown links written in the answer are model text, not grounding evidence. In the 2026-09-26 installed-client check, Gemini responses exposed no structured citations, so `answer_only` is a realistic outcome even when the answer contains links. Verify such links independently before citing them as sources.

`gemini_search` returns this shape:

```text
answer
sources[]
observed_at
grounding_state = grounded | answer_only | unavailable | failed
```

Escalate to Deep Research when:

- the question is broad or disputed;
- multiple source classes must be reconciled;
- the user wants a durable report;
- quick search returns no structured source evidence and independently verified sources are required.

## 3. Understand an Image

Use for screenshots, UI mockups, charts, diagrams, photos, game scenes, and visual errors.

Current routes, in order:

```text
focused: gemini_understand_image(image=..., task=...)
compact: chat(message=<task>, image_path=<path>)
primary: gemini_chat(message=<task>, image_paths=[...])
```

Process:

1. State what should be inspected or compared.
2. Preserve image identity when several images are involved.
3. Ask for evidence tied to visible regions rather than a generic description.
4. Return the analysis to the calling agent.
5. Continue the surrounding task, such as fixing code or revising a design.

## 4. Understand Files, URLs, and Mixed Inputs

Use for documents, source files, PDFs, web pages, images plus code, or multiple evidence types.

Current routes:

```text
focused: gemini_understand(task=..., inputs=[...])
fallback primary:
gemini_upload_file
gemini_analyze_url
gemini_chat(image_paths=[...])
```

For mixed inputs, prefer one typed focused call. On the primary fallback, run the smallest bounded calls needed, preserve the source identity of every result, then synthesize the evidence in the calling agent.

Do not claim that a URL was fetched or a file was understood if the structured result only shows an accepted prompt without source or Artifact evidence.

The focused tool's typed input shape:

```json
{
  "task": "Compare the implementation with the design",
  "inputs": [
    {"id": "design", "kind": "image", "path": "..."},
    {"id": "spec", "kind": "file", "path": "..."},
    {"id": "live", "kind": "url", "url": "..."},
    {"id": "notes", "kind": "text", "text": "..."}
  ]
}
```

## 5. Deep Research

Use for multi-source investigation, market/technical research, long comparisons, or a report the agent will cite or reuse.

Current routes:

```text
dedicated: gemini_research(query=..., retain_chat=true)
or
primary: gemini_deep_research(
  query=...,
  wait_for_completion=false,
  retain_chat=true
)
```

Default behavior:

1. Start asynchronously.
2. Preserve every returned local and upstream identifier, especially `upstream_chat_id`.
3. The dedicated `operation_id` cannot currently be queried.
4. Inspect the retained chat by ID later using compact `history(action="read", chat_id=...)` or primary `gemini_history(action="read", chat_id=...)` in the history profile. These reads may truncate long turns and may show only a completion notice.
5. If the report text is absent, use the primary core profile's `gemini_create_from_research_report(chat_id=..., artifact_type="webpage")` to attempt a local report webpage; treat an empty or failed result as inconclusive.
6. Read the verified report content and use it in the user's requested output. Do not claim a Markdown report was saved unless one actually exists.

Do not restart the same research merely because the initial MCP wait ended.

The focused tool starts the operation by default and returns an opaque `operation_id` for correlation plus observed upstream IDs. No current status/result/cancel tool accepts that local ID.

## 6. Generate or Edit an Image

Current routes:

```text
compact: create(prompt=..., type="image", image_path=<optional reference>)
compact: edit(image_path=..., prompt=...)
primary: gemini_generate_media(... media_type="image")
```

In the 2026-09-26 Gemini Web UI check, Flash-Lite used Nano Banana 2 Lite;
Flash and Pro first passes used Nano Banana 2. The Pro redo was a separate Web
action. Check the current UI and returned `observed_backend` before claiming an
exact backend for a new run.

The compact `edit` call returned a remote image URI in the 2026-09-26 live
check. When the task needs a local file, call primary
`gemini_generate_media(media_type="image", image_path=..., output_dir=...)` and
verify the saved file. The live primary edit changed only the requested mug
color and preserved the cat and composition.

Process:

1. Choose a destination appropriate to the user's task.
2. Generate or edit.
3. Verify the returned Artifact.
4. Use the Artifact in the next step.
5. Only expose technical metadata when it helps the user or another tool.

## 7. Generate Music or Video

Current routes:

```text
compact: create(prompt=..., type="music")
primary: gemini_generate_media(media_type="music")
primary: gemini_generate_music
video fallback: https://gemini.google.com/videos in an authorized browser
```

The 2026-09-26 live MCP music call produced verified local MP3 and MP4 files.
Its response did not identify the exact Lyria version, even though current
Gemini announcements describe Lyria 3.5. Treat queued/running results as
incomplete. Current compatibility media calls do not provide a durable
operation handle.

The same account's generic `gemini_generate_media(media_type="video")` call
returned chat text and `ARTIFACT_NOT_RETURNED`. Gemini Web's dedicated Omni
video mode produced a verified downloadable video. Do not retry the generic
MCP call with a longer prompt and call that video generation. Use the dedicated
Videos page until a verified MCP mode route exists.

Process:

1. Start music with a bounded MCP call, or start video in the dedicated browser mode.
2. Preserve any observed upstream IDs.
3. If the call times out, inspect available chat/artifact evidence before considering another generation.
4. Verify the final media Artifact.
5. Use or attach the file in the user's task.

## 8. Explicit Account Work

Use only when the user asks to inspect or change Gemini account data.

| Intent | Compact route | Narrow primary route |
| --- | --- | --- |
| Read history | `history(action="list"|"search"|"read"|"export")` | `GEMINI_TOOLS=history` → `gemini_history` |
| Delete history or clean test artifacts | `history(action="delete")` / `cleanup(...)` | `GEMINI_TOOLS=history,manage:history-write` → `gemini_history` for identification, then `gemini_delete_chat` / `gemini_cleanup_test_artifacts` |
| Read Notebooks or move a chat | `account(action="notebooks")` for listing only | `GEMINI_TOOLS=history-organize` → `gemini_notebooks` / `gemini_move_chat_to_notebook` |
| Read account usage or inventory | `account(action="usage"|"status"|...)` | `GEMINI_TOOLS=account-read` → `gemini_account_inventory` |
| Manage Scheduled actions | `scheduled(action="list"|"get"|"create"|"delete")` | `GEMINI_TOOLS=scheduled-admin` → matching `gemini_*scheduled_action` tools |
| Manage Gems | unavailable | `GEMINI_TOOLS=manage:gems` → `gemini_manage_gems` |
| Manage local Prompts | `prompts(action=...)` | `GEMINI_TOOLS=prompts` → `gemini_manage_prompts` |

Use list/search/read before mutation. Preserve returned IDs. Claim mutation success only after authoritative read-back.
