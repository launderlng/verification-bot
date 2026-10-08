import logging
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import ui
from cogs.files import file_autocomplete, send_file_dm
from cogs.tickets import support_button
from common import ACCENT, COLOR, DANGER, SUCCESS, WARN, UserError, check_can_send, parse_duration
from giveawayutil import pick_winners
from logutil import emit

log = logging.getLogger("verification-bot")

MIN_SECONDS = 10
MAX_SECONDS = 60 * 24 * 3600


def parse_iso(value: str):
    return discord.utils.parse_time(value)


def card_time(dt) -> str:
    return f"{discord.utils.format_dt(dt, 'R')} · {discord.utils.format_dt(dt, 'f')}"


# ---------------------------------------------------------------- cards ----

def requirement_lines(g) -> tuple:
    """The 'what you need to enter' lines shown on the giveaway card."""
    return (
        ("🔒 Requires", f"<@&{g['required_role_id']}>" if g["required_role_id"] else None),
        ("🚫 Not allowed", f"<@&{g['blocked_role_id']}> members" if g["blocked_role_id"] else None),
        ("📨 Invites", f"**{g['min_invites']:,}+** valid invites" if g["min_invites"] else None),
        ("💬 Messages", f"**{g['min_messages']:,}+** sent in the server" if g["min_messages"] else None),
        ("📍 Chat in", f"**{g['req_channel_messages']:,}+** messages in <#{g['req_channel_id']}>" if g["req_channel_id"] and g["req_channel_messages"] else None),
        ("📅 Account age", f"**{g['min_account_days']:,}+** days old" if g["min_account_days"] else None),
        ("🏠 In the server", f"**{g['min_server_days']:,}+** days" if g["min_server_days"] else None),
        ("💎 Bonus", f"<@&{g['bonus_role_id']}> gets **+{g['bonus_entries']}** extra entries" if g["bonus_role_id"] and g["bonus_entries"] else None),
    )


def prize_perks(g) -> str:
    perks = []
    if g["prize_role_id"]:
        perks.append(f"🎭 Unlocks <@&{g['prize_role_id']}>")
    if g["prize_file_id"]:
        perks.append("📥 Instant delivery by DM")
    return "  ·  ".join(perks)


def active_embed(guild: discord.Guild, g, entries: int) -> discord.Embed:
    ends = parse_iso(g["ends_at"])
    info = ui.kv(
        ("⏰ Ends", card_time(ends)), ("🏆 Winners", f"{g['winners']}"), ("👥 Entries", f"{entries:,}"),
        ("🎭 Hosted by", f"<@{g['host_id']}>"), *requirement_lines(g),
    )
    perks = prize_perks(g)
    body = (f"{g['description']}\n\n" if g["description"] else "") + (f"{perks}\n\n" if perks else "") + f"{ui.DIVIDER}\n{info}"
    return ui.card(
        g["prize"], body, color=ACCENT, guild=guild, footer=f"Giveaway #{g['id']} · Press 🎉 below to enter",
        author=("🎁  GIVEAWAY", guild.icon.url if guild.icon else None), thumbnail=guild.icon.url if guild.icon else None, image=g["image_url"] or None,
    )


def ended_embed(guild: discord.Guild, g, winners: list, entries: int) -> discord.Embed:
    ended = parse_iso(g["ended_at"]) if g["ended_at"] else parse_iso(g["ends_at"])
    got = "\n".join(f"🏆 <@{w}>" for w in winners) if winners else "No valid entries, so there are no winners."
    info = ui.kv(("🎭 Hosted by", f"<@{g['host_id']}>"), ("⏰ Ended", card_time(ended)), ("👥 Entries", f"{entries:,}"))
    return ui.card(
        g["prize"], f"**{'Winner' if len(winners) == 1 else 'Winners'}**\n{got}\n\n{ui.DIVIDER}\n{info}",
        color=SUCCESS if winners else WARN, guild=guild, footer=f"Giveaway #{g['id']} · Ended",
        author=("🏁  GIVEAWAY ENDED", guild.icon.url if guild.icon else None), thumbnail=guild.icon.url if guild.icon else None, image=g["image_url"] or None,
    )


def cancelled_embed(guild: discord.Guild, g) -> discord.Embed:
    return ui.card(
        g["prize"], f"This giveaway was cancelled by staff.\n\n{ui.DIVIDER}\n" + ui.kv(("🎭 Hosted by", f"<@{g['host_id']}>")),
        color=DANGER, guild=guild, footer=f"Giveaway #{g['id']} · Cancelled", author=("🚫  GIVEAWAY CANCELLED", guild.icon.url if guild.icon else None),
    )


