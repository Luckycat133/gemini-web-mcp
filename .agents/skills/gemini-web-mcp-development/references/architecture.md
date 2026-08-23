# Current Architecture and Target Product Topology

Use this reference when changing server boundaries, shared services, tool catalogs, or Runtime Skills. Verify statements against the current checkout and latest evidence.

## Current Compatibility Stack

The repository currently ships:

- `gemini-mcp-server`: profile-based primary MCP surface;
- `gemini-mcp-skill-server`: fixed eleven-tool low-token surface;
- `gemini-mcp-assist`: dedicated five-tool assistance surface (`gemini_assist_mcp`);
- `gemini-mcp-onboarding`: credential-free preflight plus explicitly authorized examples;
- two Runtime Skills: task-first compatibility `gemini-web-mcp` and dedicated assistance `gemini-assist`;
- one Development Skill;
- shared client/session/chat/lifecycle/Artifact/history/search/understanding/research services;
- account, Notebook, Scheduled, Gem, Prompt, Doctor, Cleanup, and compatibility services;
- MCP Python SDK v2 adapters with modern and legacy protocol smoke.

These remain supported compatibility surfaces while the create and account focused products are introduced.

## Product Priority

```text
1. Extend an agent with assistance, search, understanding, and research
2. Give an agent generated image/video/music/report Artifacts
3. Let an agent explicitly manage Gemini account data
```

Do not make account management part of the default coding-agent tool catalog.

## Target Topology

```text
Gemini Web adapter + RPC registry/parsers
                    │
        shared domain and services
                    │
 ┌──────────────────┼──────────────────┐
 │                  │                  │
assist adapter   create adapter    account adapter
 │                  │                  │
gemini-assist    gemini-create     gemini-account
Skill + MCP       Skill + MCP       Skill + MCP
```

All products remain in one repository and one Python distribution.

### Shared Layers

```text
src/domain
  DomainResult, Artifact, operation and verification models

src/infrastructure
  Gemini Web / gemini-webapi boundary
  RPC registry, payload builders, pure parsers
  SQLite migrations and repositories

src/services
  chat, understanding, search, research, media
  operations, lifecycle, cleanup
  history, account, notebooks, scheduled, gems, prompts

src/surfaces
  assist.py
  create.py
  account.py
  compatibility adapters
```

A surface must not implement a second copy of request construction, parsing, polling, Artifact extraction, verification, or persistence.

## Focused Products

### Assistance

```text
Skill: gemini-assist
entrypoint: gemini-mcp-assist
MCP name: gemini_assist_mcp
```

Public tools:

```text
gemini_ask
gemini_search
gemini_understand_image
gemini_understand
gemini_research
```

Responsibilities:

- second opinions, critique, code/design review;
- quick grounded search with observed source evidence;
- simple visual understanding;
- typed mixed-input understanding;
- asynchronous Deep Research start.

It does not expose history, Cookie, Scheduled, Gem, Prompt, or cleanup tools.

### Creation

```text
Skill: gemini-create
entrypoint: gemini-mcp-create
MCP name: gemini_create_mcp
```

Public tools:

```text
gemini_generate_image
gemini_edit_image
gemini_generate_video
gemini_generate_music
gemini_get_operation_status
gemini_get_operation_result
gemini_cancel_operation
```

Responsibilities:

- create verified image Artifacts;
- edit an existing image and preserve source Artifact identity;
- start video/music operations;
- recover operation state and final Artifacts.

It does not expose account data or generic chat/history tools.

### Account

```text
Skill: gemini-account
entrypoint: gemini-mcp-account
MCP name: gemini_account_mcp
```

Public tools:

```text
gemini_history
gemini_notebooks
gemini_scheduled
gemini_gems
gemini_prompts
gemini_account
gemini_cleanup
```

Responsibilities:

- explicit account metadata and content workflows;
- read-before-mutate object selection;
- authoritative mutation verification;
- maintenance and cleanup diagnostics.

