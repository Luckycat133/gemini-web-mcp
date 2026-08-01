---
name: gemini-web-mcp-development
description: Develop, refactor, test, package, and release the gemini-web-mcp repository as an agent-first Gemini Web gateway for text, images, video, music, files, URLs, and Deep Research. Use for architecture changes, MCP tool design, client or session lifecycle work, compact/full server alignment, Gemini Web RPC adapters, multimodal artifact handling, CI, packaging, versioning, compatibility work, or MCP SDK migration.
license: AGPL-3.0-only
compatibility: Requires a checkout of Luckycat133/gemini-web-mcp, Python 3.11+, git, and the project development dependencies. Live Gemini verification additionally requires a dedicated signed-in Gemini Web test account; most unit, contract, packaging, and skill work must remain runnable offline.
metadata:
  author: Luckycat133
  project: gemini-web-mcp
  scope: development
  version: "1.0"
---

# Gemini Web MCP Development

Use this skill to develop the repository. Use the separate `gemini-web-mcp` skill when the task is to operate the installed server rather than change its implementation.

## Mission

Build a broadly usable, agent-first Gemini Web gateway that gives MCP-compatible agents reliable access to:

- text and multi-turn model calls;
- image input, generation, and editing;
- video and music generation;
- local file and URL analysis;
- Deep Research and report-derived artifacts;
- history, notebooks, scheduled actions, account capability discovery, and Gems where the upstream Web contract is sufficiently understood.

Optimize for agent task completion, stable contracts, real concurrency, verifiable multimodal outputs, maintainable reverse-engineered adapters, and reproducible installation. Do not optimize for tool count alone.

## Working Mode

For every non-trivial change:

1. Inspect the current branch, relevant source modules, tests, manifest data, documentation, and recent changes before proposing a design.
2. Load only the references needed for the task:
   - [architecture.md](references/architecture.md) for module boundaries, known debt, and refactors;
   - [tool-design.md](references/tool-design.md) for MCP schemas, agent-facing workflows, model routing, and multimodal outputs;
   - [roadmap.md](references/roadmap.md) for prioritization and acceptance criteria;
   - [validation.md](references/validation.md) for test, package, protocol, and release checks.
3. State the user-visible or developer-visible contract being changed. Define acceptance criteria before editing.
4. Implement one coherent change through shared application/domain code. Keep MCP entrypoints thin.
5. Update tests, manifest/capability metadata, evaluations, documentation, and both skill copies when the changed contract requires them.
6. Run the smallest relevant checks first, then the maintained repository checks.
7. Report evidence: what changed, what was tested, what remains uncertain, and whether live Gemini behavior was actually observed.

## Focus Rules

- Do not turn ordinary feature or refactor work into a generic policy review. Address concrete behavior, compatibility, data-flow, or failing-test requirements that are relevant to the requested change.
- Do not block implementation on speculative upstream changes. Record the assumption, isolate it behind an adapter, and add a fixture, probe, or compatibility test.
- Do not add a third implementation path when a shared service can support both the primary and compact servers.
- Do not preserve an accidental behavior merely because a test currently encodes it. Decide whether it is a public contract, fix it if it is a bug, and update the regression test deliberately.
- Do not claim a model, media backend, successful mutation, or saved deliverable from a wrapper label alone. Distinguish requested, effective, inferred, and observed facts.
- Do not add tools simply to mirror every upstream method. Prefer coherent workflows, composable primitives, and concise structured results that agents can use reliably.

## Current Repository Map

- `src/server.py`: primary FastMCP entrypoint and profile-based registration.
- `src/skill_server.py`: compact, low-token facade server. It currently duplicates substantial business logic and should progressively become an adapter over shared services.
- `src/client_manager.py`, `client_wrapper.py`, `session_manager.py`, `remote_chat_cleanup_manager.py`, `cookie_manager.py`, `thinking_client.py`: client, session, lifecycle, authentication, and Gemini Web transport infrastructure.
- `src/tools/`: primary tool modules. `manage.py` currently combines several domains and is a major refactor target.
- `src/tools/manifest_data.py`: static tool and Web capability metadata.
- `tests/`: unit, contract, behavior, and regression tests.
- `evaluations/`: agent-facing MCP contract evaluations.
- `.agents/skills/` and `.codex/skills/`: public and local skill copies.
- `docs/`, `scripts/package_release.py`, `pyproject.toml`, and GitHub Actions: documentation, distribution, dependency, and release surfaces.

The active runtime requirement is the value in `pyproject.toml`; do not repeat a different Python or package version in new documentation.

## Engineering Invariants

### 1. Async lifecycle correctness

- Never hold a `threading.Lock` across `await`.
- Coordinate asynchronous initialization with `asyncio.Lock`, AnyIO primitives, or one shared initialization task.
- Concurrent callers must await one initialization attempt.
- Reset must not let an older in-flight initialization overwrite newer state.
- Close or otherwise retire replaced clients deliberately.

### 2. One session model

- Use opaque collision-resistant session IDs.
- Unknown IDs return an explicit not-found result; they must not silently start a new chat or clear unrelated sessions.
- `reset one` and `reset all` are separate semantics.
- The primary and compact servers must use the same session service and lifecycle rules.

### 3. One business implementation

- MCP servers are adapters: registration, schema shaping, and concise presentation belong there.
- Chat, media, research, history, notebook, account, scheduled-action, Gem, prompt, and lifecycle behavior belong in shared services.
- Do not import underscored helpers from one large tool module into another server as a long-term API.
- Extract shared code with characterization tests, route both adapters to it, then remove the duplicate implementation.

