"""Bounded retries repeat only read-only network failures, never auth decisions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi.constants import AccountStatus
from gemini_webapi.exceptions import AuthError, TemporarilyBlockedError

import src.cookie_manager as cookie_module
from src.cookie_manager import CookieManager


@pytest.fixture
def probe_factory(monkeypatch):
    clients = []
    candidates = []
    outcomes = []

    def create(cookies):
        candidates.append(dict(cookies))
        outcome = outcomes[len(clients)]
        client = SimpleNamespace(account_status=None, close=AsyncMock())

        async def initialize(**_options):
            if isinstance(outcome, BaseException):
                raise outcome
            client.account_status = outcome
            if outcome == AccountStatus.AVAILABLE:
                client.verified_browser_cookies = {**cookies, "OSID": "verified-session"}

        client.init = AsyncMock(side_effect=initialize)
        clients.append(client)
        return client

    monkeypatch.setattr(CookieManager, "_make_browser_cookie_probe_client", staticmethod(create))
    return SimpleNamespace(clients=clients, candidates=candidates, outcomes=outcomes)


def run_probe():
    return asyncio.run(CookieManager._verify_cookie_candidates([
        ("browser_chrome:Default", {"__Secure-1PSID": "candidate"}),
    ]))


@pytest.mark.parametrize("error", [ConnectionError("reset"), RuntimeError("curl: SSL_connect failed")])
def test_network_failure_retries_once_with_fresh_client_and_verified_jar(probe_factory, error):
    probe_factory.outcomes.extend([error, AccountStatus.AVAILABLE])

    result = run_probe()

    assert result.cookies == {"__Secure-1PSID": "candidate", "OSID": "verified-session"}
    assert result.source == "browser_chrome:Default"
    assert len(probe_factory.clients) == 2
    assert probe_factory.clients[0] is not probe_factory.clients[1]
    assert probe_factory.candidates == [{"__Secure-1PSID": "candidate"}] * 2
    for client in probe_factory.clients:
        client.close.assert_awaited_once()
        options = client.init.call_args.kwargs
        assert 0 < options["timeout"] <= 30
        assert {key: options[key] for key in ("auto_close", "auto_refresh", "verbose")} == {
            "auto_close": False, "auto_refresh": False, "verbose": False,
        }


def test_persistent_network_failure_stops_after_second_probe(probe_factory):
    probe_factory.outcomes.extend([ConnectionError("reset"), ConnectionError("reset")])

    result = run_probe()

    assert not result.cookies
    assert result.reason == "BROWSER_COOKIE_AUTH_UNVERIFIED"
    assert len(probe_factory.clients) == 2
    for client in probe_factory.clients:
        client.close.assert_awaited_once()


@pytest.mark.parametrize("outcome,reason", [
    (None, "BROWSER_COOKIE_AUTH_UNVERIFIED"),
    (AccountStatus.UNAUTHENTICATED, "BROWSER_COOKIE_AUTH_REJECTED"),
    (AuthError("network auth rejected"), "BROWSER_COOKIE_AUTH_REJECTED"),
    (PermissionError("network access denied"), "BROWSER_COOKIE_AUTH_UNVERIFIED"),
    (ValueError("network invalid argument"), "BROWSER_COOKIE_AUTH_UNVERIFIED"),
    (TimeoutError("network timeout"), "BROWSER_COOKIE_AUTH_UNVERIFIED"),
    (RuntimeError("unknown failure"), "BROWSER_COOKIE_AUTH_UNVERIFIED"),
])
def test_non_network_exception_or_auth_decision_never_retries(probe_factory, outcome, reason):
    probe_factory.outcomes.append(outcome)

    result = run_probe()

    assert not result.cookies
    assert result.reason == reason
    assert len(probe_factory.clients) == 1
    probe_factory.clients[0].close.assert_awaited_once()


def test_retry_uses_original_deadline_including_first_client_cleanup(monkeypatch, probe_factory):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(cookie_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    probe_factory.outcomes.extend([ConnectionError("reset"), AccountStatus.AVAILABLE])
    original_factory = CookieManager._make_browser_cookie_probe_client

    def create(cookies):
        client = original_factory(cookies)
        if len(probe_factory.clients) == 1:
            async def fail(**_options):
                clock.now += 20
                raise ConnectionError("reset")

            async def close():
                clock.now += 4

            client.init = AsyncMock(side_effect=fail)
            client.close = AsyncMock(side_effect=close)
        return client

    monkeypatch.setattr(CookieManager, "_make_browser_cookie_probe_client", staticmethod(create))

    result = run_probe()

    assert result.cookies
    assert [client.init.call_args.kwargs["timeout"] for client in probe_factory.clients] == [30, 6]
    for client in probe_factory.clients:
        client.close.assert_awaited_once()


def test_exhausted_candidate_deadline_does_not_create_second_client(monkeypatch, probe_factory):
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(cookie_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
    probe_factory.outcomes.append(ConnectionError("reset"))
    original_factory = CookieManager._make_browser_cookie_probe_client

    def create(cookies):
        client = original_factory(cookies)

        async def fail(**_options):
            clock.now += 30
            raise ConnectionError("reset")

        client.init = AsyncMock(side_effect=fail)
        return client

    monkeypatch.setattr(CookieManager, "_make_browser_cookie_probe_client", staticmethod(create))

    result = run_probe()

    assert not result.cookies
    assert result.reason == "BROWSER_COOKIE_AUTH_UNVERIFIED"
    assert len(probe_factory.clients) == 1
    probe_factory.clients[0].close.assert_awaited_once()


def test_cancellation_closes_probe_without_retry(probe_factory):
    probe_factory.outcomes.append(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        run_probe()

    assert len(probe_factory.clients) == 1
    probe_factory.clients[0].close.assert_awaited_once()


@pytest.mark.parametrize("error", [TemporarilyBlockedError("synthetic"), RuntimeError("HTTP 429")])
def test_rate_limit_stops_probe_retries_and_all_later_cookie_candidates(probe_factory, error):
    probe_factory.outcomes.extend([error, AccountStatus.AVAILABLE])
    result = asyncio.run(CookieManager._verify_cookie_candidates([
        ("browser_chrome:Default", {"__Secure-1PSID": "synthetic-default"}),
        ("browser_chrome:Other", {"__Secure-1PSID": "synthetic-other"}),
    ]))
    assert result.reason == "BROWSER_COOKIE_RATE_LIMITED" and not result.cookies
    assert len(probe_factory.clients) == 1
    probe_factory.clients[0].close.assert_awaited_once()
    assert probe_factory.candidates == [{"__Secure-1PSID": "synthetic-default"}]


def test_network_retry_that_hits_429_stops_before_another_profile(probe_factory):
    probe_factory.outcomes.extend([ConnectionError("synthetic reset"), TemporarilyBlockedError("synthetic")])
    result = asyncio.run(CookieManager._verify_cookie_candidates([
        ("browser_chrome:Default", {"__Secure-1PSID": "synthetic-default"}),
        ("browser_chrome:Other", {"__Secure-1PSID": "synthetic-other"}),
    ]))
    assert result.reason == "BROWSER_COOKIE_RATE_LIMITED" and len(probe_factory.clients) == 2
    assert all(cookies["__Secure-1PSID"] == "synthetic-default" for cookies in probe_factory.candidates)


def test_rate_limited_discovery_does_not_try_another_browser(monkeypatch):
    from unittest.mock import Mock
    from src.cookie_manager import BrowserCookieDiscovery

    read = Mock(return_value=[("Default", {"__Secure-1PSID": "synthetic"})])
    verify = Mock(return_value=BrowserCookieDiscovery(reason="BROWSER_COOKIE_RATE_LIMITED"))
    monkeypatch.setattr(CookieManager, "_read_browser_candidates", read)
    monkeypatch.setattr(CookieManager, "_validate_browser_candidates", verify)
    result = CookieManager.discover_browser_cookies()
    assert result.reason == "BROWSER_COOKIE_RATE_LIMITED"
    read.assert_called_once()
    verify.assert_called_once()


def test_profile_diagnostics_stop_after_first_rate_limit(probe_factory):
    probe_factory.outcomes.extend([TemporarilyBlockedError("synthetic"), AccountStatus.AVAILABLE])
    result = asyncio.run(CookieManager._validate_cookie_candidate_profiles_async([
        ("Default", {"__Secure-1PSID": "synthetic-default"}),
        ("Other", {"__Secure-1PSID": "synthetic-other"}),
    ]))
    assert len(result) == 1 and result[0]["error_code"] == "BROWSER_COOKIE_RATE_LIMITED"
    assert len(probe_factory.clients) == 1
    probe_factory.clients[0].close.assert_awaited_once()


def test_profile_auxiliary_read_keeps_rate_limit_visible_and_restores_language(monkeypatch):
    # The optional registry adapter's positional SDK DTO is unrelated to this guard.
    monkeypatch.setattr("gemini_webapi.types.RPCData", lambda *args: SimpleNamespace())
    monkeypatch.setattr("src.infrastructure.rpc_contracts.get_contract", lambda _key: SimpleNamespace(
        rpc_id="synthetic-registry", build_payload=lambda: "[]", source_path="/app",
    ))
    client = SimpleNamespace(language="en", _batch_execute=AsyncMock(side_effect=TemporarilyBlockedError("synthetic")))
    with pytest.raises(TemporarilyBlockedError):
        asyncio.run(CookieManager._probe_scheduled_registry_count(client))
    assert client.language == "en"


def test_http_transport_error_retries_once_during_cookie_probe(probe_factory):
    from httpx2 import HTTPError

    probe_factory.outcomes.extend([HTTPError("synthetic stream closed"), AccountStatus.AVAILABLE])
    result = run_probe()
    assert result.cookies == {"__Secure-1PSID": "candidate", "OSID": "verified-session"}
    assert len(probe_factory.clients) == 2
