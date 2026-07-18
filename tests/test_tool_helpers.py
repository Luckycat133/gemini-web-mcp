"""工具模块纯 helper 的行为测试。

调研发现以下 helper 此前零直接覆盖或仅 1 个间接用例：

- `utils.validate_local_file_path`：路径安全闸门（5 类分支），仅 1 间接用例
- `utils.validate_image_paths` / `validate_optional_image_path` / `extract_remote_chat_id`
  / `parse_response` / `get_stream_text_piece`：零直接覆盖
- `constants.resolve_model_name` / `normalize_model_alias` / `resolve_media_request`
  / `describe_model_name` / `supported_learning_modes`：纯查表函数零覆盖
- `media._safe_media_filename` / `_media_timeout` / `_set_client_timeouts`
  / `_restore_client_timeouts` / `_prepend_backend_note` / `_media_from_music_card`：零覆盖
- `file._validate_url`：4 分支纯函数零覆盖；`_validate_file_path`：纯转发壳

本文件以纯输入输出断言为主，仅在 `_media_from_music_card` 用 SimpleNamespace fake client。
"""

from types import SimpleNamespace

from mcp.types import TextContent

from src.constants import (
    describe_model_name,
    normalize_model_alias,
    resolve_media_request,
    resolve_model_name,
    supported_learning_modes,
)
from src.tools.file import _validate_file_path, _validate_url
from src.tools.media import (
    _media_from_music_card,
    _media_timeout,
    _prepend_backend_note,
    _restore_client_timeouts,
    _safe_media_filename,
    _set_client_timeouts,
)
from src.tools.utils import (
    extract_remote_chat_id,
    get_stream_text_piece,
    parse_response,
    validate_image_paths,
    validate_local_file_path,
    validate_optional_image_path,
)


# ---------------------------------------------------------------------------
# utils.validate_local_file_path
# ---------------------------------------------------------------------------


def test_validate_local_file_path_empty_returns_error():
    """空字符串 / 纯空白 → 失败。"""
    assert validate_local_file_path("")[0] is False
    assert validate_local_file_path("   ")[0] is False


def test_validate_local_file_path_rejects_path_traversal(tmp_path):
    """路径含 '..' 段 → 拒绝。"""
    ok, msg = validate_local_file_path(str(tmp_path / ".." / "secret.txt"))
    assert ok is False
    assert "路径遍历" in msg


def test_validate_local_file_path_missing_file(tmp_path):
    """文件不存在 → 失败。"""
    missing = tmp_path / "nope.png"
    ok, msg = validate_local_file_path(str(missing))
    assert ok is False
    assert "未找到" in msg


def test_validate_local_file_path_directory_not_file(tmp_path):
    """路径指向目录 → 失败。"""
    ok, msg = validate_local_file_path(str(tmp_path))
    assert ok is False
    assert "不是文件" in msg


def test_validate_local_file_path_extension_mismatch(tmp_path):
    """扩展名不在白名单 → 失败。"""
    target = tmp_path / "asset.exe"
    target.write_bytes(b"x")
    ok, msg = validate_local_file_path(
        str(target), allowed_extensions={".png", ".jpg"}
    )
    assert ok is False
    assert "不支持的附件类型" in msg
    assert ".png" in msg and ".jpg" in msg


def test_validate_local_file_path_size_limit(tmp_path):
    """超过 max_bytes → 失败。"""
    target = tmp_path / "big.png"
    target.write_bytes(b"x" * 100)
    ok, msg = validate_local_file_path(str(target), max_bytes=10)
    assert ok is False
    assert "附件过大" in msg
    assert "100" in msg and "10" in msg


def test_validate_local_file_path_happy_path_returns_absolute(tmp_path):
    """合法文件 → 成功并返回绝对路径。"""
    target = tmp_path / "ok.png"
    target.write_bytes(b"ok")
    ok, value = validate_local_file_path(str(target))
    assert ok is True
    assert value.endswith("ok.png")
    assert "/" in value  # 绝对路径


def test_validate_local_file_path_no_extensions_allows_any_suffix(tmp_path):
    """allowed_extensions=None → 不校验扩展名。"""
    target = tmp_path / "data.dat"
    target.write_bytes(b"x")
    ok, _ = validate_local_file_path(str(target))
    assert ok is True


