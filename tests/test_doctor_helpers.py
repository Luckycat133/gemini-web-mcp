"""gemini_doctor 内部 helper 的行为测试。

调研发现 _doctor_payload 已有 1 个行为测试（profile_alignment warn 分支），
但 _doctor_check / _doctor_overall_status / _format_doctor_markdown 三个
helper 零直接覆盖，_doctor_payload 的 cookie_status 4 分支仅覆盖 1 个
（no cookie），browser_profiles 4 分支仅覆盖 1 个（alignment warn），
ffprobe/generated_media_dir 的 warn 路径与 recommendations 触发未测。
本文件补充：

- _doctor_check: None 值过滤、details 结构
- _doctor_overall_status: 6 种状态组合（空/全 ok/全 skip/混合/warn 优先/error 优先）
- _format_doctor_markdown: browser=disabled / error profile / account=None / 空 recommendations / detail 白名单
- _doctor_payload:
  * cookie_status 3 分支（manager unavailable / needs_refresh / ok）
  * browser_profiles 3 分支（browser="" skip / 全 error warn / 无 psid warn）
  * browser_profile_alignment ok 分支
  * ffprobe warn + generated_media warn + recommendations 触发
"""

import json

import src.tools.manage as manage_tools
from src.tools.manage import (
    _doctor_check,
    _doctor_overall_status,
    _doctor_payload,
    _format_doctor_markdown,
)


def _patch_doctor_env(monkeypatch, *, cookie_status, browser_profiles=None,
                      ffprobe_path="/opt/ffprobe", media_dir_exists=True):
    """统一 patch doctor_payload 的 4 个外部依赖。"""
    monkeypatch.setenv("GEMINI_TOOLS", "core")
    monkeypatch.setattr(manage_tools, "get_cookie_status", lambda: cookie_status)
    if browser_profiles is not None:
        monkeypatch.setattr(
            manage_tools, "list_browser_cookie_profiles",
            lambda browser, validate=False: browser_profiles,
        )
    monkeypatch.setattr(
        manage_tools.shutil, "which",
        lambda name: ffprobe_path if name == "ffprobe" else None,
    )
    monkeypatch.setattr(
        manage_tools.os.path, "isdir",
        lambda path: media_dir_exists if path.endswith("generated_media") else False,
    )


# ---------------------------------------------------------------------------
# _doctor_check
# ---------------------------------------------------------------------------


def test_doctor_check_filters_none_values_from_details():
    """_doctor_check 把 details 中值为 None 的键过滤掉。"""
    check = _doctor_check("my_check", "ok", "all good", a=1, b=None, c="x", d=None)
    assert check["name"] == "my_check"
    assert check["status"] == "ok"
    assert check["message"] == "all good"
    assert check["details"] == {"a": 1, "c": "x"}


def test_doctor_check_with_no_details_yields_empty_dict():
    """无 kwargs 时 details 为空 dict。"""
    check = _doctor_check("x", "warn", "msg")
    assert check["details"] == {}


# ---------------------------------------------------------------------------
# _doctor_overall_status
# ---------------------------------------------------------------------------


def test_overall_status_empty_returns_ok():
    """空 checks 列表 → ok（落入 default 分支）。"""
    assert _doctor_overall_status([]) == "ok"


def test_overall_status_all_ok():
    """全 ok → ok。"""
    assert _doctor_overall_status([{"status": "ok"}, {"status": "ok"}]) == "ok"


def test_overall_status_all_skip():
    """全 skip → skip。"""
    assert _doctor_overall_status([{"status": "skip"}, {"status": "skip"}]) == "skip"


def test_overall_status_mixed_ok_skip_returns_ok():
    """ok + skip 混合（无 warn/error）→ ok。"""
    assert _doctor_overall_status([{"status": "ok"}, {"status": "skip"}]) == "ok"


def test_overall_status_warn_overrides_ok_skip():
    """warn 优先于 ok/skip。"""
    assert _doctor_overall_status([{"status": "ok"}, {"status": "warn"}, {"status": "skip"}]) == "warn"


def test_overall_status_error_overrides_all():
    """error 优先于 warn/ok/skip。"""
    assert _doctor_overall_status([{"status": "warn"}, {"status": "error"}, {"status": "skip"}]) == "error"


# ---------------------------------------------------------------------------
# _format_doctor_markdown
# ---------------------------------------------------------------------------


def _minimal_payload(**overrides):
    base = {
        "name": "gemini_doctor",
        "overall_status": "ok",
        "safe": True,
        "validate_browser": False,
        "browser": "chrome",
        "checks": [
            {"name": "python_runtime", "status": "ok", "message": "Python 3.10", "details": {"executable": "/usr/bin/python"}},
        ],
        "browser_profiles": [],
        "recommendations": [],
    }
    base.update(overrides)
    return base


def test_format_doctor_markdown_browser_disabled():
    """browser='' → 第二行渲染 'Browser: disabled'。"""
    text = _format_doctor_markdown(_minimal_payload(browser=""))
    assert "Browser: disabled" in text
    assert "validate_browser=False" in text


