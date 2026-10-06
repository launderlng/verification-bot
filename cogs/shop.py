import logging
import os
import urllib.parse
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import db
from common import COLOR, INFO, WARN, UserError, check_can_send, parse_color

log = logging.getLogger("verification-bot")

DEFAULT_BUTTON = "Buy now"
DEFAULT_FOOTER = "🔒 Secure checkout by Stripe"
MAX_PRODUCTS = 25  # Discord autocomplete shows 25 choices at most

# Stripe-hosted checkout pages. If you set up a custom domain for your Payment Links in Stripe
# (e.g. pay.example.com), add it with the STRIPE_EXTRA_HOSTS variable (comma-separated).
STRIPE_HOSTS = {"buy.stripe.com", "checkout.stripe.com", "donate.stripe.com"}
STRIPE_HOSTS |= {h.strip().lower() for h in os.getenv("STRIPE_EXTRA_HOSTS", "").split(",") if h.strip()}

HOWTO = (
    "**1.** Sign in to your **Stripe Dashboard** and open **Payment Links**.\n"
    "**2.** Click **+ New**, pick (or add) your product and price, then click **Create link**.\n"
    "**3.** Copy the link. It looks like `https://buy.stripe.com/xxxxxxxx`.\n"
    "**4.** Run `/shop add` and paste it into `stripe_link`.\n\n"
    "Tip: a link containing `/test_` is a **test-mode** link. No real money moves, so swap in your live link before launching."
)


# ---------------------------------------------------------- helpers ----

def stripe_url(url: str) -> str:
    """Only accept https links on Stripe's checkout hosts."""
    url = url.strip()
    if len(url) > 512:
        raise UserError("That link is too long (Discord buttons allow 512 characters).")
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
        super().__init__()

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

    @app_commands.command(description="Shop-wide settings: default channel, colour, button text, footer")
    @app_commands.describe(
        channel="Default channel for product cards",
        color="Accent colour as hex, e.g. #635BFF",
        button_label="Default text on the buy button (type 'default' to reset)",
        footer="Text at the bottom of every card (type 'default' to reset)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        color: Optional[str] = None,
        button_label: Optional[app_commands.Range[str, 1, 30]] = None,
        footer: Optional[app_commands.Range[str, 1, 100]] = None,
    ):
        guild = interaction.guild
        updates: dict = {}
        if channel:
            check_can_send(channel, guild.me)
            updates["default_channel_id"] = channel.id
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
            changed = f"✅ Saved." + (f" Updated {count} posted card(s)." if count else "")
        shop = await db.get_shop(guild.id)

        embed = discord.Embed(title="🛒 Shop settings", color=INFO)
        embed.add_field(name="Default channel", value=f"<#{shop['default_channel_id']}>" if shop and shop["default_channel_id"] else "Not set")
        embed.add_field(name="Accent colour", value=f"#{shop['color']}" if shop and shop["color"] else "Default green")
        embed.add_field(name="Button text", value=pick(shop["button_label"] if shop else None, DEFAULT_BUTTON))
        embed.add_field(name="Footer", value=pick(shop["footer"] if shop else None, DEFAULT_FOOTER), inline=False)
        embed.set_footer(text="Products have their own colour and button text too: see /shop edit")
        if updates:
            await interaction.followup.send(changed, embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

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
    """The member-facing /buy command."""

    @app_commands.command(description="Show a product and its Buy button")
    @app_commands.describe(product="Which product", public="Show it to everyone in the channel (default: only you)")
    @app_commands.autocomplete(product=product_autocomplete)
    async def buy(self, interaction: discord.Interaction, product: Optional[str] = None, public: bool = False):
        p = await resolve_product(interaction, product)
        shop = await db.get_shop(interaction.guild_id)
        await interaction.response.send_message(embed=build_product(interaction.guild, p, shop), view=build_view(p, shop), ephemeral=not public)


async def setup(bot: commands.Bot):
    await bot.add_cog(Shop(bot))
    await bot.add_cog(BuyCog())
