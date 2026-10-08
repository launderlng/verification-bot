import logging
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import SUCCESS, WARN, UserError, is_staff, staff_ids
from logutil import emit

log = logging.getLogger("verification-bot")

POWERFUL = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks", "ban_members", "kick_members",
            "mention_everyone", "manage_messages", "moderate_members")
BOT_NEEDS = ("manage_roles", "manage_channels", "manage_guild", "manage_messages", "kick_members", "ban_members", "moderate_members",
             "view_audit_log", "attach_files", "embed_links", "read_message_history")
STAFF_WORDS = ("staff", "mod", "admin", "owner", "private", "logs", "orders", "tickets-log", "ticket-log")
STAFF_TEXT = [("staff-chat", "Talk with the team"), ("staff-commands", "Run bot commands here"), ("staff-orders", "Order problems and manual fulfilment"),
              ("staff-giveaways", "Plan and run giveaways"), ("staff-announcements", "Internal notices")]
STAFF_VOICE = ["Staff VC 1", "Staff VC 2", "Meeting room"]

SECTIONS = {
    "shop": ("🛒 Shop and orders", [
        ("/shop add · edit · remove · post", "Manage products and post their cards"),
        ("/shop orders · order <ID>", "Look up purchases and who processed them"),
        ("/shop createorder", "Log a sale made outside Stripe and send the buyer their file"),
        ("/shop resend <ID>", "Send the receipt, file and role again"),
        ("/shop revoke <ID>", "Take back a role (refunds)"),
        ("/staff customer @member", "Everything about one customer"),
        ("/files add · list · send", "The file library"),
    ]),
    "moderation": ("🛡️ Moderation", [
        ("/warn · /warnings · /timeout · /kick · /ban", "Actions on members"),
        ("/staff notes · note", "Private notes on a member"),
        ("/staff history @member", "Warnings, notes and automod strikes together"),
        ("/automod status", "Automatic moderation rules"),
        ("/purge · /slowmode · /lock", "Channel control (lock has an unlock option)"),
    ]),
    "giveaways": ("🎉 Giveaways", [
        ("/giveaway start", "Start one, with invite, message, role and age requirements"),
        ("/giveaway edit · end · reroll · cancel", "Manage a running or finished giveaway"),
        ("/giveaway config", "The ping role and default channel"),
        ("/invites check · leaderboard · add · reset", "Invite tracking"),
        ("/messages check · top", "Message counts"),
        ("/pingroles send", "Ping a role with an announcement"),
    ]),
    "content": ("📢 Posts and pages", [
        ("/post new · edit", "Posts with a big photo, a GIF and a file"),
        ("/rules · /boosterperks · /premiumperks · /links", "Editable pages (post and edit)"),
        ("/installguides add · edit · post", "Installation guides by category"),
        ("/clothingpreviews add · edit · post", "Clothing previews"),
        ("/copychannel", "Copy a channel into another"),
    ]),
    "logs": ("📋 Logs and tickets", [
        ("/logs setup · history · stats", "Set up and search the logs"),
        ("/ticket list · stats · claim", "Support tickets"),
        ("/reviews leaderboard", "Staff and product ratings"),
        ("/audit", "Review roles, permissions and channels"),
    ]),
}


def ensure_staff_sync(member: discord.Member, cfg) -> bool:
    perms = member.guild_permissions
    return bool(perms.manage_messages or perms.manage_guild or (cfg and is_staff(member, cfg)))


async def require_staff(interaction: discord.Interaction) -> None:
    cfg = await db.get_ticket_config(interaction.guild_id)
    if not ensure_staff_sync(interaction.user, cfg):
        raise UserError("This is for staff only.")


async def count(sql: str, params: tuple) -> int:
    return (await db.fetch_one(sql, params))["c"]


