"""The metrics analysis engine: strong/weak classification, per-platform summary, 'what's
working' insights, and the long-horizon cumulative-views timeline."""
from types import SimpleNamespace

import pytest

from clipfarm import db
from clipfarm.metrics import analysis


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "clipfarm.sqlite3"
    settings = SimpleNamespace(database=SimpleNamespace(path=dbfile))
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.init_db()
    return dbfile


def _publish(conn, video_id, platform, *, views, champion=None, kind="clip",
             retention=None, subs=None, plays=None, published_at="2026-07-01 20:00:00",
             fetched_at=None, title="t"):
    mid = db.insert_media_file(conn, f"C:/v/{video_id}.mp4", kind, "2026-07-01T00:00:00")
    db.update_media_file(conn, mid, published_at=published_at, draft_title=title, champion=champion, kind=kind)
    db.ensure_publish_target(conn, mid, platform, selected=True)
    db.set_publish_target_status(conn, mid, platform, "published", platform_video_id=video_id)
    metrics = {"views": views}
    if retention is not None:
        metrics["avg_view_pct"] = retention
    if subs is not None:
        metrics["subscribers_gained"] = subs
    if plays is not None:
        metrics["reel_plays"] = plays
    db.insert_video_stats(conn, platform, video_id, mid, metrics)
    if fetched_at:  # backdate the snapshot for timeline tests
        conn.execute("UPDATE video_stats SET fetched_at=? WHERE platform_video_id=?", (fetched_at, video_id))
    return mid


def test_classify_strong_weak_by_platform_median(temp_db):
    with db.get_conn() as conn:
        _publish(conn, "hi", "youtube", views=3000)   # well above median -> strong
        _publish(conn, "mid", "youtube", views=1000)  # ~median -> average
        _publish(conn, "lo", "youtube", views=100)    # well below -> weak
    recs = {r["platform_video_id"]: r for r in analysis.video_records()}
    assert recs["hi"]["tier"] == "strong"
    assert recs["lo"]["tier"] == "weak"
    assert recs["mid"]["tier"] == "average"


def test_high_retention_rescues_modest_views(temp_db):
    with db.get_conn() as conn:
        _publish(conn, "a", "youtube", views=3000, retention=20)
        _publish(conn, "b", "youtube", views=1000, retention=95)  # median-ish views but great retention
        _publish(conn, "c", "youtube", views=200, retention=10)
    recs = {r["platform_video_id"]: r for r in analysis.video_records()}
    assert recs["b"]["tier"] == "strong"  # retention bump lifts it above 'average'


def test_summary_splits_by_platform(temp_db):
    with db.get_conn() as conn:
        _publish(conn, "y1", "youtube", views=1000, retention=50, subs=2)
        _publish(conn, "f1", "facebook", views=10, plays=8)
    s = analysis.summary()
    assert s["tracked_videos"] == 2 and s["total_views"] == 1010
    assert set(s["by_platform"]) == {"youtube", "facebook"}
    assert s["by_platform"]["youtube"]["subscribers_gained"] == 2
    assert s["by_platform"]["facebook"]["total_views"] == 10


def test_insights_ranks_champions_and_finds_converters(temp_db):
    with db.get_conn() as conn:
        _publish(conn, "d1", "youtube", views=2000, champion="Draven", retention=70, subs=3)
        _publish(conn, "d2", "youtube", views=1800, champion="Draven", retention=65)
        _publish(conn, "t1", "youtube", views=100, champion="Teemo", retention=20)
    ins = analysis.insights()
    assert ins["best_champions"][0]["champion"] == "Draven"
    assert ins["conversion_leaders"][0]["subscribers_gained"] == 3
    assert ins["retention_leaders"][0]["avg_view_pct"] == 70
    assert ins["tier_counts"]["strong"] >= 1 and ins["tier_counts"]["weak"] >= 1


