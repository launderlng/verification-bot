"""Macro license keys, run by the bot itself (replaces the separate license website).

* The macro checks a key with  POST /api/activate  (form field `code`) and gets back
  `OK|LICENSE VALID` or `DENIED|<reason>` - the same format the old website used, so the
  macro only needs the bot's web address.
* Products can hand out a key automatically: `/shop edit product:<name> license_key:<length>`
  (1 Day / 1 Week / 1 Month / Lifetime; timed keys start on their first login). Every Stripe purchase or /shop createorder for that product gets a fresh
  key in the receipt DM.
* Admins manage keys with /key ..., members see theirs with /myorders.
* Discord linking (on once DISCORD_CLIENT_SECRET is set): before a key works, the macro must
  link a Discord account. The macro gets a session from POST /api/auth/new, opens
  /auth/start?s=<session> (Discord's Authorize page), and polls GET /api/auth/status?s=<session>.
  The key check then needs that linked session: a key with no owner is locked to the first
  account that uses it, a key owned by someone else is refused, and the account must still be in
  the server. Links are remembered for LINK_DAYS days.
"""
import csv
import html
import io
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import DANGER, SUCCESS, WARN, UserError
from fileutil import public_base_url
from logutil import emit

log = logging.getLogger("verification-bot")

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I mix-ups
PREFIX = "".join(c for c in os.getenv("LICENSE_PREFIX", "XZX").upper() if c.isalnum())[:8] or "XZX"
RATE_LIMIT = 30          # key checks allowed per IP ...
RATE_WINDOW = 60         # ... per this many seconds
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
CLIENT_ID_ENV = os.getenv("DISCORD_CLIENT_ID", "").strip()
LINK_DAYS = max(1, int(os.getenv("LINK_DAYS", "30") or 30))
PENDING_MINUTES = 10     # how long a not-yet-authorized link session stays usable
DISCORD_API = "https://discord.com/api/v10"
INVITE = os.getenv("DISCORD_INVITE", "discord.gg/xzxx")

# Key lengths offered in /shop and /key. Timed keys start counting at the buyer's FIRST macro
# login, so a key bought late at night still gets its full time.
LENGTHS = {"1day": 1, "1week": 7, "1month": 30, "lifetime": 0}
LENGTH_CHOICES = [app_commands.Choice(name="1 Day", value="1day"), app_commands.Choice(name="1 Week", value="1week"),
                  app_commands.Choice(name="1 Month", value="1month"), app_commands.Choice(name="Lifetime", value="lifetime")]


def length_label(days: Optional[int]) -> str:
    if not days:
        return "Lifetime"
    return {1: "1 Day", 7: "1 Week", 30: "1 Month"}.get(days, f"{days} Days")


# ------------------------------------------------------------ helpers ----

def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def clean_code(value: Optional[str]) -> str:
    return "".join(c for c in (value or "").strip().upper() if c.isalnum() or c == "-")[:64]


def new_code() -> str:
    return PREFIX + "-" + "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))


