"""Current Web family and plan policy without opaque selector substitutions."""

from types import SimpleNamespace

from gemini_webapi.constants import AccountStatus
import pytest

from src.constants import (
    OMNI_VIDEO_MODEL,
    gemini_4_model_family,
    is_auto_model_name,
    normalize_model_alias,
    resolve_model_name,
    resolve_media_request,
)
from src.services.model_policy import (
    ModelPolicyError,
    account_model_tier,
    membership_restriction,
    model_policy_family,
    video_model_compatibility_error,
)


@pytest.mark.parametrize("label", ["neon", "Neon", "4 Neon", "gemini-neon", "gemini-4-neon", "4_neon"])
def test_neon_aliases_preserve_the_discovered_neon_family(label):
    assert gemini_4_model_family(label) == "neon"
    assert normalize_model_alias(label) == "neon"
    assert resolve_model_name(label) == "gemini-neon"


@pytest.mark.parametrize("label", ["auto", "Auto", "自动", "gemini-自动", "gemini-auto"])
def test_auto_aliases_can_match_localized_registry_names(label):
    assert is_auto_model_name(label)
    assert normalize_model_alias(label) == "auto"
    assert resolve_model_name(label) == "auto"


def test_argon_is_preserved_as_a_distinct_legacy_model():
    assert resolve_model_name("argon") == "gemini-argon"
    assert resolve_model_name("argon") != resolve_model_name("neon")


@pytest.mark.parametrize(("name", "display", "aliases", "family"), [
    ("gemini-neon", "更深入的分析", ["neon"], "neon"),
    ("gemini-4-hydrogen", "日常帮助", ["4 hydrogen"], "flash"),
    ("gemini-carbon", "代码和复杂工作", ["carbon"], "carbon"),
    ("gemini-自动", "自动", [], "auto"),
    ("gemini-3-flash", "3.8 Flash", [], "flash"),
    ("Flash-Lite", "3.5 Flash-Lite", [], "lite"),
    ("gemini-neon", "更深入的分析", ["argon"], "unknown"),
    ("gemini-future", "Unrecognized", [], "unknown"),
])
def test_current_registry_labels_classify_without_generic_display_name_guesses(name, display, aliases, family):
    model = SimpleNamespace(model_name=name, display_name=display, aliases=aliases)
    assert model_policy_family(model) == family


def test_conflicting_lite_metadata_is_refused_before_quota_can_authorize_it():
    model = SimpleNamespace(model_name="gemini-neon", display_name="更深入的分析", aliases=["flash-lite"])
    with pytest.raises(ModelPolicyError, match="Conflicting Flash-Lite metadata"):
        model_policy_family(model)


@pytest.mark.parametrize("purpose", ["query", "task", "media", "notebook_chat"])
@pytest.mark.parametrize("tier", ["pro", "ultra"])
def test_neon_available_on_verified_pro_and_ultra(purpose, tier):
    assert membership_restriction(tier, "neon", purpose) is None


@pytest.mark.parametrize("tier", [None, "free", "plus"])
def test_neon_remains_restricted_without_eligible_membership(tier):
    assert membership_restriction(tier, "neon", "query") is not None


@pytest.mark.parametrize("tier", ["free", "plus", "pro", "ultra"])
def test_current_web_task_hydrogen_is_available_on_each_verified_plan(tier):
    assert membership_restriction(tier, "flash", "task") is None


@pytest.mark.parametrize("tier", ["free", "plus", "pro", "ultra"])
def test_auto_is_available_for_chat_but_not_task_or_media(tier):
    assert membership_restriction(tier, "auto", "query") is None
    assert membership_restriction(tier, "auto", "task") is not None
    assert membership_restriction(tier, "auto", "media") is not None


@pytest.mark.parametrize(("tier_id", "label", "expected"), [
    (1, "FREE", "free"), (4, "PLUS", "plus"), (2, "PRO", "pro"),
    (3, "ULTRA", "ultra"), (6, "ULTRA", "ultra"),
    (True, "FREE", None), (3, "PRO", None), (99, "ULTRA", None),
])
def test_membership_uses_matching_authenticated_tier_id_and_label(tier_id, label, expected):
    client = SimpleNamespace(account_status=AccountStatus.AVAILABLE,
                             usage_info={"tier": {"id": tier_id, "label": label}})
    assert account_model_tier(client) == expected
    client.account_status = AccountStatus.UNAUTHENTICATED
    assert account_model_tier(client) is None


def test_neon_web_video_alias_is_accepted_without_allowing_arbitrary_models():
    assert video_model_compatibility_error("gemini-4-neon") is None
    assert video_model_compatibility_error("unknown-future-model") is not None
    assert video_model_compatibility_error("auto") is not None


@pytest.mark.parametrize("kind", ["image", "video", "music"])
def test_omitted_media_model_selects_the_discovered_hydrogen_family(kind):
    selection = resolve_media_request(None, kind)
    assert selection["requested_alias"] == "hydrogen"
    assert selection["effective_alias"] == "hydrogen"
    assert selection["request_model"] == "gemini-4-hydrogen"


@pytest.mark.parametrize("kind", ["image", "video", "music"])
@pytest.mark.parametrize("name", ["hydrogen", "neon", "carbon", "argon", "pro"])
def test_explicit_media_model_is_never_silently_replaced_by_flash(kind, name):
    selection = resolve_media_request(name, kind)
    assert selection["requested_alias"] == name
    assert selection["effective_alias"] == name
    assert selection["request_model"] == resolve_model_name(name)


@pytest.mark.parametrize("kind", ["image", "video", "music"])
def test_web_route_label_never_asserts_an_unobserved_generator_version(kind):
    selection = resolve_media_request(None, kind)
    assert selection["backend_label"] == f"Gemini Web {kind} mode"


def test_explicit_api_video_model_keeps_the_separate_key_based_route():
    selection = resolve_media_request(OMNI_VIDEO_MODEL, "video")
    assert selection["request_model"] == OMNI_VIDEO_MODEL
    assert selection["backend_label"].startswith("Gemini API")
