"""Native media submits once and recognizes only verified new media nodes."""

import asyncio
import base64
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest

from src.infrastructure import native_media_browser as browser


_PROMPT = "A short public-domain instrumental motif."
_CHAT = "c_0123456789abcdef"
_BLOB = "blob:https://gemini.google.com/00000000-0000-0000-0000-000000000000"
_REMOTE = "https://lh3.googleusercontent.com/fixture-media"
_ALERT = {"id": 1, "code": "UPSTREAM_LIMIT_EXCEEDED"}
_REFERENCE_TOKEN = f"{_BLOB}\n256x256"
_REPLACEMENT_TOKEN = "blob:https://gemini.google.com/11111111-1111-1111-1111-111111111111\n256x256"


class _Chrome:
    def __init__(self, kind, url, *, after_send=None, click_failure=False, verified_cookie=True, model_reset=False,
                 thinking_available=True, late_video_intro=False, blob_mime_type="audio/mpeg", navigation_failures=0,
                 intro_at_editor=False, reset_music=False, slow_model_menu=False, missed_mode_clicks=(),
                 slow_video_menu=False, missed_video_menu_clicks=()):
        self.kind = kind
        self.url = url
        self.after_send = after_send
        self.click_failure = click_failure
        self.verified_cookie = verified_cookie
        self.model_reset = model_reset
        self.thinking_available = thinking_available
        self.late_video_intro = late_video_intro
        self.blob_mime_type = blob_mime_type
        self.intro_acknowledged = False
        self.intro_at_editor = intro_at_editor
        self.intro_pending = False
        self.editor_clicks = 0
        self.editor_focus_scenario = "normal"
        self.editor_state = "empty"
        self.editor_has_focus = False
        self.navigation_failures = navigation_failures
        self.navigation_attempts = 0
        self.at = "about:blank"
        self.sent = False
        self.actions = []
        self.pointer_events = []
        self.video_options = []
        self.video_selected = set()
        self.pending_video_option = None
        self.pending_video_menu = None
        self.video_menu_open = None
        self.video_menu_closing = False
        self.video_menu_clicks = []
        self.slow_video_menu = slow_video_menu
        self.missed_video_menu_clicks = missed_video_menu_clicks
        self.music_selected = {}
        self.pending_music_option = None
        self.reset_music = reset_music
        self.slow_model_menu = slow_model_menu
        self.missed_mode_clicks = missed_mode_clicks
        self.mode_clicks = 0
        self.model_menu_open = False
        self.model_menu_closing = False
        self.model_menu_stale = False
        self.stale_model_clicks = ()
        self.stale_model_reset_effective = True
        self.stale_model_proofs = 0
        self.stale_model_confirmation_limit = None
        self.stale_model_reset_clicks = 0
        self.model_menu_closes_during_check = ()
        self.model_check_unverified = False
        self.model_menu_close_unverified = False
        self.model_menu_closing_checks = {}
        self.thinking_requires_settled_model = False
        self.model_checked_in_open_menu = False
        self.model_settled = False
        self.thinking_hovered = False
        self.pending_thinking_level = None
        self.selected_thinking_level = None
        self.reference_state = "ready"
        self.reference_input_count = 1
        self.cdp_input_count = 1
        self.reference_paths = []
        self.reference_token = None
        self.reference_event_pending = False
        self.format_obstruction = None
        self.closed = False
        self.body = b"instrumental" * 6000

    @asynccontextmanager
    async def open(self, _):
        try:
            yield self
        finally:
            self.closed = True

    async def call(self, method, params=None, *, session=None):
        if method == "Target.createBrowserContext":
            return {"browserContextId": "test-browser"}
        if method == "Target.createTarget":
            return {"targetId": "test-target"}
        if method == "Target.attachToTarget":
            return {"sessionId": "test-session"}
        if method == "Storage.getCookies":
            return {"cookies": [{"name": "__Secure-1PSID", "value": "synthetic" if self.verified_cookie else "different"}]}
        if method == "Emulation.setDeviceMetricsOverride":
            assert session == "test-session"
            assert params == {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False}
            self.actions.append("desktop_viewport")
        if method == "Page.navigate":
            assert params["url"] == "https://gemini.google.com/app"
            assert self.actions == ["desktop_viewport"]
            self.navigation_attempts += 1
            if self.navigation_attempts <= self.navigation_failures:
                raise TimeoutError
            self.at = params["url"]
        if method == "Page.getFrameTree":
            return {"frameTree": {"frame": {"url": self.at, "id": "top"}}}
        if method == "Page.createIsolatedWorld":
            return {"executionContextId": 1}
        if method == "DOM.getDocument":
            assert session == "test-session" and params == {"depth": 0}
            self.actions.append("reference_dom")
            return {"root": {"nodeId": 8}}
        if method == "DOM.querySelectorAll":
            assert session == "test-session" and params["nodeId"] == 8
            assert 'input[type="file"][accept="image/*,video/*"]' in params["selector"]
            self.actions.append("reference_cdp_query")
            return {"nodeIds": list(range(9, 9 + self.cdp_input_count))}
        if method == "DOM.setFileInputFiles":
            assert session == "test-session" and params["nodeId"] == 9 and len(params["files"]) == 1
            self.actions.append("uploaded_reference")
            self.reference_paths.extend(params["files"])
            self.reference_token = None if self.reference_state == "not_ready" else _REFERENCE_TOKEN
            self.reference_event_pending = True
        if method == "Input.insertText":
            assert params == {"text": _PROMPT}
            assert self.editor_has_focus and self.editor_state == "empty"
            self.actions.append("inserted_prompt")
        if method == "Input.dispatchMouseEvent":
            self.pointer_events.append((params["type"], params["x"], params["y"]))
        if method == "Input.dispatchMouseEvent" and params["x"] == 200 and params["type"] == "mouseReleased":
            assert not self.sent
            self.actions.append("submit")
            self.sent = True
            if self.click_failure:
                raise OSError("transport lost after the browser click")
            self.at = "https://gemini.google.com/app/" + _CHAT[2:]
        if method == "Input.dispatchMouseEvent" and params["x"] == 100 and params["type"] == "mouseReleased":
            assert not self.intro_acknowledged
            self.intro_acknowledged = True
            self.actions.append("acknowledged_video_intro")
            if self.late_video_intro:
                assert self.video_menu_open == "aspect"
                self.video_selected.add("aspect")
        if method == "Input.dispatchMouseEvent" and params["x"] == 30 and params["type"] == "mouseReleased":
            assert self.video_menu_open == self.pending_video_option
            self.video_selected.add(self.pending_video_option)
        if method == "Input.dispatchMouseEvent" and params["x"] == 31 and params["type"] == "mouseReleased":
            key, value = self.pending_music_option
            self.music_selected[key] = value
        if method == "Input.dispatchMouseEvent" and params["x"] == 32 and params["type"] == "mouseReleased":
            self.mode_clicks += 1
            self.actions.append("mode_click")
            if self.model_menu_stale:
                self.stale_model_reset_clicks += 1
                if self.stale_model_reset_effective:
                    self.model_menu_stale = False
            elif self.mode_clicks in self.stale_model_clicks:
                self.model_menu_stale = True
            elif self.mode_clicks not in self.missed_mode_clicks:
                self.model_menu_open = not self.model_menu_open
        if method == "Input.dispatchMouseEvent" and params["x"] == 33 and params["type"] == "mouseReleased":
            assert self.model_menu_open
            self.actions.append("selected_model")
            self.model_settled = False
        if method == "Input.dispatchMouseEvent" and params["x"] == 36 and params["type"] == "mouseReleased":
            assert self.model_menu_open and self.thinking_hovered
            self.selected_thinking_level = self.pending_thinking_level
            self.actions.append("selected_thinking_level")
        if method == "Input.dispatchMouseEvent" and params["x"] == 37 and params["type"] == "mouseReleased":
            self.editor_clicks += 1
            first = self.editor_clicks == 1
            if first and self.editor_focus_scenario in {"nonempty", "ambiguous"}:
                self.editor_state = self.editor_focus_scenario
            if first and self.editor_focus_scenario == "browser_account":
                self.verified_cookie = False
            self.editor_has_focus = (self.editor_focus_scenario != "persistent"
                                     and (not first or self.editor_focus_scenario == "normal"
                                          or self.editor_state != "empty"))
        if method == "Input.dispatchMouseEvent" and params["x"] == 34 and params["type"] == "mouseReleased":
            assert self.video_menu_open is None and not self.video_menu_closing
            self.video_menu_clicks.append(self.pending_video_menu)
            self.actions.append(f"video_menu_clicked:{self.pending_video_menu}")
            if len(self.video_menu_clicks) not in self.missed_video_menu_clicks:
                self.video_menu_open = self.pending_video_menu
        if method == "Input.dispatchKeyEvent" and params["key"] == "Escape" and params["type"] == "keyDown":
            if self.reference_event_pending:
                self.reference_token = None
                self.reference_event_pending = False
                self.actions.append("upload_input_detached_before_preview")
            if self.model_menu_open:
                self.model_menu_open = False
                self.model_menu_closing = self.slow_model_menu
                self.thinking_hovered = False
            if self.video_menu_open is not None:
                self.video_menu_open = None
                self.video_menu_closing = self.slow_video_menu
        if method == "Input.dispatchMouseEvent" and params["type"] == "mouseMoved":
            if params["x"] == 10:
                self.actions.append("hover_more")
            elif params["x"] == 11:
                self.actions.append("hover_thinking")
                self.thinking_hovered = True
            elif params["x"] == 35:
                assert self.format_obstruction == "tooltip"
                self.format_obstruction = None
                self.actions.append("moved_away_from_tooltip")
        if method == "Runtime.evaluate":
            expression = params["expression"]
            if expression.startswith("location.href === "):
                return {"result": {"value": True}}
            argument = json.loads(re.search(r"\)\((\{\"action\":.*\})\)$", expression).group(1))
            assert argument["model"] == "Observed model" and argument["kind"] == self.kind
            action = argument["action"]
            self.actions.append(action)
            return {"result": {"value": self.evaluate(action, argument)}}
        return {}

    def evaluate(self, action, arg):
        if action == "send":
            assert arg["prompt"] == _PROMPT and arg["model_token"] == "Current mode is Observed model"
            if arg["reference_required"]:
                assert arg["reference_token"] == self.reference_token == _REFERENCE_TOKEN
            else:
                assert not self.reference_paths and arg["reference_token"] is None
            for key, argument in (("duration", "music_duration"), ("vocals", "music_vocals")):
                if arg.get(argument) is not None:
                    assert arg["music_tokens"][key] == f"{key}:{arg[argument]}"
            return None if self.model_reset else {"x": 200, "y": 50}
        if action == "model_checked":
            if self.model_menu_open and self.mode_clicks in self.model_menu_closes_during_check:
                self.model_menu_open = False
                self.actions.append("model_menu_closed_by_selection_callback")
            if not self.model_menu_open or self.model_check_unverified:
                return None
            if self.reference_state == "lost_after_model":
                self.reference_token = None
            if self.reference_state == "replaced_after_model":
                self.reference_token = _REPLACEMENT_TOKEN
            if self.reset_music:
                self.music_selected.clear()
            self.actions.append("verified_model")
            self.model_checked_in_open_menu = True
            return "Current mode is Observed model"
        if action == "mode":
            return {"x": 32, "y": 50}
        if action in {"model_menu_open", "pick_model"}:
            return {"x": 33, "y": 50} if self.model_menu_open else None
        if action == "model_menu_stale":
            if not self.model_menu_stale:
                return None
            self.stale_model_proofs += 1
            if (self.stale_model_confirmation_limit is not None
                    and self.stale_model_proofs > self.stale_model_confirmation_limit):
                return None
            return {"x": 32, "y": 50}
        if action == "model_menu_closed":
            if self.model_menu_stale or (self.mode_clicks >= 2 and self.model_menu_close_unverified):
                return None
            if self.model_menu_closing_checks.get(self.mode_clicks, 0):
                self.model_menu_closing_checks[self.mode_clicks] -= 1
                self.actions.append("model_menu_closing_transient")
                return None
            closed = not self.model_menu_open and not self.model_menu_closing
            self.model_menu_closing = False
            if closed and self.model_checked_in_open_menu:
                self.model_checked_in_open_menu = False
                self.model_settled = True
                self.actions.append("model_selection_settled")
            return closed
        if action == "tool_entry":
            if self.intro_pending and not self.intro_acknowledged:
                return {"intro": {"x": 100, "y": 50}}
            return {"selected": True}
        if action == "reference_input":
            return arg["reference_required"] and self.reference_input_count == 1
        if action == "reference_token":
            self.reference_event_pending = False
            return self.reference_token if arg["reference_required"] else None
        if action == "composer_menu_closed":
            return self.model_menu_open is False and self.video_menu_open is None
        if action == "editor":
            if self.intro_at_editor and not self.intro_acknowledged:
                self.intro_pending = True
                return None
            return {"x": 37, "y": 50} if self.editor_state == "empty" else None
        if action == "editor_focused":
            return self.editor_has_focus and self.editor_state == "empty"
        if action == "more":
            return {"x": 10, "y": 50}
        if action == "thinking_menu":
            return {"x": 11, "y": 50} if self.model_menu_open else None
        if action == "thinking_target":
            if (not self.thinking_available or not self.model_menu_open or not self.thinking_hovered
                    or self.thinking_requires_settled_model and not self.model_settled):
                return None
            self.pending_thinking_level = arg["thinking_level"]
            return {"x": 36, "y": 50}
        if action == "thinking_checked":
            return (self.model_menu_open and self.thinking_hovered
                    and self.selected_thinking_level == arg["thinking_level"])
        if action == "tooltip_dismiss":
            return {"x": 35, "y": 50} if self.format_obstruction == "tooltip" else None
        if action == "video_option_menu":
            if self.video_menu_open is not None or self.video_menu_closing:
                return None
            if arg["option"] == "format" and "format" in self.video_selected and self.format_obstruction:
                return None
            self.pending_video_menu = arg["option"]
            return {"x": 34, "y": 50}
        if action == "video_option_open":
            return self.video_menu_open == arg["option"]
        if action == "video_option_closed":
            closed = self.video_menu_open is None and not self.video_menu_closing
            self.video_menu_closing = False
            return closed
        if action == "video_option_checked":
            if self.video_menu_open == arg["option"] and arg["option"] in self.video_selected:
                self.video_options.append((arg["option"], arg["duration_seconds"], arg["aspect_ratio"]))
                return True
            return False
        if action == "video_option_target":
            if self.video_menu_open != arg["option"]:
                return None
            if arg["option"] == "aspect" and self.late_video_intro and not self.intro_acknowledged:
                return {"intro": {"x": 100, "y": 50}}
            self.pending_video_option = arg["option"]
            return {"x": 30, "y": 50}
        if action == "video_intro_closed":
            return self.intro_acknowledged
        if action.startswith("music_option_"):
            key = arg["option"]
            wanted = arg[{"duration": "music_duration", "vocals": "music_vocals", "genre": "music_genre"}[key]]
            if action == "music_option_target":
                self.pending_music_option = key, wanted
                return {"x": 31, "y": 50}
            if action == "music_option_checked":
                return self.music_selected.get(key) == wanted
            if action == "music_option_token":
                return f"{key}:{self.music_selected.get(key)}"
        if action in {"tool_selected", "clear_blob", "video_options", "music_options"}:
            return True
        if action == "status":
            if not self.sent:
                return {"media": [], "signals": [_ALERT]}
            if self.after_send is not None:
                return self.after_send
            return {"media": [{"kind": self.kind, "url": self.url, "ready": True,
                                "width": 1920, "height": 1080, "duration": 10.0}], "signals": [_ALERT]}
        if action == "load_blob":
            assert arg["url"] == _BLOB
            return {"size": len(self.body), "mime_type": self.blob_mime_type}
        if action == "blob_chunk":
            start = arg["offset"]
            assert arg["size"] <= 65536
            return base64.b64encode(self.body[start:start + arg["size"]]).decode()
        return {"x": 1, "y": 50}


