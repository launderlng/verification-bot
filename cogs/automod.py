import logging
import re
import time
from collections import deque
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import SUCCESS, WARN, UserError
from logutil import emit

log = logging.getLogger("verification-bot")

URL_RE = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>()]+|\b[a-z0-9][a-z0-9-]{1,}\.(?:com|net|org|io|gg|co|xyz|me|tv|dev|app|shop|store|link|ly|ru|cc|to|sh|fm|info|biz|us|uk)\b(?:/[^\s<>()]*)?"
)
INVITE_RE = re.compile(r"(?i)\b(?:discord(?:app)?\.(?:gg|com/invite)|dsc\.gg|discord\.me)/[a-z0-9-]+")
EMOJI_RE = re.compile(r"<a?:\w+:\d+>|[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")
RULES = {
    "links": "Links", "invites": "Discord invites", "spam": "Spam", "repeat": "Repeated messages", "mentions": "Too many mentions",
    "caps": "Too many capitals", "emojis": "Too many emojis", "lines": "Message too long", "words": "Blocked word",
}
STRIKE_WINDOW = 600  # seconds: strikes older than this are forgotten


def split_list(raw: Optional[str]) -> list[str]:
    return [x.strip().lower() for x in re.split(r"[\n,]+", raw or "") if x.strip()]


