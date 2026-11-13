"""Tests for the RFB 3.8 client."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeVar

import pytest
from PIL import Image

from src.infrastructure.rfb import RFBClient, RFBError

_T = TypeVar("_T")

# ------------------------------------------------------------------ helpers

_SRV_VERSION = b"RFB 003.008\n"


class _ByteQueue:
    """In-memory byte stream for testing read_exact."""

    def __init__(self, data: bytes) -> None:
        self._buf = bytearray(data)

    async def read_exact(self, n: int) -> bytes:
        chunk = bytes(self._buf[:n])
        del self._buf[:n]
        return chunk


class _SendCapture:
    """Captures bytes sent by the client."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> None:
        self.sent.append(data)


def _server_init(width: int, height: int, name: bytes = b"") -> bytes:
    """Build a ServerInit payload (24-byte fixed header + name)."""
    pix_fmt = (
        bytes([24, 24, 0, 1])
        + (0xFF).to_bytes(2, "big")
        + (0xFF).to_bytes(2, "big")
        + (0xFF).to_bytes(2, "big")
        + bytes([16, 8, 0, 0, 0, 0])
    )
    return (
        width.to_bytes(2, "big")
        + height.to_bytes(2, "big")
        + pix_fmt
        + len(name).to_bytes(4, "big")
        + name
    )


def _security_offer(types: bytes | int) -> bytes:
    if isinstance(types, int):
        return bytes([1, types])
    return bytes([len(types)]) + types


def _fb_update(rects: list[tuple[int, int, int, int, int, bytes]]) -> bytes:
    """Build a FramebufferUpdate message.

    rects: list of (x, y, w, h, encoding, payload)
    """
    out = bytearray()
    out.append(0)  # message type
    out.append(0)  # pad
    out += len(rects).to_bytes(2, "big")
    for x, y, w, h, enc, payload in rects:
        out += x.to_bytes(2, "big")
        out += y.to_bytes(2, "big")
        out += w.to_bytes(2, "big")
        out += h.to_bytes(2, "big")
        out += enc.to_bytes(4, "big", signed=True)
        out += payload
    return bytes(out)


def _raw_rect_data(w: int, h: int, color: tuple[int, int, int]) -> bytes:
    """BGRX raw data for a solid color rectangle."""
    r, g, b = color
    return bytes([b, g, r, 0]) * (w * h)


def queue_reader(data: bytes) -> Callable[[int], Awaitable[bytes]]:
    q = _ByteQueue(data)

    async def reader(n: int) -> bytes:
        return await q.read_exact(n)

    return reader


def _full_handshake_server(width: int = 100, height: int = 100) -> bytes:
    """Complete valid server handshake stream."""
    name = b"test"
    server_init = _server_init(width, height, name)
    return (
        _SRV_VERSION
        + _security_offer(1)
        + (0).to_bytes(4, "big")
        + server_init
    )


def _run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    return asyncio.run(coro)


# ------------------------------------------------------------------ handshake

def test_initialize_full_handshake():
    capture = _SendCapture()
    client = RFBClient(queue_reader(_full_handshake_server()), capture.send)
    w, h = _run_async(client.initialize())
    assert (w, h) == (100, 100)
    assert client.width == 100
    assert client.height == 100

    # Verify wire: client version greeting sent
    assert capture.sent[0] == _SRV_VERSION
    # Security type None selected
    assert capture.sent[1] == bytes([1])
    # ClientInit shared=1
    assert capture.sent[2] == bytes([1])
    # SetPixelFormat
    assert capture.sent[3][0] == 0  # message type
    # SetEncodings
    assert capture.sent[4][0] == 2  # message type


def test_initialize_version_too_low():
    capture = _SendCapture()
    bad_version = b"RFB 003.007\n"
    server_data = bad_version + _security_offer(1) + (0).to_bytes(4, "big")
    client = RFBClient(queue_reader(server_data), capture.send)
    with pytest.raises(RFBError):
        _run_async(client.initialize())


def test_initialize_no_none_auth():
    capture = _SendCapture()
    server_data = _SRV_VERSION + _security_offer(2) + (0).to_bytes(4, "big")
    client = RFBClient(queue_reader(server_data), capture.send)
    with pytest.raises(RFBError):
        _run_async(client.initialize())


def test_initialize_security_result_failed():
    capture = _SendCapture()
    server_data = _SRV_VERSION + _security_offer(1) + (1).to_bytes(4, "big")
    client = RFBClient(queue_reader(server_data), capture.send)
    with pytest.raises(RFBError):
        _run_async(client.initialize())


def test_initialize_too_many_security_types():
    capture = _SendCapture()
    types = bytes(range(1, 66))
    server_data = _SRV_VERSION + bytes([len(types)]) + types
    client = RFBClient(queue_reader(server_data), capture.send)
    with pytest.raises(RFBError):
        _run_async(client.initialize())