def parse_time(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def expiry_text(row) -> str:
    exp = parse_time(row["expires_at"])
    if exp is None:
        if row["duration_days"]:
            return f"{length_label(row['duration_days'])} · starts on first login"
        return "Lifetime"
    if exp <= now():
        return f"Expired {discord.utils.format_dt(exp, 'R')}"
    return f"{discord.utils.format_dt(exp, 'D')} ({discord.utils.format_dt(exp, 'R')})"


def status_text(row) -> str:
    if not row["active"]:
        return "⛔ Disabled"
    exp = parse_time(row["expires_at"])
    if exp is not None and exp <= now():
        return "⌛ Expired"
    return "✅ Active"


async def log_event(license_id: Optional[int], event_type: str, detail: str = "") -> None:
    await db.execute(
        "INSERT INTO license_events (license_id, event_type, detail, created_at) VALUES (?, ?, ?, ?)",
        (license_id, event_type, detail[:300], now().isoformat()),
    )


async def create_license(guild_id: Optional[int], days: Optional[int], *, user_id: Optional[int] = None, order_code: Optional[str] = None,
                         created_by: Optional[int] = None, note: Optional[str] = None) -> str:
    """Make a new unique key. days None/0 = lifetime; otherwise the clock starts on its first login."""
    created = now()
    for _ in range(40):
        code = new_code()
        if await db.fetch_one("SELECT 1 FROM licenses WHERE code = ?", (code,)):
            continue
        await db.execute(
            "INSERT INTO licenses (code, active, created_at, expires_at, duration_days, activations, guild_id, user_id, order_code, created_by, note) "
            "VALUES (?, 1, ?, NULL, ?, 0, ?, ?, ?, ?, ?)",
            (code, created.isoformat(), days or None, guild_id, user_id, order_code, created_by, note),
        )
        row = await db.fetch_one("SELECT id FROM licenses WHERE code = ?", (code,))
        await log_event(row["id"] if row else None, "created", f"order {order_code}" if order_code else (note or "manual"))
        return code
    raise RuntimeError("Couldn't generate a unique license key")


async def issue_for_order(guild_id: int, order, product) -> Optional[str]:
    """Give an order its key if the product hands one out. Safe to call more than once (returns the existing key)."""
    if order is None:
        return None
    if order["license_code"]:
        return order["license_code"]
    if product is None or product["guild_id"] != guild_id:
        return None
    days = product["license_days"]
    if order["option_id"]:  # bought a specific payment option: its length wins
        option = await db.fetch_one("SELECT * FROM product_options WHERE id = ? AND product_id = ?", (order["option_id"], product["id"]))
        if option is not None:
            days = option["license_days"]
    if days is None:
        return None
    code = await create_license(guild_id, days or None, user_id=order["user_id"], order_code=order["code"],
                                note=f"Purchase of {order['product_name']}")
    await db.execute("UPDATE orders SET license_code = ? WHERE id = ?", (code, order["id"]))
    return code


async def receipt_line(order) -> Optional[str]:
    """The key block for a receipt DM, or None if the order has no key."""
    if not order or not order["license_code"]:
        return None
    row = await db.fetch_one("SELECT * FROM licenses WHERE code = ?", (order["license_code"],))
    length = expiry_text(row) if row else "Lifetime"
    return f"```{order['license_code']}```**Valid** · {length}\nPaste it into the macro's login screen. `/myorders` shows it any time."


async def set_active_for_order(order, active: bool) -> bool:
    if not order or not order["license_code"]:
        return False
    await db.execute("UPDATE licenses SET active = ? WHERE code = ?", (int(active), order["license_code"]))
    row = await db.fetch_one("SELECT id FROM licenses WHERE code = ?", (order["license_code"],))
    await log_event(row["id"] if row else None, "activated" if active else "deactivated", f"order {order['code']}")
    return True


async def find_license(code: str):
    row = await db.fetch_one("SELECT * FROM licenses WHERE code = ?", (clean_code(code),))
    if not row:
        raise UserError(f"I can't find a key `{clean_code(code) or code}`.")
    return row


def license_embed(row, guild: Optional[discord.Guild] = None) -> discord.Embed:
    embed = ui.card(f"🔑 {row['code']}", color=SUCCESS if status_text(row).startswith("✅") else WARN, guild=guild, section="Keys")
    last = parse_time(row["last_seen"])
    embed.description = ui.kv(
        ("Status", status_text(row)),
        ("Expires", expiry_text(row)),
        ("Owner", f"<@{row['user_id']}>" if row["user_id"] else (row["owner_name"] or None)),
        ("Order", f"`{row['order_code']}`" if row["order_code"] else None),
        ("Created", discord.utils.format_dt(parse_time(row["created_at"]), "f") if parse_time(row["created_at"]) else None),
        ("Created by", f"<@{row['created_by']}>" if row["created_by"] else None),
        ("Logins", f"{row['activations']:,}"),
        ("Last login", discord.utils.format_dt(last, "R") if last else "Never"),
        ("Note", row["note"]),
    )
    return embed


async def key_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    rows = await db.fetch_all("SELECT code FROM licenses WHERE code LIKE ? ORDER BY id DESC LIMIT 25", (f"%{clean_code(current)}%",))
    return [app_commands.Choice(name=r["code"], value=r["code"]) for r in rows]


# ----------------------------------------------------- discord linking ----

def redirect_uri() -> Optional[str]:
    base = public_base_url()
    return f"{base}/auth/callback" if base else None


def clean_session(value: Optional[str]) -> str:
    v = (value or "").strip()
    return v if 20 <= len(v) <= 64 and all(c.isalnum() or c in "-_" for c in v) else ""


def page(title: str, text: str, ok: bool = True) -> web.Response:
    """The little page shown in the browser after Authorize - same purple look as the macro."""
    icon = "✓" if ok else "!"
    accent = "#9d4eff" if ok else "#f0587e"
    body = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;font-family:Segoe UI,system-ui,sans-serif;color:#f4eeff;
background:radial-gradient(ellipse at 15% 0%,#3a1474 0%,#09060f 65%)}}
.c{{width:min(420px,88vw);padding:36px 32px;border-radius:22px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.13);text-align:center}}
.i{{width:56px;height:56px;margin:0 auto 18px;border-radius:50%;display:grid;place-items:center;font-size:28px;background:{accent};box-shadow:0 0 34px {accent}}}
h1{{font-size:22px;margin:0 0 10px}}p{{margin:0;color:#a99cc4;line-height:1.5}}</style></head>
<body><div class="c"><div class="i">{icon}</div><h1>{html.escape(title)}</h1><p>{text}</p></div></body></html>"""
    return web.Response(text=body, content_type="text/html")


# -------------------------------------------------------------- cog ----

@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Keys(commands.GroupCog, group_name="key", group_description="Macro license keys"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.hits: dict[str, list[float]] = {}
        self.login_logged: dict[tuple, float] = {}
        super().__init__()

    async def cog_load(self):
        app = getattr(self.bot, "web_app", None)
        if app is not None:
            app.router.add_post("/api/activate", self.handle_activate)
            app.router.add_post("/api/auth/new", self.handle_auth_new)
            app.router.add_get("/api/auth/status", self.handle_auth_status)
            app.router.add_get("/auth/start", self.handle_auth_start)
            app.router.add_get("/auth/callback", self.handle_auth_callback)
            log.info("License check route is ready at /api/activate (Discord linking %s)",
                     "ON" if self.linking_enabled() else "off: set DISCORD_CLIENT_SECRET to turn it on")

    # --------------------------------------------------- discord linking ----

    def client_id(self) -> Optional[str]:
        return CLIENT_ID_ENV or (str(self.bot.application_id) if getattr(self.bot, "application_id", None) else None)

    def linking_enabled(self) -> bool:
        return bool(CLIENT_SECRET and self.client_id() and redirect_uri())

    def in_server(self, user_id: int) -> bool:
        guilds = getattr(self.bot, "guilds", None) or []
        if not guilds:
            return True  # not connected yet: don't lock people out during a restart
        return any(g.get_member(user_id) is not None for g in guilds)

    async def linked_user(self, session: str):
        """The linked session row if it's authorized and not expired, else None."""
        if not session:
            return None
        row = await db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (session,))
        if not row or not row["user_id"]:
            return None
        linked = parse_time(row["linked_at"])
        if linked is None or linked + timedelta(days=LINK_DAYS) <= now():
            return None
        return row

    async def handle_auth_new(self, request: web.Request) -> web.Response:
        ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote or "?")
        if self.limited("new:" + ip):
            return web.Response(text="DENIED|TOO MANY ATTEMPTS, WAIT A MINUTE")
        if not self.linking_enabled():
            return web.Response(text="OFF|DISCORD LINKING IS NOT SET UP")
        session = secrets.token_urlsafe(24)
        await db.execute("INSERT INTO auth_sessions (id, created_at) VALUES (?, ?)", (session, now().isoformat()))
        # tidy up stale sessions now and then
        cutoff_pending = (now() - timedelta(minutes=PENDING_MINUTES * 3)).isoformat()
        cutoff_linked = (now() - timedelta(days=LINK_DAYS + 1)).isoformat()
        await db.execute("DELETE FROM auth_sessions WHERE (user_id IS NULL AND created_at < ?) OR (linked_at IS NOT NULL AND linked_at < ?)",
                         (cutoff_pending, cutoff_linked))
        return web.Response(text=f"OK|{session}|{public_base_url()}/auth/start?s={session}")

    async def handle_auth_status(self, request: web.Request) -> web.Response:
        if not self.linking_enabled():
            return web.Response(text="OFF")
        session = clean_session(request.query.get("s"))
        row = await db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (session,)) if session else None
        if not row:
            return web.Response(text="EXPIRED")
        if row["error"]:
            return web.Response(text=f"ERROR|{row['error']}")
        if row["user_id"]:
            if await self.linked_user(session) is None:
                return web.Response(text="EXPIRED")
            name = (row["username"] or "your account").replace("|", "")
            return web.Response(text=f"LINKED|{name}|{row['user_id']}")
        created = parse_time(row["created_at"])
        if created is None or created + timedelta(minutes=PENDING_MINUTES) <= now():
            return web.Response(text="EXPIRED")
        return web.Response(text="PENDING")

    async def handle_auth_start(self, request: web.Request) -> web.Response:
        session = clean_session(request.query.get("s"))
        row = await db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (session,)) if session else None
        if not self.linking_enabled() or not row:
            return page("Link expired", "Go back to the macro and press <b>Link Discord account</b> again.", ok=False)
        from urllib.parse import urlencode
        query = urlencode({"client_id": self.client_id(), "redirect_uri": redirect_uri(), "response_type": "code",
                           "scope": "identify", "state": session, "prompt": "consent"})
        raise web.HTTPFound(f"https://discord.com/oauth2/authorize?{query}")

    async def handle_auth_callback(self, request: web.Request) -> web.Response:
        session = clean_session(request.query.get("state"))
        row = await db.fetch_one("SELECT * FROM auth_sessions WHERE id = ?", (session,)) if session else None
        if not row:
            return page("Link expired", "Go back to the macro and press <b>Link Discord account</b> again.", ok=False)
        if request.query.get("error"):
            await db.execute("UPDATE auth_sessions SET error = ? WHERE id = ?", ("AUTHORIZE WAS CANCELLED", session))
            return page("Cancelled", "You didn't authorize. Go back to the macro and try again.", ok=False)
        code = request.query.get("code", "")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
                async with http.post(f"{DISCORD_API}/oauth2/token", data={
                    "client_id": self.client_id(), "client_secret": CLIENT_SECRET, "grant_type": "authorization_code",
                    "code": code, "redirect_uri": redirect_uri(),
                }) as r:
                    token = await r.json(content_type=None)
                if "access_token" not in token:
                    raise RuntimeError(f"token exchange failed: {token.get('error')} {token.get('error_description', '')}")
                async with http.get(f"{DISCORD_API}/users/@me", headers={"Authorization": f"Bearer {token['access_token']}"}) as r:
                    me = await r.json(content_type=None)
        except Exception as e:
            log.warning("Discord link failed: %s", e)
            await db.execute("UPDATE auth_sessions SET error = ? WHERE id = ?", ("DISCORD LOGIN FAILED, TRY AGAIN", session))
            return page("Something went wrong", "Discord didn't confirm the login. Go back to the macro and try again.", ok=False)
        user_id = int(me["id"])
        name = (me.get("global_name") or me.get("username") or "your account")[:40]
        if not self.in_server(user_id):
            await db.execute("UPDATE auth_sessions SET error = ? WHERE id = ?", (f"JOIN {INVITE.upper()} FIRST", session))
            return page("Join the Discord first", f"<b>{html.escape(name)}</b> isn't in the server yet. Join "
                        f"<a style='color:#b57bff' href='https://{html.escape(INVITE)}'>{html.escape(INVITE)}</a>, then link again.", ok=False)
        await db.execute("UPDATE auth_sessions SET user_id = ?, username = ?, linked_at = ?, error = NULL WHERE id = ?",
                         (user_id, name, now().isoformat(), session))
        log.info("Macro linked to Discord user %s (%s)", name, user_id)
        try:
            guild = self.log_guild()
        except Exception:
            guild = None
        if guild is not None:
            try:
                await emit(guild, "keys", "🔗 Discord account linked to the macro",
                           ui.kv(("👤 Discord", f"<@{user_id}> · `{user_id}`"), ("🏷️ Name", name)), SUCCESS, subject=user_id)
            except Exception:
                log.exception("Couldn't post a link log")
        return page("Account linked", f"Signed in as <b>{html.escape(name)}</b>.<br>You can close this tab and go back to the macro.")

    # ------------------------------------------------- macro key check ----

    def limited(self, ip: str) -> bool:
        t = time.monotonic()
        recent = [x for x in self.hits.get(ip, []) if t - x < RATE_WINDOW]
        recent.append(t)
        self.hits[ip] = recent
        if len(self.hits) > 5000:  # keep memory bounded
            self.hits = {k: v for k, v in self.hits.items() if v and t - v[-1] < RATE_WINDOW}
        return len(recent) > RATE_LIMIT

    # ------------------------------------------------------------ key logs ----

    def log_guild(self, row=None) -> Optional[discord.Guild]:
        gid = row["guild_id"] if row is not None and row["guild_id"] else None
        guild = self.bot.get_guild(gid) if gid else None
        if guild is None:
            guilds = getattr(self.bot, "guilds", None) or []
            guild = guilds[0] if guilds else None
        return guild

    async def key_log(self, row, title: str, user_id: Optional[int], lines: tuple, color=None,
                      who_label: str = "👤 Discord", name: Optional[str] = None) -> None:
        """Post to the 🔑 key-logs channel (set up with /logs). Never lets a logging problem block a login."""
        who = (f"<@{user_id}> · `{user_id}`" + (f" · {name}" if name else "") if user_id else "No Discord account linked")
        try:
            guild = self.log_guild(row)
            if guild is None:
                return
            await emit(guild, "keys", title, ui.kv((who_label, who), ("🔑 Key", f"`{row['code']}`" if row is not None else None), *lines),
                       color, subject=user_id)
        except Exception:
            log.exception("Couldn't post a key log")

    def should_log_login(self, key_id: int, user_id: Optional[int]) -> bool:
        """At most one 'logged in' entry per key + account every 10 minutes, so relaunching doesn't spam."""
        t = time.monotonic()
        k = (key_id, user_id)
        last = self.login_logged.get(k, 0)
        if t - last < 600:
            return False
        self.login_logged[k] = t
        if len(self.login_logged) > 5000:
            self.login_logged = {x: v for x, v in self.login_logged.items() if t - v < 600}
        return True

    async def handle_activate(self, request: web.Request) -> web.Response:
        ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote or "?")
        if self.limited(ip):
            return web.Response(text="DENIED|TOO MANY ATTEMPTS, WAIT A MINUTE")
        try:
            form = await request.post()
        except Exception:
            return web.Response(text="DENIED|INVALID REQUEST")
        code = clean_code(form.get("code"))
        if not code:
            return web.Response(text="DENIED|INVALID REQUEST")
        row = await db.fetch_one("SELECT * FROM licenses WHERE code = ?", (code,))
        if not row:
            return web.Response(text="DENIED|INVALID KEY")
        link = await self.linked_user(clean_session(form.get("session"))) if self.linking_enabled() else None
        who = link["user_id"] if link else row["user_id"]
        if not row["active"]:
            await log_event(row["id"], "activation_denied", "Disabled")
            await self.key_log(row, "⛔ Disabled key tried", who, (("❌ Refused", "The key is disabled"),), DANGER)
            return web.Response(text="DENIED|KEY DISABLED")
        exp = parse_time(row["expires_at"])
        if exp is not None and exp <= now():
            await log_event(row["id"], "activation_denied", "Expired")
            await self.key_log(row, "⌛ Expired key tried", who, (("❌ Refused", f"Expired {discord.utils.format_dt(exp, 'R')}"),), WARN)
            return web.Response(text="DENIED|KEY EXPIRED")
        owner = row["user_id"]
        first_link = False
        if self.linking_enabled():
            if link is None:
                return web.Response(text="DENIED|LINK YOUR DISCORD ACCOUNT FIRST")
            if not self.in_server(link["user_id"]):
                await self.key_log(row, "🚪 Key tried by someone not in the server", link["user_id"], (("❌ Refused", "Not in the server"),), WARN,
                                   who_label="🕵️ Tried by", name=link["username"])
                return web.Response(text=f"DENIED|JOIN {INVITE.upper()} FIRST")
            if owner and owner != link["user_id"]:
                await log_event(row["id"], "activation_denied", f"used by other account {link['user_id']}")
                await self.key_log(row, "🚫 Someone tried to use another person's key", link["user_id"],
                                   (("🔐 Key owner", f"<@{owner}> · `{owner}`"),
                                    ("❌ Refused", "Possible key sharing"),
                                    ("🔎 Look up", f"`/key list member:` → pick <@{link['user_id']}> to see their own keys")),
                                   DANGER, who_label="🕵️ Tried by", name=link["username"])
                return web.Response(text="DENIED|THIS KEY BELONGS TO ANOTHER DISCORD ACCOUNT")
            if not owner:  # first use locks the key to this account
                await db.execute("UPDATE licenses SET user_id = ? WHERE id = ? AND user_id IS NULL", (link["user_id"], row["id"]))
                await log_event(row["id"], "bound", str(link["user_id"]))
                first_link = True
        started = False
        if row["expires_at"] is None and row["duration_days"]:  # first login starts a timed key's clock
            await db.execute("UPDATE licenses SET expires_at = ? WHERE id = ? AND expires_at IS NULL",
                             ((now() + timedelta(days=row["duration_days"])).isoformat(), row["id"]))
            await log_event(row["id"], "started", length_label(row["duration_days"]))
            started = True
        await db.execute("UPDATE licenses SET last_seen = ?, activations = activations + 1 WHERE id = ?", (now().isoformat(), row["id"]))
        await log_event(row["id"], "activated", "Macro login")
        first = started or first_link or not row["activations"]
        if first or self.should_log_login(row["id"], who):
            fresh = await db.fetch_one("SELECT * FROM licenses WHERE id = ?", (row["id"],))
            await self.key_log(
                fresh, "🟢 Key activated (first login)" if first else "🔓 Macro login", who,
                (("⏳ Length", expiry_text(fresh)), ("🔢 Logins", f"{fresh['activations']:,}"),
                 ("🔗 Locked to", "this account (first use)" if first_link else None),
                 ("🧾 Order", f"`{fresh['order_code']}`" if fresh["order_code"] else None)),
                SUCCESS)
        return web.Response(text="OK|LICENSE VALID")

    # ---------------------------------------------------------- commands ----

    @app_commands.command(description="Make new macro keys")
    @app_commands.describe(
        length="How long the key lasts (starts on its first login)",
        count="How many keys (1-25)",
        member="Give the key(s) to this member and DM them",
        note="A private note for staff",
        custom_days="Any other length in days (overrides length)",
    )
    @app_commands.choices(length=LENGTH_CHOICES)
    async def generate(self, interaction: discord.Interaction, length: str = "lifetime", count: app_commands.Range[int, 1, 25] = 1,
                       member: Optional[discord.Member] = None, note: Optional[app_commands.Range[str, 1, 200]] = None,
                       custom_days: Optional[app_commands.Range[int, 1, 3650]] = None):
        days = custom_days or LENGTHS.get(length, 0) or None
        await interaction.response.defer(ephemeral=True)
        codes = [await create_license(interaction.guild_id, days, user_id=member.id if member else None,
                                      created_by=interaction.user.id, note=note) for _ in range(count)]
        length = length_label(days) + (" (starts on first login)" if days else "")
        dm = ""
        if member:
            try:
                embed = ui.card("🔑 Your macro key" + ("s" if count > 1 else ""),
                                "\n".join(f"```{c}```" for c in codes) + f"\n**Length** · {length}\nPaste it into the macro's login screen.",
                                color=SUCCESS, guild=interaction.guild, section="Keys")
                await member.send(embed=embed)
                dm = f"\n📬 Sent to {member.mention} by DM."
            except discord.HTTPException:
                dm = f"\n📪 Couldn't DM {member.mention} (DMs closed). Give them the key yourself."
        await emit(interaction.guild, "keys", "🔑 Keys generated",
                   ui.kv(("Count", str(count)), ("Length", length), ("For", member.mention if member else None), ("By", interaction.user.mention), ("Note", note)),
                   SUCCESS, subject=member.id if member else None)
        body = "\n".join(f"`{c}`" for c in codes)
        await interaction.followup.send(embed=ui.card(f"🔑 {count} key{'s' if count > 1 else ''} made · {length}", body + dm, color=SUCCESS,
                                                      guild=interaction.guild, section="Keys"), ephemeral=True)

    @app_commands.command(description="Look up a key")
    @app_commands.autocomplete(code=key_autocomplete)
    async def info(self, interaction: discord.Interaction, code: str):
        row = await find_license(code)
        await interaction.response.send_message(embed=license_embed(row, interaction.guild), ephemeral=True)

    @app_commands.command(description="Recent keys (optionally one member's)")
    @app_commands.describe(member="Only this member's keys", status="Filter by status")
    @app_commands.choices(status=[app_commands.Choice(name=n, value=n) for n in ("all", "active", "disabled", "expired")])
    async def list(self, interaction: discord.Interaction, member: Optional[discord.Member] = None, status: str = "all"):
        sql, params = "SELECT * FROM licenses", []
        if member:
            sql += " WHERE user_id = ?"
            params.append(member.id)
        rows = await db.fetch_all(sql + " ORDER BY id DESC LIMIT 200", tuple(params))
        wanted = {"active": "✅", "disabled": "⛔", "expired": "⌛"}.get(status)
        if wanted:
            rows = [r for r in rows if status_text(r).startswith(wanted)]
        if not rows:
            raise UserError("No keys match that.")
        total = (await db.fetch_one("SELECT COUNT(*) AS c, COALESCE(SUM(active), 0) AS a FROM licenses"))
        lines = [f"{status_text(r)[:1]} `{r['code']}` · {('<@' + str(r['user_id']) + '>') if r['user_id'] else (r['owner_name'] or 'unassigned')} · "
                 f"{expiry_text(r)}" for r in rows[:20]]
        more = f"\n…and {len(rows) - 20} more" if len(rows) > 20 else ""
        await interaction.response.send_message(
            embed=ui.card("🔑 Keys", "\n".join(lines) + more, guild=interaction.guild,
                          footer=f"{total['c']:,} keys in total · {total['a']:,} enabled"), ephemeral=True)

    async def set_active(self, interaction: discord.Interaction, code: str, active: bool):
        row = await find_license(code)
        await db.execute("UPDATE licenses SET active = ? WHERE id = ?", (int(active), row["id"]))
        await log_event(row["id"], "activated" if active else "deactivated", f"by {interaction.user.id}")
        await emit(interaction.guild, "keys", "🔑 Key enabled" if active else "⛔ Key disabled",
                   ui.kv(("Key", f"`{row['code']}`"), ("Owner", f"<@{row['user_id']}>" if row["user_id"] else None), ("By", interaction.user.mention)),
                   subject=row["user_id"])
        row = await find_license(code)
        await interaction.response.send_message(embed=license_embed(row, interaction.guild), ephemeral=True)

    @app_commands.command(description="Stop a key from working (e.g. refund or sharing)")
    @app_commands.autocomplete(code=key_autocomplete)
    async def disable(self, interaction: discord.Interaction, code: str):
        await self.set_active(interaction, code, False)

    @app_commands.command(description="Turn a disabled key back on")
    @app_commands.autocomplete(code=key_autocomplete)
    async def enable(self, interaction: discord.Interaction, code: str):
        await self.set_active(interaction, code, True)

    @app_commands.command(description="Add days to a key (or make it lifetime)")
    @app_commands.describe(days="Days to add (from today if it already expired)", lifetime="Make it never expire")
    @app_commands.autocomplete(code=key_autocomplete)
    async def extend(self, interaction: discord.Interaction, code: str, days: Optional[app_commands.Range[int, 1, 3650]] = None,
                     lifetime: bool = False):
        row = await find_license(code)
        if lifetime:
            await db.execute("UPDATE licenses SET expires_at = NULL, duration_days = NULL WHERE id = ?", (row["id"],))
        elif days and row["expires_at"] is None and row["duration_days"]:  # not started yet: make it longer
            await db.execute("UPDATE licenses SET duration_days = duration_days + ? WHERE id = ?", (days, row["id"]))
        elif days and row["expires_at"] is None:
            raise UserError("That key is already lifetime.")
        elif days:
            base = max(parse_time(row["expires_at"]) or now(), now())
            await db.execute("UPDATE licenses SET expires_at = ? WHERE id = ?", ((base + timedelta(days=days)).isoformat(), row["id"]))
        else:
            raise UserError("Fill in `days` or set `lifetime` to True.")
        await log_event(row["id"], "extended", "lifetime" if lifetime else f"+{days}d")
        row = await find_license(code)
        await interaction.response.send_message(embed=license_embed(row, interaction.guild), ephemeral=True)

    @app_commands.command(description="Give a key to a member (or move it to someone else)")
    @app_commands.autocomplete(code=key_autocomplete)
    async def assign(self, interaction: discord.Interaction, code: str, member: discord.Member):
        row = await find_license(code)
        await db.execute("UPDATE licenses SET user_id = ? WHERE id = ?", (member.id, row["id"]))
        await log_event(row["id"], "assigned", str(member.id))
        row = await find_license(code)
        await interaction.response.send_message(embed=license_embed(row, interaction.guild), ephemeral=True)

    @app_commands.command(description="Delete a key for good")
    @app_commands.autocomplete(code=key_autocomplete)
    async def delete(self, interaction: discord.Interaction, code: str):
        row = await find_license(code)
        await db.execute("DELETE FROM license_events WHERE license_id = ?", (row["id"],))
        await db.execute("DELETE FROM licenses WHERE id = ?", (row["id"],))
        await db.execute("UPDATE orders SET license_code = NULL WHERE license_code = ?", (row["code"],))
        await interaction.response.send_message(embed=ui.card("🗑️ Key deleted", f"`{row['code']}` no longer exists.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Bring over keys from the old license website (its CSV export)")
    @app_commands.describe(csv_file="The coop-licenses.csv from the old dashboard's Export button")
    async def importcsv(self, interaction: discord.Interaction, csv_file: discord.Attachment):
        if csv_file.size > 5_000_000:
            raise UserError("That file is too big (5 MB max).")
        await interaction.response.defer(ephemeral=True)
        try:
            text = (await csv_file.read()).decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
        except Exception:
            raise UserError("I couldn't read that file. Upload the CSV exactly as the old dashboard exported it.") from None
        if not rows or "code" not in rows[0]:
            raise UserError("That CSV has no `code` column. Use the old dashboard's **Export CSV** file.")
        added = skipped = 0
        for r in rows:
            code = clean_code(r.get("code"))
            if not code or await db.fetch_one("SELECT 1 FROM licenses WHERE code = ?", (code,)):
                skipped += 1
                continue
            active = str(r.get("active", "true")).strip().lower() in ("1", "true", "t", "yes")
            created = parse_time(r.get("created_at")) or now()
            expires = parse_time(r.get("expires_at"))
            last = parse_time(r.get("last_seen"))
            try:
                activations = int(float(r.get("activations") or 0))
            except ValueError:
                activations = 0
            await db.execute(
                "INSERT INTO licenses (code, active, created_at, expires_at, last_seen, activations, guild_id, owner_name, created_by, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, int(active), created.isoformat(), expires.isoformat() if expires else None, last.isoformat() if last else None,
                 activations, interaction.guild_id, (r.get("owner") or "").strip()[:120] or None, interaction.user.id,
                 (r.get("notes") or "").strip()[:300] or None),
            )
            added += 1
        await log_event(None, "imported", f"{added} keys from {csv_file.filename}")
        await interaction.followup.send(
            embed=ui.card("📥 Keys imported", ui.kv(("Added", f"{added:,}"), ("Skipped (already here or blank)", f"{skipped:,}"))
                          + "\n\nThose keys work in the macro straight away. Once you've checked, you can shut the old website down.",
                          color=SUCCESS, guild=interaction.guild, section="Keys"), ephemeral=True)

    @app_commands.command(description="The web address to put in the macro, and a quick health check")
    async def setup(self, interaction: discord.Interaction):
        base = public_base_url()
        total = await db.fetch_one("SELECT COUNT(*) AS c FROM licenses")
        selling = await db.fetch_all("SELECT name, license_days FROM products WHERE guild_id = ? AND license_days IS NOT NULL", (interaction.guild_id,))
        if base:
            where = (f"The macro checks keys at:\n```{base}```\nIn the macro script set:\n"
                     f"```global LICENSE_API_URL := \"{base}\"```")
        else:
            where = ("⚠️ I don't know my public web address yet. In Railway open this bot's service → **Settings → Networking → "
                     "Generate Domain**, redeploy, then run this again.")
        products = "\n".join(f"• **{p['name']}** · {length_label(p['license_days'])} key" for p in selling) \
            or "None yet. Use `/shop edit product:<name> license_key:<length>`."
        embed = ui.card("🔑 Key system", where, guild=interaction.guild, section="Keys")
        embed.add_field(name="Products that give a key", value=products, inline=False)
        embed.add_field(name="Keys stored", value=f"{total['c']:,}", inline=True)
        if self.linking_enabled():
            linking = f"✅ On. People must link their Discord before a key works (remembered {LINK_DAYS} days)."
        else:
            cb = redirect_uri() or "https://<your bot address>/auth/callback"
            linking = ("⚠️ Off. To turn it on:\n"
                       "**1.** Discord Developer Portal → your bot's app → **OAuth2**.\n"
                       f"**2.** Under **Redirects** add:\n```{cb}```"
                       "**3.** Copy the **Client Secret** (Reset Secret if it's hidden).\n"
                       "**4.** Railway → this bot → **Variables** → add `DISCORD_CLIENT_SECRET` = that secret, then redeploy.")
        embed.add_field(name="🔗 Discord linking", value=linking, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def my_keys(user_id: int) -> list[str]:
    """A member's keys, one line each, for /myorders (which replaced /mykeys)."""
    rows = await db.fetch_all("SELECT * FROM licenses WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,))
    return [f"`{r['code']}` · {status_text(r)} · {expiry_text(r)}" for r in rows]


async def setup(bot: commands.Bot):
    await bot.add_cog(Keys(bot))
