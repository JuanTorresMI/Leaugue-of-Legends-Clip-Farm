"""Scan the Ascent folders for recordings not yet in the queue, then re-match and claim.

Runs automatically right after an account switch (new account's clips get ingested + tagged with
that account) and can be triggered manually from the dashboard. Three steps:
  1. ingest any on-disk files not in the DB (idempotent: process_new_file skips known paths);
  2. re-match current-account items still awaiting a finished game (rematch_pending);
  3. claim items that belong to the now-active account but were processed under a different one
     (claim_for_current_account) -- this is what heals recordings that were ingested while the
     wrong account was selected, so their Riot match / thumbnail never resolved.
Idempotent and cheap to re-run.
"""
from __future__ import annotations

import logging
import threading

from clipfarm import db
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
    """Full scan + a re-match sweep + a claim sweep, serialized so overlapping triggers can't
    interleave. The claim sweep runs last (after re-match, so freshly-matched current-account
    full games are available as candidates) and adopts recordings that belong to the now-active
    account but were processed under a different one -- the account-switch case."""
    from clipfarm.jobs.rematch import claim_for_current_account, rematch_pending

    with _SCAN_LOCK:
        added = scan_recordings()
        rematch_pending()
        claim = claim_for_current_account()
        # Correct any clip whose account/rank drifted from the game it matched (e.g. processed
        # while the wrong account was selected). DB-only, so it's cheap to run every scan.
        with db.get_conn() as conn:
            repaired = db.repair_clip_attribution(conn)
        if repaired:
            logger.info("Repaired attribution on %d clip(s)", repaired)
        return {"added": added, "claimed": claim.get("claimed", 0), "repaired": repaired}


def start_scan_async() -> threading.Thread:
    thread = threading.Thread(target=scan_now, name="scan", daemon=True)
    thread.start()
    return thread
