# Testing and Evidence

Use the narrowest test that proves a changed contract, then run the broader gates required by its boundary. Keep fixture, repository, protocol, installed-product, Skill, agent-use, and live evidence distinct.

## Evidence Levels

1. **Unit/fixture** — pure helpers, parsers, services, SQLite repositories, fake clients, sanitized fixtures.
2. **Repository contracts** — Ruff, Mypy, pytest, architecture checklists, schemas, snapshots, docs assertions.
3. **Protocol** — real MCP stdio discovery/list/call for every affected entrypoint and supported protocol era.
4. **Installed product** — wheel/sdist/Skill build, clean install, entrypoints, resources, `pip check`, onboarding.
5. **Skill distribution** — Agent Skills validation, direct install/byte comparison, bundle contents, trigger evaluations.
6. **Agent-use** — a real agent selects the surface and completes a realistic task, including Artifact handoff or operation recovery.
7. **Live Gemini** — explicitly authorized current-account calls; the only evidence for current upstream behavior.

A large unit-test count does not replace protocol, installation, Skill trigger, agent-use, or live evidence.

## Fast Local Sequence

```bash
python -m py_compile <changed-python-files>
python -m pytest -q <focused-tests>
python -m ruff check <changed-python-files-and-tests>
python -m mypy <changed-source-files>
git diff --check
```

