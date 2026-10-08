import json
import logging
import os
import re
import secrets
import urllib.parse
import uuid
from typing import Optional

import aiosqlite
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import db
import ui
from cogs.files import file_autocomplete, file_payload, record_delivery
from common import ACCENT, COLOR, INFO, SUCCESS, WARN, UserError, check_can_send, parse_color
from fileutil import ATTACH_LIMIT, LINK_SECONDS, human_size
from logutil import emit
from stripeutil import format_amount, make_ref, parse_ref, tracked_url, verify_signature, webhook_secrets

log = logging.getLogger("verification-bot")

DEFAULT_BUTTON = "Buy now"
DEFAULT_FOOTER = "🔒 Secure checkout by Stripe"
MAX_PRODUCTS = 25  # Discord autocomplete shows 25 choices at most
HANDLED_EVENTS = {"checkout.session.completed", "checkout.session.async_payment_succeeded"}

# Stripe-hosted checkout pages. If you set up a custom domain for your Payment Links in Stripe
# (e.g. pay.example.com), add it with the STRIPE_EXTRA_HOSTS variable (comma-separated).
STRIPE_HOSTS = {"buy.stripe.com", "checkout.stripe.com", "donate.stripe.com"}
STRIPE_HOSTS |= {h.strip().lower() for h in os.getenv("STRIPE_EXTRA_HOSTS", "").split(",") if h.strip()}

HOWTO = (
    "**1.** Sign in to your **Stripe Dashboard** and open **Payment Links**.\n"
    "**2.** Click **+ New**, pick (or add) your product and price, then click **Create link**.\n"
    "**3.** Copy the link. It looks like `https://buy.stripe.com/xxxxxxxx`.\n"
    "**4.** Run `/shop add` and paste it into `stripe_link`.\n\n"
    "Tip: a link containing `/test_` is a **test-mode** link. No real money moves, so swap in your live link before launching.\n"
    "Want buyers to get an Invoice ID by DM after paying? Run `/shop webhook`."
)


# ---------------------------------------------------------- helpers ----

def stripe_url(url: str) -> str:
    """Only accept https links on Stripe's checkout hosts."""
    url = url.strip()
    if len(url) > 400:
        raise UserError("That link is too long. Discord buttons allow 512 characters and I need room to add the buyer's ID.")
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in STRIPE_HOSTS or parsed.username or parsed.password or parsed.path in ("", "/"):
        raise UserError(
            "That isn't a Stripe Payment Link. It should start with `https://buy.stripe.com/`.\n"
            "Create one in your Stripe Dashboard under **Payment Links → + New**, or run `/shop howto`."
        )
    return url


def is_test_link(url: str) -> bool:
    return urllib.parse.urlparse(url).path.startswith("/test_")


def image_link(url: str) -> Optional[str]:
    if url.strip().lower() == "none":
        return None
    if not url.startswith("https://"):
        raise UserError("Images must be direct links starting with `https://` (or type `none` to remove).")
    return url


def pick(*values):
    for value in values:
        if value:
            return value
    return None


def build_product(guild: discord.Guild, p, shop, rating=None) -> discord.Embed:
    """A clean product card: description, then Price / Rating / Stock in one tidy row."""
    color_hex = pick(p["color"], shop["color"] if shop else None)
    average, count = rating or (None, 0)
    embed = ui.card(
        p["name"], p["description"] or None, color=int(color_hex, 16) if color_hex else ACCENT.value,
        guild=guild, footer=pick(shop["footer"] if shop else None, DEFAULT_FOOTER),
        thumbnail=guild.icon.url if guild.icon else None, image=p["image_url"] or None,
    )
    embed.add_field(name="💰 Price", value=f"**{p['price'] or 'See checkout'}**")
    embed.add_field(name="⭐ Rating", value=ui.rating_line(average, count))
    stock = "🟢 In stock" if p["available"] else "🔴 Sold out"
    embed.add_field(name="📦 Stock", value=stock + ("\n📥 Instant delivery by DM" if p["file_id"] else "") + (f"\n🎭 Gives you <@&{p['role_id']}>" if p["role_id"] else ""))
    return embed


def buy_item(p, shop):
    """The Buy button: tracked (personal link) when the webhook is on, otherwise a plain link."""
    label = pick(p["button_label"], shop["button_label"] if shop else None, DEFAULT_BUTTON)
    if webhook_secrets():
        return BuyButton(p["id"], label if p["available"] else "Sold out", disabled=not p["available"])
    return discord.ui.Button(
        style=discord.ButtonStyle.link, label=label if p["available"] else "Sold out", emoji="🛒",
        url=p["buy_url"], disabled=not p["available"],
    )


def build_view(p, shop) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(buy_item(p, shop))
    return view


async def product_message(guild: discord.Guild, p) -> tuple:
    """(embed, view) for a product, including its live star rating."""
    shop = await db.get_shop(guild.id)
    rating = await db.product_rating(guild.id, p["name"])
    return build_product(guild, p, shop, rating), build_view(p, shop)


async def product_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    rows = await db.fetch_all(
        "SELECT name FROM products WHERE guild_id = ? AND name LIKE ? ORDER BY name LIMIT 25", (interaction.guild_id, f"%{current}%")
    )
    return [app_commands.Choice(name=r["name"], value=r["name"]) for r in rows]


async def resolve_product(interaction: discord.Interaction, name: Optional[str]):
    if name:
        row = await db.fetch_one("SELECT * FROM products WHERE guild_id = ? AND name = ?", (interaction.guild_id, name.strip()))
        if not row:
            raise UserError(f"I can't find a product called **{name}**. Try `/shop list`.")
        return row
    rows = await db.fetch_all("SELECT * FROM products WHERE guild_id = ?", (interaction.guild_id,))
    if not rows:
        raise UserError("There are no products yet. An admin can add one with `/shop add`.")
    if len(rows) > 1:
        raise UserError("There are several products, so pick one with the `product` option.")
    return rows[0]


def test_note(url: str) -> str:
    return "\n⚠️ That's a **Stripe test-mode** link. No real money moves, so swap in your live link before launch." if is_test_link(url) else ""


def fulfilment_text(o) -> str:
    """How an order was sent: by the bot on its own, or by a named staff member."""
    if o["fulfillment"] == "manual" or o["source"] == "manual":
        return f"✋ Manual, by <@{o['processed_by']}>" if o["processed_by"] else "✋ Manual"
    return "🤖 Automatic (no staff involved)"


