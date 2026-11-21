"""Shared service for explicit Gemini chat metadata mutations: rename, pin, unpin.

The service validates every request before touching transport or client state,
guards account identity across each await, fresh-reads chat metadata before
and after the mutation, sends the mutation at most once, and claims success
only when an independent fresh read-back proves the requested field on the
same chat. Transport and identity callbacks are injected; Root connects them
to observed contracts. No RPC ids, payload indices, endpoints, selectors, or
SDK methods are invented here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from ..domain import (
    DomainErrorCode,
    DomainResult,
    result_from_exception,
    is_valid_remote_chat_id,
)
from ..domain.chat_management import (
    MAX_CHAT_TITLE_LENGTH,
    ChatMetadata,
    ChatMutationData,
    ChatMutationRequest,
    STATUS_ACCOUNT_MISMATCH,
    STATUS_ACCOUNT_REQUIRED,
    STATUS_EXPECTED_TITLE_CONFLICT,
    STATUS_INVALID_REQUEST,
    STATUS_NO_OP,
    STATUS_PRE_READ_CHAT_MISSING,
    STATUS_PRE_READ_ERROR,
    STATUS_PRE_READ_WRONG_CHAT,
    STATUS_READ_BACK_CHAT_MISSING,
    STATUS_READ_BACK_ERROR,
    STATUS_READ_BACK_FIELD_MISSING,
    STATUS_READ_BACK_MISMATCH,
    STATUS_READ_BACK_WRONG_CHAT,
    STATUS_VERIFIED,
    STATUS_VERIFIED_AFTER_MUTATION_EXCEPTION,
)
from ..infrastructure.account_identity import current_account_key

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatManagementDependencies:
    """Injectable transport and account callbacks; Root connects observed contracts."""

    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    read_metadata: Callable[[Any, str], Awaitable[ChatMetadata | None]]
    mutate: Callable[[Any, str, str, dict[str, Any]], Awaitable[Any]]
    account_key_provider: Callable[[], str | None] = current_account_key


class ChatManagementService:
    """Read-guard, mutate-once, read-back verification for chat metadata mutations."""

    def __init__(self, dependencies: ChatManagementDependencies) -> None:
        self._dependencies = dependencies

    async def update(self, request: ChatMutationRequest) -> DomainResult[ChatMutationData]:
        rejection = self._reject_invalid_request(request)
        if rejection is not None:
            return rejection

        data = self._base_data(request)

        # Snapshot the optional existing account identity before initialization.
        # Automatic browser authentication may bind an identity during
        # initialization, so the pre-initialization identity is only a hint.
        original_owner = self._account_identity()

        client: Any = None
        try:
            # Automatic browser authentication may bind the account during
            # initialization. Existing project clients are synchronous objects,
            # so the provider is called synchronously and never awaited.
            await self._dependencies.client_initializer()
            client = self._dependencies.client_provider()
        except Exception as exc:
            return result_from_exception(
                exc, logger=_logger, operation="chat_management.initialize_client",
            )

        # Obtain the identity that initialization may have discovered.
        owner = self._account_identity()
        if owner is None:
            return self._failure(
                request,
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "Gemini account identity is required before chat metadata can be mutated.",
                STATUS_ACCOUNT_REQUIRED,
            )
        if original_owner is not None and original_owner != owner:
            return self._failure(
                request,
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while initializing the client; no metadata was mutated.",
                STATUS_ACCOUNT_MISMATCH,
            )

        try:
            metadata = await self._dependencies.read_metadata(client, request.chat_id)
        except Exception:
            return self._failure(
                request,
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                "The chat metadata could not be read before mutation.",
                STATUS_PRE_READ_ERROR,
            )
        if self._account_identity() != owner:
            return self._failure(
                request,
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed while reading chat metadata; no metadata was mutated.",
                STATUS_ACCOUNT_MISMATCH,
            )

        if metadata is None:
            return self._failure(
                request,
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                f"The chat {request.chat_id} is not visible to this account; no mutation was sent.",
                STATUS_PRE_READ_CHAT_MISSING,
            )
        if metadata.chat_id != request.chat_id:
            return self._failure(
                request,
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                "Pre-read metadata identifies a different chat; no mutation was sent.",
                STATUS_PRE_READ_WRONG_CHAT,
            )

        if request.expected_title is not None and metadata.title != request.expected_title:
            return self._failure(
                request,
                data,
                DomainErrorCode.VERIFICATION_FAILED,
                "The chat title changed since it was observed; aborting the mutation.",
                STATUS_EXPECTED_TITLE_CONFLICT,
            )

        if self._already_matches(request, metadata):
            return DomainResult.success(
                replace(
                    data,
                    verified=True,
                    observed_title=metadata.title,
                    observed_pinned=metadata.is_pinned,
                ),
                verification_status=STATUS_NO_OP,
                details={"service": "chat_management", "action": request.action, "mutation_sent": False},
            )

        if self._account_identity() != owner:
            return self._failure(
                request,
                data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed before the mutation; the mutation was not sent.",
                STATUS_ACCOUNT_MISMATCH,
            )

        mutation_raised = False
        try:
            await self._dependencies.mutate(
                client, request.action, request.chat_id, self._changes(request),
            )
        except Exception:
            mutation_raised = True

        # The mutation was sent but its outcome is not proven until read-back.
        changed_data = replace(data, changed=None, mutation_sent=True)

        if self._account_identity() != owner:
            return self._failure(
                request,
                changed_data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the mutation; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
            )

        try:
            observed = await self._dependencies.read_metadata(client, request.chat_id)
        except Exception:
            return self._failure(
                request,
                changed_data,
                DomainErrorCode.VERIFICATION_FAILED,
                "The post-mutation read-back failed; the change is unverified.",
                STATUS_READ_BACK_ERROR,
            )
        # Fence the account before interpreting the read-back: a switch during a
        # None-returning read is an account mismatch, not trusted missing-chat
        # evidence, and metadata from a mismatched account is never exposed.
        if self._account_identity() != owner:
            return self._failure(
                request,
                changed_data,
                DomainErrorCode.AUTH_REQUIRED,
                "The account changed after the mutation; read-back was not trusted.",
                STATUS_ACCOUNT_MISMATCH,
            )
        if observed is None:
            return self._failure(
                request,
                changed_data,
                DomainErrorCode.VERIFICATION_FAILED,
                f"The chat {request.chat_id} disappeared on read-back; the change is unverified.",
                STATUS_READ_BACK_CHAT_MISSING,
            )

        verified, status = self._verify(request, observed)
        if not verified:
            return self._failure(
                request,
                replace(
                    changed_data,
                    verified=False,
                    observed_title=observed.title,
                    observed_pinned=observed.is_pinned,
                ),
                DomainErrorCode.VERIFICATION_FAILED,
                "The post-mutation read-back did not prove the requested change.",
                status,
            )
        # Only a matching independent read-back proves the change.
        return DomainResult.success(
            replace(
                changed_data,
                changed=True,
                verified=True,
                observed_title=observed.title,
                observed_pinned=observed.is_pinned,
            ),
            verification_status=(
                STATUS_VERIFIED_AFTER_MUTATION_EXCEPTION if mutation_raised else STATUS_VERIFIED
            ),
            details={"service": "chat_management", "action": request.action, "mutation_sent": True},
        )

    def _account_identity(self) -> str | None:
        try:
            return self._dependencies.account_key_provider()
        except Exception:
            return None

    def _reject_invalid_request(self, request: ChatMutationRequest) -> DomainResult[ChatMutationData] | None:
        data = ChatMutationData(
            action=request.action,
            chat_id=request.chat_id,
            changed=False,
            verified=False,
        )
        if request.action not in {"rename", "pin", "unpin"}:
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                f"Unsupported chat metadata action: {request.action!r}",
                verification_status=STATUS_INVALID_REQUEST,
                data=data,
            )
        if not is_valid_remote_chat_id(request.chat_id):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "chat_id must be a Gemini Web chat identifier starting with c_.",
                verification_status=STATUS_INVALID_REQUEST,
                data=data,
            )
        if request.action == "rename":
            title = request.title.strip() if isinstance(request.title, str) else None
            if not title:
                return DomainResult.failure(
                    DomainErrorCode.INVALID_ARGUMENT,
                    "rename requires a nonblank title.",
                    verification_status=STATUS_INVALID_REQUEST,
                    data=data,
                )
            if len(title) > MAX_CHAT_TITLE_LENGTH:
                return DomainResult.failure(
                    DomainErrorCode.INVALID_ARGUMENT,
                    f"rename title must not exceed {MAX_CHAT_TITLE_LENGTH} characters.",
                    verification_status=STATUS_INVALID_REQUEST,
                    data=replace(data, requested_title=title),
                )
            data = replace(data, requested_title=title)
        elif request.title not in (None, ""):
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "title is not applicable to pin or unpin.",
                verification_status=STATUS_INVALID_REQUEST,
                data=data,
            )
        else:
            data = replace(data, requested_pinned=(request.action == "pin"))

        if request.expected_title is not None:
            expected = request.expected_title.strip() if isinstance(request.expected_title, str) else None
            if not expected:
                return DomainResult.failure(
                    DomainErrorCode.INVALID_ARGUMENT,
                    "expected_title must be nonblank when provided.",
                    verification_status=STATUS_INVALID_REQUEST,
                    data=data,
                )
        return None

    def _base_data(self, request: ChatMutationRequest) -> ChatMutationData:
        if request.action == "rename":
            title = request.title.strip() if isinstance(request.title, str) else None
            return ChatMutationData(
                action=request.action,
                chat_id=request.chat_id,
                changed=False,
                requested_title=title,
                requested_pinned=None,
                verified=False,
            )
        return ChatMutationData(
            action=request.action,
            chat_id=request.chat_id,
            changed=False,
            requested_title=None,
            requested_pinned=(request.action == "pin"),
            verified=False,
        )

    def _failure(
        self,
        request: ChatMutationRequest,
        data: ChatMutationData,
        code: DomainErrorCode,
        message: str,
        status: str,
    ) -> DomainResult[ChatMutationData]:
        return DomainResult.failure(
            code,
            message,
            verification_status=status,
            data=data,
            details={"service": "chat_management", "action": request.action},
        )

    @staticmethod
    def _changes(request: ChatMutationRequest) -> dict[str, Any]:
        if request.action == "rename":
            title = request.title.strip() if isinstance(request.title, str) else request.title
            return {"title": title}
        return {"is_pinned": request.action == "pin"}

    @staticmethod
    def _already_matches(request: ChatMutationRequest, metadata: ChatMetadata) -> bool:
        if request.action == "rename":
            title = request.title.strip() if isinstance(request.title, str) else request.title
            return metadata.title == title
        if request.action == "pin":
            return metadata.is_pinned is True
        return metadata.is_pinned is False

    @staticmethod
    def _verify(request: ChatMutationRequest, observed: ChatMetadata) -> tuple[bool, str]:
        if observed.chat_id != request.chat_id:
            return False, STATUS_READ_BACK_WRONG_CHAT
        if request.action == "rename":
            title = request.title.strip() if isinstance(request.title, str) else request.title
            if observed.title is None:
                return False, STATUS_READ_BACK_FIELD_MISSING
            if observed.title != title:
                return False, STATUS_READ_BACK_MISMATCH
        else:
            wanted = request.action == "pin"
            if observed.is_pinned is None:
                # Missing pinned state is never coerced into False.
                return False, STATUS_READ_BACK_FIELD_MISSING
            # Strict boolean identity: 1 or "false" never verify as a boolean
            # despite Python coercion or equality.
            if observed.is_pinned is not wanted:
                return False, STATUS_READ_BACK_MISMATCH
        return True, STATUS_VERIFIED
