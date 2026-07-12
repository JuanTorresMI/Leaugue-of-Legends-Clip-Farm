"""Knowledge of Ascent's on-disk layout and filename convention."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

# Full games: "07-01-2026-19-09.mp4" (MM-DD-YYYY-HH-MM, minute resolution).
# Clips:      "Auto Clip - 07-04-2026-15-18-55.mp4" (MM-DD-YYYY-HH-MM-SS, second resolution).
_FILENAME_RE = re.compile(
    r"^(?:Auto Clip - )?(\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})(?:-(\d{2}))?$"
)


class MediaKind(str, Enum):
    FULL_GAME = "full_game"
    CLIP = "clip"


@dataclass(frozen=True)
class ParsedMedia:
    path: Path
    kind: MediaKind
    recorded_at: datetime  # second-resolution for clips, minute-resolution for full games
    thumbnail_path: Path | None


def parse_filename_timestamp(path: Path) -> datetime:
    """Parse Ascent's filename convention (full game or clip, see _FILENAME_RE) into a naive local datetime."""
    match = _FILENAME_RE.match(path.stem)
    if not match:
        raise ValueError(f"Filename does not match Ascent's naming convention: {path.name}")
    month, day, year, hour, minute, second = match.groups()
    return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0))


def classify_media(path: Path, full_games_dir: Path, clips_dir: Path) -> ParsedMedia:
    """Classify a file as a full game or clip based on which Ascent folder it lives in."""
    recorded_at = parse_filename_timestamp(path)

    resolved = path.resolve()
    if resolved.parent == clips_dir.resolve():
        kind = MediaKind.CLIP
        thumbnail_path = clips_dir / "Thumbnails" / f"{path.stem}.jpg"
    elif resolved.parent == full_games_dir.resolve():
        kind = MediaKind.FULL_GAME
        thumbnail_path = full_games_dir / "Thumbnails" / f"{path.stem}.jpg"
    else:
        raise ValueError(f"{path} is not directly inside the configured full-game or clips directory")

    if not thumbnail_path.exists():
        thumbnail_path = None

    return ParsedMedia(path=path, kind=kind, recorded_at=recorded_at, thumbnail_path=thumbnail_path)
