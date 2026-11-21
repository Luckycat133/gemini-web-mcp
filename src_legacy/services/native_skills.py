"""Gemini's native managed Skills, separate from Gems and local prompts."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
import zipfile

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState
from ..infrastructure.rpc_contracts import (
    NATIVE_SKILL_PUSH_ID, NATIVE_SKILL_UPLOAD_URL, execute_contract,
    native_skill_name_valid, native_skill_upload_headers, native_skill_upload_url_allowed,
)
from .account import account_contract_result


def _invalid(message: str) -> DomainResult[dict[str, Any]]:
    return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, message)


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item[k] for k in ("id", "name", "description", "enabled", "drive_id", "version_id", "permissions")}


async def list_native_skills(client: Any) -> DomainResult[dict[str, Any]]:
    response = await execute_contract(client, "native_skills.list")
    result = account_contract_result(response, "native_skills.list")
    if not result.ok:
        return result
    return DomainResult.success({"items": [_summary(item) for item in (result.data or {}).get("value", [])]},
                                verification_status="observed", details=result.meta.details)


async def get_native_skill(client: Any, skill_id: str) -> DomainResult[dict[str, Any]]:
    if not isinstance(skill_id, str) or not skill_id.strip():
        return _invalid("skill_id is required.")
    response = await execute_contract(client, "native_skills.get", skill_id=skill_id)
    result = account_contract_result(response, "native_skills.get", expected_id=skill_id)
    if not result.ok:
        return result
    return DomainResult.success({"item": (result.data or {}).get("value")},
                                verification_status="observed", details=result.meta.details)


def _mutation(
    data: dict[str, Any], ack: DomainResult[dict[str, Any]], verified: bool, verification: dict[str, Any],
    *, status: str = "read_back_not_observed",
) -> DomainResult[dict[str, Any]]:
    return DomainResult.success(
        {**data, "accepted": True, "verified": verified},
        operation_state=OperationState.COMPLETED if verified else OperationState.ACCEPTED,
        verification_status="verified" if verified else status,
        warnings=() if verified else (DomainWarning(
            "MUTATION_UNVERIFIED", "Native Skill mutation was accepted but independent read-back is unverified.",
            "Read the same Skill ID again; do not repeat creation.",
        ),),
        details={"mutation": dict(ack.meta.details), "verification": verification},
    )


def _markdown(name: str, description: str, instructions: str, previous: str = "") -> str:
    # JSON quoted scalars are also valid YAML. Keep unrelated frontmatter keys.
    extra: list[str] = []
    previous = previous.replace("\r\n", "\n")
    if previous.startswith("---\n"):
        closing = re.search(r"(?m)^---\s*$", previous[4:])
        if closing is None:
            raise ValueError("Existing SKILL.md frontmatter has no closing delimiter.")
        end = 4 + closing.start()
        skipping = False
        for line in previous[4:end].splitlines():
            if re.match(r"^(name|description)\s*:", line):
                skipping = True
                continue
            if line and not line[0].isspace() and not line.startswith("#"):
                skipping = False
            if not skipping:
                extra.append(line)
    fields = [f"name: {name}",
              f"description: {json.dumps(description, ensure_ascii=False)}", *extra]
    return "---\n" + "\n".join(fields) + "\n---\n" + instructions


def build_native_skill_archive(
    *, name: str, description: str, instructions: str, entries: list[dict[str, Any]] | None = None,
    previous_markdown: str = "",
) -> tuple[bytes, list[list[Any]]]:
    """Build the UI's ZIP + external-resource pair without executing Skill files."""
    if not isinstance(name, str) or not native_skill_name_valid(name):
        raise ValueError("Native Skill name must use 1-64 lowercase letters, numbers or combining marks, separated by single hyphens.")
    if (not isinstance(description, str) or not description.strip()
            or not isinstance(instructions, str) or not instructions.strip()):
        raise ValueError("Provide nonempty Skill description and instructions.")
    entries = entries or []
    skill_paths = [entry["path"] for entry in entries
                   if entry["path"] == "SKILL.md" or entry["path"].endswith("/SKILL.md")]
    if len(skill_paths) > 1:
        raise ValueError("Multiple SKILL.md files require an explicit ZIP replacement.")
    skill_path = skill_paths[0] if skill_paths else "SKILL.md"
    previous = next((entry.get("content", "") for entry in entries if entry["path"] == skill_path), previous_markdown)
    markdown = _markdown(name, description, instructions, previous)
    stream = io.BytesIO()
    external: list[list[Any]] = []
    seen: set[str] = set()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            path = entry["path"]
            if (not path or "\\" in path or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts
                    or path in seen):
                raise ValueError("Skill bundle contains an unsafe or duplicate path.")
            seen.add(path)
            if path == skill_path:
                continue
            reference = entry.get("external_resource")
            if reference:
                external.append([path, entry.get("mime_type", ""), reference])
            elif entry.get("bytes_base64"):
                archive.writestr(path, base64.b64decode(entry["bytes_base64"], validate=True))
            else:
                archive.writestr(path, entry.get("content", ""))
        archive.writestr(skill_path, markdown)
    return stream.getvalue(), external


