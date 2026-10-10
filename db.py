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
    file_id      INTEGER,
    role_id      INTEGER,
    UNIQUE (guild_id, name)
);
CREATE TABLE IF NOT EXISTS shop_settings (
    guild_id           INTEGER PRIMARY KEY,
    default_channel_id INTEGER,
    color              TEXT,
    button_label       TEXT,
    footer             TEXT,
    ticket_channel_id  INTEGER,
    receipt_note       TEXT,
    order_prefix       TEXT,
    order_style        TEXT,
    order_counter      INTEGER NOT NULL DEFAULT 0
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
    file_id           INTEGER,
    file_status       TEXT,
    source            TEXT NOT NULL DEFAULT 'stripe',
    note              TEXT,
    role_id           INTEGER,
    role_status       TEXT,
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
CREATE TABLE IF NOT EXISTS log_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    category   TEXT NOT NULL,
    title      TEXT NOT NULL,
    summary    TEXT,
    subject_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_history_subject ON log_history (guild_id, subject_id, id);
CREATE INDEX IF NOT EXISTS idx_log_history_time ON log_history (guild_id, created_at);
CREATE TABLE IF NOT EXISTS stored_files (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id         INTEGER NOT NULL,
    name             TEXT NOT NULL COLLATE NOCASE,
    filename         TEXT NOT NULL,
    content_type     TEXT,
    size             INTEGER NOT NULL,
    data             BLOB NOT NULL,
    description      TEXT,
    required_role_id INTEGER,
    once_per_user    INTEGER NOT NULL DEFAULT 0,
    uploaded_by      INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    path             TEXT,
    UNIQUE (guild_id, name)
);
CREATE TABLE IF NOT EXISTS file_deliveries (
    file_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    count   INTEGER NOT NULL DEFAULT 1,
    last_at TEXT NOT NULL,
    PRIMARY KEY (file_id, user_id)
);
CREATE TABLE IF NOT EXISTS allowed_guilds (
    guild_id   INTEGER PRIMARY KEY,
    added_by   INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    section    TEXT NOT NULL DEFAULT '',
    name       TEXT NOT NULL COLLATE NOCASE,
    title      TEXT,
    body       TEXT,
    color      TEXT,
    image_url  TEXT,
    image_file_id INTEGER,
    footer     TEXT,
    links      TEXT,
    channel_id INTEGER,
    message_id INTEGER,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    UNIQUE (guild_id, kind, section, name)
);
CREATE TABLE IF NOT EXISTS invite_joins (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    member_id  INTEGER NOT NULL,
    inviter_id INTEGER,
    code       TEXT,
    joined_at  TEXT NOT NULL,
    left_at    TEXT,
    fake       INTEGER NOT NULL DEFAULT 0,
    counted    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_invite_joins_inviter ON invite_joins (guild_id, inviter_id);
CREATE INDEX IF NOT EXISTS idx_invite_joins_member ON invite_joins (guild_id, member_id);
CREATE TABLE IF NOT EXISTS invite_bonus (
    guild_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    amount   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS message_counts (
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    count      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, channel_id)
);
CREATE TABLE IF NOT EXISTS giveaway_settings (
    guild_id         INTEGER PRIMARY KEY,
    ping_role_id     INTEGER,
    default_channel_id INTEGER
);
CREATE TABLE IF NOT EXISTS ping_roles (
    guild_id    INTEGER NOT NULL,
    key         TEXT NOT NULL,
    name        TEXT NOT NULL,
    role_id     INTEGER,
    emoji       TEXT,
    description TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, key)
);
CREATE TABLE IF NOT EXISTS ping_panel (
    guild_id   INTEGER PRIMARY KEY,
    channel_id INTEGER,
    message_id INTEGER
);
CREATE TABLE IF NOT EXISTS automod_settings (
    guild_id        INTEGER PRIMARY KEY,
    enabled         INTEGER NOT NULL DEFAULT 0,
    links_mode      TEXT NOT NULL DEFAULT 'off',
    allowed_domains TEXT,
    block_invites   INTEGER NOT NULL DEFAULT 0,
    spam_count      INTEGER NOT NULL DEFAULT 0,
    spam_seconds    INTEGER NOT NULL DEFAULT 8,
    mention_limit   INTEGER NOT NULL DEFAULT 0,
    caps_percent    INTEGER NOT NULL DEFAULT 0,
    emoji_limit     INTEGER NOT NULL DEFAULT 0,
    max_lines       INTEGER NOT NULL DEFAULT 0,
    repeat_limit    INTEGER NOT NULL DEFAULT 0,
    blocked_words   TEXT,
    exempt_roles    TEXT,
    exempt_channels TEXT,
    action          TEXT NOT NULL DEFAULT 'delete',
    strikes_before_timeout INTEGER NOT NULL DEFAULT 3,
    timeout_minutes INTEGER NOT NULL DEFAULT 10
);
CREATE TABLE IF NOT EXISTS automod_strikes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    rule       TEXT NOT NULL,
    created_at REAL NOT NULL,
    cleared    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_automod_strikes ON automod_strikes (guild_id, user_id);
CREATE TABLE IF NOT EXISTS staff_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    author_id  INTEGER NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_staff_notes_user ON staff_notes (guild_id, user_id);
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    author_id  INTEGER NOT NULL,
    title      TEXT,
    pack_name  TEXT,
    file_name  TEXT,
    created_at TEXT NOT NULL,
    data       TEXT
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
CREATE TABLE IF NOT EXISTS upload_channels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id         INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    post_channel_id  INTEGER NOT NULL,
    pack_name        TEXT NOT NULL,
    required_role_id INTEGER,
    once_per_user    INTEGER NOT NULL DEFAULT 0,
    created_by       INTEGER NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (guild_id, channel_id)
);

