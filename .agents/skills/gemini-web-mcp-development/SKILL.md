---
name: gemini-web-mcp-development
description: Develop, review, debug, test, package, and release the gemini-web-mcp repository as an agent-first Gemini Web gateway for text, images, video, music, files, URLs, and Deep Research. Use for repository audits, regressions, MCP tool or schema changes, primary/compact adapter parity, shared services, Gemini Web RPC compatibility, multimodal artifacts, CI, packaging, onboarding, live canaries, versioning, or release work.
license: AGPL-3.0-only
compatibility: Requires a checkout of Luckycat133/gemini-web-mcp, Python 3.11+, git, and the project development dependencies. Most unit, contract, package, protocol, and skill work must remain runnable offline. Live Gemini verification requires the separately gated dedicated test-account canary.
metadata:
  author: Luckycat133
  project: gemini-web-mcp
  scope: development
  version: "0.2.0"
---

# Gemini Web MCP Development

Use this skill to change the repository. Use the separate `gemini-web-mcp` skill to operate an installed server.

Install this development skill directly from the repository with:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

## Mission

Maintain a broadly usable Gemini Web compatibility gateway that gives MCP-compatible agents dependable access to text, sessions, images, video, music, files, URLs, and Deep Research while hiding unstable Gemini Web transport details behind testable contracts.

Optimize for completed agent workflows and verifiable outputs, not raw tool count.

## Inspect the Current Baseline First

This repository moved through its original P0-P2 roadmap. Do not restart completed architecture work from an old plan.

Before proposing changes, inspect:

```bash
git status -sb
git log --oneline -20
```

Then read the relevant source, tests, `docs/changelog.md`, open issues/PRs, and the active `pyproject.toml`. Treat the checkout as the source of truth; this skill is a routing guide, not a substitute for repository inspection.

The maintained baseline already includes:

- async-safe shared client initialization and deliberate reset/retirement;
- one collision-resistant `SessionService` used by primary and compact adapters;
- serializable `DomainResult`, stable domain errors, operation states, and diagnostic IDs;
- a shared chat service and collected-stream normalization;
- one artifact model for remote/local/queued/empty/failed multimodal outputs;
- extracted history, account, Notebook, scheduled-action, Gem, manifest, doctor, and cleanup services;
- a centralized Gemini Web RPC contract/parser registry;
- observable conversation cleanup state;
- single-source package versioning, direct dependencies, package data, and three console entrypoints;
- MCP Python SDK v2 with modern and legacy protocol smoke coverage;
- Ruff, Mypy, tests, contract snapshots, clean-wheel, release, skill, and protocol gates;
- an opt-in live compatibility canary whose persisted reports are sanitized;
- public onboarding plus checked-in Codex, Claude, and VS Code examples.

Verify these claims against the current checkout, but do not describe them as missing foundations unless a regression proves otherwise.

## Working Mode

For every non-trivial task:

1. Resolve the exact branch, current commit, affected tool/profile, and recent changes.
2. Reproduce the defect or characterize the existing contract before editing.
3. State the user-visible or developer-visible contract and acceptance criteria.
4. Find the lowest shared layer that owns the behavior: domain, service, infrastructure adapter, MCP adapter, or presentation text.
5. Add a focused regression test that fails for the original defect.
6. Implement one coherent fix; avoid parallel business implementations.
7. Synchronize primary/compact surfaces, manifest data, docs, evaluations, examples, and both skill copies only where the contract requires it.
8. Run targeted checks first, then the maintained repository gates.
9. Report what was fixture-tested, package-tested, protocol-tested, and actually observed live.

## Focus Rules

- Do not turn normal feature or refactor work into a generic policy review. Address concrete behavior, compatibility, data flow, output evidence, or failing tests.
- Do not repeat completed roadmap phases because an old reference still mentions them. Confirm current code and history first.
- Do not block useful work on speculative Gemini Web changes. Isolate assumptions behind adapters and add fixtures or a canary probe.
- Do not add a second or third implementation when an existing service can own the behavior.
- Do not preserve an accidental behavior merely because an old test encoded it. Decide whether it is a public contract, then update the regression deliberately.
- Do not claim a backend, mutation, stream, or artifact from wrapper prose alone. Distinguish requested, effective, inferred, and observed evidence.
- Do not increase tool count unless the new surface improves agent discoverability, composability, or task completion.

## Current Repository Map

- `src/server.py`: primary profile-based MCP adapter.
- `src/skill_server.py`: compact low-token MCP adapter; still contains some legacy presentation and facade code that should migrate incrementally, not through a rewrite.
- `src/adapters/`: project-owned MCP SDK v2 boundary, text/structured-result adapters, and artifact rendering.
- `src/domain/`: typed results, errors, operation state, conversation lifecycle, stream metadata, and artifact contracts.
- `src/services/`: shared chat, stream, artifact, history, account, Notebook, scheduled, Gem, manifest, doctor, cleanup, and related application behavior.
- `src/infrastructure/`: centralized reverse-engineered RPC contracts and pure response parsers.
- `src/client_manager.py`, `client_wrapper.py`, `session_manager.py`, `remote_chat_cleanup_manager.py`, `cookie_manager.py`, `thinking_client.py`: runtime lifecycle and Gemini Web transport infrastructure.
- `src/tools/`: primary granular tool registration and compatibility facades.
- `src/onboarding.py`: installed-product preflight and explicitly gated live examples.
- `compatibility/`: live-canary schemas, fixtures, and dependency evidence.
- `tests/`: unit, regression, parity, schema, package, protocol, and workflow contracts.
- `scripts/`: contract, package, version, protocol, release, onboarding, and canary verification.
- `.agents/skills/` and `.codex/skills/`: byte-identical public/local copies of runtime and development skills.

