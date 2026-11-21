"""Shared SDK timeout leases protect research and media requests."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from src.services import research
from src.services.client_timeouts import client_timeout_scope
from src.services.media_generation import media_timeout_scope


def _run_research(client):
    return research.run_deep_research_start_phase(
        client, query="test", requested_model="flash", resolved_model="flash",
        research_model="flash", model_note="", thinking_level="standard",
        timeout_seconds=600, operation="research", operation_id=None,
        schedule_chat_cleanup=lambda *args, **kwargs: None, retain_chat=False,
        delete_after_seconds=None, cleanup_source="test",
    )


def _setup_research(monkeypatch, plan, start):
    monkeypatch.setattr(research, "start_fresh_research_chat", lambda *_: SimpleNamespace())
    monkeypatch.setattr(research, "research_thinking_scope", lambda *_: nullcontext())
    monkeypatch.setattr(research, "create_deep_research_plan", plan)
    monkeypatch.setattr(research, "start_deep_research_with_recovery", start)


def test_research_uses_sdk_threshold_and_overlaps_media(monkeypatch):
    async def run():
        client = SimpleNamespace(timeout=30, watchdog_timeout=30)
        plan_entered, release_plan = asyncio.Event(), asyncio.Event()
        observed = []

        async def plan(*args, **kwargs):
            # This is the SDK's stream zombie/recovery threshold.
            observed.append(min(client.timeout, client.watchdog_timeout))
            plan_entered.set()
            await release_plan.wait()
            return SimpleNamespace()

        async def start(*args, **kwargs):
            observed.append(min(client.timeout, client.watchdog_timeout))
            return SimpleNamespace()

        _setup_research(monkeypatch, plan, start)
        task = asyncio.create_task(_run_research(client))
        await plan_entered.wait()
        with media_timeout_scope(client, 180):
            assert (client.timeout, client.watchdog_timeout) == (600, 600)
            release_plan.set()
            result = await task
            assert result.error is None
            assert observed == [600, 600]
            assert (client.timeout, client.watchdog_timeout) == (180, 180)
        assert (client.timeout, client.watchdog_timeout) == (30, 30)

    asyncio.run(run())


def test_research_lease_restores_after_plan_exception(monkeypatch):
    async def run():
        client = SimpleNamespace(timeout=30, watchdog_timeout=30)

        async def plan(*args, **kwargs):
            assert (client.timeout, client.watchdog_timeout) == (600, 600)
            raise RuntimeError("upstream failure")

        async def start(*args, **kwargs):
            raise AssertionError("start must not run")

        _setup_research(monkeypatch, plan, start)
        result = await _run_research(client)
        assert isinstance(result.error, RuntimeError)
        assert (client.timeout, client.watchdog_timeout) == (30, 30)

    asyncio.run(run())


def test_research_lease_restores_after_cancellation(monkeypatch):
    async def run():
        client = SimpleNamespace(timeout=30, watchdog_timeout=30)
        entered = asyncio.Event()

        async def plan(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        async def start(*args, **kwargs):
            raise AssertionError("start must not run")

        _setup_research(monkeypatch, plan, start)
        task = asyncio.create_task(_run_research(client))
        await entered.wait()
        assert (client.timeout, client.watchdog_timeout) == (600, 600)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (client.timeout, client.watchdog_timeout) == (30, 30)

    asyncio.run(run())


def test_explicit_watchdog_budget_does_not_shorten_another_lease():
    client = SimpleNamespace(timeout=30, watchdog_timeout=200)
    with client_timeout_scope(client, 600, watchdog_seconds=240):
        with media_timeout_scope(client, 180):
            assert (client.timeout, client.watchdog_timeout) == (600, 240)
        assert (client.timeout, client.watchdog_timeout) == (600, 240)
    assert (client.timeout, client.watchdog_timeout) == (30, 200)


def test_real_sdk_silent_first_frame_uses_full_lease_budget(monkeypatch):
    """A non-output chunk after the old watchdog must not end the SDK stream."""
    import json
    from unittest.mock import AsyncMock
    from gemini_webapi import GeminiClient
    from src.services.cleanup_scope import observe_cleanup_frames
    from src.thinking_client import ThinkingLevelGeminiClient

    inner = json.dumps([None, ["c_synthetic", "r_synthetic"]], separators=(",", ":"))
    body = json.dumps([[None, None, inner]], separators=(",", ":"))
    wire = (str(len(body) + 1) + "\n" + body).encode()

    class Response:
        status_code = 200
        http_version = 2
        async def aiter_content(self):
            await asyncio.sleep(0.05)
            yield b" "
            yield wire
            raise asyncio.CancelledError()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_args):
            return False

    class Transport:
        requests = 0
        def stream(self, *_args, **_kwargs):
            self.requests += 1
            return Response()

    monkeypatch.setattr(GeminiClient, "_sync_activity", AsyncMock())
    close = AsyncMock()
    monkeypatch.setattr(GeminiClient, "close", close)
    client = ThinkingLevelGeminiClient()
    from gemini_webapi.types import AvailableModel
    client._model_registry = {"flash": AvailableModel(model_id="flash", model_name="gemini-flash", display_name="Flash", description="", capacity=1)}
    client.client = transport = Transport()
    client._running = True
    client.timeout = 0.01
    client.watchdog_timeout = 0.01
    observed = []

    async def run():
        with client_timeout_scope(client, 1), observe_cleanup_frames(lambda frame: observed.append(frame.cid)):
            with pytest.raises(asyncio.CancelledError):
                async for _ in client.generate_content_stream("synthetic", chat=client.create_cleanup_chat(), current_retry=0):
                    pass

    asyncio.run(run())
    assert "c_synthetic" in observed
    assert transport.requests == 1
    close.assert_not_awaited()
    assert (client.timeout, client.watchdog_timeout) == (0.01, 0.01)
