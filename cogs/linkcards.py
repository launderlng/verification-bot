import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import ACCENT, COLOR, UserError, parse_color

NAMES = {"pyrex": "Pyrex", "spotless": "Spotless"}
KEY_CHOICES = [app_commands.Choice(name=label, value=key) for key, label in NAMES.items()]
DISCORD_RE = re.compile(r"^https://(?:discord\.gg|(?:www\.)?discord(?:app)?\.com/invite)/[A-Za-z0-9-]{2,32}/?$", re.I)


def discord_link(url: str) -> str:
    url = url.strip()
    if not DISCORD_RE.match(url):
        raise UserError("That doesn't look like a Discord invite. It should look like `https://discord.gg/abcDEF`.")
    return url


def web_link(url: str) -> str:
    url = url.strip()
    if not url.startswith("https://") or " " in url or len(url) > 512:
        raise UserError("Links must start with `https://` and be under 512 characters.")
    return url


def build_card(guild: discord.Guild, key: str, row) -> tuple[discord.Embed, Optional[discord.ui.View]]:
    color = int(row["color"], 16) if row["color"] else ACCENT.value
    embed = ui.card(row["title"] or NAMES[key], row["description"] or None, color=color, guild=guild, section=NAMES[key], image=row["image_url"] or None)
    view = discord.ui.View(timeout=None)
    if row["link_url"]:
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=row["link_label"] or "Join Discord", emoji="💬", url=row["link_url"]))
    if row["link2_url"]:
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=row["link2_label"] or "Open link", emoji="🔗", url=row["link2_url"]))
    return embed, (view if view.children else None)


async def get_card(guild_id: int, key: str):
    return await db.fetch_one("SELECT * FROM link_cards WHERE guild_id = ? AND key = ?", (guild_id, key))


def is_ready(row) -> bool:
    return bool(row and (row["description"] or row["link_url"] or row["link2_url"] or row["title"]))


async def show(interaction: discord.Interaction, key: str, private: bool) -> None:
    row = await get_card(interaction.guild_id, key)
    if not is_ready(row):
        raise UserError(f"**{NAMES[key]}** isn't set up yet. An admin can add its Discord link with `/linkcard set which:{NAMES[key]}`.")
    embed, view = build_card(interaction.guild, key, row)
    kwargs = {"embed": embed, "ephemeral": private}
    if view is not None:
        kwargs["view"] = view
    await interaction.response.send_message(**kwargs)


@app_commands.guild_only()
class LinkShow(commands.Cog):
    """The member-facing /pyrex and /spotless commands."""

    @app_commands.command(description="Show the Pyrex info card and its Discord link")
    @app_commands.describe(private="Only show it to you (default: show it in the channel)")
    async def pyrex(self, interaction: discord.Interaction, private: bool = False):
        await show(interaction, "pyrex", private)

    @app_commands.command(description="Show the Spotless info card and its Discord link")
    @app_commands.describe(private="Only show it to you (default: show it in the channel)")
    async def spotless(self, interaction: discord.Interaction, private: bool = False):
        await show(interaction, "spotless", private)


class DescriptionModal(discord.ui.Modal):
    def __init__(self, key: str, current: Optional[str]):
        super().__init__(title=f"{NAMES[key]} description")
        self.key = key
        self.text = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=2000,
            placeholder="What members should know. Multiple lines are fine.", default=(current or "")[:2000] or None,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        await db.execute("INSERT OR IGNORE INTO link_cards (guild_id, key) VALUES (?, ?)", (interaction.guild_id, self.key))
        await db.execute("UPDATE link_cards SET description = ? WHERE guild_id = ? AND key = ?", (self.text.value.strip() or None, interaction.guild_id, self.key))
        await interaction.response.send_message(f"✅ Saved. Try `/{self.key}` to see it.", ephemeral=True)


@app_commands.guild_only()
class LinkCards(commands.GroupCog, group_name="linkcard", group_description="Set up the /pyrex and /spotless cards"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(description="Add or change the links and look of a card (only fill in what you want to change)")
    @app_commands.describe(
        which="Which card",
        discord_invite="Discord invite link, e.g. https://discord.gg/abcDEF",
        discord_label="Text on the Discord button (default: Join Discord)",
        extra_link="A second link (any https link)",
        extra_label="Text on the second button (default: Open link)",
        title="Card title (default: the name)",
        image_url="Banner image (direct https link, or 'none')",
        color="Card colour as hex, e.g. #5865F2",
    )
    @app_commands.choices(which=KEY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set(
        self,
        interaction: discord.Interaction,
        which: app_commands.Choice[str],
        discord_invite: Optional[str] = None,
        discord_label: Optional[app_commands.Range[str, 1, 30]] = None,
        extra_link: Optional[str] = None,
        extra_label: Optional[app_commands.Range[str, 1, 30]] = None,
        title: Optional[app_commands.Range[str, 1, 100]] = None,
        image_url: Optional[str] = None,
        color: Optional[str] = None,
    ):
        updates: dict = {}
        if discord_invite:
            updates["link_url"] = discord_link(discord_invite)
        if discord_label:
            updates["link_label"] = discord_label
        if extra_link:
            updates["link2_url"] = web_link(extra_link)
        if extra_label:
            updates["link2_label"] = extra_label
        if title:
            updates["title"] = title
        if image_url:
            updates["image_url"] = None if image_url.strip().lower() == "none" else web_link(image_url)
        if color:
            updates["color"] = f"{parse_color(color):06X}"
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option, for example `discord_invite`.")
        await db.execute("INSERT OR IGNORE INTO link_cards (guild_id, key) VALUES (?, ?)", (interaction.guild_id, which.value))
        assignments = ", ".join(f"{col} = ?" for col in updates)
        await db.execute(f"UPDATE link_cards SET {assignments} WHERE guild_id = ? AND key = ?", (*updates.values(), interaction.guild_id, which.value))
        row = await get_card(interaction.guild_id, which.value)
        embed, view = build_card(interaction.guild, which.value, row)
        kwargs = {"embed": embed, "ephemeral": True}
        if view is not None:
            kwargs["view"] = view
        await interaction.response.send_message(f"✅ **{which.name}** saved. This is how `/{which.value}` looks now:", **kwargs)

    @app_commands.command(description="Write a card's description in a pop-up (multiple lines)")
    @app_commands.describe(which="Which card")
    @app_commands.choices(which=KEY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def describe(self, interaction: discord.Interaction, which: app_commands.Choice[str]):
        row = await get_card(interaction.guild_id, which.value)
        await interaction.response.send_modal(DescriptionModal(which.value, row["description"] if row else None))

    @app_commands.command(description="See a card as members will see it (only you see this)")
    @app_commands.describe(which="Which card")
    @app_commands.choices(which=KEY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def preview(self, interaction: discord.Interaction, which: app_commands.Choice[str]):
        await show(interaction, which.value, True)

    @app_commands.command(description="Reset a card back to empty")
    @app_commands.describe(which="Which card")
    @app_commands.choices(which=KEY_CHOICES)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clear(self, interaction: discord.Interaction, which: app_commands.Choice[str]):
        await db.execute("DELETE FROM link_cards WHERE guild_id = ? AND key = ?", (interaction.guild_id, which.value))
        await interaction.response.send_message(f"🧹 **{which.name}** was reset.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LinkShow())
    await bot.add_cog(LinkCards(bot))
