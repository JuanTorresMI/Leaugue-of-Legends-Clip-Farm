"""Auto-post scheduler: drip-publish the best clips on a cadence instead of dumping a batch.

Why this shape (grounded in what grows a Shorts channel in 2026):
  - **Spacing beats volume.** Posting a burst of near-identical clips gets a channel dampened;
    1-3 posts/day spaced across the midday + evening peaks is the sweet spot. We publish at most
    one clip per scheduled hour-slot, with a hard min-gap so a post-downtime catch-up drips.
  - **Quality gate.** No-kill "highlight" clips are skipped; solo kills and up qualify.
  - **Per-game cap.** One match can't flood the feed (the "4 solo kills on the same enemy" case).
  - **Best first.** Each slot fires the highest-value eligible clip (penta > quadra > ... > solo).

The existing publish-time dedup ledger guarantees nothing here can double-post.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime, timedelta

from clipfarm import autopost, db

logger = logging.getLogger(__name__)

# Value of a clip by its detected multi-kill. Drives which clip a slot fires first, and (via the
# quality gate) which clips are eligible at all.
_STREAK_SCORE = {5: 100, 4: 80, 3: 60, 2: 40, 1: 15}

_TICK_LOCK = threading.Lock()

_CANDIDATE_SQL = """
    SELECT m.* FROM media_files m
    WHERE m.kind = 'clip'
      AND m.riot_match_id IS NOT NULL
      AND m.status = 'ready'                     -- matched, not yet touched by manual review
      AND (m.account = ? OR m.account IS NULL)
      AND COALESCE(m.kill_streak, 0) >= ?        -- quality gate (0 = no-kill highlight, skipped)
      AND m.source_deleted_at IS NULL
      AND m.id NOT IN (SELECT media_file_id FROM autopost_log WHERE kind = 'clip')
      AND m.id NOT IN (
            SELECT media_file_id FROM publish_targets
            WHERE platform = ? AND status IN ('published', 'uploading')
      )
"""

_FULL_GAME_CANDIDATE_SQL = """
    SELECT m.* FROM media_files m
    WHERE m.kind = 'full_game'
      AND m.riot_match_id IS NOT NULL
      AND m.status = 'ready'
      AND (m.account = ? OR m.account IS NULL)
      AND m.source_deleted_at IS NULL
      AND m.id NOT IN (SELECT media_file_id FROM autopost_log WHERE kind = 'full_game')
      AND m.id NOT IN (
            SELECT media_file_id FROM publish_targets
            WHERE platform = ? AND status IN ('published', 'uploading')
      )
    ORDER BY m.recorded_at DESC
"""


def clip_score(row) -> int:
    """Higher = post sooner. Solo kill 15 ... pentakill 100; no-kill highlight 5."""
    try:
        streak = row["kill_streak"]
    except (KeyError, IndexError, TypeError):
        streak = getattr(row, "kill_streak", None)
    return _STREAK_SCORE.get(streak or 0, 5)


def _current_riot_id() -> str:
    from clipfarm.accounts import current_account

    return current_account().riot_id


def enabled_targets(kind: str) -> list[str]:
    """Every platform auto-post should publish this media kind to -- i.e. all *enabled* platforms
    for it, straight from the publisher registry. Deriving targets from enablement (rather than a
    separate hand-kept list) is what stops the "I turned Facebook on but nothing posts there" bug:
    enabling a platform in the dashboard automatically includes it here. YouTube is always first
    and always enabled, so it stays the bookkeeping anchor for spacing/daily-count."""
    from clipfarm.publishers import registry

    return registry.enabled_platforms(kind)


def _pick_next(conn, settings: autopost.AutopostSettings, account_id: str, primary: str):
    """The single best eligible clip to post next, or None. Best = highest value, then freshest,
    subject to the per-game cap."""
    rows = conn.execute(_CANDIDATE_SQL, (account_id, settings.min_kill_streak, primary)).fetchall()
    ranked = sorted(rows, key=lambda r: (clip_score(r), r["recorded_at"]), reverse=True)
    for row in ranked:
        if db.autopost_count_for_match(conn, primary, row["riot_match_id"]) < settings.per_game_cap:
            return row
    return None


def _pick_next_full_game(conn, account_id: str, primary: str):
    """The freshest matched full game not yet auto-uploaded, or None."""
    return conn.execute(_FULL_GAME_CANDIDATE_SQL, (account_id, primary)).fetchone()


def _slots_due(hours: list[int], now_local: datetime) -> int:
    """How many of today's scheduled posts should have gone out by now."""
    return sum(1 for h in hours if h <= now_local.hour)


def _spacing_ok(conn, platform: str, kind: str, now_utc: datetime, min_gap_minutes: int) -> bool:
    last = db.autopost_last_fired(conn, platform, kind)
    if last is None:
        return True
    last_dt = datetime.fromisoformat(last.replace(" ", "T"))
    return now_utc - last_dt >= timedelta(minutes=min_gap_minutes)


def _next_slot_hour(hours: list[int], now_local: datetime) -> int | None:
    upcoming = [h for h in hours if h > now_local.hour]
    return upcoming[0] if upcoming else (hours[0] if hours else None)


