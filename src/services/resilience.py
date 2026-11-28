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
    """Extract the most relevant active quota window and reset timestamp from client.quotas."""
    if not isinstance(quotas, Mapping):
        return None
    usage = quotas.get("usage_info")
    if isinstance(usage, Mapping):
        weekly = usage.get("weekly")
        window_5h = usage.get("current_5h")
        chosen: tuple[str, Mapping[str, Any]] | None = None
        if isinstance(weekly, Mapping):
            rem = weekly.get("remaining_credits")
            if isinstance(rem, int) and rem <= 0 and weekly.get("reset_at"):
                chosen = ("weekly", weekly)
        if chosen is None:
            for window_name, payload in (("current_5h", window_5h), ("weekly", weekly)):
                if isinstance(payload, Mapping) and payload.get("reset_at"):
                    chosen = (window_name, payload)
                    break
        if chosen is not None:
            window_name, payload = chosen
            iso_text, ts = _parse_iso_timestamp(payload.get("reset_at"))
            if iso_text and ts is not None:
                rem = payload.get("remaining_credits")
                return {
                    "quota_window": window_name,
                    "reset_at": iso_text,
                    "reset_timestamp": ts,
                    "remaining_credits": rem if type(rem) is int else None,
                }
    # Fallback to model quota buckets ("None-11" / "None-4").
    for key, entry in quotas.items():
        if key == "usage_info" or not isinstance(entry, Mapping):
            continue
        reset_time = entry.get("reset_time")
        if isinstance(reset_time, (int, float)) and not isinstance(reset_time, bool) and reset_time > 0:
            ts = float(reset_time)
            rem = entry.get("remaining")
            return {
                "quota_window": str(key),
                "reset_at": _format_local_iso(ts),
                "reset_timestamp": ts,
                "remaining_credits": rem if type(rem) is int else None,
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
    info: dict[str, Any] = {}
    if error is not None:
        for attr in ("reset_at", "reset_timestamp", "retry_after_seconds", "quota_window"):
            val = getattr(error, attr, None)
            if val is not None:
                info[attr] = val
        if "retry_after_seconds" not in info and (header_seconds := _retry_after_header_seconds(error, now)):
            info["retry_after_seconds"] = header_seconds
            info.setdefault("reset_timestamp", now + header_seconds)
            info.setdefault("reset_at", _format_local_iso(info["reset_timestamp"]))
            info.setdefault("quota_window", "http_retry_after")
    if "reset_timestamp" not in info:
        target_client = client
        if target_client is None:
            try:
                from ..client_wrapper import get_gemini_client
                target_client = get_gemini_client()
            except Exception:
                target_client = None
        if target_client is not None:
            live = extract_quota_reset_from_quotas(getattr(target_client, "quotas", None))
            if live and float(live["reset_timestamp"]) > now:
                info.update(live)
    if "reset_timestamp" not in info:
        try:
            from ..infrastructure.account_identity import current_account_key
            from ..infrastructure.model_policy_store import ModelPolicyStore

            resolved_key = account_key or current_account_key()
            if resolved_key:
                cached = ModelPolicyStore().get_quota_snapshot(resolved_key)
                if cached and float(cached["reset_timestamp"]) > now:
                    info.update(cached)
        except Exception:
            pass
    ts = info.get("reset_timestamp")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > now:
        remaining = max(1, int(math.ceil(float(ts) - now)))
        info["retry_after_seconds"] = info.get("retry_after_seconds") or remaining
        info.setdefault("reset_at", _format_local_iso(float(ts)))
    elif isinstance(info.get("retry_after_seconds"), int) and info["retry_after_seconds"] > 0:
        ts = now + info["retry_after_seconds"]
        info["reset_timestamp"] = ts
        info.setdefault("reset_at", _format_local_iso(ts))
    return {k: v for k, v in info.items() if v is not None}


def format_rate_limit_guidance(
    reset_info: Mapping[str, Any] | None,
    *,
    capacity_only: bool = False,
) -> tuple[str, str]:
    """Return user- and agent-actionable (message, suggested_action) with timer guidance."""
    info = reset_info or {}
    reset_at = info.get("reset_at")
    retry_after = info.get("retry_after_seconds")
    window = info.get("quota_window")
    if isinstance(reset_at, str) and reset_at and isinstance(retry_after, int) and retry_after > 0:
        buffer_seconds = 10 if window == "browser_probe_cooldown" else 60
        delay = retry_after + buffer_seconds
        window_note = f" ({window})" if window else ""
        message = (
            f"The upstream service rate limit or quota{window_note} was reached. "
            f"Quota resets at {reset_at} (~{retry_after}s remaining)."
        )
        suggested_action = (
            f"Do not retry in a tight loop. Wait until after {reset_at} (~{delay}s including safety "
            "margin), or schedule a single deferred retry using your host environment's scheduling or "
            "timer capability if available."
        )
        return message, suggested_action
    if capacity_only:
        message = "Gemini Web reported that the generation quota or capacity limit was temporarily reached due to high traffic."
        suggested_action = (
            "Do not retry in a tight loop. Wait ~10 minutes before retrying, or schedule a single "
            "deferred retry using your host environment's scheduling or timer capability if available."
        )
        return message, suggested_action
    message = "The upstream service temporarily limited requests; exact reset time was not reported."
    suggested_action = (
        "Check `gemini-web-cli quotas` for account window status and wait (~10 minutes) or schedule "
        "a single deferred retry using your host environment's scheduling or timer capability instead "
        "of tight-loop retrying."
    )
    return message, suggested_action