def _header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {})
    return next((str(value) for key, value in headers.items() if str(key).lower() == name.lower()), "")


async def upload_native_skill_archive(
    client: Any, content: bytes, *, filename: str = "skill.zip", upload_url: str = NATIVE_SKILL_UPLOAD_URL,
    push_id: str = NATIVE_SKILL_PUSH_ID, client_pctx: str = "",
) -> DomainResult[dict[str, Any]]:
    """Mirror main:4353-4361/Y4e resumable upload using the shared session.

    Public feature defaults are explicit arguments so an observed account
    configuration can replace them without changing the ordinary SDK uploader.
    """
    if not native_skill_upload_url_allowed(upload_url) or not push_id or not content or not filename.lower().endswith(".zip"):
        return _invalid("A native Skill requires a ZIP and an observed HTTPS upload configuration.")
    session = getattr(client, "client", None)
    if session is None or not hasattr(session, "post"):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Client has no shared upload session.")
    start = await session.post(
        url=upload_url, data=f"File name: {filename}",
        headers=native_skill_upload_headers(phase="start", content_size=len(content), push_id=push_id,
                                            client_pctx=client_pctx), allow_redirects=False,
    )
    status = _header(start, "X-Goog-Upload-Status")
    diagnostic = {"stage": "native_skill_upload_start", "status_code": getattr(start, "status_code", None),
                  "upload_status": status, "configuration": "public_frontend_defaults"}
    if getattr(start, "status_code", None) != 200 or status != "active":
        return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Native Skill upload did not start.", details=diagnostic)
    target = _header(start, "X-Goog-Upload-URL")
    if not native_skill_upload_url_allowed(target):
        return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Native Skill upload session URL changed.", details=diagnostic)
    finish = await session.post(
        url=target, data=content,
        headers=native_skill_upload_headers(phase="finalize", content_size=len(content), push_id=push_id,
                                            client_pctx=client_pctx),
        allow_redirects=False,
    )
    reference = str(getattr(finish, "text", "") or "").strip()
    diagnostic.update(stage="native_skill_upload_finalize", status_code=getattr(finish, "status_code", None),
                      upload_status=_header(finish, "X-Goog-Upload-Status"))
    if (getattr(finish, "status_code", None) != 200 or diagnostic["upload_status"] != "final"
            or not reference or reference.startswith(("<", "{", "[")) or any(c.isspace() for c in reference)):
        return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Native Skill upload was not finalized.", details=diagnostic)
    return DomainResult.success({"upload_reference": reference}, verification_status="uploaded", details=diagnostic)


