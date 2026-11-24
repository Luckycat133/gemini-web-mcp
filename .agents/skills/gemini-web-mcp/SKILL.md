---
name: gemini-web-mcp
description: "Use this skill when an agent should extend itself with Gemini Web: get a second opinion, search current web sources, understand images/files/URLs, run Deep Research, generate image/video/music artifacts, edit images, delegate ongoing workflows or operate websites through Gemini Task, or explicitly work with Gemini account data. Route by user intent instead of loading every tool. This skill is the compatibility router; prefer the focused gemini-assist skill when only assistance and understanding are needed. Do not use for repository implementation, tests, CI, packaging, or releases—use gemini-web-mcp-development instead."
license: MIT-0
compatibility: "Requires Python 3.11+ and a connected Gemini Web MCP server. The function server covers assistance, understanding, media, and Deep Research; the separate account server handles explicit account work. Assist and compact compatibility servers remain supported. Media completion requires verified artifacts, and availability depends on the account and upstream service. Live calls require Gemini Web account Cookies."
metadata:
  version: "0.21.0"
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
| Second opinion, search, Gem, learning mode, image or mixed-input understanding | function `gemini_ask` with optional `inputs`, `gem_id`, `learning_mode` | usable information incorporated into the task |
| Ask about an existing Notebook's sources | `gemini-web-cli notebooks list` or account `gemini_notebooks`, then function `gemini_ask(notebook_ids=...)` | original background chat handle yields a verified full-answer Artifact |
| Delegate a workflow needing Task execution, or explicitly request Task | function `gemini_run_task` | original Task handle yields the requested result; app changes require read-back |
| Deep Research & report derivatives | function `gemini_research`, then `report_action` on ready `operation_id` | same operation handle yields a ready report or local quiz/infographic/webpage/flashcards/audio_overview/custom_app |
| Continue a Chat | function `gemini_ask(start_session=true)` on the first turn, then pass `session_id` | requested response and session continuity |
| Generate or edit media | function `gemini_create_media` with `kind` (`wait_seconds` defaults: 120s image, 240s music, 300s video; `0` = immediate handle) | verified Artifact used in the user's task |
| Report derivatives | `gemini_research(operation_id=..., report_action=...)` or `function-full` report tools | correctly identified local artifact or manuscript |
| Explicit conversation, Notebooks, Gems, Skills, Schedules, Cleanup, or account work | `gemini-web-cli` (zero MCP context tax) or account `gemini_history`, `gemini_manage_chat`, `gemini_delete_chat`, `gemini_notebooks`, `gemini_manage_notebook`, `gemini_tasks` | requested data or verified change |

For ordered image references, use `gemini_create_media(kind="image", image_paths=[...], aspect_ratio=...)` (`16:9`, `9:16`, `1:1`, `4:3`, `3:4`). To edit a saved result, use `kind="edit_image"` with its `source_operation_id` and optional `source_artifact_id`. The verified source is attached first in a new chat. Keep each operation handle and inspect the resulting images. Paid `model="gemini-omni-1.2-flash"` / `GEMINI_API_KEY` is disabled; use Gemini Web (`hydrogen`/`flash`, `neon`/`pro`, or `carbon`). [Image workflow](references/workflows.md)

Web music/video default to the native browser workflow; `GEMINI_WEB_MEDIA_TRANSPORT=sdk` explicitly selects compatibility transport without affecting image routes. Verify the actual artifact: music may be an MP4 with audio and need not include MP3. Recover the original handle instead of resubmitting after a timeout; exact completed replies and verified saved files precede the existing canonical cleanup, while unknown identities remain protected.

Native video accepts one validated local `image_path` (only `duration_seconds=10` is supported when `image_path` is attached; `15` seconds is rejected fast as `INVALID_ARGUMENT`); require its uploaded preview to remain verified before Send. Missing, changed or unknown attachment evidence blocks submission, and upload observation alone does not prove reference-video generation. `gemini_create_media` defaults `wait_seconds` to `120` (image), `240` (music), or `300` (video); pass `wait_seconds=0` for an immediate handle. Legacy primary `gemini_generate_media(background=false)` and compact `create(background=false)` wait on one protected native operation. A timeout returns the original handle with partial/wait_elapsed; unknown or save-failed outcomes never authorize cleanup. Completed inline results keep local artifacts, explicit filenames and the `generated_media/` default, with opaque default names. After an awaited read, an account change refuses new reply metadata and artifacts; keep the original operation protected.

