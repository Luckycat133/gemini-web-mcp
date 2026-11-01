# Task-First Tool, Skill, and Agent-Use Design

Use this reference when designing dedicated MCP tools, Runtime Skills, Artifacts, or end-to-end evaluations.

## Design Goal

The product succeeds when an agent recognizes the user's task, selects a small relevant tool surface, obtains truthful structured state or a usable Artifact, and continues the user's workflow.

A large tool catalog, a successful raw RPC, or response prose alone is not product success.

## Product Lanes

### Function surface

Use `gemini-mcp-function` for assistance, understanding, session chat, Task, media generation, research, report derivatives and operation recovery. Use `gemini_ask` for ordinary and current-web questions and natural-language app requests; it defaults to Flash and retains observed source evidence. Function Ask with `notebook_ids` starts background ordinary Chat, while `gemini_run_task` explicitly starts Task; preserve `execution_mode="chat"|"task"`. Both default to a 600-second phase timeout and return a persistent handle. The five-tool assist compatibility schema retains `gemini_search` and does not expose the function-only Notebook arguments. Function media starts are also always background operations. Propagate structured source and Artifact evidence; app mutations require resource read-back.

### Account surface

Use `gemini-mcp-account` only for explicit requests about history, Notebooks, Task metadata, Chat scheduled actions, Gems, account inventory or MCP-owned cleanup. Notebook metadata defaults to the source-picker view; `source="gemini"` is the same integrated product's project view. Moves require fresh target resolution in that view without guessed ID conversion. `gemini_tasks` provides metadata-only list/get, bounded progress, Task schedules and explicit exact-ID deletion with independent absence verification; `gemini_task_browser` supports verified frames and explicit one-shot input; its union annotation is destructive. The cleanup tool only operates on the MCP-owned ledger, never on title matches, and does not delete Notebook objects or sources. Cookie, Doctor and local Prompts are outside both business catalogs.

Task schedules and Chat scheduled actions need separate contracts. Rich Task permission prompts, browser handoff, step control, schedules and export are not all exposed. A visible app or public rendering field does not establish a complete execution contract.

## Skill Trigger Design

The Skill description is the primary trigger boundary. Write it in intent terms, not implementation terms.

Good description properties:

- says what user requests should activate the Skill;
- distinguishes close alternatives;
- does not require the user to say “Gemini” or “MCP” when the desired capability is clear;
- excludes repository development work;
- avoids enumerating low-level implementation details.

Main `SKILL.md` files should:

- route the task;
- state the minimum completion contract;
- link focused references;
- remain under 500 lines;
- avoid loading account/security/maintenance detail for every task.

References should be one level deep and focused on one domain.

## Trigger Evaluations

Each dedicated Skill needs positive and near-miss negative cases.

### Assist positives

```text
Check the latest framework documentation and give me sourced migration advice.
Explain the error in this screenshot and tell me what code to change.
Compare these two UI screenshots with the implementation.
Ask another strong model to criticize this architecture.
Research this technical market and produce a sourced report.
```

### Assist negatives

```text
Generate a hero image for this landing page.        -> create
Delete my old Gemini conversations.                 -> account
Refactor the MCP repository.                        -> development
```

### Create positives

```text
Generate an icon and put it into the app assets.
Edit this screenshot to remove the background.
Create a short video and add it to the presentation.
Generate background music for this game scene.
```

### Create negatives

```text
Explain what is wrong with this image.              -> assist
Search my Gemini history for an old conversation.   -> account
```

### Account positives

```text
Find the Gemini chat where I discussed the launch.
Move this Gemini chat to a Notebook.
Create a daily Gemini scheduled action.
Delete this Gem after verifying it exists.
```

### Account negatives

```text
Use Gemini to critique my code.                     -> assist
Generate a product image.                           -> create
```

Run trigger evaluation with paraphrases, mixed-language requests, indirect requests, and requests that mention multiple capabilities. The selected Skill should be the smallest one that can own the dominant user outcome.

## Tool Naming

Public tools use snake_case and a `gemini_` prefix.

Good:

```text
gemini_ask
gemini_search
gemini_understand_image
gemini_generate_image
gemini_get_operation_status
```

Avoid public names such as:

```text
ask
search
create
operation
manage
```

Hosts may aggregate multiple servers, so generic names collide and reduce model selection quality.

## Tool Granularity

Choose a dedicated tool when the user intent, input schema, or completion contract is materially different.

Keep `gemini_ask` separate from `gemini_understand` because pure text second-opinion work is common and should not require a mixed-input schema.

Keep `gemini_understand_image` separate from `gemini_understand` because single-image tasks are frequent and deserve a simple schema.

Use one shared operation service, but expose explicit status/result tools on primary dedicated surfaces. The compatibility low-token server may use an action facade while it remains supported.

## Assistance Tool Contracts

