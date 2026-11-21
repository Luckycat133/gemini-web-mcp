# Task-First Tools and Agent Acceptance

## Small, Deterministic Tool Catalogs

Function and Account are the two business choices. Public additions use
`gemini_` prefixes; old compact aliases remain compatibility contracts. Keep
catalogs deterministic but do not treat 17 tools as a permanent maximum or use
one untyped mega-request to hide unrelated actions. Reuse registration/services;
do not add another generate/create product to work around missing capabilities.

Function is for model assistance, understanding, creation and native execution.
Account is for explicitly requested Gemini content/configuration work. A compound
request may use both. Browsing general web information is not permission to read
private Gemini history. Manifest/capability discovery is on-demand, not a ritual
first call before every already-known task.

## Task-First Skills

Keep the development Skill about repository work; runtime Skills operate the
installed product. Describe user intent and close alternatives. Keep SKILL.md
short with focused references. Do not publish a dedicated Skill before its MCP
surface is actually installable and usable. Runtime installation examples must
reference the current reviewed source, not an obsolete development branch.

## Actual task routing

| User intent | Current route | Do not confuse with |
| --- | --- | --- |
| Ask, critique, current web, ordinary app request | Function `gemini_ask` | Always invoking Task or a search-only server |
| Analyze selected Notebook context | Function Ask with `notebook_ids`, background Chat | Task flags merely because the answer is asynchronous |
| Explicit Task / ongoing delegated work | `gemini_run_task` | Ordinary complex Chat or generic MCP Tasks |
| Explain an image | `gemini_understand_image` / `gemini_understand` | Editing its pixels |
| Generate/edit media | Function modality tool, then same handle | Resubmitting after timeout |
| Search/manage private content or saved preferences | Account | Unrelated prerequisite account scans |
| Recover a prior operation | status/result with original handle | A new generation with the old prompt |
| Change repository code/Skill | Development Skill | Runtime account operations |

Ask without Notebook IDs is direct; do not promise that its Function-only
operation_id argument makes ordinary Ask durable. Ordinary session recovery
requires the original process. Function media is always a background operation,
including images. Research defaults to background; action-specific exceptions
must come from its actual schema, not guessed generic behavior.

## Structured state, inputs and handoff

Use `grounding_state = grounded | answer_only | unavailable | failed` where the
current search service exposes it. Observed attribution supports grounding;
model-written URLs or merely retrieved pages are not automatically answer support.
Assist compatibility retains `gemini_search`; Function uses Ask for current-web
questions. Do not add Function-only Notebook arguments to the assist schema.

Mixed inputs preserve stable IDs, order, per-input outcomes and explicit
truncation/unsupported states. A source upload is not proof the model consumed
all of it. Resource references preserve provider/account/kind and exact identity;
never convert Notebook view IDs by prefix guessing.

Usable output needs the requested kind, stable artifact identity, saved-file
verification, source lineage and requested/effective/observed backend distinction.
Preview and full file are separate. A local server path is not remotely reachable
by magic. The calling agent should actually insert/read/use the artifact at the
requested destination, not recite a technical receipt.

An `audio_overview` script is not generated audio; a `custom_app` specification is
not a built/deployed app. Do not advertise these as native generation until an
actual provider artifact route is implemented and accepted.

## Account and side effects

Reads return bounded metadata by default and disclose coverage. Writes preserve
omitted fields, target exact IDs and require independent read-back. If a request
was sent but its result is uncertain, retain unknown/partial rather than retrying.
Notebook deletion may include conversations; an unsupported single-asset delete
must not silently broaden into source-chat deletion.

`gemini_tasks` mixes metadata and destructive actions; check action semantics and
annotations. Task schedule mutations differ from Chat scheduled actions. Saved
preferences only prove stored values. Browser input requires exact original turn,
frame hash and unique action identity, with no automatic confirmation/permission
approval. Native app connection/OAuth is not completed by text input.

`gemini_get_operation_result` can save files, resume observation and delete an
owned source under stored policy. Its destructive annotation is intentional.
`gemini_get_operation_status` is not a promise of zero network or local writes,
but must not turn into generation/result materialization. Cancellation is absent.
Do not expose a global unbounded operation list.

## Trigger Evaluations

Use positive trigger evaluations and near-miss negative evaluations, paraphrases,
mixed-language prompts and compound tasks. Check actual choices and outcomes:

- Screenshot diagnosis -> Function understanding; change the pictured background
  -> Function edit; retrieve an old screenshot from Gemini -> Account then Function.
- New song -> Function generation; write a song prompt -> text; retrieve prior
  music after disconnect -> original operation result, not another start.
- Search current public sources -> Function Ask; search private past chats -> Account.
- Notebook analysis -> background Chat; explicitly delegated browser Task -> Task.
- Delete one selected thread -> exact Account action; clear MCP-owned test work
  -> ownership ledger, never title matching.
- Edit the repository -> this Skill, not a runtime generation or account Skill.

Static route fixtures are not measured model trigger accuracy.

## End-to-End Agent Evaluations

Require downstream handoff and no duplicate upstream start in each relevant run:

1. Create an image, use it in an app/document, interrupt, retrieve the same artifact.
2. Combine multiple references and continue editing once that feature is implemented;
   inspect visual adherence as well as valid bytes.
3. Start Research once, disconnect/restart, retrieve and cite the full original report.
4. Task and Notebook Chat: verify original completed answer and full attachments;
   distinguish goal completion, answer completion and native external app effects.
5. Find old account material, export complete declared content, transform it using
   Function, then organize the new resource without losing the source.
6. Batch management: preview fixed targets, interrupt midway, resume only pending
   items and verify each mutation; never widen a target set on retry.

Record client/model/OS, source revision, exposed catalog, calls and returned IDs,
verification/partial state, owned-resource cleanup and user-visible final result.
Private transcripts and files stay outside Git.

## Compatibility Runtime Skill

Preserve installed assist/compact/explicit-profile usage that still works, but
recommend Function/Account for new business workflows. Released 0.2.2 adapters,
state and payloads need a separate compatibility matrix. Do not claim old Skills
or entrypoints survived just because new tools have similar names.
