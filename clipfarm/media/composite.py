"""High-CTR 16:9 thumbnail compositor: gameplay frame + champion art + bold impact text.

Layout (1280x720):
  - gameplay frame, scaled to cover, darkened + lightly blurred so text pops
  - champion loading art cut out on the right, feathered into the background
  - left-to-right dark gradient so the left-side text stack stays readable
  - a colored accent badge for the hook ("PENTAKILL" / "WIN" / "vs Viego")
  - champion name in Anton, big, white with a heavy black outline
  - KDA + rank subtitle underneath
Every external asset (champion art, font) degrades gracefully: if it is missing the
compositor still returns a usable thumbnail from whatever pieces it has.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from clipfarm.config import PROJECT_ROOT
from clipfarm.media import ddragon

logger = logging.getLogger(__name__)

W, H = 1280, 720
_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "Anton-Regular.ttf"

# Accent colors (RGB) keyed by "mood".
GOLD = (240, 190, 70)
BLUE = (70, 160, 240)
RED = (225, 70, 70)
GREEN = (90, 210, 120)


@dataclass
class ThumbnailSpec:
    champion: str  # internal Data Dragon name, e.g. "JarvanIV"
    hook: str  # short punchy top badge, e.g. "PENTAKILL", "WIN", "vs VIEGO"
    subtitle: str  # e.g. "12/4/6  •  Emerald Jungle"
    accent: tuple[int, int, int] = GOLD
    champion_display: str | None = None  # human name for the big text; falls back to champion


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(_FONT_PATH), size)
    except OSError:
        logger.warning("Anton font not found at %s; using default font", _FONT_PATH)
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def _cover(image: Image.Image, w: int, h: int) -> Image.Image:
    """Scale-and-center-crop `image` to exactly w x h (CSS object-fit: cover)."""
    src_ratio = image.width / image.height
    dst_ratio = w / h
    if src_ratio > dst_ratio:
        new_h = h
        new_w = int(h * src_ratio)
    else:
        new_w = w
        new_h = int(w / src_ratio)
    resized = image.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _build_background(gameplay_frame: Path | None, champion: str) -> Image.Image:
    """Prefer the actual gameplay frame; fall back to champion splash; then a flat dark canvas."""
    source: Image.Image | None = None
    if gameplay_frame and gameplay_frame.exists():
        try:
            source = Image.open(gameplay_frame).convert("RGB")
        except Exception:  # noqa: BLE001
            source = None
    if source is None:
        splash = ddragon.splash_art(champion)
        if splash:
            try:
                source = Image.open(splash).convert("RGB")
            except Exception:  # noqa: BLE001
                source = None
    if source is None:
        return Image.new("RGB", (W, H), (18, 20, 26))

    bg = _cover(source, W, H).filter(ImageFilter.GaussianBlur(4))
    # Punchier thumbnails read better on the crowded browse shelf: pump saturation + contrast
    # so colors pop at small sizes, then darken uniformly so the text/art still read clearly.
    bg = ImageEnhance.Color(bg).enhance(1.35)
    bg = ImageEnhance.Contrast(bg).enhance(1.12)
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.blend(bg, dark, 0.42)


def _paste_champion(canvas: Image.Image, champion: str) -> None:
    art_path = ddragon.loading_art(champion)
    if not art_path:
        return
    try:
        art = Image.open(art_path).convert("RGB")
    except Exception:  # noqa: BLE001
        return

    # Loading art is ~308x560 portrait; scale to fill most of the height on the right third.
    target_h = int(H * 1.05)
    scale = target_h / art.height
    art = art.resize((int(art.width * scale), target_h), Image.LANCZOS)

    # Feather the left edge so the cutout blends into the background instead of a hard seam.
    mask = Image.new("L", art.size, 255)
    fade_w = int(art.width * 0.35)
    for x in range(fade_w):
        alpha = int(255 * (x / fade_w))
        for_col = Image.new("L", (1, art.height), alpha)
        mask.paste(for_col, (x, 0))

    x = W - art.width + int(art.width * 0.08)
    y = H - art.height + int(H * 0.02)
    canvas.paste(art, (x, y), mask)


def _left_gradient(canvas: Image.Image) -> None:
    """Dark-on-the-left gradient so the text stack has guaranteed contrast."""
    grad = Image.new("L", (W, 1))
    for x in range(W):
        # Strong at the left, gone by ~65% across.
        t = max(0.0, 1.0 - x / (W * 0.65))
        grad.putpixel((x, 0), int(200 * t))
    grad = grad.resize((W, H))
    shade = Image.new("RGB", (W, H), (0, 0, 0))
    canvas.paste(shade, (0, 0), grad)


def _draw_text_stack(canvas: Image.Image, spec: ThumbnailSpec) -> None:
    draw = ImageDraw.Draw(canvas)
    margin = 60

    # --- hook badge ---
    hook = spec.hook.upper()
    hook_font = _font(74)
    hb = draw.textbbox((0, 0), hook, font=hook_font)
    hook_w, hook_h = hb[2] - hb[0], hb[3] - hb[1]
    pad = 22
    badge_top = 70
    draw.rectangle(
        [margin, badge_top, margin + hook_w + pad * 2, badge_top + hook_h + pad * 2],
        fill=spec.accent,
    )
    draw.text(
        (margin + pad, badge_top + pad - hb[1]),
        hook,
        font=hook_font,
        fill=(15, 15, 20),
    )

    # --- champion name (the big line) ---
    name = (spec.champion_display or spec.champion).upper()
    name_font = _font(150)
    # Shrink to fit within ~62% of the width if a long name would overflow the art.
    max_w = int(W * 0.62)
    size = 150
    while size > 70:
        name_font = _font(size)
        nb = draw.textbbox((0, 0), name, font=name_font, stroke_width=6)
        if nb[2] - nb[0] <= max_w:
            break
        size -= 8
    name_y = badge_top + hook_h + pad * 2 + 40
    draw.text(
        (margin, name_y),
        name,
        font=name_font,
        fill=(255, 255, 255),
        stroke_width=6,
        stroke_fill=(0, 0, 0),
    )

    # --- subtitle (KDA / rank / role) ---
    sub_font = _font(52)
    nb = draw.textbbox((0, 0), name, font=name_font, stroke_width=6)
    sub_y = name_y + (nb[3] - nb[1]) + 34
    draw.text(
        (margin + 4, sub_y),
        spec.subtitle,
        font=sub_font,
        fill=spec.accent,
        stroke_width=4,
        stroke_fill=(0, 0, 0),
    )


def _full_game_hook(won: bool, opponent_champion: str | None) -> tuple[str, tuple[int, int, int]]:
    """The badge for a full-game thumbnail. A win is a brag worth the badge; a loss is not --
    "LOSS" on a thumbnail repels the click the same way "LOSS 4/8/12" did in titles. A lost game
    leads with the matchup instead (the thing a searcher is actually looking for)."""
    from clipfarm.riot.champion_names import display_name

    if won:
        return "WIN", GREEN
    if opponent_champion:
        return f"vs {display_name(opponent_champion)}", BLUE
    return "FULL GAME", BLUE


def spec_for_full_game(match, rank: str | None) -> ThumbnailSpec:
    """Build a thumbnail spec from a matched full game. `match` is a jobs.models.MatchContext."""
    from clipfarm.riot.champion_names import display_name

    hook, accent = _full_game_hook(match.win, match.opponent_champion)
    context = " ".join(bit for bit in [rank, match.role] if bit)  # "Emerald Jungle"
    return ThumbnailSpec(
        champion=match.champion,
        hook=hook,
        subtitle=_full_game_subtitle(match.win, match.kda, match.opponent_champion, context, hook),
        accent=accent,
        champion_display=display_name(match.champion),
    )


def _full_game_subtitle(won: bool, kda: str | None, opponent_champion: str | None, context: str, hook: str) -> str:
    """KDA only when it's a brag (a win), the matchup unless the badge already says it, then
    the rank/role phrase. Mirrors the title rule: never put a losing score on the artwork."""
    from clipfarm.riot.champion_names import display_name

    bits = []
    if won and kda:
        bits.append(kda)
    if opponent_champion and not hook.startswith("vs "):
        bits.append(f"vs {display_name(opponent_champion)}")
    if context:
        bits.append(context)
    return "   ".join(bits)


def spec_for_clip(match, highlight, rank: str | None) -> ThumbnailSpec:
    """Build a thumbnail spec for a clip. `highlight` is a jobs.models.ClipHighlight or None."""
    from clipfarm.riot.champion_names import display_name
    from clipfarm.riot.metadata_builder import streak_label

    champ = match.champion
    context = " ".join(bit for bit in [rank, match.role] if bit)
    if highlight and highlight.kill_streak >= 2:
        hook = streak_label(highlight.kill_streak)
        accent = GOLD
    else:
        hook = "HIGHLIGHT"
        accent = BLUE
    return ThumbnailSpec(
        champion=champ,
        hook=hook,
        subtitle=context or match.queue_type,
        accent=accent,
        champion_display=display_name(champ),
    )


def spec_from_row(row) -> ThumbnailSpec | None:
    """Rebuild a thumbnail spec from a stored media_files row (sqlite3.Row or mapping).

    Returns None when the row has no champion data (unmatched) -- callers should fall back
    to a plain frame-grab thumbnail in that case.
    """
    from clipfarm.riot.champion_names import display_name
    from clipfarm.riot.metadata_builder import streak_label

    champion = row["champion"]
    if not champion:
        return None

    rank = row["rank"]
    role = row["role"]
    context = " ".join(bit for bit in [rank, role] if bit)

    if row["kind"] == "clip":
        streak = row["kill_streak"]
        if streak and streak >= 2:
            hook, accent = streak_label(streak), GOLD
        else:
            hook, accent = "HIGHLIGHT", BLUE
        subtitle = context or (row["queue_type"] or "League of Legends")
    else:
        hook, accent = _full_game_hook(bool(row["win"]), row["opponent_champion"])
        subtitle = _full_game_subtitle(bool(row["win"]), row["kda"], row["opponent_champion"], context, hook)

    return ThumbnailSpec(
        champion=champion,
        hook=hook,
        subtitle=subtitle,
        accent=accent,
        champion_display=display_name(champion),
    )


def _accent_border(canvas: Image.Image, color: tuple[int, int, int], width: int = 6) -> None:
    """A thin accent frame that separates the thumbnail from YouTube's grid. Kept slim on
    purpose: a fat coloured border is the signature of auto-generated thumbnails."""
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, W - 1, H - 1], outline=color, width=width)


def generate_composite(spec: ThumbnailSpec, gameplay_frame: Path | None, output_jpg_path: Path) -> Path:
    canvas = _build_background(gameplay_frame, spec.champion)
    _paste_champion(canvas, spec.champion)
    _left_gradient(canvas)
    _draw_text_stack(canvas, spec)
    _accent_border(canvas, spec.accent)

    output_jpg_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_jpg_path, "JPEG", quality=92)
    return output_jpg_path
