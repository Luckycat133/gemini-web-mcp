"""Complete Task answers as independently verified local artifacts before cleanup."""

from dataclasses import replace
import hashlib
import os
import re
from pathlib import Path
import tempfile
from typing import Any

from ..domain.artifacts import Artifact, ArtifactKind, ArtifactState, ArtifactVerification, ArtifactVerificationStatus
from ..infrastructure.account_identity import operation_account_matches
from .artifacts import artifact_from_local_path


class TaskResultUnavailable(RuntimeError):
    code = "VERIFICATION_FAILED"


def _destination(store: Any, record: Any) -> Path:
    handle: str = record.operation_id
    if not re.fullmatch(r"op_[A-Za-z0-9_-]{1,128}", handle):
        raise TaskResultUnavailable("The Task artifact handle is invalid.")
    root = Path(store.path).resolve().parent
    path = (root / "artifacts" / handle / "task-result.md").resolve()
    if not path.is_relative_to(root):
        raise TaskResultUnavailable("The Task artifact path is outside its state directory.")
    return path


def load_task_result(store: Any, record: Any, *, include_text: bool) -> tuple[Artifact, str | None] | None:
    saved = store.get_media_artifacts(record.operation_id)
    if not saved:
        return None
    reports = [item for item in saved if item['kind'] == 'report']
    if len(reports) != 1 or not operation_account_matches(record):
        raise TaskResultUnavailable("Task artifact ownership could not be verified.")
    item = reports[0]
    expected = _destination(store, record)
    path = Path(item["local_path"]).resolve()
    if path != expected or item["kind"] != "report" or not path.is_file():
        raise TaskResultUnavailable("Task artifact was removed or changed.")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if path.stat().st_size != item["size_bytes"] or digest != item["sha256"]:
        raise TaskResultUnavailable("Task artifact bytes changed.")
    artifact = artifact_from_local_path(ArtifactKind.REPORT, str(path), title="Task response",
                                        source_chat_id=record.upstream_chat_id,
                                        requested_backend=record.requested_model)
    if artifact.id != item["artifact_id"] or artifact.state is not ArtifactState.LOCAL:
        raise TaskResultUnavailable("Task artifact identity changed.")
    artifact = replace(artifact, mime_type="text/markdown", verification=ArtifactVerification(
        ArtifactVerificationStatus.VERIFIED, methods=(*artifact.verification.methods, "sha256_checked"),
        details={"sha256": digest}))
    text = None
    if include_text:
        with path.open(encoding="utf-8") as stream:
            text = stream.read(4001)
    if not operation_account_matches(record):
        raise TaskResultUnavailable("Task artifact account changed.")
    return artifact, text


def load_task_files(store: Any, record: Any) -> tuple[Artifact, ...]:
    """Revalidate every committed file without contacting the upstream service."""
    if not store.task_files_committed(record.operation_id) or not operation_account_matches(record):
        raise TaskResultUnavailable('Task file manifest unavailable.')
    root = _destination(store, record).parent
    artifacts = []
    for item in store.get_media_artifacts(record.operation_id):
        if item['kind'] != 'file':
            continue
        path = Path(item['local_path']).resolve()
        if path.parent != root or not path.name.startswith('task-file-') or not path.is_file():
            raise TaskResultUnavailable('Task file was removed or changed.')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if path.stat().st_size != item['size_bytes'] or digest != item['sha256']:
            raise TaskResultUnavailable('Task file bytes changed.')
        artifacts.append(Artifact(id=item['artifact_id'], kind=ArtifactKind.FILE, state=ArtifactState.LOCAL,
                                  local_path=str(path), mime_type=item['mime_type'], size_bytes=item['size_bytes'],
                                  source_chat_id=record.upstream_chat_id,
                                  verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED,
                                      methods=('exact_candidate_file_card', 'sha256_checked'),
                                      details={'sha256': digest})))
    if not operation_account_matches(record):
        raise TaskResultUnavailable('Task file account changed.')
    return tuple(artifacts)


def save_task_result(store: Any, record: Any, text: str) -> Artifact:
    if not text.strip() or not operation_account_matches(record):
        raise TaskResultUnavailable("A same-account nonempty Task answer is required.")
    previous = load_task_result(store, record, include_text=False)
    if previous is not None:
        expected_hash = previous[0].verification.details["sha256"]
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_hash:
            raise TaskResultUnavailable("The Task answer differs from its saved artifact.")
        return previous[0]
    path = _destination(store, record)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".task-result-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text.encode("utf-8"))
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != text.encode("utf-8"):
                raise TaskResultUnavailable("The Task output destination contains different bytes.") from None
        artifact = artifact_from_local_path(ArtifactKind.REPORT, str(path), title="Task response",
                                            source_chat_id=record.upstream_chat_id,
                                            requested_backend=record.requested_model)
        store.save_task_result_artifact(record.operation_id, artifact)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    loaded = load_task_result(store, record, include_text=False)
    if loaded is None:
        raise TaskResultUnavailable("The Task result could not be read back.")
    return loaded[0]
