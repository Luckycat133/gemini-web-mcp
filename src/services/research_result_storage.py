"""Account-bound full Research reports, stored outside the metadata database."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any
from urllib.parse import urlsplit

from ..domain.artifacts import Artifact, ArtifactKind, ArtifactState, ArtifactVerification, ArtifactVerificationStatus
from ..infrastructure.account_identity import operation_account_matches

RESEARCH_REPORT_PREVIEW_CHARS = 4000
MAX_RESEARCH_REPORT_BYTES = 50_000_000
RESEARCH_OPERATIONS = frozenset({"gemini_research", "gemini_deep_research"})


class ResearchResultUnavailable(RuntimeError):
    """A saved report cannot currently be independently verified."""


def _report_identity(record: Any, path: Path) -> str:
    identity = "\0".join((str(path), record.account_key or "", record.upstream_chat_id or "",
                            record.upstream_response_id or "", record.upstream_candidate_id or ""))
    return "artifact_" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def research_destination(store: Any, record: Any) -> Path:
    handle = record.operation_id
    if not isinstance(handle, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", handle):
        raise ResearchResultUnavailable("The report handle is invalid.")
    root = Path(store.path).resolve().parent
    directory = root / "artifacts" / handle
    for parent in (root / "artifacts", directory):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ResearchResultUnavailable("The report directory changed.")
    return directory / "research-report.md"


def _read_report_bytes(path: Path, *, expected_size: int | None = None) -> bytes:
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_size <= 0
            or before.st_size > MAX_RESEARCH_REPORT_BYTES
            or (expected_size is not None and before.st_size != expected_size)):
        raise ResearchResultUnavailable("The saved report is not a nonempty regular file.")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(MAX_RESEARCH_REPORT_BYTES + 1)
    after = path.lstat()
    if ((before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
            or not stat.S_ISREG(after.st_mode) or len(raw) != before.st_size):
        raise ResearchResultUnavailable("The saved report changed while reading.")
    if not raw.decode("utf-8").strip():
        raise ResearchResultUnavailable("The saved report body is empty.")
    return raw


def load_research_result(store: Any, record: Any, *, include_text: bool) -> tuple[Artifact, str | None] | None:
    """Rehash the original report without authentication or remote access."""
    if record.operation not in RESEARCH_OPERATIONS or not operation_account_matches(record):
        raise ResearchResultUnavailable("The report account could not be verified.")
    current = store.get(record.operation_id)
    identity_fields = ("operation", "account_key", "upstream_chat_id", "upstream_response_id", "upstream_candidate_id")
    if current is None or any(getattr(current, key) != getattr(record, key) for key in identity_fields):
        raise ResearchResultUnavailable("The report operation identity changed.")
    rows = store.get_media_artifacts(record.operation_id)
    if not rows:
        return None
    if len(rows) != 1 or rows[0]["kind"] != "report":
        raise ResearchResultUnavailable("The report manifest changed.")
    row = rows[0]
    path = research_destination(store, record)
    if row["local_path"] != str(path) or row["mime_type"] != "text/markdown":
        raise ResearchResultUnavailable("The report locator changed.")
    raw = _read_report_bytes(path, expected_size=row["size_bytes"])
    digest = hashlib.sha256(raw).hexdigest()
    identity = _report_identity(record, path)
    if digest != row["sha256"] or identity != row["artifact_id"]:
        raise ResearchResultUnavailable("The saved report bytes or identity changed.")
    # Recheck the directory and account after filesystem access, too.
    if research_destination(store, record) != path or not operation_account_matches(record):
        raise ResearchResultUnavailable("The report account or directory changed.")
    artifact = Artifact(id=identity, kind=ArtifactKind.REPORT, state=ArtifactState.LOCAL,
        title="Research report", mime_type="text/markdown", local_path=str(path), size_bytes=len(raw),
        source_chat_id=record.upstream_chat_id, requested_backend=record.requested_model,
        verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED,
            methods=("research_document_readback", "file_exists", "size_checked", "sha256_checked"),
            details={"sha256": digest}))
    return artifact, raw.decode("utf-8") if include_text else None


def report_markdown(document: Any) -> str:
    """Require an actual ready report body; a completed plan is insufficient."""
    body = getattr(document, "content", None)
    if getattr(document, "ready", False) is not True or not isinstance(body, str) or not body.strip():
        raise ResearchResultUnavailable("A completed nonempty research document is required.")
    markdown = getattr(document, "markdown", None)
    text = markdown if isinstance(markdown, str) and markdown.strip() else body
    links = []
    for source in getattr(document, "sources", ()) or ():
        url = getattr(source, "url", None)
        title = getattr(source, "title", None)
        if not isinstance(url, str) or url in text:
            continue
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                continue
        except ValueError:
            continue
        label = title if isinstance(title, str) and title.strip() else url
        links.append(f"- [{label}]({url})")
    return text + ("\n\n## Sources\n" + "\n".join(links) if links else "")


def save_research_result(store: Any, record: Any, document: Any) -> Artifact:
    """Atomically save the complete report and publish only its verified metadata."""
    if record.operation not in RESEARCH_OPERATIONS or not operation_account_matches(record):
        raise ResearchResultUnavailable("The report account could not be verified.")
    text = report_markdown(document)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_RESEARCH_REPORT_BYTES:
        raise ResearchResultUnavailable("The report exceeds the local storage limit.")
    previous = load_research_result(store, record, include_text=False)
    digest = hashlib.sha256(encoded).hexdigest()
    if previous is not None:
        if previous[0].verification.details["sha256"] != digest:
            raise ResearchResultUnavailable("The report differs from its original saved result.")
        return previous[0]
    path = research_destination(store, record)
    for directory in (path.parent.parent, path.parent):
        directory.mkdir(exist_ok=True, mode=0o700)
        research_destination(store, record)
        directory.chmod(0o700)
    research_destination(store, record)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".research-report-", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if hashlib.sha256(_read_report_bytes(path)).hexdigest() != digest:
                raise ResearchResultUnavailable("The report destination contains different bytes.") from None
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if not operation_account_matches(record):
            raise ResearchResultUnavailable("The report account changed before saving.")
        artifact = Artifact(id=_report_identity(record, path), kind=ArtifactKind.REPORT,
            state=ArtifactState.LOCAL, title="Research report", local_path=str(path), mime_type="text/markdown",
            size_bytes=len(encoded), source_chat_id=record.upstream_chat_id,
            verification=ArtifactVerification(ArtifactVerificationStatus.VERIFIED,
                methods=("research_document_readback", "sha256_checked"), details={"sha256": digest}))
        store.save_research_result_artifact(record, artifact)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    loaded = load_research_result(store, record, include_text=False)
    if loaded is None:
        raise ResearchResultUnavailable("The report metadata could not be read back.")
    return replace(loaded[0], requested_backend=record.requested_model)
