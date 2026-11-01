"""Read-only recognition of the native app-connection card on one candidate.

The legacy frontend reads tool-consent rows from candidate field34. Its row
field2 contains the native consent state (field1) and tool name (field3), while
row field3 supplies the app display name (field2). Native connection/OAuth is
a separate UI flow, not a userQuery confirmation action. No executable reply,
provider URL, tool identity or arbitrary card body is returned here.
"""

from __future__ import annotations

from typing import Any


def _field(value: list[Any], index: int) -> Any:
    return value[index] if index < len(value) else None


def _text(value: Any, maximum: int) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(character) < 32 for character in value))


def parse_app_setup_card(candidate: list[Any]) -> dict[str, Any]:
    """Recognize only the observed pending card; leave other consent states unknown.

    Presence of native tool-consent content prevents a card from becoming an
    answer, even when its controls or identity have changed. Only the exact
    supported pending shape receives the app-connection classification.
    """
    rows = _field(candidate, 33)
    result: dict[str, Any] = {"setup_card_present": rows is not None and rows != [],
                              "setup_required": False}
    completion = _field(candidate, 8)
    if (not isinstance(completion, list) or not completion or type(completion[0]) is not int
            or completion[0] != 2 or not isinstance(rows, list) or len(rows) != 1):
        return result
    row = rows[0]
    if not isinstance(row, list):
        return result
    consent = _field(row, 1)
    metadata = _field(row, 2)
    if (not isinstance(consent, list) or len(consent) != 3
            or type(consent[0]) is not int or consent[0] != 1 or consent[1] is not None
            or not _text(consent[2], 256) or _field(row, 0) != consent[2]
            or not isinstance(metadata, list) or not _text(_field(metadata, 1), 160)
            or not _text(_field(metadata, 3), 4096)):
        return result
    # The renderer only needs the display name and icon for this branch. Keep
    # optional/opaque setup details private; they may include account-linking
    # providers or credentials and are never an executable confirmation.
    result.update(setup_required=True, setup_kind="app_connection", setup_app_name=metadata[1])
    return result
