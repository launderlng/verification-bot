"""/gifcreator: turn a picture OR a short video clip into a looping GIF.

Pictures get an animation effect (zoom, spin, wobble, rainbow ...). Videos are cut to the part you pick
(start + length, up to 10 s), resized, sped up or slowed down, and can loop normally, boomerang or play
backwards. Both can have a caption. The result is shown privately first, with buttons to tweak or post it.
Video needs ffmpeg: the `imageio-ffmpeg` package in requirements.txt ships one, so nothing else to install.
"""
import asyncio
import colorsys
import io
import logging
import math
import os
import re
import shutil
import tempfile
import time
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

import ui
from common import SUCCESS, UserError
from fileutil import check_public_url

log = logging.getLogger("verification-bot")

EFFECTS = {
    "zoom": "Zoom in and out", "pulse": "Pulse", "spin": "Spin", "wobble": "Wobble", "shake": "Shake",
    "bounce": "Bounce", "slide": "Slide across", "fade": "Fade", "rainbow": "Rainbow colours", "still": "No effect",
}
LOOPS = {"normal": "Normal loop", "boomerang": "Boomerang (forward then back)", "reverse": "Backwards"}
FRAMES = 30                        # picture animations: more frames = smoother
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_CLIP_SECONDS = 10
MAX_VIDEO_FRAMES = 150
MAX_GIF_BYTES = 8 * 1024 * 1024   # stays under Discord's smallest upload limit
COOLDOWN = 8                      # seconds between uses per person
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi")


# ---------------------------------------------------------------- helpers ----

