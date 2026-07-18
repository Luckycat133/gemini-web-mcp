# Gemini Web Live UI Coverage

This page records the native Gemini Web surface observed in a signed-in Pro
account session on 2026-06-18 and maps it to the primary MCP server in
`src.server`. Browser extension UI injected into Gemini was ignored during this
pass.

## Observed Native UI

The chat surface exposed:

- New chat, chat search, Library, and Gems in the side navigation.
- Temporary chat, account switch/sign-out link, and settings controls.
- Prompt upload/tools menu, model picker, microphone, and send controls.
- Visible model picker entries: `3.1 Flash-Lite`, `3.5 Flash`, `3.1 Pro`,
  plus a separate thinking-level submenu with `标准` and `扩展`.

The upload/tools menu exposed:

- Upload file.
- Add from Google Drive.
- Import code.
- Create image.
- Create video.
- Canvas.
- Deep Research.
- Create music.
- Guided Learning (`学习辅导`).
- Personalization toggle under a `Labs` section.

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
| Observed Web Pro capability manifest | Covered | `gemini_get_web_capabilities` returns observed models, thinking levels, menu entries, and MCP coverage |
| Account feature/RPC status | Covered | `gemini_inspect_account` summarizes account probes without raw previews |
| Chat history listing | Covered | `gemini_history(action="list")` is the recommended facade; `gemini_list_chats` remains available in `all/manage` and supports pagination/JSON |
| Chat history deep source scan | Covered | `gemini_history(action="scan")` is the recommended facade; the granular scan merges observed `MaZiqc` history filters, native notebook chat lists, and `GS7W1` Remy goal conversation references without reading turn text |
| Chat history search | Covered | `gemini_history(action="search")` searches titles/IDs by default and only scans turn text when `scan_turns=true` |
| Chat history reading | Covered | `gemini_history(action="read")` reads a specific chat by ID |
| Chat history export | Covered | `gemini_history(action="export")` exports one selected chat as Markdown or JSON |
| Chat deletion | Covered | `gemini_delete_chat` maps to the installed client's `delete_chat` |
| Native Gemini Notebooks | Covered in part | `gemini_notebooks(action="list|chats")` is the recommended read-only facade; granular tools remain available in `all/manage`, and `gemini_move_chat_to_notebook` moves existing chats with verification; notebook create/delete/source mutation remains disabled except for observed source helpers |
| Library capability/templates | Covered in part | `gemini_list_library_capabilities` parses observed `cYRIkd` capability entries |
| Library assets | Probe covered | `gemini_probe_web_features(surface="library")` checks observed `sJBwce` and `VxUbXb`; no stable asset list wrapper yet |
| Add from Google Drive | UI observed | Drive picker is visible in the tools menu and opens Google Picker; no Drive attachment RPC wrapper yet |
| Canvas | Probe covered | Listed in tools menu and Library capabilities; `gemini_get_tool_mode_status` reads observed mode-status rows, but no Canvas document RPC wrapper yet |
| Guided Learning | Covered in part | Chat tools accept `learning_mode` for observed Input Companion modes; `gemini_get_tool_mode_status` remains the read-only mode-status probe |
| Thinking level submenu | Covered | Generation requests accept `thinking_level=standard/extended` |
| Personalization and settings | Probe covered in part | `gemini_probe_web_features(surface="personalization")` checks observed settings RPC reachability; no settings CRUD wrapper yet |
| Usage limits | Covered in part | `gemini_get_usage_limits` parses observed quota/model-state structures |
| Public links | Covered in part | `gemini_list_public_links` lists observed public-link entries; no create/delete/update wrapper yet |
| Scheduled actions | Covered in part | `gemini_list_scheduled_actions` reads the observed registry; `gemini_get_scheduled_action` reads known IDs and task state; `gemini_create_scheduled_action` creates daily actions; `gemini_delete_scheduled_action` sends delete by id and verifies `task_state=deleted` when available; edit/toggle still disabled |
| Memory import | Probe covered in part | `gemini_probe_web_features(surface="import")` checks observed import-entry RPC reachability; no import mutation wrapper yet |

## Observed RPC Evidence

The 2026-06-18 browser pass captured these read-only/probe-style RPC ids from
native Gemini Web navigation. `gemini_probe_web_features` uses these ids without
returning raw response bodies:

| Surface | RPC ids |
|---|---|
| Library | `sJBwce`, `VxUbXb`, `cYRIkd` |
| Public links / sharing | `K4WWud`, `GPRiHf`, `maGuAc`, `Te6DCf` |
| Usage limits | `qpEbW` |
| Personalization settings | `GPRiHf`, `maGuAc`, `Te6DCf` |
| Memory import | `Te6DCf` |
| Native notebooks | `CNgdBe` |
| Chat history / Remy references | `MaZiqc`, `GS7W1` |
| Scheduled actions | `otAQ7b`, `XPSWpd`, `MaZiqc`, `kwDCne`, `Jba3ib`, `Q4Gw3c` |
| Tool/mode status | `MyzX6c` |

