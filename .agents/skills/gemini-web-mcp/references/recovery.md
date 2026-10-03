# Recovery Playbook

Read when the selected workflow cannot continue. Diagnose the returned code and
state before choosing a recovery action.

## Missing Tools or Changed Schema

A Skill does not connect an MCP server. Check the host's connected catalog and
schema first. A compatible available tool may already complete the task;
focused tools are not a reason to replace a working connection.

For connection setup, the credential-free preflight is:

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-onboarding
```

Use the intended source checkout or reviewed revision for reproducible setup.
Connect the needed lane (`gemini-mcp-assist`, `gemini-mcp-create`,
`gemini-mcp-account`), compact `gemini-mcp-skill-server`, or primary
`gemini-mcp-server` with the appropriate profile. For example, in Codex:

```bash
codex mcp add gemini-assist -- uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-assist
codex mcp list
```

Reload the client if newly connected tools are not visible. This setup does not
authenticate Gemini. Use the existing private credential setup; keep Cookie
values out of conversation and command arguments. On compatibility surfaces,
use the manifest when checking a missing tool/profile/schema. `GEMINI_TOOLS`
changes registration only when the primary server starts.

## Authentication or Capability Failure

| Observation | Recovery |
| --- | --- |
| Missing/expired authentication | restore the existing private account setup; for an already authorized browser workflow, refresh the selected signed-in Gemini tab and reload its Cookies once |
| `BROWSER_COOKIE_ACCESS_DENIED` | resolve the MCP host's browser-data permission in system privacy settings; do not confuse this with missing login |
| `BROWSER_COOKIE_ACCESS_TIMEOUT` | preserve the bounded authorization failure and retry browser loading after the system prompt is resolved |
| Explicit profile unreadable/signed out | preserve that profile failure; do not switch to another account |
| `CAPABILITY_UNAVAILABLE` | inspect the diagnostic: SDK capability, model availability and account entitlement are distinct causes; stop repeating a known entitlement failure |

A changed authentication context cancels old sessions/cleanup. Cancellation does
not mean deletion; reselect the original account before any authorized cleanup
of its known resources. See [tool_surface.md](tool_surface.md) for these controls.

## Missing Evidence or Interrupted Work

| Result | Next action |
| --- | --- |
| Search answer without observed sources | retain answer_only; independently verify suggested links, or use Research when a broader investigation is needed |
| Research/video/music timeout | preserve operation/upstream IDs; use status/result for the same run; after start response loss, reuse its preserved idempotency_key |
| No source ID or expired/auth-mismatched handle | report the recovery limit; missing ownership evidence does not authorize unrelated history scans or another generation |
| Remote-only/partial/save-failed artifact | recover or save from its known source, then inspect matching file verification; do not regenerate to repair saving |
| Local unverified media | repair the indicated decoder or verification failure; a non-empty file is insufficient |
| Accepted but unverified account mutation | retry read-back when supported and preserve incomplete/mismatch/still_present/read_back_error states until positive verification |

Compatibility history may truncate Research turns or show only a completion
notice. For a known report chat, primary
`gemini_create_from_research_report(chat_id=..., artifact_type="webpage")` may
recover report content; verify its result. Text-only video or search-reference
images remain failed creation. A generated file can be usable while cleanup is
pending; handle the cleanup observation separately. Detailed continuation and
cancellation rules are in [operations.md](operations.md), and result envelopes
and file acceptance are in [artifacts.md](artifacts.md).

## Upstream Drift

Preserve the safe error code, diagnostic stage and observed state; distinguish
transport/envelope/rejection/parser failures from unavailable entitlement.
Pass repository defects to `gemini-web-mcp-development`. A compatibility canary
requires its explicit opt-ins and dedicated test environment; an ordinary tool
failure does not authorize a live account audit. Keep raw private responses and
credentials out of diagnostics.
