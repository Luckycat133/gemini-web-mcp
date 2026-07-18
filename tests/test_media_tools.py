"""media 模块的 gemini_generate_media 与 gemini_generate_music 行为契约测试。

调研发现这两个工具此前仅有 5 个 happy/edge 间接用例（test_tool_workflows.py），
关键集成契约零断言：

- schedule_remote_chat_cleanup_from_response 的 source 字符串（应为
  "gemini_generate_media:{media_type}"，gemini_generate_music 转发后 source 仍为
  "gemini_generate_media:music" 而非 "gemini_generate_music"——潜在不一致）
- cleanup_due_remote_chats 接收 client 对象
- _media_timeout 默认值（image=180 / 其他=600）+ timeout_seconds 覆盖
- _set_client_timeouts / _restore_client_timeouts 在 finally 块往返
- 异常分支不调 schedule cleanup，但空响应仍调（与 chat_stream 的
  `if final_response:` 守卫不同）
- gemini_generate_music 默认 thinking_level="extended"（与 generate_media 默认
  "standard" 不同），导致 music+pro 默认走 Lyria 3 Pro
- music 回收路径：response.media 为空时调 _fetch_music_media_from_chat，异常吞咽
- 后端路由：image 恒用 gemini-3-flash；music 非 pro=Lyria 3 / pro+standard=Lyria 3
  / pro+extended=Lyria 3 Pro；video=Gemini Web default

纯 helper（_media_timeout / _set_client_timeouts / _media_from_music_card /
_safe_media_filename / _prepend_backend_note / resolve_media_request）已在
test_tool_helpers.py 充分覆盖，本文件专注工具集成层。

mock 边界：4 个 client_wrapper 接缝（get_gemini_client / initialize_client /
cleanup_due_remote_chats / schedule_remote_chat_cleanup_from_response）+ 可选
_probe_duration（隔离 ffprobe subprocess）。parse_response 走真实实现。
"""

import asyncio
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

import src.tools.media as media_tools


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _call_tool(mcp, name, **kwargs):
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


class _FakeMedia:
    """模拟 GeneratedMedia，捕获 save 入参。"""

    def __init__(self, *, title="theme", mp3_url="https://x/y.mp3", url="",
                 save_return=None):
        self.title = title
        self.mp3_url = mp3_url
        self.url = url
        self._save_return = save_return or {"audio": "/tmp/fake.mp3"}
        self.captured_save_kwargs = None

    async def save(self, **kwargs):
        self.captured_save_kwargs = dict(kwargs)
        return self._save_return


class _FakeMediaClient:
    """模拟 Gemini 客户端，捕获 generate_content 入参并控制返回值/异常。

    与 _FakeChatClient / _FakeFileClient 不同：media 全用关键字传参，
    且需要可控的 timeout / watchdog_timeout 属性以验证 _set_client_timeouts。
    """

    def __init__(self, *, response_text="done", images=None, videos=None,
                 media=None, response_cid="c_media1", raise_exc=None,
                 timeout=100.0, watchdog_timeout=200.0):
        self._response_text = response_text
        self._images = images or []
        self._videos = videos or []
        self._media = media or []
        self._response_cid = response_cid
        self._raise_exc = raise_exc
        self.timeout = timeout
        self.watchdog_timeout = watchdog_timeout
        self.captured_generate_kwargs = None
        self.captured_generate_during_timeout = None
        self.last_response = None

    async def generate_content(self, **kwargs):
        self.captured_generate_kwargs = dict(kwargs)
        # 捕获调用期间的 client.timeout（验证 _set_client_timeouts 写回）
        self.captured_generate_during_timeout = self.timeout
        if self._raise_exc is not None:
            raise self._raise_exc
        response = SimpleNamespace(
            text=self._response_text,
            images=self._images,
            videos=self._videos,
            media=self._media,
            metadata=[self._response_cid, "r_response"],
        )
        self.last_response = response
        return response