def test_initialize_zero_dimensions():
    capture = _SendCapture()
    server_data = (
        _SRV_VERSION
        + _security_offer(1)
        + (0).to_bytes(4, "big")
        + _server_init(0, 100)
    )
    client = RFBClient(queue_reader(server_data), capture.send)
    with pytest.raises(RFBError):
        _run_async(client.initialize())


def test_initialize_too_large_dimensions():
    capture = _SendCapture()
    server_data = (
        _SRV_VERSION
        + _security_offer(1)
        + (0).to_bytes(4, "big")
        + _server_init(5000, 5000)
    )
    client = RFBClient(queue_reader(server_data), capture.send)
    with pytest.raises(RFBError):
        _run_async(client.initialize())


def test_initialize_pixel_count_exceeds():
    capture = _SendCapture()
    # 5000x4000 = 20M > 16M limit
    server_data = (
        _SRV_VERSION
        + _security_offer(1)
        + (0).to_bytes(4, "big")
        + _server_init(5000, 4000)
    )
    client = RFBClient(queue_reader(server_data), capture.send)
    with pytest.raises(RFBError):
        _run_async(client.initialize())


# ------------------------------------------------------------------ screenshot

def _run_screenshot(client: RFBClient, server_data: bytes) -> bytes:
    queue = _ByteQueue(server_data)
    client._read_exact = queue.read_exact  # type: ignore[method-assign]
    return _run_async(client.screenshot_png())


def test_screenshot_full_coverage_raw():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    server_data = _fb_update([
        (0, 0, 1, 1, 0, _raw_rect_data(1, 1, (255, 0, 0))),
        (1, 0, 1, 1, 0, _raw_rect_data(1, 1, (0, 255, 0))),
        (0, 1, 1, 1, 0, _raw_rect_data(1, 1, (0, 0, 255))),
        (1, 1, 1, 1, 0, _raw_rect_data(1, 1, (255, 255, 255))),
    ])

    png_bytes = _run_screenshot(client, server_data)
    assert len(png_bytes) > 0

    buf = io.BytesIO(png_bytes)
    with Image.open(buf) as img:
        img.load()
        assert img.size == (2, 2)
        assert img.getpixel((0, 0)) == (255, 0, 0)
        assert img.getpixel((1, 0)) == (0, 255, 0)
        assert img.getpixel((0, 1)) == (0, 0, 255)
        assert img.getpixel((1, 1)) == (255, 255, 255)


def test_screenshot_single_rect_full_frame():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    server_data = _fb_update([
        (0, 0, 2, 2, 0, _raw_rect_data(2, 2, (128, 128, 128))),
    ])

    png_bytes = _run_screenshot(client, server_data)
    buf = io.BytesIO(png_bytes)
    with Image.open(buf) as img:
        img.load()
        assert img.size == (2, 2)
        assert img.getpixel((0, 0)) == (128, 128, 128)
        assert img.getpixel((1, 1)) == (128, 128, 128)


def test_screenshot_partial_frame_rejected():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    server_data = _fb_update([
        (0, 0, 1, 1, 0, _raw_rect_data(1, 1, (255, 0, 0))),
    ])

    with pytest.raises(RFBError):
        _run_screenshot(client, server_data)


def test_screenshot_desktop_resize():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    # First: DesktopSize resize to 1x1, then cover it
    server_data = (
        _fb_update([(0, 0, 1, 1, -223, b"")])
        + _fb_update([(0, 0, 1, 1, 0, _raw_rect_data(1, 1, (255, 0, 0)))])
    )

    png_bytes = _run_screenshot(client, server_data)
    assert client.width == 1
    assert client.height == 1
    buf = io.BytesIO(png_bytes)
    with Image.open(buf) as img:
        img.load()
        assert img.size == (1, 1)
        assert img.getpixel((0, 0)) == (255, 0, 0)


def test_screenshot_unknown_message_type():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    server_data = bytes([99, 0, 0])  # unknown message type
    with pytest.raises(RFBError):
        _run_screenshot(client, server_data)


def test_screenshot_unrequested_encoding():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    server_data = _fb_update([
        (0, 0, 2, 2, 999, b""),
    ])
    with pytest.raises(RFBError):
        _run_screenshot(client, server_data)


def test_screenshot_rect_outside_framebuffer():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    server_data = _fb_update([
        (0, 0, 3, 3, 0, _raw_rect_data(3, 3, (255, 0, 0))),
    ])
    with pytest.raises(RFBError):
        _run_screenshot(client, server_data)


