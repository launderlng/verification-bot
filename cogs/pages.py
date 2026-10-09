import html as html_escape_mod
import io
import json
import logging
import re
import uuid
from typing import Optional

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import COLOR, SUCCESS, WARN, UserError, parse_color
from fileutil import public_base_url
from logutil import emit

log = logging.getLogger("verification-bot")

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_LINKS = 5
KINDS = {
    "rules": {"icon": "📋", "label": "Rules", "default": "Server rules"},
    "boosterperks": {"icon": "⭐", "label": "Booster perks", "default": "Booster perks"},
    "premiumperks": {"icon": "💎", "label": "Premium perks", "default": "Premium perks"},
    "links": {"icon": "🔗", "label": "Links", "default": "Our links"},
    "installguides": {"icon": "📚", "label": "Install guide", "default": "Install guide"},
    "clothingpreviews": {"icon": "👕", "label": "Clothing preview", "default": "Clothing preview"},
}
now = lambda: discord.utils.utcnow().isoformat()


# ---------------------------------------------------------------- helpers ----

def parse_links(text: Optional[str]) -> list[list[str]]:
    """One link per line: `Label | https://…` (or just the link). Up to 5."""
    links: list[list[str]] = []
    for number, line in enumerate((text or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        label, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        label, url = label.strip(), url.strip()
        if not url.startswith("https://") or " " in url or len(url) > 512:
            raise UserError(f"Link line {number} needs an `https://` link, like `Download | https://example.com`.")
        links.append([label[:80] or "Open", url])
    if len(links) > MAX_LINKS:
        raise UserError(f"You can have up to {MAX_LINKS} links on one page.")
    return links


def links_text(row) -> str:
    return "\n".join(f"{label} | {url}" for label, url in json.loads(row["links"] or "[]"))


def page_url(row) -> Optional[str]:
    base = public_base_url()
    return f"{base}/page/{row['id']}" if base else None


def links_view(row) -> Optional[discord.ui.View]:
    links = json.loads(row["links"] or "[]")
    url = page_url(row)
    if not links and not url:
        return None
    view = discord.ui.View(timeout=None)
    for label, link in links:
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=link))
    if url:
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Full page", emoji="🌐", url=url))
    return view


def check_image_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("https://") or " " in url or len(url) > 512:
        raise UserError("The image link must be a direct link starting with `https://`.")
    return url


async def check_attachment(attachment: discord.Attachment) -> None:
    if not (attachment.content_type or "").startswith("image/"):
        raise UserError("That file isn't an image (png, jpg, webp or gif).")
    if attachment.size > MAX_IMAGE_BYTES:
        raise UserError(f"That image is {attachment.size / 1024 / 1024:.1f} MB. Keep it under 8 MB.")


async def store_image(guild_id: int, user_id: int, attachment: discord.Attachment) -> int:
    """Keep an uploaded image in the database so it never expires (Discord's own links do)."""
    data = await attachment.read()
    await db.execute(
        "INSERT INTO stored_files (guild_id, name, filename, content_type, size, data, description, uploaded_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (guild_id, f"page-image-{uuid.uuid4().hex[:12]}", attachment.filename, attachment.content_type, len(data), data, "Image for an editable page", user_id, now()),
    )
    return (await db.fetch_one("SELECT id FROM stored_files WHERE guild_id = ? ORDER BY id DESC LIMIT 1", (guild_id,)))["id"]


async def drop_image(file_id: Optional[int]) -> None:
    if file_id:
        await db.execute("DELETE FROM stored_files WHERE id = ? AND name LIKE 'page-image-%'", (file_id,))


async def load_image(row) -> Optional[tuple[str, bytes]]:
    if not row["image_file_id"]:
        return None
    f = await db.fetch_one("SELECT filename, data FROM stored_files WHERE id = ?", (row["image_file_id"],))
    return (f["filename"], bytes(f["data"])) if f else None


async def render(guild: discord.Guild, row) -> dict:
    """Everything needed to show a page: the embed, its uploaded image (if any) and its link buttons."""
    kind = KINDS[row["kind"]]
    shown = row["title"] or (row["name"] if row["kind"] in ("installguides", "clothingpreviews") else kind["default"])
    body = row["body"] or "*Nothing written here yet. Staff can add it with the edit command.*"
    footer = row["footer"] or " · ".join(x for x in (guild.name, row["section"]) if x)
    embed = ui.card(f"{kind['icon']}  {shown}", body, color=int(row["color"], 16) if row["color"] else COLOR, guild=guild, footer=footer)
    out: dict = {}
    image = await load_image(row)
    if image:
        embed.set_image(url=f"attachment://{image[0]}")
        out["file"] = discord.File(io.BytesIO(image[1]), filename=image[0])
    elif row["image_url"]:
        embed.set_image(url=row["image_url"])
    out["embed"] = embed
    view = links_view(row)
    if view is not None:
        out["view"] = view
    return out


