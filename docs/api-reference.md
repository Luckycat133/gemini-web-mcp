# API Reference

This reference documents the function/account MCP surfaces and explicitly selected
compatibility profiles. It reflects the current checked-in `list_tools()` contract;
dated Gemini Web observations are documented separately in the
[feature coverage matrix](web-feature-coverage.md).

---

## Business Surfaces

`gemini-mcp-function` and `gemini-mcp-account` each expose six tools on independent MCP connections. The main server without `GEMINI_TOOLS` uses the function surface. Use `gemini-mcp-server` with `GEMINI_TOOLS=function-full` or `GEMINI_TOOLS=account-full` for the respective prior 17-tool catalogs.

| Tool | Current business contract |
| --- | --- |
| Function `gemini_ask` | Ordinary/current-web questions, mixed inputs, session turns or Notebook Chat with `execution_mode="chat"` |
| Function `gemini_run_task` | Explicit background Task with `execution_mode="task"`, default Flash compatibility alias (preferring discovered Hydrogen) and optional Notebook sources |
| Function `gemini_research` | Deep Research submission and persistent handle for the report |
| Function `gemini_create_media` | Background image generation, image edit, video or music via `kind` |
| Function `gemini_get_operation_status` | Read-only status of the original persistent operation |
| Function `gemini_get_operation_result` | Recover the result or verified artifacts. May trigger default cleanup of the operation's owned conversation |
| Account `gemini_history` | Bounded metadata and explicitly requested chat text |
| Account `gemini_manage_chat` | Rename, pin, unpin or move an exact conversation into a Notebook with fresh read-back |
| Account `gemini_delete_chat` | Delete one exact conversation and independently verify absence |
| Account `gemini_notebooks` | Default `source="notebooklm"` lists compact source metadata; `source="gemini"` retains the same product's project view and project-chat listing |
| Account `gemini_manage_notebook` | Native Notebook and text-source create/read/delete with independent metadata verification; Notebook deletion includes its conversations |
| Account `gemini_tasks` | Read-only Task list, get and bounded progress |

`gemini_ask` keeps the `flash` compatibility default and standard thinking for new requests, preferring discovered Hydrogen. Explicit `neon`, `hydrogen` and `carbon` select exact current-account models; unavailable legacy Argon/Pro selections are refused rather than replaced. Its attachment, Notebook and session paths are mutually exclusive. For attachments, pass up to 16 typed `inputs` with caller-owned `id` values. For background Notebook Chat, pass `notebook_ids` and optionally `timeout_seconds` or `operation_id`. For a local multi-turn session, send a first prompt with `start_session=true`, then pass its `session_id` to inherit the model and thinking level. Read back an unverified turn with just `session_id` and `recover_only=true`. The ordinary path returns inline.

`gemini_create_media` starts a background job and, by default, waits up to 120 seconds on that same operation. Set `wait_seconds=0` to return its handle immediately. `gemini_get_operation_result` accepts `wait_seconds=0..120`; waiting never resubmits generation. Omitted media `model` selects discovered Hydrogen. Explicit available chat-model choices are preserved. Image requests accept these reference controls.

| Argument | Contract |
| --- | --- |
| `image_paths` | Nonempty ordered local references for image generation or editing. Mutually exclusive with `image_path` |
| `image_path` | One local reference for `kind="edit_image"` or `kind="video"` |
| `source_operation_id` | Earlier completed same-account image operation whose saved artifact is verified again before upload |
| `source_artifact_id` | Selects one image from that operation. Optional when it has exactly one usable image |

For native Web video, `image_path` attaches one validated local image in the composer before Send. The uploaded preview token must remain unchanged through the final account/model/mode/format checks. An unsupported, missing or changed attachment blocks submission; the reference is never silently discarded. `image_paths` and saved-image lineage controls remain for image generation/editing. A [7c Carbon follow-up](live-model-followup-2026-11-22.md) verified one reference video through primary `background=false`, including local artifacts and cleanup. That does not establish all models, durations or compact live paths; earlier DOM-only probes remain pre-Send evidence.

Legacy primary `gemini_generate_media(background=false)` and compact `create(background=false)` use one protected native music/video operation and wait on its original handle within the caller's wait budget. Verified completion preserves the compatible `state=local` artifact response, explicit `filename` and the `generated_media/` default directory. An omitted filename is opaque rather than prompt-derived, and the prompt is not persisted in the operation ledger. A wait timeout returns the same handle with `state=partial` and `verification_status=wait_elapsed`; unknown results and failed saves retain protection without cleanup or resubmission. SDK/API synchronous behavior is unchanged. `gemini_create_media` keeps its background-operation contract and `wait_seconds` controls.

Native read-back rechecks the captured account and credentials after each awaited read before observing or checkpointing response metadata. Account drift refuses the new RID/RCID and artifacts; at most the previously observed CID stays bound to its original owner, protected from cleanup. Reading a response after a switch cannot authorize writing it into the old operation.

Native Web music adds optional `music_duration="short"|"standard"` and `music_vocals="instrumental"|"vocals"|"auto"`, both defaulting to `None`. Set only options the user explicitly requests; `None` preserves observed webpage defaults rather than selecting the first item or guessing from the prompt. These controls are music-only and are rejected before dispatch with explicit SDK transport. The current delivery includes these contracts; per-request refusal and timeout results remain distinct from the successful Neon Short/Instrumental browser control.

The same controls are accepted by `gemini_create_media(kind="music")`, `gemini_generate_music`, legacy `gemini_generate_media(media_type="music")` and compact `create(type="music")`.

