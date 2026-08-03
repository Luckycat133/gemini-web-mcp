# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11+ FastMCP server for Gemini Web workflows. Core runtime code lives in `src/`: `server.py` is the primary MCP entrypoint, `skill_server.py` exposes the low-token skill surface, `domain/` defines typed result and artifact contracts, `adapters/` preserves MCP compatibility, `services/` owns cross-adapter chat/artifact/history/account/notebook/scheduled/Gem/manifest/doctor workflows, and `infrastructure/` owns the evidence-backed Gemini Web RPC registry and pure parsers. Shared client/session/cookie/cleanup logic is split across `client_wrapper.py`, `client_manager.py`, `cookie_manager.py`, `session_manager.py`, `remote_chat_cleanup_manager.py`, `thinking_client.py`, and `error_handler.py`. Tool implementations live in `src/tools/`; `manage.py` is now a compatibility registration adapter and must not become a dependency of compact or unrelated services. Tests are in `tests/`, documentation is in `docs/`, and contract evaluation prompts live in `evaluations/gemini_web_mcp_contract.xml`. Local outputs such as `artifacts/`, `generated_media/`, cookies, and `.env` files must remain untracked.

## Build, Test, and Development Commands

- `python -m venv .venv && . .venv/bin/activate`: create and enter a local virtual environment.
- `pip install -e ".[all,dev]"`: install the package with optional browser/image support and maintained development gates.
- `GEMINI_TOOLS=core python -m src.server`: run the default MCP server surface locally.
- `GEMINI_TOOLS=all python -m src.server`: run account/history/Gems-capable tools for manual verification.
- `python -m ruff check src tests scripts && python -m mypy src scripts`: run the maintained static correctness gates.
- `python -m pytest -q`: run the complete offline test suite.
- `python scripts/run_contract_checklist.py`: run the targeted architecture/distribution contract gate.
- `python scripts/smoke_profiles.py && python scripts/smoke_mcp_protocol.py`: verify exact representative tool surfaces and both stdio MCP handshakes without live Gemini calls.
- `mcp dev src/server.py`: inspect the server with MCP Inspector after installing `mcp[cli]`.

## Coding Style & Naming Conventions

Use 4-space indentation, Python `snake_case` for functions and variables, and clear module-level grouping by tool domain. `pyproject.toml` configures Ruff and Mypy for Python 3.11 with a 120-column line length. Keep MCP tool names stable and prefixed with `gemini_`. Put cross-adapter workflow logic in `src/services/`; keep MCP text rendering and argument compatibility in the adapters. Prefer small presentation helpers in `src/tools/utils.py` when behavior is shared across tools.

## Testing Guidelines

Write pytest tests as `tests/test_*.py`, with test names that describe the behavior under contract. For tool-surface changes, assert both registration and MCP annotations, and update `evaluations/gemini_web_mcp_contract.xml` when user-visible capabilities or safety metadata change. Artifact-producing changes must cover stable identity, remote/local/queued/empty/failed state, save verification, backend evidence, and primary/compact parity where both surfaces expose the workflow. Reverse-engineered RPC changes belong in `src/infrastructure/rpc_contracts.py`; every registered parser needs fixture cases for success, empty, rejection, and changed shape, and every mutation service must return read-back verification status. Run Ruff, Mypy, the complete offline suite, and the targeted contract checklist before handing off; add the profile/protocol or clean-wheel smokes for entrypoint, tool-surface, package, or release changes.

## Commit & Pull Request Guidelines

Recent history uses concise imperative commits and conventional prefixes where useful, such as `refactor: ...`, `chore(deps): ...`, and `add ...`. Keep commits scoped to one logical change. PRs should include a short summary, tests run, configuration or environment changes, and any tool-surface, privacy, or destructive-operation implications. Link issues when available.

## Security & Configuration Tips

Never commit `.env`, `cookies.json`, `prompts.json`, generated media, or logs. Use `.env.example` for variable names only. Treat tools that read private chat text or delete Gemini account data as explicit-user-intent operations; prefer read-only discovery tools and `GEMINI_TOOLS=core` unless broader account access is required.
