# Current State, Missing Work, and Known Defects

Use this reference when auditing the repository or choosing the next engineering task. Verify every statement against the current checkout and recent CI before acting.

## What Is Already Implemented

The project already has a broad usable surface:

- primary MCP server with narrow `model`, `history`, `history-organize`, `account-read`, `scheduled-read`, `scheduled-admin`, `core`, and `all` profiles;
- compact eleven-tool facade for low-token discovery;
- one-shot and session chat, thinking levels, guided-learning modes, temporary chats, and Gem-backed chat;
- image generation/editing, video, music, local-file analysis, URL analysis, and Deep Research;
- typed artifact state for remote/local/queued/empty/failed outcomes;
- history list/scan/search/read/export/delete, native Notebook reads and chat moves;
- account inventory, usage, public-link reads, model/mode discovery, scheduled-action daily create/get/list/delete, and Gem CRUD;
- prompts, cookie diagnostics, doctor, cleanup, onboarding, package/release automation, and an opt-in live canary;
- official MCP SDK v2 discovery, structured content, generated output schemas, and compatibility negotiation.

## Confirmed Defects Fixed in the Current Audit Package

### Compact History Mapping Compatibility

The compact `history` list/read paths previously assumed upstream records were attribute objects. Mapping-backed chats could render as `Untitled` with blank IDs, and mapping-backed turns could render as `unknown` with empty text. Use the shared history accessors for both object and mapping inputs.

### Volatile README Test Count

A hardcoded numeric test badge drifted behind the real suite. Public badges should report CI verification rather than a manually maintained test count.

## Remaining High-Priority Engineering Gaps

### 1. No Current Live Baseline

Offline, package, protocol, and synthetic canary tests are strong, but the scheduled live canary remains inactive unless repository variables and a dedicated-account environment are configured. The current Gemini Web build, account routing, media generation, and private RPC behavior therefore remain unverified until an authorized live run is recorded.

### 2. Incomplete Typed-Result Coverage

Core chat/session/artifact/research paths are structured, but many history, account, prompt, cookie, doctor, cleanup, scheduled, Notebook, and compatibility tools still expose prose-first results. Agents should not need to parse phrases or emoji to determine success, retryability, pagination, or verification.

### 3. Primary/Compact Facade Duplication

The compact history, account, scheduled, prompt, cookie, doctor, and cleanup facades retain adapter-owned execution and formatting. Migrate one action family at a time to shared services; keep compactness as a discovery/presentation property, not a separate business implementation.

### 4. Mutation Verification Is Uneven

Gem mutations now fail closed without positive read-back evidence. Apply the same review to chat deletion, Notebook moves, scheduled actions, cleanup, prompt storage, and any future sharing/settings mutation. Define which source is authoritative and which ambiguous states are partial rather than successful.

### 5. Cleanup Is Process-Local

Pending remote-chat cleanup and its observations live in memory. A server restart can lose delayed deletion work. Choose between best-effort process-local cleanup, a durable local queue, or using provider-native temporary conversations wherever possible.

### 6. Long Operations Lack a First-Class Job API

Deep Research and media can report queued/running/timed-out states and preserve identifiers, but agents do not have a uniform `start / status / result / cancel` contract. A dedicated operation service would make timeout recovery and cross-client UX substantially clearer.

### 7. Search Pagination Semantics Need a Contract

Current history search treats `offset`/`limit` as the source page to scan, not necessarily the match page to return. That is bounded and predictable but can surprise users who expect global match pagination. Decide and document whether search pagination applies before or after filtering, especially when `scan_turns=true` could require many remote reads.

## Gemini UI Workflows Not Yet Implemented

These are real missing capabilities, but should only be added when they improve agent workflows and have stable evidence:

- Google Drive picker/attachment import;
- Canvas document create/read/update/export;
- scheduled-action edit, enable/disable, weekly, and richer recurrence;
- Notebook create, rename, delete, and source management;
- public-link create, update, revoke, and sharing management;
- history rename, pin/unpin, archive, and share workflows;
- personalization/settings and memory-import mutations;
- Library asset listing beyond capability/probe surfaces;
- first-class media/research job polling and cancellation;
- genuine client-visible incremental progress rather than collected upstream streams;
- onboarding commands for video, music, file, URL, and Deep Research.

Theme, help, feedback, location, subscription, and other UI chrome should remain out of scope unless a concrete agent workflow requires them.

## Recommended Priority

1. Run and record a deliberate read-only live baseline.
2. Complete typed results for compact history/account, then prompt/cookie/doctor/cleanup.
3. Audit every remote mutation for positive read-back and honest presentation.
4. Introduce a shared long-operation job contract.
5. Decide cleanup durability and release/version strategy.
6. Improve end-to-end video, music, file, URL, and research onboarding before pursuing broad UI parity.
