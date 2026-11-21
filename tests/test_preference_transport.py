"""Offline preference protocol boundaries; no account, browser, or network."""

import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domain.preferences import PermissionRecord
from src.infrastructure.rpc_contracts import get_contract
from src.infrastructure.rpc_parsers import parse_contract_body
from src.services import preference_transport as transport


FIXTURES = json.loads((Path(__file__).parent / 'fixtures/rpc_contracts/preferences.json').read_text())


def native_id(tool='fixture_tool', function='read_item'):
    return [[tool, function], None, None, None, None, None, None, None, None,
            [['ordered', 'first'], ['ordered', 'second', 'attribute-extra']], ['unknown-id-field']]


def raw_permission(*, grant='grant-fixture', scope=2, state=2, tool='fixture_tool', function='read_item'):
    return [native_id(tool, function), [grant, scope, ['unknown-grant-field']],
            [1, 'context-fixture', ['unknown-context-field']], None,
            [None, None, None, 'Synthetic action', 1], state]


def typed_record(**changes):
    parsed = parse_contract_body('preferences.list', [[raw_permission()]])
    return replace(PermissionRecord(**parsed.value[0]), **changes)


def response(key, body, *, status=200, reject=None):
    return SimpleNamespace(status_code=status, text=json.dumps([
        ['wrb.fr', get_contract(key).rpc_id, json.dumps(body), None, None,
         [reject] if reject is not None else None, 'generic']
    ]))


def fake_client(*responses):
    return SimpleNamespace(_batch_execute=AsyncMock(side_effect=list(responses)))


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setattr(transport, 'current_account_key', lambda: 'account-fixture')


@pytest.mark.parametrize(('parser_name', 'case_name'), [
    (name, case) for name in FIXTURES for case in ('success', 'empty', 'rejected', 'changed_shape')
])
def test_registered_preference_fixture_shapes(parser_name, case_name):
    fixture = FIXTURES[parser_name]
    case = fixture['cases'][case_name]
    parsed = parse_contract_body(fixture['contract_key'], case['body'], reject_code=case.get('reject_code'))
    assert parsed.status == case_name


def test_list_request_uses_global_wire_scope_and_exact_native_filter():
    key = 'preferences.list'
    assert json.loads(get_contract(key).build_payload()) == [2]
    assert json.loads(get_contract(key).build_payload(tool_name='fixture_tool', function_name='read_item')) == [
        2, None, None, None, [['fixture_tool', 'read_item']],
    ]
    with pytest.raises(ValueError):
        get_contract(key).build_payload(function_name='unbound')
    assert json.loads(get_contract('preferences.catalog').build_payload(locale='en')) == [
        'en', None, None, None, True,
    ]


def test_read_preserves_full_native_identity_unknown_state_and_ordered_repeated_attributes():
    raw = raw_permission(state=97)
    raw[4][4] = 99
    client = fake_client(response('preferences.list', [[raw]]))
    records = asyncio.run(transport.read_preferences(client, tool_name='fixture_tool', function_name='read_item'))
    assert len(records) == 1 and isinstance(records[0], PermissionRecord)
    item = records[0]
    assert item.state == 97 and item.scope == 2 and item.category == 99
    assert item.tool_name == 'fixture_tool' and item.function_name == 'read_item'
    assert item.attributes == (('ordered', 'first'), ('ordered', 'second'))
    assert item.native_permission_id == raw[0] and item.native_grant_identity == raw[1]
    assert item.context == raw[2] and item.source == 'stored'
    with pytest.raises(FrozenInstanceError):
        item.state = 1
    assert 'unknown-id-field' not in repr(item)
    assert client._batch_execute.await_args.kwargs['current_retry'] == 0


def test_parser_does_not_alias_input_identity_arrays():
    raw = raw_permission()
    parsed = parse_contract_body('preferences.list', [[raw]])
    parsed.value[0]['native_permission_id'][9][0][1] = 'changed-copy'
    parsed.value[0]['native_grant_identity'][2].append('changed-copy')
    assert raw[0][9][0][1] == 'first' and raw[1][2] == ['unknown-grant-field']


@pytest.mark.parametrize('body', [[], [[]], [None], [None, None], [[], ''], [None, '']])
def test_valid_empty_global_list_is_empty_without_invented_default_preferences(body):
    assert asyncio.run(transport.read_preferences(fake_client(response('preferences.list', body)))) == ()


