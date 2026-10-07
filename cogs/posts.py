import io
import logging
import os
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from cogs.files import GetFileButton, file_autocomplete
from common import COLOR, UserError, check_can_send, parse_color

log = logging.getLogger("verification-bot")


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def https_url(url: str, what: str) -> str:
    url = url.strip()
    if not url.startswith("https://") or " " in url or len(url) > 512:
        raise UserError(f"The {what} link must start with `https://` and be under 512 characters.")
    return url


def unique_name(wanted: str, taken: set) -> str:
    name = wanted
    while name in taken:
        name = f"file_{name}"
    taken.add(name)
    return name


class Draft:
    """Everything needed to build the post. Files are kept as bytes so they can be sent more than once."""

    def __init__(self, channel: discord.TextChannel, title: str, description: Optional[str], footer: Optional[str], color: Optional[int],
                 photo=None, photo_url=None, gif=None, gif_url=None, file=None, button=None, deliver=None):
        self.channel, self.title, self.description, self.footer, self.color = channel, title, description, footer, color
        self.photo, self.photo_url, self.gif, self.gif_url, self.file, self.button = photo, photo_url, gif, gif_url, file, button
        self.deliver = deliver  # (stored file id, name): a 📥 Get file button that DMs the file

    def files(self) -> list[discord.File]:
        return [discord.File(io.BytesIO(item[1]), filename=item[0]) for item in (self.photo, self.gif, self.file) if item]

    def embeds(self) -> list[discord.Embed]:
        color = self.color if self.color is not None else COLOR
        main = ui.card(self.title, self.description, color=color, footer=self.footer)
        main_image = f"attachment://{self.photo[0]}" if self.photo else self.photo_url
        if main_image:
            main.set_image(url=main_image)  # the big photo
        if self.file:
            main.add_field(name="📎 File", value=f"`{self.file[0]}` · {human_size(len(self.file[1]))}\nAttached to this post ⬇️", inline=False)
        embeds = [main]
        gif_image = f"attachment://{self.gif[0]}" if self.gif else self.gif_url
        if gif_image:
            embeds.append(ui.card(None, None, color=color, image=gif_image))  # the GIF gets its own spot under the card
        return embeds

    def view(self) -> Optional[discord.ui.View]:
        if not self.button and not self.deliver:
            return None
        view = discord.ui.View(timeout=None)
        if self.deliver:
            view.add_item(GetFileButton(self.deliver[0]))
        if self.button:
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=self.button[0], url=self.button[1]))
        return view

    def kwargs(self) -> dict:
        kw = {"embeds": self.embeds(), "allowed_mentions": discord.AllowedMentions.none()}
        files = self.files()
        if files:
            kw["files"] = files
        view = self.view()
        if view is not None:
            kw["view"] = view
        return kw


class ConfirmView(discord.ui.View):
    def __init__(self, user_id: int, draft: Draft):
        super().__init__(timeout=300)
        self.user_id, self.draft = user_id, draft

    @discord.ui.button(label="Post it", emoji="📨", style=discord.ButtonStyle.success)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who made this post can confirm it.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content="⏳ Posting…", embeds=[], attachments=[], view=None)
        try:
            message = await self.draft.channel.send(**self.draft.kwargs())
        except discord.HTTPException as e:
            return await interaction.edit_original_response(content=f"I couldn't post that: {e}. Check my permissions and the file sizes.")
        await interaction.edit_original_response(content=f"✅ Posted in {self.draft.channel.mention}: {message.jump_url}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who made this post can cancel it.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was posted.", embeds=[], attachments=[], view=None)


