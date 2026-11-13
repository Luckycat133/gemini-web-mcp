# Gemini Web MCP evidence reference

Read for protocol, tool, artifact, account, packaging or release changes. Documentation-only edits do not require live Gemini calls or a full test run.

## Build, Test, and Development Commands

- `python -m venv .venv && . .venv/bin/activate`: create and enter a local virtual environment.
- `pip install -e ".[all,dev]"`: install the package with optional browser/image support and maintained development gates.
- `gemini-mcp-function`: run the default 17-tool function surface locally; `gemini-mcp-server` with no `GEMINI_TOOLS` selects the same surface.
- `gemini-mcp-account`: run the separate 17-tool account surface for explicitly requested account work.
- `GEMINI_TOOLS=core python -m src.server` or `GEMINI_TOOLS=all python -m src.server`: exercise explicitly selected legacy compatibility profiles.
- `gemini-mcp-onboarding`: start the installed stdio server and call an auth-free text tool; live chat/image subcommands require `--allow-live-account`.
- `python -m ruff check src tests scripts && python -m mypy src scripts`: run the maintained static correctness gates.
- `python -m pytest -q`: run the complete offline test suite.
- `python scripts/run_contract_checklist.py`: run the targeted architecture/distribution contract gate.
- `python scripts/smoke_profiles.py && python scripts/smoke_mcp_protocol.py`: verify exact representative tool surfaces and both modern/legacy stdio list/call paths without live Gemini calls.
- `python scripts/package_release.py --outdir dist`: build wheel/sdist/skill assets; package CI must also run the built wheel through one isolated `uvx ... gemini-mcp-onboarding` command.
- `python scripts/run_live_canary.py --output /tmp/gemini-web-canary.json`: verify the refusal/report path without network access; live execution additionally requires the explicit flag, two opt-in variables, and a dedicated test account.
- `mcp dev src/server.py`: inspect the server with MCP Inspector after installing `mcp[cli]`.

## Testing Guidelines

Write pytest tests as `tests/test_*.py`, with test names that describe the behavior under contract. For tool-surface changes, assert both registration and MCP annotations, and update `evaluations/gemini_web_mcp_contract.xml` when user-visible capabilities or safety metadata change. Artifact-producing changes must cover stable identity, remote/local/queued/empty/failed state, save verification, backend evidence, and primary/compact parity where both surfaces expose the workflow. Public-onboarding changes must parse every checked-in client config, call a real auth-free text tool over stdio, and verify local image artifacts independently from response prose. Reverse-engineered RPC changes belong in `src/infrastructure/rpc_contracts.py`; every registered parser needs fixture cases for success, empty, rejection, and changed shape, and every mutation service must return read-back verification status. For code/protocol changes, run Ruff, Mypy, the offline suite, and the targeted contract checklist before handing off; add the profile/protocol or clean-wheel smokes for entrypoint, tool-surface, package, or release changes. Live compatibility probes are never part of PR CI: keep their report within `compatibility/live-canary-report.schema.json`, omit raw responses/account content/credentials/session identifiers, and use only the `gemini-live-canary` environment backed by a dedicated account.

## Notebook and Task verification

Keep ordinary Notebook chat (`gemini_ask` with `notebook_ids`, `execution_mode=chat`) separate from `gemini_run_task` (`execution_mode=task`). Validate the function/account catalogs, union destructive annotations, default Notebook source view, bounded Task metadata and legacy project-view chat moves. Do not turn an unrecognized state or an account switch into success.

For authorized local tests, use a synthetic Notebook whose source contains a marker absent from the query. Verify source retrieval independently, submitter disconnect, the original operation handle, exact completed reply identity, a full verified local answer artifact, automatic save/cleanup before the caller requests the result, and post-restart local result access. A Task answer needs both its same-chat completed or failed terminal goal and the exact completed candidate; failed answers remain failed after caching; ordinary Notebook Chat must not create a Task goal. Cover crash recovery between answer completion and file save. Status reads must not regenerate or read full chat text. Do not mark user Notebook content mutations verified from response prose; independently read back the exact resource metadata or source content. Task schedules, rich steps, app confirmations and ProcessSession experiments require separate evidence; Chat scheduled-action checks do not cover them.

Keep the isolated ownership ledger for authorized local browser tests until remote teardown is verified. When a synthetic test stops at a permission card, tear down its exact owned Task and independently verify absence of both the goal and its conversation.

See [Notebook, Chat and Task workflows](notebook-task-workflows.md) and the latest [development observations](development-status.md) for implemented scope versus live evidence.


Task browser checks cover initial full-frame coverage, raw pixel format, vertical scroll, input release on failure, stale-frame refusal, claimed-action deduplication, pre-dispatch-only connection retries, account/VM/turn isolation, idle/shutdown closure, and no persistent VM credentials. Schedule checks include all six recurrence shapes, nanosecond-preserving UTC normalization, pre/post/CAS account fences, bounded failure/no-op output, complete-list deletion proof and owned-thread lifecycle. Live checks must use explicit synthetic resources and independently verify teardown.
