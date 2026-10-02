# Gemini Web Live UI Coverage

This page records the native Gemini Web surface observed in a signed-in Pro
account session on 2026-06-18 and maps it to the primary MCP server in
`src.server`. Browser extension UI injected into Gemini was ignored during this
pass.

## 2026-09-26 Chrome and MCP Recheck

A signed-in Chrome session showed `3.5 Flash-Lite`, `3.8 Flash`, and `3.1 Pro`
in the model picker. The upload/tools menu exposed Create image, Create video,
and Create music. The dedicated `/videos` page displayed Gemini Omni with a
16:9 option and produced a downloadable 10-second MP4 in this account.

The same account's MCP calls produced verified local images with Flash and
Flash-Lite, and verified MP3 plus MP4 music files with Flash. The media response
did not identify the exact image or music backend (`observed_backend=null`).
Compact `edit` returned a remote image URI; primary image generation with an
input image saved a local 2048×2048 edited image, visually retaining the cat while
changing the mug from blue to green.
The image saver initially wrote JPEG bytes under a caller-supplied `.png` name.
After the MIME/extension fix, a fresh live call saved `mime-check.jpg` with
`image/jpeg` metadata, and the file signature confirmed JPEG.
The generic MCP video call completed with `ARTIFACT_NOT_RETURNED`: its retained
chat contained only text saying that this chat could not render video. This is
evidence of a missing MCP mode route, not an account entitlement failure.

A later authenticated Chrome/MCP music regression call on the same day returned
`ARTIFACT_NOT_RETURNED`. The newly created test chat displayed Gemini's own
technical-error message for music generation and no media Artifact. The earlier
verified MP3/MP4 result remains valid evidence of a successful run; this later
result shows the upstream music path was not consistently available.
The subsequent image regression succeeded: a 2.7 MB JPEG was saved as
`image-check_2.jpg` without replacing an existing file and was visually checked
against the prompt (orange cat, blue cup, white background).

