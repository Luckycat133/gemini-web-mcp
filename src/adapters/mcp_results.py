"""Preserve concise text while exposing machine-readable domain result metadata."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import wraps
from typing import Any, Awaitable, Callable, ParamSpec, TypeVar

from mcp.types import TextContent

from ..domain import DomainResult, result_from_exception

T = TypeVar("T")
P = ParamSpec("P")


def domain_text(
    result: DomainResult[T],
    text: str,
    *,
    data: Any = None,
    use_result_data: bool = False,
) -> list[TextContent]:
    payload = result.to_dict() if use_result_data else result.to_dict(data=data)
    return [
        TextContent(
            type="text",
            text=text,
            _meta={"domain_result": payload},
        )
    ]


def attach_domain_result(
    content: Sequence[TextContent],
    result: DomainResult[T],
    *,
    data: Any = None,
    use_result_data: bool = False,
) -> list[TextContent]:
    payload = result.to_dict() if use_result_data else result.to_dict(data=data)
    output = list(content)
    if not output:
        return domain_text(result, "", data=data, use_result_data=use_result_data)
    first = output[0]
    meta = dict(first.meta or {})
    meta["domain_result"] = payload
    output[0] = first.model_copy(update={"meta": meta})
    return output


def exception_text(
    error: BaseException,
    *,
    logger: logging.Logger,
    operation: str,
    prefix: str = "Error",
    preserve_message: bool = False,
) -> list[TextContent]:
    result = result_from_exception(error, logger=logger, operation=operation)
    assert result.error is not None
    if preserve_message:
        return domain_text(result, f"{prefix}: {error}")
    text = f"{prefix}: {result.error.code.value}: {result.error.message}"
    if result.error.suggested_action:
        text += f"\nSuggested action: {result.error.suggested_action}"
    text += f"\nDiagnostic ID: {result.error.diagnostic_id}"
    return domain_text(result, text)


def domain_error_boundary(
    operation: str,
    logger: logging.Logger,
) -> Callable[
    [Callable[P, Awaitable[list[TextContent]]]],
    Callable[P, Awaitable[list[TextContent]]],
]:
    """Convert unexpected tool exceptions into logged, typed MCP failures."""

    def decorate(
        function: Callable[P, Awaitable[list[TextContent]]],
    ) -> Callable[P, Awaitable[list[TextContent]]]:
        @wraps(function)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> list[TextContent]:
            try:
                return await function(*args, **kwargs)
            except Exception as error:
                return exception_text(
                    error,
                    logger=logger,
                    operation=operation,
                )

        return wrapped

    return decorate
