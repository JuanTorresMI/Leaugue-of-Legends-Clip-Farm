"""Regression tests for the Facebook Page-video chunked upload (the '(#100) target is required'
bug: the transfer phase was POSTing to /{upload_session_id} instead of /{page-id}/videos)."""
from types import SimpleNamespace

import pytest

from clipfarm.publishers import facebook


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.content = b"x"
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def captured_posts(monkeypatch):
    posts: list[dict] = []

    def fake_post(url, data=None, files=None, headers=None, timeout=None):
        posts.append({"url": url, "data": data or {}, "files": files, "timeout": timeout})
        phase = (data or {}).get("upload_phase")
        if phase == "start":
            return _FakeResp(
                {"video_id": "VID1", "upload_session_id": "SESS9", "start_offset": "0", "end_offset": "10"}
            )
        if phase == "transfer":
            return _FakeResp({"start_offset": "10", "end_offset": "10"})  # done after one chunk
        return _FakeResp({"success": True})

    monkeypatch.setattr(facebook, "requests", SimpleNamespace(post=fake_post))
    monkeypatch.setattr(facebook, "_credentials", lambda: ("PAGE123", "TOK"))
    return posts


def test_page_video_transfer_hits_page_edge_with_session_id(tmp_path, captured_posts):
    video = tmp_path / "game.mp4"
    video.write_bytes(b"0123456789")  # 10 bytes -> one transfer chunk

    vid = facebook.publish_page_video(video, "Title", "Desc")
    assert vid == "VID1"

    transfer = next(p for p in captured_posts if p["data"].get("upload_phase") == "transfer")
    # The whole point: transfer goes to /{page-id}/videos, not /{upload_session_id}.
    assert transfer["url"].endswith("/PAGE123/videos")
    assert transfer["data"]["upload_session_id"] == "SESS9"
    assert "SESS9" not in transfer["url"]


def test_page_video_runs_all_three_phases(tmp_path, captured_posts):
    video = tmp_path / "game.mp4"
    video.write_bytes(b"0123456789")
    facebook.publish_page_video(video, "T", "D")
    phases = [p["data"].get("upload_phase") for p in captured_posts]
    assert phases == ["start", "transfer", "finish"]


def test_every_graph_call_has_a_timeout(tmp_path, captured_posts):
    """A stalled connection without a timeout would hang a publish worker forever."""
    video = tmp_path / "game.mp4"
    video.write_bytes(b"0123456789")
    facebook.publish_page_video(video, "title", "desc")
    assert captured_posts and all(p["timeout"] for p in captured_posts)


class _ValidateResp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text
        self.content = text.encode() or b"x"

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def test_validate_returns_page_name(monkeypatch):
    monkeypatch.setattr(facebook.requests, "get", lambda *a, **kw: _ValidateResp(200, {"name": "My Page"}))
    assert facebook.validate_credentials("123", "tok") == "My Page"


def test_validate_network_error_becomes_auth_error(monkeypatch):
    def boom(*a, **kw):
        raise facebook.requests.ConnectionError("offline")

    monkeypatch.setattr(facebook.requests, "get", boom)
    with pytest.raises(facebook.FacebookAuthError, match="Could not reach Facebook"):
        facebook.validate_credentials("123", "tok")


def test_validate_non_json_error_body_does_not_crash(monkeypatch):
    monkeypatch.setattr(facebook.requests, "get", lambda *a, **kw: _ValidateResp(502, None, "Bad Gateway"))
    with pytest.raises(facebook.FacebookAuthError, match="Bad Gateway"):
        facebook.validate_credentials("123", "tok")
