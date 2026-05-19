"""Application configuration stored in config.json (project root)."""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"

# These types are always present and cannot be removed by the user.
BUILTIN_TYPES: list[str] = ["binder", "box", "deck", "commander", "overcount"]

_DEFAULTS: dict = {
    "container_types": list(BUILTIN_TYPES),
    "overcount_excluded_types": [],
    "backup_dir": "",
}


def load() -> dict:
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            merged = {**_DEFAULTS, **data}
            # Ensure all builtin types are always present.  Missing ones are
            # spliced in at the position they occupy in BUILTIN_TYPES relative
            # to their neighbours so the combo-box order stays logical.
            current: list[str] = list(merged.get("container_types", []))
            for idx, t in enumerate(BUILTIN_TYPES):
                if t not in current:
                    # Insert after the last builtin predecessor that exists.
                    insert_after = next(
                        (current.index(p) for p in reversed(BUILTIN_TYPES[:idx]) if p in current),
                        -1,
                    )
                    current.insert(insert_after + 1, t)
            merged["container_types"] = current
            return merged
        except Exception:
            pass
    return dict(_DEFAULTS)


def save(config: dict) -> None:
    _CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
