"""Behavior contracts for the real MCP stdio smoke driver."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import scripts.smoke_mcp_protocol as protocol_smoke


def test_compact_handshake_uses_auth_free_static_manifest_call(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        protocol_version = "test-protocol"
        server_info = SimpleNamespace(name="compact-test")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def list_tools(self):
            return SimpleNamespace(
                tools=[SimpleNamespace(name="account"), SimpleNamespace(name="doctor")]
            )

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(
                is_error=False,
                result_type="complete",
                structured_content={"ok": True},
            )

    monkeypatch.setattr(protocol_smoke, "_resolve_executable", lambda _command: "/tmp/fake-mcp")
    monkeypatch.setattr(protocol_smoke, "stdio_client", lambda _parameters: object())
    monkeypatch.setattr(protocol_smoke, "Client", lambda *_args, **_kwargs: FakeClient())

    result = asyncio.run(
        protocol_smoke._handshake(
            "gemini-mcp-skill-server",
            profile="model",
            expected_tools=frozenset({"account", "doctor"}),
            cwd=tmp_path,
            mode="auto",
        )
    )

    assert calls == [("account", {"action": "manifest"})]
    assert result["representative_tool"] == "account"
