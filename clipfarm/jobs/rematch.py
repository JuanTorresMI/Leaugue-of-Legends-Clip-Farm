"""Re-run matching for items processed before their game finished.

Ascent saves clips seconds after a kill -- mid-game -- but Riot's Match-V5 API only has
finished games, so early items land in status 'awaiting_match'. This sweep reprocesses them
(full games first, so clips can match against their game's row) and is invoked two ways:
  - automatically on a timer while the dashboard runs (see review/app.py lifespan)
  - manually via POST /api/rematch (the dashboard's Re-match button)

Items with 'needs_attention' (expired Riot key) are also swept, so simply fixing the key
in .env heals the queue on the next pass with no extra steps.
"""
from __future__ import annotations

import logging
import threading

from clipfarm import db
from clipfarm.riot.client import get_riot_client

logger = logging.getLogger(__name__)

# Serialize so the timer and the button can't interleave in-place reprocessing.
_SWEEP_LOCK = threading.Lock()

# A row "needs matching" when it has no Riot match yet, hasn't permanently given up
# ('unmatched'), isn't already going out the door (published/uploading), and belongs to the
# account we're currently on (or is a pre-accounts legacy row with NULL account). This
# prevents trying to match a previous account's clips against the newly-switched account.
_NEEDS_MATCH_WHERE = """
    riot_match_id IS NULL
    AND status NOT IN ('unmatched', 'published', 'failed', 'approved')
    AND (account = ? OR account IS NULL)
    AND id NOT IN (
        SELECT media_file_id FROM publish_targets WHERE status IN ('published', 'uploading')
    )
"""


def _current_riot_id() -> str:
    from clipfarm.accounts import current_account

    return current_account().riot_id


def pending_count() -> int:
    with db.get_conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM media_files WHERE {_NEEDS_MATCH_WHERE}", (_current_riot_id(),)
        ).fetchone()
        return row["n"]


def rematch_pending() -> dict:
    """Re-match every item still awaiting a finished Riot game, in place (ids are stable).
    Full games first so clips can match against their game's row. Returns counts."""
    from clipfarm.jobs.pipeline import reprocess  # local import to avoid cycles

    with _SWEEP_LOCK:
        with db.get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT id FROM media_files
                WHERE {_NEEDS_MATCH_WHERE}
                ORDER BY CASE kind WHEN 'full_game' THEN 0 ELSE 1 END, recorded_at
                """,
                (_current_riot_id(),),
            ).fetchall()

        if not rows:
            return {"attempted": 0, "matched": 0}

        # If the Riot key is dead, reprocessing would just churn -- skip and retry next pass.
        if not get_riot_client().check_key():
            logger.warning("Rematch sweep skipped: Riot API key is expired/invalid.")
            return {"attempted": 0, "matched": 0, "key_expired": True}

        attempted = 0
        matched = 0
        for row in rows:
            attempted += 1
            if reprocess(row["id"]):
                matched += 1

        logger.info("Rematch sweep: %d attempted, %d newly matched", attempted, matched)
        return {"attempted": attempted, "matched": matched}


def start_background_sweep(interval_seconds: int) -> threading.Thread:
    """Daemon thread: run the sweep on a timer. Cheap when nothing is pending."""

    def _loop() -> None:
        import time

        while True:
            time.sleep(interval_seconds)
            try:
                if pending_count() > 0:
                    rematch_pending()
            except Exception:  # noqa: BLE001 -- the sweep must never kill the app
                logger.exception("Rematch sweep failed; will retry next interval")

    thread = threading.Thread(target=_loop, name="rematch-sweep", daemon=True)
    thread.start()
    return thread