For music, use `gemini_create_media(kind="music", music_duration="short"|"standard", music_vocals="instrumental"|"vocals"|"auto", music_genre="auto"|"pop"|"hip_hop"|"rock"|"kpop"|"latin"|"electronic"|"rnb"|"country"|"afrobeats"|"reggae"|"jazz_blues"|"classical"|"folk"|"lofi"|"acoustic"|"cinematic"|"ambient", prompt=...)`. Leave unspecified options as `None` to preserve actual webpage defaults; never guess defaults from prompt style or menu order. All music entrypoints expose these optional controls, and explicit SDK transport rejects them before submission.

For workspace and account operations without adding MCP tools, use the companion JSON CLI `~/.local/bin/gemini-web-cli` (`doctor --validate-browser`, `models`, `quotas`, `notebooks`, `gems`, `skills`, `scheduled`, `tasks`, `history`, `chats`, `preferences`, `public-links`, `library`, `cleanup`).

## Choose Chat or Task

Default to **Chat** (`gemini_ask`). Choose **Task** (`gemini_run_task`) when the user explicitly asks for Task or delegates a workflow that needs Gemini's ongoing execution, work progress or website operation. Give the goal, completion conditions and expected deliverable.

| Need | Choose |
| --- | --- |
| Answer, search, write, analyze, compare Notebook sources; directly use an app supported in Chat | Chat, even for complex or multi-step requests |
| Have Gemini execute and track a delegated workflow, or operate websites with its browser | Task, if the required app/action is supported there |
| Use a Notebook, a native Gemini Skill, or a connected app | Either; these features alone do not select the mode |
| Run a long generation or recurring digest | Choose by the workflow; background execution and scheduling alone do not require Task |

Apps can differ by mode: native GitHub repository import/code questions are currently Chat-only; the Contacts connector is Task-only. Other GitHub routes are separate capabilities. Check availability for the requested action when needed, rather than assuming every app works in both modes.

App examples are not an allowlist. For a direct request such as “create a Keep note” or “find a file in Drive,” use `gemini_ask` with that natural-language instruction; a separate app-named MCP tool is unnecessary. Handle a returned connection/availability error if it occurs, and verify the requested change independently.

For recurring work, enable `account-full`. Use `gemini_create_scheduled_action` for recurring Chat content and the schedule actions of `gemini_tasks` for Task plans. A verified schedule requires its own read-back. [Schedule tools](references/tool_surface.md)

Settings that matter:

- **Model:** the Flash compatibility default prefers discovered Hydrogen. Select Neon or Carbon explicitly when the account offers them. Task supports the current account's Hydrogen/Neon/Carbon families according to its verified plan. Legacy Argon/Pro are preserved only when still discovered, never silently replaced. Lite remains a runtime-controlled exhausted-quota fallback for ordinary Chat.
- **Thinking:** use `thinking_level="low" | "medium" | "high"`. Legacy `standard` / `extended` still select low/high. Lite has no selectable thinking strength.
- **Sources and retention:** use returned `notebook_ids` when required. Temporary chats clean up automatically; set `retain_chat=true` only when the user wants retention. Associated Task schedules protect their thread.
- **Task permissions and browser.** Follow existing preferences unless the user asks to change them. In `account-full`, `gemini_preferences` manages saved `automatic` / `remote_only` and `always_ask` / `always_allow`. Select an action by its returned permission handle. Read-back verifies the stored preference. Native confirmations may still appear.

Load [workflows.md](references/workflows.md) only for detailed mode settings, app differences or model selection evidence.

## Default Server Choice

Configure `gemini-mcp-function` and `gemini-mcp-account` as separate MCP entries, each with six tools. Enable the function entry for generation and the account entry for existing conversation or resource management. State the goal and app naturally. Inspect observed source evidence and verify app changes independently. The five-tool assist compatibility server retains `gemini_search`.