def test_observed_empty_trailing_marker_keeps_the_exact_global_permission_record():
    raw = raw_permission()
    raw[4] = raw[4][:4]  # The observed metadata has no category field.
    client = fake_client(response('preferences.list', [[raw], '']))
    records = asyncio.run(transport.read_preferences(client))
    assert len(records) == 1 and records[0].grant_id == 'grant-fixture' and records[0].scope == 2
    assert records[0].category is None


@pytest.mark.parametrize('body', [
    None, {}, ['moved-field'], [None, 'unrecognized-continuation'],
    [[raw_permission()], 'unrecognized-continuation'],
    [[False]], [[[]]], [[raw_permission(state=True)]],
])
def test_changed_list_shapes_fail_closed(body):
    client = fake_client(response('preferences.list', body))
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.read_preferences(client))
    assert error.value.mutation_sent is False and client._batch_execute.await_count == 1


@pytest.mark.parametrize('bad', [
    SimpleNamespace(status_code=503, text='PRIVATE_RESPONSE'),
    SimpleNamespace(status_code=200, text='PRIVATE_RESPONSE'),
    response('preferences.list', [[raw_permission()]], reject=7),
    response('preferences.update', [raw_permission()]),
    SimpleNamespace(status_code=200, text='[]'),
])
def test_http_rejection_wrong_or_missing_envelopes_are_safe_failures(bad):
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.read_preferences(fake_client(bad)))
    assert error.value.mutation_sent is False and 'PRIVATE_RESPONSE' not in str(error.value)


def test_duplicate_native_or_granted_identity_is_not_a_successful_listing():
    first = raw_permission()
    for second in (deepcopy(first), raw_permission(grant='other'), raw_permission(tool='other_tool')):
        client = fake_client(response('preferences.list', [[first, second]]))
        with pytest.raises(transport.PreferenceTransportError):
            asyncio.run(transport.read_preferences(client))


@pytest.mark.parametrize('record,filters', [
    (raw_permission(scope=1), {}), (raw_permission(scope=9), {}),
    (raw_permission(tool='other'), {'tool_name': 'fixture_tool'}),
    (raw_permission(function='other'), {'tool_name': 'fixture_tool', 'function_name': 'read_item'}),
])
def test_read_never_returns_thread_scopes_or_wrong_function_after_global_filtered_request(record, filters):
    with pytest.raises(transport.PreferenceTransportError):
        asyncio.run(transport.read_preferences(fake_client(response('preferences.list', [[record]])), **filters))


def test_write_sends_once_with_complete_native_identity_and_does_not_verify_or_read_back():
    record = typed_record()
    snapshot = deepcopy(record.native_permission_id)
    # A different reply state is deliberately not treated as independent verification.
    client = fake_client(response('preferences.update', [raw_permission(state=2)]))
    assert asyncio.run(transport.write_preference(client, record, state=1)) is None
    assert client._batch_execute.await_count == 1
    call = client._batch_execute.await_args
    assert call.kwargs['current_retry'] == 0
    assert json.loads(call.args[0][0].payload) == [record.grant_id, snapshot, 1]
    assert record.native_permission_id == snapshot


def test_browser_attribute_patch_preserves_unknown_fields_and_other_duplicate_attributes():
    record = typed_record(native_permission_id=native_id('web_browser_agent', 'actuate'))
    replacement = deepcopy(record.native_permission_id)
    replacement[9].append(['remy_choice_id', 'vm'])
    client = fake_client(response('preferences.update', [raw_permission(tool='web_browser_agent', function='actuate')]))
    asyncio.run(transport.write_preference(client, record, state=2, native_permission_id=replacement))
    sent = json.loads(client._batch_execute.await_args.args[0][0].payload)[1]
    assert sent == replacement and sent[9][:2] == record.native_permission_id[9]


@pytest.mark.parametrize('state', [True, 0, 99, '1'])
def test_invalid_write_state_is_rejected_before_dispatch(state):
    client = fake_client()
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.write_preference(client, typed_record(), state=state))
    assert error.value.mutation_sent is False and client._batch_execute.await_count == 0


def test_thread_unknown_source_and_retargeted_function_cannot_be_written():
    for record, override in (
        (typed_record(scope=1), None), (typed_record(scope=0), None),
        (typed_record(source='unrecognized'), None), (typed_record(), native_id('other_tool')),
    ):
        client = fake_client()
        with pytest.raises(transport.PreferenceTransportError) as error:
            asyncio.run(transport.write_preference(client, record, state=1, native_permission_id=override))
        assert error.value.mutation_sent is False and client._batch_execute.await_count == 0


