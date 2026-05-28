"""Minimal translation engine.

Usage in widgets:
    from core.i18n import _
    btn = QPushButton(_("Add Card"))

Call setup() once at startup before constructing any widget:
    from core import i18n
    i18n.setup("de")
"""
from __future__ import annotations

import importlib
from typing import Optional

_lang: str = "en"
_table: dict[str, str] = {}

# (code, display name) — "en" is always available as the fallback
_BUILTIN: list[tuple[str, str]] = [
    ("en", "English"),
    ("de", "Deutsch"),
]


def setup(lang: str) -> None:
    """Load the translation table for *lang*.  Falls back silently to English."""
    global _lang, _table
    _lang = lang
    if lang == "en":
        _table = {}
        return
    try:
        mod = importlib.import_module(f"core.translations.{lang}")
        _table = getattr(mod, "STRINGS", {})
    except (ImportError, AttributeError):
        _table = {}


def _(text: str) -> str:  # noqa: A001
    return _table.get(text, text)


def get_lang() -> str:
    return _lang


def get_available() -> list[tuple[str, str]]:
    """Return [(code, display_name)] for all known languages."""
    available = list(_BUILTIN)
    # also discover any extra translation modules in core/translations/
    try:
        import pkgutil, core.translations as _pkg
        for info in pkgutil.iter_modules(_pkg.__path__):
            code = info.name
            if not any(c == code for c, _ in available):
                available.append((code, code.upper()))
    except Exception:
        pass
    return available
