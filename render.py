"""Daily cover image rendering (Workstream D).

AI generates an abstract background (OpenAI Images API); the Ukrainian text is
drawn locally with Pillow because image models render Cyrillic unreliably.
"""

import base64
import hashlib
import io
import logging
import os

from PIL import Image, ImageDraw, ImageFont
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

FONTS_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")
FONT_REGULAR = os.path.join(FONTS_DIR, "PTSans-Regular.ttf")
FONT_BOLD = os.path.join(FONTS_DIR, "PTSans-Bold.ttf")
# DejaVu Sans carries the zodiac symbols (♈–♓); PT Sans does not.
FONT_SYMBOL = os.path.join(FONTS_DIR, "DejaVuSans.ttf")

CARD_SIZE = (1080, 1350)  # Telegram portrait 4:5

# Day-over-day variety: a fixed prompt produces the same aesthetic every day, so
# we rotate palette + texture deterministically from a per-day seed. Distinct
# adjacent seeds (e.g. ISO dates) hash to unrelated indices, so consecutive days
# look clearly different while a given day stays stable (and cache-friendly).
_PALETTES = [
    "warm gold and soft rose sunrise",
    "deep indigo night with teal highlights",
    "lavender and dusty-rose dusk",
    "emerald and aquamarine aurora",
    "amber and crimson autumn glow",
    "icy blue and silver winter light",
    "violet and magenta nebula",
    "soft sage-green and cream",
    "peach and coral morning haze",
    "midnight blue with golden stars",
    "turquoise and pearl dawn",
    "burgundy and plum twilight",
]
_TEXTURES = [
    "dreamy cosmic haze with subtle stars and light bokeh",
    "soft watercolour gradients",
    "painterly oil-brush clouds",
    "starry nebula with faint constellations",
    "misty layered mountain silhouettes",
    "smooth silk-like flowing gradients",
    "gentle aurora ribbons across the sky",
]


