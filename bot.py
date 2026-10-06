import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

import db  # noqa: E402  (imported after load_dotenv so DB_PATH is picked up)
from common import UserError  # noqa: E402

TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("GUILD_ID")  # optional: instant command sync to one server while developing
EXTENSIONS = ["cogs.verify", "cogs.welcome", "cogs.logs", "cogs.moderation", "cogs.shop", "cogs.tickets", "cogs.reviews", "cogs.giveaways", "cogs.linkcards", "cogs.templates", "cogs.setup", "cogs.general"]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("verification-bot")


class VerificationBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # privileged: needed to see joins, assign roles and kick unverified members
        # Optional privileged intent: lets the log show the TEXT of deleted/edited messages.
        # Turn it on in the Developer Portal first, then set MESSAGE_CONTENT=true.
        intents.message_content = os.getenv("MESSAGE_CONTENT", "").strip().lower() in ("1", "true", "yes")
        super().__init__(command_prefix="!", intents=intents)
        self.extension_status: dict[str, str] = {}
        self.sync_status = "not run yet"

    async def setup_hook(self):
        await db.init()
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                self.extension_status[ext] = "ok"
                log.info("Loaded %s", ext)
            except commands.ExtensionNotFound:
                self.extension_status[ext] = f"missing file: expected {ext.replace('.', '/')}.py in your repo"
                log.error("MISSING FILE: %s (expected %s.py in your repo). Skipping it.", ext, ext.replace(".", "/"))
            except Exception as e:
                cause = getattr(e, "original", e)
                self.extension_status[ext] = f"failed to load: {type(cause).__name__}: {cause}"
                log.exception("Could not load %s. Skipping it.", ext)
        try:
            if DEV_GUILD_ID:
                guild = discord.Object(id=int(DEV_GUILD_ID))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                log.info("Synced commands to dev guild %s", DEV_GUILD_ID)
            else:
                await self.tree.sync()
                log.info("Synced commands globally (can take up to an hour to appear)")
            self.sync_status = "ok"
        except Exception as e:
            self.sync_status = f"failed: {type(e).__name__}: {e}"
            log.exception("Syncing slash commands failed. The bot will keep running with the previously registered commands.")

    async def on_ready(self):
        log.info("Logged in as %s (%s) in %d server(s)", self.user, self.user.id, len(self.guilds))
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="bypassing your check"))


bot = VerificationBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", None)
    if isinstance(original, UserError):
        msg = f"⚠️ {original}"
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "You don't have permission to use that command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = "I'm missing a permission I need for that. Check my role's permissions."
    elif isinstance(error, app_commands.NoPrivateMessage):
        msg = "That command only works inside a server."
    elif isinstance(original, discord.Forbidden):
        msg = "I don't have permission to do that. Check my role position and permissions."
    else:
        log.exception("Unhandled command error", exc_info=error)
        msg = "Something went wrong running that command."
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env file (see .env.example).")
    try:
        bot.run(TOKEN)
    except discord.PrivilegedIntentsRequired:
        raise SystemExit(
            "Enable 'Server Members Intent' (and 'Message Content Intent' if MESSAGE_CONTENT=true) for your bot: "
            "Developer Portal → your app → Bot → Privileged Gateway Intents."
        )
