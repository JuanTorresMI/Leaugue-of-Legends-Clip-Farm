"""Facebook stats provider -- the FB parallel of youtube_stats.

Two tiers, both reusing the saved Page token (data/facebook.json):
  - **Node fields** (`GET /{video-id}?fields=views,likes,comments`): views / likes / comments.
    Works with the basic Page token you already have -- no reauth needed.
  - **Insights** (`GET /{video-id}/video_insights`): impressions, total watch time, average time
    watched. Requires the `read_insights` permission, so it lights up after you run
    `python -m clipfarm.cli reauth-facebook`. Until then it's skipped gracefully and only the
    node-field numbers are recorded.

Everything degrades per-metric: a missing field or a 403 on insights never drops the row.
"""
from __future__ import annotations

import logging

import requests

from clipfarm.metrics import base
from clipfarm.publishers.facebook import GRAPH_API_BASE

logger = logging.getLogger(__name__)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _token() -> str | None:
    from clipfarm import fb_settings

    s = fb_settings.current()
    return s.access_token if s.configured else None


def _basic(video_id: str, token: str) -> dict:
    """views / likes / comments from the video node -- available without read_insights."""
    out: dict = {}
    resp = requests.get(
        f"{GRAPH_API_BASE}/{video_id}",
        params={"access_token": token, "fields": "views,likes.summary(true),comments.summary(true)"},
        timeout=30,
    )
    if resp.status_code != 200:
        return out
    data = resp.json()
    if data.get("views") is not None:
        out["views"] = _int(data["views"])
    likes = data.get("likes", {}).get("summary", {}).get("total_count")
    comments = data.get("comments", {}).get("summary", {}).get("total_count")
    if likes is not None:
        out["likes"] = _int(likes)
    if comments is not None:
        out["comments"] = _int(comments)
    return out


def _ms_to_min(v):
    return round(v / 60000.0, 2) if v else v


def _ms_to_sec(v):
    return round(v / 1000.0, 2) if v else v


# Insight metric name -> (our column, transform). Facebook returns durations in milliseconds.
# Two families, because clips publish as Reels and full games as regular Page videos, and each
# exposes a DIFFERENT set of insight metrics. They must be requested in SEPARATE calls: mixing a
# metric that's invalid for the given video type 400s the whole request, so a Reel-only metric in
# a full-game's call (or vice-versa) would wipe out every metric. Requesting each family on its
# own lets the wrong-family call 400 harmlessly while the right one returns data.
_REEL_METRICS = {
    "blue_reels_play_count": ("reel_plays", _int),  # unknown column -> kept in extra_json
    "post_video_view_time": ("watch_time_minutes", _ms_to_min),
    "post_video_avg_time_watched": ("avg_view_seconds", _ms_to_sec),
}
_VIDEO_METRICS = {
    "total_video_impressions": ("impressions", _int),
    "total_video_view_total_time": ("watch_time_minutes", _ms_to_min),
    "total_video_avg_time_watched": ("avg_view_seconds", _ms_to_sec),
}


def _insights_family(video_id: str, token: str, metric_map: dict) -> dict:
    """Fetch one metric family; empty on 403 (no read_insights) / 400 (wrong family for this
    video type) / transient error, so the other family and the basic stats still land."""
    out: dict = {}
    resp = requests.get(
        f"{GRAPH_API_BASE}/{video_id}/video_insights",
        params={"access_token": token, "metric": ",".join(metric_map)},
        timeout=30,
    )
    if resp.status_code != 200:
        return out
    for item in resp.json().get("data", []):
        name = item.get("name")
        values = item.get("values") or [{}]
        raw = values[0].get("value")
        if name in metric_map and raw is not None:
            col, transform = metric_map[name]
            out[col] = transform(raw)
    return out


def _insights(video_id: str, token: str) -> dict:
    """impressions / watch time / avg time watched -- needs read_insights (post-reauth).
    Merges the Reel and regular-video families; whichever matches the video type wins."""
    out = _insights_family(video_id, token, _VIDEO_METRICS)
    out.update(_insights_family(video_id, token, _REEL_METRICS))  # Reel values override if present
    if not out:
        logger.info("FB insights empty for %s (read_insights missing or no data yet).", video_id)
    return out


def fetch(video_ids: list[str]) -> dict[str, dict]:
    if not video_ids:
        return {}
    token = _token()
    if not token:
        logger.info("Facebook not configured; skipping FB stats.")
        return {}

    out: dict[str, dict] = {}
    for vid in video_ids:
        stat: dict = {}
        try:
            stat.update(_basic(vid, token))
        except requests.RequestException:
            logger.info("FB basic stats fetch failed for %s", vid, exc_info=True)
        try:
            stat.update(_insights(vid, token))
        except requests.RequestException:
            logger.info("FB insights fetch failed for %s", vid, exc_info=True)
        if stat:
            out[vid] = stat
    return out


base.register(base.StatsProvider(platform="facebook", fetch=fetch))
