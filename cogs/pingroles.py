import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import SUCCESS, WARN, UserError
from logutil import emit

log = logging.getLogger("verification-bot")

# key, emoji, name, what it's for, colour
DEFAULTS = [
    ("major", "📢", "Major Announcements", "Big news, launches and drops", 0xE74C3C),
    ("mini", "🔔", "Mini Announcements", "Small updates and reminders", 0x3498DB),
    ("giveaways", "🎉", "Giveaways", "Every new giveaway", 0xF1C40F),
    ("reshades", "🎨", "New ReShades", "Every new ReShade drop", 0xE080C0),
    ("rz", "🩸", "New RZ Releases", "New RZ packs and updates", 0xC0392B),
    ("boosters", "💎", "Booster Updates", "Updates for server boosters", 0xF47FFF),
    ("updates", "🛠️", "Server Updates", "Changes to the server and the bot", 0x95A5A6),
]
KEY_RE = re.compile(r"^[a-z0-9-]{2,20}$")


async def rows(guild_id: int) -> list:
    return await db.fetch_all("SELECT * FROM ping_roles WHERE guild_id = ? ORDER BY position, key", (guild_id,))


class PingToggle(discord.ui.DynamicItem[discord.ui.Button], template=r"pingrole:(?P<key>[a-z0-9-]+)"):
    """One button per ping role. Press to get the role, press again to give it back."""

    def __init__(self, key: str, label: str = "", emoji: Optional[str] = None):
        super().__init__(discord.ui.Button(label=label or key, emoji=emoji or None, style=discord.ButtonStyle.secondary, custom_id=f"pingrole:{key}"))
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(match["key"])

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("PingRoles")
        if cog is None:
            return await interaction.response.send_message("Ping roles aren't available right now.", ephemeral=True)
        await cog.toggle(interaction, self.key)


def panel_view(items: list) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for r in items[:25]:
        view.add_item(PingToggle(r["key"], r["name"], r["emoji"]))
    return view


