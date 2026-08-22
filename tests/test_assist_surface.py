"""Deterministic catalog and delegation contracts for the gemini_assist_mcp surface.

The focused assist server must keep a small, stable catalog (exactly
``gemini_ask`` for this slice), register it with the shared mutation
annotations, and delegate execution to the shared ChatService instead of a
surface-local copy of request construction. These tests pin:

- server identity (``gemini_assist_mcp``) and version wiring;
- the exact, order-stable tool catalog and MCP annotations;
- the deterministic ``gemini_ask`` input schema;
- delegation behavior: composed prompt, resolved model, cleanup source,
  backend/lifecycle evidence, typed input rejection, and the error boundary.
"""

import asyncio
from types import SimpleNamespace

import src.surfaces.assist as assist

from src import __version__


async def _call_tool(name: str, **kwargs):
    return (await assist.mcp.call_tool(name, kwargs)).content


class _FakeAskClient:
    """Capture the generate_content request built by the shared ChatService."""

    def __init__(self, *, response_text="second opinion", observed_backend="gemini-3-flash"):
        self._response_text = response_text
        self._observed_backend = observed_backend
        self.captured_generate_kwargs = None

    async def generate_content(self, **kwargs):
        self.captured_generate_kwargs = dict(kwargs)
        return SimpleNamespace(
            text=self._response_text,
            images=[],
            videos=[],
            media=[],
            observed_backend=self._observed_backend,
            metadata=["c_ask1", "r_response"],
        )


def _patch_ask_client_env(monkeypatch, client, *, captured_schedule=None):
    """Patch the client/cleanup seams the assist surface binds to ChatService."""
    monkeypatch.setattr(assist, "get_gemini_client", lambda: client)

    async def fake_initialize():
        return None

    monkeypatch.setattr(assist, "initialize_client", fake_initialize)

    async def fake_cleanup_due(client_arg):
        return 0

    monkeypatch.setattr(assist, "cleanup_due_remote_chats", fake_cleanup_due)

    def fake_schedule_from_response(response, *, retain_chat, delete_after_seconds, source):
        if captured_schedule is not None:
            captured_schedule.append(
                {
                    "retain_chat": retain_chat,
                    "delete_after_seconds": delete_after_seconds,
                    "source": source,
                }
            )
        return None

    monkeypatch.setattr(assist, "schedule_remote_chat_cleanup_from_response", fake_schedule_from_response)


# ---------------------------------------------------------------------------
# Server identity and deterministic catalog
# ---------------------------------------------------------------------------


def test_assist_server_identity():
    assert assist.SERVER_NAME == "gemini_assist_mcp"
    assert assist.mcp.name == "gemini_assist_mcp"
    assert assist.mcp.version == __version__


def test_assist_catalog_is_exactly_gemini_ask():
    tools = asyncio.run(assist.mcp.list_tools())

    assert [tool.name for tool in tools] == ["gemini_ask"]


def test_gemini_ask_registration_and_annotations():
    tools = {tool.name: tool for tool in asyncio.run(assist.mcp.list_tools())}
    tool = tools["gemini_ask"]

    assert tool.description and tool.description.strip()
    assert tool.annotations is not None
    # gemini_ask starts a remote Gemini chat: mutating, not destructive.
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False
    assert tool.annotations.open_world_hint is True


def test_gemini_ask_input_schema_is_deterministic():
    tools = {tool.name: tool for tool in asyncio.run(assist.mcp.list_tools())}
    schema = tools["gemini_ask"].input_schema

    assert schema["type"] == "object"
    assert schema["required"] == ["prompt"]
    assert set(schema["properties"]) == {"prompt", "context", "model", "thinking_level"}
    assert schema["properties"]["model"]["default"] == "flash"
    assert schema["properties"]["thinking_level"]["default"] == "standard"


