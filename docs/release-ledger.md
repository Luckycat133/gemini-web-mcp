# Gemini Web MCP 0.x release ledger

Published tags and Releases are historical records. Do not move, recreate or
replace their refs or assets. The 0.1.0 row is a retrospective source milestone,
not an assertion that a package, tag or GitHub Release ever existed.

| Version | Evidence | Historical milestone |
| --- | --- | --- |
| 0.1.0 | [Source milestone](https://github.com/Luckycat133/gemini-web-mcp/commit/c17eed6569a4190373d114d48907f288cfd77e4b), 2026-05-05 | First package/source/test tree on main; metadata already said 0.2.0. No 0.1.0 tag or Release observed. |
| 0.2.0 | [v0.2.0 tag](https://github.com/Luckycat133/gemini-web-mcp/tree/v0.2.0) → `4341253563870012e465ea86741d295ed311ac94` | Version and distribution contracts aligned. Retained tag; no GitHub Release object observed. |
| 0.2.1 | [v0.2.1 Release](https://github.com/Luckycat133/gemini-web-mcp/releases/tag/v0.2.1) → `d9179952e8a9d3e1b155d24c2ac2010c3d0c035a` | Dedicated Assist surface and documentation. |
| 0.2.2 | [v0.2.2 Release](https://github.com/Luckycat133/gemini-web-mcp/releases/tag/v0.2.2) → `362a368f6c68b01c6eb143586db45743c5222e2c` | Focused workflows, durable state store and downloadable distribution. |
| 0.3.0 | Candidate based on [main](https://github.com/Luckycat133/gemini-web-mcp/commit/30d22cda84bb489028f4997108f1e3352e297468); no tag or Release at preparation | Next minor: Function and Account-next coexist with released Create and Account; exact Task recovery and managed Skill fixes. |

## Release and rollback

After the release-preparation merge, verify the exact main commit and completed
CI, CodeQL and review. Publish only through the existing GitHub Release workflow
by creating tag `v0.3.0` on that commit. If the authorized operator can create
branches but not tags directly, use a separate authorized PR that only adds
`release/v0.3.0.publish` to main. Its two newline-terminated lines must be exactly
`v0.3.0` and the reviewed preparation commit SHA (the marker commit's first parent).
No regular main push launches a release. The workflow verifies exact continued
main identity, CI, CodeQL and metadata, builds the wheel, sdist and two Skill ZIPs,
and smoke-tests the isolated wheel before publication. This route creates a missing
tag without updating an existing tag, requires it to
match the tested SHA, and never replaces an existing Release. Confirm the
resulting assets and tag-to-commit binding; no PyPI or ClawHub publication is implied.

For older clients, install the unchanged v0.2.2 assets and use the matching
state store and account rather than overwriting either ledger. See
[the state compatibility guide](source-update-integration.md).
