---
name: gemini-web-mcp
description: "Use when working in the gemini-web-mcp repository or using its Gemini Web MCP/skill servers: inspect the tool manifest, choose safe Gemini Web tools, manage chat history, validate Pro Web capability coverage, update docs/tests/evaluations, or avoid unsafe destructive/private-account operations."
---

# Gemini Web MCP

Use this skill for this repository's Gemini Web MCP server and low-token skill server.

## Start Here

1. Prefer `gemini_get_tool_manifest` before choosing tools. It is always exposed by `src.server`, even when `GEMINI_TOOLS=core` or `GEMINI_TOOLS=prompts`.
2. Check `current_enabled` before assuming a tool can be called in the current MCP process.
3. Prefer read-only tools first: `gemini_doctor`, `gemini_get_tool_manifest`, `gemini_get_web_capabilities`, `gemini_probe_web_features`, `gemini_list_chats`, `gemini_search_chats` without `scan_turns`.
4. Treat `privacy=reads_private_chat_text` tools as explicit-user-intent tools: `gemini_read_chat`, `gemini_export_chat`, and `gemini_search_chats(scan_turns=true)`.
5. Treat destructive tools as requiring explicit user intent: `gemini_delete_chat`, `gemini_delete_scheduled_action`, `gemini_reset_session`, `gemini_manage_gems(action="delete")`, and prompt deletion.

## Tool Surfaces

- Primary MCP server: `src.server`
  - Default `GEMINI_TOOLS=core` plus always-on manifest and cookie helpers.
  - `GEMINI_TOOLS=all` adds account/history/Gems tools.
  - `GEMINI_TOOLS=prompts` adds local prompt management plus always-on manifest/cookie helpers.
- Low-token skill server: `src.skill_server`
  - Use `account(action="manifest")` for compact tool guidance.
  - Use `history(action="list|search|read|export|delete")` for chat history.
  - Use `cleanup(dry_run=true)` before deleting test chats or scheduled actions by marker.
  - Use `scheduled(action="list|get|create|delete")` for compact scheduled-action workflows.
  - Use `doctor(validate_browser=false)` for low-cost local preflight before live account workflows.
  - Use `cookie(action="profiles")` before `cookie(action="get", profile="...")` when Chrome has multiple signed-in profiles.

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
- Scheduled actions support observed daily create, registry list, by-id get, and explicit delete by id through `gemini_create_scheduled_action`, `gemini_list_scheduled_actions`, `gemini_get_scheduled_action`, and `gemini_delete_scheduled_action`; refresh Chrome cookies first when account context matters because browser-cookie refresh isolates gemini_webapi cookie cache and prefers profiles with visible scheduled registries. If the registry is still empty, call `gemini_list_browser_cookie_profiles` and then `gemini_get_cookie_from_browser(profile="...")` to explicitly align the profile; compare `chrome_selected_profile` with the profile that has Gemini cookies. After create/delete, check `verification_status`; after create also check `readable_by_id_after_create`, and after delete check `deleted_by_id_after_delete` or `task_state_after_delete=deleted` before claiming the task is gone. Keep edit/toggle/other recurrence variants disabled until stable RPC contracts exist.
- Keep Drive picker, Canvas mutation, settings mutation, memory import mutation, and public-link mutation disabled unless a stable RPC contract and explicit user authorization exist.
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
