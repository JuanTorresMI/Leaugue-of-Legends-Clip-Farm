"""Scan the Ascent folders for recordings not yet in the queue, then re-match.

Runs automatically right after an account switch (new account's clips get ingested + tagged with
that account) and can be triggered manually from the dashboard. Idempotent: process_new_file
skips paths already ingested, so re-scanning is cheap.
"""
from __future__ import annotations

import logging
import threading

from clipfarm.config import get_settings

logger = logging.getLogger(__name__)

_SCAN_LOCK = threading.Lock()


def scan_recordings() -> int:
    """Ingest any on-disk recordings not already in the DB. Returns how many were newly added."""
    from clipfarm.jobs.pipeline import process_new_file

    ascent = get_settings().ascent
    files = sorted(ascent.full_games_dir.glob("*.mp4")) + sorted(ascent.clips_dir.glob("*.mp4"))
    added = 0
    for path in files:
        try:
            if process_new_file(path) is not None:
                added += 1
        except Exception:  # noqa: BLE001 -- one bad file must not abort the scan
            logger.exception("Scan failed to ingest %s", path)
    logger.info("Scan complete: %d new recording(s) ingested", added)
    return added


def scan_now() -> dict:
    """Full scan + a re-match sweep, serialized so overlapping triggers can't interleave."""
    from clipfarm.jobs.rematch import rematch_pending

    with _SCAN_LOCK:
        added = scan_recordings()
        rematch_pending()
        return {"added": added}


def start_scan_async() -> threading.Thread:
    thread = threading.Thread(target=scan_now, name="scan", daemon=True)
    thread.start()
    return thread
