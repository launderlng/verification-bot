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
INVITE_PERMS = 1024 | 2048 | 16384 | 64 | 65536  # View Channel, Send Messages, Embed Links, Add Reactions, Read Message History


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


def find_match(name: str, candidates: list) -> Optional[discord.TextChannel]:
    norm = normalize(name)
    for c in candidates:
        if normalize(c.name) == norm:
            return c
    for c in candidates:
        cn = normalize(c.name)
        if norm in cn or cn in norm:
            return c
    return None


def resolve_channel_id(raw: str) -> int:
    raw = raw.strip().strip("<#>").strip()
    if not raw.isdigit():
        raise UserError("That doesn't look like a channel ID. Right-click the channel → **Copy Channel ID** (turn on **Developer Mode** in Discord's App Settings → Advanced first).")
    return int(raw)


async def used_bytes(guild_id: int) -> int:
    return (await db.fetch_one("SELECT COALESCE(SUM(size), 0) AS n FROM stored_files WHERE guild_id = ?", (guild_id,)))["n"]


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class AutoUpload(commands.GroupCog, group_name="autoupload", group_description="Drop files in a channel (even on another server) and auto-add + auto-post them"):
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

        post_channel = self.bot.get_channel(row["post_channel_id"])  # may be on a different server than this message
        if post_channel is None:
            await message.reply(embed=ui.card("⚠️ Can't post", "The channel this is set to post in doesn't exist any more, or I've lost access to it. Ask staff to run `/autoupload add` again.", color=WARN), mention_author=False)
            return
        required_role = post_channel.guild.get_role(row["required_role_id"]) if row["required_role_id"] else None

        store_guild_id = post_channel.guild.id  # the file belongs to the server people actually claim it in
        taken = {r["name"].lower() for r in await db.fetch_all("SELECT name FROM stored_files WHERE guild_id = ?", (store_guild_id,))}
        posted, failed = [], []
        for att in message.attachments:
            try:
                await self.ingest(message, att, row, post_channel, required_role, taken, store_guild_id)
                posted.append(att.filename)
            except UserError as e:
                failed.append(f"**{att.filename}:** {e}")
            except discord.HTTPException as e:
                failed.append(f"**{att.filename}:** Discord wouldn't let me post that ({e.status}).")

        if posted:
            await message.add_reaction("✅")
        if failed:
            await message.reply(embed=ui.card("⚠️ Some files didn't make it", "\n".join(failed), color=WARN), mention_author=False)

    async def ingest(self, message: discord.Message, att: discord.Attachment, row, post_channel: discord.TextChannel,
                      required_role: Optional[discord.Role], taken: set, store_guild_id: int) -> None:
        check_filename(att.filename)
        if att.size > max_file_bytes():
            raise UserError(f"it's {human_size(att.size)}, over the {human_size(max_file_bytes())} limit")
        if att.size > await self.room_left(store_guild_id):
            raise UserError(f"that would go over the {human_size(storage_cap_bytes())} storage limit")
        if att.size > ATTACH_LIMIT and not public_base_url():
            raise UserError(NEEDS_WEB)

        name = clean_name(att.filename, taken)
        data = await att.read()
        path = None
        stored_data = data
        if len(data) > ATTACH_LIMIT:
            path = new_path(store_guild_id)
            await asyncio.to_thread(Path(path).write_bytes, data)
            stored_data = b""

        await db.execute(
            "INSERT INTO stored_files (guild_id, name, filename, content_type, size, data, description, required_role_id, once_per_user, uploaded_by, created_at, path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (store_guild_id, name, att.filename, att.content_type, len(data), stored_data, f"Auto-added from #{message.channel.name} ({message.guild.name})",
             required_role.id if required_role else None, int(row["once_per_user"]), message.author.id, discord.utils.utcnow().isoformat(), path),
        )
        file_row = await db.fetch_one("SELECT id FROM stored_files WHERE guild_id = ? AND name = ?", (store_guild_id, name))

        draft = Draft(post_channel, clean_title(att.filename), None, None, None, deliver=(file_row["id"], name), pack=row["pack_name"])
        await publish_draft(post_channel.guild, message.author, draft)
        await emit(
            post_channel.guild, "files", "File auto-uploaded",
            ui.kv(("👤 By", message.author.mention), ("📦 Stored as", name), ("📍 Dropped in", f"#{message.channel.name} ({message.guild.name})"), ("📬 Posted in", post_channel.mention)),
            subject=message.author.id,
        )

    # ----------------------------------------------------------- commands ----

    @app_commands.command(description="Turn a channel into a drop zone (it can be on another server you've invited me to)")
    @app_commands.describe(
        upload_channel_id="The upload channel's ID (right-click it → Copy Channel ID). Can be on another server.",
        post_channel="Where the auto-post goes, in THIS server, e.g. #reshades",
        pack="What to call this pack/category, e.g. ReShade",
        required_role="Only members with this role can claim the file (optional)",
        once_per_user="Each member can only claim it once (default: no)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(
        self, interaction: discord.Interaction, upload_channel_id: str, post_channel: discord.TextChannel,
        pack: app_commands.Range[str, 1, 60], required_role: Optional[discord.Role] = None, once_per_user: bool = False,
    ):
        channel = self.bot.get_channel(resolve_channel_id(upload_channel_id))
        if channel is None:
            raise UserError("I can't see that channel. Make sure I've been invited to the server it's on — run `/autoupload inviteinfo` for an invite link.")
        if not isinstance(channel, discord.TextChannel):
            raise UserError("That needs to be a text channel.")
        check_can_send(post_channel, interaction.guild.me, files=False)
        await db.execute(
            "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
            "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user",
            (channel.guild.id, channel.id, post_channel.id, pack.strip(), required_role.id if required_role else None,
             int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat()),
        )
        where = f"**#{channel.name}** on **{channel.guild.name}**" if channel.guild.id != interaction.guild_id else channel.mention
        body = (f"Any file a staff member drops in {where} now gets stored in the file library and auto-posted in {post_channel.mention} "
                f"with a **📥 Get file** button, tagged as **{pack.strip()}**."
                + (f"\n\n🔒 Needs {required_role.mention} to claim." if required_role else "") + (" 🔁 Once per member." if once_per_user else ""))
        await interaction.response.send_message(embed=ui.card("✅ Drop zone ready", body, color=SUCCESS, guild=interaction.guild, section="Files"), ephemeral=True)

    @app_commands.command(description="Pair up a whole category from another server by matching channel names to this server's channels")
    @app_commands.describe(
        vault_guild_id="The other server's ID (right-click its icon → Copy Server ID)",
        category="Only match channels in this category on the other server (optional, e.g. RZ)",
        exclude="Skip channel names containing any of these, comma-separated (e.g. no-,chat)",
        required_role="Only members with this role can claim any matched file (optional)",
        once_per_user="Each member can only claim a matched file once (default: no)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bulkadd(
        self, interaction: discord.Interaction, vault_guild_id: str, category: Optional[str] = None, exclude: Optional[str] = None,
        required_role: Optional[discord.Role] = None, once_per_user: bool = False,
    ):
        if not vault_guild_id.strip().isdigit():
            raise UserError("That doesn't look like a server ID. Right-click the server's icon → **Copy Server ID**.")
        vault = self.bot.get_guild(int(vault_guild_id.strip()))
        if vault is None:
            raise UserError("I'm not in that server. Run `/autoupload inviteinfo` for an invite link, add me there, then try again.")
        skip = [s.strip().lower() for s in (exclude or "").split(",") if s.strip()]
        vault_channels = [
            c for c in vault.text_channels
            if (not category or (c.category and category.strip().lower() in c.category.name.lower()))
            and not any(s in c.name.lower() for s in skip)
        ]
        if not vault_channels:
            raise UserError("No matching text channels found over there. Check the category name, or leave it blank to scan every channel.")
        main_channels = interaction.guild.text_channels

        matched, unmatched = [], []
        for vc in vault_channels:
            target = find_match(vc.name, main_channels)
            if target and target.id != vc.id:
                matched.append((vc, target))
            else:
                unmatched.append(vc)

        for vc, target in matched:
            check_can_send(target, interaction.guild.me, files=False)
            await db.execute(
                "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
                "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user",
                (vc.guild.id, vc.id, target.id, vc.name.replace("-", " ").title(), required_role.id if required_role else None,
                 int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat()),
            )

        body = ""
        if matched:
            body += "**✅ Paired**\n" + "\n".join(f"#{vc.name} → {t.mention}" for vc, t in matched) + "\n\n"
        if unmatched:
            body += "**⚠️ No match found on this server** (add these by hand with `/autoupload add`)\n" + "\n".join(f"#{vc.name} (`{vc.id}`)" for vc in unmatched)
        await interaction.response.send_message(embed=ui.card(f"📥 {len(matched)} drop zone(s) paired", body or "Nothing matched.", color=SUCCESS if matched else WARN, guild=interaction.guild, section="Files"), ephemeral=True)

    @app_commands.command(description="Stop a channel being a drop zone")
    @app_commands.describe(upload_channel_id="The upload channel's ID (right-click it → Copy Channel ID)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, upload_channel_id: str):
        cid = resolve_channel_id(upload_channel_id)
        row = await db.fetch_one("SELECT guild_id FROM upload_channels WHERE channel_id = ?", (cid,))
        if not row:
            raise UserError("That channel isn't a drop zone.")
        await db.execute("DELETE FROM upload_channels WHERE channel_id = ?", (cid,))
        await interaction.response.send_message(embed=ui.card("🗑️ Removed", f"`{cid}` is no longer a drop zone.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="See every drop zone that posts into this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        all_rows = await db.fetch_all("SELECT * FROM upload_channels ORDER BY pack_name")
        # upload_channels isn't keyed by the posting guild (the upload side may be on another server),
        # so filter here to just the rows that actually post into this server.
        rows = [r for r in all_rows if interaction.guild.get_channel(r["post_channel_id"]) is not None]
        if not rows:
            raise UserError("No drop zones post into this server yet. Add one with `/autoupload add` or `/autoupload bulkadd`.")
        lines = []
        for r in rows:
            src = self.bot.get_channel(r["channel_id"])
            src_label = f"#{src.name} ({src.guild.name})" if src and src.guild.id != interaction.guild_id else (f"#{src.name}" if src else f"`{r['channel_id']}`")
            lines.append(
                f"📦 **{r['pack_name']}** · {src_label} → <#{r['post_channel_id']}>"
                + (f" · 🔒 <@&{r['required_role_id']}>" if r["required_role_id"] else "") + (" · 🔁 once" if r["once_per_user"] else "")
            )
        await interaction.response.send_message(embed=ui.card("📥 Drop zones", "\n".join(lines), guild=interaction.guild, section="Files"), ephemeral=True)

    @app_commands.command(description="Get an invite link to add me to another server (e.g. your vault/source server)")
    async def inviteinfo(self, interaction: discord.Interaction):
        url = f"https://discord.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot%20applications.commands&permissions={INVITE_PERMS}"
        await interaction.response.send_message(
            embed=ui.card("🔗 Invite link", f"Open this on the account that manages your other server, pick the server, and authorize:\n\n{url}\n\n"
                                             "I only ask for the minimum needed to watch for uploads: View Channel, Send Messages, Embed Links, Add Reactions, Read Message History.",
                           guild=interaction.guild, section="Files"), ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoUpload(bot))
