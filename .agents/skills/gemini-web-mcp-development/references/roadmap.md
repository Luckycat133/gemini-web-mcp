# Owner Decisions and Next Development Packages

Use this reference only when a change would alter a product contract. Routine bug fixes and bounded service migrations should proceed from the established defaults without reopening settled questions.

## Decisions That Still Need the Owner

### 1. Next Monotonic Python Package Release

The Python package, rewritten Git tag line, and ClawHub runtime skill all use the canonical `0.2.0` version.

Any future package release must move all canonical version sources together beyond `0.2.0`; the current tree uses MCP Python SDK v2, structured output contracts, and shared services.

Decide:

- whether and when to advance beyond the canonical `0.2.0` release line;
- whether any compatibility period or migration note is required;
- whether PyPI publication begins with that release or GitHub Releases remain canonical first.

Keep rewritten Git history, package metadata, and the ClawHub skill on one canonical version line.

### 2. Dedicated Live-Compatibility Account

A bounded authorized run has proven text, sessions, typed history, and chat cleanup, but it is not the repository's dedicated full canary.

Decide:

- who owns and can recover the account;
- region, locale, tier, and optional media/research entitlements;
- secret rotation and run cadence;
- which evidence may be retained in sanitized reports;
- whether release publication is blocked when the last successful live baseline is too old.

Recommended cadence: weekly read-only probes, a release-candidate multimodal run, and disposable mutation tests only when explicitly requested.

### 3. Long-Operation Persistence and Cancellation

The recommended API is fixed:

```text
start -> operation_id
status(operation_id)
result(operation_id)
cancel(operation_id)
```

The owner still needs to decide:

- whether the local operation registry survives process restarts;
- whether provider-backed IDs are sufficient when local state is lost;
- cancellation guarantees when Gemini has already accepted work;
- operation expiry and artifact-retention defaults;
- whether one operation may be resumed from another MCP client.

Recommended direction: preserve provider identifiers in every result and persist a small local registry containing only recovery metadata, not raw account content.

### 4. Durable Cleanup Semantics

The current delayed cleanup queue is process-local. The default direction is to prefer provider-native temporary chats and retain caller-controlled TTL/retention.

Decide:

- whether unavoidable delayed deletions use a durable local queue;
- persistence format and location;
- retry/backoff and terminal-failure behavior;
- how pending cleanup is listed, cancelled, or transferred after restart;
- whether a release may claim automatic cleanup without restart durability.

Recommended direction: a small local SQLite queue with explicit pending/running/completed/failed states and no stored chat text.

### 5. Official Support and Distribution Matrix

Decide which combinations are officially supported rather than merely documented as examples:

- Codex, Claude Desktop, Claude Code, VS Code, and other MCP clients;
- macOS, Windows, and Linux;
- Python 3.11 and 3.12;
- browser-Cookie discovery availability by platform;
- artifact rendering/path behavior;
- timeout guidance for video, music, and research;
- canonical install path: reviewed Git SHA, immutable GitHub Release wheel, PyPI, ClawHub/runtime skill, and MCP directories.

Recommended first official matrix: Codex plus one desktop client on macOS and Windows, Python 3.11/3.12, reviewed Git SHA for development, and immutable GitHub Release wheel for public releases.

## Settled Directions — Do Not Reopen by Default

- Browser Cookies are sensitive authentication material; the explicit-approval, restricted-cache, no-logging contract is established.
- Session reset affects only MCP/Gemini conversation state.
- The compact eleven-tool facade remains the low-token discovery product; execution should continue moving into shared services.
- Core multimodal reliability, artifact delivery, recovery, and agent task completion take priority over broad Gemini UI parity.
- `.agents/skills` is the single repository source for public skills.

Only revisit these when implementation evidence shows that the established contract cannot work.

## Issue-Sized Next Development Packages

### Package A — Dedicated Full Live Baseline

Deliverables:

- configure the dedicated GitHub environment and repository variables;
- record Web build/locale/tier when observable;
- run read-only capability probes;
- verify temporary text and primary/compact sessions;
- verify image, video, and music artifacts;
- verify local file, URL, and Deep Research workflows;
- run disposable scheduled/Gem/Notebook mutations with read-back;
- delete every created resource by returned ID and record cleanup evidence.

Acceptance criteria:

- sanitized schema-valid report;
- no raw responses, Cookies, account identifiers, or private text;
- each capability classified as observed, unavailable, drifted, or not entitled;
- all created test resources accounted for.

### Package B — Complete Typed Admin and Deep-History Results

Order:

1. primary-only deep history scan;
2. account inventory and compatibility probes;
3. prompt operations;
4. Cookie status/profile/export outcomes;
5. doctor;
6. cleanup;
7. remaining scheduled and Notebook presentation.

Acceptance criteria:

- stable `DomainResult` data/error/meta contracts;
- explicit pagination, truncation, retryability, and verification;
- compatibility text agrees with structured state;
- primary/compact semantic parity where both expose the action.

### Package C — Complete Mutation Verification Audit

Inventory every remote create/update/move/delete. For each mutation define:

- authoritative read-back source;
- positive terminal evidence;
- not-observed, mismatch, still-present, incomplete, and read-back-error states;
- idempotency/retry behavior;
- cleanup or rollback guidance.

Acceptance criteria: no success marker without positive evidence and regression coverage for verified plus ambiguous outcomes.

### Package D — Shared Long-Operation Service

Implement one domain/service contract for Deep Research, video, music, and future asynchronous media.

Acceptance criteria:

- stable operation IDs and provider IDs;
- start/status/result/cancel tools or actions;
- queued/running/completed/timed-out/cancelled/failed states;
- restart behavior matching the owner decision;
- artifact identity preserved from start through result;
- primary/compact parity.

### Package E — Durable Cleanup

Implement the selected persistence model after the owner decision.

Acceptance criteria:

- restart-safe pending work when durability is enabled;
- explicit retry and terminal failure states;
- list/cancel/retry operations;
- no private chat text in persistence;
- direct-ID cleanup remains authoritative over marker search.

### Package F — Multimodal Onboarding and Client Matrix

Add onboarding subcommands and documented manual checks for video, music, file, URL, and Deep Research. Exercise the official client/platform matrix.

Acceptance criteria:

- one copyable command or client workflow per modality;
- independently verified artifacts or structured results;
- timeout/recovery guidance;
- client-specific friction recorded as reproducible issues.

### Package G — Selected UI-Parity Work

Only after Packages A–F are stable, choose user-valued UI workflows such as Drive import, Canvas, richer recurrence, Notebook CRUD/source management, sharing, or Library management. Require current live evidence before adding private RPC contracts.

## Recommended Conversation Order

1. approve any future version advance beyond `0.2.0`;
2. define the dedicated live account and release-blocking policy;
3. choose long-operation persistence semantics;
4. choose durable cleanup semantics;
5. choose the first official client/platform/distribution matrix.
