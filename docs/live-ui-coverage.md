# Gemini Web Live UI Coverage

This page records the native Gemini Web surface observed in a signed-in Chrome
session on 2026-05-22 and maps it to the primary MCP server in `src.server`.
Browser extension UI injected into Gemini was ignored during this pass.

## Observed Native UI

The chat surface exposed:

- New chat, chat search, Library, and Gems in the side navigation.
- Temporary chat, export chat, and chat info controls.
- Prompt upload/tools menu, model picker, microphone, and send controls.
- Visible model picker entries: `3.1 Flash-Lite`, `3.5 Flash`, `3.1 Pro`,
  plus a separate thinking-level submenu.

The upload/tools menu exposed:

- Upload file.
- Add from Google Drive.
- Import code.
- Create image.
- Create video.
- Canvas.
- Deep Research.
- Create music.
- Guided Learning.
- Personalization toggle.

The settings menu exposed Activity, personalization, memory import, limits,
scheduled actions, Gems, public links, theme, subscriptions, Ultra upsell,
NotebookLM, help/feedback, and location entries.

## Primary MCP Coverage

| Native UI surface | MCP coverage | Notes |
|---|---|---|
| Chat and streaming chat | Covered | `gemini_chat`, `gemini_chat_stream`, session tools |
| Temporary chat | Covered | `temporary` is forwarded to `gemini-webapi` |
| Gems | Covered in part | CRUD and chat use via `gemini_manage_gems` and `gem_id` |
| Upload file | Covered | Local files use `gemini_upload_file` |
| Import code | Covered in part | Local code files can be uploaded; UI import workflows are not replicated |
| Create image/video/music | Covered in part | Generic web generation plus response parsing; account/UI gates still apply |
| Deep Research | Covered | Full workflow when the installed client exposes research helpers |
| Dynamic model discovery | Covered | `gemini_list_models` reports the account model registry after init |
| Library | Not covered | No current library asset RPC wrapper |
| Add from Google Drive | Not covered | No Drive picker or Drive attachment RPC wrapper |
| Canvas | Not covered | No current Canvas RPC wrapper |
| Guided Learning | Not covered | No current Guided Learning mode wrapper |
| Thinking level submenu | Covered | Generation requests accept `thinking_level=standard/extended` |
| Personalization and settings | Not covered | No settings CRUD wrapper in the installed client |
| Scheduled actions and public links | Not covered | No current RPC wrapper |

## Model Contract

Chat, file, and media generation tools accept the current Web aliases
`flash-lite`, `flash`, and `pro`; compatibility aliases such as `fast` and
`thinking` remain accepted. They also accept a runtime model/display name from
the installed `gemini-webapi` model registry. The Web UI thinking selector is
separate from the model picker and maps to `thinking_level=standard` or
`thinking_level=extended`.

The visible model names in Gemini Web can drift faster than the package enum.
Treat the runtime registry as the source of truth for an authenticated account.

## Media Routing Notes

- Image generation currently ignores the selected chat model for the first pass
  and lands on `Nano Banana 2`.
- A `pro` image redo is a post-generation UI action and is not exposed as a
  separate first-pass MCP model.
- Music generation currently splits by model family:
  `flash-lite` / `flash` / `fast` / `thinking` land on `Lyria 3`,
  while `pro` lands on `Lyria 3 Pro`.
