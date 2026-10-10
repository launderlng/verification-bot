import asyncio
import logging
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from cogs.files import delete_disk_file
from common import SUCCESS, WARN, UserError
from logutil import emit

log = logging.getLogger("verification-bot")
OLD_GIVEAWAY_DAYS = 60
TEST_NAME_SQL = "(LOWER(name) LIKE 'test%' OR LOWER(name) LIKE 'demo%' OR LOWER(filename) LIKE 'test%' OR LOWER(filename) LIKE 'demo%')"


async def survey(guild_id: int, include_test_files: bool) -> dict:
    """Find what's safe to remove. Nothing is deleted here."""
    cutoff = (discord.utils.utcnow() - timedelta(days=OLD_GIVEAWAY_DAYS)).isoformat()
    tables = {r["name"] for r in await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    plan = {
        "test_orders": [r["id"] for r in await db.fetch_all("SELECT id FROM orders WHERE guild_id = ? AND livemode = 0", (guild_id,))],
        "orphan_deliveries": [(r["file_id"], r["user_id"]) for r in await db.fetch_all("SELECT file_id, user_id FROM file_deliveries WHERE file_id NOT IN (SELECT id FROM stored_files)")],
        "orphan_images": [r["id"] for r in await db.fetch_all(
            "SELECT id FROM stored_files WHERE guild_id = ? AND name LIKE 'page-image-%' AND id NOT IN (SELECT image_file_id FROM pages WHERE image_file_id IS NOT NULL) "
            "AND id NOT IN (SELECT banner_file_id FROM welcome_config WHERE banner_file_id IS NOT NULL)", (guild_id,))],
        "old_giveaways": [r["id"] for r in await db.fetch_all("SELECT id FROM giveaways WHERE guild_id = ? AND status != 'active' AND COALESCE(ended_at, created_at) < ?", (guild_id, cutoff))],
        "dead_product_links": (await db.fetch_one(
            "SELECT COUNT(*) AS c FROM products WHERE guild_id = ? AND file_id IS NOT NULL AND file_id NOT IN (SELECT id FROM stored_files)", (guild_id,)))["c"],
        "old_link_cards": "link_cards" in tables,
        "test_files": [(r["id"], r["name"]) for r in await db.fetch_all(f"SELECT id, name FROM stored_files WHERE guild_id = ? AND name NOT LIKE 'page-image-%' AND {TEST_NAME_SQL}", (guild_id,))],
        "include_test_files": include_test_files,
    }
    return plan


def plan_total(plan: dict) -> int:
    return (len(plan["test_orders"]) + len(plan["orphan_deliveries"]) + len(plan["orphan_images"]) + len(plan["old_giveaways"]) + plan["dead_product_links"]
            + (1 if plan["old_link_cards"] else 0) + (len(plan["test_files"]) if plan["include_test_files"] else 0))


async def apply_plan(guild_id: int, plan: dict) -> dict:
    done = {}
    for oid in plan["test_orders"]:
        await db.execute("DELETE FROM orders WHERE id = ? AND guild_id = ? AND livemode = 0", (oid, guild_id))
    done["test orders"] = len(plan["test_orders"])
    for file_id, user_id in plan["orphan_deliveries"]:
        await db.execute("DELETE FROM file_deliveries WHERE file_id = ? AND user_id = ?", (file_id, user_id))
    done["orphaned file deliveries"] = len(plan["orphan_deliveries"])
    for fid in plan["orphan_images"]:
        row = await db.fetch_one("SELECT path FROM stored_files WHERE id = ?", (fid,))
        await db.execute("DELETE FROM stored_files WHERE id = ?", (fid,))
        delete_disk_file(row["path"] if row else None)
    done["unused page images"] = len(plan["orphan_images"])
    for gid in plan["old_giveaways"]:
        await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id = ?", (gid,))
        await db.execute("DELETE FROM giveaways WHERE id = ?", (gid,))
    done["old giveaways"] = len(plan["old_giveaways"])
    await db.execute("UPDATE products SET file_id = NULL WHERE guild_id = ? AND file_id IS NOT NULL AND file_id NOT IN (SELECT id FROM stored_files)", (guild_id,))
    done["products with a missing file"] = plan["dead_product_links"]
    if plan["old_link_cards"]:
        await db.execute("DROP TABLE IF EXISTS link_cards")
        done["old link-card system"] = 1
    if plan["include_test_files"]:
        for fid, _ in plan["test_files"]:
            row = await db.fetch_one("SELECT path FROM stored_files WHERE id = ?", (fid,))
            await db.execute("UPDATE products SET file_id = NULL WHERE file_id = ?", (fid,))
            await db.execute("DELETE FROM file_deliveries WHERE file_id = ?", (fid,))
            await db.execute("DELETE FROM stored_files WHERE id = ?", (fid,))
            delete_disk_file(row["path"] if row else None)
        done["test files"] = len(plan["test_files"])
    return {k: v for k, v in done.items() if v}


class ConfirmCleanup(discord.ui.View):
    def __init__(self, user_id: int, plan: dict):
        super().__init__(timeout=120)
        self.user_id, self.plan = user_id, plan

    @discord.ui.button(label="Clean up now", emoji="🧹", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who ran the command can confirm.", ephemeral=True)
        self.stop()
        done = await apply_plan(interaction.guild_id, self.plan)
        body = "\n".join(f"• {n:,} {what}" for what, n in done.items()) or "Nothing needed removing."
        await interaction.response.edit_message(embed=ui.card("✅ Cleaned up", body, color=SUCCESS), view=None)
        await emit(interaction.guild, "staff", "Database cleanup", ui.kv(("🛡️ By", interaction.user.mention), ("🧹 Removed", ", ".join(f"{n} {what}" for what, n in done.items()) or "nothing")), subject=interaction.user.id)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(embed=ui.card("Cancelled", "Nothing was removed."), view=None)


@app_commands.guild_only()
class Cleanup(commands.Cog):
    @app_commands.command(description="Find and remove test orders, unused files and other leftovers (asks first)")
    @app_commands.describe(include_test_files="Also delete stored files whose name starts with 'test' or 'demo'")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cleanup(self, interaction: discord.Interaction, include_test_files: bool = False):
        plan = await survey(interaction.guild_id, include_test_files)
        lines = [
            ("🧪 Test-mode orders (payments made in Stripe test mode)", len(plan["test_orders"])),
            ("📥 File deliveries for files that no longer exist", len(plan["orphan_deliveries"])),
            ("🖼️ Page and banner images nothing uses", len(plan["orphan_images"])),
            (f"🎉 Giveaways finished more than {OLD_GIVEAWAY_DAYS} days ago", len(plan["old_giveaways"])),
            ("🔗 Products pointing at a deleted file", plan["dead_product_links"]),
            ("🗑️ The old /pyrex and /spotless link-card data", 1 if plan["old_link_cards"] else 0),
        ]
        body = "\n".join(f"{'**' + str(n) + '**' if n else '0'} · {what}" for what, n in lines)
        if plan["test_files"]:
            names = ", ".join(f"`{n}`" for _, n in plan["test_files"][:8])
            body += f"\n\n📦 **Files that look like tests:** {names}" + ("\n(They'll be deleted too, because you set `include_test_files`.)" if include_test_files else "\nRun again with `include_test_files:True` to remove them.")
        total = plan_total(plan)
        body += "\n\n" + ("Real purchases, real files, customers' records and settings are **never** touched." if total else "✅ Nothing to clean up. Your data is tidy.")
        await interaction.response.send_message(
            embed=ui.card("🧹 Cleanup preview", body, color=WARN, guild=interaction.guild, section="Staff"), view=ConfirmCleanup(interaction.user.id, plan) if total else None, ephemeral=True,
        )


# Bot DMs worth keeping by default: purchase receipts and anything with a macro key in it.
KEEP_TITLES = ("payment received", "order confirmed", "macro key", "your keys", "your purchases")


def is_receipt(message: discord.Message) -> bool:
    for e in message.embeds:
        text = " ".join(filter(None, [e.title or "", *(f.name or "" for f in e.fields)])).lower()
        if any(k in text for k in KEEP_TITLES):
            return True
    return False


@app_commands.guild_only()
class ClearDMs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(description="Delete the messages I've sent you by DM (admins can clear a member's)")
    @app_commands.describe(
        member="Admins only: clear this member's DMs from me instead of your own",
        keep_receipts="Keep purchase receipts and macro key messages (default: yes)",
    )
    async def cleardms(self, interaction: discord.Interaction, member: Optional[discord.Member] = None, keep_receipts: bool = True):
        target = member or interaction.user
        if target.id != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
            raise UserError("Only admins can clear someone else's DMs. Leave `member` empty to clear your own.")
        if target.bot:
            raise UserError("Bots don't have DMs with me.")
        await interaction.response.defer(ephemeral=True)
        try:
            dm = target.dm_channel or await target.create_dm()
        except discord.HTTPException:
            raise UserError("I couldn't open that DM.") from None
        deleted = kept = 0
        try:
            async for message in dm.history(limit=500):
                if message.author.id != self.bot.user.id:
                    continue  # I can only delete my own messages
                if keep_receipts and is_receipt(message):
                    kept += 1
                    continue
                try:
                    await message.delete()
                    deleted += 1
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.4)  # DMs can't be bulk-deleted, so go gently
        except discord.HTTPException:
            pass
        whose = "your" if target.id == interaction.user.id else f"{target.mention}'s"
        body = f"🗑️ Deleted **{deleted}** of my messages from {whose} DMs."
        if kept:
            body += f"\n🧾 Kept **{kept}** receipt/key message{'s' if kept != 1 else ''}. Run again with `keep_receipts:False` to remove them too."
        body += "\n\nOnly my messages can be deleted. Anything you sent stays, and Discord may take a moment to update."
        if target.id != interaction.user.id:
            await emit(interaction.guild, "staff", "🧹 Bot DMs cleared",
                       ui.kv(("👤 Member", f"{target.mention} · `{target.id}`"), ("🛡️ By", interaction.user.mention), ("🗑️ Deleted", str(deleted)),
                             ("🧾 Kept receipts", "yes" if keep_receipts else "no")), subject=target.id)
        await interaction.followup.send(embed=ui.card("🧹 DMs cleared", body, color=SUCCESS, guild=interaction.guild), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ClearDMs(bot))
    await bot.add_cog(Cleanup())
