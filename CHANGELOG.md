# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses `0.x.y` pre-1.0 Semantic Versioning.

Detailed per-build validation logs live in [docs/changelog.md](docs/changelog.md), and original published release tags (`v0.2.0`, `v0.2.1`, `v0.2.2`, mainline `v0.3.0`) and acceptance archives live in [docs/version-history.md](docs/version-history.md). Local `0.18.0`–`0.21.0` entries reflect the active working tree on branch `codex/gemini-tools-polish-20261101` (`pyproject.toml` at `0.21.0`); no new public tag is implied.

## [Unreleased]

## [0.21.0] - 2026-11-25

### Added

- Added companion zero-context-tax JSON CLI `gemini-web-cli` (`src/cli.py`, registered in `pyproject.toml` & `scripts/update_local_mcp.py`) covering `doctor`, `models`, `quotas`, `inventory`, `public-links`, `library`, `notebooks`, `gems`, `skills`, `scheduled`, `tasks`, `history`, `chats`, `preferences`, and `cleanup`, auto-loading unset defaults from `~/.gemini/config/mcp_config.json`.
- Added 18 canonical `music_genre` slugs (`auto`, `pop`, `hip_hop`, `rock`, `kpop`, `latin`, `electronic`, `rnb`, `country`, `afrobeats`, `reggae`, `jazz_blues`, `classical`, `folk`, `lofi`, `acoustic`, `cinematic`, `ambient`) across `gemini_create_media`, `gemini_generate_music`, native browser automation (`src/data/native_media_browser.js`, `src/infrastructure/native_media_browser.py`), and `music_options_error`.
- Added image `aspect_ratio` support (`16:9`, `9:16`, `1:1`, `4:3`, `3:4`), `gem_id` & `learning_mode` on `gemini_ask`, and `report_action` (`list_actions`, `webpage`, `infographic`, `quiz`, `flashcards`, `audio_overview`, `custom_app`) on `gemini_research`.
- Added `canvas=True` (including immersive document extraction on `candidate_data[30][0]`) and `guided_learning`/`guided` + `canvas` to `LEARNING_MODE_CONFIG` (`src/constants.py`, `src/thinking_client.py`, `src/surfaces/assist.py`).
- Added native headless Chrome Deep Research submission (`src/data/native_research_browser.js`, `src/infrastructure/native_research_browser.py`, wired in `src/services/research.py`) for Gemini 4 BotGuard compliance, plus Gemini 4 `resolve_deep_research_transport_model` mappings (`hydrogen`/`neon`/`carbon`).

### Fixed

