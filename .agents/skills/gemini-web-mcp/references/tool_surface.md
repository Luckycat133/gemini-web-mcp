# Gemini Web Tool Surface Reference

`gemini-mcp-function` exposes the 17-tool function catalog; `gemini-mcp-account` exposes the 17-tool explicit account catalog. Use `gemini_ask` for chat and current-web questions; `gemini_search` remains on the five-tool assist compatibility surface. Use the connected server's tool list and annotations as the source of truth. The following compatibility safety map also describes tools available only in explicit legacy profiles; it is not the default catalog. `gemini_cleanup` is restricted to MCP-owned ledger entries and never matches titles. Cookie, Doctor and local Prompts stay outside both business catalogs.

On the account surface, `gemini_history` defaults to compact metadata with pagination; text reads are explicit and bounded. `gemini_manage_chat` is an idempotent, non-destructive metadata mutation for rename/pin/unpin on one exact chat ID. It reads no conversation text and requires fresh read-back before reporting completion; `mutation_sent=true, changed=null` is unverified. `gemini_delete_chat` remains destructive and separate. `gemini_notebooks` defaults to Notebook source-picker metadata; `source="gemini"` is the same integrated product's project view for chat listing and moves. A move target must be freshly resolved in that view, without guessing ID conversion.

Function `gemini_ask(notebook_ids=...)` starts background ordinary Chat; `gemini_run_task` explicitly starts Task. Both preserve an operation handle and save a verified full-answer Artifact before owned-chat cleanup. The assist compatibility server retains its five-tool catalog and does not expose Notebook attachment arguments.

Account `gemini_preferences` manages saved browser/action preferences using returned handles and optional revisions. Writes have independent read-back; their verification scope is `stored_preference`, with `behavior_verified=false`. `permission_preference` is the saved value; `effective_permission` remains unknown. Native cards may still appear. Its annotation is mutating, non-destructive and non-idempotent.

`gemini_skills` manages native Gemini Skills. List metadata first and read one exact instruction body only with explicit intent. `gemini_manage_notebook` manages exact Notebooks and plaintext, URL or UTF-8 `.txt` sources. Check the exact source ID for indexed state and verify source QA separately. Notebook deletion includes its conversations. Both are account-only destructive unions.

## Annotation legend

- `read_only` — does not mutate remote/local state
- `destructive` — can delete remote resources or clear MCP/Gemini session data; treat as explicit-user-intent
- `privacy` — what private data the tool reads or sends (see tiers below)

## Safety tiers

### Destructive tools (require explicit user intent)

| Tool | Group | Purpose |
|---|---|---|
| `gemini_reset_session` | core | Clear one MCP/Gemini session and its configured remote-chat lifecycle; never changes agent memory or agent instructions |
| `gemini_run_task` | tasks | Send user-requested Task and explicit Notebook context; connected-app requests may mutate data (`sends_user_requested_task_and_explicit_notebook_context`) |
| `gemini_tasks` | account | Task metadata, bounded progress, schedules, and exact rename/pin/unpin/delete with read-back. Destructive union |
| `gemini_task_browser` | account | Verified screenshot, conditional frame read or explicit one-shot input. Destructive union |
| `gemini_cleanup` | account | Clean MCP-owned ledger entries under the requested scope |
| `gemini_cleanup_test_artifacts` | explicit legacy history | Find and optionally delete test chats/scheduled actions by marker |
| `gemini_delete_chat` | history | Request remote chat deletion and report read-back evidence |
| `gemini_delete_scheduled_action` | account | Delete a scheduled action by id |
| `gemini_manage_gems` | gems | List/create/update/delete Gems; use an explicit account request for mutation |
| `gemini_skills` | account | Native Skills metadata, explicit instruction reads and verified create/update/enable/delete |
| `gemini_manage_notebook` | account | Native Notebook and source management; Notebook deletion includes conversations |

### Reads private chat text (require explicit user intent)

| Tool | Group | Privacy |
|---|---|---|
| `gemini_read_chat` | history | `reads_private_chat_text` |
| `gemini_export_chat` | history | `reads_private_chat_text` |
| `gemini_list_research_report_actions` | research | `reads_private_chat_text` |
| `gemini_create_from_research_report` | research | `reads_private_chat_text_and_writes_local_file` |
| `gemini_search_chats` | history | `reads_private_chat_metadata_and_optional_turn_text` (text only when `scan_turns=true`) |

### Read-only discovery / inventory (safe defaults)

| Tool | Group | Purpose |
|---|---|---|
| `gemini_get_tool_manifest` | account | Agent-facing manifest with safety/privacy/workflow metadata |
| `gemini_get_web_capabilities` | account | Static observed Pro Web capability map |
| `gemini_probe_web_features` | account | Read-only RPC reachability probe (no raw bodies) |
| `gemini_account_inventory` | account | Read-only facade: links/usage/library/notebooks/scheduled/modes/models |
| `gemini_inspect_account` | account | Sanitized account feature/RPC status |
| `gemini_doctor` | cookie | Local preflight: tool groups, cookie, browser profile, media deps |
| `gemini_get_cookie_status` | cookie | Local cookie availability (no values) |
| `gemini_list_browser_cookie_profiles` | cookie | Browser profile list + diagnostics (no values) |
| `gemini_list_models` | account | MCP model aliases + runtime registry |

### Chat / media / files (send user content to Gemini)

