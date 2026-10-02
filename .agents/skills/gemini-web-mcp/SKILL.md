---
name: gemini-web-mcp
description: "Use this skill when an agent should extend itself with Gemini Web: get a second opinion, search current web sources, understand images/files/URLs, run Deep Research, generate image/video/music artifacts, edit images, or explicitly work with Gemini account data. This is the compatibility router: prefer gemini-assist for assistance/understanding, gemini-create for creation, and gemini-account for explicit account work. Do not use for repository implementation, tests, CI, packaging, or releases—use gemini-web-mcp-development instead."
license: MIT-0
compatibility: "Requires Python 3.11+ and a connected Gemini Web MCP server. The focused assist server handles assistance, files, URLs, and Deep Research; focused create/account servers cover media creation, durable recovery and explicit account work; compatibility servers retain their existing tools. Live calls require Gemini Web account Cookies."
metadata:
  version: "0.2.2"
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

| User intent | Preferred current route | What success means |
| --- | --- | --- |
| Second opinion, critique, code/design review | focused `gemini_ask`; fallback compact `chat` or primary `gemini_chat` | useful Gemini result incorporated into the agent's work |
| Quick current-web lookup | focused `gemini_search`; fallback chat with an explicit sourcing request | `grounded` only with structured source evidence; verify any links in answer text before citing them |
| Understand one image or screenshot | focused `gemini_understand_image`; fallback compact or primary image chat | analysis returned to the agent and used in the surrounding task |
| Understand files, URLs, or mixed evidence | focused `gemini_understand`; fallback primary file/URL/image tools | each source identity and outcome preserved before synthesis |
| Deep, multi-source research | dedicated `gemini_research`; primary `gemini_deep_research(wait_for_completion=false, retain_chat=true)` | preserve the upstream chat ID for later report retrieval; the dedicated tool returns an opaque restart-safe handle with status/result/cancel actions |
| Generate or edit images | focused `gemini_generate_image` / `gemini_edit_image`; compact `create` / `edit` and primary media tools remain compatible | a decoded local image Artifact; an empty reply or a search image is insufficient |
| Generate music | focused `gemini_generate_music`; compact `create(type="music")` or primary media tools remain compatible | use a verified audio/video Artifact; do not infer the exact Lyria version from a model alias |
| Generate video | focused `gemini_generate_video` with native mode selection | recover the same operation and use a verified video file; prose or image output is insufficient |
| History, Notebook, Scheduled, Gem, Prompt, usage, or cleanup | focused `gemini-account` seven facades; compact or narrow primary profiles remain compatible | only the explicitly requested account operation is performed |

Load [workflows.md](references/workflows.md) for detailed task routes.

## Default Server Choice

Use `gemini-mcp-assist` for assistance and understanding, `gemini-mcp-create` for media creation, and `gemini-mcp-account` for explicit account work. Use `gemini-mcp-skill-server` when a compact compatibility workflow is needed.

Use the primary server only for a narrow profile:

- `GEMINI_TOOLS=model` for text/session work;
- `GEMINI_TOOLS=core` for files, URLs, media, and Deep Research;
- `GEMINI_TOOLS=history` or `history-organize` for explicit history work;
- `GEMINI_TOOLS=account-read` for explicit account inventory;
- `GEMINI_TOOLS=scheduled-admin` only for requested scheduled mutations;
- `GEMINI_TOOLS=manage:gems` only for requested Gem operations;
- `GEMINI_TOOLS=history,manage:history-write` only for requested history deletion or cleanup after identifying the target.

Do not use `GEMINI_TOOLS=all` as a general-agent default.

The three focused products are installed: `gemini-assist` (five tools), `gemini-create` (seven tools) and `gemini-account` (seven action facades), each with its own Runtime Skill and console entrypoint. This Skill remains their compatibility router.

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

Creation tools select Gemini Web's native image/video/music feature mode. Primary and
compact generation save and verify outputs; compact uses `generated_media/` in
the server's working directory. The Web backend still chooses the effective
media model. Re-verify actual artifacts after upstream changes; mode selection alone is not generation evidence.

For a newly created source chat, finished operations with verified local outputs
and definitive empty responses use immediate, bounded cleanup by default. Inspect
`domain_result.meta.details.cleanup`: only `completed`/`already_completed` with
positive absence read-back confirms removal. `pending`, `failed`, or `cancelled`
requires follow-up. Failed recovery reads, remote-only, queued, timed-out, and partially saved results
keep their source chat for recovery. Primary callers can explicitly choose
`retain_chat=true` or `delete_after_seconds`; registered delayed cleanup jobs
survive restart in authentication-scoped SQLite metadata. Pending cleanup
authority can be renewed until resolved, independently of operation expiry.
Preserve the local file before deleting a disposable test's source chat. Use
its recorded ID, with the user's authorization, instead of broad account scans.

## Long Operations

Deep Research, video, and music can take a long time. Start Deep Research without waiting for completion. Focused Research, video and music start asynchronously and return opaque operation handles. Use focused creation for the implemented native video/music routes. Native mode selection is routing evidence; a live request succeeds only when actual matching artifacts are returned and verified. Compatibility music/video calls may wait for the upstream response or return a queued state and do not provide durable operation handles.

Preserve every returned `operation_id`, `upstream_operation_id`, `upstream_chat_id`, and Artifact identity. Do not start a duplicate operation merely because one MCP call timed out.

Focused Research exposes action=status/result/cancel on its opaque, restart-safe handle. Focused creation exposes gemini_get_operation_status/result and gemini_cancel_operation. Shared local SQLite metadata lasts seven days and stores no prompt, chat/report, Cookie or raw-response content. Recover known locators and never duplicate a timed-out start. A local_cancelled_before_start verification proves cancellation before provider submission; submitted work remains cancel_requested unless provider cancellation is confirmed.

Load [operations.md](references/operations.md) before running or recovering a long operation.

## Account Workflows

Only load or use account operations when the user explicitly asks to work with Gemini account data.

Start with list/search/read actions, identify the exact object, then mutate or delete it. A remote request being accepted is not proof that the target state changed; require positive read-back before claiming success.

For browser Cookie export, ensure the user has explicitly authorized access to their signed-in browser account for the task; authorization already given in the conversation still applies. Export can create sensitive account-authentication material in a local cache. Session reset changes only MCP/Gemini conversation state; it never changes agent memory or agent instructions.

Load [tool_surface.md](references/tool_surface.md) only when detailed account, privacy, destructive, or profile information is needed.

## Recovery

Load [recovery.md](references/recovery.md) when a tool is missing, authentication fails, an entitlement is unavailable, a long operation times out, an Artifact is incomplete, or Gemini Web behavior appears to have drifted.

Do not convert an unavailable entitlement, an ungrounded answer, a queued operation, or an accepted-but-unverified mutation into a success claim.