- Fixed `create_gem()` (`src/services/gems.py`) to forward `""` instead of `None` when `description` is omitted, and updated `gemini-web-cli` `_extract_domain_payload` (`src/cli.py`) to mark legacy `"❌ ..."` tool responses as `ok=False` with non-zero exit code.
- Fixed detached `operation_worker` image save (`src/operation_worker.py` pre-init `GEMINI_PSID` + `notify=False`, `src/services/media_storage.py` in-memory `get_extra_cookies()` + bounded Keychain / `Network/Cookies` 403 fallback, and wired Gemini 4 `mode=14` `apply_image_companion_options` in `src/infrastructure/rpc_contracts.py` & `src/thinking_client.py`).
- Added fast-fail handling for `_ResearchPlanRejection` / canned Deep Research refusals (`UPSTREAM_REJECTED`), `"Since direct audio/music generation isn't available right now..."` music refusals (`src/constants.py`, `src/data/native_media_browser.js`), Gemini 4 music genre companion option IDs (`44..57, 68` in `src/infrastructure/rpc_contracts.py`), and specific failure messages in `_operation_read_result` (`src/services/operations.py`).
- Separated `_MAX_MEDIA_ARTIFACT_POLLS` (`image`: 4, `music`: 12, `video`: 15) from `_STATIC_FAILURE_PHASES` and removed unconditional `partial` cleanup on `media_resume` in `src/operation_worker.py` so long-running music/video generations are not deleted before completion.
- Updated `native_media_browser.js` and `native_media_browser.py`: added direct `media_tool` check in `open_tools_for` before hovering `more`, expanded `editors()` placeholders (`问问 Gemini`, `描述视频`, `描述要创作的音乐`, `描述任务`, `[role="textbox"]`), added fast `INVALID_ARGUMENT` rejection for disabled video controls (`video_option_target_disabled` / `video_option_menu_disabled`), relaxed `hasMusicControls()` to not require `Genre`/`流派` button presence, and forwarded signal messages into `NativeMediaObservation.error_message` / `NativeMediaGenerationError`.
- Added `_recover_exact_chat_response()` in `src/services/media_jobs.py` to recover missing `rid`/`rcid` from single-turn `read_chat(cid, limit=10)` before reporting `media_identity_incomplete`.
- Added `__Secure-OSID`, `S`, `SNID` cookie handling (`src/cookie_manager.py`), cache clearing when `GEMINI_COOKIE_PATH` is explicitly configured, and one-shot browser cookie hot-reload in `ClientManager.initialize()` (`src/client_manager.py`).
- Raised `MAX_RESULT_WAIT_SECONDS` to `300` with modality-aware `wait_seconds` defaults (`120` image, `240` music, `300` video, `0` immediate) on `gemini_create_media`, and disabled paid `gemini-omni-1.2-flash` (`GEMINI_API_KEY`) route by default with `INVALID_ARGUMENT`.
- Hardened `init_startup_infrastructure()` (`src/client_wrapper.py`) to tolerate `ClientRateLimited` on startup without crashing MCP servers, wrapped `gemini_account_inventory` (`src/tools/manage.py`) with structured `exception_text` error responses, and hardened `gemini-web-cli` (`src/cli.py`) with stdout->stderr isolation, SDK wrapper unwrapping, and a 900s `.cli_browser_session.json` session cache.

## [0.20.8] - 2026-11-23

### Fixed

- Released `cleanup_protected` and finalized remote chat deletion at worker retirement (`src/operation_worker.py`) for non-retained terminal failed/cancelled/unavailable media operations, unrecoverable `media_identity_incomplete` partials, and exhausted `media_resume` partials while preserving `retain_chat`, `RATE_LIMITED`, and fresh recoverable partial protection.
- Updated `repair_operation_cleanup` (`src/services/background.py`) to release stale protected `partial` and dead `running` media rows past the 1800s recovery TTL and expedite overdue unprotected `PENDING` cleanup rows (such as `WORKER_START_FAILED`), healing stuck `media_job:in_progress` backlogs on subsequent drain ticks.
- Hardened `CleanupStore.schedule` (`src/infrastructure/cleanup_store.py`) to tighten earlier `delete_at` deadlines on non-leased pending rows via `ON CONFLICT DO UPDATE` without pushing deadlines later or overriding `retained`/`completed`/leased rows, and prevented double-incrementing `attempts` per failed cycle.
- Extended per-account `_media_account_lock` (`src/services/media_jobs.py`) to serialize concurrent `"image"` generation alongside `"video"` and `"music"`, removing the need for external caller-side file locks during parallel batch runs.
- Added checkpoint identity fallback in `media_jobs._save_response` when a response carries verified media but drops trailing metadata (preserving strict rejection checks for text-only replies), and added bounded transient `OSError`/`ConnectionError` readback retry in `recover_media_job` (keeping `asyncio.TimeoutError` and single-shot `None`/mismatch semantics intact).
- Ensured SDK retry suppression (`current_retry=0`) in `src/services/media_generation.py` whenever CID observation/checkpointing is active to avoid duplicate stray conversations.
- Enabled SQLite WAL mode + 30s busy timeout and added `OperationStore.reap_stale_workers` (`src/infrastructure/operation_store.py`, `src/services/operations.py`), plus thread-safe `BoundedSemaphore(2)` and race-free `os.environ.copy()` worker dispatch in `src/services/background.py`.

## [0.20.7] - 2026-11-22

### Fixed

- Recognized the narrowly observed Chinese program-limitation refusal notice in both service and native browser signals so exact completed-empty video replies finalize as `UPSTREAM_REJECTED` instead of remaining indefinitely pending, while preserving reply/account/lease fences and unknown/pending/URI/media guards.

## [0.20.6] - 2026-11-22

### Fixed