def _patch_media_env(monkeypatch, client, *, captured_schedule=None,
                     captured_cleanup=None, probe_duration=None,
                     fetch_music=None):
    """统一 patch media 工具的外部接缝。"""
    monkeypatch.setattr(media_tools, "get_gemini_client", lambda: client)

    async def fake_init():
        return None
    monkeypatch.setattr(media_tools, "initialize_client", fake_init)

    async def fake_cleanup(client_arg):
        if captured_cleanup is not None:
            captured_cleanup.append(client_arg)
    monkeypatch.setattr(media_tools, "cleanup_due_remote_chats", fake_cleanup)

    def fake_schedule(response, *, retain_chat, delete_after_seconds, source):
        if captured_schedule is not None:
            captured_schedule.append({
                "response": response,
                "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds,
                "source": source,
            })
    monkeypatch.setattr(media_tools, "schedule_remote_chat_cleanup_from_response",
                        fake_schedule)

    if probe_duration is not None:
        monkeypatch.setattr(media_tools, "_probe_duration", probe_duration)

    if fetch_music is not None:
        monkeypatch.setattr(media_tools, "_fetch_music_media_from_chat", fetch_music)


def _make_mcp():
    mcp = FastMCP("test")
    media_tools.register_media_tools(mcp)
    return mcp


# ---------------------------------------------------------------------------
# A. 早退
# ---------------------------------------------------------------------------


def test_generate_media_invalid_image_path_short_circuits_before_client(monkeypatch):
    """image_path 指向不存在文件 → 在 get_gemini_client 调用前早退。"""
    def explode():
        raise AssertionError("get_gemini_client should not be called on invalid image_path")
    monkeypatch.setattr(media_tools, "get_gemini_client", explode)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image",
                                image_path="/nonexistent/x.png")

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0].text.startswith("❌")


# ---------------------------------------------------------------------------
# B. 参数注入
# ---------------------------------------------------------------------------


def test_generate_media_passes_all_fields_to_generate_content(monkeypatch):
    """generate_content 收到 prompt 模板/files/model/thinking_level/timeout。"""
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="sunset", media_type="image",
                                thinking_level="extended")

    asyncio.run(run())
    kwargs = client.captured_generate_kwargs
    assert kwargs["prompt"] == "Generate an image. Prompt: sunset"
    assert kwargs["files"] is None
    assert kwargs["model"] == "gemini-3-flash"  # image 恒用 flash
    assert kwargs["thinking_level"] == "extended"
    assert kwargs["timeout"] == 180  # image 默认


def test_generate_media_passes_safe_image_path_as_files(monkeypatch, tmp_path):
    """有效 image_path → files=[resolved_abs_path]。"""
    img = tmp_path / "ref.png"
    img.write_bytes(b"x")

    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="remix", media_type="image",
                                image_path=str(img))

    asyncio.run(run())
    assert client.captured_generate_kwargs["files"] == [str(img)]


def test_generate_media_calls_cleanup_due_remote_chats_with_client(monkeypatch):
    """cleanup_due_remote_chats 接收 get_gemini_client 返回的 client 对象。"""
    client = _FakeMediaClient()
    captured_cleanup = []
    _patch_media_env(monkeypatch, client, captured_cleanup=captured_cleanup)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image")

    asyncio.run(run())
    assert captured_cleanup == [client]


# ---------------------------------------------------------------------------
# C. timeout 计算
# ---------------------------------------------------------------------------


def test_generate_media_image_default_timeout_180(monkeypatch):
    """image + timeout_seconds=None → timeout=180。"""
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image")

    asyncio.run(run())
    assert client.captured_generate_kwargs["timeout"] == 180


def test_generate_media_music_default_timeout_600(monkeypatch):
    """music + timeout_seconds=None → timeout=600。"""
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music")

    asyncio.run(run())
    assert client.captured_generate_kwargs["timeout"] == 600


def test_generate_media_video_default_timeout_600(monkeypatch):
    """video + timeout_seconds=None → timeout=600。"""
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="video")

    asyncio.run(run())
    assert client.captured_generate_kwargs["timeout"] == 600


def test_generate_media_explicit_timeout_overrides_default(monkeypatch):
    """timeout_seconds=42 → timeout=42（任意 media_type）。"""
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image",
                                timeout_seconds=42)

    asyncio.run(run())
    assert client.captured_generate_kwargs["timeout"] == 42


@pytest.mark.parametrize("bad_value", [0, -5])
def test_generate_media_zero_or_negative_timeout_falls_back_to_default(monkeypatch, bad_value):
    """timeout_seconds<=0 → 用默认值（image=180）。

    _media_timeout 的守卫是 `if timeout_seconds and timeout_seconds > 0`，
    故 0 和负数都回退到默认。
    """
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image",
                                timeout_seconds=bad_value)

    asyncio.run(run())
    assert client.captured_generate_kwargs["timeout"] == 180


