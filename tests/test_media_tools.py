"""媒体工具的集成契约测试。

覆盖请求路由、超时与清理、产物保存和结构化状态。图片按当前
Flash-Lite/Flash 路由；音乐只报告上游可验证的 Lyria 信息；视频空产物
应要求真实视频产物并提示检查状态。

测试在 client_wrapper 接缝使用替身，parse_response 走真实实现。
"""

import asyncio
import base64
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from src.adapters.mcp_sdk import MCPServer

import src.tools.media as media_tools


_SYNTHETIC_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAADElEQVR4nGP8zwACAAYIAQFazwZIAAAAAElFTkSuQmCC"
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _call_tool(mcp, name, **kwargs):
    if name in {"gemini_generate_media", "gemini_generate_music"}:
        kwargs.setdefault("background", False)
    return (await mcp.call_tool(name, kwargs)).content


class _FakeMedia:
    """模拟 GeneratedMedia，捕获 save 入参。"""

    def __init__(self, *, title="theme", mp3_url="https://x/y.mp3", url="",
                 save_return=None):
        self.title = title
        self.mp3_url = mp3_url
        self.url = url
        self._save_return = {"audio": "/tmp/fake.mp3"} if save_return is None else save_return
        self.captured_save_kwargs = None

    async def save(self, **kwargs):
        self.captured_save_kwargs = dict(kwargs)
        return self._save_return


class _FakeSavedImage:
    """Match gemini-webapi Image.save(), which returns one path string."""

    url = "https://cdn.example.test/onboarding.png"
    title = "onboarding image"

    async def save(self, **kwargs):
        destination = Path(kwargs["path"])
        destination.mkdir(parents=True, exist_ok=True)
        filename = Path(kwargs["filename"])
        if not filename.suffix:
            filename = filename.with_suffix(".png")
        path = destination / filename
        path.write_bytes(_SYNTHETIC_PNG)
        return str(path)


class _FakeSavedJpegWithPngName(_FakeSavedImage):
    async def save(self, **kwargs):
        from PIL import Image

        destination = Path(kwargs["path"])
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / kwargs["filename"]
        Image.new("RGB", (2, 1), "red").save(path, format="JPEG")
        return str(path)


class _FailingMedia(_FakeMedia):
    async def save(self, **kwargs):
        self.captured_save_kwargs = dict(kwargs)
        raise PermissionError("cannot write output")


class _FakeMediaClient:
    """模拟 Gemini 客户端，捕获 generate_content 入参并控制返回值/异常。

    与 _FakeChatClient / _FakeFileClient 不同：media 全用关键字传参，
    且需要可控的 timeout / watchdog_timeout 属性以验证 _set_client_timeouts。
    """

    def __init__(self, *, response_text="done", images=None, videos=None,
                 media=None, response_cid="c_media1", raise_exc=None,
                 timeout=100.0, watchdog_timeout=200.0,
                 response_status=None, observed_backend=None):
        self._response_text = response_text
        self._images = images or []
        self._videos = videos or []
        self._media = media or []
        self._response_cid = response_cid
        self._raise_exc = raise_exc
        self.timeout = timeout
        self.watchdog_timeout = watchdog_timeout
        self._response_status = response_status
        self._observed_backend = observed_backend
        self.captured_generate_kwargs = None
        self.captured_generate_during_timeout = None
        self.last_response = None

    def video_generation_scope(self):
        return nullcontext()

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
            status=self._response_status,
            backend=self._observed_backend,
        )
        self.last_response = response
        return response


def _patch_media_env(monkeypatch, client, *, captured_schedule=None,
                     captured_cleanup=None, probe_duration=None,
                     fetch_music=None):
    """统一 patch media 工具的外部接缝。"""
    monkeypatch.setattr(media_tools, "get_gemini_client", lambda: client)
    monkeypatch.setattr(media_tools, "probe_playable_av_tracks",
                        lambda path: frozenset({"audio" if str(path).endswith(".mp3") else "video"}))

    async def fake_init():
        return None
    monkeypatch.setattr(media_tools, "initialize_client", fake_init)

    async def fake_cleanup(client_arg):
        if captured_cleanup is not None:
            captured_cleanup.append(client_arg)
    monkeypatch.setattr(media_tools, "cleanup_due_remote_chats", fake_cleanup)

    async def fake_schedule(response, *, retain_chat, delete_after_seconds, source):
        if captured_schedule is not None:
            captured_schedule.append({
                "response": response,
                "retain_chat": retain_chat,
                "delete_after_seconds": delete_after_seconds,
                "source": source,
            })
    monkeypatch.setattr(media_tools, "finalize_remote_chat_cleanup_from_response",
                        fake_schedule)

    if probe_duration is not None:
        monkeypatch.setattr(media_tools, "_probe_duration", probe_duration)

    if fetch_music is not None:
        monkeypatch.setattr(media_tools, "_fetch_music_media_from_chat", fetch_music)