- Verified unique model/thinking menu rows and closed/open transitions, allowing one exact stale reset while blocking unknown shapes, dialogs, and visible panels; re-select media tools only on positive `tool_unselected` evidence.
- Bounded empty-editor refocus and pre-submit native reopen to one guarded attempt under the original browser deadline (`submitted is False`, no observed reply identity or media, unchanged owner, at least 60s remaining), never retrying after Send or upstream rejection.
- Kept Low/medium/high thinking levels explicit and preserved native Web `standard` defaults without automatic High-to-Low downgrade.
- Required strict completed-empty exact-turn evidence before classifying generic failures as `UPSTREAM_REJECTED` and releasing cleanup protection.
- Included documentation JSON sidecars in source distributions so bundled verification-report links resolve.
- Retired owned worker leases in the `finally` path even when cancelled heartbeats fail or cleanup-proof reads raise.

## [0.20.5] - 2026-11-22

### Fixed

- Classified known NoTool notices, complete ABC-score fallbacks, and verified technical error cards only with exact completed replies and strict empty-media evidence, protecting quoted/code refusal examples from premature DOM signals.
- Stopped local observation once exact terminal proof is available without cancelling or replaying remote generation, preserving handles, owner bindings, and build identities through recovery.
- Reconciled verified owned Web cleanup with original-snapshot CAS before dispatch, worker observation, or recovery while excluding protected, retained, foreign-account, and API operations.

## [0.20.4] - 2026-11-22

### Fixed

- Preserved actual browser Cookie attributes instead of forcing every Cookie `HttpOnly`, and forwarded completed Google-verification Cookies only for the exact allowed root domain/path, non-expired value, and authenticated owner.
- Classified HTTP 429 responses as `RATE_LIMITED` with media-scoped quiet backoff, surfacing swallowed media readback limits without adding automatic SDK retries.
- Bounded read-only authentication network retries by the original candidate deadline and isolated worker SDK Cookie caches from parent/user caches.

## [0.20.3] - 2026-11-22

### Fixed

- Routed native Web music and video generation through the real browser composer / ProcessSession workflow, preserving the original user prompt and verifying model/options before one Send while keeping explicit SDK, image, and paid API paths separate.
- Preserved optional `music_duration` (`short` / `standard`) and `music_vocals` (`instrumental` / `vocals` / `auto`) controls, leaving Web defaults untouched when unspecified and rejecting unsupported controls on explicit SDK transport before dispatch.
- Verified single local video reference attachments before Send and reused one protected operation across inline waiting, opaque filenames, original-handle recovery, and verified local saves.
- Rechecked captured account identity after awaited reads before checkpointing reply metadata and retained the artifact-first canonical cleanup contract.

## [0.20.2] - 2026-11-22

### Fixed

- Supported reading both historical PBKDF2 and current SHA-256 account scopes when derived from the same authenticated credential, failing closed on ambiguous or cross-account claims without rewriting existing SQLite ledgers.
- Preserved stored ownership, cleanup leases, retained protection, browser-action deduplication, and schedule guards across account-hash formats.

## [0.20.1] - 2026-11-22

### Fixed

- Resolved current-account Hydrogen, Neon, and Carbon model selectors from the account registry and verified plan, keeping explicit selectors intact and refusing unavailable legacy or ambiguous matches across distinct Chat, Task, and media gates.
- Aligned SDK image/video/music modes and provider-declared companion choices (including Web video duration, aspect ratio, and format) with observed frontend contracts.
- Persisted actual Task selectors independently of requested labels and classified initialization errors without weakening worker/account fences.

## [0.20.0] - 2026-11-20

### Added

- Added background Task approval visibility and opt-in one-time permission grant submission for MCP-owned Tasks (with separate opt-in for website-created Tasks).
- Added exact-match preapproved Google Tasks deletion card authorization with persistent claim deduplication.

## [0.19.0] - 2026-11-20

### Added

- Accepted ordered local `image_paths` references for background image creation and editing.
- Added follow-up image editing from earlier verified operations via `source_operation_id` and `source_artifact_id`, recording source lineage.

### Fixed

- Re-verified source image identity, account ownership, saved bytes, and decoding before upload, preserving lineage across restarts and source-chat cleanup.

