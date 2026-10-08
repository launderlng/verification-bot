import asyncio
import io
import logging
import math
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

EFFECTS = {"zoom": "Zoom in and out", "spin": "Spin", "shake": "Shake", "pulse": "Pulse", "fade": "Fade", "bounce": "Bounce"}
FRAMES = 24
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_GIF_BYTES = 8 * 1024 * 1024
COOLDOWN = 8  # seconds between uses per person


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


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
    elif effect == "shake":
        dx = int(size * 0.04 * math.sin(4 * angle))
    elif effect == "bounce":
        dy = -int(abs(math.sin(angle)) * size * 0.15)
    elif effect == "fade":
        alpha = 0.3 + 0.7 * abs(math.sin(angle / 2))
        img = base.copy()
        img.putalpha(img.getchannel("A").point(lambda a: int(a * alpha)))
    x, y = (size - img.width) // 2 + dx, (size - img.height) // 2 + dy
    canvas.alpha_composite(img, (max(0, x), max(0, y))) if x >= 0 and y >= 0 else canvas.paste(img, (x, y), img)
    return canvas


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
    fit = min(size / source.width, size / source.height)
    base = source.resize((max(8, int(source.width * fit)), max(8, int(source.height * fit))), Image.LANCZOS)
    square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    square.paste(base, ((size - base.width) // 2, (size - base.height) // 2))
    duration = max(20, 130 - speed * 11)
    while True:
        frames = []
        for i in range(FRAMES):
            frame = frame_for(square, effect, i / FRAMES, size)
            if caption:
                draw = ImageDraw.Draw(frame)
                font = _font(max(14, size // 14))
                text = caption[:40]
                w = draw.textlength(text, font=font)
                draw.rectangle((0, size - size // 7, size, size), fill=(0, 0, 0, 170))
                draw.text(((size - w) / 2, size - size // 7 + size // 40), text, font=font, fill=(255, 255, 255, 255))
            frames.append(frame.convert("RGB").quantize(colors=128, method=Image.MEDIANCUT))
        out = io.BytesIO()
        frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=duration, loop=0, optimize=False, disposal=2)
        if out.tell() <= MAX_GIF_BYTES or size <= 128:
            return out.getvalue()
        size = int(size * 0.75)
        base = base.resize((max(8, int(base.width * 0.75)), max(8, int(base.height * 0.75))), Image.LANCZOS)
        square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        square.paste(base, ((size - base.width) // 2, (size - base.height) // 2))


class Settings:
    def __init__(self, image: bytes, effect="zoom", speed=5, size=320, caption=None):
        self.image, self.effect, self.speed, self.size, self.caption = image, effect, speed, size, caption


class GifModal(discord.ui.Modal):
    def __init__(self, cog: "GifCreator", uploaded: Optional[bytes] = None, current: Optional[Settings] = None, source_url: str = ""):
        super().__init__(title="Make a GIF" if current is None else "Edit your GIF")
        self.cog, self.uploaded, self.current = cog, uploaded, current
        self.url_in = discord.ui.TextInput(label="Image link (not needed if you uploaded one)", required=False, max_length=500, default=source_url or None, placeholder="https://example.com/picture.png")
        self.effect_in = discord.ui.TextInput(label="Effect: zoom, spin, shake, pulse, fade or bounce", max_length=10, default=(current.effect if current else "zoom"))
        self.speed_in = discord.ui.TextInput(label="Speed, 1 (slow) to 10 (fast)", max_length=2, default=str(current.speed if current else 5))
        self.size_in = discord.ui.TextInput(label="Size in pixels, 128 to 512", max_length=3, default=str(current.size if current else 320))
        self.caption_in = discord.ui.TextInput(label="Caption (optional)", required=False, max_length=40, default=(current.caption if current else None) or None)
        for item in (self.url_in, self.effect_in, self.speed_in, self.size_in, self.caption_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            effect = (self.effect_in.value or "").strip().lower()
            if effect not in EFFECTS:
                raise UserError("The effect has to be one of: " + ", ".join(EFFECTS) + ".")
            try:
                speed, size = int((self.speed_in.value or "").strip()), int((self.size_in.value or "").strip())
            except ValueError:
                raise UserError("Speed and size have to be numbers.") from None
            if not 1 <= speed <= 10 or not 128 <= size <= 512:
                raise UserError("Speed is 1 to 10 and size is 128 to 512.")
            await interaction.response.defer(ephemeral=True)
            url = (self.url_in.value or "").strip()
            image = self.uploaded or (self.current.image if self.current and not url else None)
            if image is None:
                if not url:
                    raise UserError("Upload an image when you run the command, or paste an image link.")
                image = await self.cog.fetch_image(url)
            await self.cog.render(interaction, Settings(image, effect, speed, size, (self.caption_in.value or "").strip() or None))
        except UserError as e:
            if interaction.response.is_done():
                await interaction.followup.send(str(e), ephemeral=True)
            else:
                await interaction.response.send_message(str(e), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("GIF creator error", exc_info=error)


class ResultView(discord.ui.View):
    """Under the finished GIF: change the effect, edit the settings, or post it in the channel."""

    def __init__(self, cog: "GifCreator", settings: Settings, user_id: int):
        super().__init__(timeout=900)
        self.cog, self.settings, self.user_id = cog, settings, user_id
        select = discord.ui.Select(placeholder="Try another effect…", options=[discord.SelectOption(label=label, value=key, default=key == settings.effect) for key, label in EFFECTS.items()])
        select.callback = self.change_effect
        self.effect_select = select
        self.add_item(select)

    def mine(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def change_effect(self, interaction: discord.Interaction):
        if not self.mine(interaction):
            return await interaction.response.send_message("This is someone else's GIF.", ephemeral=True)
        self.settings.effect = self.effect_select.values[0]
        await interaction.response.defer(ephemeral=True)
        await self.cog.render(interaction, self.settings, edit=True)

    @discord.ui.button(label="Edit settings", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.mine(interaction):
            return await interaction.response.send_message("This is someone else's GIF.", ephemeral=True)
        await interaction.response.send_modal(GifModal(self.cog, current=self.settings))

    @discord.ui.button(label="Post it here", emoji="📢", style=discord.ButtonStyle.success)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.mine(interaction):
            return await interaction.response.send_message("This is someone else's GIF.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        data = await asyncio.to_thread(make_gif, self.settings.image, self.settings.effect, self.settings.speed, self.settings.size, self.settings.caption)
        await interaction.channel.send(content=f"🎞️ GIF by {interaction.user.mention}", file=discord.File(io.BytesIO(data), filename="creation.gif"), allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(embed=ui.card("✅ Posted", "Your GIF is in the channel.", color=SUCCESS), ephemeral=True)


class GifCreator(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_used: dict[int, float] = {}

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
                    data = await resp.content.read(MAX_INPUT_BYTES + 1)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise UserError("I couldn't download that picture. Try another link, or upload it instead.") from None
        if len(data) > MAX_INPUT_BYTES:
            raise UserError("That picture is over 8 MB. Use a smaller one.")
        return data

    async def render(self, interaction: discord.Interaction, settings: Settings, edit: bool = False) -> None:
        data = await asyncio.to_thread(make_gif, settings.image, settings.effect, settings.speed, settings.size, settings.caption)
        embed = ui.card(
            "🎞️ Your GIF", ui.kv(("✨ Effect", EFFECTS[settings.effect]), ("⚡ Speed", f"{settings.speed}/10"), ("📐 Size", f"{settings.size}px"), ("💬 Caption", settings.caption)) + "\n\nChange the effect below, edit the settings, or post it.",
            image="attachment://creation.gif", section="GIF creator",
        )
        kwargs = {"embed": embed, "attachments": [discord.File(io.BytesIO(data), filename="creation.gif")], "view": ResultView(self, settings, interaction.user.id)}
        if edit:
            await interaction.edit_original_response(**kwargs)
        else:
            await interaction.followup.send(embed=embed, file=discord.File(io.BytesIO(data), filename="creation.gif"), view=kwargs["view"], ephemeral=True)

    @app_commands.command(description="Turn a picture into an animated GIF (a pop-up asks for the details)")
    @app_commands.describe(image="Upload the picture (or paste a link in the pop-up)")
    async def gifcreator(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None):
        wait = COOLDOWN - (time.monotonic() - self.last_used.get(interaction.user.id, -1e9))
        if wait > 0:
            raise UserError(f"Easy there. Try again in {int(wait) + 1} seconds.")
        uploaded = None
        if image is not None:
            if not (image.content_type or "").startswith("image/"):
                raise UserError("That file isn't a picture (png, jpg, webp or gif).")
            if image.size > MAX_INPUT_BYTES:
                raise UserError("That picture is over 8 MB. Use a smaller one.")
            uploaded = await image.read()
        self.last_used[interaction.user.id] = time.monotonic()
        await interaction.response.send_modal(GifModal(self, uploaded=uploaded))


async def setup(bot: commands.Bot):
    await bot.add_cog(GifCreator(bot))
