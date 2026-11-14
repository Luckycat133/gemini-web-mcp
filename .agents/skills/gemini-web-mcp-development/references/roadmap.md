# Dual-Surface Development and Acceptance Roadmap

Revision: 2026-11-14. Source baseline: `5c9f53087398f614958acf06d3c9d14f54958c52`.
Read [architecture.md](architecture.md) for current contracts and
[validation.md](validation.md) for the evidence boundary.

## Settled boundaries and current result

One distribution, Function and Account; shared services; explicit handles;
metadata-only recovery stores; MCP-owned ledger cleanup. These are implemented
foundations, not questions to reopen. Both full generation and full content
management remain equal product goals. Tool count is not feature coverage.

The owner reports local fingerprint synchronization, full contract tests and
protocol smoke passing. The reviewed remote SHA also has passing CI/CodeQL:
CI run 43433504485 includes Python 3.11/3.12 suites, contracts, profiles, modern/
legacy stdio, clean-wheel/onboarding and Skill installation. CodeQL run
43433504654 passed. This is revision-scoped evidence, not new live acceptance or
proof that removed 0.2.2 functionality has migrated. Preserve those gates;
do not spend the next milestone rebuilding or repeatedly rerunning unchanged gates.

## Next executable order

| Slice | Work packages | Deliverable and exit condition |
| --- | --- | --- |
| 0. Reconcile integration | P00/P01/D01 | Action-by-action released-to-current matrix; version/storage/entrypoint migration decision; current plan/checker works |
| 1. Shared recovery and references | P02/P03/P04 | Capability/ref contracts; failure-boundary tests; explicit same-handle versus new-intent behavior; no duplicate starts |
| 2A. Generation | G01/G02/G03/G04/G05/G06 | Extend Function, not a third server; multi-reference/continued creation; Web/API separation; real report and file handoff |
| 2B. Management | M01/M02/M03/M04/M05/M06/M07/M08 | Restore missing released actions first; complete lookup/export and resumable exact-target changes |
| 3. Remaining native generation | G07/G08 | Real native file/Canvas/audio/learning artifacts, separately evidenced by kind |
| 4. Installation and combined acceptance | P05/X01/Q01/D01/R01 | Installed client tasks, cross-surface handoff, coverage reconciliation, versioned release candidate |

Independent generation and management slices can proceed in parallel after
shared contracts settle. Migration work must not stall unrelated additive work.
A blocked account check is not a reason to stop all offline implementation, nor
a reason to mark that check accepted.

### Capability disposition required before integration

| Released or existing workflow | Current observation | Required treatment |
| --- | --- | --- |
| Function media background starts | Implemented, including images | Preserve and extend; do not propose async images as new work |
| Task/Notebook Chat, preferences, browser inputs | Implemented branch contracts | Preserve exact identity and effects; do not turn setup into text confirmation |
| Released `gemini-mcp-create` | No current entrypoint | Restore a thin compatible adapter or make an explicitly approved breaking migration |
| Released seven Account `request` facades | Replaced by flat tools under the same command | Test old input/output/annotations separately; name equality is not compatibility |
| Released unified state DB, idempotency/cancel | Different current stores and APIs | Synthetic old-DB migration/refusal/rollback tests; no silent rebind, reset or fake cancel |
| Canvas, Library, public sharing | Not re-exposed by current business catalog | Port service behavior and user-level regressions, or retain explicit open gaps |
| Native Skills ZIP and richer Notebook source actions | Current native management is not proof of every released action | Inventory list/get/update/upload/export actions individually |
| Native Remy / Task / Chat schedule contracts | Several distinct backends and identities | Map trigger/actions and callers, not just surviving RPC rows |
| Ordinary session restart | Process-local today | New opt-in persistence is a future capability, not existing operation recovery |

Record each old entrypoint/action as retained, adapted, planned, explicitly
retired or blocked with evidence. When replacing implementation-detail tests,
port the observable behavior tests first. Do not delete requirements or weaken
checks merely to obtain green CI. The current passing suite remains valid for
its current contracts; it does not answer this separate migration question.

## Work packages retained from the full plan

The IDs and case ownership in `assets/plan.json` remain stable. Rebase old private
progress to this plan revision; never carry accepted status forward automatically.
Implementation and acceptance remain separate. The following sections are
executable backlog requirements, not assertions that every item is unfinished.

### P00 — Reconcile baseline, coverage and evidence

Compare the reviewed branch, current working tree, main and published v0.2.2.
Inventory actual tools/actions and the removed module/test behaviors. Record
source SHA, dependency versions, protocol eras, gate reports and coverage gaps.
Exit A01: every goal has an owner and a current disposition; baseline CI is
credited without presenting historical account checks as today's results.