@pytest.mark.parametrize('invalid', [123, 'PRIVATE_ID', None])
def test_invalid_native_record_identity_is_a_safe_unsent_failure(invalid):
    client = fake_client()
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.write_preference(client, typed_record(native_permission_id=invalid), state=1))
    assert error.value.mutation_sent is False and client._batch_execute.await_count == 0
    assert 'PRIVATE_ID' not in str(error.value)


@pytest.mark.parametrize('bad', [
    response('preferences.update', []), response('preferences.update', [False]),
    response('preferences.update', [raw_permission()], reject=7),
    SimpleNamespace(status_code=503, text='PRIVATE_RESPONSE'),
    SimpleNamespace(status_code=200, text='PRIVATE_RESPONSE'),
])
def test_returned_but_unaccepted_mutation_reply_preserves_sent_true_without_replay(bad):
    client = fake_client(bad)
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.write_preference(client, typed_record(), state=1))
    assert error.value.mutation_sent is True and client._batch_execute.await_count == 1
    assert 'PRIVATE_RESPONSE' not in str(error.value)


def test_lost_mutation_response_is_unknown_and_error_does_not_disclose_native_data():
    client = fake_client(TimeoutError('PRIVATE_RESPONSE grant-fixture context-fixture'))
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.write_preference(client, typed_record(), state=1))
    assert error.value.mutation_sent is None and client._batch_execute.await_count == 1
    assert all(value not in str(error.value) for value in ('PRIVATE_RESPONSE', 'grant-fixture', 'context-fixture'))
    assert error.value.__cause__ is None


def test_non_http_callback_return_does_not_claim_the_mutation_was_sent():
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.write_preference(fake_client(None), typed_record(), state=1))
    assert error.value.mutation_sent is None


@pytest.mark.parametrize('mutation', [False, True])
def test_account_switch_after_await_scrubs_data_and_preserves_dispatch_evidence(monkeypatch, mutation):
    owner = {'value': 'account-a'}
    monkeypatch.setattr(transport, 'current_account_key', lambda: owner['value'])
    async def switch(*args, **kwargs):
        owner['value'] = 'account-b'
        return response('preferences.update' if mutation else 'preferences.list',
                        [raw_permission()] if mutation else [[raw_permission()]])
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=switch))
    with pytest.raises(transport.PreferenceTransportError) as error:
        if mutation:
            asyncio.run(transport.write_preference(client, typed_record(), state=1))
        else:
            asyncio.run(transport.read_preferences(client))
    assert error.value.mutation_sent is mutation
    assert 'grant-fixture' not in str(error.value)


def test_missing_account_prevents_even_the_initial_read(monkeypatch):
    monkeypatch.setattr(transport, 'current_account_key', lambda: None)
    client = fake_client()
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.read_preferences(client))
    assert error.value.mutation_sent is False and client._batch_execute.await_count == 0


def catalog_app(name, permissions=(), children=()):
    raw = [name] + [None] * 22
    raw[17], raw[22] = list(children), list(permissions)
    return raw


def test_catalog_recurses_native_children_and_keeps_unstored_defaults_distinct():
    default = raw_permission(grant='', scope=0, tool='child_tool')
    default[1] = None
    default[2] = None
    body = [[catalog_app('parent', [raw_permission()], [catalog_app('child', [default])])]]
    client = fake_client(response('preferences.catalog', body))
    records = asyncio.run(transport.read_permission_catalog(client, locale='en'))
    assert [record.tool_name for record in records] == ['fixture_tool', 'child_tool']
    assert all(record.source == 'catalog' for record in records)
    assert records[1].grant_id == '' and records[1].scope == 0
    assert records[1].context is None and records[1].native_grant_identity == []
    assert json.loads(client._batch_execute.await_args.args[0][0].payload) == ['en', None, None, None, True]
    assert client._batch_execute.await_args.kwargs['current_retry'] == 0
    writer = fake_client(response('preferences.update', [raw_permission(tool='child_tool')]))
    asyncio.run(transport.write_preference(writer, records[1], state=2))
    assert json.loads(writer._batch_execute.await_args.args[0][0].payload)[0] == ''


