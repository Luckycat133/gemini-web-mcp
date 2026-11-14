# Reviewed source baseline

Review date: 2026-11-14. The active refactor is PR #47,
`codex/gemini-tools-polish-20261113`, source SHA
`5c9f53087398f614958acf06d3c9d14f54958c52`. Observed main was
`ec12c1a5b4953728aac666eeeb95435a9fcc18a7`. Do not substitute main for this branch.

The source package and its development Skill declare 0.2.1. Published v0.2.2 is
a distinct interface/storage baseline, not permission to publish a downgrade or
reuse a tag. The earlier 2026-11-07 plan used `d5dbb40` / 0.2.2; its goals and case
IDs remain, while paths and implementation status require this rebase.

## Inspected owners

Function/Account entrypoints and catalogs; SDK adapter; MCP fingerprint builder
and tests; protocol smoke; background dispatch; operation reads; operation schema;
development Skill/references; integration notes; package entrypoints and tests.
This is a targeted contract/code review, not an assertion that every runtime line
or every Gemini action was revalidated.

## Evidence

- Owner-reported local fingerprint/contract/protocol success: accepted as a local
  report, without assuming an unpushed tree equals the reviewed SHA.
- [CI 43433504485](https://github.com/Luckycat133/gemini-web-mcp/actions/runs/43433504485): passed; includes offline 3.11/3.12, targeted contracts, profiles, stdio, clean package/onboarding and Skill install.
- [CodeQL 43433504654](https://github.com/Luckycat133/gemini-web-mcp/actions/runs/43433504654): passed.
- Source inspection establishes current contracts, not new account entitlement.
  No Google account operations or paid generation are authorized by this review.

## Findings driving the update

1. Existing Skill contained obsolete release/branch and target-topology directions.
2. The retained plan.json used version 0.2.2 and old owner paths while the source
   Skill used 0.2.1; roadmap lost package headings needed by plan.py. This is a
   separate planning-integrity defect, not a failed MCP fingerprint gate.
3. Released Account/create/state/cancel contracts differ from the new branch;
   missing Canvas/Library/sharing/ZIP actions cannot be inferred from registry
   fixtures or successful new tests.
4. Ordinary sessions, detached workers, status/result, Web/API video and script/
   native-artifact outputs have distinct boundaries that must remain explicit.

Use [architecture.md](architecture.md), [roadmap.md](roadmap.md) and
[validation.md](validation.md) as current instructions. Re-check after rebase.
