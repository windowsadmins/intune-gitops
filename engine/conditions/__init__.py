"""Condition translators, one per client, side by side.

    from conditions import get_translator
    translator = get_translator("windows")   # Cimian -> windows10AndLater
    translator = get_translator("macos")     # Munki  -> macOS

The platform comes from --platform on each stage, or INTUNE_PLATFORM.
"""

from __future__ import annotations

import os

from .common import (  # noqa: F401
    CondParseError,
    Translator,
    UnsupportedCondition,
    combine_conditions,
    forbidden_operator,
    normalize_condition,
)

PLATFORMS = ("windows", "macos")


def get_translator(platform: str | None = None) -> Translator:
    platform = (platform or os.getenv("INTUNE_PLATFORM", "")).strip().lower()
    if platform == "windows":
        from . import windows

        return windows.TRANSLATOR
    if platform == "macos":
        from . import macos

        return macos.TRANSLATOR
    raise ValueError(
        f"unknown platform {platform!r}: pass --platform windows|macos or set INTUNE_PLATFORM"
    )
