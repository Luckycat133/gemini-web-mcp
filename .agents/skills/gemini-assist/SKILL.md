---
name: gemini-assist
description: "Use this skill when the user wants a second opinion, critique, code or design review, current-web search that names its sources, image or screenshot understanding, file, URL, or mixed-input understanding, or Deep Research. Prefer this focused skill over the gemini-web-mcp compatibility skill for pure assistance and understanding workloads. Do not use it for pure image, video, or music generation, Gemini account administration, or repository development; route those requests to the creation, account, or development capability instead."
license: MIT-0
metadata:
  version: "0.2.1"
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
        description: Optional __Secure-1PSID Cookie for authenticated Gemini Web calls.
      - name: GEMINI_PSIDTS
        required: false
        description: Optional matching __Secure-1PSIDTS Cookie recommended for session stability.
      - name: GEMINI_PSIDCC
        required: false
        description: Optional __Secure-1PSIDCC Cookie forwarded when configured.
      - name: GEMINI_PROXY
        required: false
        description: Optional HTTP or HTTPS proxy used by the MCP server.
---

# Gemini Assist

Extend the current task with Gemini assistance: a second opinion, grounded current-web evidence, visual or mixed-input understanding, or Deep Research. Use the user's task to pick one tool; do not tour the Gemini surface.

This surface deliberately exposes no history, Cookie, Scheduled, Gem, Prompt, manifest, or cleanup tools.

When the default `gemini-mcp-function` surface is connected, prefer its `gemini_ask` for ordinary questions, current-web requests and natural-language app requests. Flash is the default, and no web or tool enable switch is exposed. Observed source evidence is returned, but answer prose does not prove that an external app action or Task was executed.

## Choose The Tool

The following table describes the five-tool assist compatibility server; on the default function surface, use `gemini_ask` for current-web questions as noted above.

| User intent | Tool | What success means |
| --- | --- | --- |
| Second opinion, critique, code or design review | `gemini_ask` | the answer is compared or incorporated into the agent's own conclusion |
| Current-web question that should name sources | `gemini_search` | answer plus observed `sources` and a truthful `grounding_state` |
| Understand one image or screenshot | `gemini_understand_image` | analysis tied to that image and used in the surrounding task |
| Understand files, URLs, or mixed evidence together | `gemini_understand` | every input keeps its id and per-input outcome plus one synthesized analysis |
| Multi-source investigation that yields a durable report | `gemini_research` | one asynchronous start returns a preserved operation handle |

## Server And Installation

The dedicated assistance entrypoint is `gemini-mcp-assist`; the MCP server name is `gemini_assist_mcp`.

```bash
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@codex/gemini-tools-polish-20261101 gemini-mcp-assist
```

The catalog is exactly five tools: `gemini_ask`, `gemini_search`, `gemini_understand_image`, `gemini_understand`, and `gemini_research`.

For an existing Notebook's sources, use the function server's `gemini_ask(notebook_ids=...)`, which returns a background ordinary-chat handle. Explicit multi-step Task execution uses function `gemini_run_task`; it is a separate mode. Those function-only arguments and Task tools are not part of this five-tool assist schema. Notebook/Task account metadata and explicit deletion use the account server; preserve the original operation handle for result recovery.

Use Flash by default for web search and multimodal understanding. All observed models can reason and understand images; choose by task and style. For writing, creative work, character dialogue or difficult reasoning, the project owner's experience favors Argon when available; independently check long-form consistency and constraints. Pro is for an explicit choice or a concrete higher-tier need, not a default. Argon's runtime availability and route must be verified.

