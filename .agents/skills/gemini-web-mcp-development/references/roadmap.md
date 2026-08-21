# Settled Decisions and Next Development Packages

Routine bug fixes and bounded service migrations should proceed without reopening settled product choices.

## Settled Decisions

- Preserve the existing `v0.2.0` tag and publish the completed audited patch as `v0.2.1`.
- The maintainer supplies account Cookies through a protected GitHub environment or local process environment; never commit or print them.
- Development order is full live baseline first, shared long-operation service second, then typed admin results, mutation verification, durable cleanup, onboarding/client matrix, and selected UI parity.
- Use local-only SQLite for operation recovery and delayed cleanup. Store IDs, state, timestamps, attempts, error/verification codes, and artifact locators only—never prompts, chat text, Cookies, or raw upstream responses.
- Long operations default to seven-day retention, support restart and cross-client resume, and use best-effort cancellation unless provider cancellation is observed.
- Prefer provider-native temporary chats; only non-temporary cleanup work enters the durable queue.
- Compact remains the low-token discovery product while execution belongs in shared services.

## Package A — Dedicated Full Live Baseline

Configure maintainer-provided Cookie secrets, record observable Web build/locale/tier/entitlements, test text, primary/compact sessions, image, video, music, file, URL, Deep Research, disposable Scheduled/Gem/Notebook mutations, and direct-ID cleanup. Produce a sanitized schema-valid report with every capability classified and every resource accounted for.

## Package B — Shared SQLite Long-Operation Service

Implement `start/status/result/cancel` for Deep Research, video, and music with stable operation/provider IDs, queued/running/completed/timed-out/cancelled/failed states, local SQLite migrations, restart/cross-client recovery, seven-day retention, pruning, artifact identity continuity, and best-effort cancellation.

## Package C — Complete Typed Admin and Deep History

Order: primary deep history; account/compatibility; prompts; Cookie outcomes; doctor; cleanup; remaining scheduled/Notebook presentation. Require stable `DomainResult`, pagination/truncation/retryability/verification, text agreement, and primary/compact parity.

## Package D — Mutation Verification Audit

For every remote mutation, define authoritative read-back, positive terminal evidence, ambiguous states, idempotency/retry behavior, and cleanup/rollback guidance.

## Package E — Durable SQLite Cleanup

Implement pending/running/completed/failed/cancelled states, restart recovery, retry/backoff, list/retry/cancel, direct-ID authority, temporary-chat bypass, and no private text in storage.

## Package F — Multimodal Onboarding and Client Matrix

Add one copyable, independently verified workflow for video, music, file, URL, and Deep Research, then exercise the official client/OS matrix.

## Package G — Selected UI Parity

Only after A–F: Drive, Canvas, richer recurrence, Notebook CRUD/source management, sharing, or Library workflows with current live evidence.

## Remaining Owner Question

Choose the first official support matrix across Codex, Claude Desktop, Claude Code, VS Code; macOS, Windows, Linux; and reviewed Git SHA, immutable GitHub Release wheel, PyPI, ClawHub, and discovery directories.