def test_format_doctor_markdown_error_profile_entry():
    """browser_profiles 含 error → 渲染 'error=...'。"""
    payload = _minimal_payload(browser_profiles=[
        {"browser": "chrome", "profile": None, "error": "RuntimeError: boom"},
    ])
    text = _format_doctor_markdown(payload)
    assert "### Browser Profiles" in text
    assert "chrome: error=RuntimeError: boom" in text


def test_format_doctor_markdown_account_available_none_renders_unvalidated():
    """account_available=None → 渲染 'account=unvalidated'。"""
    payload = _minimal_payload(browser_profiles=[
        {"profile": "Default", "has_psid": True, "chrome_selected_profile": True,
         "account_available": None, "scheduled_registry_count": None},
    ])
    text = _format_doctor_markdown(payload)
    assert "account=unvalidated" in text
    # scheduled_registry_count=None 直接渲染为字面量 None（formatter 只归一化 account_available）
    assert "scheduled_registry_count=None" in text


def test_format_doctor_markdown_empty_recommendations_omits_section():
    """recommendations 为空 → 不输出 '### Recommendations' 小节。"""
    text = _format_doctor_markdown(_minimal_payload(recommendations=[]))
    assert "### Recommendations" not in text


def test_format_doctor_markdown_detail_whitelist_only_renders_four_keys():
    """check 的 details 只渲染 source/selected_profile/recommended_profile/path 4 个 key。"""
    payload = _minimal_payload(checks=[
        {"name": "x", "status": "ok", "message": "m",
         "details": {"executable": "/x", "total_count": 10, "source": "env", "path": "/p"}},
    ])
    text = _format_doctor_markdown(payload)
    assert "source: env" in text
    assert "path: /p" in text
    # executable / total_count 不在白名单
    assert "executable: /x" not in text
    assert "total_count: 10" not in text


# ---------------------------------------------------------------------------
# _doctor_payload — cookie_status 分支
# ---------------------------------------------------------------------------


def test_payload_cookie_status_manager_unavailable(monkeypatch):
    """cookie manager 不可用 → cookie_status check 为 warn，details 为空。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": False})
    payload = _doctor_payload(browser="", validate_browser=False)
    cookie_check = next(c for c in payload["checks"] if c["name"] == "cookie_status")
    assert cookie_check["status"] == "warn"
    assert "unavailable" in cookie_check["message"].lower()
    assert cookie_check["details"] == {}


def test_payload_cookie_status_needs_refresh(monkeypatch):
    """needs_refresh=True → warn，details 含 source 与 cookie_status。"""
    _patch_doctor_env(monkeypatch, cookie_status={
        "available": True, "has_cookie": True, "needs_refresh": True,
        "status": "expiring", "source": "browser_chrome",
    })
    payload = _doctor_payload(browser="", validate_browser=False)
    cookie_check = next(c for c in payload["checks"] if c["name"] == "cookie_status")
    assert cookie_check["status"] == "warn"
    assert "refreshed" in cookie_check["message"].lower()
    assert cookie_check["details"].get("source") == "browser_chrome"
    assert cookie_check["details"].get("cookie_status") == "expiring"


def test_payload_cookie_status_ok(monkeypatch):
    """has_cookie=True + needs_refresh=False → ok，details 含 source。"""
    _patch_doctor_env(monkeypatch, cookie_status={
        "available": True, "has_cookie": True, "needs_refresh": False,
        "status": "loaded", "source": "env",
    })
    payload = _doctor_payload(browser="", validate_browser=False)
    cookie_check = next(c for c in payload["checks"] if c["name"] == "cookie_status")
    assert cookie_check["status"] == "ok"
    assert cookie_check["details"].get("source") == "env"


# ---------------------------------------------------------------------------
# _doctor_payload — browser_profiles 分支
# ---------------------------------------------------------------------------


def test_payload_browser_empty_skips_browser_check(monkeypatch):
    """browser='' → browser_profiles check 为 skip，browser_profiles 列表为空。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": True, "has_cookie": True, "needs_refresh": False})
    payload = _doctor_payload(browser="", validate_browser=False)
    browser_check = next(c for c in payload["checks"] if c["name"] == "browser_profiles")
    assert browser_check["status"] == "skip"
    assert "disabled" in browser_check["message"].lower()
    assert payload["browser_profiles"] == []


def test_payload_browser_all_errors_warn(monkeypatch):
    """list_browser_cookie_profiles 抛异常 → fallback error 条目，check 为 warn(errors)。"""
    def raise_error(browser, validate=False):
        raise RuntimeError("permission denied")
    monkeypatch.setenv("GEMINI_TOOLS", "core")
    monkeypatch.setattr(manage_tools, "get_cookie_status",
                       lambda: {"available": True, "has_cookie": True, "needs_refresh": False})
    monkeypatch.setattr(manage_tools, "list_browser_cookie_profiles", raise_error)
    monkeypatch.setattr(manage_tools.shutil, "which", lambda name: "/ff")
    monkeypatch.setattr(manage_tools.os.path, "isdir", lambda path: True)

    payload = _doctor_payload(browser="chrome", validate_browser=False)
    browser_check = next(c for c in payload["checks"] if c["name"] == "browser_profiles")
    assert browser_check["status"] == "warn"
    assert "errors" in browser_check["details"]
    assert payload["browser_profiles"][0]["error"] is not None
    assert "RuntimeError" in payload["browser_profiles"][0]["error"]


