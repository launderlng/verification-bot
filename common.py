import re

import discord

COLOR = discord.Color.from_rgb(43, 45, 49)       # neutral dark: blends into Discord's dark theme for a clean, borderless card
ACCENT = discord.Color.from_rgb(88, 101, 242)    # brand colour for panels, welcome cards and product cards
SUCCESS = discord.Color.from_rgb(46, 204, 113)   # green, only for "it worked" moments
WARN = discord.Color.from_rgb(250, 166, 26)
DANGER = discord.Color.from_rgb(237, 66, 69)
INFO = discord.Color.from_rgb(79, 157, 255)

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


class UserError(Exception):
    """A user-facing error. The bot shows the message as an ephemeral reply."""


def parse_duration(text: str) -> int | None:
    """Turn '90s', '10m', '2h30m' or '1d' into seconds (None if it isn't valid)."""
    cleaned = re.sub(r"\s+", "", text.lower())
    matches = re.findall(r"(\d+)([smhd])", cleaned)
    if not matches or "".join(f"{n}{u}" for n, u in matches) != cleaned:
        return None
    return sum(int(n) * UNITS[u] for n, u in matches)


def parse_color(text: str) -> int:
    match = HEX_RE.match(text.strip())
    if not match:
        raise UserError("Colour must be a hex code like `#5865F2`.")
    return int(match[1], 16)


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suffix}"


def render(text: str, member: discord.Member, mention: bool = True) -> str:
    """Fill in {user} {username} {server} {count} {ordinal} placeholders."""
    guild = member.guild
    count = guild.member_count or 0
    return (
        text.replace("{user}", member.mention if mention else member.display_name)
        .replace("{username}", member.display_name)
        .replace("{server}", guild.name)
        .replace("{count}", f"{count:,}")
        .replace("{ordinal}", ordinal(count))
    )


def check_role(interaction: discord.Interaction, role: discord.Role) -> None:
    """Make sure a role is safe to hand out and that both the bot and the admin can manage it."""
    guild = interaction.guild
    if role.is_default() or role.managed:
        raise UserError("Pick a normal role (not @everyone, a bot role or an integration role).")
    if role.permissions.administrator:
        raise UserError("For safety I won't hand out a role that has Administrator permission.")
    if role >= guild.me.top_role:
        raise UserError("My highest role must sit **above** that role. Drag my role higher in Server Settings → Roles.")
    if interaction.user != guild.owner and role >= interaction.user.top_role:
        raise UserError("You can only use roles that are below your own highest role.")


def check_target(interaction: discord.Interaction, member: discord.Member) -> None:
    """Make sure the admin and the bot are allowed to moderate this member."""
    guild = interaction.guild
    if member == interaction.user:
        raise UserError("You can't do that to yourself.")
    if member.id == guild.owner_id:
        raise UserError("You can't do that to the server owner.")
    if member == guild.me:
        raise UserError("Nice try.")
    if interaction.user.id != guild.owner_id and member.top_role >= interaction.user.top_role:
        raise UserError("That member's top role is equal to or higher than yours.")
    if member.top_role >= guild.me.top_role:
        raise UserError("That member's top role is equal to or higher than mine. Move my role higher.")


def compact(**kwargs) -> dict:
    """Drop None values so they aren't passed to discord.py send/edit calls."""
    return {k: v for k, v in kwargs.items() if v is not None}


async def reply(interaction: discord.Interaction, **kwargs):
    """Respond to an interaction whether or not it has been answered yet (ephemeral by default)."""
    kwargs = compact(**kwargs)
    kwargs.setdefault("ephemeral", True)
    if interaction.response.is_done():
        return await interaction.followup.send(**kwargs)
    return await interaction.response.send_message(**kwargs)


def check_can_send(channel: discord.TextChannel, me: discord.Member, files: bool = False) -> None:
    perms = channel.permissions_for(me)
    ok = perms.view_channel and perms.send_messages and perms.embed_links and (perms.attach_files if files else True)
    if not ok:
        needed = "View Channel, Send Messages, Embed Links" + (" and Attach Files" if files else "")
        raise UserError(f"I need {needed} in {channel.mention}.")