def order_embed(o, title: str = "🧾 Order") -> discord.Embed:
    body = ui.kv(
        ("🧾 Invoice ID", f"`{o['code']}`"), ("👤 Buyer", f"<@{o['user_id']}>"), ("📦 Product", o["product_name"]), ("💰 Amount", o["amount"] or "—"),
        ("📅 Paid", discord.utils.format_dt(discord.utils.parse_time(o["created_at"]), "f")),
        ("📬 Receipt DM", "✅ Delivered" if o["dm_sent"] else "❌ Couldn't DM"),
        ("📥 File", {"delivered": "✅ Delivered", "failed": "❌ Not delivered (use /shop resend)"}.get(o["file_status"])),
        ("🎭 Role", {"granted": "✅ Given", "failed": "❌ Not given (use /shop resend)", "removed": "↩️ Taken back"}.get(o["role_status"])),
        ("🧰 Created by", "Staff (manual order)" if o["source"] == "manual" else None), ("⚙️ Fulfilment", fulfilment_text(o)),
        ("🕒 Processed", discord.utils.format_dt(discord.utils.parse_time(o["processed_at"]), "f") if o["processed_at"] else None), ("📝 Note", o["note"]),
        ("🔖 Stripe reference", f"`{o['stripe_ref']}`" if o["stripe_ref"] and o["source"] != "manual" else None),
    )
    return ui.card(title, body, color=SUCCESS if o["livemode"] else WARN, footer="🧪 Test-mode payment" if not o["livemode"] else "Shop orders")


# --------------------------------------------------------------- sold roles ----

POWERFUL = ("administrator", "manage_guild", "manage_roles", "manage_channels", "manage_webhooks", "ban_members", "kick_members",
            "mention_everyone", "manage_messages", "moderate_members")


def check_role_sellable(guild: discord.Guild, role: discord.Role) -> None:
    """A product can only give a role I'm able to hand out, and never one with staff powers."""
    if role.is_default():
        raise UserError("That's @everyone. Pick a real role like Premium.")
    if role.managed:
        raise UserError("That role belongs to a bot or integration, so I can't hand it out.")
    risky = [name.replace("_", " ").title() for name in POWERFUL if getattr(role.permissions, name, False)]
    if risky:
        raise UserError(f"{role.mention} has staff powers ({', '.join(risky[:3])}). Buyers shouldn't get it automatically. Pick a role without them, like Premium.")
    if not guild.me.guild_permissions.manage_roles:
        raise UserError("I need the **Manage Roles** permission to hand out roles.")
    if role >= guild.me.top_role:
        raise UserError(f"{role.mention} is above (or level with) my highest role, so I can't give it out. In **Server Settings → Roles** drag **my role above {role.name}**, then try again.")


# ------------------------------------------------------------ order IDs ----

PREFIX_RE = re.compile(r"^[A-Z0-9]{2,10}$")


def example_code(prefix: str, style: str) -> str:
    return f"{prefix}-0001" if style == "sequential" else f"{prefix}-A1B2C3D4"


async def allocate_code(guild_id: int) -> str:
    """Create a new order ID using the server's prefix and style (random like INV-3F9A1C2E, or numbered like INV-0042)."""
    shop = await db.get_shop(guild_id)
    prefix = (shop["order_prefix"] if shop and shop["order_prefix"] else "INV")
    if shop and shop["order_style"] == "sequential":
        await db.execute("INSERT OR IGNORE INTO shop_settings (guild_id) VALUES (?)", (guild_id,))
        await db.execute("UPDATE shop_settings SET order_counter = order_counter + 1 WHERE guild_id = ?", (guild_id,))
        number = (await db.get_shop(guild_id))["order_counter"]
        return f"{prefix}-{number:04d}"
    return f"{prefix}-{secrets.token_hex(4).upper()}"


async def find_order(guild_id: int, text: str):
    """Find an order by its ID. Also accepts the ID without its prefix (3F9A1C2E)."""
    code = text.strip().upper()
    row = await db.fetch_one("SELECT * FROM orders WHERE guild_id = ? AND code = ?", (guild_id, code))
    if row or "-" in code:
        return row
    matches = await db.fetch_all("SELECT * FROM orders WHERE guild_id = ? AND code LIKE ? LIMIT 2", (guild_id, f"%-{code}"))
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------- UI ----

class BuyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"shop:buy:(?P<id>[0-9]+)"):
    """Persistent 'Buy now' button. Pressing it gives the buyer a personal Stripe link."""

    def __init__(self, product_id: int, label: str = DEFAULT_BUTTON, disabled: bool = False):
        super().__init__(discord.ui.Button(
            label=label, style=discord.ButtonStyle.success, emoji="🛒", custom_id=f"shop:buy:{product_id}", disabled=disabled,
        ))
        self.product_id = product_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        p = await db.get_product_by_id(self.product_id)
        if not p or p["guild_id"] != interaction.guild_id:
            return await interaction.response.send_message("That product isn't available any more.", ephemeral=True)
        if not p["available"]:
            return await interaction.response.send_message("😔 That one is sold out right now.", ephemeral=True)
        known = webhook_secrets()
        url = tracked_url(p["buy_url"], make_ref(interaction.guild_id, interaction.user.id, p["id"], known[0])) if known else p["buy_url"]
        view = discord.ui.View()
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Continue to Stripe", emoji="💳", url=url))
        embed = ui.card("💳 Ready to check out", f"**{p['name']}** · {p['price'] or ''}\n\n"
                "Press the button below to open Stripe's secure checkout page.\n"
                "After you pay, I'll **DM you your Invoice ID**. Keep your DMs open, and you can always look it up later with `/myorders`.", color=COLOR, section="Shop")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class DescriptionModal(discord.ui.Modal):
    def __init__(self, cog: "Shop", product):
        super().__init__(title=f"Describe {product['name']}"[:45])
        self.cog, self.product_id = cog, product["id"]
        self.text = discord.ui.TextInput(
            label="Description", style=discord.TextStyle.paragraph, required=False, max_length=2000,
            placeholder="What does the buyer get? What's included?", default=(product["description"] or "")[:2000] or None,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        await db.update_product(self.product_id, description=self.text.value.strip() or None)
        p = await db.get_product_by_id(self.product_id)
        refreshed = await self.cog.refresh_post(interaction.guild, p)
        await interaction.response.send_message(
            "✅ Description saved." + (" The posted card was updated too." if refreshed else " Use `/shop post` to publish it."), ephemeral=True
        )


async def catalog_embed(guild: discord.Guild, products) -> discord.Embed:
    lines = []
    for p in products:
        average, count = await db.product_rating(guild.id, p["name"])
        status = "🟢 In stock" if p["available"] else "🔴 Sold out"
        lines.append(f"**{p['name']}** · `{p['price'] or '—'}`\n　{ui.rating_line(average, count)} · {status}")
    return ui.card(
        "🛍️ Store", "\n\n".join(lines) if lines else "Nothing for sale yet.", color=ACCENT, guild=guild,
        footer=f"{guild.name} · Pick a product below to see details",
        thumbnail=guild.icon.url if guild.icon else None,
    )


class StoreSelect(discord.ui.Select):
    def __init__(self, products, selected_id=None):
        options = [
            discord.SelectOption(
                label=p["name"][:100], value=str(p["id"]), description=(p["price"] or "")[:100] or None,
                emoji="🟢" if p["available"] else "🔴", default=(p["id"] == selected_id),
            )
            for p in products[:25]
        ]
        super().__init__(placeholder="Pick a product to see details…", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        p = await db.get_product_by_id(int(self.values[0]))
        if not p or p["guild_id"] != interaction.guild_id:
            return await interaction.response.send_message("That product isn't available any more.", ephemeral=True)
        products = await db.fetch_all("SELECT * FROM products WHERE guild_id = ? ORDER BY name", (interaction.guild_id,))
        embed, _ = await product_message(interaction.guild, p)
        await interaction.response.edit_message(embed=embed, view=StoreView(products, await db.get_shop(interaction.guild_id), selected=p))


class BackButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Back to store", emoji="⬅️", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        products = await db.fetch_all("SELECT * FROM products WHERE guild_id = ? ORDER BY name", (interaction.guild_id,))
        if not products:
            return await interaction.response.edit_message(embed=ui.card("🛍️ Store", "Nothing for sale yet."), view=None)
        await interaction.response.edit_message(
            embed=await catalog_embed(interaction.guild, products), view=StoreView(products, await db.get_shop(interaction.guild_id))
        )


class StoreView(discord.ui.View):
    """Catalogue with a dropdown. Picking a product shows its card, a Buy button and a way back."""

    def __init__(self, products, shop, selected=None):
        super().__init__(timeout=300)
        self.add_item(StoreSelect(products, selected["id"] if selected else None))
        if selected:
            self.add_item(buy_item(selected, shop))
            self.add_item(BackButton())


# -------------------------------------------------------------- cog ----

@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Shop(commands.GroupCog, group_name="shop", group_description="Sell products with a Stripe Buy button"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def cog_load(self):
        self.bot.add_dynamic_items(BuyButton)
        app = getattr(self.bot, "web_app", None)
        if webhook_secrets() and app is not None:
            app.router.add_post("/stripe/webhook", self.handle_webhook)
            log.info("Stripe webhook route is ready at /stripe/webhook")
        elif not webhook_secrets():
            log.info("STRIPE_WEBHOOK_SECRET isn't set, so purchase DMs are off (run /shop webhook for setup help)")

    async def cog_unload(self):
        try:
            self.bot.remove_dynamic_items(BuyButton)
        except Exception:
            pass

    # ----------------------------------------------------- stripe webhook ----

    async def handle_webhook(self, request: web.Request) -> web.Response:
        body = await request.read()
        if not verify_signature(body, request.headers.get("Stripe-Signature", ""), webhook_secrets()):
            log.warning("Rejected a webhook call with a bad or missing Stripe signature")
            return web.Response(status=400, text="invalid signature")
        try:
            event = json.loads(body)
        except ValueError:
            return web.Response(status=400, text="invalid json")
        try:
            await self.process_event(event)
        except Exception:
            log.exception("Error while processing a Stripe event")
            return web.Response(status=500, text="error")  # Stripe will retry
        return web.Response(text="ok")

    async def insert_order(self, guild_id: int, user_id: int, product, amount, session_id: str, stripe_ref, livemode: bool,
                           source: str = "stripe", note: Optional[str] = None, processed_by: Optional[int] = None) -> Optional[str]:
        """Create the order and return its ID, or None if this payment was already recorded."""
        name = product["name"] if product and product["guild_id"] == guild_id else "Your purchase"
        file_id = product["file_id"] if product and product["guild_id"] == guild_id else None
        role_id = product["role_id"] if product and product["guild_id"] == guild_id else None
        for _ in range(8):
            code = await allocate_code(guild_id)
            try:
                await db.execute(
                    "INSERT INTO orders (guild_id, code, user_id, product_id, product_name, amount, stripe_session_id, stripe_ref, livemode, created_at, file_id, source, note, role_id, "
                    "processed_by, fulfillment, processed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (guild_id, code, user_id, product["id"] if product else None, name, amount, session_id, stripe_ref, int(livemode),
                     discord.utils.utcnow().isoformat(), file_id, source, note, role_id, processed_by, "manual" if processed_by else "auto", discord.utils.utcnow().isoformat()),
                )
                return code
            except aiosqlite.IntegrityError:
                if await db.fetch_one("SELECT 1 FROM orders WHERE stripe_session_id = ?", (session_id,)):
                    return None  # Stripe delivered the same event twice
        raise RuntimeError("Couldn't generate a unique order ID")

    async def create_order(self, guild_id: int, user_id: int, product, session: dict, livemode: bool) -> Optional[str]:
        session_id = session["id"]
        return await self.insert_order(
            guild_id, user_id, product, format_amount(session.get("amount_total"), session.get("currency")), session_id,
            session.get("invoice") or session.get("payment_intent") or session_id, livemode,
        )

    # --------------------------------------------------- receipt + file DM ----

    def receipt_embed(self, guild: discord.Guild, order, shop, support_channel_id: Optional[int], file_line: Optional[str] = None,
                      file_problem: bool = False, role_line: Optional[str] = None) -> discord.Embed:
        """The purchase DM, kept clean: your order ID big and copyable, product / paid / file in three columns, one line on getting help."""
        code = order["code"]
        embed = ui.card(
            "✅ Payment received" if order["source"] != "manual" else "✅ Order confirmed",
            f"Thanks for your order from **{guild.name}**! Everything you need is in this one message.\n\n**Your order ID**\n```{code}```",
            color=SUCCESS, thumbnail=guild.icon.url if guild.icon else None,
            footer=guild.name + (" · 🧪 TEST PAYMENT" if not order["livemode"] else ""),
        )
        embed.add_field(name="📦 Product", value=order["product_name"], inline=True)
        embed.add_field(name="💰 Paid", value=order["amount"] or "—", inline=True)
        if file_problem:
            embed.add_field(name="📥 Your file", value="⚠️ Couldn't attach it. Send your ID to support and we'll send it right away.", inline=True)
        elif file_line:
            embed.add_field(name="📥 Your file", value=file_line, inline=True)
        if role_line:
            embed.add_field(name="🎭 Your role", value=role_line, inline=True)
        where = f" in <#{support_channel_id}>" if support_channel_id else ""
        help_text = f"**Just use this ID.** Send `{code}` to support{where} or to any staff member. That's all we need."
        if shop and shop["receipt_note"]:
            help_text += f"\n{shop['receipt_note']}"
        embed.add_field(name="🎫 Need help?", value=help_text, inline=False)
        return embed

    async def deliver_order(self, guild: discord.Guild, user, order, product, shop, role_status: Optional[str] = None) -> tuple:
        """DM the buyer ONE message: receipt + Invoice ID + 'just use this ID for support' + their file. Returns (delivered, file_status)."""
        tickets_cfg = await db.get_ticket_config(guild.id)
        support_channel = (shop["ticket_channel_id"] if shop else None) or (tickets_cfg["panel_channel_id"] if tickets_cfg else None)
        stored = None
        if order["file_id"] or (product and product["file_id"]):
            stored = await db.fetch_one("SELECT * FROM stored_files WHERE id = ? AND guild_id = ?", (order["file_id"] or product["file_id"], guild.id))
        role_line = None
        role_id = order["role_id"] or (product["role_id"] if product else None)
        if role_id:
            role = guild.get_role(role_id)
            role_line = (f"✅ **{role.name}** added to your account" if role is not None else "✅ Added to your account") if role_status == "granted" else \
                "⚠️ Couldn't add it automatically. Send your ID to support and we'll add it."
        payload = None
        if stored is not None:
            linked = stored["size"] > ATTACH_LIMIT
            line = f"`{stored['filename']}`\n{human_size(stored['size'])} · " + (
                f"press **Download** below (private, valid {LINK_SECONDS // 60} min)" if linked else "attached below")
            payload = await file_payload(user, guild, stored, embed=self.receipt_embed(guild, order, shop, support_channel, line, role_line=role_line), support_channel_id=support_channel)
        if payload is not None:
            kwargs = payload
        else:  # no file for this product, or it couldn't be attached: send the receipt on its own
            kwargs = {"embed": self.receipt_embed(guild, order, shop, support_channel, file_problem=stored is not None, role_line=role_line)}
            if support_channel:
                view = discord.ui.View()
                view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Support", emoji="🎫", url=f"https://discord.com/channels/{guild.id}/{support_channel}"))
                kwargs["view"] = view
        delivered = False
        try:
            await user.send(**kwargs)
            delivered = True
        except discord.HTTPException:
            log.info("Couldn't DM the receipt for %s (DMs closed?)", order["code"])
        file_status = None
        if stored is not None:
            file_status = "delivered" if delivered and payload is not None else "failed"
            if file_status == "delivered":
                await record_delivery(stored["id"], user.id)
        await db.execute("UPDATE orders SET dm_sent = ?, file_status = ? WHERE id = ?", (int(delivered), file_status, order["id"]))
        return delivered, file_status

    async def grant_role(self, guild: discord.Guild, user, order, product) -> Optional[str]:
        """Give the buyer the role this product unlocks. Returns 'granted', 'failed', or None if the product has no role."""
        role_id = order["role_id"] or (product["role_id"] if product else None)
        if not role_id:
            return None
        role = guild.get_role(role_id)
        member = user if isinstance(user, discord.Member) else guild.get_member(user.id)
        status = "failed"
        if role is not None and member is not None:
            try:
                await member.add_roles(role, reason=f"Purchase {order['code']}")
                status = "granted"
            except discord.HTTPException:
                log.warning("Couldn't give role %s to %s for order %s (is my role above it?)", role_id, member.id, order["code"])
        await db.execute("UPDATE orders SET role_status = ? WHERE id = ?", (status, order["id"]))
        return status

    async def resolve_buyer(self, guild: discord.Guild, user_id: int):
        user = guild.get_member(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                user = None
        return user

    async def process_event(self, event: dict) -> None:
        if event.get("type") not in HANDLED_EVENTS:
            return
        session = (event.get("data") or {}).get("object") or {}
        # Delayed payment methods (e.g. bank debits) complete later and send async_payment_succeeded instead
        if session.get("payment_status") != "paid" or not session.get("id"):
            return
        parsed = parse_ref(session.get("client_reference_id"), webhook_secrets())
        if not parsed:
            log.info("Ignoring a Stripe payment that didn't come from a Buy button")
            return
        guild_id, user_id, product_id = parsed
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            log.warning("Got a payment for guild %s, which the bot isn't in", guild_id)
            return
        if await db.fetch_one("SELECT 1 FROM orders WHERE stripe_session_id = ?", (session["id"],)):
            return  # already handled

        livemode = bool(event.get("livemode", True))
        product = await db.get_product_by_id(product_id)
        code = await self.create_order(guild_id, user_id, product, session, livemode)
        if code is None:
            return
        order = await db.fetch_one("SELECT * FROM orders WHERE guild_id = ? AND code = ?", (guild_id, code))
        shop = await db.get_shop(guild_id)

        user = await self.resolve_buyer(guild, user_id)
        delivered, file_status = False, ("failed" if order["file_id"] else None)
        role_status = "failed" if order["role_id"] else None
        if user is not None:
            role_status = await self.grant_role(guild, user, order, product) or role_status
            delivered, file_status = await self.deliver_order(guild, user, order, product, shop, role_status)
        else:
            await db.execute("UPDATE orders SET dm_sent = 0, file_status = ?, role_status = ? WHERE id = ?", (file_status, role_status, order["id"]))

        await emit(
            guild, "shop", "🧪 Test purchase" if not livemode else "💸 New purchase",
            f"<@{user_id}> bought **{order['product_name']}**\n\n"
            + ui.kv(("🕒 Ordered", discord.utils.format_dt(discord.utils.utcnow(), "f")), ("💰 Amount", order["amount"]), ("🧾 Invoice ID", f"`{code}`"), ("🆔 Buyer ID", f"`{user_id}`"),
                    ("⚙️ Sent", "🤖 Automatically, no staff involved"), ("📬 Receipt DM", "✅ delivered" if delivered else "❌ couldn't DM, they can use /myorders"),
                    ("📥 File", None if file_status is None else ("✅ delivered with the receipt" if file_status == "delivered" else "❌ not delivered, use /shop resend")),
                    ("🎭 Role", None if role_status is None else ("✅ given" if role_status == "granted" else "❌ not given: put my role above it, then use /shop resend"))),
            SUCCESS if livemode else WARN, subject=user_id,
        )

    # ---------------------------------------------------------- helpers ----

    async def refresh_post(self, guild: discord.Guild, p) -> bool:
        """Edit the already-posted card in place. Returns True if there was one and it was updated."""
        if not p["channel_id"] or not p["message_id"]:
            return False
        channel = guild.get_channel(p["channel_id"])
        if channel is None:
            return False
        try:
            message = await channel.fetch_message(p["message_id"])
            embed, view = await product_message(guild, p)
            await message.edit(embed=embed, view=view)
            return True
        except discord.HTTPException:
            return False

    async def delete_post(self, guild: discord.Guild, p) -> None:
        if not p["channel_id"] or not p["message_id"]:
            return
        channel = guild.get_channel(p["channel_id"])
        if channel is None:
            return
        try:
            await (await channel.fetch_message(p["message_id"])).delete()
        except discord.HTTPException:
            pass

    async def refresh_all(self, guild: discord.Guild) -> int:
        rows = await db.fetch_all("SELECT * FROM products WHERE guild_id = ? AND message_id IS NOT NULL", (guild.id,))
        done = 0
        for p in rows:
            if await self.refresh_post(guild, p):
                done += 1
        return done

    # --------------------------------------------------------- commands ----

    @app_commands.command(description="Shop-wide settings: channels, colour, button text, footer, receipts")
    @app_commands.describe(
        channel="Default channel for product cards",
        ticket_channel="Where buyers should open a ticket after paying",
        receipt_note="Extra text added to the receipt DM (type 'default' to clear)",
        color="Accent colour as hex, e.g. #635BFF",
        button_label="Default text on the buy button (type 'default' to reset)",
        footer="Text at the bottom of every card (type 'default' to reset)",
        id_prefix="Start of every order ID, 2 to 10 letters/numbers, e.g. 14K gives 14K-0001",
        id_style="Random IDs (INV-3F9A1C2E) or numbered in order (INV-0001)",
    )
    @app_commands.choices(id_style=[app_commands.Choice(name="Random (3F9A1C2E)", value="random"), app_commands.Choice(name="Numbered (0001, 0002…)", value="sequential")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        ticket_channel: Optional[discord.TextChannel] = None,
        receipt_note: Optional[app_commands.Range[str, 1, 500]] = None,
        color: Optional[str] = None,
        button_label: Optional[app_commands.Range[str, 1, 30]] = None,
        footer: Optional[app_commands.Range[str, 1, 100]] = None,
        id_prefix: Optional[app_commands.Range[str, 2, 10]] = None,
        id_style: Optional[app_commands.Choice[str]] = None,
    ):
        guild = interaction.guild
        updates: dict = {}
        if id_prefix:
            prefix = id_prefix.strip().upper()
            if not PREFIX_RE.match(prefix):
                raise UserError("The ID prefix can only use letters and numbers (2 to 10 characters), for example `14K` or `ORD`.")
            updates["order_prefix"] = prefix
        if id_style:
            updates["order_style"] = id_style.value
        if channel:
            check_can_send(channel, guild.me)
            updates["default_channel_id"] = channel.id
        if ticket_channel:
            updates["ticket_channel_id"] = ticket_channel.id
        if receipt_note:
            updates["receipt_note"] = None if receipt_note.lower() == "default" else receipt_note
        if color:
            updates["color"] = f"{parse_color(color):06X}"
        if button_label:
            updates["button_label"] = None if button_label.lower() == "default" else button_label
        if footer:
            updates["footer"] = None if footer.lower() == "default" else footer

        changed = ""
        if updates:
            await interaction.response.defer(ephemeral=True)
            await db.upsert_shop(guild.id, **updates)
            count = await self.refresh_all(guild)
            changed = "✅ Saved." + (f" Updated {count} posted card(s)." if count else "")
        shop = await db.get_shop(guild.id)
        id_example = example_code(shop["order_prefix"] if shop and shop["order_prefix"] else "INV", shop["order_style"] if shop and shop["order_style"] else "random")

        embed = ui.card(
            "🛒 Shop settings",
            ui.kv(
                ("Order IDs", f"`{id_example}` ({'numbered' if shop and shop['order_style'] == 'sequential' else 'random'})"),
                ("Default channel", f"<#{shop['default_channel_id']}>" if shop and shop["default_channel_id"] else "Not set"),
                ("Ticket channel", f"<#{shop['ticket_channel_id']}>" if shop and shop["ticket_channel_id"] else "Not set"),
                ("Accent colour", f"#{shop['color']}" if shop and shop["color"] else "Default blue"),
                ("Button text", pick(shop["button_label"] if shop else None, DEFAULT_BUTTON)),
                ("Purchase DMs", "✅ On" if webhook_secrets() else "❌ Off (run /shop webhook)"),
                ("Footer", pick(shop["footer"] if shop else None, DEFAULT_FOOTER)),
                ("Receipt note", shop["receipt_note"] if shop and shop["receipt_note"] else None),
            ),
            guild=guild, section="Shop", footer="Products have their own colour and button text too: see /shop edit",
        )
        if updates:
            await interaction.followup.send(changed, embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Set up purchase DMs (Invoice ID + 'open a ticket' message)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def webhook(self, interaction: discord.Interaction):
        domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
        url = f"https://{domain}/stripe/webhook" if domain else "https://YOUR-PUBLIC-DOMAIN/stripe/webhook"
        status = "✅ Connected (signing secret is set)" if webhook_secrets() else "❌ Not set up yet (no `STRIPE_WEBHOOK_SECRET`)"
        steps = (
            "**1.** Give your bot a public web address. On Railway: your service → **Settings → Networking → Generate Domain**.\n"
            f"**2.** In Stripe, open **Developers → Webhooks → Add endpoint** and use this URL:\n`{url}`\n"
            "**3.** Choose the events `checkout.session.completed` and `checkout.session.async_payment_succeeded`.\n"
            "**4.** Copy the endpoint's **Signing secret** (`whsec_…`) and add it as the variable `STRIPE_WEBHOOK_SECRET` on your host.\n"
            "**5.** Redeploy, then run `/shop refresh`, and `/shop settings ticket_channel:#tickets`.\n\n"
            "Test mode and live mode have **separate** webhook endpoints and secrets. To use both, put them in one variable separated by a comma."
        )
        embed = ui.card("📬 Purchase DMs", f"**Status:** {status}\n\n{steps}", footer="Menu names in Stripe and Railway may differ slightly.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Update all posted product cards (e.g. after turning on purchase DMs)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        count = await self.refresh_all(interaction.guild)
        await interaction.followup.send(f"✅ Updated {count} posted card(s).", ephemeral=True)

    @app_commands.command(description="Look up an order by its Invoice ID")
    @app_commands.describe(invoice_id="The ID from the buyer, e.g. INV-3F9A1C2E")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def order(self, interaction: discord.Interaction, invoice_id: app_commands.Range[str, 1, 30]):
        o = await find_order(interaction.guild_id, invoice_id)
        if not o:
            raise UserError(f"I can't find an order with ID `{invoice_id.strip().upper()}` in this server.")
        await interaction.response.send_message(embed=order_embed(o), ephemeral=True)

    @app_commands.command(description="Create an order ID for a sale made outside Stripe and send the buyer their file")
    @app_commands.describe(
        member="Who bought it",
        product="Which product they bought",
        amount="What they paid, e.g. $10 PayPal (default: the product's price)",
        note="A private note for staff (the buyer doesn't see it)",
        send="DM the buyer their receipt and file now (default: yes)",
    )
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def createorder(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        product: str,
        amount: Optional[app_commands.Range[str, 1, 50]] = None,
        note: Optional[app_commands.Range[str, 1, 200]] = None,
        send: bool = True,
    ):
        if member.bot:
            raise UserError("Pick a real member, not a bot.")
        p = await resolve_product(interaction, product)
        await interaction.response.defer(ephemeral=True)
        code = await self.insert_order(
            interaction.guild_id, member.id, p, amount or p["price"], f"manual-{uuid.uuid4().hex}", None, True, source="manual", note=note, processed_by=interaction.user.id,
        )
        order = await db.fetch_one("SELECT * FROM orders WHERE guild_id = ? AND code = ?", (interaction.guild_id, code))
        shop = await db.get_shop(interaction.guild_id)
        delivered, file_status = False, ("failed" if order["file_id"] else None)
        role_status = await self.grant_role(interaction.guild, member, order, p)
        if send:
            delivered, file_status = await self.deliver_order(interaction.guild, member, order, p, shop, role_status)
        await emit(
            interaction.guild, "shop", "📝 Manual order",
            f"{member.mention} was given **{p['name']}** by {interaction.user.mention}\n\n"
            + ui.kv(("🕒 Ordered", discord.utils.format_dt(discord.utils.utcnow(), "f")), ("🧾 Invoice ID", f"`{code}`"), ("🆔 Buyer ID", f"`{member.id}`"), ("💰 Amount", order["amount"]),
                    ("⚙️ Sent", f"✋ Manually by {interaction.user.mention} (`{interaction.user.id}`)"), ("📝 Note", note),
                    ("📬 Receipt DM", ("✅ delivered" if delivered else "❌ couldn't DM, use /shop resend") if send else "Not sent yet"),
                    ("📥 File", None if file_status is None else ("✅ delivered with the receipt" if file_status == "delivered" else "❌ not delivered, use /shop resend")),
                    ("🎭 Role", None if role_status is None else ("✅ given" if role_status == "granted" else "❌ not given, use /shop resend"))),
            SUCCESS, subject=member.id,
        )
        status = "✅ Receipt and file sent to their DMs." if send and delivered and file_status in (None, "delivered") else (
            "⚠️ The receipt went out but the file didn't. Use `/shop resend`." if send and delivered else
            ("📪 I couldn't DM them (DMs closed?). Use `/shop resend` once they open DMs." if send else "Nothing was sent yet. Use `/shop resend` when you're ready."))
        await interaction.followup.send(
            embed=ui.card("🧾 Order created", ui.kv(("🧾 Invoice ID", f"`{code}`"), ("👤 Buyer", member.mention), ("📦 Product", p["name"]), ("💰 Amount", order["amount"] or "—"),
                                                   ("📥 File", "Yes" if order["file_id"] else "None attached")) + f"\n\n{status}", color=SUCCESS, guild=interaction.guild, section="Shop"),
            ephemeral=True,
        )

    @app_commands.command(description="Take back the role an order gave (for refunds)")
    @app_commands.describe(invoice_id="The order ID, e.g. INV-3F9A1C2E")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def revoke(self, interaction: discord.Interaction, invoice_id: app_commands.Range[str, 1, 30]):
        order = await find_order(interaction.guild_id, invoice_id)
        if not order:
            raise UserError(f"I can't find an order with ID `{invoice_id.strip().upper()}` in this server.")
        if not order["role_id"]:
            raise UserError("That order didn't give a role, so there's nothing to take back.")
        guild = interaction.guild
        role, member = guild.get_role(order["role_id"]), guild.get_member(order["user_id"])
        if role is None:
            raise UserError("That role doesn't exist any more.")
        if member is None:
            raise UserError("That buyer isn't in the server any more, so they don't have the role.")
        try:
            await member.remove_roles(role, reason=f"Refund/revoke for {order['code']} by {interaction.user}")
        except discord.HTTPException:
            raise UserError("I couldn't take the role back. Make sure my role is above it.") from None
        await db.execute("UPDATE orders SET role_status = 'removed' WHERE id = ?", (order["id"],))
        await emit(guild, "shop", "↩️ Role taken back", ui.kv(("🧾 Invoice ID", f"`{order['code']}`"), ("👤 Buyer", member.mention), ("🎭 Role", role.mention), ("🛡️ By", interaction.user.mention)), subject=member.id)
        await interaction.response.send_message(embed=ui.card("↩️ Role taken back", f"{role.mention} was removed from {member.mention} (order `{order['code']}`).", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Send an order's receipt and file to the buyer again")
    @app_commands.describe(invoice_id="The order ID, e.g. INV-3F9A1C2E")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def resend(self, interaction: discord.Interaction, invoice_id: app_commands.Range[str, 1, 30]):
        order = await find_order(interaction.guild_id, invoice_id)
        if not order:
            raise UserError(f"I can't find an order with ID `{invoice_id.strip().upper()}` in this server.")
        await interaction.response.defer(ephemeral=True)
        user = await self.resolve_buyer(interaction.guild, order["user_id"])
        if user is None:
            raise UserError("I can't find that buyer any more, so I can't DM them.")
        product = await db.get_product_by_id(order["product_id"]) if order["product_id"] else None
        shop = await db.get_shop(interaction.guild_id)
        role_status = await self.grant_role(interaction.guild, user, order, product)
        delivered, file_status = await self.deliver_order(interaction.guild, user, order, product, shop, role_status)
        await db.execute("UPDATE orders SET fulfillment = 'manual', processed_by = ?, processed_at = ? WHERE id = ?", (interaction.user.id, discord.utils.utcnow().isoformat(), order["id"]))
        await emit(
            interaction.guild, "shop", "🔁 Order re-sent by staff",
            f"{interaction.user.mention} re-sent order `{order['code']}` to <@{order['user_id']}>\n\n"
            + ui.kv(("🧾 Invoice ID", f"`{order['code']}`"), ("📦 Product", order["product_name"]), ("⚙️ Sent", f"✋ Manually by {interaction.user.mention} (`{interaction.user.id}`)"),
                    ("📬 Receipt DM", "✅ delivered" if delivered else "❌ couldn't DM"), ("📥 File", None if file_status is None else ("✅ delivered" if file_status == "delivered" else "❌ not delivered")),
                    ("🎭 Role", None if role_status is None else ("✅ given" if role_status == "granted" else "❌ not given")), ("🕒 When", discord.utils.format_dt(discord.utils.utcnow(), "f"))),
            subject=order["user_id"], footer=f"Order {order['code']}",
        )
        if delivered and file_status in (None, "delivered") and role_status in (None, "granted"):
            card = ui.card("✅ Sent again", f"Receipt{' and file' if file_status else ''} for `{order['code']}` sent to <@{order['user_id']}>.", color=SUCCESS)
        elif delivered and role_status == "failed":
            card = ui.card("⚠️ Receipt sent, role not given", "I couldn't give the role. Move **my role above it** in Server Settings → Roles, make sure the buyer is in the server, then try again.", color=WARN)
        elif delivered:
            card = ui.card("⚠️ Receipt sent, file not sent", "The file couldn't be attached (is it still stored? run `/files list`).", color=WARN)
        else:
            card = ui.card("📪 Couldn't DM them", "Their DMs are closed. Ask them to allow DMs from server members, then try again.", color=WARN)
        await interaction.followup.send(embed=card, ephemeral=True)

    @app_commands.command(description="Recent orders (optionally for one member)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def orders(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if member:
            rows = await db.fetch_all("SELECT * FROM orders WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 10", (interaction.guild_id, member.id))
        else:
            rows = await db.fetch_all("SELECT * FROM orders WHERE guild_id = ? ORDER BY id DESC LIMIT 10", (interaction.guild_id,))
        if not rows:
            raise UserError("No orders yet.")
        lines = [
            f"`{o['code']}` · <@{o['user_id']}> · **{o['product_name']}** · {o['amount'] or '—'}{' · 🧪' if not o['livemode'] else ''} · {'✋ <@' + str(o['processed_by']) + '>' if o['fulfillment'] == 'manual' and o['processed_by'] else '🤖 auto'}"
            for o in rows
        ]
        await interaction.response.send_message(embed=ui.card("🧾 Recent orders", "\n".join(lines), guild=interaction.guild, section="Shop"), ephemeral=True)

    @app_commands.command(description="How to get a Stripe Payment Link")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def howto(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=ui.card("💳 Getting your Stripe link", HOWTO, guild=interaction.guild, section="Shop"), ephemeral=True
        )

    @app_commands.command(description="Add a product that sells through a Stripe Payment Link")
    @app_commands.describe(
        name="Product name",
        price="Shown on the card, e.g. $9.99, £5/month or Free",
        stripe_link="Your Stripe Payment Link (https://buy.stripe.com/...)",
        description="Short description (use /shop describe for multiple lines)",
        image_url="Product image (direct https link)",
        button_label="Button text for this product (default: Buy now)",
        color="Card colour as hex, e.g. #635BFF",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 100],
        price: app_commands.Range[str, 1, 50],
        stripe_link: str,
        description: Optional[app_commands.Range[str, 1, 1000]] = None,
        image_url: Optional[str] = None,
        button_label: Optional[app_commands.Range[str, 1, 30]] = None,
        color: Optional[str] = None,
    ):
        link = stripe_url(stripe_link)
        image = image_link(image_url) if image_url else None
        color_hex = f"{parse_color(color):06X}" if color else None
        count = (await db.fetch_one("SELECT COUNT(*) AS c FROM products WHERE guild_id = ?", (interaction.guild_id,)))["c"]
        if count >= MAX_PRODUCTS:
            raise UserError(f"You've reached the limit of {MAX_PRODUCTS} products. Remove one with `/shop remove` first.")
        try:
            await db.execute(
                "INSERT INTO products (guild_id, name, description, price, buy_url, image_url, color, button_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (interaction.guild_id, name.strip(), description, price.strip(), link, image, color_hex, button_label),
            )
        except aiosqlite.IntegrityError:
            raise UserError(f"You already have a product called **{name}**. Use `/shop edit` to change it.") from None
        p = await db.fetch_one("SELECT * FROM products WHERE guild_id = ? AND name = ?", (interaction.guild_id, name.strip()))
        embed, view = await product_message(interaction.guild, p)
        await interaction.response.send_message(
            f"✅ Added **{p['name']}**. Here's how it looks. Publish it with `/shop post`.{test_note(link)}",
            embed=embed, view=view, ephemeral=True,
        )

    @app_commands.command(description="Change a product's details")
    @app_commands.describe(
        product="Which product",
        new_name="New name",
        price="New price text",
        stripe_link="New Stripe Payment Link",
        image_url="New image link (or 'none' to remove)",
        button_label="Button text (type 'default' to reset)",
        color="Card colour as hex",
        available="Set to False to show 'Sold out' and disable the button",
        delivery_file="A stored file (see /files add) sent to buyers by DM after they pay (or 'none')",
        grant_role="Buyers automatically get this role when they pay (e.g. Premium)",
        remove_role="Stop giving a role with this product",
    )
    @app_commands.autocomplete(product=product_autocomplete, delivery_file=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(
        self,
        interaction: discord.Interaction,
        product: str,
        new_name: Optional[app_commands.Range[str, 1, 100]] = None,
        price: Optional[app_commands.Range[str, 1, 50]] = None,
        stripe_link: Optional[str] = None,
        image_url: Optional[str] = None,
        button_label: Optional[app_commands.Range[str, 1, 30]] = None,
        color: Optional[str] = None,
        available: Optional[bool] = None,
        delivery_file: Optional[str] = None,
        grant_role: Optional[discord.Role] = None,
        remove_role: bool = False,
    ):
        p = await resolve_product(interaction, product)
        updates: dict = {}
        if grant_role is not None:
            check_role_sellable(interaction.guild, grant_role)
            updates["role_id"] = grant_role.id
        if remove_role:
            updates["role_id"] = None
        if new_name:
            updates["name"] = new_name.strip()
        if price:
            updates["price"] = price.strip()
        if stripe_link:
            updates["buy_url"] = stripe_url(stripe_link)
        if image_url:
            updates["image_url"] = image_link(image_url)
        if button_label:
            updates["button_label"] = None if button_label.lower() == "default" else button_label
        if color:
            updates["color"] = f"{parse_color(color):06X}"
        if available is not None:
            updates["available"] = int(available)
        if delivery_file:
            if delivery_file.strip().lower() == "none":
                updates["file_id"] = None
            else:
                stored = await db.fetch_one("SELECT id FROM stored_files WHERE guild_id = ? AND name = ?", (interaction.guild_id, delivery_file.strip()))
                if not stored:
                    raise UserError(f"I can't find a stored file called **{delivery_file}**. Add one with `/files add` first.")
                updates["file_id"] = stored["id"]
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option.")
        try:
            await db.update_product(p["id"], **updates)
        except aiosqlite.IntegrityError:
            raise UserError("Another product already has that name.") from None
        p = await db.get_product_by_id(p["id"])
        refreshed = await self.refresh_post(interaction.guild, p)
        note = " The posted card was updated too." if refreshed else " It isn't posted yet. Use `/shop post`."
        await interaction.response.send_message(f"✅ Updated **{p['name']}**.{note}{test_note(p['buy_url'])}", ephemeral=True)

    @app_commands.command(description="Write a product's description in a pop-up (multi-line)")
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def describe(self, interaction: discord.Interaction, product: Optional[str] = None):
        p = await resolve_product(interaction, product)
        await interaction.response.send_modal(DescriptionModal(self, p))

    @app_commands.command(description="Post a product card with its Buy button")
    @app_commands.describe(product="Which product", channel="Where to post it (default: the shop channel)")
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def post(self, interaction: discord.Interaction, product: Optional[str] = None, channel: Optional[discord.TextChannel] = None):
        guild = interaction.guild
        p = await resolve_product(interaction, product)
        shop = await db.get_shop(guild.id)
        target = channel or (guild.get_channel(shop["default_channel_id"]) if shop and shop["default_channel_id"] else None)
        if not isinstance(target, discord.TextChannel):
            raise UserError("Pick a channel, or set a default with `/shop settings channel:#shop`.")
        check_can_send(target, guild.me)

        await interaction.response.defer(ephemeral=True)
        await self.delete_post(guild, p)  # replace any older card for this product
        embed, view = await product_message(guild, p)
        message = await target.send(embed=embed, view=view)
        await db.update_product(p["id"], channel_id=target.id, message_id=message.id)
        await interaction.followup.send(f"✅ Posted in {target.mention}: {message.jump_url}{test_note(p['buy_url'])}", ephemeral=True)

    @app_commands.command(description="See how a product card looks (only you see it)")
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def preview(self, interaction: discord.Interaction, product: Optional[str] = None):
        p = await resolve_product(interaction, product)
        embed, view = await product_message(interaction.guild, p)
        await interaction.response.send_message(f"Preview{test_note(p['buy_url'])}", embed=embed, view=view, ephemeral=True)

    @app_commands.command(description="List your products")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        rows = await db.fetch_all("SELECT * FROM products WHERE guild_id = ? ORDER BY name", (interaction.guild_id,))
        if not rows:
            raise UserError("No products yet. Add one with `/shop add`. Need a link? Run `/shop howto`.")
        lines = []
        for p in rows:
            posted = f"posted in <#{p['channel_id']}>" if p["channel_id"] and p["message_id"] else "not posted"
            mode = " · ⚠️ test link" if is_test_link(p["buy_url"]) else ""
            lines.append(f"{'✅' if p['available'] else '❌'} **{p['name']}** · {p['price'] or '—'} · {posted}{mode}")
        await interaction.response.send_message(embed=ui.card("🛒 Products", "\n".join(lines), guild=interaction.guild, section="Shop"), ephemeral=True)

    @app_commands.command(description="Delete a product (and its posted card)")
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, product: str):
        p = await resolve_product(interaction, product)
        await interaction.response.defer(ephemeral=True)
        await self.delete_post(interaction.guild, p)
        await db.execute("DELETE FROM products WHERE id = ?", (p["id"],))
        await interaction.followup.send(f"🗑️ Removed **{p['name']}**.", ephemeral=True)


@app_commands.guild_only()
class BuyCog(commands.Cog):
    """The member-facing /buy and /myorders commands."""

    @app_commands.command(description="Show a product and its Buy button")
    @app_commands.describe(product="Which product", public="Show it to everyone in the channel (default: only you)")
    @app_commands.autocomplete(product=product_autocomplete)
    async def buy(self, interaction: discord.Interaction, product: Optional[str] = None, public: bool = False):
        p = await resolve_product(interaction, product)
        embed, view = await product_message(interaction.guild, p)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=not public)

    @app_commands.command(description="Browse everything for sale")
    async def store(self, interaction: discord.Interaction):
        products = await db.fetch_all("SELECT * FROM products WHERE guild_id = ? ORDER BY name", (interaction.guild_id,))
        if not products:
            raise UserError("There's nothing for sale yet. Check back soon!")
        shop = await db.get_shop(interaction.guild_id)
        await interaction.response.send_message(
            embed=await catalog_embed(interaction.guild, products), view=StoreView(products, shop), ephemeral=True
        )

    @app_commands.command(description="See your past purchases and Invoice IDs")
    async def myorders(self, interaction: discord.Interaction):
        rows = await db.fetch_all(
            "SELECT * FROM orders WHERE guild_id = ? AND user_id = ? ORDER BY id DESC LIMIT 10", (interaction.guild_id, interaction.user.id)
        )
        if not rows:
            raise UserError("You don't have any purchases here yet.")
        shop = await db.get_shop(interaction.guild_id)
        tickets_cfg = await db.get_ticket_config(interaction.guild_id)
        channel_id = (shop["ticket_channel_id"] if shop else None) or (tickets_cfg["panel_channel_id"] if tickets_cfg else None)
        ticket = f"Send it in <#{channel_id}>" if channel_id else "Send it to any staff member"
        lines = [f"`{o['code']}` · **{o['product_name']}** · {o['amount'] or '—'} · {discord.utils.format_dt(discord.utils.parse_time(o['created_at']), 'd')}" for o in rows]
        embed = ui.card("🧾 Your purchases", "\n".join(lines) + f"\n\n{ui.DIVIDER}\n🎫 **Need help? Just use your ID.** {ticket}, that's all staff need to find your order.", guild=interaction.guild, section="Shop")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
    await bot.add_cog(BuyCog())
