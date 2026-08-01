# Development Roadmap and Acceptance Criteria

Load this reference when planning repository work, choosing the next issue, or deciding whether a refactor is complete.

## Product North Star

Deliver a public, installable Gemini Web MCP gateway that agents can use as a dependable multimodal capability provider. A release is valuable when agents can complete workflows and verify outputs, not merely when the repository exposes more upstream calls.

## Prioritization Rule

Prefer work that removes semantic ambiguity or duplicate implementation across many tools. Do not start a broad MCP v2 rewrite before the P0 lifecycle and service boundaries are stable, unless the task is an isolated compatibility experiment.

## P0 — Correctness Foundation

### P0.1 Async-safe client lifecycle

**Problem:** synchronous locks surround awaited initialization and reset can race with in-flight initialization.

**Deliverables:**

- async-safe initialization coordinator;
- one shared initialization attempt for concurrent callers;
- deterministic failure/retry behavior;
- reset generation/race handling;
- deliberate close/retirement of replaced clients.

**Acceptance:**

- a test suspends inside fake `client.init()` and runs at least three concurrent initializers without deadlock or duplicate init;
- reset during initialization cannot mark the new generation initialized incorrectly;
- initialization failure permits a later retry;
- all existing client wrapper tests remain green.

### P0.2 Unified session lifecycle

**Problem:** primary and compact servers implement different session storage and error behavior; compact IDs can collide and unknown reset may clear all.

**Deliverables:**

- typed `SessionHandle`/`SessionState`;
- collision-resistant IDs;
- one SessionService;
- explicit get/create/send/list/reset-one/reset-all semantics;
- primary and compact adapter parity.

**Acceptance:**

- deleting a session then creating a new one cannot overwrite another session;
- unknown ID returns `SESSION_NOT_FOUND` and changes no state;
- reset-one never resets the client or unrelated sessions;
- reset-all is explicit;
- both servers return equivalent domain results.

### P0.3 Typed result and error model

**Problem:** success and failure are encoded as ordinary prose.

**Deliverables:**

- `DomainResult`, `DomainError`, warning, and result metadata types;
- stable error code taxonomy;
- adapters for current MCP text compatibility;
- first migration of client/session/chat paths.

**Acceptance:**

- callers can determine success, retryability, operation state, and suggested action without parsing text;
- raw exceptions are logged with a request/diagnostic ID;
- regression tests cover invalid input, auth failure, timeout, upstream rejection, and internal error;
- no behavior relies on emoji matching.

### P0.4 Shared service boundary for primary and compact servers

**Problem:** duplicated handlers drift and compact server imports private management helpers.

**Deliverables:**

- service layer introduced for one high-value domain, then expanded incrementally;
- both MCP adapters call the service;
- parity tests;
- duplicated implementation removed after migration.

**Acceptance:**

- no new domain logic is added directly to both servers;
- migrated compact handlers no longer import underscored helpers from `tools.manage`;
- tool names and supported arguments remain compatible unless explicitly versioned;
- characterization and parity tests pass.

## P1 — Multimodal Reliability and Maintainability

### P1.1 Unified artifact model

**Deliverables:** typed artifact schema, artifact verification helpers, and common formatting for image/video/audio/file/report outputs.

**Acceptance:**

- generated media results distinguish remote URI, local file, queued/empty/failure states;
- saved artifacts are checked for existence and relevant metadata;
- model/backend evidence is included;
- primary and compact media results refer to the same artifact identities.

### P1.2 Split management domains and centralize RPC contracts

**Deliverables:** separate history, account, notebook, scheduled, Gem, manifest/doctor modules or services; centralized RPC registry and pure parsers.

**Acceptance:**

- `manage.py` is no longer the required dependency for unrelated compact services;
- raw RPC IDs/payloads are not duplicated across handlers;
- each parser has fixture-based tests for success, empty, rejection, and changed-shape cases;
- mutation services return read-back verification status.

### P1.3 Conversation lifecycle consistency

**Deliverables:** one lifecycle service for sessions, upstream chat IDs, retention/cleanup state, and observable cleanup results where applicable.

