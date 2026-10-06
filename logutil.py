import discord

import db
from common import COLOR

CATEGORIES = {
    "verification": "Verification events",
    "members": "Joins and leaves",
    "messages": "Message edits and deletes",
    "roles": "Role changes",
    "moderation": "Bans, kicks, timeouts, warnings",
    "channels": "Channel changes",
    "nicknames": "Nickname and username changes",
    "voice": "Voice channel activity",
    "server": "Server settings changes",
    "shop": "Shop purchases",
    "tickets": "Support tickets",
}


def clip(text: str | None, limit: int = 1000) -> str:
    text = text or "—"
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def send_log(guild: discord.Guild, category: str, embed: discord.Embed) -> None:
    """Send an embed to the log channel for this category (respecting toggles and routes)."""
    cfg = await db.get_config(guild.id)
    if not cfg:
        return
    route = await db.fetch_one(
        "SELECT channel_id, enabled FROM log_routes WHERE guild_id = ? AND category = ?", (guild.id, category)
    )
    if route and not route["enabled"]:
        return
    channel_id = (route["channel_id"] if route else None) or cfg["log_channel_id"]
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


async def emit(
    guild: discord.Guild,
    category: str,
    title: str,
    description: str = "",
    color: discord.Color = COLOR,
    fields: tuple = (),
    thumbnail: str | None = None,
    footer: str | None = None,
) -> None:
    embed = discord.Embed(
        title=title, description=clip(description, 3800) if description else None, color=color, timestamp=discord.utils.utcnow()
    )
    for name, value in fields:
        embed.add_field(name=name, value=clip(value), inline=False)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if footer:
        embed.set_footer(text=footer)
    await send_log(guild, category, embed)


async def find_audit_entry(guild: discord.Guild, action: discord.AuditLogAction, target_id: int, within: int = 20):
    """Look for a recent audit-log entry about target_id (needs the View Audit Log permission)."""
    if not guild.me.guild_permissions.view_audit_log:
        return None
    try:
        async for entry in guild.audit_logs(limit=8, action=action):
            if entry.target is not None and entry.target.id == target_id:
                if (discord.utils.utcnow() - entry.created_at).total_seconds() <= within:
                    return entry
    except discord.HTTPException:
        pass
    return None
