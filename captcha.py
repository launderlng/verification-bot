import io
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# No look-alike characters (0/O, 1/I/L) so real people don't get tripped up.
CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def make_code(length: int = 5) -> str:
    return "".join(random.choices(CHARS, k=length))


def make_math() -> tuple[str, int]:
    kind = random.choice("+-x")
    if kind == "+":
        a, b = random.randint(2, 25), random.randint(2, 25)
        return f"What is {a} + {b}?", a + b
    if kind == "-":
        a = random.randint(10, 40)
        b = random.randint(2, a - 1)
        return f"What is {a} - {b}?", a - b
    a, b = random.randint(2, 9), random.randint(2, 9)
    return f"What is {a} x {b}?", a * b


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)  # Pillow >= 10.1 bundles a scalable font


def render_captcha(code: str) -> io.BytesIO:
    """Draw the code with random rotation, offsets, lines and dots."""
    width, height = 240, 90
    img = Image.new("RGB", (width, height), (240, 242, 245))
    draw = ImageDraw.Draw(img)

    for _ in range(7):  # background lines
        draw.line(
            [(random.randint(0, width), random.randint(0, height)), (random.randint(0, width), random.randint(0, height))],
            fill=tuple(random.randint(150, 215) for _ in range(3)),
            width=random.randint(1, 3),
        )

    font = _font(46)
    x = 14
    for ch in code:
        tile = Image.new("RGBA", (64, 74), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((10, 6), ch, font=font, fill=tuple(random.randint(10, 110) for _ in range(3)) + (255,))
        tile = tile.rotate(random.randint(-28, 28), expand=True, resample=Image.BICUBIC)
        img.paste(tile, (x, random.randint(2, 16)), tile)
        x += 42

    for _ in range(220):  # noise dots
        draw.point((random.randint(0, width - 1), random.randint(0, height - 1)), fill=tuple(random.randint(80, 200) for _ in range(3)))
    for _ in range(3):  # lines over the text
        draw.line(
            [(0, random.randint(10, height - 10)), (width, random.randint(10, height - 10))],
            fill=tuple(random.randint(60, 140) for _ in range(3)),
            width=2,
        )

    img = img.filter(ImageFilter.SMOOTH)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
