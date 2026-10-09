import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import discord
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


async def used_bytes(guild_id: int) -> int:
    return (await db.fetch_one("SELECT COALESCE(SUM(size), 0) AS n FROM stored_files WHERE guild_id = ?", (guild_id,)))["n"]


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class AutoUpload(commands.GroupCog, group_name="autoupload", group_description="Drop files in a channel and auto-add them to the file library and post them"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def room_left(self, guild_id: int) -> int:
        return storage_cap_bytes() - await used_bytes(guild_id)

    # ----------------------------------------------------------- listener ----

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.attachments:
            return
        row = await db.fetch_one("SELECT * FROM upload_channels WHERE guild_id = ? AND channel_id = ?", (message.guild.id, message.channel.id))
        if row is None:
            return
        perms = message.author.guild_permissions
        if not (perms.manage_guild or perms.manage_messages):
            return  # the drop zone only triggers for staff; anyone else's message there is left alone

        post_channel = message.guild.get_channel(row["post_channel_id"])
        if post_channel is None:
            await message.reply(embed=ui.card("⚠️ Can't post", "The channel this is set to post in doesn't exist any more. Ask staff to run `/autoupload add` again.", color=WARN), mention_author=False)
            return
        required_role = message.guild.get_role(row["required_role_id"]) if row["required_role_id"] else None

        taken = {r["name"].lower() for r in await db.fetch_all("SELECT name FROM stored_files WHERE guild_id = ?", (message.guild.id,))}
        posted, failed = [], []
        for att in message.attachments:
            try:
                await self.ingest(message, att, row, post_channel, required_role, taken)
                posted.append(att.filename)
            except UserError as e:
                failed.append(f"**{att.filename}:** {e}")
            except discord.HTTPException as e:
                failed.append(f"**{att.filename}:** Discord wouldn't let me post that ({e.status}).")

        if posted:
            await message.add_reaction("✅")
        if failed:
            await message.reply(embed=ui.card("⚠️ Some files didn't make it", "\n".join(failed), color=WARN), mention_author=False)

    async def ingest(self, message: discord.Message, att: discord.Attachment, row, post_channel: discord.TextChannel, required_role: Optional[discord.Role], taken: set) -> None:
        check_filename(att.filename)
        if att.size > max_file_bytes():
            raise UserError(f"it's {human_size(att.size)}, over the {human_size(max_file_bytes())} limit")
        if att.size > await self.room_left(message.guild.id):
            raise UserError(f"that would go over the {human_size(storage_cap_bytes())} storage limit")
        if att.size > ATTACH_LIMIT and not public_base_url():
            raise UserError(NEEDS_WEB)

        name = clean_name(att.filename, taken)
        data = await att.read()
        path = None
        stored_data = data
        if len(data) > ATTACH_LIMIT:
            path = new_path(message.guild.id)
            await asyncio.to_thread(Path(path).write_bytes, data)
            stored_data = b""

        await db.execute(
            "INSERT INTO stored_files (guild_id, name, filename, content_type, size, data, description, required_role_id, once_per_user, uploaded_by, created_at, path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (message.guild.id, name, att.filename, att.content_type, len(data), stored_data, f"Auto-added from #{message.channel.name}",
             required_role.id if required_role else None, int(row["once_per_user"]), message.author.id, discord.utils.utcnow().isoformat(), path),
        )
        file_row = await db.fetch_one("SELECT id FROM stored_files WHERE guild_id = ? AND name = ?", (message.guild.id, name))

        draft = Draft(post_channel, clean_title(att.filename), None, None, None, deliver=(file_row["id"], name), pack=row["pack_name"])
        await publish_draft(message.guild, message.author, draft)
        await emit(
            message.guild, "files", "File auto-uploaded",
            ui.kv(("👤 By", message.author.mention), ("📦 Stored as", name), ("📍 Dropped in", message.channel.mention), ("📬 Posted in", post_channel.mention)),
            subject=message.author.id,
        )

    # ----------------------------------------------------------- commands ----

    @app_commands.command(description="Turn a channel into a drop zone: files posted there get stored and auto-posted")
    @app_commands.describe(
        upload_channel="Where staff will drop files, e.g. #reshade-uploads",
        post_channel="Where the auto-post goes, e.g. #reshades",
        pack="What to call this pack/category, e.g. ReShade",
        required_role="Only members with this role can claim the file (optional)",
        once_per_user="Each member can only claim it once (default: no)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(
        self, interaction: discord.Interaction, upload_channel: discord.TextChannel, post_channel: discord.TextChannel,
        pack: app_commands.Range[str, 1, 60], required_role: Optional[discord.Role] = None, once_per_user: bool = False,
    ):
        check_can_send(post_channel, interaction.guild.me, files=False)
        await db.execute(
            "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
            "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user",
            (interaction.guild_id, upload_channel.id, post_channel.id, pack.strip(), required_role.id if required_role else None,
             int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat()),
        )
        body = (f"Any file a staff member drops in {upload_channel.mention} now gets stored in the file library and auto-posted in {post_channel.mention} "
                f"with a **📥 Get file** button, tagged as **{pack.strip()}**."
                + (f"\n\n🔒 Needs {required_role.mention} to claim." if required_role else "") + (" 🔁 Once per member." if once_per_user else ""))
        await interaction.response.send_message(embed=ui.card("✅ Drop zone ready", body, color=SUCCESS, guild=interaction.guild, section="Files"), ephemeral=True)

    @app_commands.command(description="Stop a channel being a drop zone")
    @app_commands.describe(upload_channel="Which channel to stop watching")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, upload_channel: discord.TextChannel):
        count = await db.execute("DELETE FROM upload_channels WHERE guild_id = ? AND channel_id = ?", (interaction.guild_id, upload_channel.id))
        if not count:
            raise UserError(f"{upload_channel.mention} isn't a drop zone.")
        await interaction.response.send_message(embed=ui.card("🗑️ Removed", f"{upload_channel.mention} is no longer a drop zone.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="See every drop zone")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        rows = await db.fetch_all("SELECT * FROM upload_channels WHERE guild_id = ? ORDER BY pack_name", (interaction.guild_id,))
        if not rows:
            raise UserError("No drop zones set up yet. Add one with `/autoupload add`.")
        lines = [
            f"📦 **{r['pack_name']}** · <#{r['channel_id']}> → <#{r['post_channel_id']}>"
            + (f" · 🔒 <@&{r['required_role_id']}>" if r["required_role_id"] else "") + (" · 🔁 once" if r["once_per_user"] else "")
            for r in rows
        ]
        await interaction.response.send_message(embed=ui.card("📥 Drop zones", "\n".join(lines), guild=interaction.guild, section="Files"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoUpload(bot))
