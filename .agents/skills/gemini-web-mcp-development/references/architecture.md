# Architecture and Technical Debt Map

Load this reference for refactors, lifecycle fixes, module extraction, package architecture, or MCP SDK migration.

## Product Direction

Gemini Web MCP is a local compatibility gateway for agents. The durable product is not a collection of reverse-engineered calls; it is a stable agent contract over a changing Gemini Web implementation.

The gateway should let a general-purpose agent obtain text, image, video, music, file/URL analysis, and Deep Research capabilities without knowing Gemini Web payload layouts. Account/history/notebook/scheduled/Gem capabilities may be exposed when their contracts are sufficiently testable.

## Current Topology

```text
MCP clients
   |
   +-- src.server ---------------- primary profile-based tool surface
   |
   +-- src.skill_server ---------- compact low-token facade surface
             |
             +-- duplicated handlers and imports from tools.manage internals

client_wrapper
   +-- ClientManager
   +-- SessionManager
   +-- RemoteChatCleanupManager
   +-- CookieManager integration
   +-- ThinkingLevelGeminiClient

src/tools
   +-- chat / media / file / research / prompts
   +-- manage (history, account, notebooks, scheduled, Gems, manifest,
               capability probes, doctor, cleanup, RPC parsers)
```

This topology works, but the primary and compact servers have become separate business implementations. The management module is also carrying too many domains.

## Confirmed High-Value Debt

### 1. Async initialization can block the event loop

`ClientManager.initialize()` currently uses a synchronous `threading.Lock` around an awaited network initialization. Under real concurrent calls, one coroutine can hold the lock while suspended and another can synchronously block the event loop trying to acquire it.

Target design:

- one async initialization lock or shared initialization task;
- all concurrent callers await the same task;
- initialization failure clears the task consistently;
- reset uses a generation token or equivalent so stale completion cannot mark a new client initialized;
- replaced clients are closed/retired deliberately.

A concurrency test must force a real suspension (`await asyncio.sleep(0)` or an event) inside fake initialization. A fake async function with no internal await is insufficient.

### 2. Compact session semantics are unsafe and inconsistent

Current compact session IDs are derived from `len(_sessions) + 1`, so deletion can cause collisions. Resetting an unknown ID can clear all sessions. Supplying an unknown session ID to compact `chat()` can silently fall back to a one-shot request.

Target design:

- opaque UUID/ULID-like IDs;
- explicit `SESSION_NOT_FOUND` data result;
- separate reset-one and reset-all operations;
- one shared SessionService used by both servers;
- session state stores model, thinking/learning mode, upstream identifiers, creation/update times, and lifecycle options in a typed model.

### 3. Errors are returned as ordinary prose

Many handlers catch every exception and return normal `TextContent` containing `Error:` or an emoji. Clients cannot reliably distinguish success, partial success, retryable upstream failure, invalid input, unavailable capability, or unverified mutation.

Target design:

```text
DomainResult[T]
  ok: bool
  data: T | null
  error: DomainError | null
  warnings: list[Warning]
  meta: ResultMeta
```

Adapters translate domain errors into the current SDK's supported tool-error mechanism and retain a text fallback for older clients. Raw exception text should remain diagnostic evidence rather than the public contract.

### 4. Primary and compact servers duplicate behavior

`src.skill_server` reimplements chat, history, account, scheduled actions, media, sessions, prompts, and cookie workflows. It imports many underscored helpers from `src.tools.manage`, so changes can drift between servers.

Target design:

```text
src/
  domain/
    results.py
    artifacts.py
    sessions.py
    capabilities.py
  services/
    chat.py
    media.py
    research.py
    history.py
    notebooks.py
    scheduled.py
    account.py
    gems.py
  infrastructure/
    gemini_client.py
    rpc_registry.py
    rpc_parsers/
    artifact_store.py
  adapters/
    mcp_primary.py
    mcp_compact.py
    mcp_v2.py          # later
```

The exact directory names may be introduced incrementally. The important boundary is that adapter code must not own domain behavior.

### 5. The management module is a monolith

`src/tools/manage.py` combines unrelated domains, payload builders, parsers, formatting, registration, and diagnostics. This raises merge conflict risk and encourages compact-server imports of private functions.

Suggested extraction order:

1. pure result/format helpers;
2. RPC registry and response parsers;
3. history service;
4. notebook service;
5. scheduled-action service;
6. account inventory service;
7. Gem service;
8. doctor/cleanup/manifest presentation.

