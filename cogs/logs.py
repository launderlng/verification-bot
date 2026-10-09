import asyncio
import time
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import ui
from common import COLOR, DANGER, INFO, SUCCESS, WARN, UserError, check_can_send, staff_ids
from logutil import (CATEGORIES, CATEGORY_STYLE, ESSENTIALS, HISTORY_DAYS, LOG_CHANNELS, clip, emit, find_audit_entry,
                     prune_history, was_handled)

CATEGORY_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in CATEGORIES.items()]
DANGEROUS = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks", "ban_members", "kick_members", "mention_everyone")
LOG_CATEGORY_NAME = "📋 Logs"


def pretty(name: str) -> str:
    return name.replace("_", " ").title()


def dangerous_perms(perms) -> list[str]:
    return [pretty(n) for n in DANGEROUS if getattr(perms, n, False)]


def perm_changes(before: discord.Permissions, after: discord.Permissions) -> str:
    lines = []
    for name, value in after:
        if getattr(before, name) != value:
            risky = " ⚠️" if name in DANGEROUS and value else ""
            lines.append(f"{'✅' if value else '❌'} {pretty(name)}{risky}")
    return "\n".join(lines)


def label_parts(category: str) -> tuple[str, str]:
    emoji, _, name = CATEGORY_STYLE[category][0].partition(" ")
    return emoji, name


# ----------------------------------------------------------------- setup UI ----

class CategorySelect(discord.ui.Select):
    def __init__(self, cog: "Logs", enabled: set):
        options = [
            discord.SelectOption(label=label_parts(key)[1], value=key, emoji=label_parts(key)[0], description=CATEGORIES[key][:100], default=key in enabled)
            for key in CATEGORIES
        ]
        super().__init__(placeholder="Choose what to log…", options=options, min_values=0, max_values=len(options), row=0)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if not await self.cog.guard(interaction):
            return
        chosen = set(self.values)
        for key in CATEGORIES:
            await self.cog.set_enabled(interaction.guild_id, key, key in chosen)
        await self.cog.refresh_dashboard(interaction)


