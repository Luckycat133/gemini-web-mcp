# Full Review Repairs — 2026-10-02

The full review identified 26 product findings and two compatibility issues in the vendored evaluator. Repairs preserve public tool names and delegate shared behavior to services/lifecycle owners. The release remains `0.2.2` preparation until the release PR passes review.

## Finding map

| ID | Repair | Regression evidence |
| --- | --- | --- |
| F01 | Cleanup requires positive fresh `verified_absent`; rejection or missing evidence remains retryable. | `test_runtime_review_regressions.py`, `test_remote_chat_cleanup_manager.py` |
| F02 | Compact and primary browser authentication updates retire the old client; changed contexts cancel old sessions/jobs and late cleanup. | `test_runtime_review_regressions.py`, `test_authentication_cleanup_scope.py` |
| F03 | Reset is destructive/open-world; partial cleanup and warnings survive the adapter. | `test_runtime_review_regressions.py`, `test_tool_workflows.py`, reviewed MCP annotation fingerprint |
| F04 | Prompt writes atomically replace the file and propagate failures; damaged sources remain untouched. | `test_prompt_storage.py` |
| F05 | An explicit browser profile fails closed. | `test_runtime_review_regressions.py`, `test_cookie_manager_browser.py` |
| F06 | Cookie data and client locks no longer wait in opposite order; update notifications remain ordered. | `test_runtime_review_regressions.py` |
| F07 | RPC parsers reject malformed child rows, missing IDs, and invalid/repeated continuations. | `test_rpc_contracts.py`, `test_account_review_regressions.py` |
| F08 | Scheduled deletion checks valid HTTP/RPC/parser observations and matching IDs before absence/tombstone acceptance. | `test_account_review_regressions.py`, `test_manage_scheduled_actions.py` |
| F09 | Caller cancellation does not cancel shared deletion; actual deletion cancellation is diagnosed. | `test_runtime_review_regressions.py` |
| F10 | Client creation/init failures return cleanup/lifecycle diagnostics. | `test_runtime_review_regressions.py` |
| F11 | Expiry skips sessions with active sends. | `test_runtime_review_regressions.py` |
| F12 | Proxy user/password fields are redacted in logs. | `test_runtime_review_regressions.py` |
| F13 | Music audio/video save independently, including suffixed and existing filenames. | `test_media_recovery_deadlines.py` with upstream `GeneratedMedia.save` |
| F14 | Completion requires the requested output kind; music requires audio. | `test_artifacts.py`, `test_media_recovery_deadlines.py`, `test_skill_server_helpers.py` |
| F15 | Images/WAV are decoded, other AV files require stream evidence; malformed files fail and decoder absence remains unverified. | `test_artifacts.py`, valid media fixtures |
| F16 | A single media deadline bounds generation, recovery, validation, and save, including endless HTTP 206 polls. | `test_media_recovery_deadlines.py` |
| F17 | Timeout/watchdog overrides are isolated by task and client, including reconnects and cancellation. | `test_thinking_client.py`, `test_media_recovery_deadlines.py` |
| F18 | File/URL inputs do not make empty output successful. | `test_file_tools.py` |
| F19 | Research recovery and no-ID polling/report/follow-up calls obey the remaining phase deadline. | `test_research_review_regressions.py` |
| F20 | Changed text and refusals are insufficient; report evidence and non-success terminal states are preserved. | `test_research_review_regressions.py`, `test_research_report_helpers.py` |
| F21 | Prompt identity uses UUIDs rather than normalized names. | `test_prompt_storage.py` |
| F22 | Prompt transactions reload under a stable cross-process lock before writing. | `test_prompt_storage.py`, including two spawned writers |
| F23 | History stops at the source bound with partial coverage and no repeated empty cursor. | `test_account_review_regressions.py` |
| F24 | Content read failures are independent of matches; all-failed scans fail and partial scans disclose coverage. | `test_account_review_regressions.py`, `test_compact_history_contract.py` |
| F25 | Doctor separates cookie presence from successful account validation and excludes failed accounts. | `test_account_review_regressions.py` |
| F26 | Scoped Notebook metadata includes the enabled facade and correct profile availability. | `test_research_review_regressions.py`, `test_evaluations.py` |
| V01 | Project evaluator bridge uses SDK v2 stdio/SSE/HTTP transport APIs. | `test_mcp_builder_evaluation.py`, real auth-free stdio connection |
| V02 | The bridge serializes native content blocks, metadata, structured content, and error/incomplete states. | `test_mcp_builder_evaluation.py`, unchanged upstream engine with fake provider |

## Contracts and documentation

