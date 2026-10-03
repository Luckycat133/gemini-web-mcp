---
name: gemini-create
description: "Generate or edit images, create video or music with Gemini, and recover or cancel existing media operations. Image/file analysis belongs to gemini-assist."
license: MIT-0
metadata:
  compatibility: "Requires a connected gemini-mcp-create server (Python 3.11+); live calls use the server's private Gemini Web authentication."
  version: "0.2.2"
  openclaw:
    emoji: "♊️"
    homepage: https://github.com/Luckycat133/gemini-web-mcp
    requires:
      bins:
        - uvx
    primaryEnv: GEMINI_PSID
    envVars:
      - name: GEMINI_PSID
        required: false
        description: Optional authenticated Gemini Web Cookie, configured privately.
      - name: GEMINI_PSIDTS
        required: false
        description: Optional matching session Cookie recommended for stability.
      - name: GEMINI_PSIDCC
        required: false
        description: Optional matching Cookie forwarded when configured.
      - name: GEMINI_PROXY
        required: false
        description: Optional HTTP or HTTPS proxy.
      - name: GEMINI_STATE_DB_PATH
        required: false
        description: Optional private SQLite metadata location for restart recovery.
---

# Gemini Create

Use the connected `gemini-mcp-create` server (`gemini_create_mcp`) to obtain
media for the user's requested destination.

| Task | Tool |
| --- | --- |
| New image | `gemini_generate_image` |
| Edit a local image | `gemini_edit_image` |
| Video | `gemini_generate_video` |
| Music | `gemini_generate_music` |
| Observe a run | `gemini_get_operation_status` |
| Retrieve its output | `gemini_get_operation_result` |
| Request cancellation | `gemini_cancel_operation` |

Supply the prompt and any input path. Choose `output_dir` or `filename` when
the destination matters; the default is `generated_media/` under the server's
working directory. Use returned paths rather than reconstructing filenames.

Read structured `ok`, `data`, `error` and `meta`. Completion requires the
requested artifact kind, a usable local file and verified output. Prose,
reference images and the editing input do not substitute for generated media.
Pass the artifact into the user's project and preview it when supported.

## Continue an operation

Image generation/editing is synchronous; video/music normally returns an
opaque `operation_id` immediately. Preserve it and use status/result after
interruption or restart. Supply an opaque `idempotency_key` when response-loss
recovery matters; reusing that key retrieves the same start. A new request uses
a new key. Queued, partial, remote-only and timed-out results retain recovery
information and are not complete local outputs.

Cancellation is best effort until confirmed. `cancel_requested` does not
prove provider work stopped; `local_cancelled_before_start` confirms only that
the local runner stopped before submission. Saved output can remain usable
while source cleanup is pending or failed. Read cleanup independently and use
`retain_chat`/`delete_after_seconds` when the task needs a different policy.

## Connection and details

If setup is needed, the source entrypoint is:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-create
```

Use an installed version or checkout that provides this entrypoint and the
server's private authentication setup. Load [examples.md](references/examples.md)
for call examples, recovery, cancellation and source-retention details.
For understanding or research use `$gemini-assist`; requested account
management uses `$gemini-account`; repository work uses `$gemini-web-mcp-development`.
