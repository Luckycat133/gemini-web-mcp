# API Reference

This reference documents the current MCP tool surface exposed by
`src.server`. It reflects the current checked-in `list_tools()` contract;
dated Gemini Web observations are documented separately in
[`live-ui-coverage.md`](./live-ui-coverage.md).

---

## Tool Groups

| Group | Tools | Intended use |
|-------|-------|--------------|
| `core` | Chat, media, file/URL, Deep Research | Recommended default |
| `manage` | History, notebooks, schedules, Skills, sharing, Library, Canvas, Gems | Account content management |
| `prompts` | Local prompt library | Optional local helper |
| `all` | `core` + `manage` | Complete Gemini workflow without local prompt library |

The server always exposes authentication helpers and `gemini_reset`.

---

## Tool Profiles

Use narrow `GEMINI_TOOLS` profiles for agent-facing deployments:

| Profile | Use case |
|---------|----------|
| `model` / `chat` | Call Gemini models only; no history, media, file, research, or account tools |
| `history` | One read-only `gemini_history` facade for list/scan/search/read/export/task_status, without delete, scheduled actions, or Gems |
| `history-organize` | `gemini_history`, `gemini_update_chat` for rename/pin/unpin, typed `gemini_notebooks`, and the explicit Notebook move operation |
| `account-read` | One read-only `gemini_account_inventory` facade for links, usage, library, notebooks, scheduled entries, modes, and models |
| `scheduled-read` | Scheduled-action list/get only |
| `scheduled-admin` | Typed native Remy create/update/pause/resume/delete and legacy scheduled tools after authorization |
| `core` | Broad content work: chat, media, file/URL analysis, and Deep Research |
| `all` | Full maintenance/verification surface; avoid as a default for general agents |

`gemini_get_tool_manifest` reports these profiles in JSON and Markdown output,
with `availability` and `current_enabled` per tool.

For focused agent deployments, `gemini-mcp-assist` exposes five assistance tools,
`gemini-mcp-create` exposes seven creation and operation tools, and
`gemini-mcp-account` exposes seven account facades. Their catalogs and task-first
Skills are documented in [tools](tools.md) and [client examples](client-examples.md).
The tables below document the primary surface.

---

## Typed account workflows

The focused account server and primary `all`/`manage` surfaces accept the same
`request` objects. Compact compatibility uses `account(request=...)` and
`scheduled(request=...)`. Action-specific schemas reject unrelated arguments.

| Tool / resource | Actions |
| --- | --- |
| `gemini_notebooks` | `list`, `chats`, `move`, `get`, `create`, `update`, `delete`, `sources`, `add_source`, `remove_source` |
| `gemini_scheduled` | `list`, `get`, `create`, `update`, `pause`, `resume`, `delete` with `backend="remy"`; legacy actions remain available |
| `gemini_account`, `action="skills"` | Nested `list`, `get`, `create`, `update`, `upload` ZIP, `enable`, `disable`, `delete` |
| `gemini_account`, `action="sharing"` | Nested `list`, `create` from an exact chat, `delete` exact token |
| `gemini_account`, `action="library_assets"` | Nested paginated `list`, exact `get`, guarded media `delete` |
| `gemini_account`, `action="canvas"` | Nested `chat_artifacts`, `get`, `versions`, `create`, `edit`, `export` |

For example, `gemini_account` accepts:

```json
{"request":{"action":"canvas","request":{"action":"edit","artifact_id":"<observed ID>","text":"Replacement document text\n"}}}
```

Canvas edit replaces the whole plain document, including formatting; it
preserves whitespace and does not create a version. Unsupported attachment,
citation and non-document shapes are rejected. Export requires a new absolute
local path. Native Skills differ from Gems and the local Prompt library. Library deletion
supports the current media kinds; Canvas/document single-asset deletion is
unavailable, matching the current Web caller.
Notebook creation/update supports title and instructions; description writes
are unavailable. Source indexing can remain `accepted` until independently
observed ready with matching content. Notebook deletion keeps chats unless
`delete_chats=true` is explicitly supplied.

