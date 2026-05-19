"""Input sanitization for user-provided strings."""
from __future__ import annotations
import re

_CONTROL_RE  = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_FTS_SPECIAL = re.compile(r'["\(\)\*\^\:]')
_FTS_OPS_RE  = re.compile(r'\b(AND|OR|NOT)\b', re.IGNORECASE)


def sanitize_text(s: str, max_len: int = 500) -> str:
    """Strip control characters, collapse whitespace, truncate."""
    if not isinstance(s, str):
        return ""
    s = _CONTROL_RE.sub("", s)
    s = " ".join(s.split())
    return s[:max_len]


def sanitize_name(s: str) -> str:
    """Clean a card or container name (max 200 chars)."""
    return sanitize_text(s, max_len=200)


def sanitize_set_code(s: str) -> str:
    """Normalise and validate a Scryfall set code. Returns '' if invalid."""
    s = s.strip().upper()
    return s if re.fullmatch(r'[A-Z0-9]{2,10}', s) else ""


def sanitize_fts_query(q: str) -> str:
    """Strip FTS5 syntax so user input cannot break SQLite FTS5 queries.

    FTS5 raises errors on unbalanced quotes, bare boolean operators,
    column filters (colon), and boost expressions (caret).
    We strip all of these and return a plain token search.
    """
    q = _FTS_SPECIAL.sub(" ", q.strip())
    q = _FTS_OPS_RE.sub(" ", q)
    return " ".join(q.split())