def _make_mcp():
    mcp = MCPServer("test")
    media_tools.register_media_tools(mcp)
    return mcp


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
def test_media_response_interruption_persists_cleanup(monkeypatch, failure):
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)
    scheduled = []

    async def fail_build(*_args, **_kwargs):
        raise failure()

    monkeypatch.setattr(media_tools, "_build_media_outcome", fail_build)
    monkeypatch.setattr(media_tools, "schedule_remote_chat_cleanup_from_response",
                        lambda response, **kwargs: scheduled.append((response.metadata[0], kwargs)))
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="x", media_type="image")

    if failure is asyncio.CancelledError:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        with pytest.raises(Exception):
            asyncio.run(run())
    assert scheduled == [("c_media1", {"retain_chat": False, "delete_after_seconds": 1800,
                                       "source": "gemini_generate_media:image:in_progress"}),
                         ("c_media1", {"retain_chat": False, "delete_after_seconds": None,
                                       "source": "gemini_generate_media:image:interrupted"})]


def test_queued_media_interruption_preserves_existing_grace(monkeypatch):
    client = _FakeMediaClient(response_status="processing")
    _patch_media_env(monkeypatch, client)
    scheduled = []

    async def fail_build(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(media_tools, "_build_media_outcome", fail_build)
    monkeypatch.setattr(media_tools, "schedule_remote_chat_cleanup_from_response",
                        lambda response, **kwargs: scheduled.append(kwargs))
    mcp = _make_mcp()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_call_tool(mcp, "gemini_generate_media", prompt="x", media_type="video"))
    assert scheduled == [{"retain_chat": False, "delete_after_seconds": 1800,
                          "source": "gemini_generate_media:video:in_progress"},
                         {"retain_chat": False, "delete_after_seconds": 1800,
                          "source": "gemini_generate_media:video:interrupted:deferred"}]


def test_image_media_fallback_only_without_candidate_provenance():
    legacy_item = SimpleNamespace(url="https://example.test/legacy-image")
    legacy_response = SimpleNamespace(images=[], media=[legacy_item])
    assert media_tools._response_media_items(legacy_response, "image") == [legacy_item]

    candidate_response = SimpleNamespace(
        images=[], media=[legacy_item], candidates=[SimpleNamespace(generated_images=[])], chosen=0
    )
    assert media_tools._response_media_items(candidate_response, "image") == []


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


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"prompt": "  "}, "prompt must not be blank"),
        ({"prompt": "cat", "filename": "../escape.png"}, "filename must be"),
        ({"prompt": "cat", "filename": "folder\\escape.png"}, "filename must be"),
    ],
)
def test_generate_media_rejects_invalid_request_before_client(monkeypatch, arguments, message):
    monkeypatch.setattr(
        media_tools,
        "get_gemini_client",
        lambda: (_ for _ in ()).throw(AssertionError("client must not be accessed")),
    )
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", media_type="image", **arguments)

    result = asyncio.run(run())
    assert message in result[0].text
    assert result[0].meta["domain_result"]["error"]["code"] == "INVALID_ARGUMENT"


def test_generate_media_initialization_failure_returns_typed_error(monkeypatch):
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)

    async def fail_initialization():
        raise RuntimeError("authentication expired")

    monkeypatch.setattr(media_tools, "initialize_client", fail_initialization)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="cat", media_type="image")

    result = asyncio.run(run())
    assert result[0].meta["domain_result"]["ok"] is False
    assert result[0].meta["domain_result"]["data"]["state"] == "failed"
    assert client.captured_generate_kwargs is None


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


def test_generate_media_music_flash_uses_unversioned_lyria_label(monkeypatch):
    """Music results do not claim a backend version the response did not expose."""
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music", model="flash")

    result = asyncio.run(run())
    assert "后端: Lyria" in result[0].text
    assert "Lyria 3 Pro" not in result[0].text


def test_generate_media_music_pro_standard_keeps_unversioned_label(monkeypatch):
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music", model="pro",
                                thinking_level="standard")

    result = asyncio.run(run())
    assert "后端: Lyria" in result[0].text
    assert "Lyria 3 Pro" not in result[0].text


def test_generate_media_music_pro_extended_does_not_invent_backend(monkeypatch):
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music", model="pro",
                                thinking_level="extended")

    result = asyncio.run(run())
    assert "后端: Lyria" in result[0].text
    assert "Lyria 3 Pro" not in result[0].text


