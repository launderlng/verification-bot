import logging
import os

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# Files that sit at the TOP LEVEL of the repo next to bot.py. Only the first two are needed just to start; if any other helper is
# missing the bot still starts (the commands that need it show as failed in /diagnose) and the log says exactly which file to upload.
CORE_FILES = ["common.py", "db.py"]
TOP_LEVEL_FILES = CORE_FILES + [
    "ui.py", "logutil.py", "statusutil.py", "stripeutil.py", "transcript.py", "templateutil.py",
    "presets.py", "giveawayutil.py", "captcha.py", "welcomecard.py", "fileutil.py", "guildlock.py",
]


def missing_top_level_files(base: str, names=None) -> list[str]:
    return [name for name in (names or TOP_LEVEL_FILES) if not os.path.exists(os.path.join(base, name))]


_here = os.path.dirname(os.path.abspath(__file__))
_missing_core = missing_top_level_files(_here, CORE_FILES)
if _missing_core:
    raise SystemExit(
        "\nMISSING FILES: " + ", ".join(_missing_core) + "\n"
        "These belong at the TOP LEVEL of your GitHub repo (next to bot.py), not inside the cogs folder.\n"
        "Upload them (the zip has all of them), let Railway redeploy, and this error will go away.\n"
    )
_missing_helpers = missing_top_level_files(_here, [n for n in TOP_LEVEL_FILES if n not in CORE_FILES])

import db  # noqa: E402  (imported after load_dotenv so DB_PATH is picked up)
from common import UserError  # noqa: E402

try:
    from guildlock import enforce_lock  # noqa: E402
except ImportError:  # guildlock.py isn't uploaded yet: the bot still runs, just without the server lock
    async def enforce_lock(bot):
        return []

try:
    from statusutil import parse_interval, parse_statuses, pick  # noqa: E402
except ImportError:  # statusutil.py isn't uploaded yet: use the default rotating status
    def parse_statuses(raw):
        return [".gg/xzxx", "bypassing the check"]

    def parse_interval(raw, default=30, minimum=15):
        return default

    def pick(index, statuses):
        return statuses[index % len(statuses)]

TOKEN = os.getenv("DISCORD_TOKEN")
# The bot's status cycles through these (comma-separated in BOT_STATUSES) every STATUS_SECONDS seconds
STATUSES = parse_statuses(os.getenv("BOT_STATUSES"))
STATUS_SECONDS = parse_interval(os.getenv("STATUS_SECONDS"))
DEV_GUILD_ID = os.getenv("GUILD_ID")  # optional: instant command sync to one server while developing
EXTENSIONS = [
    "cogs.pages", "cogs.verify", "cogs.welcome", "cogs.logs", "cogs.moderation", "cogs.automod", "cogs.shop", "cogs.tickets", "cogs.reviews",
    "cogs.giveaways", "cogs.invites", "cogs.activity", "cogs.pingroles", "cogs.files", "cogs.serverlock", "cogs.copychannel", "cogs.posts",
    "cogs.gifcreator", "cogs.staff", "cogs.cleanup", "cogs.templates", "cogs.setup", "cogs.general", "cogs.autoupload",
    "cogs.licenses",
]

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("verification-bot")
if _missing_helpers:
    log.error(
        "MISSING FILES: %s. Upload them to the TOP LEVEL of your GitHub repo (next to bot.py). Commands that need them won't load until you do.",
        ", ".join(_missing_helpers),
    )


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
        self.status_index = 0
        # One web server for the whole bot: the health check, Stripe's webhook and big-file downloads all share it
        self.web_app = web.Application(client_max_size=1_000_000)
        self.web_runner = None

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
        await self.start_web()
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

    async def start_web(self):
        async def health(request: web.Request) -> web.Response:
            return web.Response(text="ok")

        self.web_app.router.add_get("/", health)
        port = int(os.getenv("PORT", "8080"))
        try:
            self.web_runner = web.AppRunner(self.web_app)
            await self.web_runner.setup()
            await web.TCPSite(self.web_runner, "0.0.0.0", port).start()
            log.info("Web server listening on port %s", port)
        except OSError:
            log.exception("Couldn't start the web server on port %s. Stripe webhooks and big-file download links won't work.", port)

    async def close(self):
        if self.web_runner is not None:
            await self.web_runner.cleanup()
        await super().close()

    async def on_guild_join(self, guild: discord.Guild):
        log.info("Added to %s (%s)", guild.name, guild.id)
        await enforce_lock(self)

    async def on_ready(self):
        log.info("Logged in as %s (%s) in %d server(s)", self.user, self.user.id, len(self.guilds))
        await enforce_lock(self)  # leaves any server that isn't approved (only if the lock is on)
        if not self.rotate_status.is_running():
            self.rotate_status.change_interval(seconds=STATUS_SECONDS)
            self.rotate_status.start()

    @tasks.loop(seconds=30)
    async def rotate_status(self):
        text = pick(self.status_index, STATUSES)
        self.status_index += 1
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=text))

    @rotate_status.before_loop
    async def before_rotate_status(self):
        await self.wait_until_ready()


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
