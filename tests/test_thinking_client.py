"""thinking_client 模块的行为测试。

调研发现该模块覆盖率仅 53%（139 stmts, 66 miss），此前在 test_tool_workflows.py
仅有 3 个间接用例（inject_thinking_level happy / inject_web_request_options
happy with h5d / _with_learning_prompt kwargs 分支），关键分支零断言：

- _encode_learning_x9b：4 个字段名（zUa/QLd/LYd/h5d）+ 不支持字段 ValueError
- _encode_learning_goa：mode_id 编码
- inject_web_request_options：5 个早退守卫（f.req 非 str / outer 非 list / outer
  长度 <2 / inner_payload 非 str / inner_request 非 list）+ learning 元数据不完整
  ValueError + learning-only / thinking-only / 已长度 81 不扩展
- _set_web_request：全 None 无现存 → None token / 全 None 有现存 → 复用 /
  无效 thinking_level → ValueError / 无效 learning_mode → ValueError /
  有效 thinking+model / 有效 learning_mode
- _with_learning_prompt：learning_mode=None 不变 / args[0] 前缀 / 空参数不变
- _prefix_learning_prompt：非 str 不变 / 已前缀不变
- thinking_scope：enter/yield/exit 正确 reset token
- generate_content：model=None / model 传入 / 异常仍 reset token
- generate_content_stream：model=None / model 传入
- _install_thinking_transport：session 为 None / 已安装 / 正常 patch /
  stream_with_thinking 注入分支（request 为 None / url 非 GENERATE / data 非 dict
  / 命中注入）

实例构造：用 object.__new__(ThinkingLevelGeminiClient) 跳过 GeminiClient.__init__
的真实网络依赖。异步方法通过 monkeypatch GeminiClient.generate_content /
generate_content_stream 在类层面打桩。ContextVar _web_request 在每个测试前后
显式 reset 防止跨测试泄漏。
"""

import asyncio
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest
from gemini_webapi import GeminiClient
from gemini_webapi.constants import Endpoint
from gemini_webapi.types import AvailableModel

from src.infrastructure.rpc_contracts import VIDEO_GENERATION_TRANSPORT, VideoTransportChanged, apply_video_generation_transport

from src.thinking_client import (
    ThinkingLevelGeminiClient,
    WebRequestOptions,
    _encode_learning_goa,
    _encode_learning_x9b,
    _web_request,
    inject_thinking_level,
    inject_web_request_options,
)


@pytest.fixture(autouse=True)
def _clean_web_request_token():
    """每个测试前后清理 ContextVar，防止 _set_web_request 残留泄漏。"""
    _web_request.set(None)
    yield
    _web_request.set(None)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_request_data(inner_request=None):
    """构造含合法 f.req 的 request_data 字典。"""
    if inner_request is None:
        inner_request = [None] * 69
        inner_request[0] = ["prompt", 0]
    return {
        "at": "token",
        "f.req": orjson.dumps(
            [None, orjson.dumps(inner_request).decode("utf-8")]
        ).decode("utf-8"),
    }


def _parse_inner(patched):
    """从 patched request_data 中解析出 inner_request 列表。"""
    outer = orjson.loads(patched["f.req"])
    return orjson.loads(outer[1])


def _new_client():
    """用 object.__new__ 跳过 GeminiClient.__init__ 的网络依赖。"""
    return object.__new__(ThinkingLevelGeminiClient)


_VIDEO_CASES = json.loads((Path(__file__).parent / "fixtures" / "video_generation_transport_cases.json").read_text())


def _video_sdk_request(case):
    inner = [None] * 81
    inner[0] = copy.deepcopy(case["content"])
    inner[3] = case["nonce"]
    inner[4] = case["context"]
    inner[49] = case["mode"]
    inner[54] = [[[[[1]]]]]
    inner[55] = [[1]]
    return inner


def test_video_transport_contract_preserves_nonce_and_mirrors_observed_mode():
    inner = _video_sdk_request(_VIDEO_CASES["success"])
    apply_video_generation_transport(inner)
    assert inner[3:5] == ["!fixture_nonce", "fixture_context"]
    assert inner[VIDEO_GENERATION_TRANSPORT.mode_index] == 11
    assert inner[54] == [] and inner[55] == [[1]]
    assert inner[0][9] == [None] * 6 + [[[None, None, None, 1]]]


@pytest.mark.parametrize("case", ["empty", "rejected", "changed_shape"])
def test_video_transport_rejects_missing_or_changed_sdk_scaffolding(case):
    with pytest.raises(VideoTransportChanged, match="Video generation"):
        apply_video_generation_transport(_video_sdk_request(_VIDEO_CASES[case]))


def test_video_transport_shape_change_has_typed_upstream_error():
    from src.domain import result_from_exception

    result = result_from_exception(
        VideoTransportChanged("changed fixture"), logger=logging.getLogger(__name__), operation="video",
    )
    assert result.error is not None and result.error.code.value == "UPSTREAM_CHANGED"


def test_video_transport_rejects_malformed_json_as_upstream_change():
    with pytest.raises(VideoTransportChanged):
        inject_web_request_options({"f.req": "invalid"}, WebRequestOptions(video_generation=True))


