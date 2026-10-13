import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from logutil import emit
from cogs.shop import product_autocomplete
from common import ACCENT, COLOR, DANGER, SUCCESS, WARN, UserError, check_can_send, is_staff

log = logging.getLogger("verification-bot")

LABELS = {5: "Excellent", 4: "Great", 3: "Good", 2: "Poor", 1: "Terrible"}
STAR_CHOICES = [app_commands.Choice(name=f"{'★' * n}{'☆' * (5 - n)}  {LABELS[n]}", value=n) for n in (5, 4, 3, 2, 1)]


def review_embed(guild: discord.Guild, user: discord.abc.User, r) -> discord.Embed:
    """The card posted in the reviews channel. Product reviews and ticket reviews share this look."""
    is_ticket = r["kind"] == "ticket"
    lines = [f"{ui.stars(r['stars'])}  **{r['stars']}/5** · {LABELS.get(r['stars'], '')}"]
    if r["comment"]:
        lines += ["", "\n".join(f"> {line}" for line in r["comment"].splitlines() if line.strip())]
    details = ui.kv(
        ("🛡️ Staff", f"<@{r['staff_id']}>" if r["staff_id"] else None),
        ("🎫 Ticket", r["product_name"].replace("Ticket ", "", 1) if is_ticket else None),
        ("✅ Purchase", "Verified buyer" if r["order_code"] else None),
    )
    if details:
        lines += ["", ui.DIVIDER, details]
    return ui.card(
        "🎫 Support review" if is_ticket else r["product_name"], "\n".join(lines), color=SUCCESS if r["stars"] >= 4 else (WARN if r["stars"] == 3 else DANGER),
        author=(user.display_name, user.display_avatar.url), footer=f"{'Ticket review' if is_ticket else 'Review'} #{r['id']} · {guild.name}", timestamp=True,
    )


def staff_channel_id(settings) -> Optional[int]:
    """Where staff (support) reviews go: the staff reviews channel, or the product reviews channel if that isn't set."""
    if not settings:
        return None
    return settings["staff_channel_id"] or settings["channel_id"]


def product_channel_id(settings) -> Optional[int]:
    return settings["channel_id"] if settings else None


