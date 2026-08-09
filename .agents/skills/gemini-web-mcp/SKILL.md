---
name: gemini-web-mcp
description: "Operate an installed Gemini Web MCP server safely: inspect the tool manifest, choose the narrowest profile and read-only workflow, manage explicitly selected history/notebook/account tasks, and verify generated media artifacts. Use for MCP tool operation; do not use for repository implementation, tests, CI, packaging, or releases—use gemini-web-mcp-development instead."
license: MIT-0
compatibility: "Requires Python 3.11+ and an installed server (verify with uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding). Live Gemini calls require account Cookies; image verification requires the image or all extra."
metadata:
  version: "0.1.1"
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
        description: Optional primary-server tool profile such as model, core, or all.
---

# Gemini Web MCP

Use this skill only to operate the installed primary or low-token MCP server. For source changes, tests, CI, packaging, compatibility probes, or releases, stop and use the separate `gemini-web-mcp-development` skill.

## Start Here

1. Before configuring an account, verify the installed server with `uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding`; this calls a real auth-free text tool and strips Gemini Cookie variables from its child process.
2. Prefer `gemini_get_tool_manifest` before choosing primary-server tools. It is always exposed by `src.server`, including the narrow `model` profile.
3. Check manifest `current_enabled`, `groups`, and `workflows`; do not hard-code tool counts because the static manifest can include groups not loaded in the current process.
4. On the low-token server, prefer auth-free `account(action="manifest")` and `account(action="capabilities")` before account calls that initialize Gemini.
5. Prefer read-only discovery first: `gemini_doctor`, manifest/capabilities, `gemini_probe_web_features`, metadata-only history search, profile diagnostics, and inventory/list tools.
6. Treat `privacy=reads_private_chat_text` and other private text tools as explicit-user-intent tools: `gemini_read_chat`, `gemini_export_chat`, `gemini_search_chats(scan_turns=true)`, and research-report create actions that read chat text.
7. Treat destructive tools as requiring explicit user intent: `gemini_delete_chat`, `gemini_cleanup_test_artifacts(dry_run=false)`, `gemini_delete_scheduled_action`, `gemini_reset_session`, `gemini_manage_gems(action="delete")`, and prompt deletion.
8. `gemini_reset_session` changes only MCP/Gemini conversation state; it never changes agent memory or agent instructions.

> Need the full tool/group/privacy/destructive map? See [references/tool_surface.md](references/tool_surface.md). Load it on demand — the live `gemini_get_tool_manifest` remains the source of truth.

## Tool Surfaces

- Primary MCP server: `src.server`
  - Use `GEMINI_TOOLS=model` or `chat` when an agent only needs to call Gemini models.
  - Use `GEMINI_TOOLS=history` when an agent only needs `gemini_history` for list/scan/search/read/export chat history.
  - Use `GEMINI_TOOLS=history-organize` when an agent needs `gemini_history`, `gemini_notebooks`, and explicit chat-to-Notebook moves.
  - Use `GEMINI_TOOLS=account-read` when an agent only needs `gemini_account_inventory` for read-only Web surface inventory.
  - Use `GEMINI_TOOLS=scheduled-admin` only for explicitly authorized scheduled-action create/delete workflows.
  - Default `GEMINI_TOOLS=core` remains the broad content workflow: chat, media, files, and research.
  - `GEMINI_TOOLS=all` is the full maintenance/verification surface, not a good default for general agents.
  - `GEMINI_TOOLS=prompts` adds local prompt management plus always-on manifest/cookie helpers.
- Low-token skill server: `src.skill_server`
  - Use `account(action="manifest")` for compact tool guidance.
  - Use `account(action="capabilities")` for the static Web capability map without cookies.
  - Use `account(action="features|links|usage|library|notebooks|scheduled|modes")` for compact account-surface inventory.
  - Use `history(action="list|search|read|export|delete")` for chat history.
  - Record every returned remote test-resource ID; Gemini-generated titles may omit prompt markers.
  - Use `cleanup(dry_run=true)` as a bounded fallback before deleting test chats or scheduled actions by marker.
  - Use `scheduled(action="list|get|create|delete")` for compact scheduled-action workflows.
  - Use `create(type="music", model="pro")` or primary `gemini_generate_music` for Lyria 3 Pro music requests.
  - Use `doctor(validate_browser=false)` for low-cost local preflight before live account workflows.
  - Use `cookie(action="profiles")` before `cookie(action="get", profile="...")` when Chrome has multiple signed-in profiles.
  - `cookie(action="get")` and primary `gemini_get_cookie_from_browser` can materialize sensitive account-authentication material in a local cache. Obtain explicit user approval, restrict file access, never log/back up/share the cache, and remove it when it is no longer needed.

## Stream And Long-Operation Results