async def hub_stats(guild_id: int) -> dict:
    day = (discord.utils.utcnow() - timedelta(days=1)).isoformat()
    week = (discord.utils.utcnow() - timedelta(days=7)).isoformat()
    return {
        "tickets": await count("SELECT COUNT(*) AS c FROM tickets WHERE guild_id = ? AND status = 'open'", (guild_id,)),
        "giveaways": await count("SELECT COUNT(*) AS c FROM giveaways WHERE guild_id = ? AND status = 'active'", (guild_id,)),
        "orders": await count("SELECT COUNT(*) AS c FROM orders WHERE guild_id = ? AND created_at >= ?", (guild_id, day)),
        "attention": await count("SELECT COUNT(*) AS c FROM orders WHERE guild_id = ? AND (file_status = 'failed' OR role_status = 'failed' OR dm_sent = 0)", (guild_id,)),
        "automod": await count("SELECT COUNT(*) AS c FROM log_history WHERE guild_id = ? AND category = 'automod' AND created_at >= ?", (guild_id, day)),
        "warnings": await count("SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND created_at >= ?", (guild_id, week)),
    }


async def hub_embed(guild: discord.Guild) -> discord.Embed:
    s = await hub_stats(guild.id)
    body = ui.kv(
        ("🎫 Open tickets", f"{s['tickets']:,}"), ("🎉 Running giveaways", f"{s['giveaways']:,}"), ("🧾 Orders in the last 24h", f"{s['orders']:,}"),
        ("⚠️ Orders needing attention", f"**{s['attention']:,}**" if s["attention"] else "None ✅"), ("🤖 Automod actions (24h)", f"{s['automod']:,}"), ("⚖️ Warnings (7 days)", f"{s['warnings']:,}"),
    ) + "\n\nPick a section below to see its commands."
    return ui.card("🛠️ Staff hub", body, guild=guild, section="Staff")


async def section_embed(guild: discord.Guild, key: str) -> discord.Embed:
    title, commands_ = SECTIONS[key]
    body = "\n".join(f"`{cmd}`\n　{what}" for cmd, what in commands_)
    if key == "shop":
        rows = await db.fetch_all("SELECT code, product_name, user_id, file_status, role_status, dm_sent FROM orders WHERE guild_id = ? AND (file_status = 'failed' OR role_status = 'failed' OR dm_sent = 0) ORDER BY id DESC LIMIT 5", (guild.id,))
        if rows:
            lines = []
            for r in rows:
                why = ", ".join(x for x in ("DM failed" if not r["dm_sent"] else None, "file not delivered" if r["file_status"] == "failed" else None, "role not given" if r["role_status"] == "failed" else None) if x)
                lines.append(f"`{r['code']}` · <@{r['user_id']}> · {r['product_name']} · {why}")
            body += "\n\n**⚠️ Needs attention** (fix with `/shop resend`)\n" + "\n".join(lines)
    return ui.card(title, body, guild=guild, section="Staff")


class HubView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        self.guild = guild
        select = discord.ui.Select(placeholder="Open a section…", options=[discord.SelectOption(label=title.split(" ", 1)[1], value=key, emoji=title.split(" ", 1)[0]) for key, (title, _) in SECTIONS.items()] + [discord.SelectOption(label="Back to the overview", value="home", emoji="🏠")])
        select.callback = self.pick
        self.select = select
        self.add_item(select)

    async def pick(self, interaction: discord.Interaction):
        key = self.select.values[0]
        embed = await hub_embed(self.guild) if key == "home" else await section_embed(self.guild, key)
        await interaction.response.edit_message(embed=embed, view=self)


def can_manage_role(guild: discord.Guild, invoker: discord.Member, role: discord.Role) -> None:
    if role.is_default() or role.managed:
        raise UserError("I can't hand out that role (it's @everyone or belongs to a bot).")
    if not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role:
        raise UserError(f"{role.mention} is above my highest role. Drag **my role above it** in Server Settings → Roles.")
    if guild.owner_id != invoker.id and role >= invoker.top_role:
        raise UserError(f"You can only give roles that are **below your own** highest role.")
    risky = [p.replace("_", " ").title() for p in POWERFUL if getattr(role.permissions, p, False)]
    if risky and not invoker.guild_permissions.administrator:
        raise UserError(f"{role.mention} has staff powers ({', '.join(risky[:3])}). Only an administrator can hand it out.")


