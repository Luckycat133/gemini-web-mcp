# Task Workflows

Load this reference only after the `gemini-web-mcp` Skill has selected the user's capability lane.

Use `gemini-mcp-function` for Chat, Task, media and research. Connect `gemini-mcp-account` separately for existing conversation and resource management. Use `function-full` or `account-full` only for workflows that need the prior catalog.

## Chat or Task: Intent First

Default to Chat for answers, research, writing, analysis and direct app requests supported in Chat, even when the request is complex or takes multiple steps. Choose Task when explicitly requested, or when delegating a workflow needing Gemini's ongoing execution, tracked work or browser operation. This is a routing preference, not an absolute limit: Task can also research and create files, while Chat can perform app writes and run scheduled content.

Notebook context, native Gemini Skills, connected apps, a long response and background execution are shared features or execution details, not reasons to switch modes. Use the requested app/action's actual availability: the native GitHub repository-import connector is currently Chat-only, and Contacts is Task-only. Do not generalize these connector limits to browser or custom MCP routes.

Examples: compare three Notebook sources and write a report → Chat; directly create a Keep note → Chat if that action is available; delegate an ongoing workflow that visits sites, processes results and tracks completion → Task. A recurring morning digest can use Chat scheduled actions; it does not automatically need Task. Task time/event plans are a separate product feature.

