"""Deterministic catalog and delegation contracts for the gemini_assist_mcp surface.

The focused assist server must keep a small, stable catalog (exactly
``gemini_ask``, ``gemini_search``, ``gemini_understand_image``, and
``gemini_understand`` for this slice), register each tool with the shared
mutation annotations, and delegate execution to the shared
ChatService/SearchService/UnderstandService instead of a surface-local copy
of request construction. These tests pin:

- server identity (``gemini_assist_mcp``) and version wiring;
- the exact, order-stable tool catalog and MCP annotations;
- the deterministic input schemas for every catalog tool;
- ask delegation: composed prompt, resolved model, cleanup source,
  backend/lifecycle evidence, typed input rejection, and the error boundary;
- search delegation: the grounded-search contract (grounded requires observed
  source evidence; source-free prose stays ``answer_only``), observed_at,
  cleanup source, and typed failure/blank-query handling;
- understand-image delegation: one image from a local path or URI rides the
  shared file/URL chat workflows and keeps a stable input artifact identity;
- understand delegation: typed mixed inputs keep their ids and per-input
  outcomes (accepted/analyzed/skipped/failed) without one overloaded string.
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


class _FakeUnderstandClient:
    """Capture the understanding request, including uploaded files."""

    def __init__(self, *, response_text="image analysis", observed_backend="gemini-3-flash"):
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
            metadata=["c_understand1", "r_response"],
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


def test_assist_catalog_is_exactly_the_four_assist_tools():
    tools = asyncio.run(assist.mcp.list_tools())

    assert [tool.name for tool in tools] == [
        "gemini_ask",
        "gemini_search",
        "gemini_understand_image",
        "gemini_understand",
    ]


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


def test_gemini_understand_image_registration_and_annotations():
    tools = {tool.name: tool for tool in asyncio.run(assist.mcp.list_tools())}
    tool = tools["gemini_understand_image"]

    assert tool.description and tool.description.strip()
    assert tool.annotations is not None
    # gemini_understand_image starts a remote Gemini chat: mutating, not destructive.
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False
    assert tool.annotations.open_world_hint is True


def test_gemini_understand_image_input_schema_is_deterministic():
    tools = {tool.name: tool for tool in asyncio.run(assist.mcp.list_tools())}
    schema = tools["gemini_understand_image"].input_schema

    assert schema["type"] == "object"
    assert schema["required"] == ["image"]
    assert set(schema["properties"]) == {"image", "task", "model", "thinking_level"}
    assert schema["properties"]["model"]["default"] == "flash"
    assert schema["properties"]["thinking_level"]["default"] == "standard"


def test_gemini_understand_registration_and_annotations():
    tools = {tool.name: tool for tool in asyncio.run(assist.mcp.list_tools())}
    tool = tools["gemini_understand"]

    assert tool.description and tool.description.strip()
    assert tool.annotations is not None
    # gemini_understand starts a remote Gemini chat: mutating, not destructive.
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is False
    assert tool.annotations.open_world_hint is True


def test_gemini_understand_input_schema_is_typed_and_deterministic():
    tools = {tool.name: tool for tool in asyncio.run(assist.mcp.list_tools())}
    schema = tools["gemini_understand"].input_schema

    assert schema["type"] == "object"
    assert schema["required"] == ["task", "inputs"]
    assert set(schema["properties"]) == {"task", "inputs", "model", "thinking_level"}
    assert schema["properties"]["model"]["default"] == "flash"

    # Mixed inputs are one typed list, not one overloaded string.
    input_schema = schema["$defs"]["UnderstandInput"]
    assert input_schema["required"] == ["id", "kind"]
    assert set(input_schema["properties"]) == {"id", "kind", "path", "url", "text"}
    assert schema["$defs"]["UnderstandInputKind"]["enum"] == ["text", "image", "file", "url"]
    assert schema["properties"]["inputs"]["items"]["$ref"] == "#/$defs/UnderstandInput"
    # The input bound is discoverable in the schema itself.
    assert schema["properties"]["inputs"]["maxItems"] == 16


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


# ---------------------------------------------------------------------------
# gemini_understand_image — delegation to the shared UnderstandService
# ---------------------------------------------------------------------------


def test_gemini_understand_image_uploads_local_image_with_task_and_cleanup_source(monkeypatch, tmp_path):
    image = tmp_path / "screenshot.png"
    image.write_bytes(b"fake image bytes")
    client = _FakeUnderstandClient()
    captured_schedule = []
    _patch_ask_client_env(monkeypatch, client, captured_schedule=captured_schedule)

    content = asyncio.run(_call_tool("gemini_understand_image", image=str(image), task="Explain the error."))

    assert client.captured_generate_kwargs is not None
    prompt = client.captured_generate_kwargs["prompt"]
    assert prompt.startswith("Analyze the inputs below")
    assert "Task: Explain the error." in prompt
    assert "[image] image attached to this message" in prompt
    assert client.captured_generate_kwargs["files"] == [str(image)]
    assert client.captured_generate_kwargs["model"] == "gemini-3-flash"
    assert client.captured_generate_kwargs["thinking_level"] == "standard"
    assert "gem" not in client.captured_generate_kwargs
    assert "temporary" not in client.captured_generate_kwargs
    assert captured_schedule == [
        {"retain_chat": False, "delete_after_seconds": None, "source": "gemini_understand_image"}
    ]

    assert len(content) == 1
    assert "image analysis" in content[0].text


def test_gemini_understand_image_uses_default_task_when_task_is_omitted(monkeypatch, tmp_path):
    image = tmp_path / "photo.png"
    image.write_bytes(b"fake image bytes")
    client = _FakeUnderstandClient()
    _patch_ask_client_env(monkeypatch, client)

    asyncio.run(_call_tool("gemini_understand_image", image=str(image)))

    prompt = client.captured_generate_kwargs["prompt"]
    assert "Task: " in prompt
    assert "Task: None" not in prompt


def test_gemini_understand_image_references_remote_uri_without_uploading(monkeypatch):
    client = _FakeUnderstandClient()
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(
        _call_tool("gemini_understand_image", image="https://example.com/logo.png", task="Describe the logo.")
    )

    assert client.captured_generate_kwargs["files"] is None
    prompt = client.captured_generate_kwargs["prompt"]
    assert "[image] image at https://example.com/logo.png" in prompt
    assert "use the URL as the content source" in prompt
    assert "https://example.com/logo.png" in content[0].text


def test_gemini_understand_image_records_outcome_and_input_artifact_identity(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake image bytes")
    client = _FakeUnderstandClient(response_text="A TypeError dialog.", observed_backend="gemini-3-pro")
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(_call_tool("gemini_understand_image", image=str(image), model="pro"))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is True
    data = domain_result["data"]
    assert data["analysis"] == "A TypeError dialog."
    assert data["task"] == "Describe what this image shows and call out anything noteworthy."
    assert [outcome["id"] for outcome in data["inputs"]] == ["image"]
    outcome = data["inputs"][0]
    assert outcome["kind"] == "image"
    assert outcome["outcome"] == "analyzed"
    artifact = outcome["artifact"]
    assert artifact["id"].startswith("artifact_")
    assert artifact["kind"] == "image"
    assert artifact["state"] == "local"
    assert artifact["local_path"] == str(image)
    assert data["requested_model"] == "pro"
    assert data["effective_model"] == "gemini-3-pro"
    assert data["observed_backend"] == "gemini-3-pro"
    assert data["lifecycle"]["upstream_chat_id"] == "c_understand1"
    # Compatibility text repeats the outcome without contradicting it.
    assert "[image] image: analyzed" in content[0].text


def test_gemini_understand_image_blank_image_is_rejected_before_client_use(monkeypatch):
    def explode():
        raise AssertionError("get_gemini_client must not be called for a blank image")

    monkeypatch.setattr(assist, "get_gemini_client", explode)

    content = asyncio.run(_call_tool("gemini_understand_image", image="   "))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is False
    assert domain_result["error"]["code"] == "INVALID_ARGUMENT"
    assert domain_result["meta"]["verification_status"] == "input_rejected"
    assert "blank" in content[0].text


def test_gemini_understand_image_failure_is_typed_by_the_error_boundary(monkeypatch, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake image bytes")

    class _ExplodingClient:
        async def generate_content(self, **kwargs):
            raise RuntimeError("upstream exploded")

    _patch_ask_client_env(monkeypatch, _ExplodingClient())

    content = asyncio.run(_call_tool("gemini_understand_image", image=str(image)))

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is False
    assert domain_result["error"]["code"] == "INTERNAL_ERROR"
    assert domain_result["data"]["inputs"][0]["outcome"] == "failed"
    assert domain_result["meta"]["verification_status"] == "exception_classified"


# ---------------------------------------------------------------------------
# gemini_understand — typed mixed-input delegation to the shared UnderstandService
# ---------------------------------------------------------------------------


def test_gemini_understand_accepts_mixed_typed_inputs_in_one_request(monkeypatch, tmp_path):
    design = tmp_path / "design.png"
    design.write_bytes(b"fake image bytes")
    spec = tmp_path / "spec.md"
    spec.write_text("# spec")
    client = _FakeUnderstandClient(response_text="The [design] matches the [spec], and [docs] and [notes] agree.")
    captured_schedule = []
    _patch_ask_client_env(monkeypatch, client, captured_schedule=captured_schedule)

    content = asyncio.run(
        _call_tool(
            "gemini_understand",
            task="Compare the design with the implementation.",
            inputs=[
                {"id": "design", "kind": "image", "path": str(design)},
                {"id": "spec", "kind": "file", "path": str(spec)},
                {"id": "docs", "kind": "url", "url": "https://example.com/docs"},
                {"id": "notes", "kind": "text", "text": "The button should be blue."},
            ],
        )
    )

    # One shared chat request: local image/file ride the upload workflow, the URL
    # is referenced in the prompt, and text is embedded inline.
    assert client.captured_generate_kwargs is not None
    assert client.captured_generate_kwargs["files"] == [str(design), str(spec)]
    prompt = client.captured_generate_kwargs["prompt"]
    assert "Task: Compare the design with the implementation." in prompt
    assert "[design] image attached to this message" in prompt
    assert "[spec] file attached to this message" in prompt
    assert "[docs] url https://example.com/docs (use the URL as the content source)" in prompt
    assert "[notes]\nThe button should be blue." in prompt
    assert client.captured_generate_kwargs["model"] == "gemini-3-flash"
    assert captured_schedule == [
        {"retain_chat": False, "delete_after_seconds": None, "source": "gemini_understand"}
    ]

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is True
    data = domain_result["data"]
    assert data["analysis"] == "The [design] matches the [spec], and [docs] and [notes] agree."
    assert [(outcome["id"], outcome["outcome"]) for outcome in data["inputs"]] == [
        ("design", "analyzed"),
        ("spec", "analyzed"),
        ("docs", "analyzed"),
        ("notes", "analyzed"),
    ]
    assert [outcome["kind"] for outcome in data["inputs"]] == ["image", "file", "url", "text"]
    assert data["inputs"][0]["artifact"]["state"] == "local"
    assert data["inputs"][2]["artifact"]["uri"] == "https://example.com/docs"
    assert data["inputs"][3]["artifact"] is None
    assert domain_result["warnings"] == []
    assert domain_result["meta"]["details"] == {
        "service": "understanding",
        "accepted": 0,
        "analyzed": 4,
        "skipped": 0,
        "failed": 0,
    }


def test_gemini_understand_marks_only_referenced_inputs_analyzed(monkeypatch, tmp_path):
    # With multiple accepted inputs, analyzed is per-input evidence: only the
    # input the analysis references by [id] is analyzed; the rest stay accepted
    # with an acknowledgment-not-observed warning instead of overstating.
    design = tmp_path / "design.png"
    design.write_bytes(b"fake image bytes")
    client = _FakeUnderstandClient(response_text="[design] matches the implementation; nothing else was mentioned.")
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(
        _call_tool(
            "gemini_understand",
            task="Compare the design with the implementation.",
            inputs=[
                {"id": "design", "kind": "image", "path": str(design)},
                {"id": "notes", "kind": "text", "text": "The button should be blue."},
            ],
        )
    )

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is True
    data = domain_result["data"]
    assert [(outcome["id"], outcome["outcome"]) for outcome in data["inputs"]] == [
        ("design", "analyzed"),
        ("notes", "accepted"),
    ]
    assert [warning["code"] for warning in domain_result["warnings"]] == ["input_acknowledgment_not_observed"]
    assert "[notes]" in domain_result["warnings"][0]["message"]
    assert "individual acknowledgment not observed" in domain_result["warnings"][0]["message"]
    assert domain_result["meta"]["details"] == {
        "service": "understanding",
        "accepted": 1,
        "analyzed": 1,
        "skipped": 0,
        "failed": 0,
    }
    # Compatibility text repeats the truthful per-input outcomes.
    assert "- [design] image: analyzed" in content[0].text
    assert "- [notes] text: accepted" in content[0].text


def test_gemini_understand_keeps_identity_and_records_skipped_inputs(monkeypatch, tmp_path):
    design = tmp_path / "design.png"
    design.write_bytes(b"fake image bytes")
    client = _FakeUnderstandClient(response_text="Only the design was usable.")
    _patch_ask_client_env(monkeypatch, client)

    content = asyncio.run(
        _call_tool(
            "gemini_understand",
            task="Compare the inputs.",
            inputs=[
                {"id": "design", "kind": "image", "path": str(design)},
                {"id": "missing", "kind": "image", "path": str(tmp_path / "missing.png")},
                {"id": "empty", "kind": "text", "text": "   "},
                {"id": "badurl", "kind": "url", "url": "ftp://example.com/file"},
                {"id": "design", "kind": "text", "text": "duplicate id"},
            ],
        )
    )

    # The request still runs on the one usable input; nothing is silently dropped.
    assert client.captured_generate_kwargs["files"] == [str(design)]
    prompt = client.captured_generate_kwargs["prompt"]
    assert "[missing]" not in prompt
    assert "[empty]" not in prompt
    assert "[badurl]" not in prompt

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is True
    data = domain_result["data"]
    assert [(outcome["id"], outcome["kind"], outcome["outcome"]) for outcome in data["inputs"]] == [
        ("design", "image", "analyzed"),
        ("missing", "image", "skipped"),
        ("empty", "text", "skipped"),
        ("badurl", "url", "skipped"),
        ("design", "text", "skipped"),
    ]
    assert all(outcome["detail"] for outcome in data["inputs"][1:])
    assert "duplicate input id" in data["inputs"][4]["detail"]
    assert [warning["code"] for warning in domain_result["warnings"]] == ["input_skipped"] * 4
    # Compatibility text lists every input with its outcome.
    assert "- [design] image: analyzed" in content[0].text
    assert "- [missing] image: skipped" in content[0].text
    assert "- [badurl] url: skipped" in content[0].text


def test_gemini_understand_rejects_blank_task_and_empty_inputs_before_client_use(monkeypatch):
    def explode():
        raise AssertionError("get_gemini_client must not be called for rejected arguments")

    monkeypatch.setattr(assist, "get_gemini_client", explode)

    blank_task = asyncio.run(_call_tool("gemini_understand", task="   ", inputs=[{"id": "a", "kind": "text", "text": "x"}]))
    empty_inputs = asyncio.run(_call_tool("gemini_understand", task="Summarize.", inputs=[]))

    for content in (blank_task, empty_inputs):
        domain_result = content[0].meta["domain_result"]
        assert domain_result["ok"] is False
        assert domain_result["error"]["code"] == "INVALID_ARGUMENT"
        assert domain_result["data"] is None
        assert domain_result["meta"]["verification_status"] == "input_rejected"
    assert "blank" in blank_task[0].text
    assert "empty" in empty_inputs[0].text


def test_gemini_understand_reports_typed_rejection_when_every_input_is_skipped(monkeypatch):
    def explode():
        raise AssertionError("get_gemini_client must not be called when no input is usable")

    monkeypatch.setattr(assist, "get_gemini_client", explode)

    content = asyncio.run(
        _call_tool(
            "gemini_understand",
            task="Analyze this.",
            inputs=[{"id": "missing", "kind": "file", "path": "/nonexistent/spec.md"}],
        )
    )

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is False
    assert domain_result["error"]["code"] == "INVALID_ARGUMENT"
    assert domain_result["meta"]["verification_status"] == "input_rejected"
    outcomes = domain_result["data"]["inputs"]
    assert [(outcome["id"], outcome["outcome"]) for outcome in outcomes] == [("missing", "skipped")]
    assert domain_result["meta"]["details"]["skipped"] == 1
    assert "every input was skipped" in content[0].text


def test_gemini_understand_failure_marks_accepted_inputs_failed(monkeypatch, tmp_path):
    design = tmp_path / "design.png"
    design.write_bytes(b"fake image bytes")

    class _ExplodingClient:
        async def generate_content(self, **kwargs):
            raise RuntimeError("upstream exploded")

    _patch_ask_client_env(monkeypatch, _ExplodingClient())

    content = asyncio.run(
        _call_tool(
            "gemini_understand",
            task="Analyze this.",
            inputs=[{"id": "design", "kind": "image", "path": str(design)}],
        )
    )

    domain_result = content[0].meta["domain_result"]
    assert domain_result["ok"] is False
    assert domain_result["error"]["code"] == "INTERNAL_ERROR"
    assert domain_result["data"]["inputs"][0]["outcome"] == "failed"
    assert domain_result["data"]["analysis"] == ""
    assert domain_result["meta"]["details"]["failed"] == 1
    assert domain_result["meta"]["verification_status"] == "exception_classified"


def test_gemini_understand_keeps_input_artifact_identity_stable_across_calls(monkeypatch, tmp_path):
    design = tmp_path / "design.png"
    design.write_bytes(b"fake image bytes")
    client = _FakeUnderstandClient()
    _patch_ask_client_env(monkeypatch, client)

    first = asyncio.run(
        _call_tool("gemini_understand", task="Describe.", inputs=[{"id": "design", "kind": "image", "path": str(design)}])
    )
    second = asyncio.run(
        _call_tool("gemini_understand", task="Describe again.", inputs=[{"id": "design", "kind": "image", "path": str(design)}])
    )

    first_artifact = first[0].meta["domain_result"]["data"]["inputs"][0]["artifact"]
    second_artifact = second[0].meta["domain_result"]["data"]["inputs"][0]["artifact"]
    assert first_artifact["id"] == second_artifact["id"]
    assert first_artifact["id"].startswith("artifact_")
