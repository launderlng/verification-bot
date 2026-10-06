from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from common import COLOR, DANGER, INFO, WARN, UserError, check_target, parse_duration
from logutil import emit

Reason = Optional[app_commands.Range[str, 1, 400]]


async def notify(user: discord.abc.User, text: str) -> None:
    """Best-effort DM (ignored if the person has DMs closed)."""
    try:
        await user.send(text)
    except discord.HTTPException:
        pass


def audit_reason(interaction: discord.Interaction, reason: Optional[str]) -> str:
    return f"{interaction.user}: {reason or 'No reason given'}"[:512]


@app_commands.guild_only()
class Moderation(commands.Cog):
    @app_commands.command(description="Kick a member")
    @app_commands.describe(member="Who to kick", reason="Why")
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: Reason = None):
        check_target(interaction, member)
        await notify(member, f"You were kicked from **{interaction.guild.name}**. Reason: {reason or 'none given'}")
        await member.kick(reason=audit_reason(interaction, reason))
        await interaction.response.send_message(f"👢 Kicked **{member}**. Reason: {reason or 'none'}")

    @app_commands.command(description="Ban a member (or someone who already left)")
    @app_commands.describe(user="Who to ban", reason="Why", delete_days="Days of their messages to delete (0-7)")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: Reason = None,
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ):
        member = interaction.guild.get_member(user.id)
        if member:
            check_target(interaction, member)
            await notify(member, f"You were banned from **{interaction.guild.name}**. Reason: {reason or 'none given'}")
        await interaction.guild.ban(user, reason=audit_reason(interaction, reason), delete_message_seconds=delete_days * 86400)
        await interaction.response.send_message(f"🔨 Banned **{user}**. Reason: {reason or 'none'}")

    @app_commands.command(description="Unban someone by their user ID")
    @app_commands.describe(user_id="The numeric ID of the banned user", reason="Why")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: Reason = None):
        if not user_id.strip().isdigit():
            raise UserError("Give me the user's numeric ID (Developer Mode → right-click → Copy User ID).")
        try:
            await interaction.guild.unban(discord.Object(id=int(user_id)), reason=audit_reason(interaction, reason))
        except discord.NotFound:
            raise UserError("That user isn't banned.") from None
        await interaction.response.send_message(f"♻️ Unbanned `{user_id}`.")

    @app_commands.command(description="Time a member out (they can't talk or react)")
    @app_commands.describe(member="Who", duration="e.g. 10m, 2h, 1d (max 28d)", reason="Why")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: Reason = None):
        check_target(interaction, member)
        seconds = parse_duration(duration)
        if seconds is None or seconds < 1 or seconds > 28 * 86400:
            raise UserError("Use a duration like `10m`, `2h` or `1d` (max 28d).")
        await member.timeout(timedelta(seconds=seconds), reason=audit_reason(interaction, reason))
        await notify(member, f"You were timed out in **{interaction.guild.name}** for {duration}. Reason: {reason or 'none given'}")
        await interaction.response.send_message(f"🔇 Timed out {member.mention} for `{duration}`. Reason: {reason or 'none'}")

    @app_commands.command(description="End a member's timeout early")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member):
        check_target(interaction, member)
        await member.timeout(None, reason=audit_reason(interaction, "Timeout removed"))
        await interaction.response.send_message(f"🔊 Timeout removed for {member.mention}.")

    @app_commands.command(description="Warn a member (saved on their record)")
    @app_commands.describe(member="Who", reason="What they did")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 400]):
        check_target(interaction, member)
        now = discord.utils.utcnow().isoformat()
        await db.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, member.id, interaction.user.id, reason, now),
        )
        total = (await db.fetch_one("SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, member.id)))["c"]
        await notify(member, f"⚠️ You were warned in **{interaction.guild.name}**: {reason}")
        await emit(
            interaction.guild, "moderation", "⚠️ Member warned",
            f"{member.mention}\n**By:** {interaction.user.mention}\n**Reason:** {reason}\n**Total warnings:** {total}", WARN, footer=f"ID: {member.id}",
        )
        await interaction.response.send_message(f"⚠️ Warned {member.mention} (warning #{total}): {reason}")

    @app_commands.command(description="See a member's warnings")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        rows = await db.fetch_all(
            "SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 15", (interaction.guild_id, member.id)
        )
        if not rows:
            return await interaction.response.send_message(f"{member.mention} has no warnings. ✨", ephemeral=True)
        lines = [
            f"`#{r['id']}` {discord.utils.format_dt(discord.utils.parse_time(r['created_at']), 'd')} by <@{r['moderator_id']}>: {r['reason']}"
            for r in rows
        ]
        embed = discord.Embed(title=f"⚠️ Warnings for {member}", description="\n".join(lines), color=WARN)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Delete one warning by its ID")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def delwarn(self, interaction: discord.Interaction, warning_id: int):
        count = await db.execute("DELETE FROM warnings WHERE id = ? AND guild_id = ?", (warning_id, interaction.guild_id))
        if not count:
            raise UserError("I can't find a warning with that ID in this server.")
        await interaction.response.send_message(f"🗑️ Deleted warning `#{warning_id}`.", ephemeral=True)

    @app_commands.command(description="Clear all of a member's warnings")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
        count = await db.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, member.id))
        await interaction.response.send_message(f"🧹 Cleared {count} warning(s) for {member.mention}.", ephemeral=True)

    @app_commands.command(description="Delete recent messages in this channel")
    @app_commands.describe(
        amount="How many messages to delete (1-100)",
        member="Only delete messages from this member",
        include_pinned="Also delete pinned messages (default: no)",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 100],
        member: Optional[discord.Member] = None,
        include_pinned: bool = False,
    ):
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise UserError("I can only delete messages in text channels and threads.")
        await interaction.response.defer(ephemeral=True)

        # Discord can only bulk-delete messages younger than 14 days
        cutoff = discord.utils.utcnow() - timedelta(days=14) + timedelta(minutes=5)
        scan_limit = 500 if member else amount  # with a member filter, look further back to find their messages
        to_delete: list = []
        pinned_skipped = 0
        hit_old = False
        async for message in channel.history(limit=scan_limit):  # newest first
            if message.created_at < cutoff:
                hit_old = True
                break
            if member and message.author.id != member.id:
                continue
            if message.pinned and not include_pinned:
                pinned_skipped += 1
                continue
            to_delete.append(message)
            if len(to_delete) >= amount:
                break

        if to_delete:
            await channel.delete_messages(to_delete, reason=audit_reason(interaction, "Purge"))

        lines = [f"🧹 Deleted **{len(to_delete)}** message(s)." if to_delete else "Nothing to delete."]
        if pinned_skipped:
            lines.append(f"Skipped {pinned_skipped} pinned message(s). Use `include_pinned:True` to delete those too.")
        if hit_old:
            lines.append("Stopped at messages older than 14 days, which Discord doesn't allow bots to bulk-delete.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @app_commands.command(description="Set a channel's slowmode (0 turns it off)")
    @app_commands.describe(seconds="Seconds between messages (0-21600)")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds, reason=audit_reason(interaction, "Slowmode"))
        await interaction.response.send_message("🐌 Slowmode off." if seconds == 0 else f"🐌 Slowmode set to {seconds}s.")

    async def _set_lock(self, interaction: discord.Interaction, locked: bool):
        channel = interaction.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False if locked else None
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=audit_reason(interaction, "Lock" if locked else "Unlock"))
        await emit(
            interaction.guild, "moderation", "🔒 Channel locked" if locked else "🔓 Channel unlocked",
            f"{channel.mention} by {interaction.user.mention}", WARN if locked else COLOR,
        )
        await interaction.response.send_message(f"🔒 {channel.mention} is locked." if locked else f"🔓 {channel.mention} is unlocked.")

    @app_commands.command(description="Stop everyone from sending messages in this channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction):
        await self._set_lock(interaction, True)

    @app_commands.command(description="Let everyone send messages in this channel again")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction):
        await self._set_lock(interaction, False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation())