-- Guards against the same attachment being auto-uploaded twice -- e.g. if a Discord event is ever delivered more
-- than once (gateway resume, or two bot instances briefly overlapping during a deploy). A row here means that
-- attachment has already been processed; INSERT OR IGNORE against the primary key is how callers check-and-claim
-- atomically without a separate read-then-write race.
-- Macro license keys (checked by the macro at POST /api/activate, see cogs/licenses.py)
CREATE TABLE IF NOT EXISTS licenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    expires_at  TEXT,
    last_seen   TEXT,
    activations INTEGER NOT NULL DEFAULT 0,
    guild_id    INTEGER,
    user_id     INTEGER,
    owner_name  TEXT,
    order_code  TEXT,
    created_by  INTEGER,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_licenses_user ON licenses (user_id);
CREATE TABLE IF NOT EXISTS auth_sessions (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    user_id    INTEGER,
    username   TEXT,
    linked_at  TEXT,
    error      TEXT
);
CREATE TABLE IF NOT EXISTS license_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    license_id INTEGER,
    event_type TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_uploads (
    message_id    INTEGER NOT NULL,
    attachment_id INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (message_id, attachment_id)
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
    "style", "bg_url", "rules_channel_id", "extra_channel_id", "extra_label", "banner_mode", "banner_url", "banner_file_id",
}


MIGRATIONS = [
    ("pages", "video_url", "TEXT"),
    ("giveaways", "prize_role_id", "INTEGER"),
    ("giveaways", "prize_file_id", "INTEGER"),
    ("orders", "buyer_email", "TEXT"),
    ("orders", "buyer_country", "TEXT"),
    ("automod_settings", "strike_hours", "INTEGER NOT NULL DEFAULT 24"),
    ("automod_settings", "dm_user", "INTEGER NOT NULL DEFAULT 1"),
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
    ("upload_channels", "gif_url", "TEXT"),
    ("reviews", "staff_id", "INTEGER"),
    ("reviews", "ticket_id", "INTEGER"),
    ("products", "file_id", "INTEGER"),
    ("stored_files", "path", "TEXT"),
    ("orders", "file_id", "INTEGER"),
    ("orders", "file_status", "TEXT"),
    ("orders", "source", "TEXT NOT NULL DEFAULT 'stripe'"),
    ("orders", "note", "TEXT"),
    ("shop_settings", "order_prefix", "TEXT"),
    ("shop_settings", "order_style", "TEXT"),
    ("shop_settings", "order_counter", "INTEGER NOT NULL DEFAULT 0"),
    ("products", "role_id", "INTEGER"),
    ("orders", "role_id", "INTEGER"),
    ("orders", "role_status", "TEXT"),
    ("orders", "processed_by", "INTEGER"),
    ("orders", "fulfillment", "TEXT"),
    ("orders", "processed_at", "TEXT"),
    ("giveaways", "min_invites", "INTEGER NOT NULL DEFAULT 0"),
    ("giveaways", "min_messages", "INTEGER NOT NULL DEFAULT 0"),
    ("giveaways", "req_channel_id", "INTEGER"),
    ("giveaways", "req_channel_messages", "INTEGER NOT NULL DEFAULT 0"),
    ("giveaways", "blocked_role_id", "INTEGER"),
    ("giveaways", "min_account_days", "INTEGER NOT NULL DEFAULT 0"),
    ("giveaways", "min_server_days", "INTEGER NOT NULL DEFAULT 0"),
    ("giveaways", "ping_role_id", "INTEGER"),
    ("welcome_config", "banner_mode", "TEXT"),
    ("welcome_config", "banner_url", "TEXT"),
    ("welcome_config", "banner_file_id", "INTEGER"),
    ("posts", "data", "TEXT"),
    ("products", "license_days", "INTEGER"),
    ("orders", "license_code", "TEXT"),
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
    "name", "description", "price", "buy_url", "image_url", "color", "button_label", "available", "channel_id", "message_id", "file_id", "role_id",
    "license_days",
}
SHOP_COLUMNS = {"default_channel_id", "color", "button_label", "footer", "ticket_channel_id", "receipt_note", "order_prefix", "order_style", "order_counter"}


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


async def invite_stats(guild_id: int, user_id: int) -> dict:
    """What this person's invites add up to: people who joined with their link, who left, fake/new accounts, and staff bonus."""
    r = await fetch_one(
        "SELECT COALESCE(SUM(fake = 0), 0) AS joins, COALESCE(SUM(fake = 0 AND left_at IS NOT NULL), 0) AS gone, COALESCE(SUM(fake = 1), 0) AS fakes "
        "FROM invite_joins WHERE guild_id = ? AND inviter_id = ? AND counted = 1", (guild_id, user_id),
    )
    bonus_row = await fetch_one("SELECT amount FROM invite_bonus WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    bonus = bonus_row["amount"] if bonus_row else 0
    return {"joins": r["joins"], "left": r["gone"], "fake": r["fakes"], "bonus": bonus, "valid": max(0, r["joins"] - r["gone"] + bonus)}


async def message_total(guild_id: int, user_id: int, channel_id: int | None = None) -> int:
    if channel_id is None:
        row = await fetch_one("SELECT COALESCE(SUM(count), 0) AS n FROM message_counts WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
    else:
        row = await fetch_one("SELECT COALESCE(SUM(count), 0) AS n FROM message_counts WHERE guild_id = ? AND user_id = ? AND channel_id = ?", (guild_id, user_id, channel_id))
    return row["n"]
