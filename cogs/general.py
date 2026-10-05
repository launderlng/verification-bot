import discord
from discord import app_commands
from discord.ext import commands

from common import COLOR


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show how verification works and the admin commands")
    async def help_cmd(self, interaction: discord.Interaction):
        cmds = []
        for cmd in self.bot.tree.get_commands():
            if isinstance(cmd, app_commands.Group):
                cmds.extend(cmd.walk_commands())
            else:
                cmds.append(cmd)
        lines = [f"`/{c.qualified_name}`: {c.description}" for c in sorted(cmds, key=lambda c: c.qualified_name)]
        embed = discord.Embed(title="✅ Verification Bot", description="\n".join(lines), color=COLOR)
        embed.set_footer(text="Most commands need Manage Server or Manage Roles.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Check the bot's latency")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"🏓 Pong! `{round(self.bot.latency * 1000)}ms`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