**Acceptance:**

- primary and compact calls have the same lifecycle metadata;
- cleanup operations are idempotent;
- failed cleanup remains diagnosable;
- no unrelated session is affected by missing/invalid IDs.

### P1.4 Single version and release metadata source

**Deliverables:** runtime version read from package metadata or one generated module; checks for stale version literals; release script consistency.

**Acceptance:**

- package, server banners, docs examples, wheel, tag, and release assets agree;
- historical changelog versions remain untouched;
- one automated test/check detects drift.

### P1.5 Dependency and package integrity

**Deliverables:** direct dependency declarations, tested upstream ranges, package-internal data resources, explicit console entrypoints, wheel smoke tests.

**Acceptance:**

- clean wheel installation can import and start both intended server surfaces;
- default prompt/resource files are present in installed distributions;
- `pip check` succeeds;
- no runtime import relies accidentally on a transitive dependency.

### P1.6 CI and release gates

**Deliverables:** lint, type, test, package, skill, and protocol smoke jobs with useful caching and failure messages.

**Acceptance:**

- CI runs Ruff and mypy after they are added to dev dependencies;
- tests have an agreed coverage threshold or targeted contract checklist;
- wheel/sdist build and clean-install smoke run in CI;
- representative profiles list expected tools;
- both development skill copies validate and match;
- release workflow verifies generated assets before publishing.

### P1.7 Accurate stream and long-operation semantics

**Deliverables:** corrected naming/docs or client-visible progress/task support; cumulative/delta stream normalization; explicit operation states.

**Acceptance:**

- streamed content is not duplicated;
- a client can distinguish queued/running/completed/timed-out;
- timeout results retain upstream IDs when continuation is possible;
- tests cover cancellation and late completion behavior.

## P2 — Protocol and Compatibility Evolution

### P2.1 MCP Python SDK v2 adapter

**Deliverables:** dedicated adapter branch/module using current SDK v2 APIs, structured outputs, new discovery/lifecycle behavior, and cross-client tests.

**Acceptance:**

- domain services are unchanged by the protocol adapter;
- current supported clients can list and call representative tools;
- output schemas validate against actual results;
- golden tool-list/schema tests are re-baselined intentionally;
- v1 compatibility policy and deprecation date are documented.

### P2.2 Live Gemini Web compatibility canary

**Deliverables:** opt-in scheduled workflow using a dedicated account, capability probes, sanitized fixtures/diagnostics, and upstream dependency matrix.

**Acceptance:**

- unit/PR CI remains offline;
- live workflow reports capability state without exposing account content;
- failures identify dependency version, Web build/locale when available, RPC/capability, and parser stage;
- known upstream drift opens or updates an actionable issue rather than silently failing releases.

### P2.3 Public distribution and onboarding

**Deliverables:** verified one-command installs, clear distinction between runtime and development skills, package/release smoke, client examples, and compact/default profile guidance.

**Acceptance:**

- a clean user environment can install and call a text tool;
- a multimodal example produces a verifiable artifact;
- documentation distinguishes expected versus observed backend behavior;
- development contributors can install this skill directly from the repository.

## Suggested Issue Order

1. `fix(client): make initialization async-safe`
2. `fix(session): unify primary and compact session lifecycle`
3. `refactor(result): add typed domain results and errors`
4. `refactor(core): extract shared chat/session services`
5. `feat(media): introduce unified artifact results`
6. `refactor(manage): split domains and centralize RPC registry`
7. `build: establish package version and data single sources`
8. `ci: add installed-product and profile contract gates`
9. `refactor(stream): align stream and long-operation semantics`
10. `feat!: add MCP SDK v2 adapter`
11. `test: add opt-in Gemini Web compatibility canary`

## Definition of Done for Any Work Package

- acceptance criteria are encoded in tests;
- primary and compact surfaces are updated or explicitly documented as unaffected;
- structured result/manifest/docs/evaluation changes are synchronized;
- package/import behavior remains valid;
- no temporary duplicate implementation remains without a follow-up issue and removal condition;
- the PR states what was observed live versus simulated or fixture-tested.