def _submit(monkeypatch, chrome, on_chat=None, *, account_matches=lambda: True, timeout=1.0, **options):
    monkeypatch.setattr(browser, "_chrome_executable", lambda: "fixture-Chrome")
    monkeypatch.setattr(browser, "_open_browser", chrome.open)
    return asyncio.run(browser.run_native_media_browser(chrome.kind, _PROMPT, "Observed model",
        {"__Secure-1PSID": "synthetic"}, timeout=timeout, on_chat=on_chat, account_matches=account_matches, **options))


@pytest.mark.parametrize("failures", [1, 2])
def test_empty_composer_navigation_retries_once_before_any_submission(monkeypatch, failures):
    fixture = _Chrome("music", _BLOB, navigation_failures=failures)
    result = _submit(monkeypatch, fixture)
    assert fixture.navigation_attempts == 2 and fixture.closed
    assert result.submitted is (failures == 1)
    assert fixture.actions.count("submit") == (1 if failures == 1 else 0)


def test_exact_video_intro_arriving_at_editor_is_acknowledged_before_input(monkeypatch):
    fixture = _Chrome("video", _REMOTE, intro_at_editor=True)
    result = _submit(monkeypatch, fixture)
    assert result.state == "ready" and fixture.actions.count("submit") == 1
    assert fixture.actions.count("acknowledged_video_intro") == 1
    assert fixture.actions.index("acknowledged_video_intro") < fixture.actions.index("inserted_prompt")


def test_native_reclicks_the_verified_empty_editor_once_after_missing_focus(monkeypatch):
    fixture = _Chrome("music", _BLOB)
    fixture.editor_focus_scenario = "missed_first"
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(browser, "_EDITOR_FOCUS_TIMEOUT", 0.02)

    result = _submit(monkeypatch, fixture, timeout=0.2)

    assert result.state == "ready" and result.submitted
    assert fixture.editor_clicks == 2
    assert fixture.actions.count("inserted_prompt") == fixture.actions.count("submit") == 1


@pytest.mark.parametrize("scenario,clicks", [
    ("persistent", 2), ("nonempty", 1), ("ambiguous", 1), ("account", 1), ("browser_account", 1),
])
def test_native_never_inserts_after_unverified_empty_editor_focus(monkeypatch, scenario, clicks):
    fixture = _Chrome("music", _BLOB)
    fixture.editor_focus_scenario = scenario
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(browser, "_EDITOR_FOCUS_TIMEOUT", 0.02)

    def account_matches():
        return scenario != "account" or fixture.editor_clicks == 0

    result = _submit(monkeypatch, fixture, account_matches=account_matches, timeout=0.2)

    assert result.state == "unverified" and result.stage == "edit_prompt" and not result.submitted
    assert fixture.editor_clicks == clicks and fixture.closed
    assert "inserted_prompt" not in fixture.actions and "submit" not in fixture.actions


@pytest.mark.parametrize("duration", ["short", "standard"])
@pytest.mark.parametrize("vocals", ["instrumental", "vocals", "auto"])
def test_native_music_options_are_selected_and_reverified_after_model(monkeypatch, duration, vocals):
    fixture = _Chrome("music", _BLOB)
    result = _submit(monkeypatch, fixture, music_duration=duration, music_vocals=vocals)
    assert result.state == "ready" and fixture.actions.count("submit") == 1
    assert fixture.music_selected == {"duration": duration, "vocals": vocals}
    assert fixture.actions.index("music_option_target") < fixture.actions.index("model_checked")
    assert fixture.actions.index("model_checked") < len(fixture.actions) - 1 - fixture.actions[::-1].index("music_option_checked")


def test_model_resetting_explicit_music_options_prevents_submission(monkeypatch):
    fixture = _Chrome("music", _BLOB, reset_music=True)
    result = _submit(monkeypatch, fixture, timeout=0.1, music_duration="short", music_vocals="instrumental")
    assert not result.submitted and result.state == "unverified" and fixture.actions.count("submit") == 0


