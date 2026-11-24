# Original version and release records

This archive retains the published tags, original server version names, and ClawHub versions. The development iterations in [the changelog](changelog.md) were reconstructed from the referenced commits on 2026-11-20. Iteration numbers identify the scope of development work. The records below establish the original version names and release evidence.

The current development history further separates input-evidence corrections, cleanup-lease verification, and session recovery deadlines into focused patches. The original releases below remain the provenance for their cumulative contents.

## Local 0.20.x workspace milestones — recorded 2026-11-22

`0.20.1` through `0.20.6` group completed corrections by contract within the same uncommitted development checkout. They were assigned retrospectively on 2026-11-22, not recovered from six commits, tags, releases or separately versioned packages. The preceding actual local package is `0.20.6`; its actual final64e8 wheel/installation and live results were verified independently in the [0.20.6 acceptance report](live-media-0.20.6-2026-11-22.md). Existing `0.20.0` build hashes, reports and generated files keep their actual version and provenance. No GitHub tag, release or publication is created by this bookkeeping.

| Local milestone | Correction scope |
| --- | --- |
| 0.20.0 | Background Task approval baseline |
| 0.20.1 | Current Web selectors, SDK media contracts and actual Task-model evidence |
| 0.20.2 | Same-credential historical ledger ownership, leases and deduplication |
| 0.20.3 | Native media submission, inline/single-reference workflows and original prompt |
| 0.20.4 | Browser authentication attributes, human-verification Cookie and bounded 429 handling |
| 0.20.5 | Exact refusal/notation/error-card finalization and verified-cleanup reconciliation |
| 0.20.6 | Composer state, safe tool/menu recovery and original-deadline pre-submit reopening |
| 0.20.7 | New finite Chinese-refusal gap observed after the actual0.20.6 builds; original-handle finalization only |

`0.20.7` is a subsequent evidence-backed patch for a newly observed exact completed-empty refusal that the prior source did not recognize. It is not an extra retrospective split; the actualbc4c package and original-handle refusal finalization are tracked separately in the [0.20.7 report](live-media-0.20.7-2026-11-22.md).

The branch HEAD inspected for this reconstruction is `10760d1` (2026-11-13); the later correction groups are working-tree changes. The fetched original commit/tag history remains separate. Scope details and current acceptance belong in the [changelog](changelog.md).

### Original 0.20.0 local-build acceptance snapshots

The following notes were formerly accumulated under `Unreleased`. They describe the actual original-version builds, including intermediate failures and subsequently resolved limitations. Moving them here does not relabel any build as a new patch or claim each milestone was separately shipped.

- Chrome activation is verified against a synthetic native card. Live deletion approval and Linux activation remain unverified. The worker reads back Task continuation after activation.
- Authorized `default` account MCP testing returned the expected Neon answer with effective selector `gemini-neon` and completed cleanup. A two-reference image produced a verified 2048×2048 JPEG matching both synthetic references with dispatched Hydrogen; its exact image generator remains unobserved.
- Recorded official frontend evidence confirms image/video/music composer modes 14/11/21 and provider-declared media defaults for the SDK route. A 2026-11-21 MCP music request saved ffprobe-verified 74.135-second stereo MP3 and 74.167-second 1024×1024 MP4 with audio/video; video probes failed upstream. This single historical success does not verify the new native route.
- The tested 2026-11-21 wheel matched 139 source Python/data files at that snapshot. The [19-case default-account matrix](live-model-matrix-2026-11-21.md) retains 13 passes and six rejected, network-failed or unverified media outcomes. The 4,373 offline tests, 2,308 contract checks, static gates and installed stdio smokes passed for that historical build.
- After correcting browser cookie attributes, Hydrogen/Carbon music recovered original handles, saved verified MP3/audio-bearing MP4 and completed cleanup. MP3 durations were 62.093/62.615 seconds; 1024×1024 MP4 durations were approximately 62.1/62.633 seconds. Neon refused one default-settings request but a separate native browser Short/Instrumental control produced a player. This does not establish MCP option acceptance; native video success is still unverified.
- The 19-case cohort V5 wheel `ba8e0ab20c1bf3b42d1dc5dcdbfed733e3126191952a3e1bdbd9291d25e0a041` was tested through the shared CLI. Its 142-file parity, 4,628 offline/2,316 contract checks and other build gates are historical candidate evidence, with details retained in the acceptance report. Product version remains `0.20.0`.
- The ba8e 19-case cohort finished local observation: 12 strict passes, one unverified saved-artifact chain and six call errors. The runner's 13 passes include Neon editing a synthetic local control after its image call failed; that edit does not verify saved-operation continuation. All three Task cleanup retries completed and independent checks found their Goals/chats already absent without mutations. Hydrogen music has independently verified non-silent audio of about 176 seconds. No video passed, and Neon music timed out without remote identity, leaving the remote result unknown. See [11-22 report](live-model-matrix-2026-11-22.md).
- Separate 7c follow-up verified Neon ordered references/true saved-operation editing and Carbon single-reference synchronous video/music, with independent artifact and cleanup evidence. Hydrogen/Neon video observations and later sequential attempts stay separate. Hydrogen music initially remained pending after a refusal-predicate gap; final delivery resolved it by original-handle readback below. The 7c parity/build gates remain historical evidence. [Follow-up report](live-model-followup-2026-11-22.md)

