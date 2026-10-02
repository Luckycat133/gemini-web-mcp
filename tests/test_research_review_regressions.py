"""Deadlines and positive report evidence for no-ID Deep Research recovery."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain import OperationState
from src.services.manifest import tool_manifest_payload
from src.services.research import (
    research_operation_data,
    start_deep_research_with_recovery,
    wait_for_deep_research_by_chat,
)


def test_start_deadline_does_not_begin_an_unbounded_recovery_read():
    async def run():
        blocked = asyncio.Event()
        client = SimpleNamespace(
            start_deep_research=AsyncMock(side_effect=lambda *_a, **_k: None),
            fetch_latest_chat_response=AsyncMock(),
        )

        async def start(*_args, **_kwargs):
            await blocked.wait()

        client.start_deep_research = start
        result = await asyncio.wait_for(
            start_deep_research_with_recovery(client, SimpleNamespace(cid="c_test"), None, .02), .5,
        )
        assert result.timeout_during_start is True
        client.fetch_latest_chat_response.assert_not_awaited()

    asyncio.run(run())


def test_early_transport_timeout_bounds_the_recovery_fetch():
    async def run():
        cancelled = asyncio.Event()

        async def fetch(_cid):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        client = SimpleNamespace(
            start_deep_research=AsyncMock(side_effect=asyncio.TimeoutError),
            fetch_latest_chat_response=fetch,
        )
        result = await asyncio.wait_for(
            start_deep_research_with_recovery(client, SimpleNamespace(cid="c_test"), None, .02), .5,
        )
        assert result.timeout_during_start is True
        await asyncio.wait_for(cancelled.wait(), .5)

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["latest", "report", "followup"])
def test_no_id_research_bounds_every_recovery_phase(phase):
    async def run():
        blocked = asyncio.Event()

        async def hang(*_args):
            await blocked.wait()

        client = SimpleNamespace(fetch_latest_chat_response=AsyncMock(
            return_value=SimpleNamespace(text="I've finished the research"),
        ))
        fetch_report = AsyncMock(return_value=None)
        request_report = AsyncMock(return_value=None)
        if phase == "latest":
            client.fetch_latest_chat_response = hang
        elif phase == "report":
            fetch_report = hang
        else:
            request_report = hang
        result = await asyncio.wait_for(wait_for_deep_research_by_chat(
            client, SimpleNamespace(cid="c_test"), None, SimpleNamespace(text="I'm on it"),
            poll_interval=.001, timeout=.02, fetch_report=fetch_report, request_report=request_report,
        ), .5)
        assert result.done is False
        assert result.final_output is None

    asyncio.run(run())


@pytest.mark.parametrize("text", [
    "I cannot run Deep Research because the account quota has been exhausted.",
    "I am still collecting sources.",
    "Here is a revised research plan.",
])
def test_changed_text_cannot_become_a_completed_report(text):
    async def run():
        fetch_report = AsyncMock()
        result = await wait_for_deep_research_by_chat(
            SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=SimpleNamespace(text=text))),
            SimpleNamespace(cid="c_test"), None, SimpleNamespace(text="I'm on it"),
            poll_interval=.001, timeout=.01, fetch_report=fetch_report, request_report=AsyncMock(),
        )
        assert not result.done
        assert not research_operation_data(OperationState.TIMED_OUT, upstream_result=result).report_available
        fetch_report.assert_not_awaited()

    asyncio.run(run())


def test_completion_acknowledgement_followed_by_refusal_is_not_a_report():
    async def run():
        result = await wait_for_deep_research_by_chat(
            SimpleNamespace(fetch_latest_chat_response=AsyncMock(
                return_value=SimpleNamespace(text="I've finished the research"),
            )),
            SimpleNamespace(cid="c_test"), None, SimpleNamespace(text="I'm on it"),
            poll_interval=.001, timeout=.01, fetch_report=AsyncMock(return_value=None),
            request_report=AsyncMock(return_value=SimpleNamespace(text="Quota exhausted; no report is available.")),
        )
        assert result.done is False
        assert result.final_output is None

    asyncio.run(run())


def test_completed_chat_report_evidence_needs_no_extra_recovery():
    async def run():
        report = SimpleNamespace(text="The completed report body", state="completed", report_id="report_observed")
        fetch_report, request_report = AsyncMock(), AsyncMock()
        result = await wait_for_deep_research_by_chat(
            SimpleNamespace(fetch_latest_chat_response=AsyncMock(return_value=report)),
            SimpleNamespace(cid="c_test"), None, None,
            poll_interval=.001, timeout=.1, fetch_report=fetch_report, request_report=request_report,
        )
        assert result.done is True
        assert result.final_output is report
        fetch_report.assert_not_awaited()
        request_report.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("state", ["failed", "cancelled", "unavailable"])
def test_no_id_poll_preserves_an_observed_terminal_failure(state):
    async def run():
        result = await wait_for_deep_research_by_chat(
            SimpleNamespace(fetch_latest_chat_response=AsyncMock(
                return_value=SimpleNamespace(text="Quota exhausted", state=state),
            )), SimpleNamespace(cid="c_test"), None, None,
            poll_interval=.001, timeout=.1, fetch_report=AsyncMock(), request_report=AsyncMock(),
        )
        assert result.done is False
        assert result.statuses[-1].state == state

    asyncio.run(run())


def test_notebook_manifest_scope_includes_the_enabled_facade(monkeypatch):
    monkeypatch.setenv("GEMINI_TOOLS", "history-organize")
    tools = {tool["name"]: tool for tool in tool_manifest_payload("notebooks")["tools"]}
    assert tools["gemini_notebooks"]["current_enabled"] is True
    assert "account-read" not in tools["gemini_notebooks"]["availability"]
