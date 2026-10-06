import json
import logging
import os
import secrets
import urllib.parse
from typing import Optional

import aiosqlite
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import db
from common import COLOR, INFO, WARN, UserError, check_can_send, parse_color
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


def build_product(guild: discord.Guild, p, shop) -> discord.Embed:
    color_hex = pick(p["color"], shop["color"] if shop else None)
    embed = discord.Embed(title=p["name"], description=p["description"] or None, color=int(color_hex, 16) if color_hex else COLOR.value)
    embed.add_field(name="💰 Price", value=p["price"] or "See checkout")
    embed.add_field(name="📦 Status", value="✅ Available" if p["available"] else "❌ Sold out")
    if p["image_url"]:
        embed.set_image(url=p["image_url"])
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    footer = pick(shop["footer"] if shop else None, DEFAULT_FOOTER)
    embed.set_footer(text=footer, icon_url=guild.icon.url if guild.icon else None)
    return embed


def build_view(p, shop) -> discord.ui.View:
    label = pick(p["button_label"], shop["button_label"] if shop else None, DEFAULT_BUTTON)
    view = discord.ui.View(timeout=None)
    if webhook_secrets():
        # Tracked checkout: the button creates a personal link so the payment can be matched to the buyer
        view.add_item(BuyButton(p["id"], label if p["available"] else "Sold out", disabled=not p["available"]))
    else:
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link, label=label if p["available"] else "Sold out", emoji="🛒",
            url=p["buy_url"], disabled=not p["available"],
        ))
    return view


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


def order_embed(o, title: str = "🧾 Order") -> discord.Embed:
    embed = discord.Embed(title=title, color=COLOR)
    embed.add_field(name="Invoice ID", value=f"`{o['code']}`", inline=False)
    embed.add_field(name="Buyer", value=f"<@{o['user_id']}>")
    embed.add_field(name="Product", value=o["product_name"])
    embed.add_field(name="Amount", value=o["amount"] or "—")
    embed.add_field(name="Paid", value=discord.utils.format_dt(discord.utils.parse_time(o["created_at"]), "f"))
    embed.add_field(name="Receipt DM", value="✅ Delivered" if o["dm_sent"] else "❌ Couldn't DM")
    if o["stripe_ref"]:
        embed.add_field(name="Stripe reference", value=f"`{o['stripe_ref']}`", inline=False)
    if not o["livemode"]:
        embed.set_footer(text="🧪 Test-mode payment")
    return embed


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
        embed = discord.Embed(
            title="💳 Ready to check out",
            description=(
                f"**{p['name']}** · {p['price'] or ''}\n\n"
                "Press the button below to open Stripe's secure checkout page.\n"
                "After you pay, I'll **DM you your Invoice ID**. Keep your DMs open, and you can always look it up later with `/myorders`."
            ),
            color=COLOR,
        )
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


# -------------------------------------------------------------- cog ----