def test_assist_surface_exposes_no_account_or_history_tools():
    names = {tool.name for tool in asyncio.run(assist.mcp.list_tools())}

    assert not names & {
        "gemini_history",
        "gemini_get_cookie_status",
        "gemini_manage_gems",
        "gemini_list_scheduled_actions",
        "gemini_manage_prompts",
        "gemini_cleanup_test_artifacts",
    }


# ---------------------------------------------------------------------------
# gemini_ask — delegation to the shared ChatService
# ---------------------------------------------------------------------------


def test_gemini_ask_delegates_prompt_model_and_thinking_level(monkeypatch):
    client = _FakeAskClient()
    captured_schedule = []
    _patch_ask_client_env(monkeypatch, client, captured_schedule=captured_schedule)

    content = asyncio.run(_call_tool("gemini_ask", prompt="Critique this architecture."))

    assert client.captured_generate_kwargs is not None
    assert client.captured_generate_kwargs["prompt"] == "Critique this architecture."
    assert client.captured_generate_kwargs["model"] == "gemini-3-flash"
    assert client.captured_generate_kwargs["thinking_level"] == "standard"
    assert "gem" not in client.captured_generate_kwargs
    assert "temporary" not in client.captured_generate_kwargs
    assert captured_schedule == [
        {"retain_chat": False, "delete_after_seconds": None, "source": "gemini_ask"}
    ]

    assert len(content) == 1
    assert "second opinion" in content[0].text


def test_gemini_ask_returns_backend_and_lifecycle_evidence(monkeypatch):
    client = _FakeAskClient(response_text="answer text", observed_backend="gemini-3-pro")
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(_call_tool("gemini_ask", prompt="Review this code.", model="pro"))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is True
    assert domain_result["data"]["requested_model"] == "pro"
    assert domain_result["data"]["effective_model"] == "gemini-3-pro"
    assert domain_result["data"]["observed_backend"] == "gemini-3-pro"
    assert domain_result["meta"]["requested_backend"] == "pro"
    assert domain_result["meta"]["effective_backend"] == "gemini-3-pro"
    assert domain_result["data"]["lifecycle"]["upstream_chat_id"] == "c_ask1"


def test_gemini_ask_appends_optional_context_to_the_prompt(monkeypatch):
    client = _FakeAskClient()
    _patch_ask_client_env(monkeypatch, client)

    asyncio.run(
        _call_tool(
            "gemini_ask",
            prompt="Critique this design.",
            context="Must stay under budget.",
        )
    )

    prompt = client.captured_generate_kwargs["prompt"]
    assert prompt.startswith("Critique this design.")
    assert "Must stay under budget." in prompt


def test_gemini_ask_normalizes_model_alias_before_resolving(monkeypatch):
    client = _FakeAskClient()
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(_call_tool("gemini_ask", prompt="Hi", model="lite"))

    assert client.captured_generate_kwargs["model"] == "3.1 Flash-Lite"
    domain_result = content[0].meta["domain_result"]
    assert domain_result["data"]["requested_model"] == "lite"
    assert domain_result["data"]["effective_model"] == "3.1 Flash-Lite"


def test_gemini_ask_blank_prompt_is_rejected_before_client_use(monkeypatch):
    def explode():
        raise AssertionError("get_gemini_client must not be called for a blank prompt")

    monkeypatch.setattr(assist, "get_gemini_client", explode)

    content = asyncio.run(_call_tool("gemini_ask", prompt="   "))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is False
    assert domain_result["error"]["code"] == "INVALID_ARGUMENT"
    assert domain_result["meta"]["verification_status"] == "input_rejected"
    assert "blank" in content[0].text


def test_gemini_ask_failure_is_typed_by_the_error_boundary(monkeypatch):
    class _ExplodingClient:
        async def generate_content(self, **kwargs):
            raise RuntimeError("upstream exploded")

    _patch_ask_client_env(monkeypatch, _ExplodingClient())

    content = asyncio.run(_call_tool("gemini_ask", prompt="Hi"))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is False
    assert domain_result["error"]["code"] == "INTERNAL_ERROR"
    assert domain_result["meta"]["verification_status"] == "exception_classified"
