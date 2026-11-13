"""MCP browser output preserves typed metadata and verified image blocks."""

import asyncio
import hashlib
from unittest.mock import AsyncMock

import pytest
from mcp import Client
from PIL import Image

from src.adapters.mcp_sdk import MCPServer
from src.domain import DomainErrorCode, DomainResult
from src.domain.artifacts import Artifact, ArtifactKind, ArtifactState, ArtifactVerification, ArtifactVerificationStatus
from src.domain.task_browser import TaskBrowserObservation
from src.onboarding import domain_result_from_call
from src.tools import task_browser


@pytest.fixture
def browser_server(monkeypatch):
    mcp = MCPServer('Browser fixture')
    task_browser.register_task_browser_tools(mcp)
    service = AsyncMock()
    monkeypatch.setattr(task_browser, '_service', lambda: service)
    return mcp, service


def image_artifact(tmp_path):
    image_path = tmp_path / 'frame.png'
    Image.new('RGB', (2, 2), (20, 40, 60)).save(image_path)
    digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
    artifact = Artifact(id='art_fixture', kind=ArtifactKind.IMAGE, state=ArtifactState.LOCAL,
        local_path=str(image_path), mime_type='image/png', size_bytes=image_path.stat().st_size,
        verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED, details={'sha256': digest}))
    return image_path, digest, artifact


async def call(mcp, args):
    async with Client(mcp, mode='auto', cache=None) as client:
        tools = (await client.list_tools()).tools
        result = await client.call_tool('gemini_task_browser', args)
        return tools, result


def test_registration_annotations_schema_and_verified_png(browser_server, tmp_path):
    mcp, service = browser_server
    _path, digest, artifact = image_artifact(tmp_path)
    data = TaskBrowserObservation('c_fixture', 'r_fixture', 'rc_fixture', 'frame_ready', digest, (artifact,))
    service.observe.return_value = DomainResult.success(data, verification_status='browser_frame_verified')
    tools, result = asyncio.run(call(mcp, {'chat_id': 'c_fixture'}))
    assert len(tools) == 1 and tools[0].name == 'gemini_task_browser'
    assert tools[0].annotations.destructive_hint is True and tools[0].annotations.read_only_hint is False
    assert tools[0].output_schema is not None
    assert set(tools[0].input_schema['properties']) == {
        'chat_id', 'response_id', 'candidate_id', 'browser_input', 'action_id', 'expected_frame_sha256',
        'known_frame_sha256', 'include_image',
    }
    domain = domain_result_from_call(result)
    assert domain['ok'] is True and domain['data']['frame_sha256'] == digest
    assert len(result.content) == 2 and result.content[1].type == 'image'
    assert result.content[1].mime_type == 'image/png'
    service.observe.assert_awaited_once()


def test_file_only_response_keeps_verified_artifact_and_omits_inline_png(browser_server, tmp_path):
    mcp, service = browser_server
    path, digest, artifact = image_artifact(tmp_path)
    data = TaskBrowserObservation('c_fixture', 'r_fixture', 'rc_fixture', 'frame_ready', digest, (artifact,))
    service.observe.return_value = DomainResult.success(data)
    _, result = asyncio.run(call(mcp, {'chat_id': 'c_fixture', 'include_image': False}))
    domain = domain_result_from_call(result)
    assert domain['ok'] and domain['data']['artifacts'][0]['local_path'] == str(path)
    assert len(result.content) == 1 and result.content[0].type == 'text'


def test_failure_preserves_fresh_image(browser_server, tmp_path):
    mcp, service = browser_server
    _, digest, artifact = image_artifact(tmp_path)
    data = TaskBrowserObservation('c_fixture', 'r_fixture', 'rc_fixture', 'frame_changed', digest, (artifact,))
    service.observe.return_value = DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, 'Frame changed.', data=data)
    _, result = asyncio.run(call(mcp, {'chat_id': 'c_fixture'}))
    assert not domain_result_from_call(result)['ok']
    assert len(result.content) == 2 and result.content[1].type == 'image'
    assert 'Artifacts:' in result.content[0].text


@pytest.mark.parametrize('problem', ['missing', 'hash', 'unverified', 'too_large', 'not_png'])
def test_bad_images_never_get_native_image_block(browser_server, tmp_path, problem):
    from dataclasses import replace

    mcp, service = browser_server
    path, digest, artifact = image_artifact(tmp_path)
    if problem == 'missing':
        path.unlink()
    elif problem == 'hash':
        artifact = replace(artifact, verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED, details={'sha256': '0' * 64}))
    elif problem == 'unverified':
        artifact = replace(artifact, verification=ArtifactVerification(ArtifactVerificationStatus.UNVERIFIED, details={'sha256': digest}))
    elif problem == 'too_large':
        path.write_bytes(b'\x89PNG\r\n\x1a\n' + b'x' * (2 * 1024 * 1024))
        artifact = replace(artifact, verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED, details={'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
    else:
        path.write_bytes(b'not png')
        artifact = replace(artifact, verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED, details={'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
    data = TaskBrowserObservation('c_fixture', 'r_fixture', 'rc_fixture', 'frame_ready', digest, (artifact,))
    service.observe.return_value = DomainResult.success(data)
    _, result = asyncio.run(call(mcp, {'chat_id': 'c_fixture'}))
    assert domain_result_from_call(result)['ok'] and all(block.type == 'text' for block in result.content)


def test_adapter_exceptions_are_safe_and_not_exposed(browser_server):
    mcp, service = browser_server
    service.observe.side_effect = ValueError('PRIVATE_VM_TOKEN')
    _, result = asyncio.run(call(mcp, {'chat_id': 'c_fixture'}))
    assert not domain_result_from_call(result)['ok']
    assert 'PRIVATE_VM_TOKEN' not in str(result)


def test_explicit_input_is_delegated_without_replay(browser_server):
    mcp, service = browser_server
    service.observe.return_value = DomainResult.success(TaskBrowserObservation(
        'c_fixture', 'r_fixture', 'rc_fixture', 'input_sent', action_id='action_fixture_123456789', input_sent=True,
    ))
    arguments = {'chat_id': 'c_fixture', 'response_id': 'r_fixture', 'candidate_id': 'rc_fixture',
                 'browser_input': {'kind': 'click', 'x': 12, 'y': 8},
                 'action_id': 'action_fixture_123456789', 'expected_frame_sha256': 'a' * 64}
    _, result = asyncio.run(call(mcp, arguments))
    assert domain_result_from_call(result)['ok']
    service.observe.assert_awaited_once_with(
        'c_fixture', known_frame_sha256=None, **{k: v for k, v in arguments.items() if k != 'chat_id'},
    )
