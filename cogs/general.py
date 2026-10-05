from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from common import COLOR, INFO, UserError, check_can_send, parse_color

GROUP_TITLES = {"verify": "Verification", "welcome": "Welcome", "logs": "Logs"}
CATEGORY_META = {
    "Setup": ("🚀", "Get started quickly"),
    "Verification": ("✅", "Keep bots and raiders out"),
    "Welcome": ("👋", "Welcome cards, goodbyes and auto-roles"),
    "Logs": ("📋", "See everything that happens"),
    "Moderation": ("🛡️", "Kick, ban, timeout, warn and clean up"),
    "Info & tools": ("ℹ️", "Server info and handy extras"),
}
CATEGORY_ORDER = list(CATEGORY_META)


def category_of(cmd) -> str:
    if isinstance(cmd, app_commands.Group):
        return GROUP_TITLES.get(cmd.name, cmd.name.title())
    return {"Moderation": "Moderation", "Setup": "Setup"}.get(type(getattr(cmd, "binding", None)).__name__, "Info & tools")


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
