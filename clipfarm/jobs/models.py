"""Shared dataclasses passed between the ingest/riot/media/db layers."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MatchContext:
    match_id: str
    participant_id: int
    champion: str
    kills: int
    deaths: int
    assists: int
    win: bool
    queue_type: str
    game_start_ms: int
    game_end_ms: int
    role: str | None = None  # Top/Jungle/Mid/ADC/Support
    patch: str | None = None  # e.g. "26.13"
    opponent_champion: str | None = None  # enemy laner in the same position

    @property
    def kda(self) -> str:
        return f"{self.kills}/{self.deaths}/{self.assists}"


@dataclass
class ClipHighlight:
    kill_streak: int  # 1 = single kill, 2 = double, ... 5 = penta
    champion: str
    victim_champions: list[str] = field(default_factory=list)
    first_kill_ms: int | None = None  # game-relative ts of the streak's first kill (dedup signature)
    last_kill_ms: int | None = None  # game-relative ts of the streak's last kill ("3 kills in 6 seconds")


@dataclass
class DraftMetadata:
    title: str
    description: str
    hashtags: list[str]  # visible in the description
    tags: list[str] = field(default_factory=list)  # YouTube's tags API field (search keywords)
    # Which A/B title style produced `title` (see metadata_builder._TITLE_VARIANTS). Stored per
    # video so the metrics dashboard can measure which hook style actually earns views.
    title_variant: str | None = None
    # One alternate title per hook family (family -> title), including the chosen one, so the
    # scheduler can switch to a different shape at post time without re-deriving anything.
    title_alternates: dict[str, str] = field(default_factory=dict)
