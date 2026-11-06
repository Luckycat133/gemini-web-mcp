"""Exact-account Notebook/source mutations with independent metadata read-back."""
from __future__ import annotations

from dataclasses import dataclass
import os
import stat
from typing import Any, Awaitable, Callable

from ..client_manager import ClientAuthenticationRequired
from ..domain import DomainErrorCode, DomainResult, OperationState
from ..domain.notebook_admin import NotebookChange, NotebookFileSourceChange, NotebookRegisteredSourceChange, NotebookURLSourceChange
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.notebook_admin import (
    NOTEBOOK_FILE_MAX_BYTES,
    NOTEBOOK_FILE_MIME,
    create_payload,
    get_payload,
    source_create_payload,
    source_delete_payload,
    source_file_create_payload,
    source_url_create_payload,
    upload_source_file,
)
from ..services.deadlines import await_before_deadline
from ..tools.utils import validate_local_file_path


@dataclass(frozen=True)
class NotebookAdminDependencies:
    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    request: Callable[..., Awaitable[Any]]
    list_projects: Callable[..., Awaitable[Any]]
    account_key_provider: Callable[[], str | None] = current_account_key
    upload_file: Callable[..., Awaitable[str]] = upload_source_file


def _read_source_file(file_path: str) -> bytes:
    if not isinstance(file_path, str):
        raise ValueError("Invalid Notebook source path.")
    valid, path = validate_local_file_path(file_path, allowed_extensions={".txt"}, max_bytes=NOTEBOOK_FILE_MAX_BYTES)
    if not valid:
        raise ValueError("Invalid Notebook source path.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as file:
            before = os.fstat(file.fileno())
            if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= NOTEBOOK_FILE_MAX_BYTES:
                raise ValueError("Invalid Notebook source file.")
            content = file.read(before.st_size + 1)
            after = os.fstat(file.fileno())
        if (len(content) != before.st_size or before.st_mtime_ns != after.st_mtime_ns
                or b"\x00" in content or not content.decode("utf-8-sig").strip()):
            raise ValueError("Invalid Notebook source file.")
        return content
    except (OSError, UnicodeError):
        raise ValueError("Invalid Notebook source file.") from None


def _registered_source_result(notebook_id: str, source_id: str, source: dict[str, Any], *,
                              title: str, expected_location: str, file_source: bool) -> DomainResult[NotebookRegisteredSourceChange]:
    state = source.get("state")
    location_field = "source_upload_reference" if file_source else "source_url"
    shape_field = "source_upload_shape_valid" if file_source else "source_url_shape_valid"
    observed_location = source.get(location_field)
    observed_state = state if type(state) is int else None
    valid = (observed_state is not None and source.get(shape_field, True) is True
             and (observed_location is None or isinstance(observed_location, str)))
    location_verified = valid and observed_location == expected_location
    mime_matches = not file_source or all(source.get(key) in (None, NOTEBOOK_FILE_MIME)
                                          for key in ("mime", "source_upload_mime"))
    metadata_verified = (valid and source.get("title") == title and mime_matches
                         and (observed_location is None or location_verified))
    change: NotebookRegisteredSourceChange
    if file_source:
        change = NotebookFileSourceChange(notebook_id, source_id, metadata_verified, True,
                                         source_state=observed_state, upload_reference_verified=location_verified)
    else:
        change = NotebookURLSourceChange(notebook_id, source_id, metadata_verified, True,
                                         source_state=observed_state, url_verified=location_verified)
    if not valid:
        return DomainResult.failure(
            DomainErrorCode.UPSTREAM_CHANGED, "The Notebook source metadata could not be verified.",
            data=change, verification_status="source_metadata_unverified",
        )
    if not metadata_verified:
        return DomainResult.failure(
            DomainErrorCode.VERIFICATION_FAILED, "The added source metadata differs from the request.",
            data=change, verification_status="source_metadata_mismatch",
        )
    if state in {1, 4}:
        return DomainResult.success(change, operation_state=OperationState.ACCEPTED,
                                    verification_status="source_processing")
    if state == 2:
        if file_source:
            verification_status = "source_file_ready_verified" if location_verified else "source_file_reference_unobserved"
        else:
            verification_status = "source_url_ready_verified" if location_verified else "source_url_unobserved"
        return DomainResult.success(
            change, operation_state=OperationState.COMPLETED if location_verified else OperationState.ACCEPTED,
            verification_status=verification_status,
        )
    if state == 3:
        return DomainResult.failure(
            DomainErrorCode.UPSTREAM_REJECTED, "Gemini rejected indexing the Notebook source.",
            data=change, verification_status="source_ingestion_failed",
        )
    return DomainResult.failure(
        DomainErrorCode.VERIFICATION_FAILED, "Gemini returned an unrecognized Notebook source state.",
        data=change, verification_status="source_state_unrecognized",
    )


class NotebookAdminService:
    def __init__(self, dependencies: NotebookAdminDependencies) -> None:
        self.deps = dependencies

    async def run(self, action: str, *, notebook_id: str | None = None, source_id: str | None = None,
                  title: str | None = None, text: str | None = None, url: str | None = None,
                  file_path: str | None = None, limit: int = 20, offset: int = 0) -> DomainResult[Any]:
        file_content: bytes | None = None
        try:
            if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
                raise ValueError
            if action != "add_source" and (url is not None or file_path is not None):
                raise ValueError
            if action == "create":
                if notebook_id is not None or source_id is not None or text is not None:
                    raise ValueError
                create_payload(title=title or "")
            elif action in {"get", "list_sources", "delete"}:
                get_payload(notebook_id=notebook_id or "")
                if source_id is not None or title is not None or text is not None:
                    raise ValueError
            elif action == "add_source":
                if source_id is not None or sum(value is not None for value in (text, url, file_path)) != 1:
                    raise ValueError
                if url is not None:
                    source_url_create_payload(notebook_id=notebook_id or "", title=title or "", url=url)
                elif file_path is not None:
                    get_payload(notebook_id=notebook_id or "")
                    if not isinstance(title, str) or not title.strip() or len(title) > 256:
                        raise ValueError
                    file_content = _read_source_file(file_path)
                else:
                    source_create_payload(notebook_id=notebook_id or "", title=title or "", text=text or "")
            elif action == "delete_source":
                get_payload(notebook_id=notebook_id or "")
                source_delete_payload(source_id=source_id or "")
                if title is not None or text is not None:
                    raise ValueError
            else:
                raise ValueError
        except ValueError:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid Notebook request.")
        owner = self.deps.account_key_provider()
        sent = False
        actual_id, actual_source = notebook_id, source_id
        change_type: type[NotebookChange] = NotebookChange
        if file_content is not None:
            change_type = NotebookFileSourceChange
        elif url is not None:
            change_type = NotebookURLSourceChange
        try:
            try:
                await await_before_deadline(self.deps.client_initializer(), timeout=30)
            except ClientAuthenticationRequired:
                return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, "Gemini authentication is required.",
                                            verification_status="authentication_required")
            current = self.deps.account_key_provider()
            if not current or (owner and owner != current):
                raise ValueError
            owner = current
            client = self.deps.client_provider()
            def fence() -> None:
                if self.deps.account_key_provider() != owner:
                    raise ValueError
            async def request(key: str, **kw: Any) -> Any:
                async def issue() -> Any:
                    fence()
                    return await self.deps.request(client, key, expected_account=owner, **kw)
                result = await await_before_deadline(issue(), timeout=35)
                fence()
                return result
            async def get(identifier: str) -> dict[str, Any]:
                result = await request("notebook_admin.get", notebook_id=identifier)
                if result.status != "success" or not isinstance(result.value, dict) or result.value.get("notebook_id") != identifier:
                    raise ValueError
                return result.value
            async def projects() -> list[dict[str, Any]]:
                fence()
                rows, _diagnostic = await await_before_deadline(self.deps.list_projects(client), timeout=35)
                fence()
                if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] for row in rows):
                    raise ValueError
                return rows
            async def require_notebook(identifier: str) -> None:
                matches = [row for row in await projects() if row["id"] == identifier]
                if (len(matches) != 1 or type(matches[0].get("project_type")) is not int
                        or matches[0]["project_type"] != 2 or type(matches[0].get("project_subtype")) is not int
                        or matches[0]["project_subtype"] != 1):
                    raise ValueError
            if action != "create":
                await require_notebook(notebook_id or "")
            if action in {"get", "list_sources"}:
                data = await get(notebook_id or "")
                sources = data["sources"]
                if not isinstance(sources, list) or any(not isinstance(item, dict) for item in sources):
                    raise ValueError
                page = [{key: item.get(key) for key in ("source_id", "title", "state", "mime")}
                        for item in sources[offset:offset + limit]]
                end = offset + len(page)
                result = {"notebook_id": data["notebook_id"], "sources": page, "source_count": len(sources),
                          "offset": offset, "next_offset": end if end < len(sources) else None}
                if action == "get":
                    result["title"] = data["title"]
                return DomainResult.success(result, verification_status="fresh_read")
            before = await get(notebook_id or "") if action != "create" else None
            if action == "delete_source":
                assert before is not None
                if not any(x["source_id"] == source_id for x in before["sources"]):
                    raise ValueError
            upload_reference = None
            if file_content is not None:
                async def upload() -> str:
                    fence()
                    return await self.deps.upload_file(client, file_content, expected_account=owner)

                try:
                    upload_reference = await await_before_deadline(upload(), timeout=35)
                    fence()
                    source_file_create_payload(notebook_id=notebook_id or "", title=title or "",
                                               upload_reference=upload_reference)
                except Exception:
                    safe_data = change_type(notebook_id, None, False, None) if self.deps.account_key_provider() == owner else None
                    return DomainResult.failure(
                        DomainErrorCode.UPSTREAM_CHANGED, "Notebook file upload could not be verified.",
                        data=safe_data, verification_status="source_upload_unverified",
                    )
            sent = True
            if action == "create":
                result = await request("notebook_admin.create", title=title or "")
                if result.status != "success" or not isinstance(result.value, str):
                    raise ValueError
                actual_id = result.value
                after = await get(actual_id)
                verified = after["title"] == title
            elif action == "add_source" and (url is not None or upload_reference is not None):
                assert before is not None and notebook_id is not None and title is not None
                if upload_reference is not None:
                    receipt = await request("notebook_admin.source_create_file", notebook_id=notebook_id,
                                            title=title, upload_reference=upload_reference)
                else:
                    receipt = await request("notebook_admin.source_create_url", notebook_id=notebook_id,
                                            title=title, url=url)
                if receipt.status == "rejected":
                    return DomainResult.failure(
                        DomainErrorCode.UPSTREAM_REJECTED, "Gemini rejected the Notebook source request.",
                        data=change_type(notebook_id, None, False, None),
                        verification_status="source_create_rejected",
                    )
                if receipt.status != "success" or not isinstance(receipt.value, str) or not receipt.value:
                    raise ValueError
                if any(item["source_id"] == receipt.value for item in before["sources"]):
                    return DomainResult.failure(
                        DomainErrorCode.VERIFICATION_FAILED, "The creation receipt refers to an existing Notebook source.",
                        data=change_type(notebook_id, None, False, None),
                        verification_status="source_identity_unverified",
                    )
                actual_source = receipt.value
                after = await get(notebook_id)
                exact = [item for item in after["sources"] if item["source_id"] == actual_source]
                if len(exact) != 1:
                    return DomainResult.failure(
                        DomainErrorCode.VERIFICATION_FAILED, "The new Notebook source could not be read back.",
                        data=change_type(notebook_id, actual_source, False, None),
                        verification_status="mutation_unverified",
                    )
                return _registered_source_result(notebook_id, actual_source, exact[0], title=title,
                                                 expected_location=upload_reference or url or "",
                                                 file_source=upload_reference is not None)
            elif action == "add_source":
                assert before is not None
                await request("notebook_admin.source_create", notebook_id=notebook_id, title=title, text=text)
                after = await get(notebook_id or "")
                old = {x["source_id"] for x in before["sources"]}
                new = [x for x in after["sources"] if x["source_id"] not in old and x["title"] == title]
                verified = len(new) == 1
                actual_source = new[0]["source_id"] if verified else None
            elif action == "delete_source":
                await request("notebook_admin.source_delete", source_id=source_id)
                after = await get(notebook_id or "")
                verified = all(x["source_id"] != source_id for x in after["sources"])
            else:
                await request("notebook_admin.delete", notebook_id=notebook_id)
                verified = all(x["id"] != notebook_id for x in await projects())
            change = NotebookChange(actual_id, actual_source, bool(verified), True if verified else None)
            if verified:
                return DomainResult.success(change, verification_status="independent_metadata_readback")
            return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "Notebook mutation could not be verified.",
                                        data=change, verification_status="mutation_unverified")
        except Exception:
            safe_data = change_type(actual_id, actual_source, False, None) if sent and self.deps.account_key_provider() == owner else None
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Notebook operation could not be verified.",
                                        data=safe_data, verification_status="mutation_unverified" if sent else "read_unverified")
