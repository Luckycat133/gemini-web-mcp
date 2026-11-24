"""Synthetic-only coverage of detached SDK cookie-cache ownership."""

from __future__ import annotations

import io
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from curl_cffi.requests import Cookies
from gemini_webapi.utils.rotate_1psidts import save_cookies

from src import client_manager, operation_worker


@pytest.fixture(autouse=True)
def _keep_worker_log_suppression_in_its_process(monkeypatch):
    # main() normally runs in a disposable process; do not mute later test logs.
    monkeypatch.setattr(operation_worker.logging, "disable", lambda _level: None)
    monkeypatch.setattr("src.sdk_auth_logging.suppress_sdk_auth_logs", lambda: None)


def _synthetic_cookies() -> Cookies:
    cookies = Cookies()
    cookies.set("__Secure-1PSID", "synthetic-worker-only", domain=".google.com", secure=True)
    return cookies


def _shared_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(client_manager.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(client_manager, "COOKIE_MANAGER_AVAILABLE", True)
    shared = tmp_path / "gemini_web_mcp_webapi_cookie_cache"
    shared.mkdir()
    monkeypatch.setenv("GEMINI_COOKIE_PATH", str(shared))
    return shared


def _interleave_browser_prepare(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    original_write = Path.write_text
    written: list[Path] = []

    def write_then_other_worker_prepares(path: Path, *args, **kwargs):
        result = original_write(path, *args, **kwargs)
        if path.name == ".cached_cookies_synthetic-worker-only.json":
            written.append(path)
            # Force the competing worker's initialization between SDK write/chmod.
            client_manager.prepare_browser_cookie_cache(force=True)
        return result

    monkeypatch.setattr(Path, "write_text", write_then_other_worker_prepares)
    return written


def test_shared_sdk_write_chmod_race_is_reproduced_with_synthetic_cookies(tmp_path, monkeypatch):
    shared = _shared_cache(tmp_path, monkeypatch)
    written = _interleave_browser_prepare(monkeypatch)
    with pytest.raises(FileNotFoundError):
        save_cookies(_synthetic_cookies())
    assert len(written) == 1 and written[0].parent == shared
    assert not written[0].exists()


@pytest.mark.parametrize("configured", [None, "", "explicit-user-cache"])
def test_worker_cache_restores_environment_and_removes_cookie_bytes(tmp_path, monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("GEMINI_COOKIE_PATH", raising=False)
    else:
        monkeypatch.setenv("GEMINI_COOKIE_PATH", configured)
    with client_manager.isolated_worker_cookie_cache() as cache:
        assert stat.S_IMODE(cache.stat().st_mode) == 0o700
        save_cookies(_synthetic_cookies())
        files = list(cache.iterdir())
        assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert not cache.exists()
    assert os.environ.get("GEMINI_COOKIE_PATH") == configured


def test_web_main_avoids_write_chmod_race_and_preserves_parent_cache(tmp_path, monkeypatch):
    shared = _shared_cache(tmp_path, monkeypatch)
    parent_file = shared / ".cached_cookies_parent-synthetic.json"
    parent_file.write_text("synthetic parent cache")
    written = _interleave_browser_prepare(monkeypatch)
    payload = {"kind": "media", "auth_cookies": {"__Secure-1PSID": "synthetic-ipc-only"}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    async def fake_worker(received):
        assert received == payload  # Complete authentication IPC is unchanged.
        save_cookies(_synthetic_cookies())
        assert written[0].exists()
        assert written[0].parent != shared

    monkeypatch.setattr(operation_worker, "run_worker", fake_worker)
    operation_worker.main()
    assert parent_file.read_text() == "synthetic parent cache"
    assert len(written) == 1 and not written[0].parent.exists()
    assert os.environ["GEMINI_COOKIE_PATH"] == str(shared)


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_web_main_cleans_cache_on_error_or_interrupt(tmp_path, monkeypatch, failure):
    parent = tmp_path / "explicit-user-cache"
    monkeypatch.setenv("GEMINI_COOKIE_PATH", str(parent))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"kind":"task_resume"}'))
    seen = []

    async def fake_worker(_payload):
        cache = Path(os.environ["GEMINI_COOKIE_PATH"])
        seen.append(cache)
        save_cookies(_synthetic_cookies())
        raise failure()

    monkeypatch.setattr(operation_worker, "run_worker", fake_worker)
    with pytest.raises(KeyboardInterrupt if failure is KeyboardInterrupt else SystemExit):
        operation_worker.main()
    assert len(seen) == 1 and not seen[0].exists()
    assert os.environ["GEMINI_COOKIE_PATH"] == str(parent)
    assert not parent.exists()


@pytest.mark.parametrize("kind", ["omni_video", "omni_video_resume"])
def test_omni_main_keeps_existing_cache_environment_without_web_context(monkeypatch, kind):
    monkeypatch.setenv("GEMINI_COOKIE_PATH", "explicit-api-parent-cache")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"kind": kind})))

    def forbidden_context():
        pytest.fail("API-only worker must not create a Web cache")

    async def fake_worker(payload):
        assert payload == {"kind": kind}
        assert os.environ["GEMINI_COOKIE_PATH"] == "explicit-api-parent-cache"

    monkeypatch.setattr(client_manager, "isolated_worker_cookie_cache", forbidden_context)
    monkeypatch.setattr(operation_worker, "run_worker", fake_worker)
    operation_worker.main()


