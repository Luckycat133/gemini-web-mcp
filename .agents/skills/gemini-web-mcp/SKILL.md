---
name: gemini-web-mcp
description: "Use this skill when an agent should extend itself with Gemini Web: get a second opinion, search current web sources, understand images/files/URLs, run Deep Research, generate image/video/music artifacts, edit images, delegate ongoing workflows or operate websites through Gemini Task, or explicitly work with Gemini account data. Route by user intent to single-purpose tools instead of loading every tool. This skill is the compatibility router; prefer the focused gemini-assist skill when only assistance and understanding are needed. Do not use for repository implementation, tests, CI, packaging, or releases—use gemini-web-mcp-development instead."
license: MIT-0
compatibility: "Requires Python 3.11+ and a connected Gemini Web MCP server. The default function server exposes 8 unprefixed tools (`ask`, `understand`, `image`, `video`, `music`, `research`, `task`, `operation`); the separate account server handles explicit account work. Assist, full (`GEMINI_TOOLS=function-full` / `account-full`), and compact compatibility servers remain supported. Media completion requires verified artifacts. Live calls require Gemini Web account Cookies."
metadata:
  version: "0.23.0"
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

```text
1. Agent assistance and multimodal understanding
2. Generated artifacts
3. Explicit Gemini account management
```

## Choose the Capability Lane

| User intent | Preferred route (`gemini-mcp-function`) | What success means |
| --- | --- | --- |
| Second opinion, search, Gem, Canvas, learning, multi-turn chat, or Notebook Q&A | `ask` (`model`: `hydrogen`/`neon`/`carbon`/`auto`; `thinking_level`: `low`/`medium`/`high`; optional `gem_id`, `learning_mode`, `start_session`/`session_id`, `notebook_ids`) | grounded answer incorporated into task |
| Understand images, local files, URLs, or mixed inputs (up to 16) | `understand` (`task`, `inputs` accepting plain paths/URLs/text strings or typed objects) | all inputs acknowledged in verified analysis |
| Delegate ongoing / multi-step / browser-app Task workflows | `task` (optional `notebook_ids`, `input_id`/`input_option_id`) | original handle yields verified Task result |
| Deep Research & report derivatives | `research` (`query`, then `report_action` on ready `operation_id`) | verified report or local webpage/infographic/quiz/flashcards/audio_overview/custom_app |
| Generate new images or edit/transform existing images | `image` (`aspect_ratio`, optional `image_paths` / `source_operation_id`, `wait_seconds=120`) | verified local image inspected via `read_file` |
| Generate video | `video` (`duration_seconds` `10`/`15`, `aspect_ratio` `16:9`/`9:16`, optional `10`s `image_path`, `wait_seconds=300`) | verified MP4 inspected via `ffprobe` |
| Generate music or songs | `music` (`duration` `short`/`standard`, `vocals`, `genre`, `wait_seconds=240`) | verified playable MP3/MP4 inspected via `ffprobe` |
| Check or fetch async results (`research`, `task`, `image`, `video`, `music`, notebook chat) | `operation` (`operation_id`, `status_only` or `wait_seconds` `0..300`) | verified status, artifact, or report without duplicate work |
| Account history, Notebooks, Gems, Skills, Schedules, Quotas, Cleanup | `gemini-web-cli` (zero MCP context tax) or `gemini-mcp-account` | requested data or verified read-back |

## Models, Thinking & Prompting

- **LLMs (Chat / Understand / Research / Task)**: `hydrogen` (default — fast general & multimodal), `neon` (deeper analysis/reasoning), `carbon` (coding & complex work), `auto` (Gemini Web auto-routing). Default `thinking_level` is `medium` (`low` / `medium` / `high`).
- **Multimodal generation (`image`, `video`, `music`)**: backend engines are selected and managed automatically by Gemini Web — no hidden engine parameters needed.
- **Prompting Guides**: Load [prompting_guides.md](references/prompting_guides.md) before drafting complex media prompts. Keep `music` prompts focused on natural musical descriptions (mood, instrumentation, genre, tempo feel) and avoid DAW/MIDI/bar-by-bar specs that can cause text-only replies.
- **No Sensitive-Word Pre-Interception**: Do **not** pre-screen or block prompts locally using any sensitive-word list. Send the prompt as requested; if upstream returns `UPSTREAM_REJECTED`, rephrase and retry.

## Workflow, Artifacts & Resilience

Connect `gemini-mcp-function` (8 tools) and optionally `gemini-mcp-account` (6 tools, or prefer `gemini-web-cli`). Do **not** call the manifest before every known workflow; call `gemini_get_tool_manifest` or `account(action="manifest")` only on discovery/recovery gaps.

When generating media, pass that file or URI to the next relevant tool rather than stopping at a file path, and verify every saved image (`read_file`) or audio/video (`ffprobe`). See [artifacts.md](references/artifacts.md).

Image, video, music and Deep Research start in the background by default; `wait_seconds=0` returns an `operation_id` immediately for parallel work. Recover timed-out operations via `operation(operation_id=...)` — never resubmit a running handle. Cancellation is not available. See [operations.md](references/operations.md) and [workflows.md](references/workflows.md).

- Transient transport blips and expired browser Cookies auto-recover before failing.
- On `RATE_LIMITED` / `UPSTREAM_LIMIT_EXCEEDED`, inspect `meta.details.reset_at` / `retry_after_seconds` (or `gemini-web-cli quotas`). **Do not tight-loop retry** — wait until after reset or defer a single retry using your host environment's scheduling/timer capability if available.

The Skill supplies routing instructions; an MCP server must also be connected. See [tool_surface.md](references/tool_surface.md) and [recovery.md](references/recovery.md) for troubleshooting and compatibility profiles (`gemini-assist`, `function-full`, `account-full`).
