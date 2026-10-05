import discord

COLOR = discord.Color.from_rgb(46, 204, 113)  # green
WARN = discord.Color.orange()
DANGER = discord.Color.red()


class UserError(Exception):
    """A user-facing error. The bot shows the message as an ephemeral reply."""


def check_role(interaction: discord.Interaction, role: discord.Role) -> None:
    """Make sure a role is safe to hand out and that both the bot and the admin can manage it."""
    guild = interaction.guild
    if role.is_default() or role.managed:
        raise UserError("Pick a normal role (not @everyone, a bot role or an integration role).")
    if role.permissions.administrator:
        raise UserError("For safety I won't hand out a role that has Administrator permission.")
    if role >= guild.me.top_role:
        raise UserError("My highest role must sit **above** that role. Drag my role higher in Server Settings → Roles.")
    if interaction.user != guild.owner and role >= interaction.user.top_role:
        raise UserError("You can only use roles that are below your own highest role.")
