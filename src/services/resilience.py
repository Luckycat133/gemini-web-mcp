"""Transient error auto-recovery and structured rate-limit reset guidance."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import time
from typing import Any

logger = logging.getLogger(__name__)

_TRANSIENT_HTTP_STATUSES = frozenset({500, 502, 503, 504})
_TRANSIENT_MARKERS = (
    "connection reset",
    "connection refused",
    "connection closed abruptly",
    "empty reply",
    "recv failure",
    "send failure",
    "broken pipe",
    "ssl_connect",
    "remote protocol",
    "proxy error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
)
_RATE_LIMIT_DEFAULT_CAPACITY_WAIT_SECONDS = 600


def is_transient_error(error: BaseException) -> bool:
    """Return True only for recoverable transport/startup blips before remote commitment."""
    if isinstance(error, (asyncio.CancelledError, PermissionError, ValueError)):
        return False
    from ..client_manager import ClientAuthenticationRequired, ClientInitializationResetError, ClientRateLimited

    if isinstance(error, (ClientAuthenticationRequired, ClientRateLimited)):
        return False
    if isinstance(error, ClientInitializationResetError):
        return True
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        if status_code == 429 or 400 <= status_code < 500:
            return False
        if status_code in _TRANSIENT_HTTP_STATUSES:
            return True
    from ..domain.results import DomainErrorCode, classify_exception

    code = classify_exception(error).error.code  # type: ignore[union-attr]
    if code in {
        DomainErrorCode.RATE_LIMITED,
        DomainErrorCode.INVALID_ARGUMENT,
        DomainErrorCode.CAPABILITY_UNAVAILABLE,
        DomainErrorCode.UPSTREAM_REJECTED,
        DomainErrorCode.AUTH_REQUIRED,
        DomainErrorCode.AUTH_EXPIRED,
    }:
        return False
    if code in {DomainErrorCode.NETWORK_ERROR, DomainErrorCode.TIMED_OUT}:
        return True
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    lowered = str(error).lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


async def try_auto_refresh_auth(error: BaseException) -> bool:
    """Silently reload fresh Chrome cookies once when a cached session expired."""
    from ..client_manager import browser_cookie_auto_load_enabled, invalidate_verified_browser_session_cache
    from ..client_wrapper import ensure_web_cookie, initialize_client, reset_client_async
    from ..domain.results import DomainErrorCode, classify_exception

    if not browser_cookie_auto_load_enabled():
        return False
    classified = classify_exception(error)
    if classified.error is None or classified.error.code != DomainErrorCode.AUTH_EXPIRED:
        return False
    try:
        invalidate_verified_browser_session_cache()
        await reset_client_async()
        await asyncio.to_thread(ensure_web_cookie)
        await initialize_client()
        return True
    except Exception as refresh_error:
        logger.debug("Silent cookie refresh did not recover auth error_type=%s", type(refresh_error).__name__)
        return False


def _format_local_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_iso_timestamp(value: object) -> tuple[str | None, float | None]:
    if not isinstance(value, str) or not value.strip():
        return None, None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        ts = parsed.timestamp()
        return parsed.astimezone().isoformat(timespec="seconds"), ts
    except ValueError:
        return raw, None


def extract_quota_reset_from_quotas(quotas: Any) -> dict[str, Any] | None:
    """Return a future account-window reset only with a known depleted balance."""
    usage = quotas.get("usage_info") if isinstance(quotas, Mapping) else None
    if not isinstance(usage, Mapping):
        return None
    now = time.time()
    for window in ("weekly", "current_5h"):
        entry = usage.get(window)
        if not isinstance(entry, Mapping):
            continue
        remaining = entry.get("remaining_credits")
        if type(remaining) is not int or remaining > 0:
            continue
        reset_at, timestamp = _parse_iso_timestamp(entry.get("reset_at"))
        if reset_at and timestamp is not None and timestamp > now:
            return {
                "quota_window": window,
                "reset_at": reset_at,
                "reset_timestamp": timestamp,
                "remaining_credits": remaining,
            }
    return None


def persist_client_quota_snapshot(client: Any, *, account_key: str | None = None) -> dict[str, Any] | None:
    """Persist observed quota reset timestamp for offline/subsequent rate-limit guidance."""
    snapshot = extract_quota_reset_from_quotas(getattr(client, "quotas", None))
    if snapshot is None:
        return None
    from ..infrastructure.account_identity import current_account_key
    from ..infrastructure.model_policy_store import ModelPolicyStore

    resolved_key = account_key or current_account_key()
    if resolved_key:
        ModelPolicyStore().record_quota_snapshot(
            resolved_key,
            quota_window=str(snapshot["quota_window"]),
            reset_at=str(snapshot["reset_at"]),
            reset_timestamp=float(snapshot["reset_timestamp"]),
            remaining_credits=snapshot.get("remaining_credits"),
        )
    return snapshot


def _retry_after_header_seconds(error: BaseException | None, now: float) -> int | None:
    if error is None:
        return None
    headers = getattr(getattr(error, "response", None), "headers", None)
    if not isinstance(headers, Mapping):
        return None
    for key in ("Retry-After", "retry-after"):
        raw = headers.get(key)
        if raw is None:
            continue
        try:
            raw_value = str(raw).strip()
            if raw_value.isascii() and raw_value.isdecimal():
                seconds = int(raw_value)
            else:
                retry_at = parsedate_to_datetime(raw_value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = math.ceil(retry_at.timestamp() - now)
            if seconds > 0:
                _format_local_iso(now + seconds)
                return seconds
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    return None


def resolve_rate_limit_reset(
    error: BaseException | None = None,
    *,
    client: Any = None,
    account_key: str | None = None,
) -> dict[str, Any]:
    """Resolve the best available quota/cooldown reset timestamp and wait duration."""
    now = time.time()
    if error is not None:
        window = getattr(error, "quota_window", None)
        delay = getattr(error, "retry_after_seconds", None)
        timestamp = getattr(error, "reset_timestamp", None)
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            _, timestamp = _parse_iso_timestamp(getattr(error, "reset_at", None))
        header_delay = _retry_after_header_seconds(error, now)
        source = "browser_probe_cooldown" if window == "browser_probe_cooldown" else "upstream_reset"
        if header_delay is not None and (type(delay) is not int or header_delay >= delay):
            source, window, delay, timestamp = "http_retry_after", "http_retry_after", header_delay, None
        if timestamp is None and type(delay) is int and delay > 0:
            timestamp = now + delay
        if timestamp is not None and math.isfinite(timestamp) and timestamp > now:
            return {
                "retry_after_source": source,
                **({"quota_window": window} if window else {}),
                "reset_at": _format_local_iso(timestamp),
                "reset_timestamp": timestamp,
                "retry_after_seconds": max(1, math.ceil(timestamp - now)),
            }

    if client is None:
        try:
            from ..client_wrapper import get_gemini_client
            client = get_gemini_client()
        except Exception:
            pass
    quotas = getattr(client, "quotas", None)
    usage = quotas.get("usage_info") if isinstance(quotas, Mapping) else None
    snapshot = extract_quota_reset_from_quotas(quotas)
    source = "client_quota_snapshot"
    if snapshot is None:
        # A client observation for the same window supersedes a stored balance.
        try:
            from ..infrastructure.account_identity import current_account_key
            from ..infrastructure.model_policy_store import ModelPolicyStore

            resolved_key = account_key or current_account_key()
            cached = ModelPolicyStore().get_quota_snapshot(resolved_key) if resolved_key else None
            if cached:
                window = cached["quota_window"]
                current = usage.get(window) if isinstance(usage, Mapping) else None
                remaining = cached.get("remaining_credits")
                if (window in {"weekly", "current_5h"} and type(remaining) is int and remaining <= 0
                        and cached["reset_timestamp"] > now and not isinstance(current, Mapping)):
                    snapshot = cached
                    source = "stored_quota_snapshot"
        except Exception:
            pass
    if snapshot is None:
        return {}
    return {
        **snapshot,
        "retry_after_source": source,
        "retry_after_seconds": max(1, math.ceil(float(snapshot["reset_timestamp"]) - now)),
    }


def format_rate_limit_guidance(
    reset_info: Mapping[str, Any] | None,
    *,
    generation_limited: bool = False,
) -> tuple[str, str]:
    """Describe the observed limit and recommend a bounded follow-up."""
    info = reset_info or {}
    source = info.get("retry_after_source")
    reset_at = info.get("reset_at")
    retry_after = info.get("retry_after_seconds")
    message = (
        "Gemini Web limited this generation request."
        if generation_limited else "Gemini Web temporarily limited this request."
    )
    if source == "browser_probe_cooldown":
        message = "Gemini Web session initialization is in a short local cooldown."
    if isinstance(reset_at, str) and type(retry_after) is int and retry_after > 0:
        if source in {"client_quota_snapshot", "stored_quota_snapshot"}:
            window = "weekly" if info.get("quota_window") == "weekly" else "5-hour"
            message += f" A quota snapshot showed a depleted {window} balance and a scheduled reset at {reset_at}."
            next_step = f"Check the current balance with `check-quota` or wait until {reset_at}."
        else:
            message += f" Retry after {reset_at} (in about {retry_after}s)."
            next_step = f"Wait until {reset_at}."
    else:
        message += " The cause and retry time are unconfirmed."
        minutes = _RATE_LIMIT_DEFAULT_CAPACITY_WAIT_SECONDS // 60
        next_step = (
            f"Wait about {minutes} minutes or try again during off-peak hours. "
            "Use `check-quota` (CLI: `gemini-web-cli quotas`) to verify the account balance separately."
        )
    suggested_action = (
        f"{next_step} Avoid tight-loop retries. For a pending or unverified operation, "
        "resume the original handle with `get-operation`. Otherwise, retry a confirmed failed request only once "
        "after waiting, using a host environment timer if available."
    )
    return message, suggested_action
