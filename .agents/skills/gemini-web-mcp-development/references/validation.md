# Testing and Evidence

Use the narrowest test that proves the changed contract, then add the broader gates required by the affected boundary. Keep fixture, repository, protocol, installed-product, skill-distribution, and live evidence distinct.

## Evidence Levels

1. **Unit/fixture evidence** — pure helpers, parsers, services, fake clients, and sanitized response fixtures.
2. **Repository contract evidence** — Ruff, Mypy, full pytest, focused architecture contracts, snapshots, and documentation assertions.
3. **Protocol evidence** — real MCP stdio discovery, list, and representative calls against both entrypoints and supported protocol modes.
4. **Installed-product evidence** — wheel/sdist/runtime-skill build, clean environment, resources, entrypoints, `pip check`, and isolated `uvx` onboarding.
5. **Skill-distribution evidence** — Agent Skills validation, direct repository install, package contents, ClawHub dry-run/audit evidence when the runtime skill changes.
6. **Live Gemini evidence** — explicitly authorized calls against the current account and Web deployment; release-grade claims require the dedicated-account baseline.

A large test count does not replace protocol, installed-product, skill-distribution, or live evidence.

## Fast Local Sequence

```bash
python -m py_compile <changed-python-files>
python -m pytest -q <focused-tests>
python -m ruff check <changed-python-files-and-tests>
python -m mypy <changed-source-files>
git diff --check
```

For a remote mutation, cover at least:

- accepted and positively verified;
- accepted but not observed;
- incomplete pagination or unavailable verification;
- read-back error;
- mismatch or still-present state;
- blank/invalid identifiers before network access;
- structured result and compatibility-text agreement.

For a mapping/object compatibility change, cover objects, mappings, mixed nesting, missing optional fields, and unchanged legacy presentation.

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

The latest `main` baseline at the time of this Skill update is commit `d3145a3be45745523e7483b18835b4800da80ab5`; its CI and CodeQL completed successfully. Do not hard-code this commit as a permanent baseline—always inspect the current tree and current runs.

Do not encode a volatile passing-test number in the Skill or README. Cite the actual workflow run when reporting hosted evidence.

## Tool, Schema, Profile, and Adapter Changes

Verify:

- exact registration in every affected profile;
- input schema, annotations, output schema, and representative call;
- structured content validates against the generated schema;
- stable error codes, operation state, pagination, lifecycle, and verification fields;
- primary/compact semantic parity where both expose the workflow;
- manifest, evaluation, docs, and client examples are intentionally synchronized;
- no duplicate execution logic was added to a compact adapter.

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
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
uvx --from "$SOURCE" gemini-mcp-onboarding
```

The Python package and both public Skills share one active version, currently `0.2.0`. Verify their frontmatter, the changelog section, runtime metadata, release tag, and generated artifact names together. Rewritten history and release refs use the canonical project version.

## Development Skill Changes

The repository has one development-skill source under `.agents/skills`.

```bash
skills-ref validate .agents/skills/gemini-web-mcp-development
python -m pytest -q tests/test_development_skill.py tests/test_skill_packaging.py
```

Then install it directly from the repository and byte-compare the installed copy with `.agents/skills/gemini-web-mcp-development`.

Required checks include:

- frontmatter and progressive disclosure;
- no machine-specific paths;
- no obsolete `.codex` mirror;
- canonical package/runtime-Skill/development-Skill version parity;
- settled directions are not listed as owner decisions;
- every referenced command is copyable.

## Runtime Skill and ClawHub Changes

When `.agents/skills/gemini-web-mcp` changes:

- keep its version aligned with `pyproject.toml`, the development Skill, and the changelog;
- validate the exact published file set;
- run `tests/test_skill_packaging.py` and any audit-specific contract;
- confirm the ClawHub bundle license and metadata;
- perform a publish dry-run before any real publish;
- preserve the explicit browser-Cookie authentication boundary;
- do not claim that a dry-run or local archive is already published.

The canonical repository version is `0.2.0`. Inspect ClawHub before claiming that the repository version has been published there.

## Workflow Changes

A repository text assertion is useful but insufficient. Confirm that GitHub Actions actually created and executed the intended jobs.

Check:

- expression contexts are valid at their YAML location;
- all expected jobs exist;
- stale runs are cancelled according to concurrency rules;
- each diagnostic step ran;
- artifacts were uploaded when required;
- skipped live jobs are reported as skipped, not as live success;
- the final head, not an earlier branch commit, owns the cited checks.

A zero-job parse/startup failure is not a passing workflow.

## Live Evidence Boundary

The authorized 2026-08-08 run is bounded evidence for Cookie initialization, text, sessions, typed history, and verified chat cleanup. It does not prove media, files, URLs, Deep Research, account mutations, account tier, locale, or Web build.

### Dedicated Full Baseline — Required Before a New Public Package Release

1. initialize the dedicated account and record bounded environment metadata;
2. run read-only capability probes and capture Web build/locale when observable;
3. verify temporary one-shot text;
4. verify session create/send/reset across primary and compact surfaces;
5. verify a local image artifact;
6. verify video and music artifact state, URI/file, MIME, size, and duration when available;
7. verify local-file and URL analysis;
8. verify Deep Research start, terminal/timeout state, preserved IDs, recovery, and report retrieval;
9. verify history list/search/read/export and marked chat deletion with complete fresh metadata read-back;
10. perform one disposable scheduled, Gem, or Notebook mutation with read-back;
11. account for and clean up every generated resource by returned ID.

Classify entitlement absence separately from upstream drift or implementation failure.

## Live Result Record

Retain only bounded evidence:

```text
commit:
client and protocol:
dependency versions:
account tier / locale / entitlement class:
Web build when observable:
tool and arguments without credentials or private content:
operation state:
error code / retryability:
verification status and method:
artifact IDs, paths/URIs, MIME, size, dimensions/duration:
requested / effective / observed backend:
cleanup result:
```

Never treat response prose alone as proof of a mutation, artifact, or backend.
