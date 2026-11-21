"""Request-local model policy context and observed selection metadata."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


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


class ModelPolicyError(RuntimeError):
    code = "CAPABILITY_UNAVAILABLE"


_policy_context: ContextVar[ModelPolicyContext] = ContextVar(
    "gemini_model_policy", default=ModelPolicyContext("unspecified"),
)


@contextmanager
def model_policy_scope(purpose: str, *, allow_fallback: bool = False) -> Iterator[None]:
    token = _policy_context.set(ModelPolicyContext(purpose, allow_fallback))
    try:
        yield
    finally:
        _policy_context.reset(token)


def current_model_policy() -> ModelPolicyContext:
    return _policy_context.get()


def model_selection_from_response(response: Any) -> ModelSelection | None:
    value = getattr(response, "_mcp_model_selection", None)
    return value if isinstance(value, ModelSelection) else None
