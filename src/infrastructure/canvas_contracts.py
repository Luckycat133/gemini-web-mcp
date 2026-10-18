"""Pure, narrowly scoped transformations for native Canvas documents."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class CanvasDocumentShapeError(ValueError):
    """The observed Canvas document no longer matches its recorded contract."""


class UnsupportedCanvasDocument(ValueError):
    """Replacing this content could discard attachments, citations or other kinds."""


# x9QgSe:2189 GF contains the mutually exclusive artifact variant fields.
_OTHER_VARIANTS = (15, 16, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 31, 32, 33, 34)


def _present(value: Any) -> bool:
    return value is not None and value != []


def _plain_text_nodes(structured: Any) -> None:
    """Allow text/heading paragraphs and inline formatting, excluding sources."""
    if not isinstance(structured, list) or not structured or not isinstance(structured[0], list):
        raise CanvasDocumentShapeError("Canvas structured content has no node collection.")
    if any(_present(value) for value in structured[1:]):
        raise UnsupportedCanvasDocument("Canvas structured metadata requires a separate editing contract.")
    for node in structured[0]:
        # qH oneof field2 = iH. Tables, lists and attachment nodes are outside
        # this plain-document replacement scope (x9QgSe:2912-2915,3474).
        if not isinstance(node, list) or len(node) < 2 or not isinstance(node[1], list):
            raise UnsupportedCanvasDocument("Canvas replacement supports text paragraphs only.")
        if any(_present(value) for i, value in enumerate(node) if i != 1):
            raise UnsupportedCanvasDocument("Canvas contains a non-text or attachment node.")
        paragraph = node[1]
        if len(paragraph) < 3 or not isinstance(paragraph[2], str):
            raise CanvasDocumentShapeError("Canvas text paragraph shape changed.")
        if len(paragraph) > 1 and paragraph[1] is not None and type(paragraph[1]) is not int:
            raise CanvasDocumentShapeError("Canvas paragraph formatting value changed.")
        annotations = paragraph[3] if len(paragraph) > 3 else None
        if annotations is not None and not isinstance(annotations, list):
            raise CanvasDocumentShapeError("Canvas text annotations changed shape.")
        for annotation in annotations or []:
            if not isinstance(annotation, list) or len(annotation) < 3 or not isinstance(annotation[2], list):
                raise CanvasDocumentShapeError("Canvas inline annotation changed shape.")
            # LFc field3=jH; only style/link/dotted formatting (fields1-3)
            # may be discarded. jH source/citation variants start at field4.
            if any(_present(value) for value in annotation[2][3:]):
                raise UnsupportedCanvasDocument("Canvas citations cannot be replaced by this plain-text editor.")
        if _present(paragraph[0]) or any(_present(value) for value in paragraph[4:]):
            raise UnsupportedCanvasDocument("Canvas contains unsupported text metadata.")


def _document(raw_ez: Any) -> list[Any]:
    if (not isinstance(raw_ez, list) or len(raw_ez) < 14 or not isinstance(raw_ez[0], str)
            or not raw_ez[0].strip()):
        raise CanvasDocumentShapeError("Canvas artifact shape changed.")
    if type(raw_ez[10]) is not int or raw_ez[10] != 1:
        raise UnsupportedCanvasDocument("Canvas text replacement supports document kind 1 only.")
    if any(len(raw_ez) >= field and _present(raw_ez[field - 1]) for field in _OTHER_VARIANTS):
        raise UnsupportedCanvasDocument("Canvas contains a different or conflicting artifact variant.")
    document = raw_ez[13]
    if (not isinstance(document, list) or len(document) < 3
            or not isinstance(raw_ez[4], str) or not isinstance(document[0], str)):
        raise CanvasDocumentShapeError("Canvas document text/variant fields changed shape.")
    # DJc outer attachments f6/citations f12; wIc document attachments f2/
    # citations f4 (x9QgSe:2927,2938,3501-3506).
    if (_present(raw_ez[5]) or _present(raw_ez[11]) or _present(document[1])
            or (len(document) > 3 and _present(document[3]))):
        raise UnsupportedCanvasDocument("Canvas attachments or citations require a separate editing contract.")
    _plain_text_nodes(raw_ez[8])
    _plain_text_nodes(document[2])
    return document


def replace_plain_document(raw_ez: Any, text: str) -> list[Any]:
    """Replace all document text with one plain paragraph, clearing formatting.

    The observed artifact is deep-copied. Only outer text f5/structured f9 and
    document f14 text f1/structured f3 change; all other fields survive intact.
    """
    if not isinstance(text, str):
        raise ValueError("Canvas replacement text must be a string.")
    _document(raw_ez)
    updated: list[Any] = deepcopy(raw_ez)
    structured = [[[None, [None, 0, text]]]]
    updated[4], updated[8] = text, deepcopy(structured)
    updated[13][0], updated[13][2] = text, deepcopy(structured)
    return updated


def plain_document_matches(raw_ez: Any, text: str) -> bool:
    """Require exact text copies and the observed structured normalization."""
    document = _document(raw_ez)
    return bool(raw_ez[4] == text and document[0] == text
                and _structured_matches(raw_ez[8], text) and _structured_matches(document[2], text))


def _structured_matches(structured: list[Any], text: str) -> bool:
    # The provider reparses "# title\n\nbody" into VM1/VM0 nodes. Preserve
    # Markdown text when verifying. A second owned read-back also trimmed only
    # the outer ASCII spaces/LF in structured nodes while preserving both text
    # fields byte-for-byte. Do not normalize interior whitespace or the text
    # fields themselves. Both structured copies are checked independently.
    paragraphs: list[str] = []
    for node in structured[0]:
        paragraph = node[1]
        if len(paragraph) > 3 and _present(paragraph[3]):
            return False
        level = paragraph[1]
        if level is None or type(level) is int and level == 0:
            prefix = ""
        elif type(level) is int and level == 1:
            prefix = "# "
        else:
            return False
        paragraphs.append(prefix + paragraph[2])
    actual = "\n\n".join(paragraphs)
    return actual == text or actual == text.strip(" \n")