def test_video_transport_is_not_in_batch_rpc_probe_registry():
    from src.infrastructure.rpc_contracts import RPC_CONTRACTS, WEB_FEATURE_PROBE_KEYS

    assert VIDEO_GENERATION_TRANSPORT.key not in RPC_CONTRACTS
    assert VIDEO_GENERATION_TRANSPORT.key not in WEB_FEATURE_PROBE_KEYS


def _registry_client(names):
    client = ThinkingLevelGeminiClient()
    for name in names:
        client._model_registry[name] = AvailableModel(
            model_id=name, model_name=name, display_name=name, description="", capacity=1,
        )
    return client


@pytest.mark.parametrize(
    ("requested", "expected"),
    [("gemini-3-flash", "gemini-flash"), ("flash", "gemini-flash"),
     ("Flash-Lite", "gemini-flash-lite"), ("lite", "gemini-flash-lite"),
     ("gemini-3-pro", "gemini-pro"), ("pro", "gemini-pro"), ("gemini-argon", "gemini-argon")],
)
def test_model_resolution_uses_exact_discovered_family_not_first_partial_match(requested, expected):
    client = _registry_client(["gemini-flash-lite", "gemini-flash", "gemini-pro", "gemini-argon"])
    assert client._resolve_model_by_name(requested).model_name == expected


@pytest.mark.parametrize(
    ("requested", "available"),
    [("gemini-3-flash", ["gemini-flash-lite", "gemini-pro"]),
     ("Flash-Lite", ["gemini-flash", "gemini-pro"]),
     ("gemini-3-pro", ["gemini-flash-lite", "gemini-flash"]),
     ("gemini-flash", ["gemini-flash-lite"])],
)
def test_missing_model_family_fails_without_silent_fallback(requested, available):
    client = _registry_client(available)
    with pytest.raises(ValueError, match="not available"):
        client._resolve_model_by_name(requested)


def test_model_resolution_does_not_trust_cross_family_alias():
    client = _registry_client(["gemini-flash-lite"])
    client._model_registry["gemini-flash-lite"].aliases = ["gemini-3-flash"]
    with pytest.raises(ValueError, match="not available"):
        client._resolve_model_by_name("gemini-3-flash")


def test_model_resolution_accepts_explicit_legacy_family_name_when_discovered():
    client = _registry_client(["gemini-3-flash", "gemini-flash-lite"])
    assert client._resolve_model_by_name("gemini-3-flash").model_name == "gemini-3-flash"


@pytest.mark.parametrize(("model", "expected"), [
    ("gemini-flash", 1), ("gemini-flash-lite", 6), ("gemini-pro", 3),
])
def test_dynamic_sdk_names_select_correct_thinking_mode(model, expected):
    client = _new_client()
    client._set_web_request(model, "standard", None)
    assert _web_request.get().thinking_mode_id == expected


# ---------------------------------------------------------------------------
# _encode_learning_x9b
# ---------------------------------------------------------------------------


def test_encode_learning_x9b_zua():
    assert _encode_learning_x9b("zUa", 7) == [[[[[7]]]]]


def test_encode_learning_x9b_qld():
    assert _encode_learning_x9b("QLd", 3) == [[[None, [3]]]]


def test_encode_learning_x9b_lyd():
    assert _encode_learning_x9b("LYd", 5) == [[[None, None, [5]]]]


def test_encode_learning_x9b_h5d():
    assert _encode_learning_x9b("h5d", 1) == [[[None, None, None, [1]]]]


def test_encode_learning_x9b_unsupported_field_raises():
    with pytest.raises(ValueError, match="unsupported Gemini learning transport field"):
        _encode_learning_x9b("unknown", 1)


# ---------------------------------------------------------------------------
# _encode_learning_goa
# ---------------------------------------------------------------------------


def test_encode_learning_goa_wraps_mode_id():
    assert _encode_learning_goa(18) == [[18]]


# ---------------------------------------------------------------------------
# inject_web_request_options — 早退守卫
# ---------------------------------------------------------------------------


def test_inject_web_request_options_returns_unchanged_when_f_req_not_str():
    """f.req 非 str（如 dict）→ 原样返回，不修改。"""
    request_data = {"at": "token", "f.req": {"not": "a string"}}
    result = inject_web_request_options(
        request_data, WebRequestOptions(thinking_mode_id=1, thinking_level_id=2)
    )
    assert result is request_data


def test_inject_web_request_options_returns_unchanged_when_outer_not_list():
    """f.req 解析后非 list → 原样返回。"""
    request_data = {"f.req": orjson.dumps("not-a-list").decode("utf-8")}
    result = inject_web_request_options(
        request_data, WebRequestOptions(thinking_mode_id=1, thinking_level_id=2)
    )
    assert result is request_data


def test_inject_web_request_options_returns_unchanged_when_outer_too_short():
    """outer_request 长度 < 2 → 原样返回。"""
    request_data = {"f.req": orjson.dumps([None]).decode("utf-8")}
    result = inject_web_request_options(
        request_data, WebRequestOptions(thinking_mode_id=1, thinking_level_id=2)
    )
    assert result is request_data


def test_inject_web_request_options_returns_unchanged_when_inner_payload_not_str():
    """outer[1] 非 str → 原样返回。"""
    request_data = {"f.req": orjson.dumps([None, 42]).decode("utf-8")}
    result = inject_web_request_options(
        request_data, WebRequestOptions(thinking_mode_id=1, thinking_level_id=2)
    )
    assert result is request_data