- The earlier fcdf delivery snapshot passed 143-file byte parity, 4,699 offline/2,522 contract checks and its recorded install gates. Its original 7c Hydrogen music readback and six known-chat absence checks remain valid history; these do not establish that the later High/readback fixes are delivered. [Earlier report](live-model-followup-2026-11-22.md)
- The hook-free 67b cohort saved both 10-second Hydrogen/Neon videos and 61.675-second Hydrogen music; seven files passed independent hash/decode QA. Neon High failed before submission, and source NoSend success is not backfilled into that result. The 93 candidate passed 4,840 offline/2,637 retained checks but still failed its new High pre-submit attempt and left two recoveries partial. Final acceptance of the subsequent source fixes is pending.
- ca64 passed 4,922 offline/2,719 contract checks and restored two original music handles as verified technical-card refusal/score-only failure with completed cleanup, without replay. Independent checks confirmed 13/13 known targets absent with zero mutations. Its new High also failed before dispatch; those failures are retained. A later typed-429 fix passed four regression/225 related checks and awaits a new build; High investigation continues.
- 2663 passed 4,935 offline/2,732 contract checks, Ruff/Mypy, six entrypoints and profiles, with 63 browser/225 related checks. Its first High/Hydrogen-music calls timed out confirming tool entry after the media-tool click, before dispatch; Hydrogen/Neon videos reached exact completed technical rejection with cleanup. All four failures remain recorded. Source or logging-hook NoSend controls and older 67b files do not establish current live success.
- 2663 High attempt2 completed with notation only after 318.12 seconds, returning `ARTIFACT_NOT_RETURNED` with completed cleanup. A separate Neon Low call with the same prompt and short/instrumental options saved 65.149333-second MP3, 65.167333-second audio-bearing MP4 and cover in 248.17 seconds; independent hash, full-decode and non-silence checks passed. Explicit High/High/Low values were checked against their original operation records. The default remains `standard` (native Web default), without automatic downgrade or replay; this does not imply High is universally unsupported.
- The later 2663 Low Hydrogen/Neon videos failed before dispatch in 68.11/74.08 seconds. Low Hydrogen music remained pending after 802.19 seconds; independent exact completed-empty Chinese NoTool evidence later finalized through b562 original-handle readback, not another generation. A no-Send stale-menu control verified one trusted reset and closed-state readback; this is not artifact acceptance.
- b562 installed 144 byte-identical source/data files with no extras and zero differences across 46 pinned dependencies/47 distributions. Ruff, Mypy (153 files), 4,988 offline/2,783 contract checks and 73 browser checks passed. Its fresh-stdio saved/cached result reads preserved all identities with zero create/generate calls. Two new Low videos failed; separate Neon standard video passed with independent file QA. Earlier files retain their generation-build identities.
- b562's first hook-free Hydrogen Low original-handle recovery remained partial after 124.07 seconds; a separate observation-only strict read finalized the refusal in 14.07 seconds with completed cleanup. Final hook-free cached-result reads confirmed that failure alongside five completed-file cases and seven legacy unavailable records, with all identities preserved and zero create/generate calls. Neon standard video saved two files in 157.10 seconds; independent hash, full decode and sampled-frame QA verified the 10.026667-second 1280×720 H.264/AAC output. These are distinct attempts, not a rewrite of failed Low cases.
- b562 Hydrogen standard video failed in 235.10 seconds at tool-entry confirmation, explicitly not dispatched and attributed to its exact original handle. That snapshot triggered the subsequent strictly bounded pre-submit reopen under the original deadline, without retry after any Send/unknown submission, observed identity, account drift, rate limit or upstream refusal. The later 60fe build is recorded separately below.
- 60fe installed 144 exact source/data files, no extras, and zero differences across 46 pins/47 distributions. Ruff, Mypy (153 files), 5,009 offline/2,804 contract checks passed. The 274 targeted checks include real ffmpeg saving of second-ready video/music and negative retry boundaries. Its six-entrypoint/profile smokes and 13 existing-result reads passed, preserving all identities with zero create/generate calls. The latest Hydrogen standard request reached exact completed identity in 44.09 seconds but returned upstream technical rejection with completed cleanup and no artifact; it is not retried or counted as a new file success. Independently observed fresh absence is 20/20 known chats with zero mutations; seven older local reconciliations are excluded from that count.
- A subsequent final fresh snapshot verified 21/21 exact known-chat absences, zero mutations and zero active owned workers, preserving the earlier 20-target snapshot and excluding the seven legacy reconciliations. Four principal media deliverables kept matching SHA-256 and their original 67b/2663/b562 generation identities; final 60fe only read them back.

