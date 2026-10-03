"""Auto-post scheduler: scoring, eligibility, quality/per-game gates, and cadence gating."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from clipfarm import autopost, db
from clipfarm.autopost import AutopostSettings
from clipfarm.jobs import scheduler


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "clipfarm.sqlite3"
    settings = SimpleNamespace(database=SimpleNamespace(path=dbfile))
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.init_db()
    return dbfile


@pytest.fixture
def publish_calls(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(
        "clipfarm.jobs.publish_job.submit_publish_job",
        lambda mid, only_platform=None: calls.append((mid, only_platform)),
    )
    monkeypatch.setattr(scheduler, "_current_riot_id", lambda: "Me#NA1")
    return calls


def _use_settings(monkeypatch, **overrides):
    base = dict(enabled=True, post_hours=[0], min_kill_streak=1, per_game_cap=3, min_gap_minutes=90,
                platforms=["youtube"])
    base.update(overrides)
    s = AutopostSettings(**base).normalized()
    monkeypatch.setattr(autopost, "current", lambda: s)
    # Auto-post now targets all *enabled* platforms (from the publisher registry). Tests run with
    # a minimal settings object, so stub the seam to the platforms the scenario configures.
    monkeypatch.setattr(
        scheduler, "enabled_targets",
        lambda kind: s.full_game_platforms if kind == "full_game" else s.platforms,
    )
    return s


def _seed_clip(conn, *, match="NA1_1", streak=1, recorded="2026-07-04T20:00:00", status="ready", account="Me#NA1",
               champion="JarvanIV", title=None, variant=None, alternates=None):
    mid = db.insert_media_file(conn, f"C:/clips/{match}_{streak}_{recorded}_{champion}.mp4", "clip", recorded)
    db.update_media_file(
        conn, mid, riot_match_id=match, kill_streak=streak, account=account,
        status=status, champion=champion, draft_title=title or f"clip {streak}", draft_description="d",
        title_variant=variant, title_alternates=alternates,
    )
    return mid


def _today_at(hour: int) -> datetime:
    return datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)


def test_clip_score_ranks_by_streak():
    assert scheduler.clip_score({"kill_streak": 5}) == 100
    assert scheduler.clip_score({"kill_streak": 2}) == 40
    assert scheduler.clip_score({"kill_streak": 1}) == 15
    assert scheduler.clip_score({"kill_streak": None}) == 5  # no-kill highlight


def test_disabled_does_nothing(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, enabled=False)
    with db.get_conn() as conn:
        _seed_clip(conn, streak=5)
    assert scheduler.run_once(_today_at(12)) is None
    assert publish_calls == []


def test_before_first_slot_does_nothing(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, post_hours=[12])
    with db.get_conn() as conn:
        _seed_clip(conn, streak=5)
    assert scheduler.run_once(_today_at(9)) is None  # 9am, first slot is noon
    assert publish_calls == []


def test_posts_highest_value_clip_first(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        _seed_clip(conn, match="NA1_A", streak=2)
        penta = _seed_clip(conn, match="NA1_B", streak=5)
    posted = scheduler.run_once(_today_at(13))
    assert posted == penta  # the pentakill, not the double
    assert publish_calls == [(penta, "youtube")]
    with db.get_conn() as conn:
        assert db.autopost_count_today(conn, "youtube", datetime.now().date().isoformat()) == 1


def test_daily_cap_respected(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, post_hours=[0])  # one slot -> cap of 1/day
    with db.get_conn() as conn:
        _seed_clip(conn, match="NA1_A", streak=5)
        _seed_clip(conn, match="NA1_B", streak=4)
    assert scheduler.run_once(_today_at(13)) is not None
    assert scheduler.run_once(_today_at(13)) is None  # already hit today's single slot
    assert len(publish_calls) == 1


def test_quality_gate_skips_no_kill_highlight(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, min_kill_streak=1)
    with db.get_conn() as conn:
        _seed_clip(conn, streak=None)  # matched but no kill detected
    assert scheduler.run_once(_today_at(13)) is None
    assert publish_calls == []


def test_min_streak_two_skips_solo_kills(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, min_kill_streak=2)
    with db.get_conn() as conn:
        _seed_clip(conn, streak=1)  # solo kill, below the gate
    assert scheduler.run_once(_today_at(13)) is None


def test_per_game_cap(temp_db, monkeypatch):
    s = _use_settings(monkeypatch, per_game_cap=1)
    with db.get_conn() as conn:
        _seed_clip(conn, match="NA1_SAME", streak=3, recorded="2026-07-04T20:00:00")
        _seed_clip(conn, match="NA1_SAME", streak=3, recorded="2026-07-04T20:05:00")
        # Simulate one already auto-posted from this match.
        db.record_autopost(conn, 1, "youtube")
        pick = scheduler._pick_next(conn, s, "Me#NA1", "youtube")
        assert pick is None  # match is at its cap, the other clip is blocked


def test_only_current_account(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        _seed_clip(conn, streak=5, account="Someone#EUW")  # different account
    assert scheduler.run_once(_today_at(13)) is None


def test_run_once_follows_explore_rotation(temp_db, publish_calls, monkeypatch):
    # In explore mode the scheduler fires on today's rotated slots, not settings.post_hours.
    from clipfarm.autopost import explore_hours_for

    s = _use_settings(monkeypatch, schedule_mode="explore", post_hours=[12, 17, 21],
                      explore_window=[11, 23])
    with db.get_conn() as conn:
        _seed_clip(conn, streak=5)
    first_slot = explore_hours_for(s, datetime.now().date())[0]
    if first_slot > 0:
        assert scheduler.run_once(_today_at(first_slot - 1)) is None  # before today's first slot
    assert scheduler.run_once(_today_at(first_slot)) is not None      # fires exactly on it


def _seed_full_game(conn, *, match="NA1_G", account="Me#NA1"):
    mid = db.insert_media_file(conn, f"C:/games/{match}.mp4", "full_game", "2026-07-04T20:00:00")
    db.update_media_file(conn, mid, riot_match_id=match, account=account, status="ready", draft_title="game")
    return mid


def test_full_game_track_posts_when_enabled(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, full_game_enabled=True, full_game_hours=[0], full_game_platforms=["youtube"])
    with db.get_conn() as conn:
        mid = _seed_full_game(conn)
    assert scheduler.run_full_game_once(_today_at(13)) == mid
    assert publish_calls == [(mid, "youtube")]


def test_full_game_track_off_by_default(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, full_game_enabled=False)
    with db.get_conn() as conn:
        _seed_full_game(conn)
    assert scheduler.run_full_game_once(_today_at(13)) is None
    assert publish_calls == []


def test_clip_and_full_game_tracks_are_independent(temp_db, publish_calls, monkeypatch):
    # A full-game post must not consume the clip track's daily slot (separate 'kind' counters).
    _use_settings(monkeypatch, post_hours=[0], full_game_enabled=True, full_game_hours=[0])
    with db.get_conn() as conn:
        clip = _seed_clip(conn, streak=5)
        game = _seed_full_game(conn)
    assert scheduler.run_full_game_once(_today_at(13)) == game
    assert scheduler.run_once(_today_at(13)) == clip  # clip slot still available


def _seed_failed_target(conn, mid, platform="youtube", *, age="-1 hour", retry_count=0):
    db.upsert_publish_target(conn, mid, platform, selected=True)
    db.set_publish_target_status(conn, mid, platform, "failed", error_message="blip")
    conn.execute(
        "UPDATE publish_targets SET updated_at = datetime('now', ?), retry_count = ? "
        "WHERE media_file_id = ? AND platform = ?",
        (age, retry_count, mid, platform),
    )


def test_retry_backfills_failed_autopost(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        mid = _seed_clip(conn, streak=3)
        db.record_autopost(conn, mid, "youtube", "clip")
        _seed_failed_target(conn, mid, "youtube")
    assert scheduler.retry_failed_publishes() == 1
    assert (mid, "youtube") in publish_calls


def test_retry_covers_manually_approved_failures(temp_db, publish_calls, monkeypatch):
    # An Approve click means "post this" -- a failed manual approval must retry on its own,
    # even on a platform the autopost never logged (the old autopost_log join missed these).
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        mid = _seed_clip(conn, streak=3, status="approved")
        _seed_failed_target(conn, mid, "facebook")
    assert scheduler.retry_failed_publishes() == 1
    assert (mid, "facebook") in publish_calls


def test_retry_skips_recent_failures(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        mid = _seed_clip(conn, streak=3)
        db.upsert_publish_target(conn, mid, "youtube", selected=True)
        db.set_publish_target_status(conn, mid, "youtube", "failed")  # updated_at = now -> too recent
    assert scheduler.retry_failed_publishes() == 0  # don't hammer a just-failed upload


def test_retry_respects_lifetime_cap(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        mid = _seed_clip(conn, streak=3, status="approved")
        _seed_failed_target(conn, mid, "youtube", retry_count=8)  # budget exhausted
    assert scheduler.retry_failed_publishes(max_retries=8) == 0


def test_retry_paced_oldest_first(temp_db, publish_calls, monkeypatch):
    # A backlog drains as a drip: `limit` per tick, oldest failure first.
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        old = _seed_clip(conn, match="NA1_OLD", streak=3, status="approved")
        new = _seed_clip(conn, match="NA1_NEW", streak=3, status="approved")
        _seed_failed_target(conn, old, "youtube", age="-3 days")
        _seed_failed_target(conn, new, "youtube", age="-1 hour")
    assert scheduler.retry_failed_publishes(limit=1) == 1
    assert publish_calls == [(old, "youtube")]


def test_failed_publish_increments_retry_count(temp_db, monkeypatch):
    # publish_job counts each failed attempt against the auto-retry budget.
    from clipfarm.jobs import publish_job

    monkeypatch.setattr(
        "clipfarm.publishers.registry.get",
        lambda platform: SimpleNamespace(publish=lambda mf: (_ for _ in ()).throw(RuntimeError("boom"))),
    )
    with db.get_conn() as conn:
        mid = _seed_clip(conn, streak=3, status="approved")
        db.upsert_publish_target(conn, mid, "youtube", selected=True)
    publish_job._run(mid)
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status, retry_count FROM publish_targets WHERE media_file_id = ?", (mid,)
        ).fetchone()
    assert row["status"] == "failed"
    assert row["retry_count"] == 1


# --- consecutive-post variety: don't post the same champion / title shape back to back -------

def _fire(conn, mid):
    db.update_media_file(conn, mid, status="approved")
    db.record_autopost(conn, mid, "youtube", "clip")


def test_prefers_a_different_champion_than_the_last_post(temp_db, monkeypatch):
    s = _use_settings(monkeypatch)
    with db.get_conn() as conn:
        last = _seed_clip(conn, match="NA1_0", streak=3, champion="Yunara")
        _fire(conn, last)
        same = _seed_clip(conn, match="NA1_1", streak=3, champion="Yunara", recorded="2026-07-04T21:00:00")
        other = _seed_clip(conn, match="NA1_2", streak=2, champion="Draven")
        assert scheduler._pick_next(conn, s, "Me#NA1", "youtube")["id"] == other  # a weaker clip, different champ
        _ = same


def test_same_champion_penta_still_beats_a_solo_kill_of_another(temp_db, monkeypatch):
    s = _use_settings(monkeypatch)
    with db.get_conn() as conn:
        _fire(conn, _seed_clip(conn, match="NA1_0", streak=3, champion="Yunara"))
        penta = _seed_clip(conn, match="NA1_1", streak=5, champion="Yunara", recorded="2026-07-04T21:00:00")
        _seed_clip(conn, match="NA1_2", streak=1, champion="Draven")
        assert scheduler._pick_next(conn, s, "Me#NA1", "youtube")["id"] == penta


def test_vary_consecutive_off_restores_pure_value_ranking(temp_db, monkeypatch):
    s = _use_settings(monkeypatch, vary_consecutive=False)
    with db.get_conn() as conn:
        _fire(conn, _seed_clip(conn, match="NA1_0", streak=3, champion="Yunara"))
        same = _seed_clip(conn, match="NA1_1", streak=3, champion="Yunara", recorded="2026-07-04T21:00:00")
        _seed_clip(conn, match="NA1_2", streak=2, champion="Draven")
        assert scheduler._pick_next(conn, s, "Me#NA1", "youtube")["id"] == same


def test_title_shape_is_switched_when_the_last_post_used_the_same_one(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, min_gap_minutes=0, post_hours=[0, 1])
    alts = {"hype": "Yunara Triple Kill vs Zed", "wait": "Wait for the triple kill… Yunara",
            "why": "This is why you don't fight Yunara", "question": "Rate this Yunara triple kill"}
    with db.get_conn() as conn:
        _fire(conn, _seed_clip(conn, match="NA1_0", streak=3, champion="Yunara", variant="hype",
                               title="Yunara Triple Kill vs Lux"))
        nxt = _seed_clip(conn, match="NA1_1", streak=3, champion="Yunara", recorded="2026-07-04T21:00:00",
                         title=alts["hype"], variant="hype", alternates=alts)
    assert scheduler.run_once(_today_at(12)) == nxt
    with db.get_conn() as conn:
        row = db.get_media_file(conn, nxt)
    assert row["title_variant"] == "wait" and row["draft_title"] == alts["wait"]


def test_hand_edited_title_is_never_swapped(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, min_gap_minutes=0, post_hours=[0, 1])
    alts = {"hype": "Yunara Triple Kill vs Zed", "wait": "Wait for the triple kill… Yunara"}
    with db.get_conn() as conn:
        _fire(conn, _seed_clip(conn, match="NA1_0", streak=3, champion="Yunara", variant="hype"))
        nxt = _seed_clip(conn, match="NA1_1", streak=3, champion="Yunara", recorded="2026-07-04T21:00:00",
                         title="my own title", variant="hype", alternates=alts)
    assert scheduler.run_once(_today_at(12)) == nxt
    with db.get_conn() as conn:
        row = db.get_media_file(conn, nxt)
    assert row["draft_title"] == "my own title" and row["title_variant"] == "hype"


def test_different_shape_than_last_post_is_left_alone(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch, min_gap_minutes=0, post_hours=[0, 1])
    alts = {"hype": "Yunara Triple Kill vs Zed", "wait": "Wait for the triple kill… Yunara"}
    with db.get_conn() as conn:
        _fire(conn, _seed_clip(conn, match="NA1_0", streak=3, champion="Yunara", variant="why"))
        nxt = _seed_clip(conn, match="NA1_1", streak=3, champion="Yunara", recorded="2026-07-04T21:00:00",
                         title=alts["hype"], variant="hype", alternates=alts)
    assert scheduler.run_once(_today_at(12)) == nxt
    with db.get_conn() as conn:
        assert db.get_media_file(conn, nxt)["title_variant"] == "hype"


def test_recent_autoposts_newest_first(temp_db):
    with db.get_conn() as conn:
        a = _seed_clip(conn, match="NA1_0", champion="Yunara")
        b = _seed_clip(conn, match="NA1_1", champion="Draven", recorded="2026-07-04T21:00:00")
        _fire(conn, a)
        _fire(conn, b)
        recent = db.recent_autoposts(conn, "youtube", "clip", limit=2)
    assert [r["champion"] for r in recent] == ["Draven", "Yunara"]
