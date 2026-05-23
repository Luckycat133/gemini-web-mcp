# API Reference

This reference documents the current MCP tool surface exposed by
`src.server`. It reflects the actual `list_tools()` output after the
2026-05-23 Gemini Web UI alignment.

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
| `image_paths` | `list[str]` | Optional local image attachments |
| `gem_id` | `str` | Optional Gem ID for one-shot or session creation |
| `temporary` | `bool` | Use Gemini Web temporary chat behavior |
| `retain_chat` | `bool` | Keep the remote Gemini chat instead of scheduling cleanup |
| `delete_after_seconds` | `int` | Override remote chat cleanup delay |

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

### Authentication And Client State

| Tool | Purpose |
|------|---------|
| `gemini_get_cookie_status` | Report current cookie/auth status |
| `gemini_get_cookie_from_browser` | Load Gemini cookies from Chrome, Firefox, or Edge |
| `gemini_reset` | Reset the Gemini client |

---

## Optional Tools

### `GEMINI_TOOLS=all`

Adds these management tools to `core`:

| Tool | Purpose |
|------|---------|
| `gemini_list_chats` | List Gemini Web chat history visible to the client |
| `gemini_list_models` | Show MCP aliases and the account runtime model registry |
| `gemini_manage_gems` | List, create, update, or delete Gems |

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