def test_inject_web_request_options_returns_unchanged_when_inner_not_list():
    """inner_request 解析后非 list → 原样返回。"""
    request_data = {
        "f.req": orjson.dumps(
            [None, orjson.dumps("not-a-list").decode("utf-8")]
        ).decode("utf-8")
    }
    result = inject_web_request_options(
        request_data, WebRequestOptions(thinking_mode_id=1, thinking_level_id=2)
    )
    assert result is request_data


def test_inject_web_request_options_raises_when_learning_metadata_incomplete():
    """learning_mode_id 设置但 x9b_field/x9b_value 缺失 → ValueError。"""
    request_data = _make_request_data()
    with pytest.raises(ValueError, match="learning mode transport metadata is incomplete"):
        inject_web_request_options(
            request_data,
            WebRequestOptions(learning_mode_id=18),  # 缺 x9b_field 和 x9b_value
        )


# ---------------------------------------------------------------------------
# inject_web_request_options — 注入变体
# ---------------------------------------------------------------------------


def test_inject_web_request_options_learning_only_sets_companion_fields():
    """仅 learning（无 thinking）→ [54]/[55] 设置，[79]/[80] 不设置。"""
    request_data = _make_request_data()
    result = inject_web_request_options(
        request_data,
        WebRequestOptions(
            learning_mode_id=18,
            learning_x9b_field="h5d",
            learning_x9b_value=1,
        ),
    )
    inner = _parse_inner(result)
    assert inner[54] == [[[None, None, None, [1]]]]
    assert inner[55] == [[18]]
    assert inner[79] is None
    assert inner[80] is None


def test_inject_web_request_options_thinking_only_sets_mode_and_level():
    """仅 thinking（无 learning）→ [79]/[80] 设置，[54]/[55] 不设置，required_length=81。"""
    request_data = _make_request_data()
    result = inject_web_request_options(
        request_data,
        WebRequestOptions(thinking_mode_id=3, thinking_level_id=2),
    )
    inner = _parse_inner(result)
    assert inner[79] == 3
    assert inner[80] == 2
    assert inner[54] is None
    assert inner[55] is None


@pytest.mark.parametrize("selector", [None, False, True, 0, -1, "7", [], {}])
def test_invalid_existing_selector_uses_legacy_fallback(selector):
    inner = [None] * 81
    inner[79] = selector
    result = inject_thinking_level(_make_request_data(inner), mode_id=3, level_id=2)
    assert _parse_inner(result)[79:81] == [3, 2]
    assert len(inner) == 81  # 从 69 扩展到 81


def test_inject_web_request_options_does_not_extend_when_already_long_enough():
    """inner_request 已 >= 81 → 不扩展。"""
    inner_request = [None] * 90
    inner_request[0] = ["prompt", 0]
    request_data = _make_request_data(inner_request)
    result = inject_web_request_options(
        request_data,
        WebRequestOptions(thinking_mode_id=1, thinking_level_id=1),
    )
    inner = _parse_inner(result)
    assert len(inner) == 90  # 未扩展


def test_inject_web_request_options_does_not_mutate_original():
    """注入不修改原始 request_data（patched = dict(request_data) 副本）。"""
    request_data = _make_request_data()
    original_freq = request_data["f.req"]
    inject_web_request_options(
        request_data,
        WebRequestOptions(thinking_mode_id=1, thinking_level_id=1),
    )
    assert request_data["f.req"] == original_freq


def test_inject_thinking_level_delegates_to_inject_web_request_options():
    """inject_thinking_level 是 inject_web_request_options 的便捷包装。"""
    request_data = _make_request_data()
    patched = inject_thinking_level(request_data, mode_id=6, level_id=2)
    inner = _parse_inner(patched)
    assert inner[79] == 6
    assert inner[80] == 2


# ---------------------------------------------------------------------------
# _set_web_request
# ---------------------------------------------------------------------------


def test_set_web_request_all_none_no_existing_sets_none_token():
    """thinking_level=None, learning_mode=None, 无现存 request → 设置 None token。"""
    client = _new_client()
    token = client._set_web_request(None, None, None)
    assert _web_request.get() is None
    _web_request.reset(token)


def test_set_web_request_all_none_with_existing_reuses_existing():
    """thinking_level=None, learning_mode=None, 有现存 request → 复用现存 token。"""
    existing = WebRequestOptions(thinking_mode_id=1, thinking_level_id=1)
    _web_request.set(existing)
    client = _new_client()
    client._set_web_request(None, None, None)
    assert _web_request.get() is existing


def test_set_web_request_invalid_thinking_level_raises():
    client = _new_client()
    with pytest.raises(ValueError, match="thinking_level 仅支持"):
        client._set_web_request(None, "bogus", None)


def test_set_web_request_invalid_learning_mode_raises():
    client = _new_client()
    with pytest.raises(ValueError, match="learning_mode 仅支持"):
        client._set_web_request(None, None, "bogus")


def test_set_web_request_valid_thinking_and_model_sets_options():
    """有效 thinking_level + model → 设置含 mode_id/level_id 的 WebRequestOptions。"""
    client = _new_client()
    client._set_web_request("flash", "extended", None)
    opts = _web_request.get()
    assert isinstance(opts, WebRequestOptions)
    assert opts.thinking_mode_id == 1  # flash → mode 1
    assert opts.thinking_level_id == 2  # extended → 2
    assert opts.learning_mode_id is None


