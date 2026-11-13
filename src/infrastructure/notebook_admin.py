"""Evidence-backed native Notebook project request builders and parsers."""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from .rpc_parsers import RPCParseResult


NOTEBOOK_FILE_MAX_BYTES = 1024 * 1024
NOTEBOOK_FILE_MIME = "text/plain"
_UPLOAD_REFERENCE = re.compile(r"/contrib_service/[A-Za-z0-9/_-]+")


def _identity(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("Invalid notebook resource identity.")
    return value


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def create_payload(*, title: str, description: str = "") -> str:
    if not isinstance(title, str) or not title.strip() or len(title) > 256 or not isinstance(description, str) or len(description) > 800:
        raise ValueError("Invalid notebook metadata.")
    metadata: list[Any] = [None] * 17
    for field, value in {1: title, 2: description, 5: [], 8: [], 9: False, 10: False,
                         11: 1, 12: [], 17: [2, None, None, None, 1, []]}.items():
        metadata[field - 1] = value
    return _dump([metadata])


def get_payload(*, notebook_id: str) -> str:
    return _dump([_identity(notebook_id)])


def delete_payload(*, notebook_id: str) -> str:
    return get_payload(notebook_id=notebook_id)


def source_create_payload(*, notebook_id: str, title: str, text: str, mime: str = "text/plain") -> str:
    _identity(notebook_id)
    if (not isinstance(title, str) or not title.strip() or len(title) > 256 or not isinstance(text, str)
            or not text or len(text) > 100000 or mime not in {"text/plain", "text/markdown"}):
        raise ValueError("Invalid source.")
    source: list[Any] = [None] * 9
    source[1], source[5], source[8] = title, [text, 2 if mime == "text/markdown" else 1], mime
    return _dump([notebook_id, source, [1, 3]])


def source_url_create_payload(*, notebook_id: str, title: str, url: str) -> str:
    _identity(notebook_id)
    if (not isinstance(title, str) or not title.strip() or len(title) > 256
            or not isinstance(url, str) or not url or len(url) > 8192
            or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in url)):
        raise ValueError("Invalid Notebook URL source.")
    location = urlsplit(url)
    if (location.scheme not in {"http", "https"} or not location.hostname
            or location.username is not None or location.password is not None):
        raise ValueError("Notebook source requires an HTTP(S) URL without embedded credentials.")
    # Accessing the port validates malformed and out-of-range ports.
    if location.port == 0:
        raise ValueError("Invalid Notebook URL port.")
    # Source field 4 selects the URL variant; MIME is optional in the native builder.
    return _dump([notebook_id, [None, title, None, [url]], [1, 3]])


def _verified_upload_reference(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 8192 or not _UPLOAD_REFERENCE.fullmatch(value):
        raise ValueError("Notebook upload reference could not be verified.")
    return value


def source_file_create_payload(*, notebook_id: str, title: str, upload_reference: str) -> str:
    _identity(notebook_id)
    _identity(title)
    reference = _verified_upload_reference(upload_reference)
    # Source field 5 selects the uploaded file variant, with MIME in fields 5.2 and 9.
    source = [None, title, None, None, [reference, NOTEBOOK_FILE_MIME], None, None, None, NOTEBOOK_FILE_MIME]
    return _dump([notebook_id, source, [1, 3]])


def source_delete_payload(*, source_id: str) -> str:
    return _dump([_identity(source_id), [1, 3]])


def parse_created(body: Any, **_: Any) -> RPCParseResult:
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty")
    identifier = body[0]
    if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256:
        return RPCParseResult("changed_shape")
    return RPCParseResult("success", value=identifier)


def parse_source_created(body: Any, *, notebook_id: str = "", **_: Any) -> RPCParseResult:
    """Read only the explicit created source name of the exact Notebook."""
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty")
    project = body[0]
    if (not notebook_id or not isinstance(project, list) or len(project) < 2 or project[0] != notebook_id
            or not isinstance(project[1], list) or len(project[1]) < 15):
        return RPCParseResult("changed_shape")
    display = project[1][14]
    if not isinstance(display, list) or len(display) < 4:
        return RPCParseResult("changed_shape")
    source_id = display[3]
    if (not isinstance(source_id, str) or not source_id or source_id.strip() != source_id
            or len(source_id) > 256 or any(ord(character) < 32 for character in source_id)):
        return RPCParseResult("changed_shape")
    return RPCParseResult("success", value=source_id)


def parse_project(body: Any, *, notebook_id: str = "", **_: Any) -> RPCParseResult:
    if not isinstance(body, list):
        return RPCParseResult("changed_shape")
    if not body or body[0] is None:
        return RPCParseResult("empty")
    project = body[0]
    if (not isinstance(project, list) or len(project) < 2 or not isinstance(project[0], str)
            or not project[0] or (notebook_id and project[0] != notebook_id) or not isinstance(project[1], list)
            or not project[1] or not isinstance(project[1][0], str)):
        return RPCParseResult("changed_shape")
    metadata = project[1]
    source_group = metadata[10] if len(metadata) > 10 else None
    sources = []
    if source_group is not None:
        if (not isinstance(source_group, list) or len(source_group) < 2
                or not isinstance(source_group[1], list)):
            return RPCParseResult("changed_shape")
        for entry in source_group[1]:
            if (not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str)
                    or not entry[0] or not isinstance(entry[1], list) or len(entry[1]) < 7
                    or entry[1][0] != entry[0] or not isinstance(entry[1][1], str)
                    or type(entry[1][6]) is not int):
                return RPCParseResult("changed_shape")
            source = entry[1]
            url_field = source[3]
            url_shape_valid = url_field is None or (isinstance(url_field, list)
                and (not url_field or url_field[0] is None or isinstance(url_field[0], str)))
            source_url = (url_field[0] or None) if url_shape_valid and url_field else None
            upload_field = source[4]
            upload_shape_valid = upload_field is None or (isinstance(upload_field, list)
                and all(value is None or isinstance(value, str) for value in upload_field[:2]))
            upload_reference = (upload_field[0] or None) if upload_shape_valid and upload_field else None
            upload_mime = (upload_field[1] or None) if upload_shape_valid and upload_field and len(upload_field) > 1 else None
            sources.append({"source_id": entry[0], "title": source[1], "state": source[6],
                            "mime": source[8] if len(source) > 8 and isinstance(source[8], str) else None,
                            "source_url": source_url,
                            "source_url_shape_valid": url_shape_valid,
                            "source_upload_reference": upload_reference,
                            "source_upload_mime": upload_mime,
                            "source_upload_shape_valid": upload_shape_valid})
        if len({item["source_id"] for item in sources}) != len(sources):
            return RPCParseResult("changed_shape")
    return RPCParseResult("success", value={"notebook_id": project[0], "title": metadata[0], "sources": sources})