# ---------------------------------------------------------------------------
# D. client.timeout / watchdog_timeout 临时调整
# ---------------------------------------------------------------------------


def test_generate_media_sets_and_restores_client_timeouts(monkeypatch):
    """请求期间 client.timeout 提升到 max(previous, requested)；返回后恢复。"""
    client = _FakeMediaClient(timeout=100.0, watchdog_timeout=200.0)
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image",
                                timeout_seconds=300)

    asyncio.run(run())
    # 调用期间被提升到 300
    assert client.captured_generate_during_timeout == 300.0
    # 返回后恢复原值
    assert client.timeout == 100.0
    assert client.watchdog_timeout == 200.0


def test_generate_media_restores_timeouts_even_on_exception(monkeypatch):
    """异常分支 finally 块仍 restore client.timeout。"""
    client = _FakeMediaClient(timeout=100.0, watchdog_timeout=200.0,
                              raise_exc=RuntimeError("boom"))
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image",
                                timeout_seconds=300)

    asyncio.run(run())
    assert client.timeout == 100.0
    assert client.watchdog_timeout == 200.0


def test_generate_media_restores_timeouts_on_timeout_exception(monkeypatch):
    """asyncio.TimeoutError 分支 finally 块也 restore。"""
    client = _FakeMediaClient(timeout=100.0, watchdog_timeout=200.0,
                              raise_exc=asyncio.TimeoutError())
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image",
                                timeout_seconds=300)

    asyncio.run(run())
    assert client.timeout == 100.0
    assert client.watchdog_timeout == 200.0


# ---------------------------------------------------------------------------
# E. 后端路由（resolve_media_request 集成）
# ---------------------------------------------------------------------------


def test_generate_media_image_always_routes_to_flash_regardless_of_model(monkeypatch):
    """image + model=pro → 实际 model_name=gemini-3-flash（image 恒用 flash）。"""
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image", model="pro")

    asyncio.run(run())
    assert client.captured_generate_kwargs["model"] == "gemini-3-flash"


def test_generate_media_image_returns_nano_banana_2_backend_label(monkeypatch):
    """image → 返回文本含 '后端: Nano Banana 2' 与 Pro redo 说明。"""
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image")

    result = asyncio.run(run())
    text = result[0].text
    assert "后端: Nano Banana 2" in text
    assert "Pro redo 属于网页生成后的二次操作" in text


def test_generate_media_music_flash_routes_to_lyria_3(monkeypatch):
    """music + flash → 后端 Lyria 3（非 Pro），不含 'Lyria 3 Pro'。"""
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music", model="flash")

    result = asyncio.run(run())
    assert "后端: Lyria 3" in result[0].text
    assert "Lyria 3 Pro" not in result[0].text


def test_generate_media_music_pro_standard_routes_to_lyria_3(monkeypatch):
    """music + pro + standard → 后端 Lyria 3（非 Pro）。"""
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music", model="pro",
                                thinking_level="standard")

    result = asyncio.run(run())
    assert "后端: Lyria 3" in result[0].text
    assert "Lyria 3 Pro" not in result[0].text


def test_generate_media_music_pro_extended_routes_to_lyria_3_pro(monkeypatch):
    """music + pro + extended → 后端 Lyria 3 Pro。"""
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music", model="pro",
                                thinking_level="extended")

    result = asyncio.run(run())
    assert "后端: Lyria 3 Pro" in result[0].text


def test_generate_media_video_routes_to_default_backend(monkeypatch):
    """video → 后端 'Gemini Web default'。"""
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="video")

    result = asyncio.run(run())
    assert "后端: Gemini Web default" in result[0].text


# ---------------------------------------------------------------------------
# F. 异常分支
# ---------------------------------------------------------------------------


def test_generate_media_timeout_returns_timeout_message_with_backend(monkeypatch):
    """asyncio.TimeoutError → 返回含后端标签 + '生成超时' + '可增大 timeout_seconds'。"""
    client = _FakeMediaClient(raise_exc=asyncio.TimeoutError())
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image")

    result = asyncio.run(run())
    text = result[0].text
    assert "后端: Nano Banana 2" in text
    assert "❌ image 生成超时: 180s" in text
    assert "可增大 timeout_seconds" in text


