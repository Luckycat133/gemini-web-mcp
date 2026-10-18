# Gemini Web Live UI Coverage

This page records dated observations of signed-in Gemini Web and related MCP
checks. Each section identifies the tested surface and evidence scope;
historical observations do not establish current availability.

## 2026-10-18 Native Capability Workflows

The follow-up implementation uses the same observed public Web build
`BardChatUi.zh_CN.D3Wg_SjWYeA.2018.O`. Official JavaScript request builders,
response readers, URLs and SHA-256 hashes are recorded in
[the public source record](../compatibility/web-request-source-20261018.json).
Static source evidence establishes the protocol shape; it does not establish
account entitlement or successful execution. Offline fixtures cover successful,
empty, rejected and changed responses, with independent mutation read-back.

The focused account surface exposes these workflows through its existing
seven typed facades. Primary and compact adapters delegate to the same services.
The current authorized MCP acceptance run has verified Notebook CRUD and an
added source reaching ready state, native Remy schedule CRUD/pause/resume,
native Skill CRUD/enable/disable/ZIP import/replacement, public-link creation
and exact deletion, Canvas creation/get/version listing/plain text edit/export,
and Library asset listing/get/supported-image deletion. These are bounded disposable-resource observations, not a
dedicated-account full canary or evidence that every recurrence/source variant
has been exercised.

| Workflow | Implemented scope | Current acceptance boundary |
| --- | --- | --- |
| Gemini Web Notebooks | Title/instructions CRUD; text, URL and uploaded sources; source removal; existing chats/move | Description writes are not supported. Create followed by instructions update is independently read back. Text/file sources reached indexed state and were removed. Get omits their original content, so full content verification remains accepted/source_content_unobserved; URL ingestion was not live-tested. |
| Native Remy schedules | Hourly, daily, weekly, monthly, yearly and one-off triggers; exact-ID update/pause/resume/delete | Native IDs use `backend=remy`; legacy daily actions remain separate. CRUD/toggle checks do not prove execution of every trigger. Deletion was confirmed by the complete fresh registry; deleted-ID reads returned an unavailable server error, not an absence value. |
| Native Skills | Manual fields, enable/disable, delete and ZIP import/replacement | CRUD/toggle/ZIP import/replacement verified. A same-name ZIP conflict was rejected; unique-fixture import passed. Separate from Gems and local Prompts. |
| Library assets | Filtered pages with continuation, exact get/delete, unique-source protection | List/get and exact image deletion verified. Current Web rejects Canvas/document deletion from Library; the MCP rejects those kinds before mutation. Snapshot IDs change after edits; grouped/identity-free targets cannot be safely removed. |
| Public sharing | Current list, explicit public-link creation and exact-token deletion | Creation and exact deletion independently verified. No delete-all or inferred disabled flag. |
| Canvas documents | Native create, get, chat-artifact discovery, versions, plain text replacement and UTF-8 export | Create/read/versions/edit/export verified, including all four content copies and preserved leading/trailing whitespace. Edit updates the same artifact and replaces formatting without creating a new version. Other kinds, attachments and citations are outside edit scope. |
| Native understanding references | Caller-selected Drive, Photos and NotebookLM IDs in typed mixed inputs | No resource ID is guessed or obtained from a Gemini Web Notebook. Native attachment acceptance has no current live pass. |

A separate native image control produced a decoded and visually checked
2048×2048 JPEG through Flash-Lite/Nano Banana 2 Lite. Editing changed its blue
circle to green and produced another verified 2048×2048 JPEG. The edit's
2-second cleanup job completed in one attempt with `verified_absent`, observed
through MCP cleanup status. The Flash control returned a content-access refusal
and no image; its retained source was inspected without retrying that request.
This does not establish that every image model/account route works.

