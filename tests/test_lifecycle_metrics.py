"""File-lifecycle archiving + video metrics storage/analysis."""
from types import SimpleNamespace

import pytest

from clipfarm import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "clipfarm.sqlite3"
    settings = SimpleNamespace(database=SimpleNamespace(path=dbfile))
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.init_db()
    return dbfile


# --- file lifecycle -------------------------------------------------------------------------

def test_list_queue_excludes_archived(temp_db):
    with db.get_conn() as conn:
        live = db.insert_media_file(conn, "C:/v/live.mp4", "clip", "2026-07-01T00:00:00")
        gone = db.insert_media_file(conn, "C:/v/gone.mp4", "clip", "2026-07-01T00:00:00")
        db.mark_source_deleted(conn, gone)
        ids = [r["id"] for r in db.list_queue(conn)]
    assert live in ids and gone not in ids


def test_reconcile_archives_missing_sources(temp_db, tmp_path, monkeypatch):
    from clipfarm.jobs import cleanup

    # cleanup uses get_settings() for derived paths + the unmount guard; point both at tmp
    # (tmp_path exists, so the "drive offline" guard passes).
    fake = SimpleNamespace(
        project_root=tmp_path,
        ascent=SimpleNamespace(full_games_dir=tmp_path, clips_dir=tmp_path),
    )
    monkeypatch.setattr(cleanup, "get_settings", lambda: fake)

    present = tmp_path / "present.mp4"
    present.write_bytes(b"x")
    with db.get_conn() as conn:
        keep = db.insert_media_file(conn, str(present), "clip", "2026-07-01T00:00:00")
        drop = db.insert_media_file(conn, str(tmp_path / "deleted.mp4"), "clip", "2026-07-01T00:00:00")

    assert cleanup.reconcile_missing_sources() == 1  # only the missing one archived
    with db.get_conn() as conn:
        assert db.get_media_file(conn, keep)["source_deleted_at"] is None
        assert db.get_media_file(conn, drop)["source_deleted_at"] is not None


# --- metrics --------------------------------------------------------------------------------

def _published_video(conn, video_id, published_at, views, title="t"):
    mid = db.insert_media_file(conn, f"C:/v/{video_id}.mp4", "clip", "2026-07-01T00:00:00")
    db.update_media_file(conn, mid, published_at=published_at, draft_title=title)
    db.insert_video_stats(conn, "youtube", video_id, mid, {"views": views, "likes": 3, "custom_metric": 42})
    return mid


def test_video_stats_roundtrip_and_extra_json(temp_db):
    with db.get_conn() as conn:
        _published_video(conn, "V1", "2026-07-01 09:00:00", 100)
        rows = db.latest_video_stats(conn)
    assert len(rows) == 1
    assert rows[0]["views"] == 100
    assert '"custom_metric": 42' in (rows[0]["extra_json"] or "")  # unknown metric kept, no migration


def test_latest_snapshot_wins(temp_db):
    with db.get_conn() as conn:
        mid = _published_video(conn, "V1", "2026-07-01 09:00:00", 100)
        db.insert_video_stats(conn, "youtube", "V1", mid, {"views": 500})  # newer snapshot
        rows = db.latest_video_stats(conn)
    assert len(rows) == 1 and rows[0]["views"] == 500


def test_analysis_ranks_hours_and_recommends(temp_db):
    from clipfarm.metrics import analysis

    with db.get_conn() as conn:
        _published_video(conn, "V_low", "2026-07-01 09:00:00", 100)
        _published_video(conn, "V_high", "2026-07-01 20:00:00", 2000)

    perf = analysis.hour_performance()
    assert perf[0]["avg_views"] == 2000  # the 2000-view slot ranks first
    rec = analysis.recommended_hours(count=1, min_samples=1)
    assert len(rec) == 1
    summary = analysis.summary()
    assert summary["tracked_videos"] == 2 and summary["total_views"] == 2100
