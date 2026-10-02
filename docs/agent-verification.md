# Gemini Web MCP evidence reference

Read for protocol, tool, artifact, account, packaging or release changes. Documentation-only edits do not require live Gemini calls or a full test run.

## Build, Test, and Development Commands

- `python -m venv .venv && . .venv/bin/activate`: create and enter a local virtual environment.
- `pip install -e ".[all,dev]"`: install the package with optional browser/image support and maintained development gates.
- `GEMINI_TOOLS=core python -m src.server`: run the default MCP server surface locally.
- `GEMINI_TOOLS=all python -m src.server`: run account/history/Gems-capable tools for manual verification.
- `gemini-mcp-onboarding`: start the installed stdio server and call an auth-free text tool; live chat/image subcommands require `--allow-live-account`.
- `python -m ruff check src tests scripts && python -m mypy src scripts`: run the maintained static correctness gates.
- `python -m pytest -q`: run the complete offline test suite.
- `python scripts/run_contract_checklist.py`: run the targeted architecture/distribution contract gate.
- `python scripts/run_mcp_builder_evaluation.py --help`: inspect the maintained SDK v2 evaluator entrypoint; model-backed evaluation is optional and separate from offline gates.
- `python scripts/smoke_profiles.py && python scripts/smoke_mcp_protocol.py`: verify exact representative tool surfaces and both modern/legacy stdio list/call paths without live Gemini calls.
- `python scripts/package_release.py --outdir dist`: build wheel/sdist/skill assets; package CI must also run the built wheel through one isolated `uvx ... gemini-mcp-onboarding` command.
- `python scripts/run_live_canary.py --output /tmp/gemini-web-canary.json`: verify the refusal/report path without network access; live execution additionally requires the explicit flag, two opt-in variables, and a dedicated test account.
- `mcp dev src/server.py`: inspect the server with MCP Inspector after installing `mcp[cli]`.

## Testing Guidelines

Write pytest tests as `tests/test_*.py`, with test names that describe the behavior under contract. For tool-surface changes, assert both registration and MCP annotations, and update `evaluations/gemini_web_mcp_contract.xml` when user-visible capabilities or safety metadata change. Artifact-producing changes must cover stable identity, remote/local/queued/empty/failed state, save verification, backend evidence, and primary/compact parity where both surfaces expose the workflow. Public-onboarding changes must parse every checked-in client config, call a real auth-free text tool over stdio, and verify local image artifacts independently from response prose. Reverse-engineered RPC changes belong in `src/infrastructure/rpc_contracts.py`; every registered parser needs fixture cases for success, empty, rejection, and changed shape, and every mutation service must return read-back verification status. For code/protocol changes, run Ruff, Mypy, the offline suite, and the targeted contract checklist before handing off; add the profile/protocol or clean-wheel smokes for entrypoint, tool-surface, package, or release changes. Live compatibility probes are never part of PR CI: keep their report within `compatibility/live-canary-report.schema.json`, omit raw responses/account content/credentials/session identifiers, and use only the `gemini-live-canary` environment backed by a dedicated account.

## Model-backed MCP evaluation

Use the project-owned connection bridge with the unchanged vendored `mcp-builder` evaluation engine. The bridge uses MCP SDK v2 transports and serializes content blocks, structured content, and error/incomplete states before the engine consumes them. The upstream `connections.py` entrypoint targets an older SDK and is not the maintained project command.

Install the engine's optional dependency with `python -m pip install anthropic`, configure its provider secret in the process environment, and choose an explicit current provider model. This run may incur provider usage; it is not required by PR CI.

```bash
GEMINI_TOOLS=all GEMINI_AUTO_REFRESH=false python scripts/run_mcp_builder_evaluation.py \
  evaluations/gemini_web_mcp_contract.xml \
  --model "$EVALUATION_MODEL" --command .venv/bin/python --args -m src.server
```

The contract questions inspect tool metadata and safety semantics. Keep credentials out of command arguments and reports. The offline bridge tests exercise a real stdio MCP connection and the engine's content serialization with a fake provider; they do not prove model-backed task completion or remote HTTP/SSE compatibility.
