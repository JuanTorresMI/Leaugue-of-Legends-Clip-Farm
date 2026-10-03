"""Deferred custom thumbnails: set only after YouTube finishes processing the upload.

Setting a thumbnail on a still-processing Short is accepted, but YouTube then regenerates the
9:16 Shorts thumbnail from the video and ours never shows -- so publish marks it 'pending' and
the scheduler applies it once videos.list reports the upload processed.
"""
from types import SimpleNamespace

import pytest

from clipfarm import db
from clipfarm.publishers import youtube


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    settings = SimpleNamespace(database=SimpleNamespace(path=tmp_path / "clipfarm.sqlite3"))
    monkeypatch.setattr(db, "get_settings", lambda: settings)
    db.init_db()
    monkeypatch.setattr(youtube, "MediaFileUpload", lambda path: path)
    monkeypatch.setattr(youtube, "_THUMBNAIL_MIN_AGE_MIN", 0)


class _FakeYouTube:
    """videos().list(...) returns the queued processing states; thumbnails().set records calls."""

    def __init__(self, states):
        self.states = dict(states)  # video_id -> (uploadStatus, processingStatus) or None (missing)
        self.thumbnail_sets: list[str] = []
        self._pending = None

    def videos(self):
        return self

    def thumbnails(self):
        return self

    def list(self, part, id):
        state = self.states.get(id)
        items = [] if state is None else [
            {"status": {"uploadStatus": state[0]}, "processingDetails": {"processingStatus": state[1]}}
        ]
        self._pending = {"items": items}
        return self

    def set(self, videoId, media_body):
        self.thumbnail_sets.append(videoId)
        self._pending = {}
        return self

    def execute(self):
        return self._pending


def _published(conn, tmp_path, name, video_id, with_file=True) -> int:
    thumb = tmp_path / f"{name}.jpg"
    if with_file:
        thumb.write_bytes(b"jpg")
    mid = db.insert_media_file(conn, f"C:/clips/{name}.mp4", "clip", "2026-10-03T13:00:00")
    db.update_media_file(conn, mid, thumbnail_path=str(thumb))
    db.upsert_publish_target(conn, mid, "youtube", selected=True)
    db.set_publish_target_status(conn, mid, "youtube", "published", platform_video_id=video_id)
    db.set_thumbnail_status(conn, mid, "youtube", "pending")
    return mid


def _status(mid) -> str:
    with db.get_conn() as conn:
        return db.get_publish_targets(conn, mid)[0]["thumbnail_status"]


def test_waits_while_processing_then_sets_after(temp_db, tmp_path):
    with db.get_conn() as conn:
        mid = _published(conn, tmp_path, "a", "VID_A")
    yt = _FakeYouTube({"VID_A": ("uploaded", "processing")})

    assert youtube.apply_pending_thumbnails(service=yt) == 0
    assert yt.thumbnail_sets == [] and _status(mid) == "pending"  # not touched mid-processing

    yt.states["VID_A"] = ("processed", "succeeded")
    assert youtube.apply_pending_thumbnails(service=yt) == 1
    assert yt.thumbnail_sets == ["VID_A"] and _status(mid) == "set"

    assert youtube.apply_pending_thumbnails(service=yt) == 0  # done; never re-sent
    assert yt.thumbnail_sets == ["VID_A"]


def test_rejected_or_missing_video_is_dropped(temp_db, tmp_path):
    with db.get_conn() as conn:
        rejected = _published(conn, tmp_path, "r", "VID_R")
        gone = _published(conn, tmp_path, "g", "VID_G")
    yt = _FakeYouTube({"VID_R": ("rejected", None), "VID_G": None})
    assert youtube.apply_pending_thumbnails(service=yt) == 0
    assert yt.thumbnail_sets == []
    assert _status(rejected) == "failed" and _status(gone) == "failed"


def test_missing_thumbnail_file_is_dropped_without_api_calls(temp_db, tmp_path):
    with db.get_conn() as conn:
        mid = _published(conn, tmp_path, "m", "VID_M", with_file=False)
    yt = _FakeYouTube({})
    assert youtube.apply_pending_thumbnails(service=yt) == 0
    assert _status(mid) == "failed"


def test_nothing_pending_does_not_touch_youtube(temp_db):
    # service=None would try to load real credentials; returning early proves no API use.
    assert youtube.apply_pending_thumbnails() == 0