class PostModal(discord.ui.Modal):
    def __init__(self, cog: "Posts", channel, color, photo, photo_url, gif, gif_url, file, deliver=None):
        super().__init__(title="Write your post")
        self.cog, self.channel, self.color, self.deliver = cog, channel, color, deliver
        self.photo, self.photo_url, self.gif, self.gif_url, self.file = photo, photo_url, gif, gif_url, file
        self.title_input = discord.ui.TextInput(label="Title", max_length=256, placeholder="Headline of the card")
        self.text_input = discord.ui.TextInput(label="Text", style=discord.TextStyle.paragraph, required=False, max_length=3500, placeholder="Write as much as you like. Multiple lines are fine.")
        self.footer_input = discord.ui.TextInput(label="Footer (small text at the bottom)", required=False, max_length=200)
        self.button_label = discord.ui.TextInput(label="Button text (optional)", required=False, max_length=30, placeholder="e.g. Download, Join, Read more")
        self.button_url = discord.ui.TextInput(label="Button link (optional, https://…)", required=False, max_length=512)
        for item in (self.title_input, self.text_input, self.footer_input, self.button_label, self.button_url):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.cog.preview(interaction, self)
        except UserError as e:
            if interaction.response.is_done():
                await interaction.followup.send(str(e), ephemeral=True)
            else:
                await interaction.response.send_message(str(e), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Post modal error", exc_info=error)


@app_commands.guild_only()
class Posts(commands.Cog):
    """/post: build a card with a big photo, a GIF in its own spot and a file attached at the bottom."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def read(self, attachment: discord.Attachment, limit: int) -> bytes:
        if attachment.size > limit:
            raise UserError(f"**{attachment.filename}** is {human_size(attachment.size)}. This server's upload limit is {human_size(limit)}.")
        return await attachment.read()

    async def preview(self, interaction: discord.Interaction, m: PostModal) -> None:
        guild = interaction.guild
        label, link = m.button_label.value.strip(), m.button_url.value.strip()
        if bool(label) != bool(link):
            raise UserError("A button needs both a text and a link. Fill in both or leave both empty.")
        button = (label, https_url(link, "button")) if label else None
        limit = guild.filesize_limit
        await interaction.response.defer(ephemeral=True)

        taken: set = set()
        photo = gif = file = None
        if m.photo:
            ext = os.path.splitext(m.photo.filename)[1].lower() or ".png"
            photo = (unique_name(f"photo{ext}", taken), await self.read(m.photo, limit))
        if m.gif:
            gif = (unique_name("animation.gif", taken), await self.read(m.gif, limit))
        if m.file:
            file = (unique_name(m.file.filename, taken), await self.read(m.file, limit))
        total = sum(len(item[1]) for item in (photo, gif, file) if item)
        if total > limit:
            raise UserError(f"Together the files are {human_size(total)}, over this server's {human_size(limit)} limit. Use smaller files.")

        draft = Draft(m.channel, m.title_input.value.strip(), m.text_input.value.strip() or None, m.footer_input.value.strip() or None, m.color,
                      photo=photo, photo_url=m.photo_url if not photo else None, gif=gif, gif_url=m.gif_url if not gif else None, file=file, button=button, deliver=m.deliver)
        note = f"**Preview.** This is exactly what will be posted in {m.channel.mention}:"
        if m.deliver:
            note += f"\n📥 It will have a **Get file** button that sends **{m.deliver[1]}** to members by DM."
        if button:
            note += f"\n🔗 It will have a **{button[0]}** button linking to {button[1]}"
        await interaction.followup.send(
            content=note, ephemeral=True, view=ConfirmView(interaction.user.id, draft), embeds=draft.embeds(), files=draft.files(),
        )

    @app_commands.command(name="post", description="Post a card with a big photo, a GIF and a file at the bottom")
    @app_commands.describe(
        channel="Where to post it",
        photo="The big photo shown in the card",
        gif="An animated GIF, shown in its own card under the photo",
        file="A file attached at the bottom (zip, pdf, anything)",
        photo_url="Or a direct https link to the photo",
        gif_url="Or a direct https link to a GIF (must end in .gif)",
        color="Card colour as hex, e.g. #5865F2 (default: black)",
        library_file="A stored file (see /files add) that a 📥 Get file button sends to members by DM",
    )
    @app_commands.autocomplete(library_file=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def post(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        photo: Optional[discord.Attachment] = None,
        gif: Optional[discord.Attachment] = None,
        file: Optional[discord.Attachment] = None,
        photo_url: Optional[str] = None,
        gif_url: Optional[str] = None,
        color: Optional[str] = None,
        library_file: Optional[str] = None,
    ):
        check_can_send(channel, interaction.guild.me, files=bool(photo or gif or file))
        deliver = None
        if library_file:
            row = await db.fetch_one("SELECT id, name FROM stored_files WHERE guild_id = ? AND name = ?", (interaction.guild_id, library_file.strip()))
            if not row:
                raise UserError(f"I can't find a stored file called **{library_file}**. Add one with `/files add`, then pick it from the list.")
            deliver = (row["id"], row["name"])
        if photo is not None and not (photo.content_type or "").startswith("image/"):
            raise UserError("The photo has to be an image (png, jpg, webp or gif).")
        if gif is not None and not ((gif.content_type or "") == "image/gif" or gif.filename.lower().endswith(".gif")):
            raise UserError("That isn't a GIF. Put a `.gif` file in `gif`, or use `photo` for normal images.")
        if photo_url:
            photo_url = https_url(photo_url, "photo")
        if gif_url:
            gif_url = https_url(gif_url, "GIF")
        value = parse_color(color) if color else None
        await interaction.response.send_modal(PostModal(self, channel, value, photo, photo_url, gif, gif_url, file, deliver))


async def setup(bot: commands.Bot):
    await bot.add_cog(Posts(bot))