def test_champion_ranking_ignores_full_games(temp_db):
    # Full games sit at ~0 views by nature; they answer "should I upload VODs?", not "what
    # should I play?". A champion with a dead full game must not rank below one without.
    with db.get_conn() as conn:
        _publish(conn, "k1", "youtube", views=1200, champion="Kassadin")
        _publish(conn, "k2", "youtube", views=0, champion="Kassadin", kind="full_game")
        _publish(conn, "q1", "youtube", views=650, champion="Qiyana")
    champs = {c["champion"]: c for c in analysis.insights()["champions"]}
    assert champs["Kassadin"]["avg_views"] == 1200  # not dragged to 600 by the full game
    assert champs["Kassadin"]["count"] == 1


def test_channel_timeline_forward_fills_and_accumulates(temp_db):
    with db.get_conn() as conn:
        # day 1: one YT video at 100 views
        _publish(conn, "v1", "youtube", views=100, fetched_at="2026-07-01 09:00:00")
        # day 2: v1 grew to 150, and a Facebook video appears at 40
        db.insert_video_stats(conn, "youtube", "v1", None, {"views": 150})
        conn.execute("UPDATE video_stats SET fetched_at='2026-07-02 09:00:00' WHERE views=150")
        _publish(conn, "f1", "facebook", views=40, fetched_at="2026-07-02 10:00:00")
    tl = analysis.channel_timeline()
    assert [p["date"] for p in tl] == ["2026-07-01", "2026-07-02"]
    assert tl[0]["total_views"] == 100
    # day 2 carries v1 forward at its new 150 + facebook 40 = 190
    assert tl[1]["total_views"] == 190
    assert tl[1]["youtube_views"] == 150 and tl[1]["facebook_views"] == 40


def test_velocity_views_gained_last_24h(temp_db):
    with db.get_conn() as conn:
        # v1: snapshot 25h ago at 100 views, fresh snapshot at 150 -> +50 in the last 24h.
        _publish(conn, "v1", "youtube", views=100)
        conn.execute("UPDATE video_stats SET fetched_at = datetime('now', '-25 hours')")
        db.insert_video_stats(conn, "youtube", "v1", None, {"views": 150})
        # v2: published under 24h ago -> delta counted from its first snapshot.
        _publish(conn, "v2", "youtube", views=30)
        db.insert_video_stats(conn, "youtube", "v2", None, {"views": 42})
    recs = {r["platform_video_id"]: r for r in analysis.video_records()}
    assert recs["v1"]["views_24h"] == 50
    assert recs["v2"]["views_24h"] == 12
    assert analysis.summary()["views_24h"] == 62
    rising = analysis.insights()["rising"]
    assert [r["platform_video_id"] for r in rising[:2]] == ["v1", "v2"]  # biggest gainer first


def test_views_per_day_normalizes_by_age(temp_db):
    with db.get_conn() as conn:
        # Published 4 days ago with 200 views -> 50/day. Fresh video clamps to 1 day.
        _publish(conn, "old", "youtube", views=200,
                 published_at="2026-07-08 20:00:00")
        conn.execute("UPDATE media_files SET published_at = datetime('now', '-4 days') "
                     "WHERE id IN (SELECT media_file_id FROM publish_targets WHERE platform_video_id='old')")
        _publish(conn, "new", "youtube", views=80)
        conn.execute("UPDATE media_files SET published_at = datetime('now', '-2 hours') "
                     "WHERE id IN (SELECT media_file_id FROM publish_targets WHERE platform_video_id='new')")
    recs = {r["platform_video_id"]: r for r in analysis.video_records()}
    assert recs["old"]["views_per_day"] == 50.0
    assert recs["new"]["views_per_day"] == 80.0  # age clamped to 1 day


def test_title_variant_performance(temp_db):
    with db.get_conn() as conn:
        for i, (variant, views) in enumerate([("wait", 900), ("wait", 1100), ("hype", 100), ("hype", 140)]):
            mid = _publish(conn, f"t{i}", "youtube", views=views)
            db.update_media_file(conn, mid, title_variant=variant)
    variants = analysis.insights()["title_variants"]
    assert variants[0]["title_variant"] == "wait" and variants[0]["avg_views"] == 1000
    assert variants[1]["title_variant"] == "hype"
