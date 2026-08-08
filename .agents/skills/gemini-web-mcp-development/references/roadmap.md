# Decisions to Discuss With the Owner

These choices materially change product contracts. Do not bury them inside an unrelated bug-fix PR.

## 1. What Is the Next Public Version?

Current source metadata is on a `1.x` line while historical Git tags/releases include higher `2.x` versions. A future release must not silently publish a numerically lower “latest” version.

Options:

- adopt a new version greater than every historical tag, preserving old history;
- formally document separate historical/rebuilt lines, with one canonical current line;
- postpone binary releases and support reviewed source-at-SHA installs only.

Recommended discussion: use one new canonical version greater than `2.2.0`—potentially a new major because the current main includes MCP SDK v2 and substantially different structured contracts—then never rewrite historical tags.

## 2. Will the Project Operate a Dedicated Live Test Account?

Without it, the repository can prove code, package, and protocol behavior but not current Gemini Web compatibility.

Decide:

- who owns the account and recovery method;
- account tier/region/locale;
- whether media and Deep Research entitlements are included;
- secret rotation and acceptable run cadence;
- whether the first baseline is read-only only;
- what live evidence may be retained.

Recommended sequence: read-only capability baseline first, then temporary text/image, then disposable mutations with cleanup.

## 3. What Should Happen to Delayed Cleanup Across Restarts?

Options:

- keep best-effort in-memory cleanup and say so explicitly;
- persist a small local queue and retry state;
- prefer provider-native temporary chats and use deletion only for workflows that cannot be temporary;
- make retention fully caller-controlled.

This affects reliability, user expectations, and local state design.

## 4. Should Long Operations Become First-Class Jobs?

Current calls can wait or return queued/running/timed-out state, but recovery is workflow-specific.

Recommended contract:

```text
start -> operation_id
status(operation_id)
result(operation_id)
cancel(operation_id)
```

Use one operation model across Deep Research, video, music, and future asynchronous media. Decide whether operation state is process-local, provider-backed, or persisted.

## 5. What Is the Long-Term Role of the Compact Server?

Options:

- keep the fixed eleven-tool facade as a first-class low-token product;
- generate compact facades from shared tool metadata;
- eventually converge on primary profiles and deprecate the separate entrypoint.

Recommended direction: keep the compact discovery experience, but move all execution into shared services and test semantic parity rather than maintaining two implementations.

## 6. Core Reliability or Gemini UI Parity?

Possible next feature tracks:

- **Core multimodal reliability:** video/music/file/research onboarding, job recovery, artifact rendering, cross-client tests.
- **Account workflows:** typed account/admin results, verified remaining mutations, cleanup durability.
- **UI parity:** Drive picker, Canvas, richer scheduled actions, sharing/settings.

Recommended priority: core multimodal reliability and agent task completion before broad UI parity.

## 7. Which Clients and Platforms Are Officially Supported?

Current examples cover Codex, Claude Desktop, Claude Code, and VS Code, but public support should specify:

- required client versions/protocol modes;
- macOS, Windows, and Linux coverage;
- browser-cookie support boundaries;
- artifact path/rendering expectations;
- timeout recommendations for video/research.

Only claim combinations that have been exercised or clearly mark them community-supported.

## 8. What Is the Distribution Strategy?

Decide the canonical path among:

- reviewed Git commit via `uvx`;
- immutable GitHub Release wheel;
- PyPI package;
- standalone runtime/development skills;
- Glama or other MCP directories.

A directory listing is useful for discovery, but it should not precede a coherent version line and a reproducible live onboarding story.

## Suggested Owner Conversation

Resolve these in order:

1. version/release line;
2. dedicated live account and first baseline;
3. next three user workflows to optimize;
4. long-operation job model;
5. cleanup durability;
6. compact server commitment;
7. official client/platform matrix;
8. distribution/listing channels.
