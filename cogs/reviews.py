import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from cogs.shop import product_autocomplete
from common import ACCENT, COLOR, DANGER, SUCCESS, WARN, UserError, check_can_send

log = logging.getLogger("verification-bot")

LABELS = {5: "Excellent", 4: "Great", 3: "Good", 2: "Poor", 1: "Terrible"}
STAR_CHOICES = [app_commands.Choice(name=f"{'★' * n}{'☆' * (5 - n)}  {LABELS[n]}", value=n) for n in (5, 4, 3, 2, 1)]


def review_embed(guild: discord.Guild, user: discord.abc.User, r) -> discord.Embed:
    """The card posted in the reviews channel (same look as the purchase receipt DM)."""
    color = SUCCESS if r["stars"] >= 4 else (WARN if r["stars"] == 3 else DANGER)
    lines = [f"{ui.stars(r['stars'])}  **{r['stars']}/5** · {LABELS.get(r['stars'], '')}"]
    if r["comment"]:
        lines += ["", "\n".join(f"> {line}" for line in r["comment"].splitlines() if line.strip())]
    if r["order_code"]:
        lines += ["", "✅ **Verified buyer**"]
    return ui.card(
        r["product_name"], "\n".join(lines), color=color, author=(user.display_name, user.display_avatar.url),
        footer=f"Review #{r['id']} · {guild.name}", timestamp=True,
    )


async def refresh_product_card(bot: commands.Bot, guild: discord.Guild, product_name: str) -> None:
    """Keep the star rating on the posted shop card up to date."""
    shop = bot.get_cog("Shop")
    product = await db.fetch_one("SELECT * FROM products WHERE guild_id = ? AND name = ?", (guild.id, product_name))
    if shop and product:
        await shop.refresh_post(guild, product)


