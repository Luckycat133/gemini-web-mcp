"""Seven focused creation tools over shared artifact and operation services."""

import logging
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field

from .. import __version__
from ..adapters.focused_results import ArtifactToolResult, OperationToolResult, focused_result
from ..adapters.mcp_sdk import CallToolResult, MCPServer
from ..client_wrapper import init_cookie_manager_integration
from ..domain import DomainResult
from ..domain.results import result_from_exception
from ..services.creation import CreationRequest, get_creation_service
from ..services.operations import OperationContext, OperationService, get_operation_service
from ..tools.annotations import MUTATES_REMOTE, READS_PRIVATE_REMOTE

logger = logging.getLogger(__name__)
SERVER_NAME = "gemini_create_mcp"
mcp = MCPServer(
    name=SERVER_NAME, version=__version__,
    instructions="""Create image, video and music artifacts with Gemini Web.
Image generation and local-image editing return artifact results synchronously.
Video and music start once asynchronously and return an opaque operation_id.
Use operation status/result with that handle, including after a server restart;
never repeat a generation to recover a timeout. Metadata/locators expire after
seven days; prompts and response contents are not persisted in the state store.
Local output is saved and verified before automatic owned-chat cleanup. Queued,
unsaved or uncertain output retains its source; cleanup metadata is independent
of artifact readiness. Cancellation before provider submission can be confirmed
locally as local_cancelled_before_start. Submitted provider work remains
cancel_requested unless provider confirmation is observed. Tools use the
existing authenticated Gemini Web account.
""",
)

