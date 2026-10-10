import os
import sys
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import COLOR, INFO, UserError, check_can_send, parse_color

GROUP_CATEGORY = {
    "verify": "Verification", "welcome": "Welcome", "logs": "Logs", "shop": "Shop", "ticket": "Tickets", "template": "Templates", "reviews": "Reviews",
    "giveaway": "Giveaways", "files": "Files", "serverlock": "Server lock", "rules": "Server pages", "boosterperks": "Server pages",
    "premiumperks": "Server pages", "links": "Server pages", "installguides": "Guides and previews", "clothingpreviews": "Guides and previews",
    "invites": "Invites and activity", "messages": "Invites and activity", "pingroles": "Ping roles", "automod": "Automod", "staff": "Staff tools", "post": "Posts",
}
COG_CATEGORY = {
    "Moderation": "Moderation", "Setup": "Setup", "BuyCog": "Shop", "ReviewCog": "Reviews", "StaffTools": "Staff tools", "CopyChannel": "Posts",
    "Cleanup": "Staff tools", "GifCreator": "Fun and tools",
}
CATEGORY_META = {
    "Setup": ("🚀", "Get started quickly"),
    "Verification": ("✅", "Keep bots and raiders out"),
    "Welcome": ("👋", "Welcome cards, goodbyes and auto-roles"),
    "Shop": ("🛒", "Products, orders, receipts and files"),
    "Tickets": ("🎫", "Private support tickets with transcripts"),
    "Reviews": ("⭐", "Star reviews and leaderboards"),
    "Giveaways": ("🎉", "Giveaways with entry requirements"),
    "Invites and activity": ("📈", "Invite tracking and message counts"),
    "Ping roles": ("🔔", "Opt-in roles for announcements and drops"),
    "Server pages": ("📋", "Rules, perks and links you can edit"),
    "Guides and previews": ("📚", "Install guides and clothing previews"),
    "Posts": ("📢", "Posts with a big photo, a GIF and a file"),
    "Files": ("📥", "Store files and send them by DM"),
    "Fun and tools": ("🎞️", "Make GIFs and other extras"),
    "Moderation": ("🛡️", "Kick, ban, timeout, warn and clean up"),
    "Automod": ("🤖", "Automatic moderation for text channels"),
    "Staff tools": ("🛠️", "Staff hub, customers, notes, audit and cleanup"),
    "Logs": ("📋", "See everything that happens"),
    "Templates": ("🧩", "Copy and share server layouts"),
    "Server lock": ("🔒", "Make the bot private to your server"),
    "Info & tools": ("ℹ️", "Server info and handy extras"),
}
FALLBACK_META = ("📦", "More commands")
CATEGORY_ORDER = list(CATEGORY_META)
MAX_EMBED = 3900


def is_staff_cmd(cmd, parent=None) -> bool:
    """Staff commands need a permission to run: they're marked 🛡️ in /help."""
    for item in (cmd, parent):
        if item is not None and getattr(item, "default_permissions", None) is not None:
            return True
    return any("has_permissions" in getattr(check, "__qualname__", "") for check in getattr(cmd, "checks", []))


def category_of(cmd) -> str:
    if isinstance(cmd, app_commands.Group):
        return GROUP_CATEGORY.get(cmd.name, cmd.name.replace("_", " ").title())
    return COG_CATEGORY.get(type(getattr(cmd, "binding", None)).__name__, "Info & tools")


def collect(bot: commands.Bot) -> dict[str, list]:
    """Every registered command, grouped by category: {category: [(qualified name, description, staff?, command), …]}. Nothing is left out."""
    cats: dict[str, list] = {}
    for top in bot.tree.get_commands():
        items = list(top.walk_commands()) if isinstance(top, app_commands.Group) else [top]
        rows = [(c.qualified_name, c.description, is_staff_cmd(c, top if isinstance(top, app_commands.Group) else None), c) for c in items]
        cats.setdefault(category_of(top), []).extend(rows)
    ordered = {name: sorted(cats[name], key=lambda r: r[0]) for name in CATEGORY_ORDER if name in cats}
    for name in sorted(set(cats) - set(ordered)):  # a category I haven't described yet still shows up
        ordered[name] = sorted(cats[name], key=lambda r: r[0])
    return ordered


