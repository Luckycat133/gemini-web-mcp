"""Discovered model families, membership policy and request-local metadata."""

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import re
from typing import Any, Iterator, Literal

from gemini_webapi.constants import AccountStatus

from ..constants import gemini_4_model_family, is_auto_model_name, normalize_model_alias


AccountTier = Literal["free", "plus", "pro", "ultra"]
_TIER_IDS: dict[int, AccountTier] = {1: "free", 2: "pro", 3: "ultra", 4: "plus", 6: "ultra"}
# Current Web model availability is documented at:
# https://support.google.com/gemini/answer/18648722?hl=en (checked 2026-11-21).
# Legacy families remain selectable only if separately present in the account's
# discovered registry. These sets never substitute one model family for another.
_CHAT_FAMILIES: dict[AccountTier | None, frozenset[str]] = {
    "free": frozenset({"auto", "lite"}),
    "plus": frozenset({"auto", "flash", "pro", "lite"}),
    "pro": frozenset({"auto", "flash", "pro", "argon", "neon", "lite"}),
    "ultra": frozenset({"auto", "flash", "pro", "argon", "neon", "carbon", "lite"}),
    None: frozenset({"flash", "lite"}),
}
_TASK_FAMILIES: dict[AccountTier, frozenset[str]] = {
    "free": frozenset({"flash"}),
    "plus": frozenset({"flash"}),
    "pro": frozenset({"flash", "argon", "neon"}),
    "ultra": frozenset({"flash", "argon", "neon", "carbon"}),
}


def account_model_tier(client: Any) -> AccountTier | None:
    """Read a verified signed-in tier rather than infer it from a model name."""
    if getattr(client, "account_status", None) != AccountStatus.AVAILABLE:
        return None
    usage = getattr(client, "usage_info", None)
    tier = usage.get("tier") if isinstance(usage, Mapping) else None
    if not isinstance(tier, Mapping):
        return None
    tier_id = tier.get("id")
    name = _TIER_IDS.get(tier_id) if type(tier_id) is int else None
    return name if name is not None and tier.get("label") == name.upper() else None


def is_flash_lite_model_name(value: str) -> bool:
    normalized = re.sub(r"[\s_-]+", "-", value.strip().lower())
    return normalized == "lite" or bool(re.search(r"(?<![a-z0-9])flash-?lite(?![a-z0-9])", normalized))


def model_policy_family(model: Any) -> str:
    """Classify exact discovered labels and reject conflicting family evidence."""
    labels = (getattr(model, "model_name", ""), getattr(model, "display_name", ""),
              *(getattr(model, "aliases", None) or ()))
    families = set()
    for label in labels:
        if not isinstance(label, str):
            continue
        normalized = re.sub(r"[\s_-]+", "-", label.strip().lower())
        if is_flash_lite_model_name(normalized):
            families.add("lite")
        elif is_auto_model_name(normalized):
            families.add("auto")
        elif family := gemini_4_model_family(normalized):
            families.add("flash" if family == "hydrogen" else family)
        elif match := re.fullmatch(r"(?:gemini-)?(?:\d+(?:\.\d+)?-)?(flash|pro)(?:-preview)?", normalized):
            families.add(match.group(1))
    if "lite" in families and len(families) > 1:
        raise ModelPolicyError(
            "Conflicting Flash-Lite metadata cannot bypass verified Flash quota exhaustion policy."
        )
    return next(iter(families)) if len(families) == 1 else "unknown"


def membership_restriction(tier: AccountTier | None, family: str, purpose: str) -> str | None:
    """Apply membership restrictions after the model has matched the registry."""
    if purpose == "task":
        if tier is None:
            return "Task requires a verified Gemini membership."
        allowed = _TASK_FAMILIES[tier]
    else:
        allowed = _CHAT_FAMILIES[tier]
    if family not in allowed:
        if tier is None:
            return "Gemini membership could not be verified. Only the discovered default Flash family is enabled."
        if family == "carbon":
            return "Gemini 4 Carbon requires Google AI Ultra."
        if family in {"argon", "neon"}:
            return f"Gemini 4 {family.title()} requires Google AI Pro or Ultra."
        if tier == "free" and family == "flash":
            return "This free Gemini plan does not allow a manual Flash or Hydrogen selection in Chat."
        return "The requested model is unavailable for this workflow on this Gemini plan."
    if family == "lite" and purpose not in {"query", "unspecified"}:
        return "Flash-Lite is reserved for ordinary Chat quota fallback."
    if family == "auto" and purpose not in {"query", "notebook_chat", "unspecified"}:
        return "Auto is only available for Chat model selection."
    return None


_VIDEO_COMPATIBLE_ALIASES = frozenset({"flash", "hydrogen", "pro", "argon", "neon", "carbon"})


def video_model_compatibility_error(model: str | None) -> str | None:
    """Validate Web chat aliases without confusing an explicit API model ID."""
    if model is None or not model.strip():
        return None
    if is_flash_lite_model_name(model):
        return "Flash-Lite is reserved for ordinary Chat quota fallback. Use the default media model."
    if normalize_model_alias(model) in _VIDEO_COMPATIBLE_ALIASES:
        return None
    return f"Model {model!r} is not supported for Web video generation; omit model to use the default Hydrogen route."


MODEL_MEMBERSHIP_VERIFICATION = "model_membership_unavailable"


@dataclass(frozen=True)
class ModelSelection:
    requested_model: str
    effective_model: str
    fallback_reason: str | None
    thinking_selectable: bool
    effective_thinking_level: str | None


@dataclass(frozen=True)
class ModelPolicyContext:
    purpose: str
    allow_fallback: bool = False
    requested_alias: str | None = None


class ModelPolicyError(RuntimeError):
    code = "CAPABILITY_UNAVAILABLE"


_policy_context: ContextVar[ModelPolicyContext] = ContextVar(
    "gemini_model_policy", default=ModelPolicyContext("unspecified"),
)


@contextmanager
def model_policy_scope(
    purpose: str, *, allow_fallback: bool = False, requested_alias: str | None = None,
) -> Iterator[None]:
    token = _policy_context.set(ModelPolicyContext(purpose, allow_fallback, requested_alias))
    try:
        yield
    finally:
        _policy_context.reset(token)


def current_model_policy() -> ModelPolicyContext:
    return _policy_context.get()


def model_selection_from_response(response: Any) -> ModelSelection | None:
    value = getattr(response, "_mcp_model_selection", None)
    return value if isinstance(value, ModelSelection) else None