def panel_embed(guild: discord.Guild, items: list) -> discord.Embed:
    lines = [f"{r['emoji'] or '🔔'} **{r['name']}**\n　{r['description'] or ''}" for r in items]
    return ui.card("🔔 Pick your pings", "Press a button to get pinged for that. Press it again to stop.\n\n" + "\n".join(lines), guild=guild, section="Ping roles")


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class PingRoles(commands.GroupCog, group_name="pingroles", group_description="Roles people can pick to be pinged for announcements and drops"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def cog_load(self):
        self.bot.add_dynamic_items(PingToggle)

    async def cog_unload(self):
        try:
            self.bot.remove_dynamic_items(PingToggle)
        except Exception:
            pass

    # ------------------------------------------------------------- helpers ----

    async def make_role(self, guild: discord.Guild, name: str, color: int = 0) -> discord.Role:
        try:
            return await guild.create_role(name=name, colour=discord.Colour(color), mentionable=True, reason="Ping role")
        except discord.Forbidden:
            raise UserError("I need the **Manage Roles** permission to create the ping roles.") from None

    async def refresh_panel(self, guild: discord.Guild) -> bool:
        panel = await db.fetch_one("SELECT * FROM ping_panel WHERE guild_id = ?", (guild.id,))
        channel = guild.get_channel(panel["channel_id"]) if panel and panel["channel_id"] else None
        if channel is None or not panel["message_id"]:
            return False
        items = await rows(guild.id)
        try:
            message = await channel.fetch_message(panel["message_id"])
            await message.edit(embed=panel_embed(guild, items), view=panel_view(items))
            return True
        except discord.HTTPException:
            return False

    async def post_panel(self, guild: discord.Guild, channel: discord.TextChannel):
        items = await rows(guild.id)
        message = await channel.send(embed=panel_embed(guild, items), view=panel_view(items))
        await db.execute(
            "INSERT INTO ping_panel (guild_id, channel_id, message_id) VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id, message_id = excluded.message_id",
            (guild.id, channel.id, message.id),
        )
        return message

    async def toggle(self, interaction: discord.Interaction, key: str) -> None:
        guild, member = interaction.guild, interaction.user
        row = await db.fetch_one("SELECT * FROM ping_roles WHERE guild_id = ? AND key = ?", (guild.id, key))
        role = guild.get_role(row["role_id"]) if row and row["role_id"] else None
        if role is None:
            return await interaction.response.send_message(embed=ui.card("Not available", "That ping role doesn't exist any more.", color=WARN), ephemeral=True)
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Ping role toggled off")
                card = ui.card("🔕 Ping removed", f"You won't be pinged for **{row['name']}** any more.", color=WARN)
            else:
                await member.add_roles(role, reason="Ping role toggled on")
                card = ui.card("🔔 You're on the list", f"You'll now be pinged for **{row['name']}**.", color=SUCCESS)
        except discord.HTTPException:
            card = ui.card("⚠️ I couldn't change that", "My role has to be **above** the ping roles. Please tell a staff member.", color=WARN)
        await interaction.response.send_message(embed=card, ephemeral=True)

    # ------------------------------------------------------------ commands ----

    @app_commands.command(description="Create all the ping roles (and the sign-up panel) in one go")
    @app_commands.describe(channel="Post the sign-up panel here")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        made = 0
        for position, (key, emoji, name, desc, color) in enumerate(DEFAULTS):
            row = await db.fetch_one("SELECT * FROM ping_roles WHERE guild_id = ? AND key = ?", (guild.id, key))
            role = guild.get_role(row["role_id"]) if row and row["role_id"] else None
            if role is None:
                role = await self.make_role(guild, f"{emoji} {name}", color)
                made += 1
            await db.execute(
                "INSERT INTO ping_roles (guild_id, key, name, role_id, emoji, description, position) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, key) DO UPDATE SET role_id = excluded.role_id", (guild.id, key, name, role.id, emoji, desc, position),
            )
        giveaway_role = (await db.fetch_one("SELECT role_id FROM ping_roles WHERE guild_id = ? AND key = 'giveaways'", (guild.id,)))["role_id"]
        await db.execute("INSERT OR IGNORE INTO giveaway_settings (guild_id) VALUES (?)", (guild.id,))
        await db.execute("UPDATE giveaway_settings SET ping_role_id = ? WHERE guild_id = ? AND ping_role_id IS NULL", (giveaway_role, guild.id))
        note = ""
        if channel:
            message = await self.post_panel(guild, channel)
            note = f"\n\nThe sign-up panel is live in {channel.mention}: [jump to it]({message.jump_url})."
        else:
            await self.refresh_panel(guild)
            note = "\n\nUse `/pingroles panel` to post the sign-up panel in a channel."
        items = await rows(guild.id)
        body = f"**{len(items)}** ping roles are ready ({made} new).\n\n" + "\n".join(f"{r['emoji']} <@&{r['role_id']}>" for r in items) + "\n\n🎉 The **Giveaways** role is now pinged automatically when a giveaway starts." + note
        await interaction.followup.send(embed=ui.card("✅ Ping roles ready", body, color=SUCCESS, guild=guild, section="Ping roles"), ephemeral=True)

    @app_commands.command(description="Post (or refresh) the sign-up panel where members pick their pings")
    @app_commands.describe(channel="Where to post the panel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await rows(interaction.guild_id):
            raise UserError("Run `/pingroles setup` first to create the roles.")
        await interaction.response.defer(ephemeral=True)
        message = await self.post_panel(interaction.guild, channel)
        await interaction.followup.send(embed=ui.card("✅ Panel posted", f"[Jump to it]({message.jump_url})", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Add your own ping category (like 'new-clothing')")
    @app_commands.describe(key="Short id, lowercase, e.g. clothing", name="Display name, e.g. New Clothing", emoji="An emoji for its button", description="What it's for", role="Use an existing role (otherwise one is created)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(self, interaction: discord.Interaction, key: app_commands.Range[str, 2, 20], name: app_commands.Range[str, 1, 40], emoji: Optional[str] = None,
                  description: Optional[app_commands.Range[str, 1, 100]] = None, role: Optional[discord.Role] = None):
        key = key.lower().strip()
        if not KEY_RE.match(key):
            raise UserError("The id can only use lowercase letters, numbers and dashes (2 to 20 characters).")
        guild = interaction.guild
        if await db.fetch_one("SELECT 1 FROM ping_roles WHERE guild_id = ? AND key = ?", (guild.id, key)):
            raise UserError(f"There's already a ping category called `{key}`.")
        if len(await rows(guild.id)) >= 25:
            raise UserError("You can have up to 25 ping roles.")
        await interaction.response.defer(ephemeral=True)
        if role is None:
            role = await self.make_role(guild, f"{emoji + ' ' if emoji else ''}{name}")
        position = len(await rows(guild.id))
        await db.execute("INSERT INTO ping_roles (guild_id, key, name, role_id, emoji, description, position) VALUES (?, ?, ?, ?, ?, ?, ?)", (guild.id, key, name.strip(), role.id, emoji, description, position))
        updated = await self.refresh_panel(guild)
        await interaction.followup.send(embed=ui.card("✅ Ping category added", f"**{name}** uses {role.mention}." + (" The panel was updated." if updated else ""), color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Remove a ping category")
    @app_commands.describe(key="Which one", delete_role="Also delete its role")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, key: str, delete_role: bool = False):
        guild = interaction.guild
        row = await db.fetch_one("SELECT * FROM ping_roles WHERE guild_id = ? AND key = ?", (guild.id, key.lower().strip()))
        if not row:
            raise UserError(f"There's no ping category called `{key}`. Use `/pingroles list` to see them.")
        await interaction.response.defer(ephemeral=True)
        role = guild.get_role(row["role_id"]) if row["role_id"] else None
        if delete_role and role is not None:
            try:
                await role.delete(reason="Ping role removed")
            except discord.HTTPException:
                pass
        await db.execute("DELETE FROM ping_roles WHERE guild_id = ? AND key = ?", (guild.id, row["key"]))
        await self.refresh_panel(guild)
        await interaction.followup.send(embed=ui.card("🗑️ Removed", f"**{row['name']}** was removed" + (" and its role deleted." if delete_role else "."), color=SUCCESS), ephemeral=True)

    @app_commands.command(description="See the ping roles and how many members have each")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        items = await rows(interaction.guild_id)
        if not items:
            raise UserError("No ping roles yet. Run `/pingroles setup`.")
        lines = []
        for r in items:
            role = interaction.guild.get_role(r["role_id"]) if r["role_id"] else None
            count = len(getattr(role, "members", [])) if role else 0
            lines.append(f"{r['emoji'] or '🔔'} `{r['key']}` · {role.mention if role else '*role deleted*'} · **{count:,}** members")
        await interaction.response.send_message(embed=ui.card("🔔 Ping roles", "\n".join(lines), guild=interaction.guild, section="Ping roles"), ephemeral=True)

    @app_commands.command(description="Send an announcement that pings one of the ping roles")
    @app_commands.describe(category="Who to ping", message="The announcement text", title="A headline", channel="Where to send it (default: here)", image_url="A direct https link to a picture")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def send(self, interaction: discord.Interaction, category: str, message: app_commands.Range[str, 1, 3500], title: Optional[app_commands.Range[str, 1, 200]] = None,
                   channel: Optional[discord.TextChannel] = None, image_url: Optional[str] = None):
        guild = interaction.guild
        row = await db.fetch_one("SELECT * FROM ping_roles WHERE guild_id = ? AND key = ?", (guild.id, category.lower().strip()))
        if not row:
            raise UserError(f"There's no ping category called `{category}`. Use `/pingroles list` to see them.")
        role = guild.get_role(row["role_id"]) if row["role_id"] else None
        if role is None:
            raise UserError("That ping role was deleted. Run `/pingroles setup` to recreate it.")
        if image_url and not image_url.startswith("https://"):
            raise UserError("The image must be a direct link starting with `https://`.")
        if not (role.mentionable or guild.me.guild_permissions.mention_everyone):
            raise UserError(f"I can't ping {role.mention} because it isn't mentionable. Turn on *Allow anyone to @mention this role*.")
        target = channel or interaction.channel
        card = ui.card(f"{row['emoji'] or '🔔'}  {title or row['name']}", message, guild=guild, image=image_url, footer=f"{row['name']} · {guild.name}")
        await target.send(content=role.mention, embed=card, allowed_mentions=discord.AllowedMentions(roles=[role], users=False, everyone=False))
        await interaction.response.send_message(embed=ui.card("✅ Sent", f"Pinged {role.mention} in {target.mention}.", color=SUCCESS), ephemeral=True)
        await emit(guild, "posts", "Announcement sent", ui.kv(("🔔 Pinged", role.mention), ("📍 Channel", target.mention), ("📝 Title", title or row["name"]), ("🛡️ By", interaction.user.mention)), subject=interaction.user.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(PingRoles(bot))
