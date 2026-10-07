"""Draws the welcome banner image (avatar, name, server and member number)."""
import io

from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = 25_000_000  # refuse absurdly large custom backgrounds

W, H = 960, 320


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)  # Pillow >= 10.1 bundles a scalable font


def _safe(text: str, fallback: str) -> str:
    """Keep characters the font can draw (Latin, Greek, Cyrillic); drop emoji and symbols."""
    cleaned = "".join(
        ch for ch in text if ch.isprintable() and (ord(ch) < 0x0250 or 0x0370 <= ord(ch) <= 0x052F)
    )
    cleaned = " ".join(cleaned.split())  # collapse gaps left by removed emoji
    return cleaned or fallback


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _shade(rgb: tuple, factor: float) -> tuple:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)


def _gradient(c1: tuple, c2: tuple) -> Image.Image:
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for x in range(W):
        t = x / (W - 1)
        draw.line([(x, 0), (x, H)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))
    return img


def _fit(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int, minimum: int) -> tuple[ImageFont.ImageFont, str]:
    size = start
    while size > minimum:
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font, text
        size -= 2
    font = _font(minimum)
    while len(text) > 1 and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return font, text + "…"


def _background(accent: tuple, bg_bytes: bytes | None) -> Image.Image:
    if bg_bytes:
        try:
            bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")
            scale = max(W / bg.width, H / bg.height)
            bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1), Image.LANCZOS)
            left, top = (bg.width - W) // 2, (bg.height - H) // 2
            bg = bg.crop((left, top, left + W, top + H)).convert("RGBA")
            return Image.alpha_composite(bg, Image.new("RGBA", (W, H), (0, 0, 0, 120)))
        except Exception:
            pass  # fall back to the gradient if the image can't be read
    img = _gradient(_shade(accent, 0.45), _shade(accent, 1.05)).convert("RGBA")
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(deco)
    d.ellipse((W - 330, -140, W + 90, 280), fill=(255, 255, 255, 22))
    d.ellipse((W - 520, 170, W - 150, 540), fill=(255, 255, 255, 14))
    d.ellipse((-120, -160, 240, 150), fill=(255, 255, 255, 12))
    return Image.alpha_composite(img, deco)


def render_card(
    avatar_bytes: bytes,
    username: str,
    server: str,
    member_text: str,
    color_hex: str = "2ECC71",
    bg_bytes: bytes | None = None,
) -> io.BytesIO:
    accent = _rgb(color_hex)
    card = _background(accent, bg_bytes)
    draw = ImageDraw.Draw(card)

    # Avatar with a white ring
    size, x0 = 200, 52
    y0 = (H - size) // 2
    ring = 8
    draw.ellipse((x0 - ring, y0 - ring, x0 + size + ring, y0 + size + ring), fill=(255, 255, 255, 235))
    try:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        avatar = Image.new("RGBA", (size, size), _shade(accent, 0.8) + (255,))
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    card.paste(avatar, (x0, y0), mask.resize((size, size), Image.LANCZOS))

    text_x = x0 + size + ring + 42
    max_w = W - text_x - 40

    label_font = _font(26)
    name_font, name = _fit(draw, _safe(username, "New Member"), max_w, 66, 30)
    server_font, server_text = _fit(draw, "to " + _safe(server, "the server"), max_w, 32, 18)
    pill_font = _font(26)
    pill_w = int(draw.textlength(member_text, font=pill_font)) + 44
    label_chars, x = [], text_x
    for ch in "WELCOME":
        label_chars.append((x, ch))
        x += draw.textlength(ch, font=label_font) + 9

    # Soft shadows and the translucent pill go on their own layer so they blend properly
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    for cx, ch in label_chars:
        ld.text((cx + 2, 61), ch, font=label_font, fill=(0, 0, 0, 90))
    ld.text((text_x + 2, 97), name, font=name_font, fill=(0, 0, 0, 90))
    ld.text((text_x + 2, 181), server_text, font=server_font, fill=(0, 0, 0, 90))
    ld.rounded_rectangle((text_x, 238, text_x + pill_w, 284), radius=23, fill=(0, 0, 0, 95))
    card = Image.alpha_composite(card, layer)
    draw = ImageDraw.Draw(card)

    for cx, ch in label_chars:
        draw.text((cx, 58), ch, font=label_font, fill=(255, 255, 255, 220))
    draw.text((text_x, 94), name, font=name_font, fill=(255, 255, 255, 255))
    draw.text((text_x, 178), server_text, font=server_font, fill=(255, 255, 255, 230))
    draw.text((text_x + 22, 246), member_text, font=pill_font, fill=(255, 255, 255, 255))

    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