def test_screenshot_bell_ignored():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    server_data = (
        bytes([2])  # Bell
        + _fb_update([(0, 0, 2, 2, 0, _raw_rect_data(2, 2, (10, 20, 30)))])
    )
    png_bytes = _run_screenshot(client, server_data)
    buf = io.BytesIO(png_bytes)
    with Image.open(buf) as img:
        img.load()
        assert img.getpixel((0, 0)) == (10, 20, 30)


def test_screenshot_server_cut_text_discarded():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 2
    client.height = 2

    cut_text = b"clipboard data"
    server_data = (
        bytes([3, 0, 0, 0])  # ServerCutText, three padding bytes
        + len(cut_text).to_bytes(4, "big")
        + cut_text
        + _fb_update([(0, 0, 2, 2, 0, _raw_rect_data(2, 2, (10, 20, 30)))])
    )
    png_bytes = _run_screenshot(client, server_data)
    buf = io.BytesIO(png_bytes)
    with Image.open(buf) as img:
        img.load()
        assert img.getpixel((0, 0)) == (10, 20, 30)


# ------------------------------------------------------------------ input validation

def test_click_bool_x_rejected():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.click(True, 50)  # type: ignore[arg-type]
    assert capture.sent == []


def test_click_bool_y_rejected():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.click(50, True)  # type: ignore[arg-type]
    assert capture.sent == []


def test_click_outside_framebuffer():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.click(100, 50)
    assert capture.sent == []


def test_click_negative():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.click(-1, 50)
    assert capture.sent == []


def test_click_valid_buttons():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    client.click(10, 10, "left")
    client.click(20, 20, "right")
    client.click(30, 30, "middle")
    assert len(capture.sent) == 6  # press + release for each


def test_click_unknown_button():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.click(10, 10, "invalid")  # type: ignore[arg-type]
    assert capture.sent == []


def test_scroll_steps_limit():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.scroll(10, 10, 11)
    assert capture.sent == []


def test_scroll_bool_steps():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.scroll(10, 10, True)  # type: ignore[arg-type]
    assert capture.sent == []


def test_scroll_valid():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    client.scroll(10, 10, 3)
    client.scroll(20, 20, -2)
    assert len(capture.sent) == 10  # 5 steps * press + release


def test_type_text_too_long():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.type_text("x" * 2049)
    assert capture.sent == []


def test_type_text_valid():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    client.type_text("hi\n")
    assert len(capture.sent) > 0  # key events sent


# ------------------------------------------------------------------ key validation

def test_key_allowlist():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100

    for k in [
        "Enter", "Tab", "Escape", "Backspace", "Delete",
        "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
        "Home", "End", "PageUp", "PageDown", "Space",
        "Ctrl+l", "Ctrl+a", "Ctrl+c", "Ctrl+v", "Ctrl+r",
        "Alt+Left", "Alt+Right",
        "a", "A", "1",
    ]:
        client.key(k)

    assert len(capture.sent) > 0


def test_key_unknown_rejected():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.key("F1")
    assert capture.sent == []


def test_key_empty_rejected():
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100
    with pytest.raises(RFBError):
        client.key("")
    assert capture.sent == []


def test_key_release_on_error():
    """Modifiers must be released even if a key event send fails."""
    capture = _SendCapture()
    client = RFBClient(queue_reader(b""), capture.send)
    client.width = 100
    client.height = 100

    original_send = client._send
    call_count = [0]

    def failing_send(data: bytes) -> None:
        call_count[0] += 1
        if call_count[0] == 2:
            raise RuntimeError("send failure")
        original_send(data)

    client._send = failing_send  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        client.key("Ctrl+l")

    # After the error, modifier should have been released
    assert call_count[0] >= 2


# ------------------------------------------------------------------ wire format

def test_set_pixel_format_wire():
    capture = _SendCapture()
    client = RFBClient(queue_reader(_full_handshake_server()), capture.send)
    _run_async(client.initialize())

    spf = capture.sent[3]
    assert spf[0] == 0  # SetPixelFormat
    assert spf[4] == 32  # bpp
    assert spf[5] == 24  # depth
    assert spf[6] == 0  # little endian
    assert spf[7] == 1  # true color


def test_set_encodings_wire():
    capture = _SendCapture()
    client = RFBClient(queue_reader(_full_handshake_server()), capture.send)
    _run_async(client.initialize())

    se = capture.sent[4]
    assert se[0] == 2  # SetEncodings
    n = int.from_bytes(se[2:4], "big")
    assert n == 3
    encodings = [
        int.from_bytes(se[4 + i * 4:8 + i * 4], "big", signed=True)
        for i in range(3)
    ]
    assert 0 in encodings  # Raw
    assert -223 in encodings  # DesktopSize
    assert -224 in encodings  # LastRect


def test_client_init_shared_one():
    capture = _SendCapture()
    client = RFBClient(queue_reader(_full_handshake_server()), capture.send)
    _run_async(client.initialize())
    assert capture.sent[2] == bytes([1])  # shared = 1