# Current Architecture and Remaining Work

Use this reference when auditing the repository or choosing the next engineering task. Verify every statement against the current checkout, recent CI, and the latest live evidence before acting.

## Current Product Baseline

The project already exposes a broad agent-facing Gemini Web surface:

- primary MCP server profiles for narrow model, history, organization, account, scheduled, core, and full maintenance workflows;
- a compact eleven-tool facade for low-token discovery;
- one-shot chat, multi-turn sessions, thinking levels, learning modes, temporary chats, and Gem-backed chat;
- image generation/editing, video, music, local-file analysis, URL analysis, and Deep Research;
- typed artifact state for remote, local, queued, empty, partial, and failed outcomes;
- shared typed history list/search/read/export/delete across primary and compact adapters;
- native Notebook reads and chat moves;
- account inventory, usage, public-link reads, model/mode discovery, scheduled daily create/get/list/delete, and Gem CRUD;
- prompts, Cookie diagnostics, doctor, cleanup, onboarding, package/release automation, and an opt-in live canary;
- official MCP Python SDK v2 discovery, generated schemas, structured content, and modern/legacy negotiation.

## Recently Completed Foundations

These are current baseline, not future work:

### Shared History and Deletion Evidence

Primary and compact history list/search/read/export/delete use one typed history service. Object- and mapping-backed values are normalized once, compatibility text is preserved, and deletion distinguishes:

- verified absence after complete fresh metadata pagination;
- accepted but unverifiable or incomplete read-back;
- still-present records;
- read-back failure.

An ambiguous `read_chat(None)` or a zero-result marker search is not deletion proof.

### Mutation Truthfulness

Gem create/update/delete fails closed without positive read-back evidence. Notebook moves and scheduled actions preserve multiple verification states. Compatibility text may not advertise success when structured evidence is ambiguous or contradictory.

### Runtime and Protocol Boundary

The runtime uses `mcp>=2,<3` and `mcp-types>=2,<3` through the project-owned SDK adapter. Primary and compact entrypoints are exercised over real stdio in current and legacy protocol modes.

### Browser-Cookie Authorization

Browser Cookie access is bounded, sanitized, and reversible. The ClawHub `0.2.0` runtime-skill patch established that Cookie export can create sensitive local authentication material, must be explicitly approved, and must never be logged or treated as arbitrary credential-file access.

### Skill and Distribution Layout

`.agents/skills` is the single repository source for the runtime and development skills. The runtime skill and Python package share the canonical `0.2.0` version; inspect ClawHub separately for publication state. Wheel, source distribution, runtime skill archive, clean install, and isolated onboarding are maintained gates.

### Current Live Evidence Boundary

A bounded authorized run on 2026-08-08 observed Cookie initialization, temporary/retained text, multi-turn sessions, typed primary/compact history, and verified absence for four created chats. It did not establish a dedicated-account full baseline and did not observe media, files, URLs, Deep Research, Web build, account tier, or account mutations.

## Remaining High-Priority Engineering Gaps

### 1. Dedicated Full Live Baseline

The scheduled canary still skips unless the repository variables, dedicated environment, and account secrets are configured. Release-grade confidence still needs current evidence for:

- Web build, locale, account tier, and entitlements;
- image, video, and music artifacts;
- local file and URL analysis;
- Deep Research start, timeout, recovery, and report retrieval;
- disposable scheduled/Gem/Notebook mutations with read-back;
- cleanup of every created test resource.

### 2. Incomplete Typed-Result Coverage

Typed results cover core chat/session/artifact/research and shared history workflows. Remaining prose-first or uneven paths include:

- primary-only deep history scan;
- account inventory sub-actions and compatibility probes;
- prompt, Cookie, doctor, and cleanup workflows;
- portions of scheduled and Notebook presentation;
- some maintenance and manifest-adjacent compatibility handlers.

Agents should not need to parse prose or emoji to determine success, retryability, pagination, verification, or next action.

### 3. Remaining Compact-Adapter Duplication

Compact history execution is shared. Compact account, prompt, Cookie, doctor, cleanup, and parts of scheduled presentation still own duplicated execution or formatting. Migrate one bounded action family at a time into shared services while preserving the compact eleven-tool discovery surface.

### 4. Uneven Mutation Verification

Chat deletion and Gem mutations have strict evidence contracts. Complete the same audit for:

- cleanup deletions;
- prompt storage and deletion;
- remaining scheduled branches;
- remaining Notebook branches;
- future sharing, public-link, settings, and Library mutations.

Each mutation needs an authoritative source, explicit ambiguous states, and positive terminal evidence before success text.

### 5. No Shared Long-Operation Job API

Deep Research and media expose queued/running/timed-out states and identifiers, but recovery remains workflow-specific. Introduce one shared contract:

```text
start -> operation_id
status(operation_id)
result(operation_id)
cancel(operation_id)
```

The service must define provider-backed identifiers, local registry state, persistence, cancellation, expiry, and behavior after process restart.

### 6. Cleanup Is Process-Local

Delayed remote-chat cleanup and observations are held in memory. A restart can lose pending work. The likely direction is:

- prefer provider-native temporary chats;
- persist only workflows that require delayed deletion;
- keep caller-controlled retention and TTL;
- expose pending/retry/terminal cleanup state.

The persistence mechanism still requires an owner decision.

### 7. Search Pagination Contract

History search currently applies `offset`/`limit` to the source page before filtering, not to a global match result set. This is bounded and predictable but may surprise users. Preserve the current behavior until a reviewed contract defines post-filter pagination, remote-read cost, and `scan_turns=true` limits.

## Deferred Gemini UI Workflows

These are real missing capabilities, but should follow core reliability and stable live evidence:

- Google Drive picker/attachment import;
- Canvas create/read/update/export;
- scheduled-action edit, enable/disable, weekly, and richer recurrence;
- Notebook create, rename, delete, and source management;
- public-link create/update/revoke and sharing management;
- history rename, pin/unpin, archive, and share;
- personalization/settings and memory-import mutations;
- Library asset listing and management;
- genuine client-visible incremental progress.

Theme, help, feedback, location, subscription, and other UI chrome remain out of scope unless a concrete agent workflow requires them.

## Settled Product Directions

Do not repeatedly reopen these choices:

- browser-Cookie handling follows the explicit-approval and sensitive-cache contract;
- the compact server remains the low-token discovery product while execution moves into shared services;
- core multimodal reliability, artifact delivery, recovery, and agent task completion take priority over broad UI parity;
- `.agents/skills` remains the single repository skill source.

## Recommended Development Order

1. Configure and record the dedicated full live baseline.
2. Complete typed results for deep history and account/admin workflows.
3. Finish the mutation read-back audit.
4. Add the shared long-operation service and tool contract.
5. Implement the selected durable cleanup model.
6. Add video/music/file/URL/research onboarding and a real client/platform matrix.
7. Revisit UI-parity features only after the preceding workflows are stable.