## Maintained Repository Gates

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
git diff --check
```

Do not encode volatile passing-test counts in Skills or badges. Cite the exact hosted run when reporting CI.

## Focused Server Surface Contracts

For every dedicated surface, test:

- exact MCP server name and console entrypoint;
- deterministic ordered tool list;
- no unrelated tools;
- `gemini_` prefix on every public tool;
- input/output schema, descriptions, annotations, structured content, and representative calls;
- stable errors and operation/Artifact/verification fields;
- delegation to shared services rather than duplicated execution;
- compatibility behavior where the old surface exposes the same workflow.

### Function and Account Catalogs

Verify the function and explicit account tools match the current registered catalogs, including schemas and annotations. Function `gemini_ask` covers current-web requests with source evidence and Notebook-backed ordinary Chat; `gemini_run_task` explicitly starts Task. The assist compatibility server retains its five-tool catalog including `gemini_search`, without Notebook attachment arguments. Function media, Task and Notebook Chat starts always return a handle; account cleanup is limited to the MCP-owned ledger, not title matching. Cookie, Doctor and local Prompts stay outside both catalogs. The explicit legacy profiles, assist and compact remain available.

Golden catalogs and schemas are reviewed contracts. Do not regenerate them automatically after failure.

## Assistance Tests

### `gemini_ask`

- ordinary second-opinion result;
- model/thinking controls;
- structured failure/text agreement;
- no mixed-input machinery required for pure text.
- current-web and natural-language app requests preserve observed sources and truthful grounding; app execution is not inferred from prose.
- Model routing: the client gate implements account-bound basic Flash text-quota evidence and fixed Lite thinking; verify default Flash, 60-second expiry and basic-Flash-success revocation, no replay of the original request, and requested/effective/fallback/selectability metadata. Installed-SDK offline and public MCP integration checks passed; do not exhaust an account to test the unverified live quota boundary. Advanced-thinking, file, research and media-only limits do not authorize Lite. Preserve explicit Pro/Argon choices even when unavailable.

### `gemini_search` (assist compatibility)

- observed sources produce `grounded`;
- source-free answer produces `answer_only`;
- unavailable/failed are distinct;
- URL normalization and deduplication;
- recency/domain/language bounds;
- no invented source metadata.

### Understanding

- one-image simple path;
- object/mapping response normalization;
- typed mixed inputs with stable IDs;
- per-input accepted/skipped/failed state;
- multiple files/images/URLs without silent dropping;
- bounded input count/size;
- result preserves source identities.

### Research

- starts asynchronously by default;
- returns an opaque handle immediately;
- preserves upstream IDs;
- no duplicate start after timeout;
- report result becomes an Artifact.

### Notebook and Task

- Function Ask with Notebook IDs preserves `execution_mode="chat"` without Task flags; Run Task explicitly uses `execution_mode="task"`. Default phase timeout is 600 seconds.
- Caller-supplied handles prevent duplicate starts after disconnect; status is metadata only, and result reads preserve the original CID/RID/candidate without regeneration or model changes.
- Require exact completed-candidate evidence and, for Task, the same chat's completed goal metadata. A Task goal without a completed reply is not a saved answer.
- Workers save and verify the full answer Artifact before cleanup even when the caller has not requested the result. Cover crash recovery before/after save, restart local retrieval, file size/hash validation and unread/unknown cleanup protection.
- Notebook objects and sources are never temporary-chat cleanup targets; explicit retention preserves only the owned chat lifecycle requested by the caller.
- Account Notebook defaults to source-picker metadata. The same product's `source="gemini"` project view must use strict envelopes; missing/error envelopes cannot become an empty success. Move targets need fresh exact-ID/type resolution and independent read-back, without guessed ID conversion.
- Task account list/get expose metadata only; delete requires an exact Task chat ID, no mutation replay and independent absence verification. Check union destructive annotations for `gemini_tasks` and destructive `gemini_run_task`.
- Authorized live source-QA evidence needs a synthetic marker absent from the prompt, public MCP disconnect/restart retrieval and independent chat absence; ordinary Notebook Chat must not create a Task goal.
- Verify Notebook/source app mutations through fresh metadata or exact source evidence, not the generated answer alone. Keep rich Task control, Task schedules and experimental ProcessSession probes separate from current production claims.

## Skill Tests

For each Runtime Skill:

```bash
skills-ref validate .agents/skills/<skill-name>
```

Also verify:

- intent-focused description;
- main file under 500 lines;
- focused one-level references;
- no machine-specific paths;
- every named tool/entrypoint exists;
- package version and bundle version are correct;
- direct repository installation matches source bytes;
- no duplicate Skill name across discovery roots.

### Trigger Evaluations

Maintain positive cases and near-miss negatives for function/account routing. Test paraphrases, mixed languages, indirect requests, and multi-intent prompts.

Expected behavior:

- assistance and generation requests select `gemini-mcp-function`;
- explicit Gemini account requests select `gemini-mcp-account`;
- repository work selects `gemini-web-mcp-development`;
- internal account use does not select account without user account-data intent.

## SQLite OperationService Tests

Cover:

- schema creation and forward migrations;
- atomic operation creation;
- high-entropy opaque IDs;
- provider/chat ID preservation;
- queued/running/completed/timed_out/failed/expired transitions;
- legal and illegal transitions;
- restart recovery;
- cross-client status/result lookup;
- concurrent status/result;
- idempotent terminal reads;
- seven-day expiry/pruning with injectable clock;
- no unsupported cancellation claim;
- missing/corrupt/expired records;
- Artifact identity continuity;
- no duplicate upstream start on retry;
- proof that prompts, chat/report text, Cookies, raw responses, and generated bytes are never persisted.

If protocol-native MCP Tasks are added, separately test capability negotiation, task-handle mapping, down-level compatibility, and that clients without the extension continue to use the explicit operation tools.

## Durable Cleanup Tests

Cover the implemented SQLite queue and use the remaining items as future acceptance targets; pagination, cancellation and terminal-failure management are not current queue APIs:

- SQLite migrations and restart-safe pending work;
- pending/running/completed/failed/cancelled states;
- retry/backoff and terminal failure;
- list/retry/cancel with pagination;
- duplicate deletion/idempotency;
- direct-ID authority;
- positive `verified_absent` read-back;
- temporary-chat bypass;
- no private content in storage.

## Artifact Tests

For image/video/audio/report:

- local, remote, queued, partial, empty, failed;
- path resolution and destination containment;
- existing non-empty regular file;
- MIME/type;
- image dimensions;
- audio/video duration when available;
- resource-link shape when supported;
- requested/effective/observed backend separation;
- source Artifact identity for edits;
- operation-to-result Artifact identity continuity;
- downstream handoff in an agent-use evaluation.

## Mutation Tests

For every create/update/move/delete:

- accepted and positively verified;
- accepted but not observed;
- incomplete verification;
- read-back error;
- mismatch or still present;
- blank/invalid identifiers before network access;
- idempotency/retry behavior;
- compatibility prose agrees with structured state.

## Package and Onboarding

```bash
python scripts/package_release.py --outdir dist
python scripts/check_version_consistency.py --artifacts-dir dist

