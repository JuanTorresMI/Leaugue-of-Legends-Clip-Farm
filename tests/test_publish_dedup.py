"""End-to-end proof that the same content can't be published to a platform twice."""
from types import SimpleNamespace

import pytest

from clipfarm import db
from clipfarm.jobs import publish_job
from clipfarm.publishers import registry


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    dbfile = tmp_path / "clipfarm.sqlite3"
    settings = SimpleNamespace(database=SimpleNamespace(path=dbfile))
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.init_db()
    return dbfile


def _seed_item(conn) -> int:
    mid = db.insert_media_file(conn, "C:/clips/penta.mp4", "clip", "2026-07-04T20:35:02")
    db.update_media_file(
        conn, mid,
        content_hash="hash123", riot_match_id="NA1_1", highlight_ms=840000,
        kill_streak=5, champion="JarvanIV", draft_title="t", draft_description="d",
    )
    db.upsert_publish_target(conn, mid, "youtube", selected=True)
    return mid


def _stub_platform(monkeypatch, calls):
    def publish(media_file):
        calls.append(media_file["id"])
        return "yt-video-1"

    stub = registry.Platform(name="youtube", kinds=("clip",), is_enabled=lambda: True, publish=publish)
    monkeypatch.setattr(registry, "get", lambda name: stub if name == "youtube" else None)


def test_first_publish_records_ledger(temp_db, monkeypatch):
    calls: list[int] = []
    _stub_platform(monkeypatch, calls)
    with db.get_conn() as conn:
        mid = _seed_item(conn)

    publish_job._run(mid)

    assert calls == [mid]  # the publisher ran exactly once
    with db.get_conn() as conn:
        target = db.get_publish_targets(conn, mid)[0]
        assert target["status"] == "published"
        # Every content signature is now in the ledger.
        assert db.find_published_content(conn, "youtube", ["clip:NA1_1:840000"]) is not None
        assert db.find_published_content(conn, "youtube", ["file:hash123"]) is not None


def test_duplicate_content_is_blocked(temp_db, monkeypatch):
    calls: list[int] = []
    _stub_platform(monkeypatch, calls)
    with db.get_conn() as conn:
        mid = _seed_item(conn)
        # Simulate an earlier upload of the same match/kill (e.g. a re-recorded or renamed file).
        db.record_published_content(
            conn, "youtube", ["clip:NA1_1:840000"], media_file_id=999, platform_video_id="old-vid"
        )

    publish_job._run(mid)

    assert calls == []  # the publisher was never invoked -- no duplicate upload
    with db.get_conn() as conn:
        target = db.get_publish_targets(conn, mid)[0]
        assert target["status"] == "duplicate"
        assert "old-vid" in (target["error_message"] or "")
        # The item still leaves the review queue (duplicate counts as done).
        assert db.get_media_file(conn, mid)["status"] == "published"


def test_failed_upload_releases_reservation_for_retry(temp_db, monkeypatch):
    """A failed publish must not leave its content signatures locked, or the item could never
    be retried and would look permanently 'duplicate'."""
    attempts = {"n": 0}

    def flaky_publish(media_file):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("network blip")
        return "yt-video-2"

    stub = registry.Platform(name="youtube", kinds=("clip",), is_enabled=lambda: True, publish=flaky_publish)
    monkeypatch.setattr(registry, "get", lambda name: stub if name == "youtube" else None)

    with db.get_conn() as conn:
        mid = _seed_item(conn)

    publish_job._run(mid)  # first attempt fails
    with db.get_conn() as conn:
        assert db.get_publish_targets(conn, mid)[0]["status"] == "failed"
        # Reservation was released -> the keys are free again.
        assert db.find_published_content(conn, "youtube", ["clip:NA1_1:840000"]) is None

    publish_job._run(mid)  # retry succeeds
    with db.get_conn() as conn:
        assert db.get_publish_targets(conn, mid)[0]["status"] == "published"
        assert db.find_published_content(conn, "youtube", ["clip:NA1_1:840000"]) is not None


def test_file_hash_alone_blocks_a_renamed_copy(temp_db, monkeypatch):
    """Even with no Riot match, an identical re-ingested file is caught by its content hash."""
    calls: list[int] = []
    _stub_platform(monkeypatch, calls)
    with db.get_conn() as conn:
        mid = db.insert_media_file(conn, "C:/clips/copy.mp4", "clip", "2026-07-04T20:35:02")
        db.update_media_file(conn, mid, content_hash="hashXYZ", draft_title="t", draft_description="d")
        db.upsert_publish_target(conn, mid, "youtube", selected=True)
        db.record_published_content(conn, "youtube", ["file:hashXYZ"], media_file_id=1, platform_video_id="orig")

    publish_job._run(mid)

    assert calls == []
    with db.get_conn() as conn:
        assert db.get_publish_targets(conn, mid)[0]["status"] == "duplicate"


def test_interrupted_upload_is_recovered_and_can_republish(temp_db, monkeypatch):
    """A process killed mid-upload leaves the target 'uploading' with an unfinalized ledger
    reservation. Startup recovery must turn it into a retryable failure, and the retry must
    actually upload instead of being blocked by its own stale reservation."""
    calls: list[int] = []
    _stub_platform(monkeypatch, calls)
    with db.get_conn() as conn:
        mid = _seed_item(conn)
        keys = ["file:hash123", "clip:NA1_1:840000"]
        db.set_publish_target_status(conn, mid, "youtube", "uploading")
        assert db.try_reserve_content(conn, "youtube", keys, mid)
        # A real earlier publish of other content must survive recovery untouched.
        db.record_published_content(conn, "youtube", ["match:NA1_9"], media_file_id=99, platform_video_id="old")

    with db.get_conn() as conn:
        assert db.recover_interrupted_uploads(conn) == 1
        target = db.get_publish_targets(conn, mid)[0]
        assert target["status"] == "failed"
        assert "Interrupted" in target["error_message"]
        assert db.find_published_content(conn, "youtube", keys) is None
        assert db.find_published_content(conn, "youtube", ["match:NA1_9"]) is not None

    publish_job._run(mid)

    assert calls == [mid]
    with db.get_conn() as conn:
        assert db.get_publish_targets(conn, mid)[0]["status"] == "published"


def test_recovery_is_a_noop_without_interrupted_uploads(temp_db):
    with db.get_conn() as conn:
        _seed_item(conn)
        assert db.recover_interrupted_uploads(conn) == 0
