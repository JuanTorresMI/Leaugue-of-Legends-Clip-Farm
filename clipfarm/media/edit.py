"""Bulk clip editing for watchability: 9:16 vertical + background music + loudness
normalization + fade in/out, all in a single ffmpeg encode.

Music is chosen at random from the configured music/ folder, which the user fills with their
own royalty-free tracks (e.g. from YouTube's Audio Library). An empty folder simply skips the
music layer; the vertical conversion, normalization, and fades still apply.
"""
from __future__ import annotations

import logging
import random
from pathlib import Path

from clipfarm.config import get_settings
from clipfarm.media.ffmpeg import FfmpegError, _run, duration_seconds

logger = logging.getLogger(__name__)

_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
# Vertical base: full frame centered and crisp over a blurred, stretched copy (see media/ffmpeg
# to_vertical for why a plain crop looks fuzzy).
_VERTICAL_BG = "scale=1080:1920,gblur=sigma=20,setsar=1"
_VERTICAL_FG = "scale=1080:-2,setsar=1"
_VW, _VH = 1080, 1920  # vertical output dimensions


def hook_text_for(media_file) -> str | None:
    """The short punchy line burned over the start of a clip, derived from its match data.
    None when there's nothing worth saying (falls back to no caption)."""
    from clipfarm.riot.champion_names import display_name
    from clipfarm.riot.metadata_builder import streak_label

    def _get(key):
        try:
            return media_file[key]
        except (KeyError, IndexError, TypeError):
            return getattr(media_file, key, None)

    if _get("kind") != "clip":
        return None
    champ = _get("champion")
    champ_name = display_name(champ).upper() if champ else None
    streak = _get("kill_streak")
    if streak and streak >= 5:
        return "PENTAKILL!"
    if streak and streak >= 2:
        return streak_label(streak).upper() + "!"
    if champ_name:
        return champ_name
    return "WATCH THIS"