Sources checked 2026-11-01: [Task workflows](https://support.google.com/gemini/answer/17094507?hl=en), [app/mode matrix](https://support.google.com/gemini/table/17434654?hl=en), [shared Skills](https://support.google.com/gemini/answer/17094296?hl=en), [Chat scheduled actions](https://support.google.com/gemini/answer/16316416).

### Meaningful Settings

| Setting | What the caller should know |
| --- | --- |
| Model | Flash compatibility default prefers discovered Hydrogen. Explicit Neon/Carbon choices require the current account registry and verified plan. Old Argon/Pro are kept only when still discovered. Unsupported selections are refused without switching workflow or model. |
| Thinking | Use `low` / `medium` / `high`; `standard` / `extended` remain low/high aliases. The client aligns request body and model header. Lite has no selectable level and remains a quota-gated quick Chat fallback. |
| Notebook context | Supply exact discovered `notebook_ids` to either function Chat or Task. Adding a Notebook does not select Task. |
| Retention | Use `retain_chat=true` only when the user wants the thread kept. Default cleanup follows verified answer saving. An associated Task schedule protects the owned thread. |
| Browser preference | In `account-full`, use `gemini_preferences(action="get_browser")` to read the saved choice. Update with `set_browser`, `browser="automatic"` or `browser="remote_only"`, and the returned revision. Verify the saved value by read-back. |
| Permissions | In `account-full`, use returned action handles from `gemini_preferences` to explicitly set `always_ask` / `always_allow`. An always-allow change is persistent. Sensitive steps may still need confirmation, and one-time approvals use the original card. |
| Schedules | Use `account-full` for schedules. State timing, frequency, timezone and any start/end constraints. Task supports six time recurrences. Chat schedules use their own tools. |

Do not ask the user to turn on web search or tool use. Name the app and goal naturally. Native Skills work in both modes, but this MCP has no explicit native Skill-ID attachment argument. Handle a real missing entitlement, connection or permission request when returned; do not introduce a setup questionnaire.

Native Task supports further instructions while running. This MCP currently supports exact returned confirmation-card replies, not arbitrary added instructions or replanning. Reusing an operation handle without a card token only retrieves the original work.

Settings sources: [models and native thinking](https://support.google.com/gemini/answer/18648722?hl=en), [Task browser and permissions](https://support.google.com/gemini/answer/18766567?hl=en), [Task schedules](https://support.google.com/gemini/answer/17094710).

## Notebook Sources and Results

For a user-requested Notebook source question, discover the exact Notebook ID with account `gemini_notebooks(action="list")`. Its default `source="notebooklm"` returns compact source-picker metadata, including title and source count, without reading source text. Preserve the returned ID unchanged.

Choose the execution mode by intent:

- Ordinary source question: function `gemini_ask(prompt=..., notebook_ids=[...], timeout_seconds=600)` starts background ordinary chat (`execution_mode="chat"`). It does not enable Task. Without Notebook IDs, Ask keeps its ordinary direct-response path; its function-only `operation_id` requires Notebook IDs.
- Delegated Task execution: function `gemini_run_task(prompt=..., notebook_ids=[...], timeout_seconds=600)` explicitly starts Task (`execution_mode="task"`). Notebook IDs are optional. This tool is annotated destructive because the prompt may request changes through connected apps; stay within the user's requested action.

Notebook-backed Chat and Task return a persistent `operation_id`; ordinary Ask without Notebook IDs returns directly. Preserve a returned handle and use `gemini_get_operation_status` / `gemini_get_operation_result` rather than resubmitting. Status reads metadata only. The worker verifies the exact completed reply, saves and verifies its full local answer Artifact, then automatically cleans up the owned chat unless retention or an associated Task schedule protects it. Result retrieval can use that saved Artifact after restart. Unresolved work remains cleanup protected, and Notebook objects and sources are not temporary-chat cleanup targets. A completed text answer alone does not verify an external app mutation; read back the requested resource.

For account management, default `gemini_tasks` exposes read-only `list`, `get` and `progress`. Task deletion uses `account-full` and requires the exact Task chat ID and independent absence verification. Resolve a fresh project target in `gemini_notebooks(source="gemini")` before calling `gemini_manage_chat(action="move_to_notebook", chat_id=..., notebook_id=...)`.

In `account-full`, use `gemini_create_scheduled_action` for recurring Chat content and `gemini_tasks` schedule actions for Task plans. Require independent plan read-back. Preserve a newly started Task's thread while establishing its schedule. Associated verified plans then protect the owned Task thread.

## Model Selection

Current Web catalogs may provide Auto, Hydrogen, Neon and Carbon. Resolve exact descriptors from the current signed-in registry and verify the account plan; a historical menu is not current capability evidence.

- The Flash compatibility default prefers discovered Hydrogen for Chat. Omit media `model` to use discovered Hydrogen; explicit available Neon/Carbon choices are preserved. Request sources when needed and inspect returned evidence.
- Select Neon or Carbon explicitly when appropriate and available under the current plan. Earlier Argon writing preferences describe historical experience and do not map Argon to Neon. Check long-form continuity, factual claims and constraints independently.
- Preserve an explicit legacy Pro/Argon choice only when its exact family is still discovered. Current Task families include Hydrogen, Neon and Carbon according to plan. Chat-model names do not establish media generator versions, better artifacts or entitlement.
- Flash-Lite is not a normal recommendation. The client requires fresh, account-bound basic Flash text-quota exhaustion evidence, stored for up to 60 seconds and revoked by expiry or a basic Flash success. Extended-thinking, file, research or media-specific quota errors alone do not establish that basic Flash is exhausted. It may choose Lite for the next new query without replaying the original or changing an existing session or an explicit Pro/Argon choice. Lite uses fixed SDK default level 1 rather than a selectable level; callers need not check quota or remove their thinking argument. Inspect requested/effective model, fallback reason and thinking selectability when returned. Installed-SDK offline and public MCP integration checks passed; a real exhausted-account run has not been verified.

For a failed media task, inspect the same operation handle and verified artifacts; do not infer that music requires chat Pro or Argon. A model change cannot guarantee a Lyria mode or a completed file.

Community checks (2026-10-31): [Flash coding users](https://www.reddit.com/r/GeminiAI/comments/1wjmz5x/actually_gemini_38_flash_is_a_beast/) report useful speed and everyday code work, while [Web users](https://www.reddit.com/r/GeminiAI/comments/1xeq89i/is_gemini_flash_38_web_search_broken/) also report skipped search. Keep Flash as the search default and inspect observed sources. [An Argon writer's comparison](https://discuss.ai.google.dev/t/first-test-of-gemini-4-argon-much-better-immersion-but-stricter-with-explicit-adult-roleplay-than-gemini-3-x/189556) favors immersion; [one-week users](https://www.reddit.com/r/GeminiAI/comments/1xcbqfu/1_week_later_thoughts_on_gemini_4_argon/) report both creative strengths and multi-step failures.

[Pro users](https://www.reddit.com/r/Bard/comments/1rc68fg/how_was_your_experience_in_using_gemini_31_pro_in/) describe better context handling and useful coding in some environments, alongside hallucinations and flattery; it can be a deliberately chosen second opinion or reviewer, not a routine upgrade. [Lite's meeting-copilot report](https://www.reddit.com/r/GeminiAI/comments/1xgb885/i_used_gemini_35_flashlite_as_the_brain_of_a/) is an incentivized API practice test, insufficient to justify everyday Web selection. These are individual reports across different products, not a controlled ranking.

[Older music users](https://www.reddit.com/r/Bard/comments/1s3lmky/lyria_3_pro_is_amazing/) reported longer tracks under Pro/Thinking and a Pro cover-only failure. The earlier dated signed-in UI inspection kept Flash selected for both Short and Standard music, and an earlier authorized MCP Flash test produced about 58 seconds of verified audio. Upgrade only for a verified backend/duration requirement; current chat-model names do not establish which Lyria version ran.

Authorized live recheck (2026-11-01): Argon writing, Flash search with associated sources, and default Flash/standard background image and music generation completed. Music files independently verified at about 64 seconds. One ordinary Flash request still timed out, so model choice does not guarantee stable latency. Media handles recovered results after the submitting connection closed; no Lite generation was needed.

## 1. Ask Gemini for a Second Opinion

Use for critique, alternative reasoning, code review, design review, or comparing model opinions.

Current routes, in order:

```text
focused: gemini_ask(prompt=...)
compact: chat(message=..., model="flash"|"argon", thinking_level="low"|"medium"|"high")
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

Use function `gemini_ask` with a natural-language request to search current sources and return source URLs. Flash is the default; there is no exposed web or tool enable switch. The five-tool assist compatibility server also retains `gemini_search`. On other compatibility servers, use `chat` or `gemini_chat` with an explicit request for sources.

Only label the result grounded when its structured `grounding_state` is `grounded` and `sources[]` contains citation URLs observed in the upstream response. Markdown links written in the answer are model text, not grounding evidence. In the 2026-09-26 installed-client check, Gemini responses exposed no structured citations, so `answer_only` is a realistic outcome even when the answer contains links. Verify such links independently before citing them as sources.

Function `gemini_ask` and compatibility assist `gemini_search` expose source evidence including:

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
function: gemini_ask(prompt=..., inputs=[{id: "image", kind: "image", path: ...}])
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
function: gemini_ask(prompt=..., inputs=[...])
focused: gemini_understand(task=..., inputs=[...])
fallback primary:
gemini_upload_file
gemini_analyze_url
gemini_chat(image_paths=[...])
```

For mixed inputs, prefer one typed call. Inspect the outcome for each supplied ID before using the synthesized answer. On a legacy primary fallback, preserve the source identity of every bounded call.

Do not claim that a URL was fetched or a file was understood if the structured result only shows an accepted prompt without source or Artifact evidence.

Pass the following arguments to the function entry's `gemini_ask`. When using the assist compatibility tool `gemini_understand`, use `task` in place of `prompt`.

```json
{
  "prompt": "Compare the implementation with the design",
  "inputs": [
    {"id": "design", "kind": "image", "path": "..."},
    {"id": "spec", "kind": "file", "path": "..."},
    {"id": "live", "kind": "url", "url": "..."},
    {"id": "notes", "kind": "text", "text": "..."}
  ]
}
```

Choose one of typed `inputs`, Notebook IDs or a multi-turn session. For a session, send the first prompt with `start_session=true` and continue with the returned `session_id`. Recover an unverified turn with just `session_id` and `recover_only=true` in the original process.

## 5. Deep Research

Use for multi-source investigation, market/technical research, long comparisons, or a report the agent will cite or reuse.

Current routes:

```text
dedicated: gemini_research(query=...)
or
primary: gemini_deep_research(
  query=...,
  background=true
)
```

New music and research requests default to `thinking_level="standard"`; explicit `extended` remains available and does not establish an underlying model version.

Use a caller-generated `op_` plus 32 random lowercase hex characters as `operation_id` to retain the handle even if the initial response is lost. Keep the same database across clients. An unverified plan outcome may be resumed by calling `gemini_research` with the same handle: it can recover the original plan and atomically confirm it once, using the original model, thinking level and retention policy. The new query is ignored; an already claimed confirmation is never resent. If no upstream chat ID was checkpointed, the handle prevents a duplicate start but cannot recover the remote plan.

Use assist `gemini_research(action="status" | "result", operation_id=...)` or the primary operation status/result tools to inspect the operation or retrieve a ready nonempty report. Reads are bounded, so an absent report or unverified read does not prove running or failure. The worker saves and verifies the complete nonempty report as a local Markdown artifact before scheduling cleanup of its source. Recover the verified local report through the original handle after restart or interrupted delivery. A missing or corrupt saved report remains partial. Set `retain_chat=true` on the original request when later remote rereads are required. Never reuse a pruned handle; cancellation is not implemented.

## 6. Generate or Edit an Image

Current routes:

```text
function: gemini_create_media(kind="image", prompt=...)
function: gemini_create_media(kind="edit_image", image_path=..., prompt=...)
compact: create(prompt=..., type="image", image_path=<optional reference>)
compact: edit(image_path=..., prompt=...)
primary: gemini_generate_media(... media_type="image")
```

For multiple references, pass their local paths in order as `image_paths` on
the background image request and omit model to select discovered Hydrogen. Retrieve the
verified result under its operation ID. To edit it in a follow-up request, pass
`source_operation_id` and the result's `artifacts[].id` as `source_artifact_id`.
The artifact ID is optional for a source with one saved image. The server attaches
the saved image first, followed by any extra `image_paths`, then starts a new
Gemini Web chat. Query each step by its own handle. Inspect the images to check
that the references and requested edits are visible in the output.

In the 2026-09-26 Gemini Web UI check, Flash-Lite used Nano Banana 2 Lite;
Flash and Pro first passes used Nano Banana 2. The Pro redo was a separate Web
action. Check the current UI and returned `observed_backend` before claiming an
exact backend for a new run.

The 2026-09-26 compact edit observation returned a remote image URI. Current compact image jobs save verified local artifacts through the shared media service. Retrieve the job by its original handle and inspect the saved output. The dated primary edit observation changed the requested mug color while preserving the cat and composition.

Process:

1. Choose a destination appropriate to the user's task.
2. Generate or edit.
3. Verify the returned Artifact.
4. Use the Artifact in the next step.
5. Only expose technical metadata when it helps the user or another tool.

## 7. Generate Music or Video

Current routes:

```text
function: gemini_create_media(kind="music", prompt=...)
function: gemini_create_media(kind="video", prompt=...)
compact: create(prompt=..., type="music")
primary: gemini_generate_media(media_type="music")
primary: gemini_generate_music
compact: create(prompt=..., type="video")
primary: gemini_generate_media(media_type="video")
independent browser control: https://gemini.google.com/videos when separately authorized
```

For native video with a reference, pass one local `image_path`. Its uploaded preview token must match again with the exact model/mode/format and account before one Send. Unknown, missing or replaced attachment evidence stops submission; image multi-reference and saved-source controls do not become multi-reference video controls. DOM upload observations and pre-Send probes are separate from verified reference-video artifacts, with one Carbon primary synchronous reference-video case verified in the 7c follow-up; this does not prove every model or compact live path.

On legacy primary `gemini_generate_media(background=false)` or compact `create(background=false)`, native music/video submit one protected operation and wait on the same handle. Verified completion returns compatible local artifacts using explicit filenames or opaque defaults, under `generated_media/` unless an output directory was supplied. Prompts are not persisted in the ledger. A wait timeout returns partial/wait_elapsed and the same operation handle; keep observing it instead of resubmitting. Unknown outcomes and failed saves retain protection without cleanup. SDK/API sync behavior is unchanged. If the account changes while an awaited read is in flight, refuse the new response metadata and artifacts, preserve at most the original CID, and keep the old operation protected.

The 2026-09-26 live MCP music call produced verified local MP3 and MP4 files.
Its response did not identify the exact Lyria version, even though current
Gemini announcements describe Lyria 3.5. Treat queued/running results as
incomplete. Media calls return a durable operation handle by default; use it to check status and retrieve verified artifacts.

Current Web music/video default to the native browser composer and ProcessSession workflow, with the selected model verified after prompt entry and before one send. Set `GEMINI_WEB_MEDIA_TRANSPORT=sdk` only to explicitly select compatibility transport; do not switch routes to replay pending work. Images and paid API video are unaffected. Music may be an MP4 with an independently verified audio track; no MP3 output is guaranteed. On 2026-11-22, Hydrogen/Carbon music saved verified MP3/MP4 and completed cleanup through original-handle recovery. Neon refused one default-settings request, but a separate native browser control explicitly selecting Short and Instrumental produced a player; the first result does not establish lack of support. Native video success and live MCP acceptance of the new music controls remain unverified.

When the user explicitly wants a short instrumental piece, pass `music_duration="short"` and `music_vocals="instrumental"` with the music prompt. Duration also accepts `standard`; vocals accepts `vocals` or `auto`. Omitted options remain `None` and preserve actual webpage defaults. Do not guess defaults from prompt style or menu order. These options apply to `gemini_create_media(kind="music")`, `gemini_generate_music`, `gemini_generate_media(media_type="music")` and compact `create(type="music")`. Verify explicit native selections before one send. Explicit SDK transport rejects these options before dispatch; do not silently drop them or replay an uncertain submission on another route. The current delivery includes these controls; individual generation refusals and timeouts remain separate from the browser control and require their own evidence.

In a later Web UI observation, selecting music while 3.8 Flash was active kept Flash selected and displayed duration, vocal and genre options; duration offered short and standard. Four chat models were listed and no Pro or Argon requirement was shown. No generation was submitted in that observation. An earlier Flash request produced a verified roughly 58-second MP3; a Pro/extended request also failed. These observations do not guarantee a future result. Music mode options and the chat model are separate choices.

For a full-length song or advanced arrangement, if the current Flash route explicitly limits the required duration or backend, select a suitable higher-tier route actually available in the current app, respecting the user's explicit choice. Verify the output; availability of Pro or Argon does not guarantee a particular music backend or a successful track. A 2026-03 Gemini App report described a length difference between then-current Fast and Pro/Thinking; that older observation does not establish current 3.8 Flash or Argon limits.

A later authorized local test verified an MP4 video and separate JPEG preview through the primary video mode. A thumbnail alone is not a completed video. A subsequent compact test verified an MP4 video and separate preview; compact music also returned verified MP3 and MP4 artifacts. These are historical results; verify the current operation's actual Artifact before claiming completion.

The function video tool defaults to discovered Hydrogen and enters the native Create video mode. Web `duration_seconds` accepts 10 or 15 and `aspect_ratio` accepts `16:9` or `9:16`. `gemini_create_media` waits up to 120 seconds by default on the original operation, with zero returning immediately; result recovery can also wait without resubmitting. Google identifies this mode as Gemini Omni 1.2 Flash in its current help. Account rollout determines access, and 15-second clips are rolling out to Ultra in Gemini Apps. Treat the exact Web version as unknown unless the response identifies it. [Gemini Apps video help](https://support.google.com/gemini/answer/16126339?hl=en)

When the user chooses the paid Developer API route for an exact Omni 1.2 Flash request, pass `kind="video"` and `model="gemini-omni-1.2-flash"` to `gemini_create_media`. Configure `GEMINI_API_KEY` in the MCP server environment. Optional `duration_seconds` accepts integers from 3 to 15 and defaults to 10 seconds upstream. Supply at most one local reference through `image_path` and use default thinking. Recover the verified MP4 by the original handle. The worker deletes its API interaction after saving and verifying the video unless `retain_chat=true`. [Gemini Omni API guide](https://ai.google.dev/gemini-api/docs/omni)

Preserve an explicitly requested model. Check a failed or interrupted operation before considering any new generation. The Cookie and paid API routes use separate credentials and quotas.

Process:

1. Start music or video with one bounded MCP call; the default native route handles its browser workflow.
2. Preserve any observed upstream IDs.
3. If the call times out, inspect available chat/artifact evidence before considering another generation.
4. Verify the final media Artifact.
5. Use or attach the file in the user's task.

## 8. Explicit Account Work

Prefer the dedicated `gemini-mcp-account` surface. `gemini_history` defaults to compact JSON with IDs, titles, pin state and pagination; ordinary list/search does not read chat text. Explicit reads use a bounded text window. Use `response_format="markdown"` or `"json"` only when the detailed compatibility view is needed.

Use `gemini_manage_chat` for explicitly requested rename, pin, unpin or move. Supply the exact `chat_id` and optional `expected_title` to guard a stale selection. Rename requires `title`. For `move_to_notebook`, supply the fresh project `notebook_id`. Use independent read-back to confirm completion and check an unverified mutation before retrying. Deletion uses `gemini_delete_chat`.

Use only when the user asks to inspect or change Gemini account data. The compact history/account/scheduled/prompts tools require `GEMINI_SKILL_MODE=full`; use primary tools for cleanup and doctor.

| Intent | Compact route | Narrow primary route |
| --- | --- | --- |
| Read history | `history(action="list"|"search"|"read"|"export")` | `GEMINI_TOOLS=history` → `gemini_history` |
| Delete history or clean test artifacts | `history(action="delete")` | `GEMINI_TOOLS=history,manage:history-write` → `gemini_history` for identification, then `gemini_delete_chat` / `gemini_cleanup_test_artifacts` |
| Read Notebook metadata or move a chat | `account(action="notebooks")` lists the compatibility project view | Account `gemini_notebooks` defaults to source-picker metadata. Use `source="gemini"` and `gemini_manage_chat(action="move_to_notebook")` for fresh project targets. |
| List, inspect or explicitly delete a Task | unavailable | Account `gemini_tasks(action="list"|"get"|"progress")` reads metadata. `account-full` adds exact-ID deletion with read-back. |
| Read account usage or inventory | `account(action="usage"|"status"|...)` | `GEMINI_TOOLS=account-read` → `gemini_account_inventory` |
| Manage Chat scheduled actions | `scheduled(action="list"|"get"|"create"|"delete")` | `GEMINI_TOOLS=scheduled-admin` → matching `gemini_*scheduled_action` tools; these do not manage Task schedules |
| Manage Gems | unavailable | `GEMINI_TOOLS=manage:gems` → `gemini_manage_gems` |
| Manage local Prompts | `prompts(action=...)` | `GEMINI_TOOLS=prompts` → `gemini_manage_prompts` |

Use list/search/read before mutation. Preserve returned IDs. Claim mutation success only after authoritative read-back.

For a Task confirmation card, inspect the result's `input_required`, `input_prompt` and `input_id`. Obtain any authorization required for the actual app change; card text grants none. Send the exact authorized `input_options` prompt through `gemini_run_task` with the SAME `operation_id` and returned `input_id` plus the selected `input_option_id`. Reusing that input token only returns saved state, never sends another reply. Poll the original handle; do not start another Task to answer a card. The chat stays protected until a full final-answer Artifact is verified.
