---
name: gemini-web-mcp-development
description: Develop, refactor, evaluate, package, and release gemini-web-mcp as three focused agent capability products: Gemini assistance and understanding, multimodal creation, and explicit Gemini account management. Use for repository audits, MCP surface design, task-first Agent Skills, shared services, long-operation recovery, artifacts, live compatibility, tests, packaging, or releases.
license: AGPL-3.0-only
compatibility: Requires a checkout of Luckycat133/gemini-web-mcp, Python 3.11+, git, and the project development dependencies. Offline tests are the default. Current Gemini behavior may only be claimed from an explicitly authorized live run.
metadata:
  author: Luckycat133
  project: gemini-web-mcp
  scope: development
  version: "0.2.1"
---

# Gemini Web MCP Development

Use this Skill to change the repository. Use the runtime Skills to operate installed MCP servers.

```bash
npx --yes skills@1.5.21 add \
  https://github.com/Luckycat133/gemini-web-mcp \
  --skill gemini-web-mcp-development \
  --agent codex --copy --yes
```

## Mission

Turn Gemini Web into focused capabilities that another agent can discover and compose:

1. assistance, web search, image/file/URL understanding, and Deep Research;
2. image, video, and music creation with usable Artifacts;
3. explicit Gemini account management.

Optimize for task completion, small deterministic tool catalogs, truthful structured state, usable files, restart-safe long work, and reproducible installation. Do not optimize for raw tool count or complete Gemini UI imitation.

## Inspect the Actual Baseline

Before non-trivial work:

```bash
git status -sb
git log --oneline -20
```

Also inspect the affected source/tests, `pyproject.toml`, current tags/releases, changelog, development status, open PRs/issues, CI/CodeQL, live-canary evidence, and public Skill state.

The active package and both current public Skills declare `0.2.1`. Preserve the existing `v0.2.0` tag and use `v0.2.1` for the audited patch release.

A green offline suite proves repository contracts. It does not prove current Gemini Web behavior.

## Current Compatibility Baseline

The repository already has:

- MCP Python SDK v2 plus modern/legacy protocol smoke;
- profile-based primary server and fixed eleven-tool low-token server;
- shared client, chat, session, lifecycle, Artifact, and typed-history services;
- text/session, image, video, music, file, URL, and Deep Research workflows;
- Notebook, Scheduled, account, Gem, Prompt, Cookie, Doctor, and Cleanup surfaces;
- centralized reverse-engineered RPC contracts and parsers;
- verified Gem mutations and evidence-based chat deletion;
- wheel/sdist/runtime-Skill packaging, clean install, and isolated onboarding;
- an opt-in compatibility canary and a bounded 2026-08-08 live observation.

Do not rebuild these foundations. Treat the current broad/compact servers as compatibility surfaces while implementing the focused products below.

## Target Product Architecture

| Product | Agent Skill | Console entrypoint | MCP server name | Purpose |
| --- | --- | --- | --- | --- |
| Assistance | `gemini-assist` | `gemini-mcp-assist` | `gemini_assist_mcp` | second opinion, grounded search, image/multimodal understanding, Deep Research |
| Creation | `gemini-create` | `gemini-mcp-create` | `gemini_create_mcp` | image generation/editing, video, music, operation recovery |
| Account | `gemini-account` | `gemini-mcp-account` | `gemini_account_mcp` | history, Notebook, Scheduled, Gems, Prompts, inventory, cleanup |

Keep one repository, one Python distribution, one Gemini adapter, and shared domain/services. Do not create three business implementations or three repositories.

The current `gemini-web-mcp` Runtime Skill remains a task-first compatibility router until the dedicated servers and Skills are independently usable.

Read [architecture.md](references/architecture.md) before changing product boundaries.

## Public Tool Contracts

All public tools use the `gemini_` prefix because hosts may aggregate multiple MCP servers.

### Assistance

```text
gemini_ask
gemini_search
gemini_understand_image
gemini_understand
gemini_research
```

`gemini_ask` remains a distinct tool for second opinions, critique, code review, and ordinary text reasoning.

`gemini_search` may claim grounded search only when observed source evidence exists. Otherwise return `answer_only`, `unavailable`, or `failed` rather than pretending the model searched the web.

`gemini_understand_image` is the simple high-frequency visual tool. `gemini_understand` accepts typed mixed inputs—text, image, file, URL, and later audio/video—without making callers encode them into one generic string.

`gemini_research` starts asynchronously by default and returns an opaque operation handle immediately.

### Creation

```text
gemini_generate_image
gemini_edit_image
gemini_generate_video
gemini_generate_music
gemini_get_operation_status
gemini_get_operation_result
gemini_cancel_operation
```

The modality-specific tool starts work. Shared operation tools recover it. Do not add a broad unpaginated operation-list tool to assist/create surfaces.

### Account

```text
gemini_history
gemini_notebooks
gemini_scheduled
gemini_gems
gemini_prompts
gemini_account
gemini_cleanup
```

Account tools load only for explicit account-data intent. Keep list/read and mutation semantics machine-distinguishable even when one facade uses an `action` field.

## Working Method

For each change:

1. Reproduce the defect or characterize the current contract.
2. State the user task that should become easier or more reliable.
3. Choose the lowest shared owner: domain, service, infrastructure, MCP adapter, or Skill presentation.
4. Add a focused failing regression or agent-use evaluation.
5. Implement one coherent vertical slice without duplicating execution logic.
6. Verify the dedicated surface and any compatibility surface that exposes the same workflow.
7. Synchronize schemas, annotations, manifest, Skills, examples, evaluations, packaging, and docs only when the contract changes.
8. Run focused tests, then repository, protocol, installed-product, Skill, and authorized-live checks as appropriate.
9. Report fixture, protocol, package, agent-use, and live evidence separately.

