import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
from captcha import make_code, make_math, render_captcha
from common import COLOR, DANGER, INFO, WARN, UserError, check_can_send, check_role, parse_color, reply
from logutil import emit

log = logging.getLogger("verification-bot")

LOCKOUT_SECONDS = 600
CHALLENGE_SECONDS = 180
DEFAULT_PANEL = "Welcome! Complete a quick check below to unlock the rest of the server."

MODES = {
    "button": "One-click button",
    "math": "Maths question",
    "emoji": "Emoji pick",
    "code": "Image code (captcha)",
}
MODE_BLURB = {
    "button": "That's it, you'll be verified instantly.",
    "math": "Answer a quick maths question.",
    "emoji": "Tap the emoji we ask for.",
    "code": "Type the characters shown in an image.",
}
EMOJIS = {
    "🍎": "apple", "🍌": "banana", "🍇": "grapes", "🍒": "cherries", "🍋": "lemon", "🍉": "watermelon",
    "🐶": "dog", "🐱": "cat", "🐸": "frog", "🐼": "panda", "🚗": "car", "🚀": "rocket", "⚽": "football",
    "🎸": "guitar", "🌵": "cactus", "⭐": "star", "🔥": "fire", "🍕": "pizza", "🎈": "balloon", "🌙": "moon",
}


# ------------------------------------------------------------ embeds ----

def simple(title: str, text: str, color: discord.Color = INFO) -> discord.Embed:
    return discord.Embed(title=title, description=text, color=color)