Initial mixed-input understanding and Guided Learning checks timed out; bounded
MCP retests subsequently completed with upstream responses. The mixed retest
covered text, a local file and a URL, not native account-resource references.
The original Research operation was recovered by the same handle after restart
and returned `failed/completion_not_observed`; no report acceptance or restart
by resubmission is claimed. Ordinary exact-chat metadata updates passed; an
update to a Notebook-moved chat remained accepted because the history target
was not independently observed. Native repository import, Task
cancellation, settings/memory mutations, native PDF/Google Docs export and
Library rename/update remain unsupported.

**Run closeout:** all 22 recorded disposable source IDs were individually
verified absent through real MCP stdio calls. The owned Gem, two Notebooks,
two native Skills, schedule and public link were removed; all three added
Notebook sources were removed with read-back. Supported Library image deletion
passed, and a completed fresh Library scan found no remaining test entries
after source-chat cleanup. Cleanup used exact owned IDs from this run; no
marker-based bulk deletion was used. No private IDs, bodies, credentials or
generated media are included in the repository.

Final local gates passed Ruff, Mypy (90 source files), **3,461 offline tests**,
**438 contract checks**, representative profiles, and modern/legacy stdio on
all five surfaces under model and core profiles. Wheel/sdist and the four
standalone Skill packages built; isolated installed-wheel/onboarding checks
are separate from live-account acceptance. Earlier dated audits below retain
their original scope.

## 2026-10-18 Broader Capability Audit

An additional authorized, read-only Chrome pass inspected the upload/tools menu,
Library, Scheduled, settings, the native Skills page and public-link management.
The build matched the source record. It observed Google Drive, Photos,
repository import and Notebook attachment entries; image/video/music, Canvas,
Deep Research and Guided Learning; and separate native Skills, Task settings
and media-watermark controls. Opening a page or menu is UI evidence only.
Native Skills at `/customize/skills` are distinct from Gems and this package's
local Prompt library. No account setting, existing task, Skill or public link
was modified during this audit, and no conversation was generated.

Real focused account MCP reads succeeded for Gems, Notebooks, localized Library
capabilities, usage and mode status. Two compatibility failures were reproduced:
legacy `XPSWpd` returned an empty message while the current Scheduled page had
native Remy schedules; the old `K4WWud` link index did not match the current
shared-conversation listing. The current frontend identifies `Ey90Ac` /
`Mfr55e` for native schedule reads and `OXxBcc` for public links. Public source
chains and hashes are recorded in
[the source record](../compatibility/web-request-source-20261018.json).

The implementation audit also found compatibility adapters that displayed
invalid reads as empty inventories and counted a missing RPC body as a successful
probe. Shared strict response validation now separates a positively observed
empty response from HTTP, envelope, rejection and shape failures. Report-action
metadata also distinguishes an observed UI menu from a captured native mutation
RPC; local scripts/specifications are not provider-generated audio/apps.

After the fixes, **15** real read-only MCP calls passed across focused, primary
and compact surfaces. Each returned the same public-link page and read the same
observed native schedule ID through its matching backend. Legacy listing was
verified separately; an empty legacy result did not hide the native schedules.
Gems/Notebooks/Library capability reads and usage/mode status also passed.
No new conversations or remote resources were created, so there were no
disposable chats to delete. Temporary clients and the inspection tab were closed.

Local verification passed: Ruff, Mypy (82 source files), 2,906 offline tests,
371 architecture/distribution checks, representative profiles, and real
modern/legacy MCP stdio checks on all five surfaces. An independent regression
review found no additional actionable issue in backend routing, inventory
evidence or the current public-link parser. These are separate from the
15 account reads above and do not verify unsupported mutations.

