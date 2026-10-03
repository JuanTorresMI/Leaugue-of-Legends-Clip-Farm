"""Resolve a clip's timestamp into the specific kill event(s) it captured, via the Timeline API."""
from __future__ import annotations

import logging
from datetime import datetime

from clipfarm.jobs.models import ClipHighlight, MatchContext
from clipfarm.riot.client import RiotClient

logger = logging.getLogger(__name__)


def _cluster_kills(kills: list[dict], cluster_ms: int) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    current: list[dict] = [kills[0]]
    for event in kills[1:]:
        if event["timestamp"] - current[-1]["timestamp"] <= cluster_ms:
            current.append(event)
        else:
            clusters.append(current)
            current = [event]
    clusters.append(current)
    return clusters


def analyze_clip_kills(
    client: RiotClient,
    match: MatchContext,
    clip_recorded_at: datetime,
    roll_tolerance_seconds: int,
    multikill_cluster_seconds: int,
) -> ClipHighlight | None:
    """Find the kill (or multi-kill) that a clip most likely captures."""
    timeline = client.get_timeline(match.match_id)
    frames = timeline["info"]["frames"]
    all_events = [event for frame in frames for event in frame.get("events", [])]
    kill_events = [
        e for e in all_events if e.get("type") == "CHAMPION_KILL" and e.get("killerId") == match.participant_id
    ]
    if not kill_events:
        return None

    clip_offset_ms = int(clip_recorded_at.timestamp()) * 1000 - match.game_start_ms
    tolerance_ms = roll_tolerance_seconds * 1000
    window_kills = sorted(
        (e for e in kill_events if abs(e["timestamp"] - clip_offset_ms) <= tolerance_ms),
        key=lambda e: e["timestamp"],
    )
    if not window_kills:
        return None

    clusters = _cluster_kills(window_kills, multikill_cluster_seconds * 1000)
    best_cluster = min(clusters, key=lambda c: min(abs(e["timestamp"] - clip_offset_ms) for e in c))

    detail = client.get_match_detail(match.match_id)
    champion_by_participant = {
        p["participantId"]: p["championName"] for p in detail["info"]["participants"]
    }
    victim_champions = [
        champion_by_participant.get(e["victimId"], "Unknown") for e in best_cluster
    ]

    return ClipHighlight(
        kill_streak=len(best_cluster),
        champion=match.champion,
        victim_champions=victim_champions,
        first_kill_ms=best_cluster[0]["timestamp"],
        last_kill_ms=best_cluster[-1]["timestamp"],
    )
