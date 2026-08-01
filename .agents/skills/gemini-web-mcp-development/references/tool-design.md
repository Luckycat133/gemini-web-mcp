# Agent-First MCP and Multimodal Tool Design

Load this reference when adding or changing tools, models, media, files, Deep Research, output formats, profiles, or compact facades.

## Design Goal

The tool surface exists for agents, not as a one-to-one copy of Gemini Web internals. A strong tool makes the intended workflow obvious, returns bounded machine-readable results, and gives enough evidence for the agent to know what actually happened.

## Choose the Right Tool Level

Use three layers deliberately:

1. **Primitive tools** for composable operations with stable contracts, such as start chat, send message, list chats, read one chat, or generate media.
2. **Facade/workflow tools** for compact clients, such as `history(action=...)` or account inventory, when one bounded action enum saves substantial context.
3. **Diagnostic tools** for manifests, capability probes, package/connection checks, and compatibility evidence.

Do not put unrelated safety, authentication, read, mutation, and artifact workflows into one action enum merely to reduce tool count. Compactness is valuable only when the agent can still predict the result.

## Input Schema Rules

- Use explicit enums/Literals for models, media types, actions, response formats, and stability modes.
- Bound collection sizes, offsets, timeouts, output characters, and scan depth.
- Use one name for one concept across all tools (`thinking_level`, `learning_mode`, `output_dir`, `retain_chat`, and so on).
- Normalize aliases in one shared function, then carry both the requested alias and normalized value in metadata.
- Validate requirements before initializing the network client when possible.
- Keep optional arguments truly optional; distinguish omitted values from empty strings where partial updates are supported.
- For long-running operations, accept a timeout and return a terminal state rather than hanging indefinitely.

## Structured Result Contract

Prefer a typed result with a concise compatibility text. A representative JSON shape is:

```json
{
  "ok": true,
  "data": {},
  "artifacts": [],
  "warnings": [],
  "error": null,
  "meta": {
    "request_id": "...",
    "operation_state": "completed",
    "requested_model": "pro",
    "request_model": "gemini-3-pro",
    "effective_backend": "Lyria 3 Pro",
    "observed_backend": "fullsong",
    "verification_status": "artifact_saved_and_duration_probed"
  }
}
```

Do not make the agent parse an emoji, log sentence, or human-oriented paragraph to determine success.

## Domain Error Taxonomy

Use stable codes such as:

- `INVALID_ARGUMENT`
- `AUTH_REQUIRED`
- `AUTH_EXPIRED`
- `SESSION_NOT_FOUND`
- `CAPABILITY_UNAVAILABLE`
- `UPSTREAM_REJECTED`
- `UPSTREAM_CHANGED`
- `RATE_LIMITED`
- `TIMED_OUT`
- `CANCELLED`
- `ARTIFACT_NOT_RETURNED`
- `ARTIFACT_SAVE_FAILED`
- `VERIFICATION_FAILED`
- `INTERNAL_ERROR`

Include `retryable`, a concise public message, and an optional suggested next action. Keep raw exception details in logs/diagnostics rather than as the only public result.

## Artifact Model

Use one artifact representation across modalities:

```json
{
  "id": "artifact_...",
  "kind": "image|video|audio|file|report|webpage|data",
  "title": "...",
  "uri": "https://...",
  "local_path": "generated_media/...",
  "mime_type": "audio/mpeg",
  "size_bytes": 123456,
  "width": null,
  "height": null,
  "duration_seconds": 184.2,
  "source_chat_id": "c_...",
  "requested_backend": "Lyria 3 Pro",
  "effective_backend": "Lyria 3 Pro",
  "observed_backend": "fullsong",
  "verification": {
    "status": "verified",
    "methods": ["download_succeeded", "ffprobe_duration"]
  }
}
```

Populate only known fields. Preserve remote URI and local path independently. An agent may need either one.

## Multimodal Success Semantics

### Images

A completed generation should return at least one image URI or verified file. Include dimensions when easily available. Image editing must identify the input artifact and the output artifact separately.

### Video

Return queued/running/completed explicitly. A text acknowledgement is not a completed video. Verify the saved file or remote media URL and duration where possible.

### Music/audio

Separate the requested model alias from the effective music backend. Verify actual media cards/raw markers and saved duration before claiming clip/full-song behavior.

### Files and URLs

Return the analyzed source identity plus the model result. Preserve attachment metadata useful to reproduce the request. Keep provider-specific upload handling below the service boundary.

### Deep Research

Model it as phases:

```text
plan -> accepted/start -> poll/progress -> report -> sources -> artifacts
```

Return plan/research/chat identifiers needed for continuation. Distinguish a generated plan from a completed research report.

## Requested, Effective, and Observed Backends

Always keep these separate:

- **requested:** what the caller selected;
- **request model:** what was sent to `gemini-webapi` or a private RPC;
- **effective:** the backend inferred from the current routing contract;
- **observed:** evidence parsed from the response or saved artifact.

When observation is absent, say `unverified` rather than repeating the expected label as fact.

## Capability Registry Behavior

A capability lookup should return:

```text
available | unavailable | unknown
stability level
required account capability
transport adapter
last verification evidence
fallback, if any
```

Unavailable features should produce `CAPABILITY_UNAVAILABLE`, not a generic attribute error or empty text.

## Mutation Verification

When the upstream mutation response is ambiguous:

1. parse the response identifier;
2. read back by ID when supported;
3. inspect the relevant registry/list;
4. return the verification method and outcome;
5. report `partial` or `verification_failed` rather than unconditional success.

This applies to scheduled actions, notebook moves, Gems, and future account mutations.

## Long-Running Operations

- Use modality-specific default timeouts.
- Preserve cancellation cleanly.
- Return operation state and upstream identifiers on timeout when the operation may still be running.
- Prefer SDK task/progress features during MCP v2 migration.
- Do not call a result streamed if the MCP client receives it only after collection; use accurate naming and docs.

## Pagination and Bounded Output

Every collection/list/search tool should have bounded `limit`, `offset` or cursor, `has_more`, and `next_offset`/cursor. Text scanning should separately bound chats, turns per chat, and characters per turn.

A compact text rendering may truncate, but structured output should disclose truncation and original counts when known.

## Primary and Compact Parity

Both surfaces should call the same service and differ only in:

- exposed tool granularity;
- argument presets/defaults;
- response verbosity;
- selected structured fields.

Add parity tests for result codes, operation state, artifact identity, model/backend evidence, and session semantics.

## Compatibility Rules

- Keep established tool names and core argument names stable within a major version.
- Add optional fields rather than changing meanings silently.
- When fixing accidental behavior, add a release note and a regression test.
- Maintain aliases at the normalization boundary, not throughout domain code.
- Update manifest, docs, examples, evaluations, and skill references in the same change.

## Agent Evaluation Questions

Evaluation tasks should exercise complete workflows, not only tool availability. Good examples include:

- generate an image, save it, and report the verified artifact path and dimensions;
- create Pro extended music and distinguish expected routing from observed saved duration;
- run Deep Research and prove that the output is a completed report rather than a plan;
- start a session, send two turns, list it, reset only that session, and verify another session remains;
- create a scheduled action, verify it by ID or registry, then delete and verify terminal state.