### 4. Typed results and domain errors

New or substantially revised services should return typed data rather than preformatted prose. A result should make success, partial success, retryability, warnings, verification state, and diagnostics machine-readable. MCP adapters may add a concise text representation for older clients.

Prefer a stable shape equivalent to:

```text
ok
data
error {code, message, retryable, suggested_action}
warnings
meta {request_id, observed_at, requested_backend, effective_backend, verification_status}
```

Do not require an agent to infer success by parsing emoji or exception text.

### 5. Artifact-first multimodal outputs

Represent generated or downloaded outputs as artifacts with at least:

```text
kind, uri_or_path, mime_type, title, size_bytes, duration_seconds,
source_chat_id, requested_backend, effective_backend, verification
```

A media tool succeeds with a deliverable only when it returns a usable remote URI or a verified local file. Otherwise return an explicit queued, empty, unsupported, timed-out, or upstream-failed state.

### 6. Central capability and RPC evidence

- Centralize model aliases, media routing, observed RPC identifiers, payload builders, parsers, source paths, and verification metadata.
- Record enough evidence to know when a contract was last verified and against which dependency/Web build when that information is available.
- Keep raw Web response parsing in pure, fixture-tested functions.
- After a mutation, use a read-back check when the upstream response is not authoritative.

### 7. Single sources of truth

- Package version comes from one source and is read by runtime banners, release tooling, and documentation checks.
- Tool/profile metadata should come from shared registration metadata or one manifest source.
- Full and compact server behavior should be testable for parity without copying expectations manually.

### 8. Honest long-running behavior

- Media generation and Deep Research need explicit timeouts, cancellation behavior, and terminal states.
- A tool named `stream` must expose meaningful progress/incremental behavior to the MCP client; otherwise name it as a collected-stream implementation.
- Preserve upstream identifiers needed to resume, verify, or inspect a long-running operation.

## Priority Order

Unless the requested task has a narrower dependency, prefer this order:

1. **P0 correctness foundation:** async client initialization, session semantics, typed results/domain errors, and shared services for primary/compact parity.
2. **P1 maintainability and distribution:** split the management monolith, centralize RPC contracts, introduce the multimodal artifact model, unify versioning, fix package data/entrypoints/dependencies, strengthen CI, and make streaming semantics accurate.
3. **P2 protocol and compatibility evolution:** migrate through a dedicated MCP SDK v2 adapter, add live compatibility canaries, and improve cross-client installation and release verification.

Use [roadmap.md](references/roadmap.md) for issue-sized work packages and completion criteria.

## Tool Change Rules

When adding or changing an MCP tool:

1. Start from the agent workflow, not the upstream function name.
2. Decide whether the capability belongs in an existing facade, a composable primitive, or both.
3. Keep established public tool names stable unless the change is intentionally breaking and versioned.
4. Use constrained arguments (`Literal`, enums, bounded integers, explicit optional fields) and document defaults.
5. Return structured data first and concise text second where the SDK permits it.
6. Include pagination and output bounds for collection/private-RPC surfaces.
7. Keep full and compact adapters on the same service call; compactness should change presentation and tool count, not semantics.
8. Update registration/profile tests, annotations, manifest data, evaluations, tool docs, and examples together.

## Multimodal Change Rules

- Normalize local attachments and media inputs once in shared infrastructure.
- Keep requested model alias, request model, effective backend, and observed backend separate.
- Verify saved files with existence plus relevant metadata such as MIME type, size, dimensions, or duration.
- Make fallback behavior explicit; never present a fallback as the originally requested backend.
- Treat Deep Research as a resumable long-running workflow with plan/start/poll/result phases rather than one opaque text call.
- Prefer one artifact/result model across images, video, music, research exports, and future modalities.
- Keep provider-specific parsing below the domain service boundary so the MCP contract survives upstream format changes.

## Refactor Sequence

For large refactors:

1. Add characterization tests around current public behavior.
2. Define the new typed service boundary.
3. Move one domain at a time.
4. Route the primary server through the service.
5. Route the compact server through the same service.
6. Add parity/contract snapshots.
7. Delete duplicated handlers and compatibility shims that no longer have callers.
8. Re-run package, import, and MCP tool-list checks.

Avoid a repository-wide rewrite when incremental extraction can preserve working capabilities.

## Validation Baseline

Run the task-specific tests first. Before handoff, use the maintained commands that are available in the checkout:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m py_compile \
  src/tools/annotations.py src/tools/chat.py src/tools/media.py \
  src/tools/file.py src/tools/research.py src/tools/prompts.py \
  src/tools/manage.py src/server.py src/skill_server.py \
  src/client_wrapper.py src/thinking_client.py src/constants.py
git diff --check
```

For package or release work, also build distributions, install the wheel in a clean environment, import both server entrypoints, and list tools for representative profiles. For skill changes, validate and compare both copies:

```bash
skills-ref validate .agents/skills/gemini-web-mcp-development
skills-ref validate .codex/skills/gemini-web-mcp-development
diff -ru .agents/skills/gemini-web-mcp-development \
  .codex/skills/gemini-web-mcp-development
```

See [validation.md](references/validation.md) for the change matrix and stronger target CI gates.

## Handoff Format

End development work with:

- the contract or defect addressed;
- the architectural path used;
- files and public surfaces changed;
- tests and validation actually run;
- live Gemini behavior actually observed, if any;
- remaining uncertainty or the next dependency in the roadmap.