# ---------------------------------------------------------------------------
# utils.validate_image_paths / validate_optional_image_path
# ---------------------------------------------------------------------------


def test_validate_image_paths_empty_list_returns_empty_ok():
    """空列表 / None → 成功且空 paths。"""
    assert validate_image_paths(None) == (True, [], "")
    assert validate_image_paths([]) == (True, [], "")


def test_validate_image_paths_validates_each_and_returns_normalized(tmp_path):
    """多张图片 → 逐个校验，返回归一化绝对路径列表。"""
    a = tmp_path / "a.png"
    a.write_bytes(b"a")
    b = tmp_path / "b.jpg"
    b.write_bytes(b"b")
    ok, paths, msg = validate_image_paths([str(a), str(b)])
    assert ok is True
    assert msg == ""
    assert len(paths) == 2
    assert paths[0].endswith("a.png")
    assert paths[1].endswith("b.jpg")


def test_validate_image_paths_fail_fast_on_first_invalid(tmp_path):
    """第一个无效 → 立即返回，不继续校验后续。"""
    a = tmp_path / "missing.png"
    b = tmp_path / "b.jpg"
    b.write_bytes(b"b")
    ok, paths, msg = validate_image_paths([str(a), str(b)])
    assert ok is False
    assert paths == []
    assert "未找到" in msg


def test_validate_image_paths_rejects_non_image_extension(tmp_path):
    """非图片扩展名 → 失败。"""
    target = tmp_path / "doc.txt"
    target.write_bytes(b"x")
    ok, _, msg = validate_image_paths([str(target)])
    assert ok is False
    assert "不支持的附件类型" in msg


def test_validate_optional_image_path_none_returns_none_ok():
    """None / 空串 → 成功且 path=None。"""
    assert validate_optional_image_path(None) == (True, None, "")
    assert validate_optional_image_path("") == (True, None, "")


def test_validate_optional_image_path_single(tmp_path):
    """单张图片 → 成功且返回绝对路径。"""
    target = tmp_path / "x.png"
    target.write_bytes(b"x")
    ok, value, msg = validate_optional_image_path(str(target))
    assert ok is True
    assert msg == ""
    assert value is not None and value.endswith("x.png")


def test_validate_optional_image_path_invalid_returns_none(tmp_path):
    """无效 → ok=False 且 path=None。"""
    ok, value, msg = validate_optional_image_path(str(tmp_path / "missing.png"))
    assert ok is False
    assert value is None
    assert "未找到" in msg


# ---------------------------------------------------------------------------
# utils.extract_remote_chat_id
# ---------------------------------------------------------------------------


def test_extract_remote_chat_id_from_cid_attribute():
    """response.cid 是 c_ 前缀字符串 → 直接返回。"""
    resp = SimpleNamespace(cid="c_abc", metadata=None)
    assert extract_remote_chat_id(resp) == "c_abc"


def test_extract_remote_chat_id_from_metadata_first_element():
    """无 cid 但 metadata[0] 是 c_ 前缀 → 返回 metadata[0]。"""
    resp = SimpleNamespace(cid=None, metadata=["c_meta1", "r_meta2"])
    assert extract_remote_chat_id(resp) == "c_meta1"


def test_extract_remote_chat_id_returns_none_when_no_match():
    """既无 cid 也无有效 metadata → None。"""
    assert extract_remote_chat_id(SimpleNamespace(cid=None, metadata=None)) is None
    assert extract_remote_chat_id(SimpleNamespace(cid="x", metadata=[])) is None
    assert extract_remote_chat_id(SimpleNamespace(cid=None, metadata=["r_only"])) is None


# ---------------------------------------------------------------------------
# utils.parse_response
# ---------------------------------------------------------------------------


def _img(title="", url="", alt=""):
    return SimpleNamespace(title=title, url=url, alt=alt)


def _vid(title="", url=""):
    return SimpleNamespace(title=title, url=url)


def _media(title="", mp3_url="", url=""):
    return SimpleNamespace(title=title, mp3_url=mp3_url, url=url)


def test_parse_response_text_only():
    """只有 text → 单 TextContent。"""
    resp = SimpleNamespace(text="hello", images=[], videos=[], media=[], metadata=None)
    out = parse_response(resp)
    assert len(out) == 1
    assert out[0].text == "hello"


