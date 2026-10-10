import asyncio
import io
import logging
import mimetypes
import os
import re
import zipfile
from pathlib import Path
from typing import Optional

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import db
import ui
from cogs.files import NAME_RE, check_filename, new_path
from cogs.posts import Draft, publish_draft
from common import SUCCESS, WARN, UserError, check_can_send
from fileutil import ATTACH_LIMIT, human_size, max_file_bytes, public_base_url, storage_cap_bytes
from logutil import emit

log = logging.getLogger("verification-bot")

NEEDS_WEB = "Files over 25 MB need a public web address to deliver as a link. In Railway: Settings → Networking → Generate Domain."
INVITE_PERMS = 8  # Administrator -- the only permission that lets the bot see channels in locked-down categories it isn't explicitly added to


def clean_name(filename: str, taken: set) -> str:
    """Turn a filename into a valid, unique stored-file name (letters, numbers, spaces, dashes, underscores)."""
    base = re.sub(r"\.[^.]+$", "", filename)
    base = re.sub(r"[^A-Za-z0-9 _.\-]", "", base).strip()[:60] or "file"
    if not NAME_RE.match(base):
        base = (re.sub(r"^[^A-Za-z0-9]+", "", base) or "file")[:60]
    name, n = base, 2
    while name.lower() in taken:
        name = f"{base}-{n}"[:60]
        n += 1
    taken.add(name.lower())
    return name


def clean_title(filename: str) -> str:
    base = re.sub(r"\.[^.]+$", "", filename)
    base = re.sub(r"[_\-]+", " ", base).strip() or "New file"
    return (base[0].upper() + base[1:])[:256]


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


async def fetch_live(guild: discord.Guild) -> tuple[list, list]:
    """Fetch a guild's channels straight from the API instead of trusting the gateway cache, and return
    (categories, text_channels) -- both from this SAME fetch, never mixed with cached objects.

    Mixing matters: a TextChannel's `.category` property looks its category up in the gateway cache by ID
    (`guild.get_channel(category_id)`), not from wherever the channel itself came from. If the cache's copy of a
    category is stale/wrong (e.g. a leftover category object with a different ID than the real current one -- the
    kind of gap that shows up right after the bot is re-invited with new permissions or added to a category by
    hand), `.category` silently returns None or the wrong category, and anything keyed off it comes back empty.
    Matching categories and channels from one live list sidesteps that entirely."""
    try:
        channels = await guild.fetch_channels()
    except discord.HTTPException:
        return list(guild.categories), list(guild.text_channels)  # fall back to cache if the API call itself fails
    cats = [c for c in channels if isinstance(c, discord.CategoryChannel)]
    texts = [c for c in channels if isinstance(c, discord.TextChannel)]
    return cats, texts


def find_match(name: str, candidates: list) -> Optional[discord.TextChannel]:
    """Exact (normalized) name match only. A loose substring fallback used to live here, but it's actively
    dangerous for this use: "reshades" is a substring of "vault-reshades", so a channel named "reshades" would
    silently match an unrelated "vault-reshades" channel and get wired to the wrong destination -- exactly what
    happened in practice. Since every caller here can fall back to creating a brand-new channel when nothing
    matches, there's no need to guess; a wrong match that overwrites a correct pairing is worse than no match."""
    norm = normalize(name)
    for c in candidates:
        if normalize(c.name) == norm:
            return c
    return None


def resolve_channel_id(raw: str) -> int:
    raw = raw.strip().strip("<#>").strip()
    if not raw.isdigit():
        raise UserError("That doesn't look like a channel ID. Right-click the channel → **Copy Channel ID** (turn on **Developer Mode** in Discord's App Settings → Advanced first).")
    return int(raw)


def resolve_role(guild: discord.Guild, raw: str) -> Optional[discord.Role]:
    """Parse a role from text (an @mention or a plain ID) typed into a modal field -- modals can't use Discord's
    native role picker the way a slash command parameter can. Blank input means "no role", same as leaving the
    slash command's optional parameter unset."""
    raw = (raw or "").strip().strip("<@&>").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise UserError("That doesn't look like a role. @mention it or right-click it in Server Settings → Roles → **Copy Role ID** (turn on **Developer Mode** first).")
    role = guild.get_role(int(raw))
    if role is None:
        raise UserError("I can't find that role on this server.")
    return role


def parse_yes_no(raw: str, default: bool) -> bool:
    raw = (raw or "").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "true", "1", "on")


async def used_bytes(guild_id: int) -> int:
    return (await db.fetch_one("SELECT COALESCE(SUM(size), 0) AS n FROM stored_files WHERE guild_id = ?", (guild_id,)))["n"]


async def send_component_error(interaction: discord.Interaction, error: Exception) -> None:
    """Mirrors bot.py's @bot.tree.error handler, which only covers slash commands -- a button click or modal
    submit is a different kind of interaction and never reaches that handler at all, so without this a UserError
    raised from one of these would just vanish as a silent "This interaction failed" on the user's end."""
    if isinstance(error, UserError):
        msg = f"⚠️ {error}"
    elif isinstance(error, discord.Forbidden):
        msg = "I don't have permission to do that. Check my role position and permissions."
    else:
        log.exception("Unhandled component error", exc_info=error)
        msg = "Something went wrong with that."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


