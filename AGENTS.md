# Gemini Web MCP

## Runtime and ownership

Python 3.11+, MCP Python SDK v2. `src/server.py` owns the main surface; `src/skill_server.py` the compact surface; `src/surfaces/assist.py` the five-tool assist surface; `src/onboarding.py` public installation examples. All surfaces stay thin adapters over `src/services/`.

`src/domain/` defines results/artifacts; `src/adapters/mcp_sdk.py` is the single SDK/protocol-model import boundary; adapters preserve text/structured compatibility. `src/infrastructure/` owns evidence-backed Web RPC contracts and pure parsers. Shared client/session/cookie/cleanup managers remain the lifecycle owners. `src/tools/manage.py` is only a compatibility registration adapter, never a dependency of compact or unrelated services.

## Change contracts

- Keep `gemini_` tool names stable. Cross-surface workflow logic belongs in services; rendering and argument compatibility belong in adapters.
- Tool changes preserve registration, annotations, result states and relevant primary/compact parity. Update `evaluations/gemini_web_mcp_contract.xml` when capabilities or safety metadata change.
- RPC changes belong in `src/infrastructure/rpc_contracts.py`, with fixtures for success, empty, rejection and changed shape. Mutation services return read-back status; never turn missing evidence into success.
- For artifact, onboarding, tool-surface, packaging or release work, read [verification contracts and commands](docs/agent-verification.md). Offline/mock results and live-account results are separate evidence.

## Commands

- Setup: `python -m venv .venv && . .venv/bin/activate`, then `pip install -e ".[all,dev]"`.
- Default surface: `GEMINI_TOOLS=core python -m src.server`; use `all` only for tasks requiring broader account tools.
- Static gates: `python -m ruff check src tests scripts` and `python -m mypy src scripts`.
- Offline suite: `python -m pytest -q`; architecture/distribution gate: `python scripts/run_contract_checklist.py`.

## Privacy and contribution

Private chat reads and account deletion require explicit user intent. Public onboarding live examples require `--allow-live-account`; live canaries require their flag, both opt-in variables and dedicated account/environment, never PR CI. Reports follow `compatibility/live-canary-report.schema.json` and omit raw responses, account contents, credentials and session identifiers.

Do not commit `.env`, cookies, `prompts.json`, generated media/artifacts or logs. `.env.example` contains variable names only. Follow the Ruff/Mypy settings in `pyproject.toml`, 4-space indentation and existing Python naming. PRs describe actual behavior/evidence and relevant configuration, protocol and privacy effects.