_PROCESS_BOOTSTRAP = """
import asyncio, json, os, sys
from pathlib import Path
from curl_cffi.requests import Cookies
from gemini_webapi.utils.rotate_1psidts import save_cookies
from src import client_manager, operation_worker
marker, release = map(Path, sys.argv[1:])
async def synthetic_worker(payload):
    client_manager.prepare_browser_cookie_cache(force=True)
    cookies = Cookies()
    cookies.set('__Secure-1PSID', 'synthetic-worker-only', domain='.google.com', secure=True)
    save_cookies(cookies)
    marker.write_text(os.environ['GEMINI_COOKIE_PATH'])
    while not release.exists():
        await asyncio.sleep(.02)
operation_worker.run_worker = synthetic_worker
operation_worker.main()
"""


def _wait_for_markers(processes, markers):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if all(marker.exists() for marker in markers):
            return
        assert all(process.poll() is None for process in processes)
        time.sleep(0.02)
    pytest.fail("Synthetic workers did not initialize")


@pytest.mark.parametrize("interrupt", [False, True])
def test_two_real_worker_processes_isolate_caches_and_clean_on_exit(tmp_path, interrupt):
    parent = tmp_path / "parent-cache"
    parent.mkdir()
    original = parent / ".cached_cookies_parent-synthetic.json"
    original.write_text("synthetic parent cache")
    env = dict(os.environ, GEMINI_COOKIE_PATH=str(parent))
    release = tmp_path / "release"
    markers = [tmp_path / f"worker-{index}" for index in range(2)]
    processes = [subprocess.Popen(
        [sys.executable, "-c", _PROCESS_BOOTSTRAP, str(marker), str(release)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ) for marker in markers]
    try:
        for process in processes:
            assert process.stdin is not None
            process.stdin.write(b'{"kind":"media"}')
            process.stdin.close()
        _wait_for_markers(processes, markers)
        caches = [Path(marker.read_text()) for marker in markers]
        assert caches[0] != caches[1]
        for cache in caches:
            assert cache != parent and stat.S_IMODE(cache.stat().st_mode) == 0o700
            cookie_file = cache / ".cached_cookies_synthetic-worker-only.json"
            assert cookie_file.is_file() and stat.S_IMODE(cookie_file.stat().st_mode) == 0o600
        assert original.read_text() == "synthetic parent cache"
        if interrupt:
            for process in processes:
                process.send_signal(signal.SIGINT)
        else:
            release.touch()
        for process in processes:
            result = process.wait(timeout=10)
            assert result != 0 if interrupt else result == 0
        assert all(not cache.exists() for cache in caches)
        assert original.read_text() == "synthetic parent cache"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
