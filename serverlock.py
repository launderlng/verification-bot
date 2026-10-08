from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import SUCCESS, WARN, UserError
from guildlock import allowed_ids, enforce_lock, env_allowed, lock_enabled, stored_allowed


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class ServerLock(commands.GroupCog, group_name="serverlock", group_description="Make the bot private to your server(s)"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def owner_only(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            raise UserError("Only the owner of this bot can change this.")

    async def add_server(self, guild_id: int, user_id: int) -> None:
        await db.execute(
            "INSERT OR IGNORE INTO allowed_guilds (guild_id, added_by, created_at) VALUES (?, ?, ?)", (guild_id, user_id, discord.utils.utcnow().isoformat())
        )

    def names(self, ids) -> str:
        lines = []
        for gid in sorted(ids):
            guild = self.bot.get_guild(gid)
            lines.append(f"• **{guild.name}** (`{gid}`)" if guild else f"• `{gid}` (the bot isn't in this server)")
        return "\n".join(lines) or "None"

    @app_commands.command(name="on", description="Lock the bot to this server: it leaves every other server")
    async def lock_on(self, interaction: discord.Interaction):
        await self.owner_only(interaction)
        await interaction.response.defer(ephemeral=True)
        await self.add_server(interaction.guild_id, interaction.user.id)
        left = await enforce_lock(self.bot)
        body = f"The bot now **only stays in approved servers** and leaves any other server it's added to.\n\n**Approved**\n{self.names(await allowed_ids())}"
        if left:
            body += f"\n\n🚪 It just left **{len(left)}** other server(s)."
        body += "\n\n💡 Also turn off **Public Bot** in the Discord Developer Portal (Bot tab), so nobody else can even add it."
        await interaction.followup.send(embed=ui.card("🔒 Server lock is on", body, color=SUCCESS, guild=interaction.guild, section="Server lock"), ephemeral=True)

    @app_commands.command(name="allow", description="Also approve another server by its ID")
    @app_commands.describe(server_id="Server ID: turn on Developer Mode, right-click the server, Copy Server ID")
    async def allow(self, interaction: discord.Interaction, server_id: app_commands.Range[str, 5, 25]):
        await self.owner_only(interaction)
        if not server_id.strip().isdigit():
            raise UserError("A server ID is only numbers, like `1234567890123456789`.")
        await self.add_server(int(server_id.strip()), interaction.user.id)
        await interaction.response.send_message(
            embed=ui.card("✅ Server approved", f"`{server_id.strip()}` can now have this bot.\n\n**Approved**\n{self.names(await allowed_ids())}", color=SUCCESS, section="Server lock"), ephemeral=True
        )

    @app_commands.command(name="off", description="Turn the lock off (the bot can stay in any server it's added to)")
    async def lock_off(self, interaction: discord.Interaction):
        await self.owner_only(interaction)
        await db.execute("DELETE FROM allowed_guilds")
        still = env_allowed()
        note = "\n\n⚠️ `ALLOWED_GUILD_IDS` is still set in your Railway variables, so the lock stays on until you remove it there." if still else ""
        await interaction.response.send_message(
            embed=ui.card("🔓 Server lock is off", "The bot will no longer leave servers. Anyone who can add it (see **Public Bot** in the Developer Portal) can use it." + note, color=WARN, section="Server lock"), ephemeral=True
        )

    @app_commands.command(name="status", description="See which servers the bot is approved for")
    async def status(self, interaction: discord.Interaction):
        await self.owner_only(interaction)
        on = await lock_enabled()
        present = {g.id for g in self.bot.guilds}
        allowed = await allowed_ids()
        body = (
            ("🔒 **The lock is ON.** The bot leaves any server that isn't approved." if on else "🔓 **The lock is OFF.** The bot stays in any server it's added to. Run `/serverlock on` in your server.")
            + f"\n\n**Approved servers**\n{self.names(allowed)}\n\n**Servers the bot is in right now:** {len(present)}"
        )
        stranger = present - allowed
        if on and stranger:
            body += f"\n⚠️ {len(stranger)} of them aren't approved (it leaves when it next checks or is re-invited)."
        await interaction.response.send_message(embed=ui.card("🔒 Server lock", body, guild=interaction.guild, section="Server lock"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerLock(bot))
