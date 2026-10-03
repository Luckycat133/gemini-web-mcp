# Tool Surfaces and Account Controls

Read for compatibility tool selection, account work or authentication/reset
diagnostics. The connected catalog/schema is authoritative. Primary
`gemini_get_tool_manifest` and compact `account(action="manifest")` aid
discovery; they are not prerequisites for a known workflow.

## Low-token skill server facade (`src.skill_server`)

The compact server exposes these eleven tools. Its mixed-action facades have
conservative tool annotations; determine the requested action's effect from
the schema, rather than treating every call as destructive.

| Tool | Actions or input | Effect |
| --- | --- | --- |
| `chat` | message; optional image_path/session_id | sends the prompt and optional image to Gemini |
| `account` | status, models, manifest, capabilities, features, links, usage, library, notebooks, scheduled, modes | manifest/capabilities are auth-free; remaining actions inspect account data |
| `history` | list, search, read, export, delete | metadata reads; read/export or search with scan_turns=true reads private text; delete targets chat_id |
| `scheduled` | list, get, create, delete | create is daily with title/instructions/hour/timezone; get/delete require action_id |
| `create` | prompt; type=image/video/music; optional image_path | native media request, local saving/verification and owned-source cleanup |
| `edit` | image_path, prompt | edits a local image; input and generated output remain distinct |
| `session` | create, send, list, reset, reset_one, reset_all | reset/reset_one require session_id; only reset_all resets all sessions |
| `prompts` | list, get, create, delete | local library; get/delete identify by name; no Gemini generation |
| `cookie` | status, profiles, get | status/profiles show availability metadata; get caches browser authentication material |
| `doctor` | browser; validate_browser=false by default | local diagnostics, optionally validating the selected browser |
| `cleanup` | markers, target, dry_run=true, max_chats, scan_turns=false | bounded marker preview; dry_run=false requests deletion |

## Tool group selection (`GEMINI_TOOLS`)

Primary profiles select registration at process startup. Choose an appropriate
profile when configuring a server; an already connected broader profile does
not expand task authorization.

| Profile | Included capability |
| --- | --- |
| `model` / `chat` | text/image chat and sessions |
| `core` (default) | chat, media, files/URLs and Research |
| `history` | manage:history-read |
| `history-organize` | history-read plus notebooks-read/write |
| `account-read` | manage:account-read |
| `scheduled-admin` | scheduled-read/write |
| `manage:gems` | Gem list/create/update/delete |
| `history,manage:history-write` | history identification plus targeted deletion/test cleanup |
| `prompts` | local Prompt library |
| `all` | broad content and account tools; use when the authorized task needs that coverage |

Primary `_stream` tools collect the upstream stream into one MCP result.

Focused assist/create/account catalogs contain 5/7/7 tools independently of
`GEMINI_TOOLS`. Focused account facades take typed `request` objects and expose
`meta.actionSemantics` for read/mutation/local scope. Primary and compact
arguments differ: primary `gemini_history`/`gemini_notebooks` are read-only;
deletion/move use `gemini_delete_chat`/`gemini_move_chat_to_notebook`.

## Account Scope and Read-back

Private history reads and account deletion require the user's account-task
intent; existing authorization persists. Resolve targets from known IDs or
necessary authorized metadata reads. `scan_turns=true` adds private content
reads. Bounded pages, title searches and `read_chat(None)` cannot prove absence.
Content search may report `read_failures` and incomplete coverage. A partial
page with `next_offset=null` has no continuation cursor; primary history scans
offer larger bounded source coverage when needed.

Chat deletion succeeds only with `verified_absent` from complete fresh metadata
read-back. `not_available` is accepted/unverified; `still_present` and
`read_back_error` are verification failures. Notebook moves, Scheduled changes
and Gem changes likewise need positive target-state read-back. Feature probes
show RPC reachability, not account entitlement; observed models/usage do not
establish an exact backend version or inferred quota.

Registered focused cleanup status/run/cancel concerns only jobs in the current
authentication scope; run without job_id processes due registered jobs.
`test_artifacts` and compact `cleanup` are separate bounded marker scans.
Preview the authorized marker set with dry_run=true; a default marker is not
ownership evidence. Saved Prompt libraries are local and use atomic storage;
repair unreadable/corrupt data or save failures before claiming a mutation.

## Authentication and Reset

Browser Cookie export (`gemini_get_cookie_from_browser` or compact
`cookie(action="get")`) needs authorization to access the signed-in browser
account for this task; prior authorization still applies. It creates sensitive
account-authentication material in a local cache. Restrict file access and keep
values out of conversation, command arguments, logs, backups and agent memory.
Profile tools expose availability, never Cookie values. The macOS credential
prompt unlocks the browser Cookie store; it does not authorize arbitrary
credential-file inspection. Access waits are bounded and may return
`BROWSER_COOKIE_ACCESS_TIMEOUT`.

An explicit unreadable/signed-out profile fails closed; do not substitute an
account. Authentication changes retire old sessions and cancel their cleanup
with `authentication_context_changed`, so old deletions never run under a new
account. Same-material refresh preserves the context. Cancelled cleanup is not
verified deletion.

Primary `gemini_reset_session` or compact reset/reset_one resets one session.
Primary `gemini_reset` or compact reset_all resets the client/all sessions and
can delete non-retained source chats. Reset affects MCP/Gemini state, never
agent memory or agent instructions; local reset success may coexist with partial
remote cleanup, so inspect its warnings and verification.
