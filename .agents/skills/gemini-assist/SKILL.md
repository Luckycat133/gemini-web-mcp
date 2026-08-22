---
name: gemini-assist
description: "Use this skill when the user wants a second opinion, critique, code or design review, grounded current-web search with observed sources, image or screenshot understanding, file, URL, or mixed-input understanding, or Deep Research. Do not use it for pure image, video, or music generation, Gemini account administration, or repository development; route those requests to the creation, account, or development capability instead."
license: MIT-0
compatibility: "Requires Python 3.11+ and the dedicated gemini-mcp-assist MCP server. Run it with uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-assist. Live calls require Gemini Web account Cookies."
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

## Choose The Tool

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
uvx --from git+https://github.com/Luckycat133/gemini-web-mcp@main gemini-mcp-assist
```

The catalog is exactly five tools: `gemini_ask`, `gemini_search`, `gemini_understand_image`, `gemini_understand`, and `gemini_research`.

Models: `flash-lite`, `flash` (default), `thinking`, `pro`; `thinking_level` is `standard` or `extended`.

Live calls require Gemini Web account Cookies (`GEMINI_PSID` and the recommended matching `GEMINI_PSIDTS`); without them the tools return typed authentication errors instead of pretending to work.

## Truthful Grounding

`gemini_search` reports one `grounding_state`:

```text
grounded | answer_only | unavailable | failed
```

- `grounded` requires observed source URLs in `sources`;
- an answer without observed evidence is `answer_only` and is never labeled grounded;
- an empty answer is `unavailable`; an errored search is `failed`.

Do not relabel a source-free answer as grounded and do not invent source URLs. When a question needs evidence the quick search did not observe, escalate to `gemini_research`.

## Information, Not Artifacts

Search and understanding return information to the calling agent, not files. Synthesize the answer or analysis into the user's task instead of dumping raw Gemini output. A completed Deep Research report stays in the retained Gemini chat; recover it through the preserved chat identity instead of expecting a downloaded Artifact from these tools.

## Deep Research Starts Asynchronously

`gemini_research` starts one Deep Research run and returns immediately after the upstream research has started:

- the structured result carries an opaque `operation_id` plus the preserved `upstream_operation_id` and `upstream_chat_id`;
- `state` is `queued` or `running`; a started run is never a completed report;
- `timeout_seconds` bounds only the plan and start phases, never the report itself;
- the research chat is retained by default so the report stays recoverable;
- preserve every returned identifier and never start a duplicate run because one call timed out.

Deep Research requires an AI Plus subscription; report the typed `CAPABILITY_UNAVAILABLE` result instead of retrying.

## Boundaries

- Pure image, video, or music generation is a creation task, not assistance.
- Gemini account administration — history, Notebooks, Scheduled, Gems, Prompts, cleanup — needs explicit account surfaces.
- Developing the gemini-web-mcp repository itself is development work, not assistance.
- Do not call tools outside the five-tool catalog above; this surface has none.

## Standard Workflow

1. Identify the assistance intent and choose exactly one tool.
2. Supply the smallest complete input: a prompt with optional context, one image, or one typed input list.
3. Read the structured result before trusting the compatibility text.
4. Continue the user's task with the returned information.
5. For Deep Research, preserve the handle and recover the report later.