def test_parse_response_text_override_replaces_text():
    """text_override 非 None 时覆盖 response.text。"""
    resp = SimpleNamespace(text="orig", images=[], videos=[], media=[], metadata=None)
    out = parse_response(resp, text_override="override")
    assert out[0].text == "override"


def test_parse_response_includes_image_block():
    """images 非空 → 输出含 '🖼️ 图片 1' 与 URL/描述。"""
    resp = SimpleNamespace(
        text="t",
        images=[_img(title="Cat", url="https://x/cat.png", alt="a cat")],
        videos=[],
        media=[],
        metadata=None,
    )
    text = parse_response(resp)[0].text
    assert "🖼️ 图片 1" in text
    assert "Cat" in text
    assert "https://x/cat.png" in text
    assert "a cat" in text


def test_parse_response_includes_video_block():
    resp = SimpleNamespace(
        text="t",
        images=[],
        videos=[_vid(title="Clip", url="https://x/clip.mp4")],
        media=[],
        metadata=None,
    )
    text = parse_response(resp)[0].text
    assert "🎬 视频 1" in text
    assert "Clip" in text
    assert "https://x/clip.mp4" in text


def test_parse_response_music_label_depends_on_model():
    """media 块的音乐后端标签随 model 变化（pro → Lyria 3 Pro，flash → Lyria 3）。"""
    media = [_media(title="Song", mp3_url="https://x/song.mp3")]
    pro_text = parse_response(
        SimpleNamespace(text="t", images=[], videos=[], media=media, metadata=None),
        model="pro",
    )[0].text
    flash_text = parse_response(
        SimpleNamespace(text="t", images=[], videos=[], media=media, metadata=None),
        model="flash",
    )[0].text
    assert "Lyria 3 Pro" in pro_text
    assert "Lyria 3 Pro" not in flash_text
    assert "Lyria 3" in flash_text


def test_parse_response_appends_remote_chat_id():
    """有 remote_chat_id → 末尾追加 'Remote chat ID: ...'。"""
    resp = SimpleNamespace(text="t", images=[], videos=[], media=[],
                           metadata=["c_xyz", "r_abc"])
    text = parse_response(resp)[0].text
    assert "Remote chat ID: c_xyz" in text


# ---------------------------------------------------------------------------
# utils.get_stream_text_piece
# ---------------------------------------------------------------------------


def test_get_stream_text_piece_prefers_text_delta():
    """有 text_delta → 优先返回 text_delta。"""
    resp = SimpleNamespace(text_delta="delta", text="full")
    assert get_stream_text_piece(resp) == "delta"


def test_get_stream_text_piece_falls_back_to_text():
    """无 text_delta → 回退到 text。"""
    resp = SimpleNamespace(text="full")
    assert get_stream_text_piece(resp) == "full"


def test_get_stream_text_piece_handles_missing_attributes():
    """两个属性都缺失 → 空串。"""
    assert get_stream_text_piece(SimpleNamespace()) == ""


def test_get_stream_text_piece_returns_empty_when_text_delta_is_falsy():
    """text_delta=None → 返回空串（不回退到 text，因 hasattr 已 True）。

    实现细节：函数用 hasattr 而非 truthiness 判断，故 falsy text_delta 不会触发回退。
    """
    resp = SimpleNamespace(text_delta=None, text="full")
    assert get_stream_text_piece(resp) == ""


# ---------------------------------------------------------------------------
# constants.resolve_model_name / normalize_model_alias / describe_model_name
# ---------------------------------------------------------------------------


def test_resolve_model_name_known_alias_returns_configured_name():
    assert resolve_model_name("flash") == "gemini-3-flash"
    assert resolve_model_name("pro") == "gemini-3-pro"
    assert resolve_model_name("flash-lite") == "3.1 Flash-Lite"


def test_resolve_model_name_unknown_passthrough():
    """未知 key → 原样返回。"""
    assert resolve_model_name("custom-model") == "custom-model"


def test_normalize_model_alias_none_empty_returns_flash():
    assert normalize_model_alias(None) == "flash"
    assert normalize_model_alias("") == "flash"


def test_normalize_model_alias_known_aliases():
    """各种大小写/别名 → 归一化到稳定 key。"""
    assert normalize_model_alias("Flash") == "flash"
    assert normalize_model_alias("3.5 flash") == "flash"
    assert normalize_model_alias("fast") == "flash"
    assert normalize_model_alias("lite") == "flash-lite"
    assert normalize_model_alias("3.1 pro") == "pro"
    assert normalize_model_alias("thinking") == "thinking"


