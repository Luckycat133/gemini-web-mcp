"""Synthetic, credential-free WebChannel and protobuf wire fixtures."""

import base64
import json

import pytest

from src.infrastructure.webchannel import decode_frames, parse_protobuf


def frame(value):
    body = json.dumps(value, separators=(",", ":")).encode()
    return str(len(body)).encode() + b"\n" + body


def test_decodes_handshake_data_control_and_nested_protobuf():
    # field 2 -> message: field 1 = 3, field 7 -> empty message
    message = b"\x12\x04\x08\x03\x3a\x00"
    encoded = base64.b64encode(message).decode()
    stream = frame([[0, ["c", "synthetic", "", 8, 15, 1000]]]) + frame(
        [[1, [encoded]], [2, ["noop"]], [3, ["stop"]]]
    )
    frames = decode_frames(stream)
    assert [entry.kind for entry in frames] == ["handshake", "data", "control", "control"]
    assert [entry.sequence for entry in frames] == [0, 1, 2, 3]
    top = parse_protobuf(frames[1].payload)
    assert top[0].number == 2 and top[0].wire_type == 2
    nested = parse_protobuf(top[0].value)
    assert [(f.number, f.wire_type, f.value) for f in nested] == [(1, 0, 3), (7, 2, b"")]


def test_empty_stream_and_message_are_valid():
    assert decode_frames(b"") == []
    assert parse_protobuf(b"") == ()


@pytest.mark.parametrize("data", [b"12", b"x\n[]", b"3\n[]", b"2\n[}", b"0\n"])
def test_rejects_truncated_or_invalid_framing(data):
    with pytest.raises(ValueError):
        decode_frames(data)


@pytest.mark.parametrize("value", [None, {}, [[0, ["halt"]]], [[0, ["bad?"]]], [[0, ["noop", 1]]], [[True, ["stop"]]], [[0, ["c", "x"]]], [[0, ["!invalid!"]]]])
def test_rejects_changed_or_unrecognized_envelopes(value):
    with pytest.raises(ValueError):
        decode_frames(frame(value))


@pytest.mark.parametrize("message", [b"\x80", b"\x00", b"\x0b", b"\x0a\x03x", b"\x09x", b"\x08" + b"\xff" * 10])
def test_rejects_invalid_or_truncated_protobuf(message):
    with pytest.raises(ValueError):
        parse_protobuf(message)


def test_decodes_fixed_width_fields_without_inventing_schema():
    fields = parse_protobuf(b"\x09" + b"12345678" + b"\x15" + b"1234")
    assert [(f.number, f.wire_type, f.value) for f in fields] == [(1, 1, b"12345678"), (2, 5, b"1234")]
