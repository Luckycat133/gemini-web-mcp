"""RFB 3.8 client over a pure byte stream."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Final

__all__ = ["RFBClient", "RFBError"]

# ---- RFB protocol constants ----

_RFB_VERSION: Final[bytes] = b"RFB 003.008\n"
_RFB_VERSION_PREFIX: Final[bytes] = b"RFB "
_SECURITY_NONE: Final[int] = 1
_SECURITY_RESULT_OK: Final[int] = 0

_ENCODING_RAW: Final[int] = 0
_ENCODING_DESKTOP_SIZE: Final[int] = -223
_ENCODING_LAST_RECT: Final[int] = -224

_MSG_SERVER_FB_UPDATE: Final[int] = 0
_MSG_SERVER_BELL: Final[int] = 2
_MSG_SERVER_CUT_TEXT: Final[int] = 3

_CLIENT_SET_PIXEL_FORMAT: Final[int] = 0
_CLIENT_SET_ENCODINGS: Final[int] = 2
_CLIENT_FB_UPDATE_REQUEST: Final[int] = 3
_CLIENT_KEY_EVENT: Final[int] = 4
_CLIENT_POINTER_EVENT: Final[int] = 5

# X11 keysyms
_XK_RETURN: Final[int] = 0xFF0D
_XK_TAB: Final[int] = 0xFF09
_XK_ESCAPE: Final[int] = 0xFF1B
_XK_BACKSPACE: Final[int] = 0xFF08
_XK_DELETE: Final[int] = 0xFFFF
_XK_LEFT: Final[int] = 0xFF51
_XK_RIGHT: Final[int] = 0xFF53
_XK_UP: Final[int] = 0xFF52
_XK_DOWN: Final[int] = 0xFF54
_XK_HOME: Final[int] = 0xFF50
_XK_END: Final[int] = 0xFF57
_XK_PAGE_UP: Final[int] = 0xFF55
_XK_PAGE_DOWN: Final[int] = 0xFF56
_XK_SPACE: Final[int] = 0x0020
_XK_CTRL_L: Final[int] = 0xFFE3
_XK_ALT_L: Final[int] = 0xFFE9

_KEY_SYMS: Final[dict[str, int]] = {
    "Enter": _XK_RETURN,
    "Tab": _XK_TAB,
    "Escape": _XK_ESCAPE,
    "Backspace": _XK_BACKSPACE,
    "Delete": _XK_DELETE,
    "ArrowLeft": _XK_LEFT,
    "ArrowRight": _XK_RIGHT,
    "ArrowUp": _XK_UP,
    "ArrowDown": _XK_DOWN,
    "Home": _XK_HOME,
    "End": _XK_END,
    "PageUp": _XK_PAGE_UP,
    "PageDown": _XK_PAGE_DOWN,
    "Space": _XK_SPACE,
}

_MODIFIER_KEY_SYMS: Final[dict[str, int]] = {
    "Ctrl": _XK_CTRL_L,
    "Alt": _XK_ALT_L,
}

_BUTTON_MASKS: Final[dict[str, int]] = {
    "left": 0x01,
    "middle": 0x02,
    "right": 0x04,
}

# Standard vertical wheel buttons 4 and 5.
_SCROLL_UP_MASK: Final[int] = 0x08
_SCROLL_DOWN_MASK: Final[int] = 0x10

# Limits
_MAX_TEXT_CHARS: Final[int] = 2048
_MAX_SCROLL_STEPS: Final[int] = 10
_MAX_FRAMEBUFFER_DIM: Final[int] = 4096
_MAX_TOTAL_PIXELS: Final[int] = 16777216
_MAX_SERVER_NAME: Final[int] = 65536
_MAX_SERVER_MESSAGES: Final[int] = 128
_MAX_RECTANGLES: Final[int] = 4096
_MAX_IMAGE_BYTES: Final[int] = 64 * 1024 * 1024  # 64 MiB
_MAX_PNG_BYTES: Final[int] = 20 * 1024 * 1024  # 20 MiB
_MAX_CAPTURE_BYTES: Final[int] = 128 * 1024 * 1024
_MAX_COVERAGE_REGIONS: Final[int] = 8192
_SCREENSHOT_TIMEOUT: Final[float] = 30.0


class RFBError(Exception):
    """Raised on RFB protocol or usage errors."""


class RFBClient:
    """Asynchronous RFB 3.8 client over a pure byte stream.

    Parameters
    ----------
    read_exact:
        Async callable returning exactly *n* bytes (or fewer on EOF).
    send:
        Synchronous callable that writes raw bytes to the stream.
    """

    def __init__(
        self,
        read_exact: Callable[[int], Awaitable[bytes]],
        send: Callable[[bytes], None],
    ) -> None:
        self._read_exact = read_exact
        self._send = send
        self.width = 0
        self.height = 0

    # ------------------------------------------------------------------ init

    async def initialize(self) -> tuple[int, int]:
        """Perform RFB 3.8 handshake.  Returns ``(width, height)``."""
        # Server version (12 bytes)
        server_version = await self._read_exact(12)
        if len(server_version) != 12:
            raise RFBError("short server version greeting")
        if not server_version.startswith(_RFB_VERSION_PREFIX):
            raise RFBError("invalid server version greeting")
        try:
            version_text = server_version.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RFBError("non-ascii server version") from exc
        # "RFB 003.008\n" -> major at [4:7], minor at [8:11]
        try:
            s_major = int(version_text[4:7])
            s_minor = int(version_text[8:11])
        except ValueError as exc:
            raise RFBError("unparseable server version") from exc
        if (s_major, s_minor) < (3, 8):
            raise RFBError(f"RFB version {s_major}.{s_minor} < 3.8")
        if server_version[7:8] != b'.' or server_version[-1:] != b'\n':
            raise RFBError('invalid server version greeting')
        self._send(_RFB_VERSION)

        # Security types
        count_bytes = await self._read_exact(1)
        if len(count_bytes) != 1:
            raise RFBError("missing security type count")
        n_types = count_bytes[0]
        if n_types > 64:
            raise RFBError(f"too many security types: {n_types}")
        sec_types = await self._read_exact(n_types)
        if len(sec_types) != n_types:
            raise RFBError("short security type list")
        if _SECURITY_NONE not in sec_types:
            raise RFBError("No authentication not offered")

        # Select None authentication
        self._send(bytes([_SECURITY_NONE]))

        # Security result
        result_bytes = await self._read_exact(4)
        if len(result_bytes) != 4:
            raise RFBError("short security result")
        result = int.from_bytes(result_bytes, "big")
        if result != _SECURITY_RESULT_OK:
            raise RFBError(f"security result failed: {result}")

        # ClientInit: shared = 1 (do not disconnect the viewer)
        self._send(bytes([1]))

        # ServerInit (fixed 24-byte header + variable name)
        fixed = await self._read_exact(24)
        if len(fixed) != 24:
            raise RFBError("short server init")
        self.width = int.from_bytes(fixed[0:2], "big")
        self.height = int.from_bytes(fixed[2:4], "big")
        if not (1 <= self.width <= _MAX_FRAMEBUFFER_DIM):
            raise RFBError(f"invalid framebuffer width: {self.width}")
        if not (1 <= self.height <= _MAX_FRAMEBUFFER_DIM):
            raise RFBError(f"invalid framebuffer height: {self.height}")
        if self.width * self.height > _MAX_TOTAL_PIXELS:
            raise RFBError("framebuffer pixel count exceeds limit")
        name_length = int.from_bytes(fixed[20:24], "big")
        if name_length > _MAX_SERVER_NAME:
            raise RFBError(f"server name too long: {name_length}")
        if name_length:
            name_data = await self._read_exact(name_length)
            if len(name_data) != name_length:
                raise RFBError("short server name")

        # SetPixelFormat: 32 bpp, 24 depth, little-endian, truecolor
        #   R: shift 16, G: shift 8, B: shift 0  (wire order: BGRX)
        pix_fmt = bytes(
            [32, 24, 0, 1]  # bpp, depth, big-endian=0, truecolor=1
        ) + (255).to_bytes(2, "big") + (255).to_bytes(2, "big") + (255).to_bytes(2, "big") + bytes([16, 8, 0, 0, 0, 0])
        self._send(bytes([_CLIENT_SET_PIXEL_FORMAT, 0, 0, 0]) + pix_fmt)

        # SetEncodings: Raw, DesktopSize, LastRect
        enc_parts = b"".join(
            e.to_bytes(4, "big", signed=True)
            for e in (_ENCODING_RAW, _ENCODING_DESKTOP_SIZE, _ENCODING_LAST_RECT)
        )
        self._send(
            bytes([_CLIENT_SET_ENCODINGS, 0])
            + (3).to_bytes(2, "big")
            + enc_parts
        )

        return self.width, self.height

    # ----------------------------------------------------------------- screenshot

    async def screenshot_png(self) -> bytes:
        """Capture a full-frame screenshot and return PNG bytes."""
        from PIL import Image

        if self.width <= 0 or self.height <= 0:
            raise RFBError("framebuffer dimensions not established")

        async def _capture() -> bytes:
            deadline = time.monotonic() + _SCREENSHOT_TIMEOUT
            fw, fh = self.width, self.height
            image = Image.new('RGB', (fw, fh))
            uncovered: list[tuple[int, int, int, int]] = [(0, 0, fw, fh)]
            rect_count = 0
            msg_count = 0
            received_bytes = 0
            self._send(bytes([_CLIENT_FB_UPDATE_REQUEST, 0]) + b'\x00' * 4
                       + fw.to_bytes(2, 'big') + fh.to_bytes(2, 'big'))

            while uncovered:
                if time.monotonic() >= deadline:
                    raise RFBError('screenshot deadline exceeded')
                if msg_count >= _MAX_SERVER_MESSAGES:
                    raise RFBError("too many server messages in screenshot")
                msg_count += 1

                mtype_bytes = await self._read_exact(1)
                if len(mtype_bytes) != 1:
                    raise RFBError("EOF reading server message type")
                mtype = mtype_bytes[0]

                if mtype == _MSG_SERVER_FB_UPDATE:
                    pad = await self._read_exact(1)
                    if len(pad) != 1:
                        raise RFBError("EOF reading FB update pad")
                    n_rect_bytes = await self._read_exact(2)
                    if len(n_rect_bytes) != 2:
                        raise RFBError("EOF reading rectangle count")
                    n_rects = int.from_bytes(n_rect_bytes, "big")
                    if n_rects > _MAX_RECTANGLES and n_rects != 65535:
                        raise RFBError(f"too many rectangles: {n_rects}")

                    for _ in range(n_rects):
                        if rect_count >= _MAX_RECTANGLES:
                            raise RFBError("rectangle budget exceeded")
                        rect_count += 1

                        hdr = await self._read_exact(8)
                        if len(hdr) != 8:
                            raise RFBError("short rectangle header")
                        rx = int.from_bytes(hdr[0:2], "big")
                        ry = int.from_bytes(hdr[2:4], "big")
                        rw = int.from_bytes(hdr[4:6], "big")
                        rh = int.from_bytes(hdr[6:8], "big")

                        enc_bytes = await self._read_exact(4)
                        if len(enc_bytes) != 4:
                            raise RFBError("short encoding field")
                        encoding = int.from_bytes(enc_bytes, "big", signed=True)

                        if encoding == _ENCODING_RAW:
                            if rx < 0 or ry < 0 or rw <= 0 or rh <= 0:
                                raise RFBError("invalid raw rectangle dimensions")
                            if rx + rw > fw or ry + rh > fh:
                                raise RFBError("raw rectangle outside framebuffer")
                            expected = rw * rh * 4
                            received_bytes += expected
                            if expected > _MAX_IMAGE_BYTES or received_bytes > _MAX_CAPTURE_BYTES:
                                raise RFBError("raw rectangle too large")
                            raw = await self._read_exact(expected)
                            if len(raw) != expected:
                                raise RFBError("short raw rectangle data")
                            tile = Image.frombytes('RGB', (rw, rh), raw, 'raw', 'BGRX')
                            image.paste(tile, (rx, ry))
                            tile.close()
                            uncovered = _subtract_rect(uncovered, (rx, ry, rw, rh))
                            if len(uncovered) > _MAX_COVERAGE_REGIONS:
                                raise RFBError('frame coverage budget exceeded')
                            if time.monotonic() >= deadline:
                                raise RFBError('screenshot deadline exceeded')

                        elif encoding == _ENCODING_DESKTOP_SIZE:
                            # Desktop resize: dimensions are in the rect header
                            if rw < 1 or rw > _MAX_FRAMEBUFFER_DIM:
                                raise RFBError(f"invalid desktop width: {rw}")
                            if rh < 1 or rh > _MAX_FRAMEBUFFER_DIM:
                                raise RFBError(f"invalid desktop height: {rh}")
                            if rw * rh > _MAX_TOTAL_PIXELS:
                                raise RFBError("desktop pixel count exceeds limit")
                            fw, fh = rw, rh
                            self.width, self.height = fw, fh
                            image.close()
                            image = Image.new('RGB', (fw, fh))
                            uncovered = [(0, 0, fw, fh)]
                            # Re-request full frame for new size
                            self._send(
                                bytes([_CLIENT_FB_UPDATE_REQUEST, 0])
                                + (0).to_bytes(2, "big")
                                + (0).to_bytes(2, "big")
                                + fw.to_bytes(2, "big")
                                + fh.to_bytes(2, "big")
                            )

                        elif encoding == _ENCODING_LAST_RECT:
                            break

                        else:
                            raise RFBError(f"unrequested encoding: {encoding}")

                elif mtype == _MSG_SERVER_BELL:
                    # Ignore bell
                    pass

                elif mtype == _MSG_SERVER_CUT_TEXT:
                    _pad = await self._read_exact(3)
                    if len(_pad) != 3:
                        raise RFBError("EOF reading cut text pad")
                    length_bytes = await self._read_exact(4)
                    if len(length_bytes) != 4:
                        raise RFBError("EOF reading cut text length")
                    cut_length = int.from_bytes(length_bytes, "big")
                    if cut_length > _MAX_SERVER_NAME:
                        raise RFBError(f"cut text too long: {cut_length}")
                    if cut_length:
                        discarded = await self._read_exact(cut_length)
                        if len(discarded) != cut_length:
                            raise RFBError("short cut text")
                    # Deliberately not exposed

                else:
                    raise RFBError(f"unknown server message type: {mtype}")

            # Build PIL image and encode as PNG
            import io as _io

            buffer = _io.BytesIO()
            image.save(buffer, format="PNG")
            image.close()
            png_bytes = buffer.getvalue()
            if len(png_bytes) > _MAX_PNG_BYTES:
                raise RFBError(f"PNG output exceeds {_MAX_PNG_BYTES} bytes")
            # Verify decode
            buffer.seek(0)
            with Image.open(buffer) as verify:
                verify.load()
                if verify.size != (fw, fh):
                    raise RFBError("decoded PNG size mismatch")
            if time.monotonic() >= deadline:
                raise RFBError('screenshot deadline exceeded')
            return png_bytes

        try:
            return await asyncio.wait_for(_capture(), timeout=_SCREENSHOT_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise RFBError("screenshot timed out") from exc

    # ----------------------------------------------------------------- input

    def click(self, x: int, y: int, button: str = "left") -> None:
        """Click mouse button at ``(x, y)``.  Synchronous; validates first."""
        self._validate_coord(x, y)
        if button not in _BUTTON_MASKS:
            raise RFBError(f"unknown mouse button: {button!r}")
        mask = _BUTTON_MASKS[button]
        try:
            self._send_pointer_event(mask, x, y)
        finally:
            self._send_pointer_event(0, x, y)

    def scroll(self, x: int, y: int, steps: int) -> None:
        """Scroll at ``(x, y)``.  Positive = down, negative = up."""
        self._validate_coord(x, y)
        if not isinstance(steps, int) or isinstance(steps, bool):
            raise RFBError("scroll steps must be an integer")
        if abs(steps) > _MAX_SCROLL_STEPS:
            raise RFBError(f"scroll steps exceed {_MAX_SCROLL_STEPS}")
        if steps == 0:
            return
        scroll_mask = _SCROLL_DOWN_MASK if steps > 0 else _SCROLL_UP_MASK
        for _ in range(abs(steps)):
            try:
                self._send_pointer_event(scroll_mask, x, y)
            finally:
                self._send_pointer_event(0, x, y)

    def key(self, key: str) -> None:
        """Press and release a key (or key combo).  Synchronous; validates first."""
        events = self._parse_key_events(key)
        pressed: list[int] = []
        try:
            for keysym, down in events:
                if down:
                    pressed.append(keysym)
                self._send_key_event(keysym, down)
                if not down and keysym in pressed:
                    pressed.remove(keysym)
        finally:
            for keysym in reversed(pressed):
                try:
                    self._send_key_event(keysym, False)
                except Exception:
                    pass

    def type_text(self, text: str) -> None:
        """Type ``text`` character by character.  Never logs content."""
        if not isinstance(text, str):
            raise RFBError("type_text expects a string")
        if len(text) > _MAX_TEXT_CHARS:
            raise RFBError(f"type_text exceeds {_MAX_TEXT_CHARS} characters")
        if any(ord(ch) < 32 and ch not in '\n\t' or 0xD800 <= ord(ch) <= 0xDFFF for ch in text):
            raise RFBError('type_text contains an unsupported character')
        for ch in text:
            cp = ord(ch)
            keysym = _XK_RETURN if ch == '\n' else _XK_TAB if ch == '\t' else cp if cp < 0x100 else 0x01000000 | cp
            try:
                self._send_key_event(keysym, True)
            finally:
                self._send_key_event(keysym, False)

    # ----------------------------------------------------------------- private

    def _validate_coord(self, x: int, y: int) -> None:
        if not isinstance(x, int) or isinstance(x, bool):
            raise RFBError("x must be an integer, not bool")
        if not isinstance(y, int) or isinstance(y, bool):
            raise RFBError("y must be an integer, not bool")
        if self.width <= 0 or self.height <= 0:
            raise RFBError("framebuffer dimensions not established")
        if not (0 <= x < self.width):
            raise RFBError(f"x={x} outside framebuffer width={self.width}")
        if not (0 <= y < self.height):
            raise RFBError(f"y={y} outside framebuffer height={self.height}")

    def _parse_key_events(self, key: str) -> list[tuple[int, bool]]:
        """Parse a key string into ``(keysym, down)`` events."""
        if not isinstance(key, str) or not key:
            raise RFBError("key must be a non-empty string")

        if "+" in key:
            parts = key.split("+")
            if len(parts) < 2:
                raise RFBError(f"invalid key combo: {key!r}")
            mod_names = parts[:-1]
            key_name = parts[-1]

            events: list[tuple[int, bool]] = []
            for mod_name in mod_names:
                if mod_name not in _MODIFIER_KEY_SYMS:
                    raise RFBError(f"unknown modifier: {mod_name!r}")
                events.append((_MODIFIER_KEY_SYMS[mod_name], True))

            if key_name in _KEY_SYMS:
                ks = _KEY_SYMS[key_name]
                events.append((ks, True))
                events.append((ks, False))
            elif key_name in ("Left", "Right"):
                # Combo shorthand: Alt+Left/Right
                ks = _XK_LEFT if key_name == "Left" else _XK_RIGHT
                events.append((ks, True))
                events.append((ks, False))
            else:
                if len(key_name) != 1:
                    raise RFBError(f"unknown key in combo: {key_name!r}")
                cp = ord(key_name)
                ks = cp if cp < 0x100 else 0x01000000 | cp
                events.append((ks, True))
                events.append((ks, False))

            for mod_name in reversed(mod_names):
                events.append((_MODIFIER_KEY_SYMS[mod_name], False))
            return events

        if key in _KEY_SYMS:
            ks = _KEY_SYMS[key]
            return [(ks, True), (ks, False)]

        if len(key) == 1:
            cp = ord(key)
            ks = cp if cp < 0x100 else 0x01000000 | cp
            return [(ks, True), (ks, False)]

        raise RFBError(f"unknown key: {key!r}")

    def _send_key_event(self, keysym: int, down: bool) -> None:
        payload = bytes(
            [_CLIENT_KEY_EVENT, 1 if down else 0, 0, 0]
        ) + keysym.to_bytes(4, "big")
        self._send(payload)

    def _send_pointer_event(self, mask: int, x: int, y: int) -> None:
        payload = bytes([_CLIENT_POINTER_EVENT, mask]) + x.to_bytes(2, "big") + y.to_bytes(2, "big")
        self._send(payload)


# ------------------------------------------------------------------ helpers

def _blit_bgrx(
    buf: bytearray,
    stride_px: int,
    x: int,
    y: int,
    w: int,
    h: int,
    raw: bytes,
) -> None:
    """Copy a BGRX raw rectangle into an RGB buffer at ``(x, y)``."""
    mv = memoryview(raw)
    row_size = w * 4
    for row in range(h):
        src_off = row * row_size
        dst_row = y + row
        dst_base = (dst_row * stride_px + x) * 3
        for col in range(w):
            s = src_off + col * 4
            d = dst_base + col * 3
            buf[d] = mv[s + 2]     # R
            buf[d + 1] = mv[s + 1]  # G
            buf[d + 2] = mv[s]     # B


def _subtract_rect(
    uncovered: list[tuple[int, int, int, int]],
    rect: tuple[int, int, int, int],
) -> list[tuple[int, int, int, int]]:
    """Return ``uncovered`` minus ``rect`` (2D rectangle subtraction)."""
    rx, ry, rw, rh = rect
    rex = rx + rw
    rey = ry + rh
    result: list[tuple[int, int, int, int]] = []
    for ux, uy, uw, uh in uncovered:
        uex = ux + uw
        uey = uy + uh
        # No overlap
        if rx >= uex or rex <= ux or ry >= uey or rey <= uy:
            result.append((ux, uy, uw, uh))
            continue
        # Left strip
        if rx > ux:
            result.append((ux, uy, rx - ux, uh))
        # Right strip
        if rex < uex:
            result.append((rex, uy, uex - rex, uh))
        # Middle band x-range
        mid_x = max(ux, rx)
        mid_w = min(uex, rex) - mid_x
        # Top strip
        if ry > uy:
            result.append((mid_x, uy, mid_w, ry - uy))
        # Bottom strip
        if rey < uey:
            result.append((mid_x, rey, mid_w, uey - rey))
    return result
