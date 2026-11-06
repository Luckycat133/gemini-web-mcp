---
name: gemini-web-mcp-development
description: "Develop, refactor, evaluate, package, and release gemini-web-mcp as two focused business tool surfaces: Gemini functions and explicit Gemini account management. Use for repository audits, MCP surface design, task-first Agent Skills, shared services, long-operation recovery, artifacts, live compatibility, tests, packaging, or releases."
license: AGPL-3.0-only
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
  "https://github.com/Luckycat133/gemini-web-mcp#codex/gemini-tools-polish-20261101" \
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

Inspect the affected source/tests or documentation first. Read `pyproject.toml`, release history, development status, PRs/issues, CI/CodeQL, live-canary evidence, and public Skill state when needed for the task or a current compatibility/distribution claim.

Read the branch version from `pyproject.toml` and compare it with fetched published tags before preparing an update. Preserve published tags. This branch's function/account design diverges from the published 0.2.2 facades; repository integration notes describe the schema and state-store differences. A source push does not establish release compatibility or authorize a new package publication.

A green offline suite proves repository contracts. It does not prove current Gemini Web behavior.

## Current source before target topology

The source/contract routing table in [validation.md](references/validation.md)
separates implemented entrypoints from the create/account/SQLite target design.
Inspect it before exposing a tool or claiming restart recovery. A target catalog
in this Skill is not proof that the corresponding package entrypoint exists.

## Current Compatibility Baseline

The repository already has:

- MCP Python SDK v2 plus modern/legacy protocol smoke;
- profile-based primary server and fixed four-tool low-token server;
- shared client, chat, session, lifecycle, Artifact, and typed-history services;
- text/session, image, video, music, file, URL, and Deep Research workflows;
- Notebook, Scheduled, account, Gem, Prompt, Cookie, Doctor, and Cleanup surfaces;
- centralized reverse-engineered RPC contracts and parsers;
- verified Gem mutations and evidence-based chat deletion;
- wheel/sdist/runtime-Skill packaging, clean install, and isolated onboarding;
- an opt-in compatibility canary and a bounded 2026-08-08 live observation.

Do not rebuild these foundations. Treat the current broad/compact servers as compatibility surfaces while implementing the focused products below.

## Business Tool Architecture

One repository and Python distribution exposes two focused surfaces:

| Surface | Console entrypoint | Scope |
| --- | --- | --- |
| Function | `gemini-mcp-function` | 17 tools for assistance, sessions, Task, media, research and operations |
| Account | `gemini-mcp-account` | 17 explicit account tools |

The main command without `GEMINI_TOOLS` defaults to the function surface; explicit profiles retain their compatibility semantics. The older `gemini-mcp-assist` and compact servers remain supported. All surfaces reuse shared services and the same operation store. Cookie, Doctor and local Prompts are outside both business catalogs. `gemini_cleanup` is limited to MCP-owned ledger entries, never title matching.

Function tools: `gemini_ask`, `gemini_understand_image`, `gemini_understand`, `gemini_research`, `gemini_start_chat`, `gemini_send_message`, `gemini_list_sessions`, `gemini_reset_session`, `gemini_generate_image`, `gemini_edit_image`, `gemini_generate_video`, `gemini_generate_music`, `gemini_run_task`, `gemini_get_operation_status`, `gemini_get_operation_result`, `gemini_list_research_report_actions`, `gemini_create_from_research_report`.

Use `gemini_ask` for ordinary questions, current-web requests and natural-language app requests. Flash is the default; no web or tool enable switch is exposed. Preserve observed sources and grounding evidence. Function Ask with `notebook_ids` starts background ordinary Chat (`execution_mode="chat"`, default timeout 600 seconds); Ask without Notebook IDs keeps the direct-response path. `gemini_run_task` explicitly starts background Task (`execution_mode="task"`) and accepts optional Notebook IDs. Keep these modes separate: complex questions, App requests, Notebook context and asynchronous execution do not by themselves require Task. Default to Chat; choose Task for explicit Task or delegated workflows needing ongoing execution, tracked work or browser operation. Check action-specific app availability. Task supports discovered Flash/Argon only; saved browser/action preferences use account `gemini_preferences`; stored read-back never proves runtime effects. Low/medium/high thinking maps to 1/4/2 in both request body and model header; standard/extended remain low/high aliases and Lite is locked. The five-tool assist compatibility schema retains `gemini_search` and does not gain the function-only Notebook arguments. Do not infer successful external app changes from response prose.

Account tools: `gemini_history`, `gemini_manage_chat`, `gemini_delete_chat`, `gemini_notebooks`, `gemini_tasks`, `gemini_task_browser`, `gemini_skills`, `gemini_manage_notebook`, `gemini_move_chat_to_notebook`, `gemini_account_inventory`, `gemini_list_scheduled_actions`, `gemini_get_scheduled_action`, `gemini_create_scheduled_action`, `gemini_delete_scheduled_action`, `gemini_manage_gems`, `gemini_cleanup`, `gemini_preferences`.

Account history defaults to compact metadata and pagination with bounded explicit text reads. `gemini_manage_chat` renames, pins or unpins an exact chat ID after a fresh metadata pre-read and independent read-back. Preserve unknown `changed=null` after a sent but unverified mutation, and never replay it automatically. Keep API model IDs distinct from discovered Cookie transport selectors; official low/medium/high names alone do not establish Web protocol numbers.

