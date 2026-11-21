# Implemented Dual-Surface Contracts

Reviewed source: `5c9f53087398f614958acf06d3c9d14f54958c52` (2026-11-14 review).
Re-read source after rebasing; this document separates implemented behavior from
future targets in the generation/management backlog.

## Entrypoints and compatibility

| Entrypoint | Source | Current boundary |
| --- | --- | --- |
| `gemini-mcp-function` | `src/surfaces/function.py` | Function catalog |
| `gemini-mcp-account` | `src/surfaces/account.py` | Explicit Account catalog |
| `gemini-mcp-server` | `src/server.py` | Function when `GEMINI_TOOLS` is unset; explicit profiles retain branch behavior |
| `gemini-mcp-assist` | `src/surfaces/assist.py` | Five-tool assist compatibility surface |
| `gemini-mcp-skill-server` | `src/skill_server.py` | Branch compact compatibility surface |
| `gemini-mcp-onboarding` | `src/onboarding.py` | Installation/protocol checks, not another business server |

There is no current `gemini-mcp-create` console entrypoint. The released 0.2.2
Account command has the same name but different request facades and results.
Branch compatibility is NOT full released-client compatibility.

### Function catalog

`gemini_ask`, `gemini_understand_image`, `gemini_understand`, `gemini_research`,
`gemini_start_chat`, `gemini_send_message`, `gemini_list_sessions`,
`gemini_reset_session`, `gemini_generate_image`, `gemini_edit_image`,
`gemini_generate_video`, `gemini_generate_music`, `gemini_run_task`,
`gemini_get_operation_status`, `gemini_get_operation_result`,
`gemini_list_research_report_actions`, `gemini_create_from_research_report`.

### Account catalog

`gemini_history`, `gemini_manage_chat`, `gemini_delete_chat`, `gemini_notebooks`,
`gemini_manage_notebook`, `gemini_move_chat_to_notebook`, `gemini_tasks`,
`gemini_task_browser`, `gemini_skills`, `gemini_account_inventory`,
`gemini_list_scheduled_actions`, `gemini_get_scheduled_action`,
`gemini_create_scheduled_action`, `gemini_delete_scheduled_action`,
`gemini_manage_gems`, `gemini_cleanup`, `gemini_preferences`.

Membership comes from `src/surfaces/catalogs.py`; schemas and side effects come
from actual registration and services. Do not add a tool just to match this prose.

## Shared ownership

| Concern | Current owner |
| --- | --- |
| SDK/protocol import and content-wrapper compaction | `src/adapters/mcp_sdk.py` |
| Domain state and artifacts | `src/domain/` |
| Business registration and presentation | `src/surfaces/`, reusable registrars in `src/tools/` |
| Detached starts | `src/services/background.py`, `src/operation_worker.py` |
| Status/result and recoverable media/report/Task work | `src/services/operations.py`, `media_jobs.py`, `task_jobs.py`, `research.py` |
| Verified saved results | `src/services/media_storage.py`, `research_result_storage.py`, `artifacts.py` |
| Persistence and account identity | `src/infrastructure/operation_store.py`, `cleanup_store.py`, `account_identity.py` |
| Session/authentication/cleanup lifetime | shared client, session and remote-cleanup managers |
| Provider contracts and parsers | `src/infrastructure/` |

Account currently reuses registrars from `src/tools/manage.py`. That is adapter
composition, not a reason to duplicate services. Services must not import MCP
servers or depend on a registration module. A future registrar extraction must
preserve behavior and is not a prerequisite for delivering user capabilities.

## MCP v2 wire boundary

The adapter subclasses the official SDK; it is not a new protocol stack. It
defaults `structured_output=True` and conservatively compacts only a recognized
SDK TextContent/ImageContent wrapper, removing descriptive labels. Business
schemas and validation constraints remain intact; unknown shapes are untouched.

For domain-backed content-block tools, the current structured wire shape is:

```text
structuredContent.result[i]._meta.domain_result
    ok / data / error / warnings / meta
```

Do not assume every non-domain helper contains that field. The smoke tests
intentionally accept some domain rejections inside `isError=false`,
`resultType=complete` responses. This is the existing application contract, not
an assertion that MCP recommends treating all tool failures as success. Any
migration to `isError=true` or a direct domain output schema is separately
reviewed, versioned and tested for both protocol eras.

## Execution and recovery matrix

