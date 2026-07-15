"""Turn the raw stats time-series into decisions and plain-English understanding.

Everything here reads `video_stats` (an append-only time-series: every poll adds a snapshot, we
never delete one), so the numbers only get richer over time. Three jobs:

  - **video_records / classify**: the latest snapshot per video, enriched with its match context
    and a strong / average / weak tier relative to the channel's own median (per platform, since
    YouTube and Facebook are on totally different scales).
  - **insights**: "what's working" -- champion, content-kind, retention, and follower-conversion
    leaders, plus the best local hours to post (fed into the auto-post schedule).
  - **channel_timeline**: cumulative views per day per platform, forward-filled, for the growth
    chart -- this is the long-horizon view that accumulates as the DB lives on.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean

from clipfarm import db

# --- shared helpers -------------------------------------------------------------------------

def _utc_to_local_offset_hours() -> int:
    utc_now = datetime.now(UTC).replace(tzinfo=None)
    return round((datetime.now() - utc_now).total_seconds() / 3600)


def _local_hour(utc_iso: str, offset: int) -> int | None:
    try:
        dt = datetime.fromisoformat(utc_iso.replace(" ", "T"))
    except (ValueError, AttributeError):
        return None
    return (dt.hour + offset) % 24


def _extra(row) -> dict:
    try:
        return json.loads(row["extra_json"]) if row["extra_json"] else {}
    except (ValueError, TypeError):
        return {}


def _url(platform: str, video_id: str) -> str | None:
    if platform == "youtube":
        return f"https://youtu.be/{video_id}"
    if platform == "facebook":
        return f"https://www.facebook.com/{video_id}"
    return None


def _median(values: list[float]) -> float:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return 0.0
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


def _avg(values: list) -> float | None:
    xs = [v for v in values if v is not None]
    return round(mean(xs), 1) if xs else None


# --- per-video records + strong/weak classification -----------------------------------------

def _parse_ts(value: str | None) -> datetime | None:
    """Parse a DB timestamp ('YYYY-MM-DD HH:MM:SS' or ISO) into a naive UTC datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace(" ", "T")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def _velocity_by_video() -> dict[tuple[str, str], int]:
    """Views gained over the last ~24h per video, straight from the append-only time-series:
    latest snapshot minus the newest snapshot at least 24h old (or the video's first snapshot
    if it's younger than that). This is the 'what's moving right now' signal the algorithm
    itself acts on -- a spike here is the earliest visible sign of a clip taking off."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT platform, platform_video_id, fetched_at, views FROM video_stats "
            "WHERE views IS NOT NULL ORDER BY id"
        ).fetchall()
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=24)
    latest: dict[tuple[str, str], int] = {}
    baseline: dict[tuple[str, str], int] = {}
    first: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r["platform"], r["platform_video_id"])
        ts = _parse_ts(r["fetched_at"])
        if ts is None:
            continue
        latest[key] = r["views"]
        first.setdefault(key, r["views"])
        if ts <= cutoff:
            baseline[key] = r["views"]  # rows are id-ordered, so this ends on the newest pre-cutoff
    return {key: views - baseline.get(key, first[key]) for key, views in latest.items()}


def _views_per_day(views: int | None, published_at: str | None) -> float | None:
    """Lifetime views normalized by age, so a week-old and a month-old clip compare fairly.
    Ages under a day are clamped to 1 day to avoid wild extrapolation from a fresh upload."""
    published = _parse_ts(published_at)
    if views is None or published is None:
        return None
    age_days = (datetime.now(UTC).replace(tzinfo=None) - published).total_seconds() / 86400
    return round(views / max(age_days, 1.0), 1)


def video_records() -> list[dict]:
    """Latest snapshot per video, flattened with its match context and the metrics that actually
    exist per platform (YouTube: subs gained; Facebook: reel plays -- both pulled from extra_json),
    plus velocity: views gained in the last 24h and lifetime views/day."""
    with db.get_conn() as conn:
        rows = db.latest_video_stats(conn)
    velocity = _velocity_by_video()
    records = []
    for r in rows:
        extra = _extra(r)
        records.append(
            {
                "platform": r["platform"],
                "platform_video_id": r["platform_video_id"],
                "title": r["draft_title"],
                "title_variant": r["title_variant"],
                "kind": r["kind"],
                "champion": r["champion"],
                "kill_streak": r["kill_streak"],
                "queue_type": r["queue_type"],
                "published_at": r["published_at"],
                "views": r["views"],
                "likes": r["likes"],
                "comments": r["comments"],
                "shares": r["shares"],
                "watch_time_minutes": r["watch_time_minutes"],
                "avg_view_seconds": r["avg_view_seconds"],
                "avg_view_pct": r["avg_view_pct"],
                "subscribers_gained": _int(extra.get("subscribers_gained")),
                "reel_plays": _int(extra.get("reel_plays")),
                "views_24h": velocity.get((r["platform"], r["platform_video_id"])),
                "views_per_day": _views_per_day(r["views"], r["published_at"]),
                "url": _url(r["platform"], r["platform_video_id"]),
            }
        )
    return classify(records)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# Retention (avg % of the video watched) that we consider "great" on a short-form feed. Clips can
# exceed 100% when viewers loop/rewatch, which is a strong signal in its own right.
_GREAT_RETENTION = 60.0


def classify(records: list[dict]) -> list[dict]:
    """Tag each record strong / average / weak vs the channel's own median views for that platform,
    with a retention bump: a modest-view clip that holds people is still 'working'."""
    by_platform_views: dict[str, list[float]] = defaultdict(list)
    for r in records:
        by_platform_views[r["platform"]].append(r["views"] or 0)
    medians = {p: _median(v) for p, v in by_platform_views.items()}

    for r in records:
        med = medians.get(r["platform"], 0)
        views = r["views"] or 0
        ratio = (views / med) if med else None
        retention = r["avg_view_pct"]

        if ratio is None:
            tier = "average"
        elif ratio >= 1.5 or (ratio >= 1.0 and retention is not None and retention >= _GREAT_RETENTION):
            tier = "strong"
        elif ratio <= 0.5 and not (retention is not None and retention >= _GREAT_RETENTION):
            tier = "weak"
        else:
            tier = "average"

        r["tier"] = tier
        r["views_vs_median"] = round(ratio, 2) if ratio is not None else None
    return records


# --- totals ---------------------------------------------------------------------------------

def _aggregate(rows: list[dict]) -> dict:
    return {
        "tracked_videos": len(rows),
        "total_views": sum(r["views"] or 0 for r in rows),
        "total_likes": sum(r["likes"] or 0 for r in rows),
        "total_comments": sum(r["comments"] or 0 for r in rows),
        "total_shares": sum(r["shares"] or 0 for r in rows),
        "total_watch_time_minutes": round(sum(r["watch_time_minutes"] or 0 for r in rows), 1),
        "subscribers_gained": sum(r["subscribers_gained"] or 0 for r in rows),
        "avg_view_pct": _avg([r["avg_view_pct"] for r in rows]),
        "views_24h": sum(r["views_24h"] or 0 for r in rows),
    }


def summary() -> dict:
    """Channel totals overall and split per platform (for the platform tabs)."""
    records = video_records()
    out = _aggregate(records)
    out["by_platform"] = {
        platform: _aggregate([r for r in records if r["platform"] == platform])
        for platform in sorted({r["platform"] for r in records})
    }
    return out


# --- "what's working" insights --------------------------------------------------------------

def _group_perf(records: list[dict], key: str, min_count: int = 1) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        val = r.get(key)
        if val:
            groups[val].append(r)
    perf = [
        {
            key: name,
            "count": len(rows),
            "avg_views": round(mean([x["views"] or 0 for x in rows])),
            "avg_view_pct": _avg([x["avg_view_pct"] for x in rows]),
        }
        for name, rows in groups.items()
        if len(rows) >= min_count
    ]
    perf.sort(key=lambda x: x["avg_views"], reverse=True)
    return perf


def insights(records: list[dict] | None = None) -> dict:
    """The narrative layer: which champions / content kinds / clips are strong vs weak, which hold
    retention, and which actually convert subscribers. Empty-safe on a fresh DB."""
    records = records if records is not None else video_records()
    ret_pool = [r for r in records if r["avg_view_pct"] is not None and (r["views"] or 0) > 0]
    conv_pool = [r for r in records if r["subscribers_gained"]]
    rising_pool = [r for r in records if (r["views_24h"] or 0) > 0]

    # Champion ranking answers "what should I play more of?", so it must compare like with
    # like: clips only. Full games sit at near-zero views by nature, and mixing them in
    # penalized whichever champions happened to have more full games published.
    champ_perf = _group_perf([r for r in records if r["kind"] == "clip"], "champion")
    return {
        "champions": champ_perf,
        "best_champions": champ_perf[:5],
        "weak_champions": [c for c in champ_perf[::-1] if c["count"] >= 1][:5],
        "kind_performance": _group_perf(records, "kind"),
        # Which A/B title hook style is winning (only kill clips carry a variant).
        "title_variants": _group_perf(records, "title_variant"),
        # What the algorithm is pushing RIGHT NOW -- biggest view gains in the last 24h.
        "rising": sorted(rising_pool, key=lambda r: r["views_24h"], reverse=True)[:8],
        "retention_leaders": sorted(ret_pool, key=lambda r: r["avg_view_pct"], reverse=True)[:8],
        "conversion_leaders": sorted(conv_pool, key=lambda r: r["subscribers_gained"], reverse=True)[:8],
        "strong": [r for r in records if r["tier"] == "strong"][:12],
        "weak": [r for r in records if r["tier"] == "weak"][:12],
        "tier_counts": {
            "strong": sum(1 for r in records if r["tier"] == "strong"),
            "average": sum(1 for r in records if r["tier"] == "average"),
            "weak": sum(1 for r in records if r["tier"] == "weak"),
        },
    }


# --- best time to post (feeds the auto-post schedule) ----------------------------------------

def hour_performance() -> list[dict]:
    """Per local upload-hour: sample count and average views, best first."""
    offset = _utc_to_local_offset_hours()
    buckets: dict[int, list[int]] = defaultdict(list)
    for r in video_records():
        published, views = r["published_at"], r["views"]
        if not published or views is None:
            continue
        hour = _local_hour(published, offset)
        if hour is not None:
            buckets[hour].append(views)
    result = [
        {"hour": h, "samples": len(v), "avg_views": round(sum(v) / len(v), 1)}
        for h, v in buckets.items()
    ]
    result.sort(key=lambda r: r["avg_views"], reverse=True)
    return result


def recommended_hours(count: int = 3, min_samples: int = 2) -> list[int]:
    """The `count` best local hours to post at, considering only hours with enough samples.
    Falls back to the strongest hours regardless of sample count if data is still thin."""
    perf = hour_performance()
    confident = [r["hour"] for r in perf if r["samples"] >= min_samples]
    chosen = confident[:count]
    if len(chosen) < count:
        for r in perf:  # backfill from best-overall to reach the requested count
            if r["hour"] not in chosen:
                chosen.append(r["hour"])
            if len(chosen) >= count:
                break
    return sorted(chosen)


# --- long-horizon growth --------------------------------------------------------------------

def channel_timeline() -> list[dict]:
    """Cumulative views by day, per platform, forward-filled. This is the long-term retention
    payoff: as snapshots accumulate over months, this line shows the channel's real trajectory.

    For each day we take the highest view count we saw for each video that day, then carry each
    video's last-known value forward so a day where a video wasn't re-polled doesn't dip the line.
    """
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT date(fetched_at) AS day, platform, platform_video_id, MAX(views) AS views
            FROM video_stats
            WHERE views IS NOT NULL
            GROUP BY day, platform, platform_video_id
            ORDER BY day
            """
        ).fetchall()
    if not rows:
        return []

    days = sorted({r["day"] for r in rows})
    per_day: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    platforms = set()
    for r in rows:
        per_day[r["day"]][(r["platform"], r["platform_video_id"])] = r["views"]
        platforms.add(r["platform"])

    last_known: dict[tuple[str, str], int] = {}
    timeline = []
    for day in days:
        last_known.update(per_day[day])  # newest wins, older videos carry forward
        point = {"date": day, "total_views": sum(last_known.values())}
        for p in sorted(platforms):
            point[f"{p}_views"] = sum(v for (plat, _), v in last_known.items() if plat == p)
        timeline.append(point)
    return timeline
