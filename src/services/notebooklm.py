"""Application service for built-in Gemini NotebookLM notebooks.

The source picker and Gemini project catalog use different transport shapes.
This service lists verified notebook identities from one fresh upstream read per
call and resolves exact notebook ID selections. No notebook contents or
source text are returned.

The account owner is bound during asynchronous initialization and the
current account key is fenced after every await, so an account switch
before or after the fetch refuses instead of returning another account's
notebooks.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..domain import DomainErrorCode, DomainResult, result_from_exception
from ..domain.notebooklm import NotebookLMListData, NotebookLMNotebook, NotebookLMSelectionData
from ..infrastructure.account_identity import current_account_key

logger = logging.getLogger(__name__)

NOTEBOOKLM_SOURCE = "notebooklm"
_MIN_LIMIT = 1
_MAX_LIMIT = 100
_MIN_OFFSET = 0
_MAX_OFFSET = 10000
_MIN_RESOLVE_IDS = 1
_MAX_RESOLVE_IDS = 10
_MAX_ID_LENGTH = 256


@dataclass(frozen=True)
class NotebookLMServiceDependencies:
    """Seams bound at call time. ``client_provider`` is synchronous."""

    client_initializer: Callable[[], Awaitable[Any]]
    client_provider: Callable[[], Any]
    fetch_notebooks: Callable[[Any], Awaitable[tuple[NotebookLMNotebook, ...]]]
    account_key_provider: Callable[[], str | None] = current_account_key


class NotebookLMService:
    """List and resolve built-in NotebookLM notebooks for one account."""

    def __init__(self, dependencies: NotebookLMServiceDependencies) -> None:
        self._dependencies = dependencies

    @staticmethod
    def _account_failure(message: str) -> DomainResult[Any]:
        return DomainResult.failure(
            DomainErrorCode.AUTH_REQUIRED,
            message,
            suggested_action="Sign in to Gemini Web and retry.",
            verification_status="account_mismatch_or_unbound",
        )

    @staticmethod
    def _invalid_argument(message: str) -> DomainResult[Any]:
        return DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT,
            message,
            suggested_action="Correct the arguments and retry.",
            verification_status="input_rejected",
        )

    async def _prepare_client(self) -> DomainResult[tuple[Any, str]]:
        """Bind the owner during init; optional pre-init owner must match post-init owner."""
        pre_owner = self._dependencies.account_key_provider()
        await self._dependencies.client_initializer()
        client = self._dependencies.client_provider()
        owner = self._dependencies.account_key_provider()
        if not owner:
            return self._account_failure("Authentication is required for NotebookLM.")
        if pre_owner is not None and owner != pre_owner:
            return self._account_failure("The NotebookLM account changed during initialization.")
        return DomainResult.success((client, owner))

    def _fence(self, owner: str) -> DomainResult[Any] | None:
        """Refuse when the account changed since the last await."""
        if self._dependencies.account_key_provider() != owner:
            return self._account_failure("The NotebookLM account changed during the operation.")
        return None

    @staticmethod
    def _verify_fetched(notebooks: Any) -> DomainResult[Any] | None:
        """Reject malformed upstream data; unknown counts and times stay unknown."""

        def _reject() -> DomainResult[Any]:
            return DomainResult.failure(
                DomainErrorCode.UPSTREAM_CHANGED,
                "NotebookLM data could not be verified from upstream.",
                suggested_action="Retry the list request; report the diagnostic ID if it persists.",
                verification_status="upstream_changed",
                details={"service": NOTEBOOKLM_SOURCE},
            )

        if not isinstance(notebooks, (list, tuple)):
            return _reject()
        seen: set[str] = set()
        for item in notebooks:
            if not isinstance(item, NotebookLMNotebook):
                return _reject()
            if (not isinstance(item.notebook_id, str) or not item.notebook_id.strip()
                    or len(item.notebook_id) > _MAX_ID_LENGTH or item.notebook_id in seen):
                return _reject()
            seen.add(item.notebook_id)
            if not isinstance(item.title, str):
                return _reject()
            if item.source_count is not None and (
                type(item.source_count) is not int or item.source_count < 0
            ):
                return _reject()
            if item.created_at is not None and not isinstance(item.created_at, str):
                return _reject()
        return None

    async def list_notebooks(
        self,
        query: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> DomainResult[NotebookLMListData]:
        """Return one fresh typed page of notebooks matching query on title (case-insensitive substring)."""
        if not isinstance(query, str):
            return self._invalid_argument("query must be a string.")
        if type(limit) is not int or not _MIN_LIMIT <= limit <= _MAX_LIMIT:
            return self._invalid_argument(
                f"limit must be an integer between {_MIN_LIMIT} and {_MAX_LIMIT}."
            )
        if type(offset) is not int or not _MIN_OFFSET <= offset <= _MAX_OFFSET:
            return self._invalid_argument(
                f"offset must be an integer between {_MIN_OFFSET} and {_MAX_OFFSET}."
            )

        try:
            prepared = await self._prepare_client()
        except Exception as error:
            return result_from_exception(error, logger=logger, operation="gemini_notebooklm_list_notebooks")
        if not prepared.ok:
            return prepared  # type: ignore[return-value]
        assert prepared.data is not None
        client, owner = prepared.data

        fence = self._fence(owner)
        if fence is not None:
            return fence  # type: ignore[return-value]

        try:
            notebooks = await self._dependencies.fetch_notebooks(client)
        except Exception as error:
            return result_from_exception(error, logger=logger, operation="gemini_notebooklm_list_notebooks")

        fence = self._fence(owner)
        if fence is not None:
            return fence  # type: ignore[return-value]

        rejected = self._verify_fetched(notebooks)
        if rejected is not None:
            return rejected

        query_fold = query.casefold()
        if query_fold:
            matched = [notebook for notebook in notebooks if query_fold in notebook.title.casefold()]
        else:
            matched = list(notebooks)

        total_count = len(matched)
        page = matched[offset : offset + limit]
        count = len(page)
        has_more = offset + count < total_count
        next_offset = offset + count if has_more else None
        data = NotebookLMListData(
            source=NOTEBOOKLM_SOURCE,
            items=tuple(page),
            count=count,
            total_count=total_count,
            has_more=has_more,
            next_offset=next_offset,
            query=query,
        )
        return DomainResult.success(
            data,
            verification_status="fresh_read_verified",
            details={"service": NOTEBOOKLM_SOURCE, "fetched_count": len(notebooks)},
        )

    async def resolve_notebooks(self, notebook_ids: Any) -> DomainResult[NotebookLMSelectionData]:
        """Resolve exact notebook ID matches in request order; never fuzzy title or silent fallback."""
        if not isinstance(notebook_ids, (list, tuple)):
            return self._invalid_argument("notebook_ids must be a list or tuple of notebook IDs.")
        size = len(notebook_ids)
        if size < _MIN_RESOLVE_IDS or size > _MAX_RESOLVE_IDS:
            return self._invalid_argument(
                f"notebook_ids must contain between {_MIN_RESOLVE_IDS} and {_MAX_RESOLVE_IDS} IDs."
            )
        seen: set[str] = set()
        ordered: list[str] = []
        for notebook_id in notebook_ids:
            if (
                not isinstance(notebook_id, str)
                or not notebook_id.strip()
                or len(notebook_id) > _MAX_ID_LENGTH
            ):
                return self._invalid_argument(
                    "Each notebook ID must be a nonblank string of at most 256 characters."
                )
            if notebook_id in seen:
                return self._invalid_argument(f"Duplicate notebook ID: {notebook_id}")
            seen.add(notebook_id)
            ordered.append(notebook_id)

        try:
            prepared = await self._prepare_client()
        except Exception as error:
            return result_from_exception(error, logger=logger, operation="gemini_notebooklm_resolve_notebooks")
        if not prepared.ok:
            return prepared  # type: ignore[return-value]
        assert prepared.data is not None
        client, owner = prepared.data

        fence = self._fence(owner)
        if fence is not None:
            return fence  # type: ignore[return-value]

        try:
            notebooks = await self._dependencies.fetch_notebooks(client)
        except Exception as error:
            return result_from_exception(error, logger=logger, operation="gemini_notebooklm_resolve_notebooks")

        fence = self._fence(owner)
        if fence is not None:
            return fence  # type: ignore[return-value]

        rejected = self._verify_fetched(notebooks)
        if rejected is not None:
            return rejected

        index = {notebook.notebook_id: notebook for notebook in notebooks}
        missing = [notebook_id for notebook_id in ordered if notebook_id not in index]
        if missing:
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                        "A selected notebook is not available to this account.",
                                        verification_status="notebook_not_found")
        items = tuple(index[notebook_id] for notebook_id in ordered)
        data = NotebookLMSelectionData(items=items, count=len(items))
        return DomainResult.success(
            data,
            verification_status="exact_matches_verified",
            details={"service": NOTEBOOKLM_SOURCE, "requested_count": len(ordered)},
        )


async def fetch_typed_notebooks(client: Any) -> tuple[NotebookLMNotebook, ...]:
    from .web_context_transport import fetch_notebooklm_metadata
    return tuple(NotebookLMNotebook(**item) for item in await fetch_notebooklm_metadata(client))


async def resolve_notebook_context(client: Any, notebook_ids: list[str] | tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Resolve explicit IDs against fresh same-account metadata for a generation."""
    if not notebook_ids:
        return ()
    async def initialized() -> None:
        return None
    service = NotebookLMService(NotebookLMServiceDependencies(
        client_initializer=initialized, client_provider=lambda: client, fetch_notebooks=fetch_typed_notebooks))
    result = await service.resolve_notebooks(notebook_ids)
    if not result.ok or result.data is None:
        from .model_policy import ModelPolicyError
        raise ModelPolicyError("The requested notebook context could not be verified.")
    return tuple((item.notebook_id, item.title) for item in result.data.items)
