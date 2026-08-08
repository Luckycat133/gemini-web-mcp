"""Stable, serializable success and failure contracts for agent-facing workflows."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, TypeVar


class DomainErrorCode(str, Enum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    UPSTREAM_REJECTED = "UPSTREAM_REJECTED"
    UPSTREAM_CHANGED = "UPSTREAM_CHANGED"
    NETWORK_ERROR = "NETWORK_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    ARTIFACT_NOT_RETURNED = "ARTIFACT_NOT_RETURNED"
    ARTIFACT_SAVE_FAILED = "ARTIFACT_SAVE_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class OperationState(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DomainError:
    code: DomainErrorCode
    message: str
    retryable: bool = False
    suggested_action: str | None = None
    diagnostic_id: str | None = None


@dataclass(frozen=True)
class DomainWarning:
    code: str
    message: str
    suggested_action: str | None = None


@dataclass(frozen=True)
class ResultMeta:
    request_id: str
    operation_state: OperationState
    observed_at: str
    requested_backend: str | None = None
    effective_backend: str | None = None
    verification_status: str = "not_applicable"
    diagnostic_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        operation_state: OperationState,
        *,
        request_id: str | None = None,
        observed_at: str | None = None,
        requested_backend: str | None = None,
        effective_backend: str | None = None,
        verification_status: str = "not_applicable",
        diagnostic_id: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> ResultMeta:
        return cls(
            request_id=request_id or new_request_id(),
            operation_state=operation_state,
            observed_at=observed_at or datetime.now(timezone.utc).isoformat(),
            requested_backend=requested_backend,
            effective_backend=effective_backend,
            verification_status=verification_status,
            diagnostic_id=diagnostic_id,
            details=dict(details or {}),
        )


T = TypeVar("T")
_USE_RESULT_DATA = object()


@dataclass(frozen=True)
class DomainResult(Generic[T]):
    ok: bool
    data: T | None
    error: DomainError | None
    warnings: tuple[DomainWarning, ...]
    meta: ResultMeta

    def __post_init__(self) -> None:
        if self.ok and self.error is not None:
            raise ValueError("successful DomainResult cannot contain an error")
        if not self.ok and self.error is None:
            raise ValueError("failed DomainResult requires an error")
        failed_states = {
            OperationState.FAILED,
            OperationState.TIMED_OUT,
            OperationState.CANCELLED,
            OperationState.UNAVAILABLE,
        }
        if self.ok and self.meta.operation_state in failed_states:
            raise ValueError("successful DomainResult cannot use a failure operation state")
        if not self.ok and self.meta.operation_state not in failed_states:
            raise ValueError("failed DomainResult requires a failure operation state")
        if self.error is not None and self.error.diagnostic_id != self.meta.diagnostic_id:
            raise ValueError("error and result metadata diagnostic IDs must match")

    @classmethod
    def success(
        cls,
        data: T | None = None,
        *,
        operation_state: OperationState = OperationState.COMPLETED,
        warnings: Sequence[DomainWarning] = (),
        request_id: str | None = None,
        requested_backend: str | None = None,
        effective_backend: str | None = None,
        verification_status: str = "not_applicable",
        details: Mapping[str, Any] | None = None,
    ) -> DomainResult[T]:
        return cls(
            ok=True,
            data=data,
            error=None,
            warnings=tuple(warnings),
            meta=ResultMeta.create(
                operation_state,
                request_id=request_id,
                requested_backend=requested_backend,
                effective_backend=effective_backend,
                verification_status=verification_status,
                details=details,
            ),
        )

    @classmethod
    def failure(
        cls,
        code: DomainErrorCode,
        message: str,
        *,
        data: T | None = None,
        retryable: bool = False,
        suggested_action: str | None = None,
        operation_state: OperationState = OperationState.FAILED,
        warnings: Sequence[DomainWarning] = (),
        request_id: str | None = None,
        diagnostic_id: str | None = None,
        requested_backend: str | None = None,
        effective_backend: str | None = None,
        verification_status: str = "not_applicable",
        details: Mapping[str, Any] | None = None,
    ) -> DomainResult[T]:
        error = DomainError(
            code=code,
            message=message,
            retryable=retryable,
            suggested_action=suggested_action,
            diagnostic_id=diagnostic_id,
        )
        return cls(
            ok=False,
            data=data,
            error=error,
            warnings=tuple(warnings),
            meta=ResultMeta.create(
                operation_state,
                request_id=request_id,
                diagnostic_id=diagnostic_id,
                requested_backend=requested_backend,
                effective_backend=effective_backend,
                verification_status=verification_status,
                details=details,
            ),
        )

    @property
    def error_code(self) -> str | None:
        return self.error.code.value if self.error is not None else None

    @property
    def operation_state(self) -> str:
        return self.meta.operation_state.value

    @property
    def retryable(self) -> bool:
        return bool(self.error and self.error.retryable)

    @property
    def suggested_action(self) -> str | None:
        return self.error.suggested_action if self.error is not None else None

    def to_dict(self, *, data: Any = _USE_RESULT_DATA) -> dict[str, Any]:
        public_data = self.data if data is _USE_RESULT_DATA else data
        return {
            "ok": self.ok,
            "data": _json_safe(public_data),
            "error": _json_safe(self.error),
            "warnings": [_json_safe(warning) for warning in self.warnings],
            "meta": _json_safe(self.meta),
        }


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def new_diagnostic_id() -> str:
    return f"diag_{uuid.uuid4().hex}"


def result_from_exception(
    error: BaseException,
    *,
    logger: logging.Logger,
    operation: str,
    request_id: str | None = None,
) -> DomainResult[Any]:
    """Classify one exception, log raw evidence, and return a public-safe failure."""
    request_id = request_id or new_request_id()
    diagnostic_id = new_diagnostic_id()
    code, message, retryable, suggested_action, state = _classify_exception(error)
    logger.error(
        "Domain operation failed operation=%s request_id=%s diagnostic_id=%s error_type=%s error=%r",
        operation,
        request_id,
        diagnostic_id,
        type(error).__name__,
        error,
        exc_info=(type(error), error, error.__traceback__),
    )
    return DomainResult.failure(
        code,
        message,
        retryable=retryable,
        suggested_action=suggested_action,
        operation_state=state,
        request_id=request_id,
        diagnostic_id=diagnostic_id,
        verification_status="exception_classified",
        details={"operation": operation},
    )


def _classify_exception(
    error: BaseException,
) -> tuple[DomainErrorCode, str, bool, str | None, OperationState]:
    if isinstance(error, asyncio.CancelledError):
        return (
            DomainErrorCode.CANCELLED,
            "The operation was cancelled.",
            True,
            "Retry the operation if it is still needed.",
            OperationState.CANCELLED,
        )
    if isinstance(error, TimeoutError):
        return (
            DomainErrorCode.TIMED_OUT,
            "The upstream operation timed out.",
            True,
            "Retry later or increase the operation timeout.",
            OperationState.TIMED_OUT,
        )
    if isinstance(error, PermissionError):
        return (
            DomainErrorCode.AUTH_REQUIRED,
            "Authentication is required for this operation.",
            False,
            "Load a valid Gemini Web cookie and retry.",
            OperationState.FAILED,
        )
    if isinstance(error, ValueError):
        return (
            DomainErrorCode.INVALID_ARGUMENT,
            "One or more arguments are invalid.",
            False,
            "Correct the arguments and retry.",
            OperationState.FAILED,
        )
    if isinstance(error, ConnectionError):
        return (
            DomainErrorCode.NETWORK_ERROR,
            "The upstream service could not be reached.",
            True,
            "Check network or proxy settings and retry.",
            OperationState.FAILED,
        )

    legacy_code = str(getattr(error, "code", "")).upper()
    error_text = str(error).lower()
    if legacy_code == "NO_COOKIE" or (
        ("psid" in error_text or "cookie" in error_text) and ("not set" in error_text or "missing" in error_text)
    ):
        return (
            DomainErrorCode.AUTH_REQUIRED,
            "Gemini Web authentication is not configured.",
            False,
            "Use gemini_get_cookie_from_browser or configure GEMINI_PSID, then retry.",
            OperationState.FAILED,
        )
    if legacy_code in {"AUTH_REQUIRED", "UNAUTHORIZED"} or any(
        marker in error_text for marker in ("authentication required", "not authenticated")
    ):
        return (
            DomainErrorCode.AUTH_REQUIRED,
            "Authentication is required for this operation.",
            False,
            "Load a valid Gemini Web cookie and retry.",
            OperationState.FAILED,
        )
    if legacy_code == "INVALID_COOKIE" or (
        ("cookie" in error_text or "psid" in error_text)
        and any(marker in error_text for marker in ("invalid", "expired", "rejected"))
    ):
        return (
            DomainErrorCode.AUTH_EXPIRED,
            "Gemini Web authentication is invalid or expired.",
            False,
            "Refresh the Gemini Web cookie and retry.",
            OperationState.FAILED,
        )
    if legacy_code == "SESSION_NOT_FOUND" or (
        "session" in error_text and ("not found" in error_text or "不存在" in error_text)
    ):
        return (
            DomainErrorCode.SESSION_NOT_FOUND,
            "The requested session does not exist.",
            False,
            "Create a session with gemini_start_chat and use the returned ID.",
            OperationState.FAILED,
        )
    if legacy_code in {"RATE_LIMIT", "RATE_LIMITED"} or "rate limit" in error_text:
        return (
            DomainErrorCode.RATE_LIMITED,
            "The upstream service rate limit was reached.",
            True,
            "Wait before retrying or use a lower-cost model.",
            OperationState.FAILED,
        )
    if "timeout" in error_text or "timed out" in error_text:
        return (
            DomainErrorCode.TIMED_OUT,
            "The upstream operation timed out.",
            True,
            "Retry later or increase the operation timeout.",
            OperationState.TIMED_OUT,
        )
    if legacy_code == "NETWORK_ERROR" or any(
        marker in error_text for marker in ("network", "connection refused", "connection reset")
    ):
        return (
            DomainErrorCode.NETWORK_ERROR,
            "The upstream service could not be reached.",
            True,
            "Check network or proxy settings and retry.",
            OperationState.FAILED,
        )
    if legacy_code == "MODEL_UNAVAILABLE" or (
        "model" in error_text and any(marker in error_text for marker in ("unavailable", "not available"))
    ):
        return (
            DomainErrorCode.CAPABILITY_UNAVAILABLE,
            "The requested model or capability is unavailable.",
            False,
            "Choose an available model or inspect account capabilities.",
            OperationState.UNAVAILABLE,
        )
    if legacy_code == "UPSTREAM_CHANGED" or any(
        marker in error_text for marker in ("upstream response changed", "response shape changed", "parse drift")
    ):
        return (
            DomainErrorCode.UPSTREAM_CHANGED,
            "The upstream response no longer matches the supported contract.",
            False,
            "Update the adapter or report the diagnostic ID.",
            OperationState.FAILED,
        )
    if any(marker in error_text for marker in ("upstream rejected", "forbidden", "permission denied")):
        return (
            DomainErrorCode.UPSTREAM_REJECTED,
            "The upstream service rejected the operation.",
            False,
            "Check account permissions and request parameters before retrying.",
            OperationState.FAILED,
        )
    return (
        DomainErrorCode.INTERNAL_ERROR,
        "An internal error prevented the operation from completing.",
        False,
        "Retry once; if it persists, report the diagnostic ID.",
        OperationState.FAILED,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        output = {}
        for item in fields(value):
            if item.name.startswith("_") or item.metadata.get("domain_exclude"):
                continue
            output[item.name] = _json_safe(getattr(value, item.name))
        return output
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _json_safe(value.to_dict())
    return None