def meta(name: str) -> tuple:
    return CATEGORY_META.get(name, FALLBACK_META)


def overview_embed(cats: dict[str, list]) -> discord.Embed:
    everyone = [(n, rows) for n, rows in cats.items() if any(not r[2] for r in rows)]
    staff_only = [(n, rows) for n, rows in cats.items() if all(r[2] for r in rows)]
    total = sum(len(rows) for rows in cats.values())
    embed = ui.card("📖 Help", f"**{total}** commands in **{len(cats)}** categories. Pick one from the menu below, or run `/help command:name` for the details of one command.\n"
                    "🛡️ marks commands only staff can use.\n\n**New here?** Run `/setup` to get everything going in one command.", color=COLOR, section="Info")
    for name, rows in everyone:
        emoji, blurb = meta(name)
        free = sum(1 for r in rows if not r[2])
        embed.add_field(name=f"{emoji} {name}", value=f"{blurb}\n`{len(rows)}` commands" + (f" · `{free}` for everyone" if free != len(rows) else ""))
    if staff_only:
        embed.add_field(name="🛡️ Staff only", value="\n".join(f"{meta(n)[0]} {n} (`{len(r)}`)" for n, r in staff_only), inline=False)
    return embed


def category_embeds(name: str, rows: list) -> discord.Embed:
    emoji, blurb = meta(name)
    lines, size = [], 0
    for qn, desc, staff, _ in rows:
        line = f"{'🛡️ ' if staff else ''}`/{qn}`\n{desc}"
        if size + len(line) > MAX_EMBED:
            lines.append(f"*…and {len(rows) - len(lines)} more. Use `/help command:name` for any of them.*")
            break
        lines.append(line)
        size += len(line) + 2
    return ui.card(f"{emoji} {name}", f"*{blurb}*\n\n" + "\n\n".join(lines), color=COLOR, section="Info")


def command_embed(cats: dict[str, list], wanted: str) -> discord.Embed:
    wanted = wanted.strip().lstrip("/").lower()
    for name, rows in cats.items():
        for qn, desc, staff, cmd in rows:
            if qn.lower() == wanted:
                params = getattr(cmd, "parameters", [])
                usage = f"/{qn}" + "".join(f" {'<' if p.required else '['}{p.name}{'>' if p.required else ']'}" for p in params)
                lines = [f"`{p.name}` {'(required)' if p.required else '(optional)'}: {p.description or '—'}" for p in params]
                body = f"{desc}\n\n**Usage**\n`{usage}`" + ("\n\n**Options**\n" + "\n".join(lines) if lines else "")
                if staff:
                    body += "\n\n🛡️ **Staff only.**"
                return ui.card(f"{meta(name)[0]} /{qn}", body[:4000], color=COLOR, section="Info")
    raise UserError(f"I can't find a command called `/{wanted}`. Start typing in the box to pick one from the list.")


class HelpSelect(discord.ui.Select):
    def __init__(self, cats: dict[str, list]):
        options = [discord.SelectOption(label="Overview", value="overview", emoji="🏠", description="Start here")]
        for name, rows in list(cats.items())[:24]:
            emoji, blurb = meta(name)
            options.append(discord.SelectOption(label=name, value=name, emoji=emoji, description=f"{blurb} ({len(rows)})"[:100]))
        super().__init__(placeholder="Choose a category…", options=options)
        self.cats = cats

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        embed = overview_embed(self.cats) if choice == "overview" else category_embeds(choice, self.cats[choice])
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self, cats: dict[str, list]):
        super().__init__(timeout=300)
        self.add_item(HelpSelect(cats))


