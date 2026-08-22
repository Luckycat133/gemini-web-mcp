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

### Assistance Expected Catalog

```text
gemini_ask
gemini_search
gemini_understand_image
gemini_understand
gemini_research
```

Reject account/admin and generation-only tools in this catalog.

### Creation Expected Catalog

```text
gemini_generate_image
gemini_edit_image
gemini_generate_video
gemini_generate_music
gemini_get_operation_status
gemini_get_operation_result
gemini_cancel_operation
```

Reject account tools, generic chat, and unbounded operation lists.

### Account Expected Catalog

```text
gemini_history
gemini_notebooks
gemini_scheduled
gemini_gems
gemini_prompts
gemini_account
gemini_cleanup
```

Verify list/read versus mutation semantics remain distinguishable in structured output and annotations.

Golden catalogs and schemas are reviewed contracts. Do not regenerate them automatically after failure.

## Assistance Tests

### `gemini_ask`

- ordinary second-opinion result;
- model/thinking controls;
- structured failure/text agreement;
- no mixed-input machinery required for pure text.

### `gemini_search`

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

Maintain positive cases and near-miss negatives for assist/create/account. Test paraphrases, mixed languages, indirect requests, and multi-intent prompts.

Expected behavior:

- assistance requests select `gemini-assist`;
- generation requests select `gemini-create`;
- explicit Gemini account requests select `gemini-account`;
- repository work selects `gemini-web-mcp-development`;
- understanding an image does not select create;
- creating an image does not select assist;
- internal account use does not select account without user account-data intent.

## SQLite OperationService Tests

Cover:

- schema creation and forward migrations;
- atomic operation creation;
- high-entropy opaque IDs;
- provider/chat ID preservation;
- queued/running/completed/timed_out/cancel_requested/cancelled/failed/expired transitions;
- legal and illegal transitions;
- restart recovery;
- cross-client status/result lookup;
- concurrent status/result/cancel;
- idempotent terminal reads and repeated cancel;
- seven-day expiry/pruning with injectable clock;
- best-effort versus provider-confirmed cancellation;
- missing/corrupt/expired records;
- Artifact identity continuity;
- no duplicate upstream start on retry;
- proof that prompts, chat/report text, Cookies, raw responses, and generated bytes are never persisted.

If protocol-native MCP Tasks are added, separately test capability negotiation, task-handle mapping, down-level compatibility, and that clients without the extension continue to use the explicit operation tools.

## Durable Cleanup Tests

Cover:

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
