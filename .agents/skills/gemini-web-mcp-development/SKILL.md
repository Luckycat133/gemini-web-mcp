---
name: gemini-web-mcp-development
description: Develop, refactor, test, package, and release the gemini-web-mcp repository as an agent-first Gemini Web gateway for text, images, video, music, files, URLs, Deep Research, history, and account workflows. Use for repository audits, bug fixes, MCP contracts, primary/compact parity, shared services, Gemini Web compatibility, multimodal artifacts, testing, onboarding, ClawHub skill maintenance, live canaries, versioning, or releases.
license: AGPL-3.0-only
compatibility: Requires a checkout of Luckycat133/gemini-web-mcp, Python 3.11+, git, and the project development dependencies. Offline tests are the default. Current Gemini behavior may only be claimed from an explicitly authorized live run; release-grade compatibility requires the dedicated-account canary.
metadata:
  author: Luckycat133
  project: gemini-web-mcp
  scope: development
  version: "0.2.0"
---

# Gemini Web MCP Development

Use this skill to change the repository. Use the separate `gemini-web-mcp` runtime skill to operate an installed server.

Install this development skill from the repository:

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

## Mission

Maintain a public MCP gateway that lets general-purpose agents use Gemini Web text and multimodal workflows without depending directly on unstable Web payloads. Optimize for completed workflows, truthful state, verifiable artifacts, reproducible installation, recoverable long operations, and diagnosable upstream drift—not raw tool count or complete UI imitation.

## Inspect the Actual Baseline

Before planning non-trivial work:

```bash
git status -sb
git log --oneline -20
```

Also inspect:

- the affected source and tests;
- `pyproject.toml`, current tags, and current releases;
- `docs/changelog.md` and `docs/development-status.md`;
- open issues and pull requests;
- the latest CI and CodeQL runs;
- the latest dedicated live-canary run and whether it ran or was skipped;
- the runtime skill version and any ClawHub audit/publish evidence.

Use one canonical active version:

- `pyproject.toml`, the runtime Skill, and the development Skill currently declare `0.2.0`;
- release asset names and runtime banners derive from the Python package metadata;
- all rewritten Git history and release refs use the canonical `0.2.0` project version.

Update all three active version sources and `docs/changelog.md` in the same commit for every future version bump.

A green offline suite proves the repository contract. It does not prove that Gemini Web still behaves the same today.

## Current Shipped Baseline

The maintained tree already includes:

- the official MCP Python SDK v2 boundary with modern and legacy protocol smoke;
- a profile-based primary server and a fixed eleven-tool compact facade;
- shared client, session, chat, lifecycle, artifact, and typed history services;
- text, multi-turn sessions, image generation/editing, video, music, files, URLs, and Deep Research;
- typed artifacts and typed history list/search/read/export/delete across primary and compact surfaces;
- Notebook, scheduled-action, account, Gem, prompt, Cookie, doctor, cleanup, and compatibility surfaces;
- centralized reverse-engineered RPC contracts and pure parsers;
- verified Gem mutation semantics and evidence-based chat deletion;
- bounded browser-Cookie authorization handling and the security wording contract established by the ClawHub `0.2.0` audit;
- `.agents/skills` as the single repository source for public skills;
- wheel/sdist/skill packaging, clean-install smoke, isolated `uvx` onboarding, and release gates;
- a separately gated live compatibility canary;
- a bounded authorized 2026-08-08 live observation for Cookie initialization, text/session behavior, typed history, and verified chat cleanup.

Do not recreate these foundations. Read [architecture.md](references/architecture.md) for the remaining engineering gaps.

## Working Method

For each change:

1. Reproduce the defect or characterize the current contract.
2. State the user-visible or developer-visible expected behavior.
3. Find the lowest shared owner: domain, service, infrastructure adapter, MCP adapter, or presentation layer.
4. Add a focused regression that fails for the old behavior.
5. Implement one coherent change without duplicating business logic.
6. Check primary and compact semantics when both expose the workflow.
7. Synchronize schemas, profiles, manifest data, evaluations, docs, and skills only when the contract changes.
8. Run focused tests, then the appropriate repository, package, protocol, and live checks.
9. Report fixture, offline, package, protocol, workflow, and live evidence separately.

## Engineering Invariants

### Structured State Is Authoritative

Compatibility text may be concise, but it must not contradict the structured result's error code, operation state, artifact state, backend evidence, pagination, lifecycle, or verification status.

