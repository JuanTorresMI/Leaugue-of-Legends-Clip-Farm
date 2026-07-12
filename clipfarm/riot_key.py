"""Runtime-updatable Riot API key.

Riot development keys expire every ~24h, so the key needs to be changeable without editing
.env and restarting. The .env RIOT_API_KEY is the default/seed; once a key is saved from the
dashboard it lives in data/riot_key.txt (gitignored) and takes precedence, surviving restarts.
Delete that file to fall back to .env.
"""
from __future__ import annotations

from pathlib import Path

from clipfarm.config import get_settings


def _key_path() -> Path:
    return get_settings().project_root / "data" / "riot_key.txt"


def current_key() -> str:
    path = _key_path()
    if path.exists():
        saved = path.read_text(encoding="utf-8").strip()
        if saved:
            return saved
    return get_settings().secrets.riot_api_key


def set_key(key: str) -> None:
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.strip(), encoding="utf-8")