def test_generate_media_generic_exception_returns_error_message_with_backend(monkeypatch):
    """RuntimeError → 返回含后端标签 + '生成失败' + '通用 generate_content' 说明。"""
    client = _FakeMediaClient(raise_exc=RuntimeError("upstream aborted"))
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="video")

    result = asyncio.run(run())
    text = result[0].text
    assert "后端: Gemini Web default" in text
    assert "❌ video 生成失败: upstream aborted" in text
    assert "通用 generate_content" in text


def test_generate_media_exception_skips_schedule_cleanup(monkeypatch):
    """异常分支不调 schedule_remote_chat_cleanup_from_response。

    与成功路径（含空响应）不同——空响应仍调 cleanup。
    """
    client = _FakeMediaClient(raise_exc=RuntimeError("boom"))
    schedule_calls = []
    _patch_media_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image")

    asyncio.run(run())
    assert schedule_calls == []


# ---------------------------------------------------------------------------
# G. 空响应 vs 成功路径
# ---------------------------------------------------------------------------


def test_generate_media_empty_response_still_schedules_cleanup(monkeypatch):
    """空响应（text='', media=[], images=[], videos=[]，无 remote_chat_id）→ 仍调 schedule cleanup。

    这是与 gemini_chat_stream 的 `if final_response:` 守卫不同的行为：
    media 工具的 schedule 在空响应检查前执行（line 310 先于 line 316）。

    注意：parse_response 会从 metadata[0] 提取 remote_chat_id 追加到文本末尾，
    故若 response 有 cid，parsed[0].text 非空，空响应分支不触发。本测试用
    response_cid=None 使 extract_remote_chat_id 返回 None，从而 parsed[0].text
    真正为空，触发警告分支。
    """
    client = _FakeMediaClient(response_text="", media=[], images=[], videos=[],
                              response_cid=None)
    schedule_calls = []
    _patch_media_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image")

    result = asyncio.run(run())
    text = result[0].text
    assert "⚠️ image 请求已完成，但没有返回文本、图片、视频或音乐资源" in text
    assert len(schedule_calls) == 1  # 关键：空响应仍调 cleanup


def test_generate_media_appends_remote_chat_id(monkeypatch):
    """response.metadata[0]='c_xxx' → 文本含 'Remote chat ID: c_xxx'。"""
    client = _FakeMediaClient(response_text="ok", response_cid="c_media_xyz")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image")

    result = asyncio.run(run())
    assert "Remote chat ID: c_media_xyz" in result[0].text


def test_generate_media_renders_response_media_with_lyria_label(monkeypatch):
    """music + pro+extended + response.media 非空 → 文本含 '🎵 音乐 1 (Lyria 3 Pro)'。

    parse_response 收到 effective_alias='pro'，故音乐块标 'Lyria 3 Pro'。
    """
    media = _FakeMedia(title="song")
    client = _FakeMediaClient(response_text="ok", media=[media])
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music", model="pro",
                                thinking_level="extended")

    result = asyncio.run(run())
    assert "🎵 音乐 1 (Lyria 3 Pro)" in result[0].text


# ---------------------------------------------------------------------------
# H. schedule cleanup source 字符串
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("media_type,expected_source", [
    ("image", "gemini_generate_media:image"),
    ("video", "gemini_generate_media:video"),
    ("music", "gemini_generate_media:music"),
])
def test_generate_media_schedules_cleanup_with_source_per_media_type(
    monkeypatch, media_type, expected_source):
    """source = 'gemini_generate_media:{media_type}'。"""
    client = _FakeMediaClient(response_text="ok")
    schedule_calls = []
    _patch_media_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type=media_type)

    asyncio.run(run())
    assert schedule_calls[0]["source"] == expected_source


def test_generate_media_schedules_cleanup_with_response_retain_delete(monkeypatch):
    """schedule cleanup 接收 response + retain/delete 参数。"""
    client = _FakeMediaClient(response_text="ok")
    schedule_calls = []
    _patch_media_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image",
                                retain_chat=True, delete_after_seconds=300)

    asyncio.run(run())
    call = schedule_calls[0]
    assert call["response"] is client.last_response
    assert call["retain_chat"] is True
    assert call["delete_after_seconds"] == 300


# ---------------------------------------------------------------------------
# I. music 回收路径
# ---------------------------------------------------------------------------


