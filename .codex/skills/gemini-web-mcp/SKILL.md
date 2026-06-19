---
name: gemini-web-mcp
description: "Use when working in the gemini-web-mcp repository or using its Gemini Web MCP/skill servers: inspect the tool manifest, choose safe Gemini Web tools, manage chat history, validate Pro Web capability coverage, update docs/tests/evaluations, or avoid unsafe destructive/private-account operations."
---

# Gemini Web MCP

Use this skill for this repository's Gemini Web MCP server and low-token skill server.

## Start Here

1. Prefer `gemini_get_tool_manifest` before choosing tools. It is always exposed by `src.server`, even when `GEMINI_TOOLS=core` or `GEMINI_TOOLS=prompts`.
2. Check `current_enabled` before assuming a tool can be called in the current MCP process.
3. Prefer read-only tools first: `gemini_get_tool_manifest`, `gemini_get_web_capabilities`, `gemini_probe_web_features`, `gemini_list_chats`, `gemini_search_chats` without `scan_turns`.
4. Treat `privacy=reads_private_chat_text` tools as explicit-user-intent tools: `gemini_read_chat`, `gemini_export_chat`, and `gemini_search_chats(scan_turns=true)`.
5. Treat destructive tools as requiring explicit user intent: `gemini_delete_chat`, `gemini_reset_session`, `gemini_manage_gems(action="delete")`, and prompt deletion.

## Tool Surfaces

- Primary MCP server: `src.server`
  - Default `GEMINI_TOOLS=core` plus always-on manifest and cookie helpers.
  - `GEMINI_TOOLS=all` adds account/history/Gems tools.
  - `GEMINI_TOOLS=prompts` adds local prompt management plus always-on manifest/cookie helpers.
- Low-token skill server: `src.skill_server`
  - Use `account(action="manifest")` for compact tool guidance.
  - Use `history(action="list|search|read|export|delete")` for chat history.

## Chat History Workflow

1. List or search metadata first:
   - `gemini_list_chats(limit=..., offset=..., response_format="json")`
   - `gemini_search_chats(query=..., scan_turns=false, response_format="json")`
2. Only scan turn text when the user asks for content search:
   - `gemini_search_chats(query=..., scan_turns=true, turns_per_chat=..., max_chars_per_turn=...)`
3. Read/export one selected chat only after the user has indicated the target:
   - `gemini_read_chat(chat_id=...)`
   - `gemini_export_chat(chat_id=..., response_format="markdown"|"json")`
4. Delete only with explicit confirmation:
   - `gemini_delete_chat(chat_id=...)`

## Web Pro Coverage Rules

- `gemini_get_web_capabilities` is the static observed Pro Web surface map.
- `gemini_probe_web_features` checks observed read-only RPC reachability and must not expose raw private RPC bodies.
- Keep Drive picker, Canvas mutation, settings mutation, memory import mutation, public-link mutation, and scheduled-action mutation disabled unless a stable RPC contract and explicit user authorization exist.
- Guided Learning is exposed through `learning_mode`; Canvas remains probe/UI-only unless stronger evidence appears.

## Validation

Before finishing changes, run:

```bash
./.venv/bin/pytest -q
./.venv/bin/python -m py_compile src/tools/annotations.py src/tools/chat.py src/tools/media.py src/tools/file.py src/tools/research.py src/tools/prompts.py src/tools/manage.py src/server.py src/skill_server.py src/client_wrapper.py src/thinking_client.py src/constants.py
git diff --check
```

When changing manifest or capability metadata, also verify:

```bash
GEMINI_TOOLS=core ./.venv/bin/python - <<'PY'
import asyncio
from src.server import mcp
async def main():
    tools = await mcp.list_tools()
    print(len(tools), [t.name for t in tools if t.annotations is None])
asyncio.run(main())
PY
```

Keep `evaluations/gemini_web_mcp_contract.xml` aligned with `gemini_get_tool_manifest` and `gemini_get_web_capabilities`.
