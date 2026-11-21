"""Execute released Prompt actions against the shared local store."""

import logging
import re
from collections.abc import Callable
from dataclasses import replace
from string import Formatter
from typing import Any

from ..domain import DomainErrorCode, DomainResult, DomainWarning, result_from_exception
from ..domain.prompt_requests import PromptsRequest
from .history import paginate_items
from .prompts import PromptLibrary

logger = logging.getLogger(__name__)
_MUTATIONS = {"create", "update", "delete"}
_STRING_SPEC = re.compile(r"(?:(?:.[<^>])|[<^>])?[0-9]{0,4}(?:\.[0-9]{1,4})?s?")
_MAX_RENDERED_LENGTH = 1_000_000


def _render(template: str, variables: dict[str, str]) -> str:
    """Use only named strings supplied by the request, not object traversal."""
    formatter = Formatter()
    chunks = []
    size = 0
    for literal, field, spec, conversion in formatter.parse(template):
        size += len(literal)
        if size > _MAX_RENDERED_LENGTH:
            raise ValueError("Prompt render too long")
        chunks.append(literal)
        if field is None:
            continue
        if not field or field.isdecimal() or any(char in field for char in ".[]"):
            raise ValueError("Prompt field must be a named variable")
        if spec is None or not _STRING_SPEC.fullmatch(spec) or conversion not in {None, "s", "r", "a"}:
            raise ValueError("Unsupported Prompt format")
        value = variables[field]
        if len(value) > _MAX_RENDERED_LENGTH - size:
            raise ValueError("Prompt render too long")
        chunk = formatter.format_field(formatter.convert_field(value, conversion), spec)
        size += len(chunk)
        if size > _MAX_RENDERED_LENGTH:
            raise ValueError("Prompt render too long")
        chunks.append(chunk)
    return "".join(chunks)


def _execute(library: PromptLibrary, action: str, args: dict[str, Any]) -> DomainResult[dict[str, Any]]:
    if action == "categories":
        return DomainResult.success({"categories": sorted({item["category"] for item in library.list()})},
                                    verification_status="observed")
    if action == "list":
        page, pagination = paginate_items(library.list(args.get("category")), args["limit"], args["offset"])
        return DomainResult.success({**pagination, "items": page}, verification_status="observed")
    if action == "create":
        prompt_id = library.create(**args)
        item = library.get(prompt_id)
        if item is not None:
            return DomainResult.success({"item": item}, verification_status="verified_created")
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "Created Prompt could not be read back.",
                                    data={"prompt_id": prompt_id})

    prompt_id = args.pop("prompt_id")
    existing = library.get(prompt_id)
    if existing is None:
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Prompt ID was not found.",
                                    verification_status="target_not_found")
    if action == "get":
        return DomainResult.success({"item": existing}, verification_status="observed")
    if action == "render":
        try:
            text = _render(existing["content"], args["variables"])
        except (KeyError, ValueError):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Prompt variables or template are invalid.")
        return DomainResult.success({"prompt_id": prompt_id, "text": text}, verification_status="rendered_local")
    if action == "update":
        changed = library.update(prompt_id, **args)
        item = library.get(prompt_id) if changed else None
        if item is not None and all(item.get(key) == value for key, value in args.items()):
            return DomainResult.success({"item": item}, verification_status="verified_updated")
        return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "Updated Prompt could not be read back.",
                                    data={"prompt_id": prompt_id})
    library.delete(prompt_id)
    if library.get(prompt_id) is None:
        return DomainResult.success({"prompt_id": prompt_id, "deleted": True}, verification_status="verified_absent")
    return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "Deleted Prompt is still present.",
                                data={"prompt_id": prompt_id})


def execute_prompt_request(
    library_provider: Callable[[], PromptLibrary], request: PromptsRequest,
) -> DomainResult[dict[str, Any]]:
    args = request.model_dump(exclude_none=True)
    action = args.pop("action")
    effect = "mutation" if action in _MUTATIONS else "read"
    identifier = args.get("prompt_id")
    try:
        result = _execute(library_provider(), action, args)
    except Exception as error:
        result = result_from_exception(error, logger=logger, operation=f"account.prompts.{action}")
        if effect == "mutation" and result.error is not None:
            result = replace(
                result, data={"prompt_id": identifier} if identifier else {},
                error=replace(result.error, retryable=False,
                              suggested_action="Read the current local library before retrying; do not repeat creation."),
                warnings=(*result.warnings, DomainWarning("MUTATION_UNVERIFIED", "The mutation effect is unverified.")),
            )
    return replace(result, meta=replace(result.meta, details={
        **result.meta.details, "facade": "prompts", "action": action, "effect": effect,
    }))