The MCP fingerprint change is limited to `gemini_reset` annotations; tool names and input/output schemas are unchanged. The evaluation XML and manifest describe the revised safety and evidence boundaries. Runtime Skill references cover authentication changes, cleanup, incomplete history, artifact verification, and operation recovery. The upstream `mcp-builder` Skill files remain intact; use the maintained [evaluation entrypoint](agent-verification.md#model-backed-mcp-evaluation).

## Evidence limits

Offline regressions use sanitized RPC fixtures, valid image/WAV/video fixtures, two real Prompt writer processes, upstream media save with a fake HTTP boundary, and real auth-free stdio MCP calls. These do not verify current account entitlements or upstream generation availability. Local video/MP3 verification requires an available decoder; absence is explicitly unverified. Model-backed evaluator runs and live-account canaries remain separate opt-in evidence.

The previous signed-in [live observations](live-ui-coverage.md) are dated and unchanged by this repair pass. Durable restart-safe operation/cleanup storage and a verified MCP Omni route remain in the [development roadmap](development-status.md).

## Verified repair gates

Local run on 2026-10-02, Python 3.13:

| Gate | Result |
| --- | --- |
| Ruff / Mypy | Passed; 69 source files checked by Mypy |
| Complete offline suite | 1,818 passed |
| Architecture/distribution checklist | 261 passed |
| Representative profile catalogs | Seven primary profiles, eleven compact tools, five assist tools passed |
| Source stdio protocols | Modern and legacy discovery/list/call passed for all three surfaces in model/core profiles |
| Agent Skills | All three project Skills passed the repository reference validator |
| Distribution | Wheel, sdist, and two Runtime Skill bundles built; version consistency passed |
| Independent wheel installation | `pip check`, installed entrypoints/resources/profiles, core modern/legacy protocols, and isolated `uvx` onboarding passed |

The installed wheel was exercised outside the checkout with no Gemini account calls. Hosted CI, model-backed evaluation, live account acceptance, and release publication are separate evidence.

## Native media follow-up — 2026-10-02

The follow-up addresses native image/music selection and disposable generation chats. Primary and compact surfaces retain their public names, arguments and annotations; shared request, recovery and saving behavior lives in services.

| Area | Repair and regression evidence |
| --- | --- |
| Native request | Public frontend selectors are injected into the real upstream request builder; shape/conflict drift fails before generation HTTP, internal arguments do not leak, and native creation does not silently repeat after an SDK API error. `test_native_media_transport.py` covers SDK 2.0 and 2.1.1. |
| Created artifacts | Search `WebImage` does not satisfy creation. Original URI identity survives full-size URL mutation; filenames are reserved atomically and failed/cancelled reservations are released. `test_media_generation_workflows.py` and `test_artifacts.py`. |
| Music recovery | Failed transport, rejected/missing envelopes and malformed observed music-card URLs remain typed recovery failures. A valid empty read remains empty. Both surfaces retain failed recovery chats. `test_media_generation_workflows.py` and `test_rpc_contracts.py`. |
| SDK side effects | Request-local media copies suppress unrequested audio/video thumbnails, preserving existing files and the original response. Real SDK saves run against a sealed HTTP fixture in `test_media_generation_workflows.py`. |
| Partial/queued work | A ready verified local file does not complete an explicitly queued request or permit source deletion. Partial-save timeout is triggered during the second save after first-file verification. `test_media_generation_workflows.py`. |
| Source cleanup | Owned new chats with finished verified outputs or definitive empty responses use bounded positive deletion read-back. Recovery, explicit retention and delayed cleanup remain separate. Automatic due work rejects changed retention, deadline, job identity or attempts after its snapshot; source attribution survives immediate and late terminal results. `test_ephemeral_chat_cleanup.py`. |
| Browser access | OS refusal produces `BROWSER_COOKIE_ACCESS_DENIED`; modern `Network/Cookies` retains the Chrome profile name and selects one database per profile. `test_cookie_browser_access_regressions.py`. |

Independent reviewers reproduced recovery, thumbnail and queued-output defects before their repairs. After the stable source handoff, the four media/native findings passed independent checks on SDK 2.0.0 and 2.1.1. These are offline checks, not current account acceptance.

### Verified follow-up gates

| Gate | Result |
| --- | --- |
| Ruff / Mypy | Passed; 71 source files checked by Mypy |
| Complete offline suite | 1,962 passed on Python 3.13 / gemini-webapi 2.0.0 |
| Architecture/distribution checklist | 269 passed |
| SDK compatibility | 2.1.1: 173 native/media/parser cases and 66 runtime cases passed; independent media review resolved all four findings |
| Agent Skills | All three project Skills passed reference validation |
| Distribution | Wheel, sdist and two Runtime Skill bundles built; `0.2.2` version/tag consistency passed |
| Independent installation | Clean wheel with gemini-webapi 2.1.1 / MCP 2.2.0; `pip check`, resources, four entrypoints and seven primary profile catalogs passed |
| Installed MCP protocol | Primary/compact/assist, model/core, modern `2026-07-28` and legacy `2025-11-25` discovery/list/call passed |
| Isolated onboarding | `uvx` wheel preflight passed with `credentials_accessed=false` |

Signed-in generation/deletion acceptance for the new native path remains **NOT_RUN**: Chrome data access was denied by macOS, and browser control later encountered the locked Mac. No old test-chat deletion was positively verified in this pass. The [dated live results](live-ui-coverage.md) and [native source evidence](native-media-mode.md) remain distinct.