async def _save(
    client: Any, *, name: str, description: str, instructions: str, skill_id: str = "", enabled: bool = True,
    entries: list[dict[str, Any]] | None = None, previous_markdown: str = "",
) -> DomainResult[dict[str, Any]]:
    try:
        content, external = build_native_skill_archive(name=name, description=description, instructions=instructions,
                                                       entries=entries, previous_markdown=previous_markdown)
    except (ValueError, TypeError) as exc:
        return _invalid(str(exc))
    uploaded = await upload_native_skill_archive(client, content, filename=f"{name}.zip")
    if not uploaded.ok:
        return uploaded
    response = await execute_contract(
        client, "native_skills.save", upload_reference=(uploaded.data or {})["upload_reference"],
        skill_id=skill_id, mode=2 if skill_id else 3, enabled=enabled, external_resources=external,
    )
    ack = account_contract_result(response, "native_skills.save", expected_id=skill_id)
    if not ack.ok:
        return ack
    saved = (ack.data or {}).get("value") or {}
    target_id = saved.get("id", "") or skill_id
    data: dict[str, Any] = {"id": target_id, "item": None}
    if not target_id:
        return _mutation(data, ack, False, {}, status="created_id_not_observed")
    try:
        read = await get_native_skill(client, target_id)
        item = (read.data or {}).get("item")
        data["item"] = _summary(item) if item else None
        verified = bool(read.ok and item and all(item.get(k) == v for k, v in {
            "name": name, "description": description, "instructions": instructions, "enabled": enabled,
        }.items()))
        if verified and entries:
            expected_resources = {entry["path"]: entry for entry in entries
                                  if entry["path"] != "SKILL.md" and not entry["path"].endswith("/SKILL.md")}
            actual_resources = {entry["path"]: entry for entry in item["entries"]} if item else {}
            verified = all(actual_resources.get(path) == entry for path, entry in expected_resources.items())
        return _mutation(data, ack, verified, dict(read.meta.details))
    except Exception as exc:
        return _mutation(data, ack, False, {"error_type": type(exc).__name__}, status="read_back_error")


async def create_native_skill(
    client: Any, *, name: str, description: str, instructions: str,
) -> DomainResult[dict[str, Any]]:
    return await _save(client, name=name, description=description, instructions=instructions)


def _canonical_archive_files(files: dict[str, bytes]) -> dict[str, bytes]:
    # Match the UI's y1b common-root removal and x1b desktop-file exclusions.
    files = {path: content for path, content in files.items()
             if path.rsplit("/", 1)[-1].lower() not in {".ds_store", "thumbs.db", "desktop.ini"}
             and not path.rsplit("/", 1)[-1].startswith("._")
             and "__macosx" not in path.lower().split("/")}
    first = next(iter(files), "")
    prefix = first.split("/", 1)[0] + "/" if "/" in first else ""
    if prefix and all(path.startswith(prefix) for path in files):
        return {path[len(prefix):]: content for path, content in files.items()}
    return files


async def import_native_skill_archive(
    client: Any, file_path: str, *, skill_id: str = "",
) -> DomainResult[dict[str, Any]]:
    """Import/replace a ZIP through the current UI r5e/GSa workflow."""
    if not isinstance(file_path, str) or not file_path.strip() or not isinstance(skill_id, str):
        return _invalid("Provide a native Skill ZIP file_path and optional replacement skill_id.")
    path = Path(file_path).expanduser()
    if not path.is_file() or path.suffix.lower() != ".zip":
        return _invalid("file_path must name an existing native Skill ZIP.")
    try:
        content = path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files: dict[str, bytes] = {}
            for entry in archive.infolist():
                name = entry.filename
                if entry.is_dir():
                    continue
                if ("\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
                        or name in files):
                    return _invalid("Skill ZIP contains an unsafe or duplicate file path.")
                files[name] = archive.read(entry)
        files = _canonical_archive_files(files)
        if "SKILL.md" not in files or not files["SKILL.md"].strip():
            return _invalid("Skill ZIP must contain SKILL.md at its root or one common root folder.")
        files["SKILL.md"].decode("utf-8")
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return _invalid("Native Skill ZIP cannot be read as a valid UTF-8 Skill bundle.")
    enabled = True
    if skill_id:
        before = await get_native_skill(client, skill_id)
        if not before.ok:
            return before
        current = (before.data or {}).get("item")
        if not current:
            return _invalid("Replacement Skill was not found; no ZIP was uploaded.")
        if current["permissions"] is not None and current["permissions"]["can_edit"] is False:
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "This Skill does not grant editing permission.")
        enabled = current["enabled"]
    uploaded = await upload_native_skill_archive(client, content, filename=path.name)
    if not uploaded.ok:
        return uploaded
    response = await execute_contract(client, "native_skills.save", skill_id=skill_id, mode=2 if skill_id else 3,
                                      enabled=enabled, upload_reference=(uploaded.data or {})["upload_reference"])
    ack = account_contract_result(response, "native_skills.save", expected_id=skill_id)
    if not ack.ok:
        return ack
    target_id = ((ack.data or {}).get("value") or {}).get("id", "") or skill_id
    data: dict[str, Any] = {"id": target_id, "item": None}
    if not target_id:
        return _mutation(data, ack, False, {}, status="created_id_not_observed")
    try:
        read = await get_native_skill(client, target_id)
        item = (read.data or {}).get("item")
        data["item"] = _summary(item) if item else None
        observed: dict[str, bytes] = {}
        if item:
            for entry in item["entries"]:
                observed[entry["path"]] = (base64.b64decode(entry["bytes_base64"], validate=True)
                                           if entry["bytes_base64"] else entry["content"].encode("utf-8"))
            if not observed and item["skill_markdown"]:
                observed["SKILL.md"] = item["skill_markdown"].encode("utf-8")
        observed = _canonical_archive_files(observed)
        verified = bool(read.ok and item and item["enabled"] is enabled
                        and observed == files)
        return _mutation(data, ack, verified, dict(read.meta.details))
    except Exception as exc:
        return _mutation(data, ack, False, {"error_type": type(exc).__name__}, status="read_back_error")


