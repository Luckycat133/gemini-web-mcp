# Current State, Missing Work, and Known Defects

Use this reference when auditing the repository or choosing the next engineering task. Verify every statement against the current checkout and recent CI before acting.

## What Is Already Implemented

The project already has a broad usable surface:

- primary MCP server with narrow `model`, `history`, `history-organize`, `account-read`, `scheduled-read`, `scheduled-admin`, `core`, and `all` profiles;
- compact eleven-tool facade for low-token discovery;
- one-shot and session chat, thinking levels, guided-learning modes, temporary chats, and Gem-backed chat;
- image generation/editing, video, music, local-file analysis, URL analysis, and Deep Research;
- typed artifact state for remote/local/queued/empty/failed outcomes, plus shared typed results for history list/search/read/export/delete;
- history list/scan/search/read/export/delete, native Notebook reads and chat moves;
- account inventory, usage, public-link reads, model/mode discovery, scheduled-action daily create/get/list/delete, and Gem CRUD;
- prompts, cookie diagnostics, doctor, cleanup, onboarding, package/release automation, and an opt-in live canary;
- official MCP SDK v2 discovery, structured content, generated output schemas, and compatibility negotiation.

## Confirmed Defects Fixed in the Current Audit Package

### Compact History Mapping Compatibility

The compact `history` list/read paths previously assumed upstream records were attribute objects. Mapping-backed chats could render as `Untitled` with blank IDs, and mapping-backed turns could render as `unknown` with empty text. The client facade now adapts mapping-backed history values once while preserving mapping semantics and the original client identity.

### Volatile README Test Count

A hardcoded numeric test badge drifted behind the real suite. Public badges should report CI verification rather than a manually maintained test count.

### History Adapter Duplication

Primary and compact history list/search/read/export/delete now normalize object- and mapping-backed records in the shared
history service and return the same typed domain data while preserving surface-specific compatibility text. Search retains
source-page-before-filter pagination. Delete reports accepted/unverified unless read-back positively observes absence, and
fails closed when the chat remains visible or read-back errors. Positive absence requires complete fresh pagination across
the canonical recent/pinned metadata buckets; the dependency's ambiguous `read_chat(None)` result is never deletion proof.

### Unbounded macOS Keychain Wait

`browser-cookie3` previously called macOS `security ... find-generic-password` with an unbounded `communicate()`, so even
`validate=false` profile diagnostics could hang the MCP process. The repository now installs a locked, reversible reader
only around browser-cookie access, applies `GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS`, restores the dependency function, and
returns a sanitized timeout code without exposing Cookie values.

### Repository Description Drift

The GitHub repository description previously called the project a “FastMCP server.” It now identifies the project as an
agent-first MCP Python SDK v2 gateway and skill set, matching the checked-in runtime and public documentation.

## Remaining High-Priority Engineering Gaps

### 1. No Current Live Baseline

Offline, package, protocol, and synthetic canary tests are strong, but the scheduled live canary remains inactive unless repository variables and a dedicated-account environment are configured. The current Gemini Web build, account routing, media generation, and private RPC behavior therefore remain unverified until an authorized live run is recorded.

### 2. Incomplete Typed-Result Coverage

Core chat/session/artifact/research paths and shared history list/search/read/export/delete are structured, but the primary-only deep history scan plus many account, prompt, cookie, doctor, cleanup, scheduled, Notebook, and compatibility tools still expose prose-first results. Agents should not need to parse phrases or emoji to determine success, retryability, pagination, or verification.

### 3. Primary/Compact Facade Duplication

All compact history actions now use the shared history service. Compact account, prompt, cookie, doctor, cleanup, and parts of scheduled presentation still retain adapter-owned execution or formatting. Migrate one bounded action family at a time; keep compactness as a discovery/presentation property, not a separate business implementation.

### 4. Mutation Verification Is Uneven

Gem mutations and chat deletion now fail closed without positive read-back evidence; Notebook moves and scheduled actions also preserve several read-back states. Apply the same review to cleanup, prompt storage, remaining scheduled/Notebook branches, and any future sharing/settings mutation. Define which source is authoritative and which ambiguous states are partial rather than successful.

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
2. Continue typed results for the primary deep history scan and account, then prompt/cookie/doctor/cleanup.
3. Audit every remote mutation for positive read-back and honest presentation.
4. Introduce a shared long-operation job contract.
5. Decide cleanup durability and release/version strategy.
6. Improve end-to-end video, music, file, URL, and research onboarding before pursuing broad UI parity.
