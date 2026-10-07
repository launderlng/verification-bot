from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
from cogs.verify import MODES
from common import COLOR, UserError, check_can_send, check_role


class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup", description="Set up verification, welcome and logs in one go")
    @app_commands.describe(
        verify_channel="Where the verify panel goes",
        verified_role="Role members get once verified",
        welcome_channel="Where welcome cards go (optional)",
        log_channel="Where logs go (optional)",
        all_logs="Set up ALL logs in one click: private channels for every kind of log",
        method="How members prove they're human (default: maths question)",
    )
    @app_commands.choices(method=[app_commands.Choice(name=v, value=k) for k, v in MODES.items()])
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    async def quick_setup(
        self,
        interaction: discord.Interaction,
        verify_channel: discord.TextChannel,
        verified_role: discord.Role,
        welcome_channel: Optional[discord.TextChannel] = None,
        log_channel: Optional[discord.TextChannel] = None,
        all_logs: bool = False,
        method: Optional[app_commands.Choice[str]] = None,
    ):
        guild = interaction.guild
        verify = self.bot.get_cog("Verification")
        if verify is None:
            raise UserError("The verification module isn't loaded.")

        # Validate everything first so we don't half-configure the server
        check_role(interaction, verified_role)
        if welcome_channel:
            check_can_send(welcome_channel, guild.me, files=True)
        if log_channel:
            check_can_send(log_channel, guild.me)

        await interaction.response.defer(ephemeral=True)
        lines = []
        cfg = await verify.apply_setup(guild, verify_channel, verified_role, method.value if method else None, None)
        lines.append(f"✅ **Verification:** panel posted in {verify_channel.mention} (gives {verified_role.mention})")

        if all_logs:
            logs_cog = self.bot.get_cog("Logs")
            if logs_cog is None:
                raise UserError("The logs module isn't loaded.")
            made, _ = await logs_cog.run_full_setup(guild, interaction.user)
            lines.append(f"✅ **Logs:** everything is on, with {len(made)} private channels created in 📋 Logs")
        elif log_channel:
            await db.upsert_config(guild.id, log_channel_id=log_channel.id)
            lines.append(f"✅ **Logs:** sent to {log_channel.mention}")
        if welcome_channel:
            existing = await db.get_welcome(guild.id)
            updates = {"enabled": 1, "channel_id": welcome_channel.id}
            if existing is None:
                updates["style"] = "card"
            await db.upsert_welcome(guild.id, **updates)
            lines.append(f"✅ **Welcome:** cards go to {welcome_channel.mention}")

        embed = discord.Embed(title="🎉 You're set up!", description="\n".join(lines), color=COLOR)
        embed.add_field(
            name="Do these next",
            value=(
                f"• Hide your other channels from @everyone and let {verified_role.mention} see them\n"
                "• Make sure my role is **above** the roles I hand out\n"
                "• `/welcome message` to write your greeting, `/welcome test` to preview it\n"
                "• `/verify panel` and `/verify rules` to make the panel yours"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