@app_commands.guild_only()
class Shop(commands.GroupCog, group_name="shop", group_description="Sell products with a Stripe Buy button"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.runner: Optional[web.AppRunner] = None
        super().__init__()

    async def cog_load(self):
        self.bot.add_dynamic_items(BuyButton)
        await self.start_webhook()

    async def cog_unload(self):
        try:
            self.bot.remove_dynamic_items(BuyButton)
        except Exception:
            pass
        if self.runner:
            await self.runner.cleanup()

    # ----------------------------------------------------- stripe webhook ----

    async def start_webhook(self):
        if not webhook_secrets():
            log.info("STRIPE_WEBHOOK_SECRET isn't set, so purchase DMs are off (run /shop webhook for setup help)")
            return
        app = web.Application(client_max_size=1_000_000)
        app.router.add_get("/", self.health)
        app.router.add_post("/stripe/webhook", self.handle_webhook)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        port = int(os.getenv("PORT", "8080"))
        try:
            await web.TCPSite(self.runner, "0.0.0.0", port).start()
            log.info("Stripe webhook listening on port %s at /stripe/webhook", port)
        except OSError:
            log.exception("Couldn't start the Stripe webhook server on port %s", port)

    async def health(self, request: web.Request) -> web.Response:
        return web.Response(text="ok")

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

    async def create_order(self, guild_id: int, user_id: int, product, session: dict, livemode: bool) -> Optional[str]:
        """Insert the order and return its Invoice ID, or None if this payment was already recorded."""
        session_id = session["id"]
        name = product["name"] if product and product["guild_id"] == guild_id else "Your purchase"
        for _ in range(5):
            code = "INV-" + secrets.token_hex(4).upper()
            try:
                await db.execute(
                    "INSERT INTO orders (guild_id, code, user_id, product_id, product_name, amount, stripe_session_id, stripe_ref, livemode, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        guild_id, code, user_id, product["id"] if product else None, name,
                        format_amount(session.get("amount_total"), session.get("currency")), session_id,
                        session.get("invoice") or session.get("payment_intent") or session_id, int(livemode),
                        discord.utils.utcnow().isoformat(),
                    ),
                )
                return code
            except aiosqlite.IntegrityError:
                if await db.fetch_one("SELECT 1 FROM orders WHERE stripe_session_id = ?", (session_id,)):
                    return None  # Stripe delivered the same event twice
        raise RuntimeError("Couldn't generate a unique Invoice ID")

    def receipt_message(self, guild: discord.Guild, order, shop, fallback_channel_id: Optional[int] = None) -> dict:
        embed = discord.Embed(
            title="✅ Payment received",
            description=f"Thank you for your purchase from **{guild.name}**!",
            color=COLOR,
        )
        embed.add_field(name="🧾 Invoice ID", value=f"`{order['code']}`", inline=False)
        embed.add_field(name="📦 Product", value=order["product_name"])
        embed.add_field(name="💰 Amount", value=order["amount"] or "—")
        if order["stripe_ref"]:
            embed.add_field(name="🔖 Stripe reference", value=f"`{order['stripe_ref']}`", inline=False)

        ticket_id = (shop["ticket_channel_id"] if shop else None) or fallback_channel_id
        where = f" in <#{ticket_id}>" if ticket_id else " in the server"
        steps = f"🎫 **Please open a ticket**{where} and send your Invoice ID (`{order['code']}`) so we can sort out your order."
        if shop and shop["receipt_note"]:
            steps += f"\n\n{shop['receipt_note']}"
        embed.add_field(name="What to do next", value=steps, inline=False)
        embed.set_footer(text="Keep this message, you'll need the Invoice ID." + (" · 🧪 TEST PAYMENT" if not order["livemode"] else ""))
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        view = None
        if ticket_id:
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                style=discord.ButtonStyle.link, label="Go to tickets", emoji="🎫",
                url=f"https://discord.com/channels/{guild.id}/{ticket_id}",
            ))
        return {"embed": embed} if view is None else {"embed": embed, "view": view}

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

        user = guild.get_member(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                user = None
        delivered = False
        if user is not None:
            try:
                tickets_cfg = await db.get_ticket_config(guild_id)
                await user.send(**self.receipt_message(guild, order, shop, tickets_cfg["panel_channel_id"] if tickets_cfg else None))
                delivered = True
            except discord.HTTPException:
                log.info("Couldn't DM the receipt for %s (DMs closed?)", code)
        await db.execute("UPDATE orders SET dm_sent = ? WHERE guild_id = ? AND code = ?", (int(delivered), guild_id, code))

        await emit(
            guild, "shop", "🧪 Test purchase" if not livemode else "💸 New purchase",
            f"<@{user_id}> bought **{order['product_name']}**\n**Amount:** {order['amount']}\n**Invoice ID:** `{code}`\n"
            f"**Receipt DM:** {'✅ delivered' if delivered else '❌ could not DM, ask them to use /myorders'}",
            COLOR if livemode else WARN,
        )

    # ---------------------------------------------------------- helpers ----

    async def refresh_post(self, guild: discord.Guild, p) -> bool:
        """Edit the already-posted card in place. Returns True if there was one and it was updated."""
        if not p["channel_id"] or not p["message_id"]:
            return False
        channel = guild.get_channel(p["channel_id"])
        if channel is None:
            return False
        shop = await db.get_shop(guild.id)
        try:
            message = await channel.fetch_message(p["message_id"])
            await message.edit(embed=build_product(guild, p, shop), view=build_view(p, shop))
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
    )
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
    ):
        guild = interaction.guild
        updates: dict = {}
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

        embed = discord.Embed(title="🛒 Shop settings", color=INFO)
        embed.add_field(name="Default channel", value=f"<#{shop['default_channel_id']}>" if shop and shop["default_channel_id"] else "Not set")
        embed.add_field(name="Ticket channel", value=f"<#{shop['ticket_channel_id']}>" if shop and shop["ticket_channel_id"] else "Not set")
        embed.add_field(name="Accent colour", value=f"#{shop['color']}" if shop and shop["color"] else "Default green")
        embed.add_field(name="Button text", value=pick(shop["button_label"] if shop else None, DEFAULT_BUTTON))
        embed.add_field(name="Purchase DMs", value="✅ On" if webhook_secrets() else "❌ Off (run /shop webhook)")
        embed.add_field(name="Footer", value=pick(shop["footer"] if shop else None, DEFAULT_FOOTER), inline=False)
        if shop and shop["receipt_note"]:
            embed.add_field(name="Receipt note", value=shop["receipt_note"], inline=False)
        embed.set_footer(text="Products have their own colour and button text too: see /shop edit")
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
        embed = discord.Embed(title="📬 Purchase DMs", description=f"**Status:** {status}\n\n{steps}", color=INFO)
        embed.set_footer(text="Menu names in Stripe and Railway may differ slightly.")
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
        code = invoice_id.strip().upper()
        if not code.startswith("INV-"):
            code = "INV-" + code
        o = await db.fetch_one("SELECT * FROM orders WHERE guild_id = ? AND code = ?", (interaction.guild_id, code))
        if not o:
            raise UserError(f"I can't find an order with ID `{code}` in this server.")
        await interaction.response.send_message(embed=order_embed(o), ephemeral=True)

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
            f"`{o['code']}` · <@{o['user_id']}> · **{o['product_name']}** · {o['amount'] or '—'}{' · 🧪' if not o['livemode'] else ''}"
            for o in rows
        ]
        await interaction.response.send_message(embed=discord.Embed(title="🧾 Recent orders", description="\n".join(lines), color=INFO), ephemeral=True)

    @app_commands.command(description="How to get a Stripe Payment Link")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def howto(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(title="💳 Getting your Stripe link", description=HOWTO, color=INFO), ephemeral=True
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
        shop = await db.get_shop(interaction.guild_id)
        await interaction.response.send_message(
            f"✅ Added **{p['name']}**. Here's how it looks. Publish it with `/shop post`.{test_note(link)}",
            embed=build_product(interaction.guild, p, shop), view=build_view(p, shop), ephemeral=True,
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
    )
    @app_commands.autocomplete(product=product_autocomplete)
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
    ):
        p = await resolve_product(interaction, product)
        updates: dict = {}
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
        message = await target.send(embed=build_product(guild, p, shop), view=build_view(p, shop))
        await db.update_product(p["id"], channel_id=target.id, message_id=message.id)
        await interaction.followup.send(f"✅ Posted in {target.mention}: {message.jump_url}{test_note(p['buy_url'])}", ephemeral=True)

    @app_commands.command(description="See how a product card looks (only you see it)")
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def preview(self, interaction: discord.Interaction, product: Optional[str] = None):
        p = await resolve_product(interaction, product)
        shop = await db.get_shop(interaction.guild_id)
        await interaction.response.send_message(
            f"Preview{test_note(p['buy_url'])}", embed=build_product(interaction.guild, p, shop), view=build_view(p, shop), ephemeral=True
        )

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
        await interaction.response.send_message(embed=discord.Embed(title="🛒 Products", description="\n".join(lines), color=INFO), ephemeral=True)

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
        shop = await db.get_shop(interaction.guild_id)
        await interaction.response.send_message(embed=build_product(interaction.guild, p, shop), view=build_view(p, shop), ephemeral=not public)

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
        ticket = f"Open a ticket in <#{channel_id}>" if channel_id else "Open a ticket"
        lines = [f"`{o['code']}` · **{o['product_name']}** · {o['amount'] or '—'} · {discord.utils.format_dt(discord.utils.parse_time(o['created_at']), 'd')}" for o in rows]
        embed = discord.Embed(title="🧾 Your purchases", description="\n".join(lines) + f"\n\n🎫 {ticket} and send the Invoice ID so staff can help.", color=COLOR)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
    await bot.add_cog(BuyCog())
