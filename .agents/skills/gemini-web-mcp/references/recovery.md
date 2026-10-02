# Recovery Playbook

Load this reference only when a normal workflow cannot continue.

## No Gemini MCP Tools Connected

The Skill is instructions, not an MCP server installation. First run the credential-free preflight from a trusted source checkout or reviewed Git revision:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding
```

For Codex, connect the compact server as a small default surface:

```bash
codex mcp add gemini-compact -- uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-skill-server
codex mcp list
```

The compact server covers chat, image/music generation, and account facades. Connect `gemini-mcp-assist` for the five focused assistance tools, including file/URL understanding and Deep Research. Connect `gemini-mcp-server` with `GEMINI_TOOLS=core` when a compatibility file, URL, media, or report-extraction tool is required. Client configuration examples are in the project documentation. Restart or reload the client if newly connected tools are not visible in the current task.

For focused assistance or the primary content surface in Codex:

```bash
codex mcp add gemini-assist -- uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-assist
codex mcp add gemini-core --env GEMINI_TOOLS=core -- uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-server
```

This setup does not authenticate a Gemini account. Keep Cookie values in the client host's secret environment; never put them in command arguments, committed files, or chat. An authentication error after the offline preflight means the connection works but live Gemini access still needs account setup.

## Tool Missing or Wrong Schema

1. Call `gemini_get_tool_manifest` or low-token `account(action="manifest")`.
2. Check the current enabled surface/profile.
3. Switch to the narrow primary profile required by the workflow.
4. Do not enable `all` unless the task is repository maintenance or comprehensive verification.

## Authentication Failure

Return the stable error code and the required next action. Do not ask for Cookie values in ordinary prose or copy them into logs.

If browser Cookie loading succeeds but client initialization reports an expired
`__Secure-1PSIDTS`, refresh the already signed-in Gemini Chrome tab, fetch the
browser Cookies again, and retry once. This recovered the 2026-09-26 live probe.
If it still fails, report the authentication blocker without exposing values.

Authentication recovery is separate from user-facing task design. Resume the original workflow after the runtime is configured.

If an explicit browser profile fails, preserve that failure and refresh the selected profile. Do not substitute another account. A changed authentication context cancels old sessions and their cleanup; retain cancelled resource IDs as diagnostics, and do not interpret cancellation as deletion. Re-select the original account before any explicitly authorized follow-up cleanup.

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
4. if the report is absent, try primary `gemini_create_from_research_report(chat_id=..., artifact_type="webpage")` and verify the returned file;
5. if no chat ID was observed or the extraction fails, report the current recovery limit; the local operation ID has no status/result tool.

## Artifact Missing or Partial

1. inspect structured Artifact state;
2. retain remote URI and operation IDs if present;
3. retry local save without repeating upstream generation when possible;
4. do not claim a file exists until verified;
5. return the next action.

For local `unverified` media, inspect the decoder diagnostic and install/repair the relevant local decoder before acceptance. An existing non-empty file or a mismatched media kind cannot substitute for verification. File/URL input artifacts only identify what was submitted; an empty response remains empty.

For `media_type="video"`, a text-only response can mean the generic MCP call
stayed in chat mode. Inspect the retained chat, then use the native
`https://gemini.google.com/videos` mode if the user authorized browser work.
Do not repeat the generic prompt as if it had entered Omni generation.

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
