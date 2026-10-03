# Verification by changed boundary

Use the checkout's `docs/agent-verification.md` for maintained commands and
release gates. Select evidence for the actual change:

| Change | Relevant checks |
| --- | --- |
| Skill text or UI metadata | Skill validation, references, real tool/entrypoint names, example schemas and realistic routing tasks |
| Service/domain/RPC | focused regression, source gates, related compatibility behavior; RPC shape fixtures |
| Surface or protocol | schemas, annotations, catalogs and real stdio list/call on affected surfaces |
| Persistence/recovery | real SQLite migrations, cross-process claims, restart, retained identifiers, expiry and cancellation evidence |
| Artifact handling | requested kind, independent save/decode/probe, partial/queued paths, identity and downstream handoff |
| Packaging/release | source gates, complete bundles, clean installed wheel, resources/entrypoints, protocol and isolated onboarding |

Documentation-only changes do not need a live account run or the complete
repository suite. New tests should verify observable behavior or an actual
contract, rather than matching headings, prose or sample counts.

## Commands

From the checkout's development environment:

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
git diff --check
```

For changed Skills:

```bash
skills-ref validate .agents/skills/<skill-name>
```

Check frontmatter/UI consistency, resolvable references, real catalog names,
example arguments against registered schemas, bundle/source parity and direct
installation. Test positive, near-miss and mixed-intent requests independently
when guidance changes substantially. Static fixtures do not measure model
trigger accuracy; an independent handoff checks decisions in its own scope.

For distribution work:

```bash
python scripts/package_release.py --outdir dist
python scripts/check_version_consistency.py --artifacts-dir dist
```

Run `scripts/smoke_installed_wheel.py` outside the checkout in a clean wheel
environment, plus isolated `uvx` onboarding. Derive version/ref from package
metadata and the reviewed checkout. Preserve published tags.

## What the evidence proves

Fixtures and `tests._fastmcp_shim` verify local contracts. Real MCP stdio checks
verify transport and registration; clean installation verifies the shipped
product. Agent tasks verify selection, continuation and artifact use. None of
these alone establishes current Gemini availability or entitlement.

Current provider claims require authorized live calls through the MCP surface
being evaluated. Keep public frontend/request evidence separate from generated
artifact acceptance. Track returned resource IDs and account for disposable
test outputs within the authorized scope. Store private responses, account IDs,
credentials, state databases and generated media outside commits.

The full live canary requires `--allow-live-account`, both canary opt-in
variables and a dedicated account/environment; see `scripts/run_live_canary.py`.
Bounded everyday-account tests are separate evidence. Read current dated
observations in `docs/live-ui-coverage.md` and `docs/development-status.md`
rather than copying a historical result into Skill instructions.

Model-backed evaluation uses `scripts/run_mcp_builder_evaluation.py` with an
explicit provider/model and optional dependency. Vendored MCP-builder resources
remain upstream-owned; the project bridge owns SDK compatibility.