@pytest.mark.parametrize('body', [
    [[catalog_app('app', [raw_permission(), raw_permission()])]],
    [[catalog_app('app', children=[False])]], [[catalog_app('app', permissions=[False])]],
    [[[]]], [[catalog_app('app', permissions=[[[], None, None, None, [], 2]])]],
    [None, 'unrecognized-layout'],
])
def test_catalog_unknown_identity_duplicate_or_changed_layout_fails_closed(body):
    with pytest.raises(transport.PreferenceTransportError):
        asyncio.run(transport.read_permission_catalog(fake_client(response('preferences.catalog', body))))


def test_catalog_empty_never_invents_action_names_or_persistent_grants():
    client = fake_client(response('preferences.catalog', [[]]))
    assert asyncio.run(transport.read_permission_catalog(client)) == ()


def test_transport_waits_are_bounded_and_unknown_delivery_is_not_retried(monkeypatch):
    observed = []
    async def timeout(awaitable, *, timeout):
        observed.append(timeout)
        awaitable.close()
        raise TimeoutError('PRIVATE_RESPONSE')
    monkeypatch.setattr(transport, 'await_before_deadline', timeout)
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.write_preference(fake_client(), typed_record(), state=1))
    assert observed == [30] and error.value.mutation_sent is None


@pytest.mark.parametrize('body', [[], [None]])
def test_internal_revoke_sends_only_the_exact_granted_id_and_global_wire_scope(body):
    record = typed_record(context=None)
    client = fake_client(response('preferences.revoke', body))
    assert asyncio.run(transport.revoke_preference(client, record)) is None
    assert client._batch_execute.await_count == 1  # No inferred absence or automatic read-back.
    call = client._batch_execute.await_args
    assert call.kwargs['current_retry'] == 0
    assert json.loads(call.args[0][0].payload) == [['grant-fixture', 2]]


@pytest.mark.parametrize('record', [
    typed_record(context=None, scope=0, source='catalog', grant_id=''),
    typed_record(context=None, source='catalog'), typed_record(context=None, scope=1),
    typed_record(context=None, scope=True), typed_record(context=[]), typed_record(context=[1, 'thread']),
    typed_record(context=None, grant_id=''), typed_record(context=None, grant_id=' '),
    typed_record(context=None, native_grant_identity=['other-grant', 2]),
    typed_record(context=None, native_grant_identity=['grant-fixture', 1]),
    typed_record(context=None, native_grant_identity=['grant-fixture']),
    typed_record(context=None, native_grant_identity={'unexpected': 'identity'}),
])
def test_internal_revoke_refuses_unstored_thread_empty_or_inconsistent_grants_before_dispatch(record):
    client = fake_client()
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.revoke_preference(client, record))
    assert error.value.mutation_sent is False and client._batch_execute.await_count == 0


@pytest.mark.parametrize('bad', [
    response('preferences.revoke', [], reject=7),
    response('preferences.revoke', {'unexpected': 'shape'}),
    SimpleNamespace(status_code=200, text='PRIVATE_RESPONSE'),
    SimpleNamespace(status_code=503, text='PRIVATE_RESPONSE'),
    SimpleNamespace(status_code=200, text='[]'),
])
def test_revoke_rejection_or_unaccepted_envelope_is_sent_unverified_without_replay(bad):
    client = fake_client(bad)
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.revoke_preference(client, typed_record(context=None)))
    assert error.value.mutation_sent is True and client._batch_execute.await_count == 1
    assert 'PRIVATE_RESPONSE' not in str(error.value)


def test_revoke_transport_loss_is_unknown_and_never_replayed():
    client = fake_client(TimeoutError('PRIVATE_RESPONSE'))
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.revoke_preference(client, typed_record(context=None)))
    assert error.value.mutation_sent is None and client._batch_execute.await_count == 1
    assert 'PRIVATE_RESPONSE' not in str(error.value)


def test_revoke_account_switch_after_response_does_not_return_an_accepted_ack(monkeypatch):
    owner = {'key': 'account-a'}
    monkeypatch.setattr(transport, 'current_account_key', lambda: owner['key'])
    async def switch(*args, **kwargs):
        owner['key'] = 'account-b'
        return response('preferences.revoke', [])
    client = SimpleNamespace(_batch_execute=AsyncMock(side_effect=switch))
    with pytest.raises(transport.PreferenceTransportError) as error:
        asyncio.run(transport.revoke_preference(client, typed_record(context=None)))
    assert error.value.mutation_sent is True and client._batch_execute.await_count == 1
