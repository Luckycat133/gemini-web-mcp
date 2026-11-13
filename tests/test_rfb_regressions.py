"""Synthetic protocol regressions; no remote desktop is contacted."""
import asyncio
import struct

from src.infrastructure.rfb import RFBClient


def queue_reader(data):
    remaining = bytearray(data)
    async def read(n):
        result = bytes(remaining[:n])
        del remaining[:n]
        return result
    return read


def update():
    return b'\x00\x00\x00\x01' + struct.pack('>HHHHi', 0, 0, 1, 1, 0) + b'\x00\x00\xff\x00'


def test_pixel_format_uses_8_bit_maxima():
    init = struct.pack('>HH', 1, 1) + bytes(16) + struct.pack('>I', 0)
    sent = []
    client = RFBClient(queue_reader(b'RFB 003.008\n\x01\x01\x00\x00\x00\x00' + init), sent.append)
    asyncio.run(client.initialize())
    message = sent[3]
    assert struct.unpack('>HHH', message[8:14]) == (255, 255, 255)


def test_screenshot_requests_nonincremental_frame_before_reading():
    sent = []
    async def read(n):
        assert sent and sent[0] == struct.pack('>BBHHHH', 3, 0, 0, 0, 1, 1)
        return await queue(n)
    queue = queue_reader(update())
    client = RFBClient(read, sent.append)
    client.width = client.height = 1
    assert asyncio.run(client.screenshot_png()).startswith(b'\x89PNG')


def test_vertical_scroll_uses_standard_wheel_masks():
    sent = []
    client = RFBClient(queue_reader(b''), sent.append)
    client.width = client.height = 1
    client.scroll(0, 0, 1)
    client.scroll(0, 0, -1)
    assert [item[1] for item in sent] == [0x10, 0, 0x08, 0]


def test_server_cut_text_three_byte_padding_preserves_stream():
    sent = []
    cut = b'\x03\x00\x00\x00' + struct.pack('>I', 2) + b'hi'
    client = RFBClient(queue_reader(cut + update()), sent.append)
    client.width = client.height = 1
    assert asyncio.run(client.screenshot_png()).startswith(b'\x89PNG')


def test_type_tab_uses_x11_tab_keysym():
    sent = []
    client = RFBClient(queue_reader(b''), sent.append)
    client.type_text('\t')
    assert [int.from_bytes(item[4:8], 'big') for item in sent] == [0xff09, 0xff09]


def test_key_success_does_not_emit_duplicate_key_releases():
    sent = []
    client = RFBClient(queue_reader(b''), sent.append)
    client.key('Ctrl+l')
    assert [(item[1], int.from_bytes(item[4:8], 'big')) for item in sent] == [
        (1, 0xffe3), (1, ord('l')), (0, ord('l')), (0, 0xffe3),
    ]


def test_capture_rejects_cumulative_overlapping_raw_budget(monkeypatch):
    import pytest
    from src.infrastructure import rfb

    monkeypatch.setattr(rfb, '_MAX_CAPTURE_BYTES', 4)
    header = b'\x00\x00\x00\x02'
    rectangle = struct.pack('>HHHHi', 0, 0, 1, 1, 0) + b'\x00\x00\xff\x00'
    client = RFBClient(queue_reader(header + rectangle + rectangle), lambda _: None)
    client.width, client.height = 2, 1
    with pytest.raises(rfb.RFBError, match='large|budget'):
        asyncio.run(client.screenshot_png())


def test_lastrect_sentinel_terminates_before_following_update():
    first = b'\x00\x00\xff\xff' + struct.pack('>HHHHi', 0, 0, 0, 0, -224)
    client = RFBClient(queue_reader(first + update()), lambda _: None)
    client.width = client.height = 1
    assert asyncio.run(client.screenshot_png()).startswith(b'\x89PNG')


def test_modifier_released_after_interrupted_key_press():
    import pytest

    events = []
    def send(data):
        events.append((data[1], int.from_bytes(data[4:8], 'big')))
        if len(events) == 2:
            raise OSError('synthetic interruption')
    client = RFBClient(queue_reader(b''), send)
    with pytest.raises(OSError):
        client.key('Ctrl+l')
    assert events[-2:] == [(0, ord('l')), (0, 0xffe3)]


def test_mouse_button_released_after_interrupted_press():
    import pytest

    events = []
    def send(data):
        events.append(data)
        if len(events) == 1:
            raise OSError('synthetic interruption')
    client = RFBClient(queue_reader(b''), send)
    client.width = client.height = 1
    with pytest.raises(OSError):
        client.click(0, 0)
    assert [event[1] for event in events] == [1, 0]


def test_typed_key_released_after_interrupted_press():
    import pytest

    events = []
    def send(data):
        events.append(data)
        if len(events) == 1:
            raise OSError('synthetic interruption')
    client = RFBClient(queue_reader(b''), send)
    with pytest.raises(OSError):
        client.type_text('a')
    assert [event[1] for event in events] == [1, 0]


def test_invalid_full_text_is_rejected_before_any_key():
    import pytest
    from src.infrastructure.rfb import RFBError

    events = []
    client = RFBClient(queue_reader(b''), events.append)
    with pytest.raises(RFBError):
        client.type_text('a\x01')
    assert not events