def build_panel(guild: discord.Guild, cfg) -> discord.Embed:
    color = int(cfg["panel_color"], 16) if cfg["panel_color"] else COLOR.value
    embed = discord.Embed(title=cfg["panel_title"] or "✅ Verify to join", description=cfg["panel_text"] or DEFAULT_PANEL, color=color)
    label = cfg["button_label"] or "Verify"
    role = guild.get_role(cfg["verified_role_id"]) if cfg["verified_role_id"] else None
    steps = [f"Press **{label}**"]
    if cfg["rules_text"]:
        steps.append("Read and agree to the rules")
    steps.append(MODE_BLURB.get(cfg["mode"], MODE_BLURB["math"]))
    steps.append(f"Get {role.mention if role else 'your role'} and unlock the server")
    embed.add_field(name="How it works", value="\n".join(f"**{i}.** {s}" for i, s in enumerate(steps, 1)), inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if cfg["panel_image"]:
        embed.set_image(url=cfg["panel_image"])
    embed.set_footer(text=f"{guild.name} · Verification", icon_url=guild.icon.url if guild.icon else None)
    return embed


def success_embed(guild: discord.Guild, role: discord.Role) -> discord.Embed:
    embed = discord.Embed(
        title="✅ You're verified!",
        description=f"Welcome to **{guild.name}**! You now have {role.mention} and can see the rest of the server.",
        color=COLOR,
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


# ---------------------------------------------------------------- UI ----

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
        super().__init__(timeout=CHALLENGE_SECONDS)
        self.cog, self.code = cog, code

    @discord.ui.button(label="Enter code", style=discord.ButtonStyle.primary, emoji="⌨️")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AnswerModal(self.cog, "Verification", "Characters in the image", self.code, "code"))

    @discord.ui.button(label="New code", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.cog.code_challenge(interaction, edit=True)


class EmojiView(discord.ui.View):
    def __init__(self, cog: "Verification", options: list[str], answer: str):
        super().__init__(timeout=CHALLENGE_SECONDS)
        self.cog, self.answer = cog, answer
        for emoji in options:
            button = discord.ui.Button(emoji=emoji, style=discord.ButtonStyle.secondary)
            button.callback = self._make_callback(emoji)
            self.add_item(button)

    def _make_callback(self, emoji: str):
        async def callback(interaction: discord.Interaction):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)  # stops the buttons being reused
            self.stop()
            await self.cog.handle_answer(interaction, emoji == self.answer, "emoji")
        return callback


class RulesGate(discord.ui.View):
    def __init__(self, cog: "Verification"):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.button(label="I agree", style=discord.ButtonStyle.success, emoji="✅")
    async def agree(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await self.cog.after_rules(interaction)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(embed=simple("Verification cancelled", "You need to agree to the rules to verify. Press **Verify** again whenever you're ready.", WARN), view=None)


class PanelView(discord.ui.View):
    """Persistent view: fixed custom_ids keep the buttons working after restarts."""

    def __init__(self, cog: "Verification", label: str = "Verify", show_rules: bool = True):
        super().__init__(timeout=None)
        self.cog = cog
        self.verify_button.label = label[:80]
        if not show_rules:
            self.remove_item(self.rules_button)

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify:start")
    async def verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.begin(interaction)

    @discord.ui.button(label="Rules", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="verify:rules")
    async def rules_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_rules(interaction)

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary, emoji="❓", custom_id="verify:help")
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_help(interaction)


class RulesModal(discord.ui.Modal):
    def __init__(self, cog: "Verification", current: Optional[str]):
        super().__init__(title="Verification rules")
        self.cog = cog
        self.rules = discord.ui.TextInput(
            label="Rules members must agree to", style=discord.TextStyle.paragraph, required=False, max_length=2000,
            placeholder="Leave empty to turn the rules step off", default=(current or "")[:2000] or None,
        )
        self.add_item(self.rules)

    async def on_submit(self, interaction: discord.Interaction):
        text = self.rules.value.strip()
        await db.upsert_config(interaction.guild_id, rules_text=text or None)
        cfg = await db.get_config(interaction.guild_id)
        if cfg["panel_message_id"]:
            await self.cog.post_panel(interaction.guild, cfg)  # refresh so the Rules button appears or disappears
        await interaction.response.send_message(
            "✅ Rules saved. New members must agree to them before the check." if text else "✅ Rules step turned off.", ephemeral=True
        )


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
        await emit(guild, "verification", title, description, color)

    def locked_for(self, guild_id: int, user_id: int) -> float:
        entry = self.failures.get((guild_id, user_id))
        return max(0.0, entry[1] - time.monotonic()) if entry else 0.0

    def attempts_left(self, guild_id: int, user_id: int, max_attempts: int) -> int:
        entry = self.failures.get((guild_id, user_id))
        return max_attempts - (entry[0] if entry else 0)

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
        channel = guild.get_channel(cfg["panel_channel_id"]) if cfg["panel_channel_id"] else None
        if not isinstance(channel, discord.TextChannel):
            raise UserError("The panel channel no longer exists. Run `/verify setup` again.")
        await self.delete_panel(guild, old or cfg)
        view = PanelView(self, label=cfg["button_label"] or "Verify", show_rules=bool(cfg["rules_text"]))
        message = await channel.send(embed=build_panel(guild, cfg), view=view)
        await db.upsert_config(guild.id, panel_message_id=message.id)
        return message

    def status_embed(self, guild: discord.Guild, cfg) -> discord.Embed:
        def role(rid):
            r = guild.get_role(rid) if rid else None
            return r.mention if r else "Not set"

        def chan(cid):
            return f"<#{cid}>" if cid else "Not set"

        embed = discord.Embed(title="⚙️ Verification settings", color=WARN if cfg["lockdown"] else COLOR)
        embed.add_field(name="Method", value=MODES.get(cfg["mode"], cfg["mode"]))
        embed.add_field(name="Verified role", value=role(cfg["verified_role_id"]))
        embed.add_field(name="Unverified role", value=role(cfg["unverified_role_id"]))
        embed.add_field(name="Panel channel", value=chan(cfg["panel_channel_id"]))
        embed.add_field(name="Log channel", value=chan(cfg["log_channel_id"]))
        embed.add_field(name="Rules step", value="On" if cfg["rules_text"] else "Off")
        embed.add_field(name="Min account age", value=f"{cfg['min_account_age_days']} day(s)" if cfg["min_account_age_days"] else "Off")
        embed.add_field(name="Kick if unverified", value=f"After {cfg['kick_after_minutes']} min" if cfg["kick_after_minutes"] else "Off")
        embed.add_field(name="Wrong answers", value=f"{cfg['max_attempts']} allowed, then a 10 min lockout")
        embed.add_field(name="Raid auto-lockdown", value=f"{cfg['raid_threshold']} joins/min" if cfg["raid_threshold"] else "Off")
        embed.add_field(name="Lockdown", value="🔒 ON, verification paused" if cfg["lockdown"] else "Off")
        embed.add_field(name="Button label", value=cfg["button_label"] or "Verify")
        return embed

    async def apply_setup(self, guild: discord.Guild, channel: discord.TextChannel, role: discord.Role,
                          method: Optional[str], unverified_role: Optional[discord.Role]):
        """Shared by /verify setup and /setup: save the settings and post the panel."""
        if not guild.me.guild_permissions.manage_roles:
            raise UserError("I need the **Manage Roles** permission.")
        check_can_send(channel, guild.me)
        old = await db.get_config(guild.id)
        await db.upsert_config(
            guild.id, verified_role_id=role.id, panel_channel_id=channel.id,
            mode=method or (old["mode"] if old else "math"),
            unverified_role_id=unverified_role.id if unverified_role else (old["unverified_role_id"] if old else None),
            enabled_at=discord.utils.utcnow().isoformat(),
        )
        cfg = await db.get_config(guild.id)
        await self.post_panel(guild, cfg, old=old)
        return cfg

    # ----------------------------------------------------- verify flow ----

    async def precheck(self, interaction: discord.Interaction):
        """Run all the 'are they allowed to verify right now?' checks. Returns (cfg, role) or None."""
        guild, member = interaction.guild, interaction.user
        cfg = await db.get_config(guild.id)
        if not cfg or not cfg["verified_role_id"]:
            await reply(interaction, embed=simple("Not set up", "Verification isn't set up in this server yet.", WARN))
            return None
        role = guild.get_role(cfg["verified_role_id"])
        if role is None:
            await reply(interaction, embed=simple("Something's wrong", "The verified role no longer exists. Please tell an admin.", DANGER))
            return None
        if role in member.roles:
            await reply(interaction, embed=simple("Already verified 🎉", f"You already have {role.mention}. Enjoy the server!", COLOR))
            return None
        if cfg["lockdown"]:
            await reply(interaction, embed=simple("🔒 Verification paused", "Verification is temporarily paused. Please try again later or contact a moderator.", WARN))
            return None
        min_days = cfg["min_account_age_days"]
        if min_days and (discord.utils.utcnow() - member.created_at).days < min_days:
            await self.log_event(guild, cfg, "⛔ Account too new", f"{member.mention} tried to verify but their account is under {min_days} day(s) old.", WARN)
            await reply(interaction, embed=simple("Account too new", f"Your Discord account needs to be at least **{min_days} day(s)** old to verify here. Please try again later or contact a moderator.", WARN))
            return None
        wait = self.locked_for(guild.id, member.id)
        if wait > 0:
            until = discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=wait), "R")
            await reply(interaction, embed=simple("⏳ Locked out", f"Too many wrong answers. You can try again {until}.", WARN))
            return None
        return cfg, role

    async def begin(self, interaction: discord.Interaction):
        pre = await self.precheck(interaction)
        if not pre:
            return
        cfg, role = pre
        if cfg["rules_text"]:
            embed = discord.Embed(title="📜 Server rules", description=cfg["rules_text"], color=INFO)
            embed.set_footer(text="Step 1: read and agree, then you'll get a quick check")
            return await reply(interaction, embed=embed, view=RulesGate(self))
        await self.run_challenge(interaction, cfg, role)

    async def after_rules(self, interaction: discord.Interaction):
        pre = await self.precheck(interaction)
        if pre:
            await self.run_challenge(interaction, *pre)

    async def run_challenge(self, interaction: discord.Interaction, cfg, role: discord.Role):
        mode = cfg["mode"]
        if mode == "button":
            return await self.complete(interaction, cfg, role, "button")
        if mode == "math":
            question, answer = make_math()
            return await interaction.response.send_modal(AnswerModal(self, "Quick check", question, str(answer), "math"))
        if mode == "emoji":
            return await self.emoji_challenge(interaction, cfg)
        await self.code_challenge(interaction)

    async def emoji_challenge(self, interaction: discord.Interaction, cfg):
        picks = random.sample(list(EMOJIS), 5)
        target = random.choice(picks)
        left = self.attempts_left(interaction.guild_id, interaction.user.id, cfg["max_attempts"])
        embed = discord.Embed(title="🧩 Quick check", description=f"Tap the **{EMOJIS[target]}**", color=INFO)
        embed.set_footer(text=f"Attempts left: {left}")
        await reply(interaction, embed=embed, view=EmojiView(self, picks, target))

    async def code_challenge(self, interaction: discord.Interaction, edit: bool = False):
        cfg = await db.get_config(interaction.guild_id)
        left = self.attempts_left(interaction.guild_id, interaction.user.id, cfg["max_attempts"])
        code = make_code()
        file = discord.File(render_captcha(code), filename="captcha.png")
        expires = discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=CHALLENGE_SECONDS), "R")
        embed = discord.Embed(
            title="🔐 Type the code",
            description=f"Enter the characters from the image (not case-sensitive).\n⏳ Expires {expires}",
            color=INFO,
        )
        embed.set_image(url="attachment://captcha.png")
        embed.set_footer(text=f"Attempts left: {left} · Can't read it? Press New code")
        view = CodeView(self, code)
        if edit:
            await interaction.response.edit_message(embed=embed, attachments=[file], view=view)
        else:
            await reply(interaction, embed=embed, file=file, view=view)

    async def show_rules(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg or not cfg["rules_text"]:
            return await reply(interaction, embed=simple("📜 Rules", "No rules have been set yet.", INFO))
        await reply(interaction, embed=discord.Embed(title="📜 Server rules", description=cfg["rules_text"], color=INFO))

    async def show_help(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg:
            return await reply(interaction, embed=simple("Not set up", "Verification isn't set up here yet.", WARN))
        lines = [
            "**1.** Press the green **Verify** button.",
            f"**2.** {MODE_BLURB.get(cfg['mode'], MODE_BLURB['math'])}",
            "**3.** You'll get your role straight away.",
        ]
        if cfg["min_account_age_days"]:
            lines.append(f"\nAccounts must be at least **{cfg['min_account_age_days']} day(s)** old.")
        lines.append(f"\nWrong answers: you get **{cfg['max_attempts']}** tries, then a short break.")
        lines.append("Still stuck? Message a moderator and they can verify you by hand.")
        await reply(interaction, embed=discord.Embed(title="❓ Need help?", description="\n".join(lines), color=INFO))

    async def handle_answer(self, interaction: discord.Interaction, correct: bool, method: str):
        guild, member = interaction.guild, interaction.user
        cfg = await db.get_config(guild.id)
        role = guild.get_role(cfg["verified_role_id"]) if cfg and cfg["verified_role_id"] else None
        if role is None:
            return await reply(interaction, embed=simple("Something's wrong", "Verification isn't set up properly. Please tell an admin.", DANGER))
        wait = self.locked_for(guild.id, member.id)
        if wait > 0:
            until = discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=wait), "R")
            return await reply(interaction, embed=simple("⏳ Locked out", f"Too many wrong answers. Try again {until}.", WARN))
        if cfg["lockdown"]:
            return await reply(interaction, embed=simple("🔒 Verification paused", "Verification is temporarily paused. Try again later.", WARN))
        if correct:
            return await self.complete(interaction, cfg, role, method)
        remaining = self.register_failure(guild.id, member.id, cfg["max_attempts"])
        if remaining == 0:
            await self.log_event(guild, cfg, "🚫 Verification lockout", f"{member.mention} failed {cfg['max_attempts']} times and is locked out for 10 minutes.", WARN)
            until = discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=LOCKOUT_SECONDS), "R")
            return await reply(interaction, embed=simple("🚫 Too many wrong answers", f"You're locked out. You can try again {until}.", DANGER))
        await reply(interaction, embed=simple("❌ Not quite", f"**{remaining}** attempt(s) left. Press **Verify** on the panel to get a fresh challenge.", DANGER))

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
            return await reply(interaction, embed=simple("Couldn't give you the role", "Something's wrong with my permissions. Please tell an admin.", DANGER))

        self.failures.pop((guild.id, member.id), None)
        await db.execute(
            "INSERT INTO verifications (guild_id, user_id, method, verified_at) VALUES (?, ?, ?, ?)",
            (guild.id, member.id, method, discord.utils.utcnow().isoformat()),
        )
        await reply(interaction, embed=success_embed(guild, role))
        await self.log_event(guild, cfg, "✅ Member verified", f"{member.mention} verified ({method}).")
        self.bot.dispatch("member_verified", member)

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
                        await member.send(embed=simple(
                            f"You were removed from {guild.name}",
                            "You didn't verify in time. You're welcome to rejoin and verify.", WARN,
                        ))
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

    @app_commands.command(description="Set up verification and post the verify panel")
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
        check_role(interaction, role)
        if unverified_role:
            check_role(interaction, unverified_role)
        await interaction.response.defer(ephemeral=True)
        cfg = await self.apply_setup(interaction.guild, channel, role, method.value if method else None, unverified_role)
        await interaction.followup.send(
            f"✅ Verification is live in {channel.mention}.\n"
            f"**Next:** hide your other channels from @everyone and allow {role.mention}. "
            "Make it yours with `/verify panel`, add `/verify rules`, and turn on logs with `/logs setup`.",
            embed=self.status_embed(interaction.guild, cfg), ephemeral=True,
        )

    @app_commands.command(description="Customise how the verify panel looks")
    @app_commands.describe(
        title="Panel title, e.g. '🔒 Verify to enter'",
        text="The message under the title",
        color="Panel colour as hex, e.g. #5865F2",
        image_url="Banner image link (or 'none' to remove it)",
        button_label="Text on the green button (default: Verify)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(
        self,
        interaction: discord.Interaction,
        title: Optional[app_commands.Range[str, 1, 100]] = None,
        text: Optional[app_commands.Range[str, 1, 1000]] = None,
        color: Optional[str] = None,
        image_url: Optional[str] = None,
        button_label: Optional[app_commands.Range[str, 1, 30]] = None,
    ):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Run `/verify setup` first.")
        updates: dict = {}
        if title:
            updates["panel_title"] = title
        if text:
            updates["panel_text"] = text
        if color:
            updates["panel_color"] = f"{parse_color(color):06X}"
        if image_url:
            if image_url.strip().lower() == "none":
                updates["panel_image"] = None
            elif image_url.startswith(("http://", "https://")):
                updates["panel_image"] = image_url
            else:
                raise UserError("The image must be a direct link starting with `https://` (or type `none`).")
        if button_label:
            updates["button_label"] = button_label
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option.")
        await interaction.response.defer(ephemeral=True)
        await db.upsert_config(interaction.guild_id, **updates)
        cfg = await db.get_config(interaction.guild_id)
        message = await self.post_panel(interaction.guild, cfg)
        await interaction.followup.send(f"✅ Panel updated: {message.jump_url}", ephemeral=True)

    @app_commands.command(description="Set the rules members must agree to before verifying")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rules(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Run `/verify setup` first.")
        await interaction.response.send_modal(RulesModal(self, cfg["rules_text"]))

    @app_commands.command(description="Preview how the verify panel looks (only you see it)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def preview(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Run `/verify setup` first.")
        await interaction.response.send_message("Preview (the real panel has working buttons):", embed=build_panel(interaction.guild, cfg), ephemeral=True)

    @app_commands.command(description="Change verification settings (only the options you fill in)")
    @app_commands.describe(
        method="How members prove they're human",
        log_channel="Channel for verification logs",
        unverified_role="Role given on join and removed on verify",
        min_account_age_days="Block accounts younger than this many days (0 = off)",
        kick_after_minutes="Kick new members who don't verify in this long (0 = off)",
        max_attempts="Wrong answers allowed before a 10-minute lockout",
        raid_threshold="Auto-lockdown when this many members join within a minute (0 = off)",
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
            check_can_send(log_channel, guild.me)
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
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option.")

        await interaction.response.defer(ephemeral=True)
        await db.upsert_config(guild.id, **updates)
        cfg = await db.get_config(guild.id)
        if method and cfg["panel_message_id"]:
            await self.post_panel(guild, cfg)  # the 'How it works' steps depend on the method
        await interaction.followup.send("\n".join(["✅ Settings saved."] + notes), embed=self.status_embed(guild, cfg), ephemeral=True)

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
        self.bot.dispatch("member_verified", member)
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
        week_rows = await db.fetch_all(
            "SELECT verified_at FROM verifications WHERE guild_id = ? AND verified_at >= ?", (guild.id, (now - timedelta(days=7)).isoformat())
        )
        days = [(now - timedelta(days=i)).date() for i in range(6, -1, -1)]
        counts = {d: 0 for d in days}
        for row in week_rows:
            d = discord.utils.parse_time(row["verified_at"]).date()
            if d in counts:
                counts[d] += 1
        values = [counts[d] for d in days]
        peak = max(values) or 1
        bars = "▁▂▃▄▅▆▇█"
        spark = "".join(bars[min(7, int(v / peak * 7))] if v else "▁" for v in values)
        day_count = sum(1 for r in week_rows if discord.utils.parse_time(r["verified_at"]) >= now - timedelta(days=1))
        methods = await db.fetch_all("SELECT method, COUNT(*) AS c FROM verifications WHERE guild_id = ? GROUP BY method ORDER BY c DESC", (guild.id,))
        role = guild.get_role(cfg["verified_role_id"])
        humans = [m for m in guild.members if not m.bot]
        unverified = sum(1 for m in humans if role and role not in m.roles)

        embed = discord.Embed(title="📈 Verification stats", color=COLOR)
        embed.add_field(name="All time", value=f"**{total:,}**")
        embed.add_field(name="Last 24 hours", value=f"**{day_count:,}**")
        embed.add_field(name="Last 7 days", value=f"**{len(week_rows):,}**")
        embed.add_field(name="Past week, day by day", value=f"`{spark}`\n{days[0]:%d %b} → {days[-1]:%d %b}", inline=False)
        embed.add_field(name="Members without the role", value=f"{unverified:,} of {len(humans):,}")
        embed.add_field(name="By method", value="\n".join(f"{r['method']}: {r['c']:,}" for r in methods) or "None yet")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Turn verification off and remove the panel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction):
        cfg = await db.get_config(interaction.guild_id)
        if not cfg or not cfg["verified_role_id"]:
            raise UserError("Verification isn't set up.")
        await interaction.response.defer(ephemeral=True)
        await self.delete_panel(interaction.guild, cfg)
        await db.upsert_config(
            interaction.guild_id, verified_role_id=None, unverified_role_id=None, panel_channel_id=None,
            panel_message_id=None, lockdown=0, kick_after_minutes=0, raid_threshold=0,
        )
        await interaction.followup.send("🛑 Verification disabled. Members keep their roles. Your logs and welcome settings are untouched.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
