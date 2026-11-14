---
name: gemini-web-mcp-development
description: "Develop and review gemini-web-mcp as two focused business tool surfaces for all account-available Gemini generation workflows and explicit Gemini content management. Use for repository changes, dual-surface contracts, MCP SDK v2 fingerprints, recovery, compatibility migrations, Agent Skills, tests, packaging, and releases."
license: AGPL-3.0-only
compatibility: Requires a gemini-web-mcp checkout, Python 3.11+ and its development dependencies. Gemini account access and paid API usage require task-specific authorization.
metadata:
  author: Luckycat133
  project: gemini-web-mcp
  scope: development
  version: "0.2.1"
---

# Gemini Web MCP Development

Develop the repository, not an installed account workflow. The two equal product
goals are **all account-available generation capabilities** and **complete Gemini
Web content management**. A focused catalog is an interface design, not permission
to drop Canvas, Library, sharing, exports or other requirements.

## Establish the revision

Read `AGENTS.md`, inspect `git status -sb` and `git log --oneline -10`, then read
`pyproject.toml`, the affected code/tests and relevant integration notes. Preserve
uncommitted work. Distinguish local changes, the reviewed remote SHA, `main` and
published releases. Never overwrite locally refreshed MCP fingerprints with an
older remote fixture. A source edit does not authorize merging or publishing.

The 2026-11-14 review targets `5c9f53087398f614958acf06d3c9d14f54958c52` on
`codex/gemini-tools-polish-20261113` / PR #47, not `main`. That source declares
`0.2.1`; published `v0.2.2` has different interfaces/storage. Keep metadata aligned
with the checkout for documentation work; do not republish `v0.2.1` or alter tags.
Resolve the next release against actual published versions and migration scope.

## Load only the relevant reference

- [architecture.md](references/architecture.md): implemented surfaces, wire envelope, shared owners, recovery and side effects.
- [validation.md](references/validation.md): MCP v2 fingerprint review, exact commands and what each evidence level proves.
- [roadmap.md](references/roadmap.md): prioritized work, capability disposition and preserved P/G/M work-package mapping.
- [tool-design.md](references/tool-design.md): task routing, tool granularity and end-to-end agent acceptance.

The bundled `assets/plan.json` is a development backlog, not a completion receipt.
Its baseline and package version must match this revision of the plan. References
for generation/management describe goals unless expressly labeled implemented.

## Settled Product Decisions

- One distribution, shared services, two business surfaces: `gemini-mcp-function`
  and `gemini-mcp-account`. Do not invent a third `gemini-mcp-generate` product.
- `src/surfaces/catalogs.py` is the current membership contract. The reviewed
  Function/Account catalogs each contain 17 tools; the number is not a permanent
  ceiling. Keep deliberate schema/catalog changes reviewable.
- Unset `GEMINI_TOOLS` selects Function. Explicit profiles, assist and compact
  remain branch compatibility routes, not proof of released 0.2.2 parity.
- Function owns assistance, sessions, generation, native Task and Research;
  Account owns explicit content/configuration management. Cookie, Doctor and
  local Prompts are not business-catalog additions.
- Function media always returns a background handle. Research defaults to
  background. Notebook-backed Ask is ordinary background Chat, not Task.
- No public operation cancellation is implemented. A state enum, deleting a
  thread, killing a worker or resetting a session does not implement cancellation.

## Working loop

1. Identify the exact user workflow and current defect or missing capability.
2. Read the shared owner and its regressions; do not infer behavior from a Skill,
   test count, removed test file, RPC registry entry or old acceptance record.
3. Add an observable regression before changing runtime behavior. Implement at
   the lowest shared boundary; adapters own signatures, registration and rendering.
4. Preserve exact model/provider, account, CID/RID/candidate and operation identity.
   Recover observation, not generation. Do not silently replay a mutation.
5. Update affected schemas, annotations, catalog, fixtures, runtime Skills and
   examples together. Review fingerprint changes semantically before accepting them.
6. Run the narrow checks first, then the gates required by the changed boundary.
   Reuse passing evidence for unchanged code at the same revision/environment;
   documentation-only work does not require another full account test campaign.
7. Record implemented, offline/protocol/package verified, live accepted and blocked
   separately. Continue independent unblocked work without repeatedly asking to proceed.

## Non-negotiable contracts

**Structured State Is Authoritative.** The current SDK-generated content wrapper
is not a direct `DomainResult` envelope. `resultType=complete` and `isError=false`
can carry `domain_result.ok=false`; transport completion is not business success.
Do not silently change this frozen behavior or weaken schemas to match a digest.

**Accepted Is Not Verified.** Sent requests and persisted preferences do not prove
remote effects. Require exact-target read-back; preserve `changed=null` or partial
when observation is unavailable. A valid empty response differs from a failed read.

**Opaque, Explicit Handles.** Reuse `operation_id` to recover the same work.
Caller-generated handles use `op_` plus 32 lowercase hex characters. Reusing a
handle retrieves existing work; it does not prove two prompt payloads are equal.
Ordinary sessions and their `recover_only` path remain process-local.

**Local SQLite Persistence Is Settled.** The current operation and cleanup stores
are separate, with account fencing and worker ownership. Do not substitute the
released version's `GEMINI_STATE_DB_PATH`, silently rebind old records or persist
prompts/credentials/answer bytes in the ledger. Verified files live separately.

**Artifact-First Creation.** Preserve the original response identity, save usable
files and verify them before releasing cleanup protection. Task completion also
requires its full recognized attachment inventory. Lost/corrupt files do not
justify regeneration. `status` and `result` have different side effects.

**Explicit provider boundary.** Default video uses Gemini Web. The explicit
`gemini-omni-1.2-flash` route uses a paid API key; never use it as a hidden fallback,
transfer Web credentials to it or claim it consumes only the Web subscription.

**MCP-owned cleanup only.** Never expand ownership by title matching or use source
chat deletion as an implicit substitute for deleting a Notebook/Library object.
Native connection setup is not a text confirmation; do not auto-approve login,
permissions or browser actions.

## Start the next slice

Follow the roadmap: reconcile release compatibility and lost capabilities, then
advance generation and management in parallel. The baseline MCP v2 fingerprint,
contract and protocol checks have passed at the reviewed revision; they are
regression gates, not an unfinished product feature. User-reported newer local
results remain separate until tied to a commit or working-tree diff.

For this Skill, run `python -m pytest -q tests/test_development_skill.py`, the
bundled `scripts/plan.py check`, its regression tests and `git diff --check`.
Use the full runtime/protocol/package commands in validation.md for runtime or
release changes. Keep private evidence outside Git; report the exact scope run.

## Handoff

Give the baseline/diff, workflow, changed shared owner, wire/state/compatibility
impact, commands and evidence, unrun gates and next executable slice. Do not
claim all generation models or all account content are supported from a small
catalog, schema fingerprint or a single successful account sample.
