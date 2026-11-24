"""Opaque account identity shared by the operation and cleanup ledgers."""

import hashlib
import os
from functools import lru_cache

from ..constants import OMNI_VIDEO_API_BACKEND


def current_account_key() -> str | None:
    psid = os.environ.get("GEMINI_PSID", "")
    return hashlib.sha256(psid.encode()).hexdigest() if psid else None


@lru_cache(maxsize=4)
def _legacy_account_key(psid: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", psid.encode(), b"gemini-psid", 10_000).hex()


def current_account_keys() -> tuple[str, ...]:
    """Derive both persisted formats only from the same currently held PSID."""
    psid = os.environ.get("GEMINI_PSID", "")
    if not psid:
        return ()
    return hashlib.sha256(psid.encode()).hexdigest(), _legacy_account_key(psid)


def account_key_scope(account: str | None) -> tuple[str, ...]:
    """Expand a proven current credential scope; preserve other exact scopes."""
    if not account:
        return ()
    keys = current_account_keys()
    return keys if account in keys else (account,)


def canonical_account_key(bound: str | None) -> str | None:
    keys = current_account_keys()
    return keys[0] if keys and bound in keys else None


def account_matches(bound: str | None, current: str | None) -> bool:
    """Never use another account or silently assign a legacy unbound record."""
    if not bound or not current:
        return False
    if bound == current:
        return True
    keys = current_account_keys()
    return bound in keys and current in keys


def omni_video_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return key if all(33 <= ord(character) < 127 for character in key) else ""


def omni_video_owner(api_key: str) -> str | None:
    return hashlib.sha256(b"gemini-video-api\0" + api_key.encode()).hexdigest() if api_key else None


def uses_omni_video_api(record: object) -> bool:
    return getattr(record, "effective_backend", None) == OMNI_VIDEO_API_BACKEND


def operation_account_matches(record: object) -> bool:
    bound = getattr(record, "account_key", None)
    if uses_omni_video_api(record):
        owner = omni_video_owner(omni_video_api_key())
        return bool(bound and owner and bound == owner)
    return account_matches(bound, current_account_key())