def test_normalize_model_alias_unknown_passthrough_lowered():
    """未知别名 → 原样小写返回。"""
    assert normalize_model_alias("Custom") == "custom"


def test_describe_model_name_known_returns_resolved():
    assert describe_model_name("flash") == "gemini-3-flash"


def test_describe_model_name_unknown_returns_unspecified():
    """resolve_model_name 返回空时回退 'unspecified'。"""
    # MODEL_CONFIG 不含空字符串 key，resolve_model_name("") 返回 ""
    assert describe_model_name("") == "unspecified"


def test_supported_learning_modes_returns_expected_string():
    """返回稳定的 user-facing 模式串。"""
    expected = "interactive_quiz/quiz, flashcards, practice_test, study_guide/exam_prep"
    assert supported_learning_modes() == expected


# ---------------------------------------------------------------------------
# constants.resolve_media_request
# ---------------------------------------------------------------------------


def test_resolve_media_request_image_always_nano_banana_2():
    """image 类型 → 后端固定 Nano Banana 2，effective_alias=flash。"""
    for alias in ("flash-lite", "flash", "pro"):
        out = resolve_media_request(alias, "image")
        assert out["backend_label"] == "Nano Banana 2"
        assert out["effective_alias"] == "flash"
        assert out["request_model"] == resolve_model_name("flash")
        assert out["requested_alias"] == alias


def test_resolve_media_request_music_non_pro_returns_lyria_3():
    """非 pro 模型 → Lyria 3。"""
    out = resolve_media_request("flash", "music")
    assert out["backend_label"] == "Lyria 3"
    assert out["effective_alias"] == "flash"
    assert out["requested_alias"] == "flash"


def test_resolve_media_request_music_pro_standard_returns_lyria_3():
    """pro + standard → Lyria 3（非 Pro）。"""
    out = resolve_media_request("pro", "music", thinking_level="standard")
    assert out["backend_label"] == "Lyria 3"
    assert out["effective_alias"] == "flash"


def test_resolve_media_request_music_pro_extended_returns_lyria_3_pro():
    """pro + extended → Lyria 3 Pro。"""
    out = resolve_media_request("pro", "music", thinking_level="extended")
    assert out["backend_label"] == "Lyria 3 Pro"
    assert out["effective_alias"] == "pro"


def test_resolve_media_request_unknown_type_passthrough():
    """未知 media_type → 默认后端 + 空 note。"""
    out = resolve_media_request("flash", "video")
    assert out["backend_label"] == "Gemini Web default"
    assert out["effective_alias"] == "flash"
    assert out["note"] == ""


# ---------------------------------------------------------------------------
# media._safe_media_filename
# ---------------------------------------------------------------------------


def test_safe_media_filename_normal_prompt():
    """普通 prompt → 原样保留（空格转 _）。"""
    assert _safe_media_filename("sunset over ocean", "image") == "sunset_over_ocean"


def test_safe_media_filename_strips_special_chars():
    """特殊字符 → 下划线。"""
    assert _safe_media_filename("hello! @world #1", "image") == "hello_world_1"


def test_safe_media_filename_truncates_to_48_chars():
    """超长 prompt → 截断到 48 字符。"""
    long = "a" * 100
    out = _safe_media_filename(long, "image")
    assert len(out) == 48
    assert out == "a" * 48


def test_safe_media_filename_strips_trailing_dots_and_underscores():
    """末尾的 . _ - 被剥离。"""
    assert _safe_media_filename("hello...", "image") == "hello"
    assert _safe_media_filename("hello___", "image") == "hello"


def test_safe_media_filename_empty_prompt_falls_back_to_media_type():
    """空 prompt → 回退到 media_type。"""
    assert _safe_media_filename("", "music") == "music"
    assert _safe_media_filename("   ", "music") == "music"
    assert _safe_media_filename("...___", "image") == "image"


# ---------------------------------------------------------------------------
# media._media_timeout
# ---------------------------------------------------------------------------


def test_media_timeout_explicit_positive_overrides_default():
    """显式正值 → 直接返回。"""
    assert _media_timeout("image", 42) == 42
    assert _media_timeout("music", 999) == 999


