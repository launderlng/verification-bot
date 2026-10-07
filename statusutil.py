"""Helpers for the bot's rotating status (pure, easy to test)."""
from typing import Optional

DEFAULT_STATUSES = [".gg/xzxx", "bypassing the check"]
MAX_STATUSES = 10
MAX_LENGTH = 128  # Discord's limit for an activity name


def parse_statuses(raw: Optional[str]) -> list[str]:
    """Turn 'one, two, three' into a clean list. Falls back to the defaults if nothing usable is given."""
    items: list[str] = []
    for part in (raw or "").split(","):
        text = part.strip()[:MAX_LENGTH]
        if text and text not in items:
            items.append(text)
    return (items or list(DEFAULT_STATUSES))[:MAX_STATUSES]


def parse_interval(raw: Optional[str], default: int = 30, minimum: int = 15) -> int:
    """Seconds between status changes. Discord rate-limits presence updates, so never go below 15."""
    try:
        return max(minimum, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def pick(index: int, statuses: list[str]) -> str:
    return statuses[index % len(statuses)]