## [0.18.0] - 2026-11-20

### Changed

- Streamlined default `gemini-mcp-function` and `gemini-mcp-account` entrypoints to six focused tools each, retaining the prior 17-tool catalogs under explicit `function-full` and `account-full` profiles.
- Consolidated ordinary chat, typed inputs, and session turns under `gemini_ask`, and background image/edit/video/music submission under `gemini_create_media`.
- Bounded default account Task reads to list/get/progress and placed verified Notebook chat moves in `gemini_manage_chat`.

### Fixed

- Rejected undeclared tool arguments before dispatch while preserving text and structured MCP result compatibility.

## [0.17.8] - 2026-11-13

### Docs

- Added `llms.txt` and generative engine FAQ sections for AI discovery (`6702644`).
- Clarified remote chat retention defaults and session max-age behavior across documentation (`10760d1`).

## [0.17.7] - 2026-11-06

### Fixed

- Bounded session sends and automatic exact-turn recovery under a single shared 300-second deadline, preserving pending turns for later recovery when the budget expires (`bbf8652`).

## [0.17.6] - 2026-11-06

### Fixed

- Returned typed upstream rejections when Chat, search, or understanding yields an exact recognized generation failure notice while avoiding false positives on quoted notices (`4e0becd`).

## [0.17.5] - 2026-11-06

### Fixed

- Bounded single-session reset waits for in-flight sends to 30 seconds while keeping the original session and pending cleanup recoverable on timeout (`4e0becd`).

## [0.17.4] - 2026-11-06

### Fixed

- Decoded saved image bytes before reporting verified artifacts and checked the optional image decoder before upstream initialization (`4e0becd`).

## [0.17.3] - 2026-11-06

### Fixed

- Preserved discovered model selectors when encoding thinking levels across supported SDK capacity layouts and kept body/header thinking selection aligned (`19b4245`).

## [0.17.2] - 2026-11-06

### Changed

- Avoided redundant write locks on initialized operation databases and redundant report re-verification during uninterrupted reads (`0548968`).

## [0.17.1] - 2026-11-06

### Fixed

- Verified exact conversation deletion for the originating account (including Notebook chats omitted from ordinary history lists) and required independent Task-goal absence while respecting associated schedules (`00d7c42`, `fa1e852`).
- Ended Task pagination cleanly at an empty final cursor and returned usable Task entries alongside unaddressable-entry counts (`4cbffc6`).

## [0.17.0] - 2026-11-06

### Added

- Added opt-in explicit API-backed **Gemini Omni 1.2 Flash** video generation via `GEMINI_API_KEY` and the Interactions API (3–15s duration, restart-safe retrieval, verified MP4 output, and automatic interaction cleanup) (`bbf8652`).

### Changed

- Extended Gemini account tools and trimmed MCP payload verbosity (`f395991`).
- Kept Cookie-authenticated video on Gemini Web's native Create video route and reported transport/backend separately (`bbf8652`).

## [0.16.0] - 2026-11-01

### Added

- Saved exact completed Task and Notebook Chat answers and verified Task attachments before scheduling owned-chat cleanup (`d71736d`).
- Added Task browser previews and frame-bound one-shot browser input with idle/shutdown connection cleanup, plus native Task schedules and persistent browser/action preferences (`d71736d`).

### Fixed

- Recovered original Tasks and saved answers across process restarts while keeping uncertain work protected (`d71736d`).

## [0.15.5] - 2026-10-28

### Changed

- Consolidated server startup lifecycle and Cookie infrastructure initialization across entrypoints and extracted shared tool-registry helpers (`2ccf447`, `762b4ba`, `a02d987`).

## [0.15.4] - 2026-10-28

### Fixed

- Added cross-platform browser credential discovery across macOS, Windows, and Linux with preferred-browser priority and fallback (`cf8dcb5`).

## [0.15.3] - 2026-10-24

### Fixed

- Auto-initialized `CookieManager` and browser Cookie discovery on compact `skill_server` startup (`ee722c7`).

## [0.15.2] - 2026-10-24

### Changed

- Layered tool surfaces and scoped infrastructure/management tool registration to requested profiles (`dac5aba`).

