"""Use verified Web cleanup evidence to stop recovery of deleted media chats."""

import sqlite3

from ..infrastructure.account_identity import account_matches, operation_account_matches, uses_omni_video_api
from ..infrastructure.cleanup_store import CleanupStore
from ..infrastructure.operation_store import OperationRecord, OperationStore


def has_verified_owned_cleanup(record: OperationRecord) -> bool:
    """Read exact cleanup evidence for the currently bound, unprotected Web chat."""
    account, cid = record.account_key, record.upstream_chat_id
    if (not account or not cid or record.retain_chat or record.cleanup_protected
            or uses_omni_video_api(record) or not operation_account_matches(record)):
        return False
    try:
        entry = CleanupStore().get(account, cid)
    except (OSError, sqlite3.Error):
        return False
    return bool(entry and entry.get("chat_id") == cid and entry.get("state") == "completed"
                and operation_account_matches(record) and account_matches(entry.get("account_key"), account))


def reconcile_owned_media_cleanup(store: OperationStore, record: OperationRecord) -> bool:
    """Stop this recovery attempt if its original chat has verified cleanup evidence."""
    if not isinstance(record, OperationRecord) or not record.media_type or not has_verified_owned_cleanup(record):
        return False
    try:
        store.reconcile_media_cleanup(record)
    except (OSError, sqlite3.Error):
        # A local write failure cannot make the already verified remote chat
        # available again. Leave metadata unchanged and stop this read attempt.
        return operation_account_matches(record)
    # A stale snapshot cannot update a new lease. The next attempt can read the
    # fresh snapshot; this attempt still has no reason to read the deleted chat.
    return operation_account_matches(record)
