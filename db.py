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
    enabled_at           TEXT,
    panel_title          TEXT,
    panel_color          TEXT,
    panel_image          TEXT,
    button_label         TEXT,
    rules_text           TEXT
);
CREATE TABLE IF NOT EXISTS verifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    method      TEXT NOT NULL,
    verified_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verifications_guild ON verifications (guild_id, verified_at);

CREATE TABLE IF NOT EXISTS welcome_config (
    guild_id         INTEGER PRIMARY KEY,
    enabled          INTEGER NOT NULL DEFAULT 0,
    channel_id       INTEGER,
    message          TEXT,
    send_on          TEXT NOT NULL DEFAULT 'join',
    use_embed        INTEGER NOT NULL DEFAULT 1,
    embed_title      TEXT,
    embed_color      TEXT,
    image_url        TEXT,
    leave_enabled    INTEGER NOT NULL DEFAULT 0,
    leave_channel_id INTEGER,
    leave_message    TEXT,
    dm_enabled       INTEGER NOT NULL DEFAULT 0,
    dm_message       TEXT,
    autorole_id      INTEGER,
    botrole_id       INTEGER,
    style            TEXT,
    bg_url           TEXT,
    rules_channel_id INTEGER,
    extra_channel_id INTEGER,
    extra_label      TEXT
);
CREATE TABLE IF NOT EXISTS log_routes (
    guild_id   INTEGER NOT NULL,
    category   TEXT NOT NULL,
    channel_id INTEGER,
    enabled    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, category)
);
CREATE TABLE IF NOT EXISTS warnings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings (guild_id, user_id);
"""

CONFIG_COLUMNS = {
    "verified_role_id", "unverified_role_id", "panel_channel_id", "panel_message_id", "panel_text",
    "log_channel_id", "welcome_channel_id", "welcome_message", "mode", "min_account_age_days",
    "kick_after_minutes", "max_attempts", "raid_threshold", "lockdown", "enabled_at",
    "panel_title", "panel_color", "panel_image", "button_label", "rules_text",
}
WELCOME_COLUMNS = {
    "enabled", "channel_id", "message", "send_on", "use_embed", "embed_title", "embed_color", "image_url",
    "leave_enabled", "leave_channel_id", "leave_message", "dm_enabled", "dm_message", "autorole_id", "botrole_id",
    "style", "bg_url", "rules_channel_id", "extra_channel_id", "extra_label",
}


MIGRATIONS = [
    ("config", "panel_title", "TEXT"),
    ("config", "panel_color", "TEXT"),
    ("config", "panel_image", "TEXT"),
    ("config", "button_label", "TEXT"),
    ("config", "rules_text", "TEXT"),
    ("welcome_config", "style", "TEXT"),
    ("welcome_config", "bg_url", "TEXT"),
    ("welcome_config", "rules_channel_id", "INTEGER"),
    ("welcome_config", "extra_channel_id", "INTEGER"),
    ("welcome_config", "extra_label", "TEXT"),
]


async def init() -> None:
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        # Add columns that older databases don't have yet (safe to run every start)
        for table, column, decl in MIGRATIONS:
            async with conn.execute(f"PRAGMA table_info({table})") as cur:
                existing = {row[1] for row in await cur.fetchall()}
            if column not in existing:
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
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


async def _upsert(table: str, allowed: set, guild_id: int, fields: dict) -> None:
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown columns for {table}: {unknown}")
    await execute(f"INSERT OR IGNORE INTO {table} (guild_id) VALUES (?)", (guild_id,))
    if fields:
        assignments = ", ".join(f"{col} = ?" for col in fields)
        await execute(f"UPDATE {table} SET {assignments} WHERE guild_id = ?", (*fields.values(), guild_id))


async def get_config(guild_id: int) -> aiosqlite.Row | None:
    return await fetch_one("SELECT * FROM config WHERE guild_id = ?", (guild_id,))


async def upsert_config(guild_id: int, **fields) -> None:
    await _upsert("config", CONFIG_COLUMNS, guild_id, fields)


async def delete_config(guild_id: int) -> None:
    await execute("DELETE FROM config WHERE guild_id = ?", (guild_id,))


async def get_welcome(guild_id: int) -> aiosqlite.Row | None:
    return await fetch_one("SELECT * FROM welcome_config WHERE guild_id = ?", (guild_id,))


async def upsert_welcome(guild_id: int, **fields) -> None:
    await _upsert("welcome_config", WELCOME_COLUMNS, guild_id, fields)
