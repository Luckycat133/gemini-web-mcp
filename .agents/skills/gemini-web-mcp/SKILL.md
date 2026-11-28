---
name: gemini-web-mcp
description: "Use this skill when an agent should extend itself with Gemini Web: get a second opinion, search current web sources, understand images/files/URLs, run Deep Research, generate image/video/music artifacts, edit images, delegate ongoing workflows or operate websites through Gemini Task, or explicitly work with Gemini account data. Route by user intent to single-purpose tools instead of loading every tool. This skill is the compatibility router; prefer the focused gemini-assist skill when only assistance and understanding are needed. Do not use for repository implementation, tests, CI, packaging, or releases—use gemini-web-mcp-development instead."
license: MIT-0
compatibility: "Requires Python 3.11+ and a connected `gemini-web-mcp` server. The unified server exposes 8 verb-object tools (`ask-gemini`, `generate-image`, `generate-video`, `generate-music`, `run-research`, `run-task`, `get-operation`, `check-quota`); `gemini-web-cli` handles account administration. Full compatibility catalogs (`function-full` / `account-full`) remain supported internally. Media completion requires verified artifacts. Live calls require Gemini Web account Cookies."
metadata:
  version: "0.24.3"
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

| User intent | Preferred route (`gemini-web-mcp`) | What success means |
| --- | --- | --- |
| Q&A, web search, multimodal inputs (images/files/URLs), multi-turn chat, or Notebook Q&A | `ask-gemini` (`model`: `hydrogen`/`neon`/`carbon`/`auto`; `thinking_level`: `low`/`medium`/`high`; optional `inputs` up to 16 paths/URLs/text, `start_session`/`session_id`, `notebook_ids`) | grounded answer (all `inputs` acknowledged in verified analysis) |
| Delegate ongoing / multi-step / browser-app Task workflows | `run-task` (`wait_seconds=240` default, optional `notebook_ids`, `input_id`/`input_option_id`; auto-approves confirmation cards by default) | verified Task result or handle resumed via `get-operation` |
| Deep Research & report derivatives | `run-research` (`query`, `wait_seconds=240` default; `report_action` on ready `operation_id`) | verified report or local webpage/infographic/quiz/flashcards/audio_overview/custom_app |
| Generate new images or edit/transform existing images | `generate-image` (`aspect_ratio`, optional `image_paths` / `source_operation_id`, `wait_seconds=120`) | verified local image inspected via `read_file` |
| Generate video | `generate-video` (`duration_seconds` `10`/`15`, `aspect_ratio` `16:9`/`9:16`, optional `10`s `image_path`, `wait_seconds=300`) | verified MP4 inspected via `ffprobe` |
| Generate music or songs | `generate-music` (`duration` `short`/`standard`, `vocals`, `genre`, `wait_seconds=240`) | verified playable MP3/MP4 inspected via `ffprobe` |
| Check or fetch async results (`run-research`, `run-task`, `generate-image`, `generate-video`, `generate-music`) | `get-operation` (`operation_id`, `status_only` or `wait_seconds` `0..300`) | verified status, artifact, or report without duplicate work |
| Inspect 5-hour & weekly compute quotas, remaining credits & reset times | `check-quota` | verified 5h/weekly remaining credits, % used, and reset timestamps |
| Account history, Notebooks, Skills, Schedules, Cleanup | `gemini-web-cli` (zero MCP context tax) | requested data or verified read-back |

## Models, Thinking & Prompting

- **LLMs (`ask-gemini`, `run-research`, `run-task`)**: `hydrogen` (default — fast general & multimodal), `neon` (deeper analysis/reasoning), `carbon` (coding & complex work), `auto` (Gemini Web auto-routing). Default `thinking_level` is `medium` (`low` / `medium` / `high`).
- **Multimodal generation (`generate-image`, `generate-video`, `generate-music`)**: backend engines are selected and managed automatically by Gemini Web — no hidden engine parameters needed.
- **Prompting Guides**: Load [prompting_guides.md](references/prompting_guides.md) before drafting complex media prompts. For `generate-video` (Gemini Omni 1.2 Flash): anchor recurring people in a keyframe built from their character sheet, refer to them by role instead of restating face/age, write `Single continuous shot, no scene cuts` unless you script timecoded beats, and rephrase (never blind-retry) after `UPSTREAM_REJECTED`. Keep `generate-music` prompts focused on natural musical descriptions (mood, instrumentation, genre, tempo feel) and avoid DAW/MIDI/bar-by-bar specs that can cause text-only replies.
- **No Sensitive-Word Pre-Interception**: Do **not** pre-screen or block prompts locally using any sensitive-word list. Send the prompt as requested; if upstream returns `UPSTREAM_REJECTED`, rephrase and retry.

## Workflow, Artifacts & Resilience

Connect unified `gemini-web-mcp` (8 verb-object tools) alongside companion `gemini-web-cli` for account administration. Do **not** call manifest discovery before every known workflow; only check manifest on discovery/recovery gaps.

When generating media, pass that file or URI to the next relevant tool rather than stopping at a file path, and verify every saved image (`read_file`) or audio/video (`ffprobe`). See [artifacts.md](references/artifacts.md).

`generate-image`, `generate-video`, `generate-music`, `run-research`, and `run-task` wait with MCP progress heartbeats by default; pass `wait_seconds=0` to return an `operation_id` immediately for parallel work. Recover timed-out operations via `get-operation(operation_id=...)` — never resubmit a running handle. Cancellation is not available. Task confirmation cards auto-approve by default across MCP and Web UI Tasks (`GEMINI_TASK_AUTO_APPROVE=false` opts out). See [operations.md](references/operations.md) and [workflows.md](references/workflows.md).

- Transient transport blips and expired browser Cookies auto-recover before failing.
- On `RATE_LIMITED` or `UPSTREAM_LIMIT_EXCEEDED`, use `check-quota` to verify the 5-hour and weekly balances. Report which limit was actually observed. Honor `meta.details.retry_after_seconds` and its source. If the retry time is unknown, wait about 10 minutes or try during off-peak hours. Retry a confirmed failure at most once after waiting. For pending or unverified work, use `get-operation` with the original handle or recover the original chat session. `check-quota` retries initialization once after a known local browser-probe cooldown.

The Skill supplies routing instructions; an MCP server must also be connected. See [tool_surface.md](references/tool_surface.md) and [recovery.md](references/recovery.md) for troubleshooting and compatibility profiles (`gemini-assist`, `function-full`, `account-full`).
