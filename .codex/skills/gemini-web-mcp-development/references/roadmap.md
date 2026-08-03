# Development Roadmap and Acceptance Criteria

Load this reference when choosing the next issue, planning a multi-file change, or deciding whether a maintenance package is complete.

## Product North Star

Deliver a public, installable Gemini Web compatibility gateway that agents can use as a dependable text and multimodal capability provider. Progress is measured by completed workflows, correct machine-readable states, verifiable artifacts, reproducible installation, and recoverable upstream drift—not tool count.

## Baseline Status

The original roadmap through P2.3 is implemented on `main`. Verify the current checkout and changelog, but do not reopen these items as missing foundations.

| Completed phase | Implemented contract |
| --- | --- |
| P0.1 | async-safe shared initialization, generation-aware reset, deliberate client retirement |
| P0.2 | one cross-adapter `SessionService`, opaque IDs, explicit reset and not-found semantics |
| P0.3 | typed domain results/errors/warnings, operation state, diagnostics, MCP metadata |
| P0.4 | shared chat/session application service and adapter parity |
| P1.1 | unified multimodal artifact identity, state, verification, and backend evidence |
| P1.2 | extracted management services plus centralized RPC registry/parsers/read-back verification |
| P1.3 | observable conversation cleanup and lifecycle metadata |
| P1.4 | single persisted package version and release-metadata validation |
| P1.5 | direct bounded dependencies, packaged resources, explicit entrypoints, clean-wheel smoke |
| P1.6 | Ruff/Mypy/test/contract/package/profile/protocol/skill/release CI gates |
| P1.7 | collected-stream normalization and explicit long-operation state/cancellation semantics |
| P2.1 | MCP Python SDK v2 adapter with modern/legacy negotiation and structured output |
| P2.2 | opt-in live compatibility canary, sanitized schema, dependency matrix, issue automation |
| P2.3 | public onboarding, client examples, isolated `uvx` install, verified local image path |

The fact that a completed phase still has maintenance work does not mean its foundation is absent.

## Prioritization Rule

Prefer work that removes a reproducible semantic contradiction or repeated integration failure across tools. After that, complete bounded typed/service migrations, collect real compatibility evidence, and cut a coherent public release.

Do not perform a repository-wide rewrite. Use tests and shared boundaries to move one workflow at a time.

# P3 — Reliability, Release, and Adoption

## P3.1 Integration Regression Audit

**Goal:** find defects that appear only after domain, service, adapter, protocol, and compatibility-text layers are composed.

Priority seams:

- compatibility text versus `_meta.domain_result`;
- primary versus compact result semantics;
- generated `structuredContent` versus `outputSchema`;
- profile registration versus manifest/snapshots/docs;
- cleanup/lifecycle metadata versus actual scheduling outcome;
- remote artifact success versus local-save partial failure;
- modern versus legacy MCP negotiation;
- source checkout versus installed wheel/onboarding behavior.

**Acceptance:**

- every fixed defect has a focused regression that fails on the original behavior;
- coded compatibility text never contradicts the typed error code;
- parity tests compare error code, operation state, lifecycle, artifact identity, pagination, and verification as applicable;
- no golden snapshot is re-baselined without an explained contract change;
- full contract checklist remains green.

**First package:** correct compact session chat failure text so non-session failures are not displayed as `SESSION_NOT_FOUND`, and enforce the invariant in the shared MCP result adapter.

## P3.2 Complete Typed-Result Coverage

**Goal:** migrate remaining prose-only or inconsistently typed workflows without breaking established tool names.

Suggested order:

1. compact history list/search/read/export/delete;
2. compact account inventory actions;
3. prompt store actions;
4. cookie status/profile/get results;
5. doctor and cleanup results;
6. remaining primary compatibility tools that still return only prose.

For each workflow define:

- typed input/service request where useful;
- `DomainResult` data/error/warnings/meta;
- bounded collection/pagination data;
- compatibility text derived from the same result;
- primary/compact impact;
- schema and representative call tests.

**Acceptance:**

- callers can determine success, retryability, operation state, and next action without text parsing;
- existing text remains useful but cannot contradict structured state;
- raw runtime objects and private transport bodies are excluded from serialization;
- adapter errors use stable public messages and diagnostic IDs;
- updated tools validate against generated output schemas.

## P3.3 Reduce Compact-Adapter Debt

**Goal:** make `src.skill_server` progressively thinner while keeping its low-token tool surface.

Work package pattern:

1. select one facade action family;
2. characterize input schema, defaults, text, and metadata;
3. extract reusable execution/parsing to `src/services/`;
4. keep compact dispatch/defaults/presentation in the adapter;
5. route any equivalent primary tools through the same service;
6. add semantic parity tests;
7. remove obsolete underscored imports or duplicate handlers.

**Acceptance:**