def test_generate_media_video_text_only_reports_missing_video(monkeypatch):
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="video")

    result = asyncio.run(run())
    assert "后端:" in result[0].text
    assert "尚未取得可验证的视频产物" in result[0].text
    domain = result[0].meta["domain_result"]
    assert domain["error"]["code"] == "ARTIFACT_NOT_RETURNED"
    assert domain["error"]["retryable"] is False
    assert "No usable video was returned" in domain["error"]["suggested_action"]


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
    domain = result[0].meta["domain_result"]
    assert domain["error"]["code"] == "TIMED_OUT"
    assert domain["data"]["state"] == "failed"
    assert domain["data"]["request_model"] == "gemini-3-flash"
    assert domain["data"]["effective_backend"] == "Nano Banana 2"


def test_generate_media_timeout_retains_reference_image_identity(monkeypatch, tmp_path):
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"image bytes")
    client = _FakeMediaClient(raise_exc=asyncio.TimeoutError())
    _patch_media_env(monkeypatch, client)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(
            mcp,
            "gemini_generate_media",
            prompt="x",
            media_type="image",
            image_path=str(reference),
        )

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    input_artifact = domain["data"]["input_artifacts"][0]
    assert domain["error"]["code"] == "TIMED_OUT"
    assert input_artifact["id"]
    assert input_artifact["state"] == "local"
    assert input_artifact["local_path"] == str(reference.resolve())


def test_generate_media_generic_exception_returns_error_message_with_backend(monkeypatch):
    """RuntimeError → 返回含后端标签、失败和清理状态提示。"""
    client = _FakeMediaClient(raise_exc=RuntimeError("upstream aborted"))
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="video")

    result = asyncio.run(run())
    text = result[0].text
    assert "后端:" in text
    assert "❌ video 生成失败" in text and "upstream aborted" not in text
    assert "检查清理状态" in text


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
    domain = result[0].meta["domain_result"]
    assert domain["error"]["code"] == "ARTIFACT_NOT_RETURNED"
    assert domain["data"]["state"] == "empty"


def test_generate_media_queued_response_is_not_reported_as_empty(monkeypatch):
    client = _FakeMediaClient(
        response_text="accepted",
        media=[],
        images=[],
        videos=[],
        response_status="processing",
    )
    scheduled = []
    _patch_media_env(monkeypatch, client, captured_schedule=scheduled)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="x", media_type="video")

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    assert "请求已进入上游队列" in result[0].text
    assert domain["ok"] is True
    assert domain["data"]["state"] == "queued"
    assert domain["meta"]["operation_state"] == "queued"
    assert scheduled[0]["retain_chat"] is False
    assert scheduled[0]["delete_after_seconds"] == 1800
    assert scheduled[0]["source"].endswith(":deferred")


def test_empty_media_response_does_not_implicitly_retain_chat(monkeypatch):
    client = _FakeMediaClient(response_text="no media")
    scheduled = []
    _patch_media_env(monkeypatch, client, captured_schedule=scheduled)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="cat", media_type="image")

    result = asyncio.run(run())
    assert result[0].meta["domain_result"]["data"]["state"] == "empty"
    assert scheduled[0]["retain_chat"] is False
    assert scheduled[0]["delete_after_seconds"] is None


def test_queued_media_honors_explicit_cleanup_delay(monkeypatch):
    client = _FakeMediaClient(response_status="processing")
    scheduled = []
    _patch_media_env(monkeypatch, client, captured_schedule=scheduled)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="x", media_type="video", delete_after_seconds=65)

    asyncio.run(run())
    assert scheduled[0]["retain_chat"] is False
    assert scheduled[0]["delete_after_seconds"] == 65
    assert scheduled[0]["source"].endswith(":deferred")


def test_media_exposes_failed_cleanup_without_hiding_artifact_result(monkeypatch):
    from src.domain.conversations import CleanupObservation, CleanupState

    client = _FakeMediaClient(response_text="no image")
    _patch_media_env(monkeypatch, client)

    async def failed_cleanup(*_args, **_kwargs):
        return CleanupObservation(state=CleanupState.FAILED, attempts=1)

    monkeypatch.setattr(media_tools, "finalize_remote_chat_cleanup_from_response", failed_cleanup)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="image", media_type="image")

    result = asyncio.run(run())[0].meta["domain_result"]
    assert result["error"]["code"] == "ARTIFACT_NOT_RETURNED"
    assert result["meta"]["details"]["cleanup"]["state"] == "failed"