def test_native_model_menu_must_close_before_reopening_and_before_dispatch(monkeypatch):
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0)

    fixture = _Chrome("music", _BLOB, slow_model_menu=True)
    result = _submit(monkeypatch, fixture)
    assert result.state == "ready"
    picked = fixture.actions.index("selected_model")
    reopened = fixture.actions.index("mode_click", picked)
    checked, sent = fixture.actions.index("model_checked"), fixture.actions.index("send")
    assert fixture.actions[picked:reopened].count("model_menu_closed") >= 2
    assert fixture.actions[checked:sent].count("model_menu_closed") >= 2


def test_missed_model_menu_reopen_is_retried_without_resending(monkeypatch):
    monkeypatch.setattr(browser, "_MODEL_MENU_OPEN_TIMEOUT", 0.01)
    fixture = _Chrome("video", _REMOTE, missed_mode_clicks=(2,))
    result = _submit(monkeypatch, fixture)
    assert result.state == "ready" and result.submitted
    assert fixture.mode_clicks == 3 and fixture.actions.count("selected_model") == 1
    assert fixture.actions.count("model_checked") == fixture.actions.count("submit") == 1


@pytest.mark.parametrize("closure_proven", [True, False])
def test_missed_model_menu_reopen_waits_for_closure_evidence(monkeypatch, closure_proven):
    monkeypatch.setattr(browser, "_UI_TIMEOUT", 0.04)
    monkeypatch.setattr(browser, "_MODEL_MENU_OPEN_TIMEOUT", 0.01)
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.001)
    fixture = _Chrome("music", _BLOB, missed_mode_clicks=(2,))
    if closure_proven:
        fixture.model_menu_closing_checks = {2: 2}
    else:
        fixture.model_menu_close_unverified = True

    result = _submit(monkeypatch, fixture, timeout=0.2)

    assert result.submitted is closure_proven and fixture.closed
    assert fixture.mode_clicks == (3 if closure_proven else 2)
    assert fixture.actions.count("selected_model") == 1
    assert fixture.actions.count("submit") == int(closure_proven)
    if closure_proven:
        assert fixture.actions.count("model_menu_closing_transient") == 2
    else:
        assert result.stage == "model_selection" and not result.selected_model_verified


@pytest.mark.parametrize("scenario,reset_clicks,submitted", [
    ("stale_after_open", 1, True),
    ("reset_ineffective", 1, False),
    ("second_check_lost", 0, False),
    ("initial_stale", 0, False),
])
def test_model_menu_resets_only_a_reconfirmed_stale_trigger_after_open_timeout(
    monkeypatch, scenario, reset_clicks, submitted,
):
    monkeypatch.setattr(browser, "_UI_TIMEOUT", 0.06)
    monkeypatch.setattr(browser, "_MODEL_MENU_OPEN_TIMEOUT", 0.01)
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.001)
    fixture = _Chrome("music", _BLOB)
    fixture.stale_model_clicks = (2,)
    if scenario == "reset_ineffective":
        fixture.stale_model_reset_effective = False
    if scenario == "second_check_lost":
        fixture.stale_model_confirmation_limit = 1
    if scenario == "initial_stale":
        fixture.model_menu_stale = True

    observed = _submit(monkeypatch, fixture, timeout=0.3)

    assert observed.submitted is submitted and fixture.closed
    assert fixture.stale_model_reset_clicks == reset_clicks
    assert fixture.mode_clicks == (0 if scenario == "initial_stale" else 2 + reset_clicks + int(submitted))
    assert fixture.actions.count("submit") == int(submitted)
    assert fixture.actions.count("selected_model") == (0 if scenario == "initial_stale" else 1)
    if submitted:
        assert fixture.stale_model_proofs == 2
        assert observed.selected_model_verified


def test_high_thinking_readback_recovers_stale_trigger_without_reselecting_model_or_level(monkeypatch):
    monkeypatch.setattr(browser, "_UI_TIMEOUT", 0.06)
    monkeypatch.setattr(browser, "_MODEL_MENU_OPEN_TIMEOUT", 0.01)
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.001)
    fixture = _Chrome("music", _BLOB)
    fixture.stale_model_clicks = (4,)  # First reopening after High was selected.

    observed = _submit(monkeypatch, fixture, thinking_level="high")

    assert observed.state == "ready" and observed.submitted and observed.selected_model_verified
    assert fixture.stale_model_reset_clicks == 1 and fixture.mode_clicks == 7
    assert fixture.actions.count("selected_model") == fixture.actions.count("selected_thinking_level") == 1
    assert fixture.actions.index("selected_thinking_level") < fixture.actions.index("thinking_checked")
    assert fixture.actions.index("thinking_checked") < fixture.actions.index("submit")


def test_model_menu_staying_closed_stops_after_two_attempts_without_sending(monkeypatch):
    monkeypatch.setattr(browser, "_MODEL_MENU_OPEN_TIMEOUT", 0.01)
    fixture = _Chrome("video", _REMOTE, missed_mode_clicks=(1, 2))
    result = _submit(monkeypatch, fixture)
    assert result.state == "unverified" and not result.submitted and fixture.closed
    assert fixture.mode_clicks == 2
    assert not {"selected_model", "send", "submit"}.intersection(fixture.actions)


def test_model_menu_that_closes_after_opening_is_reopened_only_to_verify(monkeypatch):
    monkeypatch.setattr(browser, "_MODEL_MENU_OPEN_TIMEOUT", 0.01)
    fixture = _Chrome("video", _REMOTE)
    fixture.model_menu_closes_during_check = (2,)
    result = _submit(monkeypatch, fixture)
    assert result.state == "ready" and result.selected_model_verified and result.submitted
    assert fixture.mode_clicks == 3
    assert fixture.actions.count("selected_model") == fixture.actions.count("verified_model") == 1
    assert fixture.actions.count("submit") == 1
    collapsed = fixture.actions.index("model_menu_closed_by_selection_callback")
    assert collapsed < fixture.actions.index("mode_click", collapsed) < fixture.actions.index("verified_model")


@pytest.mark.parametrize("state", ["closed_twice", "unknown", "open_without_checked_model"])
def test_unverified_model_readback_never_reselects_or_sends(monkeypatch, state):
    monkeypatch.setattr(browser, "_UI_TIMEOUT", 0.08)
    monkeypatch.setattr(browser, "_MODEL_MENU_OPEN_TIMEOUT", 0.01)
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.002)
    fixture = _Chrome("video", _REMOTE)
    if state == "closed_twice":
        fixture.model_menu_closes_during_check = (2, 3)
    elif state == "unknown":
        fixture.model_menu_closes_during_check = (2,)
        fixture.model_menu_close_unverified = True
    else:
        fixture.model_check_unverified = True
    result = _submit(monkeypatch, fixture)
    assert result.state == "unverified" and result.stage == "model_selection"
    assert not result.selected_model_verified and not result.submitted and fixture.closed
    assert fixture.mode_clicks == (3 if state == "closed_twice" else 2)
    assert fixture.actions.count("selected_model") == 1
    assert not {"verified_model", "send", "submit"}.intersection(fixture.actions)


@pytest.mark.parametrize("kind, url, mime_type", [
    ("music", _BLOB, "audio/mpeg"),
    ("music", _BLOB, "video/mp4"),
    ("video", _REMOTE, "video/mp4"),
])
def test_native_media_verifies_model_after_input_and_observes_new_exact_chat(monkeypatch, kind, url, mime_type):
    fixture = _Chrome(kind, url, blob_mime_type=mime_type)
    on_chat = AsyncMock()
    result = _submit(monkeypatch, fixture, on_chat)
    assert result.state == "ready" and result.submitted and result.selected_model_verified and result.chat_id == _CHAT
    assert result.media[0]["url"] == url and result.media[0]["duration"] == 10.0
    assert result.media[0]["kind"] == kind
    on_chat.assert_awaited_once_with(_CHAT)
    assert fixture.closed and fixture.actions.count("submit") == 1
    assert fixture.actions.index("inserted_prompt") < fixture.actions.index("selected_model") < fixture.actions.index("model_checked")
    assert fixture.actions.index("model_checked") < fixture.actions.index("send") < fixture.actions.index("submit")
    assert ("more" in fixture.actions) is False
    assert ("hover_more" in fixture.actions) is False
    assert ("music_options" in fixture.actions) == (kind == "music")
    assert "thinking_target" not in fixture.actions
    if kind == "music":
        assert result.media[0]["content"] == fixture.body and result.media[0]["mime_type"] == mime_type
        assert result.media[0]["width"] == 1920 and result.media[0]["height"] == 1080
        assert fixture.actions.count("blob_chunk") == 2 and fixture.actions[-1] == "clear_blob"
    else:
        assert result.media[0]["content"] is None


def test_changed_browser_cookie_fails_before_submission(monkeypatch):
    fixture = _Chrome("video", _REMOTE, verified_cookie=False)
    result = _submit(monkeypatch, fixture)
    assert result.state == "unverified" and not result.submitted and result.error_code == "CAPABILITY_UNAVAILABLE"
    assert fixture.closed and "submit" not in fixture.actions


@pytest.fixture
def reference_image(tmp_path):
    path = tmp_path / "reference.png"
    path.write_bytes(b"local reference fixture")
    return path


def test_native_video_uploads_one_reference_and_reverifies_it_before_one_submit(monkeypatch, reference_image):
    fixture = _Chrome("video", _REMOTE)
    result = _submit(monkeypatch, fixture, reference_image_path=str(reference_image))
    assert result.state == "ready" and result.submitted
    assert fixture.reference_paths == [str(reference_image)]
    assert fixture.actions.count("uploaded_reference") == fixture.actions.count("submit") == 1
    assert "upload_input_detached_before_preview" not in fixture.actions
    upload = fixture.actions.index("uploaded_reference")
    model = fixture.actions.index("model_checked")
    send = fixture.actions.index("send")
    assert upload < model < send
    assert "reference_token" in fixture.actions[upload:model]
    assert "reference_token" in fixture.actions[model:send]