def host_of(link: str) -> str:
    link = re.sub(r"(?i)^https?://", "", link.strip())
    host = re.split(r"[/:?#]", link, maxsplit=1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def clean_site(entry: str) -> str:
    """'https://www.YouTube.com/' -> 'youtube.com'. Entries with a path (like discord.gg/xzxx) keep it."""
    entry = re.sub(r"(?i)^https?://", "", entry.strip().lower()).strip("/")
    return entry[4:] if entry.startswith("www.") else entry


def domain_allowed(host: str, allowed: list[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in allowed)


def check_content(content: str, s, mention_count: int = 0) -> Optional[tuple[str, str]]:
    """The content rules. Returns (rule, detail) for the first one broken, or None. (Spam and repeats need history, so they're checked separately.)"""
    if not content and not mention_count:
        return None
    allowed = split_list(s["allowed_domains"])
    if s["block_invites"]:
        for match in INVITE_RE.findall(content):
            if match.lower() not in allowed and host_of(match) not in allowed:
                return "invites", f"Discord invite `{match}`"
    if s["links_mode"] in ("block", "allowlist"):
        for match in URL_RE.findall(content):
            host = host_of(match)
            if INVITE_RE.search(match) and s["block_invites"]:
                continue  # already handled above
            if s["links_mode"] == "allowlist" and domain_allowed(host, allowed):
                continue
            if s["links_mode"] == "block" and domain_allowed(host, allowed):
                continue
            return "links", f"Link to `{host}`"
    if s["mention_limit"] and mention_count > s["mention_limit"]:
        return "mentions", f"{mention_count} mentions (limit {s['mention_limit']})"
    letters = [c for c in content if c.isalpha()]
    if s["caps_percent"] and len(letters) >= 10:
        percent = 100 * sum(1 for c in letters if c.isupper()) // len(letters)
        if percent >= s["caps_percent"]:
            return "caps", f"{percent}% capitals (limit {s['caps_percent']}%)"
    if s["emoji_limit"]:
        count = len(EMOJI_RE.findall(content))
        if count > s["emoji_limit"]:
            return "emojis", f"{count} emojis (limit {s['emoji_limit']})"
    if s["max_lines"]:
        lines = content.count("\n") + 1
        if lines > s["max_lines"]:
            return "lines", f"{lines} lines (limit {s['max_lines']})"
    lowered = content.lower()
    for word in split_list(s["blocked_words"]):
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", lowered):
            return "words", f"Blocked word `{word}`"
    return None


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Automod(commands.GroupCog, group_name="automod", group_description="Automatic moderation for text channels"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.times: dict[tuple[int, int], deque] = {}
        self.last: dict[tuple[int, int], tuple[str, int]] = {}
        self.strikes: dict[tuple[int, int], list[float]] = {}
        super().__init__()

    async def settings(self, guild_id: int):
        row = await db.fetch_one("SELECT * FROM automod_settings WHERE guild_id = ?", (guild_id,))
        if row is None:
            await db.execute("INSERT OR IGNORE INTO automod_settings (guild_id) VALUES (?)", (guild_id,))
            row = await db.fetch_one("SELECT * FROM automod_settings WHERE guild_id = ?", (guild_id,))
        return row

    async def change(self, guild_id: int, **fields) -> None:
        await self.settings(guild_id)
        cols = ", ".join(f"{c} = ?" for c in fields)
        await db.execute(f"UPDATE automod_settings SET {cols} WHERE guild_id = ?", (*fields.values(), guild_id))

    # ----------------------------------------------------------- enforcement ----

    def is_exempt(self, message: discord.Message, s) -> bool:
        member = message.author
        perms = getattr(member, "guild_permissions", None)
        if perms is not None and (perms.administrator or perms.manage_messages):
            return True  # staff are never touched
        if str(message.channel.id) in split_list(s["exempt_channels"]):
            return True
        role_ids = {str(r.id) for r in getattr(member, "roles", [])}
        return bool(role_ids & set(split_list(s["exempt_roles"])))

    def check_history(self, message: discord.Message, s, now: float) -> Optional[tuple[str, str]]:
        key = (message.guild.id, message.author.id)
        if s["spam_count"]:
            stamps = self.times.setdefault(key, deque())
            stamps.append(now)
            while stamps and now - stamps[0] > s["spam_seconds"]:
                stamps.popleft()
            if len(stamps) >= s["spam_count"]:
                stamps.clear()
                return "spam", f"{s['spam_count']} messages in {s['spam_seconds']} seconds"
        if s["repeat_limit"] and message.content:
            text, count = self.last.get(key, ("", 0))
            norm = message.content.strip().lower()
            count = count + 1 if norm == text else 1
            self.last[key] = (norm, count)
            if count >= s["repeat_limit"]:
                self.last[key] = ("", 0)
                return "repeat", f"Same message {count} times in a row"
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or getattr(message, "webhook_id", None):
            return
        s = await self.settings(message.guild.id)
        if not s["enabled"] or self.is_exempt(message, s):
            return
        mentions = len(getattr(message, "mentions", [])) + len(getattr(message, "role_mentions", []))
        found = check_content(message.content or "", s, mentions) or self.check_history(message, s, time.monotonic())
        if found:
            await self.punish(message, s, *found)

    async def punish(self, message: discord.Message, s, rule: str, detail: str) -> None:
        guild, member = message.guild, message.author
        deleted = True
        try:
            await message.delete()
        except discord.HTTPException:
            deleted = False
        key = (guild.id, member.id)
        now = time.monotonic()
        strikes = [t for t in self.strikes.get(key, []) if now - t < STRIKE_WINDOW] + [now]
        self.strikes[key] = strikes
        action_taken = "Message removed" if deleted else "Couldn't remove the message (check my permissions)"
        if s["action"] in ("warn", "timeout"):
            await db.execute(
                "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (guild.id, member.id, self.bot.user.id if getattr(self.bot, "user", None) else 0, f"Automod: {RULES[rule]} ({detail})", discord.utils.utcnow().isoformat()),
            )
            action_taken += " + warning added"
        if s["action"] == "timeout" and len(strikes) >= s["strikes_before_timeout"]:
            try:
                await member.timeout(timedelta(minutes=s["timeout_minutes"]), reason=f"Automod: {RULES[rule]}")
                action_taken += f" + timed out for {s['timeout_minutes']} min after {len(strikes)} strikes"
                self.strikes[key] = []
            except discord.HTTPException:
                action_taken += " (couldn't time them out: check my permissions and role order)"
        try:
            await message.channel.send(f"{member.mention} your message was removed: **{RULES[rule]}**.", delete_after=8, allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException:
            pass
        if s["dm_user"]:
            try:
                await member.send(embed=ui.card(
                    "⚠️ Your message was removed",
                    ui.kv(("🏠 Server", guild.name), ("📏 Rule", RULES[rule]), ("📍 Channel", f"#{message.channel.name}"), ("⚖️ Action", action_taken))
                    + "\n\nPlease read the server rules. Repeated breaks can lead to a timeout or ban.",
                    color=WARN, guild=guild, section="Automod"))
            except discord.HTTPException:
                pass  # their DMs are closed
        snippet = (message.content or "")[:900]
        await emit(
            guild, "automod", f"Automod: {RULES[rule]}",
            ui.kv(("👤 User", member.mention), ("📏 Rule", RULES[rule]), ("🔎 Detail", detail), ("📍 Channel", message.channel.mention), ("⚖️ Action", action_taken),
                  ("🆔 IDs", f"user `{member.id}` · message `{message.id}`"), ("⚡ Strikes", f"{len(strikes)} in the last 10 minutes")),
            fields=(("Message", snippet),) if snippet else (), author=(member.display_name, member.display_avatar.url), subject=member.id,
        )

    # -------------------------------------------------------------- commands ----

    @app_commands.command(description="See every automod setting")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        s = await self.settings(interaction.guild_id)
        on = lambda v: "✅ On" if v else "❌ Off"
        limit = lambda v, unit="": f"{v}{unit}" if v else "❌ Off"
        links = {"off": "❌ Off", "block": "🚫 All links blocked", "allowlist": "🚫 Blocked except allowed sites"}[s["links_mode"]]
        body = ui.kv(
            ("🤖 Automod", on(s["enabled"])), ("🔗 Links", links), ("✉️ Discord invites", on(s["block_invites"])),
            ("🌐 Allowed sites", ", ".join(f"`{d}`" for d in split_list(s["allowed_domains"])) or None),
            ("💥 Spam", f"{s['spam_count']} messages in {s['spam_seconds']}s" if s["spam_count"] else "❌ Off"), ("🔁 Repeats", limit(s["repeat_limit"], " in a row")),
            ("📣 Mentions", limit(s["mention_limit"], " per message")), ("🔠 Capitals", limit(s["caps_percent"], "%")), ("😀 Emojis", limit(s["emoji_limit"], " per message")),
            ("📜 Lines", limit(s["max_lines"], " per message")), ("🚷 Blocked words", f"{len(split_list(s['blocked_words']))} words"),
            ("🛡️ Skips", f"staff, {len(split_list(s['exempt_roles']))} roles, {len(split_list(s['exempt_channels']))} channels"),
            ("📩 DM the member", on(s["dm_user"])),
            ("⚖️ Punishment", {"delete": "Remove the message", "warn": "Remove + warn", "timeout": f"Remove + warn + timeout {s['timeout_minutes']} min after {s['strikes_before_timeout']} strikes"}[s["action"]]),
        )
        if not self.bot.intents.message_content:
            body += "\n\n⚠️ **Message Content Intent is off**, so link, word and capitals checks can't read messages. Turn it on in the Developer Portal and set `MESSAGE_CONTENT=true`."
        await interaction.response.send_message(embed=ui.card("🤖 Automod", body, guild=interaction.guild, section="Automod"), ephemeral=True)

    @app_commands.command(description="Switch automod on or off")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def toggle(self, interaction: discord.Interaction, enabled: bool):
        await self.change(interaction.guild_id, enabled=int(enabled))
        await interaction.response.send_message(embed=ui.card(f"🤖 Automod is {'on' if enabled else 'off'}", "Staff (anyone who can manage messages) are never touched." if enabled else "Nothing will be removed automatically.", color=SUCCESS if enabled else WARN), ephemeral=True)
        await emit(interaction.guild, "staff", f"Automod {'enabled' if enabled else 'disabled'}", ui.kv(("🛡️ By", interaction.user.mention)), subject=interaction.user.id)

    @app_commands.command(description="Set a number-based rule (0 turns it off)")
    @app_commands.describe(rule="Which rule", value="The limit. 0 turns the rule off")
    @app_commands.choices(rule=[
        app_commands.Choice(name="Spam: messages allowed in the time window", value="spam_count"),
        app_commands.Choice(name="Spam: the time window, in seconds", value="spam_seconds"),
        app_commands.Choice(name="Repeats: same message this many times in a row", value="repeat_limit"),
        app_commands.Choice(name="Mentions: most per message", value="mention_limit"),
        app_commands.Choice(name="Capitals: percent that triggers it", value="caps_percent"),
        app_commands.Choice(name="Emojis: most per message", value="emoji_limit"),
        app_commands.Choice(name="Long messages: most lines", value="max_lines"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rule(self, interaction: discord.Interaction, rule: app_commands.Choice[str], value: app_commands.Range[int, 0, 1000]):
        if rule.value == "caps_percent" and value > 100:
            raise UserError("Capitals is a percent, from 1 to 100.")
        if rule.value == "spam_seconds" and not 1 <= value <= 120:
            raise UserError("The spam window is 1 to 120 seconds.")
        if rule.value in ("spam_count", "repeat_limit") and value == 1:
            raise UserError("Use 2 or more, or 0 to turn it off.")
        await self.change(interaction.guild_id, **{rule.value: value})
        await interaction.response.send_message(embed=ui.card("✅ Saved", f"**{rule.name.split(':')[0]}** is now **{value if value else 'off'}**." + ("\n\nSwitch automod on with `/automod toggle`." if not (await self.settings(interaction.guild_id))["enabled"] else ""), color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Block links and Discord invites (with sites you allow)")
    @app_commands.describe(mode="Off, block every link, or block all except the allowed sites", block_invites="Remove Discord server invites", allowed_sites="Sites that are fine, comma separated (e.g. youtube.com, discord.gg/xzxx)")
    @app_commands.choices(mode=[app_commands.Choice(name="Off", value="off"), app_commands.Choice(name="Block every link", value="block"), app_commands.Choice(name="Block all except allowed sites", value="allowlist")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def links(self, interaction: discord.Interaction, mode: Optional[app_commands.Choice[str]] = None, block_invites: Optional[bool] = None, allowed_sites: Optional[app_commands.Range[str, 0, 500]] = None):
        changes: dict = {}
        if mode:
            changes["links_mode"] = mode.value
        if block_invites is not None:
            changes["block_invites"] = int(block_invites)
        if allowed_sites is not None:
            changes["allowed_domains"] = ",".join(clean_site(x) for x in split_list(allowed_sites)) or None
        if not changes:
            raise UserError("Nothing to change. Set a mode, block_invites or allowed_sites.")
        await self.change(interaction.guild_id, **changes)
        await interaction.response.send_message(embed=ui.card("✅ Link rules saved", "Run `/automod status` to see everything.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Manage the blocked word list")
    @app_commands.describe(action="Add, remove or list", word="The word or phrase")
    @app_commands.choices(action=[app_commands.Choice(name="Add", value="add"), app_commands.Choice(name="Remove", value="remove"), app_commands.Choice(name="List", value="list")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def words(self, interaction: discord.Interaction, action: app_commands.Choice[str], word: Optional[app_commands.Range[str, 1, 60]] = None):
        s = await self.settings(interaction.guild_id)
        current = split_list(s["blocked_words"])
        if action.value == "list":
            return await interaction.response.send_message(embed=ui.card("🚷 Blocked words", ("\n".join(f"• {w}" for w in current) or "None yet.") + "\n\n*Hidden from everyone but you.*"), ephemeral=True)
        if not word:
            raise UserError("Type the word to add or remove.")
        word = word.strip().lower()
        if action.value == "add":
            if word in current:
                raise UserError("That word is already on the list.")
            current.append(word)
        else:
            if word not in current:
                raise UserError("That word isn't on the list.")
            current.remove(word)
        await self.change(interaction.guild_id, blocked_words=",".join(current) or None)
        await interaction.response.send_message(embed=ui.card("✅ Saved", f"The list has **{len(current)}** words.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Choose who or where automod ignores (staff are always ignored)")
    @app_commands.describe(role="A role to ignore", channel="A channel to ignore", remove="Stop ignoring it instead")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def exempt(self, interaction: discord.Interaction, role: Optional[discord.Role] = None, channel: Optional[discord.TextChannel] = None, remove: bool = False):
        if role is None and channel is None:
            raise UserError("Pick a role or a channel.")
        s = await self.settings(interaction.guild_id)
        changes = {}
        for target, column in ((role, "exempt_roles"), (channel, "exempt_channels")):
            if target is None:
                continue
            current, tid = split_list(s[column]), str(target.id)
            if remove and tid in current:
                current.remove(tid)
            elif not remove and tid not in current:
                current.append(tid)
            changes[column] = ",".join(current) or None
        await self.change(interaction.guild_id, **changes)
        await interaction.response.send_message(embed=ui.card("✅ Saved", "Automod will " + ("no longer ignore" if remove else "ignore") + f" {', '.join(t.mention for t in (role, channel) if t)}.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="DM members when automod removes their message")
    @app_commands.describe(enabled="True to send a private DM explaining which rule they broke")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dm(self, interaction: discord.Interaction, enabled: bool):
        await self.change(interaction.guild_id, dm_user=int(enabled))
        await interaction.response.send_message(embed=ui.card(f"📩 DM notices {'on' if enabled else 'off'}", "Members get a private DM naming the rule they broke." if enabled else "Members only see the short in-channel notice.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Choose what happens when someone breaks a rule")
    @app_commands.describe(action="What automod does", strikes="Strikes in 10 minutes before a timeout", minutes="How long the timeout lasts")
    @app_commands.choices(action=[app_commands.Choice(name="Just remove the message", value="delete"), app_commands.Choice(name="Remove it and add a warning", value="warn"), app_commands.Choice(name="Remove, warn, and time out repeat offenders", value="timeout")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def punishment(self, interaction: discord.Interaction, action: app_commands.Choice[str], strikes: Optional[app_commands.Range[int, 2, 20]] = None, minutes: Optional[app_commands.Range[int, 1, 10080]] = None):
        changes: dict = {"action": action.value}
        if strikes:
            changes["strikes_before_timeout"] = strikes
        if minutes:
            changes["timeout_minutes"] = minutes
        await self.change(interaction.guild_id, **changes)
        await interaction.response.send_message(embed=ui.card("✅ Saved", f"**{action.name}**.", color=SUCCESS), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Automod(bot))