Account `gemini_notebooks` defaults to `source="notebooklm"` source-picker metadata. `source="gemini"` retains the same integrated Notebook product's project view; move targets require fresh exact-ID/type resolution in that view, not guessed prefix conversion. `gemini_tasks` list/get are metadata only; explicit exact-ID delete requires independent absence verification. Its union annotation and `gemini_run_task` are destructive. Task schedules are not Chat scheduled actions. Task progress, schedules and explicit browser inputs are available; exclusive handoff, automatic permission approval, Task pause/resume and export are unavailable; experimental ProcessSession probes are not a production transport.

Function media tools always start in the background. Research defaults to background. Preserve the same operation handle for status/result. Cancellation is not implemented. A report `audio_overview` is a script and `custom_app` is an application specification, not generated audio or deployment.

Task and Notebook Chat workers preserve the original CID/RID/candidate and read exact completed-answer evidence. They save and verify the full local answer Artifact before automatic owned-chat cleanup, independently of a caller requesting the result. Task additionally requires a complete recognized native file inventory and verified saved attachments; failed downloads or changed shapes cannot release protection. Restarted reads verify saved files under the original handle. Legacy answer-only caches remain partial without inventing an empty inventory. Unknown work stays protected, and Notebook objects/sources are not temporary-chat cleanup targets. Keep answer bytes out of SQLite; persist only verified Artifact locators and metadata.

Ordinary session uncertainty blocks new prompts; `gemini_send_message(recover_only=true)` reads the previous exact turn without generation replay or SDK latest-reply fallback. This requires the original active process, unlike persistent operation handles. Keep active/in-flight sessions protected from stale cleanup timers. Treat native App connection setup separately from confirmation choices and never turn a text reply into OAuth. Cookie extraction and SDK init success do not establish authentication; reject an unauthenticated fresh user status before dispatch, with at most one same-account pre-dispatch credential refresh.

Read [architecture.md](references/architecture.md) before changing boundaries.

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

Long work must not depend on connection-local MCP state. Return a high-entropy opaque `operation_id`; every status/result call receives it explicitly. Preserve provider/research/chat IDs in structured metadata when observed.

### Local SQLite Persistence Is Settled

Use local-only SQLite tables such as `operations` and `cleanup_jobs`. Persist IDs, type, state, timestamps, attempts, stable errors, verification status, and Artifact locators only.

Never persist Cookies, prompts, chat/report text, raw Gemini responses, or generated bytes. Long-operation metadata defaults to seven-day retention. Support restart and cross-client resume. Cancellation is not implemented.

### Artifact-First Creation

Image/video/music/report completion requires a usable Artifact—not response prose. Return a resource link or verified local file plus structured metadata. The calling agent should use the Artifact in the user's next step rather than merely report its path.

### Thin Adapters

Dedicated and compatibility servers own schemas, dispatch, defaults, and presentation. Reusable request construction, parsing, normalization, Artifact handling, operation recovery, mutation verification, and cleanup live in shared services.

### Authentication Boundary Is Settled

Treat browser Cookies as sensitive authentication material, but do not turn ordinary feature development into repeated policy debate. Session reset changes only MCP/Gemini state.

### Test Doubles Do Not Prove the Product

`tests._fastmcp_shim` proves registration/branch behavior only. Product evidence still requires real MCP stdio calls, installed-wheel smoke, Skill installation, agent-use evaluations, and explicitly authorized live verification.

## Active Development Order

1. Verify function and account catalogs, annotations, package entrypoints and compatibility profiles.
2. Validate background operation recovery, artifact verification and MCP-owned cleanup with scoped evidence.
3. Keep onboarding, Skills, evaluations and client examples aligned; distinguish fixture and live evidence.
4. Expand the client/OS support matrix and targeted compatibility evidence.

Read [roadmap.md](references/roadmap.md) for issue-sized validation packages.

## Settled Product Decisions

- One repository and shared services support the function and account surfaces.
- Preserve `gemini_ask` as a distinct assistance tool.
- Media, Research, Task and Notebook-backed Chat use explicit background operation handles; no cancel is promised.
- SQLite stores operation metadata and cleanup state, not prompts or credentials.
- Explicit legacy profiles, assist and compact remain compatibility surfaces.
- Manifest is for discovery/recovery, not a mandatory first call.

The remaining owner-level choice is the first officially supported client/OS/distribution matrix.

## Testing and Real Experience

Use [validation.md](references/validation.md) for the evidence ladder and [tool-design.md](references/tool-design.md) for agent-use and handoff evaluations.

Start with focused checks for the affected behavior. Documentation-only changes use relevant content/link checks. The following full offline gates apply to broad runtime changes and release readiness; retain profile/protocol and package checks whenever those contracts are affected:

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

- Do not reopen the two-surface split, `gemini_ask`, asynchronous research, SQLite, or task-first Skill decisions.
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

Use the relevant fields below for development handoff; omit unaffected fields for small changes and mark required but unrun checks explicitly:

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

Task schedules are separate from Chat scheduled actions: use `gemini_tasks` schedule actions, preserve timezone and up to nine fractional seconds, and independently verify each mutation. Associated schedules protect MCP-owned Task threads. For `gemini_task_browser`, install `[browser-control]`; input requires exact reply IDs, the prior frame hash and a unique action ID. The Task must remain in state 3 or 5; never replay an action ID or auto-approve login/permissions. Frames and input dispatch do not verify website effects or exclusive pause.
