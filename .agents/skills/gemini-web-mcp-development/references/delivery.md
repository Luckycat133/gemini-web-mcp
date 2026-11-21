# Integration, installation and delivery

## Development source versus published contract

This plan follows PR #47's Function/Account source, reviewed at `5c9f530` on
2026-11-14. Current metadata is 0.2.1; published v0.2.2 has a different create
entrypoint, seven Account facades and unified state store. Neither successful
current installation nor golden equality proves upgrade compatibility.

Preserve published tags. Choose a fresh release version only after comparing
actual releases and approved breaking scope, then update package/runtime/Skill
metadata and assets together. This documentation change does not change runtime
versions, merge PR #47 or publish anything.

## Integration gates

Map every released entrypoint/action/input/output to the new implementation,
a thin adapter, a planned migration or an explicitly approved retirement. Port
user-level behavior regressions before removing implementation-specific tests.
Do not promise the removed create entrypoint or unported Library/Canvas/sharing/
ZIP behavior as current features.

OperationStore and CleanupStore have separate environment paths and account
fencing. Do not point them at the released unified DB, silently reset data or
rename an environment variable as a migration. Test synthetic old DB/handles,
forward migration or actionable refusal, backup/rollback, credential rotation,
account mismatch and protected outstanding jobs. Never add prompts or secrets
to the metadata ledger to make recovery easy.

Same `operation_id` means the same existing job; payload equivalence and old
idempotency_key/cancel contracts are separate migration work. Plain sessions
remain process-local until a durable-session feature is accepted.

## Setup and distribution

Recommend Function and Account choices. Existing explicit-profile/assist/compact
routes remain tested where advertised. Interactive setup runs outside MCP stdio,
shows client-config diffs, writes atomically and provides dry-run/rollback and
noninteractive options. Do not write credentials into repository configuration.

Run auth-free preflight by default, then separately authorized bounded live
examples. Check media decoders, paths, browser extras and platform permissions;
do not silently install system services or change keychains/browser policy.
Source installation pins a reviewed SHA, not the superseded 20261101 branch.

Actual installed-client evidence owns the OS support matrix. POSIX file-mode
code or a Windows configuration example does not establish Windows support.
Paid API video needs separate explicit credentials/usage intent; it is not a
fallback for a Web subscription failure.

## Final receipt

Report exact source/diff, compatibility disposition, implemented capabilities,
commands/dependency versions and docs/offline/protocol/package/agent/live results
separately. Record uncovered actions and blocked checks. Keep raw private content,
credentials, account IDs, generated files and progress receipts outside Git.

The plan checker verifies structural/evidence integrity, not runtime truth.
A partial release can accurately declare its tested subset; only complete
reconciled goal coverage may be called full acceptance. Existing accepted
receipts cannot be transferred to a revised plan without review. User-authorized
pushes do not authorize force-push, merge, tagging, public sharing or account writes.
