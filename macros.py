"""The macros the bot sells keys for.

To add a new macro: add a line to MACROS (a short id -> name and emoji), then make that
macro's key check send the same id as `product` (e.g. `&product=altclick`).
Keys are tagged with the macro they're for, and only work in that macro.
"""
from typing import Optional

from discord import app_commands

MACROS = {
    "metal": ("Metal Detector", "⛏️"),
    "altclick": ("Alt Click", "🖱️"),
    "fishing": ("Fishing", "🎣"),
}

# Older macro builds don't say which macro they are. Those builds are all the Metal Detector.
DEFAULT_MACRO = "metal"


def clean_macro(value: Optional[str]) -> Optional[str]:
    """A known macro id, or None."""
    v = (value or "").strip().lower()
    return v if v in MACROS else None


def macro_name(mid: Optional[str]) -> str:
    if mid in MACROS:
        return MACROS[mid][0]
    if mid == "other":
        return "Not about a macro"
    return "All macros"


def macro_label(mid: Optional[str]) -> str:
    """Name with its emoji, e.g. '🖱️ Alt Click'. None means a key that works in every macro."""
    if mid in MACROS:
        name, emoji = MACROS[mid]
        return f"{emoji} {name}"
    if mid == "other":
        return "💬 Not about a macro"
    return "🌐 All macros"


# Slash-command choices. "all" = a key that works in every macro (how old keys behave).
KEY_MACRO_CHOICES = [app_commands.Choice(name=name, value=mid) for mid, (name, _) in MACROS.items()] + [
    app_commands.Choice(name="All macros", value="all")]
FILTER_MACRO_CHOICES = KEY_MACRO_CHOICES