@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
class Staff(commands.GroupCog, group_name="staff", group_description="Staff tools: hub, customers, notes, history and roles"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(description="Open the staff hub (stats, shortcuts and everything needing attention)")
    async def menu(self, interaction: discord.Interaction):
        await require_staff(interaction)
        await interaction.response.send_message(embed=await hub_embed(interaction.guild), view=HubView(interaction.guild), ephemeral=True)

    @app_commands.command(description="Everything about one customer: orders, invites, activity and record")
    @app_commands.describe(member="The customer")
    async def customer(self, interaction: discord.Interaction, member: discord.Member):
        await require_staff(interaction)
        gid = interaction.guild_id
        orders = await db.fetch_all("SELECT * FROM orders WHERE guild_id = ? AND user_id = ? ORDER BY id DESC", (gid, member.id))
        invites = await db.invite_stats(gid, member.id)
        messages = await db.message_total(gid, member.id)
        warnings = await count("SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ?", (gid, member.id))
        notes = await count("SELECT COUNT(*) AS c FROM staff_notes WHERE guild_id = ? AND user_id = ?", (gid, member.id))
        tickets = await count("SELECT COUNT(*) AS c FROM tickets WHERE guild_id = ? AND user_id = ?", (gid, member.id))
        files = await count("SELECT COALESCE(SUM(d.count), 0) AS c FROM file_deliveries d JOIN stored_files f ON f.id = d.file_id WHERE f.guild_id = ? AND d.user_id = ?", (gid, member.id))
        lines = []
        for o in orders[:5]:
            how = "manual" if o["source"] == "manual" else "automatic"
            who = f" by <@{o['processed_by']}>" if o["processed_by"] else ""
            lines.append(f"`{o['code']}` · {o['product_name']} · {o['amount'] or '—'} · {how}{who} · {discord.utils.format_dt(discord.utils.parse_time(o['created_at']), 'd')}")
        body = ui.kv(
            ("👤 Member", f"{member.mention} (`{member.id}`)"), ("📅 Joined", discord.utils.format_dt(member.joined_at, "R") if member.joined_at else None),
            ("🧾 Orders", f"**{len(orders)}**"), ("📥 Files received", f"{files:,}" if files else None), ("📨 Valid invites", f"{invites['valid']:,}"), ("💬 Messages", f"{messages:,}"),
            ("🎫 Tickets", f"{tickets:,}" if tickets else None), ("⚖️ Warnings", f"**{warnings}**" if warnings else None), ("📝 Staff notes", f"{notes}" if notes else None),
        )
        if lines:
            body += "\n\n**Recent orders**\n" + "\n".join(lines)
        await interaction.response.send_message(embed=ui.card("🧑‍💼 Customer", body, guild=interaction.guild, thumbnail=member.display_avatar.url, section="Staff"), ephemeral=True)

    @app_commands.command(description="A member's record: warnings, notes and automod strikes together")
    @app_commands.describe(member="The member")
    async def history(self, interaction: discord.Interaction, member: discord.User):
        await require_staff(interaction)
        gid = interaction.guild_id
        events = []
        for w in await db.fetch_all("SELECT * FROM warnings WHERE guild_id = ? AND user_id = ?", (gid, member.id)):
            events.append((w["created_at"], f"⚖️ **Warning** `#{w['id']}` by <@{w['moderator_id']}>: {w['reason']}"))
        for n in await db.fetch_all("SELECT * FROM staff_notes WHERE guild_id = ? AND user_id = ?", (gid, member.id)):
            events.append((n["created_at"], f"📝 **Note** `#{n['id']}` by <@{n['author_id']}>: {n['text']}"))
        for h in await db.fetch_all("SELECT * FROM log_history WHERE guild_id = ? AND subject_id = ? AND category IN ('moderation', 'automod', 'staff')", (gid, member.id)):
            detail = " · ".join((h["summary"] or "").splitlines()[:3])[:110]
            events.append((h["created_at"], f"🛡️ **{h['title']}**" + (f" · {detail}" if detail else "")))
        if not events:
            raise UserError("Nothing on record for that member. They're clean. ✅")
        events.sort(key=lambda e: e[0], reverse=True)
        lines = [f"{discord.utils.format_dt(discord.utils.parse_time(t), 'd')} {text}" for t, text in events[:15]]
        await interaction.response.send_message(embed=ui.card(f"📚 Record: {member.display_name}", "\n".join(lines) + (f"\n\n*Showing 15 of {len(events)}.*" if len(events) > 15 else ""), guild=interaction.guild, section="Staff"), ephemeral=True)

    @app_commands.command(description="Add a private staff note to a member")
    @app_commands.describe(member="The member", text="The note (only staff can see it)")
    async def note(self, interaction: discord.Interaction, member: discord.User, text: app_commands.Range[str, 1, 500]):
        await require_staff(interaction)
        await db.execute("INSERT INTO staff_notes (guild_id, user_id, author_id, text, created_at) VALUES (?, ?, ?, ?, ?)", (interaction.guild_id, member.id, interaction.user.id, text, discord.utils.utcnow().isoformat()))
        note_id = (await db.fetch_one("SELECT id FROM staff_notes ORDER BY id DESC LIMIT 1"))["id"]
        await interaction.response.send_message(embed=ui.card("📝 Note added", f"Note `#{note_id}` saved for {member.mention}. Only staff can see it.", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "staff", "Staff note added", ui.kv(("👤 Member", member.mention), ("📝 Note", text[:200]), ("🛡️ By", interaction.user.mention), ("🆔 Note", f"`#{note_id}`")), subject=member.id)

    @app_commands.command(description="Read the staff notes on a member")
    @app_commands.describe(member="The member")
    async def notes(self, interaction: discord.Interaction, member: discord.User):
        await require_staff(interaction)
        rows = await db.fetch_all("SELECT * FROM staff_notes WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 15", (interaction.guild_id, member.id))
        if not rows:
            raise UserError("There are no notes on that member.")
        lines = [f"`#{r['id']}` {discord.utils.format_dt(discord.utils.parse_time(r['created_at']), 'd')} · <@{r['author_id']}>\n　{r['text']}" for r in rows]
        await interaction.response.send_message(embed=ui.card(f"📝 Notes: {member.display_name}", "\n".join(lines), guild=interaction.guild, section="Staff"), ephemeral=True)

    @app_commands.command(description="Delete a staff note by its number")
    @app_commands.describe(note_id="The note's number")
    async def delnote(self, interaction: discord.Interaction, note_id: int):
        await require_staff(interaction)
        row = await db.fetch_one("SELECT * FROM staff_notes WHERE id = ? AND guild_id = ?", (note_id, interaction.guild_id))
        if not row:
            raise UserError("I can't find a note with that number.")
        await db.execute("DELETE FROM staff_notes WHERE id = ?", (note_id,))
        await interaction.response.send_message(embed=ui.card("🗑️ Note deleted", f"Note `#{note_id}` was removed.", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "staff", "Staff note deleted", ui.kv(("👤 Member", f"<@{row['user_id']}>"), ("🆔 Note", f"`#{note_id}`"), ("🛡️ By", interaction.user.mention)), subject=row["user_id"])

    @app_commands.command(description="Give a member a role")
    @app_commands.describe(member="Who", role="Which role")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def giverole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        can_manage_role(interaction.guild, interaction.user, role)
        await member.add_roles(role, reason=f"By {interaction.user}")
        await interaction.response.send_message(embed=ui.card("✅ Role given", f"{member.mention} now has {role.mention}.", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "staff", "Role given by staff", ui.kv(("👤 Member", member.mention), ("🎭 Role", role.mention), ("🛡️ By", interaction.user.mention)), subject=member.id)

    @app_commands.command(description="Take a role away from a member")
    @app_commands.describe(member="Who", role="Which role")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def takerole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        can_manage_role(interaction.guild, interaction.user, role)
        await member.remove_roles(role, reason=f"By {interaction.user}")
        await interaction.response.send_message(embed=ui.card("✅ Role removed", f"{member.mention} no longer has {role.mention}.", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "staff", "Role taken by staff", ui.kv(("👤 Member", member.mention), ("🎭 Role", role.mention), ("🛡️ By", interaction.user.mention)), subject=member.id)

    @app_commands.command(description="Change (or clear) a member's nickname")
    @app_commands.describe(member="Who", nickname="The new nickname (leave blank to clear it)")
    @app_commands.checks.has_permissions(manage_nicknames=True)
    async def nick(self, interaction: discord.Interaction, member: discord.Member, nickname: Optional[app_commands.Range[str, 1, 32]] = None):
        before = member.nick
        try:
            await member.edit(nick=nickname, reason=f"By {interaction.user}")
        except discord.HTTPException:
            raise UserError("I couldn't change that nickname. My role has to be above theirs.") from None
        await interaction.response.send_message(embed=ui.card("✅ Nickname updated", f"{member.mention}: **{before or '(none)'}** → **{nickname or '(none)'}**", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "staff", "Nickname changed by staff", ui.kv(("👤 Member", member.mention), ("⬅️ Before", before or "(none)"), ("➡️ After", nickname or "(none)"), ("🛡️ By", interaction.user.mention)), subject=member.id)


@app_commands.guild_only()
class StaffTools(commands.Cog):
    """/staffsetup builds the staff area; /audit reviews roles, permissions and channels."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(description="Create the staff area: private text and voice channels for your team")
    @app_commands.describe(role="Your staff role (leave blank to create one called 'staff')", voice="Also create staff voice channels (default: yes)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def staffsetup(self, interaction: discord.Interaction, role: Optional[discord.Role] = None, voice: bool = True):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        created_role = False
        if role is None:
            role = next((r for r in guild.roles if r.name == "staff"), None)
            if role is None:
                try:
                    role = await guild.create_role(name="staff", colour=discord.Colour(0xE67E22), hoist=True, mentionable=True,
                                                   permissions=discord.Permissions(manage_messages=True, moderate_members=True), reason="Staff setup")
                    created_role = True
                except discord.Forbidden:
                    raise UserError("I need the **Manage Roles** permission to create the staff role.") from None
        if role.is_default() or role.managed:
            raise UserError("Pick a real staff role, not @everyone or a bot role.")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, connect=True, speak=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True, connect=True),
        }
        category = next((c for c in guild.categories if c.name == "🔒 STAFF"), None)
        if category is None:
            try:
                category = await guild.create_category("🔒 STAFF", overwrites=overwrites, reason="Staff setup")
            except discord.Forbidden:
                raise UserError("I need the **Manage Channels** permission to build the staff area.") from None
        made = []
        for name, topic in STAFF_TEXT:
            if not any(c.name == name for c in category.channels):
                await guild.create_text_channel(name, category=category, topic=topic, reason="Staff setup")
                made.append(f"💬 #{name}")
        if voice:
            for name in STAFF_VOICE:
                if not any(c.name == name for c in category.channels):
                    await guild.create_voice_channel(name, category=category, reason="Staff setup")
                    made.append(f"🔊 {name}")
        cfg = await db.get_ticket_config(guild.id)
        linked = ""
        if cfg is not None and not staff_ids(cfg):
            await db.upsert_ticket_config(guild.id, staff_roles=str(role.id))
            linked = f"\n\n🎫 {role.mention} is now also your ticket staff role."
        body = (f"Only {role.mention}, you and me can see **🔒 STAFF**." + (" (I created the role.)" if created_role else "")
                + "\n\n" + ("**New channels**\n" + "\n".join(made) if made else "Everything already existed, nothing new was needed.") + linked
                + "\n\nOpen the staff menu with `/staff menu`.")
        await interaction.followup.send(embed=ui.card("✅ Staff area ready", body, color=SUCCESS, guild=guild, section="Staff"), ephemeral=True)
        await emit(guild, "staff", "Staff area set up", ui.kv(("🛡️ By", interaction.user.mention), ("🎭 Staff role", role.mention), ("➕ Created", f"{len(made)} channels")), subject=interaction.user.id)

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.command(description="Review roles, permissions and channels and list anything risky (changes nothing)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def audit(self, interaction: discord.Interaction):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        problems, notes = 0, []
        me_perms = guild.me.guild_permissions
        missing = [p.replace("_", " ").title() for p in BOT_NEEDS if not getattr(me_perms, p, False)]
        notes.append(("🤖 My permissions", "✅ I have everything I need." if not missing else "❌ I'm missing: " + ", ".join(missing) + ". Re-invite me with the full permissions."))
        problems += bool(missing)
        everyone = guild.default_role.permissions
        bad_everyone = [p.replace("_", " ").title() for p in POWERFUL if getattr(everyone, p, False)]
        notes.append(("🌐 @everyone", "✅ No staff powers." if not bad_everyone else "❌ Everyone can: " + ", ".join(bad_everyone) + ". Turn these off in Server Settings → Roles → @everyone."))
        problems += bool(bad_everyone)
        admins, powerful = [], []
        for role in guild.roles:
            if role.is_default() or role.managed:
                continue
            perms = [p for p in POWERFUL if getattr(role.permissions, p, False)]
            members = len(getattr(role, "members", []))
            if getattr(role.permissions, "administrator", False):
                admins.append(f"{role.mention} ({members})")
            elif perms:
                powerful.append(f"{role.mention} ({members}): " + ", ".join(p.replace("_", " ") for p in perms[:3]))
        notes.append(("👑 Administrator roles", ("⚠️ " + ", ".join(admins) + "\nOnly your owner role should have this.") if admins else "✅ None."))
        problems += len(admins) > 1
        notes.append(("🛡️ Staff-power roles", ("ℹ️ " + "\n".join(powerful[:8])) if powerful else "✅ None besides administrators."))
        above = [r.mention for r in guild.roles if not r.is_default() and not r.managed and r >= guild.me.top_role][:8]
        notes.append(("📶 Roles above mine", ("⚠️ I can't hand these out: " + ", ".join(above) + "\nDrag my role higher if they should be given by the bot.") if above else "✅ Nothing above me that I need to manage."))
        exposed = [c.mention for c in guild.channels if any(w in c.name.lower() for w in STAFF_WORDS) and c.permissions_for(guild.default_role).view_channel][:8]
        notes.append(("🔒 Staff-looking channels everyone can see", ("❌ " + ", ".join(exposed) + "\nMake them private (Edit channel → Permissions → @everyone → View Channel ❌).") if exposed else "✅ None found."))
        problems += bool(exposed)
        sold = await db.fetch_all("SELECT name, role_id FROM products WHERE guild_id = ? AND role_id IS NOT NULL", (guild.id,))
        risky_sold = []
        for p in sold:
            role = guild.get_role(p["role_id"])
            if role is not None and any(getattr(role.permissions, x, False) for x in POWERFUL):
                risky_sold.append(f"**{p['name']}** gives {role.mention}")
        notes.append(("🛒 Roles sold in the shop", ("❌ " + "; ".join(risky_sold) + "\nA product should never hand out staff powers.") if risky_sold else f"✅ {len(sold)} sold role(s), none with staff powers." if sold else "✅ No roles are sold."))
        problems += bool(risky_sold)
        embed = ui.card("🔍 Server audit", "\n\n".join(f"**{name}**\n{text}" for name, text in notes), guild=guild, section="Audit",
                        footer=("Nothing was changed. " + (f"{problems} thing(s) need attention." if problems else "All clear.")))
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Staff(bot))
    await bot.add_cog(StaffTools(bot))