class AddDropZoneModal(discord.ui.Modal, title="➕ Add a drop zone"):
    upload_channel_id = discord.ui.TextInput(label="Upload channel ID (where files get dropped)", placeholder="Right-click the channel → Copy Channel ID")
    post_channel_id = discord.ui.TextInput(label="Post channel (in THIS server)", placeholder="#channel mention or its ID")
    pack = discord.ui.TextInput(label="Pack name", placeholder="e.g. ReShade", max_length=60)
    required_role_id = discord.ui.TextInput(label="Required role to claim (optional)", placeholder="@role or role ID", required=False)
    gif_url = discord.ui.TextInput(label="Branding GIF URL (optional)", placeholder="https://....gif", required=False)

    def __init__(self, cog: "AutoUpload"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        post_channel = interaction.guild.get_channel(resolve_channel_id(self.post_channel_id.value))
        if not isinstance(post_channel, discord.TextChannel):
            raise UserError("The post channel needs to be a text channel in this server.")
        required_role = resolve_role(interaction.guild, self.required_role_id.value)
        await self.cog.add(
            interaction, self.upload_channel_id.value, post_channel, self.pack.value,
            required_role=required_role, once_per_user=False, gif_url=(self.gif_url.value.strip() or None),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await send_component_error(interaction, error)


class RemoveDropZoneModal(discord.ui.Modal, title="🗑️ Remove a drop zone"):
    upload_channel_id = discord.ui.TextInput(label="Upload channel ID to remove", placeholder="Right-click the channel → Copy Channel ID")

    def __init__(self, cog: "AutoUpload"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.remove(interaction, self.upload_channel_id.value)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await send_component_error(interaction, error)


class PresetModal(discord.ui.Modal, title="⚡ Preset: FIVEM/RZ/Boosters"):
    vault_guild_id = discord.ui.TextInput(label="Vault server ID", placeholder="Right-click the server icon → Copy Server ID")
    required_role_id = discord.ui.TextInput(label="Required role to claim (optional)", placeholder="@role or role ID", required=False)
    gif_url = discord.ui.TextInput(label="Branding GIF URL (optional)", placeholder="https://....gif", required=False)
    create_missing = discord.ui.TextInput(label="Create missing channels here? (yes/no)", placeholder="yes", required=False)

    def __init__(self, cog: "AutoUpload"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        required_role = resolve_role(interaction.guild, self.required_role_id.value)
        await self.cog.preset(
            interaction, self.vault_guild_id.value, required_role=required_role, once_per_user=False,
            create_missing=parse_yes_no(self.create_missing.value, True), gif_url=(self.gif_url.value.strip() or None),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await send_component_error(interaction, error)


class BulkAddModal(discord.ui.Modal, title="📥 Bulk add a category"):
    vault_guild_id = discord.ui.TextInput(label="Other server's ID", placeholder="Right-click the server icon → Copy Server ID")
    category = discord.ui.TextInput(label="Category to match (optional)", placeholder="e.g. RZ -- leave blank for the whole server", required=False)
    exclude = discord.ui.TextInput(label="Skip channels containing (optional)", placeholder="e.g. no-,chat", required=False)
    required_role_id = discord.ui.TextInput(label="Required role to claim (optional)", placeholder="@role or role ID", required=False)
    gif_url = discord.ui.TextInput(label="Branding GIF URL (optional)", placeholder="https://....gif", required=False)

    def __init__(self, cog: "AutoUpload"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        required_role = resolve_role(interaction.guild, self.required_role_id.value)
        await self.cog.bulkadd(
            interaction, self.vault_guild_id.value, category=(self.category.value.strip() or None),
            exclude=(self.exclude.value.strip() or None), required_role=required_role, once_per_user=False,
            create_missing=True, gif_url=(self.gif_url.value.strip() or None),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await send_component_error(interaction, error)


class SeedVaultModal(discord.ui.Modal, title="🌱 Seed an empty vault"):
    vault_guild_id = discord.ui.TextInput(label="Empty vault server's ID", placeholder="Right-click the server icon → Copy Server ID")
    required_role_id = discord.ui.TextInput(label="Required role to claim (optional)", placeholder="@role or role ID", required=False)
    gif_url = discord.ui.TextInput(label="Branding GIF URL (optional)", placeholder="https://....gif", required=False)

    def __init__(self, cog: "AutoUpload"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        required_role = resolve_role(interaction.guild, self.required_role_id.value)
        await self.cog.seedvault(
            interaction, self.vault_guild_id.value, required_role=required_role, once_per_user=False,
            gif_url=(self.gif_url.value.strip() or None),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await send_component_error(interaction, error)


class AutoUploadMenuView(discord.ui.View):
    """The buttons behind the one `/autoupload` command. The menu message is ephemeral (only the staff member who
    ran it can even see it), so there's no need to re-check who's clicking -- Discord already scoped that."""

    def __init__(self, cog: "AutoUpload"):
        super().__init__(timeout=300)
        self.cog = cog

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await send_component_error(interaction, error)

    @discord.ui.button(label="Add drop zone", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddDropZoneModal(self.cog))

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def remove_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RemoveDropZoneModal(self.cog))

    @discord.ui.button(label="List drop zones", emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def list_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.list(interaction)

    @discord.ui.button(label="Staff guide", emoji="📤", style=discord.ButtonStyle.primary, row=1)
    async def guide_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.guide(interaction)

    @discord.ui.button(label="Invite link", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def invite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.inviteinfo(interaction)

    @discord.ui.button(label="Preset (FIVEM/RZ/Boosters)", emoji="⚡", style=discord.ButtonStyle.success, row=2)
    async def preset_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PresetModal(self.cog))

    @discord.ui.button(label="Bulk add category", emoji="📥", style=discord.ButtonStyle.secondary, row=2)
    async def bulkadd_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BulkAddModal(self.cog))

    @discord.ui.button(label="Seed empty vault", emoji="🌱", style=discord.ButtonStyle.secondary, row=2)
    async def seedvault_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SeedVaultModal(self.cog))


class AutoUpload(commands.Cog):
    """Everything here is reached through the single `/autoupload` menu command below -- its buttons and modals
    call straight into the plain (undecorated) methods further down, so there's only ever one entry in Discord's
    slash-command list instead of a dozen. `/autoupload` itself is the only thing gated on Manage Server; since
    its own response (and everything opened from it) is ephemeral, nothing further down needs its own check."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        app = getattr(self.bot, "web_app", None)
        if app is not None:
            app.router.add_get("/preview/{filename}", self.handle_preview)

    # --------------------------------------------------- oversized previews ----

    async def handle_preview(self, request: web.Request) -> web.StreamResponse:
        """Serves a preview image/video that was too big to attach to the Discord message directly -- pasted as a
        plain link in the post's content instead, which Discord unfurls into a native inline player the same as
        a real attachment, as long as this answers with the right Content-Type and isn't forced to download.
        Public by design: a URL here is exactly what would otherwise have been a plain inline attachment, which
        was never role-gated either -- only the real deliverable behind the Get-file button is."""
        raw_id = request.match_info.get("filename", "").split(".", 1)[0]
        if not raw_id.isdigit():
            return web.Response(status=404, text="Not found.")
        row = await db.fetch_one("SELECT path, content_type FROM preview_media WHERE id = ?", (int(raw_id),))
        if not row or not row["path"] or not os.path.exists(row["path"]):
            return web.Response(status=404, text="This preview isn't available any more.")
        headers = {"Content-Type": row["content_type"] or "application/octet-stream", "Cache-Control": "public, max-age=3600"}
        return web.FileResponse(row["path"], headers=headers)

    @app_commands.command(name="autoupload", description="Manage drop zones that auto-upload and auto-post files")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def menu(self, interaction: discord.Interaction):
        body = (
            "**➕ Add drop zone** — wire up one channel to auto-post into another (can be on another server)\n"
            "**🗑️ Remove** — stop a channel being a drop zone\n"
            "**📋 List drop zones** — see everything currently wired up\n"
            "**📤 Staff guide** — post the forwarding how-to for staff to pin\n"
            "**🔗 Invite link** — invite me to another server (e.g. a vault)\n"
            "**⚡ Preset** — one-click setup for a FIVEM/RZ/Boosters vault\n"
            "**📥 Bulk add category** — pair a whole category by matching channel names\n"
            "**🌱 Seed empty vault** — build matching empty channels in a brand-new vault server"
        )
        await interaction.response.send_message(
            embed=ui.card("📂 Auto-Upload Menu", body, guild=interaction.guild, section="Files"),
            view=AutoUploadMenuView(self), ephemeral=True,
        )

    async def room_left(self, guild_id: int) -> int:
        return storage_cap_bytes() - await used_bytes(guild_id)

    # ----------------------------------------------------------- listener ----

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        # A forwarded message (Discord's Forward feature) carries its attachments in message_snapshots, not in
        # message.attachments -- the original message's content is "snapshotted" into the forward rather than
        # duplicated as regular attachments. Gather both so dropping a file in directly and forwarding one from
        # elsewhere both work the same way.
        attachments = list(message.attachments)
        for snap in getattr(message, "message_snapshots", None) or getattr(message, "snapshots", None) or []:
            attachments.extend(getattr(snap, "attachments", None) or [])
        if not attachments:
            return
        row = await db.fetch_one("SELECT * FROM upload_channels WHERE guild_id = ? AND channel_id = ?", (message.guild.id, message.channel.id))
        if row is None:
            return
        log.info("autoupload: on_message fired for message %s in #%s (%d attachment(s))", message.id, message.channel.name, len(attachments))
        perms = message.author.guild_permissions
        if not (perms.manage_guild or perms.manage_messages):
            return  # the drop zone only triggers for staff; anyone else's message there is left alone

        post_channel = self.bot.get_channel(row["post_channel_id"])  # may be on a different server than this message
        if post_channel is None:
            await message.reply(embed=ui.card("⚠️ Can't post", "The channel this is set to post in doesn't exist any more, or I've lost access to it. Ask staff to run `/autoupload add` again.", color=WARN), mention_author=False)
            return
        required_role = post_channel.guild.get_role(row["required_role_id"]) if row["required_role_id"] else None

        store_guild_id = post_channel.guild.id  # the file belongs to the server people actually claim it in
        taken = {r["name"].lower() for r in await db.fetch_all("SELECT name FROM stored_files WHERE guild_id = ?", (store_guild_id,))}

        # A drop message is one post, but can carry MULTIPLE real files (e.g. a giveaway forward with several
        # .zip/.rar attachments) -- those all get zipped together into a single archive, stored as one
        # stored_files row, and handed out behind ONE Get-file button. When staff forward a preview
        # screenshot/clip ALONGSIDE the real file(s) in the same message, that image/video is shown inline as a
        # preview only and is never itself stored/claimable. When there's only a media attachment (no separate
        # real file), it's used for both, same as before.
        def ctype_of(a: discord.Attachment) -> str:
            return (a.content_type or mimetypes.guess_type(a.filename)[0] or "").lower()

        media = [a for a in attachments if ctype_of(a).startswith(("image/", "video/", "audio/"))]
        non_media = [a for a in attachments if a not in media]
        preview_att = media[0] if media else None
        deliver_atts = non_media if non_media else ([preview_att] if preview_att else [])
        if len(media) > 1:
            log.info("autoupload: message %s has %d media attachment(s) -- using %s as the preview, ignoring the rest",
                      message.id, len(media), preview_att.filename if preview_att else None)

        posted, failed = [], []
        if deliver_atts:
            # Atomic check-and-claim keyed on the MESSAGE, not a single attachment -- one drop message is one
            # claim, however many files it carries. Attachment id 0 is a sentinel ("whole message"), never a
            # real Discord snowflake, so it can't collide with the old per-attachment rows this table used to
            # get before bundling existed. Stops a duplicate gateway event, or two bot instances briefly
            # overlapping during a deploy, from posting the same drop twice.
            claimed = await db.execute(
                "INSERT OR IGNORE INTO processed_uploads (message_id, attachment_id, created_at) VALUES (?, ?, ?)",
                (message.id, 0, discord.utils.utcnow().isoformat()),
            )
            names = ", ".join(a.filename for a in deliver_atts)
            log.info("autoupload: claim attempt message %s (%d deliverable file(s): %s) -> %s", message.id, len(deliver_atts), names, "claimed" if claimed else "already processed, skipping")
            if claimed:
                try:
                    await self.ingest(message, preview_att, deliver_atts, row, post_channel, required_role, taken, store_guild_id)
                    posted.append(names)
                except UserError as e:
                    failed.append(f"**{names}:** {e}")
                except discord.HTTPException as e:
                    failed.append(f"**{names}:** Discord wouldn't let me post that ({e.status}).")

        if posted:
            await message.add_reaction("✅")
        if failed:
            await message.reply(embed=ui.card("⚠️ Some files didn't make it", "\n".join(failed), color=WARN), mention_author=False)

    async def ingest(self, message: discord.Message, preview_att: Optional[discord.Attachment], deliver_atts: list[discord.Attachment], row, post_channel: discord.TextChannel,
                      required_role: Optional[discord.Role], taken: set, store_guild_id: int) -> None:
        for a in deliver_atts:
            check_filename(a.filename)
        total_declared = sum(a.size for a in deliver_atts)
        if total_declared > max_file_bytes():
            raise UserError(f"that's {human_size(total_declared)} total, over the {human_size(max_file_bytes())} limit")
        if total_declared > await self.room_left(store_guild_id):
            raise UserError(f"that would go over the {human_size(storage_cap_bytes())} storage limit")
        if total_declared > ATTACH_LIMIT and not public_base_url():
            raise UserError(NEEDS_WEB)

        single = len(deliver_atts) == 1
        if single:
            deliver_att = deliver_atts[0]
            data = await deliver_att.read()
            out_filename = deliver_att.filename
            content_type = deliver_att.content_type
        else:
            # More than one real file dropped together -- zip them into one archive so there's still only ever
            # one stored file and one Get-file button, same as the single-file case.
            buf = io.BytesIO()
            used = set()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for a in deliver_atts:
                    entry, n = a.filename, 2
                    while entry.lower() in used:
                        stem, dot, ext = a.filename.rpartition(".")
                        entry = f"{stem}-{n}.{ext}" if dot else f"{a.filename}-{n}"
                        n += 1
                    used.add(entry.lower())
                    zf.writestr(entry, await a.read())
            data = buf.getvalue()
            out_filename = (re.sub(r"[^A-Za-z0-9_\-]+", "_", row["pack_name"]).strip("_") or "files") + ".zip"
            content_type = "application/zip"

        name = clean_name(out_filename, taken)
        path = None
        stored_data = data
        if len(data) > ATTACH_LIMIT:
            path = new_path(store_guild_id)
            await asyncio.to_thread(Path(path).write_bytes, data)
            stored_data = b""

        await db.execute(
            "INSERT INTO stored_files (guild_id, name, filename, content_type, size, data, description, required_role_id, once_per_user, uploaded_by, created_at, path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (store_guild_id, name, out_filename, content_type, len(data), stored_data, f"Auto-added from #{message.channel.name} ({message.guild.name})",
             required_role.id if required_role else None, int(row["once_per_user"]), message.author.id, discord.utils.utcnow().isoformat(), path),
        )
        file_row = await db.fetch_one("SELECT id FROM stored_files WHERE guild_id = ? AND name = ?", (store_guild_id, name))

        # Image/video/audio previews go inline when small enough to attach directly -- images as the embed's big
        # photo, video/audio attached to the message so Discord renders its native player. Either way the
        # attachment is given a clean generic name (not the messy original filename), and show_file_field=False
        # hides the "📎 File <name>" text Draft.embeds() would otherwise add -- the point is a clean post with no
        # filename text visible anywhere, just the pack name, the preview, and the Get-file button.
        photo = file_attach = None
        too_large_for_preview = False
        too_large_size = too_large_limit = None
        preview_url = None
        if preview_att is not None:
            # preview_att is only ever something other than the sole deliverable when a real (non-media) file
            # was forwarded alongside a preview image/video -- that image/video is shown inline here but is
            # NEVER stored or claimable, only the real file(s) above are. When there's no separate real file,
            # preview_att IS the (single) deliverable and its bytes are already in `data`, so no second read
            # happens.
            preview_data = data if (single and preview_att is deliver_atts[0]) else await preview_att.read()
            ext = Path(preview_att.filename).suffix.lower()
            # Discord doesn't always send a content_type for every attachment (some video containers come
            # through with none at all) -- fall back to guessing from the file extension so a real video isn't
            # silently treated as "unknown" and skipped.
            ctype = (preview_att.content_type or mimetypes.guess_type(preview_att.filename)[0] or "").lower()
            clean_attach_name = (re.sub(r"[^A-Za-z0-9_\-]+", "_", row["pack_name"]).strip("_") or "file") + ext
            # Use the destination server's REAL upload limit (boost-tier aware), not just our static guess -- a
            # file that fits our guess but not this server's actual cap would otherwise fail the whole send with
            # an HTTPException, or (if our guess were too high) silently never get attempted.
            inline_limit = min(ATTACH_LIMIT, post_channel.guild.filesize_limit)
            if len(preview_data) <= inline_limit:
                if ctype.startswith("image/"):
                    photo = (clean_attach_name, preview_data, len(preview_data))
                elif ctype.startswith("video/") or ctype.startswith("audio/"):
                    file_attach = (clean_attach_name, preview_data, len(preview_data))
            elif ctype.startswith(("image/", "video/", "audio/")):
                too_large_for_preview = True
                too_large_size, too_large_limit = len(preview_data), inline_limit
                # Can't attach it to the message, but a plain https link to it in the post's CONTENT (not an
                # embed) still gets unfurled by Discord into a native inline player, the same as a real
                # attachment would -- as long as handle_preview answers with the right Content-Type and isn't
                # forced to download. Needs a public web address to serve it from; without one, the post just
                # gets the "too large to preview" note below instead.
                base = public_base_url()
                if base:
                    try:
                        preview_path = new_path(store_guild_id)
                        await asyncio.to_thread(Path(preview_path).write_bytes, preview_data)
                        await db.execute(
                            "INSERT INTO preview_media (guild_id, path, filename, content_type, created_at) VALUES (?, ?, ?, ?, ?)",
                            (store_guild_id, preview_path, preview_att.filename, ctype or "application/octet-stream", discord.utils.utcnow().isoformat()),
                        )
                        media_row = await db.fetch_one("SELECT id FROM preview_media WHERE path = ?", (preview_path,))
                        preview_url = f"{base}/preview/{media_row['id']}{ext}"
                    except OSError:
                        log.exception("autoupload: couldn't save oversized preview to disk for message %s", message.id)
            log.info(
                "autoupload: ingest preview=%s deliver=%s -- raw content_type=%r resolved ctype=%r size=%d inline_limit=%d -> photo=%s file_attach=%s too_large=%s preview_url=%s",
                preview_att.filename, out_filename, preview_att.content_type, ctype, len(preview_data), inline_limit, bool(photo), bool(file_attach), too_large_for_preview, bool(preview_url),
            )

        # Title the post after the pack/source channel, not the raw filename (which is often a meaningless name
        # like "V1" or "Cielo_15") -- no filename and no "Uploaded in #..." text shown anywhere in the public
        # post, just the pack name, the preview, and the Get-file button. gif_url is an optional per-channel
        # branding GIF configured with /autoupload add (or the gif_url option on the bulk commands), shown as its
        # own embed under the main one, same as a manual /post with a GIF attached.
        # Discord always renders a raw video/audio attachment ABOVE any embeds, no matter what order they're
        # sent in -- putting the title in the embed (like the image case does) made it look stuck below the
        # video. So for a video/audio post, the title goes in the message content instead (always renders at the
        # very top) and the embed is left titleless, giving one clean flow: title, video, GIF, button.
        title = None if (file_attach or preview_url) else row["pack_name"]
        if file_attach or preview_url:
            content = f"**{row['pack_name']}**" + (f"\n{preview_url}" if preview_url else "")
        else:
            content = None
        # If the preview itself was too big to attach AND couldn't be served as a link either (no public web
        # address set up), say so right on the post instead of the preview just silently not being there.
        description = (
            f"⚠️ Preview is {human_size(too_large_size)}, too large to show here ({human_size(too_large_limit)} limit) — press **Get file** below."
            if too_large_for_preview and not preview_url else None
        )
        draft = Draft(post_channel, title, description, None, None,
                       photo=photo, file=file_attach, gif_url=row["gif_url"],
                       deliver=(file_row["id"], name), pack=row["pack_name"], show_file_field=False, content=content)
        await publish_draft(post_channel.guild, message.author, draft)
        log_fields = [("👤 By", message.author.mention), ("📦 Stored as", name), ("📍 Dropped in", f"#{message.channel.name} ({message.guild.name})"), ("📬 Posted in", post_channel.mention)]
        if too_large_for_preview:
            log_fields.append((
                "🎬 Oversized preview" if preview_url else "⚠️ No preview",
                f"{human_size(too_large_size)} is over the {human_size(too_large_limit)} inline limit" + (" -- served as a link instead" if preview_url else ""),
            ))
        await emit(post_channel.guild, "files", "File auto-uploaded", ui.kv(*log_fields), subject=message.author.id)

    # ----------------------------------------------------------- commands ----

    async def add(
        self, interaction: discord.Interaction, upload_channel_id: str, post_channel: discord.TextChannel,
        pack: str, required_role: Optional[discord.Role] = None, once_per_user: bool = False,
        gif_url: Optional[str] = None,
    ):
        pack = pack.strip()[:60]
        channel = self.bot.get_channel(resolve_channel_id(upload_channel_id))
        if channel is None:
            raise UserError("I can't see that channel. Make sure I've been invited to the server it's on — use the menu's **Invite link** button.")
        if not isinstance(channel, discord.TextChannel):
            raise UserError("That needs to be a text channel.")
        check_can_send(post_channel, interaction.guild.me, files=False)
        await db.execute(
            "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at, gif_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
            "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user, gif_url = excluded.gif_url",
            (channel.guild.id, channel.id, post_channel.id, pack.strip(), required_role.id if required_role else None,
             int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat(), gif_url.strip() if gif_url else None),
        )
        where = f"**#{channel.name}** on **{channel.guild.name}**" if channel.guild.id != interaction.guild_id else channel.mention
        body = (f"Any file a staff member drops in {where} now gets stored in the file library and auto-posted in {post_channel.mention} "
                f"with a **📥 Get file** button, tagged as **{pack.strip()}**."
                + (f"\n\n🔒 Needs {required_role.mention} to claim." if required_role else "") + (" 🔁 Once per member." if once_per_user else ""))
        await interaction.response.send_message(embed=ui.card("✅ Drop zone ready", body, color=SUCCESS, guild=interaction.guild, section="Files"), ephemeral=True)

    async def pair_category(self, interaction: discord.Interaction, vault: discord.Guild, category: Optional[str], skip: list,
                             required_role: Optional[discord.Role], once_per_user: bool, create_missing: bool = True,
                             dest_category_name: Optional[str] = None, gif_url: Optional[str] = None) -> tuple[list, list, list]:
        """Match one category's (or the whole server's) channels on `vault` to same/similarly-named channels in
        the server the command was run in, and save a drop zone for every match. When `create_missing` is True
        (the default), any vault channel with no existing match gets a brand-new channel created for it here,
        under a category named after the vault channel's own category (or `dest_category_name`, when given, e.g.
        for mirroring within a single server where the source and destination can't share a category name),
        instead of being left unmatched. When `dest_category_name` is given, matching against an existing channel
        is also scoped to just that destination category, rather than the whole server -- important for same-guild
        mirroring, where searching the whole server by name could accidentally latch onto an unrelated channel
        (or the source channel itself).
        Returns (matched, unmatched, created) -- created is the subset of matched that are newly-made channels."""
        vault_cats, all_vault_channels = await fetch_live(vault)
        cat_ids = None
        if category:
            cat_ids = {c.id for c in vault_cats if category.strip().lower() in c.name.lower()}
        vault_channels = [
            c for c in all_vault_channels
            if (cat_ids is None or c.category_id in cat_ids)
            and not any(s in c.name.lower() for s in skip)
        ]
        if dest_category_name:
            existing_dest_cat = discord.utils.find(lambda c: c.name.lower() == dest_category_name.lower(), interaction.guild.categories)
            main_channels = list(existing_dest_cat.text_channels) if existing_dest_cat else []
        else:
            main_channels = list(interaction.guild.text_channels)
        matched, unmatched, created = [], [], []
        new_category_cache: dict = {}  # destination category name (lowered) -> main-guild CategoryChannel
        for vc in vault_channels:
            target = find_match(vc.name, main_channels)
            if target and target.id != vc.id:
                matched.append((vc, target))
                continue
            if not create_missing:
                unmatched.append(vc)
                continue
            vault_cat = next((c for c in vault_cats if c.id == vc.category_id), None)
            want_name = dest_category_name or (vault_cat.name if vault_cat else None)
            cat_key = (want_name or "").lower()
            try:
                dest_cat = new_category_cache.get(cat_key)
                if dest_cat is None:
                    dest_cat = discord.utils.find(
                        lambda c: want_name and c.name.lower() == want_name.lower(), interaction.guild.categories
                    ) if want_name else None
                    if dest_cat is None and want_name:
                        dest_cat = await interaction.guild.create_category(want_name, reason="Auto-created by /autoupload")
                    new_category_cache[cat_key] = dest_cat
                new_channel = await interaction.guild.create_text_channel(
                    vc.name, category=dest_cat, reason=f"Auto-created by /autoupload to mirror #{vc.name}"
                )
            except discord.Forbidden:
                unmatched.append(vc)
                continue
            main_channels.append(new_channel)
            matched.append((vc, new_channel))
            created.append((vc, new_channel))
        for vc, target in matched:
            check_can_send(target, interaction.guild.me, files=False)
            await db.execute(
                "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at, gif_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
                "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user, gif_url = excluded.gif_url",
                (vc.guild.id, vc.id, target.id, vc.name.replace("-", " ").title(), required_role.id if required_role else None,
                 int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat(), gif_url),
            )
        return matched, unmatched, created

    def pairing_result_embed(self, guild: discord.Guild, title: str, matched: list, unmatched: list, created: Optional[list] = None) -> discord.Embed:
        created_ids = {vc.id for vc, _ in (created or [])}
        body = ""
        if matched:
            body += "**✅ Paired**\n" + "\n".join(
                f"#{vc.name} → {t.mention}" + (" 🆕 (new channel)" if vc.id in created_ids else "") for vc, t in matched
            ) + "\n\n"
        if unmatched:
            body += "**⚠️ No match found on this server** (add these by hand from the menu's **Add drop zone** button)\n" + "\n".join(f"#{vc.name} (`{vc.id}`)" for vc in unmatched)
        return ui.card(title, body or "Nothing matched.", color=SUCCESS if matched else WARN, guild=guild, section="Files")

    async def bulkadd(
        self, interaction: discord.Interaction, vault_guild_id: str, category: Optional[str] = None, exclude: Optional[str] = None,
        required_role: Optional[discord.Role] = None, once_per_user: bool = False, create_missing: bool = True,
        gif_url: Optional[str] = None,
    ):
        if not vault_guild_id.strip().isdigit():
            raise UserError("That doesn't look like a server ID. Right-click the server's icon → **Copy Server ID**.")
        vault = self.bot.get_guild(int(vault_guild_id.strip()))
        if vault is None:
            raise UserError("I'm not in that server. Use the menu's **Invite link** button, add me there, then try again.")
        skip = [s.strip().lower() for s in (exclude or "").split(",") if s.strip()]
        await interaction.response.defer(ephemeral=True)
        matched, unmatched, created = await self.pair_category(interaction, vault, category, skip, required_role, once_per_user, create_missing, gif_url=gif_url)
        if not matched and not unmatched:
            raise UserError("No text channels found over there. Check the category name, or leave it blank to scan every channel.")
        await interaction.followup.send(embed=self.pairing_result_embed(interaction.guild, f"📥 {len(matched)} drop zone(s) paired", matched, unmatched, created), ephemeral=True)

    async def preset(
        self, interaction: discord.Interaction, vault_guild_id: str,
        required_role: Optional[discord.Role] = None, once_per_user: bool = False, create_missing: bool = True,
        gif_url: Optional[str] = None,
    ):
        """Pairs the FIVEM, RZ and Boosters categories in one go. Skips NO PROPS entirely (those are toggle/removal
        settings, not files to post) and skips any channel with "chat" in its name (e.g. booster-chat)."""
        if not vault_guild_id.strip().isdigit():
            raise UserError("That doesn't look like a server ID. Right-click the server's icon → **Copy Server ID**.")
        vault = self.bot.get_guild(int(vault_guild_id.strip()))
        if vault is None:
            raise UserError("I'm not in that server. Use the menu's **Invite link** button, add me there, then try again.")

        await interaction.response.defer(ephemeral=True)
        all_matched, all_unmatched, all_created = [], [], []
        for category in ("FIVEM", "RZ", "Boosters"):
            matched, unmatched, created = await self.pair_category(interaction, vault, category, ["chat"], required_role, once_per_user, create_missing, gif_url=gif_url)
            all_matched += matched
            all_unmatched += unmatched
            all_created += created

        await interaction.followup.send(
            embed=self.pairing_result_embed(interaction.guild, f"⚡ Preset applied: {len(all_matched)} drop zone(s) paired", all_matched, all_unmatched, all_created),
            ephemeral=True,
        )

    async def seed_vault(self, interaction: discord.Interaction, vault: discord.Guild, category: str, skip: list,
                          required_role: Optional[discord.Role], once_per_user: bool, gif_url: Optional[str] = None) -> tuple[list, list]:
        """The reverse of pair_category: for when the vault doesn't have the channels yet and the REAL content
        lives here instead. Scans `category` in THIS server (where the real channels already are), and for each
        one creates a same-named channel over in `vault` (under a same-named category there, created if needed)
        if nothing already matches, then pairs the new/matched vault channel to the existing channel here.
        Returns (paired, created) as (vault_channel, main_channel) tuples -- created is the subset of paired
        that are newly-made vault channels."""
        main_cats, main_channels_all = await fetch_live(interaction.guild)
        cat_ids = {c.id for c in main_cats if category.strip().lower() in c.name.lower()}
        main_channels = [c for c in main_channels_all if c.category_id in cat_ids and not any(s in c.name.lower() for s in skip)]
        vault_cats, vault_channels_all = await fetch_live(vault)
        paired, created = [], []
        new_category_cache: dict = {}
        for mc in main_channels:
            main_cat = next((c for c in main_cats if c.id == mc.category_id), None)
            cat_key = (main_cat.name.lower() if main_cat else "")
            vc = find_match(mc.name, vault_channels_all)
            if vc is None:
                try:
                    dest_cat = new_category_cache.get(cat_key)
                    if dest_cat is None:
                        dest_cat = discord.utils.find(
                            lambda c: main_cat and c.name.lower() == main_cat.name.lower(), vault_cats
                        ) if main_cat else None
                        if dest_cat is None and main_cat:
                            dest_cat = await vault.create_category(main_cat.name, reason="Auto-created by /autoupload to seed the vault")
                        new_category_cache[cat_key] = dest_cat
                    vc = await vault.create_text_channel(mc.name, category=dest_cat, reason=f"Auto-created by /autoupload to seed the vault, mirrors #{mc.name}")
                except discord.Forbidden:
                    continue
                vault_channels_all.append(vc)
                created.append((vc, mc))
            check_can_send(mc, interaction.guild.me, files=False)
            await db.execute(
                "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at, gif_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
                "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user, gif_url = excluded.gif_url",
                (vc.guild.id, vc.id, mc.id, mc.name.replace("-", " ").title(), required_role.id if required_role else None,
                 int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat(), gif_url),
            )
            paired.append((vc, mc))
        return paired, created

    async def seedvault(
        self, interaction: discord.Interaction, vault_guild_id: str,
        required_role: Optional[discord.Role] = None, once_per_user: bool = False, gif_url: Optional[str] = None,
    ):
        """For when the real FIVEM/RZ/Boosters channels live in THIS server, not a separate vault: creates the
        matching (empty) channel structure over in the vault server and pairs it back here, so staff can start
        dropping files into the vault and have them post here, without needing to have built the vault by hand."""
        if not vault_guild_id.strip().isdigit():
            raise UserError("That doesn't look like a server ID. Right-click the server's icon → **Copy Server ID**.")
        vault = self.bot.get_guild(int(vault_guild_id.strip()))
        if vault is None:
            raise UserError("I'm not in that server. Use the menu's **Invite link** button, add me there, then try again.")
        if vault.id == interaction.guild.id:
            raise UserError("That's this server's own ID -- `vault_guild_id` needs to be the *other*, empty server you want to seed.")

        await interaction.response.defer(ephemeral=True)
        all_paired, all_created = [], []
        for category in ("FIVEM", "RZ", "Boosters"):
            paired, created = await self.seed_vault(interaction, vault, category, ["chat"], required_role, once_per_user, gif_url=gif_url)
            all_paired += paired
            all_created += created

        await interaction.followup.send(
            embed=self.pairing_result_embed(
                interaction.guild, f"🌱 Seeded {vault.name}: {len(all_paired)} channel(s) wired up", all_paired, [], all_created
            ),
            ephemeral=True,
        )

    async def remove(self, interaction: discord.Interaction, upload_channel_id: str):
        cid = resolve_channel_id(upload_channel_id)
        row = await db.fetch_one("SELECT guild_id FROM upload_channels WHERE channel_id = ?", (cid,))
        if not row:
            raise UserError("That channel isn't a drop zone.")
        await db.execute("DELETE FROM upload_channels WHERE channel_id = ?", (cid,))
        await interaction.response.send_message(embed=ui.card("🗑️ Removed", f"`{cid}` is no longer a drop zone.", color=SUCCESS), ephemeral=True)

    async def list(self, interaction: discord.Interaction):
        all_rows = await db.fetch_all("SELECT * FROM upload_channels ORDER BY pack_name")
        # upload_channels isn't keyed by the posting guild (the upload side may be on another server),
        # so filter here to just the rows that actually post into this server.
        rows = [r for r in all_rows if interaction.guild.get_channel(r["post_channel_id"]) is not None]
        if not rows:
            raise UserError("No drop zones post into this server yet. Add one from the menu's **Add drop zone** or **Bulk add** button.")
        lines = []
        for r in rows:
            src = self.bot.get_channel(r["channel_id"])
            src_label = f"#{src.name} ({src.guild.name})" if src and src.guild.id != interaction.guild_id else (f"#{src.name}" if src else f"`{r['channel_id']}`")
            lines.append(
                f"📦 **{r['pack_name']}** · {src_label} → <#{r['post_channel_id']}>"
                + (f" · 🔒 <@&{r['required_role_id']}>" if r["required_role_id"] else "") + (" · 🔁 once" if r["once_per_user"] else "")
            )
        await interaction.response.send_message(embed=ui.card("📥 Drop zones", "\n".join(lines), guild=interaction.guild, section="Files"), ephemeral=True)

    async def guide(self, interaction: discord.Interaction):
        all_rows = await db.fetch_all("SELECT * FROM upload_channels ORDER BY pack_name")
        rows = [r for r in all_rows if interaction.guild.get_channel(r["post_channel_id"]) is not None]
        if not rows:
            raise UserError("No drop zones post into this server yet. Add one from the menu's **Add drop zone** or **Bulk add** button first, then run this again.")

        # One line per drop zone, "forward into this channel" -> "it posts here".
        lines = []
        for r in rows:
            src = self.bot.get_channel(r["channel_id"])
            src_label = f"{src.mention} ({src.guild.name})" if src and src.guild.id != interaction.guild_id else (src.mention if src else f"`{r['channel_id']}`")
            lines.append(f"**{r['pack_name']}** — forward into {src_label} → posts in <#{r['post_channel_id']}>")

        # "# " / "## " are Discord's big-heading markdown -- only renders large inside the description/content
        # text, not an embed's title field, so the whole thing (title included) goes in the description to
        # actually look bigger instead of the usual small embed text.
        description = (
            "# 📤 How to Upload Files to the Main Discord\n"
            "### (by Forwarding)\n\n"
            "## Steps\n"
            "**1.** Find the file somewhere else (a DM, another server, wherever it was sent to you).\n"
            "**2.** Right-click (or long-press on mobile) the message it's attached to → **Forward**.\n"
            "**3.** Pick the matching channel from the list below and send it there.\n"
            "**4.** That's it — I'll pull it out and auto-post it into the right channel on the main server within a few seconds, no further steps needed.\n\n"
            "⚠️ Drop the file in the **wrong** channel from the list and it'll post to the wrong place, so double check before sending.\n\n"
            "## Forward into these channels\n" + "\n".join(lines)
        )
        embed = ui.card(None, description[:4096], guild=interaction.guild, section="Files")
        await interaction.response.send_message(embed=embed)

    async def inviteinfo(self, interaction: discord.Interaction):
        url = f"https://discord.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot%20applications.commands&permissions={INVITE_PERMS}"
        await interaction.response.send_message(
            embed=ui.card(
                "🔗 Invite link", f"Open this on the account that manages your other server, pick the server, and authorize:\n\n{url}\n\n"
                "This asks for **Administrator**. That's the one permission that lets me see channels even in locked-down "
                "categories like a private vault — anything less (even Manage Channels) still can't see a category unless "
                "someone adds my role to it by hand in Discord's permission UI, since Discord hides channels from bots the "
                "same way it hides them from members who aren't allowed in. If you'd rather not grant that, the alternative "
                "is adding my role to each of FIVEM/RZ/Boosters' permissions yourself (right-click category → Edit Category → "
                "Permissions → add my role → allow View Channel) and I can use a smaller invite instead — just ask.",
                guild=interaction.guild, section="Files",
            ), ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(AutoUpload(bot))