### Reconstructed local 0.3.0 scope (2026-05-23)

The changelog's May 23 `0.3.0` heading is a reconstructed local milestone for Web model/tool routing, based on the [original Web-alignment commit](https://github.com/Luckycat133/gemini-web-mcp/commit/e1da89c). Its number and date are preserved as scope history. It is not the later published `v0.3.0` tag and does not establish a May 23 release; the official November 22 record follows separately.

## [0.3.0] - 2026-11-22

The official [v0.3.0 GitHub release](https://github.com/Luckycat133/gemini-web-mcp/releases/tag/v0.3.0) was published at 03:09:21 UTC, is neither draft nor prerelease, and points to `1116125e3113701f02acf3cc40c52a5290ca9968`. Its [tagged project metadata](https://github.com/Luckycat133/gemini-web-mcp/blob/v0.3.0/pyproject.toml) declares version `0.3.0`. This is independently published mainline history, not the local `0.20.x` development series.

Its recorded release scope includes Task recovery and Skill-description corrections after the mainline rebase, source-discovery documentation and CI/dependency-contract work. The tagged console metadata retains `gemini-mcp-account` in `src_legacy`, `gemini-mcp-create`, and the newer account entry as `gemini-mcp-account-next`; it does not establish parity with this checkout's default six-tool account surface. The original release body and tag are the source of truth for that distribution.

## [0.2.2] - 2026-10-25

### Added

- Added the seven-tool Create server and seven-facade Account server, with matching Skills and installation examples.
- Added durable metadata-only SQLite operations with opaque handles, optional idempotency keys, transactional claims, renewable leases, and delayed cleanup. Saved artifact locators allow recovery across process restarts.
- Added operation cancellation with distinct requested and confirmed states. Assist gained explicit Research start, status, result, and cancel actions.
- Added native Notebook creation, deletion, title and instruction changes, and source management. Conversation moves verify the exact destination Notebook.
- Added Remy schedules with six trigger types and pause/resume. Preserved fields outside a requested schedule change and verified the resulting record.
- Added Gemini Skill management, enable/disable, and ZIP imports that verify the full uploaded file set.
- Added paginated Library asset reads and exact media removal, plus public-link creation and exact-token deletion.
- Added Canvas creation, version reads, UTF-8 export, and whole plain-document replacement. Replacement checks the content copies and preserves unknown fields.
- Added exact Drive, Photos, and NotebookLM references for mixed-input understanding. Added conversation rename, pin, and unpin with read-back.
- Added opt-in video and music onboarding with explicit operation recovery.

### Changed

- Used the observed Gemini Web modes for image, music, and native video. Kept model and thinking selection separate and aligned dynamic Argon and native Task execution with the signed-in Web interface.
- Shared primary and compact media generation, artifact saves, and recovery. Kept unfinished Task and media operations available after disconnects and restarts.
- Saved compact image creation and editing output locally by default. Preserved requested output filenames in durable recovery records and reverified existing files when resuming.
- Updated Create and Research tool annotations to cover local file saves and source-chat cleanup. Clients can distinguish those effects from a read-only progress query.
- Raised the minimum `gemini-webapi` version to 2.1.1 for model discovery, media recovery, and the tested media paths. Refocused the runtime Skills on actual tool contracts and recovery.

### Fixed

- Verified output modality and local files before recording success. Used actual image bytes for MIME types and extensions, preserved requested filenames across restart, and distinguished queued or partial results from definitive empty output.
- Preserved original source IDs across SDK rollback and cancellation. Suppressed implicit SDK generation and mutation replays. Confirmed cleanup by exact read-back and scoped it to the owning operation, account, and conversation.
- Rejected malformed account inventories and wrong-ID read-backs. Verified Gem writes, Notebook moves, schedule changes, Skill file sets, and chat deletion against their exact targets.
- Bounded Research start, polling, and report retrieval. A plan, refusal, or changed conversation text cannot establish report completion.
- Bounded media recovery and distinguished transport errors, rejected RPCs, malformed cards, and definitive empty music output. Partial outputs preserve recovery of missing companion files.
- Reported credential and OS browser-storage failures separately. Preserved modern Chrome profile identity and stopped recommending accounts that failed verification.
- Preserved structured MCP SDK 2.1 output. Limited concurrent first-time SQLite initialization retries to one three-second budget.
- Protected local Prompt storage with atomic replacement, rollback, damaged-file preservation, and a lock across processes.

### Security

- Cancelled queued cleanup on account changes and redacted proxy credentials from diagnostics.

The [archived release notes](https://github.com/Luckycat133/gemini-web-mcp/blob/d5dbb406357ef21df3a612aa51bc9888efe07879/docs/changelog.md) include all ten entries that mainline folded into this release after tagging.

The dated acceptance record covers image creation and editing, music, native video, interrupted-job recovery, and verified cleanup. The October 17 run independently verified all five owned source conversations absent. [Recorded verification](live-ui-coverage.md)

## [0.2.1] - 2026-08-23

### Added

- Introduced `gemini-mcp-assist` with Ask, grounded search, image understanding, mixed-input understanding, and asynchronous Deep Research.
- Added grounded search with deduplicated source URLs and explicit grounded, answer-only, unavailable, and failed outcomes.
- Added image and screenshot understanding and up to 16 typed inputs with stable caller IDs. Inputs report accepted, analyzed, skipped, or failed state. Marking an input analyzed requires per-input evidence.
- Added asynchronous Research returning an opaque operation handle and retaining the source conversation for later recovery.
- Added the Assist runtime Skill, standalone Skill archive, and installed stdio verification with modern and legacy MCP negotiation.

### Changed

- Routed assistance requests to the focused Assist Skill and shared the Research start path with the compatibility tool.
- Consolidated Doctor diagnostics and split management registration, Research execution, compact history, and creation result handling while retaining the tool contracts.
- Declared Pydantic as a direct dependency for typed understanding requests and made the Assist Skill archive a required release asset.

### Fixed

- Kept history read-back compatible with the `gemini-webapi` enum change from `READ_CHAT` to `LIST_CONVERSATION_TURNS`.
- Checked exact Research source hostnames and normalized source URLs for deduplication. Rejected invalid URLs, favicon paths, and blank source titles.

### Removed

- Retired the unused Python MCP generator Skill and removed an older dated release note from active navigation. [Archived May 23 notes](https://github.com/Luckycat133/gemini-web-mcp/blob/c0d5d0c3a8ee8e100c9c7ab98b085f19c1ded121/docs/release-notes-2026-05-23.md)

## [0.2.0] - 2026-08-13

The oldest retained Git tag consolidated work from May through August. The dated source snapshots below trace that work. [Version-unification commit](https://github.com/Luckycat133/gemini-web-mcp/commit/4341253563870012e465ea86741d295ed311ac94) · [Original changelog](https://github.com/Luckycat133/gemini-web-mcp/blob/4341253563870012e465ea86741d295ed311ac94/docs/changelog.md)

### Added

- Consolidated the earlier server and compact workflows into the tagged baseline. Their original version-by-version changes are recorded below.
- Added typed result and artifact contracts for remote, local, queued, empty, and failed outcomes, with stable identity and local file validation.
- Centralized evidence-backed RPC contracts and pure parsers with success, empty, rejection, and changed-shape fixtures. Shared account, history, and lifecycle services across primary and compact entrypoints.
- Added credential-free onboarding, client examples, standalone runtime and development Skills, and reproducible wheel, source distribution, and Skill packaging.
- Added an opt-in live compatibility canary with read-only probes and sanitized reports.

### Changed

- Replaced the FastMCP adapter with the MCP Python SDK v2. Added structured results and modern and legacy stdio negotiation.
- Normalized delta and cumulative stream chunks and marked delivery as collected. Added queued, running, completed, and timed-out Research states with continuation IDs.
- Standardized session handles, retention, cleanup states, and account verification. Single-session reset and all-session reset use explicit scopes.
- Unified version metadata and artifact names. Included AGPL-3.0-only license metadata and text in the Python distributions. The separately distributed runtime Skill used MIT-0.

### Fixed

- Serialized concurrent session sends and shared asynchronous client initialization across callers. Preserved active sessions when cleanup or reset could not complete.
- Prevented a cancelled caller from cancelling shared initialization and prevented an old initialization from replacing a reset client.
- Required positive read-back for chat deletion and Gem changes. Bounded macOS browser credential lookup and avoided Keychain prompts in offline protocol tests.

A separate 2026-08-08 authorized local run observed text, session, history, and delete behavior. It was not recorded as a dedicated-account full canary. CI, fixture, package, and protocol checks are offline evidence, and current Gemini behavior is never inferred from those gates.

## Recovered server versions

These entries describe the original server package versions. The August 1 source renumbered `2.2.0` to `1.3.0`. The later history standardized the project at `0.2.0`. Dates below identify the version commits, and the linked metadata establishes the package name and version. [August renumbering](https://github.com/Luckycat133/gemini-web-mcp/commit/28681e7820822656c0ab92ae07ab8223c2a91ba5) · [August version unification](https://github.com/Luckycat133/gemini-web-mcp/commit/4341253563870012e465ea86741d295ed311ac94)

### 1.3.0 — 2026-08-01

The original changelog headed this work July 17, carrying forward the date of the underlying `2.2.0` changes. The package version changed on August 1. [Version diff](https://github.com/Luckycat133/gemini-web-mcp/commit/28681e7820822656c0ab92ae07ab8223c2a91ba5)

#### Changed

- Changed `project.version`, `src.__version__`, and the server banner from `2.2.0` to `1.3.0`.
- Capped the MCP dependency at `mcp>=1.0.0,<2` to keep the FastMCP implementation installable while SDK v2 migration was planned. Later `0.2.0` migrated the adapter to SDK v2.
- Rewrote earlier changelog headings using the `1.x` numbering. The correspondence with the original `2.x` entries is recorded below. [Changelog rewrite](https://github.com/Luckycat133/gemini-web-mcp/commit/0d3effcab37329814620a189820b66fe90b5671b)

### 2.2.0 — 2026-07-17

Version metadata changed July 17. The refactor and Skill changes merged July 18. [Version metadata](https://github.com/Luckycat133/gemini-web-mcp/blob/bcce41d6642065bf9dc597f13beb25002fa3e11d/pyproject.toml) · [Merged change](https://github.com/Luckycat133/gemini-web-mcp/commit/573eddefd00995cc64c9a333de8baac800e8bdf6)

#### Added

- Added a progressively loaded tool reference to the runtime Skill, organized by read-only discovery, private reads, generation, and destructive operations.
- Expanded regression coverage for sessions, browser credentials, prompts, Research, scheduling, Notebook moves, and test-artifact cleanup. Included concurrent initialization and invalid-attachment checks.
- Added the English repository homepage and Chinese counterpart, with packaged documentation assets.

#### Changed

- Split compact account, schedule, and session action dispatch into individual handlers. Session list and reset avoided unnecessary client initialization.
- Split Deep Research orchestration into native and fallback paths with shared error handling.
- Shared JSON and error rendering and extracted manifest data while preserving compatibility exports.
- Documented the `gemini-webapi>=2.0.0` requirement used by the implementation and synchronized package, banner, and install examples.
- Aligned Skill metadata, reference directories, and portable validation instructions. Batched independent account probes and improved history merging and parsing.

#### Fixed

- Moved the initialized check inside the client initialization lock to prevent duplicate concurrent initialization.
- Protected Prompt manager singleton creation with a lock.
- Replaced silent browser import and close failures with diagnostics and corrected local type and lint errors.

#### Removed

- Removed the unused authentication module, image helper, and stale imports.

### 2.1.2 — 2026-07-04

[Version metadata and distribution changes](https://github.com/Luckycat133/gemini-web-mcp/commit/ad3177f6a0b4372cd9addb25663b943f963e5251)

#### Added

- Added `scripts/package_release.py` to build the wheel, source distribution, and standalone runtime Skill ZIP together.
- Added `MANIFEST.in` so source distributions include documentation, evaluations, and the public Skill.
- Added the launch kit, installation links, distribution checklist, and English and Chinese release copy.

#### Changed

- Updated README version, package badges, and public installation instructions for the distribution.

The maintainer reported a clean-wheel MCP initialize/list-tools check on July 10. The Kilo Marketplace submission pinned this wheel for `core`, `model`, and `history`. The download URL returned HTTP 404 during the November 20 history audit. [Maintainer report](https://www.reddit.com/r/mcp/comments/1unu90b/showcase_gemini_web_mcp_layered_fastmcp_server/) · [Pinned marketplace configuration](https://github.com/Kilo-Org/kilo-marketplace/pull/172/commits/256157f2159e00a91afb6c78f9b972adf65c9b31)

### 2.1.1 — 2026-07-04

[Version metadata and Skill distribution changes](https://github.com/Luckycat133/gemini-web-mcp/commit/20a44eaf16069850eebf05ac17c1c57f6a12f941)

#### Added

- Published `.agents/skills/gemini-web-mcp` as the repository's discoverable runtime Skill.
- Added direct GitHub Skill installation and manual installation instructions.
- Added packaging tests comparing the public Skill with its local client copy.

#### Changed

- Documented the installation relationship between the runtime Skill and the MCP server it invokes.

### 2.1.0 — 2026-07-04

This entry collected the Web-alignment and management work developed in May and June. [Version metadata](https://github.com/Luckycat133/gemini-web-mcp/blob/7ead82cbdd286c9376ae0eccf757f7da44bee0b5/pyproject.toml) · [Original detailed notes](https://github.com/Luckycat133/gemini-web-mcp/blob/9594827f79a745ccb27697a171846fb00b14f23f/docs/changelog.md)

#### Added

- Added history list, title and ID search, bounded optional turn scanning, exact reads, and Markdown or JSON export through granular tools and the `gemini_history` facade.
- Added multi-source history scans over observed history filters, Notebook threads, and Remy conversation references, with source coverage and pagination metadata.
- Added native Notebook listing, Notebook conversation reads, and moves of an existing conversation to an exact destination with verification.
- Added account inventory, usage, public-link and Library metadata reads, Web capability probes, a tool manifest, and MCP side-effect annotations.
- Added daily scheduled-action creation and deletion, registry listing, and exact-ID inspection. Added the compact `scheduled` facade.
- Added browser profile discovery and selection, Cookie status diagnostics, `gemini_doctor`, and dry-run cleanup of explicitly marked test artifacts.
- Added `learning_mode` requests for quizzes, flashcards, practice tests, and study guides.
- Added report-derived webpages, infographics, quizzes, flashcards, audio-overview scripts, and application specifications. These tools used completed report content as their input.

#### Changed

- Made `core` the default and exposed narrower `model`/`chat`, `history`, `history-organize`, `account-read`, `scheduled-read`, and `scheduled-admin` profiles.
- Used facade-first history and account tools for ordinary agents and kept granular management in explicit maintenance profiles. Local Prompt management moved to its own opt-in profile.
- Aligned model aliases with the observed Web picker and passed `standard` and `extended` as independent thinking choices. Recorded the image and music routing observed at the time.
- Added pagination and availability diagnostics to history and account responses, and distinguished schedule acknowledgement, registry visibility, and deletion state.

#### Fixed

- Ignored unreachable local proxy settings during initialization.
- Checked candidate Chrome profiles and isolated the SDK Cookie cache when selecting an account.

#### Removed

- Removed `gemini_list_features` from the tool catalog.

### 2.0.0 — 2026-05-05

[Original package metadata](https://github.com/Luckycat133/gemini-web-mcp/blob/47f9c57d10710d379e395584b104f404653307fb/pyproject.toml) · [Original release notes](https://github.com/Luckycat133/gemini-web-mcp/blob/9594827f79a745ccb27697a171846fb00b14f23f/docs/changelog.md)

#### Added

- Added modular chat, session, media, file, Research, and management tool modules.
- Added streaming chat and session continuation, model selection, image generation and editing, video and music requests, file upload, and URL analysis.
- Added the Deep Research entrypoint and report output.
- Added local Prompt management and browser Cookie tooling during this development period.
- Added configuration, model, tool, architecture, and setup documentation.

#### Changed

- Split server registration, shared client setup, and model constants into separate owners.
- Defined explicit model aliases and tool groups through environment configuration.

The old notes included specific media model names and duration claims. Current media support and observed output are documented in the tool reference and dated verification records. [Current tools](tools.md) · [Media verification](live-ui-coverage.md)

### 0.1.0 — 2026-05-05

This is the earliest recovered Python package version. The old changelog labels it “Initial Release” without a release date. May 5 is the date of the package commit. [Original package metadata](https://github.com/Luckycat133/gemini-web-mcp/blob/323d738bdb3ebccd920ec8431fa56af4b32a70b2/pyproject.toml)

#### Added

- Added the MCP project scaffold, basic account authentication, and conversation support.

## Historical numbering correspondence

The August 1 changelog rewrite renamed earlier sections. This table retains all five additional `1.x` headings and identifies overlapping notes. Package metadata is independently confirmed for `1.3.0`. The other `1.x` labels are supported by the archived changelog. [Original rewrite](https://github.com/Luckycat133/gemini-web-mcp/commit/0d3effcab37329814620a189820b66fe90b5671b)

| Heading in the 1.x notes | Date printed in those notes | Corresponding original changes |
| --- | --- | --- |
| `1.3.0` | 2026-07-17 | `2.2.0` Skill alignment and refactor. The package was renumbered August 1. |
| `1.2.0` | 2026-07-04 | `2.1.2` asset builds and distribution guide. |
| `1.1.2` | 2026-07-04 | Repeats the public Skill and packaging text of `1.1.1`. |
| `1.1.1` | 2026-07-04 | `2.1.1` public Skill discovery and installation. |
| `1.1.0` | 2026-07-04 | `2.1.0` Web alignment, history, account, and profile work. |
| `1.0.0` | 2026-05-05 | `2.0.0` modular server and expanded tools. |

## ClawHub Skill releases

The ClawHub runtime Skill has an independent release history. Dates use Asia/Shanghai. [Registry version history](https://clawhub.ai/api/v1/skills/gemini-web-mcp/versions) · [Publisher listing](https://clawhub.ai/luckycat133/skills/gemini-web-mcp)

### Skill 0.2.1 — 2026-08-23 15:19

#### Changed

- Synchronized the Skill with repository `0.2.1`.
- Routed assistance to the focused Assist Skill and updated the supported tool coverage.
- Added artifact handoff guidance and recovery playbooks for ongoing operations.

### Skill 0.1.1 — 2026-08-09 08:54

#### Security

- Classified browser Cookie caches as sensitive account authorization material and required explicit approval and secure handling.
- Scoped session reset to conversation state and clarified macOS browser authorization boundaries.

### Skill 0.1.0 — 2026-08-09 00:17

#### Added

- Published the initial runtime Skill preview with profile-based workflows.
- Documented privacy boundaries, artifact verification, and explicit handling of destructive operations.

[Unreleased]: https://github.com/Luckycat133/gemini-web-mcp/compare/v0.2.2...codex/gemini-tools-polish-20261101
[0.2.2]: https://github.com/Luckycat133/gemini-web-mcp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Luckycat133/gemini-web-mcp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Luckycat133/gemini-web-mcp/tree/v0.2.0