Current Google help distinguishes Nano Banana 2 Lite for Flash-Lite images and
Nano Banana 2 for Flash/Pro images. Google documents Gemini Omni for video and
announced Lyria 3.5 for music. These are current Web product descriptions;
they are not backend identifiers observed in the MCP response. See the
[image](https://support.google.com/gemini/answer/14286560),
[video](https://support.google.com/gemini/answer/16126339), and
[music](https://blog.google/innovation-and-ai/products/gemini-app/better-tracks-lyria-gemini/)
sources.

## 2026-10-02 Native Media Recheck

Signed-in Chrome showed `3.5 Flash-Lite`, `3.8 Flash` and `3.1 Pro`. Selecting
Create image changed the composer to the image feature with styles and aspect
ratio controls; Create video was also visible in the tools menu. Other account
menus were not revalidated in this pass.

Public frontend source establishes image mode 14 and music mode 21 at the
StreamGenerate inner array's index 49. MCP now selects these modes explicitly
and shares local saving, verification and source-chat cleanup across primary
and compact creation. See [the source chain and policy](native-media-mode.md).
After the user enabled macOS browser-data access, a fresh current-code stdio
session negotiated MCP `2026-07-28` with SDK `2.2.0` and `gemini-webapi 2.0.0`.
Primary image generation and local-image editing each saved a 2816×1536 JPEG.
Both files passed independent full decoding, size/MIME/dimension checks and
visual inspection. Image source cleanup initially returned `pending`.
Subsequent targeted MCP deletion positively verified both owned source chats
as `verified_absent`. Both saved files still decoded with unchanged SHA256
hashes after deletion.

The native music request raised the upstream SDK's `APIError` with a message
reporting a possible upstream interruption. No music artifact or source-chat ID was returned; the original
MCP response classified it as `INTERNAL_ERROR`. This is a failed live music
attempt, not a successful creation or proof of a quota/entitlement cause.

After adding safe API-error classification, isolated request-owned metadata
and supported thinking-argument synchronization, a separate fresh MCP session
used SDK `2.1.1`. One native music request returned a verified MP3
(`audio/mpeg`, 2,947,046 bytes, 122.540333 seconds) and companion MP4
(`video/mp4`, 9,532,928 bytes, 1024×1024, 122.54 seconds). Independent FFmpeg
full-stream decoding passed for both files. Initial cleanup was `pending`;
targeted follow-up returned `ok=true`, `deleted=true` and
`verification.status=verified_absent`. Both files still fully decoded with
unchanged SHA256 hashes after deletion. The MP4 is music's companion output,
not evidence that the generic video-generation route works.

The updated compact MCP (`create` then `edit`) was also exercised with SDK
`2.1.1`, using its default `generated_media/` destination. Both outputs were
decoded 2816×1536 JPEGs; visual inspection confirmed the blue fox and the added
gold star while preserving the subject and background. Exact-ID follow-up
returned positive deletion proof for each source chat, and the saved files
remained usable. The installable package now requires `gemini-webapi>=2.1.1,<3`
to include the current model, stream-recovery and music-parser paths.

The original failed request still has no observed ID or artifact; the later
successful request does not establish its cause or final account state. No
generation retry was used to recover that unknown request. The 2026-09-26
observations remain separate. These bounded user-authorized acceptance runs
are not the dedicated-account full canary or a guarantee of repeatability.

Targeted account cleanup additionally verified absence for seven historical
test chats whose ownership was established by exact recorded IDs and test
markers. Together with four new image sources and one new music source,
twelve deletions had
positive read-back evidence. Three historical candidates were retained because
their test ownership could not be established. The failed music request had no
observed ID and was excluded from deletion; no unrelated account scan was used
to guess its chat.

## Observed Native UI (2026-06-18)

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
| Create image/music | Covered in part | Native image generation/editing and one SDK 2.1.1 music request verified on 2026-10-02; an earlier music request failed. Cleanup has separate read-back evidence, and account/UI gates still apply |
| Create video | Web UI only | Dedicated Gemini Omni mode works in Chrome; generic MCP chat route did not return a video Artifact |
| Deep Research | Covered | Full workflow when the installed client exposes research helpers |
| Dynamic model discovery | Covered | `gemini_list_models` reports the account model registry after init |
| Observed Web Pro capability manifest | Covered | `gemini_get_web_capabilities` returns observed models, thinking levels, menu entries, and MCP coverage |
| Account feature/RPC status | Covered | `gemini_inspect_account` summarizes account probes without raw previews |
| Chat history listing | Covered | `gemini_history(action="list")` is the recommended facade; `gemini_list_chats` remains available in `all/manage` and supports pagination/JSON |
| Chat history deep source scan | Covered | `gemini_history(action="scan")` is the recommended facade; the granular scan merges observed `MaZiqc` history filters, native notebook chat lists, and `GS7W1` Remy goal conversation references without reading turn text |
| Chat history search | Covered | `gemini_history(action="search")` searches titles/IDs by default and only scans turn text when `scan_turns=true` |
| Chat history reading | Covered | `gemini_history(action="read")` reads a specific chat by ID |
| Chat history export | Covered | `gemini_history(action="export")` exports one selected chat as Markdown or JSON |
| Chat deletion | Covered with explicit evidence state | `gemini_delete_chat` requests deletion, then reports `verified_absent`, `not_available`, `still_present`, or `read_back_error`; only positive absence is verified deletion |
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

## 2026-08-08 Targeted Live MCP Evidence

An explicitly authorized local run at commit
`6811a7934d836b54c4d54d184caed321b356ecef` used Python 3.12,
`gemini-mcp-server==0.2.0`, `gemini-webapi==2.0.0`, `mcp==2.0.0`,
`mcp-types==2.0.0`, and MCP protocol `2026-07-28`. It verified environment
Cookie diagnostics, both registered history RPC probes, primary temporary and
retained text, primary multi-turn context/reset, compact text, and typed
primary/compact history list/search results.

The run retained every returned remote chat ID and deleted all four created
chats. Each deletion completed a fresh recent/pinned history pagination and
returned `verification.status=verified_absent`; a final metadata-only marker
search returned zero matches. `scan_turns` stayed false, no pre-existing chat
turn was read, no non-chat remote resource was created, the temporary workspace
was removed, and no Cookie value appeared in captured server logs.

Gemini-generated titles did not retain the unique prompt marker for the
retained primary or compact chat, so metadata-only marker search did not find
those chats before deletion. Test workflows must therefore record remote IDs
at creation time and use marker cleanup only as a bounded fallback. Enabling
`scan_turns=true` can find prompt markers but reads private turn content and
still requires explicit user intent.

This was a bounded authorized observation, not the repository's
dedicated-account full canary. It did not record account tier, locale, Web
build, media, file/URL analysis, Deep Research, scheduled actions, Gems, or
Notebook mutations.

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
In the 2026-09-26 run, the runtime registry exposed generic `Flash-Lite`,
`Flash`, and `Pro` names without the full UI version labels. Check both the
runtime request names and the signed-in Web model picker when updating aliases.

## Media Routing Notes

- Flash-Lite image generation is routed to `Flash-Lite` and labeled
  `Nano Banana 2 Lite`; Flash/Pro first pass routes to `gemini-3-flash` and is
  labeled `Nano Banana 2`. The exact observed backend remains null unless the
  response exposes it.
- A `pro` image redo is a post-generation UI action and is not exposed as a
  separate first-pass MCP model.
- Music generation returns `Lyria` as the family label until the response
  identifies a version. The 2026-09-26 Flash result contained usable audio and
  cover video; the old `Lyria 3`/`Lyria 3 Pro` version inference is retired.
- Generic MCP video prompting did not enter the currently visible Gemini Omni
  mode. Use the dedicated Web Videos page until a verified MCP route exists.
