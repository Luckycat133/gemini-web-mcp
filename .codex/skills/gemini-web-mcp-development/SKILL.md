---
name: gemini-web-mcp-development
description: Develop, refactor, test, package, and release the gemini-web-mcp repository as an agent-first Gemini Web gateway for text, images, video, music, files, URLs, and Deep Research. Use for repository reviews, debugging regressions, MCP tool or schema changes, primary/compact adapter parity, shared services, Gemini Web RPC compatibility, multimodal artifacts, CI, onboarding, live canaries, versioning, or release work.
license: AGPL-3.0-only
compatibility: Requires a checkout of Luckycat133/gemini-web-mcp, Python 3.11+, git, and the project development dependencies. Offline unit, contract, package, protocol, and skill checks are the default. Live Gemini verification requires the separately gated dedicated-account canary.
metadata:
  author: Luckycat133
  project: gemini-web-mcp
  scope: development
  version: "1.2"
---

# Gemini Web MCP Development

Use this skill to change the repository. Use the separate `gemini-web-mcp` skill to operate an installed server.

Install it directly from the repository:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

## Mission

Maintain a public, agent-first compatibility gateway that exposes Gemini Web text and multimodal capabilities through stable MCP contracts while isolating reverse-engineered transport details behind tested adapters.

Optimize for completed workflows, truthful machine-readable state, verifiable artifacts, reproducible installation, and recoverable upstream drift. Do not optimize for raw tool count.

## Inspect the Current Baseline First

Before planning non-trivial work:

```bash
git status -sb
git log --oneline -20
```

Then inspect the affected source, tests, `docs/changelog.md`, open issues/PRs, current workflow runs, and `pyproject.toml`. Recent direct merges may be followed by repair commits; review the final tree, not only the original feature commit.

The current baseline already includes:

- async-safe, generation-aware Gemini client initialization and retirement;
- one collision-resistant `SessionService` shared by primary and compact adapters;
- serializable `DomainResult`, stable domain errors, operation states, warnings, request IDs, and diagnostic IDs;
- shared chat/session execution and collected-stream normalization;
- one artifact model for remote, local, queued, empty, and failed multimodal outputs;
- extracted history, account, Notebook, scheduled-action, Gem, manifest, doctor, and cleanup services;
- centralized Gemini Web RPC contracts and pure parsers;
- MCP Python SDK v2 with modern and legacy protocol contracts;
- Ruff, Mypy, complete tests, targeted contracts, profile/schema snapshots, clean-wheel, onboarding, skill, and release gates;
- an opt-in live canary with sanitized reports and issue automation;
- public Codex, Claude, and VS Code onboarding examples;
- extensive compatibility-adapter branch tests. The `tests._fastmcp_shim` object is test-only; real product behavior is still proven through `MCPServer` profile and stdio protocol smoke.

Do not reopen these foundations as missing work unless a regression demonstrates that the contract has broken.

## Working Method

For every non-trivial task:

1. Resolve the exact branch, commit, affected profile/tool, and recent changes.
2. Reproduce the defect or characterize the current contract before editing.
3. State the user-visible or developer-visible contract and acceptance criteria.
4. Find the lowest shared owner: domain, service, infrastructure adapter, MCP adapter, or presentation layer.
5. Add a focused regression that fails for the original behavior.
6. Implement one coherent fix without introducing parallel business logic.
7. Synchronize primary/compact surfaces, manifest data, schemas, evaluations, docs, examples, and both skill copies only where the contract requires it.
8. Run focused checks first, then maintained repository gates.
9. Separate fixture, package, protocol, workflow, and live evidence in the handoff.

## Focus Rules

- Do not turn normal feature or refactor work into a generic policy review. Address the requested behavior, data flow, compatibility, evidence, or failing test.
- Do not repeat completed roadmap phases because a stale note still mentions them.
- Do not block useful work on speculative Gemini Web changes. Isolate assumptions and add a fixture or canary probe.
- Do not add a second implementation when an existing service can own the behavior.
- Do not preserve an accidental behavior merely because an old test encoded it.
- Do not claim a backend, mutation, stream, or artifact from wrapper prose alone.
- Do not increase tool count unless it materially improves agent discoverability, composition, or task completion.

## Current Repository Map

- `src/server.py`: primary profile-based MCP adapter.
- `src/skill_server.py`: compact low-token MCP adapter with remaining legacy facade/presentation code.
- `src/adapters/`: project-owned MCP SDK boundary, structured/text result alignment, and artifact rendering.
- `src/domain/`: typed results, errors, operation/lifecycle/stream state, and artifact contracts.
- `src/services/`: shared application behavior, including chat, artifacts, history, account, Notebooks, scheduled actions, Gems, cleanup, manifest, and compatibility probes.
- `src/infrastructure/`: centralized reverse-engineered RPC contracts and response parsers.
- `src/client_manager.py`, `client_wrapper.py`, `session_manager.py`, `remote_chat_cleanup_manager.py`, `cookie_manager.py`, `thinking_client.py`: runtime lifecycle and Gemini Web transport infrastructure.
- `src/tools/`: primary granular tools and compatibility registration adapters; `manage.py` must become thinner incrementally rather than absorbing new domain logic.
- `src/onboarding.py`: installed-product preflight and explicitly gated live examples.
- `compatibility/`: live-canary schema, fixtures, and dependency evidence.
- `tests/`: unit, regression, parity, schema, workflow, package, protocol, and product contracts.
- `scripts/`: contract, packaging, release, onboarding, protocol, version, and canary verification.
- `.agents/skills/` and `.codex/skills/`: byte-identical runtime and development skill mirrors.

