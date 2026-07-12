"""One-off: run the ingest pipeline over recordings that already exist on disk."""
from __future__ import annotations

import logging

from clipfarm.config import get_settings
from clipfarm.jobs.pipeline import process_new_file

logger = logging.getLogger(__name__)


def run_backfill() -> None:
    settings = get_settings().ascent
    files = sorted(settings.full_games_dir.glob("*.mp4")) + sorted(settings.clips_dir.glob("*.mp4"))
    logger.info("Backfilling %d existing recordings", len(files))
    for path in files:
        try:
            media_file_id = process_new_file(path)
            if media_file_id is not None:
                print(f"Processed: {path.name} -> id={media_file_id}")
            else:
                print(f"Skipped (already processed): {path.name}")
        except Exception:
            logger.exception("Failed to backfill %s", path)


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    run_backfill()
