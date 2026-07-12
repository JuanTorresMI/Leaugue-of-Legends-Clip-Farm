"""YouTube stats provider.

Two tiers, both reusing the existing YouTube OAuth credentials:
  - **Data API** (`videos.list?part=statistics`): views, likes, comments. Works with the current
    `youtube` scope you already authorized -- no re-auth needed.
  - **Analytics API** (`youtubeAnalytics.reports.query`): watch time, average view duration/%,
    shares, and subscribers gained (the last is the real follower-conversion signal -- which
    clips turn viewers into subs). Requires the read-only analytics scope, so it lights up after
    you re-run `python -m clipfarm.cli reauth-youtube` (SCOPES now requests it). Until then it's
    skipped gracefully and only the Data API numbers are recorded. (Impressions & CTR are NOT
    available here -- the on-demand API rejects them; they live only in Studio / the bulk API.)
"""
from __future__ import annotations

import datetime as _dt
import logging

from clipfarm.metrics import base

logger = logging.getLogger(__name__)


def _chunks(items: list[str], n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def fetch(video_ids: list[str]) -> dict[str, dict]:
    if not video_ids:
        return {}
    from clipfarm.publishers.youtube import _build_service

    service = _build_service()
    out: dict[str, dict] = {}
    for batch in _chunks(video_ids, 50):  # Data API allows up to 50 ids per call
        resp = service.videos().list(part="statistics", id=",".join(batch)).execute()
        for item in resp.get("items", []):
            s = item.get("statistics", {})
            out[item["id"]] = {
                "views": _int(s.get("viewCount")),
                "likes": _int(s.get("likeCount")),
                "comments": _int(s.get("commentCount")),
            }

    try:
        _augment_with_analytics(video_ids, out)
    except Exception:  # noqa: BLE001 -- analytics is optional until the scope is granted
        logger.info(
            "YouTube Analytics not available yet (re-auth with the analytics scope to enable "
            "watch time / CTR); recording basic stats only.",
            exc_info=True,
        )
    return out


def _augment_with_analytics(video_ids: list[str], out: dict[str, dict]) -> None:
    from googleapiclient.discovery import build

    from clipfarm.publishers.youtube import _load_credentials

    analytics = build("youtubeAnalytics", "v2", credentials=_load_credentials())
    today = _dt.date.today().isoformat()

    # NOTE: impressions & impressionClickThroughRate are NOT available in the on-demand Analytics
    # API (reports.query rejects them as "Unknown identifier" -- they only exist in YouTube Studio
    # and the heavier bulk Reporting API). So we request only what this API actually serves.
    # Richest set first; fall back to the core retention subset if a metric isn't served for a
    # given video (e.g. subscribersGained can be restricted on brand-new videos).
    metric_sets = [
        "estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained,shares",
        "estimatedMinutesWatched,averageViewDuration,averageViewPercentage",
    ]
    key_map = {
        "estimatedMinutesWatched": "watch_time_minutes",
        "averageViewDuration": "avg_view_seconds",
        "averageViewPercentage": "avg_view_pct",
        "shares": "shares",
        "subscribersGained": "subscribers_gained",  # unknown column -> kept in extra_json
    }

    for vid in video_ids:
        for metrics in metric_sets:
            try:
                resp = (
                    analytics.reports()
                    .query(
                        ids="channel==MINE",
                        startDate="2005-01-01",
                        endDate=today,
                        metrics=metrics,
                        filters=f"video=={vid}",
                    )
                    .execute()
                )
            except Exception:  # noqa: BLE001 -- try the smaller metric set
                continue
            rows = resp.get("rows")
            if not rows:
                break
            headers = [h["name"] for h in resp.get("columnHeaders", [])]
            # strict=False: tolerate a header/row mismatch (API contract break) rather than
            # crash the poll -- missing metrics just stay absent for this video.
            values = dict(zip(headers, rows[0], strict=False))
            stat = out.setdefault(vid, {})
            for api_name, col in key_map.items():
                if api_name in values and values[api_name] is not None:
                    stat[col] = values[api_name]
            break


base.register(base.StatsProvider(platform="youtube", fetch=fetch))
