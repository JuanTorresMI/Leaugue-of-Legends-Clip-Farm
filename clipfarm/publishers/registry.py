"""The single place a publishing platform plugs into the app.

To add a new platform (TikTok, Instagram, ...):

1. Write `clipfarm/publishers/<name>.py` exposing `publish(media_file: sqlite3.Row) -> str`
   (returns the platform's video id; raise with a human-readable message on failure).
2. Register it in `_REGISTRY` below: which media kinds route to it ('clip', 'full_game')
   and when it counts as enabled (config flag, credential store, or just `lambda: True`).

Everything else follows automatically: new items get a checkbox for it in the dashboard,
Approve publishes to it, per-platform status chips + Retry work, and enabling it later
backfills targets onto existing queue items via its enablement check.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from clipfarm.publishers import facebook, youtube


@dataclass(frozen=True)
class Platform:
    name: str
    kinds: tuple[str, ...]  # media kinds routed here: 'clip' and/or 'full_game'
    is_enabled: Callable[[], bool]
    publish: Callable[[sqlite3.Row], str]


def _facebook_enabled() -> bool:
    from clipfarm import fb_settings

    return fb_settings.is_enabled()


_REGISTRY: tuple[Platform, ...] = (
    Platform(
        name="youtube",
        kinds=("clip", "full_game"),
        is_enabled=lambda: True,  # always on; it's the primary destination
        publish=youtube.publish,
    ),
    Platform(
        name="facebook",
        kinds=("clip", "full_game"),
        is_enabled=_facebook_enabled,
        publish=facebook.publish,
    ),
    # Example -- TikTok, once its publisher exists:
    # Platform(name="tiktok", kinds=("clip",),
    #          is_enabled=lambda: get_settings().tiktok.enabled, publish=tiktok.publish),
)


def get(name: str) -> Platform | None:
    return next((p for p in _REGISTRY if p.name == name), None)


def enabled_platforms(kind: str) -> list[str]:
    """Platform names that should receive a new item of this media kind."""
    return [p.name for p in _REGISTRY if kind in p.kinds and p.is_enabled()]
