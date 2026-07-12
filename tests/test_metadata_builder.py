from datetime import datetime

from clipfarm.jobs.models import ClipHighlight, MatchContext
from clipfarm.riot.champion_names import display_name, tag_name
from clipfarm.riot.metadata_builder import (
    YOUTUBE_TAGS_CHAR_BUDGET,
    YOUTUBE_TITLE_LIMIT,
    build_clip_metadata,
    build_full_game_metadata,
)

RECORDED_AT = datetime(2026, 7, 4, 20, 35, 2)


def _match(**overrides) -> MatchContext:
    defaults = dict(
        match_id="NA1_5000000000",
        participant_id=3,
        champion="JarvanIV",
        kills=12,
        deaths=4,
        assists=6,
        win=True,
        queue_type="Ranked Solo/Duo",
        game_start_ms=1_751_500_000_000,
        game_end_ms=1_751_502_100_000,
        role="Jungle",
        patch="26.13",
        opponent_champion="Viego",
    )
    defaults.update(overrides)
    return MatchContext(**defaults)


def test_display_names():
    assert display_name("JarvanIV") == "Jarvan IV"
    assert display_name("MonkeyKing") == "Wukong"
    assert display_name("Yasuo") == "Yasuo"  # unmapped names pass through
    assert tag_name("KSante") == "KSante"
    assert tag_name("Kaisa") == "KaiSa"


def test_clip_pentakill_title():
    highlight = ClipHighlight(kill_streak=5, champion="JarvanIV", victim_champions=["A", "B", "C", "D", "E"])
    meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")

    assert "PENTAKILL" in meta.title
    assert "Jarvan IV" in meta.title  # display name, not internal name
    assert "#shorts" in meta.title.lower()
    assert "Emerald" in meta.title
    assert len(meta.title) <= YOUTUBE_TITLE_LIMIT
    assert "#Pentakill" in meta.hashtags
    assert "pentakill" in meta.tags


def test_clip_double_kill_names_victims():
    highlight = ClipHighlight(kill_streak=2, champion="JarvanIV", victim_champions=["MissFortune", "Leona"])
    meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")

    assert "DOUBLE KILL" in meta.title
    assert "Miss Fortune" in meta.title  # victim display name
    assert "#DoubleKill" in meta.hashtags


def test_clip_single_kill_is_deterministic():
    highlight = ClipHighlight(kill_streak=1, champion="JarvanIV", victim_champions=["Zed"])
    meta1 = build_clip_metadata(_match(), highlight, RECORDED_AT, rank=None)
    meta2 = build_clip_metadata(_match(), highlight, RECORDED_AT, rank=None)

    assert meta1.title == meta2.title  # regeneration must be stable
    assert "Zed" in meta1.title
    assert "KILL -" not in meta1.title  # no more bland "KILL - Champ" format


def test_clip_no_match_fallback():
    meta = build_clip_metadata(None, None, RECORDED_AT)
    assert "League of Legends" in meta.title
    assert meta.tags  # even the fallback carries search tags


def test_full_game_matchup_title():
    meta = build_full_game_metadata(_match(), RECORDED_AT, rank="Emerald II")

    assert "Jarvan IV vs Viego" in meta.title
    assert "Jungle" in meta.title
    assert "Emerald II" in meta.title
    assert "26.13" in meta.title
    assert "WIN" in meta.title
    assert len(meta.title) <= YOUTUBE_TITLE_LIMIT
    assert "#Shorts" not in meta.hashtags  # full games are not Shorts
    assert "jarvan iv vs viego" in meta.tags


def test_full_game_without_opponent():
    meta = build_full_game_metadata(_match(opponent_champion=None, role=None), RECORDED_AT)
    assert "Full Gameplay" in meta.title


def test_clip_metadata_has_cta_and_reach_hashtags():
    highlight = ClipHighlight(kill_streak=5, champion="JarvanIV", victim_champions=["A", "B", "C", "D", "E"])
    meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")

    assert "SUBSCRIBE" in meta.description  # monetization CTA present
    assert "#Shorts" == meta.hashtags[0]     # Shorts format signal leads
    assert "#Gaming" in meta.hashtags        # broad-reach tag included
    assert "jarvan iv montage" in meta.tags  # SEO long-tail search phrase


def test_full_game_metadata_has_cta():
    meta = build_full_game_metadata(_match(), RECORDED_AT, rank="Emerald II")
    assert "SUBSCRIBE" in meta.description
    assert len(meta.title) <= YOUTUBE_TITLE_LIMIT


def test_no_match_clip_still_shorts_and_cta():
    meta = build_clip_metadata(None, None, RECORDED_AT)
    assert "#shorts" in meta.title.lower()
    assert "SUBSCRIBE" in meta.description


def test_tags_respect_budget_and_dedupe():
    highlight = ClipHighlight(kill_streak=3, champion="JarvanIV", victim_champions=["Ashe", "Leona", "Zed"])
    meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")

    assert sum(len(t) for t in meta.tags) <= YOUTUBE_TAGS_CHAR_BUDGET
    assert len({t.lower() for t in meta.tags}) == len(meta.tags)
    assert "league of legends" in meta.tags