@app_commands.guild_only()
class ReviewCog(commands.Cog):
    """The member-facing /review command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(description="Leave a star review for a product")
    @app_commands.describe(product="Which product", stars="Your rating", comment="Tell others what you thought (optional)")
    @app_commands.autocomplete(product=product_autocomplete)
    @app_commands.choices(stars=STAR_CHOICES)
    async def review(
        self,
        interaction: discord.Interaction,
        product: str,
        stars: app_commands.Choice[int],
        comment: Optional[app_commands.Range[str, 1, 500]] = None,
    ):
        guild, user = interaction.guild, interaction.user
        settings = await db.get_review_settings(guild.id)
        if not settings or not settings["channel_id"]:
            raise UserError("Reviews aren't set up yet. An admin can run `/reviews channel`.")
        channel = guild.get_channel(settings["channel_id"])
        if channel is None:
            raise UserError("The reviews channel no longer exists. An admin needs to run `/reviews channel` again.")
        p = await db.fetch_one("SELECT * FROM products WHERE guild_id = ? AND name = ?", (guild.id, product.strip()))
        if not p:
            raise UserError("I can't find that product. Start typing its name and pick one from the list.")
        order = await db.fetch_one(
            "SELECT code FROM orders WHERE guild_id = ? AND user_id = ? AND lower(product_name) = lower(?) ORDER BY id DESC LIMIT 1",
            (guild.id, user.id, p["name"]),
        )
        if settings["require_purchase"] and not order:
            raise UserError("Only verified buyers can review this product. Buy it through the shop first.")

        await interaction.response.defer(ephemeral=True)
        code = order["code"] if order else None
        existing = await db.fetch_one("SELECT * FROM reviews WHERE guild_id = ? AND user_id = ? AND product_name = ?", (guild.id, user.id, p["name"]))
        if existing:
            await db.execute("UPDATE reviews SET stars = ?, comment = ?, order_code = ? WHERE id = ?", (stars.value, comment, code, existing["id"]))
            review_id = existing["id"]
        else:
            await db.execute(
                "INSERT INTO reviews (guild_id, user_id, product_name, stars, comment, order_code, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guild.id, user.id, p["name"], stars.value, comment, code, discord.utils.utcnow().isoformat()),
            )
            review_id = (await db.fetch_one("SELECT id FROM reviews WHERE guild_id = ? AND user_id = ? AND product_name = ?", (guild.id, user.id, p["name"])))["id"]
        r = await db.fetch_one("SELECT * FROM reviews WHERE id = ?", (review_id,))
        embed = review_embed(guild, user, r)

        message = None
        if existing and existing["message_id"]:
            try:
                message = await channel.fetch_message(existing["message_id"])
                await message.edit(embed=embed)
            except discord.HTTPException:
                message = None
        if message is None:
            try:
                message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                raise UserError("I couldn't post in the reviews channel. An admin needs to check my permissions there.") from None
        await db.execute("UPDATE reviews SET channel_id = ?, message_id = ? WHERE id = ?", (channel.id, message.id, review_id))
        await refresh_product_card(self.bot, guild, p["name"])

        done = ui.card(
            "✅ Review updated" if existing else "✅ Thanks for your review!",
            f"{ui.stars(stars.value)}  **{stars.value}/5** for **{p['name']}**\n\n[See it in {channel.mention}]({message.jump_url})",
            color=SUCCESS, guild=guild, section="Reviews",
        )
        await interaction.followup.send(embed=done, ephemeral=True)


@app_commands.guild_only()
class Reviews(commands.GroupCog, group_name="reviews", group_description="Review settings and star leaderboards"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    @app_commands.command(description="Choose where reviews are posted (and whether buyers only)")
    @app_commands.describe(channel="Where new reviews appear", require_purchase="Only members who bought the product can review it")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def channel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        require_purchase: Optional[bool] = None,
    ):
        updates: dict = {}
        if channel:
            check_can_send(channel, interaction.guild.me)
            updates["channel_id"] = channel.id
        if require_purchase is not None:
            updates["require_purchase"] = int(require_purchase)
        if updates:
            await db.upsert_review_settings(interaction.guild_id, **updates)
        s = await db.get_review_settings(interaction.guild_id)
        embed = ui.card(
            "⭐ Review settings" if not updates else "✅ Review settings saved",
            ui.kv(
                ("Reviews channel", f"<#{s['channel_id']}>" if s and s["channel_id"] else "Not set"),
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
                "SELECT claimed_by AS who, AVG(rating) AS avg, COUNT(rating) AS n FROM tickets "
                "WHERE guild_id = ? AND rating IS NOT NULL AND claimed_by IS NOT NULL GROUP BY claimed_by ORDER BY avg DESC, n DESC LIMIT 10",
                (guild.id,),
            )
            overall = await db.fetch_one("SELECT AVG(rating) AS avg, COUNT(rating) AS n FROM tickets WHERE guild_id = ? AND rating IS NOT NULL", (guild.id,))
            title, section = "🏆 Staff leaderboard", "Ticket ratings"
            lines = [f"{ui.rank(i)} <@{r['who']}>\n　{ui.stars(r['avg'])} **{r['avg']:.2f}** · {ui.plural(r['n'], 'rating')}" for i, r in enumerate(rows)]
            head = f"**Overall** · {ui.rating_line(overall['avg'], overall['n'])}" if overall and overall["n"] else ""
            footer_note = "Ranked by average stars, then by number of ratings. Only tickets a staff member claimed count."
        else:
            rows = await db.fetch_all(
                "SELECT product_name AS name, AVG(stars) AS avg, COUNT(*) AS n FROM reviews WHERE guild_id = ? GROUP BY product_name ORDER BY avg DESC, n DESC LIMIT 10",
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
        lines = [f"`#{r['id']}` {ui.stars(r['stars'])} **{r['product_name']}** · <@{r['user_id']}>{' · ✅' if r['order_code'] else ''}" for r in rows]
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
        await interaction.followup.send(embed=ui.card("🗑️ Review removed", f"Deleted review `#{review_id}` of **{r['product_name']}**.", color=SUCCESS), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ReviewCog(bot))
    await bot.add_cog(Reviews(bot))