Community snapshot (2026-10-31): [Flash users](https://www.reddit.com/r/GeminiAI/comments/1wjmz5x/actually_gemini_38_flash_is_a_beast/) report useful everyday coding, but [search users](https://www.reddit.com/r/GeminiAI/comments/1xeq89i/is_gemini_flash_38_web_search_broken/) also report skipped retrieval. [An Argon writing comparison](https://discuss.ai.google.dev/t/first-test-of-gemini-4-argon-much-better-immersion-but-stricter-with-explicit-adult-roleplay-than-gemini-3-x/189556) favors immersion; [other users](https://www.reddit.com/r/GeminiAI/comments/1xcbqfu/1_week_later_thoughts_on_gemini_4_argon/) describe both strengths and multi-step failures. [Pro feedback](https://www.reddit.com/r/Bard/comments/1rc68fg/how_was_your_experience_in_using_gemini_31_pro_in/) includes better context/coding in some settings and hallucinations in others. These are individual experiences across different products, not a universal ranking. Preserve the Flash default and verify sources and task results.

Authorized live recheck (2026-11-01): Argon/standard writing and Flash/standard search with two associated sources completed and automatic cleanup succeeded. An earlier ordinary Flash request timed out; neither this run nor community reports establishes consistently low latency. No Lite generation was needed while quota remained available.

Flash-Lite is reserved for quota fallback, not everyday selection. The client requires fresh, account-bound basic Flash text-quota evidence, expires it after 60 seconds or a basic Flash success, and may select Lite for the next new query without replaying the original. Advanced-thinking, file, research, media, authentication and network failures do not establish basic Flash exhaustion. Lite uses fixed SDK default level 1 rather than a selectable level. Inspect requested/effective model, fallback reason and thinking selectability when reported; no manual quota check or level removal is needed. Installed-SDK offline and public MCP integration checks passed; a real exhausted-account run has not been verified. Existing `standard` and `extended` are compatibility parameters, not verified mappings to all current Web UI levels.

Authenticated workflows use configured Gemini Web Cookies or automatic discovery from a logged-in browser. A successful text reply alone does not prove authentication: inspect the account and capability errors for uploads, Research, and generation.

## Truthful Grounding

Function `gemini_ask` and assist compatibility `gemini_search` report one `grounding_state`:

```text
grounded | answer_only | unavailable | failed
```

- `grounded` requires observed source URLs in `sources`;
- an answer without observed evidence is `answer_only` and is never labeled grounded;
- an empty answer is `unavailable`; an errored search is `failed`.

Do not relabel a source-free answer as grounded and do not invent source URLs. When a question needs evidence the quick search did not observe, escalate to `gemini_research`.

## Information, Not Artifacts

Search and understanding return information to the calling agent. Synthesize the answer or analysis into the user's task instead of dumping raw Gemini output. A ready Deep Research report can be retrieved with its saved operation handle; default cleanup follows extraction unless retention was requested.

## Deep Research Starts Asynchronously

`gemini_research(action="start")` defaults to background execution and returns a handle without waiting for plan creation or the final report:

- the structured result carries an opaque `operation_id` plus the preserved `upstream_operation_id` and `upstream_chat_id`;
- `state` is `accepted` when the confirmation returned no progress evidence, or the observed upstream state; a started run is never a completed report;
- `timeout_seconds` bounds only the plan and start phases, never the report itself;
- `retain_chat` defaults to false; an unread research chat remains protected from automatic deletion, and taking its verified result triggers cleanup unless the caller explicitly chose retention;
- preserve every returned identifier and never start a duplicate run because one call timed out.

For tracking and duplicate prevention when the first call disconnects before returning, generate a random handle before calling: `op_` followed by 32 lowercase hexadecimal characters (for example, Python `"op_" + secrets.token_hex(16)`). Pass it as `operation_id`. While its record remains in the same database, reusing the handle ignores the new query and retention settings. For an unverified plan outcome, it may recover the original completed plan and atomically confirm it once using the saved model and thinking level. A previously claimed confirmation is never resent; otherwise the saved state is returned. Without a caller-supplied handle, a connection lost before the first response may leave the caller unable to locate the saved operation.

If the plan request is interrupted before an upstream chat ID is observed and checkpointed, the handle can prevent a duplicate start but cannot recover the remote plan. Report `continuation_unavailable`; do not silently start another query with a new handle.

Deduplication lasts only while the seven-day metadata record exists. Never reuse an expired or pruned handle: once its record has been deleted it can be treated as a new start.

Use `gemini_research(action="status" | "result", operation_id=...)` on this surface; `query` may be omitted for those actions. The primary companion server (`gemini-mcp-server`, `GEMINI_TOOLS=research` or `core`) exposes `gemini_get_operation_status(operation_id=...)` and `gemini_get_operation_result(operation_id=...)`. They use the same local SQLite metadata database as this assist server, so an explicit handle works after restart and across clients. Set `GEMINI_OPERATION_DB_PATH` consistently when overriding its default location. Records expire after seven days; prompts, reports and Cookies are never stored in that database. Reads inspect a bounded recent-history window; no observed report can also mean readback was inconclusive or the report lies outside that window. It is not proof that the upstream job is still running. Cancellation is not implemented.

Deep Research requires an AI Plus subscription; report the typed `CAPABILITY_UNAVAILABLE` result instead of retrying.

## Boundaries

- Pure image, video, or music generation is a creation task, not assistance.
- Gemini account administration — history, Notebooks, Scheduled, Gems, Prompts, cleanup — needs explicit account surfaces.
- Developing the gemini-web-mcp repository itself is development work, not assistance.
- This surface has exactly the five tools above. Use the same tool's status and result actions for research recovery.

## Standard Workflow

1. Identify the assistance intent and choose exactly one tool.
2. Supply the smallest complete input: a prompt with optional context, one image, or one typed input list.
3. Read the structured result before trusting the compatibility text.
4. Continue the user's task with the returned information.
5. For Deep Research, preserve the handle and recover the report later.
