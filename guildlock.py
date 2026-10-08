"""Keeps the bot private: when the lock is on, it leaves every server that isn't approved."""
import logging
import os
import re

import discord

import db

log = logging.getLogger("verification-bot")


def parse_ids(raw) -> set[int]:
    """'123, 456 789' -> {123, 456, 789}. Anything that isn't a number is ignored."""
    return {int(x) for x in re.split(r"[,\s]+", raw or "") if x.isdigit()}


def env_allowed() -> set[int]:
    return parse_ids(os.getenv("ALLOWED_GUILD_IDS"))


async def stored_allowed() -> set[int]:
    return {r["guild_id"] for r in await db.fetch_all("SELECT guild_id FROM allowed_guilds")}


async def lock_enabled() -> bool:
    """The lock is on once at least one server has been approved (with /serverlock or ALLOWED_GUILD_IDS)."""
    return bool(env_allowed()) or bool(await stored_allowed())


async def allowed_ids() -> set[int]:
    allowed = env_allowed() | await stored_allowed()
    allowed |= parse_ids(os.getenv("GUILD_ID"))  # the server you develop in is always welcome
    return allowed


def guilds_to_leave(present: set[int], allowed: set[int]) -> list[int]:
    """Servers to leave. Safety net: if the bot isn't in ANY approved server (a typo in an ID, say) it leaves nothing,
    so a mistake can never make it walk out of your own server."""
    if not allowed or not (present & allowed):
        return []
    return sorted(present - allowed)


async def enforce_lock(bot) -> list[int]:
    """Leave every server that isn't approved. Returns the IDs of the servers it left."""
    if not await lock_enabled():
        return []
    allowed = await allowed_ids()
    present = {g.id for g in bot.guilds}
    if allowed and not (present & allowed):
        log.warning("The server lock is on but I'm not in any approved server, so I'm leaving every server alone. Check ALLOWED_GUILD_IDS.")
    left: list[int] = []
    for guild_id in guilds_to_leave(present, allowed):
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue
        try:
            await guild.leave()
            left.append(guild_id)
            log.warning("Left %s (%s): this bot is locked to its approved servers", guild.name, guild_id)
        except discord.HTTPException:
            log.exception("Couldn't leave %s (%s)", guild.name, guild_id)
    return left
