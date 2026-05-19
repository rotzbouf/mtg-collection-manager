"""Application configuration — single source of truth in config.json.

.env is kept as a deployment override for Docker/CI: any env var present
at process start takes precedence over config.json for the bot (loaded via
python-dotenv before this module runs).  The desktop app reads only
config.json.
"""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.json"
_ENV_PATH    = Path(__file__).parent.parent / ".env"

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
        "price_source":       "cardmarket",
        "ui_port":            8080,
        "ui_host":            "0.0.0.0",
        "debug_scan_preview": False,
    },
    "container_types":          list(BUILTIN_TYPES),
    "overcount_excluded_types": [],
}

# Mapping from old .env variable names → config path (section, key)
_ENV_MIGRATE: list[tuple[str, str, str]] = [
    ("DISCORD_TOKEN",               "discord", "token"),
    ("DISCORD_GUILD_ID",            "discord", "guild_id"),
    ("DISCORD_SCAN_CHANNEL_ID",     "discord", "scan_channel_id"),
    ("DISCORD_SHOWCASE_CHANNEL_ID", "discord", "showcase_channel_id"),
    ("DISCORD_GUEST_ROLE",          "discord", "guest_role"),
    ("DISCORD_COLLECTOR_ROLE",      "discord", "collector_role"),
    ("DISCORD_ADMIN_ROLE",          "discord", "admin_role"),
    ("BACKUP_DIR",                  "app",     "backup_dir"),
    ("DEBUG_SCAN_PREVIEW",          "app",     "debug_scan_preview"),
    ("UI_PORT",                     "app",     "ui_port"),
    ("UI_HOST",                     "app",     "ui_host"),
]


def _read_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip()
    return values


def _coerce(value: str, target) -> object:
    """Cast a string from .env to the same type as the default value."""
    if isinstance(target, bool):
        return value.lower() in ("1", "true", "yes")
    if isinstance(target, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return target
    if isinstance(target, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return target
    return value


def _migrate_from_env(config: dict) -> bool:
    """Copy .env values into config.json sections on first run. Returns True if anything changed."""
    env = _read_env_file()
    if not env:
        return False
    changed = False
    for env_key, section, cfg_key in _ENV_MIGRATE:
        if env_key in env and not config[section].get(cfg_key):
            default = _DEFAULTS[section][cfg_key]
            config[section][cfg_key] = _coerce(env[env_key], default)
            changed = True
    return changed


def load() -> dict:
    import copy
    merged = copy.deepcopy(_DEFAULTS)

    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            # Handle legacy flat format (backup_dir / price_source at top level)
            if "backup_dir" in data and "app" not in data:
                data.setdefault("app", {})["backup_dir"]    = data.pop("backup_dir")
            if "price_source" in data and "app" not in data:
                data.setdefault("app", {})["price_source"]  = data.pop("price_source")

            for key, default_val in _DEFAULTS.items():
                if key not in data:
                    continue
                if isinstance(default_val, dict):
                    merged[key] = {**default_val, **data[key]}
                else:
                    merged[key] = data[key]
        except Exception:
            pass

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

    # One-time migration from .env if discord.token is still empty
    if not merged["discord"].get("token"):
        if _migrate_from_env(merged):
            save(merged)

    return merged


def save(config: dict) -> None:
    _CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def get_discord() -> dict:
    return load().get("discord", {})


def get_app() -> dict:
    return load().get("app", {})