The main server without `GEMINI_TOOLS` selects the function surface. Set `GEMINI_TOOLS=function-full` or `GEMINI_TOOLS=account-full` on a separate `gemini-mcp-server` entry when a workflow requires the previous 17-tool catalog. The full account cleanup tool acts only on MCP-owned ledger entries. Assist, compact and other legacy profiles remain available.

The Skill supplies routing instructions; an MCP server must also be connected. If no Gemini tools are available, load [recovery.md](references/recovery.md) for a credential-free connection check and client setup. Do not ask for Cookie values in chat or put them in command arguments.

## Standard Workflow

1. Identify the user's intended outcome and the next Gemini step.
2. Choose the capability and connected surface that can perform that step.
3. Call the narrowest current tool that can complete it.
4. Read the structured result before trusting compatibility prose.
5. Continue the user's actual task with the result or Artifact.
6. Use manifest or diagnostics only when discovery or recovery is needed.

Do **not** call the manifest before every known workflow. Use `gemini_get_tool_manifest` or `account(action="manifest")` when:

- the expected tool is unavailable;
- a schema or profile appears different;
- the user asks what is supported;
- upstream drift is suspected.

## Information Versus Artifacts

Search and understanding normally return information to the calling agent. The agent should synthesize it and continue working rather than dumping raw Gemini output.

Generation normally returns an Artifact. The agent should pass that file or URI to the next relevant tool:

- add the image to the document, website, slide, or app;
- use the edited image instead of merely reporting its path;
- attach the video or audio to the requested project;
- read and cite the research report.

A path, URI, or success sentence alone is not completion. Load [artifacts.md](references/artifacts.md) for acceptance and handoff rules.

## Long Operations

Media, Deep Research, Task and Notebook-backed Chat return a background handle. Plain `gemini_ask` returns its answer directly. Preserve `operation_id` for read-only status and result recovery. The result path can trigger owned conversation cleanup after verification. Workers verify the full answer, and Task also requires the recognized native file inventory and saved attachments to verify. Failed downloads or unknown shapes preserve protection. Saved results remain accessible after restart. Notebook sources are outside temporary-chat cleanup.

Compact clients use `session(action="operation_status" | "operation_result", session_id=operation_id)`, and assist clients use `gemini_research(action="status" | "result", operation_id=...)` for research. Do not repeat a generation merely because a call timed out. Cancellation is not available.

Load [operations.md](references/operations.md) for background recovery, `input_required`, App setup or an uncertain session reply. Only send an authorized response to an exact returned card; text replies cannot connect an App. Ordinary session recovery requires the original active server process, while operation handles support restart recovery. Load [tool_surface.md](references/tool_surface.md) for Task progress, schedules or explicit browser control. Chat scheduled actions and Task schedules use separate tools.

## Account Workflows

Only load or use account operations when the user explicitly asks to work with Gemini account data.

Start with list/search/read actions, identify the exact object, then mutate or delete it. A remote request being accepted is not proof that the target state changed; require positive read-back before claiming success.

Use `gemini_notebooks` to discover Notebook metadata. `gemini_manage_notebook` manages exact Notebooks and plaintext, web URL or UTF-8 `.txt` sources. Notebook deletion includes its conversations. Default `gemini_tasks` only reads metadata and progress. Load `account-full` for Task writes or schedules, native Skills, saved preferences or explicit browser input. Task deletion removes an exact thread.

For browser Cookie export, ensure the user has explicitly authorized access to their signed-in browser account for the task; authorization already given in the conversation still applies. Export can create sensitive account-authentication material in a local cache. Session reset changes only MCP/Gemini conversation state; it never changes agent memory or agent instructions.

Load [tool_surface.md](references/tool_surface.md) only when detailed account, privacy, destructive, or profile information is needed.

## Recovery

Load [recovery.md](references/recovery.md) when a tool is missing, authentication fails, an entitlement is unavailable, a long operation times out, an Artifact is incomplete, or Gemini Web behavior appears to have drifted.

Do not convert an unavailable entitlement, an ungrounded answer, a queued operation, or an accepted-but-unverified mutation into a success claim.
