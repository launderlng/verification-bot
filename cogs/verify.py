import asyncio
import logging
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from captcha import make_code, make_math, render_captcha
from common import COLOR, DANGER, WARN, UserError, check_role

log = logging.getLogger("verification-bot")

LOCKOUT_SECONDS = 600
DEFAULT_PANEL = "Welcome! Press the button below to verify and unlock the rest of the server."
MODES = {"button": "One-click button", "math": "Maths question", "code": "Image code (captcha)"}


# ------------------------------------------------------------------ UI ----

class AnswerModal(discord.ui.Modal):
    def __init__(self, cog: "Verification", title: str, label: str, answer: str, method: str):
        super().__init__(title=title)
        self.cog, self.answer, self.method = cog, answer.upper(), method
        self.reply = discord.ui.TextInput(label=label[:45], max_length=12, placeholder="Type your answer")
        self.add_item(self.reply)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.handle_answer(interaction, self.reply.value.strip().upper() == self.answer, self.method)


class CodeView(discord.ui.View):
    def __init__(self, cog: "Verification", code: str):
        super().__init__(timeout=180)
        self.cog, self.code = cog, code

    @discord.ui.button(label="Enter code", style=discord.ButtonStyle.primary, emoji="⌨️")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AnswerModal(self.cog, "Verification", "Characters in the image", self.code, "code"))