def test_browser_account_mismatch_blocks_the_reference_upload(monkeypatch, reference_image):
    fixture = _Chrome("video", _REMOTE, verified_cookie=False)
    result = _submit(monkeypatch, fixture, reference_image_path=str(reference_image))
    assert result.stage == "video_reference" and result.error_code == "CAPABILITY_UNAVAILABLE"
    assert not result.submitted and not fixture.reference_paths and "send" not in fixture.actions


@pytest.mark.parametrize("stage", ["dom", "cdp"])
def test_duplicate_video_reference_inputs_block_the_upload_and_submit(monkeypatch, reference_image, stage):
    fixture = _Chrome("video", _REMOTE)
    if stage == "dom":
        fixture.reference_input_count = 2
    else:
        fixture.cdp_input_count = 2
    result = _submit(monkeypatch, fixture, timeout=0.1, reference_image_path=str(reference_image))
    assert result.stage == "video_reference" and not result.submitted
    assert not fixture.reference_paths and "send" not in fixture.actions
    assert ("reference_cdp_query" in fixture.actions) is (stage == "cdp")


@pytest.mark.parametrize("reference_state,stage", [
    ("not_ready", "video_reference"),
    ("lost_after_model", "verify_composer"),
    ("replaced_after_model", "verify_composer"),
])
def test_video_reference_must_remain_the_uploaded_ready_attachment(monkeypatch, reference_image, reference_state, stage):
    fixture = _Chrome("video", _REMOTE)
    fixture.reference_state = reference_state
    result = _submit(monkeypatch, fixture, timeout=0.1, reference_image_path=str(reference_image))
    assert result.stage == stage and not result.submitted
    assert fixture.reference_paths == [str(reference_image)]
    assert fixture.actions.count("uploaded_reference") == 1
    assert "send" not in fixture.actions and fixture.closed


def test_changed_model_after_verified_selection_never_submits(monkeypatch):
    fixture = _Chrome("video", _REMOTE, model_reset=True)
    result = _submit(monkeypatch, fixture, timeout=0.1)
    assert result.state == "unverified" and result.selected_model_verified and not result.submitted
    assert fixture.closed and "send" in fixture.actions and "submit" not in fixture.actions


def test_requested_video_options_and_thinking_are_verified_before_send(monkeypatch):
    fixture = _Chrome("video", _REMOTE)
    result = _submit(monkeypatch, fixture, duration_seconds=15, aspect_ratio="9:16", thinking_level="low")
    assert result.state == "ready" and result.submitted
    assert fixture.video_options == [(option, 15, "9:16") for option in ("duration", "aspect", "format", "format")]
    checks = [index for index, action in enumerate(fixture.actions) if action == "model_checked"]
    assert checks[0] < fixture.actions.index("thinking_checked") < checks[-1] < fixture.actions.index("send")
    assert fixture.actions.count("hover_thinking") == 2


def test_high_thinking_waits_for_verified_model_to_settle_before_selecting(monkeypatch):
    fixture = _Chrome("music", _BLOB, slow_model_menu=True)
    fixture.thinking_requires_settled_model = True
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(browser, "_UI_TIMEOUT", 0.03)

    result = _submit(monkeypatch, fixture, thinking_level="high", music_duration="short", music_vocals="instrumental")

    assert result.state == "ready" and result.submitted and result.selected_model_verified
    settled = fixture.actions.index("model_selection_settled")
    chosen = fixture.actions.index("selected_thinking_level")
    checked = fixture.actions.index("thinking_checked")
    last_model = max(index for index, action in enumerate(fixture.actions) if action == "verified_model")
    assert settled < fixture.actions.index("hover_thinking") < chosen < checked < last_model < fixture.actions.index("send")
    assert fixture.selected_thinking_level == "high"
    assert fixture.actions.count("selected_model") == fixture.actions.count("submit") == 1


def test_explicit_thinking_does_not_open_its_submenu_before_model_can_be_verified(monkeypatch):
    fixture = _Chrome("music", _BLOB)
    fixture.model_check_unverified = True
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.001)
    monkeypatch.setattr(browser, "_UI_TIMEOUT", 0.03)

    result = _submit(monkeypatch, fixture, thinking_level="high")

    assert result.state == "unverified" and result.stage == "thinking_level" and not result.submitted
    assert "hover_thinking" not in fixture.actions and "submit" not in fixture.actions and fixture.closed


def test_native_click_moves_pointer_before_pressing_and_releasing(monkeypatch):
    fixture = _Chrome("music", _BLOB)
    result = _submit(monkeypatch, fixture)
    assert result.submitted and fixture.actions.count("submit") == 1
    for index, (event, x, y) in enumerate(fixture.pointer_events):
        if event == "mousePressed":
            assert fixture.pointer_events[index - 1:index + 2] == [
                ("mouseMoved", x, y), ("mousePressed", x, y), ("mouseReleased", x, y),
            ]


@pytest.mark.parametrize("obstruction", ["tooltip", "dialog"])
def test_only_a_known_tooltip_can_be_cleared_with_a_pointer_move(monkeypatch, reference_image, obstruction):
    fixture = _Chrome("video", _REMOTE)
    fixture.format_obstruction = obstruction
    # 不能设成 0：那会让轮询变成纯忙等，asyncio 的定时器推进拿不到机会，
    # asyncio.timeout 的取消无法生效，等待超时的路径会一直挂着（obstruction=dialog
    # 那个变体在 0 的时候会挂死 60 秒以上）。同文件其它用例都用 0.001。
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0.001)


    result = _submit(monkeypatch, fixture, timeout=0.15, reference_image_path=str(reference_image))
    assert fixture.reference_paths == [str(reference_image)]
    assert "tooltip_dismiss" in fixture.actions
    dismiss_events = [event for event, x, _ in fixture.pointer_events if x == 35]
    if obstruction == "tooltip":
        assert result.submitted and result.state == "ready" and fixture.actions.count("submit") == 1
        assert dismiss_events == ["mouseMoved"]
        assert fixture.actions.index("moved_away_from_tooltip") < fixture.actions.index("submit")
    else:
        assert not result.submitted and result.stage == "video_format"
        assert not dismiss_events and "send" not in fixture.actions


def test_video_intro_keeps_the_open_aspect_menu_and_waits_for_close_before_reopening(monkeypatch):
    monkeypatch.setattr(browser, "_POLL_INTERVAL", 0)

    fixture = _Chrome("video", _REMOTE, late_video_intro=True, slow_video_menu=True)
    result = _submit(monkeypatch, fixture)
    assert result.state == "ready" and fixture.actions.count("submit") == 1
    intro_index = fixture.actions.index("acknowledged_video_intro")
    next_aspect_click = fixture.actions.index("video_menu_clicked:aspect", intro_index)
    continued = fixture.actions[intro_index + 1:next_aspect_click]
    assert fixture.actions[intro_index - 1] == "video_option_target"
    assert continued.index("video_intro_closed") < continued.index("video_option_open") < continued.index("video_option_checked")
    assert continued.count("video_option_closed") >= 2
    assert fixture.video_menu_clicks.count("aspect") == 2


def test_missed_video_option_menu_click_retries_only_the_closed_menu(monkeypatch):
    monkeypatch.setattr(browser, "_OPTION_MENU_OPEN_TIMEOUT", 0.01)
    fixture = _Chrome("video", _REMOTE, missed_video_menu_clicks=(1,))
    result = _submit(monkeypatch, fixture)
    assert result.state == "ready" and fixture.actions.count("submit") == 1
    assert fixture.video_menu_clicks[:3] == ["duration", "duration", "duration"]


def test_explicit_thinking_level_without_native_control_is_not_dispatched(monkeypatch):
    fixture = _Chrome("video", _REMOTE, thinking_available=False)
    monkeypatch.setattr(browser, "_UI_TIMEOUT", 0.02)
    result = _submit(monkeypatch, fixture, thinking_level="extended")
    assert result.state == "unverified" and result.error_code == "CAPABILITY_UNAVAILABLE"
    assert result.stage == "thinking_level" and not result.submitted
    assert fixture.closed and "submit" not in fixture.actions


def test_failed_click_preserves_unknown_submission_and_does_not_retry(monkeypatch):
    fixture = _Chrome("video", _REMOTE, click_failure=True)
    on_chat = AsyncMock()
    result = _submit(monkeypatch, fixture, on_chat)
    assert result.state == "unverified" and result.submitted and result.chat_id is None
    assert fixture.closed and fixture.actions.count("submit") == 1
    on_chat.assert_not_awaited()


def test_new_explicit_quota_signal_is_verified_after_submission(monkeypatch):
    fixture = _Chrome("video", _REMOTE, after_send={"media": [], "signals": [_ALERT, {**_ALERT, "id": 2}]})
    result = _submit(monkeypatch, fixture)
    assert result.state == "rejected" and result.error_code == "UPSTREAM_LIMIT_EXCEEDED" and result.submitted
    assert not result.media and result.chat_id == _CHAT


def test_new_generic_generation_error_rejects_without_sending_again(monkeypatch):
    fixture = _Chrome("video", _REMOTE, after_send={"media": [], "signals": [{"id": 2, "code": "UPSTREAM_REJECTED"}]})
    result = _submit(monkeypatch, fixture)
    assert result.state == "rejected" and result.error_code == "UPSTREAM_REJECTED" and result.submitted
    assert result.stage == "observe" and result.chat_id == _CHAT and not result.media
    assert fixture.actions.count("submit") == 1 and fixture.closed


def test_new_exact_music_refusal_does_not_wait_for_timeout(monkeypatch):
    fixture = _Chrome("music", _BLOB, after_send={"media": [], "signals": [{"id": 2, "code": "ARTIFACT_NOT_RETURNED"}]})
    result = _submit(monkeypatch, fixture)
    assert result.state == "rejected" and result.error_code == "ARTIFACT_NOT_RETURNED" and result.submitted
    assert result.stage == "observe" and result.chat_id == _CHAT


