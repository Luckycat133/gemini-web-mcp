"""Exact-account Notebook/source mutations with independent metadata read-back."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..client_manager import ClientAuthenticationRequired
from ..domain import DomainErrorCode, DomainResult
from ..domain.notebook_admin import NotebookChange
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.notebook_admin import create_payload, get_payload, source_create_payload, source_delete_payload
from ..services.deadlines import await_before_deadline


@dataclass(frozen=True)
class NotebookAdminDependencies:
    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    request: Callable[..., Awaitable[Any]]
    list_projects: Callable[..., Awaitable[Any]]
    account_key_provider: Callable[[], str | None] = current_account_key


class NotebookAdminService:
    def __init__(self, dependencies: NotebookAdminDependencies) -> None:
        self.deps = dependencies

    async def run(self, action: str, *, notebook_id: str | None = None, source_id: str | None = None,
                  title: str | None = None, text: str | None = None,
                  limit: int = 20, offset: int = 0) -> DomainResult[Any]:
        try:
            if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
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
                if source_id is not None:
                    raise ValueError
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
                fence()
                result = await await_before_deadline(self.deps.request(client, key, **kw), timeout=35)
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
            sent = True
            if action == "create":
                result = await request("notebook_admin.create", title=title or "")
                if result.status != "success" or not isinstance(result.value, str):
                    raise ValueError
                actual_id = result.value
                after = await get(actual_id)
                verified = after["title"] == title
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
            safe_data = NotebookChange(actual_id, actual_source, False, None) if sent and self.deps.account_key_provider() == owner else None
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Notebook operation could not be verified.",
                                        data=safe_data, verification_status="mutation_unverified" if sent else "read_unverified")
