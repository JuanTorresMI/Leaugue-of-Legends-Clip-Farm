"""Runtime-editable auto-post settings, toggled from the dashboard (no restart).

Auto-post drip-publishes your best clips on a schedule instead of dumping a batch. The defaults
encode what actually grows a Shorts channel in 2026: a small number of posts per day, spaced
across the midday + evening viewing peaks, quality-gated, and capped per game so one match can't
flood the feed with near-identical clips. `data/autopost.json` is the source of truth once written.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from clipfarm.config import get_settings


@dataclass
class AutopostSettings:
    # Master switch (the dashboard button).
    enabled: bool = False
    # Local hours (0-23) to publish at. One post per slot per day = spaced, consistent cadence.
    # Default 3/day across the two Shorts peaks: midday, late afternoon, evening.
    post_hours: list[int] = field(default_factory=lambda: [12, 17, 21])
    # "fixed": post at exactly post_hours every day.
    # "explore": run the posting-time experiment -- each day's slots (same count as post_hours)
    # are drawn from explore_window on a deterministic rotation, so over ~2 weeks every hour in
    # the window collects samples in the metrics hour-performance table. Once a winner is clear,
    # the metrics page's "Apply recommended" locks the best hours in and returns to fixed mode.
    schedule_mode: str = "fixed"
    # [first_hour, last_hour] (local, inclusive) that explore mode samples from. The default
    # spans late morning through night -- where Shorts viewing actually happens.
    explore_window: list[int] = field(default_factory=lambda: [11, 23])
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

    def normalized(self) -> AutopostSettings:
        def _hours(hrs, default):
            return sorted({h for h in hrs if 0 <= int(h) <= 23}) or default

        window = _hours(self.explore_window, [11, 23])
        window = [window[0], window[-1]] if window[0] < window[-1] else [11, 23]

        return AutopostSettings(
            enabled=bool(self.enabled),
            post_hours=_hours(self.post_hours, [12, 17, 21]),
            schedule_mode=self.schedule_mode if self.schedule_mode in ("fixed", "explore") else "fixed",
            explore_window=window,
            min_kill_streak=max(1, int(self.min_kill_streak)),
            per_game_cap=max(1, int(self.per_game_cap)),
            min_gap_minutes=max(0, int(self.min_gap_minutes)),
            platforms=[p for p in self.platforms if p] or ["youtube"],
            full_game_enabled=bool(self.full_game_enabled),
            full_game_hours=_hours(self.full_game_hours, [15]),
            full_game_platforms=[p for p in self.full_game_platforms if p] or ["youtube"],
        )


def explore_hours_for(settings: AutopostSettings, day: date) -> list[int]:
    """The posting-time experiment's slots for `day`: as many posts as post_hours has, drawn
    from explore_window. The window is split into that many contiguous segments (so slots stay
    spread across the day like a normal schedule) and each segment cycles through its hours on
    a date-seeded rotation -- deterministic all day (the scheduler re-derives it every tick),
    different tomorrow, and covering every hour in the window within a couple of weeks. That
    variation is what feeds the metrics hour-performance table real samples; a fixed schedule
    can only ever learn about its own 3 hours."""
    n = max(1, len(settings.post_hours))
    start, end = settings.explore_window
    window = list(range(start, end + 1))
    n = min(n, len(window))
    segments = [
        window[round(i * len(window) / n): round((i + 1) * len(window) / n)] for i in range(n)
    ]
    d = day.toordinal()
    # The +i phase shift stops all segments cycling in lockstep (else day N would always pick
    # the same relative position everywhere, sampling combinations unevenly).
    return [seg[(d + i) % len(seg)] for i, seg in enumerate(segments)]


def effective_post_hours(settings: AutopostSettings, day: date) -> list[int]:
    """Today's actual clip slots: the experiment's rotation in explore mode, post_hours in fixed."""
    if settings.schedule_mode == "explore":
        return explore_hours_for(settings, day)
    return settings.post_hours


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