## [0.15.1] - 2026-10-18

### Fixed

- Aligned public-link listing and native Remy schedule read-back with observed RPC shapes (`da76156`).
- Resolved agent instruction and skill conflict markers and deduplicated skill discovery aliases (`3dafa0f`, `02b17f0`).

## [0.15.0] - 2026-10-18

### Added

- Added native Gemini account workflows: Notebook CRUD/sources, Remy schedules (six recurrences), native Skill management with ZIP import, Canvas creation/versions/export/replacement, Library asset reads/removal, public-link CRUD, typed Drive/Photos/NotebookLM references, and exact chat rename/pin/unpin (`e7b5f8f`).

### Fixed

- Verified account writes against exact targets and rejected incomplete inventories as proof of empty state or deletion (`e7b5f8f`).

## [0.14.4] - 2026-10-18

### Fixed

- Made cleanup-lease heartbeat regression tests deterministic using persisted lease renewal and controlled time advancement (`5868296`).

## [0.14.3] - 2026-10-17

### Fixed

- Retained unfinished media sources when history text reported completion without a matching media card, preserving queued/partial artifact locators (`04b319e`).

## [0.14.2] - 2026-10-17

### Fixed

- Persisted requested output filenames across interrupted operations and declared local save / source cleanup effects in Create and Research annotations (`f6ab22c`).

## [0.14.1] - 2026-10-03

### Fixed

- Bounded concurrent first-time SQLite initialization retries under a single three-second busy budget (`2b497ec`).

## [0.14.0] - 2026-10-03

### Added

- Added focused Create and Account entrypoints with typed results and durable metadata-only SQLite operations (opaque handles, idempotency keys, claims, renewable leases, cancellation, and verified artifact locators) (`576e573`).

### Fixed

- Preserved original operation identity across interrupted calls and restarts (`576e573`).

## [0.13.1] - 2026-10-02

### Fixed

- Retained observed source conversation IDs across SDK failure/rollback and normalized media failure categories without leaking arbitrary SDK prose (`985e8f8`).

## [0.13.0] - 2026-10-02

### Changed

- Aligned native image and music generation with observed Web composer modes, sharing primary and compact generation paths and saving verified local artifacts by default (`dd5780b`, `ce21e6e`).

### Fixed

- Validated output modality, byte integrity, image decoding, and audio/video streams; derived MIME/filenames from real output and prevented hidden SDK replays (`dd5780b`).

## [0.12.3] - 2026-08-23

### Fixed

- Required per-input reply acknowledgment before marking mixed understanding inputs as analyzed, skipping empty files and preserving inline spacing (`bad80ed`).

## [0.12.2] - 2026-08-23

### Changed

- Consolidated Doctor diagnostics and split management registration, compact history, Research execution, and creation result handling into shared services (`fb11c58`, `0cf9601`).

## [0.12.1] - 2026-08-23

### Fixed

- Supported SDK `READ_CHAT` / `LIST_CONVERSATION_TURNS` enum layouts and hardened source URL filtering and deduplication (`f43df2d`, `41da032`).

## [0.12.0] - 2026-08-23

