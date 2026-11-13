"""Opaque account identity shared by the operation and cleanup ledgers."""

import hashlib
import os

from ..constants import OMNI_VIDEO_API_BACKEND


def _opaque_account_id(label: str, secret: str) -> str:
    """Domain-separated, non-reversible local identifier for one credential.

    Keyed BLAKE2b is a PRF for keyed derivation, not a password hash: the input
    is already a high-entropy credential, and the result never leaves the
    machine.
    """
    return hashlib.blake2b(secret.encode(), key=label.encode(), digest_size=32).hexdigest()


def current_account_key() -> str | None:
    psid = os.environ.get("GEMINI_PSID", "")
    return _opaque_account_id("gemini-psid", psid) if psid else None


def account_matches(bound: str | None, current: str | None) -> bool:
    """Never use another account or silently assign a legacy unbound record."""
    return bool(bound and current and bound == current)


def omni_video_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return key if all(33 <= ord(character) < 127 for character in key) else ""


def omni_video_owner(api_key: str) -> str | None:
    return _opaque_account_id("gemini-video-api", api_key) if api_key else None


def uses_omni_video_api(record: object) -> bool:
    return getattr(record, "effective_backend", None) == OMNI_VIDEO_API_BACKEND


def operation_account_matches(record: object) -> bool:
    owner = omni_video_owner(omni_video_api_key()) if uses_omni_video_api(record) else current_account_key()
    return account_matches(getattr(record, "account_key", None), owner)
