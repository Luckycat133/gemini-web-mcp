"""Resolve report inputs for local derivatives without regenerating research."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState
from ..infrastructure.account_identity import current_account_key, operation_account_matches
from ..infrastructure.operation_store import RETENTION_SECONDS, OperationStore
from .research_result_storage import RESEARCH_OPERATIONS, ResearchResultUnavailable, load_research_result


@dataclass(frozen=True)
class ResearchReportSource:
    """Transient full report; never attach this internal DTO to an MCP result."""

    output: Any
    chat_id: str
    account_key: str
    operation_id: str | None = None

    def account_matches(self) -> bool:
        return self.account_key == current_account_key()


def _saved_output(text: str) -> Any:
    sources = []
    seen: set[str] = set()
    for title, url in re.findall(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", text):
        try:
            parsed = urlsplit(url)
            if not parsed.hostname or url in seen:
                continue
        except ValueError:
            continue
        seen.add(url)
        sources.append({"url": url, "title": title})
    heading = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
    return SimpleNamespace(text=text, report=text, sources=sources,
        title=heading.group(1) if heading else "Research report", immersive_id="",
        native_web_menu_observed=False)


async def resolve_research_report(
    *, chat_id: str | None, operation_id: str | None,
    client_provider: Callable[[], Any], client_initializer: Callable[[], Awaitable[Any]],
    fetch_report: Callable[[Any, str], Awaitable[Any]], store: OperationStore | None = None,
) -> tuple[ResearchReportSource | None, DomainResult[Any] | None]:
    """Require one source; operation handles are strictly local, read-only inputs."""
    if ((chat_id is None) == (operation_id is None)
            or (chat_id is not None and not chat_id.strip())
            or (operation_id is not None and not operation_id.strip())):
        return None, DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT,
            "Supply exactly one nonblank chat_id or operation_id.")
    if operation_id is not None:
        try:
            ledger = store or OperationStore()
            record = ledger.get(operation_id)
            if record is None or record.operation not in RESEARCH_OPERATIONS:
                return None, DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT,
                    "The handle does not identify a saved Research operation.", verification_status="operation_not_found")
            if not operation_account_matches(record):
                return None, DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                    "Operation account could not be verified.", verification_status="account_mismatch_or_unbound")
            if ledger.clock() - record.created_at >= RETENTION_SECONDS and not record.cleanup_protected:
                return None, DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT,
                    "The operation handle has expired.", verification_status="operation_expired")
            saved = load_research_result(ledger, record, include_text=True)
            if saved is not None and saved[1] is not None and record.account_key and record.upstream_chat_id:
                return ResearchReportSource(_saved_output(saved[1]), record.upstream_chat_id,
                                            record.account_key, operation_id), None
            reason = "saved_report_not_available"
        except (ResearchResultUnavailable, OSError, UnicodeError):
            reason = "saved_report_unverified"
        except Exception:
            return None, DomainResult.failure(DomainErrorCode.INTERNAL_ERROR,
                "The saved report metadata could not be read.", verification_status="saved_report_unavailable")
        return None, DomainResult.success(
            {"operation_id": operation_id, "state": "partial", "report_available": False},
            operation_state=OperationState.PARTIAL, verification_status=reason,
            warnings=(DomainWarning(code=reason,
                message="No verified local report is available. Read the original operation with gemini_get_operation_result; this tool did not access Gemini or regenerate research."),))
    assert chat_id is not None
    previous_owner = current_account_key()
    try:
        await client_initializer()
        owner = current_account_key()
        if not owner or (previous_owner is not None and owner != previous_owner):
            return None, DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                "Report account could not be verified.", verification_status="account_mismatch_or_unbound")
        output = await fetch_report(client_provider(), chat_id)
        if owner != current_account_key():
            return None, DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                "Report account changed while reading.", verification_status="account_mismatch_or_unbound")
        if output:
            return ResearchReportSource(output, chat_id, owner), None
    except Exception:
        return None, DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
            "The report could not be read.", verification_status="report_read_unverified")
    return None, DomainResult.success(
        {"chat_id": chat_id, "state": "partial", "report_available": False},
        operation_state=OperationState.PARTIAL, verification_status="report_not_observed")
