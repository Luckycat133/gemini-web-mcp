"""Shared media artifact storage and format verification."""

from __future__ import annotations

import logging
import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Optional

from ..constants import OMNI_VIDEO_MODEL
from ..domain import Artifact, ArtifactKind, ArtifactState
from .artifacts import (artifact_from_local_path, classify_artifact_state, detect_av_mime_type,
                        detect_image_mime_type, probe_playable_av_tracks, response_image_items)

logger = logging.getLogger(__name__)
OMNI_ARTIFACT_MODEL_VERIFIED = "omni_model_and_artifact_verified"

if TYPE_CHECKING:
    from ..infrastructure.operation_store import OperationRecord, OperationStore


class _ImageVerificationError(ValueError):
    pass


class ImageDecoderUnavailable(_ImageVerificationError):
    pass


def require_image_decoder() -> ModuleType:
    """Fail before generation when the optional decoder is unavailable."""
    try:
        from PIL import Image
    except ImportError:
        raise ImageDecoderUnavailable("image_decoder_unavailable") from None
    return Image


def _decode_generated_image(path: str) -> tuple[int, int]:
    mime = detect_image_mime_type(path)
    if mime is None:
        raise _ImageVerificationError("image_signature_unrecognized")
    decoder = require_image_decoder()
    try:
        with decoder.open(path) as image:
            if decoder.MIME.get(image.format) != mime:
                raise _ImageVerificationError("image_signature_mismatch")
            image.verify()
        # verify() validates the container without decoding its pixels.
        with decoder.open(path) as image:
            image.load()
            return int(image.width), int(image.height)
    except _ImageVerificationError:
        raise
    except (OSError, ValueError, SyntaxError, decoder.DecompressionBombError):
        raise _ImageVerificationError("image_decode_failed") from None


def _mark_image_decoded(artifact: Artifact) -> Artifact:
    return replace(artifact, verification=replace(
        artifact.verification, methods=(*artifact.verification.methods, "image_pixels_decoded"),
    ))


def read_verified_media_artifacts(
    store: OperationStore, record: OperationRecord, *, include_duration: bool = False,
) -> tuple[Artifact, ...]:
    """Read only the saved artifact identities whose local bytes still match."""
    artifacts = []
    for item in store.get_media_artifacts(record.operation_id):
        path = Path(item["local_path"])
        if not path.is_file() or path.stat().st_size != item["size_bytes"]:
            raise ValueError("saved artifact changed")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                raise ValueError("saved artifact changed")
        kind = ArtifactKind(item["kind"])
        generated_image = record.media_type == "image" and kind == ArtifactKind.IMAGE
        artifact = artifact_from_local_path(
            kind, str(path), source_chat_id=record.upstream_chat_id,
            requested_backend=record.requested_model, request_model=record.request_model,
            effective_backend=record.effective_backend,
            observed_backend=(OMNI_VIDEO_MODEL if record.verification_status == OMNI_ARTIFACT_MODEL_VERIFIED else None),
            duration_probe=_probe_duration if include_duration else None,
            dimensions_probe=_decode_generated_image if generated_image else None,
        )
        if artifact.state != ArtifactState.LOCAL:
            raise ValueError("saved artifact did not verify")
        if generated_image:
            artifact = _mark_image_decoded(artifact)
        artifacts.append(replace(artifact, id=item["artifact_id"]))
    return tuple(artifacts)


def reconcile_remote_media_types(remote: tuple[Artifact, ...], local: tuple[Artifact, ...]) -> tuple[Artifact, ...]:
    """Prefer verified local content over a contradictory URL-derived media type."""
    known: dict[str, set[ArtifactKind]] = {}
    for artifact in local:
        if artifact.uri and artifact.state == ArtifactState.LOCAL:
            known.setdefault(artifact.uri, set()).add(artifact.kind)
    return tuple(item for item in remote if not item.uri or item.uri not in known or item.kind in known[item.uri])


def classify_generated_media(
    response: object, artifacts: tuple[Artifact, ...], media_type: str, failures: tuple[str, ...],
) -> ArtifactState:
    """A preview must not prove that the requested media was generated."""
    allowed = {
        "image": {ArtifactKind.IMAGE}, "image_edit": {ArtifactKind.IMAGE},
        "video": {ArtifactKind.VIDEO}, "music": {ArtifactKind.AUDIO, ArtifactKind.VIDEO},
    }.get(media_type, set())
    primary = tuple(item for item in artifacts if item.kind in allowed)
    state = classify_artifact_state(response, primary)
    if state != ArtifactState.QUEUED and failures and (
        state == ArtifactState.EMPTY or (media_type == "video" and not any(a.state == ArtifactState.LOCAL for a in primary))
    ):
        return ArtifactState.FAILED
    return state


