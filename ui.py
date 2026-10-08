"""One look for every embed: neutral cards, a few accent colours, tidy key/value blocks, star helpers."""
from typing import Iterable, Optional

import discord

from common import ACCENT, COLOR, DANGER, INFO, SUCCESS, WARN  # noqa: F401  (re-exported for convenience)

DIVIDER = "▬▬▬▬▬▬▬▬▬▬▬▬"
MEDALS = ["🥇", "🥈", "🥉"]


def stars(value: float, total: int = 5) -> str:
    filled = max(0, min(total, int(round(value))))
    return "★" * filled + "☆" * (total - filled)


def rating_line(average: Optional[float], count: int) -> str:
    if not count:
        return "No reviews yet"
    return f"{stars(average or 0)} **{average:.1f}** ({count})"


def plural(n: int, word: str, many: Optional[str] = None) -> str:
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, int(round(fraction * width))))
    return "█" * filled + "░" * (width - filled)


def kv(*pairs: tuple) -> str:
    """Tidy 'label · value' lines. Pairs with an empty value are skipped."""
    return "\n".join(f"**{label}** · {value}" for label, value in pairs if value not in (None, ""))


def duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m"
    return f"{hours // 24}d {hours % 24}h"


def rank(i: int) -> str:
    return MEDALS[i] if i < len(MEDALS) else f"`{i + 1}.`"


def card(
    title: Optional[str] = None,
    description: Optional[str] = None,
    *,
    color: discord.Color | int = COLOR,
    guild: Optional[discord.Guild] = None,
    section: Optional[str] = None,
    footer: Optional[str] = None,
    thumbnail: Optional[str] = None,
    image: Optional[str] = None,
    author: Optional[tuple] = None,
    timestamp: bool = False,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow() if timestamp else None)
    if author:
        embed.set_author(name=author[0], icon_url=author[1] if len(author) > 1 else None)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    text = footer or (" · ".join(x for x in (guild.name if guild else None, section) if x) or None)
    if text:
        embed.set_footer(text=text, icon_url=guild.icon.url if guild is not None and guild.icon else None)
    return embed


def note(title: str, text: str, color: discord.Color | int = COLOR) -> discord.Embed:
    """A small status message (errors, confirmations)."""
    return card(title, text, color=color)