def test_untrusted_media_and_preexisting_quota_banner_are_not_a_result(monkeypatch):
    fixture = _Chrome("video", "https://lh3.googleusercontent.com.invalid/video")
    result = _submit(monkeypatch, fixture, timeout=0.1)
    assert result.state == "unverified" and result.error_code == "TIMED_OUT" and result.submitted
    assert not result.media and fixture.closed


_SCRIPT = Path(__file__).parents[1] / "src" / "data" / "native_media_browser.js"
_NODE = shutil.which("node")
_DOM_FIXTURE = r"""
const fs = require('node:fs');
const vm = require('node:vm');
function element({text = '', label, checked = true, disabled = false, editor = false, question = false,
    response = false, reactResponse = false,
    responseAncestor = false, musicOutput = false, quoted = false, textNodes, selectors = {}} = {}) {
    return {
        innerText: text,
        classList: {contains: () => editor},
        ariaLabel: label,
        textNodes,
        getAttribute(name) {
            if (name === 'aria-checked') return String(checked);
            if (name === 'aria-expanded') return this.ariaExpanded;
            if (name === 'aria-controls') return this.ariaControls;
            return this.ariaLabel;
        },
        closest(selector) {
            if (selector === 'blockquote, pre, code, q' && quoted) return this;
            if (selector.includes('[data-test-id="user-query"]') && question) return {};
            if (selector.includes('[data-test-id="model-response"]') && responseAncestor) return {};
            if (selector === '[data-test-id="music-generation-output"]' && musicOutput) return this;
            return selector === '[role="menu"]' ? this.menu : null;
        },
        getClientRects: () => [{}], getBoundingClientRect: () => ({left: 25, top: 80, width: 30, height: 20}),
        matches: selector => (selector.startsWith('model-response') && response)
            || (selector.includes('[data-model-response-turn-id]') && reactResponse)
            || (selector.startsWith(':disabled') && disabled),
        hasAttribute(name) { return name === 'aria-expanded' && this.ariaExpanded !== undefined; },
        querySelector: selector => selectors[selector]?.[0] || null,
        querySelectorAll: selector => selectors[selector] || [],
        contains(el) { return el === this; }, scrollIntoView: () => {},
    };
}
const switcher = element({label: '打开模式选择器，当前模式为“Hydrogen”'});
const selected = element({label: '已选中 日常帮助 4 Hydrogen'});
const input = element({text: 'A blue paper cube', editor: true});
const send = element({label: '发送'});
const chip = element({label: 'close 视频'});
const duration = element({label: '时长，15 秒'});
const aspect = element({label: '宽高比，纵向 (9:16)'});
const format = element({label: '格式，视频', text: '视频'});
const quota = "I couldn't do that because I'm getting a lot of requests right now. Please try again later.";
const prior = element({text: quota});
const userPrompt = element({text: quota, question: true});
let responses = [prior, userPrompt];
let dialogs = [];
let menus = [];
let tooltips = [];
let players = [];
let control = send;
let modelRadios = [selected];
let fileInputs = [];
let editableInputs = [input];
let globalReferenceCards = [];
const elementsById = new Map();
let buttons = [switcher, chip, send, duration, aspect, format];
const document = {
    getElementById: id => elementsById.get(id) || null,
    createTreeWalker(root) {
        const nodes = root.textNodes || [{nodeValue: root.innerText, parentElement: root}];
        let index = 0;
        return {nextNode: () => nodes[index++] || null};
    },
    querySelectorAll(selector) {
        if (selector.startsWith('button')) return buttons;
        if (selector === '[role="menuitemradio"]') return modelRadios;
        if (selector === '[role="menu"]') return menus;
        if (selector === '[role="tooltip"], .mat-mdc-tooltip-surface') return tooltips;
        if (selector === 'audio, video' || selector === 'audio,video') return players;
        if (selector === 'input[type="file"][accept="image/*,video/*"]') return fileInputs;
        if (selector === 'uploader-file-preview') return globalReferenceCards;
        if (selector.startsWith('textarea')) return editableInputs;
        if (selector.startsWith('model-response')) return responses;
        if (selector.startsWith('dialog')) return dialogs;
        return [];
    },
    elementFromPoint: () => control,
};
const location = {origin: 'https://gemini.google.com', pathname: '/app'};
const window = {};
window.top = window;
const fetch = async () => new Response(new Uint8Array([11, 22, 33]), {headers: {'Content-Type': 'video/mp4'}});
const context = vm.createContext({location, window, document, Blob, btoa, fetch, HTMLTextAreaElement: class {},
    getComputedStyle: () => ({visibility: 'visible', opacity: '1'})});
const run = vm.runInContext(`(${fs.readFileSync(process.argv[1], 'utf8')})`, context);
const call = (action, options) => run({action, kind: 'video', model: '日常帮助', duration_seconds: 15, aspect_ratio: '9:16', ...options});
const musicMenu = (labels, selected) => {
    const radios = labels.map(label => element({label, checked: label === selected}));
    const menu = element({selectors: {'[role="menuitemradio"]': radios}});
    radios.forEach(radio => radio.menu = menu);
    return menu;
};
(async () => {
    const introButton = element({label: '立即体验'});
    control = introButton;
    dialogs = [element({label: '创作视频', text: 'Gemini Omni 加持', selectors: {button: [introButton]}})];
    const acceptedIntro = await call('tool_entry');
    const lateIntro = await call('video_option_target', {option: 'duration'});
    const optionIntro = await call('video_option_open', {option: 'duration'});
    dialogs[0].ariaLabel = '应用授权';
    const unknownIntro = await call('tool_entry');
    const obscuredOption = await call('video_option_target', {option: 'duration'});
    const unknownMenuState = await call('video_option_closed', {option: 'duration'});
    dialogs = [];
    const short = element({label: '10 秒'});
    const long = element({label: '15 秒'});
    const menu = element({selectors: {'[role="menuitemradio"]': [short, long]}});
    short.menu = menu;
    long.menu = menu;
    const parent = element({selectors: {'[role="menuitemradio"]': [short, long]}});
    menus = [parent, menu];
    control = long;
    const nestedMenu = await call('video_option_target', {option: 'duration'});
    const checkedOption = await call('video_option_checked', {option: 'duration'});
    duration.ariaExpanded = 'true';
    const nestedMenuOpen = await call('video_option_open', {option: 'duration'});
    const unavailableLong = element({label: '15 秒', disabled: true});
    const disabledMenu = element({selectors: {'[role="menuitemradio"]': [short, unavailableLong]}});
    short.menu = disabledMenu;
    unavailableLong.menu = disabledMenu;
    menus = [disabledMenu];
    control = unavailableLong;
    const disabledMenuOpen = await call('video_option_open', {option: 'duration'});
    const disabledOptionChecked = await call('video_option_checked', {option: 'duration'});
    const disabledOptionTarget = await call('video_option_target', {option: 'duration'});
    short.menu = menu;
    const otherShort = element({label: '10 秒'});
    const otherLong = element({label: '15 秒'});
    const otherMenu = element({selectors: {'[role="menuitemradio"]': [otherShort, otherLong]}});
    otherShort.menu = otherMenu;
    otherLong.menu = otherMenu;
    menus = [parent, menu, otherMenu];
    const ambiguousMenu = await call('video_option_target', {option: 'duration'});
    const ambiguousMenuOpen = await call('video_option_open', {option: 'duration'});
    duration.ariaExpanded = 'false';
    const closingVideoMenu = await call('video_option_closed', {option: 'duration'});
    menus = [];
    const closedVideoMenu = await call('video_option_closed', {option: 'duration'});
    const expandableVideo = element({label: '视频'});
    expandableVideo.ariaExpanded = 'false';
    const explicitFormatDuplicate = element({label: 'Format: GIF'});
    explicitFormatDuplicate.ariaExpanded = 'false';
    const composerButtons = [switcher, chip, send, duration, aspect];
    buttons = [...composerButtons, format, expandableVideo];
    control = format;
    const explicitFormatTarget = await call('video_option_menu', {option: 'format'});
    const videoOnlyFormat = element({text: 'Video'});
    videoOnlyFormat.ariaExpanded = 'false';
    buttons = [...composerButtons, videoOnlyFormat];
    control = videoOnlyFormat;
    const unlabeledFormatTarget = await call('video_option_menu', {option: 'format'});
    buttons = [...composerButtons, format, expandableVideo, explicitFormatDuplicate];
    control = format;
    const ambiguousFormatTarget = await call('video_option_menu', {option: 'format'});
    buttons = [...composerButtons, format];
    control = input;
    const unrelatedOverlay = await call('tooltip_dismiss');
    const angularTooltip = element();
    tooltips = [angularTooltip];
    const tooltipMoveTarget = await call('tooltip_dismiss');
    angularTooltip.getAttribute = key => key === 'aria-hidden' ? 'true' : null;
    angularTooltip.closest = selector => selector.includes('[aria-hidden="true"]') ? angularTooltip : null;
    const physicallyObstructingTooltip = await call('tooltip_dismiss');
    const tools = element({label: '上传和工具'});
    tools.getBoundingClientRect = () => ({left: 80, top: 100, width: 30, height: 20});
    buttons.push(tools);
    control = tools;
    const toolsTooltipMoveTarget = await call('tooltip_dismiss');
    menus = [menu];
    const tooltipWithMenu = await call('tooltip_dismiss');
    menus = [];
    dialogs = [element({label: '应用授权'})];
    const tooltipWithDialog = await call('tooltip_dismiss');
    dialogs = [];
    control = angularTooltip;
    const obscuredTooltipMoveTarget = await call('tooltip_dismiss');
    buttons.pop();
    tooltips = [];
    control = send;
    const token = await call('model_checked');
    const technicalToken = await call('model_checked', {model: '4 Hydrogen'});
    switcher.ariaExpanded = 'true';
    control = selected;
    const modelMenuOpenTarget = await call('model_menu_open');
    const disabledModel = element({label: selected.ariaLabel, disabled: true});
    modelRadios = [selected, disabledModel];
    const duplicateModelMenuTarget = await call('model_menu_open');
    modelRadios = [disabledModel];
    control = disabledModel;
    const disabledModelMenuTarget = await call('model_menu_open');
    modelRadios = [selected];
    const modelMenuOpen = await call('model_menu_closed');
    switcher.ariaExpanded = 'false';
    const modelMenuClosing = await call('model_menu_closed');
    control = selected;
    const closedModelMenuTarget = await call('model_menu_open');
    selected.getClientRects = () => [];
    const modelMenuClosed = await call('model_menu_closed');
    const modelMenuStale = {};
    switcher.ariaExpanded = 'true';
    control = switcher;
    modelMenuStale.eligible = await call('model_menu_stale');
    menus = [menu];
    modelMenuStale.visibleMenu = await call('model_menu_stale');
    menus = [];
    dialogs = [element({label: '应用授权'})];
    modelMenuStale.dialog = await call('model_menu_stale');
    dialogs = [];
    buttons.push(element({label: 'Open mode selector', disabled: true}));
    modelMenuStale.duplicateTrigger = await call('model_menu_stale');
    buttons.pop();
    switcher.ariaControls = 'known-model-panel';
    const modelPanel = element();
    elementsById.set('known-model-panel', modelPanel);
    modelMenuStale.visibleControlledPanel = await call('model_menu_stale');
    modelPanel.getClientRects = () => [];
    modelMenuStale.hiddenControlledPanel = await call('model_menu_stale');
    delete switcher.ariaControls;
    modelRadios = [element({label: selected.ariaLabel})];
    modelMenuStale.visibleRequestedRadio = await call('model_menu_stale');
    modelRadios = [selected];
    control = send;
    modelMenuStale.obscuredTrigger = await call('model_menu_stale');
    control = switcher;
    switcher.ariaExpanded = 'false';
    modelMenuStale.closedTrigger = await call('model_menu_stale');
    control = send;
    const before = await call('send', {model_token: token, prompt: input.innerText});
    const referenceArgs = {reference_required: true, model_token: token, prompt: input.innerText};
    const originalSrc = 'blob:https://gemini.google.com/00000000-0000-0000-0000-000000000000';
    const thumbnail = Object.assign(element(), {currentSrc: originalSrc, complete: true, naturalWidth: 256, naturalHeight: 256});
    const controls = {
        'gem-media-attachment img': [thumbnail],
        '.gem-attachment-close-button button': [element({label: '关闭附件'})],
    };
    const preview = element({selectors: controls});
    const progressSelector = '[role="progressbar"], [aria-busy="true"]';
    const composerContents = {'uploader-file-preview': [preview], [progressSelector]: []};
    const composer = element({selectors: composerContents});
    const originalClosest = input.closest.bind(input);
    input.closest = selector => selector === '.text-input-field' ? composer : originalClosest(selector);
    globalReferenceCards = [preview];
    fileInputs = [{disabled: false}];
    const exactFileInput = await call('reference_input', referenceArgs);
    const referenceToken = await call('reference_token', referenceArgs);
    const sendVideoWithReference = () => call('send', {...referenceArgs, reference_token: referenceToken});
    const verifiedVideoReference = await sendVideoWithReference();
    const unexpectedReference = await call('send', {model_token: token, prompt: input.innerText});
    fileInputs = [{disabled: false}, {disabled: false}];
    const duplicateFileInput = await call('reference_input', referenceArgs);
    fileInputs = [{disabled: false}];
    dialogs = [element({label: 'Other dialog'})];
    const obscuredFileInput = await call('reference_input', referenceArgs);
    const obscuredReference = await call('reference_token', referenceArgs);
    dialogs = [];
    thumbnail.currentSrc = 'blob:https://gemini.google.com/11111111-1111-1111-1111-111111111111';
    const replacedReference = await sendVideoWithReference();
    thumbnail.currentSrc = originalSrc;
    thumbnail.complete = false;
    const unloadedReference = await sendVideoWithReference();
    thumbnail.complete = true;
    composerContents[progressSelector] = [element()];
    const uploadingReference = await sendVideoWithReference();
    composerContents[progressSelector] = [];
    composerContents['uploader-file-preview'] = [preview, element({selectors: controls})];
    const ambiguousReferences = await sendVideoWithReference();
    composerContents['uploader-file-preview'] = [];
    const outsideReference = await sendVideoWithReference();
    composerContents['uploader-file-preview'] = [preview];
    controls['.gem-attachment-close-button button'] = [element({disabled: true})];
    const incompleteReference = await sendVideoWithReference();
    controls['.gem-attachment-close-button button'] = [element({label: '关闭附件'})];
    const disabledSend = element({label: '发送', disabled: true});
    buttons = [switcher, chip, disabledSend, duration, aspect, format];
    control = disabledSend;
    const doubleCheckSendDisabled = await sendVideoWithReference();
    buttons = [switcher, chip, send, duration, aspect, format];
    control = send;
    input.closest = originalClosest;
    globalReferenceCards = [];
    fileInputs = [];
    format.ariaLabel = '格式，GIF';
    const changedFormat = await call('send', {model_token: token, prompt: input.innerText});
    format.ariaLabel = '格式，视频';
    dialogs = [element({label: 'Other dialog'})];
    const unrelatedDialog = await call('send', {model_token: token, prompt: input.innerText});
    dialogs = [];
    duration.ariaLabel = '时长，10 秒';
    const changedDuration = await call('send', {model_token: token, prompt: input.innerText});
    duration.ariaLabel = '时长，15 秒';
    switcher.ariaLabel = '打开模式选择器，当前模式为“自动”';
    const after = await call('send', {model_token: token, prompt: input.innerText});
    switcher.ariaLabel = token;
    const musicDuration = element({label: '时长', text: '短版'});
    const musicVocals = element({label: '人声：纯音乐'});
    const musicGenre = element({text: '流派'});
    chip.ariaLabel = 'close 音乐';
    buttons = [switcher, chip, send, musicDuration, musicVocals, musicGenre];
    const musicArgs = {kind: 'music', model_token: token, prompt: input.innerText,
        music_duration: 'short', music_vocals: 'instrumental'};
    menus = [musicMenu(['短版', '标准'], '短版'), musicMenu(['自定义, 根据你的提示', '开启人声', '纯音乐'], '纯音乐')];
    const chineseRadioChecks = [
        await call('music_option_checked', {...musicArgs, option: 'duration'}),
        await call('music_option_checked', {...musicArgs, option: 'vocals'}),
    ];
    menus = [];
    const musicTokens = {
        duration: await call('music_option_token', {...musicArgs, option: 'duration'}),
        vocals: await call('music_option_token', {...musicArgs, option: 'vocals'}),
    };
    const chineseMusic = await call('send', {...musicArgs, music_tokens: musicTokens});
    musicDuration.innerText = '标准';
    const changedMusic = await call('send', {...musicArgs, music_tokens: musicTokens});
    musicDuration.innerText = '';
    musicVocals.ariaLabel = '人声';
    const genericMusicArgs = {...musicArgs, music_vocals: 'vocals'};
    const genericMusicTokens = {
        duration: await call('music_option_token', {...genericMusicArgs, option: 'duration'}),
        vocals: await call('music_option_token', {...genericMusicArgs, option: 'vocals'}),
    };
    const genericMusic = await call('send', {...genericMusicArgs, music_tokens: {duration: '时长\n', vocals: '人声\n'}});
    const defaultMusic = await call('send', {kind: 'music', model_token: token, prompt: input.innerText});
    musicDuration.ariaLabel = 'Duration: Standard';
    musicVocals.ariaLabel = 'Vocals, Custom, Based on your prompt';
    musicGenre.innerText = 'Genre';
    const englishArgs = {...musicArgs, music_duration: 'standard', music_vocals: 'auto'};
    menus = [musicMenu(['Short', 'Standard'], 'Standard'),
        musicMenu(['Custom, Based on your prompt', 'With vocals', 'Instrumental'], 'Custom, Based on your prompt')];
    const englishRadioChecks = [
        await call('music_option_checked', {...englishArgs, option: 'duration'}),
        await call('music_option_checked', {...englishArgs, option: 'vocals'}),
    ];
    menus = [];
    const englishTokens = {
        duration: await call('music_option_token', {...englishArgs, option: 'duration'}),
        vocals: await call('music_option_token', {...englishArgs, option: 'vocals'}),
    };
    const englishMusic = await call('send', {...englishArgs, music_tokens: englishTokens});
    musicVocals.innerText = 'Instrumental';
    const conflictingMusic = await call('send', {...englishArgs, music_tokens: englishTokens});
    musicVocals.ariaLabel = 'Vocals';
    musicVocals.innerText = 'Vocals';
    const bareVocals = await call('music_option_token', {...englishArgs, option: 'vocals', music_vocals: 'vocals'});
    const priorControl = control;
    const levels = [element({text: ' 低 快速、高效', checked: true}),
        element({text: ' 中 深度适中', checked: false}), element({text: ' 高 超级详尽', checked: false})];
    const mixedMenu = element({selectors: {'[role="menuitemradio"]': [
        element({text: '自动 根据需求调整回答'}), element({text: '日常帮助 4 Hydrogen'}),
        element({text: '更深入的分析 4 Neon'}), element({text: '代码和复杂工作 4 Carbon'}), ...levels,
    ]}});
    menus = [mixedMenu]; control = levels[2];
    const mixedThinkingTarget = await call('thinking_target', {thinking_level: 'high'});
    const mixedThinkingChecked = [await call('thinking_checked', {thinking_level: 'low'}),
        await call('thinking_checked', {thinking_level: 'high'})];
    menus = [mixedMenu, element({selectors: {'[role="menuitemradio"]': levels}})];
    const nestedThinkingTarget = await call('thinking_target', {thinking_level: 'high'});
    const englishLevels = [element({text: 'Low Fast and efficient', checked: false}),
        element({text: 'Medium Balanced depth', checked: false}), element({text: 'High Very thorough', checked: true})];
    const englishThinkingMenu = element({selectors: {'[role="menuitemradio"]': englishLevels}});
    menus = [englishThinkingMenu]; control = englishLevels[2];
    const englishThinkingTarget = await call('thinking_target', {thinking_level: 'high'});
    const englishThinkingChecked = await call('thinking_checked', {thinking_level: 'high'});
    menus = [mixedMenu, englishThinkingMenu];
    const ambiguousThinkingTarget = await call('thinking_target', {thinking_level: 'high'});
    menus = [element({selectors: {'[role="menuitemradio"]': [...levels, element({text: 'High Duplicate'})]}})];
    const duplicateThinkingTarget = await call('thinking_target', {thinking_level: 'high'});
    menus = [element({selectors: {'[role="menuitemradio"]': levels.slice(1)}})];
    const missingThinkingTarget = await call('thinking_target', {thinking_level: 'high'});
    menus = [element({selectors: {'[role="menuitemradio"]': [...levels.slice(0, 2), element({text: '高 超级详尽', disabled: true})]}})];
    const disabledThinkingTarget = await call('thinking_target', {thinking_level: 'high'});
    menus = []; control = priorControl;
    const initial = (await call('status')).signals;
    location.pathname = '/app/0123456789abcdef';
    responses = [...responses, element({text: 'A narrative about capacity and limits.'}), element({text: quota})];
    const subsequent = (await call('status')).signals;
    const refusal = "I don't have a built-in audio generation tool available right now to render a playable sound file directly";
    responses = [element({text: refusal}), element({text: `${refusal}. ${'long output '.repeat(60)}`, reactResponse: true})];
    const music = (await call('status', {kind: 'music'})).signals;
    const other = (await call('status')).signals;
    const generationFailure = 'I seem to be encountering an error.';
    responses = [
        element({text: `${generationFailure} Can I try again?`, reactResponse: true}),
        element({text: generationFailure, question: true, reactResponse: true}),
        element({text: generationFailure}),
        element({text: `Example: ${generationFailure}`, reactResponse: true}),
    ];
    const videoFailure = (await call('status')).signals;
    const musicUnavailable = connector =>
        `I can't generate or output an audio file directly right now${connector} the music generation tool isn't available in this session.`;
    responses = [
        element({text: `${musicUnavailable(' because')} ${'long body '.repeat(60)}`, reactResponse: true}),
        element({text: musicUnavailable(', as'), reactResponse: true}),
        element({text: `Example: ${musicUnavailable(', as')}`, reactResponse: true}),
        element({text: musicUnavailable(', as'), question: true, reactResponse: true}),
    ];
    const musicUnavailableSignals = (await call('status', {kind: 'music'})).signals;
    const musicUnavailableForVideo = (await call('status')).signals;
    const completedMusicRefusal = 'I cannot directly generate or output playable audio files here, as the music generation tool is currently unavailable.';
    responses = [
        element({text: `${completedMusicRefusal} ${'synthetic explanation '.repeat(60)}`, reactResponse: true}),
        element({text: `Example: ${completedMusicRefusal}`, reactResponse: true}),
        element({text: `Previously: ${completedMusicRefusal}`, reactResponse: true}),
        element({text: `${completedMusicRefusal}suffix`, reactResponse: true}),
        element({text: completedMusicRefusal, question: true, reactResponse: true}),
        element({text: completedMusicRefusal}),
        ...['blockquote', 'pre', 'code', 'q'].map(tag => {
            const quote = element({text: completedMusicRefusal, quoted: true});
            return element({text: `${completedMusicRefusal} Synthetic surrounding text.`, reactResponse: true,
                selectors: {'blockquote, pre, code, q': [quote]},
                textNodes: [{nodeValue: '   ', parentElement: quote}, {nodeValue: completedMusicRefusal, parentElement: quote}],
            });
        }),
        element({text: `${completedMusicRefusal} Later unrelated quote.`, reactResponse: true,
            selectors: {'blockquote, pre, code, q': [element({text: 'Later unrelated quote.'})]}}),
        element({text: `${completedMusicRefusal} Later quote: I`, reactResponse: true,
            selectors: {'blockquote, pre, code, q': [element({text: 'I', quoted: true})]}}),
    ];
    const completedMusicRefusalSignals = (await call('status', {kind: 'music'})).signals;
    const completedMusicRefusalForVideo = (await call('status')).signals;
    const blobUrl = 'blob:https://gemini.google.com/00000000-0000-0000-0000-000000000000';
    const outsideUrl = 'blob:https://gemini.google.com/11111111-1111-1111-1111-111111111111';
    const userUrl = 'blob:https://gemini.google.com/22222222-2222-2222-2222-222222222222';
    const unrelatedUrl = 'blob:https://gemini.google.com/33333333-3333-3333-3333-333333333333';
    const videoSpec = {localName: 'video', readyState: 1, videoWidth: 1080, videoHeight: 1080, duration: 20};
    players = [
        {...element({label: 'video/mp4', responseAncestor: true, musicOutput: true}), ...videoSpec, currentSrc: blobUrl},
        {...element({label: 'video/mp4', musicOutput: true}), ...videoSpec, currentSrc: outsideUrl},
        {...element({label: 'video/mp4', responseAncestor: true, musicOutput: true, question: true}), ...videoSpec, currentSrc: userUrl},
        {...element({label: 'video/mp4', responseAncestor: true}), ...videoSpec, currentSrc: unrelatedUrl},
    ];
    const musicCoverVideo = (await call('status', {kind: 'music'})).media;
    const outsideBlob = await call('load_blob', {kind: 'music', url: outsideUrl});
    const musicBlob = await call('load_blob', {kind: 'music', url: blobUrl});
    const musicBlobContent = await call('blob_chunk', {kind: 'music', offset: 0, size: 3});
    await call('clear_blob', {kind: 'music'});
    location.pathname = '/app';
    const emptyInput = element({editor: true});
    control = emptyInput;
    editableInputs = [emptyInput];
    document.activeElement = emptyInput;
    const emptyEditor = [await call('editor'), await call('editor_focused')];
    document.activeElement = send;
    const unfocusedEditor = await call('editor_focused');
    editableInputs = [emptyInput, input];
    document.activeElement = emptyInput;
    const ambiguousEditor = [await call('editor'), await call('editor_focused')];
    editableInputs = [input];
    control = input;
    document.activeElement = input;
    const nonemptyEditor = [await call('editor'), await call('editor_focused')];
    const savedButtonsForSelection = buttons;
    const savedEditorsForSelection = editableInputs;
    buttons = [send];
    editableInputs = [emptyInput];
    dialogs = [];
    const toolSelectionProof = {empty: await call('tool_unselected')};
    buttons.push(element({label: 'close 视频', disabled: true}));
    toolSelectionProof.disabledSelectedChip = await call('tool_unselected');
    buttons.pop();
    buttons.push(element({label: 'Deselect "Music"'}));
    toolSelectionProof.selectedMusic = await call('tool_unselected');
    buttons.pop();
    editableInputs = [input];
    toolSelectionProof.nonempty = await call('tool_unselected');
    editableInputs = [emptyInput, input];
    toolSelectionProof.ambiguous = await call('tool_unselected');
    editableInputs = [emptyInput];
    dialogs = [element({label: 'Unrecognized consent'})];
    toolSelectionProof.dialog = await call('tool_unselected');
    dialogs = [];
    buttons = savedButtonsForSelection;
    editableInputs = savedEditorsForSelection;
    console.log(JSON.stringify({acceptedIntro, unknownIntro, lateIntro, obscuredOption, nestedMenu, checkedOption, ambiguousMenu,
        token, technicalToken, before, after, changedDuration, changedFormat, unrelatedDialog, initial, subsequent, music, other, musicCoverVideo, outsideBlob,
        musicBlob, musicBlobContent, chineseRadioChecks, chineseMusic, changedMusic, genericMusicTokens, genericMusic,
        defaultMusic, englishRadioChecks, englishMusic, conflictingMusic, bareVocals, modelMenuOpen, modelMenuClosing,
        modelMenuClosed, modelMenuOpenTarget, disabledModelMenuTarget, duplicateModelMenuTarget, closedModelMenuTarget,
        optionIntro, unknownMenuState, nestedMenuOpen, disabledMenuOpen, disabledOptionChecked, disabledOptionTarget,
        ambiguousMenuOpen, closingVideoMenu, closedVideoMenu, videoFailure, musicUnavailableSignals, musicUnavailableForVideo,
        exactFileInput, duplicateFileInput, obscuredFileInput, referenceToken, verifiedVideoReference, unexpectedReference,
        obscuredReference, replacedReference, unloadedReference, uploadingReference, ambiguousReferences, outsideReference,
        incompleteReference, doubleCheckSendDisabled, explicitFormatTarget, unlabeledFormatTarget, ambiguousFormatTarget,
        unrelatedOverlay, tooltipMoveTarget, physicallyObstructingTooltip, toolsTooltipMoveTarget, tooltipWithMenu,
        tooltipWithDialog, obscuredTooltipMoveTarget, completedMusicRefusalSignals, completedMusicRefusalForVideo,
        mixedThinkingTarget, mixedThinkingChecked, nestedThinkingTarget, englishThinkingTarget, englishThinkingChecked,
        ambiguousThinkingTarget, duplicateThinkingTarget, missingThinkingTarget, disabledThinkingTarget,
        emptyEditor, unfocusedEditor, ambiguousEditor, nonemptyEditor, modelMenuStale, toolSelectionProof}));
})();
"""