async def post_ticket_review(guild: discord.Guild, user: discord.abc.User, ticket, stars: int, comment: Optional[str] = None):
    """Called when someone rates a ticket in their DMs: posts the review card in the staff reviews channel,
    crediting the staff member who claimed it. Returns (review row, message), or None if no channel is set up."""
    settings = await db.get_review_settings(guild.id)
    cid = staff_channel_id(settings)
    channel = guild.get_channel(cid) if cid else None
    if channel is None:
        return None
    name = f"Ticket #{ticket['number']:04d} · {ticket['type_name']}"
    await db.execute(
        "INSERT INTO reviews (guild_id, user_id, product_name, stars, comment, created_at, kind, staff_id, ticket_id) VALUES (?, ?, ?, ?, ?, ?, 'ticket', ?, ?) "
        "ON CONFLICT(guild_id, user_id, product_name) DO UPDATE SET stars = excluded.stars, comment = excluded.comment, staff_id = excluded.staff_id",
        (guild.id, user.id, name, stars, comment, discord.utils.utcnow().isoformat(), ticket["claimed_by"], ticket["id"]),
    )
    r = await db.fetch_one("SELECT * FROM reviews WHERE guild_id = ? AND user_id = ? AND product_name = ?", (guild.id, user.id, name))
    try:
        message = await channel.send(embed=review_embed(guild, user, r), allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        return None
    await db.execute("UPDATE reviews SET channel_id = ?, message_id = ? WHERE id = ?", (channel.id, message.id, r["id"]))
    await emit(
        guild, "tickets", "Ticket review posted",
        ui.kv(("👤 Reviewer", f"{user.mention} (`{user.id}`)"), ("🎫 Ticket", name.replace("Ticket ", "", 1)), ("⭐ Rating", f"{ui.stars(stars)} {stars}/5"), ("🛡️ Staff", f"<@{ticket['claimed_by']}>" if ticket["claimed_by"] else "Nobody claimed it"),
              ("💬 Comment", "Yes" if comment else None)),
        subject=user.id, ids=(("review", r["id"]), ("ticket", ticket["number"])),
    )
    return await db.fetch_one("SELECT * FROM reviews WHERE id = ?", (r["id"],)), message


async def add_ticket_review_comment(guild: discord.Guild, user: discord.abc.User, ticket_id: int, comment: Optional[str]):
    """Adds (or changes) the written comment on a ticket review and updates the posted card. Returns the message or None."""
    r = await db.fetch_one("SELECT * FROM reviews WHERE guild_id = ? AND ticket_id = ? AND kind = 'ticket'", (guild.id, ticket_id))
    if not r:
        return None
    await db.execute("UPDATE reviews SET comment = ? WHERE id = ?", (comment, r["id"]))
    r = await db.fetch_one("SELECT * FROM reviews WHERE id = ?", (r["id"],))
    channel = guild.get_channel(r["channel_id"]) if r["channel_id"] else None
    if channel is None or not r["message_id"]:
        return None
    try:
        message = await channel.fetch_message(r["message_id"])
        await message.edit(embed=review_embed(guild, user, r))
        return message
    except discord.HTTPException:
        return None


async def refresh_product_card(bot: commands.Bot, guild: discord.Guild, product_name: str) -> None:
    """Keep the star rating on the posted shop card up to date."""
    shop = bot.get_cog("Shop")
    product = await db.fetch_one("SELECT * FROM products WHERE guild_id = ? AND name = ?", (guild.id, product_name))
    if shop and product:
        await shop.refresh_post(guild, product)


async def save_product_review(bot: commands.Bot, guild: discord.Guild, user: discord.abc.User, p, stars: int,
                              comment: Optional[str] = None, staff_id: Optional[int] = None):
    """Saves a product review and posts (or updates) its card in the product reviews channel.
    Used by /review and by 'Review a product' in the DM sent when a ticket closes.
    Returns (review row, message, existing row or None, order code or None, channel). Raises UserError if it can't."""
    settings = await db.get_review_settings(guild.id)
    cid = product_channel_id(settings)
    if not cid:
        raise UserError("Product reviews aren't set up yet. An admin can run `/reviews channel`.")
    channel = guild.get_channel(cid)
    if channel is None:
        raise UserError("The product reviews channel no longer exists. An admin needs to run `/reviews channel` again.")
    order = await db.fetch_one(
        "SELECT code FROM orders WHERE guild_id = ? AND user_id = ? AND lower(product_name) = lower(?) ORDER BY id DESC LIMIT 1",
        (guild.id, user.id, p["name"]),
    )
    if settings["require_purchase"] and not order:
        raise UserError("Only verified buyers can review this product. Buy it through the shop first.")
    code = order["code"] if order else None
    existing = await db.fetch_one("SELECT * FROM reviews WHERE guild_id = ? AND user_id = ? AND product_name = ?", (guild.id, user.id, p["name"]))
    if existing:
        await db.execute("UPDATE reviews SET stars = ?, comment = ?, order_code = ?, staff_id = ? WHERE id = ?",
                         (stars, comment, code, staff_id or existing["staff_id"], existing["id"]))
        review_id = existing["id"]
    else:
        await db.execute(
            "INSERT INTO reviews (guild_id, user_id, product_name, stars, comment, order_code, created_at, staff_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild.id, user.id, p["name"], stars, comment, code, discord.utils.utcnow().isoformat(), staff_id),
        )
        review_id = (await db.fetch_one("SELECT id FROM reviews WHERE guild_id = ? AND user_id = ? AND product_name = ?", (guild.id, user.id, p["name"])))["id"]
    r = await db.fetch_one("SELECT * FROM reviews WHERE id = ?", (review_id,))
    embed = review_embed(guild, user, r)

    message = None
    # Edit the old card if it's still in the product reviews channel; otherwise post a new one there.
    if existing and existing["message_id"] and existing["channel_id"] == channel.id:
        try:
            message = await channel.fetch_message(existing["message_id"])
            await message.edit(embed=embed)
        except discord.HTTPException:
            message = None
    if message is None:
        try:
            message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            raise UserError("I couldn't post in the product reviews channel. An admin needs to check my permissions there.") from None
    await db.execute("UPDATE reviews SET channel_id = ?, message_id = ? WHERE id = ?", (channel.id, message.id, review_id))
    await refresh_product_card(bot, guild, p["name"])
    await emit(
        guild, "shop", "Product review posted" if existing is None else "Product review updated",
        ui.kv(("👤 Reviewer", f"{user.mention} (`{user.id}`)"), ("📦 Product", p["name"]), ("⭐ Rating", f"{ui.stars(stars)} {stars}/5"),
              ("✅ Purchase", f"Verified buyer, order `{code}`" if code else "Not a verified buyer"), ("🛡️ Staff tagged", f"<@{staff_id}>" if staff_id else None)),
        subject=user.id, ids=(("product", p["name"]), ("order", code)),
    )
    return r, message, existing, code, channel


# ------------------------------------------------ 'Review a product' (ticket close DM) ----

async def reviewable_products(guild_id: int, user_id: int) -> list:
    """Products this member can review: ones they bought first, then (unless buyers-only) the rest of the shop."""
    settings = await db.get_review_settings(guild_id)
    bought = await db.fetch_all(
        "SELECT p.* FROM products p WHERE p.guild_id = ? AND EXISTS (SELECT 1 FROM orders o WHERE o.guild_id = p.guild_id AND o.user_id = ? "
        "AND lower(o.product_name) = lower(p.name)) ORDER BY p.name", (guild_id, user_id))
    if settings and settings["require_purchase"]:
        return bought
    seen = {p["id"] for p in bought}
    rest = await db.fetch_all("SELECT * FROM products WHERE guild_id = ? AND available = 1 ORDER BY name", (guild_id,))
    return bought + [p for p in rest if p["id"] not in seen]


class ProductReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"review:product:(?P<gid>[0-9]+)"):
    """'Review a product' button (keeps working after restarts). Opens a product picker, then stars, then an optional comment."""

    def __init__(self, guild_id: int):
        super().__init__(discord.ui.Button(label="Review a product", emoji="🛍️", style=discord.ButtonStyle.success, custom_id=f"review:product:{guild_id}"))
        self.guild_id = guild_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["gid"]))

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.client.get_guild(self.guild_id)
        settings = await db.get_review_settings(self.guild_id)
        if guild is None or not product_channel_id(settings):
            return await interaction.response.send_message("Product reviews aren't set up right now.", ephemeral=True)
        products = await reviewable_products(self.guild_id, interaction.user.id)
        if not products:
            msg = "Only buyers can review products, and I can't find a purchase on your account." if settings["require_purchase"] else "There are no products to review yet."
            return await interaction.response.send_message(msg, ephemeral=True)
        await interaction.response.send_message(
            embed=ui.card("🛍️ Review a product", "Which product do you want to review?", color=COLOR, guild=guild, section="Reviews"),
            view=ProductPickView(interaction.client, guild, products[:25]), ephemeral=True,
        )


