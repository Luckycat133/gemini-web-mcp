"""Browser layout and OS access failures must produce actionable diagnostics."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.cookie_manager import CookieManager


def test_chrome_directory_denial_is_reported_without_fallback(monkeypatch, tmp_path):
    import browser_cookie3

    original_iterdir = Path.iterdir

    def denied_iterdir(path):
        if path == tmp_path:
            raise PermissionError(1, "Operation not permitted")
        return original_iterdir(path)

    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(Path, "iterdir", denied_iterdir)
    reader = MagicMock(side_effect=AssertionError("Must not substitute another profile"))
    monkeypatch.setattr(browser_cookie3, "chrome", reader)

    profiles = CookieManager.list_browser_cookie_profiles("chrome", validate=False)

    assert profiles[0]["error_code"] == "BROWSER_COOKIE_ACCESS_DENIED"
    assert "privacy settings" in profiles[0]["error"]
    reader.assert_not_called()


def test_chrome_network_cookie_layout_preserves_profile_name(monkeypatch, tmp_path):
    network = tmp_path / "Profile 1" / "Network"
    network.mkdir(parents=True)
    database = network / "Cookies"
    database.touch()
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))
    profile_reader = MagicMock(return_value=[SimpleNamespace(
        name="__Secure-1PSID", value="test-only", domain=".google.com",
    )])

    candidates = CookieManager._browser_cookie_candidates(
        SimpleNamespace(chrome=profile_reader), "chrome", lambda **kwargs: [], {"__Secure-1PSID"},
    )

    assert candidates == [("Profile 1", {"__Secure-1PSID": "test-only"})]
    profile_reader.assert_called_once_with(cookie_file=str(database), domain_name="google.com")


def test_chrome_prefers_network_database_without_duplicate_profile(monkeypatch, tmp_path):
    network = tmp_path / "Default" / "Network"
    network.mkdir(parents=True)
    (network / "Cookies").touch()
    (tmp_path / "Default" / "Cookies").touch()
    monkeypatch.setattr(CookieManager, "_chrome_base_path", staticmethod(lambda: tmp_path))
    reader = MagicMock(return_value=[SimpleNamespace(
        name="__Secure-1PSID", value="test-only", domain=".google.com",
    )])

    candidates = CookieManager._browser_cookie_candidates(
        SimpleNamespace(chrome=reader), "chrome", lambda **kwargs: [], {"__Secure-1PSID"},
    )

    assert [name for name, _ in candidates] == ["Default"]
    reader.assert_called_once_with(cookie_file=str(network / "Cookies"), domain_name="google.com")
