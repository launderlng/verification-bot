import time

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
    "automod": "Automod actions",
    "posts": "Posts and packs",
    "staff": "Staff actions",
    "commands": "Every slash command anyone runs",
    "events": "Scheduled server events",
}


# Layout used by "Create log channels": one private channel per category, fully separated.
LOG_CHANNELS = {
    "verification": ("✅・verify-logs", "Verification attempts, passes and lockdown", ["verification"]),
    "members": ("👋・join-logs", "Joins and leaves", ["members"]),
    "messages": ("💬・message-logs", "Edited and deleted messages", ["messages"]),
    "roles": ("🎭・role-logs", "Roles created, deleted and changed", ["roles"]),
    "moderation": ("🛡️・mod-logs", "Bans, kicks, timeouts and warnings", ["moderation"]),
    "channels": ("📁・channel-logs", "Channels created, deleted and changed", ["channels"]),
    "nicknames": ("✏️・name-logs", "Nickname and username changes", ["nicknames"]),
    "voice": ("🔊・voice-logs", "Voice channel activity", ["voice"]),
    "server": ("⚙️・server-logs", "Server settings and emoji changes", ["server"]),
    "shop": ("🛒・shop-logs", "Shop purchases", ["shop"]),
    "tickets": ("🎫・ticket-logs", "Support tickets", ["tickets"]),
    "giveaways": ("🎉・giveaway-logs", "Giveaways", ["giveaways"]),
    "invites": ("📨・invite-logs", "Invites created, deleted and used", ["invites"]),
    "files": ("📥・file-logs", "File downloads", ["files"]),
    "automod": ("🤖・automod-logs", "Automod actions", ["automod"]),
    "posts": ("📢・post-logs", "Posts and packs", ["posts"]),
    "staff": ("🛠️・staff-logs", "Staff actions", ["staff"]),
    "commands": ("⌨️・command-logs", "Every slash command anyone runs", ["commands"]),
    "events": ("🗓️・event-logs", "Scheduled server events", ["events"]),
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
    "shop": ("🛒 Shop", SUCCESS), "tickets": ("🎫 Tickets", ACCENT), "giveaways": ("🎉 Giveaways", ACCENT), "invites": ("🔗 Invites", INFO), "files": ("📥 Files", INFO), "automod": ("🤖 Automod", WARN), "posts": ("📢 Posts", INFO), "staff": ("🛠️ Staff", INFO),
    "commands": ("⌨️ Commands", INFO), "events": ("🗓️ Events", ACCENT),
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
    actor: discord.abc.User | None = None,
    ids: tuple = (),
) -> None:
    """Send one log entry. Every log has the same shape: what happened, who it's about, the staff member responsible,
    the relevant IDs and when it happened, with the category colour and a tidy footer."""
    label, category_color = CATEGORY_STYLE.get(category, (category.title(), INFO))
    if color is None or color == COLOR:
        color = category_color
    text = description or ""
    extras = []
    if actor is not None and "🛡️" not in text:
        extras.append(f"**🛡️ Staff** · {actor.mention} (`{actor.id}`)")
    if "🆔" not in text:
        pairs = ([("member", subject)] if subject else []) + [(k, v) for k, v in ids if v is not None]
        if pairs:
            extras.append("**🆔 IDs** · " + " · ".join(f"{k} `{v}`" for k, v in pairs))
    if "🕒" not in text:
        extras.append(f"**🕒 When** · {discord.utils.format_dt(discord.utils.utcnow(), 'f')}")
    description = (text + "\n" if text else "") + "\n".join(extras)
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


# ---- commands log their own actions; the audit-log events skip those so nothing is logged twice ----
_handled: dict[tuple, float] = {}


def mark_handled(guild_id: int, user_id: int, action: str) -> None:
    _handled[(guild_id, user_id, action)] = time.monotonic()
    for key in [k for k, t in _handled.items() if time.monotonic() - t > 120]:
        _handled.pop(key, None)


def was_handled(guild_id: int, user_id: int, action: str, within: float = 30) -> bool:
    stamp = _handled.pop((guild_id, user_id, action), None)
    return stamp is not None and time.monotonic() - stamp <= within


async def action_log(guild: discord.Guild, category: str, title: str, *, target, actor, reason: str | None = None, color=None,
                     lines: tuple = (), ids: tuple = (), fields: tuple = ()) -> None:
    """A moderation-style log: the member, the staff member, the reason, the IDs and the time, always in the same order."""
    name = getattr(target, "display_name", None) or getattr(target, "name", None) or f"User {target.id}"
    mention = getattr(target, "mention", f"<@{target.id}>")
    description = ui.kv(("👤 Member", f"{mention} (`{target.id}`)"), *lines, ("📝 Reason", reason or "None given"))
    avatar = getattr(getattr(target, "display_avatar", None), "url", None)
    await emit(guild, category, title, description, color, fields, author=(name, avatar) if avatar else (name,), subject=target.id, actor=actor, ids=ids)


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
