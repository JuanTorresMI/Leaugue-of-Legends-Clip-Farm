"""Wait for a newly-created recording to finish writing, then run it through the pipeline."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from clipfarm.config import get_settings
from clipfarm.jobs.pipeline import process_new_file

logger = logging.getLogger(__name__)


def _wait_until_settled(path: Path) -> bool:
    """Poll file size until it's stable for N consecutive checks (Ascent writes the file over time)."""
    settings = get_settings().watcher
    stable_count = 0
    last_size = -1

    while stable_count < settings.settle_required_stable_checks:
        if not path.exists():
            return False
        size = path.stat().st_size
        if size == last_size and size > 0:
            stable_count += 1
        else:
            stable_count = 0
        last_size = size
        time.sleep(settings.settle_check_interval_seconds)

    return True


def handle_new_file(path: Path) -> None:
    try:
        if not _wait_until_settled(path):
            logger.warning("File disappeared before settling: %s", path)
            return
        media_file_id = process_new_file(path)
        if media_file_id is not None:
            logger.info("Processed %s -> media_file id=%s", path, media_file_id)
    except Exception:  # noqa: BLE001 -- a single bad file must not kill the watcher
        logger.exception("Unhandled error ingesting %s", path)