def test_media_timeout_image_default_180():
    """image + 无显式值 → 180。"""
    assert _media_timeout("image", None) == 180
    assert _media_timeout("image", 0) == 180
    assert _media_timeout("image", -5) == 180


def test_media_timeout_non_image_default_600():
    """非 image + 无显式值 → 600。"""
    assert _media_timeout("music", None) == 600
    assert _media_timeout("video", None) == 600


# ---------------------------------------------------------------------------
# media._set_client_timeouts / _restore_client_timeouts
# ---------------------------------------------------------------------------


def test_set_client_timeouts_no_previous_attributes_returns_none_pair():
    """client 无 timeout/watchdog_timeout 属性 → 返回 (None, None)，不设置。"""
    client = SimpleNamespace()
    prev = _set_client_timeouts(client, 300)
    assert prev == (None, None)
    assert not hasattr(client, "timeout")
    assert not hasattr(client, "watchdog_timeout")


def test_set_client_timeouts_takes_max_of_previous_and_requested():
    """previous_timeout=100, requested=300 → 设为 300（取 max）。"""
    client = SimpleNamespace(timeout=100.0, watchdog_timeout=200.0)
    prev = _set_client_timeouts(client, 300)
    assert prev == (100.0, 200.0)
    assert client.timeout == 300.0
    # watchdog = min(max(200, 120), max(300, 120)) = min(200, 300) = 200
    assert client.watchdog_timeout == 200.0


def test_set_client_timeouts_keeps_smaller_previous_when_requested_smaller():
    """previous=400, requested=200 → 设为 400（取 max，不降低）。"""
    client = SimpleNamespace(timeout=400.0, watchdog_timeout=400.0)
    _set_client_timeouts(client, 200)
    assert client.timeout == 400.0
    # watchdog = min(max(400, 120), max(200, 120)) = min(400, 200) = 200
    assert client.watchdog_timeout == 200.0


def test_set_client_timeouts_watchdog_floored_at_120():
    """watchdog 计算下限 120（即使 requested < 120）。"""
    client = SimpleNamespace(timeout=50.0, watchdog_timeout=50.0)
    _set_client_timeouts(client, 30)
    # watchdog = min(max(50, 120), max(30, 120)) = min(120, 120) = 120
    assert client.watchdog_timeout == 120.0


def test_restore_client_timeouts_writes_back_previous_values():
    """restore 把 prev 值写回 client 属性。"""
    client = SimpleNamespace(timeout=999.0, watchdog_timeout=999.0)
    _restore_client_timeouts(client, 100.0, 200.0)
    assert client.timeout == 100.0
    assert client.watchdog_timeout == 200.0


def test_restore_client_timeouts_skips_none_prev():
    """prev 为 None → 不写回（保持 client 当前值）。"""
    client = SimpleNamespace(timeout=999.0, watchdog_timeout=999.0)
    _restore_client_timeouts(client, None, None)
    assert client.timeout == 999.0
    assert client.watchdog_timeout == 999.0


def test_set_then_restore_roundtrip_restores_original():
    """set → restore 完整往返恢复原值。"""
    client = SimpleNamespace(timeout=100.0, watchdog_timeout=200.0)
    prev = _set_client_timeouts(client, 500)
    _restore_client_timeouts(client, *prev)
    assert client.timeout == 100.0
    assert client.watchdog_timeout == 200.0


# ---------------------------------------------------------------------------
# media._prepend_backend_note
# ---------------------------------------------------------------------------


def test_prepend_backend_note_empty_note_returns_parsed_unchanged():
    """空 note_lines → 原样返回。"""
    parsed = [TextContent(type="text", text="body")]
    assert _prepend_backend_note(parsed, []) is parsed
    assert _prepend_backend_note(parsed, [""]) is parsed
    assert _prepend_backend_note(parsed, ["   "]) is parsed


def test_prepend_backend_note_empty_parsed_returns_empty():
    """空 parsed → 原样返回。"""
    assert _prepend_backend_note([], ["note"]) == []


def test_prepend_backend_note_prepends_to_first_text():
    """正常 → note 拼到第一个 TextContent 前，后续不变。"""
    parsed = [
        TextContent(type="text", text="body1"),
        TextContent(type="text", text="body2"),
    ]
    out = _prepend_backend_note(parsed, ["Backend: Lyria 3", "Note: live"])
    assert len(out) == 2
    assert out[0].text == "Backend: Lyria 3\nNote: live\n\nbody1"
    assert out[1].text == "body2"


