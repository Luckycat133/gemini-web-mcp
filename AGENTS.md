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
- Business surfaces: `gemini-mcp-function` for Gemini functionality and `gemini-mcp-account` for explicit account work. `gemini-mcp-server` defaults to the function surface when `GEMINI_TOOLS` is unset.
- Compatibility profiles: `GEMINI_TOOLS=core python -m src.server`; use `all` only for broader maintenance. The published 0.2.2 interface divergence is documented in `docs/source-update-integration.md`.
- Static gates: `python -m ruff check src tests scripts` and `python -m mypy src scripts`.
- Offline suite: `python -m pytest -q`; architecture/distribution gate: `python scripts/run_contract_checklist.py`.

## Verification and contribution

Write pytest tests as `tests/test_*.py`, with test names that describe the behavior under contract. For tool-surface changes, assert both registration and MCP annotations, and update `evaluations/gemini_web_mcp_contract.xml` when user-visible capabilities or safety metadata change. Artifact-producing changes must cover stable identity, remote/local/queued/empty/failed state, save verification, backend evidence, and primary/compact parity where both surfaces expose the workflow. Public-onboarding changes must parse every checked-in client config, call a real auth-free text tool over stdio, and verify local image artifacts independently from response prose. Reverse-engineered RPC changes belong in `src/infrastructure/rpc_contracts.py`; every registered parser needs fixture cases for success, empty, rejection, and changed shape, and every mutation service must return read-back verification status. Run focused checks for the affected behavior first. For broad runtime changes or release readiness, run Ruff, Mypy, the complete offline suite, and the targeted contract checklist; retain profile/protocol and clean-wheel smokes when entrypoint, tool-surface, package, or release contracts are affected. Documentation-only changes need relevant content/link checks rather than the runtime suite. Live compatibility probes are never part of PR CI: keep their report within `compatibility/live-canary-report.schema.json`, omit raw responses/account content/credentials/session identifiers, and use only the `gemini-live-canary` environment backed by a dedicated account.

## Privacy and contribution

Private chat reads and account deletion require explicit user intent. Public onboarding live examples require `--allow-live-account`; live canaries require their flag, both opt-in variables and dedicated account/environment, never PR CI. Reports follow `compatibility/live-canary-report.schema.json` and omit raw responses, account contents, credentials and session identifiers.

Do not commit `.env`, cookies, `prompts.json`, generated media/artifacts or logs. `.env.example` contains variable names only. Follow the Ruff/Mypy settings in `pyproject.toml`, 4-space indentation and existing Python naming. PRs describe actual behavior/evidence and relevant configuration, protocol and privacy effects.
Recent history uses concise imperative commits and conventional prefixes where useful, such as `refactor: ...`, `chore(deps): ...`, and `add ...`. Keep commits scoped to one logical change. PRs should include a short summary, tests run, configuration or environment changes, and any tool-surface, privacy, or destructive-operation implications. Link issues when available.

## Security & Configuration Tips

Never commit `.env`, `cookies.json`, `prompts.json`, generated media, or logs. Use `.env.example` for variable names only. Treat tools that read private chat text or delete Gemini account data as explicit-user-intent operations; prefer read-only discovery tools and `GEMINI_TOOLS=core` unless broader account access is required.
