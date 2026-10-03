"""Small decodable media fixtures for offline artifact contracts."""

import io
import struct
import wave
from pathlib import Path

from PIL import Image

from src.domain import CleanupObservation, CleanupState
from src.services.artifacts import response_chat_id


async def fake_finalize_generated_cleanup(
    response,
    *,
    owns_chat,
    retain_chat=False,
    preserve_for_recovery=False,
    delete_after_seconds=None,
    source="",
    client=None,
):
    """Observe the adapter's decision without any account or timer access."""
    cid = response_chat_id(response)
    if cid is None:
        state = CleanupState.NOT_APPLICABLE
    elif not owns_chat or retain_chat or preserve_for_recovery:
        state = CleanupState.RETAINED
    elif delete_after_seconds is not None and delete_after_seconds > 0:
        state = CleanupState.PENDING
    else:
        state = CleanupState.COMPLETED
    return CleanupObservation(state=state, upstream_chat_id=cid, source=source)


def write_image(path: Path, *, format: str = "PNG", size: tuple[int, int] = (2, 1)) -> Path:
    Image.new("RGB", size, (32, 64, 96)).save(path, format=format)
    return path


def write_audio(path: Path, *, duration: float = 1.0) -> Path:
    """Write actual PCM WAV bytes, independent of the caller's filename suffix."""
    sample_rate = 8000
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00\x00" * round(duration * sample_rate))
    return path


def write_video(path: Path) -> Path:
    """Synthesize a one-frame, 16x16 MJPEG AVI with a valid JPEG payload."""
    frame = io.BytesIO()
    Image.new("RGB", (16, 16), (32, 64, 96)).save(frame, format="JPEG")
    jpeg = frame.getvalue()

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return kind + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")

    main_header = struct.pack("<14I", 1_000_000, len(jpeg), 0, 0x10, 1, 0, 1, len(jpeg), 16, 16, 0, 0, 0, 0)
    stream_header = struct.pack("<4s4sIHH8I4h", b"vids", b"MJPG", 0, 0, 0, 0, 1, 1, 0, 1, len(jpeg), 0xFFFFFFFF, 0, 0, 0, 16, 16)
    bitmap_header = struct.pack("<IiiHH4sIiiII", 40, 16, 16, 1, 24, b"MJPG", len(jpeg), 0, 0, 0, 0)
    streams = chunk(b"LIST", b"strl" + chunk(b"strh", stream_header) + chunk(b"strf", bitmap_header))
    header = chunk(b"LIST", b"hdrl" + chunk(b"avih", main_header) + streams)
    frames = chunk(b"LIST", b"movi" + chunk(b"00dc", jpeg))
    index = chunk(b"idx1", struct.pack("<4sIII", b"00dc", 0x10, 4, len(jpeg)))
    path.write_bytes(chunk(b"RIFF", b"AVI " + header + frames + index))
    return path