def test_generate_media_music_recovers_media_when_response_media_empty(monkeypatch, tmp_path):
    """music + response.media=[] → 调 _fetch_music_media_from_chat 恢复 media。

    恢复的 media 用于 _save_generated_media（media_items=recovered_media or None）。
    """
    client = _FakeMediaClient(response_text="ok", media=[])
    recovered = _FakeMedia(title="recovered song")

    async def fake_fetch(client_arg, cid):
        assert client_arg is client
        assert cid == "c_media1"  # 从 response.metadata[0] 提取
        return [recovered]
    _patch_media_env(monkeypatch, client, fetch_music=fake_fetch)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music",
                                output_dir=str(tmp_path), filename="rec")

    result = asyncio.run(run())
    text = result[0].text
    assert "Saved files:" in text
    # FakeMedia.save 默认返回 {"audio": "/tmp/fake.mp3"}
    assert "audio: /tmp/fake.mp3" in text


def test_generate_media_music_recovery_failure_logs_warning_and_continues(monkeypatch):
    """_fetch_music_media_from_chat 抛异常 → 工具不崩溃，saved_lines=[]。

    异常被 try/except 吞咽（line 298-299），仅 logger.warning。
    """
    client = _FakeMediaClient(response_text="ok", media=[])

    async def fake_fetch(client_arg, cid):
        raise RuntimeError("batch_execute failed")
    _patch_media_env(monkeypatch, client, fetch_music=fake_fetch)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music")

    result = asyncio.run(run())
    # 不崩溃，返回正常 parsed 文本（无 Saved files）
    assert "后端: Lyria 3" in result[0].text
    assert "Saved files:" not in result[0].text


def test_generate_media_non_music_skips_recovery(monkeypatch):
    """image/video 即使 response.media=[] 也不调 _fetch_music_media_from_chat。"""
    client = _FakeMediaClient(response_text="ok", media=[])
    fetch_calls = []

    async def fake_fetch(client_arg, cid):
        fetch_calls.append(cid)
        return []
    _patch_media_env(monkeypatch, client, fetch_music=fake_fetch)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="image")

    asyncio.run(run())
    assert fetch_calls == []


# ---------------------------------------------------------------------------
# J. gemini_generate_music
# ---------------------------------------------------------------------------


def test_generate_music_delegates_to_generate_media_with_music_prompt(monkeypatch):
    """gemini_generate_music 转发后 generate_content 收到 music prompt 模板。"""
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_music", prompt="jingle")

    asyncio.run(run())
    assert client.captured_generate_kwargs["prompt"] == (
        "Create music/audio using Gemini's music generation capability. Prompt: jingle"
    )


def test_generate_music_default_thinking_level_is_extended_routes_to_lyria_3_pro(monkeypatch):
    """gemini_generate_music(model='pro') 不传 thinking_level → 默认 extended → Lyria 3 Pro。

    这是与 gemini_generate_media(media_type='music', model='pro') 的关键差异：
    后者默认 standard → Lyria 3。两个工具对 '用 pro 生成音乐' 给出不同后端。
    """
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_music",
                                prompt="x", model="pro")  # 不传 thinking_level

    asyncio.run(run())
    assert client.captured_generate_kwargs["thinking_level"] == "extended"
    # 后端是 Lyria 3 Pro（默认 extended 触发 Pro 分支）


def test_generate_music_source_string_is_generate_media_music_not_generate_music(monkeypatch):
    """gemini_generate_music 转发后 source 仍为 'gemini_generate_media:music'。

    文档化当前不一致：music 工具转发给 media 工具，cleanup 归因全部落到
    media 工具名下，无法从 cleanup 日志区分是 music 工具还是 media 工具发起。
    对比 gemini_chat / gemini_upload_file / gemini_analyze_url 都有专属 source。
    """
    client = _FakeMediaClient(response_text="ok")
    schedule_calls = []
    _patch_media_env(monkeypatch, client, captured_schedule=schedule_calls)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_music", prompt="x")

    asyncio.run(run())
    assert schedule_calls[0]["source"] == "gemini_generate_media:music"
    # 关键：不是 "gemini_generate_music"
    assert schedule_calls[0]["source"] != "gemini_generate_music"


def test_generate_music_default_thinking_level_routes_to_lyria_3_pro_in_response(monkeypatch):
    """gemini_generate_music(model='pro') → 返回文本含 '后端: Lyria 3 Pro'。

    与上一个测试互补：上一个断言 thinking_level 入参，这个断言返回文本。
    """
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_music", prompt="x", model="pro")

    result = asyncio.run(run())
    assert "后端: Lyria 3 Pro" in result[0].text
