"""Periodically snapshot performance stats for every published video into video_stats."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict

from clipfarm import db
from clipfarm.metrics import base

logger = logging.getLogger(__name__)

# Import provider modules so they self-register. Add new platforms here.
from clipfarm.metrics import facebook_stats, youtube_stats  # noqa: E402,F401

_POLL_LOCK = threading.Lock()


def poll_once() -> int:
    """Fetch + store a fresh stats snapshot for all published videos. Returns rows written."""
    with _POLL_LOCK:
        with db.get_conn() as conn:
            videos = db.published_videos(conn)

        ids_by_platform: dict[str, list[str]] = defaultdict(list)
        mfid: dict[tuple[str, str], int] = {}
        for v in videos:
            ids_by_platform[v["platform"]].append(v["platform_video_id"])
            mfid[(v["platform"], v["platform_video_id"])] = v["media_file_id"]

        written = 0
        for platform, ids in ids_by_platform.items():
            provider = base.get(platform)
            if provider is None:
                continue
            try:
                stats = provider.fetch(ids)
            except Exception:  # noqa: BLE001 -- one platform's failure must not stop others
                logger.exception("Stats fetch failed for %s", platform)
                continue
            with db.get_conn() as conn:
                for video_id, metrics in stats.items():
                    db.insert_video_stats(conn, platform, video_id, mfid.get((platform, video_id)), metrics)
                    written += 1
        logger.info("Stats poll: wrote %d snapshot(s)", written)
        return written


def start_stats_poller(interval_seconds: int = 3600) -> threading.Thread:
    """Daemon thread that polls stats hourly (default). Cheap when there are no published videos."""

    def _loop() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                poll_once()
            except Exception:  # noqa: BLE001
                logger.exception("Stats poll failed; will retry next interval")

    thread = threading.Thread(target=_loop, name="stats-poller", daemon=True)
    thread.start()
    return thread
