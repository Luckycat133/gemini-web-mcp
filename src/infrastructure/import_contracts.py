"""Pure native attachment builders; resource selection/permission stays with the caller.

Observed 2026-10-18 in public BardChatUi x9QgSe:3912-3913 (dtd/SK),
3968 (query field4),1594-1599 (file type); current React
useChatSession-DG7QezyD rt maps the same Drive/Photos/Notebook references.
No request in this module lists resources, opens a picker or grants access.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


# Non-family exceptions to the frontend's text/image/audio/video fallback.
_MIME_TYPES = {'application/dart': 3,
 'application/ecmascript': 3,
 'application/gzip': 9,
 'application/json': 5,
 'application/ms-java': 3,
 'application/msword': 10,
 'application/pdf': 11,
 'application/protobuf': 3,
 'application/sql': 3,
 'application/typescript': 3,
 'application/vnd.apache.parquet': 3,
 'application/vnd.curl': 3,
 'application/vnd.dart': 3,
 'application/vnd.google-apps.document': 8,
 'application/vnd.google-apps.folder': 17,
 'application/vnd.google-apps.kix': 8,
 'application/vnd.google-apps.presentation': 13,
 'application/vnd.google-apps.punch': 13,
 'application/vnd.google-apps.ritz': 6,
 'application/vnd.google-apps.spreadsheet': 6,
 'application/vnd.ibm.secure-container': 3,
 'application/vnd.jupyter': 3,
 'application/vnd.ms-excel': 7,
 'application/vnd.oasis.opendocument.text': 14,
 'application/vnd.openxmlformats-officedocument.presentationml.presentation': 12,
 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 7,
 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 10,
 'application/vnd.openxmlformats-officedocument.wordprocessingml.template': 10,
 'application/x-csh': 3,
 'application/x-gzip': 9,
 'application/x-hwp': 15,
 'application/x-hwp-v5': 15,
 'application/x-latex': 3,
 'application/x-php': 3,
 'application/x-powershell': 3,
 'application/x-protobuf; type=bard.data.hydra.Logits.Content': 3,
 'application/x-protobuf; type=chrome_intelligence_proto_features.AnnotatedPageContent': 18,
 'application/x-protobuf; type=chrome_intelligence_proto_features.annotatedpagecontent': 18,
 'application/x-protobuf; type=gdm_flair_distillation.Logits': 3,
 'application/x-sh': 3,
 'application/x-shellscript': 3,
 'application/x-tex': 3,
 'application/x-zsh': 3,
 'application/xml': 3,
 'application/zip': 9,
 'message/rfc822': 19}
_CODE_EXTENSIONS = frozenset("""asm asset bib boo c c++ cc ccc clang-format class cls coffee conf config cpp cql cs csh css cu cuh curl cxx d dart el erl es ets flake8 gitignore go gv h h++ hh hpp hrl hs htc htm html hxx in ini ipynb jad java js json json5 jsx jtd kt ktm lhs local lsp ltx lua m manifest map md meta metal mjs mm mo n3 p pas patch php pie pl pm po properties ps1 py r rb rmd rs s sample sass sc scala scm scss sgm sgml sh shaclc shex shtml si sl sos spo sql sty swift symbols t tag tcl tk tm toml tr ts tsx ttl types uri uris vue wgsl wml wmls xml yaml yml zone zsh""".split())


def native_file_type(mime_type: str, name: str) -> int:
    """Match the observed frontend enum, including source-code filename priority."""
    basename = name.rsplit("/", 1)[-1]
    dot = basename.rfind(".")
    extension = basename[dot + 1:].lower() if dot > 0 else ""
    if basename in {"BUILD", "makefile"} or extension in _CODE_EXTENSIONS:
        return 16
    if mime_type in _MIME_TYPES:
        return _MIME_TYPES[mime_type]
    for prefix, value in (("text/", 3), ("image/", 1), ("audio/", 4), ("video/", 2)):
        if mime_type.startswith(prefix):
            return value
    return 0


def build_native_attachment(
    *, kind: str, resource_id: str, name: str, mime_type: str = "", url: str | None = None,
) -> list[Any]:
    """Build one exact user-selected reference for StreamGenerate query field4.

    IDs are opaque. The builder does not convert a URL into an ID, strip a
    provider prefix, manufacture a permission token or fetch the resource.
    """
    if not isinstance(kind, str) or kind not in {"drive", "photos", "notebook"}:
        raise ValueError("Native attachments support drive, photos or notebook references.")
    if (not isinstance(resource_id, str) or not resource_id.strip() or resource_id != resource_id.strip()
            or not isinstance(name, str) or not name.strip()
            or not isinstance(mime_type, str) or url is not None and not isinstance(url, str)):
        raise ValueError("A nonempty exact resource ID and name, with string MIME type and optional URL, are required.")
    # SK field2 is name; its other alternatives remain unset.
    attachment: list[Any] = [None, name]
    if kind == "drive":
        mime = mime_type or "application/octet-stream"
        attachment.extend([None, None, [[resource_id], native_file_type(mime, name), url, mime]])
    elif kind == "photos":
        # Current picker normalizes HEIC from its filename, otherwise preserving
        # a supplied MIME type or using the frontend's octet-stream fallback.
        dot = name.rfind(".")
        mime = "image/heic" if dot > 0 and name[dot + 1:].lower() == "heic" else mime_type or "application/octet-stream"
        attachment.extend([None] * 5 + [[[resource_id], mime]])
    else:
        attachment.extend([None] * 9 + [[[resource_id]]])
    return attachment


def build_native_attachments(items: Iterable[Any]) -> list[list[Any]]:
    """Accept mapping records or attribute records without importing the domain."""
    result: list[list[Any]] = []
    for item in items:
        if len(result) >= 10:
            raise ValueError("At most ten native attachments are supported per request.")
        def field(key: str, default: Any = None) -> Any:
            return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)
        result.append(build_native_attachment(kind=field("kind"), resource_id=field("resource_id"),
                                              name=field("name"), mime_type=field("mime_type", ""), url=field("url")))
    return result
