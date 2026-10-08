from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import DANGER, SUCCESS, WARN, UserError, check_target, parse_color, parse_duration
from fileutil import blocked_extension
from logutil import action_log, emit, mark_handled

Reason = Optional[app_commands.Range[str, 1, 400]]


async def notify(user: discord.abc.User, guild: discord.Guild, title: str, reason: Optional[str], extra: str = "", color=WARN) -> None:
    """Best-effort DM to the member in the same card style as everything else (ignored if they have DMs closed)."""
    body = f"You were {extra or title.lower()} in **{guild.name}**.\n\n" + ui.kv(("📝 Reason", reason or "None given"))
    try:
        await user.send(embed=ui.card(title, body, color=color, guild=guild, section="Moderation"))
    except discord.HTTPException:
        pass


def audit_reason(interaction: discord.Interaction, reason: Optional[str]) -> str:
    return f"{interaction.user}: {reason or 'No reason given'}"[:512]


def done(title: str, text: str, color=SUCCESS) -> discord.Embed:
    return ui.card(title, text, color=color, section="Moderation")


@app_commands.guild_only()
class Moderation(commands.Cog):
    @app_commands.command(description="Kick a member")
    @app_commands.describe(member="Who to kick", reason="Why")
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: Reason = None):
        check_target(interaction, member)
        await notify(member, interaction.guild, "👢 You were kicked", reason, "kicked from")
        mark_handled(interaction.guild_id, member.id, "kick")
        await member.kick(reason=audit_reason(interaction, reason))
        await action_log(interaction.guild, "moderation", "Member kicked", target=member, actor=interaction.user, reason=reason, color=WARN)
        await interaction.response.send_message(embed=done("👢 Member kicked", f"**{member}** was kicked.\n\n" + ui.kv(("📝 Reason", reason or "None given")), WARN))

    @app_commands.command(description="Ban a member (or someone who already left)")
    @app_commands.describe(user="Who to ban", reason="Why", delete_days="Days of their messages to delete (0-7)")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.User, reason: Reason = None, delete_days: app_commands.Range[int, 0, 7] = 0):
        member = interaction.guild.get_member(user.id)
        if member:
            check_target(interaction, member)
            await notify(member, interaction.guild, "🔨 You were banned", reason, "banned from", DANGER)
        mark_handled(interaction.guild_id, user.id, "ban")
        await interaction.guild.ban(user, reason=audit_reason(interaction, reason), delete_message_seconds=delete_days * 86400)
        await action_log(interaction.guild, "moderation", "Member banned", target=user, actor=interaction.user, reason=reason, color=DANGER,
                         lines=(("🗑️ Messages deleted", f"{delete_days} day(s)" if delete_days else None),))
        await interaction.response.send_message(embed=done("🔨 Member banned", f"**{user}** was banned.\n\n" + ui.kv(("📝 Reason", reason or "None given")), DANGER))

    @app_commands.command(description="Unban someone by their user ID")
    @app_commands.describe(user_id="The numeric ID of the banned user", reason="Why")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: Reason = None):
        if not user_id.strip().isdigit():
            raise UserError("Give me the user's numeric ID (Developer Mode → right-click → Copy User ID).")
        target = discord.Object(id=int(user_id))
        mark_handled(interaction.guild_id, target.id, "unban")
        try:
            await interaction.guild.unban(target, reason=audit_reason(interaction, reason))
        except discord.NotFound:
            raise UserError("That user isn't banned.") from None
        await action_log(interaction.guild, "moderation", "Member unbanned", target=target, actor=interaction.user, reason=reason, color=SUCCESS)
        await interaction.response.send_message(embed=done("♻️ Member unbanned", f"<@{user_id}> (`{user_id}`) can join again."))

    @app_commands.command(description="Time a member out, or end their timeout early")
    @app_commands.describe(member="Who", duration="e.g. 10m, 2h, 1d (max 28d), or 'off' to end a timeout", reason="Why")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: Reason = None):
        check_target(interaction, member)
        if duration.strip().lower() in ("off", "0", "remove", "end", "none"):
            mark_handled(interaction.guild_id, member.id, "untimeout")
            await member.timeout(None, reason=audit_reason(interaction, reason or "Timeout removed"))
            await action_log(interaction.guild, "moderation", "Timeout removed", target=member, actor=interaction.user, reason=reason, color=SUCCESS)
            return await interaction.response.send_message(embed=done("🔊 Timeout removed", f"{member.mention} can talk again."))
        seconds = parse_duration(duration)
        if seconds is None or seconds < 1 or seconds > 28 * 86400:
            raise UserError("Use a duration like `10m`, `2h` or `1d` (max 28d), or `off` to end a timeout.")
        mark_handled(interaction.guild_id, member.id, "timeout")
        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        await member.timeout(timedelta(seconds=seconds), reason=audit_reason(interaction, reason))
        await notify(member, interaction.guild, "🔇 You were timed out", reason, f"timed out for {duration} in")
        await action_log(interaction.guild, "moderation", "Member timed out", target=member, actor=interaction.user, reason=reason, color=WARN,
                         lines=(("⏳ Length", duration), ("⏰ Ends", discord.utils.format_dt(until, "R"))))
        await interaction.response.send_message(embed=done("🔇 Member timed out", f"{member.mention} for **{duration}**.\n\n" + ui.kv(("📝 Reason", reason or "None given")), WARN))

    @app_commands.command(description="End a member's timeout early")
    @app_commands.describe(member="Who", reason="Why")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member, reason: Reason = None):
        check_target(interaction, member)
        if not member.is_timed_out():
            raise UserError(f"{member.mention} isn't timed out.")
        mark_handled(interaction.guild_id, member.id, "untimeout")
        await member.timeout(None, reason=audit_reason(interaction, reason or "Timeout removed"))
        await action_log(interaction.guild, "moderation", "Timeout removed", target=member, actor=interaction.user, reason=reason, color=SUCCESS)
        await interaction.response.send_message(embed=done("🔊 Timeout removed", f"{member.mention} can talk again."))

    @app_commands.command(description="Send a member a private embedded DM from the bot, with optional files")
    @app_commands.describe(
        member="Who to message", message="The message", title="Embed title (optional)", color="Hex colour like #5865F2 (optional)",
        image="A picture shown big in the embed", file="A file to attach", file2="A second file", file3="A third file",
        sign="Show your name in the footer",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    async def dm(self, interaction: discord.Interaction, member: discord.Member, message: app_commands.Range[str, 1, 3500],
                 title: Optional[app_commands.Range[str, 1, 200]] = None, color: Optional[str] = None,
                 image: Optional[discord.Attachment] = None, file: Optional[discord.Attachment] = None,
                 file2: Optional[discord.Attachment] = None, file3: Optional[discord.Attachment] = None, sign: bool = True):
        if member.bot:
            raise UserError("Bots can't receive DMs from me.")
        files = [f for f in (file, file2, file3) if f]
        for f in files:
            if blocked_extension(f.filename):
                raise UserError(f"`{f.filename}` is an executable type, so I won't send it.")
        if image and not (image.content_type or "").startswith("image/"):
            raise UserError("The `image` option needs a picture. Use `file` for anything else.")
        await interaction.response.defer(ephemeral=True)
        embed = ui.card(title or f"📩 A message from {interaction.guild.name}", message,
                        **({"color": parse_color(color)} if color else {}), guild=interaction.guild, footer=f"Sent by {interaction.user.display_name}" if sign else None)
        discord_files, names = [], []
        try:
            if image:
                discord_files.append(await image.to_file(filename=f"image_{image.filename}"))
                embed.set_image(url=f"attachment://{discord_files[0].filename}")
            for f in files:
                discord_files.append(await f.to_file())
                names.append(f.filename)
            await member.send(embed=embed, files=discord_files)
        except discord.Forbidden:
            raise UserError(f"{member.mention} has DMs closed, so I couldn't message them.")
        except discord.HTTPException as e:
            raise UserError(f"Discord refused the DM ({e.status}). Files may be too large.")
        await action_log(interaction.guild, "moderation", "DM sent by staff", target=member, actor=interaction.user,
                         lines=(("🖼️ Image", "yes" if image else "no"), ("📎 Files", ", ".join(names) or "none")),
                         fields=(("Message", message[:900]),))
        await interaction.followup.send(embed=done("📩 DM sent", f"Delivered to {member.mention}."), ephemeral=True)

    @app_commands.command(description="Warn a member (saved on their record)")
    @app_commands.describe(member="Who", reason="What they did")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 400]):
        check_target(interaction, member)
        await db.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, member.id, interaction.user.id, reason, discord.utils.utcnow().isoformat()),
        )
        warning_id = (await db.fetch_one("SELECT id FROM warnings WHERE guild_id = ? ORDER BY id DESC LIMIT 1", (interaction.guild_id,)))["id"]
        total = (await db.fetch_one("SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, member.id)))["c"]
        await notify(member, interaction.guild, "⚠️ You were warned", reason, "warned in")
        await action_log(interaction.guild, "moderation", "Member warned", target=member, actor=interaction.user, reason=reason, color=WARN,
                         lines=(("⚖️ Warning", f"`#{warning_id}` (total {total})"),), ids=(("warning", warning_id),))
        await interaction.response.send_message(embed=done("⚠️ Member warned", f"{member.mention} now has **{total}** warning(s).\n\n" + ui.kv(("⚖️ Warning", f"`#{warning_id}`"), ("📝 Reason", reason)), WARN))

    @app_commands.command(description="See a member's warnings, delete one, or clear them all")
    @app_commands.describe(member="Whose warnings", delete_id="Delete this one warning (its # number)", clear="Delete all of this member's warnings")
    @app_commands.checks.has_permissions(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member, delete_id: Optional[int] = None, clear: bool = False):
        gid = interaction.guild_id
        if delete_id is not None:
            row = await db.fetch_one("SELECT * FROM warnings WHERE id = ? AND guild_id = ? AND user_id = ?", (delete_id, gid, member.id))
            if not row:
                raise UserError("I can't find that warning on this member. Check the number with `/warnings`.")
            await db.execute("DELETE FROM warnings WHERE id = ?", (delete_id,))
            await action_log(interaction.guild, "moderation", "Warning deleted", target=member, actor=interaction.user, reason=row["reason"], color=SUCCESS,
                             lines=(("⚖️ Warning", f"`#{delete_id}` (was given by <@{row['moderator_id']}>)"),), ids=(("warning", delete_id),))
            return await interaction.response.send_message(embed=done("🗑️ Warning deleted", f"Warning `#{delete_id}` was removed from {member.mention}."), ephemeral=True)
        if clear:
            count = await db.execute("DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (gid, member.id))
            await action_log(interaction.guild, "moderation", "Warnings cleared", target=member, actor=interaction.user, reason=f"{count} warning(s) removed", color=SUCCESS)
            return await interaction.response.send_message(embed=done("🧹 Warnings cleared", f"Removed **{count}** warning(s) from {member.mention}."), ephemeral=True)
        rows = await db.fetch_all("SELECT * FROM warnings WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 15", (gid, member.id))
        if not rows:
            return await interaction.response.send_message(embed=done("✨ No warnings", f"{member.mention} has a clean record."), ephemeral=True)
        lines = [f"`#{r['id']}` {discord.utils.format_dt(discord.utils.parse_time(r['created_at']), 'd')} by <@{r['moderator_id']}>: {r['reason']}" for r in rows]
        await interaction.response.send_message(embed=ui.card(f"⚠️ Warnings for {member.display_name}", "\n".join(lines) + "\n\n*Delete one with `delete_id`, or all with `clear`.*", color=WARN, section="Moderation"), ephemeral=True)

    @app_commands.command(description="Delete recent messages in this channel")
    @app_commands.describe(amount="How many messages to delete (1-100)", member="Only delete messages from this member", include_pinned="Also delete pinned messages (default: no)")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100], member: Optional[discord.Member] = None, include_pinned: bool = False):
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
            await emit(
                interaction.guild, "moderation", "Messages purged",
                ui.kv(("📍 Channel", channel.mention), ("🗑️ Deleted", f"{len(to_delete)} message(s)"), ("👤 Only from", f"{member.mention} (`{member.id}`)" if member else None)),
                WARN, actor=interaction.user, subject=member.id if member else None, ids=(("channel", channel.id),),
            )
        lines = [f"🧹 Deleted **{len(to_delete)}** message(s)." if to_delete else "Nothing to delete."]
        if pinned_skipped:
            lines.append(f"Skipped {pinned_skipped} pinned message(s). Use `include_pinned:True` to delete those too.")
        if hit_old:
            lines.append("Stopped at messages older than 14 days, which Discord doesn't allow bots to bulk-delete.")
        await interaction.followup.send(embed=done("🧹 Purge", "\n".join(lines)), ephemeral=True)

    @app_commands.command(description="Set a channel's slowmode (0 turns it off)")
    @app_commands.describe(seconds="Seconds between messages (0-21600)")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds, reason=audit_reason(interaction, "Slowmode"))
        await emit(interaction.guild, "moderation", "Slowmode changed", ui.kv(("📍 Channel", interaction.channel.mention), ("🐌 Slowmode", f"{seconds}s" if seconds else "Off")), WARN, actor=interaction.user, ids=(("channel", interaction.channel.id),))
        await interaction.response.send_message(embed=done("🐌 Slowmode", "Slowmode is **off**." if seconds == 0 else f"Slowmode is **{seconds}s**."))

    @app_commands.command(description="Lock a channel so nobody can send messages (or unlock it again)")
    @app_commands.describe(channel="Which channel (default: this one)", unlock="Unlock it instead")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None, unlock: bool = False):
        channel = channel or interaction.channel
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None if unlock else False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=audit_reason(interaction, "Unlock" if unlock else "Lock"))
        await emit(interaction.guild, "moderation", "Channel unlocked" if unlock else "Channel locked", ui.kv(("📍 Channel", channel.mention)), SUCCESS if unlock else WARN, actor=interaction.user, ids=(("channel", channel.id),))
        await interaction.response.send_message(embed=done("🔓 Channel unlocked" if unlock else "🔒 Channel locked", f"{channel.mention} is {'open again' if unlock else 'locked'}.", SUCCESS if unlock else WARN))


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation())