def test_generate_media_remote_uri_includes_backend_evidence(monkeypatch):
    image = SimpleNamespace(
        title="Generated",
        alt="generated image",
        url="https://cdn.example.test/out.png",
    )
    client = _FakeMediaClient(
        response_text="done",
        images=[image],
        observed_backend="observed-image-generator",
    )
    _patch_media_env(monkeypatch, client)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(
            mcp,
            "gemini_generate_media",
            prompt="x",
            media_type="image",
            model="pro",
        )

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    artifact = domain["data"]["artifacts"][0]
    assert domain["data"]["state"] == "remote"
    assert artifact["uri"] == "https://cdn.example.test/out.png"
    assert artifact["requested_backend"] == "pro"
    assert artifact["request_model"] == "gemini-3-flash"
    assert artifact["effective_backend"] == "Nano Banana 2"
    assert artifact["observed_backend"] == "observed-image-generator"
    assert artifact["verification"]["status"] == "unverified"


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
    """A media card labels its family without inventing an exact version."""
    media = _FakeMedia(title="song")
    client = _FakeMediaClient(response_text="ok", media=[media])
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media",
                                prompt="x", media_type="music", model="pro",
                                thinking_level="extended")

    result = asyncio.run(run())
    assert "🎵 音乐 1 (Lyria)" in result[0].text


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
    recovered_path = tmp_path / "recovered.mp3"
    recovered_path.write_bytes(b"\xff\xfb\x90\x64" + b"\0" * 16)
    recovered = _FakeMedia(
        title="recovered song",
        save_return={"audio": str(recovered_path)},
    )

    async def fake_fetch(client_arg, cid, *, rid, rcid):
        assert client_arg is client
        assert cid == "c_media1"  # 从 response.metadata[0] 提取
        assert rid == "r_response"
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
    assert f"audio: {recovered_path}" in text


def test_generate_media_verifies_saved_file_metadata(monkeypatch, tmp_path):
    saved_path = tmp_path / "saved.mp3"
    saved_path.write_bytes(b"\xff\xfb\x90\x64" + b"\0" * 16)
    media = _FakeMedia(
        title="saved song",
        mp3_url="https://cdn.example.test/saved.mp3",
        save_return={"audio": str(saved_path)},
    )
    client = _FakeMediaClient(response_text="done", media=[media])
    _patch_media_env(monkeypatch, client, probe_duration=lambda _path: 9.25)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="x", media_type="music")

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    artifact = domain["data"]["artifacts"][0]
    assert domain["data"]["state"] == "local"
    assert domain["meta"]["verification_status"] == "artifact_saved_and_verified"
    assert artifact["local_path"] == str(saved_path.resolve())
    assert artifact["uri"] == "https://cdn.example.test/saved.mp3"
    assert artifact["size_bytes"] == saved_path.stat().st_size
    assert artifact["mime_type"] == "audio/mpeg"
    assert artifact["duration_seconds"] == 9.25
    assert artifact["verification"]["status"] == "verified"


def test_generate_media_saves_and_verifies_image_string_result(monkeypatch, tmp_path):
    image = _FakeSavedImage()
    client = _FakeMediaClient(
        response_text="done",
        images=[image],
        observed_backend="observed-image-generator",
    )
    _patch_media_env(monkeypatch, client)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(
            mcp,
            "gemini_generate_media",
            prompt="onboarding",
            media_type="image",
            output_dir=str(tmp_path),
            filename="example",
        )

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    artifact = domain["data"]["artifacts"][0]
    assert domain["data"]["state"] == "local"
    assert domain["meta"]["verification_status"] == "artifact_saved_and_verified"
    assert artifact["kind"] == "image"
    assert artifact["local_path"] == str((tmp_path / "example.png").resolve())
    assert artifact["mime_type"] == "image/png"
    assert (artifact["width"], artifact["height"]) == (2, 1)
    assert artifact["observed_backend"] == "observed-image-generator"
    assert artifact["verification"]["status"] == "verified"


def test_generated_image_extension_matches_actual_jpeg_bytes(monkeypatch, tmp_path):
    image = _FakeSavedJpegWithPngName()
    client = _FakeMediaClient(response_text="done", images=[image])
    _patch_media_env(monkeypatch, client)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(
            mcp,
            "gemini_generate_media",
            prompt="onboarding",
            media_type="image",
            output_dir=str(tmp_path),
            filename="mismatch.png",
        )

    result = asyncio.run(run())
    artifact = result[0].meta["domain_result"]["data"]["artifacts"][0]
    assert artifact["local_path"] == str((tmp_path / "mismatch.jpg").resolve())
    assert artifact["mime_type"] == "image/jpeg"
    assert not (tmp_path / "mismatch.png").exists()


