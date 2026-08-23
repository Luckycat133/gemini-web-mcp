# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- Added the first focused product surface `gemini-assist`: the `gemini-mcp-assist` console entrypoint runs the `gemini_assist_mcp` server with a deterministic five-tool catalog — `gemini_ask`, `gemini_search`, `gemini_understand_image`, `gemini_understand`, and `gemini_research` — for second opinions, grounded current-web search, image/screenshot understanding, typed mixed-input understanding, and asynchronous Deep Research.
- Added the truthful grounded-search contract: `grounding_state` is `grounded` only with observed source URLs, and source-free answers are reported as `answer_only` (with `unavailable` and `failed` as distinct states); reported sources are deduplicated, capped, and counted.
- Added typed mixed-input understanding: `gemini_understand` accepts up to 16 typed inputs (text, image, file, URL) with stable caller-supplied ids and per-input `accepted`/`analyzed`/`skipped`/`failed` outcomes; `analyzed` requires per-input evidence (a referenced `[id]` or a sole accepted input) rather than request-level inference.
- Added asynchronous Deep Research start: `gemini_research` returns an opaque high-entropy `operation_id` handle immediately, preserves `upstream_operation_id`/`upstream_chat_id` in structured metadata, and retains the research chat by default so the report stays recoverable.
- Added the `gemini-assist` runtime Skill with an intent-based trigger description, offline trigger-boundary content contracts (positive assistance intents plus generation/account/development near-miss negatives), and packaging as a required `gemini-assist-skill-*.zip` release asset validated by CI and release workflows.
- Added the assist entrypoint to the real stdio protocol smoke (modern and legacy negotiation modes) with a credential-free representative `gemini_search` call.

### Changed
- Extracted the shared Deep Research start-phase workflow into `src/services/research.py`; the compatibility `gemini_deep_research` tool now reuses it with unchanged behavior, removing the duplicated start orchestration and the drifted timeout literal.
- Declared `pydantic` as a direct runtime dependency to match the new typed `gemini_understand` input schema (`maxItems: 16`) and the repository dependency contract.
- Updated the compatibility `gemini-web-mcp` runtime Skill to route pure assistance workloads to the focused `gemini-assist` Skill and to state that `gemini-create` and `gemini-account` are not implemented yet.
- Finished the oversized-function cleanup: exception classification is now an ordered rule table, deep-research native execution and report-artifact creation delegate to phase helpers, the history deep-scan tool extracts notebook collection/source-summary/markdown rendering, skill-server create/edit share media result assembly, deletion verification payloads come from one builder, local-artifact probes split out, and chat registration is grouped into conversation/session/streaming helpers; no single-responsibility function exceeds 100 lines and no MCP tool surface changed
- Consolidated doctor diagnostics: `services/doctor.py` owns the split payload implementation (static/cookie/profile/environment/recommendation steps behind injectable providers), and manage.py's byte-equivalent private twin was deleted in favor of the service exports
- Split the remaining oversized tool bodies without behavior change: `gemini_generate_media` (260 lines) into a request-context dataclass with failure/recovery/outcome/finalization helpers, `gemini_manage_prompts` (124 lines) into six per-action helpers, and the upload/URL analysis tools onto shared response-assembly and failure-response helpers; the service cleanup payload gained the same two-phase split as its manage twin
- Deduplicated `src/tools/manage.py`: 14 helpers byte-equivalent to `services/history` / `services/account` implementations are now imported instead of re-implemented (~130 lines removed); behavior verified by the typed-parity contract
- Split `register_manage_tools` (1563 lines) into a shared conditional decorator factory plus 12 domain-scoped registration helpers called in the original order; in-process tool delegation resolves through an explicitly passed registry with precise typing
- Restructured oversized functions without behavior change: doctor/cleanup payload builders split into step helpers, research deep-research helpers hoisted to module level, and skill-server history tool split into per-action helpers; no MCP tool name, schema, annotation, or response changed

### Removed
- Deleted the legacy `docs/release-notes-2026-05-23.md` snapshot (fully superseded by this changelog) and its documentation-index link
- Removed the external `python-mcp-server-generator` scaffolding skill from `.agents/skills/`, its `.claude/` and `.codex/` discovery symlinks, and its `skills-lock.json` entry; it generates new MCP servers and was unused by this repository

### Fixed
- Hardened research source extraction: percent-encoded gstatic icon paths (e.g. `/fav%69con.ico`) can no longer bypass the favicon filter, non-domain hostnames (IPv6 literals, embedded whitespace, invalid ports) are rejected, blank source titles are dropped, and duplicate sources are detected on a normalized scheme/host/default-port/path/query key instead of raw URL strings

## [0.2.1] - 2026-08-21

### Changed
- Kept the existing `v0.2.0` tag immutable and advanced the active Python package, runtime Skill, development Skill, release assets, and current documentation to `0.2.1`.
- Recorded the owner-approved development order: first establish a complete live Gemini baseline using maintainer-supplied account Cookies, then implement the shared long-operation service.
- Settled local persistence on SQLite for long-operation recovery and delayed cleanup; the database remains local-only and stores recovery metadata rather than prompts, chat text, Cookies, or raw upstream responses.

### Fixed
- Added the repository AGPL-3.0-only license to wheel and source-distribution metadata and made release verification compare embedded license contents with the repository source.
- Parsed Deep Research source URLs before matching exact Google asset hostnames, preventing path, userinfo, and lookalike-domain text from being mistaken for Google-hosted resources.
- Refreshed the development Skill and status documentation so completed foundations are not repeatedly re-planned and settled owner decisions are no longer presented as open questions.

## [0.2.0] - 2026-08-13

### Added
- Added the complete root `LICENSE` text for AGPL-3.0-only and made wheel/source-distribution checks verify both PEP 639 metadata and the embedded license file without changing the runtime Skill's separate MIT-0 license
- Prepared the unified ClawHub `0.2.0` release after its security audit: explicitly classified locally cached browser Cookies as sensitive account-authentication material, required approval and secure cache handling, scoped session clearing away from agent memory/instructions, and clarified that macOS browser authorization does not scan arbitrary credential files
- Prepared the three-file runtime skill for the unified ClawHub `0.2.0` release with explicit OpenClaw dependency and optional environment metadata; that ClawHub bundle is MIT-0 while the server source and development skill remain AGPL-3.0-only
- Added shared typed history list/search/read/export/delete results across primary and compact adapters, including one-time object/mapping normalization while preserving compatibility text
- Added chat-delete read-back states for verified absence, unavailable verification, still-present records, and read-back errors; only a complete fresh recent/pinned metadata scan can prove absence
- Added a dated development status page that separates implemented repository contracts, partial work, unobserved live behavior, deferred UI parity, and owner decisions

### Changed
- Unified the active Python package, runtime Skill, development Skill, runtime banners, release asset names, and documentation at `0.2.0`; all rewritten Git history and changelog release headings use the canonical `0.2.0` version
- Made `.agents/skills` the single repository skill source and updated CI, release, packaging, and documentation checks to install and validate that source directly
- Changed chat deletion so an accepted upstream call without read-back evidence is reported as accepted/unverified instead of verified success
- Stopped treating `read_chat(None)` as deletion proof because the dependency also uses `None` for incomplete or failed reads
- Updated the GitHub repository description and public launch copy to match the MCP Python SDK v2, agent-first gateway architecture
- Recorded the 2026-08-08 explicitly authorized targeted live result separately from the still-inactive dedicated-account canary, including primary/compact history parity and verified cleanup evidence
- Updated test-cleanup guidance to retain every returned remote chat ID because Gemini-generated titles may omit prompt markers; marker cleanup remains a bounded fallback and turn scanning remains opt-in

### Removed
- Removed the stale `fastmcp` GitHub topic and added `mcp-python-sdk` to match the current SDK v2 implementation
- Removed duplicate `.codex/skills` copies that caused clients scanning both discovery roots to list the runtime and development skills twice

### Fixed
- Parsed Deep Research source URLs before matching exact Google asset hostnames, so path, userinfo, and lookalike-domain text cannot be mistaken for `gstatic.com` or `googleusercontent.com` resources
- Made the compact stdio protocol smoke call the auth-free static account manifest instead of browser-profile doctor diagnostics, preventing macOS Keychain prompts in offline verification
- Made `GEMINI_AUTO_REFRESH=false` skip the Cookie monitor thread instead of starting an idle background monitor during offline and CI runs
- Bounded `browser-cookie3` macOS Keychain waits, restored the dependency reader after each call, and returned a sanitized `BROWSER_COOKIE_ACCESS_TIMEOUT` instead of hanging indefinitely

### Public Distribution and Onboarding
- Added `gemini-mcp-onboarding`: its default command installs/runs cleanly without Gemini credentials, launches the real stdio server, negotiates MCP, and calls the static text manifest; live chat and image examples require `--allow-live-account`
- Added independently verified local image onboarding artifacts with path-boundary, existence, non-zero-size, MIME, dimensions, and structured verification checks; fixed image saves returned as a single path so they enter the shared artifact model
- Added copyable Codex, Claude Desktop, Claude Code, and VS Code configurations with `model` as the text starting profile, `core` for multimodal workflows, and explicit compact/`all` guidance
- Replaced broken fixed wheel onboarding URLs with the current Git source path (and commit-pinning guidance), while retaining version checks for any tagged wheel URL that is documented
- Separated the runtime and repository-development skills, documented one-command installation for both, and added CI verification that the development skill installs directly from the repository
- Added clean `uvx` wheel onboarding smoke coverage to CI and tag releases plus offline config, credential-boundary, stdio, artifact, package, and distribution contracts
- CI, fixture, package, and release evidence remains offline unless a dedicated live-canary report is explicitly cited; current Gemini behavior is never inferred from those gates
- A separate 2026-08-08 authorized local run observed text, session, history, and delete behavior, but it was not recorded as a dedicated-account full canary and made no media, research, or account-mutation claim

### Live Gemini Web Compatibility Canary
- Added a separately gated weekly/manual workflow for a dedicated test account; PR, unit, package, protocol, and release gates remain offline
- Added 21 centralized read-only capability probes with explicit transport, envelope, RPC rejection, parser, and completion stages, plus bounded timeouts and sanitized exception codes
- Added a strict JSON report schema and synthetic fixtures that retain dependency versions, commit, Web build/locale when available, RPC identifiers, capability state, and parser stage while excluding raw responses, account content, credentials, and session identifiers
- Added a machine-readable upstream dependency matrix synchronized with `pyproject.toml`; this change is fixture-verified only and did not observe live Gemini behavior
- Added workflow automation that uploads the sanitized report, opens or updates one actionable compatibility issue before failing on drift/operational failure, and closes it after recovery
- Added refusal-path, schema allowlist, initialization cleanup, exception sanitization, synthetic response-stage, matrix, registry, and workflow contracts; the full offline suite now passes 1323 tests

### MCP Python SDK v2 Adapter
- Migrated the runtime to the official `mcp>=2,<3` and `mcp-types>=2,<3` packages through the project-owned `src/adapters/mcp_sdk.py` boundary, replacing removed FastMCP imports without changing domain services
- Adopted SDK v2 `MCPServer`, snake_case Python model fields, complete `CallToolResult` values, generated `outputSchema`, validated `structuredContent`, `resultType`, and `server/discover` lifecycle behavior while retaining existing text content
- Reworked the real stdio smoke to list and call representative tools through the v2 high-level client in both `auto` (`2026-07-28`) and `legacy` (`2025-11-25`) modes across both console entrypoints
- Added reviewed golden fingerprints for primary-model and compact tool lists, input/output schemas, and annotations, plus cross-client schema validation against actual offline results
- Documented the current Codex, Claude Desktop, and VS Code protocol paths and set project support for the legacy SDK-v1 compatibility track to end on **2027-01-31**
- Added six SDK v2 adapter, discovery, legacy negotiation, golden, and structured-result tests; the full offline suite now passes 1299 tests

### Stream and Long-Operation Semantics
- Kept the compatibility `_stream` tool names while documenting that Gemini upstream chunks are normalized and collected into one MCP result; added explicit `delivery=collected` metadata instead of claiming MCP incremental delivery
- Added stateful delta/cumulative/mixed chunk normalization so repeated or stale cumulative text is not duplicated, with public chunk and deduplication diagnostics
- Added Deep Research `wait_for_completion`, typed queued/running/completed/timed_out results, preserved research/chat continuation IDs, and running state for plan-only fallback responses
- Added strict cancellation/deadline handling that propagates caller cancellation and prevents a cancellation-suppressing late result from replacing a timed-out state
- Updated the canonical public skills so agents consume collected stream metadata and resumable Deep Research states correctly
- Added offline stream, cancellation, late-completion, continuation-ID, and long-operation state regression coverage; the full offline suite now passes 1293 tests

### CI and Release Gates
- Split CI into separate Ruff, Mypy, Python 3.11/3.12 test, targeted contract, protocol smoke, Agent Skill, and installed-package jobs with pip caching and focused failure steps
- Added a stable architecture contract checklist plus exact tool-name snapshots for six primary profiles and the compact surface
- Added real MCP stdio `initialize`/`tools/list` smoke checks for both console entrypoints without Gemini credentials or live model calls
- Added pinned `skills-ref` validation for both public skills and direct-install byte-parity checks against the single development-skill source
- Added clean-wheel resource/entrypoint/profile/protocol verification and a tag workflow that revalidates downloaded assets before creating a GitHub Release

### Dependency and Package Integrity
- Declared `orjson` directly, bounded the tested `gemini-webapi`/MCP major lines, bounded optional browser/image extras, and removed the unused standalone `fastmcp` dependency
- Added an AST-based dependency contract check so new runtime third-party imports cannot rely silently on transitive dependencies
- Moved the default prompt catalog into `src/data` and switched the compact server to `importlib.resources`, making defaults available from wheels and non-filesystem importers
- Added explicit `gemini-mcp-server` and `gemini-mcp-skill-server` console entrypoints plus a clean-install smoke that verifies package origin, prompt data, tool listing, stdio startup, and prompt initialization

### Single Version Source
- Made `pyproject.toml` the only persisted project-version source; both MCP server banners now read installed distribution metadata through `src.__version__`
- Added a repository consistency check for stale product-version references and release wheel URLs while excluding the historical changelog
- Made release packaging validate an explicit or tag-build Git tag, deterministic wheel/sdist/skill asset names, and wheel `Name`/`Version` metadata before reporting success
- Removed obsolete product-version labels from non-historical documentation and documented the single-source release workflow

### Management Domains and RPC Contracts
- Added an evidence-backed RPC registry in `src/infrastructure/rpc_contracts.py` for management capability IDs, source paths, payload builders, parser names, observed dates, stability, and mutation verification strategies; active handlers and cookie profile probes no longer duplicate raw RPC IDs
- Added pure RPC envelope/body parsers with explicit `success`, `empty`, `rejected`, and `changed_shape` outcomes plus fixture coverage for every registered parser
- Split shared history, account, Notebook, scheduled-action, Gem, manifest, doctor, and cleanup behavior into `src/services/`; primary and compact scheduled mutations now use one implementation
- Made `src/tools` registration lazy so importing `src.skill_server` does not load the `src.tools.manage` compatibility monolith
- Added read-back verification to Notebook moves, scheduled create/delete, and Gem create/update/delete; an accepted mutation response is no longer reported as verified without observing the target state
- Added RPC registry/parser and mutation-service regression tests; the full offline suite now passes 1247 tests

### Unified Artifact Model
- Added shared `Artifact` and `ArtifactResultData` contracts for image, video, audio, file, report, webpage, and data artifacts with explicit `remote`, `local`, `queued`, `empty`, and `failed` states
- Added deterministic artifact identities, common response extraction, remote/local observation merging, backend evidence, and local existence/non-zero/MIME/dimension/duration verification in `src/services/artifacts.py`
- Migrated primary media, file/URL analysis, research report creation, and compact create/edit outputs to `_meta.domain_result` while preserving legacy text and JSON response bodies
- Classified empty media, upstream queues, timeouts, write failures, verification failures, and remote-success/local-save partial outcomes without treating response prose as proof of a generated artifact
- Added shared artifact rendering plus offline tests for URI/file metadata, missing and empty files, queue/empty/failure states, timeout diagnostics, save failures, backend mismatch evidence, distinct MP3/MP4 identities, and primary/compact identity parity; the full offline suite now passes 1187 tests

### Shared Chat Service
- Added `src/services/chat.py` as the application boundary shared by the primary and compact MCP adapters for one-shot chat, streaming, session creation, and session sends
- Centralized model resolution, upstream request construction, client preparation, session fallback behavior, stream aggregation, cleanup policy, and typed `ChatOperationData` results while preserving each adapter's existing request shape
- Removed duplicated chat/session execution logic from `src/tools/chat.py` and the migrated compact handlers in `src/skill_server.py`; tool names, arguments, and legacy text remain compatible
- Added service unit tests and successful cross-adapter parity coverage for request construction, lifecycle state, missing sessions, cleanup fallback, streams, serialized runtime-object exclusion, structured metadata, delegation, and argument-schema compatibility; the full offline suite now passes 1166 tests

### Typed Domain Results
- Added generic `DomainResult`, `DomainError`, `DomainWarning`, and `ResultMeta` contracts with explicit operation state, retryability, suggested action, request ID, and diagnostic ID
- Added a stable error taxonomy for invalid input, authentication, missing sessions, unavailable capabilities, upstream rejection/drift, network/rate-limit/timeout/cancellation, artifact, verification, and internal failures
- Preserved current MCP tool names, return shape, and text while embedding the complete serializable contract at the first `TextContent._meta.domain_result`; callers no longer need to match prose or emoji
- Migrated the first client/session/chat slice across both primary and compact adapters; `SessionOperationResult` now specializes `DomainResult` while retaining its existing `session`, `response`, and `error_code` compatibility accessors
- Added public-safe exception classification and raw exception logging correlated by `req_<uuid>` and `diag_<uuid>` IDs
- Added contract, serialization, invalid/auth/timeout/upstream/internal regression, tool-name compatibility, runtime-object exclusion, and cross-adapter parity tests; the full offline suite now passes 1156 tests

### Session Lifecycle
- Unified the primary MCP tools and compact `skill_server` adapter on one `SessionService`; sessions created by either surface can be listed, sent to, streamed, and reset from the other
- Replaced predictable/reusable short IDs (`sess_N` and truncated UUIDs) with opaque `sess_<uuid4 hex>` IDs generated under the shared store lock with active-collision checks
- Added explicit `SESSION_NOT_FOUND` results for unknown send/reset operations; unknown IDs never fall back to one-shot chat and never clear unrelated state
- Made compact reset semantics unambiguous: `reset`/`reset_one` require a session ID and affect one session, while only explicit `reset_all` clears all sessions and retires the client
- Serialized normal and streaming sends per session; asynchronous single-session reset waits for an in-flight send before detaching state and applying the remote-retention policy
- Added cross-adapter, ID collision, unknown-ID isolation, concurrent-send, stream, reset-race, facade, and workflow tests; the P0.2 phase gate passed 1132 offline tests