*(Corresponds to tagged release `v0.2.1` on 2026-08-23; see [docs/version-history.md](docs/version-history.md#021---2026-08-23).)*

### Added

- Introduced focused `gemini-mcp-assist` surface (Ask, grounded search, image/screenshot understanding, up to 16 mixed typed inputs, and asynchronous Deep Research) plus the standalone `gemini-assist` Skill (`fcc4493`, `05167db`).

## [0.11.3] - 2026-08-13

*(Corresponds to tagged release `v0.2.0` on 2026-08-13; see [docs/version-history.md](docs/version-history.md).)*

### Fixed

- Hardened Research source-host URL parsing to match exact Google asset hostnames (`f0c738c`).

## [0.11.2] - 2026-08-13

### Fixed

- Packaged AGPL-3.0-only license metadata/text in wheel and source distributions and enforced package/Skill/banner version consistency (`e090cf6`, `4341253`).

## [0.11.1] - 2026-08-09

### Fixed

- Bounded macOS browser credential lookups to prevent Keychain prompts during offline checks and required positive read-back for deletion (`f6819ee`).

## [0.11.0] - 2026-08-03

### Added

- Added credential-free `gemini-mcp-onboarding`, four client configuration examples, opt-in live compatibility canary with sanitized reports, and CI wheel/sdist/Skill/license verification (`50c5a31`, `a73db59`).

## [0.10.0] - 2026-08-03

### Changed

- Migrated from FastMCP to a project-owned adapter over official MCP Python SDK v2 (`mcp>=2,<3`) with `outputSchema`, `structuredContent`, and modern (`2026-07-28`) + legacy (`2025-11-25`) protocol negotiation (`957b811`).

## [0.9.2] - 2026-08-03

### Fixed

- Aligned compatibility text error-code prefixes with attached typed failures and exported the shared failure renderer (`12119d0`, `de93849`).

## [0.9.1] - 2026-08-03

### Changed

- Shared conversation retention metadata and cleanup state across primary and compact chat/session paths (`a68eafd`).

## [0.9.0] - 2026-08-02

### Added

- Introduced typed domain results/errors, stable artifact identities, and centralized evidence-backed management RPC contracts and parsers (`f08649b`, `e814246`, `60e257f`).

## [0.8.0] - 2026-08-02

### Fixed

- Shared asynchronous client initialization across callers with cancellation isolation, opaque session handles, serialized sends, and separate single vs. all-session reset (`19aea1a`, `fd40357`).

## [0.7.1] - 2026-08-01

### Fixed

- Capped legacy FastMCP dependency at `mcp<2` ahead of the SDK v2 migration and aligned runtime version banners (`28681e7`).

## [0.7.0] - 2026-07-18

### Changed

- Split compact-server account, schedule, session, and Research handlers and aligned Skills with progressive-disclosure conventions (`573edde`).

### Fixed

- Protected client and Prompt manager initialization with locks and expanded lifecycle regression tests (`573edde`).

## [0.6.1] - 2026-07-04

### Added

- Added unified release packaging for wheel, source distribution, and standalone Skill ZIP (`ad3177f`).

## [0.6.0] - 2026-07-04

### Added

- Added the public runtime Skill directory, GitHub installation guide, and consistency checks (`20a44ea`).

## [0.5.1] - 2026-07-04

### Fixed

- Validated local attachments before dispatch, scheduled compact-path cleanup, and preserved omitted fields during partial Gem updates (`9162126`).

## [0.5.0] - 2026-07-04

### Added

- Added native Notebook listing and verified chat moves, daily scheduled actions (separate read/admin profiles), multi-source history, and Research report-derived content workflows (`7ead82c`).

## [0.4.0] - 2026-06-27

### Added

- Added history search/read/export, account inventory with bounded pagination, browser profile selection, Doctor diagnostics, dry-run cleanup, and MCP privacy/mutation annotations (`1b3d017`, `f949c3c`).

## [0.3.0] - 2026-05-23

*(Local scope milestone; distinct from the published mainline `v0.3.0` GitHub release of 2026-11-22 archived in [docs/version-history.md](docs/version-history.md#030---2026-11-22).)*

### Changed

- Made `core` the default profile, kept local Prompt management opt-in, aligned model aliases and image/music routing with Gemini Web, and separated thinking from model selection (`e1da89c`).

### Removed

- Removed the low-value feature enumeration tool (`e1da89c`).

## [0.2.2] - 2026-05-17

*(Local scope milestone; distinct from tagged release `v0.2.2` on 2026-10-25 archived in [docs/version-history.md](docs/version-history.md#022---2026-10-25).)*

### Fixed

- Deduplicated requested tool-group expansion and validated file paths/URLs locally before remote client initialization (`c2c2f53`).

## [0.2.1] - 2026-05-10

### Fixed

- Corrected early Pro model selector/capacity settings and restored the `image` module import compatibility alias (`d6ad449`).

## [0.2.0] - 2026-05-05

### Added

- Modularized chat/session, media, file/URL analysis, Deep Research, and management server entrypoints (`c17eed6`, `47f9c57`).

## [0.1.0] - 2026-05-05

### Added

- Initial `gemini-web-mcp` package scaffold, basic Gemini Web authentication, and conversation tools (`2ee01c7`, `323d738`).
