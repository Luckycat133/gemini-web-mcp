---
name: gemini-web-mcp-development
description: Develop, refactor, test, package, and release the gemini-web-mcp repository as an agent-first Gemini Web gateway for text, images, video, music, files, URLs, and Deep Research. Use for repository audits, bug fixes, MCP tool or schema changes, primary/compact parity, shared services, Gemini Web compatibility, multimodal artifacts, testing, onboarding, live canaries, versioning, or releases.
license: AGPL-3.0-only
compatibility: Requires a checkout of Luckycat133/gemini-web-mcp, Python 3.11+, git, and the project development dependencies. Offline tests are the default. Current Gemini behavior can only be claimed after an explicitly authorized live run with a dedicated account.
metadata:
  author: Luckycat133
  project: gemini-web-mcp
  scope: development
  version: "0.2.0"
---

# Gemini Web MCP Development

Use this skill to change the repository. Use the separate `gemini-web-mcp` skill to operate an installed server.

Install it from the repository:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

## Mission

Maintain a public MCP gateway that lets general-purpose agents use Gemini Web text and multimodal workflows without depending on unstable Web payloads. Optimize for completed workflows, truthful state, verifiable artifacts, reproducible installation, and diagnosable upstream drift—not tool count.

## Start From the Actual Tree

Before planning non-trivial work, inspect the repository rather than relying on an old roadmap:

```bash
git status -sb
git log --oneline -20
```

Also inspect:

- the affected source and tests;
- current `pyproject.toml`, tags, and releases;
- open issues and pull requests;
- the latest CI and CodeQL runs;
- the latest live-canary result, including whether it ran or was skipped;
- `docs/changelog.md` and relevant user documentation.

A green offline suite proves the repository contract. It does not prove that Gemini Web still behaves the same today.

## Current Product Shape

The maintained baseline already includes:

- official MCP Python SDK v2 adapters and modern/legacy protocol smoke;
- a profile-based primary server and an eleven-tool compact facade;
- shared client/session/chat lifecycle services;
- typed domain results for core chat, session, artifact, long-operation, and shared history list/search/read/export/delete paths;
- image, video, music, file, URL, and Deep Research workflows;
- history, Notebook, scheduled-action, account, Gem, prompt, cookie, doctor, and cleanup surfaces;
- centralized reverse-engineered RPC contracts and pure parsers;
- wheel/sdist/skill packaging, clean-install smoke, isolated `uvx` onboarding, and release gates;
- a separately gated live compatibility canary.

Do not recreate those foundations. Read [architecture.md](references/architecture.md) for the remaining product gaps and confirmed defects.

## Working Method

For each change:

1. Reproduce the defect or characterize the current contract.
2. State the user-visible or developer-visible expected behavior.
3. Find the lowest shared owner: domain, service, infrastructure adapter, MCP adapter, or presentation layer.
4. Add a focused regression that fails for the old behavior.
5. Implement one coherent change without duplicating business logic.
6. Check primary and compact behavior when both expose the workflow.
7. Synchronize schemas, profiles, manifest data, evaluations, docs, and skill state only when the contract changes.
8. Run focused tests, then the appropriate repository/package/protocol/live checks.
9. Report offline, package, protocol, workflow, and live evidence separately.

## Engineering Invariants

### Structured State Is Authoritative

Compatibility text may be concise, but it must not contradict the structured result's error code, operation state, artifact state, backend evidence, or verification status.

### Accepted Is Not Verified

A successful HTTP/RPC response is not proof that a remote create, update, move, or delete took effect.

- Use read-back or another authoritative observation.
- Render success only for positive terminal evidence.
- Preserve ambiguous states such as `read_back_error`, `not_observed`, `mismatch`, or `still_present`.
- Give the agent the next verification action.

### Adapters Stay Thin

`src/server.py`, `src/skill_server.py`, and tool-registration modules own MCP schemas and presentation. Reusable request construction, parsing, lifecycle, mutation verification, and artifact logic belong in shared services.

When touching compact history/account/admin code, migrate one bounded workflow instead of expanding duplicated facade logic.

### Normalize Boundary Data Once

Gemini Web and `gemini-webapi` may return objects, dictionaries, or mixed nested values. Use shared accessors and typed adapters rather than raw `getattr` assumptions in presentation code. Normalize required identifiers with `strip()` before remote calls while preserving meaningful explicit empty values.

### Multimodal Success Requires an Artifact

Response prose is not a generated image, video, audio file, or report. Return a usable URI or verified local file plus state and metadata. Keep requested model, transport model, effective backend, and observed backend distinct.

### Test Doubles Do Not Prove the Product

The management `tests._fastmcp_shim` is only a branch-testing double. Real product evidence still requires profile snapshots, actual MCP stdio discovery/calls, installed-wheel smoke, and client onboarding.

## Testing and Real Experience

Use [validation.md](references/validation.md) for the full evidence ladder. The minimum maintained offline gates are:

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
git diff --check
```

Use [tool-design.md](references/tool-design.md) to test the product as a user:

1. credential-free isolated install and stdio preflight;
2. explicitly authorized live text;
3. verified local image generation;
4. connection from Codex, Claude, or VS Code;
5. manual video, music, file, URL, research, history, and mutation workflows;
6. artifact and read-back inspection independent of response prose.

Do not claim a live capability from fixtures or package smoke.

## Product Decisions

Some remaining work is not a coding question. Load [roadmap.md](references/roadmap.md) when work touches:

- the next public version after the historical 2.x tags;
- whether to operate a dedicated live-canary account;
- durable cleanup across process restarts;
- first-class poll/resume/cancel for long jobs;
- the long-term role of the compact server;
- whether to prioritize core multimodal reliability or Gemini UI parity;
- supported clients/platforms and distribution channels.

Do not silently choose these product contracts in a maintenance PR.

## Focus Rules

- Do not turn ordinary engineering into a generic policy review.
- Do not reopen completed architecture phases because old prose mentions them.
- Do not preserve accidental behavior merely because an old test encoded it.
- Do not add tools only to mirror every observed Gemini UI entry.
- Do not scatter emergency RPC IDs or response indices outside the registry/parser boundary.
- Do not hardcode volatile test counts in public badges or skill instructions.
- Do not call a skipped workflow, fixture, or synthetic response a live test.

## References

Load only what the task needs:

- [architecture.md](references/architecture.md): shipped capabilities, missing workflows, confirmed defects, and current priorities.
- [validation.md](references/validation.md): focused, full, package, protocol, workflow, and live testing.
- [tool-design.md](references/tool-design.md): how to install and actually experience text and multimodal workflows.
- [roadmap.md](references/roadmap.md): owner-level choices that should be discussed before implementation.

## Handoff

End development work with:

```text
Baseline commit:
Contract or defect:
Root cause:
Implementation boundary:
Primary surface impact:
Compact surface impact:
Structured result / artifact / verification impact:
Focused tests:
Full repository checks:
Package and MCP protocol checks:
Actual client or live Gemini observations:
Remaining gaps:
Owner decision required:
```
