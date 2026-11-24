"""Submit and confirm Deep Research through Gemini's native browser composer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib.resources import files
import json
import math
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from ..constants import resolve_thinking_level_id
from .task_approval_browser import (
    TaskApprovalBrowserUnavailable, _DevToolsPipe, _chrome_executable, _cookie_parameters, _open_browser, _ready_context,
)

_COMPOSER_URL = "https://gemini.google.com/app"
_CDP_TIMEOUT = 12
_NAVIGATION_TIMEOUT = 60
_UI_TIMEOUT = 45
_TOOLS_MENU_OPEN_TIMEOUT = 8.0
_MODEL_MENU_OPEN_TIMEOUT = 2.0
_POLL_INTERVAL = 0.4
_CHAT_PATH = re.compile(r"/app/([a-f0-9]{16})")
_FAILURES = (
    TaskApprovalBrowserUnavailable, OSError, KeyError, ValueError, TypeError, EOFError, asyncio.LimitOverrunError,
)


@dataclass(frozen=True)
class NativeResearchObservation:
    state: Literal["started", "rejected", "unverified"] = "unverified"
    chat_id: str | None = None
    title: str | None = None
    submitted: bool = False
    confirmed: bool = False
    error_code: str | None = None
    error_message: str | None = None


def _gemini_path(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "gemini.google.com" or parsed.fragment:
        return None
    return parsed.path


class _ResearchComposer:
    def __init__(
        self,
        pipe: _DevToolsPipe,
        script: str,
        model: str,
        thinking_level: str,
        account_matches: Callable[[], bool],
    ) -> None:
        self.pipe = pipe
        self.script = script
        self.model = model
        self.thinking_level = thinking_level
        self.account_matches = account_matches
        self.session: str | None = None
        self.world: int | None = None
        self.frame_url: str | None = None
        self.browser_context: str | None = None

    def require_account(self) -> None:
        if self.account_matches() is not True:
            raise TaskApprovalBrowserUnavailable("The account changed during native Deep Research.")

    async def call(self, method: str, params: dict[str, Any] | None = None, *, page: bool = False) -> dict[str, Any]:
        self.require_account()
        value = await asyncio.wait_for(
            self.pipe.call(method, params, session=self.session if page else None),
            _NAVIGATION_TIMEOUT if method == "Page.navigate" else _CDP_TIMEOUT,
        )
        self.require_account()
        return value

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
            self.world = await asyncio.wait_for(_ready_context(self.pipe, self.frame_url, self.session), _CDP_TIMEOUT)
            self.require_account()

    async def evaluate(self, action: str, **values: Any) -> Any:
        await self.refresh_world()
        arguments = {
            "action": action,
            "model": self.model,
            "thinking_level": self.thinking_level,
            **values,
        }
        reply = await self.call(
            "Runtime.evaluate",
            {
                "expression": f"({self.script})({json.dumps(arguments, allow_nan=False)})",
                "contextId": self.world,
                "awaitPromise": True,
                "returnByValue": True,
            },
            page=True,
        )
        result = reply.get("result")
        if "exceptionDetails" in reply or not isinstance(result, dict):
            raise TaskApprovalBrowserUnavailable("The native Deep Research composer returned an unverified result.")
        return result.get("value")

    async def wait(self, action: str, *, timeout: float = _UI_TIMEOUT, **values: Any) -> Any:
        async with asyncio.timeout(timeout):
            while True:
                result = await self.evaluate(action, **values)
                if result:
                    return result
                if action in {"tools", "mode", "send"}:
                    if point := await self.evaluate("tooltip_dismiss"):
                        if isinstance(point, dict) and point.get("click") is True:
                            await self.click(point)
                        else:
                            await self.hover(point)
                await asyncio.sleep(_POLL_INTERVAL)

    @staticmethod
    def checked_point(point: Any) -> tuple[float, float]:
        if not isinstance(point, dict) or not all(
            type(point.get(axis)) in (int, float) and math.isfinite(point[axis]) and point[axis] >= 0
            for axis in ("x", "y")
        ):
            raise TaskApprovalBrowserUnavailable("The expected Gemini Deep Research control was not verified.")
        return float(point["x"]), float(point["y"])

    async def hover(self, point: Any) -> None:
        x, y = self.checked_point(point)
        await self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, page=True)

    async def click(self, point: Any) -> None:
        x, y = self.checked_point(point)
        await self.hover(point)
        for event, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
            await self.call(
                "Input.dispatchMouseEvent",
                {"type": event, "x": x, "y": y, "button": "left", "buttons": buttons, "clickCount": 1},
                page=True,
            )

    async def exit_menu(self) -> None:
        for event in ("keyDown", "keyUp"):
            await self.call(
                "Input.dispatchKeyEvent",
                {"type": event, "key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
                page=True,
            )

    async def navigate(self, cookies: Mapping[str, str]) -> None:
        context = await self.call("Target.createBrowserContext")
        self.browser_context = context["browserContextId"]
        await self.call(
            "Storage.setCookies",
            {"browserContextId": self.browser_context, "cookies": _cookie_parameters(cookies)},
        )
        target = await self.call("Target.createTarget", {"url": "about:blank", "browserContextId": self.browser_context})
        attached = await self.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        self.session = attached["sessionId"]
        try:
            version = await self.call("Browser.getVersion")
            ua = str(version.get("userAgent") or "")
            if "HeadlessChrome/" in ua:
                await self.call(
                    "Emulation.setUserAgentOverride",
                    {"userAgent": ua.replace("HeadlessChrome/", "Chrome/"), "platform": "MacIntel"},
                    page=True,
                )
        except Exception:
            pass
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1280, "height": 900, "deviceScaleFactor": 1, "mobile": False},
            page=True,
        )
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

    async def select_deep_research_tool(self) -> None:
        async with asyncio.timeout(_UI_TIMEOUT):
            for attempt in range(2):
                await self.click(await self.wait("tools"))
                try:
                    async with asyncio.timeout(_TOOLS_MENU_OPEN_TIMEOUT):
                        hovered_more = False
                        clicked_more = False
                        while True:
                            if point := await self.evaluate("research_tool"):
                                await self.click(point)
                                await self.wait("tool_selected", timeout=5.0)
                                return
                            if more_point := await self.evaluate("more"):
                                if not hovered_more:
                                    await self.hover(more_point)
                                    hovered_more = True
                                    await asyncio.sleep(0.5)
                                    continue
                                if not clicked_more:
                                    await self.click(more_point)
                                    clicked_more = True
                                    await asyncio.sleep(0.5)
                                    continue
                            await asyncio.sleep(_POLL_INTERVAL)
                except TimeoutError:
                    if not attempt and await self.evaluate("composer_menu_closed") is True:
                        continue
                    if more_point := await self.evaluate("more"):
                        await self.click(more_point)
                    if point := await self.wait("research_tool", timeout=6.0):
                        await self.click(point)
                        await self.wait("tool_selected", timeout=5.0)
                        return
                    raise
        raise TimeoutError

    async def configure_model_and_thinking(self) -> str | None:
        if not self.model and self.thinking_level == "low":
            return None
        try:
            async with asyncio.timeout(_MODEL_MENU_OPEN_TIMEOUT * 6):
                await self.click(await self.wait("mode"))
                opened = await self.wait("model_menu_open", timeout=_MODEL_MENU_OPEN_TIMEOUT)
                if isinstance(opened, dict) and "x" in opened and "y" in opened:
                    await self.click(opened)
                await self.exit_menu()
                await self.wait("model_menu_closed", timeout=_MODEL_MENU_OPEN_TIMEOUT)
                if self.thinking_level != "low":
                    await self.click(await self.wait("mode"))
                    await self.hover(await self.wait("thinking_menu", timeout=_MODEL_MENU_OPEN_TIMEOUT))
                    await self.click(await self.wait("thinking_target", timeout=_MODEL_MENU_OPEN_TIMEOUT))
                    await self.exit_menu()
                    await self.exit_menu()
                    await self.wait("model_menu_closed", timeout=_MODEL_MENU_OPEN_TIMEOUT)
                await self.click(await self.wait("mode"))
                token = await self.wait("model_checked", timeout=_MODEL_MENU_OPEN_TIMEOUT)
                await self.exit_menu()
                await self.wait("model_menu_closed", timeout=_MODEL_MENU_OPEN_TIMEOUT)
                return token if isinstance(token, str) else None
        except TimeoutError:
            return None


async def run_native_research_browser(
    query: str,
    model_label: str,
    cookies: Mapping[str, str],
    *,
    timeout: float,
    account_matches: Callable[[], bool],
    thinking_level: str = "standard",
    on_chat: Callable[[str], Awaitable[None]] | None = None,
    claim_confirmation: Callable[[str], bool] | None = None,
) -> NativeResearchObservation:
    """Start Deep Research and click confirm in a real browser so BotGuard succeeds."""
    level = {1: "low", 4: "medium", 2: "high"}.get(resolve_thinking_level_id(thinking_level) or 0) or "low"
    if not query.strip() or not math.isfinite(timeout) or timeout <= 0:
        return NativeResearchObservation(error_code="INVALID_ARGUMENT")
    submitted = False
    confirm_clicked = False
    observed_cid: str | None = None
    observed_title: str | None = None
    try:
        if account_matches() is not True:
            raise TaskApprovalBrowserUnavailable("The Deep Research account could not be verified.")
        script = files("src").joinpath("data/native_research_browser.js").read_text(encoding="utf-8")
        async with asyncio.timeout(timeout), _open_browser(_chrome_executable(), stealth=True) as pipe:
            composer = _ResearchComposer(pipe, script, model_label.strip(), level, account_matches)
            await composer.navigate(cookies)
            await composer.select_deep_research_tool()
            model_token = await composer.configure_model_and_thinking()
            await composer.click(await composer.wait("editor"))
            await composer.wait("editor_focused")
            clean_query = query.replace("\r\n", "\n")
            await composer.call("Input.insertText", {"text": clean_query}, page=True)
            send_point = await composer.wait("send", prompt=clean_query, model_token=model_token)
            composer.require_account()
            submitted = True
            await composer.click(send_point)

            plan_wait_started = asyncio.get_running_loop().time()
            plan_wait_limit = min(float(timeout), 60.0)
            while True:
                if not confirm_clicked and asyncio.get_running_loop().time() - plan_wait_started > plan_wait_limit:
                    raise TimeoutError
                path = _gemini_path(await composer.url())
                match = _CHAT_PATH.fullmatch(path or "")
                if match:
                    cid = "c_" + match.group(1)
                    if observed_cid is None:
                        observed_cid = cid
                        if on_chat is not None:
                            await asyncio.wait_for(on_chat(cid), _CDP_TIMEOUT)
                            composer.require_account()
                    elif observed_cid != cid:
                        raise TaskApprovalBrowserUnavailable("The Deep Research conversation changed.")
                elif path not in ("/app", "/app/") or observed_cid is not None:
                    raise TaskApprovalBrowserUnavailable("The Deep Research conversation could not be verified.")

                status = await composer.evaluate("status")
                if isinstance(status, dict):
                    if isinstance(status.get("plan_title"), str) and status["plan_title"].strip():
                        observed_title = status["plan_title"].strip()
                    confirm_btn = status.get("confirm_button")
                    if observed_cid and confirm_btn and not confirm_clicked:
                        if claim_confirmation is not None and not claim_confirmation(observed_cid):
                            return NativeResearchObservation(
                                state="started",
                                chat_id=observed_cid,
                                title=observed_title,
                                submitted=True,
                                confirmed=False,
                            )
                        await composer.click(confirm_btn)
                        confirm_clicked = True
                        await asyncio.sleep(_POLL_INTERVAL)
                        continue
                    if confirm_clicked and (not status.get("confirm_present") or status.get("progress")):
                        return NativeResearchObservation(
                            state="started",
                            chat_id=observed_cid,
                            title=observed_title,
                            submitted=True,
                            confirmed=True,
                        )
                    if status.get("progress") and observed_cid:
                        return NativeResearchObservation(
                            state="started",
                            chat_id=observed_cid,
                            title=observed_title,
                            submitted=True,
                            confirmed=True,
                        )
                    if isinstance(status.get("refusal"), str) and status["refusal"]:
                        return NativeResearchObservation(
                            state="rejected",
                            chat_id=observed_cid,
                            submitted=submitted,
                            confirmed=confirm_clicked,
                            error_code="UPSTREAM_REJECTED",
                            error_message=status["refusal"],
                        )
                await asyncio.sleep(_POLL_INTERVAL)
    except TimeoutError:
        if confirm_clicked and observed_cid:
            return NativeResearchObservation(
                state="started",
                chat_id=observed_cid,
                title=observed_title,
                submitted=submitted,
                confirmed=True,
            )
        return NativeResearchObservation(
            state="unverified",
            chat_id=observed_cid,
            title=observed_title,
            submitted=submitted,
            confirmed=confirm_clicked,
            error_code="TIMED_OUT",
        )
    except _FAILURES as exc:
        return NativeResearchObservation(
            state="unverified",
            chat_id=observed_cid,
            title=observed_title,
            submitted=submitted,
            error_code="UPSTREAM_CHANGED" if submitted else "CAPABILITY_UNAVAILABLE",
            error_message=str(exc),
        )
