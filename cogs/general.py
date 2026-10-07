import os
import sys
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from common import COLOR, INFO, UserError, check_can_send, parse_color

GROUP_TITLES = {"verify": "Verification", "welcome": "Welcome", "logs": "Logs", "shop": "Shop", "ticket": "Tickets", "template": "Templates", "reviews": "Reviews", "linkcard": "Links", "giveaway": "Giveaways", "files": "Files"}
CATEGORY_META = {
    "Setup": ("🚀", "Get started quickly"),
    "Verification": ("✅", "Keep bots and raiders out"),
    "Welcome": ("👋", "Welcome cards, goodbyes and auto-roles"),
    "Shop": ("🛒", "Sell products with a Stripe Buy button"),
    "Tickets": ("🎫", "Private support tickets with transcripts"),
    "Templates": ("🧩", "Copy and share server layouts"),
    "Reviews": ("⭐", "Star reviews and leaderboards"),
    "Giveaways": ("🎉", "Run giveaways with one-click entry"),
    "Posts": ("🖼️", "Post cards with a big photo, a GIF and a file"),
    "Files": ("📥", "Store files and send them to members by DM"),
    "Links": ("🔗", "Quick link cards like /pyrex and /spotless"),
    "Logs": ("📋", "See everything that happens"),
    "Moderation": ("🛡️", "Kick, ban, timeout, warn and clean up"),
    "Info & tools": ("ℹ️", "Server info and handy extras"),
}
CATEGORY_ORDER = list(CATEGORY_META)


def category_of(cmd) -> str:
    if isinstance(cmd, app_commands.Group):
        return GROUP_TITLES.get(cmd.name, cmd.name.title())
    return {"Moderation": "Moderation", "Setup": "Setup", "Posts": "Posts", "BuyCog": "Shop", "ReviewCog": "Reviews", "LinkShow": "Links"}.get(type(getattr(cmd, "binding", None)).__name__, "Info & tools")


def collect(bot: commands.Bot) -> dict[str, list]:
    cats: dict[str, list] = {}
    for cmd in bot.tree.get_commands():
        items = list(cmd.walk_commands()) if isinstance(cmd, app_commands.Group) else [cmd]
        cats.setdefault(category_of(cmd), []).extend(items)
    return {name: sorted(cats[name], key=lambda c: c.qualified_name) for name in CATEGORY_ORDER if name in cats}


def overview_embed(cats: dict[str, list]) -> discord.Embed:
    embed = discord.Embed(
        title="✅ Verification & Welcome Bot",
        description="Pick a category from the menu below to see its commands.\n\n**New here?** Run `/setup` to get everything going in one command.",
        color=COLOR,
    )
    for name, cmds in cats.items():
        emoji, blurb = CATEGORY_META[name]
        embed.add_field(name=f"{emoji} {name}", value=f"{blurb}\n`{len(cmds)}` commands")
    return embed


def category_embed(name: str, cmds: list) -> discord.Embed:
    emoji, blurb = CATEGORY_META[name]
    lines = [f"`/{c.qualified_name}`\n{c.description}" for c in cmds]
    return discord.Embed(title=f"{emoji} {name}", description=f"*{blurb}*\n\n" + "\n\n".join(lines), color=COLOR)


class HelpSelect(discord.ui.Select):
    def __init__(self, cats: dict[str, list]):
        options = [discord.SelectOption(label="Overview", value="overview", emoji="🏠", description="Start here")]
        for name in cats:
            emoji, blurb = CATEGORY_META[name]
            options.append(discord.SelectOption(label=name, value=name, emoji=emoji, description=blurb[:100]))
        super().__init__(placeholder="Choose a category…", options=options)
        self.cats = cats

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        embed = overview_embed(self.cats) if choice == "overview" else category_embed(choice, self.cats[choice])
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self, cats: dict[str, list]):
        super().__init__(timeout=300)
        self.add_item(HelpSelect(cats))