# ---------------------------------------------------------------------------
# media._media_from_music_card
# ---------------------------------------------------------------------------


def _music_card(title, url):
    """构造 _media_from_music_card 期望的嵌套 list 结构。"""
    # title 在 [1, 2]，url 在 [1, 7, 1]
    return [None, [None, None, title, None, None, None, None, [None, url]]]


def test_media_from_music_card_mp3_branch():
    """title 不以 .mp4 结尾 → mp3_url=media_url, url(mp4)=''。"""
    card = _music_card("My Song", "https://x/song.mp3")
    media = _media_from_music_card(
        card, client=SimpleNamespace(proxy="http://proxy"), cid="c_1", rid="r_1", rcid="rc_1"
    )
    assert media is not None
    assert media.title == "My Song"
    assert media.mp3_url == "https://x/song.mp3"
    assert media.url == ""  # mp4 url
    assert media.mp4_url == ""  # via attribute access if present, else empty
    assert media.cid == "c_1"
    assert media.rid == "r_1"
    assert media.rcid == "rc_1"
    assert media.proxy == "http://proxy"


def test_media_from_music_card_mp4_branch():
    """title 以 .mp4 结尾 → url(mp4)=media_url, mp3_url=''。"""
    card = _music_card("clip.mp4", "https://x/clip.mp4")
    media = _media_from_music_card(
        card, client=SimpleNamespace(proxy=None), cid="c_1", rid="r_1", rcid="rc_1"
    )
    assert media is not None
    assert media.mp3_url == ""
    assert media.url == "https://x/clip.mp4"
    assert media.proxy is None


def test_media_from_music_card_no_url_returns_none():
    """media_url 为空 → 返回 None。"""
    card = _music_card("Empty", "")
    media = _media_from_music_card(
        card, client=SimpleNamespace(), cid="c_1", rid="r_1", rcid="rc_1"
    )
    assert media is None


def test_media_from_music_card_empty_title_uses_media_placeholder():
    """title 为空 → 回退到 '[Media]'。"""
    card = _music_card("", "https://x/song.mp3")
    media = _media_from_music_card(
        card, client=SimpleNamespace(), cid="c_1", rid="r_1", rcid="rc_1"
    )
    assert media is not None
    assert media.title == "[Media]"


# ---------------------------------------------------------------------------
# file._validate_url
# ---------------------------------------------------------------------------


def test_validate_url_empty_returns_error():
    """空 URL → 失败。"""
    ok, msg = _validate_url("")
    assert ok is False
    assert "不能为空" in msg


def test_validate_url_missing_scheme_returns_error():
    """无 scheme → 失败。"""
    ok, msg = _validate_url("example.com/path")
    assert ok is False
    assert "格式无效" in msg


def test_validate_url_missing_netloc_returns_error():
    """有 scheme 但无 netloc → 失败。"""
    ok, msg = _validate_url("file:///local/path")
    assert ok is False
    assert "格式无效" in msg


def test_validate_url_valid_returns_url_unchanged():
    """合法 URL → 成功且原样返回。"""
    url = "https://example.com/article?q=1"
    ok, value = _validate_url(url)
    assert ok is True
    assert value == url


def test_validate_url_handles_urlparse_exception():
    """urlparse 抛异常 → 返回失败 + 异常信息。

    用 monkeypatch 替换 urlparse 触发异常路径。
    """
    import src.tools.file as file_module

    original = file_module.urlparse

    def boom(url):
        raise ValueError("boom")

    file_module.urlparse = boom  # type: ignore[assignment]
    try:
        ok, msg = _validate_url("https://x")
        assert ok is False
        assert "URL 验证失败" in msg
        assert "boom" in msg
    finally:
        file_module.urlparse = original  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# file._validate_file_path（纯转发壳）
# ---------------------------------------------------------------------------


def test_validate_file_path_delegates_to_utils_validate_local_file_path():
    """_validate_file_path 是 validate_local_file_path 的纯转发壳。"""
    # 空 → 失败
    assert _validate_file_path("") == (False, "文件路径不能为空")
    # 不存在 → 失败
    ok, msg = _validate_file_path("/nonexistent/path/to/file.png")
    assert ok is False
    assert "未找到" in msg
