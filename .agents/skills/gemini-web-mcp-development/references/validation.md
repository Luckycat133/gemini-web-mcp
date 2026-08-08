# Validation and Release Evidence

Load this reference for bug fixes, shared-service changes, MCP contracts, workflow edits, packaging, skills, compatibility probes, or releases.

## Principle

Use the cheapest deterministic test that proves the changed contract, then run broader gates. Fixture-test reverse-engineered behavior; reserve live Gemini access for the explicitly gated canary.

## Change Matrix

| Change | Minimum evidence |
| --- | --- |
| pure parser/helper | focused success/empty/malformed/changed-shape tests |
| client lifecycle | real-suspension concurrency, cancellation, reset, retry |
| session behavior | create/send/list/reset-one/reset-all/not-found/race |
| compatibility text | structured/text agreement, matching and contradictory states |
| remote mutation | accepted, verified, read-back error, mismatch/still-present tests |
| shared service | characterization plus primary/compact semantic parity |
| artifact/media | URI/local/queue/empty/timeout/save/metadata/backend evidence |
| tool/schema/profile | call test, annotations, manifest, snapshot/evaluation update |
| workflow | repaired expression/context contract plus actual Actions startup |
| package/entrypoint | wheel/sdist build, clean install, resources, `pip check`, stdio |
| skill | validator, byte parity, direct repository install, repository tests |
| live compatibility | explicit opt-in, dedicated account, schema-valid sanitized report |

## Focused Gem/Workflow Review

For Gem mutation presentation and the live-canary context regression, run:

```bash
python -m pytest -q \
  tests/test_manage_gem_verification_contract.py \
  tests/test_ci_contracts.py \
  tests/test_development_skill.py
```

Gem tests must cover mapping-backed list entries, whitespace-only required input, verified success, missing mutation ID, read-back absence/error/mismatch, and delete-still-present evidence.

The workflow contract must assert the repaired job-level report path uses an allowed context and that the invalid `${{ runner.` expression does not reappear at job scope.

## Maintained Repository Gates

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
git diff --check
```

Do not claim a gate ran unless it actually ran in the checkout or CI.

## Test-Only FastMCP Shim

`tests._fastmcp_shim` is a narrow registration/dispatch double for management-handler branch tests. It intentionally does not prove MCP SDK validation, output schemas, protocol negotiation, or installed-product behavior.

Always retain the real checks:

```bash
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
```

## Primary/Compact Parity

Compare semantic results rather than exact prose:

- error code/retryability;
- operation state;
- lifecycle/cleanup state;
- normalized model/backend evidence;
- artifact identity/verification;
- pagination/truncation;
- mutation verification outcome.

## RPC and Parser Fixtures

Cover normal success, empty result, rejection, partial response, malformed body, optional/reordered fields, and a changed shape that produces `UPSTREAM_CHANGED` rather than silent emptiness. Compare semantic payloads, not whitespace.

## Mutation Verification

For create/update/delete:

1. parse the returned identifier;
2. read back by ID and/or authoritative registry;
3. distinguish request acceptance from target-state observation;
4. return the verification method/status;
5. render success only for positive terminal evidence;
6. retain actionable warning/partial/failure text otherwise.

## Workflow Validation

Repository text contracts catch known context regressions, but GitHub Actions startup is additional evidence. After a workflow edit, inspect whether jobs were actually created and whether each intended gate ran. A zero-job parse failure is not a passing workflow.

Job-level `env` expressions may only use contexts valid at that YAML location; step-only contexts such as `runner` must stay inside steps.

## Package and Protocol Smoke

For package/release work:

```bash
python scripts/package_release.py --outdir dist
python -m venv /tmp/gemini-web-mcp-wheel-test
/tmp/gemini-web-mcp-wheel-test/bin/pip install dist/*.whl
/tmp/gemini-web-mcp-wheel-test/bin/pip check
cd /tmp
/tmp/gemini-web-mcp-wheel-test/bin/python "$OLDPWD/scripts/smoke_installed_wheel.py"
/tmp/gemini-web-mcp-wheel-test/bin/python "$OLDPWD/scripts/smoke_profiles.py"
/tmp/gemini-web-mcp-wheel-test/bin/python "$OLDPWD/scripts/smoke_mcp_protocol.py"
uvx --from "$OLDPWD"/dist/*.whl gemini-mcp-onboarding
```

Run installed-product checks outside the source checkout.

## Skill Validation

```bash
skills-ref validate .agents/skills/gemini-web-mcp-development
skills-ref validate .codex/skills/gemini-web-mcp-development
diff -ru .agents/skills/gemini-web-mcp-development \
  .codex/skills/gemini-web-mcp-development
npx --yes skills@1.5.21 add "$PWD" \
  --skill gemini-web-mcp-development --agent codex --copy --yes
```

Keep `SKILL.md` below 500 lines and detailed guidance in one-level `references/` files.

## Live Canary

Live runs require the CLI flag, repository enable variable, dedicated-account variable, and protected environment credentials. Persist only schema-allowed diagnostics; omit raw responses, account/chat content, credentials, URLs, titles, and session identifiers.

The current first live target is read-only capability compatibility. Do not describe fixture/workflow tests as live observation.

## Release Checklist

- version/tag/wheel/sdist/skill/changelog/docs agree;
- static, full test, targeted contract, skill, profile, protocol, and package gates pass;
- workflow jobs actually start and finish;
- clean-wheel and isolated onboarding pass;
- downloaded assets are revalidated;
- release notes distinguish implemented behavior, expected routing, fixture evidence, and live evidence;
- mutation claims reflect read-back verification.

## PR Evidence Template

```text
Contract or defect:
Root cause:
Implementation boundary:
Primary impact:
Compact impact:
Structured result / verification impact:
Focused tests:
Full/static/contract checks:
Package/protocol/workflow checks:
Live observations:
Remaining uncertainty:
```
