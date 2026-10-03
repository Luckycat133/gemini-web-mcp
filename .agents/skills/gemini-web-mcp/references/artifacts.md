# Artifact Acceptance and Handoff

Read when consuming generated media, a report or a file-producing result.

## Result Envelopes

Normalize the result before inspecting domain fields:

| Surface | Authoritative domain envelope |
| --- | --- |
| Focused create/account | top-level MCP `structuredContent`: `ok`, `data`, `error`, `warnings`, `meta` |
| Focused assist, primary and compact content-block tools | first block's `content[0]._meta.domain_result`; its structured representation may be `structuredContent.result[0]._meta.domain_result` |

Focused create/account also preserve compatibility block metadata. Follow the
registered `outputSchema`; a transport/container wrapper is not itself the
domain result. Some legacy tools return only text: do not invent structured
verification when it is absent.

From the envelope, inspect:

```text
ok, error, warnings, meta.operation_state, meta.verification_status
data.state, data.artifacts[], data.input_artifacts[]
artifacts[].id, kind, state, uri, local_path, verification
artifacts[].mime_type, size_bytes, width, height, duration_seconds
artifacts[].source_chat_id, requested_backend, request_model, effective_backend, observed_backend
```

Fields are optional by result type. Keep requested/routed backend evidence
separate from the backend actually observed. Input artifacts identify submitted
material; they cannot satisfy a generated-output requirement.

## Readiness and Verification

| State | Meaning |
| --- | --- |
| `local` | a local file location was observed; inspect verification and current availability |
| `remote` | a URI was observed; this does not establish local availability or access |
| `queued` | processing remains incomplete, even if a separate local artifact is already usable |
| `empty` / `failed` | no usable output or a failed artifact-producing request |

`partial` is an operation state, not an Artifact state. Inspect individual
outputs and warnings; one saved file does not establish that all requested
outputs completed. A path or URI alone is insufficient.

Accept a matching output with `verification.status=verified` and an available
regular, non-empty file at handoff. Check destination containment when the user
specified a destination. Verification decodes image pixels/WAV content or
observes an audio/video stream; missing decoders leave `unverified`, and corrupt
or HTML bytes fail. Repair saving or decoding without repeating generation.
The filename extension may change to match detected image bytes. Music audio
and companion video have distinct paths, including when a suffix was requested.

`verified` reports file integrity. Review the media itself against the requested
content before accepting or publishing it.

Focused/primary media supports `output_dir` and `filename`; compact media uses
`generated_media/` under the server's working directory. Recovered Research
reports use `generated_reports/`. Paths belong to the MCP server's filesystem:
ensure the consuming tool can access the returned location.

## Handoff

Search and understanding normally return information; synthesize it into the
requested work. When the task includes a destination, pass verified media to
that document, app, deck or edit. For a standalone media request, deliver the
artifact with a preview when supported. Read a recovered report before using or
citing it. UI preview availability does not determine artifact readiness.

Preserve remote/source locators and operation IDs when saving or verification
fails. Artifact readiness is independent of source-chat cleanup; inspect
`meta.details.cleanup` separately. See [operations.md](operations.md) for the
cleanup and recovery contract.
