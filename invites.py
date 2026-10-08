import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import SUCCESS, WARN, UserError
from logutil import emit

log = logging.getLogger("verification-bot")
NEW_ACCOUNT_DAYS = 7  # joins from accounts younger than this are counted as "fake" (not valid invites)


def now() -> str:
    return discord.utils.utcnow().isoformat()


def pick_inviter(old: dict, current: dict) -> Optional[tuple]:
    """Work out which invite was just used by comparing use counts before and after someone joined.
    `old`/`current` map code -> (uses, inviter_id, max_uses). Returns (code, inviter_id) or None if it can't be told."""
    risen = [code for code, (uses, _, _) in current.items() if uses > old.get(code, (0, None, 0))[0]]
    if len(risen) == 1:
        return risen[0], current[risen[0]][1]
    if not risen:
        # a single-use / last-use invite disappears the moment it's used
        gone = [code for code, (uses, _, max_uses) in old.items() if code not in current and max_uses and uses + 1 >= max_uses]
        if len(gone) == 1:
            return gone[0], old[gone[0]][1]
    return None


async def stats_embed(guild: discord.Guild, member: discord.abc.User, stats: dict, rank: Optional[int]) -> discord.Embed:
    body = (
        f"# {stats['valid']:,}\n**valid invites**\n\n"
        + ui.kv(("👥 Joined with your link", f"{stats['joins']:,}"), ("🚪 Left since", f"{stats['left']:,}"), ("🚩 Fake (new accounts)", f"{stats['fake']:,}" if stats["fake"] else None),
                ("🎁 Staff bonus", f"{stats['bonus']:+,}" if stats["bonus"] else None), ("🏅 Rank", f"#{rank}" if rank else None))
    )
    return ui.card(f"📈 {member.display_name}'s invites", body, guild=guild, thumbnail=member.display_avatar.url, section="Invites")


async def ranking(guild_id: int) -> list[tuple[int, dict]]:
    ids = {r["inviter_id"] for r in await db.fetch_all("SELECT DISTINCT inviter_id FROM invite_joins WHERE guild_id = ? AND inviter_id IS NOT NULL AND counted = 1", (guild_id,))}
    ids |= {r["user_id"] for r in await db.fetch_all("SELECT user_id FROM invite_bonus WHERE guild_id = ? AND amount != 0", (guild_id,))}
    board = [(uid, await db.invite_stats(guild_id, uid)) for uid in ids]
    board = [(uid, st) for uid, st in board if st["valid"] > 0 or st["joins"] > 0]
    board.sort(key=lambda x: (-x[1]["valid"], -x[1]["joins"], x[0]))
    return board