The saved source is uploaded first, followed by additional `image_paths`, into a new chat with a new operation handle. A missing, changed or unverified source stops the new dispatch. Use the result artifact ID, keep each step's handle, and inspect the generated image to verify the requested composition. Offline checks cover multi-reference and follow-up lineage. One authorized 2026-11-21 two-reference composition returned a verified 2048×2048 JPEG matching both synthetic references; follow-up edit fidelity remains a separate live check. [Image examples](tools.md#gemini_create_media)

`gemini_run_task` accepts model, thinking, Notebook, timeout, retention and operation controls. To answer a current confirmation card, keep the same `operation_id` and provide `input_id`, `input_option_id` and the exact prompt from `input_options`. The token dispatches once and preserves the original model and retention. The tool carries a destructive annotation because Task can change connected app data.

In `account-full`, `gemini_tasks` adds Task mutations and schedule management, with a destructive annotation. Schedule listing uses local offset/limit pagination. This profile also exposes `gemini_skills`, `gemini_preferences` and `gemini_task_browser` for native Skill management, saved settings and explicit browser actions.

Task schedules are separate from Chat scheduled actions. Task browser observation and explicit input do not provide exclusive pause, login or Task stop/resume. Verify website effects independently. When `GEMINI_TASK_AUTO_APPROVE=true` is set in the startup environment, the worker can submit one single-use remote-browser grant per matching card on an MCP-owned Task. The default is off. `GEMINI_TASK_WEB_APPROVALS` separately enables discovery of website-created Tasks. Exact Google Tasks deletion rules require both switches and an explicit Task/title rule. Progress reports `browser_approval_configured` for configured deletion cards and `native_review_required` when native handling is needed. [Approval settings](task-preferences.md) · [Notebook, Chat and Task workflows](notebook-task-workflows.md)

An App connection card returns `needs_attention`, `setup_required` and `setup_kind`, rather than a fabricated permission choice or completed answer. Connection setup must be resolved in the original Gemini Task; a text reply cannot grant OAuth. A metadata-only status read can report a previously observed setup state without revalidating the card.

Task result artifacts include the full answer and supported structured file cards. The complete native file inventory and every saved file must verify before default cleanup. Failed downloads or changed shapes preserve protection and return a partial result; a legacy saved answer without the new inventory remains available as partial, without claiming attachment coverage. Restarted result reads verify cached files and do not regenerate. Native export menus and every possible attachment type are not covered. Native account examples and pagination are documented in [Skills and Notebook management](native-account-resources.md).

Research workers save and verify the full ready nonempty report as a Markdown Artifact before default cleanup. Result reads return a preview of at most 4,000 characters and the full local Artifact. Cached reads work after restart and recheck account, source identity, size and SHA-256; missing or corrupt files remain partial. Status uses progress metadata and never downloads or cleans a report.

In `function-full`, `gemini_list_research_report_actions` and `gemini_create_from_research_report` accept one `chat_id` or `operation_id`. The operation path reads its verified local report. Recover an unavailable local report with the original result handle before requesting derivatives. The derivatives remain available after cleanup of the source conversation.

## Explicit Compatibility Profiles

### Tool Groups

| Group | Tools | Intended use |
|-------|-------|--------------|
| `core` | Chat, media, file/URL, Deep Research | Broad legacy content profile |
| `manage` | Chat history, model listing, Gems | Account content management |
| `prompts` | Local prompt library | Optional local helper |
| `all` | `core` + `manage` | Complete Gemini workflow without local prompt library |

The explicitly selected primary compatibility profiles expose authentication helpers and `gemini_reset`.

---

### Tool Profiles

Use narrow `GEMINI_TOOLS` profiles for agent-facing deployments:

| Profile | Use case |
|---------|----------|
| `function-full` | Prior 17-tool function catalog with separate media, understanding and session tools and Research derivatives |
| `account-full` | Prior 17-tool account catalog with Task mutations, schedules, native Skills, Gems, preferences, browser actions and cleanup |
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
| `model` | `str` | `flash` (default, preferring discovered Hydrogen), `hydrogen`, `neon`, `carbon`, or exact discovered names. Legacy `argon`/`pro` require their own family to remain available; they are never mapped to Neon. `flash-lite` requires fresh same-account basic Flash quota exhaustion evidence. |
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
| `gemini_generate_video` | Background video generation on the function surface |
| `gemini_generate_media` | Generate image, video, or music on the legacy surface |
| `gemini_generate_music` | Convenience wrapper for music generation |

Current Gemini Web media routing:

| Request | Route and evidence |
|---------|-------------------|
| `media_type=image` | Web image route; omitted model prefers Hydrogen and explicit available selectors are preserved. A static label cannot identify Nano Banana version |
| `media_type=music` | Web music route; exact generator version is unverified unless upstream identifies it |
| `media_type=video`, omitted model | Gemini Apps Create video mode with discovered Hydrogen; `duration_seconds=10|15`, `aspect_ratio="16:9"|"9:16"` |
| Video with `model=gemini-omni-1.2-flash` | Exact Gemini API model via `GEMINI_API_KEY`, with optional `duration_seconds` (integer 3–15, provider default 10) |

The chat selector does not identify the image generator. An explicit available
selector is preserved. The Pro image redo control is a separate post-generation
Gemini Web UI action. The API route is used only for the explicit API model ID.
The signed-in 2026-09-26 live check produced images and music through MCP, while
generic MCP video returned `ARTIFACT_NOT_RETURNED`. The current Web route uses
the dedicated Create video transport. The exact API model requires a billed API
project and runs in the background. It stores the original interaction ID for
recovery and independently verifies deletion after saving the MP4, unless
`retain_chat=true`. A preview or response text cannot prove a video exists.

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
