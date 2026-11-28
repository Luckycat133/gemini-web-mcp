"""Account quota parsing and formatting using Gemini Web's compute usage RPC."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import logging
from typing import Any

from gemini_webapi.constants import GRPC
from gemini_webapi.types import RPCData

from ..client_manager import ClientRateLimited
from ..domain import DomainErrorCode, DomainResult, result_from_exception

logger = logging.getLogger(__name__)

_TIER_LABELS = {
    1: "FREE",
    2: "PRO",
    3: "ULTRA",
    4: "PLUS",
    6: "ULTRA",
}
_WINDOW_KEYS = {
    1: "current_5h",
    2: "weekly",
}


def _nested(value: Any, path: tuple[int, ...]) -> Any:
    current = value
    for index in path:
        if not isinstance(current, (list, tuple)) or index >= len(current):
            return None
        current = current[index]
    return current


def _format_reset_times(reset_ts: Any) -> tuple[float | None, str | None, str | None]:
    if not isinstance(reset_ts, (int, float)) or isinstance(reset_ts, bool) or reset_ts <= 0:
        return None, None, None
    ts = float(reset_ts)
    try:
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        local_iso = dt_utc.astimezone().isoformat(timespec="seconds")
        utc_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        return ts, local_iso, utc_str
    except (OSError, OverflowError, ValueError):
        return ts, None, None


def _build_window_payload(
    *,
    remaining: Any,
    total: Any,
    usage_level: Any,
    reset_ts: Any,
    reset_at: Any = None,
) -> dict[str, Any]:
    rem = int(remaining) if isinstance(remaining, int) and not isinstance(remaining, bool) else None
    tot = int(total) if isinstance(total, int) and not isinstance(total, bool) and total > 0 else None
    used = (tot - rem) if (rem is not None and tot is not None) else None
    pct: float | None = None
    if isinstance(usage_level, (int, float)) and not isinstance(usage_level, bool):
        pct = round(float(usage_level) * 100, 1)
    elif rem is not None and tot is not None:
        pct = round(((tot - rem) / tot) * 100, 1)
    ts, local_iso, utc_str = _format_reset_times(reset_ts)
    if local_iso is None and isinstance(reset_at, str) and reset_at.strip():
        local_iso = reset_at.strip()
    return {
        "remaining_credits": rem,
        "total_credits": tot,
        "used_credits": used,
        "usage_level": float(usage_level) if isinstance(usage_level, (int, float)) else None,
        "usage_percentage": pct,
        "reset_timestamp": ts,
        "reset_at": local_iso,
        "reset_time_utc": utc_str,
    }


def parse_usage_info_bodies(bodies: list[Any]) -> dict[str, Any] | None:
    """Parse raw GRPC.GET_USAGE_INFO response parts into 5h and weekly quota windows."""
    payload: dict[str, Any] | None = None
    for part_body in bodies:
        if not isinstance(part_body, list):
            continue
        tier_id = _nested(part_body, (0,))
        usage_items = _nested(part_body, (1,))
        use_overage = _nested(part_body, (2,))
        payload = {
            "tier": {
                "id": tier_id if isinstance(tier_id, int) else None,
                "label": _TIER_LABELS.get(tier_id) if isinstance(tier_id, int) else None,
            },
            "use_overage_ai_credits": bool(use_overage) if isinstance(use_overage, bool) else use_overage,
            "ai_credits_remaining": None,
            "current_5h": None,
            "weekly": None,
        }
        if not isinstance(usage_items, list):
            continue
        for item in usage_items:
            if not isinstance(item, list):
                continue
            remaining = _nested(item, (0,))
            usage_level = _nested(item, (1,))
            metric_type = _nested(item, (2,))
            reset_ts = _nested(item, (3, 0, 0))
            total = _nested(item, (6,))
            if metric_type == 3:
                payload["ai_credits_remaining"] = remaining
                continue
            window_key = _WINDOW_KEYS.get(metric_type) if isinstance(metric_type, int) else None
            if window_key is None:
                continue
            payload[window_key] = _build_window_payload(
                remaining=remaining,
                total=total,
                usage_level=usage_level,
                reset_ts=reset_ts,
            )
    if payload is not None:
        w5 = payload.get("current_5h") or {}
        ww = payload.get("weekly") or {}
        payload["has_exact_credits"] = any(
            isinstance(item, dict) and item.get("remaining_credits") is not None
            for item in (w5, ww)
        )
    return payload


def _usage_info_from_cached(client: Any) -> dict[str, Any] | None:
    raw = getattr(client, "usage_info", None) or getattr(client, "_usage_info", None)
    if not isinstance(raw, Mapping) or not raw:
        return None
    tier_raw = raw.get("tier")
    tier: Mapping[str, Any] = tier_raw if isinstance(tier_raw, Mapping) else {}
    windows: dict[str, Any] = {}
    for key in ("current_5h", "weekly"):
        entry = raw.get(key)
        if isinstance(entry, Mapping):
            windows[key] = _build_window_payload(
                remaining=entry.get("remaining_credits"),
                total=entry.get("total_credits"),
                usage_level=entry.get("usage_level"),
                reset_ts=None,
                reset_at=entry.get("reset_at"),
            )
            if windows[key]["usage_percentage"] is None and entry.get("usage_percentage") is not None:
                windows[key]["usage_percentage"] = float(entry["usage_percentage"])
        else:
            windows[key] = None
    return {
        "tier": {"id": tier.get("id"), "label": tier.get("label")},
        "use_overage_ai_credits": raw.get("use_overage_ai_credits"),
        "ai_credits_remaining": raw.get("ai_credits_remaining"),
        "current_5h": windows["current_5h"],
        "weekly": windows["weekly"],
        "has_exact_credits": any(
            isinstance(item, dict) and item.get("remaining_credits") is not None
            for item in (windows["current_5h"], windows["weekly"])
        ),
    }


async def fetch_quota_summary(client: Any) -> DomainResult[dict[str, Any]]:
    """Fetch 5-hour and weekly Gemini Web compute quotas directly from GET_USAGE_INFO."""
    try:
        batch_execute = getattr(client, "_batch_execute", None)
        parse_results = getattr(client, "_parse_rpc_results", None)
        parsed: dict[str, Any] | None = None
        if callable(batch_execute) and callable(parse_results):
            response = await batch_execute(
                [RPCData(rpcid=GRPC.GET_USAGE_INFO, payload="[]")],
                source_path="/usage",
            )
            bodies = list(parse_results(response.text, GRPC.GET_USAGE_INFO))
            parsed = parse_usage_info_bodies(bodies)
        else:
            refresh = getattr(client, "_fetch_usage_info", None)
            if callable(refresh):
                await refresh()
        verification = "quota_observed" if parsed is not None else "quota_cached"
        if parsed is None:
            parsed = _usage_info_from_cached(client)
        if parsed is None:
            return DomainResult.failure(
                DomainErrorCode.CAPABILITY_UNAVAILABLE,
                "Gemini Web did not return usage quota information for this session.",
            )
        return DomainResult.success(parsed, verification_status=verification)
    except Exception as error:
        return result_from_exception(error, logger=logger, operation="check_quota")


def _format_window_line(label: str, window: Mapping[str, Any] | None) -> str:
    if not isinstance(window, Mapping):
        return f"- {label}: unavailable"
    rem = window.get("remaining_credits")
    tot = window.get("total_credits")
    pct = window.get("usage_percentage")
    reset_at = window.get("reset_at") or window.get("reset_time_utc") or "unknown"
    parts: list[str] = []
    if rem is not None and tot is not None:
        parts.append(f"{rem:,} / {tot:,} credits remaining")
    elif rem is not None:
        parts.append(f"{rem:,} credits remaining")
    if pct is not None:
        parts.append(f"{pct:g}% used")
    summary = ", ".join(parts) if parts else "reported without credit breakdown"
    return f"- {label}: {summary} (resets: {reset_at})"


def format_quota_markdown(payload: Mapping[str, Any]) -> str:
    tier_raw = payload.get("tier")
    tier: Mapping[str, Any] = tier_raw if isinstance(tier_raw, Mapping) else {}
    tier_label = tier.get("label") or tier.get("id") or "unknown"
    overage = payload.get("use_overage_ai_credits")
    lines = [
        f"Gemini Web Quota (tier: {tier_label}, overage_ai_credits: {overage})",
        _format_window_line("5h window", payload.get("current_5h")),
        _format_window_line("Weekly window", payload.get("weekly")),
    ]
    if payload.get("ai_credits_remaining") is not None:
        lines.append(f"- AI credits remaining: {payload['ai_credits_remaining']}")
    return "\n".join(lines)


async def fetch_initialized_quota_summary(
    client_provider: Callable[[], Any],
    client_initializer: Callable[[], Any],
) -> DomainResult[dict[str, Any]]:
    try:
        try:
            await client_initializer()
        except ClientRateLimited as error:
            delay = error.retry_after_seconds
            if error.quota_window != "browser_probe_cooldown" or type(delay) is not int or not 1 <= delay <= 30:
                raise
            await asyncio.sleep(delay)
            await client_initializer()
        return await fetch_quota_summary(client_provider())
    except Exception as error:
        return result_from_exception(error, logger=logger, operation="check_quota")
