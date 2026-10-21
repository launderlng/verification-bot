import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import COLOR, INFO, UserError
from logutil import emit

log = logging.getLogger("verification-bot")

REASON_CREATE = "Mirrored from the linked main server"
REASON_UPDATE = "Renamed/moved on the linked main server"
REASON_DELETE = "Removed: deleted on the linked main server"
MAX_MIRROR_ITEMS = 150  # keeps the first sync inside Discord's 15 minute reply window
MIRRORABLE = (discord.TextChannel, discord.VoiceChannel)
MAX_FORWARD_FILES = 10


def _is_normal(message: discord.Message) -> bool:
    """Real messages and replies only (not joins, pins, boosts and other system messages)."""
    return getattr(getattr(message, "type", None), "name", "default") in ("default", "reply")


def simple(title: str, text: str, color: discord.Color = INFO) -> discord.Embed:
    return ui.card(title, text, color=color, section="Server link")


@app_commands.guild_only()
class ServerLink(commands.Cog):
    """/linkserver: mirror a main server's channels into this (second, "vault") server, live. After the first
    catch-up sync, any channel created, renamed, moved or deleted on the main server automatically happens here
    too — channels made directly here are never touched or copied back. The other direction, for CONTENT: any
    message posted in a mirrored vault channel is forwarded into the matching channel on the main server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.syncing: set[int] = set()

    # ------------------------------------------------------------ mirroring ----

    async def _ensure_category(self, target_guild: discord.Guild, main_category: Optional[discord.CategoryChannel]) -> Optional[discord.CategoryChannel]:
        if main_category is None:
            return None
        mapped_id = await db.get_target_channel_id(target_guild.id, main_category.id)
        if mapped_id:
            existing = target_guild.get_channel(mapped_id)
            if existing is not None:
                return existing
        by_name = next((c for c in target_guild.categories if c.name.lower() == main_category.name.lower()), None)
        if by_name is not None:
            await db.set_channel_map(target_guild.id, main_category.id, by_name.id)
            return by_name
        created = await target_guild.create_category(main_category.name, reason=REASON_CREATE)
        await db.set_channel_map(target_guild.id, main_category.id, created.id)
        return created

    async def _mirror_channel(self, target_guild: discord.Guild, channel) -> Optional[discord.abc.GuildChannel]:
        if isinstance(channel, discord.CategoryChannel):
            return await self._ensure_category(target_guild, channel)
        if not isinstance(channel, MIRRORABLE):
            return None  # forum/stage/announcement channels aren't mirrored, to keep this simple and reliable
        parent = await self._ensure_category(target_guild, channel.category)
        kwargs = {"category": parent, "reason": REASON_CREATE}
        if isinstance(channel, discord.TextChannel):
            if channel.topic:
                kwargs["topic"] = channel.topic[:1024]
            kwargs["nsfw"] = channel.nsfw
            created = await target_guild.create_text_channel(channel.name, **kwargs)
        else:
            if channel.user_limit:
                kwargs["user_limit"] = min(channel.user_limit, 99)
            created = await target_guild.create_voice_channel(channel.name, **kwargs)
        await db.set_channel_map(target_guild.id, channel.id, created.id)
        return created

    async def _full_sync(self, main_guild: discord.Guild, target_guild: discord.Guild) -> int:
        total = len(main_guild.categories) + sum(1 for c in main_guild.channels if not isinstance(c, discord.CategoryChannel))
        if total > MAX_MIRROR_ITEMS:
            raise UserError(f"The main server has more than {MAX_MIRROR_ITEMS} channels/categories — too many to mirror in one go.")
        existing_map = await db.get_channel_map(target_guild.id)
        count = 0
        for cat in main_guild.categories:
            if cat.id in existing_map and target_guild.get_channel(existing_map[cat.id]):
                continue
            await self._ensure_category(target_guild, cat)
            count += 1
        for ch in main_guild.channels:
            if isinstance(ch, discord.CategoryChannel):
                continue
            if ch.id in existing_map and target_guild.get_channel(existing_map[ch.id]):
                continue
            if await self._mirror_channel(target_guild, ch) is not None:
                count += 1
        return count

    # ------------------------------------------------------------- commands ----

    @app_commands.command(name="linkserver", description="Mirror a main server's channels into this server, live")
    @app_commands.describe(main_server_id="The ID of the main server to mirror channels from (I must be in it too)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def linkserver(self, interaction: discord.Interaction, main_server_id: str):
        target_guild = interaction.guild
        raw = main_server_id.strip().strip("<#>").strip()
        if not raw.isdigit():
            raise UserError(
                "That doesn't look like a server ID. Right-click the main server's icon → **Copy Server ID** "
                "(turn on Developer Mode in Discord's App Settings → Advanced first)."
            )
        main_guild = self.bot.get_guild(int(raw))
        if main_guild is None:
            raise UserError("I'm not in a server with that ID. I need to be a member of the main server to mirror its channels.")
        if main_guild.id == target_guild.id:
            raise UserError("That's this server. Give me the ID of the **main** server you want to mirror from.")
        if not target_guild.me.guild_permissions.manage_channels:
            raise UserError("I need the **Manage Channels** permission here.")
        if not main_guild.me.guild_permissions.view_channel:
            raise UserError(f"I need **View Channel** in **{main_guild.name}** to see its channels.")
        if target_guild.id in self.syncing:
            raise UserError("A sync is already running here. Wait for it to finish.")

        await interaction.response.defer(ephemeral=True)
        await db.set_server_link(target_guild.id, main_guild.id, interaction.user.id)
        self.syncing.add(target_guild.id)
        try:
            created = await self._full_sync(main_guild, target_guild)
        except UserError:
            self.syncing.discard(target_guild.id)
            raise
        except Exception:
            log.exception("Initial server-link sync failed")
            self.syncing.discard(target_guild.id)
            raise UserError(
                "The first sync hit an error partway through. What was already created is still linked and will keep syncing live — "
                "check my Manage Channels permission in both servers and run `/linkserver` again to catch up on the rest."
            ) from None
        self.syncing.discard(target_guild.id)

        noun = "channel/category" if created == 1 else "channels/categories"
        await interaction.followup.send(embed=simple(
            "🔗 Server linked",
            f"This server now mirrors **{main_guild.name}**. Created **{created}** {noun} to catch up.\n\n"
            "From now on, channels created, renamed, moved or deleted on the main server automatically happen here too — "
            "one-way only, nothing made directly in this server is touched or copied back.",
            COLOR,
        ), ephemeral=True)
        await emit(target_guild, "server", "🔗 Server link set up", ui.kv(("🛡️ By", interaction.user.mention), ("Main server", main_guild.name)))

    @app_commands.command(name="unlinkserver", description="Stop mirroring channels from the linked main server")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def unlinkserver(self, interaction: discord.Interaction):
        link = await db.get_server_link(interaction.guild.id)
        if not link:
            raise UserError("This server isn't linked to a main server.")
        main_guild = self.bot.get_guild(link["main_guild_id"])
        await db.delete_server_link(interaction.guild.id)
        await interaction.response.send_message(embed=simple(
            "Unlinked", f"This server will no longer mirror channel changes from **{main_guild.name if main_guild else 'the main server'}**. "
            "Channels already created here are left as they are.", COLOR,
        ), ephemeral=True)

    @app_commands.command(name="linkstatus", description="Show whether this server is linked to a main server")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def linkstatus(self, interaction: discord.Interaction):
        link = await db.get_server_link(interaction.guild.id)
        if not link:
            return await interaction.response.send_message(embed=simple("Not linked", "This server isn't mirroring any main server. Use `/linkserver` to set it up.", INFO), ephemeral=True)
        main_guild = self.bot.get_guild(link["main_guild_id"])
        mapped = len(await db.get_channel_map(interaction.guild.id))
        await interaction.response.send_message(embed=simple(
            "🔗 Linked", f"Mirroring **{main_guild.name if main_guild else link['main_guild_id']}** live. **{mapped}** channel(s)/category(s) tracked so far.", COLOR,
        ), ephemeral=True)

    # --------------------------------------------------------- forwarding ----

    async def _forward_message(self, message: discord.Message, main_channel: discord.TextChannel) -> None:
        content = (message.content or "").strip()
        attachments = list(message.attachments)
        if not content and not attachments:
            return
        perms = main_channel.permissions_for(main_channel.guild.me)
        if not perms.send_messages or (attachments and not perms.attach_files):
            return
        limit = main_channel.guild.filesize_limit
        files = []
        for a in attachments[:MAX_FORWARD_FILES]:
            if a.size > limit:
                continue
            try:
                files.append(discord.File(io.BytesIO(await a.read()), filename=a.filename, spoiler=a.is_spoiler()))
            except discord.HTTPException:
                pass
        try:
            kwargs = {"files": files} if files else {}
            await main_channel.send(content=content[:2000] or None, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
        except discord.HTTPException:
            log.exception("Vault->main forward failed: %s -> %s", message.channel.id, main_channel.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not _is_normal(message):
            return
        link = await db.get_server_link(message.guild.id)
        if link is None:
            return
        main_channel_id = await db.get_main_channel_id(message.guild.id, message.channel.id)
        if main_channel_id is None:
            return
        main_guild = self.bot.get_guild(link["main_guild_id"])
        main_channel = main_guild.get_channel(main_channel_id) if main_guild else None
        if isinstance(main_channel, discord.TextChannel):
            await self._forward_message(message, main_channel)

    # ------------------------------------------------------------- listeners ----

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        for link in await db.get_server_links_for_main(channel.guild.id):
            target_guild = self.bot.get_guild(link["target_guild_id"])
            if target_guild is None or not target_guild.me.guild_permissions.manage_channels:
                continue
            try:
                await self._mirror_channel(target_guild, channel)
            except discord.HTTPException:
                log.exception("Mirror create failed: %s -> guild %s", channel.id, target_guild.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        for link in await db.get_server_links_for_main(channel.guild.id):
            target_guild_id = link["target_guild_id"]
            mapped_id = await db.get_target_channel_id(target_guild_id, channel.id)
            if mapped_id is None:
                continue
            target_guild = self.bot.get_guild(target_guild_id)
            target_channel = target_guild.get_channel(mapped_id) if target_guild else None
            if target_channel is not None:
                try:
                    await target_channel.delete(reason=REASON_DELETE)
                except discord.HTTPException:
                    log.exception("Mirror delete failed: %s -> %s", channel.id, mapped_id)
            await db.delete_channel_map_by_main(target_guild_id, channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if before.name == after.name and getattr(before, "category_id", None) == getattr(after, "category_id", None):
            return
        for link in await db.get_server_links_for_main(after.guild.id):
            target_guild_id = link["target_guild_id"]
            mapped_id = await db.get_target_channel_id(target_guild_id, after.id)
            if mapped_id is None:
                continue
            target_guild = self.bot.get_guild(target_guild_id)
            target_channel = target_guild.get_channel(mapped_id) if target_guild else None
            if target_channel is None:
                continue
            try:
                kwargs = {}
                if before.name != after.name:
                    kwargs["name"] = after.name
                if after.category_id != before.category_id:
                    kwargs["category"] = await self._ensure_category(target_guild, after.category)
                if kwargs:
                    await target_channel.edit(reason=REASON_UPDATE, **kwargs)
            except discord.HTTPException:
                log.exception("Mirror update failed: %s -> %s", after.id, mapped_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerLink(bot))