## Engineering Invariants

### 1. One Result, Two Presentations

`TextContent._meta.domain_result` is the machine-readable authority. Compatibility text may be more concise, but it must never name a different error code, success state, backend, or artifact state.

- Derive coded failure text from the same `DomainResult`.
- Use shared adapter helpers instead of hard-coded error labels.
- Add a regression whenever legacy text and structured content can diverge.
- Preserve useful human text when it does not contradict the typed result.

### 2. Async Lifecycle Correctness

- Never hold a synchronous lock across `await`.
- Concurrent initialization callers share one attempt.
- Caller cancellation must not cancel initialization needed by other requests.
- Reset must prevent stale initialization from publishing and must retire replaced clients deliberately.
- Concurrency tests must force real suspension.

### 3. Deterministic Session Semantics

- Session IDs are opaque and collision-resistant.
- Unknown IDs return `SESSION_NOT_FOUND` and change no state.
- Reset-one and reset-all remain distinct.
- Sends are serialized per session.
- Primary and compact adapters use the same `SessionService` and lifecycle metadata.

### 4. Shared Business Services

MCP adapters register tools, validate adapter-specific inputs, choose presentation, and translate results. Reusable request construction, Gemini calls, parsing, lifecycle, mutation verification, and artifact logic belong below them.

When touching a legacy compact handler, prefer migrating that bounded workflow to an existing or new shared service rather than expanding `skill_server.py`.

### 5. Verifiable Multimodal Outputs

- Use the shared artifact model for images, video, audio, files, reports, webpages, and data.
- Keep remote URI and local path independently.
- Distinguish `remote`, `local`, `queued`, `empty`, and `failed`.
- Verify local files and relevant metadata before claiming a saved deliverable.
- Keep requested model, request model, effective backend, and observed backend separate.

### 6. Reverse-Engineered Compatibility

- Put RPC IDs, source paths, payload builders, parsers, observed dates, and verification strategies in the centralized registry.
- Parse success, empty, rejection, and changed-shape states explicitly.
- Verify ambiguous mutations by read-back.
- Record fixture evidence separately from live canary evidence.

### 7. Package and Protocol Integrity

- `pyproject.toml` is the persisted version and dependency source.
- Runtime data lives inside the package and is loaded with `importlib.resources`.
- Both MCP entrypoints and onboarding must work from an installed wheel outside the source tree.
- Modern and legacy MCP negotiation, tool schemas, structured content, and representative profiles remain under contract tests.

### 8. Honest Long-Running Semantics

- Current `_stream` tools collect Gemini upstream chunks and return one MCP result; report `delivery=collected`.
- Normalize delta, cumulative, duplicate, stale, and mixed chunks without duplicated output.
- Preserve operation and continuation identifiers for queued, running, timed-out, or resumable work.
- Propagate caller cancellation.

## Current Priorities

Use [roadmap.md](references/roadmap.md) for acceptance criteria. The active direction is now P3 maintenance and adoption:

1. integration regressions and primary/compact contract drift;
2. completing typed-result coverage for remaining legacy facades;
3. reducing compact-adapter presentation debt through shared services;
4. obtaining the first deliberate live-canary baseline and handling upstream drift;
5. cutting a coherent public release with an explicit version/compatibility policy;
6. improving ecosystem onboarding from real user reports.

The deferred Glama account/listing issue is external account-side work, not an implementation prerequisite.

## Change Playbooks

### Bug Fix

1. Reproduce at the lowest stable layer.
2. Add a regression that proves the wrong behavior and the expected result.
3. Check both text and structured content when an MCP adapter is involved.
4. Fix the shared owner, not each caller independently.
5. Run the focused test, related parity tests, and contract checklist.

### Tool or Schema Change

1. Inspect current tool snapshots and manifest/profile membership.
2. Define input, structured result, compatibility text, annotations, and operation state.
3. Implement through a service or pure adapter.
4. Update both surfaces only when they expose the workflow.
5. Re-baseline golden schemas intentionally and explain why.

### Gemini Web Drift

1. Identify transport, envelope, RPC, parser, or verification stage.
2. Add a sanitized fixture reproducing the changed shape.
3. Update the registry/parser rather than scattering new indices or IDs.
4. Preserve an explicit `UPSTREAM_CHANGED` result when evidence remains insufficient.
5. Run the opt-in canary only when authorized and report whether live behavior was observed.

### Packaging or Release

1. Use release metadata derived from `pyproject.toml`.
2. Build wheel, sdist, and skill assets.
3. Install the wheel in a clean environment outside the checkout.
4. Run entrypoint, profile, protocol, resource, `pip check`, and onboarding smoke.
5. Verify downloaded release assets again before publication.

## References

Load only what the task needs:

- [architecture.md](references/architecture.md): current implemented architecture and remaining debt.
- [tool-design.md](references/tool-design.md): agent-facing schemas, multimodal results, and backend evidence.
- [roadmap.md](references/roadmap.md): completed milestones and active P3 work packages.
- [validation.md](references/validation.md): focused tests, CI gates, package/protocol smoke, skills, and live evidence.

## Maintained Validation

Start with focused tests, then run:

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
git diff --check
```

For skill changes, validate and compare both copies. For package/release changes, run the clean-wheel and `uvx` onboarding paths documented in [validation.md](references/validation.md).

## Handoff Evidence

End with:

```text
Contract or defect:
Root cause:
Implementation boundary:
Primary surface impact:
Compact surface impact:
Structured result / artifact impact:
Focused tests:
Repository / package / protocol checks:
Live Gemini observations:
Remaining uncertainty:
```