### Accepted Is Not Verified

A successful HTTP/RPC response is not proof that a remote create, update, move, or delete took effect.

- Use read-back or another authoritative observation.
- Render success only for positive terminal evidence.
- Preserve ambiguous states such as `read_back_error`, `not_observed`, `mismatch`, `still_present`, or incomplete pagination.
- Return the next verification action.

### Adapters Stay Thin

`src/server.py`, `src/skill_server.py`, and tool-registration modules own MCP schemas, dispatch, defaults, and presentation. Reusable request construction, parsing, lifecycle, mutation verification, artifact handling, and long-operation recovery belong in shared services.

Keep the compact server as the low-token discovery surface, but do not add a second business implementation.

### Normalize Boundary Data Once

Gemini Web and `gemini-webapi` may return objects, mappings, or mixed nested values. Normalize at the service or infrastructure boundary rather than scattering raw `getattr`, positional-index, or response-shape assumptions through presenters.

### Multimodal Success Requires an Artifact

Response prose is not a generated image, video, audio file, or report. Return a usable URI or verified local file plus state and metadata. Keep requested model, transport model, effective backend, and observed backend distinct.

### Authentication Boundary Is Settled

The ClawHub security review established the operational contract. Treat browser Cookies as sensitive account-authentication material, require explicit user approval before export, restrict local cache access, never log/back up/share values, and remove caches when no longer needed. Session reset affects only MCP/Gemini conversation state. Do not reopen this as a generic policy discussion unless implementation behavior changes.

### Test Doubles Do Not Prove the Product

`tests._fastmcp_shim` is only a registration/branch-testing double. Real product evidence still requires actual MCP stdio discovery/calls, installed-wheel smoke, onboarding, and explicitly authorized live verification.

## Active Development Order

Unless the owner selects a different product priority:

1. establish the dedicated full live baseline for media, files, URLs, research, and disposable account mutations;
2. complete typed results for the primary deep history scan and remaining account/admin surfaces;
3. finish the read-back audit for every remote mutation;
4. introduce one shared long-operation `start/status/result/cancel` contract;
5. decide and implement cleanup durability across restarts;
6. add end-to-end video, music, file, URL, and research onboarding plus a real client matrix;
7. pursue Drive, Canvas, richer Notebook/scheduled/sharing, and other UI-parity work only after the core workflows are reliable.

## Owner Decisions That Still Block Product Contracts

Load [roadmap.md](references/roadmap.md) before changing:

- ownership, cadence, entitlement scope, and evidence retention for the dedicated live-canary account;
- persistence and cancellation semantics for the shared long-operation API;
- process-local versus durable cleanup semantics;
- the official client/platform support matrix and canonical distribution path.

The unified `0.2.0` version contract, Cookie boundary, compact-server direction, and reliability-before-UI-parity priority are already established; do not repeatedly ask the owner to re-decide them.

## Testing and Real Experience

Use [validation.md](references/validation.md) for the evidence ladder. The minimum maintained offline gates are:

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
git diff --check
```

Use [tool-design.md](references/tool-design.md) to verify installation, live text, local image artifacts, compact/primary discovery, and the complete multimodal workflow in real MCP clients.

Do not claim a live capability from fixtures, package smoke, a skipped canary, or response prose alone.

## Focus Rules

- Do not turn ordinary engineering into a generic policy review.
- Do not reopen completed architecture phases because old prose mentions them.
- Do not preserve accidental behavior merely because an old test encoded it.
- Do not add tools only to mirror every observed Gemini UI entry.
- Do not scatter emergency RPC IDs or response indices outside the registry/parser boundary.
- Do not hardcode volatile test counts in public badges or skill instructions.
- Do not let the active package and Skill versions drift; keep rewritten history and release refs at the canonical `0.2.0` version.
- Do not call a skipped workflow, fixture, or synthetic response a live test.

## References

Load only what the task needs:

- [architecture.md](references/architecture.md): shipped capabilities, current gaps, settled directions, and priority order.
- [validation.md](references/validation.md): focused, full, package, protocol, workflow, skill, and live testing.
- [tool-design.md](references/tool-design.md): how to install and actually experience text and multimodal workflows.
- [roadmap.md](references/roadmap.md): owner decisions and issue-sized next development packages.

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
Runtime skill / ClawHub impact:
Actual client or live Gemini observations:
Remaining gaps:
Owner decision required:
```
