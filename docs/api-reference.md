# API Reference

This reference documents the function/account MCP surfaces and explicitly selected
compatibility profiles. It reflects the current checked-in `list_tools()` contract;
dated Gemini Web observations are documented separately in the
[feature coverage matrix](web-feature-coverage.md).

---

## Business Surfaces

`gemini-mcp-function` exposes 17 tools; `gemini-mcp-account` exposes 17 explicit account tools. The main server without `GEMINI_TOOLS` uses the function surface. Authentication helpers belong to explicit compatibility profiles, not these business catalogs.

| Tool | Current business contract |
| --- | --- |
| Function `gemini_ask` | Ordinary/current-web questions; optional `notebook_ids` starts a background ordinary chat with `execution_mode="chat"` |
| Function `gemini_run_task` | Explicit background Task with `execution_mode="task"`, default Flash and optional Notebook sources |
| Function `gemini_get_operation_status/result` | Read the original persistent operation; the worker verifies the full answer and recognized native Task file artifacts before default owned-thread cleanup |
| Account `gemini_notebooks` | Default `source="notebooklm"` lists compact source metadata; `source="gemini"` retains the same product's project view and project-chat listing |
| Account `gemini_manage_notebook` | Native Notebook and text-source create/read/delete with independent metadata verification; Notebook deletion includes its conversations |
| Account `gemini_skills` | Native Skills list/get/create/update/enable/delete; instruction reads require explicit opt-in |
| Account `gemini_preferences` | Saved browser/action preferences with read-back; runtime effects remain unverified unless separately observed |
| Account `gemini_tasks` | Task metadata, bounded progress, explicit deletion and Task schedule management |
| Account `gemini_task_browser` | Verified Task browser PNG, or one explicit journaled input |

The function version of `gemini_ask` accepts `prompt`, optional `context`, `model="flash"`, `thinking_level="standard"`, optional `notebook_ids`, `timeout_seconds=600`, `retain_chat=false`, optional `delete_after_seconds` and `operation_id`. A caller-supplied operation ID requires Notebook context. Other ordinary questions return through the usual direct path. Assist and compact compatibility schemas remain separate.

`gemini_run_task` accepts the same model, thinking, Notebook, timeout, retention and operation controls, without `context`, plus optional `input_id` and `input_option_id` for a current confirmation card. Start returns an operation handle; to answer a card, keep that same `operation_id` and use the exact prompt returned in `input_options`. The token dispatches once and preserves original model/retention. `gemini_tasks` accepts list/get/delete/progress plus list_schedules/get_schedule/create_schedule/update_schedule/pause_schedule/resume_schedule/delete_schedule. Task list/get do not read chat text; progress reads bounded activity metadata. Schedule listing uses local offset/limit pagination. Both Task tools carry destructive annotations because they can perform explicitly requested app changes or delete a thread.

Task schedules are separate from Chat scheduled actions. Task browser observation and explicit input do not provide exclusive pause, automatic permission approval, login or Task stop/resume; website effects need independent verification. See [Notebook, Chat and Task workflows](notebook-task-workflows.md) for examples and evidence boundaries.

An App connection card returns `needs_attention`, `setup_required` and `setup_kind`, rather than a fabricated permission choice or completed answer. Connection setup must be resolved in the original Gemini Task; a text reply cannot grant OAuth. A metadata-only status read can report a previously observed setup state without revalidating the card.

Task result artifacts include the full answer and supported structured file cards. The complete native file inventory and every saved file must verify before default cleanup. Failed downloads or changed shapes preserve protection and return a partial result; a legacy saved answer without the new inventory remains available as partial, without claiming attachment coverage. Restarted result reads verify cached files and do not regenerate. Native export menus and every possible attachment type are not covered. Native account examples and pagination are documented in [Skills and Notebook management](native-account-resources.md).

Research workers save and verify the full ready nonempty report as a Markdown Artifact before default cleanup. Result reads return a preview of at most 4,000 characters and the full local Artifact. Cached reads work after restart and recheck account, source identity, size and SHA-256; missing or corrupt files remain partial. Status uses progress metadata and never downloads or cleans a report.

`gemini_list_research_report_actions` and `gemini_create_from_research_report` accept either `chat_id` or `operation_id`, never both. The operation path reads only its verified local report and does not authenticate remotely, generate or delete. An unavailable local report must first be recovered with the original result handle. This lets derivatives work after the source conversation was cleaned.

## Explicit Compatibility Profiles

## Tool Groups

| Group | Tools | Intended use |
|-------|-------|--------------|
| `core` | Chat, media, file/URL, Deep Research | Recommended default |
| `manage` | Chat history, model listing, Gems | Account content management |
| `prompts` | Local prompt library | Optional local helper |
| `all` | `core` + `manage` | Complete Gemini workflow without local prompt library |

The explicitly selected primary compatibility profiles expose authentication helpers and `gemini_reset`.

---

## Tool Profiles

Use narrow `GEMINI_TOOLS` profiles for agent-facing deployments:

| Profile | Use case |
|---------|----------|
| `model` / `chat` | Call Gemini models only; no history, media, file, research, or account tools |
| `history` | One read-only `gemini_history` facade for list/scan/search/read/export, without delete, scheduled actions, or Gems |
| `history-organize` | `gemini_history`, read-only `gemini_notebooks`, and the explicit Notebook move operation |
| `account-read` | One read-only `gemini_account_inventory` facade for links, usage, library, notebooks, scheduled entries, modes, and models |
| `scheduled-read` | Scheduled-action list/get only |
| `scheduled-admin` | Scheduled-action list/get/create/delete after explicit authorization |
| `core` | Broad content work: chat, media, file/URL analysis, and Deep Research |
| `all` | Full maintenance/verification surface; avoid as a default for general agents |

