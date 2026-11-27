"""Submit one media request through Gemini's native browser composer."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from importlib.resources import files
import inspect
import json
import math
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from ..constants import resolve_thinking_level_id
from ..services.media_generation import MUSIC_GENRES
from .task_approval_browser import (
    TaskApprovalBrowserUnavailable, _DevToolsPipe, _chrome_executable, _cookie_parameters, _open_browser, _ready_context,
)

_COMPOSER_URL = "https://gemini.google.com/app"
_CDP_TIMEOUT = 12
_NAVIGATION_TIMEOUT = 60
_UI_TIMEOUT = 60
_EDITOR_FOCUS_TIMEOUT = 2.0
_MODEL_MENU_OPEN_TIMEOUT = 2.0
_OPTION_MENU_OPEN_TIMEOUT = 2.0
_TOOLS_MENU_OPEN_TIMEOUT = 4.0
_POLL_INTERVAL = 0.4
_MAX_BLOB_BYTES = 100 * 1024 * 1024
_MAX_REFERENCE_BYTES = 25 * 1024 * 1024
_REFERENCE_INPUT = 'input[type="file"][accept="image/*,video/*"]'
_BLOB_CHUNK_BYTES = 64 * 1024
_CHAT_PATH = re.compile(r"/app/([a-f0-9]{16})")
_BLOB_URL = re.compile(r"blob:https://gemini\.google\.com/[0-9a-f-]{36}")
_FAILURES = (TaskApprovalBrowserUnavailable, OSError, KeyError, ValueError, TypeError, EOFError, asyncio.LimitOverrunError)


@dataclass(frozen=True)
class NativeMediaObservation:
    state: Literal["ready", "rejected", "unverified"] = "unverified"
    chat_id: str | None = None
    selected_model_verified: bool = False
    media: tuple[dict[str, Any], ...] = ()
    error_code: str | None = None
    submitted: bool = False
    stage: str | None = None
    error_message: str | None = None


@dataclass
class _Attempt:
    chat_id: str | None = None
    selected_model_verified: bool = False
    submitted: bool = False
    stage: str = "request"

    def result(self, state: Literal["ready", "rejected", "unverified"] = "unverified", *,
               media: tuple[dict[str, Any], ...] = (), error_code: str | None = None,
               error_message: str | None = None) -> NativeMediaObservation:
        return NativeMediaObservation(state, self.chat_id, self.selected_model_verified, media, error_code,
                                      self.submitted, self.stage, error_message)


def _gemini_path(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "gemini.google.com" or parsed.fragment:
        return None
    return parsed.path


def _allowed_media_url(url: str) -> bool:
    if len(url) > 8192 or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        return False
    if _BLOB_URL.fullmatch(url):
        return True
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return (parsed.scheme == "https" and parsed.netloc == host and not parsed.fragment
            and (host == "googleusercontent.com" or host.endswith(".googleusercontent.com")))


class _VideoIntroAcknowledged(Exception):
    """Reopen a native composer menu that was interrupted by the introduction."""


class _InvalidNativeArgument(Exception):
    """Fail fast when Gemini Web disables a requested option for the active inputs."""


class _Composer:
    def __init__(self, pipe: _DevToolsPipe, script: str, kind: str, model: str, account_matches: Callable[[], bool],
                 *, duration_seconds: int | None, aspect_ratio: str | None, thinking_level: str,
                 music_duration: str | None = None, music_vocals: str | None = None,
                 music_genre: str | None = None, reference_required: bool = False):
        self.pipe, self.script, self.kind, self.model = pipe, script, kind, model
        self.account_matches = account_matches
        self.options = {"duration_seconds": duration_seconds, "aspect_ratio": aspect_ratio, "thinking_level": thinking_level,
                        "music_duration": music_duration, "music_vocals": music_vocals, "music_genre": music_genre,
                        "reference_required": reference_required}
        self.session: str | None = None
        self.world: int | None = None
        self.frame_url: str | None = None
        self.browser_context: str | None = None
        self.video_intro_acknowledged = False

    def require_account(self) -> None:
        if self.account_matches() is not True:
            raise TaskApprovalBrowserUnavailable("The account changed during native media generation.")

    async def call(self, method: str, params: dict[str, Any] | None = None, *, page: bool = False) -> dict[str, Any]:
        self.require_account()
        # Do not spawn a wait_for child here: on 3.11 its completion can
        # swallow the outer composer or focus deadline.
        async with asyncio.timeout(_NAVIGATION_TIMEOUT if method == "Page.navigate" else _CDP_TIMEOUT):
            value = await self.pipe.call(method, params, session=self.session if page else None)
        self.require_account()
        return value

    async def navigate(self, cookies: Mapping[str, str]) -> None:
        context = await self.call("Target.createBrowserContext")
        self.browser_context = context["browserContextId"]
        params = _cookie_parameters(cookies)
        await self.call("Storage.setCookies", {"browserContextId": self.browser_context, "cookies": params})
        target = await self.call("Target.createTarget", {"url": "about:blank", "browserContextId": self.browser_context})
        attached = await self.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        self.session = attached["sessionId"]
        if isinstance(self.pipe, _DevToolsPipe):
            with suppress(Exception):
                version = await self.call("Browser.getVersion")
                ua = str(version.get("userAgent") or "")
                if "HeadlessChrome/" in ua:
                    await self.call(
                        "Emulation.setUserAgentOverride",
                        {"userAgent": ua.replace("HeadlessChrome/", "Chrome/"), "platform": "MacIntel"},
                        page=True,
                    )
        await self.call("Emulation.setDeviceMetricsOverride", {
            "width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False,
        }, page=True)
        for attempt in range(2):
            try:
                navigation = await self.call("Page.navigate", {"url": _COMPOSER_URL}, page=True)
                if navigation.get("errorText") == "net::ERR_TIMED_OUT":
                    raise TimeoutError
                if navigation.get("errorText") or navigation.get("isDownload"):
                    raise TaskApprovalBrowserUnavailable("The Gemini composer did not open.")
                async with asyncio.timeout(_UI_TIMEOUT):
                    while (await self.url()) == "about:blank":
                        await asyncio.sleep(_POLL_INTERVAL)
                    if _gemini_path(self.frame_url or "") not in ("/app", "/app/"):
                        raise TaskApprovalBrowserUnavailable("The signed-in Gemini composer was not observed.")
                    await self.refresh_world()
                    await self.wait("tools")
                return
            except TimeoutError:
                if attempt:
                    raise
                self.frame_url, self.world = None, None

    async def url(self) -> str:
        result = await self.call("Page.getFrameTree", page=True)
        address = result["frameTree"]["frame"]["url"]
        if not isinstance(address, str):
            raise TaskApprovalBrowserUnavailable("Chrome did not verify the Gemini URL.")
        if address != self.frame_url:
            self.frame_url, self.world = address, None
        return address

    async def refresh_world(self) -> None:
        if self.world is None:
            assert self.session is not None and self.frame_url is not None
            self.require_account()
            async with asyncio.timeout(_CDP_TIMEOUT):
                self.world = await _ready_context(self.pipe, self.frame_url, self.session)
            self.require_account()

    async def evaluate(self, action: str, **values: Any) -> Any:
        await self.refresh_world()
        arguments = {"action": action, "kind": self.kind, "model": self.model, **self.options, **values}
        reply = await self.call("Runtime.evaluate", {
            "expression": f"({self.script})({json.dumps(arguments, allow_nan=False)})",
            "contextId": self.world, "awaitPromise": True, "returnByValue": True,
        }, page=True)
        result = reply.get("result")
        if "exceptionDetails" in reply or not isinstance(result, dict):
            raise TaskApprovalBrowserUnavailable("The native media composer returned an unverified result.")
        return result.get("value")

    async def wait(self, action: str, **values: Any) -> Any:
        async with asyncio.timeout(_UI_TIMEOUT):
            while True:
                result = await self.evaluate(action, **values)
                if result:
                    return result
                if action in {"tools", "mode", "video_option_menu", "music_option_menu", "send"}:
                    if point := await self.evaluate("tooltip_dismiss"):
                        if isinstance(point, dict) and point.get("click") is True:
                            await self.click(point)
                        else:
                            await self.hover(point)
                if self.kind == "video" and action in {
                    "editor", "editor_focused", "send", "reference_input", "reference_token", "composer_menu_closed",
                }:
                    entry = await self.evaluate("tool_entry")
                    if isinstance(entry, dict) and entry.get("intro"):
                        await self.accept_video_intro(entry["intro"])
                await asyncio.sleep(_POLL_INTERVAL)

    @staticmethod
    def checked_point(point: Any) -> tuple[float, float]:
        if not isinstance(point, dict) or not all(type(point.get(axis)) in (int, float)
                and math.isfinite(point[axis]) and point[axis] >= 0 for axis in ("x", "y")):
            raise TaskApprovalBrowserUnavailable("The expected Gemini control was not verified.")
        return float(point["x"]), float(point["y"])

    async def hover(self, point: Any) -> None:
        x, y = self.checked_point(point)
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, page=True)

    async def click(self, point: Any) -> None:
        x, y = self.checked_point(point)
        await self.hover(point)
        for event, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
            await self.call("Input.dispatchMouseEvent", {
                "type": event, "x": x, "y": y, "button": "left", "buttons": buttons, "clickCount": 1,
            }, page=True)

    async def verify_browser_account(self, cookies: Mapping[str, str]) -> None:
        actual = await self.call("Storage.getCookies", {"browserContextId": self.browser_context})
        expected = cookies.get("__Secure-1PSID")
        values = {item.get("value") for item in actual["cookies"] if item.get("name") == "__Secure-1PSID"}
        if not expected or values != {expected}:
            raise TaskApprovalBrowserUnavailable("The browser did not retain the verified Gemini account.")

    async def focus_empty_editor(self, cookies: Mapping[str, str]) -> None:
        async with asyncio.timeout(_UI_TIMEOUT):
            await self.click(await self.wait("editor"))
            for attempt in range(2):
                try:
                    async with asyncio.timeout(_EDITOR_FOCUS_TIMEOUT):
                        await self.wait("editor_focused")
                    return
                except TimeoutError:
                    if attempt:
                        raise
                    await self.verify_browser_account(cookies)
                    if await self.evaluate("editor_focused") is True:
                        return
                    point = await self.evaluate("editor")
                    if not point:
                        raise TaskApprovalBrowserUnavailable("The empty Gemini editor could not be verified.")
                    await self.click(point)

    async def open_tools_for(self, target_action: str) -> Any:
        """Click the tools menu and retry once only if the menu remained closed."""
        async with asyncio.timeout(_UI_TIMEOUT):
            for attempt in range(2):
                await self.click(await self.wait("tools"))
                if target_action == "media_tool" and self.kind == "music":
                    try:
                        async with asyncio.timeout(_TOOLS_MENU_OPEN_TIMEOUT):
                            hovered_more = False
                            while True:
                                if (direct := await self.evaluate("media_tool")):
                                    return direct
                                if not hovered_more and (more_point := await self.evaluate("more")):
                                    await self.hover(more_point)
                                    hovered_more = True
                                await asyncio.sleep(_POLL_INTERVAL)
                    except TimeoutError:
                        if not attempt and await self.evaluate("composer_menu_closed") is True:
                            continue
                        if (direct := await self.evaluate("media_tool")):
                            return direct
                        await self.hover(await self.wait("more"))
                        return await self.wait("media_tool")
                try:
                    async with asyncio.timeout(_TOOLS_MENU_OPEN_TIMEOUT):
                        return await self.wait(target_action)
                except TimeoutError:
                    if not attempt and await self.evaluate("composer_menu_closed") is True:
                        continue
                    return await self.wait(target_action)
        raise TimeoutError

    async def attach_reference_image(self, path: str, cookies: Mapping[str, str]) -> str:
        await self.open_tools_for("reference_input")
        document = await self.call("DOM.getDocument", {"depth": 0}, page=True)
        selected = await self.call("DOM.querySelectorAll", {
            "nodeId": document["root"]["nodeId"], "selector": _REFERENCE_INPUT,
        }, page=True)
        nodes = selected.get("nodeIds")
        if not isinstance(nodes, list) or len(nodes) != 1 or type(nodes[0]) is not int:
            raise TaskApprovalBrowserUnavailable("The native image upload input was not unique.")
        await self.verify_browser_account(cookies)
        await self.call("DOM.setFileInputFiles", {"nodeId": nodes[0], "files": [path]}, page=True)
        token = await self.wait("reference_token")
        if not isinstance(token, str) or len(token) > 8192:
            raise TaskApprovalBrowserUnavailable("The native reference preview was not verified.")
        if await self.evaluate("composer_menu_closed") is not True:
            await self.exit_menu()
        await self.wait("composer_menu_closed")
        return token

    async def exit_menu(self) -> None:
        for event in ("keyDown", "keyUp"):
            await self.call("Input.dispatchKeyEvent", {"type": event, "key": "Escape", "code": "Escape",
                                                       "windowsVirtualKeyCode": 27}, page=True)

    async def open_model_menu(self) -> Any:
        """Reopen a proven closed popup and reset a verified stale trigger once."""
        async with asyncio.timeout(_UI_TIMEOUT):
            clicks = 0
            reset = False
            while True:
                if point := await self.evaluate("model_menu_open"):
                    return point
                if await self.evaluate("model_menu_closed") is not True:
                    if clicks == 1 and not reset and await self.evaluate("model_menu_stale"):
                        if point := await self.evaluate("model_menu_stale"):
                            reset = True
                            await self.click(point)
                            continue
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue
                if clicks == 2:
                    raise TimeoutError
                point = await self.wait("mode")
                if await self.evaluate("model_menu_closed") is not True:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue
                await self.click(point)
                clicks += 1
                try:
                    async with asyncio.timeout(_MODEL_MENU_OPEN_TIMEOUT):
                        return await self.wait("model_menu_open")
                except TimeoutError:
                    continue

    async def verify_model_selection(self) -> str:
        """Reopen only when a delayed selection callback closed the read-only popup."""
        async with asyncio.timeout(_UI_TIMEOUT):
            for attempt in range(2):
                await self.open_model_menu()
                try:
                    async with asyncio.timeout(_MODEL_MENU_OPEN_TIMEOUT):
                        token = await self.wait("model_checked")
                except TimeoutError:
                    if not attempt and await self.evaluate("model_menu_closed") is True:
                        continue
                    token = await self.wait("model_checked")
                if not isinstance(token, str) or len(token) > 512:
                    raise TaskApprovalBrowserUnavailable("The checked model could not be verified.")
                return token
        raise TimeoutError

    async def accept_video_intro(self, point: Any) -> None:
        if self.video_intro_acknowledged:
            raise TaskApprovalBrowserUnavailable("The Gemini video introduction did not close.")
        self.video_intro_acknowledged = True
        await self.click(point)
        await self.wait("video_intro_closed")

    async def video_control(self, action: str, **values: Any) -> Any:
        result = await self.wait(action, **values)
        await self.check_video_intro(result)
        return result

    async def check_video_intro(self, result: Any) -> None:
        if isinstance(result, dict) and result.get("intro"):
            await self.accept_video_intro(result["intro"])
            raise _VideoIntroAcknowledged

    async def video_state(self, action: str, **values: Any) -> Any:
        result = await self.evaluate(action, **values)
        await self.check_video_intro(result)
        return result

    async def open_video_option_menu(self, option: str) -> None:
        async with asyncio.timeout(_UI_TIMEOUT):
            if await self.video_state("video_option_open", option=option) is True:
                return
            if await self.video_state("video_option_closed", option=option) is not True:
                await self.video_control("video_option_open", option=option)
                return
            for attempt in range(2):
                await self.click(await self.video_control("video_option_menu", option=option))
                try:
                    async with asyncio.timeout(_OPTION_MENU_OPEN_TIMEOUT):
                        await self.video_control("video_option_open", option=option)
                        return
                except TimeoutError:
                    if await self.video_state("video_option_closed", option=option) is not True:
                        await self.video_control("video_option_open", option=option)
                        return
                    if attempt:
                        raise

    async def wait_video_options(self) -> None:
        while True:
            try:
                await self.video_control("video_options")
                return
            except _VideoIntroAcknowledged:
                continue

    async def configure_video_option(self, option: str, *, select: bool) -> None:
        while True:
            try:
                if select:
                    if await self.evaluate("video_option_menu_disabled", option=option) is True:
                        raise _InvalidNativeArgument(
                            f"Gemini Web disabled the video {option} control for the current configuration."
                        )
                    await self.open_video_option_menu(option)
                    if await self.evaluate("video_option_checked", option=option) is not True:
                        if await self.evaluate("video_option_target_disabled", option=option) is True:
                            await self.exit_menu()
                            raise _InvalidNativeArgument(
                                f"Gemini Web disabled the requested video {option} option for the current configuration."
                            )
                        target = await self.video_control("video_option_target", option=option)
                        await self.click(target)
                    await self.exit_menu()
                    await self.video_control("video_option_closed", option=option)
                await self.open_video_option_menu(option)
                await self.video_control("video_option_checked", option=option)
                await self.exit_menu()
                await self.video_control("video_option_closed", option=option)
                return
            except _VideoIntroAcknowledged:
                continue

    async def select_thinking_level(self) -> None:
        try:
            # Verify the selected model and wait for its menu to close before opening its submenu.
            await self.verify_model_selection()
            await self.exit_menu()
            await self.wait("model_menu_closed")
            await self.open_model_menu()
            await self.hover(await self.wait("thinking_menu"))
            await self.click(await self.wait("thinking_target"))
            await self.exit_menu()
            await self.exit_menu()
            await self.wait("model_menu_closed")
            await self.open_model_menu()
            await self.hover(await self.wait("thinking_menu"))
            await self.wait("thinking_checked")
            await self.exit_menu()
            await self.exit_menu()
            await self.wait("model_menu_closed")
        except TimeoutError:
            raise TaskApprovalBrowserUnavailable("The requested native media thinking level was not verified.") from None

    async def configure_music_option(self, option: str, *, select: bool) -> str:
        await self.click(await self.wait("music_option_menu", option=option))
        if select and await self.evaluate("music_option_checked", option=option) is not True:
            await self.click(await self.wait("music_option_target", option=option))
        await self.exit_menu()
        await self.click(await self.wait("music_option_menu", option=option))
        await self.wait("music_option_checked", option=option)
        await self.exit_menu()
        token = await self.wait("music_option_token", option=option)
        if not isinstance(token, str) or len(token) > 512:
            raise TaskApprovalBrowserUnavailable("The music option state was not verified.")
        return token

    async def read_blob(self, url: str) -> tuple[bytes, str]:
        try:
            info = await self.evaluate("load_blob", url=url)
            if not isinstance(info, dict):
                raise TaskApprovalBrowserUnavailable("The generated browser object could not be read.")
            length, mime_type = info.get("size"), info.get("mime_type")
            if type(length) is not int or not 0 < length <= _MAX_BLOB_BYTES or not isinstance(mime_type, str):
                raise TaskApprovalBrowserUnavailable("The generated browser object size could not be verified.")
            result = bytearray()
            for offset in range(0, length, _BLOB_CHUNK_BYTES):
                size = min(length - offset, _BLOB_CHUNK_BYTES)
                encoded = await self.evaluate("blob_chunk", offset=offset, size=size)
                if not isinstance(encoded, str) or len(encoded) != (size + 2) // 3 * 4:
                    raise TaskApprovalBrowserUnavailable("The browser media segment could not be verified.")
                chunk = base64.b64decode(encoded, validate=True)
                if len(chunk) != size:
                    raise TaskApprovalBrowserUnavailable("The browser media segment size did not match.")
                result.extend(chunk)
            return bytes(result), mime_type
        finally:
            with suppress(TimeoutError, *_FAILURES):
                await self.evaluate("clear_blob")


def _status_items(status: Any, key: str) -> list[dict[str, Any]]:
    value = status.get(key) if isinstance(status, dict) else None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise TaskApprovalBrowserUnavailable("The exact media response could not be read.")
    return value


async def _observe_generation(composer: _Composer, attempt: _Attempt, baseline: Any,
                              on_chat: Callable[[str], Awaitable[None]] | None) -> NativeMediaObservation:
    already_present = {item.get("url") for item in _status_items(baseline, "media")}
    old_signals = {(item.get("id"), item.get("code")) for item in _status_items(baseline, "signals")}
    while True:
        path = _gemini_path(await composer.url())
        chat = _CHAT_PATH.fullmatch(path or "")
        if chat:
            identity = "c_" + chat.group(1)
            if attempt.chat_id is None:
                attempt.chat_id = identity
                if on_chat is not None:
                    async with asyncio.timeout(_CDP_TIMEOUT):
                        await on_chat(identity)
                    composer.require_account()
            elif attempt.chat_id != identity:
                raise TaskApprovalBrowserUnavailable("The native media conversation changed.")
        elif path not in ("/app", "/app/") or attempt.chat_id is not None:
            raise TaskApprovalBrowserUnavailable("The native media conversation could not be verified.")
        status = await composer.evaluate("status")
        ready: dict[str, dict[str, Any]] = {}
        if attempt.chat_id:
            for item in _status_items(status, "media"):
                url = item.get("url")
                if (item.get("kind") == composer.kind and item.get("ready") is True and isinstance(url, str)
                        and url not in already_present and _allowed_media_url(url)):
                    ready[url] = item
        if ready:
            assets = []
            for url, item in ready.items():
                content, mime_type = await composer.read_blob(url) if _BLOB_URL.fullmatch(url) else (None, item.get("mime_type"))
                assets.append({"kind": composer.kind, "url": url, "mime_type": mime_type,
                               "width": item.get("width"), "height": item.get("height"), "duration": item.get("duration"),
                               "content": content, "byte_size": len(content) if content is not None else None})
            return attempt.result("ready", media=tuple(assets))
        for item in _status_items(status, "signals"):
            code = item.get("code")
            if (code in {"UPSTREAM_LIMIT_EXCEEDED", "UPSTREAM_REJECTED"}
                    or (code == "ARTIFACT_NOT_RETURNED" and composer.kind == "music")) \
                    and (item.get("id"), code) not in old_signals:
                msg = item.get("message")
                return attempt.result(
                    "rejected",
                    error_code=code,
                    error_message=msg if isinstance(msg, str) and msg.strip() else None,
                )
        await asyncio.sleep(_POLL_INTERVAL)


async def run_native_media_browser(kind: Literal["music", "video"], prompt: str, model_label: str,
                                   cookies: Mapping[str, str], *, timeout: float,
                                   on_chat: Callable[[str], Awaitable[None]] | None = None,
                                   account_matches: Callable[[], bool], duration_seconds: int | None = None,
                                   aspect_ratio: str | None = None, thinking_level: str = "standard",
                                   music_duration: str | None = None, music_vocals: str | None = None,
                                   music_genre: str | None = None,
                                   reference_image_path: str | None = None) -> NativeMediaObservation:
    """Send once from an empty exact composer, preserving whether submission was attempted."""
    attempt = _Attempt()
    reference: Path | None = None
    if reference_image_path is not None:
        try:
            if kind != "video" or not isinstance(reference_image_path, str):
                return attempt.result(error_code="INVALID_ARGUMENT")
            reference = Path(reference_image_path).expanduser().resolve(strict=True)
            if not reference.is_file() or not 0 < reference.stat().st_size <= _MAX_REFERENCE_BYTES:
                return attempt.result(error_code="INVALID_ARGUMENT")
        except (OSError, ValueError):
            return attempt.result(error_code="INVALID_ARGUMENT")
    level = {1: "low", 4: "medium", 2: "high"}.get(resolve_thinking_level_id(thinking_level) or 0)
    if (kind not in ("music", "video") or not prompt.strip() or not model_label.strip()
            or not math.isfinite(timeout) or timeout <= 0 or level is None
            or (kind != "video" and (duration_seconds is not None or aspect_ratio is not None))
            or (kind != "music" and (music_duration is not None or music_vocals is not None or music_genre is not None))
            or music_duration not in (None, "short", "standard")
            or music_vocals not in (None, "instrumental", "vocals", "auto")
            or (music_genre is not None and music_genre not in MUSIC_GENRES)
            or (duration_seconds is not None and (type(duration_seconds) is not int or duration_seconds not in (10, 15)))
            or aspect_ratio not in (None, "16:9", "9:16")):
        return attempt.result(error_code="INVALID_ARGUMENT")
    try:
        if account_matches() is not True:
            raise TaskApprovalBrowserUnavailable("The native media account could not be verified.")
        script = files("src").joinpath("data/native_media_browser.js").read_text(encoding="utf-8")
        attempt.stage = "open_composer"
        open_kwargs = {"stealth": True} if "stealth" in inspect.signature(_open_browser).parameters else {}
        async with asyncio.timeout(timeout), _open_browser(_chrome_executable(), **open_kwargs) as pipe:
            uploaded_reference_token: str | None = None
            composer = _Composer(pipe, script, kind, model_label, account_matches, duration_seconds=duration_seconds,
                                 aspect_ratio=aspect_ratio, thinking_level=level,
                                 music_duration=music_duration, music_vocals=music_vocals, music_genre=music_genre,
                                 reference_required=reference is not None)
            await composer.navigate(cookies)
            attempt.stage = "open_tools"
            media_tool_point = await composer.open_tools_for("media_tool")
            await composer.click(media_tool_point)
            attempt.stage = "media_tool"
            try:
                async with asyncio.timeout(_TOOLS_MENU_OPEN_TIMEOUT):
                    tool_entry = await composer.wait("tool_entry")
            except TimeoutError:
                if await composer.evaluate("tool_unselected") is True:
                    if (retry_point := await composer.evaluate("media_tool")):
                        await composer.click(retry_point)
                    elif await composer.evaluate("composer_menu_closed") is True:
                        await composer.click(await composer.open_tools_for("media_tool"))
                tool_entry = await composer.wait("tool_entry")
            if not isinstance(tool_entry, dict):
                raise TaskApprovalBrowserUnavailable("The native media tool was not verified.")
            if tool_entry.get("intro"):
                attempt.stage = "media_intro"
                await composer.accept_video_intro(tool_entry["intro"])
            if kind == "video":
                attempt.stage = "video_options"
                await composer.wait_video_options()
                if reference is not None:
                    attempt.stage = "video_reference"
                    uploaded_reference_token = await composer.attach_reference_image(str(reference), cookies)
                for option in ("duration", "aspect", "format"):
                    attempt.stage = f"video_{option}"
                    await composer.configure_video_option(option, select=True)
            attempt.stage = "edit_prompt"
            await composer.focus_empty_editor(cookies)
            prompt = prompt.replace("\r\n", "\n")
            await composer.call("Input.insertText", {"text": prompt}, page=True)
            if kind == "music":
                attempt.stage = "music_options"
                await composer.wait("music_options")
                for option, value in (("duration", music_duration), ("vocals", music_vocals), ("genre", music_genre)):
                    if value is not None:
                        attempt.stage = f"music_{option}"
                        await composer.configure_music_option(option, select=True)
            attempt.stage = "model_selection"
            await composer.click(await composer.open_model_menu())
            await composer.exit_menu()
            await composer.wait("model_menu_closed")
            if thinking_level.strip().lower() not in {"standard", "标准"}:
                attempt.stage = "thinking_level"
                await composer.select_thinking_level()
                attempt.stage = "model_selection"
            model_token = await composer.verify_model_selection()
            attempt.selected_model_verified = True
            await composer.exit_menu()
            await composer.wait("model_menu_closed")
            if kind == "video":
                attempt.stage = "video_format"
                await composer.configure_video_option("format", select=False)
            music_tokens: dict[str, str] = {}
            if kind == "music":
                for option, value in (("duration", music_duration), ("vocals", music_vocals), ("genre", music_genre)):
                    if value is not None:
                        attempt.stage = f"music_{option}"
                        music_tokens[option] = await composer.configure_music_option(option, select=False)
            attempt.stage = "verify_composer"
            reference_token = await composer.wait("reference_token") if reference is not None else None
            if reference_token != uploaded_reference_token:
                raise TaskApprovalBrowserUnavailable("The native reference changed before submission.")
            baseline = await composer.evaluate("status")
            _status_items(baseline, "media")
            _status_items(baseline, "signals")
            await composer.verify_browser_account(cookies)
            await composer.wait("tool_selected")
            point = await composer.wait("send", prompt=prompt, model_token=model_token, music_tokens=music_tokens,
                                        reference_token=reference_token)
            composer.require_account()
            attempt.stage = "send"
            attempt.submitted = True
            await composer.click(point)
            attempt.stage = "observe"
            return await _observe_generation(composer, attempt, baseline, on_chat)
    except _InvalidNativeArgument as error:
        return attempt.result(error_code="INVALID_ARGUMENT", error_message=str(error))
    except TimeoutError:
        return attempt.result(error_code="TIMED_OUT")
    except _FAILURES:
        return attempt.result(error_code="UPSTREAM_CHANGED" if attempt.submitted else "CAPABILITY_UNAVAILABLE")