See the [coverage matrix](#primary-mcp-coverage) for supported workflows,
remaining gaps and evidence dates. This audit does not establish complete
Gemini Web parity or validate every account mutation.

## 2026-10-18 Models, History and Native Tasks

The user authorized this bounded run on their signed-in Chrome account. Current
public frontend build `BardChatUi.zh_CN.D3Wg_SjWYeA.2018.O` and real MCP SDK
2.2.0 stdio calls used the checkout on Python 3.13.12 / gemini-webapi 2.1.1.
Protocol source references and hashes are in
[the source record](../compatibility/web-request-source-20261018.json).

- Chrome and runtime discovery showed 3.5 Flash-Lite, 3.8 Flash, 3.1 Pro and
  4 Argon. Current low/medium/high thinking options map to 1/4/2. MCP generation
  succeeded with Argon + medium and Flash + high; captured request selectors
  matched between body and header. Native Task calls also exercised low.
- On one disposable chat, focused account renamed/pinned it, compact unpinned
  it, and primary renamed/pinned it again. Each update returned independently
  verified metadata. MCP search, read, export and final deletion also passed.
- The webpage Task sample and primary/compact MCP Task calls reached a matching
  Goal `done` read-back. The current Task request includes selector 40 and the
  observed field-84 boolean. Restarting the primary client and reading the same
  Task through compact did not create another generation.
- A compact callback signature error was found and fixed with real adapter
  wiring regressions. Its source was identified through MCP metadata and an
  exact disposable-prompt read. A later SDK timeout preserved its source ID;
  MCP recovered the same Goal as done and retrieved the model's expected answer.
  Neither attempt was automatically resubmitted. The timeout's cause is not
  attributed to the subsequently completed request contract.
- A completed Task with `delete_after_seconds=1` was automatically deleted and
  reported `completed / verified_absent`, without an explicit cleanup run for
  that source. The normal 30-minute policy was observed on the compact sample;
  that exact registered job was then explicitly executed during teardown.

All **nine** conversations created by this run were removed through MCP: eight
explicit deletions/exact-job cleanups and one automatic cleanup, each with
`verified_absent` evidence. Fresh metadata contained no owned source IDs, and
complete Task-registry pagination contained no owned Task references. No pending
cleanup jobs remained in the run's private state. Temporary clients and the
agent-created browser tab were closed; broad private history snapshots were
discarded after extracting these bounded observations.

Offline verification passed: Ruff, Mypy, 2,590 pytest cases, 361 contract cases,
representative profiles, modern/legacy MCP protocol smokes for model/core, and
an isolated built-wheel auth-free onboarding preflight. Task session concurrency,
mode switching and invalid creation text also passed an independent local
review. This is bounded manual-account evidence, not a dedicated live canary or
verification of every external Task action. Provider Task cancellation is not
exposed; a nonterminal source remains retained, and status recovery alone does
not delete it after later completion.

## 2026-10-17 Multimodal Generation, Recovery and Cleanup

The user's signed-in Chrome account was explicitly authorized for this bounded
manual run. Real MCP SDK `2.2.0` stdio clients called the checkout's focused
creation/account servers on Python `3.13.12` with `gemini-webapi 2.1.1` and
Pillow `12.3.0`. The baseline was `3678586`; the video recovery retest included
the `CreationService.recover` fix in this change. A fresh private state database
contained only this run's operation handles and cleanup jobs.

Chrome's model picker and the MCP model registry both showed `3.5 Flash-Lite`,
`3.8 Flash`, `3.1 Pro` and `4 Argon`. The Web tools menu exposed image, video and
music creation. Generation used the existing `flash` alias; this run does not
establish a generation route for Argon or identify Google's dispatched media
backend.

| Workflow | Observed output | Verification |
| --- | --- | --- |
| Image generation | 2752×1536 JPEG, 2,003,850 bytes | Independent full image decode and visual check of the requested blue teapot. |
| Local-image edit | 2752×1536 JPEG, 1,547,449 bytes | Independent full image decode; gold glaze with shape, composition and background retained. |
| Music | MP3, 85.524833 seconds; companion 1024×1024 MP4, 85.533333 seconds | Audio/video streams probed and fully decoded with FFmpeg. |
| Video after unfinished-operation restart | 1280×720 H.264/AAC MP4, 10.005 seconds | Same operation handle recovered the requested filename; streams probed and fully decoded; sampled frames showed the requested teapot, steam and camera orbit. |

The requested `.png` image names correctly became `.jpg` for the returned JPEG
bytes. Duration requests remained provider prompts: the music prompt requested
about 30 seconds and video about eight seconds; the table records measured
output. Frame checks and full decoding do not establish continuous audiovisual
viewing or a listening review.

The first unfinished-video restart exposed a defect: a valid SDK model turn
without a media card or queued flag was treated as definitive empty output,
and automatic cleanup deleted its source before cancellation could withdraw
the job. That attempt produced no local video and remains a failed sample.
Recovery now retains the source when a read-back supplies no relevant output,
or the combined saved and observed artifacts lack the required modality.
Verified saved audio can still combine with a later companion-video download.
A separate new video request exercised the repair: restart while
running, queued recovery, successful exact-source MCP history read, then the
same handle's completed video. Repeating an earlier start key also returned
its existing handle. Recovery submitted no replacement generation.

Music's delayed cleanup was pending with zero attempts when all servers stopped.
It remained pending after its deadline passed. Restarting the account MCP
server and binding the same credentials resumed automatic cleanup, which became
`completed/verified_absent` without an explicit cleanup run. After another
creation-server restart, completed music and video handles returned the same
local Artifact identities and paths.

MCP cleanup status positively verified absence for all five exact sources from
this run: two images, music, the failed video and the repaired video sample.
Registered targets exactly matched the run's recorded source IDs. No earlier
conversation or broad marker sweep was used. All five saved files remained
usable after cleanup; hashes of previously verified files were unchanged.
Private receipts, source IDs, state files, credentials and generated media
remain outside the repository.

Ruff, Mypy (80 source files), 2,268 offline tests and 353 architecture/distribution
checks passed. New regressions cover incomplete SDK model turns, misleading
completion text, wrong media kinds, saved previews, preserved errors and
video/music recovery across repeated SQLite-backed service restarts, including
partial music with a later companion-only read-back. This run
verifies controlled unfinished-video restart and completed-file recovery;
arbitrary hard-crash recovery and the dedicated-account full canary remain
separate evidence.

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

### Cleanup invocation evidence

An earlier Chrome deletion attempt opened the chat menu but did not reach a
confirmation or positive absence read-back. It was not counted as a successful
deletion. The subsequent targeted cleanup used real MCP tool calls, including
primary `gemini_delete_chat` and focused `gemini_history` with `action=delete`.
The focused test harness connected an actual MCP client over stdio; its Python
wrapper collected results from `client.call_tool`, rather than making deletion
an agent-side SDK diagnostic. Registered-job recovery used `gemini_cleanup`
with the existing `job_id`. Automatic cleanup executes in the shared server
lifecycle during MCP generation/recovery, with its own read-back evidence.

## 2026-10-03 Focused Creation and Native Video Recheck

Signed-in Chrome showed the dedicated Videos page with video mode and a
16:9 landscape control. Re-fetched public frontend modules confirmed native
video mode 11 in the same index-49 StreamGenerate feature chain used for
image mode 14 and music mode 21. This establishes the request selection, not
the generator Google actually dispatched or successful creation.

One native primary MCP video request observed its new source chat, then
returned `UPSTREAM_REJECTED` after a possible upstream interruption. It
produced no video file. Exact-ID disposal subsequently returned positive
`verified_absent` evidence. The cause remains unconfirmed; no exclusive
generator endpoint or verified model-bypass route was established.

The focused seven-tool creation server was exercised over real MCP
`2026-07-28` stdio with SDK `2.2.0` and `gemini-webapi 2.1.1`. One image
request and one local-image edit each returned a saved 2816×1536 JPEG.
Independent decoding confirmed their MIME, dimensions and hashes; visual
inspection confirmed the blue wooden cat became gold with its shape,
tabletop and camera composition preserved. Both automatic cleanup jobs
completed, then exact-ID follow-up independently verified their sources
absent. At this stage fifteen owned-source deletions had positive read-back
proof, including the earlier twelve and the failed primary video source above.

Two focused asynchronous music starts failed in TLS initialization before
the media request was submitted. The second run reported `NETWORK_ERROR`
with `generation_not_started`; no observed source or artifact was returned.
Reading their failed handles after process restart worked, but that is not
proof of completed asynchronous music/video artifact recovery. A separate
authenticated model-registry read succeeded. These bounded personal-account
tests do not satisfy the dedicated-account full-canary gate.

Subsequent testing distinguished interrupted generation from completed-file
recovery. Two deliberately interrupted asynchronous music attempts observed
their sources but returned no local files: one recovery failed in transport,
the other with an upstream SDK failure. Their exact sources were discarded and
verified absent. A focused video start also failed before submission in TLS
initialization. No request was automatically repeated. Client initialization
now bounds typed transient transport retries to three; authentication and
argument failures are not retried.

One uninterrupted focused music start then completed with an MP3
(`audio/mpeg`, 1,764,013 bytes, 73.247292 seconds) and its companion MP4
(`video/mp4`, 5,688,253 bytes, 1024×1024, 73.247007 seconds). A separate native
video start completed with a 1280×720 H.264/AAC MP4 (`video/mp4`, 1,731,336
bytes, 10.005 seconds). Full FFmpeg stream decoding passed for all three files;
a sampled native-video frame showed the requested green paper cat. Each start
was submitted once. After the server exited, a new focused stdio process
retrieved the same completed operation's saved locators with no new generation.

Exact-ID follow-up returned positive absence proof for both successful
operations. The failed automatic music cleanup job was also resumed through
its existing durable job and became `completed/verified_absent`. Saved music
files still decoded with unchanged hashes after deletion. Nineteen recorded
owned test-chat deletions now have positive read-back evidence. This proves
current focused image/edit/music/video artifacts and completed-file recovery;
unfinished-provider recovery after a hard crash remains a separate live gate.

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

Source audit: **2026-10-18**. Implemented means a registered path exists with
local contract coverage; it does not mean every current provider flow was run.
Recent live evidence is identified explicitly. Focused servers provide the
same shared workflows where listed in [development status](development-status.md).

| Capability | Current implementation | Evidence and limits |
| --- | --- | --- |
| Models, thinking, chat and sessions | Dynamic registry, low/medium/high, one-shot/multi-turn/stream collection, temporary and Gem chat | Model/thinking calls verified 10-18; stream tools return collected upstream streams rather than client token streaming. |
| Native Tasks | Primary/compact `task_mode`, exact-source `task_status`, unfinished-source retention | Matching Goal completion and restart recovery verified 10-18; provider cancellation not exposed. |
| Image generation and local-image editing | Native selector, generated-image validation and local save | Flash-Lite native image/edit decode and visual checks verified 10-18; Flash control returned a content-access refusal. UI Pro redo is not a separate first-pass API. |
| Video and music | Native selectors, local verification; focused asynchronous handles | Verified files, controlled unfinished-video restart and completed-file retrieval on 10-17. Arbitrary hard-crash recovery is unverified. |
| Cleanup and cancellation | Account-scoped durable jobs; operation cancellation request | Delayed restart cleanup verified 10-17/18. Cancellation does not prove the provider stopped. |
| Image, file and URL understanding | Focused mixed inputs and primary file/URL tools | A 10-18 text/local-file/URL retest completed after an initial timeout. Remote URI inputs are prompt references; acknowledgement does not prove independent fetching. |
| Grounded search | Focused `gemini_search` with observed structured citations | Source-free answers remain `answer_only`; no new dated live acceptance in this audit. |
| Deep Research | Primary/focused start/status/result/cancel and report save | The current same-handle restart/result check returned failed/completion_not_observed; no new run was submitted and no current report pass is claimed. |
| Report-derived outputs | Local HTML/Markdown templates | Audio overview is a script; custom app is a specification. Neither proves native audio/app generation. |
| Guided Learning | Primary/compact native `learning_mode=guided_learning` plus companion modes | Native request retest completed with an upstream response on 10-18 after an initial timeout. |
| History | List/scan/search/read/export/delete, rename, pin/unpin | Fresh metadata and exact-chat updates/read/export/delete verified 10-18; bounded pages do not prove full account coverage. |
| Native Notebooks | CRUD for title/instructions, source list/add/remove, recent chats and move with read-back | CRUD and an added source's ready state verified 10-18; all source variants/current move acceptance are separate. Description writes unavailable; acceptance can precede ingestion readiness. |
| Scheduled actions | Legacy list/get/daily-create/delete; native Remy CRUD/pause/resume and six trigger types | Native CRUD/toggle checked 10-18; every trigger's future execution is unverified. Preserve `backend=remy` with native IDs; legacy mutations were last live-tested 06-20. |
| Gems | List/create/update/delete with complete custom-registry read-back | Create, partial update, invocation and exact deletion verified 10-18. Gems are separate from native Skills. |
| Local Prompts | CRUD, categories, get and variable rendering | Local atomic storage and regression coverage; does not submit to Gemini. |
| Library | Localized capabilities plus native asset list/get/delete with pagination and identity guards | List/get and exact image deletion verified 10-18. Current Web rejects Canvas/document single-asset deletion. No rename/update; partial pages do not prove absence. |
| Public links | Current list, create and exact-token delete | Creation/deletion verified 10-18. No delete-all or inferred disabled state. |
| Usage, model/mode status and feature probes | Sanitized read-only observations | Usage/modes rechecked 10-18; RPC reachability does not establish entitlement or named mode semantics. |
| Canvas | Native document create/get/chat-artifacts/versions, plain text edit and UTF-8 export | Create/read/versions/edit/export verified 10-18, including source whitespace and four content slots. Edit replaces formatting on the same artifact, without a new version; attached/cited/other-kind documents are excluded. |
| Drive, Photos and NotebookLM references | Native encoding for exact caller-selected resource IDs in mixed understanding | Current live acceptance unverified. NotebookLM is distinct from Gemini Web Notebooks; this is not automatic picker discovery. |
| Repository import | UI entry observed; local code files can upload | Native repository import is unsupported. |
| Native Skills | List/get/manual CRUD/enable/disable and ZIP import/replacement | CRUD/toggle/ZIP import/replacement verified 10-18. Separate from Gems and local Prompts. |
| Personalization, memory import and Task settings | Selected read-only probes/UI observations | Settings mutations and memory import remain unsupported. |
| UI exports and preferences | Observed entries only | Native PDF/Google Docs export and media-watermark/preferences administration are unsupported. Local Markdown/JSON chat export remains available. |

## Observed RPC Evidence

The following registry and cookie-context observations are historical June/July
evidence. Current native account, attachment and Canvas contracts are recorded in
the 2026-10-18 sections above; old registry emptiness is not current-page coverage.

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

The current model aliases, dynamic discovery and low/medium/high thinking
contract are maintained in [models.md](models.md). `standard` and `extended`
remain compatibility aliases. Historical model-picker observations above
apply to their stated dates; they are not a current model directory.

Guided Learning and native Task selection are independent request controls;
use only the mode needed by the requested workflow.

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
- Prompt-only video attempts did not establish a native route in the historical
  09-26 run. Native selector 11 subsequently produced verified video on 10-03
  and 10-17. Use the creation tools and require a verified video Artifact; the
  dated failed attempts remain separate observations.
