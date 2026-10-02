# Gemini Web Tool Surface Reference

Compact safety/group map of the Gemini Web MCP tools. Load this only when you need
to pick tools by safety tier or group. The connected server's source of truth is
`gemini_get_tool_manifest` (primary server) and `account(action="manifest")`
(low-token server) — re-check those at runtime because `GEMINI_TOOLS` controls
which primary tools are registered in the current process. The focused
`gemini-mcp-assist` server exposes only its fixed five-tool catalog.

## Annotation legend

- `read_only` — does not mutate remote/local state
- `destructive` — can delete remote resources or clear MCP/Gemini session data; treat as explicit-user-intent
- `privacy` — what private data the tool reads or sends (see tiers below)

## Safety tiers

### Destructive tools (require explicit user intent)

| Tool | Group | Purpose |
|---|---|---|
| `gemini_reset_session` | core | Clear one MCP/Gemini session and its configured remote-chat lifecycle; never changes agent memory or agent instructions |
| `gemini_reset` | always | Clear all local sessions and reset the client; deletes non-retained remote chats and reports cleanup verification |
| `gemini_cleanup_test_artifacts` | history | Find and optionally delete test chats/scheduled actions by marker |
| `gemini_delete_chat` | history | Request remote chat deletion and report read-back evidence |
| `gemini_delete_scheduled_action` | account | Delete a scheduled action by id |
| `gemini_manage_gems` | gems | List/create/update/delete Gems; use an explicit account request for mutation |

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

### Authentication and local state

| Tool | Group | Effect |
|---|---|---|
| `gemini_get_cookie_from_browser` | cookie | With explicit user approval already given for this task or earlier in the conversation, caches sensitive account-authentication material locally; restrict file access and never log, back up, or share it |
| `gemini_reset` | always | Destructive reset; local success can accompany partial remote cleanup, so preserve warnings and verification |

## Low-token skill server facade (`src.skill_server`)

Fewer, broader tools with `action` parameters. Same safety tiers apply.

| Tool | Annotations | Notes |
|---|---|---|
| `chat` | `MUTATES_REMOTE` | text/image chat; sends user content to Gemini |
| `account` | `READS_PRIVATE_REMOTE` | `action="manifest\|capabilities"` are auth-free; other actions read private inventory |
| `history` | `DESTRUCTIVE_REMOTE` | `action="list\|search\|read\|export"` read-only; `action="delete"` destructive |
| `scheduled` | `DESTRUCTIVE_REMOTE` | `action="list\|get"` read-only; `action="create\|delete"` mutate/destroy |
| `create` | `MUTATES_REMOTE` | native image/music selection, local verification and new source-chat cleanup; video compatibility route remains unverified |
| `edit` | `MUTATES_REMOTE` | native image editing, local verification and new source-chat cleanup |
| `session` | `DESTRUCTIVE_REMOTE` | create/send/list/reset actions; reset affects Gemini conversation state |
| `prompts` | `DESTRUCTIVE_LOCAL` | local prompt list/get/create/delete |
| `cookie` | `MUTATES_LOCAL` | `action="profiles"` read-only; `action="get"` requires explicit user approval because it caches sensitive account-authentication material locally; restrict file access and remove the cache when no longer needed |
| `doctor` | `READ_ONLY_LOCAL` | local preflight |
| `cleanup` | `DESTRUCTIVE_REMOTE` | `dry_run=true` is safe; `dry_run=false` deletes |

History list/search/read/export/delete share typed domain data across primary and compact surfaces. For deletion,
`verified_absent` is the only positive deletion proof; `not_available` is accepted/unverified, while `still_present` and
`read_back_error` are `VERIFICATION_FAILED` outcomes. Positive absence requires a complete fresh recent/pinned metadata
read-back; `read_chat(None)` is inconclusive. Record returned test-chat IDs because Gemini-generated titles may omit prompt
markers; metadata-only cleanup is a fallback, and `scan_turns=true` requires explicit permission to read turn text. Browser
profile tools never return Cookie values. On macOS, the system browser-credential prompt only unlocks the browser Cookie
store for `browser-cookie3`; the workflow does not inspect arbitrary credential files. Authorization timeouts surface as
`BROWSER_COOKIE_ACCESS_TIMEOUT`.

An explicitly selected browser profile fails closed if unreadable or signed out. Changing authentication retires the old client and sessions, and cancels their cleanup with `cancellation_reason=authentication_context_changed`; it never runs those deletions under the new account. Same-material refresh preserves the active context. Cancelled work is not verified deletion.

Content search exposes `read_failures` and incomplete coverage even when no title or body matched. A bounded history page with `operation_state=partial` and `next_offset=null` cannot be continued through the same cursor. Use the metadata scan with a larger source bound. Prompt changes use atomic, locked storage; an unreadable/corrupt library or failed save must be repaired before reporting success.

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
