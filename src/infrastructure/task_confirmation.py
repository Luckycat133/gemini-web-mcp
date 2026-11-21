"""Pure replies for an exact native Remy confirmation card.

Only the observed userQuery branch is executable. Native choice identities stay
in transient private metadata; callers select an opaque, card-bound option.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_MAX_CHOICES = 20
_MAX_BUTTONS = 50


def _field(value: list[Any], index: int) -> Any:
    return value[index] if index < len(value) else None


def _text(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _query_button(button: Any) -> tuple[str, str] | None:
    if not isinstance(button, list) or not _text(_field(button, 0), 512):
        raise ValueError("Unsupported confirmation button.")
    disabled = _field(button, 2)
    if disabled is not None and type(disabled) not in (bool, int):
        raise ValueError("Invalid confirmation button state.")
    if disabled not in (None, False, True, 0, 1):
        raise ValueError("Unknown confirmation button state.")
    if disabled:
        return None
    # Field3 is the public legacy disabled getter; field6 is its icon. No
    # legacy hidden-field mapping is proved, so unknown controls fail closed.
    if any(_present(value) for index, value in enumerate(button[3:], 3) if index != 5):
        raise ValueError("Unsupported confirmation button controls.")
    icon = _field(button, 5)
    if icon is not None and not isinstance(icon, str):
        raise ValueError("Invalid confirmation icon.")
    action = _field(button, 1)
    if not isinstance(action, list) or not _text(_field(action, 3), 8192):
        raise ValueError("Unsupported confirmation action.")
    if any(_present(value) for index, value in enumerate(action) if index != 3):
        raise ValueError("Unsupported confirmation action oneof.")
    return button[0], action[3]


def parse_confirmation_replies(card: list[Any], *, chat_id: str, response_id: str,
                               candidate_id: str) -> dict[str, Any]:
    """Return public options plus private reply context, never an inferred grant."""
    public: list[dict[str, str]] = []
    replies: list[dict[str, Any]] = []
    choices = _field(card, 13)
    context: dict[str, Any] = {"requires_option_id": bool(choices), "replies": replies}
    result = {"input_options": public, "input_card": context}
    try:
        if _present(_field(card, 14)):
            raise ValueError("The confirmation card already has a selection.")
        if _present(_field(card, 18)):
            raise ValueError("Subagent confirmation requires a native handoff.")
        if _present(_field(card, 19)):
            raise ValueError("Agent authentication requires a native handoff.")
        if _present(_field(card, 3)) or _present(_field(card, 4)):
            raise ValueError("Unsupported legacy confirmation button groups.")
        if _present(_field(card, 16)) or any(_present(value) for value in card[20:]):
            raise ValueError("Unsupported confirmation context fields.")
        encoded = json.dumps(card, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded) > 1_000_000:
            raise ValueError("The confirmation card exceeds the supported bound.")
        fingerprint = hashlib.sha256(json.dumps(
            [chat_id, response_id, candidate_id, encoded], ensure_ascii=False,
        ).encode()).hexdigest()
        variants: list[tuple[str | None, str | None, list[Any] | None]] = [(None, None, None)]
        if choices is not None and not isinstance(choices, list):
            raise ValueError("Invalid confirmation choices.")
        if choices:
            if len(choices) > _MAX_CHOICES:
                raise ValueError("Too many confirmation choices.")
            variants = []
            seen: set[str] = set()
            for choice in choices:
                if (not isinstance(choice, list) or not _text(_field(choice, 0), 512)
                        or not _text(_field(choice, 1), 512) or choice[0] in seen):
                    raise ValueError("Ambiguous confirmation choice identity.")
                if any(_present(value) for value in choice[6:]):
                    raise ValueError("Unsupported confirmation choice fields.")
                override = _field(choice, 3)
                if override is not None and not isinstance(override, list):
                    raise ValueError("Invalid confirmation choice override.")
                if isinstance(override, list) and any(_present(value) for value in override[8:]):
                    raise ValueError("Unsupported confirmation override fields.")
                seen.add(choice[0])
                variants.append((choice[0], choice[1], override))
        for choice_id, choice_label, override in variants:
            for slot in (6, 7, 8):
                buttons = _field(card, slot)
                if override is not None:
                    replacement = _field(override, slot - 1)
                    if replacement is not None and not isinstance(replacement, list):
                        raise ValueError("Invalid confirmation action override.")
                    if replacement:
                        buttons = replacement
                if buttons is None:
                    continue
                if not isinstance(buttons, list) or len(buttons) > _MAX_BUTTONS:
                    raise ValueError("Invalid confirmation action group.")
                for index, button in enumerate(buttons):
                    parsed = _query_button(button)
                    if parsed is None:
                        continue
                    label, query = parsed
                    material = json.dumps([fingerprint, choice_id, slot, index], ensure_ascii=False)
                    option_id = "option_" + hashlib.sha256(material.encode()).hexdigest()[:32]
                    option = {"label": label, "prompt": query, "input_option_id": option_id}
                    if choice_label is not None:
                        option["choice_label"] = choice_label
                    public.append(option)
                    replies.append({"input_option_id": option_id, "prompt": query,
                                    "action_choice_id": choice_id, "subagent_conversation_id": None,
                                    "action_path": [slot, index]})
                    if len(public) > _MAX_CHOICES * _MAX_BUTTONS:
                        raise ValueError("Too many confirmation actions.")
    except (ValueError, TypeError, OverflowError):
        # A partial interpretation must not authorize any branch of this card.
        public.clear()
        replies.clear()
        context["unsupported"] = True
    return result
