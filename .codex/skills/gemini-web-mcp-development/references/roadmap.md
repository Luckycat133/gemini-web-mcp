# Development Roadmap and Acceptance Criteria

Load this reference when choosing the next issue, planning a multi-file change, or deciding whether a maintenance package is complete.

## Product North Star

Deliver a public Gemini Web compatibility gateway that agents can use as a dependable text and multimodal capability provider. Progress is measured by completed workflows, truthful machine-readable state, verifiable artifacts, reproducible installation, and recoverable upstream drift—not tool count.

## Completed Foundation

The original P0-P2.3 roadmap is implemented:

| Phase | Implemented contract |
| --- | --- |
| P0.1 | async-safe initialization, generation-aware reset, deliberate retirement |
| P0.2 | one cross-adapter `SessionService`, opaque IDs, explicit reset/not-found semantics |
| P0.3 | typed results/errors/warnings, operation state, diagnostics, MCP metadata |
| P0.4 | shared chat/session service and parity |
| P1.1 | unified multimodal artifact identity/state/verification/backend evidence |
| P1.2 | management services plus centralized RPC registry/parsers/read-back verification |
| P1.3 | observable conversation cleanup and lifecycle metadata |
| P1.4 | single persisted version and release metadata validation |
| P1.5 | direct bounded dependencies, package resources, entrypoints, clean-wheel smoke |
| P1.6 | lint/type/test/contract/package/profile/protocol/skill/release gates |
| P1.7 | collected-stream normalization and long-operation state/cancellation |
| P2.1 | MCP SDK v2 adapter with modern/legacy negotiation and structured output |
| P2.2 | opt-in live canary, sanitized schema, dependency matrix, issue automation |
| P2.3 | public onboarding, client examples, isolated install, verified image example |

## Completed P3.1 Integration Packages

### Compatibility text versus typed errors

The shared MCP result adapter now prevents an explicit legacy error-code prefix from contradicting `DomainResult`. Compact session chat no longer labels authentication/network/upstream failures as missing sessions.

### Post-development manage/CI stabilization

Broad management-handler branch contracts were added, missing Gem helper aliases restored, tests decoupled from an MCP-v1-only FastMCP package through a test-only shim, and the live-canary job-level environment context was repaired.

### Gem mutation verification presentation

Gem list rendering now supports mapping-backed and object-backed values, required names/IDs reject surrounding-whitespace-only input, and create/update/delete text follows read-back evidence. Only `verified`, matching update evidence, or `verified_deleted` produces a success marker; mismatch, read-back failure, missing ID, or still-present evidence remains explicit and non-successful.

# P3 — Active Reliability, Release, and Adoption Work

## P3.2 Complete Typed-Result Coverage

**Goal:** migrate remaining prose-only or inconsistently typed workflows without breaking tool names.

Suggested order:

1. compact history list/search/read/export/delete;
2. compact account inventory;
3. primary management compatibility actions, including Gems;
4. prompt store actions;
5. cookie status/profile/get;
6. doctor and cleanup.

**Acceptance:**

- success, retryability, operation state, verification, and next action are machine-readable;
- text and structured state agree;
- runtime objects/raw transport bodies are excluded;
- collection results expose bounded pagination/truncation;
- generated output schemas validate actual calls.

## P3.3 Reduce Adapter Debt

Migrate one facade family at a time from `skill_server.py` or `tools/manage.py` into a shared service. Preserve compact discoverability and existing public names. Add primary/compact semantic parity before deleting duplicate execution/parsing.

## P3.4 Establish the First Live Canary Baseline

Use a dedicated non-personal account and all explicit opt-in controls. Start with bounded read-only capability probes. Retain one schema-valid report with commit/dependency evidence, distinguish operational setup from provider/parser drift, and state exactly what was observed.

## P3.5 Cut a Coherent Public Release

Decide the next semantic version and whether any result/schema changes are additive or breaking. Align `pyproject.toml`, tag, wheel, sdist, skill asset, changelog, docs, and protocol policy. Reinstall downloaded assets and rerun onboarding before publication.

## P3.6 Ecosystem Adoption

Improve Codex, Claude Desktop, Claude Code, VS Code, and other client setup from reproducible reports. Focus on command/path issues, profile selection, diagnostic quality, artifact discoverability, and long-operation UX.

## P3.7 Ongoing RPC and Model Compatibility

For each drift: identify the failing stage, add a sanitized fixture, update centralized registry/parser/routing, preserve explicit unavailable/changed state, run service/tool/canary/package tests, and label the evidence fixture-only or live-observed.

## Immediate Suggested Issue Order

1. `refactor(compact): migrate history facade to typed shared service`
2. `refactor(compact): migrate account facade result presentation`
3. `refactor(manage): add typed Gem mutation results while preserving truthful text`
4. `refactor(compact): type prompts, cookie, doctor, and cleanup results`
5. `test(integration): audit remaining mutation read-back presentation`
6. `test(canary): record first deliberate read-only live baseline`
7. `release: decide and prepare the next public version`
8. `docs(onboarding): incorporate reproducible client reports`

External marketplace/account work does not block these engineering packages.

## Definition of Done

- state the contract or defect before implementation;
- encode acceptance criteria in focused tests;
- change the lowest shared owner;
- prove primary/compact impact or non-impact;
- keep compatibility text, structured result, and verification evidence consistent;
- synchronize manifest/schema/snapshot/docs/evaluation/skill changes when required;
- run focused checks before full gates;
- run installed-product/protocol/workflow checks for affected surfaces;
- keep development skill mirrors byte-identical;
- distinguish fixture/package/protocol/workflow evidence from live observation;
- leave remaining uncertainty with a concrete dependency or issue.