def test_generated_image_suffix_fix_does_not_replace_existing_file(tmp_path):
    existing = tmp_path / "mismatch.jpg"
    existing.write_bytes(b"keep")
    saved = tmp_path / "mismatch.png"
    saved.write_bytes(b"\xff\xd8\xff\xe0" + b"jpeg-content")

    normalized = media_tools._normalize_saved_image_extension(str(saved))

    assert normalized == str(tmp_path / "mismatch_2.jpg")
    assert existing.read_bytes() == b"keep"
    assert not saved.exists()


def test_named_image_save_does_not_replace_existing_file(monkeypatch, tmp_path):
    existing = tmp_path / "example.png"
    existing.write_bytes(b"keep")
    image = _FakeSavedImage()
    client = _FakeMediaClient(response_text="done", images=[image])
    _patch_media_env(monkeypatch, client)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(
            mcp,
            "gemini_generate_media",
            prompt="cat",
            media_type="image",
            output_dir=str(tmp_path),
            filename="example.png",
        )

    result = asyncio.run(run())
    artifact = result[0].meta["domain_result"]["data"]["artifacts"][0]
    assert existing.read_bytes() == b"keep"
    assert artifact["local_path"] == str((tmp_path / "example_2.png").resolve())


def test_multiple_images_insert_index_before_requested_extension(monkeypatch, tmp_path):
    client = _FakeMediaClient(response_text="done", images=[_FakeSavedImage(), _FakeSavedImage()])
    _patch_media_env(monkeypatch, client)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(
            mcp,
            "gemini_generate_media",
            prompt="two images",
            media_type="image",
            output_dir=str(tmp_path),
            filename="double.png",
        )

    asyncio.run(run())
    assert (tmp_path / "double_1.png").is_file()
    assert (tmp_path / "double_2.png").is_file()


def test_generate_music_keeps_audio_and_video_artifacts_distinct(monkeypatch, tmp_path):
    audio_path = tmp_path / "song.mp3"
    video_path = tmp_path / "song.mp4"
    audio_path.write_bytes(b"\xff\xfb\x90\x64" + b"\0" * 16)
    video_path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\0" * 12)
    media = _FakeMedia(
        title="song",
        mp3_url="https://cdn.example.test/song.mp3",
        url="https://cdn.example.test/song.mp4",
        save_return={"audio": str(audio_path), "video": str(video_path)},
    )
    client = _FakeMediaClient(response_text="done", media=[media])
    _patch_media_env(monkeypatch, client, probe_duration=lambda _path: 4.0)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="x", media_type="music")

    result = asyncio.run(run())
    artifacts = result[0].meta["domain_result"]["data"]["artifacts"]
    assert len(artifacts) == 2
    assert {artifact["kind"] for artifact in artifacts} == {"audio", "video"}
    assert {artifact["uri"] for artifact in artifacts} == {
        "https://cdn.example.test/song.mp3",
        "https://cdn.example.test/song.mp4",
    }
    assert len({artifact["id"] for artifact in artifacts}) == 2
    assert all(artifact["state"] == "local" for artifact in artifacts)


def test_music_downloads_streams_separately_without_overwrite_and_ignores_thumbnails(monkeypatch, tmp_path):
    (tmp_path / "instrumental.mp3").write_bytes(b"existing audio")
    (tmp_path / "instrumental.mp4").write_bytes(b"existing video")
    calls = []

    class Media:
        mp3_url = "https://example.test/audio"
        url = "https://example.test/video"
        title = "instrumental"

        async def save(self, *, path, filename, verbose, download_type):
            calls.append((filename, download_type))
            output = Path(path) / filename
            data = (b"\xff\xfb\x90\x64" + b"\0" * 16) if download_type == "audio" else (
                b"\x00\x00\x00\x18ftypisom" + b"\0" * 12
            )
            output.write_bytes(data)
            thumbnail = Path(str(output) + "_thumb")
            thumbnail.write_bytes(b"\xff\xd8\xff\xe0" + b"thumbnail")
            return {download_type: str(output), f"{download_type}_thumbnail": str(thumbnail)}

    monkeypatch.setattr(media_tools, "probe_playable_av_tracks",
                        lambda path: frozenset({"audio" if str(path).endswith(".mp3") else "video"}))
    outcome = asyncio.run(media_tools._save_generated_media(
        SimpleNamespace(), media_type="music", output_dir=str(tmp_path), filename="instrumental.mp3",
        prompt="piano", media_items=[Media()], requested_model="flash", request_model="flash",
        effective_backend="Lyria", observed_backend=None, source_chat_id=None,
    ))
    assert calls == [("instrumental_2.mp3", "audio"), ("instrumental_2.mp4", "video")]
    assert (tmp_path / "instrumental.mp3").read_bytes() == b"existing audio"
    assert (tmp_path / "instrumental.mp4").read_bytes() == b"existing video"
    assert {a.mime_type for a in outcome.artifacts} == {"audio/mpeg", "video/mp4", "image/jpeg"}
    assert len(outcome.artifacts) == 4
    assert sum(a.kind.value == "image" for a in outcome.artifacts) == 2
    assert not outcome.failures


