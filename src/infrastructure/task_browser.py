"""Pure extraction of private Task browser signaling identity."""

from __future__ import annotations

from typing import Any


def browser_identity(observed: dict[str, Any]) -> list[str]:
    """Extract a verified turn's opaque identity; never expose it to users."""
    if not isinstance(observed, dict):
        raise ValueError('Task browser identity is unavailable.')
    candidate = observed.get('candidate')
    cid, rid = observed.get('chat_id'), observed.get('response_id')
    if (not isinstance(candidate, list) or len(candidate) <= 12
            or not isinstance(candidate[0], str) or not candidate[0].startswith('rc_')
            or not candidate[0][3:].strip() or len(candidate[0]) > 256
            or not isinstance(cid, str) or not cid.startswith('c_') or not cid[2:].strip() or len(cid) > 256
            or not isinstance(rid, str) or not rid.startswith('r_') or not rid[2:].strip() or len(rid) > 256):
        raise ValueError('Task browser identity is unavailable.')
    requested_candidate = observed.get('candidate_id')
    if requested_candidate is not None and (
        not isinstance(requested_candidate, str) or not requested_candidate.strip()
        or len(requested_candidate) > 256 or candidate[0] != requested_candidate
    ):
        raise ValueError('Task browser identity is unavailable.')
    rich = candidate[12]
    metadata = rich[0].get('62') if isinstance(rich, list) and rich and isinstance(rich[0], dict) else None
    if (not isinstance(metadata, list) or len(metadata) <= 11
            or not isinstance(metadata[4], str) or not metadata[4].startswith('projects/')
            or not metadata[4][9:].strip() or len(metadata[4]) > 2048
            or not isinstance(metadata[5], str) or not metadata[5].strip() or len(metadata[5]) > 2048
            or not isinstance(metadata[11], str) or not metadata[11].strip() or len(metadata[11]) > 2048):
        raise ValueError('Task browser identity is unavailable.')
    return [metadata[4], metadata[5], cid, rid, metadata[11]]
