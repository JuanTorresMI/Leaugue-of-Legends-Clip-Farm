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


def claim_for_current_account() -> dict:
    """Adopt not-yet-matched recordings that belong to the now-current account but were
    processed while a DIFFERENT account was active (so they're mis-tagged and the normal sweep,
    which is scoped to the current account, skips them), or that previously gave up matching.

    Each candidate is re-matched against the current account but persisted ONLY if it actually
    matches (see pipeline.claim_reprocess) -- so switching to account B never steals account A's
    clips on a miss. Full games are attempted first, so a freshly-claimed full game becomes a
    candidate its own clips can then match against for free. A per-row/per-account marker stops
    repeated switches from re-querying the Riot API for the same misses.

    Runs on an account switch and on a manual scan (see clipfarm/jobs/scan.py).
    """
    from clipfarm.jobs.pipeline import claim_reprocess

    current = _current_riot_id()
    with _SWEEP_LOCK:
        with db.get_conn() as conn:
            rows = db.claimable_media_files(conn, current)
        if not rows:
            return {"attempted": 0, "claimed": 0}

        # A dead key would make every attempt look like a miss and wrongly mark rows tried.
        if not get_riot_client().check_key():
            logger.warning("Claim sweep skipped: Riot API key is expired/invalid.")
            return {"attempted": 0, "claimed": 0, "key_expired": True}

        attempted = claimed = 0
        for row in rows:
            attempted += 1
            result = claim_reprocess(row["id"])
            if result == "matched":
                claimed += 1
            elif result == "no_match":
                # Remember the miss so we don't re-query Riot for this row/account next time.
                with db.get_conn() as conn:
                    db.mark_claim_checked(conn, row["id"], current)
            # 'error'/'gone': leave unmarked so a later scan retries once things recover.

        logger.info("Claim sweep for %s: %d attempted, %d newly claimed", current, attempted, claimed)
        return {"attempted": attempted, "claimed": claimed}


# Rank tiers as they appear in a title's context suffix ("... | Emerald Jungle #shorts").
_RANK_TIERS = ("Iron", "Bronze", "Silver", "Gold", "Platinum", "Emerald", "Diamond",
               "Master", "Grandmaster", "Challenger")


def _rank_tier(rank: str | None) -> str | None:
    return rank.split()[0] if rank else None


def stale_metadata_clip_ids() -> list[int]:
    """Matched clips whose *title* names a rank tier different from their stored rank -- the
    tell-tale of a clip processed while the wrong account was active, whose title + thumbnail
    were baked with one account's rank before the row was re-attributed to another (e.g. a
    Platinum game's clip still titled 'Emerald'). Published/approved and source-deleted clips
    are left alone."""
    with db.get_conn() as conn:
        rows = db.clip_metadata_rows(conn)
    stale = []
    for r in rows:
        tier = _rank_tier(r["rank"])
        if not tier:
            continue
        title_tiers = [t for t in _RANK_TIERS if t in r["draft_title"]]
        if title_tiers and tier not in title_tiers:
            stale.append(r["id"])
    return stale


def repair_stale_metadata() -> dict:
    """One-shot (re-runnable) deep repair: reprocess clips whose title/thumbnail rank drifted
    from their attribution, so title, thumbnail, and fields are regenerated together and stay
    consistent. Unlike a field-only patch this can't leave a 'Platinum' row wearing an 'Emerald'
    title. Needs a valid Riot key; skips cleanly if it's down. Returns counts."""
    from clipfarm.jobs.pipeline import reprocess

    with _SWEEP_LOCK:
        ids = stale_metadata_clip_ids()
        if not ids:
            return {"stale": 0, "repaired": 0}
        if not get_riot_client().check_key():
            logger.warning("Metadata repair skipped: Riot API key is expired/invalid.")
            return {"stale": len(ids), "repaired": 0, "key_expired": True}

        repaired = 0
        for mid in ids:
            try:
                reprocess(mid)
                repaired += 1
            except Exception:  # noqa: BLE001 -- one bad clip mustn't abort the batch
                logger.exception("Metadata repair failed for clip %s", mid)
        logger.info("Metadata repair: %d stale, %d reprocessed", len(ids), repaired)
        return {"stale": len(ids), "repaired": repaired}


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
