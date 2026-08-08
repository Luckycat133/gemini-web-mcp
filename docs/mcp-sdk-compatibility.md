# MCP SDK and Client Compatibility

This project uses the official MCP Python SDK through the project-owned adapter
`src/adapters/mcp_sdk.py`.

## Supported server SDK

| Project line | MCP Python SDK | Status |
| --- | --- | --- |
| `main` and the next feature release | `mcp>=2.0.0,<3` plus `mcp-types>=2.0.0,<3` | Supported |
| Legacy SDK-v1 compatibility track | `mcp>=1.28,<2` | Deprecated; critical fixes only through **2027-01-31** |

SDK v1 reaches project end of support on **2027-01-31**. New features, schema
changes, and normal bug fixes target SDK v2 only. Existing legacy SDK-v1
artifacts remain available after that date, but they receive no compatibility or
security maintenance from this project.

The SDK major and the negotiated MCP protocol version are separate concerns. A
server running SDK v2 supports both of the protocol paths covered below; users do
not need the Python SDK installed in their MCP host.

## Supported MCP clients

The documented Codex, Claude Desktop, and VS Code MCP configurations are
supported when the host negotiates either of these protocol paths:

| Client path | Negotiation | Protocol | CI coverage |
| --- | --- | --- | --- |
| Current clients | `server/discover` (`Client(mode="auto")`) | `2026-07-28` | Both stdio entrypoints list tools and call a representative local tool |
| Compatibility clients | `initialize` (`Client(mode="legacy")`) | `2025-11-25` | Both stdio entrypoints list tools and call a representative local tool |

The server advertises an `outputSchema` for every tool. SDK v2 returns a complete
`CallToolResult` with `resultType`, the existing text content, and validated
`structuredContent`. The compatibility `_stream` tool names still collect Gemini
Web upstream chunks into one MCP tool result; they do not claim incremental MCP
delivery.

## Maintainer contract

- Runtime MCP imports belong in `src/adapters/mcp_sdk.py`; `src/services/` must
  remain independent of the protocol adapter.
- `tests/fixtures/mcp_v2_tool_contract.json` is the intentional golden baseline
  for representative primary and compact tool lists, input schemas, output
  schemas, and annotations.
- Any deliberate tool/schema change must regenerate and review that fixture with
  `python scripts/snapshot_mcp_v2_contract.py`.
- `python scripts/smoke_mcp_protocol.py` verifies modern discovery, legacy
  initialization, listing, calls, structured output, and both installed console
  entrypoints without Gemini credentials.

See the official [MCP Python SDK migration guide](https://py.sdk.modelcontextprotocol.io/migration/)
for upstream v1-to-v2 API changes.
