"""Connection/frame observation retries never replay browser input."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from src.domain import DomainErrorCode
from src.infrastructure.rfb import RFBError
from src.services import task_browser
from tests.test_task_browser_service import CID, Harness


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-browser-account-a')
    monkeypatch.setenv('GEMINI_BROWSER_AUTO_LOAD', 'false')
    real_sleep = asyncio.sleep
    class FastAsyncio:
        def __getattr__(self, name):
            return getattr(asyncio, name)
        async def sleep(self, _seconds):
            await real_sleep(0)
    monkeypatch.setattr(task_browser, 'asyncio', FastAsyncio())
    return Harness(tmp_path, monkeypatch)


def transient():
    return asyncio.IncompleteReadError(partial=b'', expected=1)


def set_connection_failures(harness, failures):
    attempts = []
    @asynccontextmanager
    async def connection(client, observed):
        attempts.append((client, observed['chat_id']))
        if len(attempts) <= len(failures):
            raise failures[len(attempts) - 1]
        yield harness.rfb
    harness.service.dependencies = replace(harness.dependencies, connection=connection)
    return attempts


@pytest.mark.parametrize('failures', [[transient()], [transient(), transient()]])
def test_transient_connect_eof_recovers_without_replaying_input(harness, failures):
    attempts = set_connection_failures(harness, failures)
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments()))
    assert result.ok and len(attempts) == len(failures) + 1
    assert len(harness.rfb.inputs) == 1 and harness.action()['state'] == 'verified'
    assert len(harness.turn_reads) == len(attempts)
    assert all(cid == CID for _, cid in harness.task_reads)


def test_frame_eof_before_input_recovers_once(harness):
    first = True
    original = harness.rfb.screenshot_png
    async def capture():
        nonlocal first
        if first:
            first = False
            raise transient()
        return await original()
    harness.rfb.screenshot_png = capture
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments()))
    assert result.ok and len(harness.connections) == 2 and len(harness.rfb.inputs) == 1


def test_three_failed_attempts_are_bounded(harness):
    attempts = set_connection_failures(harness, [transient(), transient(), transient(), transient()])
    result = asyncio.run(harness.service.observe(CID))
    assert not result.ok and len(attempts) == 3
    assert result.data.input_sent is False and result.data.action_state is None
    assert not harness.rfb.inputs and harness.action_count() == 0


def test_failure_after_input_never_retries(harness):
    harness.rfb.fail_capture = 2
    result = asyncio.run(harness.service.observe(CID, **harness.input_arguments()))
    assert not result.ok and len(harness.connections) == 1 and len(harness.rfb.inputs) == 1
    assert result.data.input_sent is True and result.data.action_state == 'outcome_unverified'


def test_consumed_action_does_not_reconnect_or_retry(harness):
    args = harness.input_arguments()
    first = asyncio.run(harness.service.observe(CID, **args))
    second = asyncio.run(harness.service.observe(CID, **args))
    assert first.ok and second.ok and second.data.state == 'duplicate'
    assert len(harness.connections) == 1 and len(harness.rfb.inputs) == 1


def test_account_switch_during_retry_delay_prevents_new_reads(harness, monkeypatch):
    attempts = set_connection_failures(harness, [transient()])
    real_sleep = asyncio.sleep
    class SwitchAsyncio:
        def __getattr__(self, name):
            return getattr(asyncio, name)
        async def sleep(self, _seconds):
            monkeypatch.setenv('GEMINI_PSID', 'synthetic-browser-account-b')
            await real_sleep(0)
    monkeypatch.setattr(task_browser, 'asyncio', SwitchAsyncio())
    result = asyncio.run(harness.service.observe(CID))
    assert not result.ok and result.error.code == DomainErrorCode.AUTH_REQUIRED
    assert len(attempts) == 1 and len(harness.task_reads) == 1 and len(harness.turn_reads) == 1


@pytest.mark.parametrize('error', [PermissionError('forbidden'), RFBError('protocol'), ValueError('changed')])
def test_nontransient_errors_never_retry(harness, error):
    attempts = set_connection_failures(harness, [error])
    result = asyncio.run(harness.service.observe(CID))
    assert not result.ok and len(attempts) == 1 and not harness.rfb.inputs