async def publish(guild: discord.Guild, row, channel: discord.TextChannel):
    """Post the page. If it's already posted in that channel the message is edited in place instead of duplicated."""
    existing = await message_of(guild, row)
    if existing is not None and existing.channel.id == channel.id:
        await refresh(guild, row)
        return existing
    parts = await render(guild, row)
    message = await channel.send(**parts, allowed_mentions=discord.AllowedMentions.none())
    await db.execute("UPDATE pages SET channel_id = ?, message_id = ? WHERE id = ?", (channel.id, message.id, row["id"]))
    return message


async def message_of(guild: discord.Guild, row):
    channel = guild.get_channel(row["channel_id"]) if row["channel_id"] else None
    if channel is None or not row["message_id"]:
        return None
    try:
        return await channel.fetch_message(row["message_id"])
    except discord.HTTPException:
        return None


async def refresh(guild: discord.Guild, row) -> bool:
    """Update the posted message after an edit. Returns False if it isn't posted (or was deleted)."""
    message = await message_of(guild, row)
    if message is None:
        return False
    parts = await render(guild, row)
    file = parts.pop("file", None)
    try:
        await message.edit(embed=parts["embed"], attachments=[file] if file else [], view=parts.get("view"))
    except discord.HTTPException:
        return False
    return True


async def get_page(guild_id: int, kind: str, name: Optional[str] = None):
    if name is None:
        return await db.fetch_one("SELECT * FROM pages WHERE guild_id = ? AND kind = ? ORDER BY id LIMIT 1", (guild_id, kind))
    return await db.fetch_one("SELECT * FROM pages WHERE guild_id = ? AND kind = ? AND name = ?", (guild_id, kind, name.strip()))


async def save_page(guild_id: int, user_id: int, kind: str, name: str, fields: dict, section: str = "") -> object:
    existing = await db.fetch_one("SELECT * FROM pages WHERE guild_id = ? AND kind = ? AND name = ?", (guild_id, kind, name))
    if existing is None:
        await db.execute("INSERT INTO pages (guild_id, kind, section, name, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?)", (guild_id, kind, section, name, user_id, now()))
    cols = ", ".join(f"{c} = ?" for c in fields)
    await db.execute(
        f"UPDATE pages SET {cols + ', ' if cols else ''}section = ?, updated_by = ?, updated_at = ? WHERE guild_id = ? AND kind = ? AND name = ?",
        (*fields.values(), section if existing is None else (section or existing["section"]), user_id, now(), guild_id, kind, name),
    )
    return await db.fetch_one("SELECT * FROM pages WHERE guild_id = ? AND kind = ? AND name = ?", (guild_id, kind, name))


class Media:
    """The picture options of an edit command, checked up front so a bad image never wastes the pop-up."""

    def __init__(self, image: Optional[discord.Attachment], image_url: Optional[str], remove: bool = False):
        self.image, self.url, self.remove = image, image_url.strip() if image_url else None, remove

    async def check(self) -> "Media":
        if self.image is not None:
            await check_attachment(self.image)
        if self.url:
            check_image_url(self.url)
        return self

    async def apply(self, guild_id: int, user_id: int, row) -> dict:
        """Returns the column changes for this page."""
        if self.image is not None:
            file_id = await store_image(guild_id, user_id, self.image)
            if row is not None:
                await drop_image(row["image_file_id"])
            return {"image_file_id": file_id, "image_url": None}
        if self.url:
            if row is not None:
                await drop_image(row["image_file_id"])
            return {"image_file_id": None, "image_url": self.url}
        if self.remove:
            if row is not None:
                await drop_image(row["image_file_id"])
            return {"image_file_id": None, "image_url": None}
        return {}


