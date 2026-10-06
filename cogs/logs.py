import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import ACCENT, COLOR, DANGER, INFO, SUCCESS, WARN, UserError
from logutil import CATEGORIES, clip, emit, find_audit_entry

CATEGORY_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in CATEGORIES.items()]


def perm_changes(before: discord.Permissions, after: discord.Permissions) -> str:
    lines = []
    for name, value in after:
        if getattr(before, name) != value:
            lines.append(f"{'✅' if value else '❌'} {name.replace('_', ' ').title()}")
    return "\n".join(lines)


@app_commands.guild_only()
class Logs(commands.GroupCog, group_name="logs", group_description="Server activity logs"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.content_enabled = bot.intents.message_content
        super().__init__()

    # --------------------------------------------------------- commands ----

    @app_commands.command(description="Choose the channel where logs are sent")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise UserError(f"I need View Channel, Send Messages and Embed Links in {channel.mention}.")
        await db.upsert_config(interaction.guild_id, log_channel_id=channel.id)
        await emit(interaction.guild, "server", "📋 Logging enabled", f"Logs will appear here. Set up by {interaction.user.mention}.", COLOR)
        notes = "" if self.content_enabled else (
            "\nℹ️ Message *contents* aren't logged yet. To enable that, turn on **Message Content Intent** in the "
            "Developer Portal and set `MESSAGE_CONTENT=true` in your host's variables."
        )
        await interaction.response.send_message(f"✅ Logs will go to {channel.mention}. Use `/logs status` to see every category.{notes}", ephemeral=True)

    @app_commands.command(description="Turn a log category on or off")
    @app_commands.choices(category=CATEGORY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle(self, interaction: discord.Interaction, category: app_commands.Choice[str], enabled: bool):
        await db.execute(
            "INSERT INTO log_routes (guild_id, category, enabled) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, category) DO UPDATE SET enabled = excluded.enabled",
            (interaction.guild_id, category.value, int(enabled)),
        )
        await interaction.response.send_message(f"{'✅' if enabled else '❌'} **{category.name}** logging is now {'on' if enabled else 'off'}.", ephemeral=True)

    @app_commands.command(description="Send one category to its own channel (leave channel blank to reset)")
    @app_commands.choices(category=CATEGORY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def route(self, interaction: discord.Interaction, category: app_commands.Choice[str], channel: Optional[discord.TextChannel] = None):
        if channel:
            perms = channel.permissions_for(interaction.guild.me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                raise UserError(f"I need View Channel, Send Messages and Embed Links in {channel.mention}.")
        await db.execute(
            "INSERT INTO log_routes (guild_id, category, channel_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, category) DO UPDATE SET channel_id = excluded.channel_id",
            (interaction.guild_id, category.value, channel.id if channel else None),
        )
        where = channel.mention if channel else "the default log channel"
        await interaction.response.send_message(f"✅ **{category.name}** now goes to {where}.", ephemeral=True)

    @app_commands.command(description="Show what's being logged and where")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        default = f"<#{cfg['log_channel_id']}>" if cfg and cfg["log_channel_id"] else None
        if not default:
            raise UserError("No log channel yet. Run `/logs setup` first.")
        routes = {r["category"]: r for r in await db.fetch_all("SELECT * FROM log_routes WHERE guild_id = ?", (interaction.guild_id,))}
        lines = []
        for key, label in CATEGORIES.items():
            r = routes.get(key)
            on = not r or r["enabled"]
            where = f"<#{r['channel_id']}>" if r and r["channel_id"] else default
            lines.append(f"{'✅' if on else '❌'} **{label}** → {where if on else 'off'}")
        embed = ui.card(
            "📋 Logging", "\n".join(lines), guild=interaction.guild, section="Logs",
            footer="Message contents: " + ("logged" if self.content_enabled else "not logged (needs Message Content Intent)"),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------------------------------------------------- members ----

    @staticmethod
    def who(user) -> tuple:
        return (f"{user.display_name if hasattr(user, 'display_name') else user.name}", user.display_avatar.url)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await emit(
            member.guild, "members", "Member joined",
            ui.kv(("Member", member.mention), ("Account created", discord.utils.format_dt(member.created_at, "R")),
                  ("Member count", f"{member.guild.member_count:,}"), ("Type", "🤖 Bot" if member.bot else None)),
            SUCCESS, author=self.who(member), footer=f"ID {member.id}",
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        await asyncio.sleep(1.5)  # give the audit log a moment to catch up
        if await find_audit_entry(guild, discord.AuditLogAction.ban, member.id):
            return  # the ban handler logs this one
        kick = await find_audit_entry(guild, discord.AuditLogAction.kick, member.id)
        if kick:
            await emit(
                guild, "moderation", "Member kicked",
                ui.kv(("Member", member.mention), ("By", kick.user.mention if kick.user else "Unknown"), ("Reason", kick.reason or "None given")),
                WARN, author=self.who(member), footer=f"ID {member.id}",
            )
            return
        roles = ", ".join(r.mention for r in reversed(member.roles) if not r.is_default()) or "None"
        await emit(
            guild, "members", "Member left",
            ui.kv(("Member", member.mention), ("Joined", discord.utils.format_dt(member.joined_at, "R") if member.joined_at else None),
                  ("Roles", clip(roles, 800)), ("Member count", f"{guild.member_count:,}")),
            DANGER, author=self.who(member), footer=f"ID {member.id}",
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await asyncio.sleep(1.5)
        entry = await find_audit_entry(guild, discord.AuditLogAction.ban, user.id)
        await emit(
            guild, "moderation", "Member banned",
            ui.kv(("Member", user.mention), ("By", entry.user.mention if entry and entry.user else "Unknown"), ("Reason", (entry.reason if entry else None) or "None given")),
            DANGER, author=self.who(user), footer=f"ID {user.id}",
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await asyncio.sleep(1.5)
        entry = await find_audit_entry(guild, discord.AuditLogAction.unban, user.id)
        await emit(
            guild, "moderation", "Member unbanned", ui.kv(("Member", user.mention), ("By", entry.user.mention if entry and entry.user else "Unknown")),
            SUCCESS, author=self.who(user), footer=f"ID {user.id}",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            entry = await find_audit_entry(guild, discord.AuditLogAction.member_role_update, after.id, within=10)
            await emit(
                guild, "roles", "Roles updated",
                ui.kv(("Member", after.mention), ("➕ Added", ", ".join(r.mention for r in added) if added else None),
                      ("➖ Removed", ", ".join(r.mention for r in removed) if removed else None), ("By", entry.user.mention if entry and entry.user else None)),
                author=self.who(after), footer=f"ID {after.id}",
            )
        if before.nick != after.nick:
            await emit(
                guild, "nicknames", "Nickname changed",
                ui.kv(("Member", after.mention), ("Before", before.nick or "(none)"), ("After", after.nick or "(none)")), author=self.who(after), footer=f"ID {after.id}",
            )
        if before.timed_out_until != after.timed_out_until:
            entry = await find_audit_entry(guild, discord.AuditLogAction.member_update, after.id, within=10)
            details = (("By", entry.user.mention if entry and entry.user else None), ("Reason", entry.reason if entry and entry.reason else None))
            if after.timed_out_until:
                await emit(guild, "moderation", "Member timed out", ui.kv(("Member", after.mention), ("Until", discord.utils.format_dt(after.timed_out_until, "f")), *details), WARN, author=self.who(after), footer=f"ID {after.id}")
            else:
                await emit(guild, "moderation", "Timeout removed", ui.kv(("Member", after.mention), details[0]), SUCCESS, author=self.who(after), footer=f"ID {after.id}")

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if before.name == after.name and before.global_name == after.global_name:
            return
        for guild in self.bot.guilds:
            if guild.get_member(after.id):
                await emit(
                    guild, "nicknames", "Username changed",
                    ui.kv(("User", after.mention), ("Before", before.global_name or before.name), ("After", after.global_name or after.name)), author=self.who(after), footer=f"ID {after.id}",
                )

    # --------------------------------------------------------- messages ----

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        text = message.content if self.content_enabled and message.content else "*(content not available)*"
        fields = [("Message", text)]
        if message.attachments:
            fields.append(("Attachments", "\n".join(a.filename for a in message.attachments)))
        await emit(
            message.guild, "messages", "Message deleted", ui.kv(("Author", message.author.mention), ("Channel", message.channel.mention)),
            DANGER, fields=tuple(fields), author=self.who(message.author), footer=f"Author ID {message.author.id}",
        )

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        await emit(
            messages[0].guild, "messages", "Messages bulk deleted",
            ui.kv(("Channel", messages[0].channel.mention), ("Deleted", ui.plural(len(messages), "message"))), DANGER,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot or not self.content_enabled or before.content == after.content:
            return
        await emit(
            after.guild, "messages", "Message edited",
            ui.kv(("Author", after.author.mention), ("Channel", after.channel.mention), ("Link", f"[Jump to message]({after.jump_url})")),
            WARN, fields=(("Before", before.content), ("After", after.content)), author=self.who(after.author), footer=f"Author ID {after.author.id}",
        )

    # ---------------------------------------------------------- channels ----

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        await emit(channel.guild, "channels", "Channel created", ui.kv(("Channel", channel.mention), ("Type", str(channel.type).title())), SUCCESS)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await emit(channel.guild, "channels", "Channel deleted", ui.kv(("Channel", f"**#{channel.name}**"), ("Type", str(channel.type).title())), DANGER)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        changes = []
        if before.name != after.name:
            changes.append(("Name", f"{before.name} → {after.name}"))
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append(("Topic", f"{clip(getattr(before, 'topic', None), 200)} → {clip(getattr(after, 'topic', None), 200)}"))
        if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
            changes.append(("Slowmode", f"{getattr(before, 'slowmode_delay', 0)}s → {getattr(after, 'slowmode_delay', 0)}s"))
        if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
            changes.append(("Age-restricted", f"{getattr(before, 'nsfw', False)} → {getattr(after, 'nsfw', False)}"))
        if changes:
            await emit(after.guild, "channels", "Channel updated", ui.kv(("Channel", after.mention), *changes))

    # ------------------------------------------------------ roles/server ----

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await emit(role.guild, "roles", "Role created", ui.kv(("Role", role.mention)), SUCCESS)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await emit(role.guild, "roles", "Role deleted", ui.kv(("Role", f"**{role.name}**")), DANGER)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []
        if before.name != after.name:
            changes.append(("Name", f"{before.name} → {after.name}"))
        if before.color != after.color:
            changes.append(("Colour", f"{before.color} → {after.color}"))
        if before.permissions != after.permissions:
            changes.append(("Permissions", "\n" + perm_changes(before.permissions, after.permissions)))
        if changes:
            await emit(after.guild, "roles", "Role updated", ui.kv(("Role", after.mention), *changes), WARN if before.permissions != after.permissions else None)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        changes = []
        if before.name != after.name:
            changes.append(("Name", f"{before.name} → {after.name}"))
        if before.icon != after.icon:
            changes.append(("Icon", "was changed"))
        if before.verification_level != after.verification_level:
            changes.append(("Verification level", f"{before.verification_level} → {after.verification_level}"))
        if changes:
            await emit(after, "server", "Server updated", ui.kv(*changes), WARN)

    # ------------------------------------------------------------ voice ----

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if before.channel == after.channel:
            return
        if before.channel is None:
            title, text, color = "Joined voice", ui.kv(("Member", member.mention), ("Channel", after.channel.mention)), SUCCESS
        elif after.channel is None:
            title, text, color = "Left voice", ui.kv(("Member", member.mention), ("Channel", before.channel.mention)), DANGER
        else:
            title, text, color = "Moved voice channel", ui.kv(("Member", member.mention), ("From", before.channel.mention), ("To", after.channel.mention)), INFO
        await emit(member.guild, "voice", title, text, color, author=self.who(member))


async def setup(bot: commands.Bot):
    await bot.add_cog(Logs(bot))
