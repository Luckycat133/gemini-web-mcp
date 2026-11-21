"""Typed contracts for explicit Gemini chat metadata mutations: rename, pin, unpin.

Gemini Apps help confirms rename, pin, and unpin as user chat operations, but
publishes no public Cookie RPC API for chat metadata. This module declares only
the domain boundary: requests, observed metadata, mutation outcomes, and the
injectable transport and account callbacks that Root connects to observed
contracts. It invents no RPC ids, payload indices, endpoint URLs, browser
selectors, or SDK methods.

``MAX_CHAT_TITLE_LENGTH`` is a conservative service-side sanity limit for chat
titles, not an observed upstream constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ChatAction = Literal["rename", "pin", "unpin"]

# Conservative service-side sanity limit for a chat title. Gemini Web publishes
# no public cookie RPC contract for chat metadata, so this bound is a local
# guard against obviously malformed input, not an observed upstream constant.
MAX_CHAT_TITLE_LENGTH = 200

STATUS_VERIFIED = "verified"
STATUS_VERIFIED_AFTER_MUTATION_EXCEPTION = "verified_after_mutation_exception"
STATUS_NO_OP = "no_op"
STATUS_ACCOUNT_REQUIRED = "account_required"
STATUS_ACCOUNT_MISMATCH = "account_mismatch"
STATUS_INVALID_REQUEST = "invalid_request"
STATUS_PRE_READ_ERROR = "pre_read_error"
STATUS_PRE_READ_CHAT_MISSING = "pre_read_chat_missing"
STATUS_PRE_READ_WRONG_CHAT = "pre_read_wrong_chat"
STATUS_EXPECTED_TITLE_CONFLICT = "expected_title_conflict"
STATUS_READ_BACK_ERROR = "read_back_error"
STATUS_READ_BACK_CHAT_MISSING = "read_back_chat_missing"
STATUS_READ_BACK_WRONG_CHAT = "read_back_wrong_chat"
STATUS_READ_BACK_FIELD_MISSING = "read_back_field_missing"
STATUS_READ_BACK_MISMATCH = "read_back_mismatch"


@dataclass(frozen=True)
class ChatMetadata:
    """Freshly observed chat metadata; conversation text is never included."""

    chat_id: str
    title: str | None
    is_pinned: bool | None


@dataclass(frozen=True)
class ChatMutationRequest:
    """One explicit chat metadata mutation request."""

    action: ChatAction
    chat_id: str
    title: str | None = None
    expected_title: str | None = None


@dataclass(frozen=True)
class ChatMutationData:
    """Outcome of one metadata mutation request with read-back evidence.

    ``changed`` is evidence-based: it is False before a mutation is sent, None
    once a mutation was sent but its effect is not yet independently proven,
    and True only when a fresh read-back proves the requested change.
    ``mutation_sent`` records whether the mutation call was dispatched
    regardless of the verification outcome.
    """

    action: ChatAction
    chat_id: str
    changed: bool | None = False
    mutation_sent: bool = False
    requested_title: str | None = None
    requested_pinned: bool | None = None
    observed_title: str | None = None
    observed_pinned: bool | None = None
    verified: bool = False