class PageModal(discord.ui.Modal):
    """Title, text, footer and links, prefilled with what's there now."""

    def __init__(self, kind: str, row, name: str, media: Media, color: Optional[str], section: str, after, heading: str):
        super().__init__(title=heading[:45])
        self.kind, self.row, self.name, self.media, self.color, self.section, self.after = kind, row, name, media, color, section, after
        default = KINDS[kind]["default"]
        self.title_in = discord.ui.TextInput(label="Title", max_length=200, required=False, default=(row["title"] if row else None) or None, placeholder=default)
        self.body_in = discord.ui.TextInput(label="Text", style=discord.TextStyle.paragraph, max_length=3900, required=False, default=(row["body"] if row else None) or None,
                                            placeholder="Write it here. Markdown works: **bold**, bullet points, links.")
        self.footer_in = discord.ui.TextInput(label="Footer (small text at the bottom)", max_length=200, required=False, default=(row["footer"] if row else None) or None)
        self.links_in = discord.ui.TextInput(label="Link buttons, one per line (up to 5)", style=discord.TextStyle.paragraph, max_length=500, required=False,
                                             default=links_text(row) if row else None, placeholder="Download | https://example.com/file")
        self.video_in = discord.ui.TextInput(label="Video (YouTube link or direct .mp4, optional)", max_length=300, required=False,
                                             default=(row["video_url"] if row else None) or None, placeholder="https://youtube.com/watch?v=…")
        for item in (self.title_in, self.body_in, self.footer_in, self.links_in, self.video_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            links = parse_links(self.links_in.value)
            await interaction.response.defer(ephemeral=True)
            guild = interaction.guild
            changes = await self.media.apply(guild.id, interaction.user.id, self.row)
            video = (self.video_in.value or "").strip()
            if video and not video.startswith("https://"):
                raise UserError("The video link must start with `https://`.")
            changes.update({"title": (self.title_in.value or "").strip() or None, "body": (self.body_in.value or "").strip() or None,
                            "footer": (self.footer_in.value or "").strip() or None, "links": json.dumps(links) if links else None,
                            "video_url": video or None})
            if self.color:
                changes["color"] = self.color
            row = await save_page(guild.id, interaction.user.id, self.kind, self.name, changes, self.section)
            await self.after(interaction, row)
        except UserError as e:
            if interaction.response.is_done():
                await interaction.followup.send(str(e), ephemeral=True)
            else:
                await interaction.response.send_message(str(e), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Page editor error", exc_info=error)


async def after_edit(interaction: discord.Interaction, row) -> None:
    updated = await refresh(interaction.guild, row)
    kind = KINDS[row["kind"]]
    where = f"\nThe posted message in <#{row['channel_id']}> was updated." if updated else "\nIt isn't posted anywhere yet. Use the **post** command to put it in a channel."
    web_note = "" if public_base_url() else "\n\n⚠️ No public web address is set up, so there's no styled 'Full page' link yet. In Railway: **Settings → Networking → Generate Domain**."
    await interaction.followup.send(embed=ui.card(f"✅ {kind['label']} saved", f"**{row['title'] or row['name']}** is stored in the database.{where}{web_note}", color=SUCCESS), ephemeral=True)
    await emit(interaction.guild, "posts", f"{kind['label']} edited", ui.kv(("📄 Page", row["title"] or row["name"]), ("🛡️ By", interaction.user.mention), ("📍 Posted in", f"<#{row['channel_id']}>" if row["channel_id"] else None)), subject=interaction.user.id)


# ------------------------------------------------- single pages (/rules …) ----

class SinglePageBase:
    """/rules, /boosterperks, /premiumperks and /links all work the same way: view it, post it, edit it."""

    KIND = ""

    @app_commands.command(description="Show it here")
    async def view(self, interaction: discord.Interaction):
        row = await get_page(interaction.guild_id, self.KIND)
        if row is None or not (row["body"] or row["title"]):
            raise UserError("Nothing has been written here yet. Staff can add it with the **edit** command.")
        parts = await render(interaction.guild, row)
        await interaction.response.send_message(ephemeral=True, **parts)

    @app_commands.command(description="Post it in a channel (or update the posted copy)")
    @app_commands.describe(channel="Where to post it")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def post(self, interaction: discord.Interaction, channel: discord.TextChannel):
        row = await get_page(interaction.guild_id, self.KIND)
        if row is None or not (row["body"] or row["title"]):
            raise UserError("Write it first with the **edit** command, then post it.")
        await interaction.response.defer(ephemeral=True)
        message = await publish(interaction.guild, row, channel)
        await interaction.followup.send(embed=ui.card("✅ Posted", f"{KINDS[self.KIND]['label']} is live in {channel.mention}.\n\n[Jump to it]({message.jump_url})", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "posts", f"{KINDS[self.KIND]['label']} posted", ui.kv(("📍 Channel", channel.mention), ("🛡️ By", interaction.user.mention)), subject=interaction.user.id)

    @app_commands.command(description="Edit the text, links and picture")
    @app_commands.describe(
        image="Upload a picture (stored safely in the database)",
        image_url="Or a direct https link to a picture",
        remove_image="Take the picture off",
        color="Colour as hex, e.g. #5865F2",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None, image_url: Optional[str] = None,
                   remove_image: bool = False, color: Optional[str] = None):
        media = await Media(image, image_url, remove_image).check()
        value = f"{parse_color(color):06X}" if color else None
        row = await get_page(interaction.guild_id, self.KIND)
        await interaction.response.send_modal(PageModal(self.KIND, row, self.KIND, media, value, "", after_edit, f"Edit {KINDS[self.KIND]['label'].lower()}"))


@app_commands.guild_only()
class Rules(SinglePageBase, commands.GroupCog, group_name="rules", group_description="The server rules: view, post and edit"):
    KIND = "rules"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()


@app_commands.guild_only()
class BoosterPerks(SinglePageBase, commands.GroupCog, group_name="boosterperks", group_description="Booster perks: view, post and edit"):
    KIND = "boosterperks"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()


@app_commands.guild_only()
class PremiumPerks(SinglePageBase, commands.GroupCog, group_name="premiumperks", group_description="Premium perks: view, post and edit"):
    KIND = "premiumperks"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()


@app_commands.guild_only()
class Links(SinglePageBase, commands.GroupCog, group_name="links", group_description="Your server and partner links: view, post and edit"):
    KIND = "links"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()


# ---------------------------------------------- many pages (guides, previews) ----

async def name_autocomplete(kind: str, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    rows = await db.fetch_all("SELECT name, section FROM pages WHERE guild_id = ? AND kind = ? AND name LIKE ? ORDER BY section, name LIMIT 25", (interaction.guild_id, kind, f"%{current}%"))
    return [app_commands.Choice(name=(f"{r['name']} · {r['section']}" if r["section"] else r["name"])[:100], value=r["name"]) for r in rows]


async def guide_names(interaction: discord.Interaction, current: str):
    return await name_autocomplete("installguides", interaction, current)


async def preview_names(interaction: discord.Interaction, current: str):
    return await name_autocomplete("clothingpreviews", interaction, current)


async def category_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    rows = await db.fetch_all("SELECT DISTINCT section FROM pages WHERE guild_id = ? AND kind = 'installguides' AND section LIKE ? ORDER BY section LIMIT 25", (interaction.guild_id, f"%{current}%"))
    return [app_commands.Choice(name=r["section"], value=r["section"]) for r in rows if r["section"]]


class ManyPagesBase:
    KIND = ""

    async def find(self, interaction: discord.Interaction, name: str):
        row = await get_page(interaction.guild_id, self.KIND, name)
        if row is None:
            raise UserError(f"I can't find **{name}**. Start typing in the box and pick one from the list.")
        return row

    async def remove_page(self, interaction: discord.Interaction, name: str):
        row = await self.find(interaction, name)
        message = await message_of(interaction.guild, row)
        if message is not None:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        await drop_image(row["image_file_id"])
        await db.execute("DELETE FROM pages WHERE id = ?", (row["id"],))
        await interaction.response.send_message(embed=ui.card("🗑️ Removed", f"**{row['name']}** was deleted" + (" and its posted message too." if message else "."), color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "posts", f"{KINDS[self.KIND]['label']} removed", ui.kv(("📄 Page", row["name"]), ("🛡️ By", interaction.user.mention)), subject=interaction.user.id)

    async def post_pages(self, interaction: discord.Interaction, rows: list, channel: discord.TextChannel, intro: Optional[str] = None):
        if not rows:
            raise UserError("There's nothing to post yet. Add some first.")
        await interaction.response.defer(ephemeral=True)
        if intro:
            await channel.send(embed=ui.card(intro, None, guild=interaction.guild), allowed_mentions=discord.AllowedMentions.none())
        for row in rows:
            await publish(interaction.guild, row, channel)
        await interaction.followup.send(embed=ui.card("✅ Posted", f"{len(rows)} post(s) are live in {channel.mention}. Editing one later updates it in place.", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "posts", f"{KINDS[self.KIND]['label']}s posted", ui.kv(("📍 Channel", channel.mention), ("📄 Posts", str(len(rows))), ("🛡️ By", interaction.user.mention)), subject=interaction.user.id)


class GuideBrowser(discord.ui.View):
    """Members pick a category, then a guide. Everything updates in place."""

    def __init__(self, guild: discord.Guild, sections: list[str]):
        super().__init__(timeout=300)
        self.guild = guild
        select = discord.ui.Select(placeholder="Pick a category…", options=[discord.SelectOption(label=s[:100], value=s[:100]) for s in sections[:25]])
        select.callback = self.pick_category
        self.category = select
        self.add_item(select)

    async def pick_category(self, interaction: discord.Interaction):
        section = self.category.values[0]
        rows = await db.fetch_all("SELECT * FROM pages WHERE guild_id = ? AND kind = 'installguides' AND section = ? ORDER BY name LIMIT 25", (self.guild.id, section))
        view = GuideBrowser(self.guild, [r["section"] for r in await db.fetch_all("SELECT DISTINCT section FROM pages WHERE guild_id = ? AND kind = 'installguides' AND section != '' ORDER BY section", (self.guild.id,))])
        guide = discord.ui.Select(placeholder=f"Pick a guide in {section}…", options=[discord.SelectOption(label=r["name"][:100], value=r["name"][:100]) for r in rows])

        async def pick_guide(inter: discord.Interaction):
            chosen = await get_page(self.guild.id, "installguides", guide.values[0])
            parts = await render(self.guild, chosen)
            await inter.response.edit_message(embed=parts["embed"], view=view_with(parts, view), attachments=[parts["file"]] if "file" in parts else [])

        guide.callback = pick_guide
        view.add_item(guide)
        await interaction.response.edit_message(embed=ui.card(f"📚 {section}", "Pick a guide below.", guild=self.guild, section="Install guides"), view=view)


def view_with(parts: dict, browser: discord.ui.View) -> discord.ui.View:
    """Keep the browser's dropdowns and add the guide's own link buttons under them."""
    extra = parts.get("view")
    if extra is not None:
        for item in extra.children:
            browser.add_item(item)
    return browser


@app_commands.guild_only()
class InstallGuides(ManyPagesBase, commands.GroupCog, group_name="installguides", group_description="Installation guides, sorted into categories"):
    KIND = "installguides"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(description="Browse the installation guides")
    async def browse(self, interaction: discord.Interaction):
        sections = [r["section"] for r in await db.fetch_all("SELECT DISTINCT section FROM pages WHERE guild_id = ? AND kind = 'installguides' AND section != '' ORDER BY section", (interaction.guild_id,))]
        if not sections:
            raise UserError("There are no guides yet. Staff can add one with `/installguides add`.")
        await interaction.response.send_message(embed=ui.card("📚 Installation guides", "Pick a category, then a guide.", guild=interaction.guild, section="Install guides"), view=GuideBrowser(interaction.guild, sections), ephemeral=True)

    @app_commands.command(description="Add an installation guide to a category")
    @app_commands.describe(category="The section it goes in, e.g. ReShade, FPS packs, Sound packs", name="The guide's name, e.g. How to install a ReShade",
                           image="Upload a picture", image_url="Or a direct https link to a picture", color="Colour as hex, e.g. #5865F2")
    @app_commands.autocomplete(category=category_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(self, interaction: discord.Interaction, category: app_commands.Range[str, 1, 60], name: app_commands.Range[str, 1, 80],
                  image: Optional[discord.Attachment] = None, image_url: Optional[str] = None, color: Optional[str] = None):
        if await get_page(interaction.guild_id, self.KIND, name):
            raise UserError(f"A guide called **{name}** already exists. Use `/installguides edit` to change it.")
        media = await Media(image, image_url).check()
        value = f"{parse_color(color):06X}" if color else None
        await interaction.response.send_modal(PageModal(self.KIND, None, name.strip(), media, value, category.strip(), after_edit, "New installation guide"))

    @app_commands.command(description="Edit a guide's text, links, picture or category")
    @app_commands.describe(guide="Which guide", category="Move it to another category", image="Upload a new picture", image_url="Or a direct https link", remove_image="Take the picture off", color="Colour as hex")
    @app_commands.autocomplete(guide=guide_names, category=category_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(self, interaction: discord.Interaction, guide: str, category: Optional[app_commands.Range[str, 1, 60]] = None, image: Optional[discord.Attachment] = None,
                   image_url: Optional[str] = None, remove_image: bool = False, color: Optional[str] = None):
        row = await self.find(interaction, guide)
        media = await Media(image, image_url, remove_image).check()
        value = f"{parse_color(color):06X}" if color else None
        await interaction.response.send_modal(PageModal(self.KIND, row, row["name"], media, value, (category or row["section"]).strip(), after_edit, "Edit guide"))

    @app_commands.command(description="Delete a guide")
    @app_commands.describe(guide="Which guide")
    @app_commands.autocomplete(guide=guide_names)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, guide: str):
        await self.remove_page(interaction, guide)

    @app_commands.command(description="See every guide and where it's posted")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        rows = await db.fetch_all("SELECT * FROM pages WHERE guild_id = ? AND kind = 'installguides' ORDER BY section, name", (interaction.guild_id,))
        if not rows:
            raise UserError("No guides yet. Add one with `/installguides add`.")
        lines, current = [], None
        for r in rows:
            if r["section"] != current:
                current = r["section"]
                lines.append(f"\n**📂 {current or 'Uncategorised'}**")
            lines.append(f"• {r['name']}" + (f" · <#{r['channel_id']}>" if r["channel_id"] else " · *not posted*"))
        await interaction.response.send_message(embed=ui.card("📚 Installation guides", "\n".join(lines).strip(), guild=interaction.guild, section="Install guides"), ephemeral=True)

    @app_commands.command(description="Post a whole category of guides (or one guide) in a channel")
    @app_commands.describe(channel="Where to post", category="Post every guide in this category", guide="Or post just one guide")
    @app_commands.autocomplete(category=category_autocomplete, guide=guide_names)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def post(self, interaction: discord.Interaction, channel: discord.TextChannel, category: Optional[str] = None, guide: Optional[str] = None):
        if guide:
            rows, intro = [await self.find(interaction, guide)], None
        elif category:
            rows = await db.fetch_all("SELECT * FROM pages WHERE guild_id = ? AND kind = 'installguides' AND section = ? ORDER BY name", (interaction.guild_id, category.strip()))
            intro = f"📚 {category.strip()}"
        else:
            raise UserError("Pick a **category** to post, or a single **guide**.")
        await self.post_pages(interaction, rows, channel, intro)


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class ClothingPreviews(ManyPagesBase, commands.GroupCog, group_name="clothingpreviews", group_description="Clothing previews with pictures, stored in the database"):
    KIND = "clothingpreviews"

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(description="Add a clothing preview")
    @app_commands.describe(name="The preview's name, e.g. 14K Triad tracksuit", image="Upload the preview picture", image_url="Or a direct https link to it", color="Colour as hex")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 80], image: Optional[discord.Attachment] = None, image_url: Optional[str] = None, color: Optional[str] = None):
        if image is None and not image_url:
            raise UserError("Add the preview picture: upload it in `image`, or give a link in `image_url`.")
        if await get_page(interaction.guild_id, self.KIND, name):
            raise UserError(f"A preview called **{name}** already exists. Use `/clothingpreviews edit`.")
        media = await Media(image, image_url).check()
        value = f"{parse_color(color):06X}" if color else None
        await interaction.response.send_modal(PageModal(self.KIND, None, name.strip(), media, value, "", after_edit, "New clothing preview"))

    @app_commands.command(description="Edit a preview's text, links or picture")
    @app_commands.describe(preview="Which preview", image="Upload a new picture", image_url="Or a direct https link", remove_image="Take the picture off", color="Colour as hex")
    @app_commands.autocomplete(preview=preview_names)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(self, interaction: discord.Interaction, preview: str, image: Optional[discord.Attachment] = None, image_url: Optional[str] = None,
                   remove_image: bool = False, color: Optional[str] = None):
        row = await self.find(interaction, preview)
        media = await Media(image, image_url, remove_image).check()
        value = f"{parse_color(color):06X}" if color else None
        await interaction.response.send_modal(PageModal(self.KIND, row, row["name"], media, value, "", after_edit, "Edit clothing preview"))

    @app_commands.command(description="Delete a preview")
    @app_commands.describe(preview="Which preview")
    @app_commands.autocomplete(preview=preview_names)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, preview: str):
        await self.remove_page(interaction, preview)

    @app_commands.command(description="See every preview and where it's posted")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        rows = await db.fetch_all("SELECT * FROM pages WHERE guild_id = ? AND kind = 'clothingpreviews' ORDER BY name", (interaction.guild_id,))
        if not rows:
            raise UserError("No previews yet. Add one with `/clothingpreviews add`.")
        lines = [f"• **{r['name']}**" + (" · 🖼️ uploaded" if r["image_file_id"] else " · 🔗 linked" if r["image_url"] else "") + (f" · <#{r['channel_id']}>" if r["channel_id"] else " · *not posted*") for r in rows]
        await interaction.response.send_message(embed=ui.card("👕 Clothing previews", "\n".join(lines), guild=interaction.guild, section="Previews"), ephemeral=True)

    @app_commands.command(description="Post previews in a channel (all of them, or one)")
    @app_commands.describe(channel="Where to post", preview="Post just this one (default: all)")
    @app_commands.autocomplete(preview=preview_names)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def post(self, interaction: discord.Interaction, channel: discord.TextChannel, preview: Optional[str] = None):
        rows = [await self.find(interaction, preview)] if preview else await db.fetch_all("SELECT * FROM pages WHERE guild_id = ? AND kind = 'clothingpreviews' ORDER BY name", (interaction.guild_id,))
        await self.post_pages(interaction, rows, channel)


# ------------------------------------------------------- styled web page ----
# A real, styled webpage version of a page (/rules, /boosterperks, …), served
# by the bot's shared web server. Discord embeds can't do gradient text, an
# inline video player or fully custom layout, so for people who want that
# look, the "Full page" button on the embed opens this instead.

YOUTUBE_RE = re.compile(r"(?:youtube\.com/watch\?v=|youtube\.com/embed/|youtu\.be/)([A-Za-z0-9_-]{6,20})")
BULLET_RE = re.compile(r"^\s*[-*•]\s+(.*)$")


def esc(s: Optional[str]) -> str:
    return html_escape_mod.escape(s or "", quote=True)


def inline_md(escaped: str) -> str:
    """Very small markdown-ish formatting, applied AFTER escaping (so **/`_` never let raw HTML through)."""
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


def body_html(body: Optional[str]) -> str:
    """Turns the page's text into either a bullet 'tree' (when staff writes `- Title` lines, like the
    rules/perks screenshots) or plain paragraphs, matching whichever style the text is already written in."""
    lines = (body or "").splitlines()
    groups: list[list[str]] = []
    for line in lines:
        m = BULLET_RE.match(line)
        if m:
            groups.append([m.group(1)])
        elif line.strip():
            if groups:
                groups[-1].append(line.strip())
            else:
                groups.append([None, line.strip()])  # plain paragraph line before any bullet
        else:
            groups.append([])  # blank line = paragraph break marker
    groups = [g for g in groups if g]

    has_bullets = any(g[0] is not None for g in groups)
    out = []
    if has_bullets:
        for g in groups:
            if g[0] is None:
                out.append(f'<p class="lead">{inline_md(esc(g[1]))}</p>')
                continue
            title = inline_md(esc(g[0]))
            sub = "".join(f'<div class="tree-line"><span class="tree-glyph">└</span>{inline_md(esc(s))}</div>' for s in g[1:])
            out.append(f'<div class="bullet"><div class="bullet-dot"></div><div class="bullet-body"><div class="bullet-title">{title}</div>{sub}</div></div>')
    else:
        paras = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
        for p in paras:
            out.append(f'<p>{inline_md(esc(p)).replace(chr(10), "<br>")}</p>')
    return "\n".join(out) or '<p class="lead">Nothing written here yet.</p>'


def video_embed_html(url: Optional[str]) -> str:
    if not url:
        return ""
    m = YOUTUBE_RE.search(url)
    if m:
        return (
            '<div class="video"><iframe src="https://www.youtube.com/embed/' + m.group(1)
            + '" title="Video" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>'
        )
    if url.lower().split("?")[0].endswith((".mp4", ".webm", ".ogg")):
        return f'<div class="video"><video controls src="{esc(url)}"></video></div>'
    return ""


PAGE_CSS = """
:root { --a: #8b5cf6; --b: #22d3ee; --bg: #0b0b10; --card: #15151c; --line: #2a2a35; --text: #e6e6ec; --muted: #9a9aa8; }
* { box-sizing: border-box; }
body { margin: 0; background: radial-gradient(circle at 50% -10%, #1b1430 0%, var(--bg) 55%); color: var(--text);
       font-family: "Segoe UI", Roboto, Helvetica, Arial, sans-serif; display: flex; justify-content: center; padding: 32px 16px 64px; }
.wrap { width: 100%; max-width: 760px; }
.banner { display: flex; align-items: center; gap: 18px; margin-bottom: 28px; }
.banner img { width: 64px; height: 64px; border-radius: 50%; }
.banner .name { font-size: 15px; letter-spacing: 2px; color: var(--muted); font-weight: 700; text-transform: uppercase; }
.banner .title { font-size: 32px; font-weight: 800; letter-spacing: 1px; text-transform: uppercase;
                  background: linear-gradient(90deg, var(--a), var(--b)); -webkit-background-clip: text; background-clip: text; color: transparent; }
.card { background: var(--card); border: 1px solid var(--line); border-left: 5px solid; border-image: linear-gradient(var(--a), var(--b)) 1;
        border-radius: 14px; padding: 28px 30px; }
.heading { display: flex; align-items: center; gap: 10px; font-size: 22px; font-weight: 800; margin: 0 0 10px; }
.intro { color: var(--muted); font-size: 15px; line-height: 1.6; margin: 0 0 18px; }
.divider { height: 2px; background: linear-gradient(90deg, var(--a), var(--b)); border-radius: 2px; opacity: .6; margin: 0 0 22px; }
.video { position: relative; width: 100%; aspect-ratio: 16/9; margin-bottom: 22px; border-radius: 10px; overflow: hidden; background: #000; }
.video iframe, .video video { width: 100%; height: 100%; border: 0; }
.bullet { display: flex; gap: 12px; margin-bottom: 18px; }
.bullet-dot { width: 9px; height: 9px; border-radius: 50%; background: linear-gradient(135deg, var(--a), var(--b)); margin-top: 7px; flex: none; }
.bullet-title { font-weight: 700; font-size: 16px; }
.tree-line { color: var(--muted); font-size: 14.5px; line-height: 1.6; margin-top: 2px; }
.tree-glyph { color: var(--b); margin-right: 8px; }
.lead, p { color: var(--muted); font-size: 15px; line-height: 1.7; margin: 0 0 14px; }
.links { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 22px; }
.links a { color: var(--text); background: #1f1f29; border: 1px solid var(--line); padding: 9px 16px; border-radius: 999px;
           text-decoration: none; font-size: 14px; font-weight: 600; }
.links a:hover { border-color: var(--b); }
.pagefooter { text-align: center; color: var(--muted); font-size: 12.5px; margin-top: 22px; }
img.pageimg { width: 100%; border-radius: 10px; margin-bottom: 22px; display: block; }
code { background: #000; padding: 1px 6px; border-radius: 5px; font-size: 90%; }
"""


def render_html(guild, row) -> str:
    kind = KINDS[row["kind"]]
    title = row["title"] or row["name"] or kind["label"]
    guild_name = guild.name if guild else "Server"
    icon = guild.icon.url if guild and guild.icon else None
    links = json.loads(row["links"] or "[]")
    links_html = "".join(f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(label)}</a>' for label, url in links)
    image_url = row["image_url"] or None  # uploaded (database) images aren't served over HTTP, only direct links work here
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} · {esc(guild_name)}</title>
<style>{PAGE_CSS}</style></head>
<body><div class="wrap">
<div class="banner">
  {f'<img src="{esc(icon)}">' if icon else ""}
  <div><div class="name">{esc(guild_name)}</div><div class="title">{esc(title)}</div></div>
</div>
<div class="card">
  <div class="heading">{kind["icon"]} {esc(title)}</div>
  <div class="divider"></div>
  {f'<img class="pageimg" src="{esc(image_url)}">' if image_url else ""}
  {video_embed_html(row["video_url"])}
  {body_html(row["body"])}
  {f'<div class="links">{links_html}</div>' if links_html else ""}
</div>
<div class="pagefooter">{esc(row["footer"] or guild_name)}</div>
</div></body></html>"""


async def handle_page_request(request: web.Request) -> web.Response:
    try:
        page_id = int(request.match_info.get("id", ""))
    except ValueError:
        return web.Response(status=404, text="Not found.")
    row = await db.fetch_one("SELECT * FROM pages WHERE id = ?", (page_id,))
    if row is None or not (row["body"] or row["title"]):
        return web.Response(status=404, text="This page isn't available any more.")
    guild = _bot_ref.get_guild(row["guild_id"]) if _bot_ref is not None else None
    return web.Response(text=render_html(guild, row), content_type="text/html")


_bot_ref: Optional[commands.Bot] = None


async def setup(bot: commands.Bot):
    global _bot_ref
    _bot_ref = bot
    for cog in (Rules, BoosterPerks, PremiumPerks, Links, InstallGuides, ClothingPreviews):
        await bot.add_cog(cog(bot))
    app = getattr(bot, "web_app", None)
    if app is not None:
        app.router.add_get("/page/{id}", handle_page_request)
