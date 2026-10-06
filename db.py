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

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    name         TEXT NOT NULL COLLATE NOCASE,
    description  TEXT,
    price        TEXT,
    buy_url      TEXT NOT NULL,
    image_url    TEXT,
    color        TEXT,
    button_label TEXT,
    available    INTEGER NOT NULL DEFAULT 1,
    channel_id   INTEGER,
    message_id   INTEGER,
    UNIQUE (guild_id, name)
);
CREATE TABLE IF NOT EXISTS shop_settings (
    guild_id           INTEGER PRIMARY KEY,
    default_channel_id INTEGER,
    color              TEXT,
    button_label       TEXT,
    footer             TEXT,
    ticket_channel_id  INTEGER,
    receipt_note       TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          INTEGER NOT NULL,
    code              TEXT NOT NULL,
    user_id           INTEGER NOT NULL,
    product_id        INTEGER,
    product_name      TEXT NOT NULL,
    amount            TEXT,
    stripe_session_id TEXT NOT NULL UNIQUE,
    stripe_ref        TEXT,
    livemode          INTEGER NOT NULL DEFAULT 1,
    dm_sent           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    UNIQUE (guild_id, code)
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders (guild_id, user_id);

CREATE TABLE IF NOT EXISTS ticket_config (
    guild_id              INTEGER PRIMARY KEY,
    category_id           INTEGER,
    panel_channel_id      INTEGER,
    panel_message_id      INTEGER,
    panel_title           TEXT,
    panel_text            TEXT,
    panel_color           TEXT,
    panel_image           TEXT,
    staff_roles           TEXT,
    transcript_channel_id INTEGER,
    max_open              INTEGER NOT NULL DEFAULT 1,
    auto_close_hours      INTEGER NOT NULL DEFAULT 0,
    ping_staff            INTEGER NOT NULL DEFAULT 1,
    dm_transcript         INTEGER NOT NULL DEFAULT 1,
    ask_rating            INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS ticket_types (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id      INTEGER NOT NULL,
    name          TEXT NOT NULL COLLATE NOCASE,
    emoji         TEXT,
    description   TEXT,
    welcome       TEXT,
    needs_invoice INTEGER NOT NULL DEFAULT 0,
    UNIQUE (guild_id, name)
);
CREATE TABLE IF NOT EXISTS tickets (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id           INTEGER NOT NULL,
    number             INTEGER NOT NULL,
    channel_id         INTEGER,
    user_id            INTEGER NOT NULL,
    type_name          TEXT NOT NULL,
    subject            TEXT,
    details            TEXT,
    invoice_code       TEXT,
    status             TEXT NOT NULL DEFAULT 'open',
    priority           TEXT NOT NULL DEFAULT 'normal',
    claimed_by         INTEGER,
    control_message_id INTEGER,
    created_at         TEXT NOT NULL,
    closed_at          TEXT,
    closed_by          INTEGER,
    close_reason       TEXT,
    last_activity      TEXT NOT NULL,
    warned             INTEGER NOT NULL DEFAULT 0,
    first_response_at  TEXT,
    rating             INTEGER,
    UNIQUE (guild_id, number)
);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets (guild_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets (channel_id);
CREATE TABLE IF NOT EXISTS ticket_blacklist (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    reason     TEXT,
    added_by   INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    product_name TEXT NOT NULL COLLATE NOCASE,
    stars        INTEGER NOT NULL,
    comment      TEXT,
    order_code   TEXT,
    channel_id   INTEGER,
    message_id   INTEGER,
    created_at   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'product',
    staff_id     INTEGER,
    ticket_id    INTEGER,
    UNIQUE (guild_id, user_id, product_name)
);
CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews (guild_id, product_name);
CREATE TABLE IF NOT EXISTS review_settings (
    guild_id         INTEGER PRIMARY KEY,
    channel_id       INTEGER,
    require_purchase INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS giveaways (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id         INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    message_id       INTEGER,
    host_id          INTEGER NOT NULL,
    prize            TEXT NOT NULL,
    description      TEXT,
    image_url        TEXT,
    winners          INTEGER NOT NULL DEFAULT 1,
    required_role_id INTEGER,
    bonus_role_id    INTEGER,
    bonus_entries    INTEGER NOT NULL DEFAULT 0,
    ends_at          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    winner_ids       TEXT,
    created_at       TEXT NOT NULL,
    ended_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways (status, ends_at);
CREATE TABLE IF NOT EXISTS giveaway_entries (
    giveaway_id INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    joined_at   TEXT NOT NULL,
    PRIMARY KEY (giveaway_id, user_id)
);
CREATE TABLE IF NOT EXISTS link_cards (
    guild_id    INTEGER NOT NULL,
    key         TEXT NOT NULL,
    title       TEXT,
    description TEXT,
    link_label  TEXT,
    link_url    TEXT,
    link2_label TEXT,
    link2_url   TEXT,
    image_url   TEXT,
    color       TEXT,
    PRIMARY KEY (guild_id, key)
);
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
    ("shop_settings", "ticket_channel_id", "INTEGER"),
    ("shop_settings", "receipt_note", "TEXT"),
    ("reviews", "kind", "TEXT NOT NULL DEFAULT 'product'"),
    ("reviews", "staff_id", "INTEGER"),
    ("reviews", "ticket_id", "INTEGER"),
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


PRODUCT_COLUMNS = {
    "name", "description", "price", "buy_url", "image_url", "color", "button_label", "available", "channel_id", "message_id",
}
SHOP_COLUMNS = {"default_channel_id", "color", "button_label", "footer", "ticket_channel_id", "receipt_note"}


async def get_shop(guild_id: int) -> aiosqlite.Row | None:
    return await fetch_one("SELECT * FROM shop_settings WHERE guild_id = ?", (guild_id,))


async def upsert_shop(guild_id: int, **fields) -> None:
    await _upsert("shop_settings", SHOP_COLUMNS, guild_id, fields)


async def get_product_by_id(product_id: int) -> aiosqlite.Row | None:
    return await fetch_one("SELECT * FROM products WHERE id = ?", (product_id,))


async def update_product(product_id: int, **fields) -> None:
    unknown = set(fields) - PRODUCT_COLUMNS
    if unknown:
        raise ValueError(f"Unknown product columns: {unknown}")
    if fields:
        assignments = ", ".join(f"{col} = ?" for col in fields)
        await execute(f"UPDATE products SET {assignments} WHERE id = ?", (*fields.values(), product_id))


TICKET_COLUMNS = {
    "category_id", "panel_channel_id", "panel_message_id", "panel_title", "panel_text", "panel_color", "panel_image",
    "staff_roles", "transcript_channel_id", "max_open", "auto_close_hours", "ping_staff", "dm_transcript", "ask_rating",
}


async def get_ticket_config(guild_id: int) -> aiosqlite.Row | None:
    return await fetch_one("SELECT * FROM ticket_config WHERE guild_id = ?", (guild_id,))


async def upsert_ticket_config(guild_id: int, **fields) -> None:
    await _upsert("ticket_config", TICKET_COLUMNS, guild_id, fields)


REVIEW_SETTING_COLUMNS = {"channel_id", "require_purchase"}


async def get_review_settings(guild_id: int) -> aiosqlite.Row | None:
    return await fetch_one("SELECT * FROM review_settings WHERE guild_id = ?", (guild_id,))


async def upsert_review_settings(guild_id: int, **fields) -> None:
    await _upsert("review_settings", REVIEW_SETTING_COLUMNS, guild_id, fields)


async def product_rating(guild_id: int, product_name: str) -> tuple[float | None, int]:
    """(average stars, number of reviews) for a product, or (None, 0)."""
    row = await fetch_one("SELECT AVG(stars) AS a, COUNT(*) AS c FROM reviews WHERE guild_id = ? AND product_name = ?", (guild_id, product_name))
    return (row["a"], row["c"]) if row and row["c"] else (None, 0)