Model = Literal["flash-lite", "flash", "thinking", "pro"]
ThinkingLevel = Literal["standard", "extended"]
Timeout = Annotated[int | None, Field(ge=1, le=1800)]
CleanupDelay = Annotated[int | None, Field(ge=0, le=86400)]
IdempotencyKey = Annotated[str | None, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


async def _generate(request: CreationRequest) -> CallToolResult:
    try:
        result = await get_creation_service().generate(request)
    except Exception as error:
        result = result_from_exception(error, logger=logger, operation="creation")
    return focused_result(result, "Creation result; inspect artifact verification and cleanup metadata.")


def _operations() -> OperationService:
    service = get_operation_service()
    for kind in ("music", "video"):
        service.register_recovery(kind, get_creation_service().recover)
    return service


async def _start(request: CreationRequest, idempotency_key: str | None = None) -> CallToolResult:
    creation = get_creation_service()
    invalid = creation.validate(request)
    if invalid is not None:
        return focused_result(invalid, "Invalid creation request.")

    async def runner(context: OperationContext) -> DomainResult[Any]:
        owned_request = replace(
            request, filename=request.filename or context.operation_id,
            output_dir=str(Path(request.output_dir or "generated_media").expanduser().absolute()),
        )
        return await creation.generate(
            owned_request, on_chat_observed=context.observe_chat_sync,
            on_artifacts_observed=context.observe_artifacts_sync,
        )

    try:
        result = await _operations().start(
            request.media_type, runner, recovery=creation.recover,
            output_dir=str(Path(request.output_dir or "generated_media").expanduser().absolute()),
            retain_chat=request.retain_chat, delete_after_seconds=request.delete_after_seconds,
            idempotency_key=idempotency_key,
        )
    except Exception as error:
        result = result_from_exception(error, logger=logger, operation="creation.start")
    return focused_result(result, "Creation operation registered; inspect its state and use the same handle for recovery.")


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_generate_image(
    prompt: str, model: Model = "flash", thinking_level: ThinkingLevel = "standard",
    output_dir: str | None = None, filename: str | None = None,
    timeout_seconds: Timeout = None, retain_chat: bool = False,
    delete_after_seconds: CleanupDelay = None,
) -> ArtifactToolResult:
    """Generate an image using the native image mode, then save and verify it.

    Return verified local artifacts and structured cleanup observations. Only
    this request's saved or definitively empty chat is eligible for cleanup;
    queued, remote-only and failed downloads retain their recoverable source.
    """
    return await _generate(CreationRequest(
        prompt, "image", model, thinking_level, output_dir=output_dir, filename=filename,
        timeout_seconds=timeout_seconds, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
    ))


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_edit_image(
    prompt: str, image_path: str, model: Model = "flash", thinking_level: ThinkingLevel = "standard",
    output_dir: str | None = None, filename: str | None = None,
    timeout_seconds: Timeout = None, retain_chat: bool = False,
    delete_after_seconds: CleanupDelay = None,
) -> ArtifactToolResult:
    """Edit one local image in native image mode and return verified saved output.

    Input and generated output keep distinct artifact identities. The same
    owned-chat cleanup and recovery policy as image generation applies.
    """
    return await _generate(CreationRequest(
        prompt, "image_edit", model, thinking_level, image_path=image_path,
        output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
        retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
    ))


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_generate_video(
    prompt: str, model: Model = "flash", thinking_level: ThinkingLevel = "standard",
    output_dir: str | None = None, filename: str | None = None,
    timeout_seconds: Timeout = None, retain_chat: bool = False,
    delete_after_seconds: CleanupDelay = None,
    idempotency_key: IdempotencyKey = None,
) -> OperationToolResult:
    """Start one native video request asynchronously and return its operation_id.

    Use status/result to recover the existing request after a timeout or server
    restart. No automatic resubmission occurs. Readiness requires verified video
    bytes; prose or image output does not count as a video. Reuse an opaque
    idempotency_key to retrieve the same start after response loss; never use
    request content as the key.
    """
    return await _start(CreationRequest(
        prompt, "video", model, thinking_level, output_dir=output_dir, filename=filename,
        timeout_seconds=timeout_seconds, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
    ), idempotency_key)


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_generate_music(
    prompt: str, model: Model = "flash", thinking_level: ThinkingLevel = "standard",
    output_dir: str | None = None, filename: str | None = None,
    timeout_seconds: Timeout = None, retain_chat: bool = False,
    delete_after_seconds: CleanupDelay = None,
    idempotency_key: IdempotencyKey = None,
) -> OperationToolResult:
    """Start one native music request asynchronously and return its operation_id.

    Poll the existing operation for independently saved audio and video outputs.
    Metadata-only recovery lasts seven days; never regenerate to resume a run.
    An opaque idempotency_key safely identifies a repeated start after response
    loss; use the same key instead of a new request or the prompt text.
    """
    return await _start(CreationRequest(
        prompt, "music", model, thinking_level, output_dir=output_dir, filename=filename,
        timeout_seconds=timeout_seconds, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
    ), idempotency_key)


async def _operation(action: str, operation_id: str) -> CallToolResult:
    try:
        result = await getattr(_operations(), action)(operation_id)
    except Exception as error:
        result = result_from_exception(error, logger=logger, operation=f"creation.{action}")
    return focused_result(result, "Operation state and recoverable artifacts.")


@mcp.tool(annotations=READS_PRIVATE_REMOTE)
async def gemini_get_operation_status(operation_id: str) -> OperationToolResult:
    """Read one owned operation's state, using its saved source without resubmission."""
    return await _operation("status", operation_id)


@mcp.tool(annotations=READS_PRIVATE_REMOTE)
async def gemini_get_operation_result(operation_id: str) -> OperationToolResult:
    """Recover verified artifacts from this operation's existing source or local files."""
    return await _operation("result", operation_id)


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_cancel_operation(operation_id: str) -> OperationToolResult:
    """Request cancellation and inspect local or provider confirmation.

    A local cancel request does not delete the provider source or start another
    generation. local_cancelled_before_start proves no provider submission;
    otherwise check cancellation_confirmed and the returned operation state.
    """
    return await _operation("cancel", operation_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting %s (v%s)", SERVER_NAME, __version__)
    init_cookie_manager_integration()
    mcp.run()


if __name__ == "__main__":
    main()
