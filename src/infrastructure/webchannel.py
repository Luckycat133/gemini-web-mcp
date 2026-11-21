"""Pure WebChannel framing and protobuf wire decoding.

These functions decode observed wire structures only; they do not authenticate,
perform requests, infer application message schemas, or log payloads.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Literal

_MAX_FRAME = 16 * 1024 * 1024
_MAX_FIELDS = 100_000


@dataclass(frozen=True)
class WebChannelFrame:
    sequence: int
    kind: Literal["handshake", "data", "control"]
    payload: bytes | None = None
    control: str | None = None


@dataclass(frozen=True)
class ProtoField:
    number: int
    wire_type: int
    value: int | bytes


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for index in range(10):
        if pos >= len(data):
            raise ValueError("truncated varint")
        byte = data[pos]
        pos += 1
        if index == 9 and byte > 1:
            raise ValueError("varint overflow")
        value |= (byte & 0x7f) << (7 * index)
        if byte < 0x80:
            return value, pos
    raise ValueError("varint overflow")


def parse_protobuf(data: bytes) -> tuple[ProtoField, ...]:
    """Decode one protobuf message; length-delimited fields remain raw bytes."""
    fields: list[ProtoField] = []
    pos = 0
    while pos < len(data):
        if len(fields) >= _MAX_FIELDS:
            raise ValueError("too many protobuf fields")
        tag, pos = _varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if not number or number >= 1 << 29:
            raise ValueError("invalid protobuf field number")
        value: int | bytes
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, pos = _varint(data, pos)
            else:
                size = 8 if wire == 1 else 4
            if size > len(data) - pos:
                raise ValueError("truncated protobuf field")
            value = data[pos : pos + size]
            pos += size
        else:
            raise ValueError("unsupported protobuf wire type")
        fields.append(ProtoField(number, wire, value))
    return tuple(fields)


def decode_frames(data: bytes) -> list[WebChannelFrame]:
    """Decode complete length-prefixed JSON envelopes into recognized frames."""
    result: list[WebChannelFrame] = []
    pos = 0
    while pos < len(data):
        newline = data.find(b"\n", pos, min(len(data), pos + 12))
        if newline < 0:
            raise ValueError("missing frame length")
        digits = data[pos:newline]
        if not digits or not digits.isdigit() or len(digits) > 8:
            raise ValueError("invalid frame length")
        size = int(digits)
        if size > _MAX_FRAME or size > len(data) - newline - 1:
            raise ValueError("invalid or truncated frame")
        pos = newline + 1
        raw = data[pos : pos + size]
        pos += size
        try:
            envelope = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("invalid frame JSON") from exc
        if not isinstance(envelope, list):
            raise ValueError("invalid envelope")
        for item in envelope:
            if not isinstance(item, list) or len(item) != 2:
                raise ValueError("invalid envelope item")
            sequence, parts = item
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
                raise ValueError("invalid sequence")
            if not isinstance(parts, list) or not parts or not isinstance(parts[0], str):
                raise ValueError("invalid envelope body")
            if parts[0] == "c":
                if len(parts) != 6 or not isinstance(parts[1], str) or not isinstance(parts[2], str) or any(isinstance(v, bool) or not isinstance(v, int) for v in parts[3:]):
                    raise ValueError("invalid handshake")
                result.append(WebChannelFrame(sequence, "handshake"))
            elif parts[0] in ("noop", "stop"):
                if len(parts) != 1:
                    raise ValueError("invalid control")
                result.append(WebChannelFrame(sequence, "control", control=parts[0]))
            else:
                if len(parts) != 1:
                    raise ValueError("unknown envelope body")
                if len(parts[0]) <= 4:
                    raise ValueError("unknown control")
                try:
                    payload = base64.b64decode(parts[0], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("invalid data encoding") from exc
                if not payload:
                    raise ValueError("empty data payload")
                result.append(WebChannelFrame(sequence, "data", payload=payload))
    return result
