"""file 模块的 gemini_upload_file 与 gemini_analyze_url 行为测试。

调研发现这两个工具此前仅有 1 个间接用例（test_tool_workflows.py 同时验证
路径遍历与 URL 格式无效的早退），关键行为契约零断言：

- gemini_upload_file：generate_content 入参（位置 prompt + files=[safe_path] +
  model/thinking_level/timeout=60）、analysis_prompt 默认值、response.images
  拼接、remote_chat_id 拼接、返回前缀 "✅ Successfully analyzed {filename}"、
  asyncio.TimeoutError 分支、通用 Exception 分支、schedule cleanup 入参
- gemini_analyze_url：_validate_url 早退、prompt 构造（有/无 analysis_prompt）、
  generate_content 入参（无 files）、response.images/remote_chat_id 拼接、
  返回无前缀、timeout/exception 分支

mock 边界：与 chat 同构——patch get_gemini_client / initialize_client /
cleanup_due_remote_chats / schedule_remote_chat_cleanup_from_response。
generate_content 是位置参数调用（prompt 位置 + files/model/thinking_level/
timeout 关键字），FakeClient 用 *args + **kwargs 捕获。包在 asyncio.wait_for
里，故 timeout=60 既传给 client.generate_content 也传给 wait_for。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from src.adapters.mcp_sdk import MCPServer

import src.tools.file as file_tools
from src.domain import CleanupObservation, CleanupState


@pytest.fixture(autouse=True)
def synthetic_cleanup_seams(monkeypatch):
    monkeypatch.setattr(file_tools, 'current_account_key', lambda: 'test-account')
    monkeypatch.setattr(file_tools, 'schedule_remote_chat_cleanup',
                        lambda cid, **_kwargs: CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid))


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _call_tool(mcp, name, **kwargs):
    return (await mcp.call_tool(name, kwargs)).content


class _FakeFileClient:
    """模拟 Gemini 客户端，捕获 generate_content 入参并控制返回值/异常。"""

    def __init__(self, *, response_text="analysis result", images=None,
                 response_cid="c_file1", raise_exc=None):
        self._response_text = response_text
        self._images = images or []
        self._response_cid = response_cid
        self._raise_exc = raise_exc
        self.captured_args = None
        self.captured_kwargs = None

    async def generate_content(self, *args, **kwargs):
        self.captured_args = args
        self.captured_kwargs = dict(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return SimpleNamespace(
            text=self._response_text,
            images=self._images,
            videos=[],
            media=[],
            metadata=[self._response_cid, "r_resp"],
        )


def _patch_file_client_env(monkeypatch, client, *, captured_schedule=None,
                           captured_cleanup=None):
    """统一 patch file 工具的 4 个外部接缝。"""
    monkeypatch.setattr(file_tools, "get_gemini_client", lambda: client)

    async def fake_init():
        return None
    monkeypatch.setattr(file_tools, "initialize_client", fake_init)

    async def fake_cleanup(client_arg):
        if captured_cleanup is not None:
            captured_cleanup.append(client_arg)
    monkeypatch.setattr(file_tools, "cleanup_due_remote_chats", fake_cleanup)

    async def fake_schedule(response, *, retain_chat, delete_after_seconds, source):
        if captured_schedule is not None:
            captured_schedule.append({
                "response": response,
                "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds,
                "source": source,
            })
    monkeypatch.setattr(file_tools, "finalize_remote_chat_cleanup_from_response", fake_schedule)


def _make_mcp():
    mcp = MCPServer("test")
    file_tools.register_file_tools(mcp)
    return mcp


@pytest.mark.parametrize("tool", ["gemini_upload_file", "gemini_analyze_url"])
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_file_response_interruption_persists_cleanup(monkeypatch, tmp_path, tool, failure):
    source = tmp_path / "input.txt"
    source.write_text("test")
    client = _FakeFileClient()
    _patch_file_client_env(monkeypatch, client)
    scheduled = []
    monkeypatch.setattr(file_tools, "_response_result_text", lambda _response: (_ for _ in ()).throw(failure()))
    def schedule(cid, **kwargs):
        scheduled.append((cid, kwargs))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)
    monkeypatch.setattr(file_tools, "schedule_remote_chat_cleanup", schedule)
    mcp = _make_mcp()
    args = {"file_path": str(source)} if tool == "gemini_upload_file" else {"url": "https://example.com"}

    async def run():
        return await _call_tool(mcp, tool, **args)

    if failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert scheduled == [("c_file1", {"retain_chat": False, "delete_after_seconds": 1800,
                                      "source": f"{tool}:in_progress", "_owner_account": "test-account"})]


def test_cancelled_file_result_leaves_durable_cleanup_after_loop_exit(monkeypatch, tmp_path):
    from src.remote_chat_cleanup_manager import RemoteChatCleanupManager

    db = tmp_path / "interrupted.sqlite3"
    manager = RemoteChatCleanupManager(store_path=db, account_key_provider=lambda: "test-account")
    source = tmp_path / "input.txt"
    source.write_text("test")
    _patch_file_client_env(monkeypatch, _FakeFileClient())
    monkeypatch.setattr(file_tools, "_response_result_text",
                        lambda _response: (_ for _ in ()).throw(asyncio.CancelledError()))
    monkeypatch.setattr(file_tools, "schedule_remote_chat_cleanup", manager.schedule_cleanup_result)
    mcp = _make_mcp()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_call_tool(mcp, "gemini_upload_file", file_path=str(source), delete_after_seconds=60))

    restarted = RemoteChatCleanupManager(store_path=db, account_key_provider=lambda: "test-account")
    assert "c_file1" in restarted.list_pending_cleanup()


# ---------------------------------------------------------------------------
# gemini_upload_file — 早退
# ---------------------------------------------------------------------------


def test_upload_file_invalid_path_short_circuits_before_client(monkeypatch):
    """无效文件路径 → 在 get_gemini_client 调用前早退。"""
    def explode():
        raise AssertionError("get_gemini_client should not be called on invalid path")
    monkeypatch.setattr(file_tools, "get_gemini_client", explode)
    monkeypatch.setattr(file_tools, "finalize_remote_chat_cleanup_from_response",
                        lambda *a, **kw: None)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file",
                                file_path="/nonexistent/missing.png")

    result = asyncio.run(run())
    assert result[0].text.startswith("❌")
    assert "未找到" in result[0].text


# ---------------------------------------------------------------------------
# gemini_upload_file — happy path
# ---------------------------------------------------------------------------


def test_upload_file_passes_positional_prompt_and_files_to_client(monkeypatch, tmp_path):
    """generate_content 收到位置 prompt + files=[safe_path] + model/thinking_level/timeout。"""
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"pdf content")
    client = _FakeFileClient()
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file",
                                file_path=str(target), model="pro",
                                thinking_level="extended")

    asyncio.run(run())
    # 位置参数：prompt
    assert client.captured_args == ("Please analyze this file and tell me what you see.",)
    # 关键字参数
    assert client.captured_kwargs["files"] == [str(target.resolve())]
    assert client.captured_kwargs["model"] == "gemini-3-pro"
    assert client.captured_kwargs["thinking_level"] == "extended"
    assert client.captured_kwargs["timeout"] == 60


def test_upload_file_uses_custom_analysis_prompt_when_provided(monkeypatch, tmp_path):
    """analysis_prompt 自定义 → 位置 prompt 用传入值。"""
    target = tmp_path / "doc.pdf"
    target.write_bytes(b"x")
    client = _FakeFileClient()
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file",
                                file_path=str(target),
                                analysis_prompt="Summarize this document")

    asyncio.run(run())
    assert client.captured_args == ("Summarize this document",)


def test_upload_file_returns_success_prefix_with_filename(monkeypatch, tmp_path):
    """返回文本前缀 '✅ Successfully analyzed {filename}'。"""
    target = tmp_path / "report.pdf"
    target.write_bytes(b"x")
    client = _FakeFileClient(response_text="it is a report")
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file", file_path=str(target))

    result = asyncio.run(run())
    assert result[0].text.startswith("✅ Successfully analyzed report.pdf")
    assert "it is a report" in result[0].text


def test_upload_file_exposes_verified_input_artifact_metadata(monkeypatch, tmp_path):
    target = tmp_path / "report.pdf"
    target.write_bytes(b"pdf content")
    client = _FakeFileClient(response_text="ok", images=[])
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(
            mcp,
            "gemini_upload_file",
            file_path=str(target),
            model="pro",
        )

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    artifact = domain["data"]["input_artifacts"][0]
    assert domain["ok"] is True
    assert domain["data"]["state"] == "local"
    assert domain["meta"]["verification_status"] == "input_artifact_verified"
    assert artifact["kind"] == "file"
    assert artifact["state"] == "local"
    assert artifact["local_path"] == str(target.resolve())
    assert artifact["mime_type"] == "application/pdf"
    assert artifact["size_bytes"] == len(b"pdf content")
    assert artifact["verification"]["status"] == "verified"
    assert artifact["requested_backend"] == "pro"
    assert artifact["request_model"] == "gemini-3-pro"
    assert artifact["effective_backend"] == "gemini-3-pro"


def test_upload_file_appends_images_block_when_response_has_images(monkeypatch, tmp_path):
    """response.images 非空 → 拼接 '📷 Images in response:' + 编号 + title + url。"""
    target = tmp_path / "img.png"
    target.write_bytes(b"x")
    client = _FakeFileClient(
        response_text="ok",
        images=[SimpleNamespace(title="Chart", url="https://x/chart.png")],
    )
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file", file_path=str(target))

    text = asyncio.run(run())[0].text
    assert "📷 Images in response:" in text
    assert "1. Chart: https://x/chart.png" in text


def test_upload_file_appends_remote_chat_id(monkeypatch, tmp_path):
    """response.metadata 含 c_ 前缀 → 拼接 'Remote chat ID: ...'。"""
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    client = _FakeFileClient(response_cid="c_upload42")
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file", file_path=str(target))

    text = asyncio.run(run())[0].text
    assert "Remote chat ID: c_upload42" in text


def test_upload_file_schedules_cleanup_with_correct_args(monkeypatch, tmp_path):
    """schedule cleanup 接收 response + retain/delete/source。"""
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    client = _FakeFileClient()
    schedule_calls = []
    _patch_file_client_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file",
                                file_path=str(target), retain_chat=True,
                                delete_after_seconds=120)

    asyncio.run(run())
    assert len(schedule_calls) == 1
    call = schedule_calls[0]
    assert call["retain_chat"] is True
    assert call["delete_after_seconds"] == 120
    assert call["source"] == "gemini_upload_file"


def test_upload_file_calls_cleanup_due_remote_chats_with_client(monkeypatch, tmp_path):
    """cleanup_due_remote_chats 接收 client 对象。"""
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    client = _FakeFileClient()
    captured_cleanup = []
    _patch_file_client_env(monkeypatch, client, captured_cleanup=captured_cleanup)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file", file_path=str(target))

    asyncio.run(run())
    assert captured_cleanup == [client]


# ---------------------------------------------------------------------------
# gemini_upload_file — 异常分支
# ---------------------------------------------------------------------------


def test_upload_file_timeout_returns_timeout_message(monkeypatch, tmp_path):
    """generate_content 抛 asyncio.TimeoutError → 返回 '文件分析超时'。"""
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    client = _FakeFileClient(raise_exc=asyncio.TimeoutError())
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file", file_path=str(target))

    result = asyncio.run(run())
    assert "文件分析超时" in result[0].text
    assert result[0].text.startswith("❌")


def test_upload_file_timeout_keeps_failed_artifact_contract(monkeypatch, tmp_path):
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    client = _FakeFileClient(raise_exc=asyncio.TimeoutError())
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file", file_path=str(target))

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    assert domain["ok"] is False
    assert domain["error"]["code"] == "TIMED_OUT"
    assert domain["data"]["state"] == "failed"
    assert domain["data"]["input_artifacts"][0]["local_path"] == str(target.resolve())


def test_upload_file_generic_exception_returns_error_message(monkeypatch, tmp_path):
    """Raw upstream exception details do not appear in tool text."""
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    client = _FakeFileClient(raise_exc=RuntimeError("network down"))
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file", file_path=str(target))

    result = asyncio.run(run())
    assert "文件分析失败" in result[0].text and "network down" not in result[0].text


def test_upload_file_exception_skips_schedule_cleanup(monkeypatch, tmp_path):
    """异常分支不调 schedule cleanup（在 try 块外）。"""
    target = tmp_path / "f.txt"
    target.write_bytes(b"x")
    client = _FakeFileClient(raise_exc=RuntimeError("boom"))
    schedule_calls = []
    _patch_file_client_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_upload_file", file_path=str(target))

    asyncio.run(run())
    assert schedule_calls == []


# ---------------------------------------------------------------------------
# gemini_analyze_url — 早退
# ---------------------------------------------------------------------------


def test_analyze_url_invalid_url_short_circuits_before_client(monkeypatch):
    """无效 URL → 在 get_gemini_client 调用前早退。"""
    def explode():
        raise AssertionError("get_gemini_client should not be called on invalid url")
    monkeypatch.setattr(file_tools, "get_gemini_client", explode)
    monkeypatch.setattr(file_tools, "finalize_remote_chat_cleanup_from_response",
                        lambda *a, **kw: None)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url", url="not-a-url")

    result = asyncio.run(run())
    assert result[0].text.startswith("❌")
    assert "格式无效" in result[0].text


# ---------------------------------------------------------------------------
# gemini_analyze_url — prompt 构造
# ---------------------------------------------------------------------------


def test_analyze_url_default_prompt_without_analysis_prompt(monkeypatch):
    """analysis_prompt=None → prompt = 'Please analyze the content at this URL: {url}'。"""
    client = _FakeFileClient()
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url",
                                url="https://example.com/article")

    asyncio.run(run())
    prompt = client.captured_args[0]
    assert prompt == "Please analyze the content at this URL: https://example.com/article"


def test_analyze_url_custom_prompt_has_exact_url_structure(monkeypatch):
    """analysis_prompt 自定义 → prompt 精确保留用户提示和独立 URL 行。"""
    client = _FakeFileClient()
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url",
                                url="https://example.com/video",
                                analysis_prompt="Summarize this video")

    asyncio.run(run())
    prompt = client.captured_args[0]
    assert prompt == (
        "Summarize this video\n\n"
        "URL: https://example.com/video\n"
        "Use the URL above as the content source for your answer."
    )


def test_analyze_url_passes_model_and_thinking_level_to_client(monkeypatch):
    """generate_content 收到 model_name + thinking_level + timeout=60（无 files）。"""
    client = _FakeFileClient()
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url",
                                url="https://x.com", model="pro",
                                thinking_level="extended")

    asyncio.run(run())
    assert client.captured_kwargs["model"] == "gemini-3-pro"
    assert client.captured_kwargs["thinking_level"] == "extended"
    assert client.captured_kwargs["timeout"] == 60
    assert "files" not in client.captured_kwargs  # analyze_url 不传 files


# ---------------------------------------------------------------------------
# gemini_analyze_url — 返回值与拼接
# ---------------------------------------------------------------------------


def test_analyze_url_returns_response_text_without_success_prefix(monkeypatch):
    """返回 result_text（无 '✅' 前缀，与 upload_file 不同）。"""
    client = _FakeFileClient(response_text="url content summary")
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url", url="https://x.com")

    text = asyncio.run(run())[0].text
    assert text.startswith("url content summary")
    assert not text.startswith("✅")


def test_analyze_url_exposes_remote_source_artifact(monkeypatch):
    client = _FakeFileClient(response_text="summary", images=[])
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(
            mcp,
            "gemini_analyze_url",
            url="https://example.com/article",
        )

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    artifact = domain["data"]["input_artifacts"][0]
    assert domain["ok"] is True
    assert domain["data"]["state"] == "remote"
    assert artifact["kind"] == "webpage"
    assert artifact["state"] == "remote"
    assert artifact["uri"] == "https://example.com/article"
    assert artifact["verification"]["status"] == "unverified"
    assert artifact["verification"]["methods"] == ["input_uri_provided"]


def test_analyze_url_appends_images_block(monkeypatch):
    """response.images 非空 → 拼接 images block。"""
    client = _FakeFileClient(
        response_text="ok",
        images=[SimpleNamespace(title="Pic", url="https://x/pic.png")],
    )
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url", url="https://x.com")

    text = asyncio.run(run())[0].text
    assert "📷 Images in response:" in text
    assert "1. Pic: https://x/pic.png" in text


def test_analyze_url_appends_remote_chat_id(monkeypatch):
    """response.metadata 含 c_ 前缀 → 拼接 remote_chat_id。"""
    client = _FakeFileClient(response_cid="c_url99")
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url", url="https://x.com")

    text = asyncio.run(run())[0].text
    assert "Remote chat ID: c_url99" in text


def test_analyze_url_schedules_cleanup_with_correct_source(monkeypatch):
    """schedule cleanup source='gemini_analyze_url'。"""
    client = _FakeFileClient()
    schedule_calls = []
    _patch_file_client_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url", url="https://x.com",
                                retain_chat=True, delete_after_seconds=300)

    asyncio.run(run())
    assert schedule_calls[0]["source"] == "gemini_analyze_url"
    assert schedule_calls[0]["retain_chat"] is True
    assert schedule_calls[0]["delete_after_seconds"] == 300


# ---------------------------------------------------------------------------
# gemini_analyze_url — 异常分支
# ---------------------------------------------------------------------------


def test_analyze_url_timeout_returns_timeout_message(monkeypatch):
    """generate_content 抛 asyncio.TimeoutError → 返回 'URL 分析超时'。"""
    client = _FakeFileClient(raise_exc=asyncio.TimeoutError())
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url", url="https://x.com")

    result = asyncio.run(run())
    assert "URL 分析超时" in result[0].text
    assert result[0].text.startswith("❌")


def test_analyze_url_generic_exception_returns_error_message(monkeypatch):
    """Raw upstream exception details do not appear in tool text."""
    client = _FakeFileClient(raise_exc=ValueError("bad url response"))
    _patch_file_client_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_analyze_url", url="https://x.com")

    result = asyncio.run(run())
    assert "URL 分析失败" in result[0].text and "bad url response" not in result[0].text


@pytest.mark.parametrize('url', ['ftp://example.test/file', 'file://example.test/path',
                                  'https://user:secret@example.test/', 'https://example.test:bad/'])
def test_analyze_url_rejects_unsupported_or_invalid_urls_before_client(monkeypatch, url):
    monkeypatch.setattr(file_tools, 'get_gemini_client', lambda: (_ for _ in ()).throw(AssertionError('no init')))
    mcp = _make_mcp()
    result = asyncio.run(_call_tool(mcp, 'gemini_analyze_url', url=url))
    assert result[0].meta['domain_result']['error']['code'] == 'INVALID_ARGUMENT'


@pytest.mark.parametrize('tool', ['gemini_upload_file', 'gemini_analyze_url'])
def test_file_tools_initializer_error_is_typed(monkeypatch, tmp_path, tool):
    target = tmp_path / 'input.txt'
    target.write_text('synthetic')
    monkeypatch.setattr(file_tools, 'get_gemini_client', lambda: object())
    monkeypatch.setattr(file_tools, 'initialize_client', AsyncMock(side_effect=PermissionError('synthetic credential')))
    mcp = _make_mcp()
    kwargs = {'file_path': str(target)} if tool == 'gemini_upload_file' else {'url': 'https://example.test/'}
    result = asyncio.run(_call_tool(mcp, tool, **kwargs))
    domain = result[0].meta['domain_result']
    assert domain['ok'] is False and domain['error']['code'] == 'AUTH_REQUIRED'
    assert 'synthetic credential' not in result[0].text


@pytest.mark.parametrize('tool', ['gemini_upload_file', 'gemini_analyze_url'])
@pytest.mark.parametrize('failure', [RuntimeError, asyncio.CancelledError])
@pytest.mark.parametrize('retain', [False, True])
def test_early_identity_survives_sdk_rollback_and_failure(monkeypatch, tmp_path, tool, failure, retain):
    from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
    from src.thinking_client import _ObservedChatSession
    db = tmp_path / 'early.sqlite3'
    manager = RemoteChatCleanupManager(store_path=db, account_key_provider=lambda: 'test-account')
    target = tmp_path / 'input.txt'
    target.write_text('synthetic')
    calls = []
    class Client:
        def create_cleanup_chat(self):
            return _ObservedChatSession(geminiclient=self)
        async def generate_content(self, _prompt, **kwargs):
            calls.append('sent')
            chat = kwargs['chat']
            chat.cid = 'c_early'
            chat.cid = ''  # SDK rolls metadata back before propagating.
            raise failure('synthetic')
    _patch_file_client_env(monkeypatch, Client())
    monkeypatch.setattr(file_tools, 'schedule_remote_chat_cleanup', manager.schedule_cleanup_result)
    finalizer = AsyncMock()
    monkeypatch.setattr(file_tools, 'finalize_remote_chat_cleanup_from_response', finalizer)
    args = {'file_path': str(target)} if tool == 'gemini_upload_file' else {'url': 'https://example.test/'}
    args.update(retain_chat=retain, delete_after_seconds=20)
    if failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_call_tool(_make_mcp(), tool, **args))
    else:
        result = asyncio.run(_call_tool(_make_mcp(), tool, **args))
        assert not result[0].meta['domain_result']['ok']
    with manager._store._connect() as db:
        row = db.execute('select state,delete_at from cleanup where chat_id=?', ('c_early',)).fetchone()
    assert row is not None and row['state'] == ('retained' if retain else 'pending')
    if not retain:
        import time
        assert row['delete_at'] - time.time() > 1700
    assert calls == ['sent']
    finalizer.assert_not_awaited()


def test_early_identity_remains_bound_to_request_account_after_switch(monkeypatch):
    from src.services.cleanup_scope import observe_upstream_identity
    account = ['account-a']
    records = []
    class Client:
        def create_cleanup_chat(self):
            return SimpleNamespace(cid='', metadata=[])
        async def generate_content(self, _prompt, **kwargs):
            chat = kwargs['chat']
            account[0] = 'account-b'
            chat.cid, chat.metadata = 'c_late', ['c_late']
            observe_upstream_identity(chat)
            return SimpleNamespace(text='answer', images=[], videos=[], media=[], metadata=['c_late'])
    _patch_file_client_env(monkeypatch, Client())
    monkeypatch.setattr(file_tools, 'current_account_key', lambda: account[0])
    def schedule(cid, **kwargs):
        records.append((cid, kwargs))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)
    monkeypatch.setattr(file_tools, 'schedule_remote_chat_cleanup', schedule)
    finalizer = AsyncMock()
    monkeypatch.setattr(file_tools, 'finalize_remote_chat_cleanup_from_response', finalizer)
    result = asyncio.run(_call_tool(_make_mcp(), 'gemini_analyze_url', url='https://example.test/'))
    assert result[0].meta['domain_result']['error']['code'] == 'AUTH_REQUIRED'
    assert len(records) == 1 and records[0][1]['_owner_account'] == 'account-a'
    finalizer.assert_not_awaited()


def test_early_cleanup_registration_failure_is_reported(monkeypatch):
    from src.services.cleanup_scope import observe_upstream_identity
    class Client:
        def create_cleanup_chat(self):
            return SimpleNamespace(cid='', metadata=[])
        async def generate_content(self, _prompt, **kwargs):
            chat = kwargs['chat']
            chat.cid = 'c_unregistered'
            observe_upstream_identity(chat)
            chat.cid = ''
            raise RuntimeError('synthetic')
    _patch_file_client_env(monkeypatch, Client())
    monkeypatch.setattr(file_tools, 'schedule_remote_chat_cleanup', lambda *_a, **_kw:
                        CleanupObservation(state=CleanupState.FAILED, upstream_chat_id='c_unregistered'))
    result = asyncio.run(_call_tool(_make_mcp(), 'gemini_analyze_url', url='https://example.test/'))
    domain = result[0].meta['domain_result']
    assert not domain['ok']
    assert any(w['code'] == 'cleanup_registration_unverified' for w in domain['warnings'])
    assert domain['meta']['details']['cleanup_registration_failed_chat_ids'] == ['c_unregistered']
