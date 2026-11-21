"""Verify SDK authentication logs never emit synthetic cache credentials."""

import importlib
from pathlib import Path

from curl_cffi.requests import Cookies
from loguru import logger

from src.sdk_auth_logging import suppress_sdk_auth_logs


def test_sdk_cache_cleanup_and_read_errors_do_not_expose_paths(monkeypatch, tmp_path):
    rotate = importlib.import_module("gemini_webapi.utils.rotate_1psidts")
    access = importlib.import_module("gemini_webapi.utils.get_access_token")
    secret = "synthetic-psid-log-test"
    monkeypatch.setenv("GEMINI_COOKIE_PATH", str(tmp_path))
    jar = Cookies()
    jar.set("__Secure-1PSID", secret, domain=".google.com", secure=True)
    cache = tmp_path / f".cached_cookies_{secret}.json"
    messages = []
    sink = logger.add(messages.append, level="DEBUG", format="{message}")
    try:
        suppress_sdk_auth_logs()
        cache.write_text("[]")
        rotate.clear_cookies_cache(jar, verbose=False)
        assert not cache.exists()
        cache.write_text("[]")
        original_unlink = Path.unlink
        original_read = Path.read_text

        def fail_unlink(path, *args, **kwargs):
            if path == cache:
                raise OSError(f"failed at {path}")
            return original_unlink(path, *args, **kwargs)

        def fail_read(path, *args, **kwargs):
            if path == cache:
                raise OSError(f"failed at {path}")
            return original_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_unlink)
        monkeypatch.setattr(Path, "read_text", fail_read)
        rotate.clear_cookies_cache(jar, verbose=True)
        assert access._load_cached_jar(cache) is None
        logger.info("user logger remains available")
        output = "".join(map(str, messages))
        assert secret not in output
        assert str(cache) not in output
        assert "user logger remains available" in output
    finally:
        logger.remove(sink)