Drive, Photos and **NotebookLM** references in focused `gemini_understand`
use `kind`, a real `resource_id`, `name`, and optional `mime_type`/`url`.
`kind="notebook"` identifies NotebookLM, not a Gemini Web Notebook. These
references require existing account access; the tool does not grant access or
operate the picker. At most ten native/local attachments can be combined.

---

## Default Tools (`GEMINI_TOOLS=core`)

For native Tasks, include a history profile (for example `GEMINI_TOOLS=core,history`)
to read `gemini_history(action="task_status", chat_id=...)` after reconnect.
Task status is read-only and does not resubmit execution. Unsupported cancel
semantics are not inferred from chat deletion.

### Chat

| Tool | Purpose |
|------|---------|
| `gemini_chat` | One-shot Gemini conversation |
| `gemini_chat_stream` | Collect and normalize a Gemini upstream stream into one MCP result |
| `gemini_start_chat` | Create a multi-turn session |
| `gemini_send_message` | Send a message to an existing session |
| `gemini_send_message_stream` | Collect and normalize an existing session's upstream stream into one MCP result |
| `gemini_list_sessions` | List local active sessions |
| `gemini_reset_session` | Reset a local session and optionally delete the remote chat |

Common chat parameters:

| Parameter | Type | Notes |
|-----------|------|-------|
| `model` | `str` | Stable aliases or a current runtime directory name, including `4 Argon` when available |
| `thinking_level` | `str` | `low`, `medium`, `high`; `standard=low`, `extended=high` remain compatible |
| `task_mode` | `bool` | Native Task execution; omitted session values inherit. Read status by exact chat ID; unfinished sources are retained |
| `learning_mode` | `str` | `guided_learning`/`guided` selects native Guided Learning; existing `interactive_quiz`/`quiz`, `flashcards`, `practice_test`, `study_guide`/`exam_prep` remain companion modes |
| `image_paths` | `list[str]` | Optional local image attachments |
| `gem_id` | `str` | Optional Gem ID for one-shot or session creation |
| `temporary` | `bool` | Use Gemini Web temporary chat behavior |
| `retain_chat` | `bool` | Keep the remote Gemini chat instead of scheduling cleanup |
| `delete_after_seconds` | `int` | Override remote chat cleanup delay |

Native Guided Learning uses the observed 2026-10-18 Web selector. The older
companion modes retain their separate request fields. Both are available on
one-shot and session tools, including streaming variants.

The compatibility names ending in `_stream` describe the Gemini Web transport,
not MCP incremental delivery. These tools collect the upstream chunks, normalize
delta/cumulative/mixed text without duplication, and return the final text once.
`_meta.domain_result.data.stream.delivery` is `collected` and includes observed
chunk semantics and counts.

### Media

| Tool | Purpose |
|------|---------|
| `gemini_generate_media` | Native image, reference-image editing, video, and music creation with verified local artifacts |
| `gemini_generate_music` | Convenience wrapper for music generation |

Current Gemini Web media routing:

| Request | Effective backend |
|---------|-------------------|
| `media_type=image`, `model=flash-lite` | `Nano Banana 2 Lite` route; exact observed backend may be unavailable |
| `media_type=image`, `model=flash` / `pro` | First pass reports `Nano Banana 2`; Pro redo is separate |
| `media_type=music` | `Lyria` family; exact version is unverified unless upstream identifies it |
| `media_type=video` | Explicit native video feature mode 11; require a matching video Artifact, with backend version reported only when observed |