class AnnounceModal(discord.ui.Modal):
    def __init__(self, channel: discord.TextChannel, color: int, ping: str):
        super().__init__(title="New announcement")
        self.channel, self.color, self.ping = channel, color, ping
        self.title_input = discord.ui.TextInput(label="Title", max_length=200)
        self.body_input = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=3000)
        self.add_item(self.title_input)
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(title=self.title_input.value, description=self.body_input.value, color=self.color)
        embed.set_footer(text=f"Announced by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        content = {"everyone": "@everyone", "here": "@here"}.get(self.ping)
        mentions = discord.AllowedMentions(everyone=bool(content))
        try:
            await self.channel.send(content=content, embed=embed, allowed_mentions=mentions)
        except discord.HTTPException:
            return await interaction.response.send_message("I couldn't post in that channel. Check my permissions there.", ephemeral=True)
        await interaction.response.send_message(f"📣 Announcement posted in {self.channel.mention}.", ephemeral=True)


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Browse every command with an interactive menu")
    async def help_cmd(self, interaction: discord.Interaction):
        cats = collect(self.bot)
        await interaction.response.send_message(embed=overview_embed(cats), view=HelpView(cats), ephemeral=True)

    @app_commands.command(description="Check that every part of the bot loaded correctly (admins)")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def diagnose(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        bot = self.bot
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        modules = [f"{'✅' if status == 'ok' else '❌'} `{name}`: {status}" for name, status in getattr(bot, "extension_status", {}).items()]

        top_files = ["bot.py", "db.py", "common.py", "logutil.py", "stripeutil.py", "transcript.py", "templateutil.py", "presets.py", "ui.py", "giveawayutil.py", "statusutil.py", "welcomecard.py", "captcha.py"]
        missing = [f for f in top_files if not os.path.exists(os.path.join(base, f))]
        misplaced = [f"{n}.py" for n in ("verify", "welcome", "logs", "moderation", "shop", "tickets", "reviews", "giveaways", "files", "posts", "linkcards", "templates", "setup", "general") if os.path.exists(os.path.join(base, f"{n}.py"))]

        expected_tables = {
            "config": {"verified_role_id", "rules_text"}, "verifications": set(), "welcome_config": {"style", "bg_url"}, "log_routes": set(),
            "warnings": set(), "products": {"buy_url", "available", "file_id"}, "shop_settings": {"ticket_channel_id", "receipt_note"},
            "orders": {"code", "livemode"}, "ticket_config": {"staff_roles"}, "ticket_types": {"needs_invoice"}, "tickets": {"rating"}, "ticket_blacklist": set(), "reviews": {"stars", "kind", "staff_id", "ticket_id"}, "review_settings": {"require_purchase"}, "link_cards": {"link_url"}, "giveaways": {"required_role_id"}, "giveaway_entries": set(), "log_history": {"subject_id"}, "stored_files": {"data", "required_role_id"}, "file_deliveries": set(),
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

        embed = discord.Embed(title="🩺 Bot diagnosis", color=COLOR if all(s.endswith("ok") for s in getattr(bot, "extension_status", {}).values()) else INFO)
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
        embed.add_field(name="Commands", value=f"{total} registered · groups: {', '.join('/' + g for g in groups) or 'none'}\nSync: {bot.sync_status if hasattr(bot, 'sync_status') else 'unknown'}", inline=False)
        embed.add_field(name="Settings", value=f"Message Content Intent: {'✅ on' if bot.intents.message_content else 'off'}\nStripe webhook secret: {stripe}", inline=False)
        embed.set_footer(text=f"Python {sys.version.split()[0]} · discord.py {discord.__version__}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(description="Check the bot's latency")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🏓 Pong! `{round(self.bot.latency * 1000)}ms`", ephemeral=True)

    @app_commands.command(description="Show info about this server")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild
        humans = sum(1 for m in g.members if not m.bot)
        embed = discord.Embed(title=g.name, description=g.description, color=INFO)
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

    @app_commands.command(description="Show info about a member")
    @app_commands.describe(member="Who to look up (defaults to you)")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        guild = interaction.guild
        roles = [r.mention for r in reversed(member.roles) if not r.is_default()]
        by_join = sorted((m for m in guild.members if m.joined_at), key=lambda m: m.joined_at)
        position = by_join.index(member) + 1 if member in by_join else None
        warns = (await db.fetch_one("SELECT COUNT(*) AS c FROM warnings WHERE guild_id = ? AND user_id = ?", (guild.id, member.id)))["c"]

        embed = discord.Embed(title=str(member), color=member.color if member.color.value else INFO)
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
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="Show someone's avatar")
    @app_commands.describe(user="Whose avatar (defaults to you)")
    async def avatar(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        user = user or interaction.user
        embed = discord.Embed(title=f"{user.display_name}'s avatar", color=INFO)
        embed.set_image(url=user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(description="How many members are in the server?")
    @app_commands.guild_only()
    async def membercount(self, interaction: discord.Interaction):
        g = interaction.guild
        humans = sum(1 for m in g.members if not m.bot)
        await interaction.response.send_message(f"👥 **{g.member_count:,}** members in **{g.name}** ({humans:,} people, {g.member_count - humans:,} bots).")

    @app_commands.command(description="Post a nicely formatted announcement")
    @app_commands.describe(channel="Where to post it", color="Colour as hex, e.g. #5865F2", ping="Ping everyone or here (needs permission)")
    @app_commands.choices(ping=[
        app_commands.Choice(name="No ping", value="none"),
        app_commands.Choice(name="@here", value="here"),
        app_commands.Choice(name="@everyone", value="everyone"),
    ])
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def announce(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        color: Optional[str] = None,
        ping: Optional[app_commands.Choice[str]] = None,
    ):
        check_can_send(channel, interaction.guild.me)
        value = parse_color(color) if color else COLOR.value
        if ping and ping.value != "none" and not interaction.user.guild_permissions.mention_everyone:
            raise UserError("You need the **Mention Everyone** permission to ping @here or @everyone.")
        await interaction.response.send_modal(AnnounceModal(channel, value, ping.value if ping else "none"))


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
