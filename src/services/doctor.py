"""Safe local preflight diagnostics independent of management tools."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any, Callable, Literal

from ..client_wrapper import get_cookie_status, list_browser_cookie_profiles
from .manifest import _current_enabled_manifest_groups, tool_manifest_payload


DoctorStatus = Literal["ok", "warn", "error", "skip"]


def doctor_check(name: str, status: DoctorStatus, message: str, **details: Any) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": {key: value for key, value in details.items() if value is not None},
    }


def doctor_overall_status(checks: list[dict[str, Any]]) -> DoctorStatus:
    statuses = {check["status"] for check in checks}
    if "error" in statuses:
        return "error"
    if "warn" in statuses:
        return "warn"
    if "skip" in statuses and statuses == {"skip"}:
        return "skip"
    return "ok"


CookieStatusProvider = Callable[[], dict[str, Any]]
ProfileProvider = Callable[..., list[dict[str, Any]]]


def _static_checks() -> list[dict[str, Any]]:
    current_tool_groups, enabled_groups = _current_enabled_manifest_groups()
    manifest = tool_manifest_payload("all")
    python_check = doctor_check(
        "python_runtime",
        "ok",
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        executable=sys.executable,
    )
    surface_check = doctor_check(
        "tool_surface",
        "ok",
        f"{manifest['current_enabled_count']} of {manifest['total_count']} manifest tools are enabled",
        current_tool_groups=current_tool_groups,
        enabled_groups=sorted(enabled_groups),
        total_count=manifest["total_count"],
        current_enabled_count=manifest["current_enabled_count"],
    )
    return [python_check, surface_check]


def _cookie_check(cookie_status: dict[str, Any]) -> dict[str, Any]:
    has_cookie = bool(cookie_status.get("has_cookie"))
    needs_refresh = bool(cookie_status.get("needs_refresh", False))
    if not cookie_status.get("available", False):
        return doctor_check("cookie_status", "warn", "Cookie manager is unavailable")
    if not has_cookie:
        return doctor_check("cookie_status", "warn", "No runtime Gemini cookie is configured")
    if needs_refresh:
        return doctor_check(
            "cookie_status",
            "warn",
            "Runtime Gemini cookie exists but should be refreshed",
            source=cookie_status.get("source"),
            cookie_status=cookie_status.get("status"),
        )
    return doctor_check(
        "cookie_status",
        "ok",
        "Runtime Gemini cookie is configured",
        source=cookie_status.get("source"),
        cookie_status=cookie_status.get("status"),
    )


def _sanitize_profiles(browser: str, raw_profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "browser": item.get("browser", browser),
            "profile": item.get("profile"),
            "has_psid": item.get("has_psid"),
            "has_psidts": item.get("has_psidts"),
            "cookie_count": item.get("cookie_count"),
            "chrome_selected_profile": item.get("chrome_selected_profile"),
            "chrome_selected_profile_directory": item.get("chrome_selected_profile_directory"),
            "account_available": item.get("account_available"),
            "scheduled_registry_count": item.get("scheduled_registry_count"),
            "error": item.get("error"),
            "error_code": item.get("error_code"),
        }
        for item in raw_profiles
    ]


def _collect_profiles(
    browser: str,
    validate_browser: bool,
    profile_provider: ProfileProvider,
) -> list[dict[str, Any]]:
    if not browser:
        return []
    try:
        raw_profiles = profile_provider(browser, validate=validate_browser)
    except Exception as exc:
        return [{"browser": browser, "error": f"{type(exc).__name__}: {exc}"}]
    return _sanitize_profiles(browser, raw_profiles)


def select_recommended_profile(browser_profiles: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    profiles_with_psid = [item for item in browser_profiles if item.get("has_psid")]
    selected_profile = next((item for item in browser_profiles if item.get("chrome_selected_profile")), None)
    recommended_profile = next(
        (item for item in profiles_with_psid if item.get("account_available") is True),
        profiles_with_psid[0] if profiles_with_psid else None,
    )
    return selected_profile, recommended_profile


def _browser_profile_check(browser: str, validate_browser: bool, browser_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    profile_errors = [item for item in browser_profiles if item.get("error")]
    profiles_with_psid = [item for item in browser_profiles if item.get("has_psid")]
    selected_profile, recommended_profile = select_recommended_profile(browser_profiles)

    if not browser:
        return doctor_check("browser_profiles", "skip", "Browser profile diagnostics were disabled")
    if profile_errors and not profiles_with_psid:
        return doctor_check(
            "browser_profiles",
            "warn",
            f"Could not read usable {browser} Gemini cookies",
            errors=profile_errors,
        )
    if not profiles_with_psid:
        return doctor_check(
            "browser_profiles",
            "warn",
            f"No {browser} profile has a Gemini PSID",
            profiles=browser_profiles,
        )
    if selected_profile and not selected_profile.get("has_psid"):
        return doctor_check(
            "browser_profile_alignment",
            "warn",
            "Chrome selected profile has no Gemini PSID, but another profile does",
            selected_profile=selected_profile.get("profile"),
            selected_profile_directory=selected_profile.get("chrome_selected_profile_directory"),
            recommended_profile=recommended_profile.get("profile") if recommended_profile else None,
            validate_browser=validate_browser,
        )
    return doctor_check(
        "browser_profile_alignment",
        "ok",
        f"{browser} has a usable Gemini cookie profile",
        selected_profile=selected_profile.get("profile") if selected_profile else None,
        recommended_profile=recommended_profile.get("profile") if recommended_profile else None,
        validate_browser=validate_browser,
    )


def _environment_checks(ffprobe_path: str | None) -> list[dict[str, Any]]:
    generated_media_dir = os.path.abspath("generated_media")
    ffprobe_check = doctor_check(
        "ffprobe",
        "ok" if ffprobe_path else "warn",
        "ffprobe is available for media duration verification" if ffprobe_path else "ffprobe was not found in PATH",
        path=ffprobe_path,
    )
    media_dir_check = doctor_check(
        "generated_media_dir",
        "ok" if os.path.isdir(generated_media_dir) else "warn",
        "generated_media directory exists"
        if os.path.isdir(generated_media_dir)
        else "generated_media directory does not exist yet",
        path=generated_media_dir,
    )
    return [ffprobe_check, media_dir_check]


def _recommendations(browser: str, validate_browser: bool, profile_state: dict[str, Any]) -> list[str]:
    has_cookie = profile_state["has_cookie"]
    selected_profile = profile_state["selected_profile"]
    recommended_profile = profile_state["recommended_profile"]
    ffprobe_path = profile_state["ffprobe_path"]

    recommendations: list[str] = []
    if recommended_profile and selected_profile and not selected_profile.get("has_psid"):
        recommendations.append(
            f'Use gemini_get_cookie_from_browser(browser="{browser}", profile="{recommended_profile.get("profile")}") before live account checks.'
        )
    elif not has_cookie and recommended_profile:
        recommendations.append(
            f'Load cookies with gemini_get_cookie_from_browser(browser="{browser}", profile="{recommended_profile.get("profile")}").'
        )
    if validate_browser is False:
        recommendations.append(
            "Run gemini_doctor(validate_browser=true) when you need live account/profile validation."
        )
    if not ffprobe_path:
        recommendations.append("Install ffmpeg/ffprobe before relying on music/video duration checks.")
    return recommendations


def doctor_payload(
    browser: str = "chrome",
    validate_browser: bool = False,
    *,
    cookie_status_provider: CookieStatusProvider = get_cookie_status,
    profile_provider: ProfileProvider = list_browser_cookie_profiles,
) -> dict[str, Any]:
    """Build a safe preflight report without exposing cookie values."""
    checks = _static_checks()

    cookie_status = cookie_status_provider()
    has_cookie = bool(cookie_status.get("has_cookie"))
    checks.append(_cookie_check(cookie_status))

    browser_profiles = _collect_profiles(browser, validate_browser, profile_provider)
    checks.append(_browser_profile_check(browser, validate_browser, browser_profiles))

    ffprobe_path = shutil.which("ffprobe")
    checks.extend(_environment_checks(ffprobe_path))

    selected_profile, recommended_profile = select_recommended_profile(browser_profiles)
    profile_state = {
        "has_cookie": has_cookie,
        "selected_profile": selected_profile,
        "recommended_profile": recommended_profile,
        "ffprobe_path": ffprobe_path,
    }

    return {
        "name": "gemini_doctor",
        "overall_status": doctor_overall_status(checks),
        "safe": True,
        "validate_browser": validate_browser,
        "browser": browser,
        "checks": checks,
        "browser_profiles": browser_profiles,
        "recommendations": _recommendations(browser, validate_browser, profile_state),
    }


def format_doctor_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "## Gemini Web MCP Doctor",
        f"Overall: {payload['overall_status']}",
        f"Browser: {payload['browser'] or 'disabled'} · validate_browser={payload['validate_browser']}",
        "",
        "### Checks",
    ]
    for check in payload["checks"]:
        lines.append(f"- {check['name']}: {check['status']} - {check['message']}")
        details = check.get("details") if isinstance(check.get("details"), dict) else {}
        for key in ("source", "selected_profile", "recommended_profile", "path"):
            if details.get(key):
                lines.append(f"  {key}: {details[key]}")
    if payload.get("browser_profiles"):
        lines.extend(["", "### Browser Profiles"])
        for item in payload["browser_profiles"]:
            if item.get("error"):
                error_code = f" [{item['error_code']}]" if item.get("error_code") else ""
                lines.append(f"- {item.get('profile') or item.get('browser')}: error={item['error']}{error_code}")
                continue
            selected = "yes" if item.get("chrome_selected_profile") else "no"
            account = item.get("account_available")
            account_text = "yes" if account is True else "no" if account is False else "unvalidated"
            lines.append(
                f"- {item.get('profile')}: psid={'yes' if item.get('has_psid') else 'no'}, "
                f"selected={selected}, account={account_text}, "
                f"scheduled_registry_count={item.get('scheduled_registry_count', 'unvalidated')}"
            )
    if payload.get("recommendations"):
        lines.extend(["", "### Recommendations"])
        lines.extend(f"- {item}" for item in payload["recommendations"])
    return "\n".join(lines)


_doctor_check = doctor_check
_doctor_overall_status = doctor_overall_status
_doctor_payload = doctor_payload
_format_doctor_markdown = format_doctor_markdown