class ProductPickView(discord.ui.View):
    def __init__(self, bot, guild: discord.Guild, products: list):
        super().__init__(timeout=600)
        self.bot, self.guild = bot, guild
        select = discord.ui.Select(placeholder="Pick a product", options=[
            discord.SelectOption(label=p["name"][:100], value=str(p["id"]), emoji="📦") for p in products])

        async def picked(interaction: discord.Interaction):
            p = await db.fetch_one("SELECT * FROM products WHERE id = ? AND guild_id = ?", (int(select.values[0]), guild.id))
            if not p:
                return await interaction.response.send_message("That product doesn't exist any more.", ephemeral=True)
            await interaction.response.edit_message(
                embed=ui.card(f"🛍️ {p['name']}", "How many stars do you give it?", color=COLOR, guild=guild, section="Reviews"),
                view=StarPickView(bot, guild, p))

        select.callback = picked
        self.add_item(select)


class StarPickView(discord.ui.View):
    def __init__(self, bot, guild: discord.Guild, p):
        super().__init__(timeout=600)
        for n in (1, 2, 3, 4, 5):
            button = discord.ui.Button(label=str(n), emoji="⭐", style=discord.ButtonStyle.secondary)

            async def clicked(interaction: discord.Interaction, n=n):
                await interaction.response.send_modal(ProductReviewModal(bot, guild, p, n))

            button.callback = clicked
            self.add_item(button)


class ProductReviewModal(discord.ui.Modal):
    def __init__(self, bot, guild: discord.Guild, p, stars: int):
        super().__init__(title=f"{stars}/5 for {p['name']}"[:45])
        self.bot, self.guild, self.p, self.stars = bot, guild, p, stars
        self.text = discord.ui.TextInput(label="Comment (optional)", style=discord.TextStyle.paragraph, max_length=500, required=False,
                                         placeholder="What did you think of it?")
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            r, message, existing, code, channel = await save_product_review(
                self.bot, self.guild, interaction.user, self.p, self.stars, self.text.value.strip() or None)
        except UserError as e:
            return await interaction.response.edit_message(embed=ui.card("Couldn't post your review", str(e), color=DANGER, guild=self.guild, section="Reviews"), view=None)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="See your review", emoji="⭐", url=message.jump_url))
        await interaction.response.edit_message(embed=ui.card(
            "✅ Review updated" if existing else "✅ Thanks for your review!",
            f"{ui.stars(self.stars)}  **{self.stars}/5** for **{self.p['name']}**" + ("\n✅ Verified buyer" if code else ""),
            color=SUCCESS, guild=self.guild, section="Reviews"), view=view)