def due_status(settings: autopost.AutopostSettings | None = None) -> dict:
    """Snapshot for the dashboard: posted-today, daily caps, and next slots for both tracks."""
    settings = settings or autopost.current()
    now_local = datetime.now()
    clip_hours = autopost.effective_post_hours(settings, now_local.date())
    clip_targets = enabled_targets("clip") or ["youtube"]
    fg_targets = enabled_targets("full_game") or ["youtube"]
    with db.get_conn() as conn:
        clip_posted = db.autopost_count_today(conn, clip_targets[0], now_local.date().isoformat(), "clip")
        fg_posted = db.autopost_count_today(
            conn, fg_targets[0], now_local.date().isoformat(), "full_game"
        )
    return {
        "posted_today": clip_posted,
        "daily_cap": len(clip_hours),
        "next_slot_hour": _next_slot_hour(clip_hours, now_local),
        "post_hours": clip_hours,  # today's ACTUAL slots (rotates daily in explore mode)
        "schedule_mode": settings.schedule_mode,
        "platforms": clip_targets,  # where clips actually go (all enabled platforms)
        "full_game": {
            "posted_today": fg_posted,
            "daily_cap": len(settings.full_game_hours),
            "next_slot_hour": _next_slot_hour(settings.full_game_hours, now_local),
            "platforms": fg_targets,
        },
    }


def run_once(now_local: datetime | None = None) -> int | None:
    """Clip track: publish at most one clip if a slot is due, spacing allows, and a clip qualifies.
    Returns the media_file id posted, else None."""
    settings = autopost.current()
    if not settings.enabled:
        return None

    now_local = now_local or datetime.now()
    # SQLite's datetime('now') writes naive UTC; match that so the gap math lines up.
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    targets = enabled_targets("clip")
    if not targets:
        return None
    primary = targets[0]  # YouTube: the always-enabled anchor for spacing/daily-count bookkeeping

    # Today's slots: fixed post_hours, or the posting-time experiment's daily rotation.
    post_hours = autopost.effective_post_hours(settings, now_local.date())

    with _TICK_LOCK:
        with db.get_conn() as conn:
            slots_due = _slots_due(post_hours, now_local)
            if slots_due == 0:
                return None
            posted_today = db.autopost_count_today(conn, primary, now_local.date().isoformat(), "clip")
            if posted_today >= min(slots_due, len(post_hours)):
                return None
            if not _spacing_ok(conn, primary, "clip", now_utc, settings.min_gap_minutes):
                return None

            pick = _pick_next(conn, settings, _current_riot_id(), primary)
            if pick is None:
                return None

            media_file_id = pick["id"]
            title = pick["draft_title"]
            for platform in targets:
                db.ensure_publish_target(conn, media_file_id, platform, selected=True)
            db.update_media_file(conn, media_file_id, status="approved")
            db.record_autopost(conn, media_file_id, primary, "clip")

    from clipfarm.jobs.publish_job import submit_publish_job

    for platform in targets:
        submit_publish_job(media_file_id, only_platform=platform)
    logger.info("Auto-post fired: clip id=%s -> %s | %s", media_file_id, targets, title)
    return media_file_id


def run_full_game_once(now_local: datetime | None = None) -> int | None:
    """Long-form track: publish at most one full game on its own separate schedule."""
    settings = autopost.current()
    if not (settings.enabled and settings.full_game_enabled):
        return None

    now_local = now_local or datetime.now()
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    targets = enabled_targets("full_game")
    if not targets:
        return None
    primary = targets[0]

    with _TICK_LOCK:
        with db.get_conn() as conn:
            slots_due = _slots_due(settings.full_game_hours, now_local)
            if slots_due == 0:
                return None
            posted_today = db.autopost_count_today(conn, primary, now_local.date().isoformat(), "full_game")
            if posted_today >= min(slots_due, len(settings.full_game_hours)):
                return None
            if not _spacing_ok(conn, primary, "full_game", now_utc, settings.min_gap_minutes):
                return None

            pick = _pick_next_full_game(conn, _current_riot_id(), primary)
            if pick is None:
                return None

            media_file_id = pick["id"]
            title = pick["draft_title"]
            for platform in targets:
                db.ensure_publish_target(conn, media_file_id, platform, selected=True)
            db.update_media_file(conn, media_file_id, status="approved")
            db.record_autopost(conn, media_file_id, primary, "full_game")

    from clipfarm.jobs.publish_job import submit_publish_job

    for platform in targets:
        submit_publish_job(media_file_id, only_platform=platform)
    logger.info("Auto-post fired: full game id=%s -> %s | %s", media_file_id, targets, title)
    return media_file_id


def retry_failed_publishes(older_than_min: int = 30, max_retries: int = 8, limit: int = 2) -> int:
    """Backfill missed uploads: re-submit publish targets that FAILED (quota hit, transient API
    error, a crash mid-upload) while their source file still exists. Covers auto-posted *and*
    manually-approved items -- approving means "post this", so a failure must eventually retry
    on its own. Each target gets at most `max_retries` lifetime attempts (no infinite churn on
    a permanently-bad item), and at most `limit` are re-submitted per tick so a backlog drains
    as a drip, not a burst. Returns how many were re-submitted."""
    with db.get_conn() as conn:
        targets = db.retryable_failed_targets(conn, older_than_min, max_retries, limit)
    if not targets:
        return 0
    from clipfarm.jobs.publish_job import submit_publish_job

    for t in targets:
        logger.info("Retrying failed publish: media_file %s -> %s", t["media_file_id"], t["platform"])
        submit_publish_job(t["media_file_id"], only_platform=t["platform"])
    return len(targets)


def tick(now_local: datetime | None = None) -> None:
    """One full scheduler pass: backfill any missed uploads, then the clip and long-form tracks."""
    retry_failed_publishes()
    run_once(now_local)
    run_full_game_once(now_local)


def start_autopost_scheduler(interval_seconds: int = 300) -> threading.Thread:
    """Daemon thread that ticks the scheduler. Cheap when disabled or nothing is due."""

    def _loop() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                tick()
            except Exception:  # noqa: BLE001 -- the scheduler must never kill the app
                logger.exception("Auto-post tick failed; will retry next interval")

    thread = threading.Thread(target=_loop, name="autopost-scheduler", daemon=True)
    thread.start()
    return thread