@pytest.fixture(scope="module")
def native_media_dom():
    if _NODE is None:
        pytest.skip("Node.js is needed to execute the bundled JavaScript fixture")
    run = subprocess.run([_NODE, "-e", _DOM_FIXTURE, str(_SCRIPT)], capture_output=True, check=True, timeout=5)
    return json.loads(run.stdout)


def test_bundled_dom_script_identifies_reset_model_and_only_exact_response_rejection(native_media_dom):
    result = native_media_dom
    assert result["token"] == "打开模式选择器，当前模式为“Hydrogen”"
    assert result["technicalToken"] == result["token"]
    assert not result["modelMenuOpen"] and not result["modelMenuClosing"] and result["modelMenuClosed"]
    assert result["modelMenuOpenTarget"] == {"x": 40, "y": 90}
    assert all(result[key] is None for key in ("disabledModelMenuTarget", "duplicateModelMenuTarget", "closedModelMenuTarget"))
    assert result["before"] == {"x": 40, "y": 90} and result["after"] is None and result["changedDuration"] is None
    assert result["changedFormat"] is None and result["unrelatedDialog"] is None
    assert result["acceptedIntro"] == {"intro": {"x": 40, "y": 90}} and result["unknownIntro"] is None
    assert result["lateIntro"] == result["acceptedIntro"] and result["obscuredOption"] is None
    assert result["nestedMenu"] == {"x": 40, "y": 90} and result["checkedOption"] and result["ambiguousMenu"] is None
    assert [{k: v for k, v in item.items() if k != "message"} for item in result["initial"]] == [_ALERT]
    assert [{k: v for k, v in item.items() if k != "message"} for item in result["subsequent"]] == [_ALERT, {**_ALERT, "id": 2}]
    assert [{k: v for k, v in item.items() if k != "message"} for item in result["music"]] == [{"id": 3, "code": "ARTIFACT_NOT_RETURNED"}]
    assert all(isinstance(item.get("message"), str) and item["message"] for item in result["initial"] + result["subsequent"] + result["music"])
    assert result["other"] == []


