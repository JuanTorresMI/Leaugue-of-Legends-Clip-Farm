"""Runtime-editable Facebook Page credentials + on/off, so they can be set from the dashboard
without editing .env and restarting. Seeded once from .env (FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN)
and config.yaml (facebook.enabled); afterwards data/facebook.json is the source of truth.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from clipfarm.config import get_settings


@dataclass
class FacebookSettings:
    page_id: str | None
    access_token: str | None
    enabled: bool

    @property
    def configured(self) -> bool:
        return bool(self.page_id and self.access_token)


def _store_path() -> Path:
    return get_settings().project_root / "data" / "facebook.json"


def _seed() -> FacebookSettings:
    settings = get_settings()
    return FacebookSettings(
        page_id=settings.secrets.fb_page_id or None,
        access_token=settings.secrets.fb_page_access_token or None,
        enabled=settings.facebook.enabled,
    )


def current() -> FacebookSettings:
    path = _store_path()
    if not path.exists():
        seeded = _seed()
        save(seeded)
        return seeded
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return FacebookSettings(
        page_id=data.get("page_id"),
        access_token=data.get("access_token"),
        enabled=bool(data.get("enabled", False)),
    )


def save(settings: FacebookSettings) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, indent=2)


def is_enabled() -> bool:
    s = current()
    return s.enabled and s.configured
