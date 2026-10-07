import discord
from datetime import timedelta

import db
import ui
from common import ACCENT, COLOR, DANGER, INFO, SUCCESS, WARN

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
    "giveaways": "Giveaways",
    "invites": "Invites",
    "files": "File downloads",
}


# Layout used by "Create log channels": channel -> (name, topic, categories sent there)
LOG_CHANNELS = {
    "member-logs": ("👥・member-logs", "Joins, leaves, invites and name changes", ["members", "nicknames", "invites"]),
    "message-logs": ("💬・message-logs", "Edited and deleted messages", ["messages"]),
    "mod-logs": ("🛡️・mod-logs", "Bans, kicks, timeouts and verification", ["moderation", "verification"]),
    "server-logs": ("⚙️・server-logs", "Roles, channels, voice and server changes", ["roles", "channels", "server", "voice"]),
    "ticket-logs": ("🎫・ticket-logs", "Tickets and giveaways", ["tickets", "giveaways"]),
    "shop-logs": ("🛒・shop-logs", "Shop purchases and file downloads", ["shop", "files"]),
}
ESSENTIALS = {"members", "messages", "moderation", "verification", "tickets", "roles"}
HISTORY_DAYS = 30


def clip(text: str | None, limit: int = 1000) -> str:
    text = text or "—"
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def send_log(guild: discord.Guild, category: str, embed: discord.Embed) -> bool:
    """Send an embed to the log channel for this category (respecting toggles and routes)."""
    cfg = await db.get_config(guild.id)
    if not cfg:
        return False
    route = await db.fetch_one(
        "SELECT channel_id, enabled FROM log_routes WHERE guild_id = ? AND category = ?", (guild.id, category)
    )
    if route and not route["enabled"]:
        return False
    channel_id = (route["channel_id"] if route else None) or cfg["log_channel_id"]
    if not channel_id:
        return False
    channel = guild.get_channel(channel_id)
    if channel is None:
        return False
    try:
        await channel.send(embed=embed)
        return True
    except discord.HTTPException:
        return False


CATEGORY_STYLE = {
    "verification": ("✅ Verification", SUCCESS), "members": ("👥 Members", INFO), "messages": ("💬 Messages", WARN),
    "roles": ("🎭 Roles", ACCENT), "moderation": ("🛡️ Moderation", DANGER), "channels": ("📁 Channels", INFO),
    "nicknames": ("✏️ Names", INFO), "voice": ("🔊 Voice", ACCENT), "server": ("⚙️ Server", WARN),
    "shop": ("🛒 Shop", SUCCESS), "tickets": ("🎫 Tickets", ACCENT), "giveaways": ("🎉 Giveaways", ACCENT), "invites": ("🔗 Invites", INFO), "files": ("📥 Files", INFO),
}


async def emit(
    guild: discord.Guild,
    category: str,
    title: str,
    description: str = "",
    color: discord.Color | None = None,
    fields: tuple = (),
    thumbnail: str | None = None,
    footer: str | None = None,
    author: tuple | None = None,
    subject: int | None = None,
) -> None:
    """Send one log entry. Looks the same everywhere: category colour, who it's about at the top, a tidy footer."""
    label, category_color = CATEGORY_STYLE.get(category, (category.title(), INFO))
    if color is None or color == COLOR:
        color = category_color
    embed = ui.card(
        title, clip(description, 3800) if description else None, color=color, author=author, thumbnail=thumbnail,
        footer=f"{label} · {footer}" if footer else label, guild=guild, timestamp=True,
    )
    for name, value in fields:
        embed.add_field(name=name, value=clip(value), inline=False)
    if await send_log(guild, category, embed):
        # Keep a short, searchable history (never message text, only the headline and mentions)
        await db.execute(
            "INSERT INTO log_history (guild_id, category, title, summary, subject_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (guild.id, category, title[:200], clip(description, 400) if description else None, subject, discord.utils.utcnow().isoformat()),
        )


async def prune_history(days: int = HISTORY_DAYS) -> None:
    cutoff = (discord.utils.utcnow() - timedelta(days=days)).isoformat()
    await db.execute("DELETE FROM log_history WHERE created_at < ?", (cutoff,))


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