async def upload_source_file(client: Any, content: bytes, *, expected_account: str) -> str:
    """Send a single SDK-shaped multipart upload using the bound account session."""
    from curl_cffi import CurlMime
    from gemini_webapi.constants import Endpoint, Headers

    from .account_identity import current_account_key
    from ..services.deadlines import await_before_deadline

    session = getattr(client, "client", None)
    post_upload = getattr(session, "post", None)
    push_id = getattr(client, "push_id", None)
    if (not expected_account or current_account_key() != expected_account
            or not callable(post_upload) or getattr(client, "_running", None) is False
            or not isinstance(push_id, str) or not 1 <= len(push_id) <= 512
            or any(character.isspace() or ord(character) < 32 for character in push_id)
            or not isinstance(content, bytes) or not 1 <= len(content) <= NOTEBOOK_FILE_MAX_BYTES):
        raise ValueError("Notebook source upload is unavailable.")

    async def dispatch(post: Callable[..., Awaitable[Any]] = post_upload) -> Any:
        if (current_account_key() != expected_account or getattr(client, "client", None) is not session
                or getattr(client, "_running", None) is False):
            raise ValueError("Notebook upload account or client changed.")
        multipart = CurlMime()
        try:
            multipart.addpart(name="file", content_type=NOTEBOOK_FILE_MIME,
                              filename="notebook-source.txt", data=content)
            headers = {**Headers.REFERER.value, **Headers.UPLOAD.value, "Push-ID": push_id}
            return await post(Endpoint.UPLOAD, headers=headers, multipart=multipart, allow_redirects=False)
        finally:
            multipart.close()

    response = await await_before_deadline(dispatch(), timeout=30)
    if (current_account_key() != expected_account or getattr(client, "client", None) is not session
            or getattr(response, "status_code", None) != 200):
        raise ValueError("Notebook file upload could not be verified.")
    return _verified_upload_reference(getattr(response, "text", None))


async def request(client: Any, key: str, *, expected_account: str | None = None, **arguments: Any) -> RPCParseResult:
    """Send one request, validate envelope, and fence account identity."""
    from .account_identity import current_account_key
    from .rpc_contracts import execute_contract, get_contract
    from .rpc_parsers import parse_contract_body, parse_rpc_envelope
    from ..services.deadlines import await_before_deadline
    owner = expected_account or current_account_key()
    if not owner or current_account_key() != owner:
        raise ValueError("Account unavailable or changed.")
    contract = get_contract(key)
    contract.build_payload(**arguments)

    async def dispatch() -> Any:
        if current_account_key() != owner or getattr(client, "_running", None) is False:
            raise ValueError("Notebook account or initialized client unavailable.")
        return await execute_contract(client, key, retry_count=0, **arguments)

    response = await await_before_deadline(dispatch(), timeout=30)
    if current_account_key() != owner:
        raise ValueError("Account changed.")
    if getattr(response, "status_code", None) != 200:
        raise ValueError("Notebook HTTP response unavailable.")
    envelope = parse_rpc_envelope(getattr(response, "text", ""), contract.rpc_id)
    receipt_creation = key in {"notebook_admin.source_create_url", "notebook_admin.source_create_file"}
    if receipt_creation and envelope.parsed and envelope.reject_code is not None:
        return RPCParseResult("rejected", reject_code=envelope.reject_code)
    if not envelope.parsed or envelope.reject_code is not None or len(envelope.bodies) != 1:
        raise ValueError("Notebook response unavailable.")
    parsed = parse_contract_body(contract, envelope.bodies[0],
                                 **({"notebook_id": arguments["notebook_id"]}
                                    if key == "notebook_admin.get" or receipt_creation else {}))
    if not parsed.ok and not receipt_creation:
        raise ValueError("Notebook response shape changed.")
    return parsed
