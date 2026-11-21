# Validation and MCP v2 Fingerprint Review

## Evidence already established

For reviewed SHA `5c9f53087398f614958acf06d3c9d14f54958c52`, remote CI run
43433504485 and CodeQL run 43433504654 succeeded. CI includes complete Python
3.11/3.12 offline suites, targeted contracts, profile/stdio smoke, clean-wheel
and isolated onboarding, Skill validation and direct repository installation.
The owner also reports local fingerprint/contract/protocol passes. Treat those
as local evidence until their working-tree revision is identified; do not erase
local changes or assert the local and remote trees are identical.

No additional runtime or Gemini acceptance follows from this documentation
review. Reuse unchanged gates at the same revision/environment; rerun affected
gates after behavior changes. Do not hardcode volatile passing-test counts.

## Fingerprint contract: what is and is not checked

`scripts/snapshot_mcp_v2_contract.py` imports each surface in a credential-stripped
subprocess with browser loading and refresh disabled. It records format 1,
SDK major 2 and these five keys:

- `primary:model`: `src.server`, explicit model profile;
- `compact`: `src.skill_server`, model profile;
- `account`, `function`, `assist`: their dedicated modules.

Each tool entry contains name and SHA-256 of canonical input schema, output
schema and annotations. Tools are sorted by name. Descriptions, original
registration order, server instructions, semantic behavior and provider
availability are NOT covered by this hash. Test those separately.

`tests/fixtures/mcp_v2_tool_contract.json` is the reviewed expected snapshot;
`tests/test_mcp_sdk_v2.py` compares it and validates structured results. The
adapter's conservative SDK-label compaction does not authorize removal of schema
constraints or changing the business envelope to force a match.

### Deliberate refresh procedure

1. Capture checkout SHA/dirty diff, Python, `mcp`, `mcp-types`, Pydantic and provider
   SDK versions. Identify whether drift comes from code, dependencies or compaction.
2. Build a temporary candidate, NOT directly over the checked-in fixture:

```bash
CANDIDATE=$(mktemp)
python scripts/snapshot_mcp_v2_contract.py > "$CANDIDATE"
diff -u tests/fixtures/mcp_v2_tool_contract.json "$CANDIDATE"
```

3. For each changed digest, inspect actual old/new schemas and annotations. State
   changed defaults, required fields, unions, side effects and consumer impact.
   An unchanged digest is not evidence that descriptions or runtime stayed equal.
4. Only for reviewed intentional changes, copy the candidate to the fixture;
   otherwise fix the regression. Never regenerate every golden after a failure.
5. Run SDK adapter/schema tests, both surface tests, profile/protocol smoke and
   the affected business tests. Broader runtime/release changes use the full gates.

For documentation-only changes do not regenerate MCP fingerprints.

## Protocol and domain-result semantics

Real smoke uses SDK Client `mode=auto` (2026-07-28) and `mode=legacy`
(2025-11-25), catalog discovery and representative auth-free calls. Test the
unset-profile Function default separately from explicit model/core profiles.

The current content-block tools use `structuredContent.result[...]`; domain-backed
blocks carry `_meta.domain_result`. The smoke explicitly tests some
`INVALID_ARGUMENT`/`AUTH_REQUIRED` rejections as protocol-complete non-error
responses with domain `ok=false`. Do not conflate transport completion with job
completion, or silently switch to direct `DomainResult` output/isError behavior.
If improving this design, add a versioned migration and positive/negative tests.

Source-level unit tests and `tests._fastmcp_shim` do not prove transport. Protocol
smoke is not a real generation, account read, or downstream file-use test.

## Boundary-specific checks