Each extraction should route existing tools through the service before deleting old code.

### 6. Conversation lifecycle behavior is fragmented

Sessions and remote-chat cleanup are maintained by separate in-memory implementations, with compact and primary behavior differing. Background cleanup is not durable across process restarts, and lifecycle decisions are not always visible in results.

Target design:

- one conversation lifecycle service;
- explicit lifecycle fields in session and result metadata;
- idempotent cleanup operations;
- observable pending/complete/failed cleanup states where cleanup is part of the requested workflow;
- persistence only when there is a concrete product requirement, not as a prerequisite for every change.

### 7. Version information has drifted

The package metadata, server banners, compact-server header, docs, release URLs, and changelog have carried different version lines. This makes bug reports and release verification ambiguous.

Target design:

- package metadata is the source of truth;
- runtime reads version through package metadata or one generated module;
- release tooling validates tags, wheel metadata, documentation examples, and asset names;
- CI fails on known stale version literals outside historical changelog entries.

### 8. Dependency and package data contracts need tightening

The code directly imports packages such as `orjson`, while dependency intent is not always explicit. Runtime code should not depend accidentally on a transitive dependency. The compact prompt defaults are read from a repository-root JSON file that may not be included in a wheel, and the compact server does not have a clear console entrypoint.

Target design:

- every direct runtime import is a direct dependency or an optional import behind an extra;
- reverse-engineered upstream dependencies use a tested compatible range;
- package data lives inside the importable package and is accessed with `importlib.resources`;
- both intended server entrypoints have package-install smoke tests;
- optional system tools such as `ffprobe` are detected and reported as optional capabilities.

### 9. CI must verify the installed product

The workflow separates source checks from installed-product checks. A large unit suite alone does not prove that the wheel contains required files, entrypoints start, profiles register the intended tools, or a real MCP handshake succeeds.

Required CI layers:

- format/lint/type checks;
- unit and behavioral tests;
- full/compact parity and tool-list snapshots;
- wheel/sdist build and clean installation;
- entrypoint import/start/list-tools smoke;
- skill validation and copy parity;
- optional scheduled live compatibility canary using a dedicated account.

### 10. Streaming naming exceeds current client behavior

The current stream tools consume the upstream stream and return after completion. They may still be useful for upstream reliability, but they are not client-visible incremental streams.

Target design:

- either rename/document them as collected stream calls;
- or adopt SDK progress/task primitives that expose meaningful progress to MCP clients;
- handle cumulative-text versus delta-text upstream events without duplication.

### 11. Reverse-engineered contracts need a registry

RPC IDs, source paths, payload shapes, response parsers, and model/media mappings are distributed across constants, transport code, media, research, and management modules.

A registry entry should be able to describe:

```text
capability name
rpc id / endpoint
source path
read or mutation
payload builder
response parser
last verified date
verified dependency version
observed Web build/account notes
verification strategy
stability: stable | preview | experimental | unavailable
```

Tool handlers should ask the registry/service for a capability rather than embed raw payload knowledge.

## Target Runtime Flow

```text
MCP adapter
  -> validate/normalize input schema
  -> application service
  -> capability/model/RPC adapter
  -> Gemini Web client
  -> pure parser
  -> typed domain result + artifacts + evidence
  -> adapter structured output + concise text compatibility
```

## Core Domain Types

### `DomainResult[T]`

Carries success, data, domain error, warnings, and request/verification metadata.

### `Artifact`

Unifies image, video, audio, uploaded/downloaded file, and research-export deliverables.

### `SessionHandle`

Carries opaque local ID, upstream chat ID when available, model options, lifecycle state, and timestamps.

### `CapabilityEvidence`

Carries requested/effective/observed backend, source, verification method, and stability metadata.

### `OperationState`

For long-running work: `accepted`, `queued`, `running`, `completed`, `partial`, `timed_out`, `cancelled`, `failed`, `unavailable`.

## Incremental Migration Pattern

Do not begin with a repository-wide move. For one domain:

1. lock current public behavior with characterization tests;
2. create typed domain models and a service;
3. move transport/parsing logic below the service;
4. route one primary tool through it;
5. route the matching compact facade through it;
6. add parity tests;
7. delete duplicated handlers/helpers;
8. update manifest/docs/evaluations;
9. repeat for the next domain.

This pattern keeps the gateway usable throughout the refactor.