## Engineering Invariants

### One Result, Two Presentations

`TextContent._meta.domain_result` is the machine-readable authority. Compatibility text may be concise, but it must not name a different error code, success state, backend, artifact state, or verification outcome.

### Accepted Is Not Verified

A remote mutation being accepted is not proof that its target state changed.

- Only show a success marker when the relevant read-back evidence is terminal and positive.
- Treat `read_back_error`, `read_back_not_observed`, `read_back_mismatch`, `still_present`, `missing_mutation_id`, and equivalent states as warning, partial, or failed outcomes—not success.
- Preserve the verification status and actionable next check in the result.
- Add regression tests for both positive and ambiguous mutation states.

### Async and Session Correctness

- Never hold a synchronous lock across `await`.
- Concurrent initialization callers share one attempt; one caller's cancellation must not cancel work needed by others.
- Reset prevents stale publication and retires replaced clients deliberately.
- Session IDs are opaque; unknown IDs change no state; reset-one and reset-all stay distinct; sends are serialized per session.

### Shared Business Services

Adapters register tools, normalize adapter-specific input, and present results. Reusable request construction, Gemini calls, parsing, mutation verification, lifecycle, and artifact logic belong below them.

When touching a legacy `skill_server.py` or `tools/manage.py` path, migrate one bounded workflow to a shared service instead of expanding the adapter.

### Verifiable Multimodal Outputs

Use the shared artifact model. Keep remote URI and local path independently, distinguish terminal states, verify local files and relevant metadata, and keep requested model, request model, effective backend, and observed backend separate.

### Reverse-Engineered Compatibility

Centralize RPC IDs, source paths, payload builders, parsers, observation dates, and verification strategies. Parse success, empty, rejection, and changed shape explicitly. Return `UPSTREAM_CHANGED` when evidence is insufficient.

### Package, Protocol, and Workflow Integrity

- `pyproject.toml` is the persisted version/dependency source.
- Runtime data is package-internal and loaded through `importlib.resources`.
- Installed-wheel entrypoints, profiles, modern/legacy protocol negotiation, schemas, and onboarding remain under smoke tests.
- GitHub Actions expressions must use contexts valid at their YAML location. Add a repository contract after every workflow parse/startup regression.
- Test-only framework shims never replace real MCP protocol/product smoke.

### Honest Long Operations

Current `_stream` tools collect Gemini upstream chunks and return one MCP result; report `delivery=collected`. Normalize mixed stream semantics, preserve continuation identifiers, and propagate cancellation.

## Active Direction

The active roadmap is P3 maintenance, reliability, release, and adoption. Load [roadmap.md](references/roadmap.md) for issue-sized acceptance criteria.

Near-term order:

1. complete typed-result coverage for remaining prose-only primary and compact facades;
2. reduce compact/manage adapter debt one workflow at a time;
3. audit mutation verification and compatibility text across all remote writes;
4. establish the first deliberate read-only live-canary baseline;
5. decide and cut a coherent public release;
6. improve onboarding from reproducible client reports;
7. maintain RPC/model compatibility through centralized fixtures and probes.

## Change Playbooks

### Bug Fix

1. Reproduce at the lowest stable layer.
2. Test both structured and compatibility presentations when an MCP adapter is involved.
3. Fix the shared owner.
4. Add positive and negative/ambiguous regression cases.
5. Run focused tests, related parity tests, and the contract checklist.

### Tool or Schema Change

1. Inspect tool snapshots, profile membership, manifest, and current output schema.
2. Define input, structured result, compatibility text, annotations, operation state, and verification evidence.
3. Implement through a service or pure adapter.
4. Re-baseline golden schemas only for an explained contract change.

### Gemini Web Drift

1. Identify transport, envelope, RPC, parser, or verification stage.
2. Add a sanitized fixture.
3. Update the registry/parser rather than scattering new indices or IDs.
4. Preserve explicit unavailable or changed state when observation remains incomplete.
5. State whether the result is fixture-only or live-observed.

### Packaging, CI, or Release

1. Use metadata derived from `pyproject.toml`.
2. Validate workflow syntax/context assumptions in repository tests.
3. Build wheel, sdist, and skill assets.
4. Install outside the checkout and run entrypoint/profile/protocol/resource/onboarding smoke.
5. Re-verify downloaded assets before publication.

## References

Load only what the task needs:

- [architecture.md](references/architecture.md): current implemented architecture and remaining debt.
- [tool-design.md](references/tool-design.md): agent-facing schemas, multimodal results, and mutation/backend evidence.
- [roadmap.md](references/roadmap.md): completed integration packages and active P3 work.
- [validation.md](references/validation.md): focused tests, CI, package/protocol smoke, skills, and live evidence.

## Maintained Validation

Start with task-specific tests, then run:

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
git diff --check
```

Use the stronger package, workflow, skill, and canary checks from [validation.md](references/validation.md) when those surfaces change.

## Handoff Evidence

End with:

```text
Contract or defect:
Root cause:
Implementation boundary:
Primary surface impact:
Compact surface impact:
Structured result / verification impact:
Focused tests:
Repository / package / protocol / workflow checks:
Live Gemini observations:
Remaining uncertainty:
```