- Treat `gemini_chat_stream` and `gemini_send_message_stream` as compatibility names for Gemini upstream streaming. The current MCP tools normalize and collect all chunks, then return one result; they do not provide MCP incremental delivery.
- Read `_meta.domain_result.data.stream`: `delivery="collected"`, while `chunk_semantics` reports `delta`, `cumulative`, `mixed`, or `empty`. Do not concatenate the returned text again.
- For `gemini_deep_research`, read `_meta.domain_result.meta.operation_state` and `data.state`; distinguish `queued`, `running`, `completed`, and `timed_out` rather than inferring completion from prose.
- Use `wait_for_completion=false` when the user wants plan/start only. Prefer `retain_chat=true` for later retrieval, and preserve `upstream_chat_id` / `upstream_operation_id` whenever `continuation_possible=true`.
- A `timed_out` result means this MCP wait ended, not necessarily that Gemini stopped the upstream research. Only claim a report exists when `report_available=true` or a later report-read call verifies it.

## Chat History Workflow

1. Deep-scan metadata sources when completeness matters:
   - `gemini_history(action="scan", limit=..., offset=..., response_format="json")`
2. List or search metadata first:
   - `gemini_history(action="list", limit=..., offset=..., response_format="json")`
   - `gemini_history(action="search", query=..., scan_turns=false, response_format="json")`
3. Only scan turn text when the user asks for content search:
   - `gemini_history(action="search", query=..., scan_turns=true, turns_per_chat=..., max_chars_per_turn=...)`
4. Read/export one selected chat only after the user has indicated the target:
   - `gemini_history(action="read", chat_id=...)`
   - `gemini_history(action="export", chat_id=..., response_format="markdown"|"json")`
5. Move chats to native notebooks only after identifying both target chat and notebook:
   - `gemini_notebooks(action="list", ...)`
   - `gemini_move_chat_to_notebook(chat_id=..., notebook_id=...)`
   - `gemini_notebooks(action="chats", notebook_id=...)`
6. Delete only with explicit confirmation:
   - `gemini_delete_chat(chat_id=...)`
   - Claim deletion only when `_meta.domain_result.data.deleted=true` and
     `verification.status=verified_absent`; this requires a complete fresh history-metadata read-back.
     `not_available` means accepted but unverified, and `read_chat(None)` alone is never absence proof.
   - For test chats, retain the returned remote ID at creation time. A metadata-only marker search may miss the chat when
     Gemini generates a title without the prompt marker; use `scan_turns=true` only with explicit permission to read turn text.

## Web Pro Coverage Rules

- `gemini_get_web_capabilities` is the static observed Pro Web surface map.
- `gemini_probe_web_features` checks observed read-only RPC reachability and must not expose raw private RPC bodies.
- Use `gemini_account_inventory(surface=...)` or the manifest `web_surface_inventory` workflow for read-only account inventory: public links, usage limits, native notebooks, library capabilities, scheduled actions, and tool mode status.
- Treat `gemini_list_library_capabilities` as localized template/capability discovery, not private Library asset export.
- Treat `gemini_get_tool_mode_status` as a read-only Canvas/Guided Learning mode-status probe; Canvas document mutation remains disabled.
- Guided Learning is exposed through chat `learning_mode`; prefer this over UI assumptions.
- Keep Drive picker, Canvas mutation, settings mutation, memory import mutation, public-link mutation, and unsupported scheduled-action recurrence/edit/toggle variants disabled until stable RPC contracts and explicit user authorization exist.

## Media Workflow

- For music/video/image generation requests, use the MCP tool path and finish only when the tool reports saved local media files or an explicit export failure.
- Keep `requested_model`, `request_model`, `effective_backend`, and `observed_backend` separate. An expected/effective label is not live backend evidence.
- For a local image claim, verify that the path is inside the requested output directory, the file exists and is non-empty, MIME is `image/*`, dimensions are positive, and structured verification is `verified`; response prose or a remote URI alone is insufficient.
- For Lyria 3 Pro/fullsong claims, verify raw backend markers and saved media duration; do not trust wrapper labels, model names, or chat prose alone.
- `gemini_generate_music` can recover media from raw chat payloads even when `response.media` is empty; inspect returned file paths and duration metadata before summarizing success.

## Scheduled Actions

- Use observed daily create, registry list, by-id get, and explicit delete by id through `gemini_create_scheduled_action`, `gemini_list_scheduled_actions`, `gemini_get_scheduled_action`, and `gemini_delete_scheduled_action`.
- Refresh Chrome cookies first when account context matters. If the registry is unexpectedly empty, call `gemini_list_browser_cookie_profiles`, then `gemini_get_cookie_from_browser(profile="...")` for the profile with Gemini cookies or scheduled registry entries.
- On macOS, treat `BROWSER_COOKIE_ACCESS_TIMEOUT` as a local browser-credential authorization timeout, not an invalid-account result. This workflow does not read arbitrary credential files. Adjust `GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS` only when the user controls that host; never request or print Cookie values.
- After create/delete, check `verification_status`; after create also check `readable_by_id_after_create`, and after delete check `deleted_by_id_after_delete` or `task_state_after_delete=deleted` before claiming the task is gone.

## Operational Verification

- Run the credential-free onboarding command before the first live call and retain its JSON protocol/profile evidence.
- For a live text check, use `gemini-mcp-onboarding chat --allow-live-account --prompt ...`; for an image deliverable, use the `image` subcommand with the image extra and a dedicated output directory.
- State explicitly whether backend behavior was observed live or only expected from routing metadata.
- Use `evaluations/gemini_web_mcp_contract.xml` only as a repository contract reference; changing it or any implementation file is development work and belongs to `gemini-web-mcp-development`.
