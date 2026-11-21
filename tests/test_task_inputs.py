"""Explicit input stays on one Task handle and each observed card dispatches once."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import client_wrapper
from src.domain import OperationState
from src.infrastructure.operation_store import OperationStore
from src.infrastructure.task_confirmation import parse_confirmation_replies
from src.services import background, task_inputs, web_context_transport


@pytest.fixture
def waiting(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-task-input-account")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "false")
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite3"))
    store = OperationStore()
    handle = "op_" + "a" * 32
    store.create(handle, "gemini_run_task", requested_model="argon", thinking_level="extended", retain_chat=True)
    assert store.claim_worker(handle, "old")
    store.checkpoint_media(handle, worker_token="old", chat_id="c_task", response_id="r_card", candidate_id="rc_card")
    store.finish_worker(handle, worker_token="old", state="partial", phase="task_input_required", verification_status="card_observed")
    record = store.get(handle)
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: SimpleNamespace())
    monkeypatch.setattr(web_context_transport, "fetch_task_metadata", AsyncMock(return_value={"chat_id": "c_task", "upstream_status": 5}))
    monkeypatch.setattr(web_context_transport, "fetch_workflow_turn", AsyncMock(return_value={"input_required": True, "input_options": [{"label": "Confirm", "prompt": "PRIVATE_EXPLICIT_USER_REPLY"}]}))
    dispatched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: dispatched.append(payload) or 12345)
    return store, record, task_inputs.input_handle(record), dispatched


def test_card_input_dispatches_once_on_same_handle_and_keeps_original_policy(waiting):
    store, record, token, dispatched = waiting
    request = {"input_id": token, "prompt": "PRIVATE_EXPLICIT_USER_REPLY", "model": "flash", "retain_chat": False}
    first = asyncio.run(task_inputs.submit_task_input(request, operation_id=record.operation_id))
    again = asyncio.run(task_inputs.submit_task_input(request, operation_id=record.operation_id))
    assert first.ok and again.ok and first.data.operation_id == again.data.operation_id == record.operation_id
    assert len(dispatched) == 1 and dispatched[0]["kind"] == "task_input"
    payload = dispatched[0]["request"]
    assert payload["chat_context"] == ["c_task", "r_card", "rc_card"]
    assert payload["model"] == "argon" and payload["thinking_level"] == "extended"
    saved = store.get(record.operation_id)
    assert saved.retain_chat and saved.cleanup_protected and saved.upstream_chat_id == "c_task"
    assert saved.upstream_response_id is None and saved.upstream_candidate_id is None
    assert not store.is_worker_owner(record.operation_id, "old")
    assert b"PRIVATE_EXPLICIT_USER_REPLY" not in store.path.read_bytes()


@pytest.mark.parametrize("changed", ["goal", "card", "account", "token"])
def test_input_refuses_changed_target_or_missing_card_before_dispatch(waiting, monkeypatch, changed):
    _store, record, token, dispatched = waiting
    if changed == "goal":
        monkeypatch.setattr(web_context_transport, "fetch_task_metadata", AsyncMock(return_value={"chat_id": "c_other", "upstream_status": 5}))
    elif changed == "card":
        monkeypatch.setattr(web_context_transport, "fetch_workflow_turn", AsyncMock(return_value={"completed": True, "input_required": False}))
    elif changed == "account":
        monkeypatch.setenv("GEMINI_PSID", "other-account")
    else:
        token = "input_" + "b" * 32
    result = asyncio.run(task_inputs.submit_task_input({"input_id": token, "prompt": "PRIVATE_EXPLICIT_USER_REPLY"}, operation_id=record.operation_id))
    assert not result.ok and not dispatched


def test_setup_status_cannot_be_answered_as_a_permission_or_oauth_text_reply(waiting, monkeypatch):
    store, record, token, dispatched = waiting
    monkeypatch.setattr(web_context_transport, "fetch_task_metadata", AsyncMock(
        return_value={"chat_id": "c_task", "upstream_status": 8}))
    card = AsyncMock(return_value={"setup_required": True, "input_required": False})
    monkeypatch.setattr(web_context_transport, "fetch_workflow_turn", card)
    result = asyncio.run(task_inputs.submit_task_input({"input_id": token, "prompt": "Continue"},
                                                     operation_id=record.operation_id))
    assert not result.ok and not dispatched and not store.task_input_dispatched(record.operation_id, token)
    assert store.get(record.operation_id).cleanup_protected
    card.assert_not_awaited()


def test_native_setup_card_never_becomes_text_approval_even_with_input_goal_status(waiting, monkeypatch):
    store, record, token, dispatched = waiting
    monkeypatch.setattr(web_context_transport, "fetch_workflow_turn", AsyncMock(return_value={
        "setup_required": True, "setup_kind": "app_connection", "input_required": False,
        "input_options": [],
    }))
    result = asyncio.run(task_inputs.submit_task_input({"input_id": token, "prompt": "Continue"},
                                                     operation_id=record.operation_id))
    assert not result.ok and not dispatched and not store.task_input_dispatched(record.operation_id, token)
    assert store.get(record.operation_id).cleanup_protected


def test_failed_input_dispatch_is_not_replayed(waiting, monkeypatch):
    store, record, token, _dispatched = waiting
    launch = []
    def fail(*args):
        launch.append(True)
        raise OSError("synthetic dispatch failure")
    monkeypatch.setattr(background, "_launch_detached_worker", fail)
    request = {"input_id": token, "prompt": "PRIVATE_EXPLICIT_USER_REPLY"}
    first = asyncio.run(task_inputs.submit_task_input(request, operation_id=record.operation_id))
    second = asyncio.run(task_inputs.submit_task_input(request, operation_id=record.operation_id))
    assert not first.ok and second.ok and second.data.state == OperationState.PARTIAL
    assert launch == [True] and store.get(record.operation_id).cleanup_protected


def test_two_concurrent_replies_only_claim_one_card(waiting):
    store, record, token, dispatched = waiting
    async def submit():
        return await asyncio.gather(*(task_inputs.submit_task_input(
            {"input_id": token, "prompt": "PRIVATE_EXPLICIT_USER_REPLY"}, operation_id=record.operation_id) for _ in range(2)))
    assert all(r.ok for r in asyncio.run(submit()))
    assert len(dispatched) == 1 and store.task_input_dispatched(record.operation_id, token)


@pytest.mark.parametrize("failure", ["initialize", "goal", "turn"])
def test_input_read_failure_returns_structured_unverified_without_claim(waiting, monkeypatch, failure):
    store, record, token, dispatched = waiting
    if failure == "initialize":
        monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock(side_effect=asyncio.TimeoutError()))
    else:
        method = "fetch_task_metadata" if failure == "goal" else "fetch_workflow_turn"
        monkeypatch.setattr(web_context_transport, method, AsyncMock(side_effect=RuntimeError("synthetic read failed")))
    result = asyncio.run(task_inputs.submit_task_input({"input_id": token, "prompt": "PRIVATE_EXPLICIT_USER_REPLY"}, operation_id=record.operation_id))
    assert not result.ok and result.meta.verification_status == "task_input_unverified"
    assert not dispatched and not store.task_input_dispatched(record.operation_id, token)
    assert store.get(record.operation_id).cleanup_protected


def test_input_initialize_deadline_leaves_original_card_available(waiting, monkeypatch):
    store, record, token, dispatched = waiting
    async def never_returns():
        await asyncio.Event().wait()
    monkeypatch.setattr(client_wrapper, "initialize_client", never_returns)
    monkeypatch.setattr(task_inputs, "TASK_INPUT_INITIALIZE_SECONDS", 0.01)
    result = asyncio.run(task_inputs.submit_task_input({"input_id": token, "prompt": "PRIVATE_EXPLICIT_USER_REPLY"}, operation_id=record.operation_id))
    assert not result.ok and result.meta.verification_status == "task_input_unverified"
    assert not dispatched and not store.task_input_dispatched(record.operation_id, token)


def test_card_reply_requires_the_exact_current_button_query(waiting):
    store, record, token, dispatched = waiting
    result = asyncio.run(task_inputs.submit_task_input({"input_id": token, "prompt": "paraphrased confirmation"}, operation_id=record.operation_id))
    assert not result.ok and result.meta.verification_status == "task_input_choice_required"
    assert not dispatched and not store.task_input_dispatched(record.operation_id, token)


def _choice_turn(*, local=False, body='Synthetic public page permission'):
    allow = ['Allow', [None, None, None, 'synthetic-allow']]
    deny = ['Deny', [None, None, None, 'synthetic-deny']]
    override = ['Browser permission', body, None, None, None, None, [allow], [deny]]
    choices = [['vm', 'Remote browser', 'Read-only public fixture', override]]
    if local:
        choices.append(['local_chrome:synthetic-private-device', 'Local browser', None, override])
    card = [None, 'Browser permission', body, None, None, None, None, [allow], [deny], 1, None, None, None, choices]
    return {'input_required': True, **parse_confirmation_replies(
        card, chat_id='c_task', response_id='r_card', candidate_id='rc_card')}


def test_native_choice_card_requires_opaque_option_and_dispatches_exact_choice_once(waiting, monkeypatch):
    store, record, token, dispatched = waiting
    turn = _choice_turn(local=True)
    monkeypatch.setattr(web_context_transport, 'fetch_workflow_turn', AsyncMock(return_value=turn))
    service_request = {'input_id': token, 'prompt': 'synthetic-allow'}
    refused = asyncio.run(task_inputs.submit_task_input(service_request, operation_id=record.operation_id))
    assert not refused.ok and not dispatched
    selected = turn['input_options'][2]
    service_request['input_option_id'] = selected['input_option_id']
    accepted = asyncio.run(task_inputs.submit_task_input(service_request, operation_id=record.operation_id))
    assert accepted.ok and len(dispatched) == 1
    assert dispatched[0]['request']['task_action_choice_id'] == 'local_chrome:synthetic-private-device'
    assert dispatched[0]['request']['chat_context'] == ['c_task', 'r_card', 'rc_card']
    assert b'synthetic-private-device' not in store.path.read_bytes()
    assert b'synthetic-allow' not in store.path.read_bytes()
    service_request['input_option_id'] = turn['input_options'][0]['input_option_id']
    duplicate = asyncio.run(task_inputs.submit_task_input(service_request, operation_id=record.operation_id))
    assert duplicate.ok and len(dispatched) == 1
    assert any(warning.code == 'task_input_consumed' for warning in duplicate.warnings)
    assert duplicate.meta.operation_state == duplicate.data.state


@pytest.mark.parametrize('change', ['stale_card', 'wrong_prompt', 'wrong_option', 'subagent', 'unsupported'])
def test_selected_native_option_fails_closed_before_claim_on_card_or_identity_change(waiting, monkeypatch, change):
    store, record, token, dispatched = waiting
    turn = _choice_turn()
    option = turn['input_options'][0]
    request = {'input_id': token, 'prompt': option['prompt'], 'input_option_id': option['input_option_id']}
    if change == 'stale_card':
        turn = _choice_turn(body='Changed requested browser effect')
    elif change == 'wrong_prompt':
        request['prompt'] = 'synthetic-deny'
    elif change == 'wrong_option':
        request['input_option_id'] = 'option_' + 'f' * 32
    elif change == 'subagent':
        turn['input_card']['replies'][0]['subagent_conversation_id'] = 'synthetic-subagent'
    else:
        turn['input_card']['unsupported'] = True
    monkeypatch.setattr(web_context_transport, 'fetch_workflow_turn', AsyncMock(return_value=turn))
    result = asyncio.run(task_inputs.submit_task_input(request, operation_id=record.operation_id))
    assert not result.ok and not dispatched and not store.task_input_dispatched(record.operation_id, token)


def test_account_switch_after_goal_read_never_reads_the_turn_or_claims_a_reply(waiting, monkeypatch):
    store, record, token, dispatched = waiting
    async def switched(client, cid):
        monkeypatch.setenv('GEMINI_PSID', 'changed-synthetic-account')
        return {'chat_id': cid, 'upstream_status': 5}
    turn_read = AsyncMock(return_value=_choice_turn())
    monkeypatch.setattr(web_context_transport, 'fetch_task_metadata', switched)
    monkeypatch.setattr(web_context_transport, 'fetch_workflow_turn', turn_read)
    result = asyncio.run(task_inputs.submit_task_input(
        {'input_id': token, 'prompt': 'synthetic-allow'}, operation_id=record.operation_id))
    assert not result.ok and not dispatched and turn_read.await_count == 0
    assert not store.task_input_dispatched(record.operation_id, token)


def test_duplicate_button_prompts_without_a_unique_option_are_not_interpreted_as_a_choice(waiting, monkeypatch):
    _store, record, token, dispatched = waiting
    turn = {'input_required': True, 'input_options': [
        {'label': 'Once', 'prompt': 'same'}, {'label': 'Always', 'prompt': 'same'},
    ]}
    monkeypatch.setattr(web_context_transport, 'fetch_workflow_turn', AsyncMock(return_value=turn))
    result = asyncio.run(task_inputs.submit_task_input({'input_id': token, 'prompt': 'same'}, operation_id=record.operation_id))
    assert not result.ok and not dispatched


@pytest.mark.parametrize('stage', ['turn', 'launch'])
def test_account_switch_after_turn_or_worker_launch_scrubs_original_card_result(waiting, monkeypatch, stage):
    store, record, token, dispatched = waiting
    turn = _choice_turn()
    option = turn['input_options'][0]
    if stage == 'turn':
        async def switched(*args):
            monkeypatch.setenv('GEMINI_PSID', 'changed-synthetic-account')
            return turn
        monkeypatch.setattr(web_context_transport, 'fetch_workflow_turn', switched)
    else:
        monkeypatch.setattr(web_context_transport, 'fetch_workflow_turn', AsyncMock(return_value=turn))
        def switched(payload, store):
            dispatched.append(payload)
            monkeypatch.setenv('GEMINI_PSID', 'changed-synthetic-account')
            return 12345
        monkeypatch.setattr(background, '_launch_detached_worker', switched)
    result = asyncio.run(task_inputs.submit_task_input(
        {'input_id': token, 'prompt': option['prompt'], 'input_option_id': option['input_option_id']},
        operation_id=record.operation_id))
    assert not result.ok and result.data is None
    assert len(dispatched) == (1 if stage == 'launch' else 0)
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-task-input-account')
    assert store.task_input_dispatched(record.operation_id, token) is (stage == 'launch')


@pytest.mark.parametrize('option_id', ['', 0, True, 'allow', 'option_' + 'g' * 32])
def test_invalid_option_handle_never_initializes_or_dispatches(waiting, monkeypatch, option_id):
    _store, record, token, dispatched = waiting
    initialize = AsyncMock()
    monkeypatch.setattr(client_wrapper, 'initialize_client', initialize)
    result = asyncio.run(task_inputs.submit_task_input(
        {'input_id': token, 'prompt': 'synthetic-allow', 'input_option_id': option_id}, operation_id=record.operation_id))
    assert not result.ok and not dispatched
    initialize.assert_not_awaited()


def test_has_task_input_is_read_only_and_separates_accounts(waiting, monkeypatch):
    store, record, token, _dispatched = waiting
    assert not store.has_task_input(record.operation_id)
    assert store.begin_task_input(record.operation_id, token, response_id='r_card', candidate_id='rc_card', worker_token='new')
    assert store.has_task_input(record.operation_id)
    assert not store.has_task_input('op_' + 'f' * 32)
    monkeypatch.setenv('GEMINI_PSID', 'other-synthetic-account')
    assert not store.has_task_input(record.operation_id)
    monkeypatch.setenv('GEMINI_PSID', 'synthetic-task-input-account')
    assert store.has_task_input(record.operation_id)