def _render_text_png(
    text: str,
    out_path: Path,
    *,
    y_frac: float,
    max_size: int,
    min_size: int,
    stroke: int,
    alpha: int = 255,
    uppercase: bool = True,
) -> Path | None:
    """Render a text line as a transparent 1080x1920 PNG (Anton, white on a black outline) so
    it can be overlaid with a plain ffmpeg overlay -- far more robust across platforms than
    ffmpeg's drawtext escaping. The font shrinks from max_size until the line fits 90% of the
    width. Returns None if Pillow/font is unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return None

    font_path = get_settings().project_root / "assets" / "fonts" / "Anton-Regular.ttf"
    canvas = Image.new("RGBA", (_VW, _VH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    if uppercase:
        text = text.upper()
    max_w = int(_VW * 0.9)
    size = max_size
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    while size >= min_size:
        try:
            font = ImageFont.truetype(str(font_path), size)
        except OSError:
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:
                font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if bbox[2] - bbox[0] <= max_w:
            break
        size -= 10

    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw = bbox[2] - bbox[0]
    x = (_VW - tw) // 2 - bbox[0]
    y = int(_VH * y_frac) - bbox[1]
    draw.text(
        (x, y), text, font=font,
        fill=(255, 255, 255, alpha), stroke_width=stroke, stroke_fill=(0, 0, 0, alpha),
    )

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, "PNG")
        return out_path
    except OSError:
        return None


def _render_caption_png(text: str, out_path: Path) -> Path | None:
    """The big hook line burned over the clip's first seconds (upper third, clear of the
    phone UI at the very top)."""
    return _render_text_png(text, out_path, y_frac=0.16, max_size=150, min_size=60, stroke=8)


def _render_watermark_png(text: str, out_path: Path) -> Path | None:
    """A small translucent channel handle shown for the whole clip -- brand recall for viewers
    who never open the description. Sits at ~74% height: below the action, above the Shorts
    bottom UI (title/handle overlay), left of nothing (centered, clear of the right icon rail)."""
    return _render_text_png(
        text, out_path, y_frac=0.74, max_size=44, min_size=28, stroke=3, alpha=160, uppercase=False
    )


def pick_music() -> Path | None:
    """A random track from the music folder, or None if the folder is empty/missing."""
    music_dir = get_settings().editing.music_dir
    if not music_dir.exists():
        return None
    tracks = [p for p in music_dir.iterdir() if p.suffix.lower() in _AUDIO_EXTS]
    return random.choice(tracks) if tracks else None


def _fade_suffix(kind: str, duration: float, fin: float, fout: float) -> str:
    """Build ',fade=...'/',afade=...' for video ('fade') or audio ('afade'). Empty when the
    clip is too short to fade cleanly, and zeroed fades are dropped individually (the default
    config runs fade-free so clips loop seamlessly -- see config.yaml's editing block)."""
    if duration < fin + fout + 0.5:
        return ""
    name = "afade" if kind == "audio" else "fade"
    parts = []
    if fin > 0:
        parts.append(f",{name}=t=in:st=0:d={fin:.2f}")
    if fout > 0:
        parts.append(f",{name}=t=out:st={max(duration - fout, 0):.2f}:d={fout:.2f}")
    return "".join(parts)


def prepared_clip(video_path: Path, hook_text: str | None = None) -> Path:
    """Cached upload-ready vertical clip, shared by every clip publisher (YouTube Shorts,
    Facebook Reels, ...). Full editing when enabled, else plain 9:16 vertical. `hook_text`
    is burned over the first seconds as a retention hook when editing + hook_caption are on."""
    from clipfarm.media.ffmpeg import to_vertical

    settings = get_settings()
    cache_path = settings.project_root / "data" / "converted" / f"{video_path.stem}_edited.mp4"
    if cache_path.exists():
        return cache_path
    if settings.editing.enabled:
        try:
            return prepare_clip(video_path, cache_path, hook_text=hook_text)
        except FfmpegError:
            # An edit failure (bad filter graph, missing codec, ...) must never block the
            # upload entirely -- fall back to a plain vertical conversion.
            logger.exception("Full clip edit failed for %s; falling back to plain vertical", video_path)
            return to_vertical(video_path, cache_path)
    return to_vertical(video_path, cache_path)


def prepare_clip(input_path: Path, output_path: Path, hook_text: str | None = None) -> Path:
    """Produce the upload-ready vertical clip with music/normalization/fades (+ optional hook
    caption) applied."""
    settings = get_settings()
    ed = settings.editing
    ffmpeg = str(settings.ascent.ffmpeg_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        duration = duration_seconds(input_path)
    except FfmpegError:
        duration = 0.0

    vfade = _fade_suffix("video", duration, ed.fade_in_seconds, ed.fade_out_seconds)
    afade = _fade_suffix("audio", duration, ed.fade_in_seconds, ed.fade_out_seconds)
    norm = "loudnorm," if ed.normalize_audio else ""

    music = pick_music() if ed.enabled else None
    inputs = ["-i", str(input_path)]
    next_index = 1
    music_label = None
    if music is not None:
        inputs += ["-i", str(music)]
        music_label = next_index
        next_index += 1
        logger.info("Editing clip with music track: %s", music.name)

    # Optional hook caption: rendered to a PNG (robust) and overlaid for the first N seconds.
    caption_png: Path | None = None
    caption_index: int | None = None
    if ed.hook_caption and hook_text:
        caption_png = _render_caption_png(hook_text, output_path.with_suffix(".caption.png"))
        if caption_png is not None:
            inputs += ["-loop", "1", "-t", f"{ed.hook_seconds:.2f}", "-i", str(caption_png)]
            caption_index = next_index
            next_index += 1

    # Optional channel-handle watermark, shown for the whole clip. The looped PNG input is
    # bounded to the clip's duration so the graph can't run past the main stream.
    watermark_png: Path | None = None
    watermark_index: int | None = None
    if ed.watermark_text.strip():
        watermark_png = _render_watermark_png(ed.watermark_text.strip(), output_path.with_suffix(".wm.png"))
        if watermark_png is not None:
            wm_seconds = duration if duration > 0 else 600
            inputs += ["-loop", "1", "-t", f"{wm_seconds:.2f}", "-i", str(watermark_png)]
            watermark_index = next_index
            next_index += 1

    # --- video chain: blurred bg + crisp fg, then caption/watermark overlays, then fades ---
    base = (
        f"[0:v]{_VERTICAL_BG}[bg];"
        f"[0:v]{_VERTICAL_FG}[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
    )
    overlays: list[str] = []
    if caption_index is not None:
        overlays.append(f"[{caption_index}:v]overlay=0:0:enable='lte(t,{ed.hook_seconds:.2f})'")
    if watermark_index is not None:
        overlays.append(f"[{watermark_index}:v]overlay=0:0")

    if overlays:
        steps = [f"{base}[v0]"]
        for i, overlay in enumerate(overlays):
            fade = vfade if i == len(overlays) - 1 else ""  # fades apply after the last overlay
            out_label = "[v]" if i == len(overlays) - 1 else f"[v{i + 1}]"
            steps.append(f"[v{i}]{overlay}{fade}{out_label}")
        video_part = ";".join(steps)
    else:
        video_part = f"{base}{vfade}[v]"

    # --- audio chain ---
    if music_label is not None:
        audio_part = (
            f"[0:a:0]{norm.rstrip(',')}[ga];"  # game audio, normalized
            f"[{music_label}:a]volume={ed.clip_music_volume}[ma];"  # music, quiet
            f"[ga][ma]amix=inputs=2:duration=first:normalize=0{afade}[a]"
        )
    else:
        audio_part = f"[0:a:0]{norm}anull{afade}[a]"

    filter_complex = f"{video_part};{audio_part}"

    try:
        _run(
            [
                ffmpeg, "-y", *inputs,
                "-filter_complex", filter_complex,
                "-map", "[v]", "-map", "[a]",
                "-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "19",
                "-c:a", "aac", "-b:a", "192k",
                str(output_path),
            ]
        )
    finally:
        if caption_png is not None:
            caption_png.unlink(missing_ok=True)
        if watermark_png is not None:
            watermark_png.unlink(missing_ok=True)
    return output_path
