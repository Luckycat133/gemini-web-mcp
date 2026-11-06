"""Opaque account identity shared by the operation and cleanup ledgers."""

import hashlib
import os

from ..constants import OMNI_VIDEO_API_BACKEND


def current_account_key() -> str | None:
    psid = os.environ.get("GEMINI_PSID", "")
    return hashlib.sha256(psid.encode()).hexdigest() if psid else None


def account_matches(bound: str | None, current: str | None) -> bool:
    """Never use another account or silently assign a legacy unbound record."""
    return bool(bound and current and bound == current)


def omni_video_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return key if all(33 <= ord(character) < 127 for character in key) else ""


def omni_video_owner(api_key: str) -> str | None:
    return hashlib.sha256(b"gemini-video-api\0" + api_key.encode()).hexdigest() if api_key else None


def uses_omni_video_api(record: object) -> bool:
    return getattr(record, "effective_backend", None) == OMNI_VIDEO_API_BACKEND


def operation_account_matches(record: object) -> bool:
    owner = omni_video_owner(omni_video_api_key()) if uses_omni_video_api(record) else current_account_key()
    return account_matches(getattr(record, "account_key", None), owner)