def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def add_caption(frame: Image.Image, caption: Optional[str]) -> Image.Image:
    """White bold text on a soft dark strip along the bottom, sized to the frame."""
    if not caption:
        return frame
    frame = frame.convert("RGBA")
    w, h = frame.size
    font = _font(max(12, min(w, h) // 12))
    text = caption[:40]
    draw = ImageDraw.Draw(frame)
    tw = draw.textlength(text, font=font)
    while tw > w * 0.92 and font.size > 10:
        font = _font(font.size - 2)
        tw = draw.textlength(text, font=font)
    strip = int(font.size * 1.7)
    overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle((0, h - strip, w, h), fill=(0, 0, 0, 150))
    frame = Image.alpha_composite(frame, overlay)
    ImageDraw.Draw(frame).text(((w - tw) / 2, h - strip + (strip - font.size) / 2 - 1), text, font=font,
                               fill=(255, 255, 255, 255), stroke_width=max(1, font.size // 14), stroke_fill=(0, 0, 0, 255))
    return frame


def encode_gif(frames: list, duration_ms: int) -> bytes:
    """Frames -> GIF. One shared palette (taken from a few sample frames) keeps colours steady instead of flickering."""
    rgb = [f.convert("RGB") for f in frames]
    picks = rgb[:: max(1, len(rgb) // 6)][:6]
    sheet = Image.new("RGB", (rgb[0].width, rgb[0].height * len(picks)))
    for i, f in enumerate(picks):
        sheet.paste(f, (0, i * rgb[0].height))
    palette = sheet.quantize(colors=255, method=Image.MEDIANCUT)
    out_frames = [f.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG) for f in rgb]
    out = io.BytesIO()
    out_frames[0].save(out, format="GIF", save_all=True, append_images=out_frames[1:], duration=max(20, duration_ms), loop=0, optimize=False, disposal=1)
    return out.getvalue()


# ------------------------------------------------------------- picture GIFs ----

def frame_for(base: Image.Image, effect: str, t: float, size: int) -> Image.Image:
    """One frame of the animation. `t` runs from 0 to 1 and the loop is seamless."""
    angle = 2 * math.pi * t
    canvas = Image.new("RGBA", (size, size), (17, 17, 17, 255))
    img, dx, dy = base, 0, 0
    if effect == "zoom":
        factor = 1 + 0.25 * math.sin(angle)
        img = base.resize((max(8, int(size * factor)),) * 2, Image.LANCZOS)
    elif effect == "pulse":
        factor = 1 + 0.12 * abs(math.sin(2 * angle))
        img = base.resize((max(8, int(size * factor)),) * 2, Image.LANCZOS)
    elif effect == "spin":
        img = base.rotate(360 * t, resample=Image.BICUBIC, expand=False)
    elif effect == "wobble":
        img = base.rotate(12 * math.sin(angle), resample=Image.BICUBIC, expand=False)
    elif effect == "shake":
        dx = int(size * 0.04 * math.sin(4 * angle))
    elif effect == "bounce":
        dy = -int(abs(math.sin(angle)) * size * 0.15)
    elif effect == "slide":  # glides across and wraps round seamlessly
        off = int(size * t)
        canvas.paste(base, (off, 0), base)
        canvas.paste(base, (off - size, 0), base)
        return canvas
    elif effect == "fade":
        alpha = 0.3 + 0.7 * abs(math.sin(angle / 2))
        img = base.copy()
        img.putalpha(img.getchannel("A").point(lambda a: int(a * alpha)))
    elif effect == "rainbow":
        img = hue_shift(base, t)
    x, y = (size - img.width) // 2 + dx, (size - img.height) // 2 + dy
    if x >= 0 and y >= 0:
        canvas.alpha_composite(img, (x, y))
    else:
        canvas.paste(img, (x, y), img)
    return canvas


def hue_shift(img: Image.Image, t: float) -> Image.Image:
    alpha = img.getchannel("A")
    h, s, v = img.convert("RGB").convert("HSV").split()
    h = h.point(lambda p: (p + int(255 * t)) % 256)
    out = Image.merge("HSV", (h, s, v)).convert("RGBA")
    out.putalpha(alpha)
    return out


def make_gif(image_bytes: bytes, effect: str = "zoom", speed: int = 5, size: int = 320, caption: Optional[str] = None) -> bytes:
    """Turn one picture into a looping animated GIF."""
    if effect not in EFFECTS:
        raise UserError("Pick one of the effects: " + ", ".join(EFFECTS))
    size = max(128, min(512, size))
    speed = max(1, min(10, speed))
    try:
        source = Image.open(io.BytesIO(image_bytes))
        source.seek(0)
        source = source.convert("RGBA")
    except Exception:
        raise UserError("I couldn't read that picture. Use a png, jpg, webp or gif.") from None
    duration = max(20, 110 - speed * 9)
    while True:
        fit = min(size / source.width, size / source.height)
        base = source.resize((max(8, int(source.width * fit)), max(8, int(source.height * fit))), Image.LANCZOS)
        square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        square.paste(base, ((size - base.width) // 2, (size - base.height) // 2))
        count = 1 if effect == "still" else FRAMES
        frames = [add_caption(frame_for(square, effect, i / count, size), caption) for i in range(count)]
        data = encode_gif(frames, duration)
        if len(data) <= MAX_GIF_BYTES or size <= 128:
            return data
        size = int(size * 0.8)


# --------------------------------------------------------------- video GIFs ----

def ffmpeg_path() -> Optional[str]:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # ships its own ffmpeg binary
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def looks_like_video(filename: str, content_type: Optional[str]) -> bool:
    return (content_type or "").startswith("video/") or filename.lower().endswith(VIDEO_EXTS)


async def run_ffmpeg(args: list, timeout: int = 60) -> tuple[int, bytes]:
    exe = ffmpeg_path()
    if not exe:
        raise UserError("Video GIFs aren't available yet: the bot is missing ffmpeg. Ask the owner to redeploy after updating requirements.txt.")
    proc = await asyncio.create_subprocess_exec(exe, "-hide_banner", "-nostdin", *args,
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise UserError("That video took too long to process. Try a shorter clip.") from None
    return proc.returncode, err


async def video_length(path: str) -> Optional[float]:
    _, err = await run_ffmpeg(["-i", path], timeout=20)
    m = re.search(rb"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


class VideoSettings:
    def __init__(self, start: float = 0.0, length: float = 5.0, speed: float = 1.0, width: int = 360, loop: str = "normal"):
        self.start, self.length, self.speed, self.width, self.loop = start, length, speed, width, loop


async def make_video_gif(video: bytes, vs: VideoSettings, caption: Optional[str]) -> tuple[bytes, float]:
    """Cut the clip, pull frames with ffmpeg, then caption + encode with Pillow. Shrinks until it fits.
    Returns (gif bytes, seconds of video in it)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in.video")
        with open(src, "wb") as fh:
            fh.write(video)
        total = await video_length(src)
        if total is None:
            raise UserError("I couldn't read that video. Use an mp4, mov or webm.")
        start = max(0.0, min(vs.start, max(0.0, total - 0.2)))
        length = max(0.5, min(vs.length, MAX_CLIP_SECONDS, total - start))
        width, fps = vs.width, 15
        for _attempt in range(5):
            for f in os.listdir(tmp):
                if f.endswith(".png"):
                    os.remove(os.path.join(tmp, f))
            # take fps*speed frames per second of SOURCE video; each is shown 1/fps s, so speed changes playback
            take = fps * vs.speed
            count = min(MAX_VIDEO_FRAMES, max(2, int(length * take)))
            code, err = await run_ffmpeg([
                "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", src,
                "-vf", f"fps={take:.3f},scale={width}:-2:flags=lanczos", "-frames:v", str(count),
                os.path.join(tmp, "f%04d.png"),
            ])
            names = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
            if code != 0 or not names:
                log.warning("ffmpeg failed: %s", err[-400:])
                raise UserError("I couldn't turn that video into frames. Try a different clip (mp4 works best).")
            frames = []
            for n in names:
                with Image.open(os.path.join(tmp, n)) as im:
                    frames.append(add_caption(im.convert("RGBA"), caption))
            if vs.loop == "reverse":
                frames.reverse()
            elif vs.loop == "boomerang":
                frames = frames + frames[-2:0:-1]
            data = await asyncio.to_thread(encode_gif, frames, round(1000 / fps))
            if len(data) <= MAX_GIF_BYTES:
                return data, length
            # too big: smaller first, then fewer frames per second
            width = max(160, int(width * 0.8))
            fps = max(8, fps - 3)
        raise UserError("Even shrunk down, that clip makes a GIF over 8 MB. Pick a shorter part (lower `length`).")


# ----------------------------------------------------------------- the UI ----

class Settings:
    def __init__(self, media: bytes, kind: str = "image", effect="zoom", speed=5, size=320, caption=None, video: Optional[VideoSettings] = None):
        self.media, self.kind, self.effect, self.speed, self.size, self.caption = media, kind, effect, speed, size, caption
        self.video = video or VideoSettings()


def _num(text: str, what: str, low: float, high: float) -> float:
    try:
        value = float((text or "").strip().replace("s", "").replace("x", ""))
    except ValueError:
        raise UserError(f"{what} has to be a number.") from None
    if not low <= value <= high:
        raise UserError(f"{what} has to be between {low:g} and {high:g}.")
    return value


class ImageModal(discord.ui.Modal):
    def __init__(self, cog: "GifCreator", current: Settings):
        super().__init__(title="Edit your GIF")
        self.cog, self.current = cog, current
        self.speed_in = discord.ui.TextInput(label="Speed, 1 (slow) to 10 (fast)", max_length=2, default=str(current.speed))
        self.size_in = discord.ui.TextInput(label="Size in pixels, 128 to 512", max_length=3, default=str(current.size))
        self.caption_in = discord.ui.TextInput(label="Caption (optional)", required=False, max_length=40, default=current.caption or None)
        for item in (self.speed_in, self.size_in, self.caption_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.current.speed = int(_num(self.speed_in.value, "Speed", 1, 10))
            self.current.size = int(_num(self.size_in.value, "Size", 128, 512))
            self.current.caption = (self.caption_in.value or "").strip() or None
        except UserError as e:
            return await interaction.response.send_message(str(e), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self.cog.render(interaction, self.current, edit=True)


class VideoModal(discord.ui.Modal):
    def __init__(self, cog: "GifCreator", current: Settings):
        super().__init__(title="Edit your video GIF")
        self.cog, self.current = cog, current
        v = current.video
        self.start_in = discord.ui.TextInput(label="Start at (seconds into the video)", max_length=6, default=f"{v.start:g}")
        self.length_in = discord.ui.TextInput(label=f"Length in seconds (max {MAX_CLIP_SECONDS})", max_length=4, default=f"{v.length:g}")
        self.speed_in = discord.ui.TextInput(label="Speed: 0.5 = slow-mo, 1 = normal, 2 = fast", max_length=4, default=f"{v.speed:g}")
        self.width_in = discord.ui.TextInput(label="Width in pixels, 160 to 480", max_length=3, default=str(v.width))
        self.caption_in = discord.ui.TextInput(label="Caption (optional)", required=False, max_length=40, default=current.caption or None)
        for item in (self.start_in, self.length_in, self.speed_in, self.width_in, self.caption_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        v = self.current.video
        try:
            v.start = _num(self.start_in.value, "Start", 0, 36000)
            v.length = _num(self.length_in.value, "Length", 0.5, MAX_CLIP_SECONDS)
            v.speed = _num(self.speed_in.value, "Speed", 0.25, 4)
            v.width = int(_num(self.width_in.value, "Width", 160, 480))
            self.current.caption = (self.caption_in.value or "").strip() or None
        except UserError as e:
            return await interaction.response.send_message(str(e), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self.cog.render(interaction, self.current, edit=True)


class ResultView(discord.ui.View):
    """Under the finished GIF: pick an effect / loop style, edit the settings, or post it."""

    def __init__(self, cog: "GifCreator", settings: Settings, user_id: int):
        super().__init__(timeout=900)
        self.cog, self.settings, self.user_id = cog, settings, user_id
        if settings.kind == "video":
            options = [discord.SelectOption(label=label, value=key, default=key == settings.video.loop) for key, label in LOOPS.items()]
            placeholder = "Loop style…"
        else:
            options = [discord.SelectOption(label=label, value=key, default=key == settings.effect) for key, label in EFFECTS.items()]
            placeholder = "Try another effect…"
        self.choice = discord.ui.Select(placeholder=placeholder, options=options, row=0)
        self.choice.callback = self.change_choice
        self.add_item(self.choice)
        self.last_data: Optional[bytes] = None

    def mine(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def change_choice(self, interaction: discord.Interaction):
        if not self.mine(interaction):
            return await interaction.response.send_message("This is someone else's GIF.", ephemeral=True)
        if self.settings.kind == "video":
            self.settings.video.loop = self.choice.values[0]
        else:
            self.settings.effect = self.choice.values[0]
        await interaction.response.defer(ephemeral=True)
        await self.cog.render(interaction, self.settings, edit=True)

    @discord.ui.button(label="Edit settings", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.mine(interaction):
            return await interaction.response.send_message("This is someone else's GIF.", ephemeral=True)
        modal = VideoModal if self.settings.kind == "video" else ImageModal
        await interaction.response.send_modal(modal(self.cog, self.settings))

    @discord.ui.button(label="Post it here", emoji="📢", style=discord.ButtonStyle.success, row=1)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.mine(interaction):
            return await interaction.response.send_message("This is someone else's GIF.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        data = self.last_data or (await self.cog.build(self.settings))[0]
        await interaction.channel.send(content=f"🎞️ GIF by {interaction.user.mention}", file=discord.File(io.BytesIO(data), filename="creation.gif"),
                                       allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(embed=ui.card("✅ Posted", "Your GIF is in the channel.", color=SUCCESS), ephemeral=True)


class GifCreator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_used: dict[int, float] = {}
        self.busy: set[int] = set()

    async def fetch_image(self, url: str) -> bytes:
        try:
            url = await asyncio.to_thread(check_public_url, url)
        except ValueError as e:
            raise UserError(str(e)) from None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.get(url, allow_redirects=False) as resp:
                    if resp.status != 200 or not resp.headers.get("Content-Type", "").lower().startswith("image/"):
                        raise UserError("That link isn't a direct picture. Open it in a browser: it should show only the image.")
                    data = await resp.content.read(MAX_IMAGE_BYTES + 1)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise UserError("I couldn't download that picture. Try another link, or upload it instead.") from None
        if len(data) > MAX_IMAGE_BYTES:
            raise UserError("That picture is over 8 MB. Use a smaller one.")
        return data

    async def build(self, s: Settings) -> tuple[bytes, str]:
        """Returns (gif, one-line summary of the settings)."""
        if s.kind == "video":
            data, seconds = await make_video_gif(s.media, s.video, s.caption)
            v = s.video
            summary = ui.kv(("🎬 Clip", f"{v.start:g}s → {v.start + seconds:g}s"), ("⚡ Speed", f"{v.speed:g}×"), ("📐 Width", f"{v.width}px"),
                            ("🔁 Loop", LOOPS[v.loop]), ("💬 Caption", s.caption))
        else:
            data = await asyncio.to_thread(make_gif, s.media, s.effect, s.speed, s.size, s.caption)
            summary = ui.kv(("✨ Effect", EFFECTS[s.effect]), ("⚡ Speed", f"{s.speed}/10"), ("📐 Size", f"{s.size}px"), ("💬 Caption", s.caption))
        return data, summary

    async def render(self, interaction: discord.Interaction, settings: Settings, edit: bool = False) -> None:
        if interaction.user.id in self.busy:
            return await interaction.followup.send("Still working on your last one, hang on a sec.", ephemeral=True)
        self.busy.add(interaction.user.id)
        try:
            data, summary = await self.build(settings)
        except UserError as e:
            return await interaction.followup.send(f"⚠️ {e}", ephemeral=True)
        finally:
            self.busy.discard(interaction.user.id)
        embed = ui.card(
            "🎞️ Your GIF", f"{summary}\n\n-# {len(data) / 1024 / 1024:.1f} MB · change it below, or post it in the channel.",
            image="attachment://creation.gif", section="GIF creator",
        )
        view = ResultView(self, settings, interaction.user.id)
        view.last_data = data
        file = discord.File(io.BytesIO(data), filename="creation.gif")
        if edit:
            await interaction.edit_original_response(embed=embed, attachments=[file], view=view)
        else:
            await interaction.followup.send(embed=embed, file=file, view=view, ephemeral=True)

    @app_commands.command(description="Turn a picture or a short video into a GIF")
    @app_commands.describe(
        file="A picture (png, jpg, webp, gif) or a short video (mp4, mov, webm)",
        link="Or a direct link to a picture",
        effect="Pictures: the animation (you can change it after)",
        caption="Text along the bottom (optional)",
        speed="Pictures: 1 (slow) to 10 (fast). Videos: 1-10 maps to 0.5×-2× speed",
        size="Pixels: pictures 128-512, videos 160-480 wide",
        start="Videos: start this many seconds in",
        length=f"Videos: how many seconds to use (max {MAX_CLIP_SECONDS})",
        loop="Videos: normal, boomerang or backwards",
    )
    @app_commands.choices(
        effect=[app_commands.Choice(name=label, value=key) for key, label in EFFECTS.items()],
        loop=[app_commands.Choice(name=label, value=key) for key, label in LOOPS.items()],
    )
    async def gifcreator(
        self, interaction: discord.Interaction,
        file: Optional[discord.Attachment] = None, link: Optional[str] = None,
        effect: Optional[app_commands.Choice[str]] = None, caption: Optional[app_commands.Range[str, 1, 40]] = None,
        speed: Optional[app_commands.Range[int, 1, 10]] = None, size: Optional[app_commands.Range[int, 128, 512]] = None,
        start: Optional[app_commands.Range[float, 0, 36000]] = None, length: Optional[app_commands.Range[float, 0.5, MAX_CLIP_SECONDS]] = None,
        loop: Optional[app_commands.Choice[str]] = None,
    ):
        wait = COOLDOWN - (time.monotonic() - self.last_used.get(interaction.user.id, -1e9))
        if wait > 0:
            raise UserError(f"Easy there. Try again in {int(wait) + 1} seconds.")
        if file is None and not link:
            raise UserError("Upload a picture or video in `file`, or paste a picture link in `link`.")
        if file is not None and looks_like_video(file.filename, file.content_type):
            if file.size > MAX_VIDEO_BYTES:
                raise UserError(f"That video is over {MAX_VIDEO_BYTES // 1024 // 1024} MB. Trim it first, or use a shorter clip.")
            kind = "video"
        elif file is not None:
            if not (file.content_type or "").startswith("image/"):
                raise UserError("That file isn't a picture or a video. Use png, jpg, webp, gif, mp4, mov or webm.")
            if file.size > MAX_IMAGE_BYTES:
                raise UserError("That picture is over 8 MB. Use a smaller one.")
            kind = "image"
        else:
            kind = "image"
        self.last_used[interaction.user.id] = time.monotonic()
        await interaction.response.defer(ephemeral=True, thinking=True)
        media = await file.read() if file is not None else await self.fetch_image(link.strip())
        video = VideoSettings(
            start=start or 0.0, length=length or 5.0,
            speed=round(0.5 + (speed - 1) * (1.5 / 9), 2) if speed else 1.0,  # 1 -> 0.5x, 4 -> 1x, 10 -> 2x
            width=max(160, min(480, size or 360)), loop=loop.value if loop else "normal",
        )
        settings = Settings(media, kind, effect.value if effect else "zoom", speed or 5, size or 320, caption, video)
        await self.render(interaction, settings)


async def setup(bot: commands.Bot):
    await bot.add_cog(GifCreator(bot))
