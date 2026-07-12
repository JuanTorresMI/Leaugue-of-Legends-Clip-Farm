from datetime import datetime
from pathlib import Path

import pytest

from clipfarm.paths import MediaKind, classify_media, parse_filename_timestamp


def test_parse_filename_timestamp_valid():
    path = Path("07-01-2026-19-09.mp4")
    assert parse_filename_timestamp(path) == datetime(2026, 7, 1, 19, 9)


def test_parse_filename_timestamp_invalid():
    with pytest.raises(ValueError):
        parse_filename_timestamp(Path("not-a-timestamp.mp4"))


def test_parse_filename_timestamp_clip_convention():
    # Real Ascent clip filenames: "Auto Clip - MM-DD-YYYY-HH-MM-SS.mp4" (second resolution).
    path = Path("Auto Clip - 07-04-2026-15-18-55.mp4")
    assert parse_filename_timestamp(path) == datetime(2026, 7, 4, 15, 18, 55)


def test_classify_media_full_game(tmp_path):
    full_games_dir = tmp_path / "Ascent"
    clips_dir = full_games_dir / "Clips"
    full_games_dir.mkdir()
    clips_dir.mkdir()

    video = full_games_dir / "07-01-2026-19-09.mp4"
    video.touch()

    parsed = classify_media(video, full_games_dir, clips_dir)
    assert parsed.kind == MediaKind.FULL_GAME
    assert parsed.recorded_at == datetime(2026, 7, 1, 19, 9)
    assert parsed.thumbnail_path is None  # no Thumbnails/07-01-2026-19-09.jpg created


def test_classify_media_clip(tmp_path):
    full_games_dir = tmp_path / "Ascent"
    clips_dir = full_games_dir / "Clips"
    thumbs_dir = clips_dir / "Thumbnails"
    full_games_dir.mkdir()
    clips_dir.mkdir()
    thumbs_dir.mkdir()

    clip = clips_dir / "Auto Clip - 07-04-2026-15-18-55.mp4"
    clip.touch()
    (thumbs_dir / "Auto Clip - 07-04-2026-15-18-55.jpg").touch()

    parsed = classify_media(clip, full_games_dir, clips_dir)
    assert parsed.kind == MediaKind.CLIP
    assert parsed.recorded_at == datetime(2026, 7, 4, 15, 18, 55)
    assert parsed.thumbnail_path == thumbs_dir / "Auto Clip - 07-04-2026-15-18-55.jpg"


def test_classify_media_unrelated_dir_raises(tmp_path):
    full_games_dir = tmp_path / "Ascent"
    clips_dir = full_games_dir / "Clips"
    full_games_dir.mkdir()
    clips_dir.mkdir()

    other_dir = tmp_path / "Elsewhere"
    other_dir.mkdir()
    stray = other_dir / "07-01-2026-19-09.mp4"
    stray.touch()

    with pytest.raises(ValueError):
        classify_media(stray, full_games_dir, clips_dir)
