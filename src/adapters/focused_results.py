"""Validated direct domain output schemas for the dedicated capability surfaces."""

from __future__ import annotations

from typing import Annotated, Any, Generic, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict

from ..domain import ArtifactResultData, DomainError, DomainResult, DomainWarning, LongOperationData, ResultMeta
from .mcp_results import domain_failure_text, domain_text
from .mcp_sdk import CallToolResult

DataT = TypeVar("DataT")


class FocusedDomainResult(BaseModel, Generic[DataT]):
    """The authoritative domain envelope, also visible to modern MCP clients."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: DataT | None = None
    error: DomainError | None = None
    warnings: tuple[DomainWarning, ...] = ()
    meta: ResultMeta


ArtifactToolResult: TypeAlias = Annotated[CallToolResult, FocusedDomainResult[ArtifactResultData]]
OperationToolResult: TypeAlias = Annotated[CallToolResult, FocusedDomainResult[LongOperationData]]
AccountToolResult: TypeAlias = Annotated[CallToolResult, FocusedDomainResult[dict[str, Any]]]


def focused_result(result: DomainResult[Any], summary: str) -> CallToolResult:
    """Preserve compatibility text/meta while returning the direct typed envelope.

    MCP validates structured_content against the model in each tool's return
    annotation. No protocol types or rendering choices enter the services.
    """
    return CallToolResult(
        content=[*domain_text(
            result, summary if result.ok else domain_failure_text(result), use_result_data=True,
        )],
        structured_content=result.to_dict(),
    )
