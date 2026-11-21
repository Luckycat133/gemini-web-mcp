"""Direct MCP result contract from the released Prompt facade."""

from typing import Annotated, Any, Generic, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict

from ..domain import DomainError, DomainResult, DomainWarning, ResultMeta
from .mcp_results import domain_failure_text, domain_text
from .mcp_sdk import CallToolResult

DataT = TypeVar("DataT")


class FocusedDomainResult(BaseModel, Generic[DataT]):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: DataT | None = None
    error: DomainError | None = None
    warnings: tuple[DomainWarning, ...] = ()
    meta: ResultMeta


PromptToolResult: TypeAlias = Annotated[CallToolResult, FocusedDomainResult[dict[str, Any]]]


def prompt_result(result: DomainResult[dict[str, Any]]) -> CallToolResult:
    summary = "Local Prompt library result." if result.ok else domain_failure_text(result)
    return CallToolResult(content=[*domain_text(result, summary, use_result_data=True)], structured_content=result.to_dict())
