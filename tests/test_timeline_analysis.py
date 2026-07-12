from datetime import datetime

from clipfarm.jobs.models import MatchContext
from clipfarm.riot.timeline_analysis import analyze_clip_kills


def _match_context(sample_match_detail) -> MatchContext:
    info = sample_match_detail["info"]
    return MatchContext(
        match_id=sample_match_detail["metadata"]["matchId"],
        participant_id=3,
        champion="Yasuo",
        kills=12,
        deaths=4,
        assists=6,
        win=True,
        queue_type="Ranked Solo/Duo",
        game_start_ms=info["gameStartTimestamp"],
        game_end_ms=info["gameEndTimestamp"],
    )


def test_analyze_clip_kills_detects_triple_kill(fake_riot_client, sample_match_detail):
    match = _match_context(sample_match_detail)
    game_start_seconds = match.game_start_ms // 1000
    clip_recorded_at = datetime.fromtimestamp(game_start_seconds + 605)  # inside the 600s-609s kill cluster

    highlight = analyze_clip_kills(
        fake_riot_client, match, clip_recorded_at, roll_tolerance_seconds=20, multikill_cluster_seconds=10
    )

    assert highlight is not None
    assert highlight.kill_streak == 3
    assert highlight.champion == "Yasuo"
    assert highlight.victim_champions == ["Malphite", "Ashe", "Leona"]


def test_analyze_clip_kills_no_kills_in_window_returns_none(fake_riot_client, sample_match_detail):
    match = _match_context(sample_match_detail)
    game_start_seconds = match.game_start_ms // 1000
    clip_recorded_at = datetime.fromtimestamp(game_start_seconds + 60)  # far from any kill

    highlight = analyze_clip_kills(
        fake_riot_client, match, clip_recorded_at, roll_tolerance_seconds=20, multikill_cluster_seconds=10
    )

    assert highlight is None


def test_analyze_clip_kills_solo_kill_outside_cluster(fake_riot_client, sample_match_detail):
    match = _match_context(sample_match_detail)
    game_start_seconds = match.game_start_ms // 1000
    clip_recorded_at = datetime.fromtimestamp(game_start_seconds + 1500)  # the lone kill at 1500000ms

    highlight = analyze_clip_kills(
        fake_riot_client, match, clip_recorded_at, roll_tolerance_seconds=20, multikill_cluster_seconds=10
    )

    assert highlight is not None
    assert highlight.kill_streak == 1
    assert highlight.victim_champions == ["Zed"]