- migrated behavior exists once below the adapters;
- no new private `tools.manage` dependency is introduced;
- compact tool count and discoverability are preserved unless intentionally changed;
- schema snapshots and installed entrypoint smoke remain stable;
- the change is incremental and reviewable.

## P3.4 Establish the First Live Canary Baseline

**Goal:** convert the already-tested canary machinery into a deliberate, bounded observation of current Gemini Web behavior.

Prerequisites:

- dedicated non-personal test account;
- repository/environment opt-in controls enabled intentionally;
- no PR or normal CI path can trigger live access;
- current dependency matrix and report schema green offline.

Initial live scope should remain read-only and bounded:

- account status/availability stage;
- centralized capability probes;
- parser/envelope/rejection classification;
- Web build/locale only when available;
- no raw bodies or account content persisted.

**Acceptance:**

- one schema-valid live report is retained as explicit live evidence;
- report states repository commit and dependency versions;
- failures distinguish operational setup from provider rejection or parser drift;
- issue automation creates/updates one actionable compatibility issue on drift and closes it on recovery;
- documentation says exactly what was and was not observed.

Only expand to live text/media/research/mutation workflows after the read-only baseline is stable and each expansion has its own cleanup and evidence contract.

## P3.5 Cut a Coherent Public Release

**Goal:** publish the accumulated unreleased architecture and product work with a clear compatibility story.

Decisions required:

- next semantic version based on public behavior and compatibility;
- whether any changed result/schema behavior is additive or breaking;
- legacy `2025-11-25` protocol support/deprecation messaging;
- release notes for typed results, artifacts, onboarding, live canary, and SDK v2;
- tagged versus evergreen install guidance.

**Acceptance:**

- `pyproject.toml`, tag, wheel metadata, sdist, skill asset, changelog, and documented tagged URLs agree;
- all static/test/contract/skill/package/protocol gates pass;
- downloaded release assets are reinstalled and revalidated outside the build job;
- onboarding succeeds from the published asset/source path;
- release notes distinguish implemented routing, fixture evidence, and live observations;
- no old higher/lower version line is silently overwritten or misrepresented.

## P3.6 Ecosystem Adoption and Feedback

**Goal:** improve actual usability for Codex, Claude Desktop, Claude Code, VS Code, and other MCP clients from reproducible reports.

Candidate work:

- platform-specific command/path fixes;
- clearer profile selection for text versus multimodal use;
- actionable browser/Cookie diagnostics;
- artifact path discoverability and client rendering;
- long-operation progress/resume UX;
- issue templates that collect client version, package source, profile, protocol, domain result, and diagnostic ID.

**Acceptance:**

- each change links to a reproducible user report or test gap;
- checked-in examples parse and are exercised in CI;
- no credentials are required for installation/protocol preflight;
- live account steps are clearly separated from offline installation;
- tool additions are justified by workflow value rather than marketplace comparison alone.

## P3.7 Ongoing RPC and Model Compatibility

**Goal:** keep centralized reverse-engineered contracts current without scattering emergency fixes.

For every observed drift:

1. identify transport/envelope/RPC/parser/verification stage;
2. add a sanitized fixture;
3. update the central registry/parser/model routing;
4. return `UPSTREAM_CHANGED` or explicit unavailable state when evidence is insufficient;
5. run related service, tool, canary, and package tests;
6. report whether the fix is fixture-only or live-observed.

**Acceptance:**

- raw IDs and payload shapes remain centralized;
- parser tests cover success, empty, rejected, and changed shape;
- mutations retain read-back verification;
- capability evidence includes observation metadata only when known;
- dependency bounds/matrix are updated when compatibility changes.

# Immediate Suggested Issue Order

1. `fix(adapter): align compatibility failure text with DomainResult`
2. `test(integration): audit primary/compact error and operation-state parity`
3. `refactor(compact): migrate history facade to typed shared service`
4. `refactor(compact): migrate account facade result presentation`
5. `refactor(compact): type prompts/cookie/doctor/cleanup results`
6. `test(canary): record first deliberate read-only live baseline`
7. `release: decide and prepare the next public version`
8. `docs(onboarding): incorporate reproducible client installation reports`

External Glama listing/account work may proceed separately and should not block these engineering packages.

# Definition of Done for Any Work Package

- the contract or defect is stated before implementation;
- acceptance criteria are encoded in focused tests;
- the lowest shared owner is changed rather than duplicating adapter fixes;
- primary and compact surfaces are updated or explicitly proven unaffected;
- compatibility text and structured result agree;
- manifest/schema/snapshot/docs/evaluation changes are synchronized when required;
- targeted checks run before full repository gates;
- installed-product/protocol checks run when registration, dependencies, resources, or entrypoints change;
- both development skill mirrors remain byte-identical when the baseline/roadmap changes;
- the PR states fixture/package/protocol evidence separately from live Gemini observation;
- remaining uncertainty has a concrete next dependency or issue.