### Skill Best Practices Alignment
- Aligned with agentskills.io specification: renamed `mcp-builder` `reference/` directory to `references/` (plural form required by spec), updated 9 links in SKILL.md
- Removed non-portable hardcoded paths `/Users/jack/...` from `gemini-web-mcp` SKILL.md, switched to spec-standard `skills-ref validate` validation command
- Added "when to use" trigger words to `python-mcp-server-generator` `description` (per spec anti-example requirements); added `license` and `compatibility` fields
- Added `compatibility` field to `gemini-web-mcp` (Python 3.10+ / .venv / Chrome cookies / launch commands)
- Added `gemini-web-mcp/references/tool_surface.md`: compact tool surface documentation organized by safety tiers (destructive/read private text/read-only discovery/chat+media/history metadata), referenced by SKILL.md on demand, following progressive disclosure
- Kept `.agents` and `.codex` skill copies in sync

### Dependencies & Configuration
- Bumped `gemini-webapi` dependency lower bound from `>=1.20.0` to `>=2.0.0` (silently upgraded during the early 0.2.0 history, retroactively documented in changelog): code deeply uses `types.RPCData`, `constants.GRPC`, `constants.Model`, `constants.Endpoint`, `constants.AccountStatus`, `types.video.GeneratedMedia`, `types.ModelOutput`, `utils.extract_json_from_response`, `utils.get_nested_value` and other APIs stable only in 2.x
- Synchronized `src/server.py` self-reported project version at `v0.2.0` (3 locations: docstring / FastMCP instructions / startup log), consistent with `pyproject.toml`
- Fixed `AGENTS.md` module inventory: removed deleted `auth.py`, added `remote_chat_cleanup_manager.py`, `thinking_client.py`, `error_handler.py`, and `tools/` modules `annotations.py`, `manifest_data.py`, `utils.py`
- Fixed all project wheel URLs at `v0.2.0` in `README.md` / `README.zh-CN.md` / `docs/launch-kit.md`; fixed README badge `tests-70` → `tests-118`; cleaned remaining `/Users/jack/...` hardcoded paths from `README.md`, switched to `skills-ref validate`

### Code Quality
- Deleted dead code `src/auth.py` (5 public functions, zero references across repository)
- Removed unused `load_images` function and unused imports
- Fixed `tools/__init__.py:register_tools` public API type annotations (`mcp: FastMCP`, `list[str] | None`, `-> None`)
- Cleaned `error_handler.py` unused imports, modernized types `Dict` → `dict`
- Fixed 3 silent import error swallowing in `cookie_manager.py` (`except Exception: return {}`) → specific exceptions + `logger.warning`
- Fixed 2 `client.close()` silent error swallowing in `cookie_manager.py` (`except Exception: pass`) → `logger.debug` to log close exceptions
- Added `_json_response()` helper in `manage.py`, replaced 23 instances of repeated `json.dumps(payload, ensure_ascii=False, indent=2)` pattern
- Extracted `_error_text(e, tool_name)` helper in `skill_server.py`, replaced 11 instances of repeated `logger.error + return [TextContent(text=f"Error: {e}")]` template
- Fixed `ClientManager.initialize` TOCTOU race: moved `if not self._initialized` check inside `_init_lock`, preventing concurrent coroutines from calling `client.init()` repeatedly

### Refactoring
- Split `skill_server.py` `account` god function (157 lines / 11 actions) into 11 independent async handlers + 2 dispatch tables (auth-free / client-based), dispatcher only 12 lines, preserving original semantics
- Split `skill_server.py` `scheduled` god function (4 actions: list/get/create/delete) into 4 independent async handlers + dispatcher, dispatcher retains only try/except + action dispatch
- Split `skill_server.py` `session` god function (4 actions: create/send/list/reset) into 4 independent handlers; `list`/`reset` don't need client, downgraded to sync handlers, client initialization retained only in `create`/`send`
- Split `research.py` `gemini_deep_research` (204 lines) into 35-line main function + 4 helper functions: `_run_native_deep_research` (client native plan/start/wait path), `_run_fallback_deep_research` (`generate_content(deep_research=True)` fallback path), `_deep_research_timeout_error`, `_deep_research_generic_error`
- Added lock to `_prompt_manager` singleton in `tools/prompts.py` and `skill_server.py` (`_prompt_manager_lock`), preventing concurrent MCP tool calls from creating multiple instances and overwriting JSON files