`model=pro` does not directly select a different first-pass image backend than
Flash. The Pro image redo control is a post-generation Gemini Web UI action.
Bounded signed-in focused MCP tests on 2026-10-03 produced independently decoded
image/edit JPEGs, music MP3 plus companion MP4, and a native 1280×720 video MP4.
Earlier generic-video and interrupted requests remain separate dated observations
in [live UI coverage](live-ui-coverage.md).

Primary and compact creation save and verify local output by default. Image/edit, video, and music select their native Web features explicitly. Finished verified outputs or definitive empty responses trigger bounded cleanup of the request's new source chat. Queued, unsaved and failed recovery results retain the source. Primary `retain_chat` / `delete_after_seconds` overrides the default; structured cleanup observations do not replace Artifact verification. Focused video/music return durable operation handles for polling and completed-file retrieval after restart. See [native media and cleanup](native-media-mode.md) and [operations](operations-and-cleanup.md).

### File And URL

| Tool | Purpose |
|------|---------|
| `gemini_upload_file` | Upload a local file and ask Gemini to analyze it |
| `gemini_analyze_url` | Ask Gemini to analyze a web or YouTube URL |

### Research

| Tool | Purpose |
|------|---------|
| `gemini_deep_research` | Start Deep Research and return explicit queued/running/completed/timed_out state plus continuation IDs |
| `gemini_list_research_report_actions` | List MCP-side create actions for a completed Deep Research immersive report |
| `gemini_create_from_research_report` | Create a local webpage, infographic, quiz, flashcards, audio overview script, or custom app spec from a completed report |

### Discovery

| Tool | Purpose |
|------|---------|
| `gemini_get_tool_manifest` | Return agent-facing tool safety, privacy, pagination, availability, and workflow metadata |

### Authentication And Client State

| Tool | Purpose |
|------|---------|
| `gemini_get_cookie_status` | Report current cookie/auth status |
| `gemini_doctor` | Run safe preflight over tool groups, cookie status, browser profile alignment, and media verification dependencies |
| `gemini_list_browser_cookie_profiles` | List local browser profile diagnostics without returning cookie values; macOS Keychain waits are bounded |
| `gemini_get_cookie_from_browser` | Load Gemini cookies from Chrome, Firefox, Edge, or a selected Chrome profile, with the same bounded macOS Keychain access |
| `gemini_reset` | Reset the Gemini client |

---

## Optional Tools

### `GEMINI_TOOLS=all`

Adds these management tools to `core`:

| Tool | Purpose |
|------|---------|
| `gemini_history` | Read-only facade for chat-history list/scan/search/read/export workflows |
| `gemini_account_inventory` | Read-only facade for account and Gemini Web surface inventory |
| `gemini_notebooks` | Read-only facade for native Gemini Web Notebook list/chats inventory |
| `gemini_inspect_account` | Inspect current account feature/RPC status without exposing raw RPC previews |
| `gemini_get_web_capabilities` | Return the observed Pro Web model/menu/settings capability manifest and MCP coverage map |
| `gemini_list_chats` | List Gemini Web chat history metadata with pagination |
| `gemini_scan_chat_history_sources` | Deep-scan Gemini Web chat-history metadata sources across observed RPC filters |
| `gemini_search_chats` | Search chat history metadata, optionally scanning turns when explicitly requested |
| `gemini_read_chat` | Read turns from a specific Gemini Web chat |
| `gemini_export_chat` | Export one Gemini Web chat as Markdown or JSON |
| `gemini_delete_chat` | Request deletion of a specific chat and report verified-absent, unverified, still-present, or read-back-error state |
| `gemini_cleanup_test_artifacts` | Dry-run or delete marked test chats and scheduled actions |
| `gemini_probe_web_features` | Probe observed read-only RPC reachability for newer Web UI surfaces |
| `gemini_list_public_links` | List public links returned by Gemini Web sharing surface |
| `gemini_get_usage_limits` | Read usage/quota structures from Gemini Web usage surface |
| `gemini_list_library_capabilities` | List localized Library capability/template entries |
| `gemini_list_notebooks` | List native Gemini Web Notebooks with ids and metadata |
| `gemini_list_notebook_chats` | List recent chats assigned to a native Gemini Web Notebook |
| `gemini_move_chat_to_notebook` | Move an existing Gemini Web chat into a native Gemini Web Notebook |
| `gemini_list_scheduled_actions` | Read legacy actions, or current native Task schedules with `backend=remy` |
| `gemini_get_scheduled_action` | Read one action by id from the explicitly selected schedule backend |
| `gemini_create_scheduled_action` | Create a daily Gemini Web scheduled action |
| `gemini_delete_scheduled_action` | Delete a Gemini Web scheduled action by id |
| `gemini_get_tool_mode_status` | Read Web-internal tool/mode status rows observed around Canvas and Guided Learning toggles |
| `gemini_list_models` | Show MCP aliases and the account runtime model registry |
| `gemini_manage_gems` | List, create, update, or delete Gems |