async def help_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    names = [qn for rows in collect(interaction.client).values() for qn, *_ in rows if current.lower().lstrip("/") in qn.lower()]
    return [app_commands.Choice(name=f"/{n}"[:100], value=n) for n in sorted(names)[:25]]


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Browse every command, or look one up")
    @app_commands.describe(command="Look up one command, e.g. shop createorder")
    @app_commands.autocomplete(command=help_autocomplete)
    async def help_cmd(self, interaction: discord.Interaction, command: Optional[str] = None):
        cats = collect(self.bot)
        if command:
            return await interaction.response.send_message(embed=command_embed(cats, command), ephemeral=True)
        await interaction.response.send_message(embed=overview_embed(cats), view=HelpView(cats), ephemeral=True)

    @app_commands.command(description="Check that every part of the bot loaded correctly (admins)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def diagnose(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        bot = self.bot
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        modules = [f"{'✅' if status == 'ok' else '❌'} `{name}`: {status}" for name, status in getattr(bot, "extension_status", {}).items()]

        top_files = ["bot.py", "db.py", "common.py", "logutil.py", "stripeutil.py", "transcript.py", "templateutil.py", "presets.py", "ui.py", "giveawayutil.py", "statusutil.py", "fileutil.py", "guildlock.py", "welcomecard.py", "captcha.py"]
        missing = [f for f in top_files if not os.path.exists(os.path.join(base, f))]
        misplaced = [f"{n}.py" for n in ("verify", "welcome", "logs", "moderation", "shop", "tickets", "reviews", "giveaways", "files", "serverlock", "copychannel", "posts", "pages", "invites", "activity", "pingroles", "automod", "staff", "gifcreator", "cleanup", "templates", "setup", "general") if os.path.exists(os.path.join(base, f"{n}.py"))]

        expected_tables = {
            "config": {"verified_role_id", "rules_text"}, "verifications": set(), "welcome_config": {"style", "bg_url", "banner_mode"}, "log_routes": set(),
            "warnings": set(), "products": {"buy_url", "available", "file_id", "role_id"}, "shop_settings": {"ticket_channel_id", "order_prefix", "order_style", "order_counter"},
            "orders": {"code", "file_status", "source", "role_status", "processed_by", "fulfillment"}, "ticket_config": {"staff_roles"}, "ticket_types": {"needs_invoice"}, "tickets": {"rating"}, "ticket_blacklist": set(), "reviews": {"stars", "kind", "staff_id", "ticket_id"}, "review_settings": {"require_purchase"}, "giveaways": {"required_role_id", "min_invites", "ping_role_id"}, "giveaway_entries": set(), "log_history": {"subject_id"}, "stored_files": {"data", "required_role_id", "path"}, "allowed_guilds": {"guild_id"}, "file_deliveries": set(), "pages": {"image_file_id"}, "invite_joins": {"inviter_id"}, "message_counts": {"count"}, "ping_roles": {"role_id"}, "automod_settings": {"enabled"}, "staff_notes": {"text"}, "posts": {"message_id"},
            "licenses": {"code", "expires_at", "user_id"}, "license_events": set(), "auth_sessions": {"user_id", "linked_at"},
        }
        db_problems = []
        existing = {r["name"] for r in await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for table, cols in expected_tables.items():
            if table not in existing:
                db_problems.append(f"missing table `{table}`")
                continue
            have = {r["name"] for r in await db.fetch_all(f"PRAGMA table_info({table})")}
            if cols - have:
                db_problems.append(f"`{table}` is missing columns: {', '.join(sorted(cols - have))}")

        groups = sorted(c.name for c in bot.tree.get_commands() if isinstance(c, app_commands.Group))
        total = sum(len(list(c.walk_commands())) if isinstance(c, app_commands.Group) else 1 for c in bot.tree.get_commands())
        stripe = "✅ set" if os.getenv("STRIPE_WEBHOOK_SECRET") else "not set (purchase DMs off)"

        embed = ui.card("🩺 Bot diagnosis", color=COLOR if all(s.endswith("ok") for s in getattr(bot, "extension_status", {}).values()) else INFO, section="Info")
        embed.add_field(name="Modules", value="\n".join(modules) or "No information", inline=False)
        if missing:
            embed.add_field(name="❌ Missing files (top level of your repo)", value=", ".join(f"`{f}`" for f in missing), inline=False)
        if misplaced:
            embed.add_field(name="⚠️ Files in the wrong place", value=", ".join(f"`{f}`" for f in misplaced) + "\nThese belong inside the `cogs` folder.", inline=False)
        embed.add_field(name="Database", value="✅ All tables and columns are present" if not db_problems else "❌ " + "; ".join(db_problems), inline=False)
        db_path = os.path.abspath(db.DB_PATH)
        on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
        if on_railway and not db_path.startswith("/data"):
            storage = f"⚠️ `{db_path}` is **not** on your Railway volume, so your data (settings, tickets, shop, stored files) can be wiped on every redeploy.\nFix: in Railway → Variables add `DB_PATH` = `/data/verification.db` (with the volume mounted at `/data`), then redeploy."
        else:
            storage = f"✅ `{db_path}`" + (" (on the Railway volume)" if on_railway else "")
        embed.add_field(name="💾 Where your data is saved", value=storage, inline=False)
        from guildlock import lock_enabled
        embed.add_field(
            name="🔒 Private mode",
            value="✅ On: the bot only stays in servers you approved" if await lock_enabled()
            else "⚠️ Off: anyone who can invite this bot can add it to other servers. Run `/serverlock on` in your server and turn off **Public Bot** in the Developer Portal.",
            inline=False,
        )
        embed.add_field(name="🏓 Latency", value=f"{round(bot.latency * 1000)} ms" if bot.latency == bot.latency else "unknown", inline=True)
        embed.add_field(name="Commands", value=f"{total} registered · groups: {', '.join('/' + g for g in groups) or 'none'}\nSync: {bot.sync_status if hasattr(bot, 'sync_status') else 'unknown'}", inline=False)
        embed.add_field(name="Settings", value=f"Message Content Intent: {'✅ on' if bot.intents.message_content else 'off'}\nStripe webhook secret: {stripe}", inline=False)
        embed.set_footer(text=f"Python {sys.version.split()[0]} · discord.py {discord.__version__}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(description="Show info about this server, including its member count")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild
        humans = sum(1 for m in g.members if not m.bot)
        embed = ui.card(g.name, g.description, color=INFO, section="Info")
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        if g.banner:
            embed.set_image(url=g.banner.url)
        embed.add_field(name="👑 Owner", value=g.owner.mention if g.owner else "Unknown")
        embed.add_field(name="📅 Created", value=discord.utils.format_dt(g.created_at, "D"))
        embed.add_field(name="🔐 Verification level", value=str(g.verification_level).title())
        embed.add_field(name="👥 Members", value=f"{g.member_count:,}\n{humans:,} people · {g.member_count - humans:,} bots")
        embed.add_field(name="💬 Channels", value=f"{len(g.text_channels)} text · {len(g.voice_channels)} voice\n{len(g.categories)} categories")
        embed.add_field(name="🎭 Roles & emoji", value=f"{len(g.roles) - 1} roles · {len(g.emojis)} emoji")
        embed.add_field(name="🚀 Boosts", value=f"{g.premium_subscription_count} (level {g.premium_tier})")
        embed.set_footer(text=f"ID: {g.id}")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Show info about a member, including their avatar")
    @app_commands.describe(member="Who to look up (defaults to you)")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        guild = interaction.guild
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        by_join = sorted((m for m in guild.members if m.joined_at), key=lambda m: m.joined_at)
        position = by_join.index(member) + 1 if member in by_join else None
        warns = (await db.fetch_one("SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ?", (guild.id, member.id)))["c"]

        embed = ui.card(str(member), color=member.color if member.color.value else INFO, section="Info")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="📅 Account created", value=discord.utils.format_dt(member.created_at, "R"))
        embed.add_field(name="📥 Joined", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Unknown")
        embed.add_field(name="🔢 Join position", value=f"#{position:,}" if position else "Unknown")
        embed.add_field(name="🏅 Top role", value=member.top_role.mention if not member.top_role.is_default() else "None")
        if member.guild_permissions.administrator:
            embed.add_field(name="🔑 Key permission", value="Administrator")
        if member.timed_out_until:
            embed.add_field(name="🔇 Timed out until", value=discord.utils.format_dt(member.timed_out_until, "f"))
        if warns:
            embed.add_field(name="⚠️ Warnings", value=str(warns))
        embed.add_field(name=f"🎭 Roles ({len(roles)})", value=" ".join(roles[:20]) or "None", inline=False)
        embed.set_footer(text=f"ID: {member.id}")
        avatar_view = discord.ui.View()
        avatar_view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Open avatar", emoji="🖼️", url=member.display_avatar.url))
        await interaction.response.send_message(embed=embed, view=avatar_view)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