| Change | First checks / required evidence |
| --- | --- |
| Skill/docs/plan | development Skill tests, plan integrity and checker regressions, references, metadata, whitespace |
| Catalog/schema/annotations | `test_function_surface.py`, `test_account_surface.py`, `test_mcp_sdk_v2.py`, profile and real stdio smoke |
| Detached work / stores | `test_background_operations.py`, `test_operation_store.py`, `test_operations.py`, relevant media/Task/Research tests, real multi-process fault cases |
| Exact session recovery | `test_session_sdk_recovery.py`, `test_session_cleanup_guards.py`, relevant chat tests |
| Task answers/files | `test_task_jobs.py`, `test_task_files.py`, input/setup/confirmation regressions; goal, original reply and attachment inventory |
| Cleanup / account fence | cleanup store/runtime, owned-cleanup, account-read/mutation fencing, schedule guards and identity migrations |
| Browser actions/preferences | browser session/turn/retry/action-store and preference tests; no auto-approval or input replay |
| Release migration | old input/output adapters, old database fixtures, explicit refusal/rollback; current tests alone cannot cover deleted capabilities |
| Package/client | clean installed wheel, all advertised entrypoints, resources, protocol eras and isolated onboarding |

Re-read `scripts/run_contract_checklist.py` for maintained exact filenames before
invoking them. Never invent test paths for removed services.

## Documentation and plan checks

```bash
python -m pytest -q tests/test_development_skill.py
python .agents/skills/gemini-web-mcp-development/scripts/plan.py check
python .agents/skills/gemini-web-mcp-development/scripts/test_plan.py
skills-ref validate .agents/skills/<skill-name>
git diff --check
```

Use the actual Skill name in the validator command. The plan checker verifies
references, case ownership, version agreement and evidence-file integrity; it
cannot establish runtime truth. Its `--complete` target means the whole declared
backlog, not merely current dual-surface protocol smoke. Rebase old private
progress without inventing new passing receipts.

## Full gates for broad runtime changes / release readiness

```bash
python -m ruff check src tests scripts
python -m mypy src scripts
python -m pytest -q
python scripts/run_contract_checklist.py
python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
python scripts/package_release.py --outdir dist
python scripts/check_version_consistency.py --artifacts-dir dist
git diff --check
```

Run installed-wheel smoke outside the checkout, including `pip check`, real
entrypoints/resources and both protocol eras. Test isolated source installation:

```bash
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
uvx --from "$SOURCE" gemini-mcp-onboarding
```

Do not install from the superseded 20261101 branch in new examples. Current
0.2.1 metadata reflects the development source, not permission to overwrite a
published release. Preserve all published tags, including v0.2.0/v0.2.1/v0.2.2.

## Recovery acceptance beyond schema hashes

Exercise server disconnect and actual worker termination separately, at reserve,
submission, locator capture, save, manifest and cleanup boundaries. Assert no
additional provider start, correct account and original reply identity, protection
of uncertain results, and verification of complete local files after restart.
Missing/corrupt files stay partial/unavailable; source loss does not enable replay.

Status must not become a hidden result read. Result may resume observation and
cleanup under the stored policy; its annotations and tests must reflect that.
Ordinary sessions are process-local until a new durable-session feature passes
A10. Same-handle reuse is not request-content equivalence. No public cancel is
claimed. Retention tests include protected-operation exceptions and account change.

## Agent-use, live and installed acceptance

Maintain positive trigger evaluations and near-miss negative cases, plus real
end-to-end tasks described in tool-design.md. Test verified artifact downstream
handoff, not just printed paths. Optional protocol-native MCP Tasks would require
separate negotiation; they are not Gemini's native Task or detached workers.

A bounded 2026-08-08 or later dated account run proves only its own scope. Current
claims need explicitly authorized bounded MCP calls on the evaluated commit.
Never expose credentials/raw private content in reports, exhaust real quota for
a negative test or silently consume the paid video API. Track all owned resources
and verify cleanup; do not delete unrelated user content.

The full canary's dedicated-account/environment and opt-in gates remain intact.
A skipped canary is not passed live evidence. Report fixture, offline, protocol,
package, agent and live results separately, with command, revision and dependencies.

## Primary standards checked for this review

- [MCP 2026-07-28 tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools): structured-content/schema and tool semantics; check the matching legacy version too.
- [Agent Skills specification](https://agentskills.io/specification): frontmatter, progressive disclosure and portable relative references.

These standards inform the review; the repository's explicit current compatibility
behavior must still be migrated deliberately rather than silently rewritten.