The 2026-06-18 chat-page pass also observed the model picker and settings menu
without additional raw response capture. The upload/tools button triggered
`ESY5D` and `L5adhe` from `/app`; `L5adhe` carried a large client-state shaped
payload, so it is documented as UI evidence rather than exposed as a general
MCP wrapper.

The 2026-06-18 scheduled-actions pass navigated to `/scheduled` and observed
`otAQ7b` with payload `[]` plus two `MaZiqc` history/related-chat pagination
variants: `[13,null,[1,null,1]]` and `[13,null,[0,null,1]]`.

The 2026-06-19 scheduled-actions pass created and deleted temporary marked
tasks, confirming `XPSWpd` for the current scheduled-task registry, `Jba3ib`
for daily create, and `Q4Gw3c` for explicit delete by id. The 2026-06-20
scheduled module inspection also confirmed `kwDCne` as `/BardFrontendService.GetTask`.
The MCP wrapper exposes only those confirmed mutations and reports
`visible_in_registry` plus by-id readability after create. If a local
cookie/session creates an ID but cannot see it in `XPSWpd`, the tool returns a
diagnostic instead of silently treating the task as registry-verified; edit,
toggle, weekly and other recurrence variants stay disabled until their RPC
contracts are captured and verified.

The 2026-06-19 cookie-context follow-up found that gemini_webapi's default
cookie cache can select a stale-but-authenticated session before freshly loaded
Chrome cookies. Browser-cookie refresh now uses a dedicated cache path and
Chrome profile validation probes `XPSWpd`, preferring a profile whose scheduled
registry is visible.

The 2026-06-20 cookie-context pass added explicit profile diagnostics. On this
machine, Chrome's selected profile is `Default` and has no Gemini PSID, while
`Profile 1` has a valid Gemini account cookie but still returns
`scheduled_registry_count=0`. A controlled create/delete smoke test returned a
new scheduled-action id and accepted the delete RPC; no scheduled registry
entries were visible afterward. This is surfaced as an account/profile-context
diagnostic rather than treated as a verified visible list state.

A follow-up 2026-06-20 smoke test verified that a newly created scheduled-action
ID was immediately readable through `kwDCne`; after `Q4Gw3c` delete, the same
ID remained readable as a tombstone with the frontend `Rg=6` / `Deleted` state.
The delete wrapper therefore reports `verification_status=deleted_state_by_id`
and `deleted_by_id_after_delete=true` when by-id readability proves a deleted
state rather than an active residual task.

The final 2026-06-20 MCP E2E smoke test created
`codex-final-scheduled-e2e-*`, verified the returned task by id as
`task_state=created`, deleted it, then verified the same id as
`task_state=deleted` with `verification_status=deleted_state_by_id`. The
scheduled registry remained empty before creation, after creation, and after
deletion for the current cookie/profile context.

The final 2026-06-20 chat-history E2E smoke test created a temporary marked
Gemini chat, found it by scanning recent turn text, deleted the returned chat
ID, then searched the marker again. The post-delete search returned
`match_count=0` across the scanned recent chats, so the temporary verification
chat was cleaned up.

The 2026-06-19 chat-page pass toggled Canvas and Guided Learning without
sending a prompt. Both surfaces showed visible chips/placeholders and triggered
`MyzX6c` with payload `[]`, which returns Web-internal mode status rows.

Bundle inspection of the same Web build found the Guided Learning Input
Companion path: selected options are copied into `GOa.H4`, translated into
`X9b`, and sent through StreamGenerate. The MCP chat tools expose the confirmed
learning entries as `learning_mode=interactive_quiz`, `flashcards`,
`practice_test`, and `study_guide`. Canvas still remains probe/UI-only because a
stable document creation or mutation RPC was not captured.

The same pass opened the Google Drive picker entry without selecting a file.
Gemini loaded `apis.google.com/js/api.js` and embedded
`docs.google.com/picker/v2/home`; the picker document returned HTTP 401 in this
session. No stable Gemini attachment RPC was observed, so Drive selection stays
UI-only until a confirmed file-selection flow is captured with explicit user
approval.

## Model Contract

Chat, file, and media generation tools accept the current Web aliases
`flash-lite`, `flash`, and `pro`; compatibility aliases such as `fast` and
`thinking` remain accepted. They also accept a runtime model/display name from
the installed `gemini-webapi` model registry. The Web UI thinking selector is
separate from the model picker and maps to `thinking_level=standard` or
`thinking_level=extended`.

Guided Learning is separate from both the model picker and thinking selector.
Use `learning_mode` only when the desired output is a learning artifact or
guided study flow; leave it unset for ordinary chat.

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