class ConfirmReset(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id

    @discord.ui.button(label="Yes, reset everyone", emoji="⚠️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who ran the command can confirm.", ephemeral=True)
        await db.execute("UPDATE invite_joins SET counted = 0 WHERE guild_id = ?", (interaction.guild_id,))
        await db.execute("DELETE FROM invite_bonus WHERE guild_id = ?", (interaction.guild_id,))
        self.stop()
        await interaction.response.edit_message(embed=ui.card("✅ Everyone's invites were reset", "Counts start again from now.", color=SUCCESS), view=None)
        await emit(interaction.guild, "staff", "All invite counts reset", ui.kv(("🛡️ By", interaction.user.mention)), subject=interaction.user.id)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(embed=ui.card("Cancelled", "Nothing was changed."), view=None)


@app_commands.guild_only()
class Invites(commands.GroupCog, group_name="invites", group_description="Invite tracking: who brought who"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cache: dict[int, dict] = {}
        super().__init__()

    # ------------------------------------------------------------ tracking ----

    async def snapshot(self, guild: discord.Guild) -> dict:
        try:
            invites = await guild.invites()
        except discord.HTTPException:
            return {}  # needs the Manage Server permission
        return {i.code: (i.uses or 0, i.inviter.id if i.inviter else None, i.max_uses or 0) for i in invites}

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            self.cache[guild.id] = await self.snapshot(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        self.cache[guild.id] = await self.snapshot(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild is not None:
            self.cache.setdefault(invite.guild.id, {})[invite.code] = (invite.uses or 0, invite.inviter.id if invite.inviter else None, invite.max_uses or 0)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        # keep it in the cache: if it vanished because its last use was just spent, the next join needs it to find the inviter
        pass

    async def attribute(self, member: discord.Member) -> Optional[int]:
        """Record who invited a member who just joined. Returns the inviter's ID if known."""
        guild = member.guild
        old = self.cache.get(guild.id, {})
        current = await self.snapshot(guild)
        found = pick_inviter(old, current)
        self.cache[guild.id] = current
        code, inviter_id = found if found else (None, None)
        if inviter_id == member.id:
            inviter_id = None
        fake = int((discord.utils.utcnow() - member.created_at).days < NEW_ACCOUNT_DAYS and not member.bot)
        previous = await db.fetch_one("SELECT id FROM invite_joins WHERE guild_id = ? AND member_id = ? ORDER BY id LIMIT 1", (guild.id, member.id))
        if previous:  # leaving and re-joining doesn't count twice: the original record just becomes active again
            await db.execute("UPDATE invite_joins SET left_at = NULL, joined_at = ? WHERE id = ?", (now(), previous["id"]))
        else:
            await db.execute("INSERT INTO invite_joins (guild_id, member_id, inviter_id, code, joined_at, fake) VALUES (?, ?, ?, ?, ?, ?)", (guild.id, member.id, inviter_id, code, now(), fake))
        return inviter_id

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        inviter_id = await self.attribute(member)
        await emit(
            member.guild, "invites", "Invite used",
            ui.kv(("👤 Joined", member.mention), ("🔗 Invited by", f"<@{inviter_id}>" if inviter_id else "Unknown (vanity link, or I couldn't tell)")), subject=member.id,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await db.execute("UPDATE invite_joins SET left_at = ? WHERE guild_id = ? AND member_id = ? AND left_at IS NULL", (now(), member.guild.id, member.id))

    # ------------------------------------------------------------ commands ----

    @app_commands.command(description="See how many people someone has invited (you, if you leave it blank)")
    @app_commands.describe(member="Whose invites to check")
    async def check(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        stats = await db.invite_stats(interaction.guild_id, member.id)
        board = await ranking(interaction.guild_id)
        rank = next((i for i, (uid, _) in enumerate(board, start=1) if uid == member.id), None)
        await interaction.response.send_message(embed=await stats_embed(interaction.guild, member, stats, rank), ephemeral=True)

    @app_commands.command(description="The top inviters in the server")
    async def leaderboard(self, interaction: discord.Interaction):
        board = (await ranking(interaction.guild_id))[:10]
        if not board:
            raise UserError("Nobody has invited anyone yet.")
        lines = [f"{ui.rank(i)} <@{uid}> · **{st['valid']:,}** invites · {st['joins']:,} joined, {st['left']:,} left" for i, (uid, st) in enumerate(board)]
        await interaction.response.send_message(embed=ui.card("🏆 Invite leaderboard", "\n".join(lines), guild=interaction.guild, section="Invites"), ephemeral=True)

    @app_commands.command(description="Staff: see who invited a member")
    @app_commands.describe(member="The member")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def inviter(self, interaction: discord.Interaction, member: discord.Member):
        row = await db.fetch_one("SELECT * FROM invite_joins WHERE guild_id = ? AND member_id = ? ORDER BY id LIMIT 1", (interaction.guild_id, member.id))
        if not row:
            raise UserError("I have no record of how that member joined (they joined before invite tracking started).")
        body = ui.kv(("👤 Member", member.mention), ("🔗 Invited by", f"<@{row['inviter_id']}>" if row["inviter_id"] else "Unknown"), ("🎟️ Invite code", f"`{row['code']}`" if row["code"] else None),
                     ("📅 Joined", discord.utils.format_dt(discord.utils.parse_time(row["joined_at"]), "f")), ("🚩 Flag", "New account, not counted as a valid invite" if row["fake"] else None))
        await interaction.response.send_message(embed=ui.card("🔎 Who invited them", body, guild=interaction.guild, section="Invites"), ephemeral=True)

    @app_commands.command(description="Staff: add bonus invites to someone")
    @app_commands.describe(member="Who", amount="How many to add")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100000]):
        await self.adjust(interaction, member, amount)

    @app_commands.command(description="Staff: take invites away from someone")
    @app_commands.describe(member="Who", amount="How many to remove")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100000]):
        await self.adjust(interaction, member, -amount)

    async def adjust(self, interaction: discord.Interaction, member: discord.Member, delta: int) -> None:
        await db.execute(
            "INSERT INTO invite_bonus (guild_id, user_id, amount) VALUES (?, ?, ?) ON CONFLICT(guild_id, user_id) DO UPDATE SET amount = amount + excluded.amount",
            (interaction.guild_id, member.id, delta),
        )
        stats = await db.invite_stats(interaction.guild_id, member.id)
        await interaction.response.send_message(embed=ui.card("✅ Invites updated", f"{member.mention} now has **{stats['valid']:,}** valid invites ({delta:+,}).", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "staff", "Invites adjusted", ui.kv(("👤 Member", member.mention), ("🔢 Change", f"{delta:+,}"), ("📈 Now", f"{stats['valid']:,}"), ("🛡️ By", interaction.user.mention)), subject=member.id)

    @app_commands.command(description="Staff: reset one person's invites, or everyone's")
    @app_commands.describe(member="Whose invites to reset", everyone="Reset EVERYONE (asks you to confirm)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset(self, interaction: discord.Interaction, member: Optional[discord.Member] = None, everyone: bool = False):
        if everyone:
            return await interaction.response.send_message(
                embed=ui.card("⚠️ Reset everyone's invites?", "This sets every member's invite count back to zero. It can't be undone.", color=WARN), view=ConfirmReset(interaction.user.id), ephemeral=True
            )
        if member is None:
            raise UserError("Pick a **member** to reset, or set **everyone** to True.")
        await db.execute("UPDATE invite_joins SET counted = 0 WHERE guild_id = ? AND inviter_id = ?", (interaction.guild_id, member.id))
        await db.execute("DELETE FROM invite_bonus WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, member.id))
        await interaction.response.send_message(embed=ui.card("✅ Invites reset", f"{member.mention} is back to **0** valid invites.", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "staff", "Invites reset", ui.kv(("👤 Member", member.mention), ("🛡️ By", interaction.user.mention)), subject=member.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Invites(bot))
