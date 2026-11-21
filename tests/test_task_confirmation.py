"""Synthetic native card shapes; no account values, URL tokens or live reads."""

from copy import deepcopy
import json

import pytest

from src.infrastructure.task_confirmation import parse_confirmation_replies


def _button(label='Allow', prompt='synthetic-allow'):
    return [label, [None, None, None, prompt]]


def _remote_card():
    allow, deny = _button(), _button('Do not allow', 'synthetic-deny')
    details = [[None, None, 'Public read-only fixture context']]
    override = ['Browser permission', 'Synthetic public browsing only', None, None, details,
                None, [allow], [deny]]
    return [None, 'Browser permission', 'Synthetic public browsing only', None, None, None,
            None, [allow], [deny], 1, None, details, None,
            [['vm', 'Remote browser', 'Works away from the device', override, None, 'cloud']]]


def _parse(card, *, rid='r_fixture'):
    return parse_confirmation_replies(card, chat_id='c_fixture', response_id=rid, candidate_id='rc_fixture')


def test_real_remote_permission_shape_includes_field8_allow_and_opaque_choice_bound_options():
    card = _remote_card()
    before = deepcopy(card)
    parsed = _parse(card)
    assert parsed['input_card']['requires_option_id'] is True
    assert [(option['label'], option['prompt'], option['choice_label']) for option in parsed['input_options']] == [
        ('Allow', 'synthetic-allow', 'Remote browser'), ('Do not allow', 'synthetic-deny', 'Remote browser'),
    ]
    assert all(reply['action_choice_id'] == 'vm' for reply in parsed['input_card']['replies'])
    assert [reply['action_path'] for reply in parsed['input_card']['replies']] == [[7, 0], [8, 0]]
    assert card == before
    assert 'action_choice_id' not in json.dumps(parsed['input_options'])


def test_same_button_query_in_different_native_choices_has_distinct_ids_and_exact_override():
    card = _remote_card()
    second = deepcopy(card[13][0])
    second[0:2] = ['local_chrome:synthetic-device', 'Local browser']
    second[3][6] = [_button('Allow local', 'synthetic-allow')]
    card[13].append(second)
    parsed = _parse(card)
    assert len(parsed['input_options']) == 4
    assert len({option['input_option_id'] for option in parsed['input_options']}) == 4
    assert parsed['input_options'][2]['label'] == 'Allow local'
    assert parsed['input_card']['replies'][2]['action_choice_id'] == 'local_chrome:synthetic-device'


@pytest.mark.parametrize('change', ['turn', 'body', 'choice_id', 'button', 'extra_metadata'])
def test_option_ids_bind_the_exact_turn_and_entire_card_not_only_its_button_text(change):
    card = _remote_card()
    first = _parse(card)['input_options'][0]['input_option_id']
    rid = 'r_changed' if change == 'turn' else 'r_fixture'
    if change == 'body':
        card[2] = 'Changed requested effect'
    elif change == 'choice_id':
        card[13][0][0] = 'different-returned-native-choice'
    elif change == 'button':
        card[13][0][3][6][0][1][3] = 'changed-exact-query'
    elif change == 'extra_metadata':
        card[11][0][2] = 'Changed native context'
    assert _parse(card, rid=rid)['input_options'][0]['input_option_id'] != first


@pytest.mark.parametrize('change', ['subagent', 'selected', 'agent_auth', 'duplicate_choices', 'invalid_choices',
                                    'unknown_choice_field', 'unknown_override_field', 'action_output', 'hidden_control',
                                    'unknown_button_control', 'ambiguous_oneof', 'malformed_group', 'unknown_card_context'])
def test_unsupported_or_unverified_native_card_shapes_expose_no_executable_reply(change):
    card = _remote_card()
    if change in ('subagent', 'selected', 'agent_auth'):
        card.extend([None] * (20 - len(card)))
        card[{'subagent': 18, 'selected': 14, 'agent_auth': 19}[change]] = 'synthetic-native-context'
    elif change == 'duplicate_choices':
        card[13].append(deepcopy(card[13][0]))
    elif change == 'invalid_choices':
        card[13] = {'not': 'a-native-array'}
    elif change == 'unknown_choice_field':
        card[13][0].append('unknown-field')
    elif change == 'unknown_override_field':
        card[13][0][3].append('unknown-field')
    elif change == 'action_output':
        card[13][0][3][6][0][1] = [None, None, ['unknown-output-oneof']]
    elif change in ('hidden_control', 'unknown_button_control'):
        card[13][0][3][6][0].extend([False, True if change == 'hidden_control' else ['unknown-control']])
    elif change == 'ambiguous_oneof':
        card[13][0][3][6][0][1][1] = 'https://example.invalid/'
    elif change == 'unknown_card_context':
        card.extend([None] * (20 - len(card)))
        card.append(['unknown-context'])
    else:
        card[13][0][3][6] = 'not-a-button-list'
    parsed = _parse(card)
    assert not parsed['input_options'] and not parsed['input_card']['replies']
    assert parsed['input_card']['unsupported'] is True


def test_disabled_native_button_is_not_an_executable_option_and_empty_override_uses_parent_group():
    card = _remote_card()
    card[13][0][3][6] = []
    card[7][0].append(True)
    parsed = _parse(card)
    assert [option['label'] for option in parsed['input_options']] == ['Do not allow']
    assert parsed['input_card']['replies'][0]['action_choice_id'] == 'vm'
