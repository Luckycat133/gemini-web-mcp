"""Execute one guarded browser action in an isolated, short-lived Chrome context."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, suppress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
from typing import Any, BinaryIO


class TaskApprovalBrowserUnavailable(RuntimeError):
    """The browser action has no verified outcome. Do not retry it automatically."""


_GEMINI_TASK_URL = re.compile(r"https://gemini\.google\.com/app/[A-Za-z0-9_-]{1,128}")
_MAC_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
_MAX_CDP_MESSAGE = 1024 * 1024
_MAX_RESULT_BYTES = 4096
# Google's page code uses these browser-readable cookies for request authentication.
_BROWSER_READABLE_AUTH_COOKIES = frozenset({
    "APISID", "SAPISID", "SID", "__Secure-1PAPISID", "__Secure-3PAPISID", "GOOGLE_ABUSE_EXEMPTION",
})
_BROWSER_ENV = frozenset({
    "HOME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "USER", "LOGNAME",
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
})


def _chrome_executable() -> str:
    if os.name != "posix":
        raise TaskApprovalBrowserUnavailable("The Task approval browser requires macOS or Linux.")
    configured = os.getenv("GEMINI_TASK_APPROVAL_CHROME", "").strip()
    if configured:
        executable = shutil.which(str(Path(configured).expanduser()))
    elif _MAC_CHROME.is_file() and os.access(_MAC_CHROME, os.X_OK):
        executable = str(_MAC_CHROME)
    else:
        executable = shutil.which("google-chrome") or shutil.which("chromium")
    if not executable:
        raise TaskApprovalBrowserUnavailable("Chrome is not available for the Task approval browser.")
    return executable


def _cookie_parameters(cookies: Mapping[str, str]) -> list[dict[str, Any]]:
    from ..cookie_manager import gemini_cookie_jar

    if (not cookies.get("__Secure-1PSID") or len(cookies) > 128
            or any(not name or len(name) > 256 or len(value) > 8192 for name, value in cookies.items())):
        raise TaskApprovalBrowserUnavailable("The Task approval browser has no usable session.")
    jar = gemini_cookie_jar(dict(cookies))
    scoped = ((name, value, ".google.com") for name, value in jar.items()) if isinstance(jar, dict) else (
        (cookie.name, cookie.value, cookie.domain) for cookie in jar.jar
    )
    result: list[dict[str, Any]] = []
    for name, value, domain in scoped:
        # A URL sets a host-only cookie. The leading dot is reserved for
        # credentials already scoped to the parent Google domain.
        scope = {"domain": domain} if domain.startswith(".") else {"url": f"https://{domain}/"}
        result.append({
            "name": name, "value": value, "path": "/", "secure": True,
            "httpOnly": name not in _BROWSER_READABLE_AUTH_COOKIES, **scope,
        })
    return result


class _DevToolsPipe:
    """Null-delimited CDP messages over child-only file descriptors 3 and 4."""

    def __init__(self, reader: asyncio.StreamReader, write_fd: int):
        self.reader = reader
        self.write_fd = write_fd
        self.message_id = 0

    async def _send(self, message: bytes) -> None:
        loop = asyncio.get_running_loop()
        remaining = memoryview(message)
        while remaining:
            try:
                remaining = remaining[os.write(self.write_fd, remaining):]
            except BlockingIOError:
                ready: asyncio.Future[None] = loop.create_future()

                def writable() -> None:
                    if not ready.done():
                        ready.set_result(None)

                loop.add_writer(self.write_fd, writable)
                try:
                    await ready
                finally:
                    loop.remove_writer(self.write_fd)

    async def call(self, method: str, params: dict[str, Any] | None = None,
                   *, session: str | None = None) -> dict[str, Any]:
        self.message_id += 1
        request: dict[str, Any] = {"id": self.message_id, "method": method}
        if params is not None:
            request["params"] = params
        if session is not None:
            request["sessionId"] = session
        await self._send(json.dumps(request, allow_nan=False).encode() + b"\0")
        while True:
            message = json.loads((await self.reader.readuntil(b"\0"))[:-1])
            if not isinstance(message, dict) or message.get("id") != self.message_id:
                continue
            result = message.get("result")
            if "error" in message or not isinstance(result, dict):
                raise TaskApprovalBrowserUnavailable("Chrome did not verify a Task browser command.")
            return result


def _spawn_chrome(
    executable: str, profile: str, command_read: int, response_write: int, *, stealth: bool = False,
) -> int:
    import fcntl

    # Move the source FDs above both fixed destination FDs before dup2. This
    # works without a subprocess preexec callback in the threaded MCP server.
    pipe_read = fcntl.fcntl(command_read, fcntl.F_DUPFD_CLOEXEC, 5)
    try:
        pipe_write = fcntl.fcntl(response_write, fcntl.F_DUPFD_CLOEXEC, 5)
        try:
            actions = (
                (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
                (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
                (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0),
                (os.POSIX_SPAWN_DUP2, pipe_read, 3),
                (os.POSIX_SPAWN_DUP2, pipe_write, 4),
            )
            headless_flag = "--headless=new" if stealth else "--headless"
            args = [executable, headless_flag, "--remote-debugging-pipe", f"--user-data-dir={profile}",
                    "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
                    "--disable-component-update", "--disable-sync", "--disable-breakpad"]
            if stealth:
                args.append("--disable-blink-features=AutomationControlled")
            args.append("about:blank")
            env = {name: value for name, value in os.environ.items() if name in _BROWSER_ENV}
            return os.posix_spawn(executable, args, env, file_actions=actions, setpgroup=0)
        finally:
            os.close(pipe_write)
    finally:
        os.close(pipe_read)


async def _stop_chrome(pid: int) -> None:
    with suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)
    with suppress(ChildProcessError):
        for _ in range(20):
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
            await asyncio.sleep(0.05)
        with suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
        await asyncio.to_thread(os.waitpid, pid, 0)


@asynccontextmanager
async def _open_browser(executable: str, *, stealth: bool = False) -> AsyncIterator[_DevToolsPipe]:
    with tempfile.TemporaryDirectory(prefix="gemini-task-approval-") as profile:
        command_read, command_write = os.pipe()
        response_read, response_write = os.pipe()
        pid = None
        response_file: BinaryIO | None = None
        read_transport: asyncio.ReadTransport | None = None
        try:
            try:
                pid = (
                    _spawn_chrome(executable, profile, command_read, response_write, stealth=stealth)
                    if stealth
                    else _spawn_chrome(executable, profile, command_read, response_write)
                )
            finally:
                os.close(command_read)
                os.close(response_write)
            os.set_blocking(command_write, False)
            reader = asyncio.StreamReader(limit=_MAX_CDP_MESSAGE)
            loop = asyncio.get_running_loop()
            response_file = os.fdopen(response_read, "rb", buffering=0)
            read_transport, _ = await loop.connect_read_pipe(
                lambda: asyncio.StreamReaderProtocol(reader), response_file,
            )
            yield _DevToolsPipe(reader, command_write)
        finally:
            os.close(command_write)
            if read_transport is not None:
                read_transport.close()
            elif response_file is not None:
                response_file.close()
            else:
                os.close(response_read)
            if pid is not None:
                await _stop_chrome(pid)


async def _ready_context(pipe: _DevToolsPipe, url: str, session: str) -> int:
    while True:
        tree = await pipe.call("Page.getFrameTree", session=session)
        frame = tree["frameTree"]["frame"]
        if frame["url"] == url:
            try:
                world = await pipe.call("Page.createIsolatedWorld", {
                    "frameId": frame["id"], "worldName": "gemini-task-approval",
                }, session=session)
                context_id = world["executionContextId"]
                ready = await pipe.call("Runtime.evaluate", {
                    "contextId": context_id, "returnByValue": True,
                    "expression": f"location.href === {json.dumps(url)} && document.readyState !== 'loading'",
                }, session=session)
                ready_value = ready.get("result")
                if type(context_id) is int and isinstance(ready_value, dict) and ready_value.get("value") is True:
                    return context_id
            except TaskApprovalBrowserUnavailable:
                # A navigation may replace the read-only world before it is ready.
                pass
        await asyncio.sleep(0.1)


async def _run_browser(executable: str, url: str, cookie_params: list[dict[str, Any]], function: str,
                       argument: Mapping[str, object], *, timeout: float,
                       before_execute: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
    async with asyncio.timeout(timeout), _open_browser(executable) as pipe:
        context = await pipe.call("Target.createBrowserContext")
        context_id = context["browserContextId"]
        if cookie_params:
            await pipe.call("Storage.setCookies", {"browserContextId": context_id, "cookies": cookie_params})
        target = await pipe.call("Target.createTarget", {"url": "about:blank", "browserContextId": context_id})
        attached = await pipe.call("Target.attachToTarget", {"targetId": target["targetId"], "flatten": True})
        session = attached["sessionId"]
        navigation = await pipe.call("Page.navigate", {"url": url}, session=session)
        if navigation.get("errorText") or navigation.get("isDownload"):
            raise TaskApprovalBrowserUnavailable("The Task page could not be opened in Chrome.")
        world_id = await _ready_context(pipe, url, session)
        expression = f"""(async () => {{
            if (location.href !== {json.dumps(url)} || window !== window.top) {{
                return JSON.stringify({{status: "skipped"}});
            }}
            const result = await ({function})({json.dumps(dict(argument), allow_nan=False)});
            const encoded = JSON.stringify(result);
            return typeof encoded === "string" && encoded.length <= {_MAX_RESULT_BYTES} ? encoded : null;
        }})()"""
        if before_execute is not None and await before_execute() is not True:
            return {"status": "skipped"}
        reply = await pipe.call("Runtime.evaluate", {
            "expression": expression, "contextId": world_id, "awaitPromise": True, "returnByValue": True,
        }, session=session)
        value = reply.get("result")
        encoded = value.get("value") if isinstance(value, dict) else None
        if not isinstance(encoded, str) or len(encoded.encode()) > _MAX_RESULT_BYTES or "exceptionDetails" in reply:
            raise TaskApprovalBrowserUnavailable("The Task browser action has no verified result.")
        result = json.loads(encoded)
        if not isinstance(result, dict):
            raise TaskApprovalBrowserUnavailable("The Task browser action has no verified result.")
        return result


async def run_task_approval_browser(url: str, cookies: Mapping[str, str], function: str,
                                    argument: Mapping[str, object], *, timeout: float = 25.0,
                                    before_execute: Callable[[], Awaitable[bool]] | None = None) -> dict[str, Any]:
    """Run a trusted function once on the exact Task page after an optional fresh claim."""
    if not _GEMINI_TASK_URL.fullmatch(url):
        raise TaskApprovalBrowserUnavailable("The approval target is not a canonical Gemini Task URL.")
    try:
        return await _run_browser(_chrome_executable(), url, _cookie_parameters(cookies), function, argument,
                                  timeout=timeout, before_execute=before_execute)
    except (OSError, KeyError, ValueError, TypeError, EOFError, TimeoutError, asyncio.LimitOverrunError):
        # CDP errors can include arbitrary page text or request details.
        raise TaskApprovalBrowserUnavailable("The Task browser action has no verified result.") from None
