# Development Status and Next Steps

Updated: 2026-08-08

This page translates the repository development skill into a human-readable status. The canonical agent instructions remain
in [`.agents/skills/gemini-web-mcp-development`](../.agents/skills/gemini-web-mcp-development/SKILL.md).

## Completion Answer

The development skill is not fully implemented, and it is not intended to become a one-time checklist. It combines shipped
engineering contracts, a repeatable maintenance method, open product work, and decisions that require the repository owner.

In this page, **implemented** means the checked-in repository contract has offline, package, or MCP protocol evidence. It does
not mean that the current Gemini Web deployment was observed. Live compatibility is reported separately.

## Current Status

| Area | State | Current boundary |
| --- | --- | --- |
| MCP SDK v2 and protocol negotiation | Implemented | Primary and compact entrypoints exercise current discovery and the legacy initialize path over real stdio. |
| Packaging and onboarding | Implemented | Wheel, source distribution, runtime skill zip, clean installation, and credential-free `uvx` onboarding are maintained gates. |
| Agent Skill distribution | Implemented | Runtime and development skills have unique names under the single `.agents/skills` repository source. |
| Chat, session, and artifact services | Implemented | Primary and compact adapters share core execution and typed results for these workflows. |
| History typed results | Partial | Primary/compact list, search, read, export, and delete share one typed service. The primary-only deep source scan remains prose-first. |
| Account/admin typed results | Partial | Account, prompt, cookie, doctor, cleanup, scheduled, Notebook, and compatibility coverage remains uneven. |
| Mutation verification | Partial | Chat delete, Gem mutations, and several Notebook/scheduled paths now use read-back or explicitly report unverified acceptance, but every remote mutation has not completed the same audit. |
| Live Gemini compatibility | Not observed for this change | The workflow exists, but the latest scheduled run was skipped; offline or fixture evidence does not establish current Web compatibility. |
| Delayed cleanup | Partial | Retention and process-local cleanup exist, but delayed work is not durable across server restarts. |
| Long-running operations | Partial | Media and Deep Research preserve queued/running/timed-out states and identifiers, but no shared `start/status/result/cancel` job API exists. |
| Additional Gemini UI workflows | Deferred | Drive import, Canvas mutations, richer Notebook/scheduled/sharing workflows, and other UI parity work require stable evidence and user value. |
| Release version line | Owner decision | Source metadata remains `1.3.0`; Git tags already reach `v2.2.0`, so publishing `2.0.0` would not produce a monotonic release line. |

The latest recorded live-canary run at the time of this update was
[skipped](https://github.com/Luckycat133/gemini-web-mcp/actions/runs/30889281960). It is not live evidence.

## Current Unreleased Change Set

- Removed duplicate `.codex/skills` copies and updated CI, release, packaging, and documentation references to use
  `.agents/skills` as the single source.
- Added shared typed history list/read results with primary/compact parity and object/mapping normalization.
- Extended that shared history service to search/export/delete. Delete now distinguishes verified absence, unavailable read-back,
  a still-present chat, and read-back failure instead of equating an accepted call or ambiguous `read_chat(None)` with
  verified deletion; positive absence requires a complete fresh recent/pinned metadata scan.
- Made `GEMINI_AUTO_REFRESH=false` avoid starting the Cookie monitor thread.
- Made offline compact protocol smoke use the auth-free static manifest instead of browser-profile diagnostics.
- Bounded macOS `browser-cookie3` Keychain waits with `GEMINI_BROWSER_COOKIE_TIMEOUT_SECONDS`; timeout responses are
  sanitized and the dependency function is restored after each access window.
- Preserved product version `1.3.0`; no tag or release is part of this change set.

## Evidence Boundary

The current change set has local evidence from:

- Ruff and Mypy;
- the complete offline pytest suite and targeted contract checklist;
- representative profile snapshots and four primary/compact modern/legacy stdio handshakes;
- pinned Agent Skill validation plus direct repository installation;
- clean Python 3.12 wheel installation, `pip check`, installed entrypoints, and offline onboarding.

No live Gemini account, private chat content, media entitlement, or current Web RPC response was observed. An authorized,
local, value-free Chrome profile probe returned `BROWSER_COOKIE_ACCESS_TIMEOUT`; this is local Keychain evidence only and
does not establish account validity or a dedicated live-canary account.

## Recommended Next Order

1. Configure a dedicated test account and record a read-only live baseline.
2. Continue one bounded typed-result slice at a time: primary deep history scan, then account, prompt, cookie, doctor, and cleanup.
3. Audit every remote mutation for positive read-back and honest partial states.
4. Define one long-operation job contract for media and Deep Research.
5. Decide cleanup durability and the canonical public version/release line.
6. Add end-to-end video, music, file, URL, and research onboarding before broad UI parity.

## Owner Decisions Still Required

- the next public version above the historical tag line, or whether releases should remain source-at-SHA for now;
- ownership, secrets, region, tier, and cadence for a dedicated live-canary account;
- durable cleanup versus documented process-local best effort;
- persistence and cancellation semantics for long-running jobs;
- the long-term role of the fixed compact server;
- officially supported clients, operating systems, and distribution channels.

See the development skill's [architecture status](../.agents/skills/gemini-web-mcp-development/references/architecture.md)
and [owner decision record](../.agents/skills/gemini-web-mcp-development/references/roadmap.md) for the detailed contract.
