"""Thin subprocess wrapper around Ascent's bundled ffmpeg binary.

Ascent only ships ffmpeg.exe (no ffprobe.exe), so media inspection is done by parsing
ffmpeg's own stderr banner (`-i <file>` with no output) rather than shelling out to ffprobe.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from clipfarm.config import get_settings

logger = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")


class FfmpegError(RuntimeError):
    pass


def _run(args: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise FfmpegError(f"Command failed ({args[0]}): {result.stderr.strip()[-2000:]}")
    return result


def duration_seconds(path: Path) -> float:
    """Parse duration out of ffmpeg's info banner. ffmpeg exits non-zero here by design
    (no output was requested) so we read stderr directly instead of using _run()."""
    ffmpeg_path = str(get_settings().ascent.ffmpeg_path)
    result = subprocess.run([ffmpeg_path, "-i", str(path)], capture_output=True, text=True)
    match = _DURATION_RE.search(result.stderr)
    if not match:
        raise FfmpegError(f"Could not determine duration for {path}: {result.stderr.strip()[-500:]}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def grab_frame(input_path: Path, output_png_path: Path, offset_seconds: float) -> Path:
    ffmpeg_path = str(get_settings().ascent.ffmpeg_path)
    output_png_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg_path,
            "-y",
            "-ss",
            str(max(offset_seconds, 0)),
            "-i",
            str(input_path),
            "-frames:v",
            "1",
            str(output_png_path),
        ]
    )
    return output_png_path


def to_vertical(input_path: Path, output_path: Path) -> Path:
    """Convert a 16:9 gameplay clip to a 9:16 vertical frame for Shorts/Reels/TikTok.

    A naive scale-to-height-then-crop-width approach zooms in ~3x on a 16:9 source to fill
    a 9:16 frame, which upscales a narrow center sliver and looks badly fuzzy. Instead this
    keeps the full frame crisp (only a modest downscale) centered on a blurred, stretched
    copy of the same video filling the top/bottom bars -- the standard look for gameplay
    clips converted to vertical.
    """
    ffmpeg_path = str(get_settings().ascent.ffmpeg_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    filter_complex = (
        "[0:v]scale=1080:1920,gblur=sigma=20,setsar=1[bg];"
        "[0:v]scale=1080:-2,setsar=1[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    _run(
        [
            ffmpeg_path,
            "-y",
            "-i",
            str(input_path),
            "-filter_complex",
            filter_complex,
            # Ascent's ffmpeg build is hardware-encoder-only (no libx264) -- use NVENC.
            "-c:v",
            "h264_nvenc",
            "-rc",
            "vbr",
            "-cq",
            "19",
            "-c:a",
            "copy",
            str(output_path),
        ]
    )
    return output_path
