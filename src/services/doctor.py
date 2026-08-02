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


def doctor_payload(
    browser: str = "chrome",
    validate_browser: bool = False,
    *,
    cookie_status_provider: CookieStatusProvider = get_cookie_status,
    profile_provider: ProfileProvider = list_browser_cookie_profiles,
) -> dict[str, Any]:
    """Build a safe preflight report without exposing cookie values."""

    checks: list[dict[str, Any]] = []
    current_tool_groups, enabled_groups = _current_enabled_manifest_groups()
    manifest = tool_manifest_payload("all")
    checks.append(
        doctor_check(
            "python_runtime",
            "ok",
            f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            executable=sys.executable,
        )
    )
    checks.append(
        doctor_check(
            "tool_surface",
            "ok",
            f"{manifest['current_enabled_count']} of {manifest['total_count']} manifest tools are enabled",
            current_tool_groups=current_tool_groups,
            enabled_groups=sorted(enabled_groups),
            total_count=manifest["total_count"],
            current_enabled_count=manifest["current_enabled_count"],
        )
    )

    cookie_status = cookie_status_provider()
    has_cookie = bool(cookie_status.get("has_cookie"))
    needs_refresh = bool(cookie_status.get("needs_refresh", False))
    if not cookie_status.get("available", False):
        cookie_check = doctor_check("cookie_status", "warn", "Cookie manager is unavailable")
    elif not has_cookie:
        cookie_check = doctor_check("cookie_status", "warn", "No runtime Gemini cookie is configured")
    elif needs_refresh:
        cookie_check = doctor_check(
            "cookie_status",
            "warn",
            "Runtime Gemini cookie exists but should be refreshed",
            source=cookie_status.get("source"),
            cookie_status=cookie_status.get("status"),
        )
    else:
        cookie_check = doctor_check(
            "cookie_status",
            "ok",
            "Runtime Gemini cookie is configured",
            source=cookie_status.get("source"),
            cookie_status=cookie_status.get("status"),
        )
    checks.append(cookie_check)

    browser_profiles: list[dict[str, Any]] = []
    if browser:
        try:
            raw_profiles = profile_provider(browser, validate=validate_browser)
            for item in raw_profiles:
                browser_profiles.append(
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
                    }
                )
        except Exception as exc:
            browser_profiles = [{"browser": browser, "error": f"{type(exc).__name__}: {exc}"}]

    profile_errors = [item for item in browser_profiles if item.get("error")]
    profiles_with_psid = [item for item in browser_profiles if item.get("has_psid")]
    selected_profile = next((item for item in browser_profiles if item.get("chrome_selected_profile")), None)
    recommended_profile = next(
        (item for item in profiles_with_psid if item.get("account_available") is True),
        profiles_with_psid[0] if profiles_with_psid else None,
    )
    if not browser:
        checks.append(doctor_check("browser_profiles", "skip", "Browser profile diagnostics were disabled"))
    elif profile_errors and not profiles_with_psid:
        checks.append(
            doctor_check(
                "browser_profiles",
                "warn",
                f"Could not read usable {browser} Gemini cookies",
                errors=profile_errors,
            )
        )
    elif not profiles_with_psid:
        checks.append(
            doctor_check(
                "browser_profiles",
                "warn",
                f"No {browser} profile has a Gemini PSID",
                profiles=browser_profiles,
            )
        )
    elif selected_profile and not selected_profile.get("has_psid"):
        checks.append(
            doctor_check(
                "browser_profile_alignment",
                "warn",
                "Chrome selected profile has no Gemini PSID, but another profile does",
                selected_profile=selected_profile.get("profile"),
                selected_profile_directory=selected_profile.get("chrome_selected_profile_directory"),
                recommended_profile=recommended_profile.get("profile") if recommended_profile else None,
                validate_browser=validate_browser,
            )
        )
    else:
        checks.append(
            doctor_check(
                "browser_profile_alignment",
                "ok",
                f"{browser} has a usable Gemini cookie profile",
                selected_profile=selected_profile.get("profile") if selected_profile else None,
                recommended_profile=recommended_profile.get("profile") if recommended_profile else None,
                validate_browser=validate_browser,
            )
        )

    ffprobe_path = shutil.which("ffprobe")
    checks.append(
        doctor_check(
            "ffprobe",
            "ok" if ffprobe_path else "warn",
            "ffprobe is available for media duration verification" if ffprobe_path else "ffprobe was not found in PATH",
            path=ffprobe_path,
        )
    )
    generated_media_dir = os.path.abspath("generated_media")
    checks.append(
        doctor_check(
            "generated_media_dir",
            "ok" if os.path.isdir(generated_media_dir) else "warn",
            "generated_media directory exists"
            if os.path.isdir(generated_media_dir)
            else "generated_media directory does not exist yet",
            path=generated_media_dir,
        )
    )

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
    return {
        "name": "gemini_doctor",
        "overall_status": doctor_overall_status(checks),
        "safe": True,
        "validate_browser": validate_browser,
        "browser": browser,
        "checks": checks,
        "browser_profiles": browser_profiles,
        "recommendations": recommendations,
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
                lines.append(f"- {item.get('profile') or item.get('browser')}: error={item['error']}")
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
