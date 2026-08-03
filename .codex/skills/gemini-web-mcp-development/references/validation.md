# Validation and Release Evidence

Load this reference when fixing a bug, changing a tool contract, refactoring shared services, modifying package/release files, changing a skill, or preparing a release.

## Validation Principle

Use the cheapest deterministic test that proves the changed contract, then add the broader repository gates appropriate to the affected boundary.

Unit-test reverse-engineered parsing with sanitized fixtures. Keep PR and normal CI offline. Report live Gemini observations only when the separately gated canary or an explicitly authorized manual workflow actually ran.

## Change Matrix

| Change | Minimum evidence |
| --- | --- |
| Pure helper/parser | focused unit tests; success, empty, malformed/rejected fixtures where applicable |
| MCP compatibility text | text assertion plus `_meta.domain_result` code/state assertion; contradictory-code regression |
| Client lifecycle | real-suspension concurrency, cancellation, reset, failure, retry, and retirement tests |
| Session behavior | create/send/list/reset-one/reset-all/not-found/collision/concurrent-send tests |
| Tool schema/registration | representative call, annotations/profile test, golden schema review, manifest/docs update |
| Shared service extraction | characterization tests plus primary/compact semantic parity |
| Media/artifact | URI/local/queued/empty/timeout/save-failure tests and metadata/backend verification |
| Long operation/stream | delta/cumulative/mixed normalization, cancellation, timeout, continuation and terminal-state tests |
| Private RPC adapter | registry/payload tests, parser fixtures, rejection/shape-drift, mutation read-back verification |
| Package resource/entrypoint | wheel/sdist build, clean install outside checkout, import/start/profile/resource smoke |
| Version/release | metadata/tag/docs/asset consistency plus downloaded-asset revalidation |
| Public onboarding/config | parse examples, auth-free stdio call, credential stripping, isolated `uvx` install, artifact verification |
| MCP SDK/protocol | discovery, modern/legacy negotiation, list/call, generated schemas, structured output golden |
| Live canary | refusal path offline; schema-valid sanitized report and explicit live evidence only when opted in |
| Development skill | `skills-ref`, mirror parity, freshness assertions, direct repository installation |

## Maintained Repository Gates

