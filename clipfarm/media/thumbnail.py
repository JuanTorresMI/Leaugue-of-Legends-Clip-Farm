"""Generate a review thumbnail: ffmpeg frame-grab + optional Pillow text overlay."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from clipfarm.media.ffmpeg import FfmpegError, duration_seconds, grab_frame


def generate_thumbnail(
    video_path: Path, output_jpg_path: Path, offset_seconds: float, overlay_text: str | None = None
) -> Path:
    try:
        # Clamp so a short clip's configured offset can't seek past the end of the file.
        offset_seconds = min(offset_seconds, max(duration_seconds(video_path) - 0.1, 0))
    except FfmpegError:
        pass

    tmp_png = output_jpg_path.with_suffix(".tmp.png")
    grab_frame(video_path, tmp_png, offset_seconds)
    try:
        return _save_with_overlay(tmp_png, output_jpg_path, overlay_text)
    finally:
        tmp_png.unlink(missing_ok=True)


def add_overlay_to_image(source_image_path: Path, output_jpg_path: Path, overlay_text: str | None) -> Path:
    """Same overlay treatment as generate_thumbnail, but starting from an existing image
    (e.g. Ascent's own auto-generated clip thumbnail) instead of grabbing a fresh frame."""
    return _save_with_overlay(source_image_path, output_jpg_path, overlay_text)


def _save_with_overlay(source_image_path: Path, output_jpg_path: Path, overlay_text: str | None) -> Path:
    image = Image.open(source_image_path).convert("RGB")
    if overlay_text:
        _draw_overlay(image, overlay_text)

    output_jpg_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_jpg_path, "JPEG", quality=90)
    return output_jpg_path


def _draw_overlay(image: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(image)
    font_size = max(image.width // 14, 32)
    try:
        font = ImageFont.load_default(size=font_size)
    except TypeError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=4)
    text_width = bbox[2] - bbox[0]
    x = max((image.width - text_width) // 2, 10)
    y = image.height - font_size - image.height // 12

    draw.text(
        (x, y),
        text,
        font=font,
        fill="white",
        stroke_width=4,
        stroke_fill="black",
    )
