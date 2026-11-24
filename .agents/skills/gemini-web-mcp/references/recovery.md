# Recovery Playbook

Load this reference only when a normal workflow cannot continue.

## No Gemini MCP Tools Connected

The Skill is instructions, not an MCP server installation. First run the credential-free preflight from a trusted source checkout or reviewed Git revision:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 gemini-mcp-onboarding
```

Connect `gemini-mcp-function` for Chat, Task, media and Research or `gemini-mcp-account` for existing conversation and resource management. In Codex, run only the command for the surface the task needs.

```bash
codex mcp add gemini-function -- uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 gemini-mcp-function
codex mcp add gemini-account -- uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 gemini-mcp-account
```

Use `codex mcp list` to confirm the entry. To load the prior catalog for an advanced workflow, configure an independent `gemini-mcp-server` entry with `GEMINI_TOOLS=function-full` or `account-full`. Assist, compact and other legacy profiles remain available.

This setup does not authenticate a Gemini account. Keep Cookie values in the client host's secret environment; never put them in command arguments, committed files, or chat. An authentication error after the offline preflight means the connection works but live Gemini access still needs account setup.

## Tool Missing or Wrong Schema

1. Inspect the current connection's tool list and selected profile.
2. Switch to the narrow entry required by the workflow.
3. Use `gemini_get_tool_manifest` or compact `account(action="manifest")` when the connected compatibility surface provides it.

## Authentication Failure

Return the stable error code and the required next action. Do not ask for Cookie values in ordinary prose or copy them into logs.

If browser Cookie loading succeeds but client initialization reports an expired
`__Secure-1PSIDTS`, refresh the already signed-in Gemini Chrome tab, fetch the
browser Cookies again, and retry once. This recovered the 2026-09-26 live probe.
If it still fails, report the authentication blocker without exposing values.

Authentication recovery is separate from user-facing task design. Resume the original workflow after the runtime is configured.

Successful Cookie extraction and SDK initialization do not prove authentication. A fresh unauthenticated user status returns `AUTH_REQUIRED` before dispatch; the runtime can refresh matching browser credentials once at this boundary. It never replays an already submitted generation. Respect a user's decision to proceed without login and report which remote result or cleanup checks remain unavailable.

## Entitlement Unavailable

Classify a missing account entitlement separately from:

- invalid authentication;
- upstream drift;
- implementation failure;
- queued work.

Do not retry a known entitlement failure repeatedly.

## Quick Search Without Sources

If quick search returns an answer but no observed sources:

1. label it `answer_only` or ungrounded;
2. avoid presenting it as verified current-web research;
3. treat links written in answer text as leads and verify them independently before citing;
4. when a broader source investigation is needed, escalate to Deep Research and inspect its report evidence.

## Long Operation Timeout

1. preserve every operation/upstream ID;
2. use the retained upstream chat ID to inspect the chat; history reads can be truncated or show only a completion notice;
3. do not restart automatically;
4. if the report is absent and the request needs a derivative, use `function-full` for `gemini_create_from_research_report(chat_id=..., artifact_type="webpage")` with the exact source chat ID and verify the returned file;
5. use the same operation handle with the relevant status/result action; if no upstream identity was observed, report the recovery limit without starting a duplicate.

For an ordinary active session, use `gemini_ask(session_id=..., recover_only=true)` to read the exact unverified turn. The original process must still hold the session. New messages remain blocked while the prior reply is unverified. In `function-full`, the equivalent call uses `gemini_send_message`.

## Artifact Missing or Partial

1. inspect structured Artifact state;
2. retain remote URI and operation IDs if present;
3. retry local save without repeating upstream generation when possible;
4. do not claim a file exists until verified;
5. return the next action.

Web video/music default to the native browser workflow. A text-only response is
not a verified media Artifact. Keep the original operation and observed CID;
do not replay it through another browser or the SDK after a timeout. Read the
exact original RID/candidate and completion evidence, save and verify local
files, then use the existing protection release and canonical cleanup/read-back.
Unknown reply identity stays protected; guest history gaps are not deletion proof.

Legacy primary/compact native `background=false` also waits through one protected operation. After timeout, preserve the returned operation handle and partial/wait_elapsed state; unknown or save-failed results do not authorize cleanup or a new generation. Verified completion keeps compatible local artifacts, an explicit filename or opaque default, and the `generated_media/` default directory. SDK/API synchronous behavior remains unchanged.

Recheck the captured account and credentials after awaited native read-back, before accepting reply metadata or artifacts. If they changed, refuse new RID/RCID metadata and keep at most the previously observed CID under its original owner; do not release protection or clean based on that response. A native video reference must also remain attached with the same verified preview token before Send; missing or changed evidence stops submission rather than dropping the image.

## Accepted but Unverified Mutation

Use the verification state:

```text
not_observed
read_back_error
mismatch
still_present
incomplete
```

Do not show success. Retry read-back or tell the agent what must be checked.

## Upstream Drift

When a parser, RPC, or capability appears to have changed:

1. capture the stable diagnostic stage and code;
2. do not persist raw private responses;
3. run the bounded compatibility probe;
4. classify the failure as transport, envelope, rejection, parser, or entitlement;
5. hand the evidence to `gemini-web-mcp-development`.
