"""Save a same-candidate generated Task file before remote cleanup."""

import hashlib
import os
from pathlib import Path
import tempfile
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from ..domain.artifacts import Artifact, ArtifactKind, ArtifactState, ArtifactVerification, ArtifactVerificationStatus
from ..domain.task_files import TaskFile

MAX_TASK_FILE_BYTES = 50_000_000


async def save_task_file(client: Any, item: TaskFile, *, output_dir: Path, chat_id: str,
                         rid: str, rcid: str, account_matches: Callable[[], bool] = lambda: True) -> Artifact:
    """Download the structured card without redirects and save under a stable identity."""
    url = urlsplit(item.download_url)
    if (url.scheme != "https" or url.hostname != "contribution.usercontent.google.com"
            or url.path != "/download" or url.username or url.password or url.port):
        raise ValueError("Unverified Task download endpoint")
    identity = hashlib.sha256(f"{chat_id}\0{rid}\0{rcid}\0{item.card_id}".encode()).hexdigest()[:24]
    suffix = Path(item.name).suffix.lower()
    if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
        suffix = ".bin"
    path = output_dir.resolve() / f"task-file-{identity}{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        if not account_matches():
            raise ValueError("Task account changed")
        async with client.stream("GET", item.download_url, allow_redirects=False, timeout=60) as response:
            if not account_matches():
                raise ValueError("Task account changed")
            if response.status_code != 200:
                raise ValueError("Task file download failed")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type and content_type not in {item.mime_type.lower(), "application/octet-stream"}:
                raise ValueError("Task file content type differs from card")
            size = 0
            hasher = hashlib.sha256()
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".task-file-", delete=False) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                async for chunk in response.aiter_content():
                    if not account_matches():
                        raise ValueError("Task account changed")
                    if not isinstance(chunk, bytes):
                        raise ValueError("Invalid Task file bytes")
                    size += len(chunk)
                    if size > MAX_TASK_FILE_BYTES:
                        raise ValueError("Task file exceeds the download limit")
                    hasher.update(chunk)
                    stream.write(chunk)
            if not size:
                raise ValueError("Task file is empty")
            digest = hasher.hexdigest()
        if not account_matches():
            raise ValueError("Task account changed")
        try:
            os.link(temporary, path)
        except FileExistsError:
            with path.open("rb") as previous:
                prior_digest = hashlib.file_digest(previous, "sha256").hexdigest()
            if path.stat().st_size != size or prior_digest != digest:
                raise ValueError("Task file identity conflicts with existing bytes") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    with path.open("rb") as saved:
        saved_digest = hashlib.file_digest(saved, "sha256").hexdigest()
    if path.stat().st_size != size or saved_digest != digest:
        raise ValueError("Task file readback failed")
    return Artifact(id=f"artifact_{identity}", kind=ArtifactKind.FILE, state=ArtifactState.LOCAL,
                    title=item.name, local_path=str(path), mime_type=item.mime_type, size_bytes=size,
                    source_chat_id=chat_id, verification=ArtifactVerification(
                        ArtifactVerificationStatus.VERIFIED,
                        methods=("exact_candidate_file_card", "downloaded", "sha256_checked"),
                        details={"sha256": digest}))