def test_payload_browser_no_psid_warn(monkeypatch):
    """所有 profile 都无 psid → browser_profiles check 为 warn(profiles)。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": True, "has_cookie": True, "needs_refresh": False},
                      browser_profiles=[
                          {"browser": "chrome", "profile": "Default", "has_psid": False, "has_psidts": False, "cookie_count": 0},
                      ])
    payload = _doctor_payload(browser="chrome", validate_browser=False)
    browser_check = next(c for c in payload["checks"] if c["name"] == "browser_profiles")
    assert browser_check["status"] == "warn"
    assert "profiles" in browser_check["details"]


def test_payload_browser_alignment_ok(monkeypatch):
    """selected profile 有 psid → browser_profile_alignment check 为 ok。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": True, "has_cookie": True, "needs_refresh": False},
                      browser_profiles=[
                          {"browser": "chrome", "profile": "Default", "has_psid": True, "has_psidts": True,
                           "cookie_count": 10, "chrome_selected_profile": True,
                           "chrome_selected_profile_directory": "Default",
                           "account_available": True, "scheduled_registry_count": 2},
                      ])
    payload = _doctor_payload(browser="chrome", validate_browser=False)
    alignment_check = next(c for c in payload["checks"] if c["name"] == "browser_profile_alignment")
    assert alignment_check["status"] == "ok"
    assert alignment_check["details"].get("selected_profile") == "Default"


# ---------------------------------------------------------------------------
# _doctor_payload — ffprobe / generated_media / recommendations
# ---------------------------------------------------------------------------


def test_payload_ffprobe_missing_warns_and_recommends(monkeypatch):
    """ffprobe 缺失 → ffprobe check 为 warn，recommendations 含安装提示。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": True, "has_cookie": True, "needs_refresh": False},
                      browser_profiles=[], ffprobe_path=None)
    payload = _doctor_payload(browser="", validate_browser=False)
    ffprobe_check = next(c for c in payload["checks"] if c["name"] == "ffprobe")
    assert ffprobe_check["status"] == "warn"
    assert "path" not in ffprobe_check["details"]  # None 被过滤
    assert any("ffmpeg" in r.lower() or "ffprobe" in r.lower() for r in payload["recommendations"])


def test_payload_generated_media_dir_missing_warns(monkeypatch):
    """generated_media 目录不存在 → generated_media_dir check 为 warn。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": True, "has_cookie": True, "needs_refresh": False},
                      browser_profiles=[], media_dir_exists=False)
    payload = _doctor_payload(browser="", validate_browser=False)
    media_check = next(c for c in payload["checks"] if c["name"] == "generated_media_dir")
    assert media_check["status"] == "warn"
    assert "does not exist" in media_check["message"]


def test_payload_validate_browser_false_recommends_validation(monkeypatch):
    """validate_browser=False → recommendations 含 'validate_browser=true' 提示。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": True, "has_cookie": True, "needs_refresh": False},
                      browser_profiles=[])
    payload = _doctor_payload(browser="", validate_browser=False)
    assert any("validate_browser=true" in r for r in payload["recommendations"])


def test_payload_overall_status_warn_when_cookie_warn(monkeypatch):
    """cookie_status 为 warn 时 overall_status 也为 warn（即使其他全 ok）。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": False},
                      browser_profiles=[])
    payload = _doctor_payload(browser="", validate_browser=False)
    assert payload["overall_status"] == "warn"


def test_payload_overall_status_ok_when_all_ok(monkeypatch):
    """所有 check 为 ok 时 overall_status 为 ok。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": True, "has_cookie": True, "needs_refresh": False},
                      browser_profiles=[
                          {"browser": "chrome", "profile": "Default", "has_psid": True, "has_psidts": True,
                           "cookie_count": 5, "chrome_selected_profile": True,
                           "chrome_selected_profile_directory": "Default",
                           "account_available": True, "scheduled_registry_count": 0},
                      ])
    payload = _doctor_payload(browser="chrome", validate_browser=False)
    assert payload["overall_status"] == "ok"


def test_payload_does_not_leak_cookie_values(monkeypatch):
    """_doctor_payload 归一化 browser_profiles 时丢弃非白名单字段（如 cookie_value）。"""
    _patch_doctor_env(monkeypatch, cookie_status={"available": True, "has_cookie": True, "needs_refresh": False},
                      browser_profiles=[
                          {"browser": "chrome", "profile": "Default", "has_psid": True,
                           "cookie_value": "__Secure-1PSID=secret", "extra_field": "leak"},
                      ])
    payload = _doctor_payload(browser="chrome", validate_browser=False)
    text = json.dumps(payload, ensure_ascii=False)
    assert "secret" not in text.lower()
    assert "__Secure-1PSID" not in text
    assert "cookie_value" not in text
    assert "extra_field" not in text
