# Artifact Acceptance and Handoff

Load this reference for image, video, audio, file, webpage, data, or report outputs.

## Principle

A generated result is useful when the calling agent can consume it. Response prose is not an Artifact.

The agent should normally use the Artifact in the user's requested workflow rather than merely report a path.

Examples:

- insert the image into a document or website;
- replace the old asset in an app;
- attach the video to the project;
- use the audio file in the requested edit;
- read a research report and cite it;
- pass a generated file to another tool.

## Structured Fields To Inspect

```text
domain_result.ok, error, meta.operation_state
domain_result.data.state
domain_result.data.artifacts[].id, kind, state, uri, local_path
domain_result.data.artifacts[].mime_type, size_bytes, width, height, duration_seconds
domain_result.data.artifacts[].source_chat_id
domain_result.data.artifacts[].requested_backend, request_model, effective_backend, observed_backend
domain_result.data.artifacts[].verification.status
```

The tools attach `domain_result` to the first content block's `_meta`; MCP clients may also expose it through structured content. Many fields are optional or apply only to certain media. Keep requested, routed/effective, and observed backend evidence separate.

## State Semantics

- `local` — a local file is available.
- `remote` — an upstream URI was observed; access has not necessarily been independently verified, and it is not a local file.
- `queued` — generation has started; no completed Artifact exists yet.
- `partial` is an operation state when some locations failed verification; it is not an Artifact state. Check the result's warnings and individual Artifact states.
- `empty` — no usable Artifact was observed.
- `failed` — the operation failed.

Do not convert `queued`, `partial`, or `empty` into completed success.

## Local File Verification

Before treating a local Artifact as complete:

1. resolve the path;
2. confirm it exists;
3. confirm it is a regular non-empty file;
4. confirm it is inside the requested destination when a destination was specified;
5. inspect MIME/type;
6. inspect dimensions for images;
7. inspect duration when available for audio/video;
8. preserve the structured verification result.

A non-empty file alone is insufficient. Image verification decodes its container and pixels; WAV verification checks the PCM stream; other audio/video verification requires an observed stream from the decoder. Decoder absence leaves `verification.status=unverified` and the operation partial. Corrupt/HTML bytes fail verification. Only output artifacts matching the requested modality count: an image cannot satisfy a video or music request. Input files and URLs cannot make an empty analysis response complete.

For generated images, inspect actual file bytes rather than trusting a requested
suffix. The primary media saver now aligns a mismatched image extension with
the detected format (for example, JPEG bytes requested as `.png` become `.jpg`).
Music audio and cover-video files use separate destination paths, including when a filename already has `.mp3` or `.mp4`.

## Resource Links

When the MCP client supports resource links or embedded resources, prefer returning them alongside structured metadata. The local path remains a practical fallback for local stdio agents.

Do not base workflow completion on whether a particular chat UI renders the preview. The Artifact contract is the source of truth.

## Agent Handoff

### Search and Understanding

These normally return information to the calling agent. Synthesize it and continue the task. A file is optional unless the user asked for a durable deliverable.

### Image, Video, and Music

These normally return files or resource links. Use them in the next step. Do not end with only “saved to …” when the user's request includes a downstream use.

### Deep Research

Prefer a Markdown report Artifact plus structured operation/source metadata. The calling agent should read the report, extract conclusions and sources, and create the user's requested deliverable.

## Failure Handoff

When the Artifact is unavailable, return:

```text
state
error code
retryability
observed upstream identifiers
what evidence exists
the next recovery action
```

Do not invent a local file, MIME type, duration, dimensions, or backend identity.