@app_commands.guild_only()
class ReviewCog(commands.Cog):
    """The member-facing /review command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(description="Leave a star review for a product")
    @app_commands.describe(product="Which product", stars="Your rating", comment="Tell others what you thought (optional)", staff="Tag the staff member who helped you (optional)")
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.choices(stars=STAR_CHOICES)
    async def review(
        self,
        interaction: discord.Interaction,
        product: str,
        stars: app_commands.Choice[int],
        comment: Optional[app_commands.Range[str, 1, 500]] = None,
        staff: Optional[discord.Member] = None,
    ):
        guild, user = interaction.guild, interaction.user
        if staff is not None:
            tickets_cfg = await db.get_ticket_config(guild.id)
            if staff.bot or staff.id == user.id:
                raise UserError("Tag a real staff member who helped you, not yourself or a bot.")
            if tickets_cfg and not is_staff(staff, tickets_cfg):
                raise UserError(f"{staff.mention} isn't on the staff team. Tag the staff member who helped you.")
        p = await db.fetch_one("SELECT * FROM products WHERE guild_id = ? AND name = ?", (guild.id, product.strip()))
        if not p:
            raise UserError("I can't find that product. Start typing its name and pick one from the list.")
        await interaction.response.defer(ephemeral=True)
        r, message, existing, code, channel = await save_product_review(self.bot, guild, user, p, stars.value, comment, staff.id if staff else None)

        done = ui.card(
            "✅ Review updated" if existing else "✅ Thanks for your review!",
            f"{ui.stars(stars.value)}  **{stars.value}/5** for **{p['name']}**" + (f"\n🛡️ Thanks to {staff.mention}" if staff else "") + f"\n\n[See it in {channel.mention}]({message.jump_url})",
            color=SUCCESS, guild=guild, section="Reviews",
        )
        await interaction.followup.send(embed=done, ephemeral=True)


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Reviews(commands.GroupCog, group_name="reviews", group_description="Review settings and star leaderboards"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(description="Choose where product reviews and staff reviews are posted (and whether buyers only)")
    @app_commands.describe(
        channel="Where product reviews appear",
        staff_channel="Where staff / support reviews appear (from the stars after a ticket closes)",
        require_purchase="Only members who bought the product can review it",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        staff_channel: Optional[discord.TextChannel] = None,
        require_purchase: Optional[bool] = None,
    ):
        updates: dict = {}
        if channel:
            check_can_send(channel, interaction.guild.me)
            updates["channel_id"] = channel.id
        if staff_channel:
            check_can_send(staff_channel, interaction.guild.me)
            updates["staff_channel_id"] = staff_channel.id
        if require_purchase is not None:
            updates["require_purchase"] = int(require_purchase)
        if updates:
            await db.upsert_review_settings(interaction.guild_id, **updates)
        s = await db.get_review_settings(interaction.guild_id)
        embed = ui.card(
            "⭐ Review settings" if not updates else "✅ Review settings saved",
            ui.kv(
                ("🛍️ Product reviews", f"<#{s['channel_id']}>" if s and s["channel_id"] else "Not set"),
                ("🛡️ Staff reviews", f"<#{s['staff_channel_id']}>" if s and s["staff_channel_id"]
                 else (f"<#{s['channel_id']}> (same as products, set `staff_channel` to split them)" if s and s["channel_id"] else "Not set")),
                ("Buyers only", "Yes" if s and s["require_purchase"] else "No"),
            ),
            guild=interaction.guild, section="Reviews", color=SUCCESS if updates else COLOR,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Admin leaderboard: staff by ticket ratings, or products by reviews")
    @app_commands.describe(board="Which leaderboard to show")
    @app_commands.choices(board=[
        app_commands.Choice(name="Staff (ticket star ratings)", value="staff"),
        app_commands.Choice(name="Products (customer reviews)", value="products"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leaderboard(self, interaction: discord.Interaction, board: Optional[app_commands.Choice[str]] = None):
        guild = interaction.guild
        kind = board.value if board else "staff"
        if kind == "staff":
            rows = await db.fetch_all(
                "SELECT who, AVG(score) AS avg, COUNT(*) AS n FROM ("
                " SELECT claimed_by AS who, rating AS score FROM tickets WHERE guild_id = ? AND rating IS NOT NULL AND claimed_by IS NOT NULL"
                " UNION ALL"
                " SELECT staff_id AS who, stars AS score FROM reviews WHERE guild_id = ? AND staff_id IS NOT NULL AND kind = 'product'"
                ") GROUP BY who ORDER BY avg DESC, n DESC LIMIT 10",
                (guild.id, guild.id),
            )
            overall = await db.fetch_one(
                "SELECT AVG(score) AS avg, COUNT(*) AS n FROM ("
                " SELECT rating AS score FROM tickets WHERE guild_id = ? AND rating IS NOT NULL"
                " UNION ALL SELECT stars AS score FROM reviews WHERE guild_id = ? AND staff_id IS NOT NULL AND kind = 'product'"
                ")", (guild.id, guild.id),
            )
            title, section = "🏆 Staff leaderboard", "Staff ratings"
            lines = [f"{ui.rank(i)} <@{r['who']}>\n　{ui.stars(r['avg'])} **{r['avg']:.2f}** · {ui.plural(r['n'], 'rating')}" for i, r in enumerate(rows)]
            head = f"**Overall** · {ui.rating_line(overall['avg'], overall['n'])}" if overall and overall["n"] else ""
            footer_note = "Counts ticket ratings (tickets a staff member claimed) and product reviews that tag a staff member. Ranked by average, then by number of ratings."
        else:
            rows = await db.fetch_all(
                "SELECT product_name AS name, AVG(stars) AS avg, COUNT(*) AS n FROM reviews WHERE guild_id = ? AND kind = 'product' GROUP BY product_name ORDER BY avg DESC, n DESC LIMIT 10",
                (guild.id,),
            )
            title, section = "🏆 Product leaderboard", "Customer reviews"
            lines = [f"{ui.rank(i)} **{r['name']}**\n　{ui.stars(r['avg'])} **{r['avg']:.2f}** · {ui.plural(r['n'], 'review')}" for i, r in enumerate(rows)]
            head, footer_note = "", "Ranked by average stars, then by number of reviews."
        if not rows:
            raise UserError("There are no ratings yet. Members rate staff after a ticket closes, and review products with `/review`.")
        body = "\n\n".join(lines)
        embed = ui.card(title, (head + "\n\n" if head else "") + body, color=ACCENT, guild=guild, section=section)
        embed.add_field(name="\u200b", value=f"*{footer_note}*", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="List recent reviews with their IDs")
    @app_commands.describe(product="Only this product")
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction, product: Optional[str] = None):
        sql, params = "SELECT * FROM reviews WHERE guild_id = ?", [interaction.guild_id]
        if product:
            sql += " AND product_name = ?"
            params.append(product.strip())
        rows = await db.fetch_all(sql + " ORDER BY id DESC LIMIT 15", tuple(params))
        if not rows:
            raise UserError("No reviews yet.")
        lines = []
        for r in rows:
            kind = "🎫" if r["kind"] == "ticket" else "🛍️"
            extra = (" · ✅" if r["order_code"] else "") + (f" · 🛡️ <@{r['staff_id']}>" if r["staff_id"] else "")
            lines.append(f"`#{r['id']}` {ui.stars(r['stars'])} {kind} **{r['product_name']}** · <@{r['user_id']}>{extra}")
        await interaction.response.send_message(embed=ui.card("⭐ Recent reviews", "\n".join(lines), guild=interaction.guild, section="Reviews"), ephemeral=True)

    @app_commands.command(description="Delete a review by its ID")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, review_id: int):
        r = await db.fetch_one("SELECT * FROM reviews WHERE id = ? AND guild_id = ?", (review_id, interaction.guild_id))
        if not r:
            raise UserError("I can't find a review with that ID. See `/reviews list`.")
        await interaction.response.defer(ephemeral=True)
        if r["channel_id"] and r["message_id"]:
            channel = interaction.guild.get_channel(r["channel_id"])
            if channel is not None:
                try:
                    await (await channel.fetch_message(r["message_id"])).delete()
                except discord.HTTPException:
                    pass
        await db.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
        await refresh_product_card(self.bot, interaction.guild, r["product_name"])
        await emit(
            interaction.guild, "shop", "Review removed",
            ui.kv(("👤 Reviewer", f"<@{r['user_id']}> (`{r['user_id']}`)"), ("📦 Product", r["product_name"]), ("⭐ Rating", f"{r['stars']}/5")),
            actor=interaction.user, subject=r["user_id"], ids=(("review", review_id),),
        )
        await interaction.followup.send(embed=ui.card("🗑️ Review removed", f"Deleted review `#{review_id}` of **{r['product_name']}**.", color=SUCCESS), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReviewCog(bot))
    await bot.add_cog(Reviews(bot))
