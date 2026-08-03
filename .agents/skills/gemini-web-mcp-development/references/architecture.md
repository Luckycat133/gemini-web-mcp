# Current Architecture and Remaining Debt

Load this reference for repository audits, refactors, lifecycle fixes, adapter work, package architecture, or protocol evolution.

## Product Direction

Gemini Web MCP is a local compatibility gateway for agents. Its durable value is a stable, observable MCP contract over a changing Gemini Web implementation—not a collection of private RPC snippets.

The gateway should let a general-purpose agent complete text and multimodal workflows without knowing Gemini Web payload layouts. Reverse-engineered behavior belongs behind registries, parsers, services, typed results, fixtures, and separately reported live evidence.

## Implemented Runtime Topology

```text
MCP clients
   |
   +-- gemini-mcp-onboarding / src.onboarding
   |      installed-product preflight + gated live examples
   |
   +-- src.server
   |      primary profile-based MCP adapter
   |
   +-- src.skill_server
          compact low-token MCP adapter
                |
                +--------------------------+
                                           |
                         src/adapters       | MCP SDK/result/artifact translation
                         src/domain         | typed result/lifecycle/artifact contracts
                         src/services       | shared application behavior
                         src/infrastructure | RPC registry and pure parsers
                                           |
                         client_wrapper / ClientManager / SessionService
                                           |
                         ThinkingLevelGeminiClient + gemini-webapi
                                           |
                         Gemini Web
```

The original P0-P2 foundations have been implemented. Do not use an old debt list to claim they are absent.

## Completed Foundations

### Async client lifecycle

`ClientManager` now shares one initialization task across concurrent callers, shields shared initialization from individual caller cancellation, uses a generation boundary to reject stale completion, and retires replaced clients deliberately. Tests force real suspension and reset/failure races.

### Shared session lifecycle

Primary and compact adapters use one `SessionService` with opaque UUID-based IDs, active-collision checks, explicit `SESSION_NOT_FOUND`, distinct reset-one/reset-all semantics, per-session send serialization, lifecycle metadata, and cross-adapter tests.

### Typed results and adapter metadata

`DomainResult`, `DomainError`, `DomainWarning`, `ResultMeta`, and operation states provide serializable success/failure contracts under `TextContent._meta.domain_result`. Public-safe exception classification keeps raw diagnostics in logs while exposing request and diagnostic IDs.

### Shared chat and stream behavior

`src/services/chat.py` owns one-shot chat, session creation/send, model resolution, request construction, cleanup policy, and lifecycle output for both adapters. `src/services/streams.py` normalizes delta, cumulative, duplicate, stale, and mixed upstream chunks and reports collected-stream metadata accurately.

### Unified multimodal artifacts

The artifact domain represents remote/local/queued/empty/failed image, video, audio, file, report, webpage, and data outputs. Services preserve URI and local path, requested/request/effective/observed backend evidence, file metadata, dimensions/duration where available, and explicit save/verification failures.

### Management service extraction and RPC registry

History, account, Notebook, scheduled-action, Gem, manifest, doctor, and cleanup behavior have shared service modules. `src/infrastructure/rpc_contracts.py` and pure parsers centralize observed RPC identifiers, source paths, payload/parser metadata, stability, and mutation verification strategy. Ambiguous mutations use read-back checks.

### Cleanup lifecycle

Remote conversation cleanup exposes typed pending/completed/failed observations and is integrated with shared chat/session workflows. Lifecycle evidence is carried in result metadata rather than hidden entirely in background behavior.

### Package, CI, protocol, and onboarding

The repository now has:

- one persisted version source in `pyproject.toml`;
- direct bounded dependencies and packaged prompt resources;
- primary, compact, and onboarding console entrypoints;
- MCP Python SDK v2 behind `src/adapters/mcp_sdk.py`;
- modern `2026-07-28` and legacy `2025-11-25` protocol smoke;
- Ruff, Mypy, unit tests, contract snapshots, clean-wheel, release, skill, and installed-product jobs;
- checked-in client configurations and an auth-free onboarding path;
- a separately gated live compatibility canary with sanitized report schema.

## Current Architectural Boundaries

### Domain

`src/domain/` owns values that must survive adapter changes:

- errors and operation states;
- serializable results and warnings;
- conversation/session/cleanup lifecycle;
- stream collection metadata;
- artifact identity, state, evidence, and verification.

Domain code must not import MCP SDK types, Gemini clients, file-system presentation helpers, or tool registration.

### Services

`src/services/` owns reusable workflows:

- request construction and execution;
- session/chat lifecycle orchestration;
- artifact extraction and verification;
- management reads and mutations;
- doctor, cleanup, manifest, and compatibility behavior;
- long-operation state and stream normalization.

Services return domain data/results or provider-neutral values. They should not decide final MCP wording.

### Infrastructure

`src/infrastructure/` owns provider-specific facts:

- RPC IDs and source paths;
- payload builders;
- response envelope/body parsers;
- observed contract metadata and stability;
- adapter-specific verification steps.

Changed upstream shapes should be reproduced with sanitized fixtures and fail explicitly as rejection/drift rather than silently becoming an empty result.

### MCP adapters

`src/server.py`, `src/skill_server.py`, `src/tools/`, and `src/adapters/` own:

- tool registration and profile membership;
- MCP input/output schemas and annotations;
- primary versus compact granularity/defaults;
- compatibility text and structured-content attachment;
- SDK/protocol translation.

They must not create a second business implementation.

## Confirmed Remaining Debt

### 1. Compact adapter still has legacy presentation logic

`src.skill_server` now delegates chat/session and some management behavior to shared services, but it still contains a large amount of facade dispatch, prose formatting, prompt storage, cookie/doctor/cleanup presentation, and direct account/history orchestration.

Target approach:

1. select one bounded workflow;
2. characterize current schema/text/metadata;
3. move reusable behavior to an existing or new service;
4. keep compact defaults/presentation in the adapter;
5. add primary/compact semantic parity;
6. delete obsolete private-helper imports.

Do not rewrite the entire compact server at once.

### 2. Typed-result coverage is incomplete

The chat/session, artifact, long-operation, mutation, and selected management slices have typed contracts. Some legacy history, account, prompts, cookie, doctor, cleanup, and helper paths still return only prose or attach typed data inconsistently.

Migrate by workflow. Preserve useful compatibility text, but make success, error code, retryability, operation state, pagination, and verification machine-readable.

### 3. Compatibility text can drift from structured results

A compact session send previously hard-coded `SESSION_NOT_FOUND` for every failed `ChatService.send_session` result, so authentication, network, upstream, or internal failures could be shown to text-only clients as a missing session while `_meta.domain_result` contained the real code.

Architectural rule:

- structured result is authoritative;
- any explicit error-code prefix in compatibility text must match it;
- shared result adapters should derive or validate coded text;
- regression tests must inspect both text and `_meta.domain_result`.

This is an integration-seam class of bug: individual services and schemas can be correct while the final adapter presentation is not.

### 4. No deliberate live baseline has been recorded yet

The live canary implementation, refusal paths, fixtures, report schema, workflow, and issue automation are tested offline. That is not evidence that current Gemini Web behavior has been observed successfully with the dedicated account.

Next live step:

- configure the dedicated environment deliberately;
- run the bounded read-only canary;
- retain only schema-approved diagnostics;
- record dependency/Web build evidence where available;
- distinguish provider drift from credentials, account capability, network, or workflow configuration failure.

### 5. Release/version policy needs a public decision

`main` contains extensive unreleased changes after the existing `1.3.0` line, including MCP SDK v2 and new result/artifact contracts. The mechanics for single-source versioning and verified release assets exist; the remaining question is product versioning and compatibility communication.

Before release:

- decide the next semantic version based on public compatibility, not SDK marketing version;
- document changed guarantees and retained tool names/schemas;
- state the legacy protocol support window;
- produce and independently re-verify release assets;
- ensure onboarding examples point to a valid evergreen or tagged source.

### 6. RPC evidence requires ongoing freshness

The centralized registry solves scattering, not upstream volatility. Each changed capability still needs:

- sanitized success/empty/rejected/changed-shape fixtures;
- parser-stage diagnostics;
- current dependency matrix;
- live canary evidence when explicitly run;
- an observed date/build/account note only when actually known.

### 7. Onboarding needs real ecosystem feedback

Offline clean-install, stdio, configuration parsing, credential stripping, artifact verification, and `uvx` paths are covered. Remaining product work should come from real Codex/Claude/VS Code installation reports:

- platform-specific command/path behavior;
- browser/cookie setup failures;
- long-operation UX;
- artifact discovery and local save expectations;
- profile selection and tool discoverability.

Do not invent client-specific complexity without a reproducible report.

### 8. Development guidance can itself become stale

The previous development skill still described async initialization, session collisions, missing typed results, an unsplit management monolith, missing package data, absent SDK v2, and weak CI as current debt after all had been implemented.

Prevent recurrence by testing that:

- both skill mirrors remain byte-identical;
- the skill names the current foundational modules;
- completed milestones are not presented as active missing work;
- active roadmap phases and validation commands match the checkout.

## Current Runtime Flow

```text
MCP request
  -> SDK v2 adapter and generated schema
  -> adapter-specific validation/defaults
  -> shared application service
  -> Gemini client or registered RPC contract
  -> pure parser / lifecycle / artifact verification
  -> DomainResult + typed data/evidence
  -> structured MCP content + concise compatible text
```

## Refactor Decision Rules

Create or extend a shared service when two adapters need the same workflow, parsing/lifecycle logic is duplicated, or a provider-specific detail leaks into multiple tools.

Keep logic in an adapter when it is only tool registration, argument presets, profile membership, compatibility wording, or selection of structured fields.

Create a domain type when a value must be serialized consistently across workflows or survive provider/SDK changes.

Create an infrastructure contract/parser when the behavior is specific to Gemini Web transport or private response shapes.

## Incremental Migration Pattern

For one remaining legacy workflow:

1. inspect current tool schema, profile registration, text, and tests;
2. add characterization and failure-path tests;
3. define provider-neutral service inputs/results;
4. move RPC/client parsing below the service;
5. route primary and compact adapters through it;
6. compare domain semantics, not necessarily prose;
7. remove duplicated helpers;
8. update manifest, docs, evaluations, and skill references where needed;
9. run package/protocol gates if registration or distribution changed.

This keeps the gateway usable while reducing debt.
