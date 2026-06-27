# API Reference

This reference documents the current MCP tool surface exposed by
`src.server`. It reflects the actual `list_tools()` output after the
2026-06-18 Gemini Web Pro UI alignment.

---

## Tool Groups

| Group | Tools | Intended use |
|-------|-------|--------------|
| `core` | Chat, media, file/URL, Deep Research | Recommended default |
| `manage` | Chat history, model listing, Gems | Account content management |
| `prompts` | Local prompt library | Optional local helper |
| `all` | `core` + `manage` | Complete Gemini workflow without local prompt library |

The server always exposes authentication helpers and `gemini_reset`.

---

## Default Tools (`GEMINI_TOOLS=core`)

### Chat

| Tool | Purpose |
|------|---------|
| `gemini_chat` | One-shot Gemini conversation |
| `gemini_chat_stream` | One-shot streaming conversation |
| `gemini_start_chat` | Create a multi-turn session |
| `gemini_send_message` | Send a message to an existing session |
| `gemini_send_message_stream` | Stream a message in an existing session |
| `gemini_list_sessions` | List local active sessions |
| `gemini_reset_session` | Reset a local session and optionally delete the remote chat |

Common chat parameters:

| Parameter | Type | Notes |
|-----------|------|-------|
| `model` | `str` | `flash-lite`, `flash`, `pro`; `fast` and `thinking` remain compatibility aliases |
| `thinking_level` | `str` | `standard` or `extended` |
| `learning_mode` | `str` | Optional guided-learning companion: `interactive_quiz`/`quiz`, `flashcards`, `practice_test`, `study_guide`/`exam_prep` |
| `image_paths` | `list[str]` | Optional local image attachments |
| `gem_id` | `str` | Optional Gem ID for one-shot or session creation |
| `temporary` | `bool` | Use Gemini Web temporary chat behavior |
| `retain_chat` | `bool` | Keep the remote Gemini chat instead of scheduling cleanup |
| `delete_after_seconds` | `int` | Override remote chat cleanup delay |

`learning_mode` is implemented as a Web-compatible request injection for the
observed 2026-06-19 Guided Learning companion. It is available on one-shot and
session chat tools, including streaming variants.

### Media

| Tool | Purpose |
|------|---------|
| `gemini_generate_media` | Generate image, video, or music |
| `gemini_generate_music` | Convenience wrapper for music generation |

Current Gemini Web media routing:

| Request | Effective backend |
|---------|-------------------|
| `media_type=image` | First pass always reports `Nano Banana 2` |
| `media_type=music`, `model=flash-lite` / `flash` / `fast` / `thinking` | `Lyria 3` |
| `media_type=music`, `model=pro` | `Lyria 3 Pro` |
| `media_type=video` | Gemini Web default video path, currently documented as Veo 3.1 |

`model=pro` does not directly select a different first-pass image backend.
The Pro image redo control is a post-generation Gemini Web UI action.

### File And URL

| Tool | Purpose |
|------|---------|
| `gemini_upload_file` | Upload a local file and ask Gemini to analyze it |
| `gemini_analyze_url` | Ask Gemini to analyze a web or YouTube URL |

### Research

| Tool | Purpose |
|------|---------|
| `gemini_deep_research` | Create a Deep Research plan, start research, and poll for the result when supported by the client |
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
| `gemini_list_browser_cookie_profiles` | List local browser profile diagnostics without returning cookie values |
| `gemini_get_cookie_from_browser` | Load Gemini cookies from Chrome, Firefox, Edge, or a selected Chrome profile |
| `gemini_reset` | Reset the Gemini client |

---

## Optional Tools

### `GEMINI_TOOLS=all`

Adds these management tools to `core`:

| Tool | Purpose |
|------|---------|
| `gemini_inspect_account` | Inspect current account feature/RPC status without exposing raw RPC previews |
| `gemini_get_web_capabilities` | Return the observed Pro Web model/menu/settings capability manifest and MCP coverage map |
| `gemini_list_chats` | List Gemini Web chat history metadata with pagination |
| `gemini_search_chats` | Search chat history metadata, optionally scanning turns when explicitly requested |
| `gemini_read_chat` | Read turns from a specific Gemini Web chat |
| `gemini_export_chat` | Export one Gemini Web chat as Markdown or JSON |
| `gemini_delete_chat` | Delete a specific Gemini Web chat |
| `gemini_cleanup_test_artifacts` | Dry-run or delete marked test chats and scheduled actions |
| `gemini_probe_web_features` | Probe observed read-only RPC reachability for newer Web UI surfaces |
| `gemini_list_public_links` | List public links returned by Gemini Web sharing surface |
| `gemini_get_usage_limits` | Read usage/quota structures from Gemini Web usage surface |
| `gemini_list_library_capabilities` | List localized Library capability/template entries |
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

### `GEMINI_TOOLS=prompts`

Adds the local prompt-library helper:

| Tool | Purpose |
|------|---------|
| `gemini_manage_prompts` | List, get, create, or delete local prompt snippets |

This tool is intentionally not part of `core` or `all`, because it is local
state management rather than a Gemini Web AI capability.

---

## Removed Or Merged Tools

| Tool | Status |
|------|--------|
| `gemini_list_features` | Removed |
| Independent image tool aliases | Merged into `gemini_generate_media` |
| `gemini_health_check` | Not exposed by the current server |