def _safe_media_filename(prompt: str, media_type: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", prompt.strip())[:48].strip("._-")
    return stem or media_type


def _valid_media_filename(filename: str | None) -> bool:
    """A caller may name a file, but cannot escape the requested directory."""
    if filename is None:
        return True
    return bool(
        filename.strip()
        and filename not in {".", ".."}
        and Path(filename).name == filename
        and not any(character in filename for character in ("/", "\\", "\x00"))
        and not any(ord(character) < 32 for character in filename)
    )


def _unused_media_filename(destination: Path, filename: str) -> str:
    """Avoid overwriting an existing named artifact on repeated requests."""
    candidate = Path(filename)
    if not (destination / candidate).exists():
        return filename
    suffix = candidate.suffix
    stem = candidate.stem if suffix else candidate.name
    index = 2
    while (destination / f"{stem}_{index}{suffix}").exists():
        index += 1
    return f"{stem}_{index}{suffix}"


def _reserve_music_filename(destination: Path, filename: str) -> str:
    """Reserve a destination atomically so concurrent calls cannot overwrite it."""
    destination.mkdir(parents=True, exist_ok=True)
    while True:
        candidate = _unused_media_filename(destination, filename)
        try:
            fd = os.open(destination / candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return candidate


def _rename_music_suffix(path: str, suffix: str) -> str:
    """Change a validated container's suffix without replacing any file."""
    source = Path(path)
    if source.suffix.lower() == suffix:
        return path
    while True:
        candidate = source.with_name(_unused_media_filename(source.parent, source.stem + suffix))
        try:
            os.link(source, candidate)
        except FileExistsError:
            continue
        try:
            source.unlink()
        except BaseException:
            try:
                candidate.unlink()
            except OSError:
                logger.warning("Could not roll back media suffix link")
            raise
        return str(candidate)


def _discard_unreported_files(owned: dict[Path, tuple[int, int]], verified: set[Path]) -> None:
    """Remove only files reserved by this save and not returned as local artifacts."""
    for path, identity in owned.items():
        if path in verified:
            continue
        try:
            stat = path.lstat()
            if (stat.st_dev, stat.st_ino) == identity:
                path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            logger.warning("Could not remove unverified media file error_type=%s", type(error).__name__)


def _follow_owned_move(owned: dict[Path, tuple[int, int]], previous: Path, current: Path) -> None:
    if previous != current and previous in owned:
        owned[current] = owned.pop(previous)


def _normalize_saved_image_extension(path: str) -> str:
    """Keep a saved image's filename consistent with its actual bytes."""
    source = Path(path)
    mime_type = detect_image_mime_type(source)
    suffixes = {
        "image/jpeg": (".jpg", ".jpeg"),
        "image/png": (".png",),
        "image/gif": (".gif",),
        "image/webp": (".webp",),
    }.get(mime_type or "")
    if suffixes is None or source.suffix.lower() in suffixes:
        return path
    try:
        return _rename_music_suffix(path, suffixes[0])
    except OSError as error:
        logger.warning("Could not align saved image extension error_type=%s", type(error).__name__)
        return path


def _probe_duration(path: str) -> Optional[float]:
    if not shutil.which("ffprobe"):
        return None
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


async def _save_generated_media(
    response,
    *,
    media_type: str,
    output_dir: Optional[str],
    filename: Optional[str],
    prompt: str,
    media_items: Optional[list] = None,
    requested_model: str,
    request_model: str,
    effective_backend: str,
    observed_backend: str | None,
    source_chat_id: str | None,
    duration_probe = None,
    track_probe = None,
) -> "_MediaSaveOutcome":
    duration_probe = duration_probe or _probe_duration
    track_probe = track_probe or probe_playable_av_tracks
    media_items = media_items if media_items is not None else _response_media_items(response, media_type)
    if not media_items:
        return _MediaSaveOutcome()

    destination = Path(output_dir or "generated_media").expanduser()
    saved_lines: list[str] = []
    saved_artifacts: list[Artifact] = []
    failures: list[str] = []
    for index, media in enumerate(media_items, 1):
        base_name = filename or _safe_media_filename(prompt, media_type)
        if len(media_items) > 1:
            name_path = Path(base_name)
            base_name = (
                f"{name_path.stem}_{index}{name_path.suffix}"
                if name_path.suffix
                else f"{base_name}_{index}"
            )
        requests: list[tuple[str | None, str]] = []
        if media_type == "music":
            # The upstream saver downloads both streams concurrently using the
            # same supplied filename. A filename with a suffix makes them
            # overwrite each other, so download each stream separately.
            stem = Path(base_name).stem if Path(base_name).suffix else base_name
            if getattr(media, "mp3_url", None):
                requests.append(("audio", f"{stem}.mp3"))
            if getattr(media, "url", None):
                requests.append(("video", f"{stem}.mp4"))
        else:
            # The SDK writes the supplied destination unconditionally; a
            # suffix also prevents it from generating an unreserved filename.
            if not Path(base_name).suffix:
                base_name += ".png" if media_type == "image" else ".mp4"
            requests = [(None, base_name)]
        if not requests:
            failures.append(f"item_{index}:no_media_url")
        for expected_kind, save_name in requests:
            try:
                save_name = _reserve_music_filename(destination, save_name)
            except OSError as error:
                failures.append(f"{type(error).__name__}:reserve")
                continue
            reserved = (destination / save_name).absolute()
            reserved_stat = reserved.lstat()
            owned_paths = {reserved: (reserved_stat.st_dev, reserved_stat.st_ino)}
            save_kwargs = {"path": str(destination), "filename": save_name, "verbose": False}
            if expected_kind is not None:
                save_kwargs["download_type"] = expected_kind
            completed_save = False
            try:
                raw_saved = await media.save(**save_kwargs)
                completed_save = True
            except Exception as error:
                logger.error(
                    "artifact save failed media_type=%s index=%s error_type=%s",
                    media_type,
                    index,
                    type(error).__name__,
                )
                failures.append(f"{type(error).__name__}:save")
                continue
            finally:
                try:
                    if not completed_save or reserved.lstat().st_size == 0:
                        _discard_unreported_files(owned_paths, set())
                except OSError:
                    pass
            saved = _normalize_saved_paths(raw_saved, media_type)
            verified_paths: set[Path] = set()
            if saved is None:
                failures.append(f"item_{index}:unsupported_save_result")
                _discard_unreported_files(owned_paths, verified_paths)
                continue
            if expected_kind is not None:
                # Preserve thumbnails, but never count them as the main stream.
                thumbnails = {key: value for key, value in saved.items() if "thumb" in key.lower()}
                saved = {expected_kind: saved.get(expected_kind), **thumbnails}
            elif media_type == "video" and "video" not in saved:
                failures.append("video:no_saved_path")
            for kind, path in sorted(saved.items()):
                if not path:
                    if "thumb" in kind.lower():
                        continue
                    failures.append(f"{kind}:no_saved_path")
                    continue
                artifact_kind = _saved_artifact_kind(media_type, kind)
                if media_type == "video" and artifact_kind == ArtifactKind.VIDEO:
                    if detect_image_mime_type(path):
                        # Some upstream responses return only a preview under the video key.
                        failures.append(f"{kind}:content_signature")
                        artifact_kind = ArtifactKind.IMAGE
                if media_type == "video" and artifact_kind == ArtifactKind.VIDEO:
                    container = detect_av_mime_type(path)
                    if container == "video/mp4":
                        try:
                            previous_path = Path(path).absolute()
                            path = _rename_music_suffix(path, ".mp4")
                            _follow_owned_move(owned_paths, previous_path, Path(path).absolute())
                        except OSError:
                            failures.append(f"{kind}:extension")
                            continue
                    tracks = track_probe(path) if container == "video/mp4" else None
                    if tracks is None or "video" not in tracks:
                        failures.append(f"{kind}:content_signature")
                        continue
                if media_type == "music" and expected_kind is not None and kind == expected_kind:
                    container = detect_av_mime_type(path)
                    # AVFoundation chooses parsers partly from the extension;
                    # normalize the container before probing its tracks.
                    provisional_suffix = ".mp4" if container == "video/mp4" else ".mp3"
                    if container:
                        try:
                            previous_path = Path(path).absolute()
                            path = _rename_music_suffix(path, provisional_suffix)
                            _follow_owned_move(owned_paths, previous_path, Path(path).absolute())
                        except OSError:
                            failures.append(f"{kind}:extension")
                            continue
                    tracks = track_probe(path) if container else None
                    if not tracks:
                        failures.append(f"{kind}:content_signature")
                        continue
                    if expected_kind not in tracks:
                        failures.append(f"{kind}:track_missing")
                    if container == "audio/mpeg" and "audio" in tracks:
                        mime_type, suffix, artifact_kind = "audio/mpeg", ".mp3", ArtifactKind.AUDIO
                    elif container == "video/mp4" and "video" in tracks:
                        mime_type, suffix, artifact_kind = "video/mp4", ".mp4", ArtifactKind.VIDEO
                    elif container == "video/mp4" and "audio" in tracks:
                        mime_type, suffix, artifact_kind = "audio/mp4", ".m4a", ArtifactKind.AUDIO
                    else:
                        failures.append(f"{kind}:content_signature")
                        continue
                    try:
                        previous_path = Path(path).absolute()
                        path = _rename_music_suffix(path, suffix)
                        _follow_owned_move(owned_paths, previous_path, Path(path).absolute())
                    except OSError:
                        failures.append(f"{kind}:extension")
                        continue
                if artifact_kind == ArtifactKind.IMAGE:
                    previous_path = Path(path).absolute()
                    path = _normalize_saved_image_extension(path)
                    _follow_owned_move(owned_paths, previous_path, Path(path).absolute())
                if "thumbnail" in kind.lower() or "thumb" in kind.lower() or (
                    media_type == "video" and artifact_kind == ArtifactKind.IMAGE
                ):
                    uri = getattr(media, "mp3_thumbnail", None) if "audio" in kind.lower() else getattr(media, "thumbnail", None)
                else:
                    uri = (getattr(media, "mp3_url", None) if expected_kind == "audio" else
                           getattr(media, "url", None)) if expected_kind is not None else _saved_artifact_uri(media, artifact_kind)
                generated_image = media_type == "image" and artifact_kind == ArtifactKind.IMAGE
                try:
                    artifact = artifact_from_local_path(
                        artifact_kind,
                        path,
                        title=getattr(media, "title", None),
                        uri=uri,
                        source_chat_id=source_chat_id,
                        requested_backend=requested_model,
                        request_model=request_model,
                        effective_backend=effective_backend,
                        observed_backend=observed_backend,
                        duration_probe=duration_probe,
                        dimensions_probe=_decode_generated_image if generated_image else None,
                    )
                except _ImageVerificationError as error:
                    failures.append(f"{kind}:{error}")
                    continue
                if generated_image and artifact.state == ArtifactState.LOCAL:
                    artifact = _mark_image_decoded(artifact)
                if media_type == "music" and expected_kind is not None and kind == expected_kind and artifact.state == ArtifactState.LOCAL:
                    artifact = replace(artifact, mime_type=mime_type, verification=replace(
                        artifact.verification,
                        methods=(*artifact.verification.methods, "media_content_signature", "media_track_probe")
                    ))
                if media_type == "video" and artifact_kind == ArtifactKind.VIDEO and artifact.state == ArtifactState.LOCAL:
                    artifact = replace(artifact, mime_type="video/mp4", verification=replace(
                        artifact.verification, methods=(*artifact.verification.methods, "media_content_signature", "media_track_probe")
                    ))
                saved_artifacts.append(artifact)
                if artifact.state == ArtifactState.FAILED:
                    failures.append(f"{kind}:verification")
                else:
                    if artifact.state == ArtifactState.LOCAL:
                        verified_paths.add(Path(path).absolute())
                    line = f"{artifact_kind.value}: {path}"
                    duration = artifact.duration_seconds
                    if duration is not None:
                        line += f" ({duration:.2f}s)"
                    saved_lines.append(line)
            if not saved:
                failures.append(f"item_{index}:no_saved_path")
            _discard_unreported_files(owned_paths, verified_paths)
    return _MediaSaveOutcome(
        lines=tuple(saved_lines),
        artifacts=tuple(saved_artifacts),
        failures=tuple(failures),
    )


def _response_media_items(response, media_type: str) -> list:
    if media_type == "image":
        images = response_image_items(response, generated_only=True)
        if getattr(response, "candidates", None) is None:
            return images or list(getattr(response, "media", None) or [])
        return images
    if media_type == "video":
        return list(getattr(response, "videos", None) or [])
    return list(getattr(response, "media", None) or [])


def _normalize_saved_paths(saved, media_type: str) -> dict[str, str | None] | None:
    if isinstance(saved, (str, Path)):
        return {media_type: str(saved)}
    if isinstance(saved, Mapping):
        normalized: dict[str, str | None] = {}
        for kind, path in saved.items():
            if path is None:
                normalized[str(kind)] = None
            elif isinstance(path, (str, Path)):
                normalized[str(kind)] = str(path)
            else:
                return None
        return normalized
    return None


@dataclass(frozen=True)
class _MediaSaveOutcome:
    lines: tuple[str, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    failures: tuple[str, ...] = ()


def _saved_artifact_kind(media_type: str, saved_kind: str) -> ArtifactKind:
    normalized = saved_kind.lower()
    if "thumbnail" in normalized or "thumb" in normalized:
        return ArtifactKind.IMAGE
    if normalized in {"video", "mp4", "webm"}:
        return ArtifactKind.VIDEO
    if normalized in {"audio", "mp3", "music", "wav", "m4a"}:
        return ArtifactKind.AUDIO
    if normalized in {"image", "png", "jpg", "jpeg", "webp"}:
        return ArtifactKind.IMAGE
    if media_type == "music":
        return ArtifactKind.AUDIO
    if media_type == "image":
        return ArtifactKind.IMAGE
    return ArtifactKind.VIDEO


def _saved_artifact_uri(media, kind: ArtifactKind) -> str | None:
    if kind == ArtifactKind.AUDIO:
        return getattr(media, "mp3_url", None) or getattr(media, "url", None)
    return getattr(media, "url", None)