def test_music_audio_mp4_is_saved_as_m4a_and_not_falsely_reported_as_mp3(monkeypatch, tmp_path):
    class Media:
        mp3_url = "https://example.test/audio"
        url = ""
        title = "audio"

        async def save(self, *, path, filename, verbose, download_type):
            output = Path(path) / filename
            output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\0" * 12)
            return {"audio": str(output)}

    monkeypatch.setattr(media_tools, "probe_playable_av_tracks", lambda path: frozenset({"audio"}))
    outcome = asyncio.run(media_tools._save_generated_media(
        SimpleNamespace(), media_type="music", output_dir=str(tmp_path), filename="instrumental.mp3",
        prompt="piano", media_items=[Media()], requested_model="flash", request_model="flash",
        effective_backend="Lyria", observed_backend=None, source_chat_id=None,
    ))
    assert len(outcome.artifacts) == 1
    assert outcome.artifacts[0].local_path.endswith(".m4a")
    assert outcome.artifacts[0].mime_type == "audio/mp4"
    assert not (tmp_path / "instrumental.mp3").exists()


def test_music_video_with_only_audio_track_is_not_claimed_as_video(monkeypatch, tmp_path):
    class Media:
        mp3_url = ""
        url = "https://example.test/video"
        title = "audio only"

        async def save(self, *, path, filename, verbose, download_type):
            output = Path(path) / filename
            output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\0" * 12)
            return {"video": str(output)}

    monkeypatch.setattr(media_tools, "probe_playable_av_tracks", lambda path: frozenset({"audio"}))
    outcome = asyncio.run(media_tools._save_generated_media(
        SimpleNamespace(), media_type="music", output_dir=str(tmp_path), filename="clip.mp4",
        prompt="piano", media_items=[Media()], requested_model="flash", request_model="flash",
        effective_backend="Lyria", observed_backend=None, source_chat_id=None,
    ))
    assert len(outcome.artifacts) == 1
    assert outcome.artifacts[0].kind.value == "audio"
    assert outcome.artifacts[0].local_path.endswith(".m4a")
    assert outcome.failures == ("video:track_missing",)


def test_audio_endpoint_with_av_container_is_probed_as_mp4_before_classification(monkeypatch, tmp_path):
    class Media:
        mp3_url = "https://example.test/audio"
        url = ""
        title = "audio"

        async def save(self, *, path, filename, verbose, download_type):
            output = Path(path) / filename
            output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\0" * 12)
            return {"audio": str(output)}

    def probe(path):
        assert str(path).endswith(".mp4")
        return frozenset({"audio", "video"})

    monkeypatch.setattr(media_tools, "probe_playable_av_tracks", probe)
    outcome = asyncio.run(media_tools._save_generated_media(
        SimpleNamespace(), media_type="music", output_dir=str(tmp_path), filename="instrumental.mp3",
        prompt="piano", media_items=[Media()], requested_model="flash", request_model="flash",
        effective_backend="Lyria", observed_backend=None, source_chat_id=None,
    ))
    assert len(outcome.artifacts) == 1
    assert outcome.artifacts[0].kind.value == "video"
    assert outcome.artifacts[0].local_path.endswith(".mp4")
    assert outcome.artifacts[0].mime_type == "video/mp4"
    assert outcome.artifacts[0].uri == Media.mp3_url


def test_generate_media_save_failure_returns_remote_artifact_with_partial_warning(monkeypatch):
    media = _FailingMedia(
        title="remote song",
        mp3_url="https://cdn.example.test/remote.mp3",
    )
    client = _FakeMediaClient(response_text="done", media=[media])
    _patch_media_env(monkeypatch, client)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="x", media_type="music")

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    assert domain["ok"] is True
    assert domain["meta"]["operation_state"] == "partial"
    assert domain["data"]["state"] == "remote"
    assert domain["data"]["artifacts"][0]["uri"] == "https://cdn.example.test/remote.mp3"
    assert domain["warnings"][0]["code"] == "ARTIFACT_SAVE_PARTIAL"
    assert "Local artifact save was incomplete" in result[0].text


