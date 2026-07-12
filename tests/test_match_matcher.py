from datetime import datetime

from clipfarm.jobs.models import MatchContext
from clipfarm.riot.match_matcher import (
    find_match_containing_timestamp,
    find_match_covering_time,
    find_match_for_full_game,
)


def test_find_match_for_full_game_matches_closest_start(fake_riot_client, sample_match_detail):
    game_start_seconds = sample_match_detail["info"]["gameStartTimestamp"] // 1000
    recorded_at = datetime.fromtimestamp(game_start_seconds)

    match = find_match_for_full_game(fake_riot_client, recorded_at, tolerance_minutes=15)

    assert match is not None
    assert match.champion == "Yasuo"
    assert match.kda == "12/4/6"
    assert match.win is True
    assert match.queue_type == "Ranked Solo/Duo"
    assert match.participant_id == 3
    assert match.role == "Mid"
    assert match.patch == "26.13"
    assert match.opponent_champion == "Zed"  # enemy MIDDLE laner


def test_find_match_for_full_game_no_puuid_match_returns_none(fake_riot_client):
    fake_riot_client._puuid = "does-not-exist"
    recorded_at = datetime.fromtimestamp(1751500000)

    match = find_match_for_full_game(fake_riot_client, recorded_at, tolerance_minutes=15)

    assert match is None


def test_find_match_covering_time_hits_when_inside_game(fake_riot_client, sample_match_detail):
    game_start_s = sample_match_detail["info"]["gameStartTimestamp"] // 1000
    mid_game = datetime.fromtimestamp(game_start_s + 600)  # 10 min into the game

    match = find_match_covering_time(fake_riot_client, mid_game)

    assert match is not None
    assert match.match_id == sample_match_detail["metadata"]["matchId"]
    assert match.champion == "Yasuo"


def test_find_match_covering_time_misses_outside_game(fake_riot_client, sample_match_detail):
    game_end_s = sample_match_detail["info"]["gameEndTimestamp"] // 1000
    well_after = datetime.fromtimestamp(game_end_s + 3600)  # an hour after the game ended

    assert find_match_covering_time(fake_riot_client, well_after) is None


def test_find_match_containing_timestamp():
    game = MatchContext(
        match_id="NA1_1",
        participant_id=3,
        champion="Yasuo",
        kills=1,
        deaths=1,
        assists=1,
        win=True,
        queue_type="Ranked Solo/Duo",
        game_start_ms=1_751_500_000_000,
        game_end_ms=1_751_502_100_000,
    )
    inside = datetime.fromtimestamp(1_751_500_000 + 605)
    outside = datetime.fromtimestamp(1_751_500_000 - 10_000)

    assert find_match_containing_timestamp([game], inside, roll_tolerance_seconds=20) is game
    assert find_match_containing_timestamp([game], outside, roll_tolerance_seconds=20) is None
