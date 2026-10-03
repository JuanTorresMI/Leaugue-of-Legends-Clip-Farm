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
    assert "#shorts" not in meta.title.lower()  # YouTube detects the format; the tag just looks spammy
    assert "Emerald" in meta.title
    assert len(meta.title) <= YOUTUBE_TITLE_LIMIT
    assert "#Pentakill" in meta.hashtags
    assert "pentakill" in meta.tags


def test_clip_double_kill_names_victims():
    highlight = ClipHighlight(kill_streak=2, champion="JarvanIV", victim_champions=["MissFortune", "Leona"])
    meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")

    assert "double kill" in meta.title.lower()
    assert "Miss Fortune" in meta.title  # victim display name
    assert "#DoubleKill" in meta.hashtags


def test_clip_single_kill_is_deterministic():
    highlight = ClipHighlight(kill_streak=1, champion="JarvanIV", victim_champions=["Zed"])
    meta1 = build_clip_metadata(_match(), highlight, RECORDED_AT, rank=None)
    meta2 = build_clip_metadata(_match(), highlight, RECORDED_AT, rank=None)

    assert meta1.title == meta2.title  # regeneration must be stable
    assert "Zed" in meta1.title
    assert "KILL -" not in meta1.title  # no more bland "KILL - Champ" format
    assert "Jarvan IV" in meta1.title  # the matchup is spelled out: that's what people search


def test_clip_no_match_fallback():
    meta = build_clip_metadata(None, None, RECORDED_AT)
    assert "League of Legends" in meta.title
    assert meta.tags  # even the fallback carries search tags


def test_full_game_matchup_title():
    meta = build_full_game_metadata(_match(), RECORDED_AT, rank="Emerald II")

    assert "Jarvan IV vs Viego" in meta.title
    assert "Jungle" in meta.title
    assert "Emerald" in meta.title       # tier only -- "Emerald II Ranked Solo/Duo" is robot-speak
    assert "26.13" in meta.title
    assert meta.title.startswith("12/4/6 ")  # a winning KDA is the hook that earns the click
    assert "Full Gameplay" in meta.title     # the search phrase people actually type
    assert len(meta.title) <= YOUTUBE_TITLE_LIMIT
    assert "#Shorts" not in meta.hashtags  # full games are not Shorts
    assert "jarvan iv vs viego" in meta.tags
    assert "WIN" in meta.description  # result stays in the description for search


def test_full_game_loss_title_hides_the_score():
    # "LOSS 4/8/12" in a title repels the click; a loss leads with the matchup instead.
    meta = build_full_game_metadata(_match(win=False, kills=4, deaths=8, assists=12), RECORDED_AT,
                                    rank="Emerald II")
    assert "LOSS" not in meta.title
    assert "4/8/12" not in meta.title
    assert "Jarvan IV vs Viego" in meta.title
    assert "LOSS" in meta.description  # honesty lives in the description


def test_full_game_zero_kda_win_gets_no_score_hook():
    # Remakes / missing data carry a 0/0/0 score -- "0/0/0 Illaoi vs Garen" repels the click.
    meta = build_full_game_metadata(_match(kills=0, deaths=0, assists=0), RECORDED_AT)
    assert not meta.title.startswith("0/0/0")


def test_full_game_without_opponent():
    meta = build_full_game_metadata(_match(opponent_champion=None, role=None), RECORDED_AT)
    assert "Full Gameplay" in meta.title


def test_clip_metadata_has_cta_and_reach_hashtags():
    highlight = ClipHighlight(kill_streak=5, champion="JarvanIV", victim_champions=["A", "B", "C", "D", "E"])
    meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")

    assert "Subscribe" in meta.description   # one plain subscribe line, not a shouted banner
    assert "SUBSCRIBE" not in meta.description
    # YouTube shows the first three hashtags above the title: champion + game, not "#Shorts".
    assert meta.hashtags[:2] == ["#JarvanIV", "#LeagueOfLegends"]
    assert "#Shorts" in meta.hashtags        # format signal still present, just not the headline
    assert "#Gaming" not in meta.hashtags    # too broad to help; reads as filler
    assert 3 <= len(meta.hashtags) <= 5
    assert "jarvan iv montage" in meta.tags  # SEO long-tail search phrase


def test_full_game_metadata_has_cta():
    meta = build_full_game_metadata(_match(), RECORDED_AT, rank="Emerald II")
    assert "Subscribe" in meta.description
    assert len(meta.title) <= YOUTUBE_TITLE_LIMIT


def test_no_match_clip_still_shorts_and_cta():
    meta = build_clip_metadata(None, None, RECORDED_AT)
    assert "#shorts" not in meta.title.lower()
    assert "#Shorts" in meta.hashtags
    assert "Subscribe" in meta.description