def test_set_web_request_valid_learning_mode_sets_options():
    """有效 learning_mode → 设置含 learning 字段的 WebRequestOptions。"""
    client = _new_client()
    client._set_web_request(None, None, "quiz")
    opts = _web_request.get()
    assert isinstance(opts, WebRequestOptions)
    assert opts.learning_mode_id == 18  # quiz → interactive_quiz → id 18
    assert opts.learning_x9b_field == "h5d"
    assert opts.learning_x9b_value == 1
    assert opts.thinking_mode_id is None


def test_set_web_request_chinese_thinking_level_resolves():
    """中文 thinking_level '标准' → level_id=1（验证 .strip().lower() 查表）。"""
    client = _new_client()
    client._set_web_request("flash", "标准", None)
    opts = _web_request.get()
    assert opts.thinking_level_id == 1


def test_task_confirmation_choice_scope_is_request_local_and_resets_after_failure():
    client = _new_client()
    with client.task_generation_scope('c_0123456789abcdef', action_choice_id='vm'):
        with client.thinking_scope('flash', 'medium'):
            assert _web_request.get().task_action_choice_id == 'vm'
            assert _web_request.get().task_conversation_id == 'c_0123456789abcdef'
            assert _web_request.get().thinking_level_id == 4
        with pytest.raises(RuntimeError), client.task_generation_scope('c_fedcba9876543210', action_choice_id='local_chrome:fixture'):
            with client.thinking_scope('flash', 'high'):
                assert _web_request.get().task_action_choice_id == 'local_chrome:fixture'
                raise RuntimeError('synthetic failure')
        with client.thinking_scope('flash', 'medium'):
            assert _web_request.get().task_action_choice_id == 'vm'
    with client.thinking_scope('flash', 'low'):
        assert not _web_request.get().task_generation
        assert _web_request.get().task_action_choice_id is None
        assert _web_request.get().task_conversation_id is None


# ---------------------------------------------------------------------------
# _with_learning_prompt + _prefix_learning_prompt
# ---------------------------------------------------------------------------


def test_with_learning_prompt_returns_unchanged_when_no_learning_mode():
    """learning_mode=None → 原样返回 args/kwargs。"""
    client = _new_client()
    args, kwargs = client._with_learning_prompt(("hi",), {"extra": 1}, None)
    assert args == ("hi",)
    assert kwargs == {"extra": 1}


def test_with_learning_prompt_prefixes_positional_args_prompt():
    """prompt 在 args[0] + learning_mode → 前缀 args[0]。"""
    client = _new_client()
    args, kwargs = client._with_learning_prompt(("光合作用",), {}, "quiz")
    assert args == ("生成一份关于以下内容的互动式测验： 光合作用",)
    assert kwargs == {}


def test_with_learning_prompt_prefixes_kwargs_prompt():
    """prompt 在 kwargs['prompt'] + learning_mode → 前缀 kwargs['prompt']。"""
    client = _new_client()
    args, kwargs = client._with_learning_prompt((), {"prompt": "光合作用"}, "quiz")
    assert args == ()
    assert kwargs["prompt"] == "生成一份关于以下内容的互动式测验： 光合作用"


def test_with_learning_prompt_returns_unchanged_when_empty_args_no_prompt_kwarg():
    """空 args + 无 prompt kwarg + learning_mode → 原样返回（无处可前缀）。"""
    client = _new_client()
    args, kwargs = client._with_learning_prompt((), {"other": 1}, "quiz")
    assert args == ()
    assert kwargs == {"other": 1}


def test_prefix_learning_prompt_returns_non_str_unchanged():
    """非 str prompt → 原样返回。"""
    result = ThinkingLevelGeminiClient._prefix_learning_prompt("prefix: ", 42)
    assert result == 42


def test_prefix_learning_prompt_returns_already_prefixed_unchanged():
    """prompt 已以 prefix 开头 → 不重复前缀。"""
    prefix = "生成一份关于以下内容的互动式测验： "
    result = ThinkingLevelGeminiClient._prefix_learning_prompt(prefix, prefix + "内容")
    assert result == prefix + "内容"


# ---------------------------------------------------------------------------
# thinking_scope
# ---------------------------------------------------------------------------


def test_thinking_scope_sets_and_resets_token():
    """thinking_scope 进入时设置 options，退出时 reset token。"""
    client = _new_client()
    assert _web_request.get() is None
    with client.thinking_scope("flash", "extended"):
        opts = _web_request.get()
        assert isinstance(opts, WebRequestOptions)
        assert opts.thinking_mode_id == 1
        assert opts.thinking_level_id == 2
    # 退出后 reset 为 None
    assert _web_request.get() is None


def test_thinking_scope_resets_token_even_on_exception():
    """thinking_scope 内抛异常 → finally 仍 reset token。"""
    client = _new_client()
    with pytest.raises(RuntimeError, match="boom"):
        with client.thinking_scope("flash", "standard"):
            raise RuntimeError("boom")
    assert _web_request.get() is None


