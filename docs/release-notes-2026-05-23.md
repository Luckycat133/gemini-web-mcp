# Release Notes 2026-05-23

## Summary

This update aligns the MCP server with the Gemini Web UI observed on
2026-05-22 and trims the default tool surface for AI clients.

## User-visible changes

- Default tool group is now `core`.
- First-pass image generation always reports `Nano Banana 2`.
- Music generation now reports its effective backend:
  `flash-lite` / `flash` / `fast` / `thinking` -> `Lyria 3`,
  `pro` -> `Lyria 3 Pro`.
- Image requests now explain that Pro redo is a post-generation UI action.
- `all` no longer auto-loads the local prompt-management tool.
- `gemini_list_features` has been removed.

## Current tool surface

Default `GEMINI_TOOLS=core`:

- `gemini_chat`
- `gemini_chat_stream`
- `gemini_start_chat`
- `gemini_send_message`
- `gemini_send_message_stream`
- `gemini_list_sessions`
- `gemini_reset_session`
- `gemini_generate_media`
- `gemini_generate_music`
- `gemini_upload_file`
- `gemini_analyze_url`
- `gemini_deep_research`
- `gemini_get_cookie_status`
- `gemini_get_cookie_from_browser`
- `gemini_reset`

`GEMINI_TOOLS=all` adds:

- `gemini_list_chats`
- `gemini_list_models`
- `gemini_manage_gems`

`GEMINI_TOOLS=prompts` exposes:

- `gemini_manage_prompts`

## Verification

- Tool workflow tests cover the narrowed `core` surface.
- Media tests cover image and music backend routing.
- Full pytest was rerun after the changes.