## Engineering Invariants

### Small, Deterministic Tool Catalogs

Each dedicated MCP exposes only tools relevant to its capability lane. Tool order and schemas are deterministic. Do not require every agent to discover dozens of unrelated account and maintenance operations.

### Task-First Skills

Skill descriptions describe user intent because description matching is the trigger boundary. Main `SKILL.md` files route tasks; focused references carry details. Add positive trigger evaluations and near-miss negative evaluations for every dedicated Skill.

Do not publish a dedicated Skill before its MCP surface is actually installable and usable.

### Structured State Is Authoritative

Compatibility prose must not contradict error code, operation state, Artifact state, source evidence, pagination, lifecycle, or verification status. Define `outputSchema` and validated structured content for public tools.

### Accepted Is Not Verified

A successful request is not proof that a remote create/update/move/delete took effect. Use read-back or another authoritative observation and preserve ambiguous states.

### Opaque, Explicit Handles

Long work must not depend on connection-local MCP state. Return a high-entropy opaque `operation_id`; every status/result/cancel call receives it explicitly. Preserve provider/research/chat IDs in structured metadata when observed.

### Local SQLite Persistence Is Settled

Use local-only SQLite tables such as `operations` and `cleanup_jobs`. Persist IDs, type, state, timestamps, attempts, stable errors, verification status, and Artifact locators only.

Never persist Cookies, prompts, chat/report text, raw Gemini responses, or generated bytes. Long-operation metadata defaults to seven-day retention. Support restart and cross-client resume. Cancellation is best effort unless provider cancellation is positively observed.

### Artifact-First Creation

Image/video/music/report completion requires a usable Artifact—not response prose. Return a resource link or verified local file plus structured metadata. The calling agent should use the Artifact in the user's next step rather than merely report its path.

### Thin Adapters

Dedicated and compatibility servers own schemas, dispatch, defaults, and presentation. Reusable request construction, parsing, normalization, Artifact handling, operation recovery, mutation verification, and cleanup live in shared services.

### Authentication Boundary Is Settled

Treat browser Cookies as sensitive authentication material, but do not turn ordinary feature development into repeated policy debate. Session reset changes only MCP/Gemini state.

### Test Doubles Do Not Prove the Product

`tests._fastmcp_shim` proves registration/branch behavior only. Product evidence still requires real MCP stdio calls, installed-wheel smoke, Skill installation, agent-use evaluations, and explicitly authorized live verification.

## Active Development Order

1. Finish the task-first compatibility Runtime Skill and its trigger/hand-off contracts.
2. Implement `gemini-assist` as the first focused vertical slice.
3. Implement `gemini-create` with Artifact-first image generation/editing.
4. Add the shared SQLite OperationService; connect Deep Research, video, and music with asynchronous starts.
5. Implement `gemini-account` from shared account/history services.
6. Run the dedicated full live baseline through the new surfaces.
7. Complete typed admin results, mutation verification, and durable SQLite cleanup.
8. Add multimodal onboarding, real-agent evaluations, and the official client/OS matrix.
9. Add selected Drive/Canvas/Notebook/sharing parity only after core workflows are reliable.

Read [roadmap.md](references/roadmap.md) for issue-sized packages and acceptance criteria.

## Settled Product Decisions

Do not reopen these during routine work:

- product priority is assistance/understanding, then generated Artifacts, then explicit account management;
- one repository exposes three focused MCP servers and three focused Runtime Skills;
- `gemini_ask` remains distinct;
- Deep Research starts asynchronously by default;
- dedicated public tools use the `gemini_` prefix;
- long-operation status/result/cancel use explicit opaque handles;
- local SQLite provides operation recovery and delayed cleanup;
- the broad and eleven-tool servers remain compatibility surfaces during migration;
- manifest is for discovery/recovery, not a mandatory call before every known workflow;
- reliability and agent task completion precede broad UI parity.

The remaining owner-level choice is the first officially supported client/OS/distribution matrix.

## Testing and Real Experience

Use [validation.md](references/validation.md) for the evidence ladder and [tool-design.md](references/tool-design.md) for agent-use and handoff evaluations.

Minimum offline gates:

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
git diff --check
```

Do not claim a live capability from fixtures, package smoke, a skipped canary, or generated prose alone.

## Focus Rules

- Do not reopen the three-product split, `gemini_ask`, asynchronous research, SQLite, or task-first Skill decisions.
- Do not add generic unprefixed names such as `search` to public aggregated tool surfaces.
- Do not put account/admin tools into assist or create catalogs.
- Do not publish a Skill whose named tools do not exist.
- Do not introduce modality-specific operation stores or duplicate polling logic.
- Do not add tools only to mirror every observed Gemini UI entry.
- Do not scatter volatile RPC IDs or response indices outside the registry/parser boundary.
- Do not hardcode volatile passing-test counts.
- Preserve `v0.2.0`; use `v0.2.1` for this patch line.

## References

- [architecture.md](references/architecture.md): current compatibility stack and target focused topology.
- [tool-design.md](references/tool-design.md): task-first tools, Skills, Artifacts, and agent-use evaluations.
- [validation.md](references/validation.md): repository, protocol, package, Skill, operation, Artifact, and live evidence.
- [roadmap.md](references/roadmap.md): settled decisions and issue-sized development packages.

## Handoff

End development work with:

```text
Baseline commit:
User workflow:
Contract or defect:
Root cause:
Shared implementation boundary:
Dedicated surface impact:
Compatibility surface impact:
Structured result / Artifact / operation impact:
Focused tests and agent-use evaluations:
Full repository checks:
Package and MCP protocol checks:
Runtime Skill impact:
Actual client or live Gemini observations:
Remaining gaps:
Owner decision required:
```