def test_generate_media_empty_save_result_is_explicit_save_failure(monkeypatch):
    media = _FakeMedia(title="missing song", mp3_url="", url="", save_return={})
    client = _FakeMediaClient(response_text="done", media=[media])
    _patch_media_env(monkeypatch, client)
    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_media", prompt="x", media_type="music")

    result = asyncio.run(run())
    domain = result[0].meta["domain_result"]
    assert "产物未能保存或通过本地验证" in result[0].text
    assert domain["ok"] is False
    assert domain["error"]["code"] == "ARTIFACT_SAVE_FAILED"
    assert domain["data"]["state"] == "failed"
    assert domain["meta"]["details"]["save_failure_count"] == 1


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
    assert "后端: Lyria" in result[0].text
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


def test_generate_music_default_thinking_level_is_standard(monkeypatch):
    """The music facade uses standard thinking without a backend version claim."""
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_music",
                                prompt="x", model="pro")  # 不传 thinking_level

    asyncio.run(run())
    assert client.captured_generate_kwargs["thinking_level"] == "standard"


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


def test_generate_music_default_thinking_level_does_not_invent_backend(monkeypatch):
    """A Pro music request is still labeled Lyria until backend evidence exists."""
    client = _FakeMediaClient(response_text="ok")
    _patch_media_env(monkeypatch, client)

    mcp = _make_mcp()

    async def run():
        return await _call_tool(mcp, "gemini_generate_music", prompt="x", model="pro")

    result = asyncio.run(run())
    assert "后端: Lyria" in result[0].text
    assert "Lyria 3 Pro" not in result[0].text


def test_concurrent_image_saves_reserve_distinct_names(tmp_path):
    from src.services.media_storage import _save_generated_media
    async def run():
        entered = 0
        ready = asyncio.Event()
        class Image:
            url = 'https://example.test/image'
            title = 'image'
            async def save(self, *, path, filename, verbose):
                nonlocal entered
                entered += 1
                if entered == 2:
                    ready.set()
                await ready.wait()
                output = Path(path) / filename
                output.write_bytes(_SYNTHETIC_PNG)
                return str(output)
        async def save_one():
            return await _save_generated_media(SimpleNamespace(), media_type='image', output_dir=str(tmp_path),
                filename='same.png', prompt='', media_items=[Image()], requested_model='flash',
                request_model='flash', effective_backend='flash', observed_backend=None, source_chat_id=None)
        return await asyncio.gather(save_one(), save_one())
    results = asyncio.run(run())
    paths = [Path(result.artifacts[0].local_path) for result in results]
    assert len(set(paths)) == 2 and all(path.read_bytes() == _SYNTHETIC_PNG for path in paths)


def test_media_save_failure_does_not_log_signed_url(tmp_path, caplog):
    from src.services.media_storage import _save_generated_media
    class Media:
        async def save(self, **_kwargs):
            raise RuntimeError('https://example.test/download?secret=synthetic-signature')
    result = asyncio.run(_save_generated_media(SimpleNamespace(), media_type='image', output_dir=str(tmp_path),
        filename='image.png', prompt='', media_items=[Media()], requested_model='flash', request_model='flash',
        effective_backend='flash', observed_backend=None, source_chat_id=None))
    assert result.failures == ('RuntimeError:save',)
    assert 'synthetic-signature' not in caplog.text and 'RuntimeError' in caplog.text
    assert not list(tmp_path.iterdir())


def test_failed_media_save_removes_partial_reserved_file(tmp_path):
    from src.services.media_storage import _save_generated_media
    class Media:
        async def save(self, *, path, filename, verbose):
            (Path(path) / filename).write_bytes(b'partial data')
            raise ConnectionError('interrupted')
    result = asyncio.run(_save_generated_media(SimpleNamespace(), media_type='video', output_dir=str(tmp_path),
        filename='partial.mp4', prompt='', media_items=[Media()], requested_model='flash', request_model='flash',
        effective_backend='flash', observed_backend=None, source_chat_id=None))
    assert result.failures == ('ConnectionError:save',) and not list(tmp_path.iterdir())


def test_image_extension_correction_preserves_existing_file(tmp_path):
    from src.services.media_storage import _normalize_saved_image_extension
    source = tmp_path / 'photo.png'
    source.write_bytes(b'\xff\xd8\xff\xe0' + b'fake-jpeg')
    existing = tmp_path / 'photo.jpg'
    existing.write_bytes(b'previous')
    corrected = Path(_normalize_saved_image_extension(str(source)))
    assert corrected != existing and corrected.suffix == '.jpg'
    assert corrected.read_bytes() == b'\xff\xd8\xff\xe0' + b'fake-jpeg'
    assert existing.read_bytes() == b'previous'


