# Architecture and Remaining Debt

Load this reference for repository audits, refactors, service extraction, adapter parity, workflow repairs, or release planning.

## Product Boundary

Gemini Web MCP is a local compatibility gateway for agents. Its durable product is the stable MCP/domain contract, not any individual reverse-engineered payload.

```text
MCP clients
  |
  +-- src.server -------- primary granular/profile adapter
  +-- src.skill_server -- compact low-token facade adapter
  +-- src.onboarding ---- installed-product preflight/examples
          |
          v
  adapters -> domain results/artifacts -> shared services
          -> infrastructure RPC/model adapters -> Gemini Web client
```

## Implemented Foundation

### Runtime lifecycle

`ClientManager` coordinates one asynchronous initialization attempt, shields it from unrelated caller cancellation, prevents stale reset generations from publishing, and retires old clients deliberately. `SessionService` owns opaque IDs, per-session send serialization, explicit not-found state, expiry, reset-one, and reset-all.

### Typed contracts

`src/domain/` defines `DomainResult`, stable error codes, warnings, operation state, lifecycle/cleanup metadata, stream collection metadata, and multimodal artifacts. Runtime objects stay outside serialization.

### Shared services

`src/services/` owns chat/session execution, stream normalization, artifacts, history helpers, account inventory, Notebooks, scheduled actions, Gems, cleanup, manifests, and compatibility probing. Primary and compact adapters should differ in granularity/defaults/presentation rather than business semantics.

### Reverse-engineered infrastructure

`src/infrastructure/rpc_contracts.py` and parser modules centralize observed RPC identifiers, payload builders, source paths, parsers, observation metadata, and mutation verification strategies. Changed shapes should become explicit parser state rather than silent emptiness.

### MCP, package, and product verification

`src/adapters/mcp_sdk.py` is the MCP SDK v2 boundary. CI covers modern/legacy negotiation, generated output schemas, representative profiles, wheel/sdist resources, all console entrypoints, isolated onboarding, skill parity/installation, and release assets.

### Compatibility adapter testing

Recent development added broad branch tests for `tools/manage.py`. These tests use `tests._fastmcp_shim` only as a minimal registration/dispatch double because standalone FastMCP is incompatible with the project's MCP SDK v2 dependency line. The shim is not product evidence; real `MCPServer` and stdio smoke remain mandatory.

## Remaining Debt

### 1. Prose-only management surfaces

A significant portion of `tools/manage.py` and `skill_server.py` still returns untyped prose. Failures, pagination, partial results, and mutation verification may therefore require text parsing. Migrate one action family at a time to shared services and `DomainResult`.

### 2. Mutation presentation drift

Services already return read-back evidence, but adapters can still overstate it. Every remote write must map verification state to truthful text and structured state. `accepted` is not equivalent to `verified`; `still_present`, mismatch, read-back failure, or missing mutation ID must not receive a success marker.

### 3. Compatibility monolith size

`tools/manage.py` remains large because it preserves many public tool names and formatting paths. Do not rewrite it wholesale. Extract bounded workflows, route both adapters through the service, add parity tests, then remove obsolete helper aliases.

### 4. Live evidence gap

The canary machinery is extensively fixture/workflow tested, but a deliberate dedicated-account baseline is still required before making current live compatibility claims. Keep live observation separate from offline correctness.

### 5. Release accumulation

Substantial additive and behavioral changes remain under `Unreleased` while the package version is still 0.2.0. The next release needs an explicit semantic-version decision, protocol compatibility statement, migration notes, and downloaded-asset revalidation.

### 6. Workflow expression regressions

GitHub Actions can fail before creating jobs when a context is invalid at a YAML location. Repository contracts should lock every repaired expression, especially job-level `env`, permissions, and conditional contexts.

### 7. Skill and documentation freshness

The development skill is a routing layer, not an independent architecture source. Update both mirrors whenever the implemented baseline, active priorities, maintained gates, or evidence rules materially change.

## Incremental Migration Pattern

For one workflow:

1. characterize current names, schema, text, metadata, and side effects;
2. add a regression for the defect or ambiguity;
3. define typed service input/output;
4. route the primary adapter;
5. route the compact adapter where applicable;
6. compare semantic parity;
7. remove duplicate execution/parsing;
8. update manifest/schema/docs/evaluations;
9. run installed-product and protocol checks when registration changes.

## Target Runtime Flow

```text
MCP adapter
  -> normalize/validate adapter input
  -> shared application service
  -> Gemini Web capability/RPC adapter
  -> pure parser
  -> DomainResult + artifacts + verification evidence
  -> structured MCP output + non-contradictory compatibility text
```