class LogsDashboard(discord.ui.View):
    def __init__(self, cog: "Logs", enabled: set):
        super().__init__(timeout=600)
        self.cog = cog
        self.add_item(CategorySelect(cog, enabled))

    @discord.ui.button(label="Set up everything", emoji="⚡", style=discord.ButtonStyle.success, row=1)
    async def setup_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        """One click: turn every log on, create the private channels, send each log to the right one, and test."""
        if not await self.cog.guard(interaction):
            return
        await interaction.response.defer()
        try:
            made, tested = await self.cog.run_full_setup(interaction.guild, interaction.user)
        except UserError as e:
            return await self.cog.fail(interaction, e)
        await self.cog.refresh_dashboard(
            interaction, note=f"⚡ **All set!** Every log is on and sent to {len(made)} private channels in **{LOG_CATEGORY_NAME}**. I posted a test message in each ({tested} sent).", edited=True,
        )

    @discord.ui.button(label="Essentials only", emoji="⭐", style=discord.ButtonStyle.secondary, row=1)
    async def essentials(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.guard(interaction):
            return
        await interaction.response.defer()
        try:
            made, tested = await self.cog.run_full_setup(interaction.guild, interaction.user, only=ESSENTIALS)
        except UserError as e:
            return await self.cog.fail(interaction, e)
        await self.cog.refresh_dashboard(interaction, note=f"⭐ Only the essential logs are on, sent to private channels in **{LOG_CATEGORY_NAME}**.", edited=True)

    @discord.ui.button(label="All in this channel", emoji="📌", style=discord.ButtonStyle.secondary, row=1)
    async def use_here(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.guard(interaction):
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Run this inside a normal text channel.", ephemeral=True)
        try:
            check_can_send(channel, interaction.guild.me)
        except UserError as e:
            return await self.cog.fail(interaction, e)
        for key in CATEGORIES:
            await db.execute(
                "INSERT INTO log_routes (guild_id, category, channel_id, enabled) VALUES (?, ?, NULL, 1) "
                "ON CONFLICT(guild_id, category) DO UPDATE SET channel_id = NULL, enabled = 1", (interaction.guild_id, key),
            )
        await db.upsert_config(interaction.guild_id, log_channel_id=channel.id)
        await self.cog.refresh_dashboard(interaction, note=f"📌 Every log now goes to {channel.mention}.")

    @discord.ui.button(label="Send test", emoji="🧪", style=discord.ButtonStyle.secondary, row=1)
    async def send_test(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.cog.guard(interaction):
            return
        sent = await self.cog.send_test(interaction.guild)
        await self.cog.refresh_dashboard(interaction, note=f"🧪 Sent a test message to {sent} channel(s)." if sent else "🧪 Nothing to test yet. Press **⚡ Set up everything** first.")


# --------------------------------------------------------------------- cog ----

@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Logs(commands.GroupCog, group_name="logs", group_description="Server activity logs"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.content_enabled = bot.intents.message_content
        self.voice_since: dict[tuple[int, int], float] = {}
        super().__init__()

    async def cog_load(self):
        self.pruner.start()

    async def cog_unload(self):
        self.pruner.cancel()

    @tasks.loop(hours=6)
    async def pruner(self):
        await prune_history()

    @pruner.before_loop
    async def before_pruner(self):
        await self.bot.wait_until_ready()

    # ---------------------------------------------------------- helpers ----

    async def guard(self, interaction: discord.Interaction) -> bool:
        """True if the person may change logging. Otherwise tells them privately and returns False."""
        if interaction.user.guild_permissions.manage_guild:
            return True
        await interaction.response.send_message("You need the **Manage Server** permission to change logging.", ephemeral=True)
        return False

    async def fail(self, interaction: discord.Interaction, error: UserError) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(str(error), ephemeral=True)
        else:
            await interaction.response.send_message(str(error), ephemeral=True)

    async def set_enabled(self, guild_id: int, category: str, enabled: bool) -> None:
        await db.execute(
            "INSERT INTO log_routes (guild_id, category, enabled) VALUES (?, ?, ?) ON CONFLICT(guild_id, category) DO UPDATE SET enabled = excluded.enabled",
            (guild_id, category, int(enabled)),
        )

    async def enabled_map(self, guild_id: int) -> tuple:
        rows = {r["category"]: r for r in await db.fetch_all("SELECT * FROM log_routes WHERE guild_id = ?", (guild_id,))}
        return {key: (rows[key]["enabled"] if key in rows else 1) == 1 for key in CATEGORIES}, rows

    async def dashboard_embed(self, guild: discord.Guild, note: Optional[str] = None) -> tuple:
        cfg = await db.get_config(guild.id)
        enabled, routes = await self.enabled_map(guild.id)
        default = f"<#{cfg['log_channel_id']}>" if cfg and cfg["log_channel_id"] else None
        lines = []
        for key in CATEGORIES:
            emoji, name = label_parts(key)
            if not enabled[key]:
                lines.append(f"❌ {emoji} ~~{name}~~")
                continue
            route = routes.get(key)
            where = f"<#{route['channel_id']}>" if route and route["channel_id"] else default
            lines.append(f"✅ {emoji} **{name}** · {where or '*no channel yet*'}")
        tip = ""
        if not default and not any(r["channel_id"] for r in routes.values()):
            tip = "\n\n💡 **Start here:** press **⚡ Set up everything**. One click turns every log on, creates the private channels and tests them."
        body = (f"{note}\n\n" if note else "") + "Pick what to log and where it goes. This screen updates as you click.\n\n" + ui.DIVIDER + "\n" + "\n".join(lines) + tip
        embed = ui.card(
            "📋 Logging setup", body, guild=guild, section="Logs",
            footer="Message text: " + ("logged" if self.content_enabled else "not logged (turn on Message Content Intent to include it)"),
        )
        return embed, {k for k, v in enabled.items() if v}

    async def refresh_dashboard(self, interaction: discord.Interaction, note: Optional[str] = None, edited: bool = False) -> None:
        embed, on = await self.dashboard_embed(interaction.guild, note)
        view = LogsDashboard(self, on)
        if edited:
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)

    async def create_log_channels(self, guild: discord.Guild, invoker: discord.Member) -> dict:
        """Make a private '📋 Logs' category with one channel per kind of log, and route each category there."""
        tickets_cfg = await db.get_ticket_config(guild.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True),
            invoker: discord.PermissionOverwrite(view_channel=True, read_message_history=True),
        }
        for rid in staff_ids(tickets_cfg) if tickets_cfg else []:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
        category = next((c for c in guild.categories if c.name == LOG_CATEGORY_NAME), None)
        if category is None:
            try:
                category = await guild.create_category(LOG_CATEGORY_NAME, overwrites=overwrites, reason="Logging setup")
            except discord.Forbidden:
                raise UserError("I need the **Manage Channels** permission to create the log channels.") from None
        made = {}
        for key, (name, topic, _) in LOG_CHANNELS.items():
            channel = next((c for c in category.channels if c.name == name), None) if hasattr(category, "channels") else None
            if channel is None:
                try:
                    channel = await guild.create_text_channel(name, category=category, topic=topic, reason="Logging setup")
                except discord.HTTPException:
                    raise UserError("I couldn't create a log channel. Check my Manage Channels permission and the 50-channel category limit.") from None
            made[key] = channel
        for key, (_, _, categories) in LOG_CHANNELS.items():
            for cat in categories:
                await db.execute(
                    "INSERT INTO log_routes (guild_id, category, channel_id, enabled) VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(guild_id, category) DO UPDATE SET channel_id = excluded.channel_id",  # keeps whatever is switched on/off
                    (guild.id, cat, made[key].id),
                )
        await db.upsert_config(guild.id, log_channel_id=made["moderation"].id)
        return made

    async def run_full_setup(self, guild: discord.Guild, invoker: discord.Member, only: Optional[set] = None) -> tuple:
        """Everything in one go: switch the logs on, create the private channels, route every category and post a test in each.
        Returns (channels made, tests sent)."""
        on = set(CATEGORIES) if only is None else set(only)
        for key in CATEGORIES:
            await self.set_enabled(guild.id, key, key in on)
        made = await self.create_log_channels(guild, invoker)
        tested = await self.send_test(guild)
        return made, tested

    async def send_test(self, guild: discord.Guild) -> int:
        cfg = await db.get_config(guild.id)
        enabled, routes = await self.enabled_map(guild.id)
        targets: dict[int, list] = {}
        for key in CATEGORIES:
            route = routes.get(key)
            cid = (route["channel_id"] if route and route["channel_id"] else None) or (cfg["log_channel_id"] if cfg else None)
            if cid and enabled[key]:
                targets.setdefault(cid, []).append(label_parts(key))
        sent = 0
        for cid, cats in targets.items():
            channel = guild.get_channel(cid)
            if channel is None:
                continue
            body = "Logging works here ✅\n\n" + "\n".join(f"{emoji} {name}" for emoji, name in cats)
            try:
                await channel.send(embed=ui.card("🧪 Test log", body, guild=guild, section="Logs"))
                sent += 1
            except discord.HTTPException:
                pass
        return sent

    async def by(self, guild: discord.Guild, action: str, target_id: int):
        """Who did it, according to the audit log (needs View Audit Log)."""
        await asyncio.sleep(1.2)
        entry = await find_audit_entry(guild, getattr(discord.AuditLogAction, action), target_id)
        return entry.user.mention if entry and entry.user else None

    @staticmethod
    def who(user) -> tuple:
        return (user.display_name if hasattr(user, "display_name") else user.name, user.display_avatar.url)

    # --------------------------------------------------------- commands ----

    @app_commands.command(description="Set up logging with one screen (channels, what to log, test)")
    @app_commands.describe(channel="Optional: send logs to this one channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        note = None
        if channel:
            check_can_send(channel, interaction.guild.me)
            await db.upsert_config(interaction.guild_id, log_channel_id=channel.id)
            note = f"📌 Logs now go to {channel.mention}."
        embed, on = await self.dashboard_embed(interaction.guild, note)
        await interaction.response.send_message(embed=embed, view=LogsDashboard(self, on), ephemeral=True)

    @app_commands.command(description="Set up ALL logging in one step (creates private log channels)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def auto(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        made, tested = await self.run_full_setup(interaction.guild, interaction.user)
        lines = [f"{name.split('・')[0]} <#{made[key].id}> · {topic}" for key, (name, topic, _) in LOG_CHANNELS.items()]
        embed = ui.card(
            "⚡ Logging is ready",
            f"Every log is **on** and sent to its own private channel in **{LOG_CATEGORY_NAME}** (only you, me and your ticket staff can see them). I posted a test message in each.\n\n" + "\n".join(lines)
            + f"\n\n{ui.DIVIDER}\nChange anything later with `/logs setup`.",
            guild=interaction.guild, section="Logs",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(description="Turn a log category on or off")
    @app_commands.choices(category=CATEGORY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle(self, interaction: discord.Interaction, category: app_commands.Choice[str], enabled: bool):
        await self.set_enabled(interaction.guild_id, category.value, enabled)
        await interaction.response.send_message(f"{'✅' if enabled else '❌'} **{category.name}** logging is now {'on' if enabled else 'off'}.", ephemeral=True)

    @app_commands.command(description="Send one category to its own channel (leave channel blank to reset)")
    @app_commands.choices(category=CATEGORY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def route(self, interaction: discord.Interaction, category: app_commands.Choice[str], channel: Optional[discord.TextChannel] = None):
        if channel:
            check_can_send(channel, interaction.guild.me)
        await db.execute(
            "INSERT INTO log_routes (guild_id, category, channel_id) VALUES (?, ?, ?) ON CONFLICT(guild_id, category) DO UPDATE SET channel_id = excluded.channel_id",
            (interaction.guild_id, category.value, channel.id if channel else None),
        )
        await interaction.response.send_message(f"✅ **{category.name}** now goes to {channel.mention if channel else 'the default log channel'}.", ephemeral=True)

    @app_commands.command(description="Show what's being logged and where")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        embed, _ = await self.dashboard_embed(interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Send a test message to every log channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test(self, interaction: discord.Interaction):
        sent = await self.send_test(interaction.guild)
        if not sent:
            raise UserError("There's nowhere to log to yet. Run `/logs setup` first.")
        await interaction.response.send_message(f"🧪 Sent a test message to {sent} channel(s).", ephemeral=True)

    @app_commands.command(description="Search recent log entries, for one member or one category")
    @app_commands.describe(member="Only entries about this member", category="Only this category")
    @app_commands.choices(category=CATEGORY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def history(self, interaction: discord.Interaction, member: Optional[discord.User] = None, category: Optional[app_commands.Choice[str]] = None):
        sql, params = "SELECT * FROM log_history WHERE guild_id = ?", [interaction.guild_id]
        if member:
            sql += " AND subject_id = ?"
            params.append(member.id)
        if category:
            sql += " AND category = ?"
            params.append(category.value)
        rows = await db.fetch_all(sql + " ORDER BY id DESC LIMIT 15", tuple(params))
        if not rows:
            raise UserError("Nothing found. History keeps the last %d days of events that were logged." % HISTORY_DAYS)
        lines = []
        for r in rows:
            emoji = CATEGORY_STYLE.get(r["category"], ("•",))[0].split(" ")[0]
            summary = (r["summary"] or "").replace("\n", " · ")
            lines.append(f"{discord.utils.format_dt(discord.utils.parse_time(r['created_at']), 'R')} {emoji} **{r['title']}**\n　{clip(summary, 120)}")
        title = "📜 History" + (f" for {member.display_name}" if member else "")
        await interaction.response.send_message(embed=ui.card(title, "\n\n".join(lines), guild=interaction.guild, section="Logs"), ephemeral=True)

    @app_commands.command(description="How much has been logged lately")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stats(self, interaction: discord.Interaction):
        now = discord.utils.utcnow()
        day = (now - timedelta(days=1)).isoformat()
        week = (now - timedelta(days=7)).isoformat()
        rows = await db.fetch_all(
            "SELECT category, SUM(created_at >= ?) AS d, COUNT(*) AS w FROM log_history WHERE guild_id = ? AND created_at >= ? GROUP BY category ORDER BY w DESC",
            (day, interaction.guild_id, week),
        )
        if not rows:
            raise UserError("No log entries yet. Once events are logged they show up here.")
        peak = max(r["w"] for r in rows)
        lines = [
            f"{CATEGORY_STYLE.get(r['category'], ('•',))[0]}\n　`{ui.bar(r['w'] / peak)}` **{r['w']:,}** this week · {int(r['d'] or 0):,} today" for r in rows
        ]
        total = sum(r["w"] for r in rows)
        await interaction.response.send_message(embed=ui.card("📊 Log stats", f"**{total:,}** events in the last 7 days\n\n" + "\n\n".join(lines), guild=interaction.guild, section="Logs"), ephemeral=True)

    # ---------------------------------------------------------- members ----

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        age = (discord.utils.utcnow() - member.created_at).days
        await emit(
            member.guild, "members", "Member joined",
            ui.kv(("👤 Member", member.mention), ("📅 Account created", discord.utils.format_dt(member.created_at, "R")),
                  ("👥 Member count", f"{member.guild.member_count:,}"), ("🏷️ Type", "🤖 Bot" if member.bot else None),
                  ("🚩 Warning", f"New account, only {age} day(s) old" if age < 7 and not member.bot else None)),
            SUCCESS, author=self.who(member), footer=f"ID {member.id}", subject=member.id,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        if was_handled(guild.id, member.id, "kick"):
            return  # /kick already logged this, with who did it and why
        await asyncio.sleep(1.5)  # give the audit log a moment to catch up
        if await find_audit_entry(guild, discord.AuditLogAction.ban, member.id):
            return  # the ban handler logs this one
        kick = await find_audit_entry(guild, discord.AuditLogAction.kick, member.id)
        if kick:
            await emit(
                guild, "moderation", "Member kicked",
                ui.kv(("👤 Member", member.mention), ("🛡️ By", kick.user.mention if kick.user else "Unknown"), ("📝 Reason", kick.reason or "None given")),
                WARN, author=self.who(member), footer=f"ID {member.id}", subject=member.id,
            )
            return
        roles = ", ".join(r.mention for r in reversed(member.roles) if not r.is_default()) or "None"
        stayed = ui.duration((discord.utils.utcnow() - member.joined_at).total_seconds()) if member.joined_at else None
        await emit(
            guild, "members", "Member left",
            ui.kv(("👤 Member", member.mention), ("📥 Joined", discord.utils.format_dt(member.joined_at, "R") if member.joined_at else None),
                  ("⏱️ Stayed", stayed), ("🎭 Roles", clip(roles, 800)), ("👥 Member count", f"{guild.member_count:,}")),
            DANGER, author=self.who(member), footer=f"ID {member.id}", subject=member.id,
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if was_handled(guild.id, user.id, "ban"):
            return  # /ban already logged this
        await asyncio.sleep(1.5)
        entry = await find_audit_entry(guild, discord.AuditLogAction.ban, user.id)
        await emit(
            guild, "moderation", "Member banned",
            ui.kv(("👤 Member", user.mention), ("🛡️ By", entry.user.mention if entry and entry.user else "Unknown"), ("📝 Reason", (entry.reason if entry else None) or "None given")),
            DANGER, author=self.who(user), footer=f"ID {user.id}", subject=user.id,
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        if was_handled(guild.id, user.id, "unban"):
            return  # /unban already logged this
        await asyncio.sleep(1.5)
        entry = await find_audit_entry(guild, discord.AuditLogAction.unban, user.id)
        await emit(
            guild, "moderation", "Member unbanned", ui.kv(("👤 Member", user.mention), ("🛡️ By", entry.user.mention if entry and entry.user else "Unknown")),
            SUCCESS, author=self.who(user), footer=f"ID {user.id}", subject=user.id,
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]
            entry = await find_audit_entry(guild, discord.AuditLogAction.member_role_update, after.id, within=10)
            risky = sorted({p for r in added for p in dangerous_perms(r.permissions)})
            await emit(
                guild, "roles", "Roles updated",
                ui.kv(("👤 Member", after.mention), ("➕ Added", ", ".join(r.mention for r in added) if added else None),
                      ("➖ Removed", ", ".join(r.mention for r in removed) if removed else None), ("🛡️ By", entry.user.mention if entry and entry.user else None),
                      ("⚠️ Dangerous", "Now has " + ", ".join(risky) if risky else None)),
                WARN if risky else None, author=self.who(after), footer=f"ID {after.id}", subject=after.id,
            )
        if before.nick != after.nick:
            await emit(
                guild, "nicknames", "Nickname changed",
                ui.kv(("👤 Member", after.mention), ("⬅️ Before", before.nick or "(none)"), ("➡️ After", after.nick or "(none)")),
                author=self.who(after), footer=f"ID {after.id}", subject=after.id,
            )
        if before.timed_out_until != after.timed_out_until and was_handled(guild.id, after.id, "timeout" if after.timed_out_until else "untimeout"):
            pass  # /timeout already logged this
        elif before.timed_out_until != after.timed_out_until:
            entry = await find_audit_entry(guild, discord.AuditLogAction.member_update, after.id, within=10)
            details = (("🛡️ By", entry.user.mention if entry and entry.user else None), ("📝 Reason", entry.reason if entry and entry.reason else None))
            if after.timed_out_until:
                await emit(guild, "moderation", "Member timed out", ui.kv(("👤 Member", after.mention), ("⏳ Until", discord.utils.format_dt(after.timed_out_until, "f")), *details), WARN, author=self.who(after), footer=f"ID {after.id}", subject=after.id)
            else:
                await emit(guild, "moderation", "Timeout removed", ui.kv(("👤 Member", after.mention), details[0]), SUCCESS, author=self.who(after), footer=f"ID {after.id}", subject=after.id)

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        if before.name == after.name and before.global_name == after.global_name:
            return
        for guild in self.bot.guilds:
            if guild.get_member(after.id):
                await emit(
                    guild, "nicknames", "Username changed",
                    ui.kv(("👤 User", after.mention), ("⬅️ Before", before.global_name or before.name), ("➡️ After", after.global_name or after.name)),
                    author=self.who(after), footer=f"ID {after.id}", subject=after.id,
                )

    # --------------------------------------------------------- messages ----

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        text = message.content if self.content_enabled and message.content else "*(content not available)*"
        fields = [("Message", text)]
        if message.attachments:
            fields.append(("Attachments", "\n".join(f"{a.filename}" for a in message.attachments)))
        reply = getattr(getattr(message, "reference", None), "message_id", None)
        await emit(
            message.guild, "messages", "Message deleted",
            ui.kv(("✍️ Author", message.author.mention), ("💬 Channel", message.channel.mention),
                  ("↩️ Replying to", f"[a message](https://discord.com/channels/{message.guild.id}/{message.channel.id}/{reply})" if reply else None),
                  ("📅 Sent", discord.utils.format_dt(message.created_at, "R") if getattr(message, "created_at", None) else None)),
            DANGER, fields=tuple(fields), author=self.who(message.author), footer=f"Author ID {message.author.id}", subject=message.author.id,
        )

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        await emit(
            messages[0].guild, "messages", "Messages bulk deleted",
            ui.kv(("💬 Channel", messages[0].channel.mention), ("🗑️ Deleted", ui.plural(len(messages), "message"))), DANGER,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not after.guild or after.author.bot or not self.content_enabled or before.content == after.content:
            return
        await emit(
            after.guild, "messages", "Message edited",
            ui.kv(("✍️ Author", after.author.mention), ("💬 Channel", after.channel.mention), ("🔗 Link", f"[Jump to message]({after.jump_url})")),
            WARN, fields=(("⬅️ Before", before.content), ("➡️ After", after.content)), author=self.who(after.author), footer=f"Author ID {after.author.id}", subject=after.author.id,
        )

    # ---------------------------------------------------------- channels ----

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        by = await self.by(channel.guild, "channel_create", channel.id)
        await emit(channel.guild, "channels", "Channel created", ui.kv(("💬 Channel", channel.mention), ("🏷️ Type", str(channel.type).title()), ("🛡️ By", by)), SUCCESS)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        by = await self.by(channel.guild, "channel_delete", channel.id)
        await emit(channel.guild, "channels", "Channel deleted", ui.kv(("💬 Channel", f"**#{channel.name}**"), ("🏷️ Type", str(channel.type).title()), ("🛡️ By", by)), DANGER)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        changes = []
        if before.name != after.name:
            changes.append(("✏️ Name", f"{before.name} → {after.name}"))
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append(("📌 Topic", f"{clip(getattr(before, 'topic', None), 200)} → {clip(getattr(after, 'topic', None), 200)}"))
        if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
            changes.append(("🐌 Slowmode", f"{getattr(before, 'slowmode_delay', 0)}s → {getattr(after, 'slowmode_delay', 0)}s"))
        if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
            changes.append(("🔞 Age-restricted", f"{getattr(before, 'nsfw', False)} → {getattr(after, 'nsfw', False)}"))
        if before.overwrites != after.overwrites:
            changes.append(("🔑 Permissions", "Channel permissions were changed"))
        if changes:
            by = await self.by(after.guild, "channel_update", after.id)
            await emit(after.guild, "channels", "Channel updated", ui.kv(("💬 Channel", after.mention), *changes, ("🛡️ By", by)))

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        await emit(thread.guild, "channels", "Thread created", ui.kv(("🧵 Thread", thread.mention), ("💬 In", thread.parent.mention if thread.parent else None), ("👤 By", f"<@{thread.owner_id}>" if thread.owner_id else None)), SUCCESS)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        await emit(thread.guild, "channels", "Thread deleted", ui.kv(("🧵 Thread", f"**{thread.name}**"), ("💬 In", thread.parent.mention if thread.parent else None)), DANGER)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel):
        await emit(channel.guild, "server", "Webhooks changed", ui.kv(("💬 Channel", channel.mention), ("ℹ️ Note", "A webhook was created, edited or deleted here")), WARN)

    # ------------------------------------------------------ roles/server ----

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        by = await self.by(role.guild, "role_create", role.id)
        await emit(role.guild, "roles", "Role created", ui.kv(("🎭 Role", role.mention), ("🛡️ By", by)), SUCCESS)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        by = await self.by(role.guild, "role_delete", role.id)
        await emit(role.guild, "roles", "Role deleted", ui.kv(("🎭 Role", f"**{role.name}**"), ("🛡️ By", by)), DANGER)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        changes = []
        if before.name != after.name:
            changes.append(("✏️ Name", f"{before.name} → {after.name}"))
        if before.color != after.color:
            changes.append(("🎨 Colour", f"{before.color} → {after.color}"))
        risky = False
        if before.permissions != after.permissions:
            diff = perm_changes(before.permissions, after.permissions)
            risky = "⚠️" in diff
            changes.append(("🔑 Permissions", "\n" + diff))
        if changes:
            by = await self.by(after.guild, "role_update", after.id)
            await emit(after.guild, "roles", "Role updated", ui.kv(("🎭 Role", after.mention), *changes, ("🛡️ By", by)), WARN if risky or before.permissions != after.permissions else None)

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        changes = []
        if before.name != after.name:
            changes.append(("✏️ Name", f"{before.name} → {after.name}"))
        if before.icon != after.icon:
            changes.append(("🖼️ Icon", "was changed"))
        if before.verification_level != after.verification_level:
            changes.append(("🔐 Verification level", f"{before.verification_level} → {after.verification_level}"))
        if changes:
            by = await self.by(after, "guild_update", after.id)
            await emit(after, "server", "Server updated", ui.kv(*changes, ("🛡️ By", by)), WARN)

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild: discord.Guild, before, after):
        added = [e for e in after if e not in before]
        removed = [e for e in before if e not in after]
        if not added and not removed:
            return
        await emit(
            guild, "server", "Emoji updated",
            ui.kv(("➕ Added", " ".join(str(e) for e in added) if added else None), ("➖ Removed", ", ".join(f"`:{e.name}:`" for e in removed) if removed else None)),
        )

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        guild = self.bot.get_guild(invite.guild.id) if invite.guild else None
        if guild is None:
            return
        expires = discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=invite.max_age), "R") if invite.max_age else "Never"
        await emit(
            guild, "invites", "Invite created",
            ui.kv(("🔗 Code", f"`{invite.code}`"), ("👤 By", invite.inviter.mention if invite.inviter else None), ("💬 Channel", invite.channel.mention if invite.channel else None),
                  ("🔢 Max uses", str(invite.max_uses) if invite.max_uses else "Unlimited"), ("⏳ Expires", expires)),
            SUCCESS, subject=invite.inviter.id if invite.inviter else None,
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        guild = self.bot.get_guild(invite.guild.id) if invite.guild else None
        if guild is None:
            return
        await emit(guild, "invites", "Invite deleted", ui.kv(("🔗 Code", f"`{invite.code}`"), ("💬 Channel", invite.channel.mention if invite.channel else None)), DANGER)

    # ------------------------------------------------------------ voice ----

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        key = (member.guild.id, member.id)
        if before.channel != after.channel:
            if before.channel is None:
                self.voice_since[key] = time.monotonic()
                title, text, color = "Joined voice", ui.kv(("👤 Member", member.mention), ("💬 Channel", after.channel.mention)), SUCCESS
            elif after.channel is None:
                started = self.voice_since.pop(key, None)
                title, text, color = "Left voice", ui.kv(("👤 Member", member.mention), ("💬 Channel", before.channel.mention), ("⏱️ Time in voice", ui.duration(time.monotonic() - started) if started else None)), DANGER
            else:
                title, text, color = "Moved voice channel", ui.kv(("👤 Member", member.mention), ("⬅️ From", before.channel.mention), ("➡️ To", after.channel.mention)), INFO
            await emit(member.guild, "voice", title, text, color, author=self.who(member), subject=member.id)
        elif before.mute != after.mute or before.deaf != after.deaf:
            parts = []
            if before.mute != after.mute:
                parts.append("🔇 Server muted" if after.mute else "🔊 Server unmuted")
            if before.deaf != after.deaf:
                parts.append("🙉 Server deafened" if after.deaf else "👂 Server undeafened")
            await emit(member.guild, "voice", "Voice moderation", ui.kv(("👤 Member", member.mention), ("💬 Channel", after.channel.mention if after.channel else None), ("🛡️ Action", " · ".join(parts))), WARN, author=self.who(member), subject=member.id)
        elif before.self_stream != after.self_stream:
            await emit(member.guild, "voice", "Started streaming" if after.self_stream else "Stopped streaming", ui.kv(("👤 Member", member.mention), ("💬 Channel", after.channel.mention if after.channel else None)), None, author=self.who(member), subject=member.id)

    # ------------------------------------------------------- commands ----

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command) -> None:
        """A full audit trail of every slash command run, in its own channel, separate from the specific
        action logs (so a staff member's /ban still shows in #mod-logs AND in this command history)."""
        if interaction.guild is None:
            return
        options = []
        try:
            for name, value in interaction.namespace:
                options.append(f"`{name}`: {clip(str(value), 80)}")
        except Exception:
            pass
        await emit(
            interaction.guild, "commands", f"/{command.qualified_name}",
            ui.kv(("👤 Used by", interaction.user.mention), ("📍 Channel", interaction.channel.mention if interaction.channel else None),
                  ("⚙️ Options", ", ".join(options) if options else "None")),
            author=self.who(interaction.user), subject=interaction.user.id,
        )

    # ------------------------------------------------- scheduled events ----

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent) -> None:
        await emit(
            event.guild, "events", "Event created",
            ui.kv(("📅 Event", event.name), ("🕒 Starts", discord.utils.format_dt(event.start_time, "f") if event.start_time else None),
                  ("📍 Location", getattr(event, "location", None) or (event.channel.mention if event.channel else None)),
                  ("👤 By", event.creator.mention if event.creator else None)),
            SUCCESS,
        )

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent) -> None:
        await emit(event.guild, "events", "Event cancelled", ui.kv(("📅 Event", event.name)), DANGER)

    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before: discord.ScheduledEvent, after: discord.ScheduledEvent) -> None:
        changes = []
        if before.name != after.name:
            changes.append(("✏️ Name", f"{before.name} → {after.name}"))
        if before.start_time != after.start_time:
            changes.append(("🕒 Starts", discord.utils.format_dt(after.start_time, "f") if after.start_time else None))
        if before.status != after.status:
            changes.append(("📊 Status", f"{before.status} → {after.status}"))
        if changes:
            await emit(after.guild, "events", "Event updated", ui.kv(("📅 Event", after.name), *changes))


async def setup(bot: commands.Bot):
    await bot.add_cog(Logs(bot))