python -m venv /tmp/gemini-wheel-smoke
/tmp/gemini-wheel-smoke/bin/python -m pip install dist/*.whl
/tmp/gemini-wheel-smoke/bin/python -m pip check
```

Outside the checkout, smoke every installed entrypoint and protocol surface. For the public source path:

```bash
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
uvx --from "$SOURCE" gemini-mcp-onboarding
```

The active package and public Skills use `0.2.1`; preserve `v0.2.0` as immutable history.

## Compatibility Runtime Skill

Until dedicated products ship, verify the umbrella Skill:

- routes by user intent;
- uses compact first when sufficient;
- routes file/URL/Research to narrow primary profiles;
- does not require manifest before known tasks;
- returns information to the agent for search/understanding;
- requires downstream use for generated Artifacts;
- starts Research asynchronously and preserves IDs;
- only loads detailed account/tool reference for explicit account or recovery needs.

## Agent-Use Evaluations

A real agent should complete:

1. sourced current-web answer;
2. screenshot diagnosis followed by a code fix;
3. design/image plus code comparison;
4. image generation followed by insertion into an app/document;
5. image edit followed by replacement of the source asset;
6. Research start, interruption/restart, result recovery, and report use;
7. video/music start and final Artifact handoff;
8. explicit history selection and verified account mutation.

Record surface/tool selection, calls, retries, duplicate starts, structured state, Artifact/operation handoff, and final task completion.

## Live Evidence Boundary

A bounded 2026-08-08 run observed authentication, text, sessions, typed history, and verified chat cleanup. It does not prove media, files, URLs, Research, account mutations, tier, locale, or Web build.

A full live baseline should verify the new focused surfaces, classify entitlement absence separately from drift/failure, and account for every created resource by returned ID. Retain only sanitized state and metadata—never credentials, private content, or raw responses.

## Implemented source and focused contract routing (2026-10-03)

Re-read pyproject.toml and the current sources before reporting implementation.
The inspected package exposes `gemini-mcp-server`, `gemini-mcp-skill-server`,
`gemini-mcp-onboarding`, and `gemini-mcp-assist`. `src/surfaces/assist.py` owns
five assistance tools. Function and account entrypoints use shared SQLite operation and cleanup services; compatibility surfaces remain available.

| Change | Shared owner / contract | First useful checks |
|---|---|---|
| Assist schema/catalog/presentation | `src/surfaces/assist.py`, `src/adapters/mcp_sdk.py` | `tests/test_assist_surface.py`, `tests/test_assist_skill.py` |
| Source-backed search | `src/services/search.py` | `tests/test_search_service.py`, assist surface checks |
| Mixed input understanding | `src/services/understanding.py` | `tests/test_understanding_service.py`, assist surface checks |
| Research async start/recovery IDs | `src/services/research.py`, `src/tools/research.py` | `tests/test_research_tools.py`, assist surface checks |
| Usable generated artifacts | `src/services/artifacts.py` | `tests/test_artifacts.py`, `tests/test_media_tools.py` |
| Account/history parity or verified mutation | `src/services/history.py`, `gems.py`, `cleanup.py` | `tests/test_history_typed_parity.py`, `tests/test_manage_gem_verification_contract.py`, `tests/test_remote_chat_cleanup_manager.py` |
| Public skill packaging | Existing .agents skill directories and `scripts/package_release.py` | `tests/test_development_skill.py`, `tests/test_skill_packaging.py`, `tests/test_package_integrity.py` |

ResearchService persists operation metadata in SQLite before issuing a remote
start and checkpoints the observed chat identity. Primary research-profile
`gemini_get_operation_status` and `gemini_get_operation_result` recover these
explicit handles across restart; the focused assist catalog remains five tools.
Ordinary records expire after seven days, while a protected research operation
with an upstream chat ID remains recoverable past that period. Verify storage
failures, expiry, concurrent terminal reads and ready-document
evidence before claiming completed report recovery. Compatibility research and media starts also return durable operation handles by default; verify same-handle status/result and artifact recovery on primary, compact and assist surfaces.
A timeout after start must not trigger a duplicate upstream research request.

Focused documentation/skill checks can run without Gemini access:

```bash
python -m pytest -q tests/test_development_skill.py
```

Use the repository environment. `test_development_skill.py` validates the exact
public file set and reference links: extend existing references for a routing
clarification rather than adding stray files to a published Skill. If the
package contract intentionally changes, update its test deliberately with the
implementation; do not regenerate golden output just to remove a failure.

For a runtime edit, select tests above, then protocol and installed-wheel checks
only when their boundary is affected. The fastmcp shim remains a fixture; source
inspection, mock pass, real MCP discovery and live Gemini calls are different
claims. No account/cookie access is needed to maintain this routing table.