`gemini_get_tool_manifest` reports these profiles in JSON and Markdown output,
with `availability` and `current_enabled` per tool.

---

## Compatibility Tools (`GEMINI_TOOLS=core`)

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
| `model` | `str` | `flash` (default), `argon`, `pro`; `fast` and `thinking` remain compatibility aliases. `flash-lite` requires fresh same-account basic Flash quota exhaustion evidence. |
| `thinking_level` | `str` | `low`, `medium` or `high`; `standard`/`extended` remain low/high aliases. Lite ignores caller strength and reports `thinking_selectable=false` |
| `learning_mode` | `str` | Optional guided-learning companion: `interactive_quiz`/`quiz`, `flashcards`, `practice_test`, `study_guide`/`exam_prep` |
| `image_paths` | `list[str]` | Optional local image attachments |
| `gem_id` | `str` | Optional Gem ID for one-shot or session creation |
| `temporary` | `bool` | Use Gemini Web temporary chat behavior |
| `retain_chat` | `bool` | Keep the remote Gemini chat instead of scheduling cleanup |
| `delete_after_seconds` | `int` | Override remote chat cleanup delay |

`learning_mode` is implemented as a Web-compatible request injection for the
observed 2026-06-19 Guided Learning companion. It is available on one-shot and
session chat tools, including streaming variants.

`gemini_send_message(session_id=..., recover_only=true)` reads the previous unverified session turn without sending another prompt. Omit `message` and `image_paths`. An uncertain turn blocks new messages until the original exact reply can be verified; no latest-reply fallback or generation replay is used. Session recovery requires the original active server process and is distinct from durable background operation handles. Reset cannot claim deletion while a reply remains unverified. The compact session schema and streaming tools do not expose `recover_only`.

The compatibility names ending in `_stream` describe the Gemini Web transport,
not MCP incremental delivery. These tools collect the upstream chunks, normalize
delta/cumulative/mixed text without duplication, and return the final text once.
`_meta.domain_result.data.stream.delivery` is `collected` and includes observed
chunk semantics and counts.

### Media

| Tool | Purpose |
|------|---------|
| `gemini_generate_media` | Generate image, video, or music |
| `gemini_generate_music` | Convenience wrapper for music generation |

Current Gemini Web media routing:

| Request | Effective backend |
|---------|-------------------|
| `media_type=image`, `model=flash-lite` | `Nano Banana 2 Lite` route; exact observed backend may be unavailable |
| `media_type=image`, `model=flash` / `pro` | First pass reports `Nano Banana 2`; Pro redo is separate |
| `media_type=music` | `Lyria` family; exact version is unverified unless upstream identifies it |
| `media_type=video` | Generic chat route is not a verified Omni video mode; require a video Artifact |

`model=pro` does not directly select a different first-pass image backend than
Flash. The Pro image redo control is a post-generation Gemini Web UI action.
The signed-in 2026-09-26 live check produced images and music through MCP, while
generic MCP video returned `ARTIFACT_NOT_RETURNED`; Gemini Web's dedicated
Videos page produced a downloadable Omni video.

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
| `gemini_notebooks` | Read-only Notebook facade; compatibility profiles default to the Gemini project view, while the account surface defaults to the source picker |
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
| `gemini_move_chat_to_notebook` | Resolve a target in the fresh Gemini project view, move the exact chat, then independently verify membership |
| `gemini_list_scheduled_actions` | Read scheduled-action entries returned by Gemini Web scheduled actions surface |
| `gemini_get_scheduled_action` | Read one scheduled action by id using the observed Web GetTask RPC |
| `gemini_create_scheduled_action` | Create a daily Gemini Web scheduled action |
| `gemini_delete_scheduled_action` | Delete a Gemini Web scheduled action by id |
| `gemini_get_tool_mode_status` | Read Web-internal tool/mode status rows observed around Canvas and Guided Learning toggles |
| `gemini_list_models` | Show MCP aliases and the account runtime model registry |
| `gemini_manage_gems` | List, create, update, or delete Gems |

List-style tools in this group return pagination metadata in JSON mode
(`total_count`, `count`, `offset`, `limit`, `has_more`, and `next_offset`) when
the underlying Web surface can be represented as a bounded list.
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

`gemini_task_browser(chat_id, response_id=None, candidate_id=None, browser_input=None, action_id=None, expected_frame_sha256=None)` is a destructive union tool. A read can return a verified PNG artifact and native image block. Explicit `click`, `scroll`, `key` or `type` requires both exact reply IDs, its prior frame hash and a unique action ID of at least 20 characters; the Task must have fresh unchanged state 3 or 5. Duplicate IDs are consumed and never replayed. A frame does not prove a website action, exclusive pause or authorization. Install the `browser-control` extra. See [workflow details](notebook-task-workflows.md).

## Persistent Task preferences

`gemini_preferences` supports `get_browser`, `set_browser`, `list_permissions` and `set_permission`. Browser values are `automatic` / `remote_only`; permission values are `always_ask` / `always_allow`. Select app actions by returned opaque handles, and use `expected_revision` to reject stale snapshots. These settings affect future Tasks; once/thread confirmation cards remain separate. See [examples and read-back rules](task-preferences.md). Chat and Task thinking strength accepts `low` / `medium` / `high`, with legacy low/high aliases `standard` / `extended`.
