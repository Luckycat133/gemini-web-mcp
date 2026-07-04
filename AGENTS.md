# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.10+ FastMCP server for Gemini Web workflows. Core runtime code lives in `src/`: `server.py` is the primary MCP entrypoint, `skill_server.py` exposes the low-token skill surface, and shared client/session/auth logic is split across `client_wrapper.py`, `client_manager.py`, `cookie_manager.py`, `session_manager.py`, and `auth.py`. Tool implementations live in `src/tools/`, grouped by capability (`chat.py`, `media.py`, `file.py`, `research.py`, `manage.py`, `prompts.py`). Tests are in `tests/`, documentation is in `docs/`, and contract evaluation prompts live in `evaluations/gemini_web_mcp_contract.xml`. Local outputs such as `artifacts/`, `generated_media/`, cookies, and `.env` files must remain untracked.

## Build, Test, and Development Commands

- `python -m venv .venv && . .venv/bin/activate`: create and enter a local virtual environment.
- `pip install -e ".[all]" pytest`: install the package with optional browser/image support plus tests.
- `GEMINI_TOOLS=core python -m src.server`: run the default MCP server surface locally.
- `GEMINI_TOOLS=all python -m src.server`: run account/history/Gems-capable tools for manual verification.
- `pytest -q`: run the maintained test suite.
- `python -m py_compile src/server.py src/skill_server.py src/client_wrapper.py src/thinking_client.py src/constants.py src/tools/*.py`: catch syntax/import issues across main modules.
- `mcp dev src/server.py`: inspect the server with MCP Inspector after installing `mcp[cli]`.

## Coding Style & Naming Conventions

Use 4-space indentation, Python `snake_case` for functions and variables, and clear module-level grouping by tool domain. `pyproject.toml` configures Ruff for Python 3.10 with a 120-column line length and Mypy for Python 3.10. Keep MCP tool names stable and prefixed with `gemini_`. Prefer small helpers in `src/tools/utils.py` when behavior is shared across tools.

## Testing Guidelines

Write pytest tests as `tests/test_*.py`, with test names that describe the behavior under contract. For tool-surface changes, assert both registration and MCP annotations, and update `evaluations/gemini_web_mcp_contract.xml` when user-visible capabilities or safety metadata change. Run `pytest -q` before handing off; include targeted `py_compile` for entrypoint or import-sensitive changes.

## Commit & Pull Request Guidelines

Recent history uses concise imperative commits and conventional prefixes where useful, such as `refactor: ...`, `chore(deps): ...`, and `add ...`. Keep commits scoped to one logical change. PRs should include a short summary, tests run, configuration or environment changes, and any tool-surface, privacy, or destructive-operation implications. Link issues when available.

## Security & Configuration Tips

Never commit `.env`, `cookies.json`, `prompts.json`, generated media, or logs. Use `.env.example` for variable names only. Treat tools that read private chat text or delete Gemini account data as explicit-user-intent operations; prefer read-only discovery tools and `GEMINI_TOOLS=core` unless broader account access is required.
