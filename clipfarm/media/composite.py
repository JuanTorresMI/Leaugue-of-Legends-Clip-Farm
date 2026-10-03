"""Thumbnail compositor: gameplay frame + champion art + bold impact text.

Two layouts. Full games get a 16:9 (1280x720) composite; clips get a 9:16 (1080x1920) one
built around the actual gameplay frame, so every clip thumbnail is visibly its own moment
rather than the same art-plus-badge card with a name swapped (see `_generate_vertical`).

Full-game layout (1280x720):
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
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from clipfarm.config import PROJECT_ROOT
from clipfarm.media import ddragon

logger = logging.getLogger(__name__)

W, H = 1280, 720
VW, VH = 1080, 1920  # vertical (clip) layout
_FONT_PATH = PROJECT_ROOT / "assets" / "fonts" / "Anton-Regular.ttf"

# Accent colors (RGB) keyed by "mood".
GOLD = (240, 190, 70)
BLUE = (70, 160, 240)
RED = (225, 70, 70)
GREEN = (90, 210, 120)
# Clip accents: one colour per champion (hashed), so a channel's thumbnails aren't all gold but
# the same champion always looks the same -- recognisable without being uniform.
_CLIP_PALETTE = (
    (240, 190, 70), (70, 160, 240), (90, 210, 120), (240, 110, 90), (190, 120, 240),
    (80, 220, 210), (250, 150, 60), (235, 95, 160),
)


def _roll(seed: str, salt: str, n: int) -> int:
    return zlib.crc32(f"{salt}:{seed}".encode()) % n if n else 0


def champion_accent(champion: str) -> tuple[int, int, int]:
    return _CLIP_PALETTE[_roll(champion, "accent", len(_CLIP_PALETTE))]


@dataclass
class ThumbnailSpec:
    champion: str  # internal Data Dragon name, e.g. "JarvanIV"
    hook: str  # short punchy top badge, e.g. "PENTAKILL", "WIN", "vs VIEGO"
    subtitle: str  # e.g. "12/4/6  •  Emerald Jungle"
    accent: tuple[int, int, int] = GOLD
    champion_display: str | None = None  # human name for the big text; falls back to champion
    vertical: bool = False  # 9:16 clip layout instead of the 16:9 full-game card
    victims: list[str] = field(default_factory=list)  # internal names of who the kill(s) were on
    kill_streak: int = 0
    seed: str = ""  # drives the per-clip layout rolls (art side etc.)


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


def _clip_spec(champion: str, streak: int, victims: list[str], context: str, queue: str, seed: str) -> ThumbnailSpec:
    from clipfarm.riot.champion_names import display_name
    from clipfarm.riot.metadata_builder import streak_label

    if streak >= 2:
        hook, accent = streak_label(streak), (GOLD if streak >= 5 else champion_accent(champion))
    elif streak == 1:
        hook, accent = "KILL", champion_accent(champion)
    else:
        hook, accent = "HIGHLIGHT", champion_accent(champion)
    return ThumbnailSpec(
        champion=champion,
        hook=hook,
        subtitle=context or queue or "League of Legends",
        accent=accent,
        champion_display=display_name(champion),
        vertical=True,
        victims=list(victims),
        kill_streak=streak,
        seed=seed,
    )


def spec_for_clip(match, highlight, rank: str | None) -> ThumbnailSpec:
    """Build a thumbnail spec for a clip. `highlight` is a jobs.models.ClipHighlight or None."""
    context = " ".join(bit for bit in [rank, match.role] if bit)
    streak = highlight.kill_streak if highlight else 0
    victims = list(highlight.victim_champions) if highlight else []
    seed = f"{match.match_id}:{highlight.first_kill_ms if highlight else ''}"
    return _clip_spec(match.champion, streak, victims, context, match.queue_type, seed)


def spec_from_row(row) -> ThumbnailSpec | None:
    """Rebuild a thumbnail spec from a stored media_files row (sqlite3.Row or mapping).

    Returns None when the row has no champion data (unmatched) -- callers should fall back
    to a plain frame-grab thumbnail in that case.
    """
    from clipfarm.riot.champion_names import display_name

    champion = row["champion"]
    if not champion:
        return None

    rank = row["rank"]
    role = row["role"]
    context = " ".join(bit for bit in [rank, role] if bit)

    if row["kind"] == "clip":
        import json

        try:
            victims = json.loads(row["victim_champions"] or "[]")
        except (KeyError, IndexError, TypeError, ValueError):
            victims = []
        try:
            seed = f"{row['riot_match_id']}:{row['highlight_ms']}"
        except (KeyError, IndexError):
            seed = champion
        return _clip_spec(champion, row["kill_streak"] or 0, victims, context, row["queue_type"], seed)
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
    if spec.vertical:
        return _generate_vertical(spec, gameplay_frame, output_jpg_path)
    canvas = _build_background(gameplay_frame, spec.champion)
    _paste_champion(canvas, spec.champion)
    _left_gradient(canvas)
    _draw_text_stack(canvas, spec)
    _accent_border(canvas, spec.accent)

    output_jpg_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_jpg_path, "JPEG", quality=92)
    return output_jpg_path


# --- 9:16 clip layout -------------------------------------------------------------------------
#
#   top    (0..690)     champion loading art on one side (feathered), headline stack on the other
#   band   (690..1298)  the actual gameplay frame, crisp, full width, accent rules above/below
#   bottom (1298..1920) "vs" + victim icons with names, then the rank/role line
#
# What makes each one different: the frame (its own moment), the art side (seeded), the accent
# (per champion), the headline (streak or champion), and the victims (icons from Data Dragon).

_BAND_TOP, _BAND_H = 690, 608


def _open_rgb(path: Path | None) -> Image.Image | None:
    if not path or not Path(path).exists():
        return None
    try:
        return Image.open(path).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _vertical_background(gameplay_frame: Path | None, champion: str) -> Image.Image:
    source = _open_rgb(gameplay_frame) or _open_rgb(ddragon.splash_art(champion))
    if source is None:
        return Image.new("RGB", (VW, VH), (14, 16, 22))
    bg = _cover(source, VW, VH).filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Color(bg).enhance(1.3)
    return Image.blend(bg, Image.new("RGB", (VW, VH), (0, 0, 0)), 0.55)


def _feathered(art: Image.Image, *, fade_bottom: float, fade_inner: float, inner_on_right: bool) -> Image.Image:
    """Alpha mask that fades the art's bottom edge and its inner (towards-the-text) edge."""
    w, h = art.size
    mask = Image.new("L", (w, h), 255)
    fh = int(h * fade_bottom)
    if fh:
        grad = Image.linear_gradient("L").rotate(180).resize((w, fh))  # white -> black downwards
        mask.paste(grad, (0, h - fh))
    fw = int(w * fade_inner)
    if fw:
        # linear_gradient runs black (top) -> white (bottom); rotate so it runs white -> black
        # *towards* the inner edge (clockwise puts the black end on the right).
        strip = Image.linear_gradient("L").rotate(-90 if inner_on_right else 90, expand=True).resize((fw, h))
        inner = Image.new("L", (w, h), 255)
        inner.paste(strip, (w - fw if inner_on_right else 0, 0))
        mask = ImageChops.multiply(mask, inner)
    return mask


