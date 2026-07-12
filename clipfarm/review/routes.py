"""API routes backing the review dashboard."""
from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from clipfarm import accounts, db, fb_settings, riot_key
from clipfarm.config import get_settings
from clipfarm.jobs.pipeline import reprocess
from clipfarm.jobs.publish_job import submit_publish_job
from clipfarm.jobs.rematch import pending_count, rematch_pending
from clipfarm.media.composite import generate_composite, spec_from_row
from clipfarm.media.ffmpeg import FfmpegError, duration_seconds, grab_frame
from clipfarm.media.thumbnail import generate_thumbnail
from clipfarm.publishers import quota
from clipfarm.riot.client import (
    AccountNotFoundError,
    RiotKeyExpiredError,
    get_riot_client,
    reset_riot_client,
    validate_account,
    validate_key,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _canonical_thumbnail_path(video_path: Path, kind: str) -> Path:
    suffix = "clip" if kind == "clip" else "full_game"
    return get_settings().project_root / "data" / "thumbnails" / f"{video_path.stem}_{suffix}.jpg"


def _row_to_dict(
    row: sqlite3.Row, targets: list[sqlite3.Row], already_published_on: list[str] | None = None
) -> dict:
    return {
        "id": row["id"],
        "path": row["path"],
        "kind": row["kind"],
        "recorded_at": row["recorded_at"],
        "status": row["status"],
        "champion": row["champion"],
        "kda": row["kda"],
        "win": bool(row["win"]) if row["win"] is not None else None,
        "queue_type": row["queue_type"],
        "kill_streak": row["kill_streak"],
        "role": row["role"],
        "patch": row["patch"],
        "rank": row["rank"],
        "opponent_champion": row["opponent_champion"],
        "draft_title": row["draft_title"],
        "draft_description": row["draft_description"],
        "hashtags": json.loads(row["draft_hashtags"]) if row["draft_hashtags"] else [],
        "tags": json.loads(row["draft_tags"]) if row["draft_tags"] else [],
        "thumbnail_url": f"/media/{row['id']}/thumbnail" if row["thumbnail_path"] else None,
        "video_url": f"/media/{row['id']}/video",
        "error_message": row["error_message"],
        # Platforms this exact content was already published to (any earlier row/file). The
        # dashboard warns on these so you don't approve a duplicate; publishing also hard-blocks it.
        "already_published_on": already_published_on or [],
        "publish_targets": [
            {
                "platform": t["platform"],
                "selected": bool(t["selected"]),
                "status": t["status"],
                "error": t["error_message"],
            }
            for t in targets
        ],
    }


def _row_dict_with_dupes(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """_row_to_dict plus the ledger lookup for which platforms already have this content."""
    from clipfarm.publishers import dedup

    dupes = db.platforms_already_published(conn, dedup.content_keys(row))
    return _row_to_dict(row, db.get_publish_targets(conn, row["id"]), already_published_on=dupes)


@router.get("/api/queue")
def get_queue() -> list[dict]:
    with db.get_conn() as conn:
        return [_row_dict_with_dupes(conn, row) for row in db.list_queue(conn)]


@router.get("/api/queue/{media_file_id}")
def get_queue_item(media_file_id: int) -> dict:
    with db.get_conn() as conn:
        row = db.get_media_file(conn, media_file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _row_dict_with_dupes(conn, row)


class EditRequest(BaseModel):
    draft_title: str | None = None
    draft_description: str | None = None
    hashtags: list[str] | None = None
    tags: list[str] | None = None


@router.patch("/api/queue/{media_file_id}")
def edit_queue_item(media_file_id: int, edit: EditRequest) -> dict:
    fields = {k: v for k, v in edit.model_dump().items() if v is not None}
    with db.get_conn() as conn:
        if db.get_media_file(conn, media_file_id) is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.update_media_file(conn, media_file_id, **fields)
        row = db.get_media_file(conn, media_file_id)
        return _row_dict_with_dupes(conn, row)


class ApproveRequest(BaseModel):
    platforms: list[str]


@router.post("/api/queue/{media_file_id}/approve")
def approve_queue_item(media_file_id: int, approve: ApproveRequest) -> dict:
    with db.get_conn() as conn:
        row = db.get_media_file(conn, media_file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        for target in db.get_publish_targets(conn, media_file_id):
            db.upsert_publish_target(conn, media_file_id, target["platform"], target["platform"] in approve.platforms)
        db.update_media_file(conn, media_file_id, status="approved")

    submit_publish_job(media_file_id)
    logger.info("Approved media_file id=%s -- publishing to: %s", media_file_id, approve.platforms)
    return {"status": "approved", "publishing_to": approve.platforms}


@router.post("/api/queue/{media_file_id}/retry/{platform}")
def retry_platform(media_file_id: int, platform: str) -> dict:
    """Re-attempt a single failed platform without touching the ones that succeeded."""
    with db.get_conn() as conn:
        row = db.get_media_file(conn, media_file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        targets = {t["platform"]: t for t in db.get_publish_targets(conn, media_file_id)}
        if platform not in targets:
            raise HTTPException(status_code=404, detail=f"No {platform} target for this item")
        db.upsert_publish_target(conn, media_file_id, platform, selected=True)

    submit_publish_job(media_file_id, only_platform=platform)
    logger.info("Retrying %s for media_file id=%s", platform, media_file_id)
    return {"ok": True}


@router.get("/api/autopost")
def get_autopost() -> dict:
    """Auto-post settings + a live status snapshot for the dashboard."""
    from clipfarm import autopost
    from clipfarm.jobs.scheduler import due_status

    return _autopost_payload(autopost.current())


def _autopost_payload(s) -> dict:
    from clipfarm.jobs.scheduler import due_status

    return {
        "enabled": s.enabled,
        "post_hours": s.post_hours,
        "min_kill_streak": s.min_kill_streak,
        "per_game_cap": s.per_game_cap,
        "platforms": s.platforms,
        "full_game_enabled": s.full_game_enabled,
        "full_game_hours": s.full_game_hours,
        "status": due_status(s),
    }


class AutopostRequest(BaseModel):
    enabled: bool | None = None
    post_hours: list[int] | None = None
    min_kill_streak: int | None = None
    per_game_cap: int | None = None
    platforms: list[str] | None = None
    full_game_enabled: bool | None = None
    full_game_hours: list[int] | None = None


@router.post("/api/autopost")
def update_autopost(req: AutopostRequest) -> dict:
    """Toggle auto-post on/off or adjust its cadence. Only provided fields change."""
    from clipfarm import autopost

    s = autopost.current()
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    updated = autopost.AutopostSettings(**{**s.__dict__, **fields})
    autopost.save(updated)
    updated = autopost.current()
    logger.info(
        "Auto-post settings updated: clips=%s hours=%s | full_games=%s hours=%s",
        updated.enabled, updated.post_hours, updated.full_game_enabled, updated.full_game_hours,
    )
    return _autopost_payload(updated)


@router.post("/api/autopost/apply-recommended")
def apply_recommended_hours() -> dict:
    """Set the clip post-hours to the data-driven best times from the metrics analysis."""
    from clipfarm import autopost
    from clipfarm.metrics.analysis import recommended_hours

    hours = recommended_hours(count=len(autopost.current().post_hours) or 3)
    if not hours:
        raise HTTPException(status_code=400, detail="Not enough performance data yet to recommend times.")
    s = autopost.current()
    updated = autopost.AutopostSettings(**{**s.__dict__, "post_hours": hours})
    autopost.save(updated)
    logger.info("Applied recommended post hours: %s", hours)
    return _autopost_payload(autopost.current())


@router.get("/api/metrics")
def get_metrics() -> dict:
    """Everything the metrics dashboard needs: per-video latest stats (with a strong/weak tier),
    per-platform totals, 'what's working' insights, best-time-to-post, and the long-term growth
    timeline. All sourced from the append-only video_stats time-series."""
    from clipfarm.metrics import analysis

    records = analysis.video_records()
    return {
        "summary": analysis.summary(),
        "videos": records,
        "insights": analysis.insights(records),
        "hour_performance": analysis.hour_performance(),
        "recommended_hours": analysis.recommended_hours(),
        "timeline": analysis.channel_timeline(),
        "platforms": sorted({r["platform"] for r in records}),
    }


@router.post("/api/metrics/refresh")
def refresh_metrics() -> dict:
    """Trigger an immediate stats poll in the background."""
    import threading

    from clipfarm.metrics.poller import poll_once

    threading.Thread(target=poll_once, name="metrics-refresh", daemon=True).start()
    return {"refreshing": True}


@router.get("/api/quota")
def get_quota_usage() -> dict:
    settings = get_settings().youtube
    return {
        "youtube": {"used": quota.get_usage("youtube"), "limit": settings.daily_upload_quota},
    }


@router.post("/api/queue/{media_file_id}/regenerate-thumbnail")
def regenerate_thumbnail(media_file_id: int, offset_seconds: float | None = None) -> dict:
    """Roll a new high-quality thumbnail. Each click grabs a fresh random gameplay frame.
    If the item isn't matched yet, we first try to match it (its game may have just
    finished) so we can build the full composite instead of a bare frame."""
    with db.get_conn() as conn:
        row = db.get_media_file(conn, media_file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")

    # No champion data yet -> attempt an on-demand match so the composite can be built.
    if not row["champion"]:
        reprocess(media_file_id)
        with db.get_conn() as conn:
            row = db.get_media_file(conn, media_file_id)

    video_path = Path(row["path"])
    if offset_seconds is None:
        try:
            total = duration_seconds(video_path)
            offset_seconds = random.uniform(0.15, 0.85) * total
        except FfmpegError:
            offset_seconds = 1.5

    # Overwrite the item's canonical thumbnail (same path the pipeline uses) rather than a
    # separate _regen.jpg, so a regenerated thumbnail can't get orphaned/deleted out from
    # under the DB. Cache-busting on the client handles the browser showing the new image.
    output_jpg = _canonical_thumbnail_path(video_path, row["kind"])
    spec = spec_from_row(row)
    if spec is not None:
        frame = output_jpg.with_suffix(".frame.png")
        try:
            grab_frame(video_path, frame, offset_seconds)
            generate_composite(spec, frame, output_jpg)
        finally:
            frame.unlink(missing_ok=True)
    else:
        generate_thumbnail(video_path, output_jpg, offset_seconds, row["draft_title"])

    with db.get_conn() as conn:
        db.update_media_file(conn, media_file_id, thumbnail_path=str(output_jpg))
    return {"thumbnail_url": f"/media/{media_file_id}/thumbnail?t={int(time.time())}"}


@router.post("/api/rematch")
def rematch_now() -> dict:
    """Kick off a batch re-match in the background (the dashboard button). The frontend
    polls /api/rematch/pending until it drains, then reloads the queue."""
    import threading

    started_with = pending_count()
    threading.Thread(target=rematch_pending, name="rematch-manual", daemon=True).start()
    return {"started": True, "pending": started_with}


@router.get("/api/rematch/pending")
def rematch_pending_count() -> dict:
    return {"pending": pending_count()}


@router.get("/api/riot-health")
def riot_health() -> dict:
    ok = get_riot_client().check_key()
    return {"ok": ok}


class RiotKeyRequest(BaseModel):
    key: str


@router.post("/api/riot-key")
def update_riot_key(req: RiotKeyRequest) -> dict:
    """Validate and save a new Riot API key from the dashboard, taking effect immediately."""
    key = req.key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="Paste a Riot API key first.")
    if not validate_key(key):
        raise HTTPException(status_code=400, detail="Riot rejected that key (expired or mistyped).")
    riot_key.set_key(key)
    reset_riot_client()
    logger.info("Riot API key updated from dashboard")
    return {"ok": True}


@router.get("/api/facebook")
def get_facebook() -> dict:
    s = fb_settings.current()
    return {"page_id": s.page_id or "", "enabled": s.enabled, "configured": s.configured}


class FacebookRequest(BaseModel):
    page_id: str
    access_token: str
    enabled: bool


@router.post("/api/facebook")
def update_facebook(req: FacebookRequest) -> dict:
    """Save Facebook Page credentials + enable flag. Validates the token against the Page,
    and when enabling, backfills a Facebook target onto existing unpublished queue items."""
    from clipfarm.fb_settings import FacebookSettings
    from clipfarm.publishers.facebook import FacebookAuthError, validate_credentials

    page_id = req.page_id.strip()
    token = req.access_token.strip()
    page_name = None
    if page_id and token:
        try:
            page_name = validate_credentials(page_id, token)
        except FacebookAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif req.enabled:
        raise HTTPException(status_code=400, detail="Enter the Page ID and access token to enable Facebook.")

    fb_settings.save(FacebookSettings(page_id=page_id or None, access_token=token or None, enabled=req.enabled))

    if req.enabled:
        with db.get_conn() as conn:
            for row in conn.execute("SELECT id FROM media_files WHERE status != 'published'").fetchall():
                db.ensure_publish_target(conn, row["id"], "facebook", selected=True)

    logger.info("Facebook settings updated (enabled=%s, page=%s)", req.enabled, page_name or page_id)
    return {"ok": True, "page_name": page_name, "enabled": req.enabled}


@router.get("/api/accounts")
def get_accounts() -> dict:
    current = accounts.current_account()
    return {
        "current": current.riot_id,
        "accounts": [
            {"riot_id": a.riot_id, "platform": a.platform, "region": a.region}
            for a in accounts.list_accounts()
        ],
    }


class SwitchAccountRequest(BaseModel):
    riot_id: str  # "Name#TAG"
    platform: str | None = None  # required when adding a brand-new account


@router.post("/api/accounts/switch")
def switch_account(req: SwitchAccountRequest) -> dict:
    if "#" not in req.riot_id:
        raise HTTPException(status_code=400, detail="Riot ID must be in Name#TAG format.")
    game_name, tag_line = req.riot_id.rsplit("#", 1)
    game_name, tag_line = game_name.strip(), tag_line.strip()

    known = {a.riot_id: a for a in accounts.list_accounts()}
    if req.riot_id in known:
        platform = known[req.riot_id].platform
        region = known[req.riot_id].region
    else:
        if not req.platform:
            raise HTTPException(status_code=400, detail="New account needs a platform (e.g. na1, euw1, kr).")
        platform = req.platform
        region = accounts.region_for_platform(platform)

    # Confirm the Riot ID actually resolves before committing the switch.
    try:
        validate_account(game_name, tag_line, region)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RiotKeyExpiredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    accounts.upsert_account(game_name, tag_line, platform, region)
    current = accounts.set_current(f"{game_name}#{tag_line}")
    reset_riot_client()
    # Auto-scan: pull in any of this account's recordings that aren't in the queue yet, then
    # re-match. Runs in the background so the switch responds immediately.
    from clipfarm.jobs.scan import start_scan_async

    start_scan_async()
    logger.info("Switched active account to %s -- auto-scan started", current.riot_id)
    return {"current": current.riot_id, "scanning": True}


@router.post("/api/scan")
def scan_now_endpoint() -> dict:
    """Manual 'scan folders now' trigger (also runs automatically on account switch)."""
    from clipfarm.jobs.scan import start_scan_async

    start_scan_async()
    return {"scanning": True}


@router.get("/media/{media_file_id}/video")
def get_video(media_file_id: int) -> FileResponse:
    with db.get_conn() as conn:
        row = db.get_media_file(conn, media_file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(row["path"])


@router.get("/media/{media_file_id}/thumbnail")
def get_thumbnail(media_file_id: int) -> FileResponse:
    with db.get_conn() as conn:
        row = db.get_media_file(conn, media_file_id)
    if row is None or not row["thumbnail_path"]:
        raise HTTPException(status_code=404, detail="No thumbnail")

    # Serve the stored thumbnail if present; otherwise fall back to the canonical path or
    # Ascent's own thumbnail before giving up. A missing file must never 500 the dashboard.
    candidates = [row["thumbnail_path"], str(_canonical_thumbnail_path(Path(row["path"]), row["kind"]))]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="Thumbnail file missing")
