"""Behavior contracts for the real MCP stdio smoke driver."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import scripts.smoke_mcp_protocol as protocol_smoke
from scripts.smoke_profiles import ASSIST_TOOLS

_BLANK_SEARCH_DOMAIN_FAILURE = {
    "ok": False,
    "error": {"code": "INVALID_ARGUMENT", "message": "query must not be blank."},
}


def test_compact_handshake_uses_auth_free_blank_prompt_rejection(monkeypatch, tmp_path) -> None:
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
                tools=[SimpleNamespace(name="create")]
            )

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(
                is_error=False,
                result_type="complete",
                structured_content={"result": [{"_meta": {"domain_result": _BLANK_SEARCH_DOMAIN_FAILURE}}]},
            )

    monkeypatch.setattr(protocol_smoke, "_resolve_executable", lambda _command: "/tmp/fake-mcp")
    monkeypatch.setattr(protocol_smoke, "stdio_client", lambda _parameters: object())
    monkeypatch.setattr(protocol_smoke, "Client", lambda *_args, **_kwargs: FakeClient())

    result = asyncio.run(
        protocol_smoke._handshake(
            "gemini-mcp-skill-server",
            profile="model",
            expected_tools=frozenset({"create"}),
            cwd=tmp_path,
            mode="auto",
        )
    )

    assert calls == [("create", {"prompt": ""})]
    assert result["representative_tool"] == "create"


def _install_fake_assist_client(
    monkeypatch,
    *,
    tools: list[str],
    structured_content: object,
    calls: list[tuple[str, dict]] | None = None,
) -> None:
    class FakeClient:
        protocol_version = "test-protocol"
        server_info = SimpleNamespace(name=protocol_smoke.ASSIST_SERVER_NAME)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in tools])

        async def call_tool(self, name, arguments):
            if calls is not None:
                calls.append((name, arguments))
            return SimpleNamespace(
                is_error=False,
                result_type="complete",
                structured_content=structured_content,
            )

    monkeypatch.setattr(protocol_smoke, "_resolve_executable", lambda _command: "/tmp/fake-mcp")
    monkeypatch.setattr(protocol_smoke, "stdio_client", lambda _parameters: object())
    monkeypatch.setattr(protocol_smoke, "Client", lambda *_args, **_kwargs: FakeClient())


def test_assist_handshake_asserts_typed_blank_query_invalid_argument(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, dict]] = []

    _install_fake_assist_client(
        monkeypatch,
        tools=sorted(ASSIST_TOOLS),
        structured_content={"result": [{"_meta": {"domain_result": _BLANK_SEARCH_DOMAIN_FAILURE}}]},
        calls=calls,
    )

    result = asyncio.run(
        protocol_smoke._assist_handshake(
            "gemini-mcp-assist",
            profile="model",
            cwd=tmp_path,
            mode="auto",
        )
    )

    assert calls == [("gemini_search", {"query": ""})]
    assert result["representative_tool"] == "gemini_search"
    assert result["server_name"] == "gemini_assist_mcp"
    assert result["tools"] == len(ASSIST_TOOLS)


@pytest.mark.parametrize(
    "structured_content",
    [
        {"result": [{"_meta": {"domain_result": {"ok": False, "error": {"code": "AUTHENTICATION"}}}}]},
        {"result": [{"_meta": {"domain_result": {"ok": True, "error": None}}}]},
        {"result": []},
        {"unexpected": True},
        None,
    ],
)
def test_assist_handshake_rejects_non_invalid_argument_domain_failure(
    monkeypatch, tmp_path, structured_content
) -> None:
    _install_fake_assist_client(
        monkeypatch,
        tools=sorted(ASSIST_TOOLS),
        structured_content=structured_content,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            protocol_smoke._assist_handshake(
                "gemini-mcp-assist",
                profile="model",
                cwd=tmp_path,
                mode="auto",
            )
        )


@pytest.mark.parametrize("tool,arguments,code", [
    ("gemini_ask", {"prompt": ""}, "INVALID_ARGUMENT"),
    ("gemini_cleanup", {"action": "list"}, "AUTH_REQUIRED"),
])
def test_function_and_account_smokes_assert_typed_auth_free_responses(monkeypatch, tmp_path, tool, arguments, code):
    calls = []
    _install_fake_assist_client(
        monkeypatch, tools=[tool],
        structured_content={"result": [{"_meta": {"domain_result": {"ok": False, "error": {"code": code}}}}]},
        calls=calls,
    )
    result = asyncio.run(protocol_smoke._handshake(
        "test-entrypoint", profile="function", expected_tools=frozenset({tool}), cwd=tmp_path, mode="auto",
    ))
    assert calls == [(tool, arguments)]
    assert result["representative_tool"] == tool


def test_default_protocol_smoke_unsets_legacy_profile_and_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_TOOLS", "all")
    monkeypatch.setenv("GEMINI_PSID", "synthetic")
    environment = protocol_smoke._safe_environment(None, tmp_path)
    assert "GEMINI_TOOLS" not in environment and "GEMINI_PSID" not in environment
    assert environment["GEMINI_BROWSER_AUTO_LOAD"] == "false"
    assert environment["GEMINI_CLEANUP_DB_PATH"] == str(tmp_path / "cleanup.sqlite3")