def test_concurrent_video_saves_reserve_distinct_names(tmp_path):
    from src.services.media_storage import _save_generated_media
    mp4 = b'\x00\x00\x00\x18ftypisom' + b'\0' * 12
    async def run():
        entered = 0
        ready = asyncio.Event()
        class Video:
            url = 'https://example.test/video'
            title = 'video'
            async def save(self, *, path, filename, verbose):
                nonlocal entered
                entered += 1
                if entered == 2:
                    ready.set()
                await ready.wait()
                output = Path(path) / filename
                output.write_bytes(mp4)
                return {'video': str(output)}
        async def save_one():
            return await _save_generated_media(SimpleNamespace(), media_type='video', output_dir=str(tmp_path),
                filename='same.mp4', prompt='', media_items=[Video()], requested_model='flash',
                request_model='flash', effective_backend='flash', observed_backend=None, source_chat_id=None,
                track_probe=lambda _path: frozenset({'video'}))
        return await asyncio.gather(save_one(), save_one())
    results = asyncio.run(run())
    paths = [Path(result.artifacts[0].local_path) for result in results]
    assert len(set(paths)) == 2 and all(path.read_bytes() == mp4 for path in paths)


@pytest.mark.parametrize('shape', ['invalid_video', 'unsupported_result'])
def test_unusable_media_save_removes_only_own_reserved_file(tmp_path, shape):
    from src.services.media_storage import _save_generated_media
    existing = tmp_path / 'same.mp4'
    existing.write_bytes(b'preexisting')
    class Video:
        async def save(self, *, path, filename, verbose):
            output = Path(path) / filename
            output.write_bytes(b'invalid media')
            return {'video': str(output)} if shape == 'invalid_video' else object()
    result = asyncio.run(_save_generated_media(SimpleNamespace(), media_type='video', output_dir=str(tmp_path),
        filename='same.mp4', prompt='', media_items=[Video()], requested_model='flash', request_model='flash',
        effective_backend='flash', observed_backend=None, source_chat_id=None))
    assert result.failures and not result.artifacts
    assert existing.read_bytes() == b'preexisting' and sorted(p.name for p in tmp_path.iterdir()) == ['same.mp4']


def test_suffix_rename_rolls_back_link_if_original_cannot_be_removed(tmp_path, monkeypatch):
    from src.services.media_storage import _rename_music_suffix
    source = tmp_path / 'song.mp3'
    source.write_bytes(b'original')
    original_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == source:
            raise PermissionError('synthetic')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    with pytest.raises(PermissionError):
        _rename_music_suffix(str(source), '.mp4')
    assert source.read_bytes() == b'original' and sorted(p.name for p in tmp_path.iterdir()) == ['song.mp3']


def test_invalid_video_removes_main_but_preserves_verified_preview(tmp_path):
    from src.services.media_storage import _save_generated_media
    preview = b'\xff\xd8\xff\xe0' + b'preview'
    class Video:
        thumbnail = 'https://example.test/thumb'
        async def save(self, *, path, filename, verbose):
            main = Path(path) / filename
            main.write_bytes(b'invalid video')
            thumb = Path(path) / 'preview.jpg'
            thumb.write_bytes(preview)
            return {'video': str(main), 'video_thumbnail': str(thumb)}
    result = asyncio.run(_save_generated_media(SimpleNamespace(), media_type='video', output_dir=str(tmp_path),
        filename='video.mp4', prompt='', media_items=[Video()], requested_model='flash', request_model='flash',
        effective_backend='flash', observed_backend=None, source_chat_id=None))
    assert result.failures and len(result.artifacts) == 1
    assert result.artifacts[0].kind.value == 'image' and result.artifacts[0].state.value == 'local'
    assert (tmp_path / 'preview.jpg').read_bytes() == preview and not (tmp_path / 'video.mp4').exists()


def test_invalid_music_after_extension_change_removes_reserved_output(tmp_path):
    from src.services.media_storage import _save_generated_media
    class Media:
        mp3_url = 'https://example.test/audio'
        url = ''
        async def save(self, *, path, filename, verbose, download_type):
            output = Path(path) / filename
            output.write_bytes(b'\x00\x00\x00\x18ftypisom' + b'\0' * 12)
            return {'audio': str(output)}
    result = asyncio.run(_save_generated_media(SimpleNamespace(), media_type='music', output_dir=str(tmp_path),
        filename='song.mp3', prompt='', media_items=[Media()], requested_model='flash', request_model='flash',
        effective_backend='flash', observed_backend=None, source_chat_id=None, track_probe=lambda _path: None))
    assert result.failures and not result.artifacts and not list(tmp_path.iterdir())