def _rounded(image: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, image.width - 1, image.height - 1], radius=radius, fill=255)
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


def _headline_lines(spec: ThumbnailSpec) -> tuple[list[str], str]:
    """(big lines, small line) for the top block."""
    from clipfarm.riot.champion_names import display_name

    champ = (spec.champion_display or spec.champion).upper()
    if spec.kill_streak >= 5:
        return ["PENTA", "KILL"], champ
    if spec.kill_streak >= 2:
        first = spec.hook.upper().split()[0]
        return [first, "KILL"], champ
    words = champ.split()
    lines = [champ] if len(words) == 1 or len(champ) <= 9 else [words[0], " ".join(words[1:])]
    if spec.kill_streak == 1 and spec.victims:
        return lines, f"vs {display_name(spec.victims[0]).upper()}"
    return lines, "HIGHLIGHT" if spec.kill_streak == 0 else "SOLO KILL"


def _draw_fitted(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, max_w: int, size: int,
                 min_size: int, fill, stroke: int, right_align: bool = False) -> int:
    """Draw one line, shrinking to fit max_w. Returns the line height used."""
    font = _font(size)
    while size > min_size:
        font = _font(size)
        bb = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if bb[2] - bb[0] <= max_w:
            break
        size -= 6
    bb = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tx = x - (bb[2] - bb[0]) if right_align else x
    draw.text((tx - bb[0], y - bb[1]), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))
    return bb[3] - bb[1]


