"""Mirror the current native video companion's format field without guessing defaults."""

from copy import deepcopy

import pytest

from src.infrastructure.rpc_contracts import (
    VIDEO_FORMAT_CODES,
    VideoTransportChanged,
    apply_video_generation_transport,
)


def _sdk_video_request():
    request = [None] * 56
    request[0] = ["A synthetic scene", None, None, None, None, None, None]
    request[3] = "!synthetic-nonce"
    request[4] = "synthetic-context"
    request[49] = 1
    request[54] = [[[[[1]]]]]
    request[55] = [[1]]
    return request


def test_native_video_format_67_writes_observed_gvd_field_five():
    # Official frontend hve[67]=2 -> ive() -> og(gvd,5,2), in addition
    # to the separate selected companion IDs in query field56.
    assert VIDEO_FORMAT_CODES == {67: 2}
    request = _sdk_video_request()
    apply_video_generation_transport(request, duration_seconds=15, aspect_ratio="9:16",
                                     format_code=VIDEO_FORMAT_CODES[67])
    assert request[49] == 11
    assert request[0][9] == [None] * 6 + [[[None, None, 2, 2, 2]]]
    assert request[0][9][6][0][4] == 2
    assert request[54] == []


def test_omitted_video_format_preserves_the_legacy_payload_shape():
    request = _sdk_video_request()
    apply_video_generation_transport(request)
    assert request[0][9] == [None] * 6 + [[[None, None, None, 1]]]
    assert len(request[0][9][6][0]) == 4


@pytest.mark.parametrize("invalid", [False, True, 0, 1, 3, 67, 2.0, "2", [], {}])
def test_invalid_format_is_rejected_before_mutating_the_request(invalid):
    request = _sdk_video_request()
    original = deepcopy(request)
    with pytest.raises(ValueError, match="format_code"):
        apply_video_generation_transport(request, format_code=invalid)
    assert request == original


@pytest.mark.parametrize("changed", ["envelope", "content", "nonce", "context", "mode", "companion", "existing_options"])
def test_changed_video_shape_is_rejected_without_partial_format_mutation(changed):
    request = _sdk_video_request()
    if changed == "envelope":
        request = request[:55]
    elif changed == "content":
        request[0] = request[0][:6]
    elif changed == "nonce":
        request[3] = None
    elif changed == "context":
        request[4] = ""
    elif changed == "mode":
        request[49] = 14
    elif changed == "companion":
        request[55] = [[13, 16]]
    elif changed == "existing_options":
        request[0] += [None, None, ["unrecognized-options"]]
    original = deepcopy(request)
    with pytest.raises(VideoTransportChanged):
        apply_video_generation_transport(request, format_code=2)
    assert request == original
