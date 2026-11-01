"""Opaque account identity shared by the operation and cleanup ledgers."""

import hashlib
import os


def current_account_key() -> str | None:
    psid = os.environ.get("GEMINI_PSID", "")
    return hashlib.sha256(psid.encode()).hexdigest() if psid else None


def account_matches(bound: str | None, current: str | None) -> bool:
    """Never use another account or silently assign a legacy unbound record."""
    return bool(bound and current and bound == current)


def operation_account_matches(record: object) -> bool:
    return account_matches(getattr(record, "account_key", None), current_account_key())
