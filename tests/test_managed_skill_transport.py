"""Native Skill uploads must honor current flags and never replay writes."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.services import managed_skill_transport as transport


def bootstrap(*, flags=None, **values):
    data = {"TSDtV": json.dumps({"flags": flags or {}}), "KnDnFf": "feeds/bootstrap", **values}
    return "window.WIZ_global_data = " + json.dumps(data) + ";"


def test_native_skill_upload_uses_current_flag_override_and_optional_context():
    endpoint, headers = transport.skill_upload_configuration(bootstrap(
        flags={"45785736": True, "45785737": "feeds/selected", "45853319": False}, Ylro7b="context"))
    assert endpoint == "https://content-push.googleapis.com/upload/"
    assert headers == {"Push-ID": "feeds/selected", "X-Tenant-Id": "agent_skills",
                       "Content-Type": "application/zip", "X-Client-Pctx": "context"}
    endpoint, headers = transport.skill_upload_configuration(bootstrap(
        flags={"45785736": False, "45853319": True}))
    assert endpoint == "https://contribution.usercontent.google.com/upload?upload_schema=agent_skills"
    assert headers["Push-ID"] == "feeds/bootstrap"


@pytest.mark.parametrize("html", ["", "window.WIZ_global_data = {};", bootstrap(flags={"45853319": 1}),
    bootstrap(flags={"45785737": "invalid"}), bootstrap(flags={"45853319": True}, h1eoVe="https://unknown.test/"),
    bootstrap(flags={"45853319": True}, h1eoVe="https://push.clients6.google.com.evil.example/upload/"),
    bootstrap(flags={"45853319": True}, h1eoVe="https://push.clients6.google.com/upload/?other=value")])
def test_unknown_skill_upload_configuration_fails_before_upload(html):
    with pytest.raises(transport.SkillTransportUnavailable):
        transport.skill_upload_configuration(html)


def test_account_switch_after_config_read_prevents_skill_upload(monkeypatch):
    account = ["first"]
    monkeypatch.setattr(transport, "current_account_key", lambda: account[0])
    calls = []
    class HTTP:
        async def get(self, url):
            account[0] = "second"
            return SimpleNamespace(status_code=200, text=bootstrap())
        async def post(self, *args, **kwargs):
            calls.append(args)
    with pytest.raises(transport.SkillTransportUnavailable):
        asyncio.run(transport.upload_skill(SimpleNamespace(client=HTTP()), b"PK\x03\x04fixture", "fixture.zip"))
    assert not calls


def test_skill_upload_unknown_delivery_is_not_repeated(monkeypatch):
    monkeypatch.setattr(transport, "current_account_key", lambda: "first")
    calls = []
    class HTTP:
        async def get(self, url):
            return SimpleNamespace(status_code=200, text=bootstrap())
        async def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            raise ConnectionError("delivery unknown")
    with pytest.raises(transport.SkillTransportUnavailable, match="start was unavailable"):
        asyncio.run(transport.upload_skill(SimpleNamespace(client=HTTP()), b"PK\x03\x04fixture", "fixture.zip"))
    assert len(calls) == 1


def test_skill_save_dispatches_once_and_checks_returned_update_identity(monkeypatch):
    monkeypatch.setattr(transport, "current_account_key", lambda: "first")
    calls = []
    async def execute(client, key, **arguments):
        calls.append((key, arguments))
        body = json.dumps(["unexpected-skill"])
        return SimpleNamespace(status_code=200, text=json.dumps([["wrb.fr", "SMuXte", body]]))
    monkeypatch.setattr(transport, "execute_contract", execute)
    with pytest.raises(transport.SkillTransportUnavailable):
        asyncio.run(transport.mutate_skill(object(), "update", skill_id="skill-fixture",
                                          upload_handle="opaque-upload", enabled=True))
    assert len(calls) == 1
    assert calls[0][1]["retry_count"] == 0


@pytest.mark.parametrize(("html", "endpoint", "session_url"), [
    (bootstrap(), "https://content-push.googleapis.com/upload/",
     "https://content-push.googleapis.com/upload/session?opaque=1"),
    (bootstrap(flags={"45853319": True}, h1eoVe="https://push.clients6.google.com/upload/"),
     "https://push.clients6.google.com/upload/?upload_schema=agent_skills",
     "https://push.clients6.google.com/upload/session?opaque=1"),
])
def test_skill_upload_uses_native_resumable_handshake(monkeypatch, html, endpoint, session_url):
    monkeypatch.setattr(transport, "current_account_key", lambda: "account")
    calls = []
    class HTTP:
        async def get(self, _url):
            return SimpleNamespace(status_code=200, text=html)
        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                return SimpleNamespace(status_code=200, headers={"X-Goog-Upload-URL": session_url})
            return SimpleNamespace(status_code=200, text="synthetic-upload-handle")
    archive = b"PK\x03\x04fixture"
    assert asyncio.run(transport.upload_skill(SimpleNamespace(client=HTTP()), archive, "fixture.zip")) == "synthetic-upload-handle"
    assert [url for url, _kwargs in calls] == [endpoint, session_url]
    start, final = calls[0][1], calls[1][1]
    assert start["data"] == "File name: fixture.zip"
    assert start["headers"]["X-Goog-Upload-Protocol"] == "resumable"
    assert start["headers"]["X-Goog-Upload-Command"] == "start"
    assert start["headers"]["X-Goog-Upload-Header-Content-Length"] == str(len(archive))
    assert final["data"] == archive
    assert final["headers"]["X-Goog-Upload-Command"] == "upload, finalize"
    assert final["headers"]["X-Goog-Upload-Offset"] == "0"
    assert start["allow_redirects"] is final["allow_redirects"] is False


@pytest.mark.parametrize("url", ["https://evil.example/upload", "http://content-push.googleapis.com/upload",
    "https://content-push.googleapis.com.evil.example/", "https://user@content-push.googleapis.com/", "https://content-push.googleapis.com:444/"])
def test_skill_upload_rejects_untrusted_session_url_before_finalizing(monkeypatch, url):
    monkeypatch.setattr(transport, "current_account_key", lambda: "account")
    calls = []
    class HTTP:
        async def get(self, _url):
            return SimpleNamespace(status_code=200, text=bootstrap())
        async def post(self, url, **kwargs):
            calls.append(url)
            return SimpleNamespace(status_code=200, headers={"X-Goog-Upload-URL": url_to_return})
    url_to_return = url
    with pytest.raises(transport.SkillTransportUnavailable):
        asyncio.run(transport.upload_skill(SimpleNamespace(client=HTTP()), b"PK\x03\x04fixture", "fixture.zip"))
    assert len(calls) == 1


def test_account_switch_after_upload_start_never_finalizes(monkeypatch):
    account = ["first"]
    monkeypatch.setattr(transport, "current_account_key", lambda: account[0])
    calls = []
    class HTTP:
        async def get(self, _url):
            return SimpleNamespace(status_code=200, text=bootstrap())
        async def post(self, url, **kwargs):
            calls.append(url)
            account[0] = "second"
            return SimpleNamespace(status_code=200, headers={"X-Goog-Upload-URL": "https://content-push.googleapis.com/session"})
    with pytest.raises(transport.SkillTransportUnavailable):
        asyncio.run(transport.upload_skill(SimpleNamespace(client=HTTP()), b"PK\x03\x04fixture", "fixture.zip"))
    assert len(calls) == 1