def _generate_vertical(spec: ThumbnailSpec, gameplay_frame: Path | None, output_jpg_path: Path) -> Path:
    from clipfarm.riot.champion_names import display_name

    seed = spec.seed or spec.champion
    art_left = _roll(seed, "side", 2) == 0
    canvas = _vertical_background(gameplay_frame, spec.champion)
    draw = ImageDraw.Draw(canvas)

    # --- the gameplay band: this clip's own moment, crisp ---
    frame = _open_rgb(gameplay_frame)
    if frame is not None:
        band = _cover(frame, VW, _BAND_H)
        band = ImageEnhance.Contrast(band).enhance(1.08)
        canvas.paste(band, (0, _BAND_TOP))
    draw.rectangle([0, _BAND_TOP - 6, VW, _BAND_TOP], fill=spec.accent)
    draw.rectangle([0, _BAND_TOP + _BAND_H, VW, _BAND_TOP + _BAND_H + 6], fill=spec.accent)

    # --- champion art, top, one side ---
    art_w = 0
    art = _open_rgb(ddragon.loading_art(spec.champion))
    if art is not None:
        target_h = 780
        art = art.resize((int(art.width * target_h / art.height), target_h), Image.LANCZOS)
        art_w = art.width
        mask = _feathered(art, fade_bottom=0.3, fade_inner=0.3, inner_on_right=art_left)
        x = -int(art_w * 0.06) if art_left else VW - art_w + int(art_w * 0.06)
        canvas.paste(art, (x, -20), mask)
        draw = ImageDraw.Draw(canvas)

    # --- headline stack on the opposite side ---
    margin = 48
    text_w = VW - margin * 2 - (int(art_w * 0.72) if art_w else 0)
    text_x = (VW - margin) if art_left else margin
    big, small = _headline_lines(spec)
    y = 120
    for line in big:
        h = _draw_fitted(draw, line, text_x, y, text_w, 170, 72, (255, 255, 255), 7, right_align=art_left)
        y += h + 14
    y += 10
    _draw_fitted(draw, small, text_x, y, text_w, 64, 36, spec.accent, 4, right_align=art_left)

    # --- bottom: who it was on, then where ---
    y = _BAND_TOP + _BAND_H + 70
    victims = [v for v in spec.victims if v][:5]
    if victims:
        icon = 128 if len(victims) <= 3 else 104
        gap = 22
        label_font = _font(56)
        label_w = draw.textbbox((0, 0), "vs", font=label_font)[2]
        total = label_w + 28 + len(victims) * icon + (len(victims) - 1) * gap
        x = (VW - total) // 2
        draw.text((x, y + icon // 2 - 34), "vs", font=label_font, fill=spec.accent,
                  stroke_width=3, stroke_fill=(0, 0, 0))
        x += label_w + 28
        name_font = _font(32)
        for victim in victims:
            tile = _open_rgb(ddragon.square_icon(victim))
            if tile is not None:
                tile = tile.resize((icon, icon), Image.LANCZOS)
                canvas.paste(_rounded(tile, 18), (x, y), _rounded(tile, 18))
                draw = ImageDraw.Draw(canvas)
                draw.rounded_rectangle([x, y, x + icon - 1, y + icon - 1], radius=18, outline=spec.accent, width=3)
            else:
                draw.rounded_rectangle([x, y, x + icon - 1, y + icon - 1], radius=18, fill=(30, 32, 40),
                                       outline=spec.accent, width=3)
            name = display_name(victim)
            nb = draw.textbbox((0, 0), name, font=name_font)
            nx = x + (icon - (nb[2] - nb[0])) // 2
            draw.text((nx, y + icon + 12), name, font=name_font, fill=(235, 235, 235),
                      stroke_width=2, stroke_fill=(0, 0, 0))
            x += icon + gap
        y += icon + 12 + 48 + 40
    else:
        y += 40

    sub_font = _font(60)
    sb = draw.textbbox((0, 0), spec.subtitle, font=sub_font, stroke_width=4)
    draw.text(((VW - (sb[2] - sb[0])) // 2 - sb[0], y), spec.subtitle, font=sub_font, fill=spec.accent,
              stroke_width=4, stroke_fill=(0, 0, 0))

    output_jpg_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_jpg_path, "JPEG", quality=92)
    return output_jpg_path