### Testing
- Added `test_skill_server_session_lifecycle_and_dispatch`: covers session 4 actions + invalid action short-circuit; uses FakeSession/FakeClient to verify single-session reset doesn't trigger client reset, reset_all does
- Added `test_skill_server_session_invalid_image_path_short_circuits`: verifies invalid image_path fails before client initialization
- Added `tests/test_error_and_session.py` (38 tests): full `error_handler.py` coverage (7 ERROR_CODES branches + handle_error string matching edge cases + format_error_response + GeminiError + wrap_tool_error); full `session_manager.py` coverage (store/get/remove/pop/list/clear + `_clean_expired_sessions` expiration logic + get/pop trigger cleanup); `extract_remote_chat_id` drift guard for two implementations (5 scenarios)
- Added `tests/test_skill_server_prompts_cookie.py` (8 tests): skill_server `prompts` (4 actions + invalid + missing parameter early exit) and `cookie` (3 actions + invalid + profiles list + empty profiles) had zero functional tests previously
- Added `tests/test_cookie_manager.py` (25 tests): `CookieManager` core lifecycle behavior coverage (`__init__` + `_load_initial_cookie` + `_load_extra_cookies_from_env`, `update_cookie` + `on_cookie_update` callback chain including exception swallowing, `get_cookie_status` VALID/EXPIRED/UNKNOWN state machine, `needs_refresh`, `refresh_cookie` three paths: no browser / browser success / browser failure, `to_env_vars`, `start_monitor`/`stop_monitor` start/stop idempotency + short-interval loop stability, `CookieData` defaults, `CookieStatus` enum stability, 4-thread concurrent `update_cookie` safety) — previously only browser candidate detection covered, callback chain/state machine/refresh paths had zero behavioral tests
- Added `tests/test_client_manager.py` (23 tests): `validate_config` / `get_configured_proxy` / `get_default_chat_retention_seconds` pure function boundary coverage (missing PSID throws error, locally unreachable proxy early exit, invalid retention fallback, 0/negative boundary); `prepare_browser_cookie_cache` 6 path coverage (force=False early exit / source non browser_ early exit / source=browser_ create+set env / force=True skip check / GEMINI_COOKIE_PATH mismatch early exit / clear stale cache files); `ClientManager` lifecycle coverage (get_client creates once / reset clears and rebuilds / initialize short-circuits when initialized / concurrent initialize doesn't call init repeatedly, verifies `_init_lock` TOCTOU fix)
- Added `tests/test_chat_session_lifecycle.py` (6 tests): `gemini_reset_session` (destructiveHint=True) 4 delete_remote_chat decision paths (session doesn't exist / retain_chat=False triggers delete / retain_chat=True skips / session has no cid passes None); `gemini_list_sessions` empty list and non-empty list rendering — previously only annotation shape tests
- Fixed 2 silent error swallowing in `tools/research.py` `_walk_nested_json` and `tools/manage.py` `_summarize_probe_response` (`except Exception: return` → added `logger.debug` to log path/rpcid for troubleshooting)
- Fixed all `src/` ruff errors (9 → 0): removed unused imports, removed f-string prefix without placeholders, added `# noqa: F401` for `client_wrapper.py` facade re-exports, added `# noqa: E402` for `client_manager.py` imports after try/except
- Fixed mypy type errors in 4 files (57 → 51): `cookie_manager.py` `psidts` nullable fallback, `thinking_client.py` `int(learning_config[...])` added `# type: ignore[call-overload]` and refactored if/return flow for proper mypy narrowing, `client_wrapper.py` `list_sessions` filter None, `tools/prompts.py` renamed loop variable `prompt` → `item` to avoid type conflict with `Optional[dict]` assignment in same function
- Eliminated all `src/` mypy errors (67 → 0, 22 source files clean): `constants.py` uses `TypedDict` (`ModelConfig` / `LearningModeConfig`) instead of bare `dict` literals, making `resolve_model_name` return `str` instead of `object`, eliminating 4 cascading errors; `tools/manage.py` `_tool` decorator introduces `TypeVar("_F", bound=Callable[..., Any])` and switches to side-effect registration (`mcp.tool(...)(func)` discards return value, always `return func`), preserving decorated function's declared return type, eliminating 18 dispatcher pattern `no-any-return` errors at once; `_clamp_int` added `number: int` annotation + `# type: ignore[call-overload]`; `_sanitize_account_status` / `_format_chat_export_markdown` / `gemini_move_chat_to_notebook` use temporary variables instead of `X if isinstance(X, dict) else {}` double-call pattern (mypy doesn't narrow across calls); `_read_chat_turns` `turns_raw` + `isinstance` narrowing; `_move_chat_to_notebook_payload` `conversation: list[Any]`; `_web_capabilities_payload` / `gemini_list_notebook_chats` `payload: dict[str, Any]`; `gemini_search_chats` annotates `matches`/`fields`/`snippets` and renames `fields` → `fields_str` (avoiding `list[str]` vs `str` name conflict); `gemini_get_usage_limits` annotates `results`/`entries`; `_fetch_conversation_metadata_sources` callers annotate `pinned_diag`/`recent_diag`; `tools/media.py` `gemini_generate_music` forward return value adds `cast(list[TextContent], ...)`; `skill_server.py` history export branch renames `chat` → `history` (avoiding type conflict with search branch `_chat_to_dict` returning `dict`); `pyproject.toml` adds `[[tool.mypy.overrides]]` for `gemini_webapi.*` and `browser_cookie3` setting `ignore_missing_imports = true` (third-party packages haven't published PEP 561 stubs)
- Added `tests/test_cleanup_test_artifacts.py` (34 tests): `gemini_cleanup_test_artifacts` (destructiveHint=True) previously only had annotation shape tests, **dry_run=False actually deletes remote chats and scheduled tasks but had zero behavioral coverage** — this file adds `_split_cleanup_markers` (empty/whitespace filtering/multi-value/preserve case), `_marker_hits` (case-insensitive/None/multi marker), `_format_cleanup_markdown` (empty payload/chats three states deleted-matched-error/scheduled verification_status priority/errors section/dry_run hint), `_cleanup_test_artifacts_payload` (chats dry_run hits id/title, dry_run=False successful delete/delete throws exception/missing delete_chat capability, scan_turns hits turn/throws exception doesn't interrupt loop, missing list_chats capability, target=chats skips scheduled, target=scheduled skips chats, scheduled dry_run/dry_run=False delete/dry_run=False RPC throws exception, empty markers fallback codex-, max_chats clamped to [1,100] and slice window, missing _batch_execute capability); tool layer registration + DESTRUCTIVE_REMOTE annotation + call_tool markdown/json dual format
- Cleaned unused imports introduced in previous rounds from 3 test files (`test_cleanup_test_artifacts.py` `pytest`, `test_chat_session_lifecycle.py` `pytest`, `test_client_manager.py` `pathlib.Path`)
- Cleaned historical ruff errors in tests/ (4 → 0): `test_error_and_session.py` removed unused `SessionData`, `test_imports.py` removed duplicate `src.tools.media` import (typo), `test_core.py`/`test_imports.py` side-effect imports added `# noqa: F401`
- Added `tests/test_server_cookie_tools.py` (13 tests): `src/server.py` `gemini_get_cookie_status` (Manager unavailable / available+set / available+unset+needs refresh), `gemini_list_browser_cookie_profiles` (empty profiles / entries with error / normal multi-field rendering / account_available=None renders unknown / response_format=json / throws exception handle_error fallback), `gemini_get_cookie_from_browser` (success no profile / success with profile / failure / throws exception handle_error fallback) — previously only annotation shape tests
- Added `tests/test_doctor_helpers.py` (26 tests): `gemini_doctor` previously only had annotation shape tests, `_doctor_check` / `_doctor_overall_status` / `_format_doctor_markdown` / `_doctor_payload` four helpers had zero direct coverage — this file adds `_doctor_check` (None value filtering / empty details), `_doctor_overall_status` (empty/all ok/all skip/mixed/warn priority/error priority 6 combinations), `_format_doctor_markdown` (browser=disabled / error profile / account=None / empty recommendations / detail whitelist 4 keys), `_doctor_payload` (cookie_status 3 branches / browser_profiles 3 branches / alignment ok / ffprobe warn + recommendations / generated_media warn / validate_browser recommendation / overall_status warn & ok / cookie values not leaked)
- Added `tests/test_tool_helpers.py` (69 tests): fills zero-coverage gap for pure helpers in `utils` / `constants` / `media` / `file` four modules — `validate_local_file_path` (empty/path traversal/doesn't exist/not file/extension/size/happy/no extension 8 branches), `validate_image_paths` (empty/validate one by one/fail-fast/non-image extension), `validate_optional_image_path` (None/single/invalid), `extract_remote_chat_id` (cid/metadata/no match), `parse_response` (text/override/image/video/music model routing/remote_chat_id), `get_stream_text_piece` (text_delta priority/fallback/missing/falsy fallback), `resolve_model_name` / `normalize_model_alias` / `describe_model_name` / `supported_learning_modes` (lookup function full coverage), `resolve_media_request` (image fixed Nano Banana 2 / music non pro=Lyria 3 / pro+standard=Lyria 3 / pro+extended=Lyria 3 Pro / unknown type passthrough), `_safe_media_filename` (normal/special characters/48 character truncation/end stripping/empty fallback), `_media_timeout` (explicit/image=180/other=600), `_set_client_timeouts`+`_restore_client_timeouts` (no attribute/max algorithm/don't lower/watchdog lower limit 120/write back/None skip/round trip), `_prepend_backend_note` (empty note/empty parsed/normal concatenation), `_media_from_music_card` (mp3/mp4/no URL/empty title fallback), `_validate_url` (empty/no scheme/no netloc/valid/exception), `_validate_file_path` forwarding shell
- Extended `tests/test_chat_session_lifecycle.py` (+19 tests, 6→25): fills `gemini_send_message` parameter fallback logic — previously `temporary` / `learning_mode` / `retain_chat` / `delete_after_seconds` four parameters' "fallback from session_data when None, override when passed" behavior had zero branch coverage (only walked indirectly via happy path). Added: session doesn't exist early exit / image_paths invalid early exit before session check / temporary three states (None fallback / explicit override / session missing fallback False) / learning_mode three states (None fallback / explicit override / both None don't write to kwargs) / retain_chat three states / delete_after_seconds three states (including both None pass None) / thinking_level from session + missing fallback standard / schedule cleanup uses session.cid / returns response.text / request_kwargs contains prompt+files
- Added `tests/test_chat_tools.py` (14 tests): `gemini_chat` and `gemini_start_chat` entry tools previously only had happy path indirect coverage, key behavioral contracts had zero assertions — `gemini_chat` adds image_paths invalid early exit before client init / request_kwargs full field injection (prompt/files/model/thinking_level/gem/temporary) / model alias resolved via resolve_model_name / learning_mode conditional injection (None omitted, truthy written) / cleanup_due_remote_chats receives client / schedule_remote_chat_cleanup_from_response input (response same object + retain/delete/source) / parse_response uses model to parse containing remote_chat_id; `gemini_start_chat` adds client.start_chat receives model_name and gem / store_session all inputs (session_id 8 characters / session / model original alias / thinking_level / learning_mode / temporary / retain_chat / delete_after_seconds) / defaults (learning_mode=None / temporary=False / retain_chat=False / delete_after_seconds=None) / returns text containing session_id and model_name / cleanup_due_remote_chats receives client / doesn't call schedule cleanup (no response)
- Extended `tests/test_chat_tools.py` (+13 tests, 14→27): fills `gemini_chat_stream` and `gemini_send_message_stream` streaming tools empty stream branch and accumulation logic — previously two streaming tools only had happy path indirect coverage, `final_response is None` fallback branch had zero coverage. `gemini_chat_stream` adds image_paths invalid early exit / empty stream returns empty text and skips cleanup (`if final_response:` guard) / multi chunk text_delta accumulation / schedule cleanup uses last response / request_kwargs full field injection / learning_mode conditional omission; `gemini_send_message_stream` adds session doesn't exist early exit / image_paths invalid early exit before session check / multi chunk accumulation / **empty stream still calls cleanup passing None (documents inconsistency with chat_stream: send_message_stream has no `if final_response:` guard, always calls)** / schedule cleanup uses last response / temporary fallback session / learning_mode both None omitted
- Added `tests/test_file_tools.py` (21 tests): `gemini_upload_file` and `gemini_analyze_url` previously only had 1 indirect test case (simultaneously verifying path traversal and URL format invalid early exit), key behavioral contracts had zero assertions — `gemini_upload_file` adds invalid path early exit before client init / generate_content positional argument prompt + files=[safe_path] + model/thinking_level/timeout=60 / analysis_prompt default and custom values / returns prefix "✅ Successfully analyzed {filename}" / response.images concatenation (📷 Images + number + title + url) / remote_chat_id concatenation / schedule cleanup input / cleanup_due_remote_chats receives client / asyncio.TimeoutError branch ("File analysis timed out") / generic Exception branch ("❌ Error: {e}") / exception branch skips schedule cleanup; `gemini_analyze_url` adds invalid URL early exit / prompt default ("Please analyze the content at this URL: {url}") / prompt custom concatenation (user prompt + URL + "Use the URL above...") / generate_content no files parameter / returns without ✅ prefix (different from upload_file) / response.images concatenation / remote_chat_id concatenation / schedule cleanup source / asyncio.TimeoutError branch ("URL analysis timed out") / generic Exception branch
- Added `tests/test_media_tools.py` (36 tests): `gemini_generate_media` and `gemini_generate_music` previously only had 5 happy/edge indirect test cases, key integration contracts had zero assertions — `gemini_generate_media` adds image_path invalid early exit before client init / generate_content full field injection (prompt template / files / model / thinking_level / timeout) / valid image_path converts to files / cleanup_due_remote_chats receives client / timeout defaults (image=180 / music=600 / video=600) / explicit timeout override / zero and negative timeout fallback to default / client.timeout temporarily raised and restored (including exception and TimeoutError branch finally) / backend routing (image always uses flash / image returns Nano Banana 2 + Pro redo note / music+flash=Lyria 3 / music+pro+standard=Lyria 3 / music+pro+extended=Lyria 3 Pro / video=Gemini Web default) / asyncio.TimeoutError branch (including backend label + "can increase timeout_seconds") / generic Exception branch (including backend label + "generic generate_content") / exception branch skips schedule cleanup / **empty response still calls schedule cleanup (documents inconsistency with chat_stream's `if final_response:` guard)** / remote_chat_id concatenation / response.media uses effective_alias to render Lyria 3 Pro label / schedule cleanup source routes by media type ("gemini_generate_media:{media_type}") / schedule cleanup input (response same object + retain/delete); music recovery path adds response.media empty calls _fetch_music_media_from_chat to recover / recovery exception swallowed doesn't crash / non-music skips recovery; `gemini_generate_music` adds forwarding uses music prompt template / **default thinking_level=extended → Lyria 3 Pro (key difference from generate_media default standard → Lyria 3)** / **source still "gemini_generate_media:music" not "gemini_generate_music" (documents cleanup attribution inconsistency)**
- Added `tests/test_research_tools.py` (33 tests): `gemini_deep_research` previously had 9 indirect test cases in test_tool_workflows.py (native happy path / fallback / chat-history polling / immersive report extraction), but key integration contracts had zero assertions — entry adds cleanup_due_remote_chats receives client / default thinking_level=extended (different from gemini_chat default standard) / default timeout_seconds=600; fallback path adds generate_content receives deep_research=True / model=parsed model_name / timeout=original timeout_seconds (doesn't go through _phase_timeout's max(30,...) lower bound, different from native path) / prompt contains "Requested MCP model alias" and "Transport model selection" / schedule source="gemini_deep_research:fallback" (distinguished from native's "gemini_deep_research") / schedule receives response same object / retain_chat and delete_after_seconds forwarded (including default False/None) / returns text prefix "# 📚 Deep Research Plan: {query}" / contains "- Requested model:" and "- Actual research transport:" lines / contains "⚠️ Current gemini-webapi client doesn't expose complete research polling API" warning / contains response.text content; native path adds has_native_api determination (client has 3 methods) / start_chat receives research_model (default Model.UNSPECIFIED) / create_deep_research_plan receives query containing model metadata / start_deep_research receives plan / plan.research_id exists calls wait_for_deep_research(plan, poll_interval=, timeout=) / poll_interval max(3, ...) clamp / schedule cid falls back from plan.cid (chat.cid cleared to "" by _start_fresh_research_chat) / schedule source="gemini_deep_research" / retain/delete forwarded / done=True returns "# 📚 Deep Research Report:" + "Completed: Yes" + "## Report" + result text / contains Research ID and title / contains model_note; thinking_scope adds non default transport (non-standard alias like "gemini-3-pro") calls / default transport (standard alias → Model.UNSPECIFIED) skips; error handling adds asyncio.TimeoutError → "❌ Deep Research timed out ({N} seconds)" + "AI Plus subscription" / RuntimeError → "❌ Deep Research failed: {str(e)}" + "Whether this feature is available in your region" / exception branch skips schedule cleanup / native path wait throws TimeoutError and RuntimeError also caught by outer
- Added `tests/test_prompts_tools.py` (44 tests): `tools/prompts.py` coverage 48% → 100% (136 stmts, 0 miss), previously only test_tool_workflows.py indirectly covered create + list happy path, key behavioral contracts had zero assertions — `PromptManager._load_prompts` adds file doesn't exist skips / JSON parse exception swallowed and logs ERROR / normally loads existing prompts dict; `_save_prompts` adds writing to non-existent directory throws FileNotFoundError swallowed and logs; `create_prompt` adds returns uuid4 string / persists to file / fields complete (id/name/content/category/description/created_at/updated_at) / default category='通用' and description=''; `get_prompt` adds hit / miss returns None; `list_prompts` adds empty / no category filter sorted by created_at descending / filtered by category still descending; `list_categories` adds empty / multiple categories deduplicated and sorted; `update_prompt` adds not found returns False / only name partial update (other fields unchanged) / full update and refresh updated_at / **explicit empty string update (verifies `is not None` check is not falsy check, allows clearing fields)**; `delete_prompt` adds not found returns False / hit deletes and persists; `get_prompt_manager` adds singleton created once (**found DEFAULT_PROMPTS_FILE bound as `__init__` default parameter at class definition time, monkeypatching module-level constant ineffective, switched to subclass hardcoded tmp_path**) / 8 threads concurrent return same instance (verifies `_prompt_manager_lock`); `gemini_manage_prompts` 6 actions full coverage — list (empty/category filter empty/non-empty contains category header and description line/no description omits line), list_categories (empty/non-empty contains entry count per category), get (missing prompt_id/not found/full field details/missing description key triggers `.get('description','无描述')` default), create (missing name/missing content/default category/explicit category), update (missing prompt_id/not found/success), delete (missing prompt_id/not found/success), **invalid action throws ToolError via MCP (FastMCP pydantic Literal validation before dispatch) + directly calling tool.fn bypasses validation triggers line 251 '❌ 无效的 action。' fallback (defensive fallback unreachable via MCP in production)**, exception fallback (manager.list_prompts throws RuntimeError → '❌ 失败: {e}')
- Added `tests/test_thinking_client.py` (48 tests): `thinking_client.py` coverage 53% → 100% (139 stmts, 0 miss), previously only test_tool_workflows.py had 3 indirect test cases (inject_thinking_level happy / inject_web_request_options happy with h5d / _with_learning_prompt kwargs branch), key branches had zero assertions — `_encode_learning_x9b` adds 4 field names (zUa/QLd/LYd/h5d) + unsupported field throws ValueError; `_encode_learning_goa` adds mode_id encoded as `[[mode_id]]`; `inject_web_request_options` adds 5 early exit guards (f.req not str / outer not list / outer length <2 / inner_payload not str / inner_request not list) + learning metadata incomplete throws ValueError + learning-only injection ([54]/[55] set, [79]/[80] not set) + thinking-only injection ([79]/[80] set, inner_request extends from 69 to 81) + already ≥81 doesn't extend + doesn't modify original data + `inject_thinking_level` delegation; `_set_web_request` adds all None no existing → None token / all None has existing → reuse token / invalid thinking_level throws ValueError / invalid learning_mode throws ValueError / valid thinking+model / valid learning_mode (quiz → interactive_quiz → id 18) / Chinese thinking_level '标准' (verifies .strip().lower() lookup); `_with_learning_prompt` adds learning_mode=None unchanged / args[0] prefix / **kwargs['prompt'] prefix** / empty parameters unchanged; `_prefix_learning_prompt` adds non str unchanged / already prefixed unchanged; `thinking_scope` adds entry sets + exit resets / exception still resets; `generate_content` adds model passed in / model=None omits model kwarg / exception still resets token / learning_mode prefix injection args[0]; `generate_content_stream` adds model passed in / model=None / exception still resets token; `_install_thinking_transport` adds session is None early exit / already installed (`_mcp_thinking_stream=True`) early exit / normal patch+set flag / stream_with_thinking no active request skips / url not GENERATE skips / data not dict skips / hit injects (f.req rewrite + `x-goog-ext-73010990-jspb` header) + preserves existing headers; `init` adds super().init() + _install_thinking_transport() call order. **Key design: uses `object.__new__(ThinkingLevelGeminiClient)` to skip GeminiClient.__init__ real network dependency; class-level monkeypatch `GeminiClient.generate_content` / `generate_content_stream` / `init` async methods; autouse fixture explicitly resets `_web_request` ContextVar before and after each test to prevent cross-test leakage**
- Added `tests/test_client_wrapper.py` (39 tests): `client_wrapper.py` coverage 45% → 97% (104 stmts, 3 miss; remaining 3 are import-time `except ImportError` defensive branches, cookie_manager always available in venv, post-import cannot trigger), was lowest coverage module in entire repository with zero direct tests — `_session_data_to_dict` adds None short-circuit + full 8 field mapping; client facade adds `get_gemini_client` / `initialize_client` / `reset_client` (simultaneously reset client_manager + clear sessions) delegation; session facade adds `store_session` (all parameters forwarded) / `get_session` (hit dict + miss None) / `remove_session` / `pop_session` (hit + miss) / `clear_sessions` / `cleanup_expired_sessions` / `list_sessions` (filter None + empty); cleanup facade adds `schedule_remote_chat_cleanup_from_response` / `schedule_remote_chat_cleanup` all parameters forwarded + `delete_remote_chat` / `cleanup_due_remote_chats` (client explicitly passed skips init / client=None self-fetches client+initializes two branches) + `list_pending_remote_chat_cleanup` (CleanupTask mapping + empty); Cookie integration layer adds `_on_cookie_update` (reset + write env, psidts true/false two branches) / `init_cookie_manager_integration` (unavailable short-circuit / auto_refresh=true / auto_refresh=false three branches) / `get_cookie_from_browser` (unavailable / no psid / update success write env / update failure don't write env / psidts empty don't write / profile concatenation source) / `list_browser_cookie_profiles` (unavailable / validate=True calls cache / validate=False skips cache) / `get_cookie_status` (unavailable / available expands status+info). **Key design: `_RecordingFake` dynamically records method calls via `__getattr__`, `_async_methods` parameter lets specified methods return coroutines to adapt facade's `await`; uses `monkeypatch.setattr(cw, "_session_manager", fake)` to replace module-level singleton avoiding touching real manager state; Cookie functions patch `cw.COOKIE_MANAGER_AVAILABLE` + `cw.get_cookie_manager` + `cw.init_cookie_manager` + `cw._prepare_browser_cookie_cache` + `cw.reset_client` to isolate real browser/singleton side effects**
- Added `tests/test_remote_chat_cleanup_manager.py` (26 tests): `remote_chat_cleanup_manager.py` coverage 81% → 100% (91 stmts, 0 miss), previously only `extract_remote_chat_id` had 4 indirect test cases in test_error_and_session.py, `RemoteChatCleanupManager` class 5 instance methods (`schedule_cleanup_from_response` / `schedule_cleanup` / `_delete_after_delay` / `delete_chat` / `cleanup_due_chats`) had zero direct coverage — `extract_remote_chat_id` adds cid attribute / metadata list / no match / non c_ prefix 4 scenarios regression guard; `schedule_cleanup_from_response` adds cid hit registers to `_pending_cleanup` + no cid returns None; `schedule_cleanup` adds cid falsy / retain_chat=True / retention_provider resolves delete_after_seconds / synchronous context `except RuntimeError` early exit only registers 4 branches; `_delete_after_delay` adds pending delete_at doesn't match early exit / pending missing early exit / **happy path calls `await self.delete_chat(cid)` and removes pending** (line 114); `delete_chat` adds no cid / client_initializer resolves / client_provider resolves / client has no delete_chat method warning / client.delete_chat throws exception swallowed / success removes pending 6 branches; `cleanup_due_chats` adds client_initializer / client_provider / multiple due loop counting / partial failure counting / no due returns 0 / explicit client skips parsing 6 branches; **`schedule_cleanup` called in running event loop covers `loop.create_task(self._delete_after_delay(...))` branch** (line 103), via `asyncio.sleep(0.05)` lets coroutine created by create_task run until delete_chat is called. **Key design: `SimpleNamespace` + `AsyncMock` constructs fake client with async `delete_chat`; synchronous context calling `schedule_cleanup` triggers `except RuntimeError` branch avoiding creating real deletion task; `_delete_after_delay` / `delete_chat` / `cleanup_due_chats` use `asyncio.run` to run in isolated event loops**
- Extended `tests/test_client_manager.py` (+8 tests, 23→31): `client_manager.py` coverage 89% → 96% (114 stmts, 5 miss; remaining 5 are two `except ImportError` defensive branches, cookie_manager and thinking_client always available in venv, post-import cannot trigger), previously `get_extra_cookies` had zero direct tests, `_create_client` extra_cookies loading branch and `prepare_browser_cookie_cache` exception swallowing branch uncovered — `get_configured_proxy` adds **local port reachable happy path** (uses real socket listening on occupied port to verify `create_connection` success passes through proxy, line 46); `get_extra_cookies` adds unavailable returns empty / cookie_data is None returns empty / cookie_data exists returns extra_cookies three branches (lines 72, 75, 76); `prepare_browser_cookie_cache` adds COOKIE_MANAGER_AVAILABLE=False early exit (line 82) / `cache_dir.chmod` throws OSError silently swallowed (lines 95-96) / stale cache file `unlink` throws OSError logs debug doesn't interrupt (lines 101-102); `ClientManager._create_client` adds extra_cookies non-empty loads cookies and calls prepare_browser_cookie_cache (lines 156-158). **Key design: local proxy reachable uses real `socket.socket` listening to avoid mocking network stack; chmod/unlink exceptions use `monkeypatch.setattr(Path, "chmod"/"unlink", raising_fn)` global patching + monkeypatch automatically restores; `_create_client` extra_cookies branch uses monkeypatch to replace `get_extra_cookies` + `prepare_browser_cookie_cache` avoiding real filesystem side effects**
- Added `tests/test_server_core_tools.py` (7 tests): `server.py` coverage 83% → 94% (90 stmts, 5 miss; remaining 5 are `main()` + `__main__` blocking entry points, untestable), previously `gemini_get_tool_manifest` / `gemini_reset` / `gemini_doctor` three core tools only had annotation shape tests, zero behavioral coverage — `gemini_get_tool_manifest` adds markdown default format / json format / scope parameter passthrough 3 branches (lines 97-100); `gemini_reset` adds calls reset_client + returns fixed text (lines 106-107); `gemini_doctor` adds markdown default format (including browser/validateBrowser defaults chrome/False) / json format / browser and validate_browser parameter passthrough 3 branches (lines 117-120). **Key design: calls via `server.mcp.call_tool(name, kwargs)` through MCP dispatch (same pattern as test_server_cookie_tools.py), monkeypatches underlying `_tool_manifest_payload` / `_format_tool_manifest_markdown` / `_doctor_payload` / `_format_doctor_markdown` / `reset_client` to isolate real dependencies; ManifestScope pydantic Literal validation executes before MCP dispatch, scope must be valid enum value**
- Added `tests/test_cookie_manager_browser.py` (54 tests) + extended `tests/test_cookie_manager.py` (+5 tests, 25→30): `cookie_manager.py` coverage 62% → 97% (404 stmts, 14 miss; remaining 14 lines are all `except ImportError` defensive branches, browser_cookie3 and gemini_webapi always available in venv, post-import cannot trigger), previously browser Cookie extraction static methods and async Gemini account validation functions had zero direct coverage — `_read_cookie_jar` adds cookie name filtering / empty value filtering / non google.com domain filtering 3 branches; `_chrome_base_path` adds darwin / win32 / linux 3 platform branches (lines 258-263); `_browser_cookie_candidates` adds non chrome early exit / base=None early exit / auto+profile candidate collection / auto exception skips / profile exception skips / require_psid=False preserves candidates without PSID / no candidates returns empty 7 branches (lines 276-304); `_chrome_selected_profile_directory` adds base=None / no Local State / illegal JSON / last_used string / last_active_profiles list / profile not dict / no selected key 7 branches; `_select_valid_cookie_candidate` adds single candidate direct return / multiple candidates validation success returns / multiple candidates validation failure falls back to first 3 branches (lines 329-345); `_select_named_cookie_candidate` adds not found returns empty / case-insensitive match 2 branches; `get_cookies_from_browser` adds unsupported browser / profile+candidates naming selection / no profile single candidate / no candidates fallback read_jar / no PSID returns empty / exception fallback 6 branches (lines 155-184); `list_browser_cookie_profiles` adds unsupported browser / has candidates rendering / no candidates fallback / fallback read exception / validate=True merges verification 5 branches (lines 206-242); `_validate_cookie_candidates_async` adds available+scheduled>0 / unavailable skips / available but scheduled==0 falls back first_available / init exception skips / close exception swallowed 5 branches (lines 370-403); `_validate_cookie_candidate_profiles_async` adds available marks / init exception records / close exception swallowed 3 branches (lines 406-439); `_validate_cookie_candidate_profiles` synchronous thread wrapper adds normal return / thread exception returns empty 2 branches (lines 359-367); `_probe_scheduled_registry_count` adds batch_execute exception returns 0 / restores language / happy path parses JSON returns count / body not string returns 0 / parsed[0] not list returns 0 / skips non-matching part 6 branches (lines 442-472); `get_cookie_manager` / `init_cookie_manager` module-level singletons add lazy loading creation / same instance returns / init overwrites existing 3 branches (lines 700-727); `_monitor_loop` adds expired+auto_refresh triggers refresh_cookie / exception swallowed+sleep(60) 2 branches (lines 622-633). **Key design: `_make_async_probe(value)` constructs coroutine-returning async substitute — can't use `lambda: asyncio.run(...)` because `_validate_cookie_candidates_async` already runs inside event loop, nested `asyncio.run` would throw RuntimeError swallowed by except; `_make_fake_gemini_client_class` uses AsyncMock to construct init/close with side effects; `_probe_scheduled_registry_count` happy path needs monkeypatch `gemini_webapi.types.RPCData` to simple class (production code `RPCData("XPSWpd", "[]")` fails in current gemini_webapi version — pydantic BaseModel doesn't accept positional arguments + XPSWpd not valid GRPC enum value, this is known production bug, function actually always returns 0); `_browser_cookie_candidates` chrome path uses tmp_path + empty files to simulate profile directory structure**
- Added `tests/test_research_report_helpers.py` (104 tests): `tools/research.py` coverage 70% → 100% (440 stmts, 0 miss), previously rendering helpers / immersive report extraction / entry tools / native recovery all had zero direct coverage — pure rendering helpers add `_markdown_sections` (split/empty body filtering/no heading) / `_iter_source_links` (non dict filtering/empty title fallback/limit) / `_plain_excerpt` (0 characters/stripped/short/long/1 character) / `_title_from_markdown` (H1 extraction) / `_safe_filename` (special character replacement/strip/empty fallback); report actions add `_research_report_actions_payload` (with/without title) / `_format_research_report_actions` / `_create_research_report_artifact` (write file + fallback text) / `_format_research_report_artifact`; immersive report extraction adds `_walk_nested_json` (dict/list/JSON-string/non JSON/invalid JSON string) / `_extract_sources_from_node` (filter gstatic/googleusercontent/duplicates/non str/no valid) / `_extract_deep_research_immersive_report` (no match/no cite/heading/multiple candidates pick longest/markdown title fallback/immersive_id empty/JSON-string payload); `_fetch_deep_research_immersive_report` adds empty cid / no `_batch_execute` / **ImportError (`monkeypatch.delattr` GRPC, more accurate than `setattr` setting None — `from X import Y` succeeds when Y is None, only throws ImportError when Y is missing)** / batch_execute exception / no report / happy path with/without sources; `_request_completed_research_report` adds no send_message / happy path / exception returns None; native recovery helpers add `_resolve_deep_research_transport_model` (flash/lite/pro/thinking/None/empty/non alias/ImportError) / `_is_default_deep_research_transport` (UNSPECIFIED/string/other) / `_is_capability_probe_false_negative` (dual marker/missing marker) / `_is_research_start_message` / `_is_research_completion_message`; `_create_deep_research_plan` / `_start_deep_research_with_recovery` / `_start_fresh_research_chat` add happy path / non probe exception / missing recovery attrs / output no plan / synthetic plan / preserve existing fields / recovery happy / timeout fallback fetch_latest / timeout fallback plan.cid / no fetch returns empty / fetch returns None / clear cid/rid/rcid / attr set exception swallowed; `_wait_for_deep_research_by_chat` polling branch adds completion + immersive report / completion + followup report / completion + followup is start message skip / latest_text different from start_text / timeout returns running; `_format_deep_research_result` boundary adds not done + start message clears / not done no report uses start_text / done no report uses plan_text no prefix / empty statuses; entry tools add `gemini_list_research_report_actions` (no report/markdown/json) / `gemini_create_from_research_report` (no report/markdown/json + 6 artifact_type parameterized dispatch) / `gemini_deep_research` goes through `_wait_for_deep_research_by_chat` branch (plan.research_id empty → doesn't call `wait_for_deep_research`). **Key design: `_long_report(body_seed, length)` constructs ≥1000 character + `## ` marked report body to satisfy `_extract_deep_research_immersive_report` report threshold; `_patch_entry_env` uniformly patches entry tools' 3 external seams (get_gemini_client / initialize_client / _fetch_deep_research_immersive_report); `_NativeClientWithoutResearchId` substitute class triggers line 90 `_wait_for_deep_research_by_chat` branch; polling loop tests use `call_count` dict counter to control `fetch_latest_chat_response` returning different values + `monkeypatch.setattr(asyncio, "sleep", _no_sleep)` to avoid real waiting**
- Added `tests/test_skill_server_helpers.py` (158 tests): `skill_server.py` coverage 77% → 99% (648 stmts, 6 miss; remaining 6 are `except ImportError` mcp missing guard + `main()`/`__main__` blocking entry points, post-import cannot trigger), previously `PromptManager` full set / `_format_response` / `_truncate_text` / `_error_text` / configuration helpers / `chat` / `history` / `account` sub-handlers / `scheduled` sub-handler / `create` / `edit` / `session` / `doctor` / `cleanup` all had zero direct coverage — pure helpers add `_truncate_text` (short/empty/long truncation+marker/rstrip) / `_error_text` (log+TextContent) / `_normalize_model` (f/t/p/l/lite/pro alias+case/passthrough) / `_normalize_media_type` (img/picture/photo/case/passthrough); `_format_response` adds text only / empty text / images iteration (including empty url skip) / videos / audio_url / backend_label prefix / backend_label+note / remote_chat_id injection (with/without) / combined media+backend; `PromptManager` adds `__init__`+`_load` (file missing/normal read/corrupted JSON/missing prompts key) / `_save` (persist/IOError swallowed) / `list_all` (sorted by name case-insensitive) / `get_by_name` (case-insensitive/miss) / `create` (generates id+persists) / `delete` (hit+persist/miss False); configuration helpers add `_ensure_config_dir` (create/idempotent) / `_init_default_prompts` (missing copies/already exists skips/no default file skips) / `get_prompts` singleton cache; `chat` adds invalid image early exit / no session goes through generate_content (learning_mode injection/omission) / session hit goes through send_message (learning_mode falls back from session/explicit override/both None omitted) / top-level except; `history` adds list (empty/non-empty/has_more) / search (missing query/no read_chat/no match/title hit/id hit/scan_turns collects snippet/has_more) / read (missing chat_id/no read_chat/no turns/renders turns/chat None) / export (missing chat_id/no read_chat/renders markdown/list_chats hits metadata) / delete (missing chat_id/no delete_chat/calls delete_chat) / Invalid action / top-level except; `account` sub-handler adds `_account_models` (empty/renders) / `_account_features` (no _batch_execute/concurrent probe ok/exception type name/reject_code) / `_account_links` (empty/renders) / `_account_usage` (renders/empty) / `_account_library` (empty/renders) / `_account_notebooks` (empty/renders) / `_account_scheduled` (renders/empty) / `_account_modes` (empty/renders) / `_account_status` (no inspect/empty summary/renders/non dict) / `_account_manifest` / main entry dispatch (auth-free/client-based/unknown action fallback status) / top-level except; `scheduled` sub-handler adds `_scheduled_list` (empty_hint/renders) / `_scheduled_get` (missing id/matched_task False/default not_found/renders found/disabled no state) / `_scheduled_create` (missing title/instructions/invalid hour/visible_in_registry/not_visible_in_nonempty_registry/registry_empty_unverified/readable_by_id_registry_empty/readable_by_id_not_visible_in_registry/no created_id) / `_scheduled_delete` (missing id/rpc_unconfirmed/still_visible/deleted_state_by_id/registry_empty_active_or_unknown/not_visible_active_or_unknown/registry_empty_not_readable/not_visible_not_readable) / main entry dispatch (list/get/create/delete/Invalid action/no _batch_execute/top-level except); `create` adds invalid image / top-level except / image happy path (prompt prefix+model+backend_label) / music happy path (audio_url); `edit` adds invalid image / top-level except / happy path (files+prompt); `session` adds `_session_send` (invalid session/learning_mode fallback/explicit override/thinking_level fallback) / `_session_create` / `_session_list` (empty/renders) / `_session_reset` (single/all+reset_client) / Invalid action / invalid image early exit / send dispatch / top-level except; `doctor` happy path / top-level except; `cleanup` happy path / top-level except; `cookie` status/get/profiles happy path + Invalid action + top-level except. **Key design: `_patch_client_seams` uniformly patches 3 client_wrapper seams (get_gemini_client / initialize_client / cleanup_due_remote_chats); `_ACCOUNT_AUTH_FREE_ACTIONS` binds original function references at module load time, patching module attributes ineffective, switched to patching functions called by handlers (like `_web_capabilities_payload`); `@mcp.tool` decorator returns original function, directly `await skill_server.tool(action="unknown")` bypasses pydantic Literal validation to cover Invalid action fallback; `_extract_rpc_bodies` nested list structure needs precise construction (when `bodies[0][0]` is list, entries parsed from its elements)**
- Added `tests/test_manage_scheduled_actions.py` (50 tests): `tools/manage.py` coverage 73% → 79% (1844 stmts, 396 miss), previously scheduled action create/delete MCP handlers and remy_goals pagination helper had zero direct coverage — `_fetch_remy_goal_conversation_refs` adds 5 stopped_reason branches (no_next_page_token / max_items / empty_page / no_new_unique_items / max_pages) + parameter clamping (page_size/max_pages/max_pages illegal values converge) + same-page deduplication + cross-page deduplication + no id entry skip + response_length accumulation + request_payload first page/continuation page format + empty bodies fallback empty_page + pages diagnostic metadata; `gemini_create_scheduled_action` adds parameter validation (empty title/instructions/timezone + hour negative/over 23) + no `_batch_execute` capability early exit + verification_status matrix (visible_in_registry / not_visible_in_nonempty_registry / registry_empty_unverified / readable_by_id_registry_empty / readable_by_id_not_visible_in_registry / verification_error / get_task_error) + no created id (ok=False) + non 200 status + response_format=json + schedule_label empty/non-empty rendering + top-level except; `gemini_delete_scheduled_action` adds empty action_id + no `_batch_execute` + verification_status matrix (deleted_state_by_id / still_visible_in_registry / not_visible_not_readable_by_id / registry_empty_not_readable_by_id / not_visible_active_or_unknown_by_id / registry_empty_active_or_unknown_by_id / verification_error / get_task_error) + empty bodies (ok=False) + non 200 status + response_format=json + top-level except + deletion RPC payload format assertion. **Key design: `_FakeBatchClient` returns response text by queue + captures `_RawRPCData` payload for assertion; `_extract_rpc_bodies` nested list structure needs precise construction (`bodies[0]=body`, `body[0]=raw_entries`, `body[1]=next_page_token`, so bodies shaped like `[[[entry,...], token]]` three layers); MCP handlers registered via `register_manage_tools(mcp, layers=["all"])` then dispatched through `mcp.call_tool`, patches `get_gemini_client`/`initialize_client` + `_extract_rpc_bodies`/`_fetch_scheduled_registry`/`_fetch_scheduled_task_by_id` to isolate real RPC**
- Added `tests/test_manage_history_account_tools.py` (77 tests): `tools/manage.py` coverage 79% → 83% (1844 stmts, 313 miss), previously 4 history/account read-only MCP handlers only had happy path indirect coverage in test_tool_workflows.py, key branches had zero assertions — `gemini_search_chats` adds empty query / pure whitespace query early exit / scan_turns no read_chat early exit / `_batch_execute` path (goes through `_fetch_recent_conversation_metadata`) vs `list_chats` path (goes through client_cache diagnostics) / title-only / id-only / title+id sort deduplication / scan_turns turn matching collects snippet / read_chat exception swallowed as snippet error doesn't interrupt loop / `has_remote_more` overrides `has_more`+`next_offset` / markdown snippet newline replaces space / scan_turns doesn't show "Currently only searching titles/IDs" footer / limit/offset illegal values clamp / top-level except; `gemini_get_tool_mode_status` (zero existing tests) adds no `_batch_execute` early exit / empty bodies / body not list / leading_enabled three states (True/False/None) / body[1] not list entries empty / markdown rendering mode_id+available+quota+used+state + has_more next page + explanation line / top-level except; `gemini_get_usage_limits` adds no `_batch_execute` early exit / scope→probe_names mapping (all=2 / quota=1 / model_state=1, verifies `_batch_execute` call count) / bodies structure 4 branches (empty / bodies[0] not list / bodies[0] empty list / first not list) / markdown no entries yet + reset= rendering (with/without) / top-level except; `gemini_list_notebook_chats` adds no `_batch_execute` early exit / `_find_notebook` miss (empty notebooks / non-empty available title rendering) / hit (by id / exact title / casefold title) / items empty "No recent chats" / time rendering / has_more next page / response_format=json ok matrix (False/True) / untitled fallback / top-level except. Helper functions synchronously covered: `_turn_matches_query` (role/text/case-insensitive/empty query/no match) / `_read_chat_turns` (no read_chat throws RuntimeError / truncation+limit slicing / history None empty turns) / `_parse_tool_mode_entry` (non list / complete 6 fields / partial / available non bool→None) / `_parse_usage_entry` (non list / complete with reset_time / no reset / reset not list / reset seconds non-numeric / empty list) / `_find_notebook` (by id / miss / exact title / casefold title / exact multiple matches None / folded multiple matches None / empty input / whitespace strip) / `_fetch_notebook_chats` (single page no token stops / multiple pages token driven / empty entries even with token breaks / limit+offset clamp). **Key design: `_FakeBatchClient` returns response text by queue + captures payload; `_ListChatsClient`/`_ReadChatClient` goes through client_cache path; patches `_extract_rpc_bodies` / `_fetch_native_notebooks` / `_fetch_notebook_chats` / `_fetch_recent_conversation_metadata` to isolate real RPC; note `_fetch_notebook_chats` body structure is `[something, next_page_token_str_or_None, raw_entries]` (body[1]=token, body[2]=entries), opposite order from `_fetch_remy_goal_conversation_refs`'s `body[0]=entries, body[1]=token`**
- Added `tests/test_manage_notebook_history_facade.py` (38 tests): `tools/manage.py` coverage 83% → 86% (1844 stmts, 265 miss), previously 3 notebook/history facade MCP handlers + 1 payload helper only had happy path indirect coverage in test_tool_workflows.py, key branches had zero assertions — `_move_chat_to_notebook_payload` adds default project_type=2 (conversation[0]=chat_id, [7]=notebook_id, [13]=[2]) / custom project_type / compact JSON separators; `gemini_move_chat_to_notebook` adds empty chat_id / pure whitespace chat_id early exit / missing notebook_id+title early exit / no `_batch_execute` early exit / notebook miss (empty + non-empty available title rendering + json payload ok=False) / ok+verified (✅ rendering + MUAZcd payload capture + rpcid assertion) / ok+not verified (⚠️ rendering + didn't verify to chat_id) / not ok (❌ rendering + status_code != 200) / empty bodies → not ok / json payload three states / updated_entry None (body[1] not list) / project_type passed from notebook (int / non int fallback default 2) / top-level except / chat_id strip; `gemini_history` facade adds default action=list / action=list/scan/search/read/export dispatch (each action header assertion) / **unknown action rejected by pydantic Literal validation before dispatcher (ToolError), dispatcher's `❌ 不支持的 history action` fallback branch is defensive dead code under Literal type protection, unreachable via mcp.call_tool — use `pytest.raises(Exception)` to assert pydantic rejection** / search query forwarding; `gemini_scan_chat_history_sources` adds no `_batch_execute` early exit / empty sources renders empty structure / source_counts rendering / coverage_warnings (stopped_reason in {max_items, max_pages} rendering / other stopped_reason doesn't render) / json payload / include_notebook_chats (_fetch_native_notebooks miss skips / hit _fetch_notebook_chats pagination) / include_remy_goals / parameter clamping (limit/offset/max_items_per_source/page_size/max_pages_per_source illegal values converge) / markdown current page items + has_more (including 📌 pinned rendering, needs to give high timestamp item to rank on first page) / top-level except. **Key design: `_source_block` helper constructs source_block returned by `_fetch_conversation_metadata_sources` (containing diagnostic.stopped_reason); `_merge_conversation_source_items` sorts by `(timestamp, id) reverse=True`, without timestamp id dictionary order large first — test data needs to give expected item ranking on first page higher timestamp; `gemini_history` closure references can't patch module attributes, controls underlying helpers (`_fetch_conversation_metadata_sources` / `_fetch_native_notebooks` / `_fetch_notebook_chats`) to verify dispatch correctness; uses `pytest.raises(Exception)` instead of `ToolError` to avoid `mcp.server.fastmcp.exceptions` new mypy import-not-found baseline**
- Test suite 70 → 943 → 993 → 1070 → 1108 passed

### Performance Optimizations
- Hoisted `gemini_webapi.utils` imports to module level, eliminating function-level imports inside pagination loops in `_extract_rpc_bodies`/`_summarize_probe_response`
- `research.py` 3 loop `re.match` → module-level `re.compile`
- `_merge_conversation_source_items` `sources_by_id` from `list` (O(k) membership test) → `set` (O(1)), `sorted()` materializes on output
- `WEB_UI_CAPABILITIES` deep copy from `json.loads(json.dumps())` → `copy.deepcopy`
- `_account_features` (8 probes) and `_account_usage` (2 probes) serial RPC → `asyncio.gather` concurrent, preserves output order, N×RTT → 1×RTT

### Repository Structure
- Extracted 676 lines of pure data to new module `src/tools/manifest_data.py` (`WEB_UI_CAPABILITIES` / `WEB_FEATURE_PROBES` / `TOOL_MANIFEST`), `manage.py` from 4591 → 3924 lines, maintains backward compatibility via re-exports
- `.gitignore` added `*.egg-info/`, `build/`, `dist/` rules

### Documentation & Distribution
- Changed default `README.md` to English public homepage, added `README.zh-CN.md` to preserve complete Chinese entry point
- Added `docs/assets/gemini-web-mcp-banner.svg`, improves GitHub repository first-screen visual presentation
- Updated packaging manifest, ensures source distribution includes Chinese README and README banner assets
- Updated `docs/architecture.md` project structure, added `manifest_data.py`, `skill_server.py` and other modules

---


### Release & Distribution
- Added `scripts/package_release.py`, one-click build of wheel, sdist and standalone Codex skill zip
- Added `MANIFEST.in`, ensures source distribution includes docs, evaluations and public `.agents/skills/gemini-web-mcp`
- Added `docs/launch-kit.md`, provides installation links, distribution checklist and Chinese/English social media release copy
- Updated README top version, Release/Skill/License badges and public distribution instructions

---


### Release & Distribution
- Added public repo skill path `.agents/skills/gemini-web-mcp`, lets Codex and skill aggregators discover/install directly from GitHub
- Added GitHub skill installation commands, manual installation steps in README, clarified layered relationship between skill and MCP server runtime
- Extended skill packaging tests, verify `.codex/skills` local copy and `.agents/skills` public copy stay consistent

---


### Release & Distribution
- Added public repo skill path `.agents/skills/gemini-web-mcp`, lets Codex and skill aggregators discover/install directly from GitHub
- Added GitHub skill installation commands, manual installation steps in README, clarified layered relationship between skill and MCP server runtime
- Extended skill packaging tests, verify `.codex/skills` local copy and `.agents/skills` public copy stay consistent

---


### Web UI Alignment
- Aligned with Gemini Web model surface observed on 2026-05-22: `3.1 Flash-Lite`, `3.5 Flash`, `3.1 Pro`
- Verified 2026-06-18 Pro account web page: tool menu includes upload, Drive, import code, images, video, Canvas, Deep Research, music, learning assistance, personalization/Labs; settings menu includes activity record, memory import, usage limits, scheduled actions, public links and other entries
- Solidified `standard` / `extended` as independent `thinking_level` choices
- Added `learning_mode`, aligned with 2026-06-19 Web frontend learning assistance Input Companion:
  interactive quiz, flashcards, practice test, study guide write to corresponding `X9b` / `GOa` request fields
- Explicitly wrote actual backend rules observed on web in media tools:
  image first round fixed to Nano Banana 2, music routes by `flash` / `pro` to Lyria 3 / Lyria 3 Pro

### Account & Chat Management
- Added `gemini_inspect_account`, checks current account Web RPC/capability status and hides raw RPC previews
- Added `gemini_read_chat`, reads historical conversation turns by chat ID
- Added `gemini_search_chats`, paginated search of historical conversation titles/IDs, can explicitly scan current page body snippets
- Added `gemini_export_chat`, exports single historical conversation to Markdown or JSON
- Added `gemini_delete_chat`, deletes specified remote historical conversation
- Added `gemini_scan_chat_history_sources`, enumerates chat metadata by frontend-observed multiple history RPC filters,
  native notebook conversations and Remy goal conversation references, convenient for verifying history coverage
- Added `gemini_history` as read-only history aggregation entry, merges list/scan/search/read/export into one agent-facing tool
- Added `gemini_cleanup_test_artifacts`, default dry-run finds and optionally deletes test chats and test scheduled tasks matching explicit markers
- Added `gemini_get_tool_manifest`, exposes tool safety, privacy, pagination, available groups, current enabled state and recommended workflow metadata for agents
- Primary MCP tools add MCP `ToolAnnotations`, marking read-only, remote modification, local modification and destructive operations
- Added `gemini_probe_web_features`, probes Library, public links, usage, personalization, memory import and other new Web entries with browser-tested read-only RPCs
- Added `gemini_get_web_capabilities`, returns Pro web models, thinking levels, tool menu, settings entries and MCP coverage checklist
- Added `gemini_list_public_links`, `gemini_get_usage_limits`, `gemini_list_library_capabilities`, upgrades stably parseable new Web entries from probes to read-only tools
- Added `gemini_list_notebooks`, `gemini_list_notebook_chats`, `gemini_move_chat_to_notebook`, supports Gemini Web native notebook list, recent conversations in notebook read, and moving existing chats to target notebook with verification
- Added `gemini_notebooks` as native Notebook read-only aggregation entry, for `history-organize` use
- Added `gemini_list_scheduled_actions`, lists active/inactive task entries returned by scheduled actions page
- Added `gemini_get_scheduled_action`, read-only verifies scheduled actions by ID using frontend-confirmed `kwDCne` / GetTask RPC
- Added `gemini_create_scheduled_action` / `gemini_delete_scheduled_action`, supports daily scheduled action creation and deletion by ID
- Added `gemini_get_tool_mode_status`, read-only reads Web internal state enumeration appearing near Canvas / learning assistance and other tool modes
- Added `gemini_account_inventory` as account read-only aggregation entry, consolidates capabilities/status/features/links/usage/library/notebooks/scheduled/modes/models into one tool
- Added `gemini_list_research_report_actions` / `gemini_create_from_research_report`, provides MCP-side equivalent entry for web-measured "Create" menu after Deep Research completion, can generate web pages, infographics, quizzes, flashcards, audio overview scripts and custom app specs; currently no stable native web menu mutation RPC observed
- `gemini_list_chats` adds `offset`, `response_format` and pagination metadata
- `gemini_list_public_links`, `gemini_list_library_capabilities`, `gemini_list_scheduled_actions`
  and `gemini_get_tool_mode_status` add unified pagination metadata, convenient for agents to paginate account content
- Ignores unreachable local `GEMINI_PROXY`, avoids old proxy ports causing client initialization failure
- When reading Cookies from Chrome, verifies multiple local profiles, isolates gemini_webapi cookie cache,
  and prioritizes profile that can read scheduled registry
- Added `gemini_list_browser_cookie_profiles`, lists non-sensitive account diagnostics of Chrome profiles; `gemini_get_cookie_from_browser` supports `profile` parameter, convenient for manually aligning multi-account context
- Added `gemini_doctor`, for read-only preflight tool surface, Cookie status, browser profile alignment and media validation dependency checks
- `gemini_list_browser_cookie_profiles` adds `chrome_selected_profile` diagnostics; scheduled action
  create/delete adds `verification_status`, by-id readability and `task_state` verification, distinguishing RPC accepted, registry verified and deleted tombstone
- Low token `src.skill_server` adds `history` and `scheduled`, where `scheduled` supports list/get/create/delete; and extends `history` to support search/export, `account` to support manifest/features/links/usage/library
- Added in-project Codex skill: `.codex/skills/gemini-web-mcp`, guides agents to safely use manifest, chat records and verification workflows

### Tool Surface Contraction
- New default tool group changed to `core`
- Added intent-based tool profiles: `model`/`chat` only calls models, `history` read-only history organization,
  `history-organize` allows moving historical conversations to native Notebook, `account-read` only read-only account
  Web surface inventory, `scheduled-read`/`scheduled-admin` separate scheduled action read/write permissions
- `history` and `account-read` changed to facade-first: normal agents only see `gemini_history`
  and `gemini_account_inventory`; old granular tools continue to be maintained in `manage` / `all` as compatible maintenance surface
- `all` retains full maintenance/verification tool surface, but no longer loads local prompt tools
- `manage` internally registers by profile layering, avoiding agents who only want to organize history from simultaneously getting account write operations,
  scheduled mutation or Gems management tools
- Removed `gemini_list_features`, reduces low-value enumeration tools
- Current default tool surface is `core` plus always-available manifest/cookie helpers; `all` additionally provides
  `gemini_history`, `gemini_account_inventory`, `gemini_notebooks`,
  `gemini_inspect_account`, `gemini_cleanup_test_artifacts`, `gemini_list_chats`, `gemini_search_chats`,
  `gemini_scan_chat_history_sources`, `gemini_read_chat`, `gemini_export_chat`, `gemini_delete_chat`,
  `gemini_get_web_capabilities`, `gemini_probe_web_features`, `gemini_list_public_links`,
  `gemini_get_usage_limits`, `gemini_list_library_capabilities`,
  `gemini_list_notebooks`, `gemini_list_notebook_chats`, `gemini_move_chat_to_notebook`,
  `gemini_list_scheduled_actions`, `gemini_get_scheduled_action`, `gemini_create_scheduled_action`,
  `gemini_delete_scheduled_action`, `gemini_get_tool_mode_status`,
  `gemini_list_models`, `gemini_manage_gems`
- `gemini_manage_prompts` retained as `prompts` optional group, not part of default tool surface

### Documentation & Verification
- Added Gemini Web live UI coverage and media routing documentation
- Added `evaluations/gemini_web_mcp_contract.xml`, provides 17 read-only, stable-answer MCP contract evaluations
- Extended tests to verify media backend routing, learning mode request injection, tool annotations, evaluation XML, Codex skill and default tool surface

---


### ✨ Major Updates

#### Completely Redesigned Architecture
- Complete architecture redesign
- Clear module separation (server, client_wrapper, constants)
- Modularized tools (chat, research, media, file, manage)

#### Latest Model Support
- `fast` → gemini-3-flash
- `thinking` → gemini-3-flash-thinking
- `pro` → gemini-3.1-pro

#### Media Generation Enhancements
- **Images**: Nano Banana 2 (all models)
- **Video**: Veo 3.1 (up to 60 seconds, all models)
- **Music**: Lyria 3 Clip (30s) and Lyria 3 Pro (full)

#### Deep Research Support
- Added `gemini_deep_research` tool
- Deep research and report generation

#### Complete Tool System (Historical)

The following is the tool surface from the early v0.2.0 history; current support is subject to
`docs/tools.md` and `docs/api-reference.md`.

**Conversation Tools:**
- `gemini_chat` - Single conversation (supports image input)
- `gemini_start_chat` - Create session
- `gemini_send_message` - Send session message
- `gemini_list_sessions` - Active session list
- `gemini_reset_session` - Reset session

**Media Tools:**
- `gemini_generate_media` - General media generation
- `gemini_generate_music` - Convenient music generation

**File Tools:**
- `gemini_upload_file` - File upload
- `gemini_analyze_url` - URL analysis

**Management Tools:**
- `gemini_list_chats` - Chat history
- `gemini_manage_gems` - Gem management (CRUD)
- `gemini_list_models` - Model list
- `gemini_list_features` - Feature list
- `gemini_health_check` - Health check
- `gemini_reset` - Reset client

#### Complete Documentation System
- docs/README.md - Documentation center
- docs/quickstart.md - Quick start
- docs/tools.md - Tool usage
- docs/models.md - Model selection
- docs/configuration.md - Configuration
- docs/faq.md - FAQ
- docs/architecture.md - Technical architecture
- docs/changelog.md - Changelog

### 📦 Technical Improvements
- Client wrapper (client_wrapper.py)
- Constants system (constants.py)
- Tool registration architecture
- Session management system
- Better error handling

### ⚙️ Configuration Updates
- Environment variable `GEMINI_PSID` (required)
- `GEMINI_PSIDTS` (recommended)
- `GEMINI_PROXY` (optional)
- `GEMINI_AUTO_REFRESH` (default true)

### 📚 Dependencies
- gemini-webapi >= 1.20.0
- mcp (FastMCP)
- Python >= 3.10

---


### Initial Version
- Basic authentication mechanism
- Simple conversation functionality
- Project framework


---

## 测试覆盖渐进（2026-07-18）

### manage.py cleanup scheduled 分支 + helper 函数覆盖（Cycle 41）
- 新增 `tests/test_manage_final_branches.py`（13 个测试）：覆盖 manage.py 剩余可操作 miss——cleanup scheduled 的 `elif bodies: deleted=True` 分支（RPC accepted 但 task_state_id != 6 或 task_after_delete=None）+ scheduled 顶层 except（`_fetch_scheduled_registry` 抛异常）+ 4 个零覆盖 helper 函数（`_conversation_metadata_payload` payload 包装、`resolve_manage_tool_names` 空 configured 回退、`_configured_manage_layers` manage: prefix 分支、`_tool_availability` 兜底 return []）

---


### Skill 最佳实践对齐
- 对齐 agentskills.io 规范：`mcp-builder` 的 `reference/` 目录重命名为 `references/`（规约规定的复数形式），SKILL.md 内 9 处链接同步更新
- `gemini-web-mcp` SKILL.md 移除不可移植的硬编码路径 `/Users/jack/...`，改用规约标准的 `skills-ref validate` 校验命令
- `python-mcp-server-generator` 的 `description` 补充"何时使用"触发词（对齐规约反面示例要求）；新增 `license` 与 `compatibility` 字段
- `gemini-web-mcp` 新增 `compatibility` 字段（Python 3.10+ / .venv / Chrome cookies / 启动命令）
- 新增 `gemini-web-mcp/references/tool_surface.md`：按安全分层（破坏性/读取私密文本/只读发现/聊天媒体/历史元数据）紧凑记录工具表面，SKILL.md 按需引用，符合渐进式披露
- `.agents` 与 `.codex` 两份 skill 副本保持同步

### 依赖与配置
- `gemini-webapi` 依赖下限从 `>=1.20.0` 升至 `>=2.0.0`（早期 v0.2.0 历史期间静默升级，此条补登 changelog）：代码深度使用 `types.RPCData`、`constants.GRPC`、`constants.Model`、`constants.Endpoint`、`constants.AccountStatus`、`types.video.GeneratedMedia`、`types.ModelOutput`、`utils.extract_json_from_response`、`utils.get_nested_value` 等 2.x 才稳定的 API
- 同步修正 `src/server.py` 自报项目版本号为 `v0.2.0`（docstring / FastMCP instructions / 启动日志 3 处），与 `pyproject.toml` 保持一致
- 修正 `AGENTS.md` 模块清单：删除已移除的 `auth.py`，补全 `remote_chat_cleanup_manager.py`、`thinking_client.py`、`error_handler.py` 及 `tools/` 下的 `annotations.py`、`manifest_data.py`、`utils.py`
- 将 `README.md` / `README.zh-CN.md` / `docs/launch-kit.md` 中所有项目 wheel URL 统一为 `v0.2.0`；修正 README 徽章 `tests-70` → `tests-118`；清理 `README.md` 残留的 `/Users/jack/...` 硬编码路径，改用 `skills-ref validate`

### 代码质量
- 删除死代码 `src/auth.py`（5 个公共函数全仓零引用）
- 删除未使用的 `load_images` 函数及未用导入
- 修复 `tools/__init__.py:register_tools` 公共 API 类型标注（`mcp: FastMCP`、`list[str] | None`、`-> None`）
- 清理 `error_handler.py` 未使用导入，`Dict` → `dict` 现代化类型
- 修复 `cookie_manager.py` 3 处静默吞导入错误（`except Exception: return {}`）→ 具体异常 + `logger.warning`
- 修复 `cookie_manager.py` 2 处 `client.close()` 的 `except Exception: pass` 静默吞错 → `logger.debug` 记录关闭异常
- `manage.py` 新增 `_json_response()` helper，替换 23 处重复的 `json.dumps(payload, ensure_ascii=False, indent=2)` 模式
- `skill_server.py` 抽取 `_error_text(e, tool_name)` helper，替换 11 处重复的 `logger.error + return [TextContent(text=f"Error: {e}")]` 模板
- 修复 `ClientManager.initialize` 的 TOCTOU 竞态：`if not self._initialized` 检查移入 `_init_lock`，防止并发协程重复调用 `client.init()`

### 重构
- `skill_server.py` 的 `account` god function（157 行 / 11 action）拆分为 11 个独立 async handler + 2 个分发表（auth-free / client-based），dispatcher 仅 12 行，保留原语义
- `skill_server.py` 的 `scheduled` god function（4 action：list/get/create/delete）拆为 4 个独立 async handler + dispatcher，dispatcher 仅保留 try/except + action 分发
- `skill_server.py` 的 `session` god function（4 action：create/send/list/reset）拆为 4 个独立 handler；`list`/`reset` 不需 client，下放为 sync handler，client 初始化只保留在 `create`/`send` 内
- `research.py` 的 `gemini_deep_research`（204 行）拆为 35 行主函数 + 4 个辅助函数：`_run_native_deep_research`（client 原生 plan/start/wait 路径）、`_run_fallback_deep_research`（`generate_content(deep_research=True)` 回退路径）、`_deep_research_timeout_error`、`_deep_research_generic_error`
- `tools/prompts.py` 和 `skill_server.py` 的 `_prompt_manager` 单例加锁（`_prompt_manager_lock`），防止并发 MCP 工具调用创建多实例并覆盖 JSON 文件

### 测试
- 新增 `test_skill_server_session_lifecycle_and_dispatch`：覆盖 session 4 个 action + invalid action 短路；用 FakeSession/FakeClient 验证 single-session reset 不触发 client reset、reset_all 触发
- 新增 `test_skill_server_session_invalid_image_path_short_circuits`：验证无效 image_path 在 client 初始化前失败
- 新增 `tests/test_error_and_session.py`（38 个测试）：`error_handler.py` 全模块覆盖（7 个 ERROR_CODES 分支 + handle_error 字符串匹配的边界误判 + format_error_response + GeminiError + wrap_tool_error）；`session_manager.py` 全模块覆盖（store/get/remove/pop/list/clear + `_clean_expired_sessions` 过期逻辑 + get/pop 触发清理）；`extract_remote_chat_id` 两份实现的漂移守护（5 个场景）
- 新增 `tests/test_skill_server_prompts_cookie.py`（8 个测试）：skill_server 的 `prompts`（4 action + invalid + 缺参数早退）和 `cookie`（3 action + invalid + profiles 列表 + 空 profiles）此前零功能测试
- 新增 `tests/test_cookie_manager.py`（25 个测试）：`CookieManager` 核心生命周期行为覆盖（`__init__` + `_load_initial_cookie` + `_load_extra_cookies_from_env`、`update_cookie` + `on_cookie_update` 回调链含异常吞并、`get_cookie_status` VALID/EXPIRED/UNKNOWN 状态机、`needs_refresh`、`refresh_cookie` 无 browser / browser 成功 / browser 失败三条路径、`to_env_vars`、`start_monitor`/`stop_monitor` 启停幂等 + 短间隔循环不崩、`CookieData` 默认值、`CookieStatus` 枚举值稳定、4 线程并发 `update_cookie` 安全性）—— 此前仅浏览器候选探测覆盖，回调链/状态机/刷新路径零行为测试
- 新增 `tests/test_client_manager.py`（23 个测试）：`validate_config` / `get_configured_proxy` / `get_default_chat_retention_seconds` 纯函数边界覆盖（缺 PSID 抛错、本地不可达 proxy 早退、无效 retention 回退、0/负数边界）；`prepare_browser_cookie_cache` 6 条路径覆盖（force=False 早退 / source 非 browser_ 早退 / source=browser_ 创建+设 env / force=True 跳过检查 / GEMINI_COOKIE_PATH 不一致早退 / 清空 stale cache 文件）；`ClientManager` 生命周期覆盖（get_client 创建一次 / reset 清空后重建 / initialize 已初始化短路 / 并发 initialize 不重复调用 init，验证 `_init_lock` TOCTOU 修复）
- 新增 `tests/test_chat_session_lifecycle.py`（6 个测试）：`gemini_reset_session`（destructiveHint=True）4 条 delete_remote_chat 决策路径覆盖（session 不存在 / retain_chat=False 触发删除 / retain_chat=True 跳过 / session 无 cid 时 delete None）；`gemini_list_sessions` 空列表与非空列表渲染 —— 此前仅有注解形状测试
- 修复 `tools/research.py` `_walk_nested_json` 和 `tools/manage.py` `_summarize_probe_response` 的 2 处静默吞错（`except Exception: return` → 加 `logger.debug` 记录路径/rpcid 便于排障）
- 修复 `src/` 全部 ruff 错误（9 → 0）：删除未使用 import、移除无占位符 f-string 前缀、为 `client_wrapper.py` 的 facade re-export 加 `# noqa: F401`、为 `client_manager.py` try/except 后的 import 加 `# noqa: E402`
- 修复 4 个文件的 mypy 类型错误（57 → 51）：`cookie_manager.py` 的 `psidts` 可空回退、`thinking_client.py` 的 `int(learning_config[...])` 加 `# type: ignore[call-overload]` 并重构 if/return 流让 mypy 正确收窄、`client_wrapper.py` 的 `list_sessions` 过滤 None、`tools/prompts.py` 重命名循环变量 `prompt` → `item` 避免与同函数内 `Optional[dict]` 赋值的类型冲突
- 消除 `src/` 全部 mypy 错误（67 → 0，22 个源文件 clean）：`constants.py` 用 `TypedDict`（`ModelConfig` / `LearningModeConfig`）替代裸 `dict` 字面量，使 `resolve_model_name` 返回 `str` 而非 `object`，消除 4 个级联错误；`tools/manage.py` 的 `_tool` 装饰器引入 `TypeVar("_F", bound=Callable[..., Any])` 并改为通过副作用注册（`mcp.tool(...)(func)` 丢弃返回值、始终 `return func`），保留被装饰函数的声明返回类型，一次消除 18 个 dispatcher 模式 `no-any-return`；`_clamp_int` 加 `number: int` 标注 + `# type: ignore[call-overload]`；`_sanitize_account_status` / `_format_chat_export_markdown` / `gemini_move_chat_to_notebook` 用临时变量替代 `X if isinstance(X, dict) else {}` 双调用模式（mypy 不跨调用收窄）；`_read_chat_turns` 的 `turns_raw` + `isinstance` 收窄；`_move_chat_to_notebook_payload` 的 `conversation: list[Any]`；`_web_capabilities_payload` / `gemini_list_notebook_chats` 的 `payload: dict[str, Any]`；`gemini_search_chats` 标注 `matches`/`fields`/`snippets` 并重命名 `fields` → `fields_str`（避免 `list[str]` 与 `str` 同名冲突）；`gemini_get_usage_limits` 标注 `results`/`entries`；`_fetch_conversation_metadata_sources` 调用方标注 `pinned_diag`/`recent_diag`；`tools/media.py` 的 `gemini_generate_music` 转发返回值加 `cast(list[TextContent], ...)`；`skill_server.py` 的 history export 分支重命名 `chat` → `history`（避免与 search 分支 `_chat_to_dict` 返回的 `dict` 类型冲突）；`pyproject.toml` 新增 `[[tool.mypy.overrides]]` 对 `gemini_webapi.*` 和 `browser_cookie3` 设置 `ignore_missing_imports = true`（第三方包未发布 PEP 561 stubs）
- 新增 `tests/test_cleanup_test_artifacts.py`（34 个测试）：`gemini_cleanup_test_artifacts`（destructiveHint=True）此前仅有注解形状测试，**dry_run=False 会真实删除远端聊天与定时任务却零行为覆盖**——本文件补充 `_split_cleanup_markers`（空串/空白过滤/多值/保留大小写）、`_marker_hits`（大小写不敏感/None/多 marker）、`_format_cleanup_markdown`（空 payload/chats 三态 deleted-matched-error/scheduled verification_status 优先/errors 段/dry_run 提示）、`_cleanup_test_artifacts_payload`（chats dry_run 命中 id/title、dry_run=False 成功删除/删除抛异常/缺 delete_chat 能力、scan_turns 命中 turn/抛异常不中断、缺 list_chats 能力、target=chats 跳过 scheduled、target=scheduled 跳过 chats、scheduled dry_run/dry_run=False 删除/dry_run=False RPC 抛异常、空 markers 回退 codex-、max_chats 夹紧到 [1,100] 与切片窗口、缺 _batch_execute 能力）；工具层注册 + DESTRUCTIVE_REMOTE 注解 + call_tool markdown/json 双格式
- 清理 3 个测试文件中前几轮引入的未使用 import（`test_cleanup_test_artifacts.py` 的 `pytest`、`test_chat_session_lifecycle.py` 的 `pytest`、`test_client_manager.py` 的 `pathlib.Path`）
- 清理 tests/ 历史遗留 ruff 错误（4 → 0）：`test_error_and_session.py` 删未用的 `SessionData`、`test_imports.py` 删重复的 `src.tools.media` import（typo）、`test_core.py`/`test_imports.py` 的 side-effect import 加 `# noqa: F401`
- 新增 `tests/test_server_cookie_tools.py`（13 个测试）：`src/server.py` 的 `gemini_get_cookie_status`（Manager 不可用 / 可用+已设置 / 可用+未设置+需刷新）、`gemini_list_browser_cookie_profiles`（空 profiles / 含 error 条目 / 正常多字段渲染 / account_available=None 渲染 unknown / response_format=json / 抛异常 handle_error 兜底）、`gemini_get_cookie_from_browser`（成功无 profile / 成功带 profile / 失败 / 抛异常 handle_error 兜底）—— 此前仅有注解形状测试
- 新增 `tests/test_doctor_helpers.py`（26 个测试）：`gemini_doctor` 此前仅有注解形状测试，`_doctor_check` / `_doctor_overall_status` / `_format_doctor_markdown` / `_doctor_payload` 四个 helper 零直接覆盖——本文件补充 `_doctor_check`（None 值过滤 / 空 details）、`_doctor_overall_status`（空/全 ok/全 skip/混合/warn 优先/error 优先 6 种组合）、`_format_doctor_markdown`（browser=disabled / error profile / account=None / 空 recommendations / detail 白名单 4 key）、`_doctor_payload`（cookie_status 3 分支 / browser_profiles 3 分支 / alignment ok / ffprobe warn + recommendations / generated_media warn / validate_browser 推荐 / overall_status warn & ok / cookie 值不泄露）
- 新增 `tests/test_tool_helpers.py`（69 个测试）：补齐 `utils` / `constants` / `media` / `file` 四模块纯 helper 的零覆盖空洞——`validate_local_file_path`（空/路径遍历/不存在/非文件/扩展名/size/happy/无扩展名 8 分支）、`validate_image_paths`（空/逐个校验/fail-fast/非图片扩展名）、`validate_optional_image_path`（None/单张/无效）、`extract_remote_chat_id`（cid/metadata/无匹配）、`parse_response`（text/override/image/video/music 模型分流/remote_chat_id）、`get_stream_text_piece`（text_delta 优先/回退/缺失/falsy 不回退）、`resolve_model_name` / `normalize_model_alias` / `describe_model_name` / `supported_learning_modes`（查表函数全覆盖）、`resolve_media_request`（image 固定 Nano Banana 2 / music 非 pro=Lyria 3 / pro+standard=Lyria 3 / pro+extended=Lyria 3 Pro / 未知类型 passthrough）、`_safe_media_filename`（正常/特殊字符/48 字符截断/末尾剥离/空回退）、`_media_timeout`（显式/image=180/其他=600）、`_set_client_timeouts`+`_restore_client_timeouts`（无属性/max 算法/不降低/watchdog 下限 120/写回/None 跳过/往返）、`_prepend_backend_note`（空 note/空 parsed/正常拼接）、`_media_from_music_card`（mp3/mp4/无 URL/空 title 回退）、`_validate_url`（空/无 scheme/无 netloc/合法/异常）、`_validate_file_path` 转发壳
- 扩展 `tests/test_chat_session_lifecycle.py`（+19 个测试，6→25）：补齐 `gemini_send_message` 的参数回退逻辑——此前 `temporary` / `learning_mode` / `retain_chat` / `delete_after_seconds` 四参的 "None 时从 session_data 回退、传入则覆盖" 行为零分支覆盖（仅 happy path 间接走过）。新增：session 不存在早退 / image_paths 无效在 session 检查前早退 / temporary 三态（None 回退 / 显式覆盖 / session 缺失回退 False）/ learning_mode 三态（None 回退 / 显式覆盖 / 双 None 不写入 kwargs）/ retain_chat 三态 / delete_after_seconds 三态（含双 None 传 None）/ thinking_level 从 session 取+缺失回退 standard / schedule cleanup 用 session.cid / 返回 response.text / request_kwargs 含 prompt+files
- 新增 `tests/test_chat_tools.py`（14 个测试）：`gemini_chat` 与 `gemini_start_chat` 入口工具此前仅 happy path 间接覆盖，关键行为契约零断言——`gemini_chat` 补齐 image_paths 无效在 client init 前早退 / request_kwargs 全字段注入（prompt/files/model/thinking_level/gem/temporary）/ model alias 经 resolve_model_name 解析 / learning_mode 条件注入（None 省略、truthy 写入）/ cleanup_due_remote_chats 接收 client / schedule_remote_chat_cleanup_from_response 入参（response 同一对象 + retain/delete/source）/ parse_response 用 model 解析含 remote_chat_id；`gemini_start_chat` 补齐 client.start_chat 接收 model_name 与 gem / store_session 全入参（session_id 8 字符 / session / model 原始 alias / thinking_level / learning_mode / temporary / retain_chat / delete_after_seconds）/ 默认值（learning_mode=None / temporary=False / retain_chat=False / delete_after_seconds=None）/ 返回文本含 session_id 与 model_name / cleanup_due_remote_chats 接收 client / 不调 schedule cleanup（无 response）
- 扩展 `tests/test_chat_tools.py`（+13 个测试，14→27）：补齐 `gemini_chat_stream` 与 `gemini_send_message_stream` 流式工具的空流分支与累加逻辑——此前两个流式工具仅 happy path 间接覆盖，`final_response is None` 回退分支零覆盖。`gemini_chat_stream` 补齐 image_paths 无效早退 / 空流返回空文本且跳过 cleanup（`if final_response:` 守卫）/ 多 chunk text_delta 累加 / schedule cleanup 用最后一个 response / request_kwargs 全字段注入 / learning_mode 条件省略；`gemini_send_message_stream` 补齐 session 不存在早退 / image_paths 无效在 session 检查前早退 / 多 chunk 累加 / **空流仍调 cleanup 传 None（文档化与 chat_stream 的不一致：send_message_stream 无 `if final_response:` 守卫，总是调用）** / schedule cleanup 用最后 response / temporary 回退 session / learning_mode 双 None 省略
- 新增 `tests/test_file_tools.py`（21 个测试）：`gemini_upload_file` 与 `gemini_analyze_url` 此前仅有 1 个间接用例（同时验证路径遍历与 URL 格式无效早退），关键行为契约零断言——`gemini_upload_file` 补齐 无效路径在 client init 前早退 / generate_content 位置参数 prompt + files=[safe_path] + model/thinking_level/timeout=60 / analysis_prompt 默认值与自定义值 / 返回前缀 "✅ Successfully analyzed {filename}" / response.images 拼接（📷 Images + 编号 + title + url）/ remote_chat_id 拼接 / schedule cleanup 入参 / cleanup_due_remote_chats 接收 client / asyncio.TimeoutError 分支（"文件分析超时"）/ 通用 Exception 分支（"❌ Error: {e}"）/ 异常分支跳过 schedule cleanup；`gemini_analyze_url` 补齐 无效 URL 早退 / prompt 默认值（"Please analyze the content at this URL: {url}"）/ prompt 自定义拼接（用户提示 + URL + "Use the URL above..."）/ generate_content 无 files 参数 / 返回无 ✅ 前缀（与 upload_file 不同）/ response.images 拼接 / remote_chat_id 拼接 / schedule cleanup source / asyncio.TimeoutError 分支（"URL 分析超时"）/ 通用 Exception 分支
- 新增 `tests/test_media_tools.py`（36 个测试）：`gemini_generate_media` 与 `gemini_generate_music` 此前仅 5 个 happy/edge 间接用例，关键集成契约零断言——`gemini_generate_media` 补齐 image_path 无效在 client init 前早退 / generate_content 全字段注入（prompt 模板 / files / model / thinking_level / timeout）/ 有效 image_path 转 files / cleanup_due_remote_chats 接收 client / timeout 默认值（image=180 / music=600 / video=600）/ 显式 timeout 覆盖 / 零与负 timeout 回退默认 / client.timeout 临时提升并 restore（含异常与 TimeoutError 分支 finally）/ 后端路由（image 恒用 flash / image 返回 Nano Banana 2 + Pro redo note / music+flash=Lyria 3 / music+pro+standard=Lyria 3 / music+pro+extended=Lyria 3 Pro / video=Gemini Web default）/ asyncio.TimeoutError 分支（含后端标签 + "可增大 timeout_seconds"）/ 通用 Exception 分支（含后端标签 + "通用 generate_content"）/ 异常分支跳过 schedule cleanup / **空响应仍调 schedule cleanup（文档化与 chat_stream 的 `if final_response:` 守卫不一致）** / remote_chat_id 拼接 / response.media 用 effective_alias 渲染 Lyria 3 Pro 标签 / schedule cleanup source 按媒体类型分流（"gemini_generate_media:{media_type}"）/ schedule cleanup 入参（response 同一对象 + retain/delete）；music 回收路径补齐 response.media 为空时调 _fetch_music_media_from_chat 恢复 / 回收异常吞咽不崩溃 / 非 music 跳过回收；`gemini_generate_music` 补齐 转发后用 music prompt 模板 / **默认 thinking_level=extended → Lyria 3 Pro（与 generate_media 默认 standard → Lyria 3 的关键差异）** / **source 仍为 "gemini_generate_media:music" 非 "gemini_generate_music"（文档化 cleanup 归因不一致）**
- 新增 `tests/test_research_tools.py`（33 个测试）：`gemini_deep_research` 此前在 test_tool_workflows.py 有 9 个间接用例（native happy path / fallback / chat-history 轮询 / immersive report 提取），但关键集成契约零断言——入口补齐 cleanup_due_remote_chats 接收 client / 默认 thinking_level=extended（与 gemini_chat 默认 standard 不同）/ 默认 timeout_seconds=600；fallback 路径补齐 generate_content 收到 deep_research=True / model=解析后的 model_name / timeout=原始 timeout_seconds（不走 _phase_timeout 的 max(30,...) 底线，与 native 路径不同）/ prompt 含 "Requested MCP model alias" 与 "Transport model selection" / schedule source="gemini_deep_research:fallback"（与 native 的 "gemini_deep_research" 区分）/ schedule 接收 response 同一对象 / retain_chat 与 delete_after_seconds 转发（含默认 False/None）/ 返回文本前缀 "# 📚 Deep Research 计划: {query}" / 含 "- 请求模型:" 与 "- 实际研究传输:" 行 / 含 "⚠️ 当前 gemini-webapi 客户端没有暴露完整研究轮询 API" 警告 / 含 response.text 内容；native 路径补齐 has_native_api 判定（client 有 3 个方法）/ start_chat 接收 research_model（默认 Model.UNSPECIFIED）/ create_deep_research_plan 收到含模型元数据的 query / start_deep_research 接收 plan / plan.research_id 存在时调 wait_for_deep_research(plan, poll_interval=, timeout=) / poll_interval 的 max(3, ...) clamp / schedule cid 从 plan.cid 回退（chat.cid 被 _start_fresh_research_chat 清空为 ""）/ schedule source="gemini_deep_research" / retain/delete 转发 / done=True 时返回 "# 📚 Deep Research 报告:" + "完成: 是" + "## 报告" + result 文本 / 含 Research ID 与标题 / 含 model_note；thinking_scope 补齐 非 default transport（非标准 alias 如 "gemini-3-pro"）时调用 / default transport（标准 alias → Model.UNSPECIFIED）时跳过；错误处理补齐 asyncio.TimeoutError → "❌ Deep Research 超时（{N}秒）" + "AI Plus 订阅" / RuntimeError → "❌ Deep Research 失败: {str(e)}" + "该功能在您所在的区域是否可用" / 异常分支跳过 schedule cleanup / native 路径 wait 抛 TimeoutError 与 RuntimeError 也被外层捕获
- 新增 `tests/test_prompts_tools.py`（44 个测试）：`tools/prompts.py` 覆盖率 48% → 100%（136 stmts, 0 miss），此前仅 test_tool_workflows.py 间接覆盖 create + list happy path，关键行为契约零断言——`PromptManager._load_prompts` 补齐 文件不存在跳过 / JSON 解析异常吞咽并记录 ERROR 日志 / 正常加载既有 prompts 字典；`_save_prompts` 补齐 写入到不存在目录抛 FileNotFoundError 被吞咽并记录日志；`create_prompt` 补齐 返回 uuid4 字符串 / 持久化到文件 / 字段齐全（id/name/content/category/description/created_at/updated_at）/ 默认 category='通用' 与 description=''；`get_prompt` 补齐 命中 / 未命中返回 None；`list_prompts` 补齐 空 / 无分类过滤按 created_at 降序 / 按分类过滤仍降序；`list_categories` 补齐 空 / 多分类去重排序；`update_prompt` 补齐 未找到返回 False / 仅 name 部分更新（其他字段不变）/ 全量更新并刷新 updated_at / **显式空字符串更新（验证 `is not None` 检查非 falsy 检查，允许清空字段）**；`delete_prompt` 补齐 未找到返回 False / 命中删除并持久化；`get_prompt_manager` 补齐 单例创建一次（**发现 DEFAULT_PROMPTS_FILE 在类定义时绑定为 `__init__` 默认参数，monkeypatch 模块级常量无效，改用子类硬编码 tmp_path**）/ 8 线程并发返回同一实例（验证 `_prompt_manager_lock`）；`gemini_manage_prompts` 6 个 action 全覆盖——list（空/分类过滤空/非空含 category 头与 description 行/无 description 省略行）、list_categories（空/非空含每类条目数）、get（缺 prompt_id/未找到/全字段详情/缺 description 键触发 `.get('description','无描述')` 默认值）、create（缺 name/缺 content/默认 category/explicit category）、update（缺 prompt_id/未找到/成功）、delete（缺 prompt_id/未找到/成功）、**invalid action 经 MCP 抛 ToolError（FastMCP pydantic Literal 校验在 dispatch 前）+ 直接调 tool.fn 绕过校验触发 line 251 '❌ 无效的 action。' 兜底（生产经由 MCP 不可达的防御性 fallback）**、异常兜底（manager.list_prompts 抛 RuntimeError → '❌ 失败: {e}'）
- 新增 `tests/test_thinking_client.py`（48 个测试）：`thinking_client.py` 覆盖率 53% → 100%（139 stmts, 0 miss），此前仅 test_tool_workflows.py 3 个间接用例（inject_thinking_level happy / inject_web_request_options happy with h5d / _with_learning_prompt kwargs 分支），关键分支零断言——`_encode_learning_x9b` 补齐 4 个字段名（zUa/QLd/LYd/h5d）+ 不支持字段抛 ValueError；`_encode_learning_goa` 补齐 mode_id 编码为 `[[mode_id]]`；`inject_web_request_options` 补齐 5 个早退守卫（f.req 非 str / outer 非 list / outer 长度 <2 / inner_payload 非 str / inner_request 非 list）+ learning 元数据不完整抛 ValueError + learning-only 注入（[54]/[55] 设置，[79]/[80] 不设置）+ thinking-only 注入（[79]/[80] 设置，inner_request 从 69 扩展到 81）+ 已 ≥81 不扩展 + 不修改原数据 + `inject_thinking_level` 委托；`_set_web_request` 补齐 全 None 无现存 → None token / 全 None 有现存 → 复用 token / 无效 thinking_level 抛 ValueError / 无效 learning_mode 抛 ValueError / 有效 thinking+model / 有效 learning_mode（quiz → interactive_quiz → id 18）/ 中文 thinking_level '标准'（验证 .strip().lower() 查表）；`_with_learning_prompt` 补齐 learning_mode=None 不变 / args[0] 前缀 / **kwargs['prompt'] 前缀** / 空参数不变；`_prefix_learning_prompt` 补齐 非 str 不变 / 已前缀不变；`thinking_scope` 补齐 进入设置 + 退出 reset / 异常仍 reset；`generate_content` 补齐 model 传入 / model=None 省略 model kwarg / 异常仍 reset token / learning_mode 前缀注入 args[0]；`generate_content_stream` 补齐 model 传入 / model=None / 异常仍 reset token；`_install_thinking_transport` 补齐 session 为 None 早退 / 已安装（`_mcp_thinking_stream=True`）早退 / 正常 patch+设 flag / stream_with_thinking 无 active request 跳过 / url 非 GENERATE 跳过 / data 非 dict 跳过 / 命中注入（f.req 改写 + `x-goog-ext-73010990-jspb` header）+ 保留已有 headers；`init` 补齐 super().init() + _install_thinking_transport() 调用顺序。**关键设计：用 `object.__new__(ThinkingLevelGeminiClient)` 跳过 GeminiClient.__init__ 的真实网络依赖；类层面 monkeypatch `GeminiClient.generate_content` / `generate_content_stream` / `init` 异步方法；autouse fixture 在每个测试前后显式 reset `_web_request` ContextVar 防跨测试泄漏**
- 新增 `tests/test_client_wrapper.py`（39 个测试）：`client_wrapper.py` 覆盖率 45% → 97%（104 stmts, 3 miss；剩余 3 行为 import 时 `except ImportError` 防御分支，cookie_manager 在 venv 中恒可用，post-import 不可触发），是全仓此前最低覆盖模块且零直接测试——`_session_data_to_dict` 补齐 None 短路 + 全 8 字段映射；客户端门面补齐 `get_gemini_client` / `initialize_client` / `reset_client`（同时 reset client_manager + clear sessions）委托；会话门面补齐 `store_session`（全参数转发）/ `get_session`（命中 dict + 未命中 None）/ `remove_session` / `pop_session`（命中 + 未命中）/ `clear_sessions` / `cleanup_expired_sessions` / `list_sessions`（过滤 None + 空）；清理门面补齐 `schedule_remote_chat_cleanup_from_response` / `schedule_remote_chat_cleanup` 全参数转发 + `delete_remote_chat` / `cleanup_due_remote_chats`（client 显式传入跳过 init / client=None 自取 client+初始化两条分支）+ `list_pending_remote_chat_cleanup`（CleanupTask 映射 + 空）；Cookie 集成层补齐 `_on_cookie_update`（reset + 写 env，psidts 真假两分支）/ `init_cookie_manager_integration`（unavailable 短路 / auto_refresh=true / auto_refresh=false 三分支）/ `get_cookie_from_browser`（unavailable / 无 psid / update 成功写 env / update 失败不写 env / psidts 空不写 / profile 拼接 source）/ `list_browser_cookie_profiles`（unavailable / validate=True 调 cache / validate=False 跳过 cache）/ `get_cookie_status`（unavailable / available 展开 status+info）。**关键设计：`_RecordingFake` 通过 `__getattr__` 动态录制方法调用，`_async_methods` 参数让指定方法返回协程以适配 facade 的 `await`；用 `monkeypatch.setattr(cw, "_session_manager", fake)` 替换模块级单例避免触碰真实管理器状态；Cookie 函数 patch `cw.COOKIE_MANAGER_AVAILABLE` + `cw.get_cookie_manager` + `cw.init_cookie_manager` + `cw._prepare_browser_cookie_cache` + `cw.reset_client` 隔离真实浏览器/单例副作用**
- 新增 `tests/test_remote_chat_cleanup_manager.py`（26 个测试）：`remote_chat_cleanup_manager.py` 覆盖率 81% → 100%（91 stmts, 0 miss），此前仅 `extract_remote_chat_id` 在 test_error_and_session.py 有 4 个间接用例，`RemoteChatCleanupManager` 类的 5 个实例方法（`schedule_cleanup_from_response` / `schedule_cleanup` / `_delete_after_delay` / `delete_chat` / `cleanup_due_chats`）零直接覆盖——`extract_remote_chat_id` 补齐 cid 属性 / metadata list / 无匹配 / 非 c_ 前缀 4 场景回归守护；`schedule_cleanup_from_response` 补齐 cid 命中注册到 `_pending_cleanup` + 无 cid 返回 None；`schedule_cleanup` 补齐 cid falsy / retain_chat=True / retention_provider 解析 delete_after_seconds / 同步上下文 `except RuntimeError` 早退仅注册 4 分支；`_delete_after_delay` 补齐 pending delete_at 不匹配早退 / pending 缺失早退 / **happy path 调 `await self.delete_chat(cid)` 并移除 pending**（line 114）；`delete_chat` 补齐无 cid / client_initializer 解析 / client_provider 解析 / client 无 delete_chat 方法警告 / client.delete_chat 抛异常吞咽 / 成功移除 pending 6 分支；`cleanup_due_chats` 补齐 client_initializer / client_provider / 多 due 循环计数 / 部分失败计数 / 无 due 返回 0 / 显式 client 跳过解析 6 分支；**`schedule_cleanup` 在运行事件循环中调用覆盖 `loop.create_task(self._delete_after_delay(...))` 分支**（line 103），通过 `asyncio.sleep(0.05)` 让 create_task 创建的协程运行至 delete_chat 被调用。**关键设计：`SimpleNamespace` + `AsyncMock` 构造带 async `delete_chat` 的假 client；同步上下文调用 `schedule_cleanup` 触发 `except RuntimeError` 分支避免创建真实删除任务；`_delete_after_delay` / `delete_chat` / `cleanup_due_chats` 用 `asyncio.run` 在隔离事件循环中运行**
- 扩展 `tests/test_client_manager.py`（+8 个测试，23→31）：`client_manager.py` 覆盖率 89% → 96%（114 stmts, 5 miss；剩余 5 行为两处 `except ImportError` 防御分支，cookie_manager 与 thinking_client 在 venv 中恒可用，post-import 不可触发），此前 `get_extra_cookies` 零直接测试、`_create_client` 的 extra_cookies 加载分支与 `prepare_browser_cookie_cache` 的异常吞咽分支未覆盖——`get_configured_proxy` 补齐 **本地端口可达 happy path**（用真实 socket 监听占用端口验证 `create_connection` 成功后透传 proxy，line 46）；`get_extra_cookies` 补齐 unavailable 返回空 / cookie_data 为 None 返回空 / cookie_data 存在返回 extra_cookies 三分支（lines 72, 75, 76）；`prepare_browser_cookie_cache` 补齐 COOKIE_MANAGER_AVAILABLE=False 早退（line 82）/ `cache_dir.chmod` 抛 OSError 静默吞咽（lines 95-96）/ stale cache 文件 `unlink` 抛 OSError 记录 debug 日志不中断（lines 101-102）；`ClientManager._create_client` 补齐 extra_cookies 非空时加载 cookies 并调 prepare_browser_cookie_cache（lines 156-158）。**关键设计：本地 proxy 可达用真实 `socket.socket` 监听避免 mock 网络栈；chmod/unlink 异常用 `monkeypatch.setattr(Path, "chmod"/"unlink", raising_fn)` 全局打桩 + monkeypatch 自动还原；`_create_client` 的 extra_cookies 分支用 monkeypatch 替换 `get_extra_cookies` + `prepare_browser_cookie_cache` 避免真实文件系统副作用**
- 新增 `tests/test_server_core_tools.py`（7 个测试）：`server.py` 覆盖率 83% → 94%（90 stmts, 5 miss；剩余 5 行为 `main()` + `__main__` 阻塞入口，不可测试），此前 `gemini_get_tool_manifest` / `gemini_reset` / `gemini_doctor` 三个核心工具仅有注解形状测试，零行为覆盖——`gemini_get_tool_manifest` 补齐 markdown 默认格式 / json 格式 / scope 参数透传 3 分支（lines 97-100）；`gemini_reset` 补齐 调 reset_client + 返回固定文本（lines 106-107）；`gemini_doctor` 补齐 markdown 默认格式（含 browser/validate_browser 默认值 chrome/False）/ json 格式 / browser 与 validate_browser 参数透传 3 分支（lines 117-120）。**关键设计：通过 `server.mcp.call_tool(name, kwargs)` 经 MCP 分发调用（与 test_server_cookie_tools.py 同模式），monkeypatch 底层 `_tool_manifest_payload` / `_format_tool_manifest_markdown` / `_doctor_payload` / `_format_doctor_markdown` / `reset_client` 隔离真实依赖；ManifestScope 的 pydantic Literal 校验在 MCP 分发前执行，scope 必须为有效枚举值**
- 新增 `tests/test_cookie_manager_browser.py`（54 个测试）+ 扩展 `tests/test_cookie_manager.py`（+5 个测试，25→30）：`cookie_manager.py` 覆盖率 62% → 97%（404 stmts, 14 miss；剩余 14 行全部为 `except ImportError` 防御分支，browser_cookie3 与 gemini_webapi 在 venv 中恒可用，post-import 不可触发），此前浏览器 Cookie 提取的静态方法与异步 Gemini 账号验证函数零直接覆盖——`_read_cookie_jar` 补齐 cookie 名过滤 / 空值过滤 / 非 google.com 域名过滤 3 分支；`_chrome_base_path` 补齐 darwin / win32 / linux 3 平台分支（lines 258-263）；`_browser_cookie_candidates` 补齐 非 chrome 早退 / base=None 早退 / auto+profile 候选收集 / auto 异常跳过 / profile 异常跳过 / require_psid=False 保留无 PSID 候选 / 无候选返回空 7 分支（lines 276-304）；`_chrome_selected_profile_directory` 补齐 base=None / 无 Local State / 非法 JSON / last_used 字符串 / last_active_profiles 列表 / profile 非 dict / 无选中键 7 分支；`_select_valid_cookie_candidate` 补齐 单候选直返 / 多候选验证成功返回 / 多候选验证失败回退首个 3 分支（lines 329-345）；`_select_named_cookie_candidate` 补齐 未找到返回空 / 大小写不敏感匹配 2 分支；`get_cookies_from_browser` 补齐 不支持浏览器 / profile+candidates 命名选择 / 无 profile 单候选 / 无 candidates fallback read_jar / 无 PSID 返回空 / 异常兜底 6 分支（lines 155-184）；`list_browser_cookie_profiles` 补齐 不支持浏览器 / 有 candidates 渲染 / 无 candidates fallback / fallback 读取异常 / validate=True 合并验证 5 分支（lines 206-242）；`_validate_cookie_candidates_async` 补齐 可用+scheduled>0 / 不可用跳过 / 可用但 scheduled==0 回退 first_available / init 异常跳过 / close 异常吞咽 5 分支（lines 370-403）；`_validate_cookie_candidate_profiles_async` 补齐 可用标记 / init 异常记录 / close 异常吞咽 3 分支（lines 406-439）；`_validate_cookie_candidate_profiles` 同步线程包装器补齐 正常返回 / 线程异常返回空 2 分支（lines 359-367）；`_probe_scheduled_registry_count` 补齐 batch_execute 异常返回 0 / 恢复 language / happy path 解析 JSON 返回 count / body 非字符串返回 0 / parsed[0] 非列表返回 0 / 跳过非匹配 part 6 分支（lines 442-472）；`get_cookie_manager` / `init_cookie_manager` 模块级单例补齐 懒加载创建 / 同实例返回 / init 覆盖现有 3 分支（lines 700-727）；`_monitor_loop` 补齐 过期+auto_refresh 触发 refresh_cookie / 异常吞咽+sleep(60) 2 分支（lines 622-633）。**关键设计：`_make_async_probe(value)` 构造返回协程的 async 替身——不能用 `lambda: asyncio.run(...)` 因为 `_validate_cookie_candidates_async` 已在事件循环内运行，嵌套 `asyncio.run` 会抛 RuntimeError 被 except 吞掉；`_make_fake_gemini_client_class` 用 AsyncMock 构造 init/close 支持副作用；`_probe_scheduled_registry_count` happy path 需 monkeypatch `gemini_webapi.types.RPCData` 为简单类（生产代码 `RPCData("XPSWpd", "[]")` 在当前 gemini_webapi 版本会失败——pydantic BaseModel 不接受位置参数 + XPSWpd 非有效 GRPC 枚举值，这是已知生产 bug，函数实际恒返回 0）；`_browser_cookie_candidates` 的 chrome 路径用 tmp_path + 空文件模拟 profile 目录结构**
- 新增 `tests/test_research_report_helpers.py`（104 个测试）：`tools/research.py` 覆盖率 70% → 100%（440 stmts, 0 miss），此前渲染 helper / 沉浸式报告提取 / 入口工具 / native recovery 全部零直接覆盖——纯渲染 helper 补齐 `_markdown_sections`（切分/空 body 过滤/无 heading）/ `_iter_source_links`（非 dict 过滤/空 title 回退/limit）/ `_plain_excerpt`（0 字符/剥离/短/长/1 字符）/ `_title_from_markdown`（H1 提取）/ `_safe_filename`（特殊字符替换/strip/空回退）；`_render_research_artifact` 补齐 6 种 artifact_type 分发 + 未知类型抛 ValueError + webpage 有/无 section + infographic 无 section；报告动作补齐 `_research_report_actions_payload`（有/无 title）/ `_format_research_report_actions` / `_create_research_report_artifact`（写文件 + 回退 text）/ `_format_research_report_artifact`；沉浸式报告提取补齐 `_walk_nested_json`（dict/list/JSON-string/非 JSON/无效 JSON string）/ `_extract_sources_from_node`（过滤 gstatic/googleusercontent/重复/非 str/无有效）/ `_extract_deep_research_immersive_report`（无匹配/无 cite/heading/多候选取最长/markdown title 回退/immersive_id 空/JSON-string payload）；`_fetch_deep_research_immersive_report` 补齐 空 cid / 无 `_batch_execute` / **ImportError（`monkeypatch.delattr` GRPC，比 `setattr` 设 None 更准确——`from X import Y` 在 Y 为 None 时仍成功，仅在 Y 缺失时抛 ImportError）** / batch_execute 异常 / 无报告 / happy path with/without sources；`_request_completed_research_report` 补齐 无 send_message / happy path / 异常返回 None；native recovery helpers 补齐 `_resolve_deep_research_transport_model`（flash/lite/pro/thinking/None/empty/非 alias/ImportError）/ `_is_default_deep_research_transport`（UNSPECIFIED/string/other）/ `_is_capability_probe_false_negative`（双 marker/缺 marker）/ `_is_research_start_message` / `_is_research_completion_message`；`_create_deep_research_plan` / `_start_deep_research_with_recovery` / `_start_fresh_research_chat` 补齐 happy path / 非 probe 异常 / 缺 recovery attrs / output 无 plan / 合成 plan / 保留已有字段 / recovery happy / 超时回退 fetch_latest / 超时回退 plan.cid / 无 fetch 返回空 / fetch 返回 None / 清空 cid/rid/rcid / attr set 异常吞咽；`_wait_for_deep_research_by_chat` 轮询分支补齐 completion + immersive 报告 / completion + followup 报告 / completion + followup 是 start message 跳过 / latest_text 与 start_text 不同 / 超时返回 running；`_format_deep_research_result` 边界补齐 not done + start message 清空 / not done 无 report 用 start_text / done 无 report 用 plan_text 无前缀 / 空 statuses；入口工具补齐 `gemini_list_research_report_actions`（无报告/markdown/json）/ `gemini_create_from_research_report`（无报告/markdown/json + 6 种 artifact_type 参数化分发）/ `gemini_deep_research` 走 `_wait_for_deep_research_by_chat` 分支（plan.research_id 空 → 不调 `wait_for_deep_research`）。**关键设计：`_long_report(body_seed, length)` 构造 ≥1000 字符 + `## ` 标记的报告正文满足 `_extract_deep_research_immersive_report` 的报告阈值；`_patch_entry_env` 统一 patch 入口工具的 3 个外部接缝（get_gemini_client / initialize_client / _fetch_deep_research_immersive_report）；`_NativeClientWithoutResearchId` 替身类触发 line 90 的 `_wait_for_deep_research_by_chat` 分支；轮询循环测试用 `call_count` dict 计数器控制 `fetch_latest_chat_response` 返回不同值 + `monkeypatch.setattr(asyncio, "sleep", _no_sleep)` 避免真实等待**
- 新增 `tests/test_skill_server_helpers.py`（158 个测试）：`skill_server.py` 覆盖率 77% → 99%（648 stmts, 6 miss；剩余 6 行为 `except ImportError` mcp 缺失守卫 + `main()`/`__main__` 阻塞入口，post-import 不可触发），此前 `PromptManager` 全套 / `_format_response` / `_truncate_text` / `_error_text` / 配置 helper / `chat` / `history` / `account` 子处理器 / `scheduled` 子处理器 / `create` / `edit` / `session` / `doctor` / `cleanup` 全部零直接覆盖——纯 helper 补齐 `_truncate_text`（短/空/长截断+marker/rstrip）/ `_error_text`（日志+TextContent）/ `_normalize_model`（f/t/p/l/lite/pro 别名+大小写+passthrough）/ `_normalize_media_type`（img/picture/photo/大小写/passthrough）；`_format_response` 补齐 text only / 空 text / images 迭代（含空 url 跳过）/ videos / audio_url / backend_label 前缀 / backend_label+note / remote_chat_id 注入（有/无）/ 组合 media+backend；`PromptManager` 补齐 `__init__`+`_load`（文件缺失/正常读/损坏 JSON/缺 prompts key）/ `_save`（持久化/IOError 吞咽）/ `list_all`（按 name 大小写不敏感排序）/ `get_by_name`（大小写不敏感/未命中）/ `create`（生成 id+持久化）/ `delete`（命中+持久化/未命中 False）；配置 helper 补齐 `_ensure_config_dir`（创建/幂等）/ `_init_default_prompts`（缺失复制/已存在跳过/无默认文件跳过）/ `get_prompts` 单例缓存；`chat` 补齐 invalid image 早退 / 无 session 走 generate_content（learning_mode 注入/省略）/ session 命中走 send_message（learning_mode 从 session 回退/显式覆盖/双 None 省略）/ 顶层 except；`history` 补齐 list（空/非空/has_more）/ search（缺 query/无 read_chat/无匹配/标题命中/id 命中/scan_turns 收集 snippet/has_more）/ read（缺 chat_id/无 read_chat/无 turns/渲染 turns/chat None）/ export（缺 chat_id/无 read_chat/渲染 markdown/list_chats 命中 metadata）/ delete（缺 chat_id/无 delete_chat/调 delete_chat）/ Invalid action / 顶层 except；`account` 子处理器补齐 `_account_models`（空/渲染）/ `_account_features`（无 _batch_execute/并发 probe ok/异常 type name/reject_code）/ `_account_links`（空/渲染）/ `_account_usage`（渲染/空）/ `_account_library`（空/渲染）/ `_account_notebooks`（空/渲染）/ `_account_scheduled`（渲染/空）/ `_account_modes`（空/渲染）/ `_account_status`（无 inspect/空 summary/渲染/非 dict）/ `_account_manifest` / 主入口 dispatch（auth-free/client-based/未知 action 兜底 status）/ 顶层 except；`scheduled` 子处理器补齐 `_scheduled_list`（empty_hint/渲染）/ `_scheduled_get`（缺 id/matched_task False/默认 not_found/渲染 found/disabled 无 state）/ `_scheduled_create`（缺 title/instructions/invalid hour/visible_in_registry/not_visible_in_nonempty_registry/registry_empty_unverified/readable_by_id_registry_empty/readable_by_id_not_visible_in_registry/无 created_id）/ `_scheduled_delete`（缺 id/rpc_unconfirmed/still_visible/deleted_state_by_id/registry_empty_active_or_unknown/not_visible_active_or_unknown/registry_empty_not_readable/not_visible_not_readable）/ 主入口 dispatch（list/get/create/delete/Invalid action/无 _batch_execute/顶层 except）；`create` 补齐 invalid image / 顶层 except / image happy path（prompt 前缀+model+backend_label）/ music happy path（audio_url）；`edit` 补齐 invalid image / 顶层 except / happy path（files+prompt）；`session` 补齐 `_session_send`（invalid session/learning_mode 回退/显式覆盖/thinking_level 回退）/ `_session_create` / `_session_list`（空/渲染）/ `_session_reset`（单个/全部+reset_client）/ Invalid action / invalid image 早退 / send dispatch / 顶层 except；`doctor` happy path / 顶层 except；`cleanup` happy path / 顶层 except；`cookie` status/get/profiles happy path + Invalid action + 顶层 except。**关键设计：`_patch_client_seams` 统一 patch 3 个 client_wrapper 接缝（get_gemini_client / initialize_client / cleanup_due_remote_chats）；`_ACCOUNT_AUTH_FREE_ACTIONS` 在模块加载时绑定原函数引用，patch 模块属性无效，改为 patch handler 调用的底层函数（如 `_web_capabilities_payload`）；`@mcp.tool` 装饰器返回原函数，直接 `await skill_server.tool(action="unknown")` 绕过 pydantic Literal 校验覆盖 Invalid action 兜底；`_extract_rpc_bodies` 的嵌套 list 结构需精确构造（`bodies[0][0]` 为 list 时 entries 从其元素解析）**
- 新增 `tests/test_manage_scheduled_actions.py`（50 个测试）：`tools/manage.py` 覆盖率 73% → 79%（1844 stmts, 396 miss），此前定时操作 create/delete MCP handler 与 remy_goals 分页 helper 零直接覆盖——`_fetch_remy_goal_conversation_refs` 补齐 5 种 stopped_reason 分支（no_next_page_token / max_items / empty_page / no_new_unique_items / max_pages）+ 参数 clamp（page_size/max_pages/max_items 非法值收敛）+ 同页去重 + 跨页去重 + 无 id entry 跳过 + response_length 累加 + request_payload 首页/续页格式 + 空 bodies 回退 empty_page + pages 诊断元数据；`gemini_create_scheduled_action` 补齐 参数校验（空 title/instructions/timezone + hour 负数/超 23）+ 无 `_batch_execute` 能力早退 + verification_status 矩阵（visible_in_registry / not_visible_in_nonempty_registry / registry_empty_unverified / readable_by_id_registry_empty / readable_by_id_not_visible_in_registry / verification_error / get_task_error）+ 无 created id（ok=False）+ 非 200 状态 + response_format=json + schedule_label 空/非空渲染 + 顶层 except；`gemini_delete_scheduled_action` 补齐 空 action_id + 无 `_batch_execute` + verification_status 矩阵（deleted_state_by_id / still_visible_in_registry / not_visible_not_readable_by_id / registry_empty_not_readable_by_id / not_visible_active_or_unknown_by_id / registry_empty_active_or_unknown_by_id / verification_error / get_task_error）+ 空 bodies（ok=False）+ 非 200 状态 + response_format=json + 顶层 except + 删除 RPC payload 格式断言。**关键设计：`_FakeBatchClient` 按队列返回响应文本 + 捕获 `_RawRPCData` payload 供断言；`_extract_rpc_bodies` 的嵌套 list 结构需精确构造（`bodies[0]=body`，`body[0]=raw_entries`，`body[1]=next_page_token`，故 bodies 形如 `[[[entry,...], token]]` 三层）；MCP handler 经 `register_manage_tools(mcp, layers=["all"])` 注册后通过 `mcp.call_tool` 分发，patch `get_gemini_client`/`initialize_client` + `_extract_rpc_bodies`/`_fetch_scheduled_registry`/`_fetch_scheduled_task_by_id` 隔离真实 RPC**
- 新增 `tests/test_manage_history_account_tools.py`（77 个测试）：`tools/manage.py` 覆盖率 79% → 83%（1844 stmts, 313 miss），此前 4 个历史/账号只读 MCP handler 仅 `test_tool_workflows.py` 有 happy path 间接覆盖，关键分支零断言——`gemini_search_chats` 补齐 空 query / 纯空白 query 早退 / scan_turns 无 read_chat 早退 / `_batch_execute` 路径（走 `_fetch_recent_conversation_metadata`）vs `list_chats` 路径（走 client_cache 诊断）/ title-only / id-only / title+id 排序去重 / scan_turns turn 匹配收集 snippet / read_chat 异常吞咽为 snippet error 不中断循环 / `has_remote_more` 覆盖 `has_more`+`next_offset` / markdown snippet 换行替换空格 / scan_turns 时不显示 "当前只搜索标题/ID" footer / limit/offset 非法值 clamp / 顶层 except；`gemini_get_tool_mode_status`（零现有测试）补齐 无 `_batch_execute` 早退 / 空 bodies / body 非列表 / leading_enabled 三态（True/False/None）/ body[1] 非列表 entries 空 / markdown 渲染 mode_id+available+quota+used+state + has_more 下一页 + 说明行 / 顶层 except；`gemini_get_usage_limits` 补齐 无 `_batch_execute` 早退 / scope→probe_names 映射（all=2 / quota=1 / model_state=1，验证 `_batch_execute` 调用次数）/ bodies 结构 4 分支（空 / bodies[0] 非列表 / bodies[0] 空列表 / first 非列表）/ markdown 暂无条目 + reset= 渲染（有/无）/ 顶层 except；`gemini_list_notebook_chats` 补齐 无 `_batch_execute` 早退 / `_find_notebook` 未命中（空 notebooks / 非空可用标题渲染）/ 命中（by id / exact title / casefold title）/ items 空 "暂无最近对话" / time 渲染 / has_more 下一页 / response_format=json ok 矩阵（False/True）/ untitled 回退 / 顶层 except。辅助 helper 同步覆盖：`_turn_matches_query`（role/text/大小写不敏感/空 query/无匹配）/ `_read_chat_turns`（无 read_chat 抛 RuntimeError / 截断+limit 切片 / history None 空 turns）/ `_parse_tool_mode_entry`（非列表 / 完整 6 字段 / 部分 / available 非 bool→None）/ `_parse_usage_entry`（非列表 / 完整含 reset_time / 无 reset / reset 非列表 / reset seconds 非数字 / 空列表）/ `_find_notebook`（by id / 未命中 / exact title / casefold title / exact 多匹配 None / folded 多匹配 None / 空输入 / 空白 strip）/ `_fetch_notebook_chats`（单页无 token 停止 / 多页 token 驱动 / 空 entries 即使有 token 也 break / limit+offset clamp）。**关键设计：`_FakeBatchClient` 按队列返回响应文本 + 捕获 payload；`_ListChatsClient`/`_ReadChatClient` 走 client_cache 路径；patch `_extract_rpc_bodies` / `_fetch_native_notebooks` / `_fetch_notebook_chats` / `_fetch_recent_conversation_metadata` 隔离真实 RPC；注意 `_fetch_notebook_chats` 的 body 结构为 `[something, next_page_token_str_or_None, raw_entries]`（body[1]=token，body[2]=entries），与 `_fetch_remy_goal_conversation_refs` 的 `body[0]=entries, body[1]=token` 顺序相反**
- 新增 `tests/test_manage_notebook_history_facade.py`（38 个测试）：`tools/manage.py` 覆盖率 83% → 86%（1844 stmts, 265 miss），此前 3 个 notebook/history facade MCP handler + 1 个 payload helper 仅 `test_tool_workflows.py` 有 happy path 间接覆盖，关键分支零断言——`_move_chat_to_notebook_payload` 补齐 默认 project_type=2（conversation[0]=chat_id, [7]=notebook_id, [13]=[2]）/ 自定义 project_type / compact JSON separators；`gemini_move_chat_to_notebook` 补齐 空 chat_id / 纯空白 chat_id 早退 / 缺 notebook_id+title 早退 / 无 `_batch_execute` 早退 / notebook 未命中（空 + 非空可用标题渲染 + json payload ok=False）/ ok+verified（✅ 渲染 + MUAZcd payload 捕获 + rpcid 断言）/ ok+not verified（⚠️ 渲染 + 未验证到 chat_id）/ not ok（❌ 渲染 + status_code != 200）/ 空 bodies → not ok / json payload 三态 / updated_entry None（body[1] 非 list）/ project_type 从 notebook 传递（int / 非 int 回退默认 2）/ 顶层 except / chat_id strip；`gemini_history` facade 补齐 默认 action=list / action=list/scan/search/read/export dispatch（各 action header 断言）/ **未知 action 经 pydantic Literal 校验在 dispatcher 前被拒（ToolError），dispatcher 的 `❌ 不支持的 history action` 兜底分支是 Literal 类型保护下的防御性死代码，无法经 mcp.call_tool 触达——用 `pytest.raises(Exception)` 断言 pydantic 拒绝** / search query 转发；`gemini_scan_chat_history_sources` 补齐 无 `_batch_execute` 早退 / 空 sources 渲染空结构 / source_counts 渲染 / coverage_warnings（stopped_reason in {max_items, max_pages} 渲染 / 其他 stopped_reason 不渲染）/ json payload / include_notebook_chats（_fetch_native_notebooks 未命中跳过 / 命中 _fetch_notebook_chats 分页）/ include_remy_goals / 参数 clamp（limit/offset/max_items_per_source/page_size/max_pages_per_source 非法值收敛）/ markdown 当前页 items + has_more（含 📌 pinned 渲染，需给高 timestamp item 使其排在前页）/ 顶层 except。**关键设计：`_source_block` 辅助构造 `_fetch_conversation_metadata_sources` 返回的 source_block（含 diagnostic.stopped_reason）；`_merge_conversation_source_items` 按 `(timestamp, id) reverse=True` 排序，无 timestamp 时 id 字典序大的在前——测试数据需给期望排在前页的 item 更高 timestamp；`gemini_history` 的 closure 引用无法 patch 模块属性，通过控制底层 helper（`_fetch_conversation_metadata_sources` / `_fetch_native_notebooks` / `_fetch_notebook_chats`）验证 dispatch 正确性；用 `pytest.raises(Exception)` 而非 `ToolError` 避免 `mcp.server.fastmcp.exceptions` 新增 mypy import-not-found 基线**
- 测试套件 70 → 943 → 993 → 1070 → 1108 passed

### 性能优化
- 上提 `gemini_webapi.utils` 导入到模块级，消除 `_extract_rpc_bodies`/`_summarize_probe_response` 在分页循环内的函数级 import
- `research.py` 3 处循环内 `re.match` → 模块级 `re.compile`
- `_merge_conversation_source_items` 的 `sources_by_id` 从 `list`（O(k) 成员测试）→ `set`（O(1)），输出时 `sorted()` 物化
- `WEB_UI_CAPABILITIES` 深拷贝从 `json.loads(json.dumps())` → `copy.deepcopy`
- `_account_features`（8 probe）与 `_account_usage`（2 probe）串行 RPC → `asyncio.gather` 并发，保持输出顺序，N×RTT → 1×RTT

### 仓库结构
- 抽取 676 行纯数据到新模块 `src/tools/manifest_data.py`（`WEB_UI_CAPABILITIES` / `WEB_FEATURE_PROBES` / `TOOL_MANIFEST`），`manage.py` 从 4591 → 3924 行，通过 re-export 保持向后兼容
- `.gitignore` 新增 `*.egg-info/`、`build/`、`dist/` 规则

### 文档与分发
- 将默认 `README.md` 改为英文公开首页，并新增 `README.zh-CN.md` 保留完整中文入口
- 新增 `docs/assets/gemini-web-mcp-banner.svg`，改善 GitHub 仓库首屏视觉呈现
- 更新打包清单，确保源码包包含中文 README 和 README banner 资产
- 更新 `docs/architecture.md` 项目结构，补充 `manifest_data.py`、`skill_server.py` 等模块


### 发布与分发
- 新增 `scripts/package_release.py`，一键构建 wheel、sdist 和 standalone Codex skill zip
- 新增 `MANIFEST.in`，确保源码包包含 docs、evaluations 和公开 `.agents/skills/gemini-web-mcp`
- 新增 `docs/launch-kit.md`，提供安装链接、分发清单和中英文社交媒体发布文案
- 更新 README 顶部版本、Release/Skill/License badges 和公开分发说明


### 发布与分发
- 新增公开 repo skill 路径 `.agents/skills/gemini-web-mcp`，让 Codex 和 skill 聚合站可直接从 GitHub 发现/安装
- 补充 README 中的 GitHub skill 安装命令、手动安装步骤，并明确 skill 与 MCP server runtime 的分层关系
- 扩展 skill packaging 测试，校验 `.codex/skills` 本地副本和 `.agents/skills` 公开副本保持一致


### Web UI 对齐
- 对齐 2026-05-22 观察到的 Gemini Web 模型面：`3.1 Flash-Lite`、`3.5 Flash`、`3.1 Pro`
- 复核 2026-06-18 Pro 账号网页面：工具菜单包含上传、Drive、导入代码、图片、视频、Canvas、Deep Research、音乐、学习辅导、个性化/Labs；设置菜单包含活动记录、记忆导入、用量限额、定时操作、公开链接等入口
- 将 `standard` / `extended` 固化为独立 `thinking_level` 选择
- 新增 `learning_mode`，对齐 2026-06-19 Web 前端学习辅导 Input Companion：
  互动测验、抽认卡、模拟测试、备考/学习指南会写入对应 `X9b` / `GOa` 请求字段
- 在媒体工具中显式写入网页实际后端规则：
  图像首轮固定为 Nano Banana 2，音乐按 `flash` / `pro` 分流到 Lyria 3 / Lyria 3 Pro

### 账号和聊天管理
- 新增 `gemini_inspect_account`，检查当前账号 Web RPC/能力状态并隐藏原始 RPC 预览
- 新增 `gemini_read_chat`，按 chat ID 读取历史对话 turns
- 新增 `gemini_search_chats`，分页搜索历史对话标题/ID，并可显式扫描当前页正文片段
- 新增 `gemini_export_chat`，将单个历史对话导出为 Markdown 或 JSON
- 新增 `gemini_delete_chat`，删除指定远端历史对话
- 新增 `gemini_scan_chat_history_sources`，按前端观测到的多个历史 RPC 过滤器、
  原生 notebook 对话和 Remy goal conversation 引用深度枚举聊天元数据，便于验证历史覆盖范围
- 新增 `gemini_history` 作为只读历史聚合入口，把 list/scan/search/read/export 合并到一个 agent-facing 工具
- 新增 `gemini_cleanup_test_artifacts`，默认 dry-run 查找并可选删除显式 marker 匹配的测试聊天和测试定时任务
- 新增 `gemini_get_tool_manifest`，为 agent 暴露工具安全、隐私、分页、可用分组、当前启用状态和推荐工作流元数据
- primary MCP 工具增加 MCP `ToolAnnotations`，标记只读、远端修改、本地修改和 destructive 操作
- 新增 `gemini_probe_web_features`，用浏览器实测到的只读 RPC 探测 Library、公开链接、用量、个性化、记忆导入等新版 Web 入口
- 新增 `gemini_get_web_capabilities`，返回 Pro 网页模型、思考等级、工具菜单、设置入口和 MCP 覆盖清单
- 新增 `gemini_list_public_links`、`gemini_get_usage_limits`、`gemini_list_library_capabilities`，把可稳定解析的新版 Web 入口从 probe 升级为只读工具
- 新增 `gemini_list_notebooks`、`gemini_list_notebook_chats`、`gemini_move_chat_to_notebook`，支持 Gemini Web 原生笔记本列表、笔记本内最近对话读取，以及把已有聊天移动到目标笔记本并校验
- 新增 `gemini_notebooks` 作为原生 Notebook 只读聚合入口，供 `history-organize` 使用
- 新增 `gemini_list_scheduled_actions`，列出定时操作页面返回的 active/inactive 任务条目
- 新增 `gemini_get_scheduled_action`，用前端确认的 `kwDCne` / GetTask RPC 按 ID 只读校验定时操作
- 新增 `gemini_create_scheduled_action` / `gemini_delete_scheduled_action`，支持每日定时操作的创建和按 ID 删除
- 新增 `gemini_get_tool_mode_status`，只读读取 Canvas / 学习辅导等工具模式附近出现的 Web 内部状态枚举
- 新增 `gemini_account_inventory` 作为账号只读聚合入口，把 capabilities/status/features/links/usage/library/notebooks/scheduled/modes/models 收口到一个工具
- 新增 `gemini_list_research_report_actions` / `gemini_create_from_research_report`，为 Deep Research 完成后的网页实测“创建”菜单提供 MCP 侧等价入口，可生成网页、信息图、测验、抽认卡、音频概览脚本和自定义应用规格；当前未观测到稳定原生网页菜单 mutation RPC
- `gemini_list_chats` 增加 `offset`、`response_format` 和分页元数据
- `gemini_list_public_links`、`gemini_list_library_capabilities`、`gemini_list_scheduled_actions`
  和 `gemini_get_tool_mode_status` 增加统一分页元数据，便于 agent 分页读取账号内容
- 忽略不可达的本地 `GEMINI_PROXY`，避免旧代理端口导致客户端初始化失败
- 从 Chrome 读取 Cookie 时验证多个本地 profile，隔离 gemini_webapi cookie cache，
  并优先选择能读取 scheduled registry 的 profile
- 新增 `gemini_list_browser_cookie_profiles`，用于列出 Chrome profile 的非敏感账号
  诊断；`gemini_get_cookie_from_browser` 支持 `profile` 参数，便于手动对齐多账号上下文
- 新增 `gemini_doctor`，用于只读预检工具面、Cookie 状态、浏览器 profile 对齐和媒体校验依赖
- `gemini_list_browser_cookie_profiles` 增加 `chrome_selected_profile` 诊断；定时操作
  create/delete 增加 `verification_status`、by-id 可读性和 `task_state` 校验，区分 RPC 已接受、registry 已验证和 deleted tombstone
- 低 token `src.skill_server` 增加 `history` 和 `scheduled`，其中 `scheduled` 支持 list/get/create/delete；并扩展 `history` 支持 search/export、`account` 支持 manifest/features/links/usage/library
- 新增项目内 Codex skill：`.codex/skills/gemini-web-mcp`，用于指导 agent 安全使用 manifest、聊天记录和验证流程

### 工具面收缩
- 新默认工具组改为 `core`
- 新增意图型工具 profile：`model`/`chat` 仅调用模型，`history` 只读历史整理，
  `history-organize` 允许将历史对话移动到 native Notebook，`account-read` 只读盘点账号
  Web surface，`scheduled-read`/`scheduled-admin` 分离定时操作读写权限
- `history` 和 `account-read` 改为 facade-first：普通 agent 分别只看到 `gemini_history`
  和 `gemini_account_inventory`；旧颗粒工具继续保留在 `manage` / `all` 作为兼容维护面
- `all` 保留完整维护/验证工具面，但不再加载本地提示词工具
- `manage` 内部改为按 profile 分层注册，避免只想整理历史的 agent 同时拿到账号写操作、
  scheduled mutation 或 Gems 管理工具
- 移除 `gemini_list_features`，减少低价值枚举型工具
- 当前默认工具面为 `core` 加始终可用的 manifest/cookie helpers；`all` 额外提供
  `gemini_history`、`gemini_account_inventory`、`gemini_notebooks`、
  `gemini_inspect_account`、`gemini_cleanup_test_artifacts`、`gemini_list_chats`、`gemini_search_chats`、
  `gemini_scan_chat_history_sources`、`gemini_read_chat`、`gemini_export_chat`、`gemini_delete_chat`、
  `gemini_get_web_capabilities`、`gemini_probe_web_features`、`gemini_list_public_links`、
  `gemini_get_usage_limits`、`gemini_list_library_capabilities`、
  `gemini_list_notebooks`、`gemini_list_notebook_chats`、`gemini_move_chat_to_notebook`、
  `gemini_list_scheduled_actions`、`gemini_get_scheduled_action`、`gemini_create_scheduled_action`、
  `gemini_delete_scheduled_action`、`gemini_get_tool_mode_status`、
  `gemini_list_models`、`gemini_manage_gems`
- `gemini_manage_prompts` 保留为 `prompts` 可选分组，不属于默认工具面

### 文档与验证
- 补充 Gemini Web live UI 覆盖说明和媒体路由说明
- 新增 `evaluations/gemini_web_mcp_contract.xml`，提供 17 个只读、稳定答案的 MCP contract evaluation
- 扩展测试以校验媒体后端分流、学习模式请求注入、工具 annotations、evaluation XML、Codex skill 和默认工具面

---


### ✨ 主要更新

#### 全新架构
- 完整重新设计的架构
- 清晰的模块分离（server, client_wrapper, constants）
- 工具模块化（chat, research, media, file, manage）

#### 支持最新模型
- `fast` → gemini-3-flash
- `thinking` → gemini-3-flash-thinking
- `pro` → gemini-3.1-pro

#### 媒体生成增强
- **图像**：Nano Banana 2（所有模型）
- **视频**：Veo 3.1（最长 60 秒，所有模型）
- **音乐**：Lyria 3 Clip（30秒）和 Lyria 3 Pro（完整）

#### Deep Research 支持
- 新增 `gemini_deep_research` 工具
- 深度研究与报告生成

#### 完整工具系统（历史记录）

以下为早期 v0.2.0 历史中的工具面；当前支持情况以
`docs/tools.md` 和 `docs/api-reference.md` 为准。

**对话工具：**
- `gemini_chat` - 单次对话（支持图片输入）
- `gemini_start_chat` - 创建会话
- `gemini_send_message` - 发送会话消息
- `gemini_list_sessions` - 活跃会话列表
- `gemini_reset_session` - 重置会话

**媒体工具：**
- `gemini_generate_media` - 通用媒体生成
- `gemini_generate_music` - 便捷音乐生成

**文件工具：**
- `gemini_upload_file` - 文件上传
- `gemini_analyze_url` - URL 分析

**管理工具：**
- `gemini_list_chats` - 历史聊天
- `gemini_manage_gems` - Gem 管理（CRUD）
- `gemini_list_models` - 模型列表
- `gemini_list_features` - 功能列表
- `gemini_health_check` - 健康检查
- `gemini_reset` - 重置客户端

#### 完整文档系统
- docs/README.md - 文档中心
- docs/quickstart.md - 快速开始
- docs/tools.md - 工具使用
- docs/models.md - 模型选择
- docs/configuration.md - 配置说明
- docs/faq.md - 常见问题
- docs/architecture.md - 技术架构
- docs/changelog.md - 更新历史

### 📦 技术改进
- 客户端封装（client_wrapper.py）
- 常量系统（constants.py）
- 工具注册架构
- 会话管理系统
- 更好的错误处理

### ⚙️ 配置更新
- 环境变量 `GEMINI_PSID`（必需）
- `GEMINI_PSIDTS`（推荐）
- `GEMINI_PROXY`（可选）
- `GEMINI_AUTO_REFRESH`（默认 true）

### 📚 依赖
- gemini-webapi >= 1.20.0
- mcp (FastMCP)
- Python >= 3.10

---


### 最初版本
- 基础认证机制
- 简单对话功能
- 项目框架