def _all_titles():
    """Every title shape the builder can produce, across variants and streak sizes."""
    titles = [build_full_game_metadata(_match(), RECORDED_AT, rank="Emerald II").title,
              build_full_game_metadata(_match(win=False), RECORDED_AT).title,
              build_clip_metadata(_match(), None, RECORDED_AT, rank="Emerald II").title,
              build_clip_metadata(None, None, RECORDED_AT).title]
    for streak in (1, 2, 3, 4, 5):
        for ms in range(0, 12):
            highlight = ClipHighlight(kill_streak=streak, champion="JarvanIV",
                                      victim_champions=["Zed", "Ahri", "Lux", "Jinx", "Lulu"][:streak],
                                      first_kill_ms=ms * 31_000)
            titles.append(build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II").title)
    return titles


def test_titles_read_like_a_person_wrote_them():
    # The tells of a generated title: a #shorts suffix, em dashes, emoji pile-ups, SHOUTED
    # hype adjectives. None of them may appear in any shape the builder produces.
    emoji = set("⚔🔥😳😱💀👉")  # base code points (the swords carry a variation selector)
    for title in _all_titles():
        assert "#" not in title, title
        assert "—" not in title and "--" not in title, title
        assert sum(ch in emoji for ch in title) <= 1, title
        for shout in ("INSANE", "DISGUSTING", "UNREAL", "CRAZY"):
            assert shout not in title, title


def test_kill_clip_titles_vary_in_shape_not_just_words():
    # Three hook styles must produce genuinely different sentence shapes, so a day's uploads
    # don't read as one template with the champion swapped in.
    highlight = ClipHighlight(kill_streak=3, champion="JarvanIV", victim_champions=["Zed", "Ahri", "Lux"])
    shapes = set()
    for ms in range(0, 30):
        highlight.first_kill_ms = ms * 31_000
        shapes.add(build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II").title)
    assert len(shapes) == 3
    assert any(t.startswith("Wait for") for t in shapes)
    assert any(t.startswith("This is why") for t in shapes)
    assert any(t.startswith("Jarvan IV") for t in shapes)


def test_solo_kill_lesson_variant_is_the_matchup_search_phrase():
    # Across kill timestamps the "why" variant must surface, and it should be the literal
    # "X as Y" matchup query -- the single best search phrase a solo-kill clip can own.
    found = False
    for second in range(0, 40):
        highlight = ClipHighlight(kill_streak=1, champion="JarvanIV", victim_champions=["Zed"])
        meta = build_clip_metadata(_match(), highlight, RECORDED_AT.replace(second=second), rank=None)
        if meta.title_variant == "why":
            assert meta.title.startswith("How to punish Zed as Jarvan IV")
            found = True
    assert found
    assert "jarvan iv vs zed" in meta.tags  # the matchup is also a search tag


def test_rank_role_context_is_dropped_before_the_hook_is_truncated():
    # A long hook keeps its meaning; the "| Emerald Jungle" suffix is what gives way.
    highlight = ClipHighlight(kill_streak=4, champion="AurelionSol",
                              victim_champions=["MissFortune", "TahmKench", "Lux", "Zed"], first_kill_ms=0)
    meta = build_clip_metadata(_match(champion="AurelionSol", role="Support"), highlight, RECORDED_AT,
                               rank="Grandmaster")
    assert len(meta.title) <= 80 or "|" not in meta.title
    assert "…" not in meta.title


def test_description_leads_with_the_searchable_sentence():
    highlight = ClipHighlight(kill_streak=2, champion="JarvanIV", victim_champions=["MissFortune", "Leona"])
    meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")
    first_line = meta.description.splitlines()[0]
    assert first_line.startswith("Jarvan IV double kill on Miss Fortune & Leona in Ranked Solo/Duo")
    assert "Emerald II" in first_line and "patch 26.13" in first_line
    assert "👉" not in meta.description and "--" not in meta.description
    assert meta.description.rstrip().endswith(" ".join(meta.hashtags))


def test_tags_respect_budget_and_dedupe():
    highlight = ClipHighlight(kill_streak=3, champion="JarvanIV", victim_champions=["Ashe", "Leona", "Zed"])
    meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")

    assert sum(len(t) for t in meta.tags) <= YOUTUBE_TAGS_CHAR_BUDGET
    assert len({t.lower() for t in meta.tags}) == len(meta.tags)
    assert "league of legends" in meta.tags


def test_title_variants_rotate_and_are_recorded():
    # Across many kill timestamps every A/B style should appear, and the style used must be
    # recorded on the metadata so the dashboard can score it later.
    seen = set()
    for ms in range(0, 40):
        highlight = ClipHighlight(
            kill_streak=3, champion="JarvanIV", victim_champions=["Zed", "Ahri", "Lux"],
            first_kill_ms=ms * 31_000,
        )
        meta = build_clip_metadata(_match(), highlight, RECORDED_AT, rank="Emerald II")
        assert meta.title_variant in ("hype", "wait", "why")
        seen.add(meta.title_variant)
        assert len(meta.title) <= YOUTUBE_TITLE_LIMIT
    assert seen == {"hype", "wait", "why"}


def test_title_variant_is_deterministic_per_kill():
    highlight = ClipHighlight(kill_streak=2, champion="JarvanIV",
                              victim_champions=["MissFortune", "Leona"], first_kill_ms=840_000)
    meta1 = build_clip_metadata(_match(), highlight, RECORDED_AT, rank=None)
    meta2 = build_clip_metadata(_match(), highlight, RECORDED_AT, rank=None)
    assert meta1.title == meta2.title
    assert meta1.title_variant == meta2.title_variant


def test_no_variant_without_a_kill():
    # Full games and highlight-less clips have nothing to A/B -- no variant recorded.
    assert build_clip_metadata(_match(), None, RECORDED_AT).title_variant is None
    assert build_clip_metadata(None, None, RECORDED_AT).title_variant is None
    assert build_full_game_metadata(_match(), RECORDED_AT).title_variant is None
