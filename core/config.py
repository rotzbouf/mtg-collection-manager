"""Application configuration — single source of truth in config.json.

All settings (Discord token, channel IDs, roles, UI port, …) live in
config.json.  Server and cog modules read them via os.getenv() after
calling inject_env() at startup.  Real environment variables (Docker /
CI / systemd EnvironmentFile) always win over config.json values.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _config_dir() -> Path:
    """Return the writable directory where config.json lives.

    PyInstaller bundle: directory alongside the executable (user-writable).
    Source / venv:      project root.
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def _seed_bundled_config(path: Path) -> None:
    """Copy the bundled default config.json next to the exe on first run.

    PyInstaller: files extracted to sys._MEIPASS.
    Nuitka onefile: files extracted relative to __file__ (no _MEIPASS).
    """
    if path.exists():
        return
    # PyInstaller provides _MEIPASS; Nuitka uses __file__-relative extraction dir.
    extract_root = Path(getattr(sys, '_MEIPASS', None) or Path(__file__).parent.parent)
    bundled = extract_root / "config.json"
    if bundled.exists():
        import shutil
        shutil.copy(bundled, path)


_base        = _config_dir()
_CONFIG_PATH = _base / "config.json"

# Exported: all user-data paths (db, images, logs, backups) are resolved here.
DATA_DIR: Path = _base

if getattr(sys, 'frozen', False):
    _seed_bundled_config(_CONFIG_PATH)

BUILTIN_TYPES: list[str] = ["binder", "box", "deck", "commander", "overcount"]

_DEFAULTS: dict = {
    "discord": {
        "token":               "",
        "guild_id":            "",
        "scan_channel_id":     "",
        "showcase_channel_id": "",
        "guest_role":          "",
        "collector_role":      "",
        "admin_role":          "",
    },
    "app": {
        "backup_dir":         "",
        "ui_port":            8080,
        "ui_host":            "127.0.0.1",
        "debug_scan_preview": False,
    },
    "container_types":          list(BUILTIN_TYPES),
    "overcount_excluded_types": [],
    "buylist_sources":          [],
    # Per-domain login credentials for protected buylist pages.
    # Each entry: {domain, username, password, login_url}
    # Stored in plaintext — this is intentional for a local desktop app.
    "store_credentials":        [],
    "brave": {
        "api_key":     "",
        "keywords":    [
            # German
            "MTG Karten Ankauf Buylist",
            "Magic the Gathering Ankauf Buylist",
            "MTG Karten Ankauf Liste",
            "Magic Karten verkaufen Ankauf",
            "MTG Ankauf Preisliste",
            # English
            "MTG buylist",
            "Magic the Gathering buylist",
            "MTG singles buylist",
            "Magic cards buylist store",
            "MTG we buy singles",
            "Magic the Gathering buy list",
        ],
        "max_results": 15,
    },
}

# config.json path → environment variable name
_ENV_MAP: list[tuple[str, str, str]] = [
    ("discord", "token",               "DISCORD_TOKEN"),
    ("discord", "guild_id",            "DISCORD_GUILD_ID"),
    ("discord", "scan_channel_id",     "DISCORD_SCAN_CHANNEL_ID"),
    ("discord", "showcase_channel_id", "DISCORD_SHOWCASE_CHANNEL_ID"),
    ("discord", "guest_role",          "DISCORD_GUEST_ROLE"),
    ("discord", "collector_role",      "DISCORD_COLLECTOR_ROLE"),
    ("discord", "admin_role",          "DISCORD_ADMIN_ROLE"),
    ("app",     "backup_dir",          "BACKUP_DIR"),
    ("app",     "ui_port",             "UI_PORT"),
    ("app",     "ui_host",             "UI_HOST"),
    ("app",     "debug_scan_preview",  "DEBUG_SCAN_PREVIEW"),
]


def load() -> dict:
    import copy
    merged = copy.deepcopy(_DEFAULTS)

    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            # Handle legacy flat format (backup_dir / price_source at top level)
            if "backup_dir" in data and "app" not in data:
                data.setdefault("app", {})["backup_dir"]   = data.pop("backup_dir")
            for key, default_val in _DEFAULTS.items():
                if key not in data:
                    continue
                if isinstance(default_val, dict):
                    merged[key] = {**default_val, **data[key]}
                else:
                    merged[key] = data[key]
        except Exception as exc:
            logger.error("Failed to load config.json: %s", exc)

    # Strip obsolete keys
    merged.get("app", {}).pop("price_source", None)

    # Merge default brave keywords into existing configs so new entries are picked up
    default_kws = _DEFAULTS["brave"]["keywords"]
    existing_kws: list = merged.get("brave", {}).get("keywords", [])
    existing_lower = {k.lower() for k in existing_kws}
    for kw in default_kws:
        if kw.lower() not in existing_lower:
            existing_kws.append(kw)
    merged.setdefault("brave", {})["keywords"] = existing_kws

    # Ensure builtin types are always present
    current: list[str] = list(merged.get("container_types", []))
    for idx, t in enumerate(BUILTIN_TYPES):
        if t not in current:
            insert_after = next(
                (current.index(p) for p in reversed(BUILTIN_TYPES[:idx]) if p in current),
                -1,
            )
            current.insert(insert_after + 1, t)
    merged["container_types"] = current

    return merged


def save(config: dict) -> None:
    _CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def inject_env() -> None:
    """Push config.json values into os.environ for modules that read via os.getenv().

    Only sets variables not already present — real env vars (Docker / CI /
    systemd EnvironmentFile) always take precedence.
    """
    import os
    cfg = load()
    for section, key, env_var in _ENV_MAP:
        if os.environ.get(env_var):
            continue
        val = cfg.get(section, {}).get(key)
        if val is None or val == "":
            continue
        if isinstance(val, bool):
            if val:
                os.environ[env_var] = "1"
        else:
            os.environ[env_var] = str(val)


def get_discord() -> dict:
    return load().get("discord", {})


def get_app() -> dict:
    return load().get("app", {})