### P01 — Freeze wire meaning and trustworthy read/write boundaries

Review the five MCP fingerprint surfaces and both protocol eras. Keep the
current wrapper and domain-error semantics explicit. Test false-empty reads,
wrong IDs, union destructive annotations and read-only profile leakage. Resolve
published-client migration, not just branch golden equality. Exit A02/A03:
invalid reads never prove absence; legacy requests have tested compatibility or
an explicit approved migration. Do not overwrite the golden to hide a regression.

### P02 — Discover actual account capabilities and model policy

Reuse current runtime model discovery and policy. Make necessary capability
metadata usable by Function without general account-history privileges. Record
input/action support, policy denial, unavailable and unknown separately. Preserve
explicit selection, Lite quota evidence, body/header consistency and provider
identity. Exit A04/A05: fixture drift and bounded live calls agree; no hidden
paid-API fallback or deliberate real-quota exhaustion.

### P03 — Common resource references and usable artifacts

Extend existing artifact/understanding/media storage rather than recreating
removed `CreationService`. Keep account/provider/kind and exact turn identity,
ordered inputs, input/output lineage and lossless original files. Separate
preview, native output and local conversion. Exit A06/A07: no dropped input or
wrong-account reference; local and supported remote-client handoff are honest.

### P04 — Complete failure-boundary and continuation contracts

Reuse separate OperationStore/CleanupStore and detached workers. Cover loss
before dispatch, after provider submission but before locator persistence,
after save and before cleanup. Same handle does not validate prompt equality.
Define changed-intent rejection/fingerprint policy without storing raw prompts.
Ordinary session persistence is a separate new feature; current `recover_only`
remains process-local until implemented. Cancellation is a future versioned
contract, not a requirement to fabricate success. Exit A08/A09/A10: same-job
recovery never replays generation; storage migration is explicit; persistent
sessions are accepted only after real continuation tests.

### P05 — Interactive setup without protocol pollution

Reuse onboarding/doctor and expose Function/Account choices with config diff,
atomic writes and rollback. TTY setup is separate from MCP stdio; noninteractive
usage remains supported. Exit A11: clean auth-free install works without reading
private content or silently writing credentials.

### X01 — Cross-surface handoff and retention

Use an existing managed resource as generation input, then organize/export the
new artifact. Preserve identity and intentional user works. Task schedules,
active sessions, unresolved replies and unread/unverified native attachments
remain protected. Exit A45/A46: all owned sources accounted for, no unrelated
resource or Notebook deletion, and the downstream consumer uses verified output.

### Q01 — Expanded action matrix and agent acceptance

Port missing user-level tests from released workflows, then expand parameterized
coverage by action/model/input/selector. Evaluate both lanes and interruptions
with independent expected outcomes. Current full-suite totals are not coverage
metrics. Exit A47/A48: complete declared matrix, actual downstream handoff and
zero duplicate starts/unauthorized destructive actions in the exercised scope.

### D01 — Installed compatibility and client/platform evidence

Verify current entrypoints and explicitly migrated/reinstated released clients
from a clean wheel. Separate live acceptance from auth-free protocol smoke.
Current 0.2.1 source metadata is not a releasable update over published 0.2.2;
select a fresh version after scope review and synchronize metadata together.
Exit A49/A50: reproducible installation, tested upgrade/refusal/rollback and an
honest client/OS matrix. No automatic publication or tag mutation.

### R01 — Release and complete-scope accounting

Reconcile every original generation/management requirement, including missing
Canvas/Library/sharing/native formats. Retired upstream functions require dated
proof and an explicit scope change, not a passing substitute. Exit A51/A52:
required evidence for the declared scope is complete; blocked/skipped stays
visible. A partial compatible release can ship only with its real limited scope,
not a claim of full Gemini Web parity.

## Generation and management packages

Detailed target requirements remain in [generation.md](generation.md) (G01–G08)
and [management.md](management.md) (M01–M08), with procedures in
[acceptance.md](acceptance.md). They now use current shared owners; new features
are explicitly proposals. Prioritize G03 alongside M01/M02 and the missing M06/M07
actions; do not spend both lanes' budget on more Task-browser controls alone.

## Remaining Owner Choice

The two surfaces and both full product goals are settled. The recommended default
is to preserve released behavior with thin adapters where feasible, isolate old
state until migration is tested, and advance generation/management together.
A deliberate breaking release or a newly advertised client/OS support guarantee
requires an explicit decision and evidence; neither blocks documentation or
unrelated additive implementation. Do not repeatedly ask to reconfirm this scope.