### `gemini_ask`

Input:

```text
prompt
optional context
model / thinking controls
function-only optional notebook_ids / timeout_seconds / operation_id
```

Output:

```text
answer
requested/effective/observed backend evidence
source conversation/lifecycle metadata when relevant
```

The calling agent compares or incorporates the answer; it should not merely quote it without completing the task.

With Notebook IDs, Ask returns a background operation handle instead of a direct answer. Keep ordinary Chat semantics; do not set Task flags. The worker verifies the exact completed original reply, saves and verifies its full local answer Artifact before owned-chat cleanup, and allows restart result retrieval. Status reads metadata only. Without Notebook IDs, Ask preserves its direct-response behavior, and a function-only `operation_id` is invalid.

### `gemini_run_task`

Use this separate tool for explicit Task execution, with optional Notebook context and a persistent operation handle. Preserve the selected model, thinking level and retention across recovery. The tool is destructive because connected-app requests may change account data. Result completion needs exact reply evidence and the Task's completed metadata, plus a verified full local answer Artifact before chat cleanup. Account mutation claims still require resource read-back. Do not advertise Task deletion as cancellation or reuse Chat scheduled-action tools for Task schedules.

### `gemini_search` (assist compatibility)

Input:

```text
query
optional recency
domains
language
max_results
```

Output:

```text
answer
sources[]
observed_at
grounding_state
```

`grounding_state = grounded | answer_only | unavailable | failed`. Grounded requires a structured citation or verified source-attribution span; `retrieved_only` evidence does not prove answer support.

### `gemini_understand_image`

Input:

```text
image path/URI
task
```

Output:

```text
analysis
observations tied to the image
input Artifact identity
```

### `gemini_understand`

Input is a bounded typed list:

```json
{
  "task": "Compare the design with the implementation",
  "inputs": [
    {"id": "design", "kind": "image", "path": "..."},
    {"id": "code", "kind": "file", "path": "..."},
    {"id": "docs", "kind": "url", "url": "..."}
  ]
}
```

Output records per-input acceptance/failure plus synthesized analysis. Do not silently drop inputs.

### `gemini_research`

Starts asynchronously by default. Input contains the research question and bounded options. Output returns an opaque `operation_id` plus observed upstream identifiers and initial state.

## Creation Tool Contracts

Each modality-specific tool starts the operation and returns either:

- a completed Artifact;
- a queued/running operation handle;
- a truthful partial/empty/failed result.

Image tools may often complete synchronously. Video/music should normally use OperationService.

## Artifact Handoff

Search and understanding normally return information. Creation and completed Research normally return files or resource links.

The calling agent should use the Artifact in the user's requested destination:

- image into website/app/document/slide;
- edited image replacing the original;
- video/audio into the project;
- Markdown report read and cited in a deliverable.

Technical fields are machine-facing acceptance evidence. Do not force the agent to recite MIME, dimensions, or paths to the user when it can directly use the file.

Minimum Artifact evidence:

```text
artifact_id
kind
state
uri/local_path
mime_type
size
width/height or duration when relevant
verification
backend evidence
```

## Operation UX

Start returns immediately:

```json
{
  "operation_id": "opaque",
  "state": "queued",
  "continuation_possible": true
}
```

Status/result accept only explicit handles; cancellation is not implemented. Unknown and expired IDs return stable errors. Do not auto-start another job after a lookup failure.

Do not expose a global unbounded operation list to ordinary function agents. Account/maintenance diagnostics may expose a paginated owner-scoped list.

## Compatibility Router

The current `gemini-web-mcp` Skill must be useful now:

- task-first capability lanes;
- compact default when it can finish;
- narrow primary profile for files/URLs/Research;
- manifest only for discovery/recovery;
- Artifact downstream use;
- async Research guidance.

Do not claim that dedicated Skills or entrypoints exist before implementation.

## End-to-End Agent Evaluations

Tool-unit tests are insufficient. Evaluate agents on complete tasks.

### Assistance

- finds current sources and includes observed URLs;
- distinguishes source-free answer from grounded search;
- interprets an error screenshot and edits the correct code;
- compares design and implementation without losing input identity;
- starts Research once and recovers it.

### Creation

- generates an image and inserts it into an actual document/app;
- edits an existing image and uses the edited file;
- starts video/music once, survives timeout/restart, retrieves the final Artifact;
- does not declare queued work complete.

### Account

- selects the target through list/search before mutation;
- does not read turn text unless the task requires it;
- preserves IDs;
- does not claim deletion/update without positive read-back.

Record:

```text
client/model/version
OS and package commit
tool catalog exposed
tool selected and arguments
structured result
number of retries/duplicate starts
Artifact handoff outcome
operation recovery outcome
final user-task completion
```

A good evaluation asks whether the agent completed the task, not whether it called a particular tool by rote.
