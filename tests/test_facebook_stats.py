"""Facebook stats provider: node-field basics always, insights when read_insights is granted,
and per-metric graceful degradation (a 403 on insights must not drop the row)."""
from types import SimpleNamespace

import pytest

from clipfarm.metrics import base, facebook_stats


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _configured_token(monkeypatch):
    monkeypatch.setattr(facebook_stats, "_token", lambda: "TOK")


def _wire(monkeypatch, *, node, by_family):
    """by_family maps a metric-name substring ('total_video' / 'blue_reels') to (status, payload).
    Lets a test serve the regular-video family and the Reel family different responses, matching
    how Facebook 400s the wrong family for a given video type."""

    def fake_get(url, params=None, timeout=None):
        if url.endswith("/video_insights"):
            metric = (params or {}).get("metric", "")
            for key, (status, payload) in by_family.items():
                if key in metric:
                    return _FakeResp(payload, status=status)
            return _FakeResp({"data": []})
        return _FakeResp(node)

    monkeypatch.setattr(facebook_stats, "requests", SimpleNamespace(get=fake_get, RequestException=Exception))


def test_regular_video_insights_map_and_convert(monkeypatch):
    """Full game (regular Page video): total_video_* family returns data; Reel family 400s."""
    _wire(
        monkeypatch,
        node={"views": "1500", "likes": {"summary": {"total_count": 40}}, "comments": {"summary": {"total_count": 7}}},
        by_family={
            "total_video": (200, {"data": [
                {"name": "total_video_impressions", "values": [{"value": 9000}]},
                {"name": "total_video_view_total_time", "values": [{"value": 600000}]},  # ms -> 10 min
                {"name": "total_video_avg_time_watched", "values": [{"value": 5000}]},  # ms -> 5 s
            ]}),
            "blue_reels": (400, {"error": {"message": "(#100) invalid metric for this video"}}),
        },
    )
    stats = facebook_stats.fetch(["V1"])["V1"]
    assert stats["views"] == 1500 and stats["likes"] == 40 and stats["comments"] == 7
    assert stats["impressions"] == 9000
    assert stats["watch_time_minutes"] == 10.0
    assert stats["avg_view_seconds"] == 5.0


def test_reel_insights_map_and_convert(monkeypatch):
    """Clip (Reel): blue_reels/post_video_* family returns data; regular-video family is empty."""
    _wire(
        monkeypatch,
        node={"views": "8", "likes": {"summary": {"total_count": 0}}},
        by_family={
            "total_video": (200, {"data": []}),  # regular-video metrics come back empty for a Reel
            "blue_reels": (200, {"data": [
                {"name": "blue_reels_play_count", "values": [{"value": 6}]},
                {"name": "post_video_view_time", "values": [{"value": 69940}]},  # ms -> 1.17 min
                {"name": "post_video_avg_time_watched", "values": [{"value": 11656}]},  # ms -> 11.66 s
            ]}),
        },
    )
    stats = facebook_stats.fetch(["V1"])["V1"]
    assert stats["views"] == 8
    assert stats["reel_plays"] == 6  # unknown column -> lands in extra_json downstream
    assert stats["watch_time_minutes"] == 1.17
    assert stats["avg_view_seconds"] == 11.66


def test_insights_403_keeps_basic_stats(monkeypatch):
    """Before reauth: read_insights missing -> both families 403, but views/likes still recorded."""
    err = {"error": {"message": "(#200) read_insights permission missing"}}
    _wire(
        monkeypatch,
        node={"views": "300", "likes": {"summary": {"total_count": 2}}},
        by_family={"total_video": (403, err), "blue_reels": (403, err)},
    )
    stats = facebook_stats.fetch(["V1"])["V1"]
    assert stats["views"] == 300 and stats["likes"] == 2
    assert "impressions" not in stats and "watch_time_minutes" not in stats


def test_unconfigured_returns_empty(monkeypatch):
    monkeypatch.setattr(facebook_stats, "_token", lambda: None)
    assert facebook_stats.fetch(["V1"]) == {}


def test_provider_is_registered():
    provider = base.get("facebook")
    assert provider is not None and provider.platform == "facebook"
