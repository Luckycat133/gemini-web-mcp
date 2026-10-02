---
name: gemini-create
description: "Use this skill when the user wants to generate image, video or music artifacts with Gemini, edit a local image, or check, recover or cancel a started Gemini media operation. Prefer this focused skill over gemini-web-mcp for creation. Do not use for image/file understanding, current-web search, critique, Deep Research, Gemini account administration, or repository development; route those to gemini-assist, gemini-account or gemini-web-mcp-development."
license: MIT-0
metadata:
  version: "0.2.2"
  compatibility: "Requires Python 3.11+, the gemini-mcp-create MCP server and authenticated Gemini Web Cookies for live requests. Install with uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-create."
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

Use this Skill to complete the user's task with Gemini media and pass usable artifacts into the requested work. Do not use for repository implementation.

## Choose The Tool

| Intent | Tool | Required evidence |
| --- | --- | --- |
| Generate a new image | `gemini_generate_image` | decoded, verified local image |
| Edit an existing local image | `gemini_edit_image` | distinct input/output identities and verified edited image |
| Generate video | `gemini_generate_video` | start handle, then verified video bytes |
| Generate music | `gemini_generate_music` | start handle, then independently verified audio/video outputs |
| Check a started operation | `gemini_get_operation_status` | observed state for the same operation_id |
| Recover its output | `gemini_get_operation_result` | usable saved artifacts from its existing source |
| Stop an operation | `gemini_cancel_operation` | local_cancelled_before_start or observed provider cancellation; otherwise cancel_requested |

The catalog is exactly these seven tools. The entrypoint is `gemini-mcp-create`; the MCP name is `gemini_create_mcp`. `GEMINI_TOOLS` does not change this catalog.

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-create
```

## Create Once, Then Recover

Images/editing are synchronous. Video/music start asynchronously and return an opaque `operation_id` immediately. Preserve it and poll status/result for the same run, including after server restart. A queued, timed_out, interrupted or cancel_requested request is not a finished artifact. Never repeat generation to recover a timeout. Before each new video/music start, create and preserve an opaque idempotency_key and send it with that start. After a lost response reuse that key to retrieve the registered operation without submitting again. Use a new key for a genuinely new request; never use prompt contents as the key.

Model aliases are `flash-lite`, `flash` (default), `thinking`, `pro`; thinking_level is `standard` or `extended`. A request alias does not prove the observed backend version. Native Web mode selection requests the media tool explicitly; only actual output of the requested kind counts as successful generation.

Default output is `generated_media/` under the server working directory; output_dir and filename can choose another destination. Results preserve each artifact's stable id, kind, URI/local path, dimensions/duration, source identity and verification. The input image is not a generated output. Search/reference images or prose alone do not count as creation.

## Finish The User's Work

1. Use the smallest complete prompt and a local input path for editing.
2. Read direct structured `ok`, `error`, `data` and `meta` before trusting text.
3. For async work, preserve the handle; poll/result instead of starting again.
4. Require the requested kind and `verification.status=verified` on a usable local file. A URI/path alone is insufficient.
5. Use that file in the user's document, website, deck, app or media project. Show the artifact where the client supports preview.
6. Report partial/remote/queued/save-failed outputs honestly; retain their recovery handle.

Successful saved output can remain usable while cleanup is pending or failed. Synchronous creation results include `meta.details.cleanup`; inspect it independently: `state=completed` or `already_completed` records observed absence, while accepted/pending/retained does not. Automatic cleanup owns only this request's new chat after all outputs are saved and verified or a response is definitively empty. Queued, partially saved, uncertain and interrupted requests keep the source. retain_chat/delete_after_seconds explicitly override the default policy.

## Recovery And Boundaries

Operation handles/locators and their retention policies persist for seven days in private authentication-scoped SQLite metadata. Pending cleanup jobs can renew their retention until resolved. The store contains no prompts, chat/report contents, credentials, raw responses or generated bytes. Files remain separate. Recovery reads known source IDs or re-verifies local files and never submits the original prompt again. Expired metadata returns `OPERATION_EXPIRED`; a missing/auth-mismatched handle cannot authorize an account scan.

Cancellation is best effort. `meta.verification_status=local_cancelled_before_start` proves that the local runner stopped before provider submission. For submitted work, only `cancellation_confirmed=true` with provider evidence confirms provider cancellation. `cancel_requested` alone does not mean provider work stopped.

Use `$gemini-assist` for understanding, critique, sourced search and Research; use `$gemini-account` only for explicitly requested account administration. Requests to analyze a screenshot or compose a text music prompt are assistance, not media generation. Repository code/Skill development belongs to `$gemini-web-mcp-development`.

The Skill supplies routing instructions; the MCP server must also be connected. Configure credentials privately using the user's existing authentication setup. Do not request or print Cookie values in conversation or store them in agent memory.

Load [examples.md](references/examples.md) for valid calls and positive/near-miss routing cases.
