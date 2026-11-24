"""Fixture-backed current Web media companion parser contracts."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from src.infrastructure.media_companions import parse_media_companions


FIXTURES = json.loads((Path(__file__).parent / "fixtures/media_companions.json").read_text())


def success_body():
    return deepcopy(FIXTURES["cases"]["success"]["body"])


@pytest.mark.parametrize("status", ["success", "empty", "rejected", "changed_shape"])
def test_media_companion_fixture_covers_each_response_state(status):
    fixture = FIXTURES["cases"][status]
    result = parse_media_companions(fixture["body"], reject_code=fixture.get("reject_code"))
    assert result.status == status


def test_defaults_preserve_exact_current_provider_option_ids_and_global_choices():
    result = parse_media_companions(success_body())
    catalog = result.value
    assert catalog is not None
    assert catalog.default_option_ids(21) == (42, 26, 80)
    assert catalog.default_option_ids(14) == (64, 80)
    assert catalog.default_option_ids(11) == (13, 80)
    music = catalog.by_mode[21][0]
    assert music.mode == 21 and music.id == 101 and music.title == "Length"
    assert [(option.id, option.title, option.is_default) for option in music.options] == [
        (42, "Short", True), (43, "Standard", False), (69, "Custom", False),
    ]
    assert not result.warnings


def test_capability_requirements_gate_both_groups_and_choices():
    result = parse_media_companions(success_body())
    catalog = result.value
    assert catalog is not None
    gated_image = catalog.by_mode[14][1]
    assert gated_image.required_capability == 99
    assert gated_image.available is False
    assert gated_image.options[0].available is False
    gated_video = catalog.by_mode[11][0].options[2]
    assert gated_video.is_default is True and gated_video.available is False
    assert 15 not in catalog.default_option_ids(11)


def test_missing_default_is_reported_without_selecting_first_available_choice():
    body = success_body()
    body[19][0][7][0][2] = False
    result = parse_media_companions(body)
    assert result.value is not None
    assert result.value.by_mode[21][0].default_option_ids == ()
    assert result.value.default_option_ids(21) == (26, 80)
    assert "media_companion_default_unconfirmed:21:101" in result.warnings


def test_missing_capability_list_cannot_authorize_gated_default_choices():
    body = success_body()
    body[17] = None
    result = parse_media_companions(body)
    assert result.status == "success" and result.value is not None
    assert result.value.default_option_ids(21) == (42, 26)


def test_current_authenticated_capability_catalog_accepts_unspecified_zero():
    body = success_body()
    body[17] = [0, 185, 230, 246]
    body[19][0][7][0][1] = 185
    result = parse_media_companions(body)
    assert result.status == "success" and result.value is not None
    assert result.value.capabilities == frozenset({0, 185, 230, 246})
    assert result.value.default_option_ids(21) == (42, 26)
    assert result.value.by_mode[21][0].options[0].available is True


@pytest.mark.parametrize("capability", [-1, True, "185", 185.0, {}])
def test_invalid_capability_values_still_fail_closed(capability):
    body = success_body()
    body[17] = [0, capability]
    assert parse_media_companions(body).status == "changed_shape"


def test_no_companions_is_verified_empty_without_inventing_defaults():
    body = success_body()
    body[19] = []
    result = parse_media_companions(body)
    assert result.status == "empty" and result.value is not None
    assert result.value.default_option_ids(21) == ()


def test_account_rejection_is_preserved_without_exposing_options():
    body = success_body()
    body[14] = 1016
    result = parse_media_companions(body)
    assert result.status == "rejected" and result.reject_code == 1016
    assert result.value is None


@pytest.mark.parametrize("field,value", [(0, True), (0, "42"), (0, -1), (2, 1), (2, "true"), (3, 123)])
def test_malformed_option_identity_default_or_title_fails_closed(field, value):
    body = success_body()
    body[19][0][7][0][field] = value
    result = parse_media_companions(body)
    assert result.status == "changed_shape" and result.value is None


@pytest.mark.parametrize("position,value", [(0, True), (1, "7"), (7, {}), (8, False), (8, -1)])
def test_malformed_relevant_group_fails_closed(position, value):
    body = success_body()
    body[19][0][position] = value
    assert parse_media_companions(body).status == "changed_shape"


def test_duplicate_group_and_option_ids_are_not_silently_merged():
    body = success_body()
    body[19].append(deepcopy(body[19][0]))
    assert parse_media_companions(body).status == "changed_shape"
    body = success_body()
    body[19][0][7].append(deepcopy(body[19][0][7][0]))
    assert parse_media_companions(body).status == "changed_shape"


def test_conflicting_single_choice_defaults_are_not_guessed():
    body = success_body()
    body[19][0][7][1][2] = True
    assert parse_media_companions(body).status == "changed_shape"
    body[19][0][2] = 2
    result = parse_media_companions(body)
    assert result.value is not None
    assert result.value.by_mode[21][0].default_option_ids == (42, 43)


def test_parser_does_not_mutate_body_or_allow_mutating_result_catalog():
    body = success_body()
    original = deepcopy(body)
    result = parse_media_companions(body)
    assert body == original and result.value is not None
    with pytest.raises(TypeError):
        result.value.by_mode[21] = ()
    with pytest.raises(FrozenInstanceError):
        result.value.by_mode[21][0].title = "changed"


@pytest.mark.parametrize("body", [{}, None, [], [None] * 19])
def test_missing_status_shape_does_not_claim_known_empty_options(body):
    assert parse_media_companions(body).status == "changed_shape"