def build_background_prompt(
    global_summary: str = "", day_seed: str = "", mood_hint: str = ""
) -> str:
    """Short ENGLISH prompt for the image model, varied per day. Forbids any text.

    palette/texture are chosen from a hash of day_seed (falls back to the
    summary) so each day gets a distinct look while staying deterministic.
    """
    basis = day_seed or global_summary or "default"
    h = int(hashlib.sha1(basis.encode("utf-8")).hexdigest(), 16)
    palette = _PALETTES[h % len(_PALETTES)]
    texture = _TEXTURES[(h // len(_PALETTES)) % len(_TEXTURES)]
    base = (
        f"Abstract atmospheric celestial poster background, {texture}, "
        f"{palette} colour palette, elegant editorial aesthetic, painterly, "
        f"high quality, vertical composition"
    )
    if mood_hint:
        base += f", mood: {mood_hint}"
    base += ". Absolutely no text, no letters, no words, no numbers, no typography, no symbols."
    return base


def _crop_to_size(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    target_ratio = size[0] / size[1]
    w, h = img.size
    ratio = w / h
    if ratio > target_ratio:
        new_w = int(h * target_ratio)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target_ratio)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    return img.resize(size, Image.LANCZOS)


def _wrap(draw, text, font, max_width):
    lines = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def render_card(
    title: str,
    body: str,
    background_bytes: bytes,
    size: tuple[int, int] = CARD_SIZE,
    *,
    scrim_opacity: int = 150,
    body_size: int = 56,
    text_anchor: str = "bottom",
) -> io.BytesIO:
    """Compose a cover: background + dark scrim + bold title + wrapped body.

    Returns a PNG BytesIO. Auto-shrinks the body font if the text overflows.
    """
    bg = Image.open(io.BytesIO(background_bytes)).convert("RGB")
    bg = _crop_to_size(bg, size)
    img = bg.copy()
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    margin = int(size[0] * 0.08)
    max_width = size[0] - 2 * margin

    title_size = int(body_size * 1.25)
    # Fit text by shrinking body font until the block fits ~62% of height.
    for attempt_body in range(body_size, 28, -4):
        attempt_title = int(attempt_body * 1.25)
        title_font = ImageFont.truetype(FONT_BOLD, attempt_title)
        body_font = ImageFont.truetype(FONT_REGULAR, attempt_body)
        title_lines = _wrap(draw, title, title_font, max_width) if title else []
        body_lines = _wrap(draw, body, body_font, max_width)
        line_h_title = attempt_title + 12
        line_h_body = attempt_body + 14
        block_h = len(title_lines) * line_h_title + (
            18 if title_lines else 0
        ) + len(body_lines) * line_h_body
        if block_h <= size[1] * 0.62:
            body_size, title_size = attempt_body, attempt_title
            break

    # Scrim behind the text region.
    pad = int(margin * 0.5)
    if text_anchor == "bottom":
        region_top = size[1] - block_h - margin - pad
        text_y = size[1] - block_h - margin
    else:  # center
        region_top = (size[1] - block_h) // 2 - pad
        text_y = (size[1] - block_h) // 2
    draw.rounded_rectangle(
        [margin - pad, max(region_top, 0), size[0] - margin + pad, text_y + block_h + pad],
        radius=32,
        fill=(0, 0, 0, scrim_opacity),
    )

    y = text_y
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += line_h_title
    if title_lines:
        y += 18
    for line in body_lines:
        draw.text((margin, y), line, font=body_font, fill=(240, 240, 240, 255))
        y += line_h_body

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def render_sign_card(
    symbol: str,
    name: str,
    vibe: str,
    background_bytes: bytes,
    size: tuple[int, int] = CARD_SIZE,
    *,
    scrim_opacity: int = 160,
    body_size: int = 52,
) -> io.BytesIO:
    """Compose a per-sign card: shared background + large zodiac symbol + vibe.

    The zodiac symbol (DejaVu) is a big translucent watermark in the upper area;
    the sign name (bold) and vibe text sit in a scrim at the bottom. Returns PNG.
    """
    bg = Image.open(io.BytesIO(background_bytes)).convert("RGB")
    bg = _crop_to_size(bg, size)
    img = bg.copy().convert("RGBA")
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    margin = int(size[0] * 0.08)
    max_width = size[0] - 2 * margin

    # Large translucent zodiac symbol as the visual accent (upper third).
    symbol_font = ImageFont.truetype(FONT_SYMBOL, int(size[0] * 0.40))
    sb = draw.textbbox((0, 0), symbol, font=symbol_font)
    sx = (size[0] - (sb[2] - sb[0])) // 2 - sb[0]
    sy = int(size[1] * 0.10) - sb[1]
    draw.text((sx, sy), symbol, font=symbol_font, fill=(255, 255, 255, 90))

    # Title (name) + body (vibe), auto-shrunk to fit, anchored to the bottom.
    name_text = name
    for attempt_body in range(body_size, 26, -3):
        attempt_title = int(attempt_body * 1.4)
        title_font = ImageFont.truetype(FONT_BOLD, attempt_title)
        body_font = ImageFont.truetype(FONT_REGULAR, attempt_body)
        body_lines = _wrap(draw, vibe, body_font, max_width)
        line_h_title = attempt_title + 12
        line_h_body = attempt_body + 14
        block_h = line_h_title + 18 + len(body_lines) * line_h_body
        if block_h <= size[1] * 0.50:
            break

    pad = int(margin * 0.5)
    text_y = size[1] - block_h - margin
    draw.rounded_rectangle(
        [margin - pad, text_y - pad, size[0] - margin + pad, text_y + block_h + pad],
        radius=32,
        fill=(0, 0, 0, scrim_opacity),
    )
    y = text_y
    draw.text((margin, y), name_text, font=title_font, fill=(255, 255, 255, 255))
    y += line_h_title + 18
    for line in body_lines:
        draw.text((margin, y), line, font=body_font, fill=(240, 240, 240, 255))
        y += line_h_body

    img = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


@retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
async def generate_background(client, prompt: str) -> bytes:
    """Generate a background via OpenAI Images, with a dall-e-3 fallback.

    gpt-image-1 may require a verified org; on access errors we fall back to
    dall-e-3 which returns b64 JSON.
    """
    try:
        resp = await client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1536",
            quality="medium",
        )
        return base64.b64decode(resp.data[0].b64_json)
    except Exception as exc:  # noqa: BLE001 - fall back to dall-e-3
        logger.warning("gpt-image-1 unavailable (%s); falling back to dall-e-3", exc)
        resp = await client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1792",
            response_format="b64_json",
        )
        return base64.b64decode(resp.data[0].b64_json)
