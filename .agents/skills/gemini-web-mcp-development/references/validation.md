# Testing and Evidence

Use the narrowest test that proves the changed contract, then add the broader gates required by the affected boundary.

## Evidence Levels

Keep these distinct in every PR:

1. **Unit/fixture evidence** — pure helpers, parsers, services, fake clients.
2. **Repository contract evidence** — full pytest, Ruff, Mypy, snapshots.
3. **Protocol evidence** — real MCP stdio discovery/list/call.
4. **Installed-product evidence** — wheel/sdist, clean environment, resources, entrypoints, `uvx` onboarding.
5. **Live Gemini evidence** — explicitly authorized calls against a dedicated current account.

A higher test count is not a substitute for levels 3–5.

## Fast Local Sequence

```bash
python -m py_compile <changed-python-files>
python -m pytest -q <focused-tests>
python -m ruff check <changed-python-files-and-tests>
python -m mypy <changed-source-files>
git diff --check
```

For the compact-history mapping regression:

```bash
python -m pytest -q tests/test_compact_history_contract.py
```

For a remote mutation, cover at least:

- accepted and positively verified;
- accepted but not observed;
- read-back error;
- mismatch or still-present state;
- invalid/blank identifiers before network access;
- compatibility text and structured result agreement.

## Maintained Offline Gates

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
git diff --check
```

Do not encode a volatile passing-test number into the skill or README. Cite the actual CI run instead.

## Tool, Schema, and Profile Changes

Verify:

- exact tool registration in every affected profile;
- input schema, annotations, output schema, and representative call;
- structured content validates against the generated schema;
- primary/compact semantic parity where both expose the workflow;
- manifest, evaluation, docs, and client examples are intentionally synchronized.

Golden snapshots are reviewed contracts, not files to regenerate automatically after a failure.

## Package and Onboarding Changes

```bash
python scripts/package_release.py --outdir dist
python scripts/check_version_consistency.py --artifacts-dir dist

python -m venv /tmp/gemini-wheel-smoke
/tmp/gemini-wheel-smoke/bin/python -m pip install dist/*.whl
/tmp/gemini-wheel-smoke/bin/python -m pip check

cd /tmp
/tmp/gemini-wheel-smoke/bin/python <checkout>/scripts/smoke_installed_wheel.py
/tmp/gemini-wheel-smoke/bin/python <checkout>/scripts/smoke_profiles.py
/tmp/gemini-wheel-smoke/bin/python <checkout>/scripts/smoke_mcp_protocol.py
```

Also prove the public one-command path outside the checkout:

```bash
uvx --from <wheel-or-reviewed-git-sha> gemini-mcp-onboarding
```

## Skill Changes

```bash
skills-ref validate .agents/skills/gemini-web-mcp-development
skills-ref validate .codex/skills/gemini-web-mcp-development
diff -ru .agents/skills/gemini-web-mcp-development \
  .codex/skills/gemini-web-mcp-development
python -m pytest -q tests/test_development_skill.py tests/test_skill_packaging.py
```

Then install the development skill from the repository and byte-compare it with the source copy.

## Workflow Changes

A repository text assertion is useful but insufficient. Confirm that GitHub Actions actually created the intended jobs. A zero-job parse/startup failure is not a passing workflow.

Check:

- expression contexts are valid at their YAML location;
- all expected jobs exist;
- each diagnostic step ran;
- artifacts were uploaded when required;
- skipped live jobs are reported as skipped, not live success.

## Live Test Ladder

Use a dedicated non-personal account and record the exact commit, dependency versions, locale, Web build when visible, account tier, and client.

### P0 — Must Prove Before a Public Release

1. credential/account initialization;
2. one-shot temporary text call;
3. session create/send/reset across primary and compact surfaces;
4. local verified image artifact;
5. video and music artifact state, URI/file, MIME, size, and duration when available;
6. local file and URL analysis;
7. Deep Research start, terminal/timeout state, preserved IDs, and report retrieval;
8. history list/search/read/export and a marked temporary delete verification;
9. one reversible or disposable scheduled/Gem mutation with read-back;
10. cleanup outcome and no untracked test artifacts.

### P1 — Account/Compatibility Matrix

- at least Codex plus one other MCP client;
- Python 3.11 and 3.12 installed-product paths;
- supported desktop OSes that the project claims;
- multiple model aliases and thinking levels;
- accounts with and without optional media/research entitlements;
- browser-cookie profile diagnostics where supported.

## Live Result Record

For each call retain only bounded evidence:

```text
commit:
client and protocol:
dependency versions:
account tier / locale:
tool and arguments (without credentials):
operation state:
error code / retryability:
verification status:
artifact IDs, paths/URIs, MIME, size, dimensions/duration:
requested / effective / observed backend:
cleanup result:
```

Never treat response prose alone as proof of a mutation or artifact.
