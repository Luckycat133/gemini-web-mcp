"""Pure parsing of current Gemini Web media companion choices.

Observed in the official 2026-11-21 Web bundle ``c3ZkJFn_XPs.2018.O``:
GetUserStatus field20 supplies companion groups, field18 capabilities, and
``qkd``/``tkd`` map option IDs, defaults and capability requirements. The
``ubn`` composer filter applies groups with no feature mode to every mode.
Only provider-declared defaults are selected here. An absent default is not
permission to substitute the first available choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping


MEDIA_FEATURE_MODES = frozenset({11, 14, 21})
MediaCompanionStatus = Literal["success", "empty", "rejected", "changed_shape"]


@dataclass(frozen=True, slots=True)
class MediaCompanionOption:
    id: int
    title: str
    is_default: bool
    available: bool
    required_capability: int | None = None
    subtitle: str | None = None


@dataclass(frozen=True, slots=True)
class MediaCompanion:
    id: int
    mode: int
    title: str
    selection_mode: int
    available: bool
    options: tuple[MediaCompanionOption, ...]
    required_capability: int | None = None

    @property
    def default_option_ids(self) -> tuple[int, ...]:
        return tuple(option.id for option in self.options if option.is_default and option.available)


@dataclass(frozen=True, slots=True)
class MediaCompanionCatalog:
    by_mode: Mapping[int, tuple[MediaCompanion, ...]]
    capabilities: frozenset[int]

    def default_option_ids(self, mode: int) -> tuple[int, ...]:
        """Return confirmed available defaults, never guess a missing choice."""
        return tuple(dict.fromkeys(
            option_id
            for group in self.by_mode.get(mode, ())
            for option_id in group.default_option_ids
        ))


@dataclass(frozen=True, slots=True)
class MediaCompanionParseResult:
    status: MediaCompanionStatus
    value: MediaCompanionCatalog | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)
    reject_code: int | None = None


def _at(value: list[Any], index: int) -> Any:
    return value[index] if len(value) > index else None


def _integer(value: Any, *, positive: bool = False) -> int:
    if type(value) is not int or value < int(positive):
        raise ValueError("Invalid companion integer")
    return value


def _optional_integer(value: Any) -> int | None:
    return _integer(value) or None if value is not None else None


def _text(value: Any, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid companion title")
    return value


def _parse_group(row: Any, capabilities: frozenset[int]) -> MediaCompanion | None:
    if not isinstance(row, list):
        raise ValueError("Invalid companion group")
    mode = _optional_integer(_at(row, 0)) or 0
    if mode not in MEDIA_FEATURE_MODES and mode != 0:
        return None
    required = _optional_integer(_at(row, 1))
    available = required is None or required in capabilities
    choices = _at(row, 7)
    if not isinstance(choices, list):
        raise ValueError("Invalid companion choices")
    group_id = _integer(_at(row, 8), positive=True)
    selection_mode = _optional_integer(_at(row, 2)) or 0
    title = _text(_at(row, 3), optional=True) or ""
    options = []
    option_ids: set[int] = set()
    for choice in choices:
        if not isinstance(choice, list):
            raise ValueError("Invalid companion option")
        option_id = _integer(_at(choice, 0), positive=True)
        if option_id in option_ids:
            raise ValueError("Duplicate companion option")
        option_ids.add(option_id)
        option_required = _optional_integer(_at(choice, 1))
        default = _at(choice, 2)
        if default is not None and type(default) is not bool:
            raise ValueError("Invalid companion default")
        option_title = _text(_at(choice, 3))
        assert option_title is not None
        options.append(MediaCompanionOption(
            id=option_id, title=option_title, is_default=default is True,
            available=available and (option_required is None or option_required in capabilities),
            required_capability=option_required, subtitle=_text(_at(choice, 4), optional=True),
        ))
    if selection_mode == 1 and sum(option.is_default and option.available for option in options) > 1:
        raise ValueError("Conflicting single-select companion defaults")
    return MediaCompanion(group_id, mode, title, selection_mode, available, tuple(options), required)


def parse_media_companions(body: Any, *, reject_code: int | None = None) -> MediaCompanionParseResult:
    """Parse decoded GetUserStatus media options without retaining account data."""
    if reject_code is not None:
        return MediaCompanionParseResult("rejected", reject_code=reject_code)
    if not isinstance(body, list) or len(body) <= 19:
        return MediaCompanionParseResult("changed_shape")
    status = _at(body, 14)
    if status is not None:
        if type(status) is not int:
            return MediaCompanionParseResult("changed_shape")
        if status != 1000:
            return MediaCompanionParseResult("rejected", reject_code=status)
    raw_capabilities = _at(body, 17)
    if raw_capabilities is None:
        raw_capabilities = []
    rows = body[19]
    if rows is None:
        rows = []
    if not isinstance(raw_capabilities, list) or not isinstance(rows, list):
        return MediaCompanionParseResult("changed_shape")
    try:
        # Current authenticated catalogs include enum zero (UNSPECIFIED). It
        # carries no permission, but must not invalidate the entire response.
        capabilities = frozenset(_integer(value) for value in raw_capabilities)
        groups: dict[int, list[MediaCompanion]] = {mode: [] for mode in sorted(MEDIA_FEATURE_MODES)}
        seen: set[tuple[int, int]] = set()
        warnings = []
        for row in rows:
            group = _parse_group(row, capabilities)
            if group is None:
                continue
            identity = (group.mode, group.id)
            if identity in seen:
                raise ValueError("Duplicate companion group")
            seen.add(identity)
            modes = MEDIA_FEATURE_MODES if group.mode == 0 else (group.mode,)
            for mode in modes:
                groups[mode].append(group)
                if group.available and any(option.available for option in group.options) and not group.default_option_ids:
                    warnings.append(f"media_companion_default_unconfirmed:{mode}:{group.id}")
        mapping = MappingProxyType({mode: tuple(entries) for mode, entries in groups.items()})
        catalog = MediaCompanionCatalog(mapping, capabilities)
        nonempty = any(option.available for entries in groups.values() for group in entries for option in group.options)
        return MediaCompanionParseResult("success" if nonempty else "empty", catalog, tuple(warnings))
    except (TypeError, ValueError):
        return MediaCompanionParseResult("changed_shape")
