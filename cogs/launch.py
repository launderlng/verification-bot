"""
/launch — sends a signed launch signal to the user's XZX tray agent.
The tray agent polls the API, sees the token, and opens the XZX Launcher
on their PC without them needing to touch any files.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse
import urllib.request

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("verification-bot")

API_BASE    = os.getenv("XZX_API_BASE", "https://api.xzxsuite.com")
SECRET_SALT = b"xzx_suite_2025_salt_noclone"

# ── helpers ──────────────────────────────────────────────────────────────────────

async def _get_hwid_for_user(discord_id: str) -> str | None:
    """Look up the HWID registered by this user's tray agent via the API."""
    import asyncio
    def _do_get():
        try:
            params = urllib.parse.urlencode({"discord_id": discord_id})
            req = urllib.request.Request(f"{API_BASE}/tray/hwid?{params}")
            req.add_header("X-XZX-Bot", "1")
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            return data.get("hwid") or None
        except Exception:
            return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do_get)

def _make_launch_token(hwid: str, nonce: str) -> str:
    """HMAC(HWID:nonce:ts_window, SECRET_SALT)[:32] — same logic verified by the tray agent."""
    ts_window = str(int(time.time() // 300))
    raw = f"{hwid}:{nonce}:{ts_window}".encode()
    return hmac.new(SECRET_SALT, raw, hashlib.sha256).hexdigest()[:32]

async def _post_launch_signal(hwid: str, discord_id: str) -> bool:
    """POST a signed launch token to the API so the tray agent picks it up."""
    nonce = secrets.token_urlsafe(12)
    token = _make_launch_token(hwid, nonce)

    payload = urllib.parse.urlencode({
        "hwid":       hwid,
        "discord_id": discord_id,
        "token":      token,
        "nonce":      nonce,
        "ts":         int(time.time()),
    }).encode()

    import asyncio
    loop = asyncio.get_event_loop()

    def _do_post():
        req = urllib.request.Request(
            f"{API_BASE}/tray/signal",
            data=payload, method="POST"
        )
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("X-XZX-Bot", "1")
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        return data.get("ok", False)

    try:
        return await loop.run_in_executor(None, _do_post)
    except Exception as exc:
        log.warning(f"[launch] signal POST failed: {exc}")
        return False

async def _user_has_valid_license(discord_id: str) -> bool:
    """Check bot DB — user must have an active license to use /launch."""
    db_path = os.getenv("DB_PATH", "verification.db")
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                """
                SELECT 1 FROM licenses
                WHERE user_id = ?
                  AND active = 1
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
                LIMIT 1
                """,
                (int(discord_id),)
            ) as cur:
                return await cur.fetchone() is not None
    except Exception:
        return False

# ── cog ──────────────────────────────────────────────────────────────────────────

class Launch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="launch",
        description="Launch the XZX Suite on your PC instantly — no files needed."
    )
    async def launch(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        # 1. Must have a valid license
        if not await _user_has_valid_license(discord_id):
            embed = discord.Embed(
                title="❌ No active license",
                description=(
                    "You don't have an active XZX license.\n\n"
                    "Get access in <#shop-channel> or visit **discord.gg/xzxx**."
                ),
                color=0xFF1744,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        # 2. Must have the tray agent installed and registered
        hwid = await _get_hwid_for_user(discord_id)
        if not hwid:
            embed = discord.Embed(
                title="🖥️ Tray agent not found",
                description=(
                    "Your PC isn't connected yet.\n\n"
                    "**One-time setup:**\n"
                    "1. Download **XZX Tray** below\n"
                    "2. Run it once — it installs itself to startup\n"
                    "3. Come back and run `/launch` again\n\n"
                    "After setup, `/launch` will open XZX Suite on your PC instantly."
                ),
                color=0x5865F2,
            )
            embed.set_footer(text="The tray agent runs silently in the background.")

            # send download button
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Download XZX Tray",
                style=discord.ButtonStyle.link,
                url=f"{API_BASE}/tray/install?discord_id={discord_id}",
                emoji="⬇️",
            ))
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return

        # 3. Send launch signal
        ok = await _post_launch_signal(hwid, discord_id)

        if ok:
            embed = discord.Embed(
                title="🚀 Launching XZX Suite",
                description=(
                    "Signal sent to your PC!\n"
                    "The launcher should open in a few seconds.\n\n"
                    "*Make sure your PC is on and the tray agent is running.*"
                ),
                color=0x00E676,
            )
            embed.set_footer(text="XZX Suite • Validated server-side every launch")
        else:
            embed = discord.Embed(
                title="⚠️ Signal failed",
                description=(
                    "Couldn't reach your PC right now.\n\n"
                    "Make sure:\n"
                    "• Your PC is on\n"
                    "• XZX Tray is running (check your system tray)\n"
                    "• You have internet\n\n"
                    "Then try `/launch` again."
                ),
                color=0xFF9100,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle the Launch button on receipt DMs."""
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("launch_xzx:"):
            return

        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        # must have valid license
        if not await _user_has_valid_license(discord_id):
            await interaction.followup.send(
                "❌ No active license found. Get access in the shop.", ephemeral=True)
            return

        # look up HWID
        hwid = await _get_hwid_for_user(discord_id)
        if not hwid:
            embed = discord.Embed(
                title="🖥️ Tray agent not installed",
                description=(
                    "You need to install **XZX Tray** once first.\n\n"
                    "**Setup (one time only):**\n"
                    "1. Download and run **XZX Tray** below\n"
                    "2. Come back and click **Launch** again\n\n"
                    "After that, one click opens XZX Suite instantly."
                ),
                color=0x5865F2,
            )
            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Download XZX Tray",
                style=discord.ButtonStyle.link,
                url=f"{API_BASE}/tray/install?discord_id={discord_id}",
                emoji="⬇️",
            ))
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return

        # send launch signal
        ok = await _post_launch_signal(hwid, discord_id)
        if ok:
            await interaction.followup.send(
                "🚀 Launching… check your desktop in a few seconds.", ephemeral=True)
        else:
            await interaction.followup.send(
                "⚠️ Couldn't reach your PC. Make sure XZX Tray is running, then try again.",
                ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Launch(bot))