class PanelView(discord.ui.View):
    """Persistent view: the fixed custom_id keeps the button working after restarts."""

    def __init__(self, cog: "Verification"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify:start")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.begin(interaction)


# ----------------------------------------------------------------- cog ----

@app_commands.guild_only()
class Verification(commands.GroupCog, group_name="verify", group_description="Member verification"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.failures: dict[tuple[int, int], list] = {}  # (guild, user) -> [fail count, lockout-until]
        self.joins: dict[int, deque] = defaultdict(deque)  # guild -> recent join timestamps
        super().__init__()

    async def cog_load(self):
        self.bot.add_view(PanelView(self))
        self.sweeper.start()

    async def cog_unload(self):
        self.sweeper.cancel()

    # ---------------------------------------------------------- helpers ----

    async def log_event(self, guild: discord.Guild, cfg, title: str, description: str, color: discord.Color = COLOR):
        if not cfg or not cfg["log_channel_id"]:
            return
        channel = guild.get_channel(cfg["log_channel_id"])
        if channel is None:
            return
        embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    def locked_for(self, guild_id: int, user_id: int) -> float:
        entry = self.failures.get((guild_id, user_id))
        return max(0.0, entry[1] - time.monotonic()) if entry else 0.0

    def register_failure(self, guild_id: int, user_id: int, max_attempts: int) -> int:
        """Record a wrong answer. Returns attempts left (0 means the user is now locked out)."""
        entry = self.failures.setdefault((guild_id, user_id), [0, 0.0])
        entry[0] += 1
        if entry[0] >= max_attempts:
            entry[0], entry[1] = 0, time.monotonic() + LOCKOUT_SECONDS
            return 0
        return max_attempts - entry[0]

    async def delete_panel(self, guild: discord.Guild, cfg) -> None:
        if not cfg or not cfg["panel_channel_id"] or not cfg["panel_message_id"]:
            return
        channel = guild.get_channel(cfg["panel_channel_id"])
        if channel is None:
            return
        try:
            await (await channel.fetch_message(cfg["panel_message_id"])).delete()
        except discord.HTTPException:
            pass

    async def post_panel(self, guild: discord.Guild, cfg, old=None) -> discord.Message:
        channel = guild.get_channel(cfg["panel_channel_id"])
        if not isinstance(channel, discord.TextChannel):
            raise UserError("The panel channel no longer exists. Run `/verify setup` again.")
        await self.delete_panel(guild, old or cfg)
        embed = discord.Embed(title="✅ Verification", description=cfg["panel_text"] or DEFAULT_PANEL, color=COLOR)
        message = await channel.send(embed=embed, view=PanelView(self))
        await db.upsert_config(guild.id, panel_message_id=message.id)
        return message

    def status_embed(self, guild: discord.Guild, cfg) -> discord.Embed:
        def role(rid):
            r = guild.get_role(rid) if rid else None
            return r.mention if r else "Not set"

        def chan(cid):
            return f"<#{cid}>" if cid else "Not set"

        embed = discord.Embed(title="⚙️ Verification settings", color=WARN if cfg["lockdown"] else COLOR)
        embed.add_field(name="Method", value=MODES[cfg["mode"]])
        embed.add_field(name="Verified role", value=role(cfg["verified_role_id"]))
        embed.add_field(name="Unverified role", value=role(cfg["unverified_role_id"]))
        embed.add_field(name="Panel channel", value=chan(cfg["panel_channel_id"]))
        embed.add_field(name="Log channel", value=chan(cfg["log_channel_id"]))
        embed.add_field(name="Welcome", value=chan(cfg["welcome_channel_id"]) if cfg["welcome_message"] else "Off")
        embed.add_field(name="Min account age", value=f"{cfg['min_account_age_days']} day(s)" if cfg["min_account_age_days"] else "Off")
        embed.add_field(name="Kick if unverified", value=f"After {cfg['kick_after_minutes']} min" if cfg["kick_after_minutes"] else "Off")
        embed.add_field(name="Max wrong answers", value=f"{cfg['max_attempts']} (then 10 min lockout)")
        embed.add_field(name="Raid auto-lockdown", value=f"{cfg['raid_threshold']} joins/min" if cfg["raid_threshold"] else "Off")
        embed.add_field(name="Lockdown", value="🔒 ON, verification paused" if cfg["lockdown"] else "Off")
        return embed

    # ----------------------------------------------------- verify flow ----

    async def begin(self, interaction: discord.Interaction):
        guild, member = interaction.guild, interaction.user
        cfg = await db.get_config(guild.id)
        if not cfg or not cfg["verified_role_id"]:
            return await interaction.response.send_message("Verification isn't set up in this server.", ephemeral=True)
        role = guild.get_role(cfg["verified_role_id"])
        if role is None:
            return await interaction.response.send_message("The verified role no longer exists. Please tell an admin.", ephemeral=True)
        if role in member.roles:
            return await interaction.response.send_message("You're already verified! 🎉", ephemeral=True)
        if cfg["lockdown"]:
            return await interaction.response.send_message("🔒 Verification is temporarily paused. Please try again later or contact a moderator.", ephemeral=True)

        min_days = cfg["min_account_age_days"]
        if min_days and (discord.utils.utcnow() - member.created_at).days < min_days:
            await self.log_event(guild, cfg, "⛔ Account too new", f"{member.mention} tried to verify but their account is under {min_days} day(s) old.", WARN)
            return await interaction.response.send_message(
                f"Your Discord account needs to be at least **{min_days} day(s)** old to verify here. Please try again later or contact a moderator.",
                ephemeral=True,
            )

        wait = self.locked_for(guild.id, member.id)
        if wait > 0:
            return await interaction.response.send_message(f"Too many wrong answers. Try again in about {int(wait // 60) + 1} minute(s).", ephemeral=True)

        mode = cfg["mode"]
        if mode == "button":
            return await self.complete(interaction, cfg, role, "button")
        if mode == "math":
            question, answer = make_math()
            return await interaction.response.send_modal(AnswerModal(self, "Quick check", question, str(answer), "math"))
        code = make_code()
        file = discord.File(render_captcha(code), filename="captcha.png")
        await interaction.response.send_message(
            "Type the characters shown in the image (not case-sensitive). You have 3 minutes.",
            file=file, view=CodeView(self, code), ephemeral=True,
        )

    async def handle_answer(self, interaction: discord.Interaction, correct: bool, method: str):
        guild, member = interaction.guild, interaction.user
        cfg = await db.get_config(guild.id)
        role = guild.get_role(cfg["verified_role_id"]) if cfg and cfg["verified_role_id"] else None
        if role is None:
            return await interaction.response.send_message("Verification isn't set up properly. Please tell an admin.", ephemeral=True)
        if self.locked_for(guild.id, member.id) > 0:
            return await interaction.response.send_message("Too many wrong answers. Please wait a few minutes and try again.", ephemeral=True)
        if cfg["lockdown"]:
            return await interaction.response.send_message("🔒 Verification is temporarily paused.", ephemeral=True)
        if correct:
            return await self.complete(interaction, cfg, role, method)
        remaining = self.register_failure(guild.id, member.id, cfg["max_attempts"])
        if remaining == 0:
            await self.log_event(guild, cfg, "🚫 Verification lockout", f"{member.mention} failed {cfg['max_attempts']} times and is locked out for 10 minutes.", WARN)
            return await interaction.response.send_message("❌ Too many wrong answers. You're locked out for 10 minutes.", ephemeral=True)
        await interaction.response.send_message(f"❌ Not quite. {remaining} attempt(s) left. Press **Verify** to get a fresh challenge.", ephemeral=True)

    async def complete(self, interaction: discord.Interaction, cfg, role: discord.Role, method: str):
        guild, member = interaction.guild, interaction.user
        try:
            await member.add_roles(role, reason="Passed verification")
            if cfg["unverified_role_id"]:
                unverified = guild.get_role(cfg["unverified_role_id"])
                if unverified and unverified in member.roles:
                    await member.remove_roles(unverified, reason="Passed verification")
        except discord.Forbidden:
            await self.log_event(guild, cfg, "⚠️ Couldn't assign role", f"I failed to give {role.mention} to {member.mention}. Move my role above it.", DANGER)
            return await interaction.response.send_message("I couldn't give you the role. Please tell an admin.", ephemeral=True)

        self.failures.pop((guild.id, member.id), None)
        await db.execute(
            "INSERT INTO verifications (guild_id, user_id, method, verified_at) VALUES (?, ?, ?, ?)",
            (guild.id, member.id, method, discord.utils.utcnow().isoformat()),
        )
        await interaction.response.send_message(f"✅ You're verified! Welcome to **{guild.name}**.", ephemeral=True)
        await self.log_event(guild, cfg, "✅ Member verified", f"{member.mention} verified ({method}).")
        await self.send_welcome(member, cfg)

    async def send_welcome(self, member: discord.Member, cfg):
        if not cfg["welcome_channel_id"] or not cfg["welcome_message"]:
            return
        channel = member.guild.get_channel(cfg["welcome_channel_id"])
        if channel is None:
            return
        text = (
            cfg["welcome_message"]
            .replace("{user}", member.mention)
            .replace("{server}", member.guild.name)
            .replace("{count}", str(member.guild.member_count))
        )
        try:
            await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException:
            pass

    # ----------------------------------------------------------- events ----

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        cfg = await db.get_config(member.guild.id)
        if not cfg or not cfg["verified_role_id"]:
            return

        if cfg["unverified_role_id"]:
            role = member.guild.get_role(cfg["unverified_role_id"])
            if role:
                try:
                    await member.add_roles(role, reason="New member awaiting verification")
                except discord.HTTPException:
                    pass

        threshold = cfg["raid_threshold"]
        if threshold:
            recent, now = self.joins[member.guild.id], time.monotonic()
            recent.append(now)
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= threshold and not cfg["lockdown"]:
                await db.upsert_config(member.guild.id, lockdown=1)
                await self.log_event(
                    member.guild, cfg, "🚨 Possible raid detected",
                    f"{len(recent)} members joined in under a minute. **Verification is now paused.**\n"
                    "Run `/verify lockdown enabled:False` when it's safe.", DANGER,
                )

    @tasks.loop(minutes=1)
    async def sweeper(self):
        """Kick members who joined after the feature was enabled and still haven't verified in time."""
        for guild in self.bot.guilds:
            cfg = await db.get_config(guild.id)
            if not cfg or not cfg["kick_after_minutes"] or not cfg["enabled_at"] or not cfg["verified_role_id"]:
                continue
            role = guild.get_role(cfg["verified_role_id"])
            if role is None or not guild.me.guild_permissions.kick_members:
                continue
            enabled_at = discord.utils.parse_time(cfg["enabled_at"])
            cutoff = discord.utils.utcnow() - timedelta(minutes=cfg["kick_after_minutes"])
            for member in list(guild.members):
                if member.bot or role in member.roles or member.id == guild.owner_id or not member.joined_at:
                    continue
                if member.joined_at < enabled_at or member.joined_at > cutoff:
                    continue  # joined before the feature was on, or still inside the time limit
                if member.guild_permissions.administrator or member.top_role >= guild.me.top_role:
                    continue
                try:
                    try:
                        await member.send(f"You were removed from **{guild.name}** for not verifying in time. You're welcome to rejoin and verify.")
                    except discord.HTTPException:
                        pass
                    await member.kick(reason="Did not verify in time")
                    await self.log_event(guild, cfg, "👢 Kicked unverified member", f"{member.mention} didn't verify within {cfg['kick_after_minutes']} minute(s).", WARN)
                except discord.HTTPException:
                    log.warning("Couldn't kick %s in %s", member, guild)
                await asyncio.sleep(1)

    @sweeper.before_loop
    async def before_sweeper(self):
        await self.bot.wait_until_ready()

    # --------------------------------------------------------- commands ----

    @app_commands.command(description="Set up verification and post the verify button")
    @app_commands.describe(
        channel="Where to post the verify panel",
        role="Role members receive when verified",
        method="How members prove they're human (default: maths question)",
        unverified_role="Optional role given on join and removed on verify",
    )
    @app_commands.choices(method=[app_commands.Choice(name=v, value=k) for k, v in MODES.items()])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        method: Optional[app_commands.Choice[str]] = None,
        unverified_role: Optional[discord.Role] = None,
    ):
        guild = interaction.guild
        if not guild.me.guild_permissions.manage_roles:
            raise UserError("I need the **Manage Roles** permission.")
        check_role(interaction, role)
        if unverified_role:
            check_role(interaction, unverified_role)
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise UserError(f"I need View Channel, Send Messages and Embed Links in {channel.mention}.")

        await interaction.response.defer(ephemeral=True)
        old = await db.get_config(guild.id)
        await db.upsert_config(
            guild.id, verified_role_id=role.id, panel_channel_id=channel.id, mode=method.value if method else (old["mode"] if old else "math"),
            unverified_role_id=unverified_role.id if unverified_role else (old["unverified_role_id"] if old else None),
            enabled_at=discord.utils.utcnow().isoformat(),
        )
        cfg = await db.get_config(guild.id)
        await self.post_panel(guild, cfg, old=old)
        await interaction.followup.send(
            f"✅ Verification is live in {channel.mention}.\n"
            f"**Next:** hide your other channels from @everyone and allow {role.mention}. "
            "Use `/verify settings` for logging, kick timers, account-age limits and raid protection.",
            embed=self.status_embed(guild, cfg), ephemeral=True,
        )

    @app_commands.command(description="Change verification settings (only the options you fill in)")
    @app_commands.describe(
        method="How members prove they're human",
        log_channel="Channel for verification logs",
        unverified_role="Role given on join and removed on verify",
        min_account_age_days="Block accounts younger than this many days (0 = off)",
        kick_after_minutes="Kick new members who don't verify in this long (0 = off)",
        max_attempts="Wrong answers allowed before a 10-minute lockout",
        raid_threshold="Auto-lockdown when this many members join within a minute (0 = off)",
        panel_text="New text for the verify panel",
    )
    @app_commands.choices(method=[app_commands.Choice(name=v, value=k) for k, v in MODES.items()])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings(
        self,
        interaction: discord.Interaction,
        method: Optional[app_commands.Choice[str]] = None,
        log_channel: Optional[discord.TextChannel] = None,
        unverified_role: Optional[discord.Role] = None,
        min_account_age_days: Optional[app_commands.Range[int, 0, 365]] = None,
        kick_after_minutes: Optional[app_commands.Range[int, 0, 10080]] = None,
        max_attempts: Optional[app_commands.Range[int, 1, 10]] = None,
        raid_threshold: Optional[app_commands.Range[int, 0, 100]] = None,
        panel_text: Optional[app_commands.Range[str, 1, 1000]] = None,
    ):
        guild = interaction.guild
        cfg = await db.get_config(guild.id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Run `/verify setup` first.")

        updates: dict = {}
        notes: list[str] = []
        if method:
            updates["mode"] = method.value
        if log_channel:
            perms = log_channel.permissions_for(guild.me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                raise UserError(f"I need View Channel, Send Messages and Embed Links in {log_channel.mention}.")
            updates["log_channel_id"] = log_channel.id
        if unverified_role:
            check_role(interaction, unverified_role)
            updates["unverified_role_id"] = unverified_role.id
        if min_account_age_days is not None:
            updates["min_account_age_days"] = min_account_age_days
        if kick_after_minutes is not None:
            updates["kick_after_minutes"] = kick_after_minutes
            if kick_after_minutes > 0:
                if not guild.me.guild_permissions.kick_members:
                    raise UserError("I need the **Kick Members** permission for that.")
                updates["enabled_at"] = discord.utils.utcnow().isoformat()
                notes.append(f"Members who **join from now on** and don't verify within {kick_after_minutes} min will be kicked. Existing members are never affected.")
        if max_attempts is not None:
            updates["max_attempts"] = max_attempts
        if raid_threshold is not None:
            updates["raid_threshold"] = raid_threshold
        if panel_text:
            updates["panel_text"] = panel_text
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option.")

        await interaction.response.defer(ephemeral=True)
        await db.upsert_config(guild.id, **updates)
        cfg = await db.get_config(guild.id)
        if panel_text:
            await self.post_panel(guild, cfg)
        await interaction.followup.send("\n".join(["✅ Settings saved."] + notes), embed=self.status_embed(guild, cfg), ephemeral=True)

    @app_commands.command(description="Set (or turn off) the welcome message sent after verification")
    @app_commands.describe(
        channel="Where to send it",
        message="Use {user}, {server} and {count}. Leave blank to turn the welcome off.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        message: Optional[app_commands.Range[str, 1, 1000]] = None,
    ):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg:
            raise UserError("Run `/verify setup` first.")
        if not message:
            await db.upsert_config(interaction.guild_id, welcome_message=None, welcome_channel_id=None)
            return await interaction.response.send_message("Welcome message turned off.", ephemeral=True)
        if channel is None:
            raise UserError("Pick the channel to send the welcome message in.")
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            raise UserError(f"I can't send messages in {channel.mention}.")
        await db.upsert_config(interaction.guild_id, welcome_message=message, welcome_channel_id=channel.id)
        preview = message.replace("{user}", interaction.user.mention).replace("{server}", interaction.guild.name).replace("{count}", str(interaction.guild.member_count))
        await interaction.response.send_message(f"✅ Saved. Preview:\n>>> {preview}", ephemeral=True)

    @app_commands.command(description="Show the current verification settings")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Verification isn't set up. Use `/verify setup`.")
        await interaction.response.send_message(embed=self.status_embed(interaction.guild, cfg), ephemeral=True)

    @app_commands.command(description="Re-post the verify panel (e.g. if it was deleted)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def repost(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Verification isn't set up. Use `/verify setup`.")
        await interaction.response.defer(ephemeral=True)
        message = await self.post_panel(interaction.guild, cfg)
        await interaction.followup.send(f"✅ Panel re-posted: {message.jump_url}", ephemeral=True)

    @app_commands.command(description="Pause or resume verification (use during a raid)")
    @app_commands.describe(enabled="True = pause verification, False = resume")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def lockdown(self, interaction: discord.Interaction, enabled: bool):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Verification isn't set up. Use `/verify setup`.")
        await db.upsert_config(interaction.guild_id, lockdown=int(enabled))
        await self.log_event(interaction.guild, cfg, "🔒 Lockdown enabled" if enabled else "🔓 Lockdown lifted", f"By {interaction.user.mention}.", WARN if enabled else COLOR)
        await interaction.response.send_message(
            "🔒 Verification is **paused**. Nobody can verify until you run this again with `enabled: False`." if enabled else "🔓 Verification is **back on**.",
            ephemeral=True,
        )

    @app_commands.command(description="Manually verify a member")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def manual(self, interaction: discord.Interaction, member: discord.Member):
        guild = interaction.guild
        cfg = await db.get_config(guild.id)
        role = guild.get_role(cfg["verified_role_id"]) if cfg and cfg["verified_role_id"] else None
        if role is None:
            raise UserError("Verification isn't set up. Use `/verify setup`.")
        check_role(interaction, role)
        await member.add_roles(role, reason=f"Manually verified by {interaction.user}")
        if cfg["unverified_role_id"]:
            unverified = guild.get_role(cfg["unverified_role_id"])
            if unverified and unverified in member.roles:
                await member.remove_roles(unverified, reason=f"Manually verified by {interaction.user}")
        await db.execute(
            "INSERT INTO verifications (guild_id, user_id, method, verified_at) VALUES (?, ?, ?, ?)",
            (guild.id, member.id, "manual", discord.utils.utcnow().isoformat()),
        )
        await self.log_event(guild, cfg, "🛠️ Manually verified", f"{member.mention} was verified by {interaction.user.mention}.")
        await interaction.response.send_message(f"✅ Verified {member.mention}.", ephemeral=True)

    @app_commands.command(description="Remove a member's verified status")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def unverify(self, interaction: discord.Interaction, member: discord.Member):
        guild = interaction.guild
        cfg = await db.get_config(guild.id)
        role = guild.get_role(cfg["verified_role_id"]) if cfg and cfg["verified_role_id"] else None
        if role is None:
            raise UserError("Verification isn't set up. Use `/verify setup`.")
        check_role(interaction, role)
        await member.remove_roles(role, reason=f"Unverified by {interaction.user}")
        if cfg["unverified_role_id"]:
            unverified = guild.get_role(cfg["unverified_role_id"])
            if unverified:
                await member.add_roles(unverified, reason=f"Unverified by {interaction.user}")
        self.failures.pop((guild.id, member.id), None)
        await self.log_event(guild, cfg, "↩️ Unverified", f"{member.mention} was unverified by {interaction.user.mention}.", WARN)
        await interaction.response.send_message(f"↩️ {member.mention} is no longer verified.", ephemeral=True)

    @app_commands.command(description="Verification statistics")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stats(self, interaction: discord.Interaction):
        guild = interaction.guild
        cfg = await db.get_config(guild.id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Verification isn't set up. Use `/verify setup`.")
        now = discord.utils.utcnow()
        total = (await db.fetch_one("SELECT COUNT(*) AS c FROM verifications WHERE guild_id = ?", (guild.id,)))["c"]
        day = (await db.fetch_one("SELECT COUNT(*) AS c FROM verifications WHERE guild_id = ? AND verified_at >= ?", (guild.id, (now - timedelta(days=1)).isoformat())))["c"]
        week = (await db.fetch_one("SELECT COUNT(*) AS c FROM verifications WHERE guild_id = ? AND verified_at >= ?", (guild.id, (now - timedelta(days=7)).isoformat())))["c"]
        methods = await db.fetch_all("SELECT method, COUNT(*) AS c FROM verifications WHERE guild_id = ? GROUP BY method ORDER BY c DESC", (guild.id,))
        role = guild.get_role(cfg["verified_role_id"])
        humans = [m for m in guild.members if not m.bot]
        unverified = sum(1 for m in humans if role and role not in m.roles)

        embed = discord.Embed(title="📈 Verification stats", color=COLOR)
        embed.add_field(name="Verified (all time)", value=total)
        embed.add_field(name="Last 24 hours", value=day)
        embed.add_field(name="Last 7 days", value=week)
        embed.add_field(name="Members without the role", value=f"{unverified} of {len(humans)}")
        embed.add_field(name="By method", value="\n".join(f"{r['method']}: {r['c']}" for r in methods) or "None yet", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Turn verification off and remove the panel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg:
            raise UserError("Verification isn't set up.")
        await interaction.response.defer(ephemeral=True)
        await self.delete_panel(interaction.guild, cfg)
        await db.delete_config(interaction.guild_id)
        await interaction.followup.send("🛑 Verification disabled and settings cleared. Members keep their roles.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
