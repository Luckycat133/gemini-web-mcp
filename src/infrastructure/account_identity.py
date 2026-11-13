"""Opaque account identity shared by the operation and cleanup ledgers."""

import functools
import hashlib
import os

from ..constants import OMNI_VIDEO_API_BACKEND


@functools.lru_cache(maxsize=4)
def _opaque_account_id(label: str, secret: str) -> str:
    """Domain-separated, non-reversible local identifier for one credential.

    The credential itself is never stored: only this derived identifier is
    written to the local ledgers. PBKDF2 with the label as salt keeps the
    derivation one-way and expensive to brute-force; the result is memoised
    because the credential does not change inside a process.
    """
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), label.encode(), 10_000).hex()


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
