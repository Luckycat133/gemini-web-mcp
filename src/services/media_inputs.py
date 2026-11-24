"""Validate ordered local references and resolve a saved image by durable identity."""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from ..infrastructure.account_identity import operation_account_matches
from ..infrastructure.operation_store import RETENTION_SECONDS, OperationStore
from ..tools.utils import MAX_IMAGE_ATTACHMENT_BYTES
from .media_storage import _decode_generated_image, verify_saved_media_file


class MediaSourceUnavailable(ValueError):
    pass


def image_workflow_options_error(
    *, media_type: str, image_path: str | None, image_paths: list[str] | None,
    source_operation_id: str | None, source_artifact_id: str | None, background: bool = True,
) -> str | None:
    if all(value is None for value in (image_paths, source_operation_id, source_artifact_id)):
        return None
    if media_type != "image" or not background:
        return "Image references and prior artifacts require background image generation or editing."
    if image_paths is not None and (image_path is not None or not image_paths):
        return "Provide a nonempty image_paths list or a single image_path."
    if source_operation_id is not None and not re.fullmatch(r"op_[0-9a-f]{32}", source_operation_id):
        return "source_operation_id must be a valid media operation handle."
    if source_artifact_id is not None and (not source_operation_id or not source_artifact_id.strip()):
        return "source_artifact_id requires a source operation and a nonblank artifact ID."
    return None


def resolve_saved_image(store: OperationStore, operation_id: str, artifact_id: str | None) -> dict:
    parent = store.get(operation_id)
    if (parent is None or not operation_account_matches(parent) or parent.media_type != "image"
            or parent.state != "completed"
            or (store.clock() - parent.created_at >= RETENTION_SECONDS and not parent.cleanup_protected)):
        raise MediaSourceUnavailable("Source must be a completed image operation available to this account.")
    matches = [
        item for item in store.get_media_artifacts(operation_id)
        if item["kind"] == "image" and (artifact_id is None or item["artifact_id"] == artifact_id)
    ]
    if len(matches) > 1:
        raise MediaSourceUnavailable("Select the source image with source_artifact_id.")
    if not matches:
        raise MediaSourceUnavailable("The source operation has no matching saved image.")
    return matches[0]


def verify_source_image(item: dict, path: Path) -> None:
    try:
        if item["size_bytes"] > MAX_IMAGE_ATTACHMENT_BYTES:
            raise ValueError("source exceeds attachment size limit")
        verify_saved_media_file(path, size_bytes=item["size_bytes"], sha256=item["sha256"])
        _decode_generated_image(str(path))
    except (OSError, ValueError) as error:
        raise MediaSourceUnavailable(
            "The saved source image is missing, changed, invalid, or exceeds the image attachment limit."
        ) from error


@contextmanager
def source_image_upload(store: OperationStore, operation_id: str, artifact_id: str) -> Iterator[str]:
    """Keep the SDK's filename-based media type and upload only the verified bytes."""
    item = resolve_saved_image(store, operation_id, artifact_id)
    source = Path(item["local_path"])
    with TemporaryDirectory(prefix="gemini-image-source-") as directory:
        upload = Path(directory) / source.name
        try:
            with source.open("rb") as original, upload.open("wb") as snapshot:
                snapshot.write(original.read(min(item["size_bytes"], MAX_IMAGE_ATTACHMENT_BYTES) + 1))
        except OSError as error:
            raise MediaSourceUnavailable("The saved source image could not be prepared for upload.") from error
        verify_source_image(item, upload)
        yield str(upload)
