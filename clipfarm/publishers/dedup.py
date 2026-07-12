"""Content signatures used to guarantee we never publish the same video twice.

A *content key* identifies the underlying content independently of which media_files row or
file path carries it, so the same match/clip can't slip out twice as two different rows. We
compute several keys per item and treat a hit on *any* of them as "already published":

  - ``file:<hash>``           -- the file's byte content (catches re-ingested / renamed copies)
  - ``match:<match_id>``      -- a full game (one upload per match per platform)
  - ``clip:<match_id>:<ms>``  -- a specific kill in a game (one upload per highlight per platform)

The keys are derived purely from columns already stored on the row (``content_hash`` and
``highlight_ms`` are populated at ingest), so publishing does no extra file I/O.
"""
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Hash a bounded sample so signing a 30-60 min full game is still instant. Two files with the
# same size and identical head+tail are, for our purposes, the same recording.
_SAMPLE_BYTES = 4 * 1024 * 1024


def file_content_hash(path: Path) -> str | None:
    """A cheap, stable content signature: file size + first and last few MB. None if unreadable."""
    try:
        size = path.stat().st_size
        h = hashlib.sha1(str(size).encode())
        with path.open("rb") as f:
            h.update(f.read(_SAMPLE_BYTES))
            if size > _SAMPLE_BYTES * 2:
                f.seek(-_SAMPLE_BYTES, 2)
                h.update(f.read(_SAMPLE_BYTES))
        return h.hexdigest()
    except OSError:
        return None


def _get(row: Any, key: str) -> Any:
    if isinstance(row, (sqlite3.Row, Mapping)):
        try:
            return row[key]
        except (KeyError, IndexError):
            return None
    return getattr(row, key, None)


def content_keys(row: Any) -> list[str]:
    """All content signatures for a media_files row (sqlite3.Row or mapping)."""
    keys: list[str] = []

    content_hash = _get(row, "content_hash")
    if content_hash:
        keys.append(f"file:{content_hash}")

    match_id = _get(row, "riot_match_id")
    kind = _get(row, "kind")
    if match_id:
        if kind == "clip":
            highlight_ms = _get(row, "highlight_ms")
            if highlight_ms is not None:
                # A specific kill in the game -- lets multiple *different* clips from the same
                # game through, while blocking a re-post of the same moment.
                keys.append(f"clip:{match_id}:{highlight_ms}")
            # else: no resolved kill timestamp -> rely on the file hash only, so two genuinely
            # different highlight-less clips from one game aren't wrongly treated as duplicates.
        else:
            # One full-game upload per match.
            keys.append(f"match:{match_id}")

    return keys
