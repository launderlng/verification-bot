import os

import aiosqlite

DB_PATH = os.getenv("DB_PATH", "verification.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    guild_id             INTEGER PRIMARY KEY,
    verified_role_id     INTEGER,
    unverified_role_id   INTEGER,
    panel_channel_id     INTEGER,
    panel_message_id     INTEGER,
    panel_text           TEXT,
    log_channel_id       INTEGER,
    welcome_channel_id   INTEGER,
    welcome_message      TEXT,
    mode                 TEXT NOT NULL DEFAULT 'math',
    min_account_age_days INTEGER NOT NULL DEFAULT 0,
    kick_after_minutes   INTEGER NOT NULL DEFAULT 0,
    max_attempts         INTEGER NOT NULL DEFAULT 3,
    raid_threshold       INTEGER NOT NULL DEFAULT 0,
    lockdown             INTEGER NOT NULL DEFAULT 0,
    enabled_at           TEXT
);
CREATE TABLE IF NOT EXISTS verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    method      TEXT NOT NULL,
    verified_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verifications_guild ON verifications (guild_id, verified_at);
"""

CONFIG_COLUMNS = {
    "verified_role_id", "unverified_role_id", "panel_channel_id", "panel_message_id", "panel_text",
    "log_channel_id", "welcome_channel_id", "welcome_message", "mode", "min_account_age_days",
    "kick_after_minutes", "max_attempts", "raid_threshold", "lockdown", "enabled_at",
}


async def init() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


async def fetch_all(sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, params) as cur:
            return list(await cur.fetchall())


async def fetch_one(sql: str, params: tuple = ()) -> aiosqlite.Row | None:
    rows = await fetch_all(sql, params)
    return rows[0] if rows else None


async def execute(sql: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(DB_PATH) as conn:
        cur = await conn.execute(sql, params)
        await conn.commit()
        return cur.rowcount


async def get_config(guild_id: int) -> aiosqlite.Row | None:
    return await fetch_one("SELECT * FROM config WHERE guild_id = ?", (guild_id,))


async def upsert_config(guild_id: int, **fields) -> None:
    unknown = set(fields) - CONFIG_COLUMNS
    if unknown:
        raise ValueError(f"Unknown config columns: {unknown}")
    await execute("INSERT OR IGNORE INTO config (guild_id) VALUES (?)", (guild_id,))
    if fields:
        assignments = ", ".join(f"{col} = ?" for col in fields)
        await execute(f"UPDATE config SET {assignments} WHERE guild_id = ?", (*fields.values(), guild_id))


async def delete_config(guild_id: int) -> None:
    await execute("DELETE FROM config WHERE guild_id = ?", (guild_id,))
