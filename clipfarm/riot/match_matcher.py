"""Turn an Ascent filename timestamp into the specific Riot match it corresponds to."""
from __future__ import annotations

import logging
from datetime import datetime

from clipfarm.jobs.models import MatchContext
from clipfarm.riot.client import RiotClient

logger = logging.getLogger(__name__)

# Common queue IDs -- see https://static.developer.riotgames.com/docs/lol/queues.json
QUEUE_NAMES = {
    400: "Normal Draft",
    420: "Ranked Solo/Duo",
    430: "Normal Blind",
    440: "Ranked Flex",
    450: "ARAM",
    480: "Swiftplay",
    700: "Clash",
    900: "ARURF",
}


def _queue_name(queue_id: int) -> str:
    return QUEUE_NAMES.get(queue_id, f"Queue {queue_id}")


ROLE_NAMES = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "ADC", "UTILITY": "Support"}


def _role_name(team_position: str | None) -> str | None:
    return ROLE_NAMES.get(team_position or "")


def _patch(game_version: str | None) -> str | None:
    """'26.13.685.9138' -> '26.13'."""
    if not game_version:
        return None
    parts = game_version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else game_version


def _opponent_champion(participants: list[dict], me: dict) -> str | None:
    """The enemy laner in the same position -- the classic 'X vs Y' matchup for titles."""
    my_position = me.get("teamPosition")
    if not my_position:
        return None
    for p in participants:
        if p.get("teamId") != me.get("teamId") and p.get("teamPosition") == my_position:
            return p["championName"]
    return None


def _context_from_detail(match_id: str, detail: dict, puuid: str) -> MatchContext | None:
    """Build a MatchContext for our player from a raw match-detail response, or None if
    the player wasn't in this match."""
    info = detail["info"]
    participants = info["participants"]
    participant = next((p for p in participants if p["puuid"] == puuid), None)
    if participant is None:
        return None
    game_start_ms = info["gameStartTimestamp"]
    return MatchContext(
        match_id=match_id,
        participant_id=participant["participantId"],
        champion=participant["championName"],
        kills=participant["kills"],
        deaths=participant["deaths"],
        assists=participant["assists"],
        win=participant["win"],
        queue_type=_queue_name(info["queueId"]),
        game_start_ms=game_start_ms,
        game_end_ms=info.get("gameEndTimestamp", game_start_ms + info.get("gameDuration", 0) * 1000),
        role=_role_name(participant.get("teamPosition")),
        patch=_patch(info.get("gameVersion")),
        opponent_champion=_opponent_champion(participants, participant),
    )


def find_match_for_full_game(
    client: RiotClient, recorded_at: datetime, tolerance_minutes: int, max_game_duration_minutes: int = 60
) -> MatchContext | None:
    """Match a full-game recording's filename timestamp to the closest real match.

    Riot's startTime/endTime filter on this endpoint filters by the match's *end* timestamp,
    not its start timestamp (confirmed by direct testing -- not clearly documented by Riot).
    So the query window's end bound has to extend past recorded_at by a generous max game
    length, or games longer than `tolerance_minutes` are silently missed entirely.
    """
    tolerance_seconds = tolerance_minutes * 60
    center = int(recorded_at.timestamp())
    start_time = center - tolerance_seconds
    end_time = center + tolerance_seconds + max_game_duration_minutes * 60

    match_ids = client.get_match_ids_in_window(start_time, end_time)
    if not match_ids:
        logger.warning("No Riot matches found within %sm of %s", tolerance_minutes, recorded_at)
        return None

    puuid = client.get_puuid()
    best: MatchContext | None = None
    best_delta_ms: int | None = None
    target_ms = center * 1000

    for match_id in match_ids:
        detail = client.get_match_detail(match_id)
        context = _context_from_detail(match_id, detail, puuid)
        if context is None:
            continue
        delta_ms = abs(context.game_start_ms - target_ms)
        if best_delta_ms is None or delta_ms < best_delta_ms:
            best_delta_ms = delta_ms
            best = context

    return best


def find_match_covering_time(
    client: RiotClient, recorded_at: datetime, max_game_duration_minutes: int = 60, tolerance_seconds: int = 120
) -> MatchContext | None:
    """Find the match whose [start, end] range contains `recorded_at`.

    Used for clips, whose timestamps land mid-game. Because Riot's startTime/endTime
    filter matches on game END time, a game containing this moment must end between
    recorded_at and recorded_at + max game length -- that's the query window.
    This makes clip matching independent of whether the full-game recording was
    processed first (or recorded at all).
    """
    ts = int(recorded_at.timestamp())
    match_ids = client.get_match_ids_in_window(
        ts - tolerance_seconds, ts + max_game_duration_minutes * 60
    )
    if not match_ids:
        return None

    puuid = client.get_puuid()
    target_ms = ts * 1000
    tolerance_ms = tolerance_seconds * 1000
    for match_id in match_ids:
        detail = client.get_match_detail(match_id)
        context = _context_from_detail(match_id, detail, puuid)
        if context is None:
            continue
        if (context.game_start_ms - tolerance_ms) <= target_ms <= (context.game_end_ms + tolerance_ms):
            return context
    return None


def find_match_containing_timestamp(
    candidate_matches: list[MatchContext], recorded_at: datetime, roll_tolerance_seconds: int
) -> MatchContext | None:
    """Given already-matched full games, find which one's time range a clip falls inside."""
    target_ms = int(recorded_at.timestamp()) * 1000
    tolerance_ms = roll_tolerance_seconds * 1000
    for match in candidate_matches:
        if (match.game_start_ms - tolerance_ms) <= target_ms <= (match.game_end_ms + tolerance_ms):
            return match
    return None
