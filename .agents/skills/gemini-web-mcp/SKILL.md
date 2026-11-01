---
name: gemini-web-mcp
description: "Use this skill when an agent should extend itself with Gemini Web: get a second opinion, search current web sources, understand images/files/URLs, run Deep Research, generate image/video/music artifacts, edit images, delegate ongoing workflows or operate websites through Gemini Task, or explicitly work with Gemini account data. Route by user intent instead of loading every tool. This skill is the compatibility router; prefer the focused gemini-assist skill when only assistance and understanding are needed. Do not use for repository implementation, tests, CI, packaging, or releases—use gemini-web-mcp-development instead."
license: MIT-0
compatibility: "Requires Python 3.11+ and a connected Gemini Web MCP server. The function server covers assistance, understanding, media, and Deep Research; the separate account server handles explicit account work. Assist and compact compatibility servers remain supported. Media completion requires verified artifacts, and availability depends on the account and upstream service. Live calls require Gemini Web account Cookies."
metadata:
  version: "0.2.1"
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
        description: Optional primary-server tool profile such as model, core, history, or account-read.
---

# Gemini Web MCP

Use this Skill to complete the user's task with Gemini, not to tour the Gemini tool surface.

The product priority is:

```text
1. Agent assistance and multimodal understanding
2. Generated artifacts
3. Explicit Gemini account management
```

Choose the next capability for the user's task; switch lanes when the task needs several steps. Do not expose account-management tools merely because they exist.

## Choose the Capability Lane

| User intent | Preferred route | What success means |
| --- | --- | --- |
| Second opinion, search, image or mixed-input understanding | function `gemini_ask`, `gemini_understand_image`, `gemini_understand` | usable information incorporated into the task |
| Ask about an existing Notebook's sources | account `gemini_notebooks`, then function `gemini_ask(notebook_ids=...)` | original background chat handle yields a verified full-answer Artifact |
| Delegate a workflow needing Task execution, or explicitly request Task | function `gemini_run_task` | original Task handle yields the requested result; app changes require read-back |
| Deep Research | function `gemini_research` | same operation handle yields a ready report |
| Chat sessions | function `gemini_start_chat` / `gemini_send_message` | requested response and session continuity |
| Generate or edit media | function `gemini_generate_image`, `gemini_edit_image`, `gemini_generate_video`, `gemini_generate_music` | verified Artifact used in the user's task |
| Report derivatives | function `gemini_list_research_report_actions`, `gemini_create_from_research_report` | correctly identified local artifact or manuscript |
| Explicit account work | account `gemini_history`, `gemini_notebooks`, `gemini_tasks`, `gemini_account_inventory` and scoped mutation tools | only the requested action, with read-back evidence |

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

For recurring Chat content, use account `gemini_create_scheduled_action`; an immediate `gemini_ask` alone does not establish a schedule. Task plans use account `gemini_tasks` schedule actions; putting “daily” in `gemini_run_task` alone does not establish a verified plan. Load [tool_surface.md](references/tool_surface.md) for their settings, and use each tool's actual schema rather than inventing scheduling arguments.

Settings that matter:

- **Model:** default Flash; prefer Argon for writing or unusually difficult reasoning. Task supports Flash/Argon. Lite is only a runtime-controlled exhausted-quota fallback for quick Chat text/search.
- **Thinking:** use `thinking_level="low" | "medium" | "high"`. Legacy `standard` / `extended` still select low/high. Lite has no selectable thinking strength.
- **Sources and retention:** use returned `notebook_ids` when required. Temporary chats clean up automatically; set `retain_chat=true` only when the user wants retention. Associated Task schedules protect their thread.
- **Task permissions and browser:** follow existing preferences unless the user asks to change them. Account `gemini_preferences` manages saved `automatic` / `remote_only` and `always_ask` / `always_allow`; select an action by its returned permission handle. Read-back verifies `stored_preference`, not runtime effects: `permission_preference` is saved, `effective_permission` remains unknown and native confirmations may still appear. Do not invent `browser_mode` or `auto_approve`.

Load [workflows.md](references/workflows.md) only for detailed mode settings, app differences or model selection evidence.

## Default Server Choice

Use `gemini-mcp-function` for the 17-tool function surface and `gemini-mcp-account` for the 17-tool explicit account surface. State the goal and app naturally; web and tool availability need no enable switch. Inspect observed source evidence, and verify app changes independently of answer prose. The five-tool assist compatibility server retains `gemini_search` and has no Notebook attachment arguments.

The main server without `GEMINI_TOOLS` also selects the function surface. Explicit `GEMINI_TOOLS` profiles and the older assist and compact servers remain compatibility routes. Cookie, Doctor and local Prompts do not belong to either business catalog. `gemini_cleanup` addresses only MCP-owned ledger entries, never title matches.

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

Function media, Deep Research, Task and Notebook-backed Chat return a background handle. Ordinary `gemini_ask` without Notebook IDs returns its answer directly. Preserve `operation_id` for status/result reads; do not regenerate after a timeout. A background handle does not imply Task mode. Workers verify the full answer before cleanup; Task also requires the recognized native file inventory and saved attachments to verify. Failed downloads or unknown shapes preserve protection. Saved results remain accessible after restart. Notebook sources are never temporary-chat cleanup targets.

Compact clients use `session(action="operation_status" | "operation_result", session_id=operation_id)`, and assist clients use `gemini_research(action="status" | "result", operation_id=...)` for research. Do not repeat a generation merely because a call timed out. Cancellation is not available.

Load [operations.md](references/operations.md) for background recovery, `input_required`, App setup or an uncertain session reply. Only send an authorized response to an exact returned card; text replies cannot connect an App. Ordinary session recovery requires the original active server process, while operation handles support restart recovery. Load [tool_surface.md](references/tool_surface.md) for Task progress, schedules or explicit browser control. Chat scheduled actions and Task schedules use separate tools.

## Account Workflows

Only load or use account operations when the user explicitly asks to work with Gemini account data.

Start with list/search/read actions, identify the exact object, then mutate or delete it. A remote request being accepted is not proof that the target state changed; require positive read-back before claiming success.

Account `gemini_notebooks` discovers Notebook metadata; `gemini_manage_notebook` manages exact native Notebooks and plaintext sources. Notebook deletion includes its conversations. `gemini_skills` manages native Gemini Skills, distinct from Gems and local agent Skills; instruction reads are explicit. `gemini_tasks` manages metadata, activity and schedules; `gemini_task_browser` supports verified frames and authorized input; `gemini_preferences` manages saved preferences. Task deletion is not cancellation; exclusive browser handoff, Task pause/resume and automatic confirmation remain unavailable.

For browser Cookie export, ensure the user has explicitly authorized access to their signed-in browser account for the task; authorization already given in the conversation still applies. Export can create sensitive account-authentication material in a local cache. Session reset changes only MCP/Gemini conversation state; it never changes agent memory or agent instructions.

Load [tool_surface.md](references/tool_surface.md) only when detailed account, privacy, destructive, or profile information is needed.

## Recovery

Load [recovery.md](references/recovery.md) when a tool is missing, authentication fails, an entitlement is unavailable, a long operation times out, an Artifact is incomplete, or Gemini Web behavior appears to have drifted.

Do not convert an unavailable entitlement, an ungrounded answer, a queued operation, or an accepted-but-unverified mutation into a success claim.