def test_bundled_dom_script_exports_music_cover_mp4_only_from_native_response(native_media_dom):
    result = native_media_dom
    assert result["musicCoverVideo"] == [{
        "kind": "music", "url": _BLOB, "mime_type": "video/mp4", "width": 1080, "height": 1080,
        "duration": 20, "ready": True,
    }]
    assert result["outsideBlob"] is None
    assert result["musicBlob"] == {"size": 3, "mime_type": "video/mp4"}
    assert base64.b64decode(result["musicBlobContent"], validate=True) == bytes([11, 22, 33])


def test_bundled_dom_script_confirms_video_menu_state_before_clicking(native_media_dom):
    result = native_media_dom
    assert result["optionIntro"] == result["acceptedIntro"] and result["unknownMenuState"] is None
    assert result["nestedMenuOpen"] and result["disabledMenuOpen"] and result["disabledOptionChecked"]
    assert result["disabledOptionTarget"] is None and not result["ambiguousMenuOpen"]
    assert not result["closingVideoMenu"] and result["closedVideoMenu"]


def test_bundled_dom_uses_explicit_format_control_before_generic_video_fallback(native_media_dom):
    result = native_media_dom
    assert result["explicitFormatTarget"] == result["unlabeledFormatTarget"] == {"x": 40, "y": 90}
    assert result["ambiguousFormatTarget"] is None


