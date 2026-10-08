import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import ui
from common import SUCCESS, UserError
from logutil import emit

log = logging.getLogger("verification-bot")


@app_commands.guild_only()
class Activity(commands.GroupCog, group_name="messages", group_description="Message counts (used by giveaway requirements)"):
    """Counts how many messages each person sends, per channel. Only the number is kept, never what was said."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.buffer: dict[tuple[int, int, int], int] = {}
        super().__init__()

    async def cog_load(self):
        self.flusher.start()

    async def cog_unload(self):
        self.flusher.cancel()
        await self.flush()

    @tasks.loop(seconds=10)
    async def flusher(self):
        await self.flush()

    @flusher.before_loop
    async def before_flusher(self):
        await self.bot.wait_until_ready()

    async def flush(self) -> None:
        pending, self.buffer = self.buffer, {}
        for (guild_id, user_id, channel_id), n in pending.items():
            await db.execute(
                "INSERT INTO message_counts (guild_id, user_id, channel_id, count) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(guild_id, user_id, channel_id) DO UPDATE SET count = count + excluded.count", (guild_id, user_id, channel_id, n),
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or getattr(message, "webhook_id", None):
            return
        key = (message.guild.id, message.author.id, message.channel.id)
        self.buffer[key] = self.buffer.get(key, 0) + 1

    async def total(self, guild_id: int, user_id: int, channel_id: Optional[int] = None) -> int:
        await self.flush()
        return await db.message_total(guild_id, user_id, channel_id)

    @app_commands.command(description="See how many messages someone has sent (you, if you leave it blank)")
    @app_commands.describe(member="Whose messages to check")
    async def check(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        await self.flush()
        total = await db.message_total(interaction.guild_id, member.id)
        top = await db.fetch_all("SELECT channel_id, count FROM message_counts WHERE guild_id = ? AND user_id = ? ORDER BY count DESC LIMIT 5", (interaction.guild_id, member.id))
        lines = [f"<#{r['channel_id']}> · **{r['count']:,}**" for r in top]
        body = f"# {total:,}\n**messages counted**\n\n" + ("**Most active in**\n" + "\n".join(lines) if lines else "Nothing counted yet.") + "\n\n*Counting started when message tracking was switched on.*"
        await interaction.response.send_message(embed=ui.card(f"💬 {member.display_name}'s messages", body, guild=interaction.guild, thumbnail=member.display_avatar.url, section="Activity"), ephemeral=True)

    @app_commands.command(description="The most active members")
    async def top(self, interaction: discord.Interaction):
        await self.flush()
        rows = await db.fetch_all("SELECT user_id, SUM(count) AS n FROM message_counts WHERE guild_id = ? GROUP BY user_id ORDER BY n DESC LIMIT 10", (interaction.guild_id,))
        if not rows:
            raise UserError("No messages have been counted yet.")
        lines = [f"{ui.rank(i)} <@{r['user_id']}> · **{r['n']:,}** messages" for i, r in enumerate(rows)]
        await interaction.response.send_message(embed=ui.card("🏆 Most active", "\n".join(lines), guild=interaction.guild, section="Activity"), ephemeral=True)

    @app_commands.command(description="Staff: reset someone's message count")
    @app_commands.describe(member="Whose count to reset")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reset(self, interaction: discord.Interaction, member: discord.Member):
        await self.flush()
        await db.execute("DELETE FROM message_counts WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, member.id))
        await interaction.response.send_message(embed=ui.card("✅ Reset", f"{member.mention}'s message count is back to 0.", color=SUCCESS), ephemeral=True)
        await emit(interaction.guild, "staff", "Message count reset", ui.kv(("👤 Member", member.mention), ("🛡️ By", interaction.user.mention)), subject=member.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(Activity(bot))
