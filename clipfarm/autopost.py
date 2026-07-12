"""Runtime-editable auto-post settings, toggled from the dashboard (no restart).

Auto-post drip-publishes your best clips on a schedule instead of dumping a batch. The defaults
encode what actually grows a Shorts channel in 2026: a small number of posts per day, spaced
across the midday + evening viewing peaks, quality-gated, and capped per game so one match can't
flood the feed with near-identical clips. `data/autopost.json` is the source of truth once written.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from clipfarm.config import get_settings


@dataclass
class AutopostSettings:
    # Master switch (the dashboard button).
    enabled: bool = False
    # Local hours (0-23) to publish at. One post per slot per day = spaced, consistent cadence.
    # Default 3/day across the two Shorts peaks: midday, late afternoon, evening.
    post_hours: list[int] = field(default_factory=lambda: [12, 17, 21])
    # Skip clips weaker than this streak. 1 = allow solo kills (they still perform); a clip with
    # no detected kill (highlight-only) scores below 1 and is always skipped by auto-post.
    min_kill_streak: int = 1
    # Never auto-post more than this many clips from a single match (kills same-game floods).
    per_game_cap: int = 3
    # Hard floor between two auto-posts, so a catch-up after downtime drips instead of bursting.
    min_gap_minutes: int = 90
    # Platforms clip auto-post publishes to.
    platforms: list[str] = field(default_factory=lambda: ["youtube"])

    # --- long-form (full game) track, separate schedule ---
    # Long-form and Shorts peak at different times; full games go out mid-afternoon so they're
    # live before the late-afternoon + evening gaming peaks. Research says 1-3/week beats daily
    # for long-form, so this defaults to a single afternoon slot (~1/day max) and OFF.
    full_game_enabled: bool = False
    full_game_hours: list[int] = field(default_factory=lambda: [15])
    full_game_platforms: list[str] = field(default_factory=lambda: ["youtube"])

    def normalized(self) -> "AutopostSettings":
        def _hours(hrs, default):
            return sorted({h for h in hrs if 0 <= int(h) <= 23}) or default

        return AutopostSettings(
            enabled=bool(self.enabled),
            post_hours=_hours(self.post_hours, [12, 17, 21]),
            min_kill_streak=max(1, int(self.min_kill_streak)),
            per_game_cap=max(1, int(self.per_game_cap)),
            min_gap_minutes=max(0, int(self.min_gap_minutes)),
            platforms=[p for p in self.platforms if p] or ["youtube"],
            full_game_enabled=bool(self.full_game_enabled),
            full_game_hours=_hours(self.full_game_hours, [15]),
            full_game_platforms=[p for p in self.full_game_platforms if p] or ["youtube"],
        )


def _store_path() -> Path:
    return get_settings().project_root / "data" / "autopost.json"


def current() -> AutopostSettings:
    path = _store_path()
    if not path.exists():
        seeded = AutopostSettings()
        save(seeded)
        return seeded
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    known = AutopostSettings().__dict__
    return AutopostSettings(**{k: data[k] for k in known if k in data}).normalized()


def save(settings: AutopostSettings) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(asdict(settings.normalized()), f, indent=2)


def is_enabled() -> bool:
    return current().enabled
