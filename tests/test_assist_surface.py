"""Deterministic catalog and delegation contracts for the gemini_assist_mcp surface.

The focused assist server must keep a small, stable catalog (exactly
``gemini_ask`` and ``gemini_search`` for this slice), register each tool with
the shared mutation annotations, and delegate execution to the shared
ChatService/SearchService instead of a surface-local copy of request
construction. These tests pin:

- server identity (``gemini_assist_mcp``) and version wiring;
- the exact, order-stable tool catalog and MCP annotations;
- the deterministic ``gemini_ask``/``gemini_search`` input schemas;
- ask delegation: composed prompt, resolved model, cleanup source,
  backend/lifecycle evidence, typed input rejection, and the error boundary;
- search delegation: the grounded-search contract (grounded requires observed
  source evidence; source-free prose stays ``answer_only``), observed_at,
  cleanup source, and typed failure/blank-query handling.
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


class _FakeSearchClient:
    """Capture the search request and return citations as upstream evidence."""

    def __init__(self, *, response_text="search answer", citations=None, observed_backend="gemini-3-flash"):
        self._response_text = response_text
        self._citations = list(citations or [])
        self._observed_backend = observed_backend
        self.captured_generate_kwargs = None

    async def generate_content(self, **kwargs):
        self.captured_generate_kwargs = dict(kwargs)
        return SimpleNamespace(
            text=self._response_text,
            images=[],
            videos=[],
            media=[],
            citations=list(self._citations),
            observed_backend=self._observed_backend,
            metadata=["c_search1", "r_response"],
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


def test_assist_catalog_is_exactly_ask_and_search():
    tools = asyncio.run(assist.mcp.list_tools())

    assert [tool.name for tool in tools] == ["gemini_ask", "gemini_search"]


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


def test_gemini_search_registration_and_annotations():
    tools = {tool.name: tool for tool in asyncio.run(assist.mcp.list_tools())}
    tool = tools["gemini_search"]

    assert tool.description and tool.description.strip()
    assert tool.annotations is not None
    # gemini_search starts a remote Gemini chat: mutating, not destructive.
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False
    assert tool.annotations.open_world_hint is True


def test_gemini_search_input_schema_is_deterministic():
    tools = {tool.name: tool for tool in asyncio.run(assist.mcp.list_tools())}
    schema = tools["gemini_search"].input_schema

    assert schema["type"] == "object"
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "recency", "domains", "language", "max_results", "model"}
    assert schema["properties"]["max_results"]["default"] == 8
    assert schema["properties"]["model"]["default"] == "flash"


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


# ---------------------------------------------------------------------------
# gemini_search — delegation to the shared SearchService
# ---------------------------------------------------------------------------


def test_gemini_search_delegates_query_model_and_cleanup_source(monkeypatch):
    client = _FakeSearchClient()
    captured_schedule = []
    _patch_ask_client_env(monkeypatch, client, captured_schedule=captured_schedule)

    content = asyncio.run(_call_tool("gemini_search", query="What changed in the latest release?"))

    assert client.captured_generate_kwargs is not None
    prompt = client.captured_generate_kwargs["prompt"]
    assert "What changed in the latest release?" in prompt
    assert prompt.startswith("Search the web for current information")
    assert client.captured_generate_kwargs["model"] == "gemini-3-flash"
    assert client.captured_generate_kwargs["thinking_level"] == "standard"
    assert "gem" not in client.captured_generate_kwargs
    assert "temporary" not in client.captured_generate_kwargs
    assert captured_schedule == [
        {"retain_chat": False, "delete_after_seconds": None, "source": "gemini_search"}
    ]

    assert len(content) == 1
    assert "search answer" in content[0].text


def test_gemini_search_returns_grounded_state_with_observed_sources(monkeypatch):
    client = _FakeSearchClient(
        response_text="Release notes say X shipped.",
        citations=[
            {"url": "https://example.com/release-notes", "title": "Release Notes"},
            {"url": "https://blog.example.com/announcement", "title": None},
        ],
        observed_backend="gemini-3-pro",
    )
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(_call_tool("gemini_search", query="latest release", model="pro"))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is True
    data = domain_result["data"]
    assert data["grounding_state"] == "grounded"
    assert data["query"] == "latest release"
    assert data["sources"] == [
        {"url": "https://example.com/release-notes", "title": "Release Notes"},
        {"url": "https://blog.example.com/announcement", "title": None},
    ]
    assert data["observed_at"]
    assert data["requested_model"] == "pro"
    assert data["effective_model"] == "gemini-3-pro"
    assert data["observed_backend"] == "gemini-3-pro"
    assert data["lifecycle"]["upstream_chat_id"] == "c_search1"
    assert domain_result["meta"]["details"]["grounding_state"] == "grounded"
    # Compatibility text may repeat the evidence, never contradict it.
    assert "https://example.com/release-notes" in content[0].text
    assert "Grounding state: grounded" in content[0].text


def test_gemini_search_never_labels_source_free_prose_as_grounded(monkeypatch):
    client = _FakeSearchClient(response_text="Probably the newest version is fine.", citations=[])
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(_call_tool("gemini_search", query="which version is newest?"))

    domain_result = content[0].meta["domain_result"]
    data = domain_result["data"]
    assert data["grounding_state"] == "answer_only"
    assert data["sources"] == []
    assert data["answer"] == "Probably the newest version is fine."
    assert "Grounding state: answer_only" in content[0].text
    assert "No source evidence was observed" in content[0].text


def test_gemini_search_ignores_citations_without_usable_urls(monkeypatch):
    client = _FakeSearchClient(
        response_text="Answer without usable citations.",
        citations=[
            {"url": None, "title": "No URL"},
            {"url": "not a url", "title": "Invalid"},
            {"url": "ftp://example.com/file", "title": "Wrong scheme"},
            {"url": "https://example.com/real", "title": "Real"},
        ],
    )
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(_call_tool("gemini_search", query="anything"))

    data = content[0].meta["domain_result"]["data"]
    assert data["grounding_state"] == "grounded"
    assert [source["url"] for source in data["sources"]] == ["https://example.com/real"]


def test_gemini_search_reports_unavailable_when_no_answer_is_returned(monkeypatch):
    client = _FakeSearchClient(response_text="", citations=[{"url": "https://example.com/x", "title": "X"}])
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(_call_tool("gemini_search", query="anything"))

    domain_result = content[0].meta["domain_result"]
    data = domain_result["data"]
    assert data["grounding_state"] == "unavailable"
    assert data["answer"] == ""
    # Observed evidence is still reported; only the answer was missing.
    assert [source["url"] for source in data["sources"]] == ["https://example.com/x"]
    assert domain_result["ok"] is True
    assert "No answer was returned for this search." in content[0].text
    assert "Grounding state: unavailable" in content[0].text


def test_gemini_search_failure_state_is_failed_and_typed(monkeypatch):
    class _ExplodingClient:
        async def generate_content(self, **kwargs):
            raise RuntimeError("upstream exploded")

    _patch_ask_client_env(monkeypatch, _ExplodingClient())

    content = asyncio.run(_call_tool("gemini_search", query="Hi"))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is False
    assert domain_result["error"]["code"] == "INTERNAL_ERROR"
    assert domain_result["data"]["grounding_state"] == "failed"
    assert domain_result["meta"]["verification_status"] == "exception_classified"
    assert domain_result["meta"]["details"]["grounding_state"] == "failed"


def test_gemini_search_blank_query_is_rejected_before_client_use(monkeypatch):
    def explode():
        raise AssertionError("get_gemini_client must not be called for a blank query")

    monkeypatch.setattr(assist, "get_gemini_client", explode)

    content = asyncio.run(_call_tool("gemini_search", query="   "))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is False
    assert domain_result["error"]["code"] == "INVALID_ARGUMENT"
    assert domain_result["meta"]["verification_status"] == "input_rejected"
    assert domain_result["data"] is None
    assert "blank" in content[0].text


def test_gemini_search_passes_constraints_into_the_composed_prompt(monkeypatch):
    client = _FakeSearchClient()
    _patch_ask_client_env(monkeypatch, client)

    asyncio.run(
        _call_tool(
            "gemini_search",
            query="python 3.14 release",
            recency="7 days",
            domains=["Python.org", "python.org"],
            language="English",
        )
    )

    prompt = client.captured_generate_kwargs["prompt"]
    assert "Question: python 3.14 release" in prompt
    assert "last 7 days" in prompt
    assert "python.org" in prompt
    assert "Answer language: English" in prompt
    assert prompt.count("python.org") == 1
