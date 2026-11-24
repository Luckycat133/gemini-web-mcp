"""Native Skill upload and one-shot RPC writes with account fences."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlsplit

from ..infrastructure.account_identity import current_account_key
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import _extract_parts, parse_contract_body, parse_rpc_envelope
from .deadlines import await_before_deadline


_SKILL_PUSH_UPLOAD = "https://push.clients6.google.com/upload/"
_SKILL_BARD_ERROR_REASONS: dict[int, str] = {
    1125: "Uploaded file is not a valid ZIP archive.",
    1126: "Skill folder contains too many files.",
    1127: "Skill folder exceeds the maximum allowed size.",
    1128: "Skill folder contains an unsupported file type.",
    1129: "Uploaded Skill archive failed integrity verification.",
    1130: "Uploaded Skill archive failed Gemini Web's safety scan (avoid prompt-injection-like directives in SKILL.md).",
    1131: "SKILL.md was not found at the archive root.",
    1132: "SKILL.md frontmatter delimiters ('---') are invalid.",
    1133: "SKILL.md frontmatter is missing the required 'name' field.",
    1134: "Skill name must be 1-64 lowercase alphanumeric/hyphen characters.",
    1135: "Skill description exceeds Gemini Web's maximum length.",
    1136: "Uploaded Skill archive handle was not found.",
    1138: "Skill was not found or was already deleted.",
    1139: "Skill version was not found.",
    1140: "Uploaded Skill archive was rejected as invalid.",
    1141: "Skill folder contains an unsupported file type.",
    1142: "Uploaded Skill archive was rejected as invalid.",
    1145: "Uploaded file is not a valid ZIP archive.",
    1158: "A Skill with this name already exists.",
    1194: "Gemini Web refused to save this Skill.",
    1195: "Maximum number of custom Skills reached (100).",
}


class SkillTransportUnavailable(RuntimeError):
    """A safe failure; a sent mutation must not be replayed."""

    def __init__(self, message: str, *, stage: str = "unknown") -> None:
        super().__init__(message)
        self.stage = stage


class SkillMutationRejected(SkillTransportUnavailable):
    """Gemini Web explicitly rejected the Skill mutation with a BardErrorInfo code."""

    def __init__(self, bard_code: int, message: str) -> None:
        super().__init__(message, stage="rejected")
        self.bard_code = bard_code


def _extract_skill_rejection(response_text: str, rpc_id: str) -> SkillMutationRejected | None:
    try:
        parts = _extract_parts(response_text)
    except Exception:
        return None
    for part in parts:
        if not isinstance(part, list) or len(part) < 6 or part[0] != "wrb.fr" or part[1] != rpc_id:
            continue
        meta = part[5]
        if not isinstance(meta, list) or len(meta) < 3 or not isinstance(meta[2], list):
            continue
        for entry in meta[2]:
            if (isinstance(entry, list) and len(entry) >= 2
                    and isinstance(entry[0], str) and entry[0].endswith("BardErrorInfo")
                    and isinstance(entry[1], list) and entry[1] and isinstance(entry[1][0], int)):
                code = entry[1][0]
                if code in _SKILL_BARD_ERROR_REASONS or 1120 <= code <= 1200:
                    detail = _SKILL_BARD_ERROR_REASONS.get(code)
                    upstream_msg = entry[1][2] if len(entry[1]) > 2 and isinstance(entry[1][2], str) else None
                    msg = f"{detail} (BardErrorInfo {code})" if detail else f"Skill rejected by Gemini Web (BardErrorInfo {code}: {upstream_msg or 'unknown'})"
                    return SkillMutationRejected(code, msg)
    return None


def _check(owner: str | None) -> None:
    if not owner or current_account_key() != owner:
        raise SkillTransportUnavailable("The Skill account is unavailable or changed.")


def skill_upload_configuration(html: str) -> tuple[str, dict[str, str]]:
    """Resolve the native editor's upload switches from current bootstrap data.

    Public core 3be5e6ce247ecf4e defines mIb=45785736 (default true),
    KIb=45785737 (default feeds/or3baakvdgsg4i), PBn=45853319 (false).
    TSDtV.flags provides runtime overrides; upload handles remain in memory.
    """
    match = re.search(r"WIZ_global_data\s*=\s*(\{.*?\});", html, re.S)
    try:
        bootstrap = json.loads(match.group(1)) if match else None
        if not isinstance(bootstrap, dict):
            raise ValueError
        encoded = bootstrap.get("TSDtV")
        config = json.loads(encoded) if isinstance(encoded, str) else encoded
        flags = config.get("flags") if isinstance(config, dict) else None
        if not isinstance(flags, dict):
            raise ValueError
        use_flag_id = flags.get("45785736", True)
        contribution = flags.get("45853319", False)
        if type(use_flag_id) is not bool or type(contribution) is not bool:
            raise ValueError
        push_id = flags.get("45785737", "feeds/or3baakvdgsg4i") if use_flag_id else bootstrap.get("KnDnFf")
        if (not isinstance(push_id, str) or not push_id.startswith("feeds/")
                or len(push_id) > 512 or any(ord(char) < 32 for char in push_id)):
            raise ValueError
        endpoint = "https://content-push.googleapis.com/upload/"
        if contribution:
            configured = bootstrap.get("h1eoVe", "")
            if configured == _SKILL_PUSH_UPLOAD:
                endpoint = _SKILL_PUSH_UPLOAD + "?upload_schema=agent_skills"
            elif not configured or (isinstance(configured, str)
                                    and configured.startswith("https://contribution.usercontent.google.com/")):
                endpoint = "https://contribution.usercontent.google.com/upload?upload_schema=agent_skills"
            else:
                raise ValueError
        headers = {"Push-ID": push_id, "X-Tenant-Id": "agent_skills", "Content-Type": "application/zip"}
        context = bootstrap.get("Ylro7b")
        if context:
            if not isinstance(context, str) or len(context) > 8192 or any(ord(c) < 32 for c in context):
                raise ValueError
            headers["X-Client-Pctx"] = context
        return endpoint, headers
    except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
        raise SkillTransportUnavailable("The current native Skill upload configuration was not verified.") from None


async def upload_skill(client: Any, archive_bytes: bytes, filename: str) -> str:
    owner = current_account_key()
    _check(owner)
    if (not isinstance(archive_bytes, bytes) or not archive_bytes.startswith(b"PK\x03\x04")
            or len(archive_bytes) > 2 * 1024 * 1024 or not re.fullmatch(r"[a-z0-9-]+\.zip", filename)):
        raise SkillTransportUnavailable("Invalid native Skill archive.")
    try:
        _check(owner)
        response = await await_before_deadline(client.client.get("https://gemini.google.com/app"), timeout=20)
    except Exception:
        raise SkillTransportUnavailable("Skill upload bootstrap was unavailable.", stage="bootstrap") from None
    _check(owner)
    if getattr(response, "status_code", None) != 200:
        raise SkillTransportUnavailable("The native Skill upload configuration was unavailable.")
    endpoint, headers = skill_upload_configuration(response.text)
    start_headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
    start_headers.update({"X-Goog-Upload-Header-Content-Length": str(len(archive_bytes)),
                          "X-Goog-Upload-Header-Content-Type": "application/zip",
                          "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start"})
    try:
        _check(owner)
        started = await await_before_deadline(client.client.post(
            endpoint, data=f"File name: {filename}", headers=start_headers, allow_redirects=False), timeout=30)
    except Exception:
        raise SkillTransportUnavailable("Skill upload start was unavailable.", stage="upload_start") from None
    _check(owner)
    upload_url = getattr(started, "headers", {}).get("X-Goog-Upload-URL")
    if getattr(started, "status_code", None) != 200 or not isinstance(upload_url, str):
        raise SkillTransportUnavailable("Skill upload start was not accepted.", stage="upload_start")
    try:
        parsed = urlsplit(upload_url)
        if (parsed.scheme != "https" or parsed.hostname not in {"content-push.googleapis.com",
                "contribution.usercontent.google.com", "push.clients6.google.com"}
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment or len(upload_url) > 8192
                or any(ord(char) < 32 or ord(char) == 127 for char in upload_url)):
            raise ValueError
    except ValueError:
        raise SkillTransportUnavailable("Skill upload destination was not accepted.", stage="upload_start") from None
    final_headers = dict(headers)
    final_headers.update({"X-Goog-Upload-Command": "upload, finalize", "X-Goog-Upload-Offset": "0"})
    try:
        _check(owner)
        uploaded = await await_before_deadline(client.client.post(
            upload_url, data=archive_bytes, headers=final_headers, allow_redirects=False), timeout=30)
    except Exception:
        raise SkillTransportUnavailable("Skill upload finalization was unavailable.", stage="upload_finalize") from None
    _check(owner)
    handle = getattr(uploaded, "text", None)
    if (getattr(uploaded, "status_code", None) != 200 or not isinstance(handle, str)
            or not handle.strip() or len(handle) > 65536):
        raise SkillTransportUnavailable("The native Skill upload outcome was unavailable.", stage="upload_finalize")
    return handle


async def mutate_skill(client: Any, action: str, *, skill_id: str | None,
                       upload_handle: str | None, enabled: bool | None) -> str | None:
    owner = current_account_key()
    _check(owner)
    key = {"create": "managed_skills.save", "update": "managed_skills.save",
           "enable": "managed_skills.enable", "delete": "managed_skills.delete"}.get(action)
    if key is None:
        raise SkillTransportUnavailable("Unknown Skill mutation.")
    args: dict[str, Any] = {"skill_id": skill_id}
    if action in {"create", "update"}:
        args.update(upload_handle=upload_handle, mode=3 if action == "create" else 2, enabled=enabled)
    elif action == "enable":
        args["enabled"] = enabled
    contract = get_contract(key)
    contract.build_payload(**args)
    _check(owner)
    response = await await_before_deadline(execute_contract(client, key, retry_count=0, **args), timeout=25)
    _check(owner)
    if getattr(response, "status_code", None) != 200:
        raise SkillTransportUnavailable("The Skill mutation response was not accepted.")
    raw_text = getattr(response, "text", "")
    envelope = parse_rpc_envelope(raw_text, contract.rpc_id)
    if envelope.reject_code is not None:
        if rejection := _extract_skill_rejection(raw_text, contract.rpc_id):
            raise rejection
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise SkillTransportUnavailable("The Skill mutation envelope was not accepted.")
    parsed = parse_contract_body(contract, envelope.bodies[0])
    if not parsed.ok:
        raise SkillTransportUnavailable("The Skill mutation response shape changed.")
    if action in {"create", "update"}:
        returned = parsed.value
        if not isinstance(returned, str) or action == "update" and returned != skill_id:
            raise SkillTransportUnavailable("The Skill mutation identity changed.")
        return returned
    return None