| Workflow | Start | Recovery / limitation |
| --- | --- | --- |
| Ask without Notebook IDs | Direct Chat | No durable ordinary Ask job implied |
| Ask with Notebook IDs | Background ordinary Chat, `execution_mode=chat` | Persistent handle; not a native Task |
| `gemini_run_task` | Background native Task, `execution_mode=task` | Exact goal AND completed original answer; attachment completeness |
| Function image/edit/video/music | Background handle | Resume observation/materialization, never resubmit generation |
| Research | Background by default | Exact original report; verified saved artifact before cleanup |
| Ordinary session send | Process-local session | `recover_only=true` reads the uncertain original turn in the original process |

Defaults, bounds and model support must be read from the request schema and
current account policy. Complex prompts, app requests and Notebook context do
not automatically select Task. Assist retains `gemini_search` and does not gain
Function-only Notebook arguments.

`get_operation_status` reads recorded/observed metadata; it may validate local
files, read upstream progress or update local metadata. It must not generate,
fetch arbitrary account history or turn into result materialization. `result`
can resume a recovery observer, fetch/save/verify files and apply stored cleanup;
it is declared destructive. Local-only status is not promised.

## Persistence, ownership and idempotency

- `GEMINI_OPERATION_DB_PATH` selects `OperationStore`; reviewed schema version 6
  includes operation identity, artifact hashes/locators and Task guards/input
  metadata. `GEMINI_CLEANUP_DB_PATH` selects the separate cleanup ledger.
  Task browser action deduplication has its own owner; do not conflate these stores.
- `GEMINI_STATE_DB_PATH` belongs to the different released architecture. Renaming
  an environment variable does not migrate data or preserve old handles.
- Detached workers receive ephemeral request data through anonymous stdin, not
  a durable prompt spool. Claims/heartbeats fence workers. A server disconnect is
  not a worker crash; a recorded handle is not proof the provider accepted work.
- Persist observed CID/RID/candidate/provider IDs before relying on them. A crash
  before any provider locator may be unrecoverable without a new explicitly
  authorized request. Unknown submission is never automatically retried.
- The current retry handle is `operation_id`, not the released `idempotency_key`
  API. Existing-handle calls return existing work after account/kind checks;
  payload equivalence is not established. New intent needs a new handle.
- Keep seven-day retention, with protected operations retaining recoverability
  under implemented locator/protection rules. Do not promise unconditional expiry
  or global unbounded operation listing. Account mismatch/unbound fails closed.
- Credentials, prompts, raw responses and report/answer bytes are excluded from
  ledgers. Verified result files are deliberately persistent outside them.
  New content indexes/backups require separate opt-in storage and retention.

## Exact completion and cleanup

Task/Notebook workers save a full verified answer, not just a preview. Task also
records the complete recognized native attachment inventory; an unknown shape
is not an empty inventory. Missing downloads keep protection. Restarted reads
recheck files; a deleted/corrupted artifact does not justify provider replay.

Uncertain session turns block new sends and use the exact turn's recovery path,
not the SDK latest-reply fallback. Protect active/in-flight sessions from stale
timers. Cleanup is account-scoped and limited to the MCP-owned ledger; it does
not sweep titles or delete Notebook objects/sources. Scheduled Task relationships
also protect their threads. Retention and artifact success are distinct states.

## Models, transport and account mutations

Fresh user-status evidence is required before Web dispatch; Cookie extraction
or SDK initialization alone is insufficient. Preserve same-account fencing
across permitted pre-dispatch refresh; no hidden generation replay.

Low/medium/high and compatibility aliases must keep body/header selection
consistent. The current Lite policy requires recent same-account basic Flash
quota evidence and locked thinking; it is not a fallback for every failure.
Do not exhaust real quota to test this policy. Keep explicit Pro/Argon intent.

Default video is Web. Explicit `gemini-omni-1.2-flash` selects the paid API using
`GEMINI_API_KEY`; only that current route accepts `duration_seconds` 3..15.
Do not reinterpret those API options as Web controls or silently switch providers.

Account reads minimize body access. Exact chat mutations use pre-read and
independent read-back; sent-but-unverified updates keep `changed=null`.
Notebook source-picker and `source=gemini` project views are resolved by observed
ID/type, never prefix conversion. Notebook deletion can delete its conversations.
Task schedules differ from Chat scheduled actions. Preference read-back proves
stored values, not runtime effects. Task browser inputs require original reply
IDs, frame hash and unique action ID; no input replay or automatic permission
approval. Connection setup/OAuth, confirmations and browser input are different.

## Explicit non-claims

No public cancel, Task pause/resume, exclusive browser handoff or Task export is
promised. ProcessSession probes are experimental. Report `audio_overview` and
`custom_app` outputs are scripts/specifications, not native audio or deployment.
Canvas/Library/sharing/ZIP and other released workflows need action-level migration
accounting. Neither retained RPC fixtures nor green new-surface tests prove parity.
