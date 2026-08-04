# Agent-First Tool and Result Design

Load this reference when adding or changing tools, facades, schemas, models, media, files, Deep Research, or remote mutations.

## Design Goal

A tool exists to help an agent complete a workflow. It should make the intended action discoverable, constrain inputs, bound output, and return enough evidence for the agent to know what actually happened.

## Tool Levels

Use three layers deliberately:

1. composable primitive tools for stable operations;
2. compact facade/workflow tools where a bounded action enum saves meaningful context;
3. diagnostic tools for manifests, capabilities, installation, compatibility, and drift.

Compactness must not merge unrelated semantics or force the agent to guess the result.

## Input Rules

- Use enums/Literals for actions, models, media types, formats, and stability values.
- Bound limits, offsets, scan depth, characters, and timeouts.
- Normalize aliases once and preserve requested versus normalized values.
- Strip identifiers and names where surrounding whitespace has no semantic value.
- Reject whitespace-only required values before client initialization.
- Distinguish omitted values from explicit empty values for partial updates.
- Accept continuation identifiers for resumable operations.

## Result Contract

Prefer a typed result plus concise compatibility text:

```json
{
  "ok": true,
  "data": {},
  "warnings": [],
  "error": null,
  "meta": {
    "request_id": "req_...",
    "operation_state": "completed",
    "requested_backend": "pro",
    "effective_backend": "Lyria 3 Pro",
    "verification_status": "artifact_saved_and_verified"
  }
}
```

The first text block and structured result must agree. An agent must not need emoji matching or exception-string parsing to determine success.

## Error Taxonomy

Use stable codes such as `INVALID_ARGUMENT`, `AUTH_REQUIRED`, `AUTH_EXPIRED`, `SESSION_NOT_FOUND`, `CAPABILITY_UNAVAILABLE`, `UPSTREAM_REJECTED`, `UPSTREAM_CHANGED`, `NETWORK_ERROR`, `RATE_LIMITED`, `TIMED_OUT`, `CANCELLED`, `ARTIFACT_NOT_RETURNED`, `ARTIFACT_SAVE_FAILED`, `VERIFICATION_FAILED`, and `INTERNAL_ERROR`.

Include retryability, a public-safe message, optional suggested action, and a diagnostic ID for logged raw evidence.

## Mutation Evidence

A mutation has at least two stages:

```text
request accepted -> target state observed
```

Only the second stage proves success.

### Positive terminal evidence

Examples:

- created object is visible or readable by returned ID;
- updated fields match the requested values on read-back;
- deleted object is explicitly in a deleted state or is absent from an authoritative non-empty registry and unreadable by ID.

These may receive a success marker and completed state.

### Ambiguous or contradictory evidence

The following must not be presented as success:

- `missing_mutation_id`;
- `read_back_not_observed`;
- `read_back_error`;
- `read_back_mismatch`;
- `still_present`;
- empty-registry-only absence without corroboration;
- RPC acceptance with no authoritative state observation.

Return warning/partial/failed state as appropriate, preserve the verification code, and tell the agent which read/list operation can resolve the uncertainty.

Test each mutation with verified, ambiguous, and contradictory read-back states.

## Collection Rendering

Services may return mapping-backed or object-backed entries. Presentation must use shared field adapters instead of assuming attributes. Preserve stable IDs, pagination, source diagnostics, and truncation metadata.

## Artifact Model

Use one representation across image, video, audio, file, report, webpage, and data artifacts:

```text
id, kind, state, title, uri, local_path, mime_type, size_bytes,
width, height, duration_seconds, source_chat_id,
requested_backend, request_model, effective_backend, observed_backend,
verification {status, methods}
```

A local deliverable is successful only after relevant file and metadata checks. A remote URI can be useful but remains separately identified and normally unverified.

## Requested, Effective, and Observed Backends

Keep separate:

- requested alias selected by the caller;
- request model sent upstream;
- effective backend inferred from routing;
- observed backend parsed from response/artifact evidence.

Never repeat an expected label as observed fact.

## Long Operations

- use modality-specific timeouts;
- return queued/running/completed/timed-out explicitly;
- retain upstream continuation IDs;
- propagate cancellation;
- describe current streams as collected unless MCP clients receive real incremental progress.

## Primary and Compact Parity

Both adapters should call the same service and compare:

- error code and retryability;
- operation state;
- lifecycle/cleanup metadata;
- normalized model/backend evidence;
- artifact identity and verification;
- pagination/truncation;
- mutation verification status.

Text verbosity may differ; semantic state may not.

## Compatibility Rules

- Keep established public names within a major version unless intentionally breaking.
- Add optional fields rather than silently changing existing meanings.
- Fix accidental behavior with a release note and regression.
- Update manifest, schema snapshots, docs, examples, evaluations, and skills together when the public contract changes.
