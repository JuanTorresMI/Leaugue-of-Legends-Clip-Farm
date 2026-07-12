"""Runs approved publish_targets in a background thread so the dashboard's Approve click
doesn't block on a long upload; the frontend polls /api/queue/{id} for status instead."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from clipfarm import db
from clipfarm.publishers import dedup, registry

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="publish")


def submit_publish_job(media_file_id: int, only_platform: str | None = None) -> None:
    _EXECUTOR.submit(_run, media_file_id, only_platform)


def _run(media_file_id: int, only_platform: str | None = None) -> None:
    with db.get_conn() as conn:
        media_file = db.get_media_file(conn, media_file_id)
        targets = [t for t in db.get_publish_targets(conn, media_file_id) if t["selected"]]

    if media_file is None:
        logger.warning("media_file %s vanished before publish job ran", media_file_id)
        return

    for target in targets:
        platform = target["platform"]
        if only_platform and platform != only_platform:
            continue
        entry = registry.get(platform)
        if entry is None:
            continue  # target for a platform with no publisher yet; stays pending
        publisher = entry.publish

        # Claim the target atomically: only pending/failed may transition to uploading.
        # This is what makes a double-clicked Approve (or overlapping retry) post once,
        # not twice -- the second job finds nothing left to claim.
        with db.get_conn() as conn:
            claimed = conn.execute(
                """
                UPDATE publish_targets SET status='uploading', error_message=NULL, updated_at=datetime('now')
                WHERE media_file_id=? AND platform=? AND status IN ('pending', 'failed')
                """,
                (media_file_id, platform),
            ).rowcount
        if not claimed:
            logger.info("Skipping %s for media_file %s (already uploading/published)", platform, media_file_id)
            continue

        # Last line of defense against double-posting: atomically reserve this content's
        # signatures for the platform. If any is already taken (a prior upload, or a racing
        # publish of an identical file), we lose the reservation and never upload again.
        keys = dedup.content_keys(media_file)
        with db.get_conn() as conn:
            reserved = db.try_reserve_content(conn, platform, keys, media_file_id)
        if not reserved:
            with db.get_conn() as conn:
                existing = db.find_published_content(conn, platform, keys)
            prior_video = (existing["platform_video_id"] if existing else None) or "an earlier upload"
            logger.warning(
                "Blocking duplicate %s upload for media_file %s -- same content already published as %s",
                platform, media_file_id, prior_video,
            )
            with db.get_conn() as conn:
                db.set_publish_target_status(
                    conn, media_file_id, platform, "duplicate",
                    platform_video_id=existing["platform_video_id"] if existing else None,
                    error_message=f"Already published to {platform} ({prior_video}); skipped to avoid a duplicate.",
                )
            continue

        try:
            platform_video_id = publisher(media_file)
        except Exception as exc:  # noqa: BLE001 -- one platform's failure must not stop the others
            logger.exception("Failed to publish media_file %s to %s", media_file_id, platform)
            with db.get_conn() as conn:
                db.release_reserved_content(conn, platform, keys)  # free the signatures for a retry
                db.set_publish_target_status(
                    conn, media_file_id, platform, "failed", error_message=str(exc)[:500]
                )
        else:
            with db.get_conn() as conn:
                db.finalize_reserved_content(conn, platform, keys, media_file_id, platform_video_id)
                db.set_publish_target_status(
                    conn, media_file_id, platform, "published", platform_video_id=platform_video_id
                )
                # Stamp first-publish time once (used by best-time-to-post analysis).
                conn.execute(
                    "UPDATE media_files SET published_at = COALESCE(published_at, datetime('now')) WHERE id = ?",
                    (media_file_id,),
                )

    # If every selected target is done (published, or skipped as a known duplicate), mark the
    # item itself published so it leaves the review queue.
    with db.get_conn() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) AS n FROM publish_targets "
            "WHERE media_file_id=? AND selected=1 AND status NOT IN ('published', 'duplicate')",
            (media_file_id,),
        ).fetchone()["n"]
        if remaining == 0:
            db.update_media_file(conn, media_file_id, status="published")
            fully_published = True
        else:
            fully_published = False

    # Reclaim the big regenerated upload file once everything is out (keeps source + thumbnail).
    if fully_published:
        from clipfarm.jobs.cleanup import purge_upload_cache

        purge_upload_cache(media_file_id)
