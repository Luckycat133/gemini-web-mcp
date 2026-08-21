# Development Status and Next Steps

Updated: 2026-08-21

The active project version is `0.2.1`; the existing `v0.2.0` tag remains immutable. This patch contains the final license/distribution verification, Deep Research source-host validation, and refreshed Development Skill.

## Implemented

MCP SDK v2 and modern/legacy stdio compatibility; primary/compact server surfaces; shared chat/session/lifecycle/artifact/history services; typed artifacts and history list/search/read/export/delete; Gem mutation verification; evidence-based chat deletion; centralized RPC contracts/parsers; wheel/sdist/runtime-Skill packaging; embedded AGPL license verification; isolated onboarding; Skill validation; CI and CodeQL gates.

## Partial

- Live Gemini evidence covers only the bounded 2026-08-08 Cookie/text/session/history/cleanup run.
- Deep history and account/admin typed-result coverage remains uneven.
- Remaining mutations need a complete read-back audit.
- Long operations preserve states/IDs but lack one durable service.
- Delayed cleanup is process-local until SQLite durability is implemented.

## Settled Decisions

- Preserve `v0.2.0`; release the completed patch as `v0.2.1`.
- The maintainer supplies account Cookies through protected environment values.
- Full live baseline is the next development package; shared long-operation service follows.
- Long-operation and cleanup persistence use local-only SQLite with no prompts, chat text, Cookies, or raw responses.
- Long operations default to seven-day retention, restart/cross-client resume, and best-effort cancellation.

## Next Order

1. Merge and publish `v0.2.1`.
2. Run the dedicated full live baseline.
3. Build the shared SQLite long-operation service.
4. Complete typed admin/deep history.
5. Finish mutation verification.
6. Add SQLite durable cleanup.
7. Add multimodal onboarding and the official support matrix.
8. Consider selected UI parity.

## Remaining Owner Question

Select the first officially supported client, operating-system, and distribution combinations.