List-style tools in this group return pagination metadata in JSON mode
(`total_count`, `count`, `offset`, `limit`, `has_more`, and `next_offset`) when
the underlying Web surface can be represented as a bounded list.
Scheduled list/get defaults to `backend=legacy` for compatibility. Use
`backend=remy` for the current `/scheduled` page and preserve that backend with
the returned ID. Native schedule mutations are unsupported; the existing daily
create/delete tools operate on the legacy backend only. A legacy empty registry
does not establish that the current page has no tasks.

Primary server tools expose native MCP `ToolAnnotations` for read-only,
destructive, idempotent, and open-world hints. `gemini_get_tool_manifest`
provides a client-readable safety map for planning multi-step workflows, with
`availability` and `current_enabled` fields so agents can distinguish known
optional tools from tools enabled in the current `GEMINI_TOOLS` process.

`gemini_list_chats`, `gemini_search_chats`, `gemini_read_chat`,
`gemini_export_chat`, and `gemini_delete_chat` share the same typed history
service used by the compact server. Their first text block exposes
`_meta.domain_result`. Search pagination applies to the source page before
filtering; deletion is only verified when read-back observes absence.

### `GEMINI_TOOLS=prompts`

Adds the local prompt-library helper:

| Tool | Purpose |
|------|---------|
| `gemini_manage_prompts` | List, get, create, or delete local prompt snippets |

This tool is intentionally not part of `core` or `all`, because it is local
state management rather than a Gemini Web AI capability.

---

## Dedicated Assist Surface

The dedicated assistance entrypoint `gemini-mcp-assist` (server name
`gemini_assist_mcp`) exposes exactly five tools. It deliberately exposes no
history, Cookie, Scheduled, Gem, Prompt, manifest, or cleanup tools:

| Tool | Purpose |
|------|---------|
| `gemini_ask` | One focused question with optional context, for second opinions, critique, or code/design review |
| `gemini_search` | Grounded current-web search with observed `sources` and a truthful `grounding_state` (`grounded`, `answer_only`, `unavailable`, or `failed`) |
| `gemini_understand_image` | Understand one image from a local path or http(s) image URI |
| `gemini_understand` | Typed mixed-input understanding over text, image, file, and URL inputs (max 16), with per-input outcomes |
| `gemini_research` | Start one Deep Research run asynchronously and return a preserved operation handle with upstream IDs |

`gemini_search` and `gemini_understand` return information to the calling
agent, not Artifacts. A completed Deep Research report stays in the retained
Gemini chat and is recovered through the preserved upstream identifiers. Start
this server with the `gemini-mcp-assist` console entrypoint; the `gemini-assist`
Runtime Skill documents its trigger boundary.

---

## Removed Or Merged Tools

| Tool | Status |
|------|--------|
| `gemini_list_features` | Removed |
| Independent image tool aliases | Merged into `gemini_generate_media` |
| `gemini_health_check` | Not exposed by the current server |