def test_video_scope_builds_video_transport_and_preserves_thinking(monkeypatch):
    observed = []

    async def fake_stream(_client, *args, **kwargs):
        observed.append((kwargs.get("deep_research"), _web_request.get()))
        patched = inject_web_request_options(_make_request_data(_video_sdk_request(_VIDEO_CASES["success"])), _web_request.get())
        inner = _parse_inner(patched)
        assert inner[49] == 11 and inner[0][9][6][0][3] == 1
        yield SimpleNamespace(text="video")

    monkeypatch.setattr(GeminiClient, "generate_content_stream", fake_stream)
    client = _new_client()

    async def run():
        with client.thinking_scope("flash", "standard"):
            with client.video_generation_scope():
                assert [item.text async for item in client.generate_content_stream("make a video")] == ["video"]
            assert _web_request.get().video_generation is False
        assert _web_request.get() is None

    asyncio.run(run())
    assert observed[0][0] is True
    assert observed[0][1].video_generation is True
    assert observed[0][1].thinking_mode_id == 1 and observed[0][1].thinking_level_id == 1


def test_video_and_normal_generation_scopes_are_task_local(monkeypatch):
    observed = {}

    async def fake_generate(_client, prompt, **kwargs):
        await asyncio.sleep(0)
        observed[prompt] = (kwargs.get("deep_research", False), _web_request.get())
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)
    client = _new_client()

    async def video():
        with client.video_generation_scope():
            await client.generate_content("video", model="flash", thinking_level="standard")

    async def normal():
        await client.generate_content("normal", model="flash", thinking_level="extended")

    async def run():
        await asyncio.gather(video(), normal())

    asyncio.run(run())
    assert observed["video"][0] is True and observed["video"][1].video_generation is True
    assert observed["normal"][0] is False and observed["normal"][1].video_generation is False
    assert observed["normal"][1].thinking_level_id == 2


def test_video_scope_rejects_deep_research_and_learning_modes(monkeypatch):
    called = []

    async def fake_generate(_client, *args, **kwargs):
        called.append(True)
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)
    client = _new_client()

    async def run():
        with client.video_generation_scope():
            with pytest.raises(ValueError, match="Deep Research"):
                await client.generate_content("video", deep_research=True)
            with pytest.raises(ValueError, match="learning mode"):
                await client.generate_content("video", learning_mode="quiz")

    asyncio.run(run())
    assert called == []


# ---------------------------------------------------------------------------
# generate_content
# ---------------------------------------------------------------------------


def test_generate_content_with_model_calls_super_with_model(monkeypatch):
    """model 传入 → super().generate_content(*args, model=model, **kwargs)。"""
    captured = {}

    async def fake_generate(self, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(text="result")

    monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)
    client = _new_client()
    result = asyncio.run(
        client.generate_content("prompt", model="flash", thinking_level="standard")
    )
    assert result.text == "result"
    assert captured["args"] == ("prompt",)
    assert captured["kwargs"]["model"] == "flash"
    # token 在 finally 中 reset
    assert _web_request.get() is None


def test_generate_content_without_model_omits_model_kwarg(monkeypatch):
    """model=None → super().generate_content(*args, **kwargs) 不传 model。"""
    captured = {}

    async def fake_generate(self, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)
    client = _new_client()
    asyncio.run(client.generate_content("prompt"))
    assert "model" not in captured["kwargs"]
    assert _web_request.get() is None


def test_generate_content_resets_token_on_exception(monkeypatch):
    """super().generate_content 抛异常 → finally 仍 reset token。"""

    async def fake_generate(self, *args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)
    client = _new_client()
    with pytest.raises(RuntimeError, match="network down"):
        asyncio.run(
            client.generate_content("p", model="flash", thinking_level="standard")
        )
    assert _web_request.get() is None


def test_generate_content_applies_learning_prompt_prefix(monkeypatch):
    """learning_mode 传入 → _with_learning_prompt 前缀注入 args[0]。"""
    captured = {}

    async def fake_generate(self, *args, **kwargs):
        captured["args"] = args
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)
    client = _new_client()
    asyncio.run(
        client.generate_content("光合作用", model="flash", learning_mode="quiz")
    )
    assert captured["args"][0].startswith("生成一份关于以下内容的互动式测验：")


# ---------------------------------------------------------------------------
# generate_content_stream
# ---------------------------------------------------------------------------


