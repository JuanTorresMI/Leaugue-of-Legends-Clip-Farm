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


def _seed_clip(conn, *, match="NA1_1", streak=1, recorded="2026-07-04T20:00:00", status="ready", account="Me#NA1"):
    mid = db.insert_media_file(conn, f"C:/clips/{match}_{streak}_{recorded}.mp4", "clip", recorded)
    db.update_media_file(
        conn, mid, riot_match_id=match, kill_streak=streak, account=account,
        status=status, champion="JarvanIV", draft_title=f"clip {streak}", draft_description="d",
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


def test_retry_failed_autoposts_backfills(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        mid = _seed_clip(conn, streak=3)
        db.upsert_publish_target(conn, mid, "youtube", selected=True)
        db.record_autopost(conn, mid, "youtube", "clip")
        db.set_publish_target_status(conn, mid, "youtube", "failed", error_message="blip")
        # Backdate so it's outside the "don't hammer" window and gets picked up.
        conn.execute(
            "UPDATE publish_targets SET updated_at = datetime('now', '-1 hour') WHERE media_file_id = ?", (mid,)
        )
    assert scheduler.retry_failed_autoposts() == 1
    assert (mid, "youtube") in publish_calls


def test_retry_skips_recent_failures(temp_db, publish_calls, monkeypatch):
    _use_settings(monkeypatch)
    with db.get_conn() as conn:
        mid = _seed_clip(conn, streak=3)
        db.upsert_publish_target(conn, mid, "youtube", selected=True)
        db.record_autopost(conn, mid, "youtube", "clip")
        db.set_publish_target_status(conn, mid, "youtube", "failed")  # updated_at = now -> too recent
    assert scheduler.retry_failed_autoposts() == 0  # don't hammer a just-failed upload
