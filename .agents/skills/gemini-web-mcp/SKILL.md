---
name: gemini-web-mcp
description: "Use this skill when an agent should extend itself with Gemini Web: get a second opinion, search current web sources, understand images/files/URLs, run Deep Research, generate image/video/music artifacts, edit images, delegate ongoing workflows or operate websites through Gemini Task, or explicitly work with Gemini account data. Route by user intent to single-purpose tools instead of loading every tool. This skill is the compatibility router; prefer the focused gemini-assist skill when only assistance and understanding are needed. Do not use for repository implementation, tests, CI, packaging, or releases—use gemini-web-mcp-development instead."
license: MIT-0
compatibility: "Requires Python 3.11+ and a connected Gemini Web MCP server. The function server exposes 10 single-purpose tools (`gemini_ask`, `gemini_understand`, `gemini_generate_image`, `gemini_edit_image`, `gemini_generate_video`, `gemini_generate_music`, `gemini_research`, `gemini_run_task`, `gemini_get_operation_status`, `gemini_get_operation_result`); the separate account server handles explicit account work. Assist and compact compatibility servers remain supported. Media completion requires verified artifacts, and availability depends on the account and upstream service. Live calls require Gemini Web account Cookies."
metadata:
  version: "0.22.0"
  openclaw:
    emoji: "♊️"
    homepage: https://github.com/Luckycat133/gemini-web-mcp
    requires:
      bins:
        - uvx
    primaryEnv: GEMINI_PSID
    envVars:
      - name: GEMINI_PSID
        required: false
        description: Optional __Secure-1PSID Cookie for authenticated Gemini Web calls.
      - name: GEMINI_PSIDTS
        required: false
        description: Optional matching __Secure-1PSIDTS Cookie recommended for session stability.
      - name: GEMINI_PSIDCC
        required: false
        description: Optional __Secure-1PSIDCC Cookie forwarded when configured.
      - name: GEMINI_PROXY
        required: false
        description: Optional HTTP or HTTPS proxy used by the MCP server.
      - name: GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS
        required: false
        description: Optional bounded macOS browser-credential authorization wait for Cookie discovery.
      - name: GEMINI_TOOLS
        required: false
        description: Optional primary-server profile such as function-full, account-full, model, or history.
---

# Gemini Web MCP

Use this Skill to complete the user's task with Gemini, not to tour the Gemini tool surface.

The product priority is:

```text
1. Agent assistance and multimodal understanding
2. Generated artifacts
3. Explicit Gemini account management
```

Choose the MCP entry that serves the user's next step. For a task that spans both entries, use each only when its tools are needed.

## Choose the Capability Lane

| User intent | Preferred route | What success means |
| --- | --- | --- |
| Second opinion, search, Gem, Canvas, learning mode, or multi-turn chat | `gemini_ask` with optional `gem_id`, `canvas`, `learning_mode`, or `start_session`/`session_id` | usable grounded answer incorporated into the task |
| Understand images, files, URLs, or mixed inputs (up to 16) | `gemini_understand` (`task`, `inputs`) | analyzed inputs acknowledged in verified output |
| Ask about an existing Notebook's sources | `gemini-web-cli notebooks list` or account `gemini_notebooks`, then `gemini_ask(notebook_ids=...)` | original background chat handle yields a verified full-answer Artifact |
| Delegate a workflow needing Task execution or browser operation | `gemini_run_task` | original Task handle yields the requested result |
| Deep Research & report derivatives | `gemini_research`, then `report_action` on ready `operation_id` | verified report or local quiz/infographic/webpage/flashcards/audio_overview/custom_app |
| Generate images (`Nano Banana 2.1 / Nano Banana Pro`) | `gemini_generate_image` (`aspect_ratio`, optional `image_paths`, `wait_seconds=120`) | verified local image inspected via `read_file` |
| Edit local or saved images (`Nano Banana 2.1 / Pro`) | `gemini_edit_image` (`image_path` / `image_paths` / `source_operation_id`, `wait_seconds=120`) | verified edited image matching requested delta |
| Generate video (`Gemini Omni`) | `gemini_generate_video` (`duration_seconds` `10`/`15`, `aspect_ratio` `16:9`/`9:16`, optional `10`s `image_path`, `wait_seconds=300`) | verified MP4 inspected via `ffprobe` |
| Generate music/songs (`Lyria 3.5`) | `gemini_generate_music` (`music_duration`, `music_vocals`, `music_genre`, `wait_seconds=240`) | verified playable MP4/MP3 inspected via `ffprobe` |
| Account history, Notebooks, Gems, Skills, Schedules, Quotas, Cleanup | `~/.local/bin/gemini-web-cli` (zero MCP context tax) or `gemini-mcp-account` | requested data or verified read-back |

- **Latest models (`Default` Ultra)**:
  - Default `flash` / `hydrogen` (and omitted media `model=None`) selects **4 Hydrogen**; `pro` / `neon` selects **4 Neon** (`更深入的分析`); `carbon` selects **4 Carbon** (`代码和复杂工作`). Paid `model="gemini-omni-1.2-flash"` / `GEMINI_API_KEY` is disabled (`INVALID_ARGUMENT`).
  - Creative engines: **Nano Banana 2.1 / Pro** (Image/Edit), **Gemini Omni** (Video), **Lyria 3.5** (Music).
- **Never substitute built-in generators**: always call the Gemini Web MCP generation tools when asked for Gemini Web media.
- **Official Prompting Guides**: Before drafting complex media prompts, load [prompting_guides.md](references/prompting_guides.md) (Nano Banana 2.1 6-part sentences + positive framing + quoted text; Gemini Omni 7-part director's brief + native audio + verb physics; Lyria 3.5 6-part arrangement/timestamps + UI options).
- **No Sensitive-Word Pre-Interception**: Do **not** pre-screen or block prompts locally using any sensitive-word list. Pass prompts directly; only if upstream returns `UPSTREAM_REJECTED` should you adapt wording and retry.

## Standard Workflow & Default Surface

Configure `gemini-mcp-function` and `gemini-mcp-account` as separate MCP entries (`10` function tools and `6` account tools, or use `gemini-web-cli`). Do **not** call the manifest before every known workflow; call `gemini_get_tool_manifest` or `account(action="manifest")` only on discovery/recovery gaps.

For generation, pass that file or URI to the next relevant tool rather than stopping at a file path, and verify every saved image (`read_file`) or MP4/MP3 (`ffprobe`). [Artifacts & handoff](references/artifacts.md)

## Long Operations, Auto-Recovery & Rate Limits

Image, video, music and Deep Research start in the background by default (`wait_seconds` defaults: `120`s image/edit, `240`s music, `300`s video; pass `0` for immediate parallel handles). Recover timed-out operations via `gemini_get_operation_result` using the same `operation_id` — never duplicate a timed-out job. Cancellation is not available. Load [operations.md](references/operations.md) and [workflows.md](references/workflows.md) for recovery and mode settings.

- Transient transport blips and expired browser Chrome cookies auto-recover internally before failing.
- When `RATE_LIMITED` / `UPSTREAM_LIMIT_EXCEEDED` is returned, inspect `meta.details.reset_at` / `retry_after_seconds` (or `gemini-web-cli quotas`). **Do not tight-loop retry** — set a one-shot timer (`schedule` tool / `/schedule` with `DurationSeconds = retry_after_seconds + 60`, or `~600`s for traffic spikes) to retry automatically after reset.

The Skill supplies routing instructions; an MCP server must also be connected. For account setup, cleanup rules, or troubleshooting, see [tool_surface.md](references/tool_surface.md) and [recovery.md](references/recovery.md).
