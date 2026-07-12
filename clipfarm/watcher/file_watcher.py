"""Watches Ascent's full-game and clips folders for new recordings."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from clipfarm.config import get_settings
from clipfarm.watcher.ingest import handle_new_file

logger = logging.getLogger(__name__)


class _NewVideoHandler(FileSystemEventHandler):
    def on_created(self, event) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() != ".mp4":
            return
        logger.info("New recording detected: %s", path)
        threading.Thread(target=handle_new_file, args=(path,), daemon=True).start()


def start_watching() -> Observer:
    settings = get_settings().ascent
    handler = _NewVideoHandler()
    observer = Observer()
    # non-recursive: skips the Thumbnails/Clips subfolders under the full-games dir
    observer.schedule(handler, str(settings.full_games_dir), recursive=False)
    observer.schedule(handler, str(settings.clips_dir), recursive=False)
    observer.start()
    logger.info("Watching %s and %s", settings.full_games_dir, settings.clips_dir)
    return observer


def run_forever() -> None:
    observer = start_watching()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
