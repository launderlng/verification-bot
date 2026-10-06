import logging
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import ui
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
    return (
        ("🔒 Requires", f"<@&{g['required_role_id']}>" if g["required_role_id"] else None),
        ("💎 Bonus", f"<@&{g['bonus_role_id']}> gets **+{g['bonus_entries']}** extra entries" if g["bonus_role_id"] and g["bonus_entries"] else None),
    )


def active_embed(guild: discord.Guild, g, entries: int) -> discord.Embed:
    ends = parse_iso(g["ends_at"])
    info = ui.kv(
        ("⏰ Ends", card_time(ends)), ("🏆 Winners", f"{g['winners']}"), ("👥 Entries", f"{entries:,}"),
        ("🎭 Hosted by", f"<@{g['host_id']}>"), *requirement_lines(g),
    )
    body = (f"{g['description']}\n\n" if g["description"] else "") + f"{ui.DIVIDER}\n{info}"
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
            if g["required_role_id"] and g["required_role_id"] not in role_ids:
                continue
            weight = 1 + (g["bonus_entries"] if g["bonus_role_id"] and g["bonus_role_id"] in role_ids else 0)
            result.append((member.id, weight))
        return result

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

    async def dm_winners(self, guild: discord.Guild, g, winners: list, message) -> None:
        tickets_cfg = await db.get_ticket_config(guild.id)
        for uid in winners:
            member = guild.get_member(uid)
            if member is None:
                continue
            body = (
                "Congratulations, you won! 🎉\n\n"
                + ui.kv(("🎁 Prize", g["prize"]), ("🏠 Server", guild.name), ("🏷️ Giveaway", f"#{g['id']}"))
                + f"\n\n{ui.DIVIDER}\n{self.claim_text(g, tickets_cfg)}"
            )
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
        if g["required_role_id"] and g["required_role_id"] not in {r.id for r in member.roles}:
            return await interaction.response.send_message(
                embed=ui.card("🔒 You can't enter yet", f"You need the <@&{g['required_role_id']}> role to enter this giveaway.", color=WARN), ephemeral=True
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
    )
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
    ):
        guild = interaction.guild
        seconds = parse_duration(duration)
        if seconds is None or seconds < MIN_SECONDS:
            raise UserError("Use a duration like `30m`, `2h` or `1d12h` (at least 10 seconds).")
        if seconds > MAX_SECONDS:
            raise UserError("Giveaways can run for up to 60 days.")
        if image_url and not image_url.startswith("https://"):
            raise UserError("The image must be a direct link starting with `https://`.")
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            raise UserError("Pick a text channel to post the giveaway in.")
        check_can_send(target, guild.me)

        await interaction.response.defer(ephemeral=True)
        now = discord.utils.utcnow()
        ends = now + timedelta(seconds=seconds)
        await db.execute(
            "INSERT INTO giveaways (guild_id, channel_id, host_id, prize, description, image_url, winners, required_role_id, bonus_role_id, bonus_entries, ends_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild.id, target.id, interaction.user.id, prize.strip(), description, image_url, winners, required_role.id if required_role else None,
             bonus_role.id if bonus_role else None, bonus_entries if bonus_role else 0, ends.isoformat(), now.isoformat()),
        )
        g = await db.fetch_one("SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT 1", (guild.id,))
        try:
            message = await target.send(embed=active_embed(guild, g, 0), view=build_view(g, 0))
        except discord.HTTPException:
            await db.execute("DELETE FROM giveaways WHERE id = ?", (g["id"],))
            raise UserError("I couldn't post in that channel. Check my permissions there.") from None
        await db.execute("UPDATE giveaways SET message_id = ? WHERE id = ?", (message.id, g["id"]))
        await emit(guild, "giveaways", "Giveaway started", ui.kv(("Prize", g["prize"]), ("Host", interaction.user.mention), ("Channel", target.mention), ("Ends", discord.utils.format_dt(ends, "R"))), footer=f"#{g['id']}")
        await interaction.followup.send(
            embed=ui.card("✅ Giveaway started", f"**{g['prize']}** is live in {target.mention}.\n\n[Jump to it]({message.jump_url})", color=SUCCESS, guild=guild, footer=f"Giveaway #{g['id']} · ends automatically"),
            ephemeral=True,
        )

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