Use the checkout's environment. Run focused tests first, then:

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
git diff --check
```

The project development extra declares the required lint/type/test/build dependencies. Do not describe Ruff or Mypy as future gates.

CI additionally separates representative contract, protocol, installed-product, skill, and release checks so one failure is diagnosable without reading one monolithic job.

## Focused Regression Pattern

A valid bug fix should prove all relevant layers:

```text
input / triggering condition
-> shared service or adapter result
-> domain code and operation state
-> compatibility text
-> structured MCP content / _meta.domain_result
-> state or artifact side effects
```

For an MCP failure, assert at minimum:

```python
assert content.text.startswith(expected_error_code)
assert content.meta["domain_result"]["error"]["code"] == expected_error_code
assert content.meta["domain_result"]["meta"]["operation_state"] == expected_state
```

When preserving legacy prose, it need not start with a code. But if it explicitly advertises a known domain code, that code must match the structured result.

## Async Concurrency Test Pattern

A lifecycle test must force scheduling while initialization owns the shared attempt:

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

Also test:

- one waiting caller cancels without cancelling the shared attempt;
- initialization failure permits retry;
- reset during initialization rejects stale completion;
- reset retires/ closes the detached client;
- another event loop does not silently await an incompatible task.

A fake async function with no internal `await` cannot reveal event-loop blocking.

## Session and Chat Contract Tests

At minimum cover:

- IDs remain unique after arbitrary deletion/creation order;
- unknown send/reset changes no state and returns `SESSION_NOT_FOUND`;
- `chat(session_id=unknown)` never becomes a one-shot request;
- reset-one preserves other sessions and the current client;
- reset-all is explicit;
- normal and streaming sends serialize per session;
- async reset waits for an in-flight send;
- expiry and cleanup lifecycle agree across adapters;
- valid-session authentication/network/upstream errors retain their real code in text and structured metadata.

## Primary/Compact Parity

Use table-driven tests or shared fixtures to compare:

- `ok`, error code, retryability, and operation state;
- normalized model and requested/effective/observed backend;
- session IDs, lifecycle state, and cleanup observation;
- artifact IDs, kinds, URIs, paths, and verification;
- pagination/count/truncation metadata;
- mutation verification status;
- long-operation continuation IDs.

Text formatting may differ, except it must not contradict these semantics.

## RPC and Parser Fixtures

For every registered contract retain sanitized fixtures for:

- normal success;
- valid empty response;
- provider/account rejection;
- partial response;
- malformed envelope/body;
- unknown/reordered optional fields;
- changed shape that must report drift instead of silently returning empty data.

Payload builder tests should compare semantic structures. Parser tests should identify transport, envelope, RPC, parser, and verification stage where possible.

## Multimodal Artifact Tests

For each modality cover:

1. remote URI only;
2. successful local save;
3. multiple artifacts and stable identities;
4. input artifact versus output artifact;
5. missing media despite successful response text;
6. queued/running state;
7. timeout/cancellation;
8. download/save/verification failure;
9. metadata probes available and unavailable;
10. requested/request/effective/observed backend mismatch;
11. primary/compact artifact identity parity.

Use temporary directories. Verify file existence, non-zero size, MIME, dimensions for images, and duration for audio/video when the fixture/tool is available.

## Stream and Long-Operation Tests

Current stream tools collect upstream chunks into one MCP result. Assert:

- `delivery=collected`;
- delta chunks append exactly once;
- cumulative chunks emit only new suffixes;
- duplicates and stale cumulative chunks do not duplicate text;
- mixed semantics are reported;
- caller cancellation propagates and closes the upstream iterator;
- timeout cannot be replaced by a cancellation-suppressing late result;
- Deep Research distinguishes plan/running/completed/timed-out and retains continuation identifiers.

## Tool, Profile, and Protocol Contracts

Representative primary profiles and the compact surface have reviewed golden tool names and schema fingerprints. When registration changes:

1. list tools in source tests;
2. inspect the intended diff in the golden fixture;
3. validate generated `outputSchema` against actual `structuredContent`;
4. run both modern (`2026-07-28`) and legacy (`2025-11-25`) client paths;
5. run real stdio list/call smoke for both installed entrypoints;
6. update manifest/docs/examples only for intentional changes.

Do not re-baseline a golden file merely to make CI pass.

## Package and Installed-Product Smoke

For packaging/release work:

```bash
python scripts/package_release.py --outdir dist
python -m venv /tmp/gemini-web-mcp-wheel-test
/tmp/gemini-web-mcp-wheel-test/bin/pip install dist/*.whl
/tmp/gemini-web-mcp-wheel-test/bin/pip check
/tmp/gemini-web-mcp-wheel-test/bin/python scripts/smoke_installed_wheel.py
/tmp/gemini-web-mcp-wheel-test/bin/python scripts/smoke_profiles.py
/tmp/gemini-web-mcp-wheel-test/bin/python scripts/smoke_mcp_protocol.py
```

Run clean-wheel smoke outside the source checkout. It must verify installed origin, resources, all console entrypoints, representative profiles, and an auth-free MCP text call.

Also prove the public one-command path in isolation:

```bash
cd /tmp
uvx --from /absolute/path/to/dist/gemini_mcp_server-*.whl gemini-mcp-onboarding
```

Offline onboarding should report:

- `mode=offline`;
- `credentials_accessed=false`;
- the auth-free manifest text tool;
- a non-empty negotiated protocol version;
- no live Gemini request.

A live image example succeeds only when the returned local artifact stays inside the requested output directory and has path/existence/non-zero/MIME/dimensions plus structured verification evidence.

## Version and Release Checks

`pyproject.toml` is the persisted version source. Use repository scripts to verify:

- runtime version consumers use package metadata;
- tag equals the derived release tag;
- wheel/sdist/skill names and wheel metadata agree;
- public source-install examples use the canonical evergreen source unless a valid tagged asset is intentionally documented;
- stale release assets are rejected;
- historical changelog entries are not rewritten;
- downloaded release assets pass the same install/profile/protocol/onboarding checks before publication.

## Skill Validation and Freshness

```bash
skills-ref validate .agents/skills/gemini-web-mcp-development
skills-ref validate .codex/skills/gemini-web-mcp-development
diff -ru .agents/skills/gemini-web-mcp-development \
  .codex/skills/gemini-web-mcp-development
npx --yes skills@1.5.21 add "$PWD" \
  --skill gemini-web-mcp-development --agent codex --copy --yes
```

Skill contract tests should verify:

- both mirrors contain the same files and bytes;
- `SKILL.md` remains below 500 lines with one-level references;
- no machine-specific path is introduced;
- current foundational modules and P3 direction are named;
- completed P0-P2 work is not described as absent current architecture;
- maintained validation commands match configured tooling.

## Live Compatibility Canary

Normal tests and PR CI stay offline. The maintained canary requires every explicit opt-in and a dedicated environment/account.

Offline validation:

```bash
python -m pytest -q tests/test_live_canary.py tests/test_ci_contracts.py
python scripts/run_live_canary.py --output /tmp/gemini-web-canary.json
```

Without all opt-ins, the CLI must refuse live access. When live execution is deliberately enabled, persisted diagnostics must validate against `compatibility/live-canary-report.schema.json` and must not contain raw responses, exception messages, cookies, session identifiers, chat/account content, titles, or URLs.

Report stages should distinguish:

- dependency/import/setup;
- client initialization/account availability;
- transport/envelope;
- RPC rejection;
- parser completion or shape drift;
- capability result.

Fixture-only work must be labeled not live-observed.

## Release Checklist

- semantic version decision and compatibility policy are explicit;
- version/tag/metadata/docs/assets agree;
- Ruff, Mypy, unit, contract, and `git diff --check` pass;
- representative schema/profile/protocol tests pass;
- clean wheel/sdist and installed resources/entrypoints pass;
- isolated `uvx` onboarding calls the auth-free text tool;
- all checked-in client examples parse;
- both runtime and development skills validate and mirrors match;
- development skill installs directly from the repository;
- downloaded release assets are independently revalidated;
- live canary status is reported separately from offline CI;
- release notes distinguish implemented contract, expected routing, fixture evidence, and live observation.

## PR Evidence Template

```text
Contract or defect:
Root cause:
Implementation boundary:
Primary surface impact:
Compact surface impact:
Structured result / artifact impact:
Focused tests:
Static / repository checks:
Package / protocol / onboarding checks:
Live Gemini observations:
Known uncertainty / next dependency:
```