async def update_native_skill(
    client: Any, skill_id: str, *, name: str | None = None, description: str | None = None,
    instructions: str | None = None,
) -> DomainResult[dict[str, Any]]:
    changes = {k: v for k, v in {"name": name, "description": description, "instructions": instructions}.items()
               if v is not None}
    if (not changes or any(not isinstance(value, str) for value in changes.values())
            or (name is not None and not name.strip()) or (instructions is not None and not instructions.strip())):
        return _invalid("Provide at least one string field; name and instructions cannot be blank.")
    read = await get_native_skill(client, skill_id)
    if not read.ok:
        return read
    item = (read.data or {}).get("item")
    if not item:
        return _invalid("Native Skill was not found; no mutation was sent.")
    if item["permissions"] is not None and item["permissions"]["can_edit"] is False:
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE,
                                    "This native Skill does not grant editing permission.")
    fields = {k: changes.get(k, item[k]) for k in ("name", "description", "instructions")}
    return await _save(client, **fields, skill_id=skill_id, enabled=item["enabled"],
                       entries=item["entries"], previous_markdown=item["skill_markdown"])


async def set_native_skill_enabled(client: Any, skill_id: str, enabled: bool) -> DomainResult[dict[str, Any]]:
    if type(enabled) is not bool:
        return _invalid("enabled must be a boolean.")
    read = await get_native_skill(client, skill_id)
    if not read.ok:
        return read
    item = (read.data or {}).get("item")
    if not item:
        return _invalid("Native Skill was not found; no mutation was sent.")
    key = "native_skills.set_license_enabled" if item["drive_id"] else "native_skills.save"
    arguments: dict[str, Any] = {"skill_id": skill_id, "enabled": enabled}
    if not item["drive_id"]:
        arguments.update(upload_reference="", mode=2)
    response = await execute_contract(client, key, **arguments)
    ack = account_contract_result(response, key, expected_id=skill_id)
    if not ack.ok:
        return ack
    data: dict[str, Any] = {"id": skill_id, "enabled": enabled}
    try:
        after = await get_native_skill(client, skill_id)
        observed = (after.data or {}).get("item")
        verified = bool(after.ok and observed and observed["enabled"] is enabled)
        return _mutation(data, ack, verified, dict(after.meta.details))
    except Exception as exc:
        return _mutation(data, ack, False, {"error_type": type(exc).__name__}, status="read_back_error")


async def delete_native_skill(client: Any, skill_id: str) -> DomainResult[dict[str, Any]]:
    before = await get_native_skill(client, skill_id)
    if not before.ok:
        return before
    item = (before.data or {}).get("item")
    if not item:
        return _invalid("Native Skill was not found; no mutation was sent.")
    remove_license = item["permissions"] is not None and item["permissions"]["can_delete"] is False
    key = "native_skills.remove_license" if remove_license else "native_skills.delete"
    response = await execute_contract(client, key, skill_id=skill_id)
    ack = account_contract_result(response, key)
    if not ack.ok:
        return ack
    data = {"id": skill_id, "removed_license": remove_license}
    try:
        read = await list_native_skills(client)
        verified = bool(read.ok and all(s["id"] != skill_id for s in (read.data or {}).get("items", [])))
        return _mutation(data, ack, verified, dict(read.meta.details))
    except Exception as exc:
        return _mutation(data, ack, False, {"error_type": type(exc).__name__}, status="read_back_error")