This surface may expose a paginated operation/cleanup diagnostics view for maintenance. Assist/create should not expose an unbounded list of other clients' operations.

## Tool Design Constraints

### Prefixes and Discoverability

Public tools use `gemini_` prefixes because hosts can aggregate multiple MCP servers. Names are verb-first and task-specific. Descriptions state exactly when the model should invoke them and what evidence they return.

### Deterministic Catalogs

Each dedicated server has a stable, small catalog. Authentication may affect execution or capabilities, but not silently replace the catalog with unrelated tools. Tool order, names, schemas, and annotations are snapshot-tested.

### Structured Output

Every public tool defines an output schema and returns validated structured content. Compatibility text is optional presentation and cannot contradict structured state.

### Grounded Search

`gemini_search` returns:

```text
answer
sources[]
observed_at
grounding_state = grounded | answer_only | unavailable | failed
```

`grounded` requires observed source URLs or equivalent structured source evidence. A model answer without evidence is `answer_only`.

### Mixed Inputs

`gemini_understand` accepts a typed input collection rather than one overloaded string:

```text
text
image path/URI
file path/URI
URL
later audio/video
```

Each input keeps a stable identity. The result records which inputs were accepted, analyzed, skipped, or failed.

### Artifacts

Creation and completed Research return resource links or verified local files plus structured metadata. The calling agent uses the Artifact in its downstream task. Remote URI, local file, queued, partial, empty, and failed remain distinct.

### Long Operations

Modality-specific tools start operations and return an opaque handle immediately. Explicit status/result/cancel tools receive the handle as an argument. Do not rely on transport connection state.

If the official MCP Tasks extension is available and negotiated, it can be supported as an additional protocol-native representation. The project-owned operation contract remains the compatibility path because supported clients and the Python SDK may not all expose the extension uniformly.

## SQLite Boundaries

Use one local database with explicit schema versions.

### `operations`

Allowed fields:

```text
operation_id
operation_type
provider_operation_id
upstream_chat_id
state
created_at / updated_at / expires_at
attempt_count
error_code
verification_status
artifact_id / artifact locator
```

### `cleanup_jobs`

Allowed fields:

```text
job_id
resource_type / resource_id
delete_at
state
attempt_count / next_attempt_at
error_code
verification_status
```

Never store Cookies, prompts, chat/report text, raw responses, or generated bytes.

Operation IDs are high-entropy, opaque, restart-safe, and independent of MCP connection identity. Default retention is seven days. Status and result are idempotent. Cancellation is cooperative/best-effort until positively observed.

## Existing Foundations to Preserve

- async-safe client initialization/reset;
- shared Session and Chat services;
- typed `DomainResult` and stable public errors;
- Artifact normalization and local verification;
- centralized reverse-engineered contracts/parsers;
- typed shared history and evidence-based deletion;
- Gem read-back verification;
- package/protocol/onboarding gates;
- explicit live-evidence boundary.

## Migration Strategy

1. Keep the compatibility servers and `gemini-web-mcp` Skill working.
2. Make the compatibility Skill task-first and route current tools truthfully.
3. Build one complete `gemini-assist` vertical slice without copying business logic.
4. Build `gemini-create` image flows.
5. Add OperationService, then Research/video/music.
6. Build `gemini-account` from shared account/history services.
7. Publish dedicated Skills only after their entrypoints and trigger evaluations pass.
8. Deprecate compatibility surfaces only after real client adoption proves the dedicated products.

## Remaining Engineering Gaps

- no dedicated create/account entrypoints yet;
- no restart-safe OperationService yet;
- no restart-safe Cleanup queue yet;
- prose-first admin paths remain;
- mutation verification is uneven outside History/Gems;
- full current live evidence is incomplete;
- official client/OS matrix is undecided.

## Deferred UI Parity

Drive import, Canvas mutation, richer recurrence, Notebook CRUD/source management, sharing, settings, memory import, and Library asset management remain secondary until the focused core workflows are reliable.