def test_generate_content_stream_with_model_yields_outputs(monkeypatch):
    """model 传入 → super().generate_content_stream(*args, model=model) 产出。"""
    captured = {}

    async def fake_stream(self, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        for i in range(3):
            yield SimpleNamespace(text=f"chunk{i}")

    monkeypatch.setattr(GeminiClient, "generate_content_stream", fake_stream)
    client = _new_client()

    async def run():
        results = []
        async for output in client.generate_content_stream(
            "prompt", model="flash", thinking_level="standard"
        ):
            results.append(output.text)
        return results

    chunks = asyncio.run(run())
    assert chunks == ["chunk0", "chunk1", "chunk2"]
    assert captured["kwargs"]["model"] == "flash"
    assert _web_request.get() is None


def test_generate_content_stream_without_model_omits_model_kwarg(monkeypatch):
    """model=None → super().generate_content_stream(*args, **kwargs) 不传 model。"""
    captured = {}

    async def fake_stream(self, *args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        yield SimpleNamespace(text="done")

    monkeypatch.setattr(GeminiClient, "generate_content_stream", fake_stream)
    client = _new_client()

    async def run():
        async for _ in client.generate_content_stream("prompt"):
            pass

    asyncio.run(run())
    assert "model" not in captured["kwargs"]
    assert _web_request.get() is None


def test_generate_content_stream_resets_token_on_exception(monkeypatch):
    """stream 迭代中抛异常 → finally 仍 reset token。"""

    async def fake_stream(self, *args, **kwargs):
        yield SimpleNamespace(text="partial")
        raise RuntimeError("stream broke")

    monkeypatch.setattr(GeminiClient, "generate_content_stream", fake_stream)
    client = _new_client()

    async def run():
        async for _ in client.generate_content_stream("p", model="flash",
                                                       thinking_level="standard"):
            pass

    with pytest.raises(RuntimeError, match="stream broke"):
        asyncio.run(run())
    assert _web_request.get() is None


# ---------------------------------------------------------------------------
# _install_thinking_transport
# ---------------------------------------------------------------------------


def test_install_thinking_transport_noop_when_session_is_none():
    """self.client 为 None → 直接返回，不 patch。"""
    client = _new_client()
    client.client = None
    client._install_thinking_transport()
    # 无异常即通过


def test_install_thinking_transport_noop_when_already_installed():
    """session 已有 _mcp_thinking_stream=True → 直接返回。"""
    client = _new_client()
    session = SimpleNamespace(stream=lambda *a, **kw: None, _mcp_thinking_stream=True)
    client.client = session
    client._install_thinking_transport()
    # stream 未被替换
    assert session.stream.__name__ == "<lambda>"


def test_install_thinking_transport_patches_stream_and_sets_flag():
    """正常 session → 替换 stream 为 stream_with_thinking，设 _mcp_thinking_stream=True。"""
    client = _new_client()
    original_stream = lambda *a, **kw: "original"  # noqa: E731
    session = SimpleNamespace(stream=original_stream)
    client.client = session
    client._install_thinking_transport()
    assert session.stream.__name__ == "stream_with_thinking"
    assert session._mcp_thinking_stream is True


def test_install_thinking_transport_skips_injection_when_no_active_request():
    """stream_with_thinking: _web_request 为 None → 调原始 stream，不注入。"""
    client = _new_client()
    calls = []

    def original_stream(method, url, *args, **kwargs):
        calls.append(("original", method, url, kwargs))
        return "result"

    session = SimpleNamespace(stream=original_stream)
    client.client = session
    client._install_thinking_transport()
    # _web_request 为 None（fixture 清理）
    result = session.stream("POST", "https://example.com", data={"key": "val"})
    assert result == "result"
    assert calls[0][0] == "original"
    assert "headers" not in calls[0][3] or calls[0][3].get("headers") is None


def test_install_thinking_transport_skips_when_url_not_generate():
    """url != Endpoint.GENERATE → 调原始 stream，不注入。"""
    client = _new_client()
    _web_request.set(WebRequestOptions(thinking_mode_id=1, thinking_level_id=1))
    calls = []

    def original_stream(method, url, *args, **kwargs):
        calls.append(kwargs)
        return "ok"

    session = SimpleNamespace(stream=original_stream)
    client.client = session
    client._install_thinking_transport()
    session.stream("POST", "https://other.example.com", data={"k": "v"})
    # 未注入 headers
    assert "x-goog-ext-73010990-jspb" not in (calls[0].get("headers") or {})


def test_install_thinking_transport_skips_when_data_not_dict():
    """url==GENERATE 但 data 非 dict → 调原始 stream，不注入。"""
    client = _new_client()
    _web_request.set(WebRequestOptions(thinking_mode_id=1, thinking_level_id=1))
    calls = []

    def original_stream(method, url, *args, **kwargs):
        calls.append(kwargs)
        return "ok"

    session = SimpleNamespace(stream=original_stream)
    client.client = session
    client._install_thinking_transport()
    session.stream("POST", Endpoint.GENERATE, data="not-a-dict")
    assert "x-goog-ext-73010990-jspb" not in (calls[0].get("headers") or {})


def test_video_transport_rejects_missing_form_data_instead_of_sending_research():
    client = _new_client()
    _web_request.set(WebRequestOptions(video_generation=True))
    calls = []
    session = SimpleNamespace(stream=lambda *args, **kwargs: calls.append((args, kwargs)))
    client.client = session
    client._install_thinking_transport()
    with pytest.raises(VideoTransportChanged, match="Video generation transport request is unavailable"):
        session.stream("POST", Endpoint.GENERATE, data="not-a-dict")
    assert calls == []


def test_install_thinking_transport_injects_on_generate_with_dict_data():
    """url==GENERATE + data 是 dict + 有 active request → 注入 options + 加 header。"""
    client = _new_client()
    _web_request.set(WebRequestOptions(thinking_mode_id=1, thinking_level_id=1))
    calls = []

    def original_stream(method, url, *args, **kwargs):
        calls.append(kwargs)
        return "ok"

    session = SimpleNamespace(stream=original_stream)
    client.client = session
    client._install_thinking_transport()
    request_data = _make_request_data()
    session.stream("POST", Endpoint.GENERATE, data=request_data)
    injected = calls[0]
    # data 被注入（f.req 改变）
    assert injected["data"]["f.req"] != request_data["f.req"]
    # header 被添加
    assert injected["headers"]["x-goog-ext-73010990-jspb"] == "[0,0,0]"


def test_install_thinking_transport_preserves_existing_headers():
    """注入时保留已有 headers（dict(kwargs.get('headers') or {}) 副本）。"""
    client = _new_client()
    _web_request.set(WebRequestOptions(thinking_mode_id=1, thinking_level_id=1))
    calls = []

    def original_stream(method, url, *args, **kwargs):
        calls.append(kwargs)
        return "ok"

    session = SimpleNamespace(stream=original_stream)
    client.client = session
    client._install_thinking_transport()
    session.stream(
        "POST", Endpoint.GENERATE,
        data=_make_request_data(),
        headers={"existing": "kept"},
    )
    assert calls[0]["headers"]["existing"] == "kept"
    assert calls[0]["headers"]["x-goog-ext-73010990-jspb"] == "[0,0,0]"


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_calls_super_init_then_installs_transport(monkeypatch):
    """init → super().init() + _install_thinking_transport()。"""

    async def fake_init(self, *args, **kwargs):
        self._initialized = True

    monkeypatch.setattr(GeminiClient, "init", fake_init)
    client = _new_client()
    client.client = SimpleNamespace(stream=lambda *a, **kw: None)

    asyncio.run(client.init())

    assert client._initialized is True
    assert client.client._mcp_thinking_stream is True


@pytest.mark.parametrize(
    ("api", "level", "scope_level", "native", "expected"),
    [
        ("generate", "standard", None, True, 1),
        ("generate", "extended", None, False, 2),
        ("stream", "standard", None, True, 1),
        ("stream", "extended", None, False, 2),
        ("generate", "low", None, True, 1),
        ("generate", "medium", None, False, 4),
        ("stream", "high", None, False, 2),
        ("stream", "中", None, False, 4),
        ("generate", None, "medium", False, 4),
        ("generate-default", "medium", None, False, 4),
        ("generate", None, "extended", False, 2),
        ("stream", None, "extended", False, 2),
        ("generate", None, None, True, 2),
        ("stream", None, None, False, 1),
    ],
)
def test_real_sdk_model_header_and_body_agree_on_thinking_level(
    monkeypatch, api, level, scope_level, native, expected,
):
    """Exercise SDK construction and our transport with a synthetic, unsent request."""
    from contextlib import nullcontext
    from gemini_webapi.constants import MODEL_HEADER_KEY

    class CapturedRequest(BaseException):
        pass

    class FakeSession:
        def __init__(self):
            self.kwargs = None

        def stream(self, method, url, **kwargs):
            assert method == "POST" and url == Endpoint.GENERATE
            self.kwargs = kwargs
            raise CapturedRequest()

    async def no_activity(_self):
        return None

    monkeypatch.setattr(GeminiClient, "_sync_activity", no_activity)
    client = _registry_client(["gemini-pro", "gemini-flash"])
    model = client._model_registry["gemini-pro"]
    session = FakeSession()
    client.client = session
    client._running = True
    client._install_thinking_transport()

    async def run():
        scope = client.thinking_scope("gemini-pro", scope_level) if scope_level else nullcontext()
        with scope, pytest.raises(CapturedRequest):
            kwargs = {"model": model, "extended_thinking": native}
            if api == "generate-default":
                kwargs.pop("model")
            if level:
                kwargs["thinking_level"] = level
            if api.startswith("generate"):
                await client.generate_content("synthetic", **kwargs)
            else:
                async for _ in client.generate_content_stream("synthetic", **kwargs):
                    pass

    asyncio.run(run())
    assert session.kwargs is not None
    header = json.loads(session.kwargs["headers"][MODEL_HEADER_KEY])
    body = _parse_inner(session.kwargs["data"])
    assert header[-2] == expected
    assert body[80] == expected


@pytest.mark.parametrize("number", [7, 8])
@pytest.mark.parametrize("video", [False, True])
@pytest.mark.parametrize("capacity_field", [12, 13])
def test_real_sdk_dynamic_model_selector_survives_transport(monkeypatch, number, video, capacity_field):
    """The SDK-discovered selector remains aligned with its model header."""
    from contextlib import nullcontext
    from gemini_webapi.constants import MODEL_HEADER_KEY

    class CapturedRequest(BaseException):
        pass

    class FakeSession:
        def stream(self, method, url, **kwargs):
            assert method == "POST" and url == Endpoint.GENERATE
            self.kwargs = kwargs
            raise CapturedRequest()

    async def no_activity(_self):
        return None

    monkeypatch.setattr(GeminiClient, "_sync_activity", no_activity)
    monkeypatch.setattr(GeminiClient, "_check_account_status", lambda *_a, **_kw: None)
    client = _registry_client(["gemini-pro"])
    client._model_registry["gemini-pro"].model_number = number
    client._model_registry["gemini-pro"].capacity_field = capacity_field
    client.client = session = FakeSession()
    client._running = True
    client._install_thinking_transport()

    async def run():
        with client.video_generation_scope() if video else nullcontext():
            with pytest.raises(CapturedRequest):
                async for _ in client.generate_content_stream(
                    "synthetic", model="gemini-3-pro", thinking_level="high" if video else "medium",
                    current_retry=0,
                ):
                    pass

    asyncio.run(run())
    header = json.loads(session.kwargs["headers"][MODEL_HEADER_KEY])
    body = _parse_inner(session.kwargs["data"])
    expected_level = 2 if video else 4
    assert header[-3:-1] == [number, expected_level]
    assert body[79:81] == [number, expected_level]
    assert body[49] == (11 if video else None)
    if video:
        assert body[0][9][6][0][3] == 1


def test_native_level_propagates_through_video_and_learning_without_leaking(monkeypatch):
    captured = []

    async def fake_generate(_self, *args, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(GeminiClient, "generate_content", fake_generate)
    client = _new_client()

    async def run():
        with client.thinking_scope("pro", "extended"):
            with client.video_generation_scope():
                await client.generate_content("video")
        await client.generate_content("quiz", model="pro", thinking_level="standard", learning_mode="quiz")
        await client.generate_content("normal", extended_thinking=True)

    asyncio.run(run())
    assert captured[0]["deep_research"] is True
    assert captured[0]["extended_thinking"] is True
    assert captured[1]["extended_thinking"] is False
    assert captured[2]["extended_thinking"] is True
    assert "deep_research" not in captured[2]
    assert _web_request.get() is None


def test_sdk_transport_observes_metadata_before_any_model_output(monkeypatch):
    """Use the installed SDK frame parser with synthetic bytes and no network."""
    from src.services.cleanup_scope import observe_cleanup_frames

    inner = json.dumps([None, ["c_first_frame", "r_first_frame"]], separators=(",", ":"))
    body = json.dumps([[None, None, inner]], separators=(",", ":"))
    wire = (str(len(body) + 1) + "\n" + body).encode()

    class Response:
        status_code = 200
        http_version = 2

        async def aiter_content(self):
            yield wire
            raise asyncio.CancelledError()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class Session:
        def stream(self, *args, **kwargs):
            return Response()

    async def no_activity(_self):
        return None

    monkeypatch.setattr(GeminiClient, "_sync_activity", no_activity)
    client = _registry_client(["gemini-pro"])
    client.client = Session()
    client._running = True
    chat = client.create_cleanup_chat()
    observed = []

    async def run():
        with observe_cleanup_frames(lambda item: observed.append(getattr(item, "cid", ""))):
            with pytest.raises(asyncio.CancelledError):
                await client.generate_content("synthetic", chat=chat, model=client._model_registry["gemini-pro"])

    asyncio.run(run())
    assert "c_first_frame" in observed


@pytest.mark.parametrize("early_identity", [False, True])
@pytest.mark.parametrize("retry_argument", [None, 5])
def test_sdk_research_plan_never_replays_failed_generation(monkeypatch, early_identity, retry_argument):
    """Exercise native research and the installed retry decorator without network."""
    from unittest.mock import AsyncMock
    from gemini_webapi.exceptions import APIError
    from src.services.cleanup_scope import observe_cleanup_frames

    inner = json.dumps([None, ["c_synthetic", "r_synthetic"]], separators=(",", ":"))
    body = json.dumps([[None, None, inner]], separators=(",", ":"))
    wire = (str(len(body) + 1) + "\n" + body).encode()

    class Response:
        status_code = 200
        http_version = 2
        async def aiter_content(self):
            if early_identity:
                yield wire
            raise APIError("synthetic transport failure")
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return False

    class Transport:
        requests = 0
        def stream(self, *_args, **_kwargs):
            self.requests += 1
            return Response()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    monkeypatch.setattr(GeminiClient, "_check_account_status", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(GeminiClient, "close", AsyncMock())
    monkeypatch.setattr(GeminiClient, "fetch_latest_chat_response", AsyncMock(return_value=None))
    client = _registry_client(["gemini-pro"])
    client.client = transport = Transport()
    client._running = True
    chat = client.start_chat(model=client._model_registry["gemini-pro"])
    original_send = chat.send_message
    async def send_with_retry_argument(prompt, **kwargs):
        return await original_send(prompt, current_retry=retry_argument, **kwargs)
    monkeypatch.setattr(type(chat), "send_message", lambda _self, prompt, **kwargs: send_with_retry_argument(prompt, **kwargs))

    observed = []
    async def run():
        with observe_cleanup_frames(lambda value: observed.append(value.cid)):
            with pytest.raises(APIError):
                await client.create_deep_research_plan("synthetic", chat=chat)
    asyncio.run(run())
    assert transport.requests == 1
    assert ("c_synthetic" in observed) is early_identity


def test_native_research_plan_and_confirmation_are_distinct_turns(monkeypatch):
    from unittest.mock import AsyncMock
    from gemini_webapi.types import Candidate, DeepResearchPlan, ModelOutput

    prompts = []
    async def fake_sdk_generate(_client, prompt, **_kwargs):
        prompts.append(prompt)
        plan = DeepResearchPlan(title="Synthetic", confirm_prompt="Start research") if len(prompts) == 1 else None
        yield ModelOutput(metadata=["c_synthetic", "r_synthetic", "rc_synthetic"],
                          candidates=[Candidate(rcid="rc_synthetic", text="ok", deep_research_plan=plan)])

    monkeypatch.setattr(GeminiClient, "_generate", fake_sdk_generate)
    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    monkeypatch.setattr(GeminiClient, "_check_account_status", lambda *_args, **_kwargs: True)
    client = _registry_client(["gemini-pro"])
    client._running = True
    chat = client.start_chat(model=client._model_registry["gemini-pro"])
    async def run():
        plan = await client.create_deep_research_plan("synthetic", chat=chat)
        await client.start_deep_research(plan, chat=chat)
    asyncio.run(run())
    assert prompts == ["synthetic", "Start research"]