def test_bundled_dom_chooses_a_safe_hover_target_for_a_physical_tooltip(native_media_dom):
    result = native_media_dom
    assert result["tooltipMoveTarget"] == result["physicallyObstructingTooltip"] == {"x": 40, "y": 90}
    assert result["toolsTooltipMoveTarget"] == {"x": 95, "y": 110}
    assert all(result[key] is None for key in (
        "unrelatedOverlay", "tooltipWithMenu", "tooltipWithDialog", "obscuredTooltipMoveTarget",
    ))


def test_bundled_dom_script_ignores_quoted_or_nonresponse_generation_errors(native_media_dom):
    result = native_media_dom
    assert [entry["code"] for entry in result["videoFailure"]] == ["UPSTREAM_REJECTED"]
    assert [entry["code"] for entry in result["musicUnavailableSignals"]] == ["ARTIFACT_NOT_RETURNED"] * 2
    assert result["musicUnavailableForVideo"] == []


def test_bundled_dom_script_recognizes_only_the_observed_playable_audio_refusal(native_media_dom):
    assert [entry["code"] for entry in native_media_dom["completedMusicRefusalSignals"]] == ["ARTIFACT_NOT_RETURNED"] * 3
    assert native_media_dom["completedMusicRefusalForVideo"] == []


def test_native_thinking_levels_share_the_model_menu_and_include_descriptions(native_media_dom):
    assert native_media_dom["mixedThinkingTarget"] == {"x": 40, "y": 90}
    assert native_media_dom["mixedThinkingChecked"] == [True, False]
    assert native_media_dom["nestedThinkingTarget"] == native_media_dom["englishThinkingTarget"] == {"x": 40, "y": 90}
    assert native_media_dom["englishThinkingChecked"]


def test_native_thinking_levels_reject_ambiguous_incomplete_or_disabled_choices(native_media_dom):
    assert all(native_media_dom[key] is None for key in (
        "ambiguousThinkingTarget", "duplicateThinkingTarget", "missingThinkingTarget", "disabledThinkingTarget",
    ))


def test_bundled_dom_script_checks_the_exact_native_composer_attachment_before_sending(native_media_dom):
    result = native_media_dom
    assert result["exactFileInput"] and not result["duplicateFileInput"] and not result["obscuredFileInput"]
    assert result["referenceToken"] == _REFERENCE_TOKEN
    assert result["verifiedVideoReference"] == result["before"] == {"x": 40, "y": 90}
    assert all(result[key] is None for key in (
        "unexpectedReference", "obscuredReference", "replacedReference", "unloadedReference", "uploadingReference",
        "ambiguousReferences", "outsideReference", "incompleteReference", "doubleCheckSendDisabled",
    ))


def test_native_editor_target_and_focus_require_one_empty_editor(native_media_dom):
    assert native_media_dom["emptyEditor"] == [{"x": 40, "y": 90}, True]
    assert native_media_dom["unfocusedEditor"] is False
    assert native_media_dom["ambiguousEditor"] == native_media_dom["nonemptyEditor"] == [None, False]


def test_bundled_dom_script_requires_music_token_to_name_the_requested_choice_at_send(native_media_dom):
    result = native_media_dom
    send = {"x": 40, "y": 90}
    assert result["chineseRadioChecks"] == result["englishRadioChecks"] == [True, True]
    assert result["chineseMusic"] == result["englishMusic"] == result["defaultMusic"] == send
    assert result["genericMusicTokens"] == {"duration": None, "vocals": None}
    assert all(result[key] is None for key in ("changedMusic", "genericMusic", "conflictingMusic", "bareVocals"))


def test_bundled_dom_script_rejects_stale_reset_when_model_panel_or_trigger_is_unverified(native_media_dom):
    stale = native_media_dom["modelMenuStale"]
    assert stale["eligible"] == stale["hiddenControlledPanel"] == {"x": 40, "y": 90}
    assert all(stale[key] is None for key in (
        "visibleMenu", "dialog", "duplicateTrigger", "visibleControlledPanel", "visibleRequestedRadio",
        "obscuredTrigger", "closedTrigger",
    ))


@pytest.mark.parametrize("selection_known_absent,expected_tool_clicks", [(True, 2), (False, 1), (None, 1)])
def test_tool_confirmation_retry_requires_positive_unselected_evidence(monkeypatch, selection_known_absent, expected_tool_clicks):
    fixture = _Chrome("video", _REMOTE)
    evaluate = fixture.evaluate
    wait = browser._Composer.wait
    tool_checks = 0

    def evaluate_selection(action, arg):
        if action == "media_tool":
            return {"x": 40, "y": 50}
        if action == "tool_unselected":
            return selection_known_absent
        return evaluate(action, arg)

    async def delayed_confirmation(self, action, **kwargs):
        nonlocal tool_checks
        if action == "tool_entry":
            tool_checks += 1
            if tool_checks == 1:
                raise TimeoutError
        return await wait(self, action, **kwargs)

    monkeypatch.setattr(fixture, "evaluate", evaluate_selection)
    monkeypatch.setattr(browser._Composer, "wait", delayed_confirmation)
    result = _submit(monkeypatch, fixture)
    assert result.state == "ready" and result.submitted
    assert sum(event == ("mouseReleased", 40, 50) for event in fixture.pointer_events) == expected_tool_clicks
    assert fixture.actions.count("submit") == 1


def test_tool_selection_dom_proof_excludes_existing_disabled_or_ambiguous_choices(native_media_dom):
    assert native_media_dom["toolSelectionProof"] == {
        "empty": True, "disabledSelectedChip": False, "selectedMusic": False,
        "nonempty": False, "ambiguous": False, "dialog": False,
    }


def test_native_music_hovers_more_when_nested_and_supports_genre(monkeypatch):
    fixture = _Chrome("music", _BLOB)
    base_evaluate = fixture.evaluate

    def evaluate_nested(action, arg):
        if action == "media_tool" and "hover_more" not in fixture.actions:
            return None
        return base_evaluate(action, arg)

    monkeypatch.setattr(fixture, "evaluate", evaluate_nested)
    result = _submit(monkeypatch, fixture, music_duration="short", music_vocals="instrumental", music_genre="acoustic")
    assert result.state == "ready" and result.submitted
    assert "more" in fixture.actions and "hover_more" in fixture.actions
    assert fixture.music_selected == {"duration": "short", "vocals": "instrumental", "genre": "acoustic"}


def test_native_video_disabled_option_fails_fast_invalid_argument(monkeypatch):
    fixture = _Chrome("video", _REMOTE)
    base_evaluate = fixture.evaluate

    def evaluate_disabled(action, arg):
        if action == "video_option_target_disabled" and arg.get("option") == "duration":
            return True
        return base_evaluate(action, arg)

    monkeypatch.setattr(fixture, "evaluate", evaluate_disabled)
    result = _submit(monkeypatch, fixture, duration_seconds=15, timeout=30.0)
    assert result.state == "unverified" and not result.submitted and result.error_code == "INVALID_ARGUMENT"
    assert "disabled" in (result.error_message or "").lower()
    assert "submit" not in fixture.actions
