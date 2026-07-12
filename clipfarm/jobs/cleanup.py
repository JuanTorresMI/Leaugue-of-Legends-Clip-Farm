"""Storage housekeeping that never touches the data we need for analysis.

Two space savers, both keeping every DB row + YouTube/Facebook video id + performance stats:
  - After an item is published everywhere, delete our big regenerated upload file (the vertical
    re-encode in data/converted) -- it's reproducible from the source and only existed to upload.
  - When Ascent's 50GB rollover deletes a source recording, mark the row archived (it leaves the
    review panel) and delete any remaining derived files (thumbnails, frames, converted clip).

Originals are only ever deleted by Ascent; we never delete a source recording ourselves.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from clipfarm import db
from clipfarm.config import get_settings

logger = logging.getLogger(__name__)

_RECONCILE_LOCK = threading.Lock()


def _converted_path(stem: str) -> Path:
    return get_settings().project_root / "data" / "converted" / f"{stem}_edited.mp4"


def _derived_paths(row) -> list[Path]:
    """Every file WE generated for a row (safe to delete; regeneratable)."""
    root = get_settings().project_root
    stem = Path(row["path"]).stem
    thumbs = root / "data" / "thumbnails"
    paths = [
        _converted_path(stem),
        thumbs / f"{stem}_clip.jpg",
        thumbs / f"{stem}_full_game.jpg",
        thumbs / f"{stem}_frame.png",
    ]
    try:
        tp = row["thumbnail_path"]
        if tp:
            paths.append(Path(tp))
    except (KeyError, IndexError):
        pass
    return paths


def _unlink_all(paths: list[Path]) -> int:
    freed = 0
    for p in paths:
        try:
            if p.exists():
                p.unlink()
                freed += 1
        except OSError:
            logger.warning("Could not delete %s", p, exc_info=True)
    return freed


def purge_upload_cache(media_file_id: int) -> None:
    """Delete the regenerated upload file after a full publish (keeps source + thumbnail)."""
    with db.get_conn() as conn:
        row = db.get_media_file(conn, media_file_id)
    if row is None:
        return
    if _unlink_all([_converted_path(Path(row["path"]).stem)]):
        logger.info("Freed converted upload cache for media_file %s", media_file_id)


def reconcile_missing_sources() -> int:
    """Archive rows whose source recording is gone and delete their leftover derived files.
    Returns how many were newly archived."""
    with _RECONCILE_LOCK:
        # Guard against a transient unmount: if the Ascent folders themselves are missing, the
        # drive is likely offline -- do nothing rather than mass-archive the whole queue.
        ascent = get_settings().ascent
        if not ascent.full_games_dir.exists() and not ascent.clips_dir.exists():
            logger.warning("Ascent folders unavailable; skipping source reconcile this pass")
            return 0

        with db.get_conn() as conn:
            rows = db.rows_with_live_source(conn)
        archived = 0
        for row in rows:
            if Path(row["path"]).exists():
                continue
            with db.get_conn() as conn:
                full = db.get_media_file(conn, row["id"])
                db.mark_source_deleted(conn, row["id"])
            if full is not None:
                _unlink_all(_derived_paths(full))
            archived += 1
        if archived:
            logger.info("Archived %d item(s) whose source recording was deleted", archived)
        return archived


def start_reconcile_sweep(interval_seconds: int = 900) -> threading.Thread:
    """Daemon thread that periodically reconciles deleted sources (Ascent deletes as it rolls
    over 50GB, so we check on a timer rather than watching for delete events)."""
    import time

    def _loop() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                reconcile_missing_sources()
            except Exception:  # noqa: BLE001
                logger.exception("Source reconcile sweep failed; will retry")

    thread = threading.Thread(target=_loop, name="reconcile-sweep", daemon=True)
    thread.start()
    return thread
