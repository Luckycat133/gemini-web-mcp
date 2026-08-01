# Validation and Release Evidence

Load this reference when implementing a bug fix, changing a tool contract, refactoring shared services, modifying package/release files, or preparing a release.

## Validation Principle

Use the cheapest deterministic test that proves the changed contract, then add broader checks. Unit-test reverse-engineered parsers with fixtures; reserve live Gemini calls for explicit canary/manual verification.

## Change Matrix

| Change | Minimum evidence |
| --- | --- |
| Pure helper/parser | focused unit tests, success/empty/malformed fixtures |
| Client lifecycle | real-suspension concurrency tests, reset/failure/retry tests |
| Session behavior | create/send/list/reset-one/reset-all/not-found/collision tests |
| Tool schema or registration | call test, annotation/profile registration test, manifest/evaluation update |
| Shared service extraction | characterization tests plus primary/compact parity tests |
| Media/artifact | URI/file/empty/timeout/save-failure tests and metadata verification |
| Private RPC adapter | payload builder tests, parser fixtures, rejection/shape-drift tests, read-back verification |
| Package data/entrypoint | wheel/sdist build, clean install, import/start smoke |
| Version/release | metadata/tag/docs/asset consistency check |
| MCP SDK migration | protocol discovery, list-tools schema snapshots, representative calls across clients |
| Skill change | `skills-ref validate`, copy parity, repository test |

## Current Repository Baseline

Use the checkout's virtual environment and commands documented by the repository. The current maintained baseline is:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m py_compile \
  src/tools/annotations.py src/tools/chat.py src/tools/media.py \
  src/tools/file.py src/tools/research.py src/tools/prompts.py \
  src/tools/manage.py src/server.py src/skill_server.py \
  src/client_wrapper.py src/thinking_client.py src/constants.py
git diff --check
```

Run targeted tests before the full suite, for example:

```bash
./.venv/bin/python -m pytest -q tests/test_client_manager.py
./.venv/bin/python -m pytest -q tests/test_chat_session_lifecycle.py
./.venv/bin/python -m pytest -q tests/test_media_tools.py tests/test_tool_helpers.py
```

## Async Concurrency Test Pattern

A valid initialization test must force scheduling while initialization owns the coordinator:

```python
started = asyncio.Event()
release = asyncio.Event()
calls = 0

async def fake_init(**kwargs):
    nonlocal calls
    calls += 1
    started.set()
    await release.wait()

first = asyncio.create_task(manager.initialize())
await started.wait()
others = [asyncio.create_task(manager.initialize()) for _ in range(3)]
await asyncio.sleep(0)
release.set()
await asyncio.gather(first, *others)
assert calls == 1
```

Also test failure followed by retry and reset during initialization. Avoid a fake async function that never awaits; it cannot reveal event-loop blocking.

## Session Contract Tests

At minimum assert:

- IDs remain unique after arbitrary deletion and creation order;
- unknown ID changes no state;
- `chat(session_id=unknown)` returns not-found rather than one-shot fallback;
- reset-one preserves other sessions and the client;
- reset-all is explicit and deterministic;
- expiry/cleanup behavior is consistent between primary and compact adapters;
- concurrent create/send/list/reset operations do not corrupt the store.

## Primary/Compact Parity

Create table-driven tests that call shared services or both adapters and compare:

- result/error code;
- operation state;
- normalized model and backend evidence;
- artifact IDs/URIs/paths;
- session identifiers and state changes;
- pagination metadata;
- mutation verification status.

Text formatting may differ; domain semantics should not.

## Parser and RPC Fixtures

Store sanitized fixtures representing:

- normal success;
- empty result;
- permission/account rejection;
- partial response;
- malformed JSON/body;
- unknown fields and reordered optional fields;
- a changed shape that should return `UPSTREAM_CHANGED` rather than silently empty data.

Payload builder tests should compare semantic JSON/list structures, not fragile whitespace.

## Multimodal Artifact Tests

For each modality cover:

1. remote URI only;
2. successful local save;
3. multiple artifacts;
4. missing media despite successful text response;
5. download/save failure;
6. timeout/queued state;
7. metadata probe available and unavailable;
8. requested/effective/observed backend mismatch.

Use temporary directories. Verify file existence, non-zero size, and modality metadata when a local fixture/tool is available.

## Tool/Profile Contract Checks

For representative profiles, list tools and snapshot names plus important schemas/annotations. Do not snapshot volatile descriptions unless wording is part of the contract.

```bash
GEMINI_TOOLS=core ./.venv/bin/python - <<'PY'
import asyncio
from src.server import mcp

async def main():
    tools = await mcp.list_tools()
    print([tool.name for tool in tools])

asyncio.run(main())
PY
```

Repeat for `model`, `history`, `account-read`, `scheduled-admin`, and `all` when those surfaces are changed.

## Package Smoke

For packaging or release work:

```bash
rm -rf dist build *.egg-info
./.venv/bin/python -m build
python -m venv /tmp/gemini-web-mcp-wheel-test
/tmp/gemini-web-mcp-wheel-test/bin/pip install dist/*.whl
/tmp/gemini-web-mcp-wheel-test/bin/pip check
/tmp/gemini-web-mcp-wheel-test/bin/python - <<'PY'
import importlib.metadata
import src.server
import src.skill_server
print(importlib.metadata.version("gemini-mcp-server"))
PY
```

Then start/list tools through the installed console entrypoints. Check that package data such as default prompts, docs/evaluations intended for distribution, and skill resources actually exist in the wheel/sdist.

## Target Static Gates

Once the dependencies are declared in the development extra, CI should run:

```bash
python -m ruff check src tests
python -m mypy src
python -m pytest -q
python -m build
python -m pip check
```

Do not claim these gates ran if the current checkout does not install them. Add them to `pyproject.toml` and CI in the same work package that makes them required.

## Skill Validation

```bash
skills-ref validate .agents/skills/gemini-web-mcp-development
skills-ref validate .codex/skills/gemini-web-mcp-development
diff -ru .agents/skills/gemini-web-mcp-development \
  .codex/skills/gemini-web-mcp-development
```

Keep `SKILL.md` below 500 lines and detailed guidance in focused one-level `references/` files.

## Live Compatibility Canary

Live tests must be opt-in and use a dedicated test account. A canary should record only diagnostic metadata needed to reproduce drift:

- repository commit;
- Python, MCP SDK, and `gemini-webapi` versions;
- locale and Web build label when available;
- requested capability/model;
- terminal operation state;
- parser/verification stage;
- sanitized error code.

Candidate canary workflows:

1. text call with each supported alias;
2. one image generation and artifact verification;
3. short media request where account capability permits it;
4. Deep Research plan/start/status using a fixed harmless query;
5. history metadata list/read on canary-created content;
6. scheduled-action create/read/delete with a unique marker;
7. notebook list/move/read-back on canary content.

The live workflow should clean up its own marked artifacts and open/update an actionable compatibility issue on sustained failure.

## Release Checklist

- version source, tag, wheel metadata, README commands, changelog, and asset names agree;
- unit/behavior/parity tests pass;
- static gates pass where configured;
- wheel/sdist clean-install smoke passes;
- MCP list/call smoke passes for representative profiles;
- both skills validate and mirrored copies match;
- release artifacts contain expected files;
- live canary status is reported separately from offline CI;
- release notes distinguish implemented contract, expected routing, and observed live behavior.

## PR Evidence Template

```text
Contract/defect:
Implementation boundary:
Primary surface impact:
Compact surface impact:
Structured result/artifact impact:
Tests run:
Package/protocol checks run:
Live Gemini observations:
Known uncertainty / next dependency:
```
