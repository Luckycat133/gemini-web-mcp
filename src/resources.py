"""Access immutable package data through ``importlib.resources``."""

from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable


def default_prompts_resource() -> Traversable:
    """Return the packaged default prompt catalog without assuming a filesystem path."""

    return files("src").joinpath("data", "prompts_default.json")


def read_default_prompts() -> str:
    """Read the packaged default prompt catalog as UTF-8 JSON text."""

    return default_prompts_resource().read_text(encoding="utf-8")