class EnterButton(discord.ui.DynamicItem[discord.ui.Button], template=r"giveaway:enter:(?P<id>[0-9]+)"):
    """Persistent one-click entry button. Shows how many people have entered."""

    def __init__(self, giveaway_id: int, entries: int = 0, ended: bool = False):
        label = "Ended" if ended else ("Enter" if not entries else f"Enter ({entries:,})")
        super().__init__(discord.ui.Button(
            label=label, emoji="🎉", style=discord.ButtonStyle.secondary if ended else discord.ButtonStyle.success,
            custom_id=f"giveaway:enter:{giveaway_id}", disabled=ended,
        ))
        self.giveaway_id = giveaway_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Giveaways")
        if cog is None:
            return await interaction.response.send_message("Giveaways aren't available right now.", ephemeral=True)
        await cog.handle_enter(interaction, self.giveaway_id)


def build_view(g, entries: int, ended: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(EnterButton(g["id"], entries, ended))
    return view


class LeaveView(discord.ui.View):
    def __init__(self, cog: "Giveaways", giveaway_id: int):
        super().__init__(timeout=60)
        self.cog, self.giveaway_id = cog, giveaway_id

    @discord.ui.button(label="Leave giveaway", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = await db.fetch_one("SELECT * FROM giveaways WHERE id = ?", (self.giveaway_id,))
        if not g or g["status"] != "active":
            return await interaction.response.edit_message(embed=ui.card("Too late", "That giveaway has already ended.", color=WARN), view=None)
        await db.execute("DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (self.giveaway_id, interaction.user.id))
        self.stop()
        await interaction.response.edit_message(embed=ui.card("🚪 You left the giveaway", "You're no longer entered. You can press 🎉 again to rejoin.", color=COLOR), view=None)
        await self.cog.refresh_message(interaction.guild, g)


# ------------------------------------------------------------------ cog ----

async def giveaway_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if interaction.guild_id is None:
        return []
    rows = await db.fetch_all("SELECT id, prize, status FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT 40", (interaction.guild_id,))
    choices = [app_commands.Choice(name=f"#{r['id']} · {r['prize'][:60]} ({r['status']})", value=r["id"]) for r in rows if current.lower() in f"#{r['id']} {r['prize']}".lower()]
    return choices[:25]


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Giveaways(commands.GroupCog, group_name="giveaway", group_description="Run giveaways with one-click entry"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def cog_load(self):
        self.bot.add_dynamic_items(EnterButton)
        self.sweeper.start()

    async def cog_unload(self):
        self.sweeper.cancel()
        try:
            self.bot.remove_dynamic_items(EnterButton)
        except Exception:
            pass

    # ---------------------------------------------------------- helpers ----

    async def entry_count(self, giveaway_id: int) -> int:
        return (await db.fetch_one("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)))["c"]

    async def get_giveaway(self, interaction: discord.Interaction, giveaway_id: int):
        g = await db.fetch_one("SELECT * FROM giveaways WHERE id = ? AND guild_id = ?", (giveaway_id, interaction.guild_id))
        if not g:
            raise UserError("I can't find a giveaway with that ID. Start typing in the box and pick one from the list.")
        return g

    async def message_for(self, guild: discord.Guild, g):
        channel = guild.get_channel(g["channel_id"])
        if channel is None or not g["message_id"]:
            return None
        try:
            return await channel.fetch_message(g["message_id"])
        except discord.HTTPException:
            return None

    async def refresh_message(self, guild: discord.Guild, g) -> None:
        """Update the entry count on the giveaway card."""
        message = await self.message_for(guild, g)
        if message is None:
            return
        count = await self.entry_count(g["id"])
        try:
            await message.edit(embed=active_embed(guild, g, count), view=build_view(g, count))
        except discord.HTTPException:
            pass

    async def eligible(self, guild: discord.Guild, g, exclude=()) -> list:
        """Entrants who are still here, aren't bots and still meet the requirement, with their ticket weight."""
        rows = await db.fetch_all("SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (g["id"],))
        result = []
        for r in rows:
            member = guild.get_member(r["user_id"])
            if member is None or member.bot or r["user_id"] in exclude:
                continue
            role_ids = {role.id for role in member.roles}
            if not all(ok for ok, _ in await self.requirement_report(guild, member, g)):
                continue
            weight = 1 + (g["bonus_entries"] if g["bonus_role_id"] and g["bonus_role_id"] in role_ids else 0)
            result.append((member.id, weight))
        return result

    async def messages_sent(self, guild_id: int, user_id: int, channel_id: Optional[int] = None) -> int:
        tracker = self.bot.get_cog("Activity")
        if tracker is not None:
            return await tracker.total(guild_id, user_id, channel_id)
        return await db.message_total(guild_id, user_id, channel_id)

    async def requirement_report(self, guild: discord.Guild, member: discord.Member, g) -> list:
        """Every requirement as (met, text) so people can see exactly where they stand."""
        report = []
        role_ids = {role.id for role in member.roles}
        if g["required_role_id"]:
            report.append((g["required_role_id"] in role_ids, f"Have the <@&{g['required_role_id']}> role"))
        if g["blocked_role_id"]:
            report.append((g["blocked_role_id"] not in role_ids, f"Not have the <@&{g['blocked_role_id']}> role"))
        if g["min_invites"]:
            have = (await db.invite_stats(guild.id, member.id))["valid"]
            report.append((have >= g["min_invites"], f"Invite **{g['min_invites']:,}** people (you have **{have:,}**)"))
        if g["min_messages"]:
            have = await self.messages_sent(guild.id, member.id)
            report.append((have >= g["min_messages"], f"Send **{g['min_messages']:,}** messages (you've sent **{have:,}**)"))
        if g["req_channel_id"] and g["req_channel_messages"]:
            have = await self.messages_sent(guild.id, member.id, g["req_channel_id"])
            report.append((have >= g["req_channel_messages"], f"Send **{g['req_channel_messages']:,}** messages in <#{g['req_channel_id']}> (you've sent **{have:,}**)"))
        if g["min_account_days"]:
            have = (discord.utils.utcnow() - member.created_at).days
            report.append((have >= g["min_account_days"], f"Have an account at least **{g['min_account_days']:,}** days old (yours is **{have:,}**)"))
        if g["min_server_days"]:
            have = (discord.utils.utcnow() - member.joined_at).days if member.joined_at else 0
            report.append((have >= g["min_server_days"], f"Be in the server for **{g['min_server_days']:,}** days (you've been here **{have:,}**)"))
        return report

    def claim_text(self, g, tickets_cfg) -> str:
        if tickets_cfg and tickets_cfg["panel_channel_id"]:
            return f"🎫 **Next step:** open a ticket in <#{tickets_cfg['panel_channel_id']}> and mention giveaway **#{g['id']}** to claim your prize."
        return f"🎫 **Next step:** message <@{g['host_id']}> to claim your prize."

    async def announce(self, guild: discord.Guild, g, winners: list, message) -> None:
        channel = guild.get_channel(g["channel_id"])
        if channel is None:
            return
        view = None
        if message is not None:
            view = discord.ui.View()
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Jump to giveaway", emoji="🎉", url=message.jump_url))
        if winners:
            mentions = " ".join(f"<@{w}>" for w in winners)
            card = ui.card("🏆 We have a winner!" if len(winners) == 1 else "🏆 We have winners!", f"Congratulations {mentions}!\nYou won **{g['prize']}** 🎉", color=SUCCESS, guild=guild, footer=f"Giveaway #{g['id']}")
            kwargs = {"content": mentions, "embed": card, "allowed_mentions": discord.AllowedMentions(users=True)}
        else:
            card = ui.card("😔 No winner this time", f"Nobody entered **{g['prize']}**, so there's no winner.", color=WARN, guild=guild, footer=f"Giveaway #{g['id']}")
            kwargs = {"embed": card}
        if view is not None:
            kwargs["view"] = view
        try:
            await channel.send(**kwargs)
        except discord.HTTPException:
            log.warning("Couldn't announce giveaway %s", g["id"])

    async def grant_prize_role(self, guild: discord.Guild, member: discord.Member, g) -> Optional[str]:
        """Give the winner the role this giveaway unlocks. Returns 'granted', 'failed', or None if there's no role."""
        if not g["prize_role_id"]:
            return None
        role = guild.get_role(g["prize_role_id"])
        if role is None:
            return "failed"
        try:
            await member.add_roles(role, reason=f"Giveaway #{g['id']} prize")
            return "granted"
        except discord.HTTPException:
            log.warning("Couldn't give giveaway role %s to %s (is my role above it?)", g["prize_role_id"], member.id)
            return "failed"

    async def dm_winners(self, guild: discord.Guild, g, winners: list, message) -> None:
        tickets_cfg = await db.get_ticket_config(guild.id)
        prize_file = await db.fetch_one("SELECT * FROM stored_files WHERE id = ?", (g["prize_file_id"],)) if g["prize_file_id"] else None
        for uid in winners:
            member = guild.get_member(uid)
            if member is None:
                continue
            role_status = await self.grant_prize_role(guild, member, g)
            lines = [("🎁 Prize", g["prize"]), ("🏠 Server", guild.name), ("🏷️ Giveaway", f"#{g['id']}")]
            if role_status == "granted":
                lines.append(("🎭 Role", f"✅ You got <@&{g['prize_role_id']}>"))
            elif role_status == "failed":
                lines.append(("🎭 Role", "❌ Couldn't be given automatically, ask staff"))
            body = "Congratulations, you won! 🎉\n\n" + ui.kv(*lines) + f"\n\n{ui.DIVIDER}\n{self.claim_text(g, tickets_cfg)}"
            card = ui.card("You won! 🎉", body, color=SUCCESS, thumbnail=guild.icon.url if guild.icon else None, footer=f"{guild.name} · Giveaway #{g['id']}")
            view = discord.ui.View()
            if message is not None:
                view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="See giveaway", emoji="🎉", url=message.jump_url))
            link = support_button(guild, tickets_cfg)
            if link:
                view.add_item(link)
            try:
                await member.send(embed=card, **({"view": view} if view.children else {}))
            except discord.HTTPException:
                pass  # DMs closed: the announcement in the channel still pings them
            if prize_file is not None:
                await send_file_dm(member, guild, prize_file, title="🎁 Your prize", note=f"From **{g['prize']}** · Giveaway #{g['id']}")

    async def end_giveaway(self, guild: discord.Guild, g, by: Optional[discord.abc.User] = None):
        """Draw winners, update the card, announce and DM. Returns the winners, or None if it had already ended."""
        changed = await db.execute(
            "UPDATE giveaways SET status = 'ended', ended_at = ? WHERE id = ? AND status = 'active'", (discord.utils.utcnow().isoformat(), g["id"])
        )
        if not changed:
            return None
        winners = pick_winners(await self.eligible(guild, g), g["winners"])
        await db.execute("UPDATE giveaways SET winner_ids = ? WHERE id = ?", (",".join(str(w) for w in winners) or None, g["id"]))
        g = await db.fetch_one("SELECT * FROM giveaways WHERE id = ?", (g["id"],))
        count = await self.entry_count(g["id"])
        message = await self.message_for(guild, g)
        if message is not None:
            try:
                await message.edit(embed=ended_embed(guild, g, winners, count), view=build_view(g, count, ended=True))
            except discord.HTTPException:
                pass
        await self.announce(guild, g, winners, message)
        await self.dm_winners(guild, g, winners, message)
        await emit(
            guild, "giveaways", "Giveaway ended",
            ui.kv(("Prize", g["prize"]), ("Winners", ", ".join(f"<@{w}>" for w in winners) or "None"), ("Entries", f"{count:,}"), ("Ended by", by.mention if by else "Timer")),
            footer=f"#{g['id']}",
        )
        return winners

    # ---------------------------------------------------------- entering ----

    async def handle_enter(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        guild, member = interaction.guild, interaction.user
        g = await db.fetch_one("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))
        if not g or g["guild_id"] != interaction.guild_id:
            return await interaction.response.send_message(embed=ui.card("Not available", "That giveaway doesn't exist any more.", color=WARN), ephemeral=True)
        if g["status"] != "active" or parse_iso(g["ends_at"]) <= discord.utils.utcnow():
            return await interaction.response.send_message(embed=ui.card("⏰ Too late", "This giveaway has ended.", color=WARN), ephemeral=True)
        report = await self.requirement_report(guild, member, g)
        if not all(ok for ok, _ in report):
            checklist = "\n".join(f"{'✅' if ok else '❌'} {text}" for ok, text in report)
            return await interaction.response.send_message(
                embed=ui.card("🔒 You can't enter yet", f"To enter **{g['prize']}** you need to:\n\n{checklist}\n\nCome back and press 🎉 once you've met them.", color=WARN), ephemeral=True
            )
        existing = await db.fetch_one("SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (giveaway_id, member.id))
        if existing:
            return await interaction.response.send_message(
                embed=ui.card("✅ You're already in", "You're entered in this giveaway. Good luck! 🍀", color=SUCCESS), view=LeaveView(self, giveaway_id), ephemeral=True
            )
        await db.execute("INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id, joined_at) VALUES (?, ?, ?)", (giveaway_id, member.id, discord.utils.utcnow().isoformat()))
        count = await self.entry_count(giveaway_id)
        await interaction.response.edit_message(embed=active_embed(guild, g, count), view=build_view(g, count))
        bonus = ""
        if g["bonus_role_id"] and g["bonus_entries"] and g["bonus_role_id"] in {r.id for r in member.roles}:
            bonus = f"\nYou also get **+{g['bonus_entries']}** bonus entries from <@&{g['bonus_role_id']}>. 💎"
        await interaction.followup.send(embed=ui.card("🎉 You're in!", f"You entered **{g['prize']}**. Good luck! 🍀{bonus}", color=SUCCESS), ephemeral=True)

    # ------------------------------------------------------------ timer ----

    @tasks.loop(seconds=15)
    async def sweeper(self):
        now = discord.utils.utcnow().isoformat()
        rows = await db.fetch_all("SELECT * FROM giveaways WHERE status = 'active' AND ends_at <= ?", (now,))
        for g in rows:
            guild = self.bot.get_guild(g["guild_id"])
            if guild is None:
                continue
            try:
                await self.end_giveaway(guild, g)
            except Exception:
                log.exception("Couldn't end giveaway %s", g["id"])

    @sweeper.before_loop
    async def before_sweeper(self):
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------- commands ----

    @app_commands.command(description="Start a giveaway")
    @app_commands.describe(
        prize="What you're giving away",
        duration="How long it runs, e.g. 30m, 2h, 1d12h",
        winners="How many winners (default 1)",
        channel="Where to post it (default: this channel)",
        description="Extra details shown on the card",
        required_role="Only members with this role can enter",
        bonus_role="Members with this role get extra entries",
        bonus_entries="How many extra entries the bonus role gets (default 1)",
        image_url="A banner image for the card (direct https link)",
        min_invites="Members need this many valid invites (see /invites)",
        min_messages="Members need to have sent this many messages in the server",
        message_channel="Count messages in this one channel (use with channel_messages)",
        channel_messages="How many messages they need in message_channel",
        blocked_role="Members with this role can't enter",
        min_account_days="Their Discord account must be at least this many days old",
        min_server_days="They must have been in the server this many days",
        ping="Role to ping when it starts (default: the giveaway ping role)",
        prize_role="A role to automatically give the winner(s)",
        prize_file="A file from your library to automatically DM the winner(s)",
    )
    @app_commands.autocomplete(prize_file=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def start(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 200],
        duration: str,
        winners: app_commands.Range[int, 1, 20] = 1,
        channel: Optional[discord.TextChannel] = None,
        description: Optional[app_commands.Range[str, 1, 500]] = None,
        required_role: Optional[discord.Role] = None,
        bonus_role: Optional[discord.Role] = None,
        bonus_entries: app_commands.Range[int, 1, 10] = 1,
        image_url: Optional[str] = None,
        min_invites: app_commands.Range[int, 0, 1000] = 0,
        min_messages: app_commands.Range[int, 0, 1000000] = 0,
        message_channel: Optional[discord.TextChannel] = None,
        channel_messages: app_commands.Range[int, 0, 100000] = 0,
        blocked_role: Optional[discord.Role] = None,
        min_account_days: app_commands.Range[int, 0, 3650] = 0,
        min_server_days: app_commands.Range[int, 0, 3650] = 0,
        ping: Optional[discord.Role] = None,
        prize_role: Optional[discord.Role] = None,
        prize_file: Optional[str] = None,
    ):
        guild = interaction.guild
        prize_file_row = None
        if prize_file:
            prize_file_row = await db.fetch_one("SELECT id FROM stored_files WHERE guild_id = ? AND name = ?", (guild.id, prize_file.strip()))
            if prize_file_row is None:
                raise UserError(f"I can't find a file called **{prize_file}** in your library. Try `/files list`.")
        if bool(message_channel) != bool(channel_messages):
            raise UserError("To require messages in one channel, set **both** `message_channel` and `channel_messages`.")
        seconds = parse_duration(duration)
        if seconds is None or seconds < MIN_SECONDS:
            raise UserError("Use a duration like `30m`, `2h` or `1d12h` (at least 10 seconds).")
        if seconds > MAX_SECONDS:
            raise UserError("Giveaways can run for up to 60 days.")
        if image_url and not image_url.startswith("https://"):
            raise UserError("The image must be a direct link starting with `https://`.")
        settings = await db.fetch_one("SELECT * FROM giveaway_settings WHERE guild_id = ?", (guild.id,))
        default_channel = guild.get_channel(settings["default_channel_id"]) if settings and settings["default_channel_id"] else None
        target = channel or default_channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            raise UserError("Pick a text channel to post the giveaway in.")
        check_can_send(target, guild.me)
        ping_role = ping or (guild.get_role(settings["ping_role_id"]) if settings and settings["ping_role_id"] else None)

        await interaction.response.defer(ephemeral=True)
        now = discord.utils.utcnow()
        ends = now + timedelta(seconds=seconds)
        await db.execute(
            "INSERT INTO giveaways (guild_id, channel_id, host_id, prize, description, image_url, winners, required_role_id, bonus_role_id, bonus_entries, ends_at, created_at, "
            "min_invites, min_messages, req_channel_id, req_channel_messages, blocked_role_id, min_account_days, min_server_days, ping_role_id, prize_role_id, prize_file_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild.id, target.id, interaction.user.id, prize.strip(), description, image_url, winners, required_role.id if required_role else None,
             bonus_role.id if bonus_role else None, bonus_entries if bonus_role else 0, ends.isoformat(), now.isoformat(),
             min_invites, min_messages, message_channel.id if message_channel else None, channel_messages, blocked_role.id if blocked_role else None,
             min_account_days, min_server_days, ping_role.id if ping_role else None,
             prize_role.id if prize_role else None, prize_file_row["id"] if prize_file_row else None),
        )
        g = await db.fetch_one("SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT 1", (guild.id,))
        try:
            can_ping = ping_role is not None and (ping_role.mentionable or guild.me.guild_permissions.mention_everyone)
            message = await target.send(
                content=ping_role.mention if can_ping else None, embed=active_embed(guild, g, 0), view=build_view(g, 0),
                allowed_mentions=discord.AllowedMentions(roles=[ping_role] if can_ping else False, users=False, everyone=False),
            )
        except discord.HTTPException:
            await db.execute("DELETE FROM giveaways WHERE id = ?", (g["id"],))
            raise UserError("I couldn't post in that channel. Check my permissions there.") from None
        await db.execute("UPDATE giveaways SET message_id = ? WHERE id = ?", (message.id, g["id"]))
        await emit(guild, "giveaways", "Giveaway started", ui.kv(("Prize", g["prize"]), ("Host", interaction.user.mention), ("Channel", target.mention), ("Ends", discord.utils.format_dt(ends, "R"))), footer=f"#{g['id']}")
        delivery = []
        if prize_role:
            delivery.append(f"🎭 Winners automatically get {prize_role.mention}")
        if prize_file_row:
            delivery.append(f"📥 Winners are automatically DMed **{prize_file.strip()}**")
        await interaction.followup.send(
            embed=ui.card("✅ Giveaway started", f"**{g['prize']}** is live in {target.mention}.\n\n[Jump to it]({message.jump_url})"
                          + (f"\n\n🔔 Pinged {ping_role.mention}." if can_ping else (f"\n\n⚠️ I couldn't ping {ping_role.mention}. Make that role **mentionable** (Server Settings → Roles), or run `/pingroles setup`." if ping_role else ""))
                          + ("\n\n" + "\n".join(delivery) if delivery else ""),
                          color=SUCCESS, guild=guild, footer=f"Giveaway #{g['id']} · ends automatically"),
            ephemeral=True,
        )

    @app_commands.command(description="Set giveaway defaults: the role to ping and the usual channel")
    @app_commands.describe(ping_role="Role pinged whenever a giveaway starts", clear_ping="Stop pinging a role automatically", default_channel="Where giveaways are posted by default")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def config(self, interaction: discord.Interaction, ping_role: Optional[discord.Role] = None, clear_ping: bool = False, default_channel: Optional[discord.TextChannel] = None):
        guild = interaction.guild
        await db.execute("INSERT OR IGNORE INTO giveaway_settings (guild_id) VALUES (?)", (guild.id,))
        if ping_role:
            await db.execute("UPDATE giveaway_settings SET ping_role_id = ? WHERE guild_id = ?", (ping_role.id, guild.id))
        if clear_ping:
            await db.execute("UPDATE giveaway_settings SET ping_role_id = NULL WHERE guild_id = ?", (guild.id,))
        if default_channel:
            check_can_send(default_channel, guild.me)
            await db.execute("UPDATE giveaway_settings SET default_channel_id = ? WHERE guild_id = ?", (default_channel.id, guild.id))
        row = await db.fetch_one("SELECT * FROM giveaway_settings WHERE guild_id = ?", (guild.id,))
        role = guild.get_role(row["ping_role_id"]) if row["ping_role_id"] else None
        warn = "\n\n⚠️ That role isn't **mentionable**, so I can't ping it. Turn on *Allow anyone to @mention this role* for it, or run `/pingroles setup`." if role and not role.mentionable and not guild.me.guild_permissions.mention_everyone else ""
        body = ui.kv(("🔔 Ping role", role.mention if role else "None (no automatic ping)"), ("📍 Default channel", f"<#{row['default_channel_id']}>" if row["default_channel_id"] else "The channel you run it in")) + warn
        await interaction.response.send_message(embed=ui.card("🎉 Giveaway settings", body, guild=guild, section="Giveaways"), ephemeral=True)

    @app_commands.command(description="Change a running giveaway: prize, winners, time, description, or auto-delivery")
    @app_commands.describe(
        giveaway_id="Which giveaway", prize="New prize", winners="New number of winners", extend="Add time, e.g. 1h or 1d",
        description="New description", prize_role="A role to automatically give the winner(s)",
        prize_file="A file from your library to automatically DM the winner(s)",
        clear_prize_role="Remove the auto-given role", clear_prize_file="Remove the auto-delivered file",
    )
    @app_commands.autocomplete(giveaway_id=giveaway_autocomplete, prize_file=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(self, interaction: discord.Interaction, giveaway_id: int, prize: Optional[app_commands.Range[str, 1, 200]] = None,
                   winners: Optional[app_commands.Range[int, 1, 20]] = None, extend: Optional[str] = None, description: Optional[app_commands.Range[str, 1, 500]] = None,
                   prize_role: Optional[discord.Role] = None, prize_file: Optional[str] = None,
                   clear_prize_role: bool = False, clear_prize_file: bool = False):
        g = await self.get_giveaway(interaction, giveaway_id)
        if g["status"] != "active":
            raise UserError("Only a running giveaway can be changed.")
        updates: dict = {}
        if prize:
            updates["prize"] = prize.strip()
        if winners:
            updates["winners"] = winners
        if description:
            updates["description"] = description
        if clear_prize_role:
            updates["prize_role_id"] = None
        elif prize_role:
            updates["prize_role_id"] = prize_role.id
        if clear_prize_file:
            updates["prize_file_id"] = None
        elif prize_file:
            row = await db.fetch_one("SELECT id FROM stored_files WHERE guild_id = ? AND name = ?", (interaction.guild_id, prize_file.strip()))
            if row is None:
                raise UserError(f"I can't find a file called **{prize_file}** in your library. Try `/files list`.")
            updates["prize_file_id"] = row["id"]
        if extend:
            seconds = parse_duration(extend)
            if seconds is None or seconds < MIN_SECONDS:
                raise UserError("Use a time like `30m`, `2h` or `1d` for `extend`.")
            new_end = parse_iso(g["ends_at"]) + timedelta(seconds=seconds)
            if (new_end - discord.utils.utcnow()).total_seconds() > MAX_SECONDS:
                raise UserError("A giveaway can run for up to 60 days in total.")
            updates["ends_at"] = new_end.isoformat()
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option.")
        await db.execute(f"UPDATE giveaways SET {', '.join(f'{c} = ?' for c in updates)} WHERE id = ?", (*updates.values(), g["id"]))
        g = await db.fetch_one("SELECT * FROM giveaways WHERE id = ?", (g["id"],))
        await self.refresh_message(interaction.guild, g)
        await emit(interaction.guild, "giveaways", "Giveaway edited", ui.kv(("🎁 Prize", g["prize"]), ("🔧 Changed", ", ".join(updates)), ("🛡️ By", interaction.user.mention)), footer=f"#{g['id']}")
        await interaction.response.send_message(embed=ui.card("✅ Giveaway updated", f"Changed: {', '.join(updates)}. The card was updated.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="End a giveaway now and pick the winners")
    @app_commands.describe(giveaway_id="Which giveaway")
    @app_commands.autocomplete(giveaway_id=giveaway_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def end(self, interaction: discord.Interaction, giveaway_id: int):
        g = await self.get_giveaway(interaction, giveaway_id)
        if g["status"] != "active":
            raise UserError(f"That giveaway is already {g['status']}.")
        await interaction.response.defer(ephemeral=True)
        winners = await self.end_giveaway(interaction.guild, g, interaction.user)
        await interaction.followup.send(
            embed=ui.card("🏁 Giveaway ended", ("Winners: " + ", ".join(f"<@{w}>" for w in winners)) if winners else "Nobody had a valid entry, so there are no winners.", color=SUCCESS if winners else WARN), ephemeral=True
        )

    @app_commands.command(description="Pick new winners for a giveaway that has ended")
    @app_commands.describe(giveaway_id="Which giveaway", winners="How many new winners (default 1)")
    @app_commands.autocomplete(giveaway_id=giveaway_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def reroll(self, interaction: discord.Interaction, giveaway_id: int, winners: app_commands.Range[int, 1, 20] = 1):
        g = await self.get_giveaway(interaction, giveaway_id)
        if g["status"] != "ended":
            raise UserError("You can only reroll a giveaway that has ended.")
        previous = [int(x) for x in (g["winner_ids"] or "").split(",") if x]
        picked = pick_winners(await self.eligible(interaction.guild, g, exclude=set(previous)), winners)
        if not picked:
            raise UserError("There's nobody left to pick. Everyone eligible has already won.")
        await interaction.response.defer(ephemeral=True)
        await db.execute("UPDATE giveaways SET winner_ids = ? WHERE id = ?", (",".join(str(x) for x in previous + picked), g["id"]))
        g = await db.fetch_one("SELECT * FROM giveaways WHERE id = ?", (g["id"],))
        message = await self.message_for(interaction.guild, g)
        await self.announce(interaction.guild, g, picked, message)
        await self.dm_winners(interaction.guild, g, picked, message)
        await interaction.followup.send(embed=ui.card("🎲 Rerolled", "New winner" + ("s" if len(picked) > 1 else "") + ": " + ", ".join(f"<@{w}>" for w in picked), color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Cancel a running giveaway (no winners are picked)")
    @app_commands.describe(giveaway_id="Which giveaway")
    @app_commands.autocomplete(giveaway_id=giveaway_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def cancel(self, interaction: discord.Interaction, giveaway_id: int):
        g = await self.get_giveaway(interaction, giveaway_id)
        if g["status"] != "active":
            raise UserError(f"That giveaway is already {g['status']}.")
        await interaction.response.defer(ephemeral=True)
        await db.execute("UPDATE giveaways SET status = 'cancelled', ended_at = ? WHERE id = ? AND status = 'active'", (discord.utils.utcnow().isoformat(), g["id"]))
        message = await self.message_for(interaction.guild, g)
        if message is not None:
            try:
                await message.edit(embed=cancelled_embed(interaction.guild, g), view=build_view(g, 0, ended=True))
            except discord.HTTPException:
                pass
        await emit(interaction.guild, "giveaways", "Giveaway cancelled", ui.kv(("Prize", g["prize"]), ("By", interaction.user.mention)), footer=f"#{g['id']}")
        await interaction.followup.send(embed=ui.card("🚫 Giveaway cancelled", f"**{g['prize']}** was cancelled.", color=DANGER), ephemeral=True)

    @app_commands.command(description="See the giveaways that are running")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        rows = await db.fetch_all("SELECT * FROM giveaways WHERE guild_id = ? AND status = 'active' ORDER BY ends_at LIMIT 15", (interaction.guild_id,))
        if not rows:
            raise UserError("There are no running giveaways. Start one with `/giveaway start`.")
        lines = []
        for g in rows:
            count = await self.entry_count(g["id"])
            lines.append(f"`#{g['id']}` **{g['prize']}** · <#{g['channel_id']}>\n　⏰ ends {discord.utils.format_dt(parse_iso(g['ends_at']), 'R')} · 👥 {ui.plural(count, 'entry', 'entries')} · 🏆 {g['winners']}")
        await interaction.response.send_message(embed=ui.card("🎉 Running giveaways", "\n\n".join(lines), color=ACCENT, guild=interaction.guild, section="Giveaways"), ephemeral=True)

    @app_commands.command(description="Details about one giveaway")
    @app_commands.describe(giveaway_id="Which giveaway")
    @app_commands.autocomplete(giveaway_id=giveaway_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def info(self, interaction: discord.Interaction, giveaway_id: int):
        g = await self.get_giveaway(interaction, giveaway_id)
        count = await self.entry_count(g["id"])
        winners = [w for w in (g["winner_ids"] or "").split(",") if w]
        body = ui.kv(
            ("📍 Status", g["status"].title()), ("🎁 Prize", g["prize"]), ("🎭 Hosted by", f"<@{g['host_id']}>"), ("💬 Channel", f"<#{g['channel_id']}>"),
            ("⏰ Ends", card_time(parse_iso(g["ends_at"]))), ("🏆 Winners wanted", str(g["winners"])), ("👥 Entries", f"{count:,}"),
            ("🥇 Winners", ", ".join(f"<@{w}>" for w in winners) if winners else None), *requirement_lines(g),
        )
        await interaction.response.send_message(embed=ui.card(f"🎉 Giveaway #{g['id']}", body, color=ACCENT, guild=interaction.guild, section="Giveaways"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))
