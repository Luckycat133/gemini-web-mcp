"""Task approval browser tests use fake cookies and local HTML exclusively."""

import asyncio
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock

import pytest

from src.infrastructure import task_approval_browser as browser


def test_gemini_browser_session_preserves_sign_in_cookie_scopes():
    browser_auth = {"APISID", "SAPISID", "SID", "__Secure-1PAPISID", "__Secure-3PAPISID"}
    parent_domain_cookies = browser_auth | {"__Secure-1PSID", "__Secure-1PSIDTS", "HSID", "SSID"}
    cookies = {name: "synthetic" for name in parent_domain_cookies | {"LSID", "OSID", "COMPASS"}}
    params = browser._cookie_parameters(cookies)
    by_name = {cookie["name"]: cookie for cookie in params}
    assert set(by_name) == set(cookies)
    assert all(by_name[name]["domain"] == ".google.com" and "url" not in by_name[name] for name in parent_domain_cookies)
    assert by_name["LSID"]["url"] == "https://accounts.google.com/"
    assert by_name["OSID"]["url"] == "https://gemini.google.com/"
    assert by_name["COMPASS"]["url"] == "https://gemini.google.com/"
    assert all("domain" not in by_name[name] for name in {"LSID", "OSID", "COMPASS"})
    assert {cookie["name"] for cookie in params if not cookie["httpOnly"]} == browser_auth
    assert all(cookie["secure"] and cookie["path"] == "/" and "expires" not in cookie for cookie in params)


def test_browser_cookie_conversion_does_not_add_unprovided_auth_cookies():
    params = browser._cookie_parameters({"__Secure-1PSID": "synthetic", "__Secure-3PSIDTS": "synthetic"})
    assert {cookie["name"] for cookie in params} == {"__Secure-1PSID", "__Secure-3PSIDTS"}
    assert all(cookie["domain"] == ".google.com" and cookie["secure"] and cookie["httpOnly"] for cookie in params)


def test_manual_google_verification_cookie_keeps_owner_scope_and_browser_readable_flag():
    cookies = {"__Secure-1PSID": "synthetic-same-owner", "GOOGLE_ABUSE_EXEMPTION": "synthetic-manual-verification"}
    by_name = {cookie["name"]: cookie for cookie in browser._cookie_parameters(cookies)}
    assert set(by_name) == set(cookies)
    assert by_name["__Secure-1PSID"]["value"] == "synthetic-same-owner"
    assert by_name["__Secure-1PSID"]["httpOnly"] is True
    verified = by_name["GOOGLE_ABUSE_EXEMPTION"]
    assert verified["domain"] == ".google.com" and verified["path"] == "/" and verified["secure"] is True
    assert verified["httpOnly"] is False and "url" not in verified


def test_manual_google_verification_cookie_alone_is_not_a_gemini_login():
    with pytest.raises(browser.TaskApprovalBrowserUnavailable, match="no usable session"):
        browser._cookie_parameters({"GOOGLE_ABUSE_EXEMPTION": "synthetic-manual-verification"})


@pytest.mark.parametrize("url", [
    "https://gemini.google.com/app/fixture?authuser=1",
    "https://gemini.google.com@invalid.example/app/fixture",
    "https://gemini.google.com/app/fixture/other",
])
def test_rejects_noncanonical_browser_task_targets(url, monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(browser, "_run_browser", run)
    with pytest.raises(browser.TaskApprovalBrowserUnavailable, match="canonical Gemini Task URL"):
        asyncio.run(browser.run_task_approval_browser(url, {}, "() => ({})", {}))
    run.assert_not_called()


@pytest.mark.skipif(sys.platform != "darwin" or not browser._MAC_CHROME.is_file(), reason="local Chrome smoke")
@pytest.mark.parametrize("claim", [True, False])
def test_native_click_fixture_checks_claim_and_reaps_chrome(tmp_path, monkeypatch, claim):
    page = tmp_path / "card.html"
    page.write_text('<button onclick="this.dataset.count=Number(this.dataset.count)+1" data-count="0">Allow</button>')
    started = []
    spawn = browser._spawn_chrome

    def observe_spawn(executable, profile, command_read, response_write):
        pid = spawn(executable, profile, command_read, response_write)
        started.append((pid, Path(profile)))
        return pid

    monkeypatch.setattr(browser, "_spawn_chrome", observe_spawn)
    before_execute = AsyncMock(return_value=claim)
    result = asyncio.run(browser._run_browser(
        str(browser._MAC_CHROME), page.as_uri(), [],
        "({label}) => { const button = document.querySelector('button');"
        "if (button.textContent !== label) throw new Error();"
        "button.click(); return {status: 'clicked', count: Number(button.dataset.count)}; }",
        {"label": "Allow"}, timeout=15, before_execute=before_execute,
    ))
    before_execute.assert_awaited_once_with()
    assert result == ({"status": "clicked", "count": 1} if claim else {"status": "skipped"})
    (pid, profile), = started
    assert not profile.exists()
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