| Tool | Group | Privacy |
|---|---|---|
| `gemini_chat` / `gemini_chat_stream` | core | `sends_user_prompt_and_optional_files`; `_stream` collects the upstream stream into one MCP result |
| `gemini_start_chat` / `gemini_send_message` / `gemini_send_message_stream` | core | `sends_user_prompt...`; `_stream` collects the upstream stream into one MCP result |
| `gemini_list_sessions` | core | `local_session_metadata` (read-only) |
| `gemini_generate_media` | media | `sends_user_prompt_and_optional_reference_files` |
| `gemini_generate_music` | media | `sends_user_prompt` |
| `gemini_upload_file` | files | `sends_local_file_content` |
| `gemini_analyze_url` | files | `sends_url_to_gemini` |
| `gemini_deep_research` | research | `sends_research_query`; returns typed queued/running/completed/timed_out state and continuation IDs |

### History metadata (read-only, no turn text unless noted)

| Tool | Group | Privacy |
|---|---|---|
| `gemini_history` | history | facade: `reads_private_chat_metadata_and_optional_turn_text` |
| `gemini_list_chats` | history | `reads_private_chat_metadata` |
| `gemini_scan_chat_history_sources` | history | `reads_private_chat_metadata` |
| `gemini_list_public_links` | account | `reads_private_public_link_index` |
| `gemini_get_usage_limits` | account | `reads_private_usage_state` |
| `gemini_list_library_capabilities` | account | `reads_template_capabilities` |
| `gemini_notebooks` / `gemini_list_notebooks` / `gemini_list_notebook_chats` | account | `reads_private_notebook_metadata` |
| `gemini_move_chat_to_notebook` | account | `moves_private_chat_metadata` (mutates, not destructive) |
| `gemini_list_scheduled_actions` / `gemini_get_scheduled_action` | account | `reads_private_scheduled_action_*` |
| `gemini_create_scheduled_action` | account | `creates_private_scheduled_action` (mutates) |
| `gemini_get_tool_mode_status` | account | `reads_mode_status_only` |

`gemini_tasks` list/get read metadata only and do not return full answer text; delete is not a cancellation API. Task schedules are distinct from Chat scheduled actions and are not managed by the scheduled-action tools. Notebook metadata and sources are not MCP-owned temporary chats.

### Authentication and local state

| Tool | Group | Effect |
|---|---|---|
| `gemini_get_cookie_from_browser` | cookie | With explicit user approval already given for this task or earlier in the conversation, caches sensitive account-authentication material locally; restrict file access and never log, back up, or share it |
| `gemini_reset` | always | Resets the local Gemini client connection; it does not delete agent memory or instructions |

## Low-token skill server facade (`src.skill_server`)

Fewer, broader tools with `action` parameters. Same safety tiers apply.

| Tool | Annotations | Notes |
|---|---|---|
| `chat` | `MUTATES_REMOTE` | text/image chat; sends user content to Gemini |
| `account` | `READS_PRIVATE_REMOTE` | `action="manifest\|capabilities"` are auth-free; other actions read private inventory |
| `history` | `DESTRUCTIVE_REMOTE` | `action="list\|search\|read\|export"` read-only; `action="delete"` destructive |
| `scheduled` | `DESTRUCTIVE_REMOTE` | `action="list\|get"` read-only; `action="create\|delete"` mutate/destroy |
| `create` | `MUTATES_REMOTE` | image/video/music generation; previews do not substitute for playable media artifacts |
| `edit` | `MUTATES_REMOTE` | image editing |
| `session` | `DESTRUCTIVE_REMOTE` | create/send/list/reset actions; reset affects Gemini conversation state |
| `prompts` | `DESTRUCTIVE_LOCAL` | local prompt list/get/create/delete |

History list/search/read/export/delete share typed domain data across primary and compact surfaces. For deletion,
`verified_absent` is the only positive deletion proof; `not_available` is accepted/unverified, while `still_present` and
`read_back_error` are `VERIFICATION_FAILED` outcomes. Positive absence requires a complete fresh recent/pinned metadata
read-back; `read_chat(None)` is inconclusive. Record returned test-chat IDs because Gemini-generated titles may omit prompt
markers; metadata-only cleanup is a fallback, and `scan_turns=true` requires explicit permission to read turn text. Browser
profile tools never return Cookie values. On macOS, the system browser-credential prompt only unlocks the browser Cookie
store for `browser-cookie3`; the workflow does not inspect arbitrary credential files. Authorization timeouts surface as
`BROWSER_COOKIE_ACCESS_TIMEOUT`.

## Tool group selection (`GEMINI_TOOLS`)

| Group | Tools included | Good default? |
|---|---|---|
| `model` / `chat` | chat | model-only agents |
| `history` | manage:history-read | read-only history |
| `history-organize` | manage:history-read + notebooks-read/write | history + notebook moves |
| `account-read` | manage:account-read | read-only inventory |
| `scheduled-admin` | manage:scheduled-read + scheduled-write | authorized scheduled CRUD |
| `manage:gems` | manage:gems | explicit Gem list/create/update/delete |
| `history,manage:history-write` | manage:history-read + history-write | identify a chat, then explicitly delete it or clean test artifacts |
| `prompts` | prompts | local saved prompts |
| `core` (default) | chat + media + file + research | broad content workflow |
| `all` | everything + manage:all | maintenance/verification only |
