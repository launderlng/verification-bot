"""Macro license keys, run by the bot itself (replaces the separate license website).

* The macro checks a key with  POST /api/activate  (form field `code`) and gets back
  `OK|LICENSE VALID` or `DENIED|<reason>` - the same format the old website used, so the
  macro only needs the bot's web address.
* Products can hand out a key automatically: `/shop edit product:<name> license_days:<n>`
  (0 = lifetime). Every Stripe purchase or /shop createorder for that product gets a fresh
  key in the receipt DM.
* Admins manage keys with /key ..., members see theirs with /mykeys.
"""
import csv
import io
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import SUCCESS, WARN, UserError
from fileutil import public_base_url
from logutil import emit

log = logging.getLogger("verification-bot")

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I mix-ups
PREFIX = "".join(c for c in os.getenv("LICENSE_PREFIX", "XZX").upper() if c.isalnum())[:8] or "XZX"
RATE_LIMIT = 30          # key checks allowed per IP ...
RATE_WINDOW = 60         # ... per this many seconds


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
    """Make a new unique key. days None/0 = lifetime."""
    created = now()
    expires = (created + timedelta(days=days)).isoformat() if days else None
    for _ in range(40):
        code = new_code()
        if await db.fetch_one("SELECT 1 FROM licenses WHERE code = ?", (code,)):
            continue
        await db.execute(
            "INSERT INTO licenses (code, active, created_at, expires_at, activations, guild_id, user_id, order_code, created_by, note) "
            "VALUES (?, 1, ?, ?, 0, ?, ?, ?, ?, ?)",
            (code, created.isoformat(), expires, guild_id, user_id, order_code, created_by, note),
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
    if product is None or product["guild_id"] != guild_id or product["license_days"] is None:
        return None
    code = await create_license(guild_id, product["license_days"] or None, user_id=order["user_id"], order_code=order["code"],
                                note=f"Purchase of {order['product_name']}")
    await db.execute("UPDATE orders SET license_code = ? WHERE id = ?", (code, order["id"]))
    return code


async def receipt_line(order) -> Optional[str]:
    """The key block for a receipt DM, or None if the order has no key."""
    if not order or not order["license_code"]:
        return None
    row = await db.fetch_one("SELECT * FROM licenses WHERE code = ?", (order["license_code"],))
    length = expiry_text(row) if row else "Lifetime"
    return f"```{order['license_code']}```**Valid** · {length}\nPaste it into the macro's login screen. `/mykeys` shows it any time."


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


# -------------------------------------------------------------- cog ----

@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Keys(commands.GroupCog, group_name="key", group_description="Macro license keys"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.hits: dict[str, list[float]] = {}
        super().__init__()

    async def cog_load(self):
        app = getattr(self.bot, "web_app", None)
        if app is not None:
            app.router.add_post("/api/activate", self.handle_activate)
            log.info("License check route is ready at /api/activate")

    # ------------------------------------------------- macro key check ----

    def limited(self, ip: str) -> bool:
        t = time.monotonic()
        recent = [x for x in self.hits.get(ip, []) if t - x < RATE_WINDOW]
        recent.append(t)
        self.hits[ip] = recent
        if len(self.hits) > 5000:  # keep memory bounded
            self.hits = {k: v for k, v in self.hits.items() if v and t - v[-1] < RATE_WINDOW}
        return len(recent) > RATE_LIMIT

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
        if not row["active"]:
            await log_event(row["id"], "activation_denied", "Disabled")
            return web.Response(text="DENIED|KEY DISABLED")
        exp = parse_time(row["expires_at"])
        if exp is not None and exp <= now():
            await log_event(row["id"], "activation_denied", "Expired")
            return web.Response(text="DENIED|KEY EXPIRED")
        await db.execute("UPDATE licenses SET last_seen = ?, activations = activations + 1 WHERE id = ?", (now().isoformat(), row["id"]))
        await log_event(row["id"], "activated", "Macro login")
        return web.Response(text="OK|LICENSE VALID")

    # ---------------------------------------------------------- commands ----

    @app_commands.command(description="Make new macro keys")
    @app_commands.describe(
        count="How many keys (1-25)",
        days="How long they last in days (leave empty for lifetime)",
        member="Give the key(s) to this member and DM them",
        note="A private note for staff",
    )
    async def generate(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 25] = 1,
                       days: Optional[app_commands.Range[int, 1, 3650]] = None, member: Optional[discord.Member] = None,
                       note: Optional[app_commands.Range[str, 1, 200]] = None):
        await interaction.response.defer(ephemeral=True)
        codes = [await create_license(interaction.guild_id, days, user_id=member.id if member else None,
                                      created_by=interaction.user.id, note=note) for _ in range(count)]
        length = f"{days} days" if days else "Lifetime"
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
        await emit(interaction.guild, "shop", "🔑 Keys generated",
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
                 f"{'Lifetime' if not r['expires_at'] else expiry_text(r)}" for r in rows[:20]]
        more = f"\n…and {len(rows) - 20} more" if len(rows) > 20 else ""
        await interaction.response.send_message(
            embed=ui.card("🔑 Keys", "\n".join(lines) + more, guild=interaction.guild,
                          footer=f"{total['c']:,} keys in total · {total['a']:,} enabled"), ephemeral=True)

    async def set_active(self, interaction: discord.Interaction, code: str, active: bool):
        row = await find_license(code)
        await db.execute("UPDATE licenses SET active = ? WHERE id = ?", (int(active), row["id"]))
        await log_event(row["id"], "activated" if active else "deactivated", f"by {interaction.user.id}")
        await emit(interaction.guild, "shop", "🔑 Key enabled" if active else "⛔ Key disabled",
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
            new_exp = None
        elif days:
            base = max(parse_time(row["expires_at"]) or now(), now())
            new_exp = (base + timedelta(days=days)).isoformat()
        else:
            raise UserError("Fill in `days` or set `lifetime` to True.")
        await db.execute("UPDATE licenses SET expires_at = ? WHERE id = ?", (new_exp, row["id"]))
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
        products = "\n".join(f"• **{p['name']}** · {'Lifetime' if not p['license_days'] else str(p['license_days']) + ' days'}" for p in selling) \
            or "None yet. Use `/shop edit product:<name> license_days:0` (0 = lifetime)."
        embed = ui.card("🔑 Key system", where, guild=interaction.guild, section="Keys")
        embed.add_field(name="Products that give a key", value=products, inline=False)
        embed.add_field(name="Keys stored", value=f"{total['c']:,}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.guild_only()
class MyKeys(commands.Cog):
    @app_commands.command(description="See your macro keys")
    async def mykeys(self, interaction: discord.Interaction):
        rows = await db.fetch_all("SELECT * FROM licenses WHERE user_id = ? ORDER BY id DESC LIMIT 10", (interaction.user.id,))
        if not rows:
            raise UserError("You don't have any keys yet. Buy one from the shop with `/store`.")
        lines = [f"```{r['code']}```{status_text(r)} · {expiry_text(r)}" for r in rows]
        await interaction.response.send_message(
            embed=ui.card("🔑 Your keys", "\n".join(lines) + "\nPaste a key into the macro's login screen.", guild=interaction.guild, section="Keys"),
            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Keys(bot))
    await bot.add_cog(MyKeys())
