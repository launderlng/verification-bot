import asyncio
import io
import logging
import re
from collections import Counter
from datetime import timedelta
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks

import db
import ui
from common import ACCENT, COLOR, DANGER, INFO, SUCCESS, WARN, UserError, check_can_send, is_staff, parse_color, reply, staff_ids, support_button
from cogs.reviews import add_ticket_review_comment, post_ticket_review
from logutil import emit
from transcript import render_transcript

log = logging.getLogger("verification-bot")

PRIORITIES = {"low": ("🟢", "Low"), "normal": ("🔵", "Normal"), "high": ("🟠", "High"), "urgent": ("🔴", "Urgent")}
MAX_TYPES = 10
MAX_MESSAGES = 5000
CLOSE_DELAY = 5  # seconds between "closing…" and deleting the channel
DEFAULT_WELCOME = "Thanks for reaching out! A member of staff will be with you shortly. Please describe your issue in as much detail as you can."
DEFAULT_TYPES = [
    ("Support", "🛠️", "Get help with anything", "Thanks for reaching out! A member of staff will be with you shortly. Please describe your issue in as much detail as you can.", 0),
    ("Purchase help", "🧾", "Questions about an order (have your Invoice ID ready)", "Thanks for your purchase! Staff will check your Invoice ID and sort out your order.", 1),
]
INVOICE_RE = re.compile(r"^[A-Z0-9]{2,10}-[A-Z0-9]{3,12}$")  # any prefix: INV-3F9A1C2E, 14K-0042 …


# ---------------------------------------------------------- helpers ----

def number_label(t) -> str:
    return f"#{t['number']:04d}"


def slugify(text: str, fallback: str = "user", limit: int = 20) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-")
    return slug or fallback


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes}m"
    return f"{hours // 24}d {hours % 24}h"


def clean_emoji(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    if re.fullmatch(r"<a?:\w+:\d+>", text):
        return text
    if text.isascii() or len(text) > 8:
        raise UserError("Use a real emoji for `emoji`, like 🛠️ or 🧾 (or a custom server emoji).")
    return text


def simple(title: str, text: str, color: discord.Color = INFO) -> discord.Embed:
    return ui.card(title, text, color=color, section="Tickets")


def parse_iso(value: str):
    return discord.utils.parse_time(value)


# ---------------------------------------------------------------- UI ----

class TicketModal(discord.ui.Modal):
    def __init__(self, cog: "Tickets", ttype):
        super().__init__(title=f"{ttype['name']} ticket"[:45])
        self.cog, self.ttype = cog, ttype
        self.subject_input = discord.ui.TextInput(label="Subject", max_length=100, placeholder="A short summary")
        self.details_input = discord.ui.TextInput(
            label="Details", style=discord.TextStyle.paragraph, max_length=1000, placeholder="Tell us what's going on"
        )
        self.invoice_input = None
        self.add_item(self.subject_input)
        self.add_item(self.details_input)
        if ttype["needs_invoice"]:
            self.invoice_input = discord.ui.TextInput(label="Invoice ID", max_length=30, placeholder="INV-3F9A1C2E (from your receipt DM)")
            self.add_item(self.invoice_input)

    async def on_submit(self, interaction: discord.Interaction):
        invoice = self.invoice_input.value if self.invoice_input else None
        await self.cog.safe(interaction, self.cog.create_ticket(interaction, self.ttype, self.subject_input.value, self.details_input.value, invoice))

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Ticket modal error", exc_info=error)


class CloseModal(discord.ui.Modal):
    def __init__(self, cog: "Tickets", ticket):
        super().__init__(title=f"Close ticket {number_label(ticket)}"[:45])
        self.cog, self.ticket_id = cog, ticket["id"]
        self.reason_input = discord.ui.TextInput(
            label="Reason (optional)", style=discord.TextStyle.paragraph, required=False, max_length=300, placeholder="Why is this ticket being closed?"
        )
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.safe(interaction, self.cog.close_from_interaction(interaction, self.reason_input.value.strip() or None))

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Close modal error", exc_info=error)


class TicketControls(discord.ui.View):
    """Persistent buttons on the first message of every ticket."""

    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket:close")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe(interaction, self.cog.on_close_button(interaction))

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, emoji="🙋", custom_id="ticket:claim")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe(interaction, self.cog.do_claim(interaction))


class OpenSelect(discord.ui.Select):
    def __init__(self, cog: "Tickets", options: Optional[list] = None):
        options = options or [discord.SelectOption(label="Open a ticket", value="0")]
        super().__init__(placeholder="Choose a ticket type…", options=options, custom_id="ticket:open_select", min_values=1, max_values=1)
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await self.cog.safe(interaction, self.cog.on_panel_choice(interaction, self.values[0]))


class PanelSelectView(discord.ui.View):
    def __init__(self, cog: "Tickets", options: Optional[list] = None):
        super().__init__(timeout=None)
        self.add_item(OpenSelect(cog, options))


class PanelButtonView(discord.ui.View):
    def __init__(self, cog: "Tickets", label: str = "Open a ticket", emoji: str = "🎫"):
        super().__init__(timeout=None)
        self.cog = cog
        self.open_button.label = label[:80]
        self.open_button.emoji = emoji

    @discord.ui.button(label="Open a ticket", style=discord.ButtonStyle.primary, emoji="🎫", custom_id="ticket:open_button")
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.safe(interaction, self.cog.on_panel_choice(interaction, None))


class ChooseTypeView(discord.ui.View):
    """Fallback shown privately when the panel has several types but a plain button was pressed."""

    def __init__(self, cog: "Tickets", options: list):
        super().__init__(timeout=120)
        select = discord.ui.Select(placeholder="Choose a ticket type…", options=options)

        async def callback(interaction: discord.Interaction):
            await cog.safe(interaction, cog.on_panel_choice(interaction, select.values[0]))

        select.callback = callback
        self.add_item(select)


class RateButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:rate:(?P<tid>[0-9]+):(?P<n>[1-5])"):
    """Star buttons in the DM sent after a ticket closes. Pressing one posts the review to the reviews channel straight away."""

    def __init__(self, ticket_id: int, rating: int):
        super().__init__(discord.ui.Button(label=str(rating), emoji="⭐", style=discord.ButtonStyle.secondary, custom_id=f"ticket:rate:{ticket_id}:{rating}"))
        self.ticket_id, self.rating = ticket_id, rating

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["tid"]), int(match["n"]))

    async def callback(self, interaction: discord.Interaction):
        t = await db.fetch_one("SELECT * FROM tickets WHERE id = ?", (self.ticket_id,))
        if not t or t["user_id"] != interaction.user.id:
            return await interaction.response.send_message("This rating isn't for you.", ephemeral=True)
        if t["rating"]:
            return await interaction.response.send_message(embed=ui.card("Already rated", f"You rated this ticket {ui.stars(t['rating'])} already. Thanks! 💙"), ephemeral=True)
        await db.execute("UPDATE tickets SET rating = ? WHERE id = ?", (self.rating, self.ticket_id))
        t = await db.fetch_one("SELECT * FROM tickets WHERE id = ?", (self.ticket_id,))
        guild = interaction.client.get_guild(t["guild_id"])
        cfg = await db.get_ticket_config(t["guild_id"])
        posted = await post_ticket_review(guild, interaction.user, t, self.rating) if guild else None
        review, message = posted if posted else (None, None)

        body = f"{ui.stars(self.rating)}  **{self.rating}/5** for ticket {number_label(t)}"
        if t["claimed_by"]:
            body += f"\n🛡️ Handled by <@{t['claimed_by']}>"
        if message:
            body += f"\n\nYour review is now live in <#{message.channel.id}>. You can add a comment below."
        view = discord.ui.View(timeout=None)
        if review:
            view.add_item(CommentButton(self.ticket_id))
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="See your review", emoji="⭐", url=message.jump_url))
        button = support_button(guild, cfg) if guild else None
        if button:
            view.add_item(button)
        await interaction.response.edit_message(embed=ui.card("Thanks for your feedback! 💙", body, color=SUCCESS, guild=guild, section="Support"), view=view)
        if guild:
            await emit(
                guild, "tickets", "Ticket rated",
                ui.kv(("🎫 Ticket", f"{number_label(t)} · {t['type_name']}"), ("⭐ Rating", f"{ui.stars(self.rating)} {self.rating}/5"), ("🛡️ Staff", f"<@{t['claimed_by']}>" if t["claimed_by"] else None)),
                footer=f"Ticket {number_label(t)}",
            )


class CommentButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket:comment:(?P<tid>[0-9]+)"):
    """'Add a comment' under a ticket review (persistent across restarts)."""

    def __init__(self, ticket_id: int):
        super().__init__(discord.ui.Button(label="Add a comment", emoji="💬", style=discord.ButtonStyle.primary, custom_id=f"ticket:comment:{ticket_id}"))
        self.ticket_id = ticket_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["tid"]))

    async def callback(self, interaction: discord.Interaction):
        t = await db.fetch_one("SELECT * FROM tickets WHERE id = ?", (self.ticket_id,))
        if not t or t["user_id"] != interaction.user.id:
            return await interaction.response.send_message("This isn't your ticket.", ephemeral=True)
        await interaction.response.send_modal(CommentModal(self.ticket_id))


class CommentModal(discord.ui.Modal):
    def __init__(self, ticket_id: int):
        super().__init__(title="Add a comment to your review")
        self.ticket_id = ticket_id
        self.text = discord.ui.TextInput(
            label="Your comment", style=discord.TextStyle.paragraph, max_length=500, placeholder="What went well? What could be better?",
        )
        self.add_item(self.text)

    async def on_submit(self, interaction: discord.Interaction):
        t = await db.fetch_one("SELECT * FROM tickets WHERE id = ?", (self.ticket_id,))
        guild = interaction.client.get_guild(t["guild_id"]) if t else None
        if not t or guild is None or t["user_id"] != interaction.user.id:
            return await interaction.response.send_message("I couldn't add that comment.", ephemeral=True)
        message = await add_ticket_review_comment(guild, interaction.user, self.ticket_id, self.text.value.strip() or None)
        cfg = await db.get_ticket_config(guild.id)
        view = discord.ui.View(timeout=None)
        if message:
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="See your review", emoji="⭐", url=message.jump_url))
        button = support_button(guild, cfg)
        if button:
            view.add_item(button)
        await interaction.response.edit_message(
            embed=ui.card("Comment added 💬", "Thanks! Your comment is now part of your review.", color=SUCCESS, guild=guild, section="Support"), view=view
        )


def rating_view(ticket_id: int, guild: Optional[discord.Guild] = None, cfg=None) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for n in range(1, 6):
        view.add_item(RateButton(ticket_id, n))
    button = support_button(guild, cfg) if guild else None
    if button:
        view.add_item(button)
    return view


# -------------------------------------------------------------- cog ----

@app_commands.guild_only()
class Tickets(commands.GroupCog, group_name="ticket", group_description="Private support tickets with transcripts"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.channels: dict[int, int] = {}  # open ticket channel id -> ticket id
        super().__init__()

    async def cog_load(self):
        self.bot.add_view(PanelSelectView(self))
        self.bot.add_view(PanelButtonView(self))
        self.bot.add_view(TicketControls(self))
        self.bot.add_dynamic_items(RateButton, CommentButton)
        rows = await db.fetch_all("SELECT id, channel_id FROM tickets WHERE status = 'open' AND channel_id IS NOT NULL")
        self.channels = {r["channel_id"]: r["id"] for r in rows}
        self.sweeper.start()

    async def cog_unload(self):
        self.sweeper.cancel()
        try:
            self.bot.remove_dynamic_items(RateButton, CommentButton)
        except Exception:
            pass

    # ---------------------------------------------------------- helpers ----

    async def safe(self, interaction: discord.Interaction, coro) -> None:
        try:
            await coro
        except UserError as e:
            await reply(interaction, embed=simple("⚠️ Hold on", str(e), WARN))
        except Exception:
            log.exception("Unexpected ticket error")
            await reply(interaction, embed=simple("Something went wrong", "Please try again, or ask a moderator.", DANGER))

    async def cfg_or_error(self, guild_id: int):
        cfg = await db.get_ticket_config(guild_id)
        if not cfg:
            raise UserError("Tickets aren't set up yet. An admin can run `/ticket setup`.")
        return cfg

    async def ticket_here(self, interaction: discord.Interaction):
        row = await db.fetch_one("SELECT * FROM tickets WHERE channel_id = ? AND status = 'open'", (interaction.channel_id,))
        if not row:
            raise UserError("This isn't an open ticket channel.")
        return row

    async def staff_ticket(self, interaction: discord.Interaction):
        """For staff-only commands run inside a ticket. Returns (cfg, ticket)."""
        cfg = await self.cfg_or_error(interaction.guild_id)
        if not is_staff(interaction.user, cfg):
            raise UserError("Only ticket staff can do that.")
        return cfg, await self.ticket_here(interaction)

    async def render_embed(self, guild: discord.Guild, t) -> discord.Embed:
        ttype = await db.fetch_one("SELECT * FROM ticket_types WHERE guild_id = ? AND name = ?", (guild.id, t["type_name"]))
        emoji = f"{ttype['emoji']} " if ttype and ttype["emoji"] else ""
        p_emoji, p_label = PRIORITIES.get(t["priority"], PRIORITIES["normal"])
        if t["status"] != "open":
            status = "🔒 Closed"
        elif t["claimed_by"]:
            status = f"🙋 Claimed by <@{t['claimed_by']}>"
        else:
            status = "⏳ Waiting for staff"
        welcome = ttype["welcome"] if ttype and ttype["welcome"] else DEFAULT_WELCOME
        info = ui.kv(
            ("👤 Opened by", f"<@{t['user_id']}>"), ("🚦 Priority", f"{p_emoji} {p_label}"), ("📍 Status", status),
            ("📌 Subject", t["subject"]), ("🧾 Invoice ID", f"`{t['invoice_code']}`" if t["invoice_code"] else None),
        )
        embed = ui.card(
            f"🎫 Ticket {number_label(t)} · {emoji}{t['type_name']}", f"{welcome}\n\n{ui.DIVIDER}\n{info}",
            color=DANGER if t["priority"] == "urgent" else ACCENT, footer="Use the buttons below · Staff commands: /ticket claim, priority, add, close",
        )
        if t["details"]:
            embed.add_field(name="📝 Details", value=t["details"][:1000], inline=False)
        return embed

    async def refresh_controls(self, guild: discord.Guild, t) -> None:
        """Update the embed on the ticket's first message (claim / priority changes)."""
        channel = guild.get_channel(t["channel_id"]) if t["channel_id"] else None
        if channel is None or not t["control_message_id"]:
            return
        try:
            message = await channel.fetch_message(t["control_message_id"])
            await message.edit(embed=await self.render_embed(guild, t))
        except discord.HTTPException:
            pass

    async def can_open(self, guild: discord.Guild, member: discord.Member, cfg) -> Optional[str]:
        if await db.fetch_one("SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?", (guild.id, member.id)):
            return "You're not able to open tickets in this server."
        open_rows = await db.fetch_all("SELECT channel_id FROM tickets WHERE guild_id = ? AND user_id = ? AND status = 'open'", (guild.id, member.id))
        if len(open_rows) >= cfg["max_open"]:
            where = " ".join(f"<#{r['channel_id']}>" for r in open_rows if r["channel_id"])
            return f"You already have {len(open_rows)} open ticket(s): {where}\nPlease use that one, or close it first."
        return None

    def build_overwrites(self, guild: discord.Guild, member: discord.Member, cfg) -> dict:
        user_perms = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_channels=True
            ),
            member: user_perms,
        }
        for rid in staff_ids(cfg):
            role = guild.get_role(rid)
            if role:
                overwrites[role] = user_perms
        return overwrites

    # ----------------------------------------------------------- panel ----

    async def types_for(self, guild_id: int):
        return await db.fetch_all("SELECT * FROM ticket_types WHERE guild_id = ? ORDER BY id", (guild_id,))

    def type_options(self, types) -> list:
        return [
            discord.SelectOption(label=t["name"][:100], value=str(t["id"]), description=(t["description"] or "")[:100] or None, emoji=t["emoji"] or None)
            for t in types
        ]

    def build_panel(self, guild: discord.Guild, cfg, types) -> tuple[discord.Embed, discord.ui.View]:
        color = int(cfg["panel_color"], 16) if cfg["panel_color"] else COLOR.value
        embed = ui.card(cfg["panel_title"] or "🎫 Need help?", cfg["panel_text"] or "Open a private ticket and our team will get back to you as soon as they can.", color=color, section="Tickets")
        if len(types) > 1:
            embed.add_field(
                name="Ticket types",
                value="\n".join(f"{t['emoji'] or '🎫'} **{t['name']}**: {t['description'] or ''}".rstrip(": ") for t in types),
                inline=False,
            )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if cfg["panel_image"]:
            embed.set_image(url=cfg["panel_image"])
        embed.set_footer(text=f"{guild.name} · Support")
        if len(types) > 1:
            return embed, PanelSelectView(self, self.type_options(types))
        only = types[0]
        return embed, PanelButtonView(self, f"Open a {only['name']} ticket" if only["name"] else "Open a ticket", only["emoji"] or "🎫")

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
            raise UserError("The panel channel no longer exists. Run `/ticket setup` again.")
        types = await self.types_for(guild.id)
        if not types:
            raise UserError("Add at least one ticket type first: `/ticket addtype`.")
        await self.delete_panel(guild, old or cfg)
        embed, view = self.build_panel(guild, cfg, types)
        message = await channel.send(embed=embed, view=view)
        await db.upsert_ticket_config(guild.id, panel_message_id=message.id)
        return message

    async def refresh_panel(self, guild: discord.Guild) -> None:
        cfg = await db.get_ticket_config(guild.id)
        if cfg and cfg["panel_message_id"]:
            try:
                await self.post_panel(guild, cfg)
            except UserError:
                pass

    async def on_panel_choice(self, interaction: discord.Interaction, value: Optional[str]) -> None:
        guild = interaction.guild
        cfg = await self.cfg_or_error(guild.id)
        types = await self.types_for(guild.id)
        if not types:
            raise UserError("There are no ticket types yet. Please tell an admin.")
        chosen = None
        if value and value.isdigit() and value != "0":
            chosen = next((t for t in types if t["id"] == int(value)), None)
        elif len(types) == 1:
            chosen = types[0]
        if chosen is None:
            return await reply(interaction, embed=simple("🎫 Open a ticket", "What do you need help with?", INFO), view=ChooseTypeView(self, self.type_options(types)))

        problem = await self.can_open(guild, interaction.user, cfg)
        if problem:
            raise UserError(problem)
        await interaction.response.send_modal(TicketModal(self, chosen))
        # Reset the dropdown so the same option can be picked again (best effort)
        if interaction.message is not None and interaction.message.id == cfg["panel_message_id"] and len(types) > 1:
            try:
                await interaction.message.edit(view=PanelSelectView(self, self.type_options(types)))
            except discord.HTTPException:
                pass

    # ----------------------------------------------------- ticket life ----

    async def create_ticket(self, interaction: discord.Interaction, ttype, subject: str, details: str, invoice: Optional[str]) -> None:
        guild, member = interaction.guild, interaction.user
        cfg = await self.cfg_or_error(guild.id)
        problem = await self.can_open(guild, member, cfg)
        if problem:
            raise UserError(problem)

        invoice_code = None
        if ttype["needs_invoice"]:
            raw = (invoice or "").strip().upper()
            invoice_code = raw if INVOICE_RE.match(raw) else None
            if invoice_code is None and re.fullmatch(r"[A-Z0-9]{3,12}", raw):  # typed without the prefix
                matches = await db.fetch_all("SELECT code FROM orders WHERE guild_id = ? AND code LIKE ? LIMIT 2", (guild.id, f"%-{raw}"))
                if len(matches) == 1:
                    invoice_code = matches[0]["code"]
            if invoice_code is None:
                raise UserError("That doesn't look like an Invoice ID. It looks like `INV-3F9A1C2E` and is in your receipt DM (or use `/myorders`).")

        await interaction.response.defer(ephemeral=True)
        category = guild.get_channel(cfg["category_id"]) if cfg["category_id"] else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        overwrites = self.build_overwrites(guild, member, cfg)
        now = discord.utils.utcnow().isoformat()

        channel, number = None, None
        for _ in range(3):
            number = (await db.fetch_one("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM tickets WHERE guild_id = ?", (guild.id,)))["n"]
            try:
                channel = await guild.create_text_channel(
                    f"ticket-{number:04d}-{slugify(member.display_name)}", category=category, overwrites=overwrites,
                    topic=f"{ttype['name']} ticket for {member} (ID {member.id})", reason=f"Ticket opened by {member}",
                )
            except discord.Forbidden:
                raise UserError("I don't have permission to create ticket channels. An admin needs to give me **Manage Channels**.") from None
            except discord.HTTPException:
                raise UserError("I couldn't create the ticket channel. The ticket category may be full (50 channels max). Please tell an admin.") from None
            try:
                await db.execute(
                    "INSERT INTO tickets (guild_id, number, channel_id, user_id, type_name, subject, details, invoice_code, created_at, last_activity) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (guild.id, number, channel.id, member.id, ttype["name"], subject.strip(), details.strip(), invoice_code, now, now),
                )
                break
            except aiosqlite.IntegrityError:  # two tickets raced for the same number
                await channel.delete(reason="Ticket number clash, retrying")
                channel = None
        if channel is None:
            raise UserError("I couldn't create your ticket just now. Please try again.")

        t = await db.fetch_one("SELECT * FROM tickets WHERE guild_id = ? AND number = ?", (guild.id, number))
        self.channels[channel.id] = t["id"]

        staff_roles = [r for r in (guild.get_role(rid) for rid in staff_ids(cfg)) if r]
        content = member.mention
        if cfg["ping_staff"] and staff_roles:
            content += " " + " ".join(r.mention for r in staff_roles)
        message = await channel.send(
            content=content, embed=await self.render_embed(guild, t), view=TicketControls(self),
            allowed_mentions=discord.AllowedMentions(users=[member], roles=staff_roles if cfg["ping_staff"] else []),
        )
        await db.execute("UPDATE tickets SET control_message_id = ? WHERE id = ?", (message.id, t["id"]))

        if invoice_code:
            await channel.send(embed=await self.invoice_embed(guild, member, invoice_code))

        await emit(guild, "tickets", "🎫 Ticket opened", ui.kv(("👤 Opened by", f"{member.mention} (`{member.id}`)"), ("📍 Channel", channel.mention), ("🎫 Ticket", f"{number_label(t)} · {ttype['name']}"), ("📌 Subject", subject.strip()), ("🧾 Invoice", f"`{invoice_code}`" if invoice_code else None)), COLOR, subject=member.id, ids=(("ticket", number_label(t)), ("channel", channel.id)))
        await interaction.followup.send(embed=simple("✅ Ticket created", f"Head over to {channel.mention}. A staff member will be with you soon.", COLOR), ephemeral=True)

    async def invoice_embed(self, guild: discord.Guild, member: discord.Member, code: str) -> discord.Embed:
        order = await db.fetch_one("SELECT * FROM orders WHERE guild_id = ? AND code = ?", (guild.id, code))
        if order is None:
            return simple("❌ Invoice not found", f"There's no order with ID `{code}` in this server. Staff: please double-check it with the buyer.", DANGER)
        embed = ui.card(color=COLOR if order["user_id"] == member.id else WARN, section="Tickets")
        if order["user_id"] == member.id:
            embed.title = "✅ Verified purchase"
        else:
            embed.title = "⚠️ Invoice belongs to someone else"
            embed.description = f"This invoice was bought by <@{order['user_id']}>, not the person who opened this ticket."
        embed.add_field(name="Invoice ID", value=f"`{order['code']}`", inline=False)
        embed.add_field(name="Product", value=order["product_name"])
        embed.add_field(name="Amount", value=order["amount"] or "—")
        embed.add_field(name="Paid", value=discord.utils.format_dt(parse_iso(order["created_at"]), "f"))
        if order["source"] == "manual":
            embed.add_field(name="Created by", value="Staff (manual order)")
        if not order["livemode"]:
            embed.set_footer(text="🧪 This was a test-mode payment")
        return embed

    async def on_close_button(self, interaction: discord.Interaction) -> None:
        ticket = await self.ticket_here(interaction)
        cfg = await self.cfg_or_error(interaction.guild_id)
        if interaction.user.id != ticket["user_id"] and not is_staff(interaction.user, cfg):
            raise UserError("Only the ticket owner or staff can close this ticket.")
        await interaction.response.send_modal(CloseModal(self, ticket))

    async def close_from_interaction(self, interaction: discord.Interaction, reason: Optional[str]) -> None:
        ticket = await self.ticket_here(interaction)
        cfg = await self.cfg_or_error(interaction.guild_id)
        if interaction.user.id != ticket["user_id"] and not is_staff(interaction.user, cfg):
            raise UserError("Only the ticket owner or staff can close this ticket.")
        await reply(interaction, embed=simple("🔒 Closing", "Saving the transcript and closing this ticket…", INFO))
        await self.close_ticket(interaction.guild, ticket, interaction.user, reason)

    async def ticket_log(self, guild: discord.Guild, ticket, title: str, actor, *lines, color=None) -> None:
        """Log a staff action on a ticket in the standard shape: the ticket, who did it, the IDs and the time."""
        await emit(
            guild, "tickets", title,
            ui.kv(("🎫 Ticket", f"{number_label(ticket)} · {ticket['type_name']}"), ("👤 Opened by", f"<@{ticket['user_id']}>"), ("📍 Channel", f"<#{ticket['channel_id']}>"), *lines),
            color, actor=actor, subject=ticket["user_id"], ids=(("ticket", number_label(ticket)), ("channel", ticket["channel_id"])),
        )

    async def do_claim(self, interaction: discord.Interaction) -> None:
        cfg = await self.cfg_or_error(interaction.guild_id)
        if not is_staff(interaction.user, cfg):
            raise UserError("Only ticket staff can claim tickets.")
        ticket = await self.ticket_here(interaction)
        if ticket["claimed_by"] and ticket["claimed_by"] != interaction.user.id:
            raise UserError(f"This ticket is already claimed by <@{ticket['claimed_by']}>.")
        if ticket["claimed_by"] == interaction.user.id:
            raise UserError("You've already claimed this ticket.")
        await db.execute("UPDATE tickets SET claimed_by = ? WHERE id = ?", (interaction.user.id, ticket["id"]))
        ticket = await db.fetch_one("SELECT * FROM tickets WHERE id = ?", (ticket["id"],))
        await self.refresh_controls(interaction.guild, ticket)
        await self.ticket_log(interaction.guild, ticket, "Ticket claimed", interaction.user)
        await reply(interaction, embed=simple("🙋 Ticket claimed", f"{interaction.user.mention} will be helping with this ticket.", COLOR), ephemeral=False)

    async def build_transcript(self, guild: discord.Guild, channel: discord.TextChannel, ticket, closer_text: str = "") -> tuple[bytes, int]:
        messages = []
        async for m in channel.history(limit=MAX_MESSAGES, oldest_first=True):
            messages.append({
                "author_name": m.author.display_name, "author_id": m.author.id, "avatar_url": m.author.display_avatar.url, "bot": m.author.bot,
                "timestamp": m.created_at, "content": m.content or getattr(m, "system_content", "") or "",
                "attachments": [(a.filename, a.url) for a in m.attachments], "embeds": [(e.title, e.description) for e in m.embeds],
                "edited": m.edited_at is not None,
            })
        details = [
            f"Server: {guild.name}", f"Opened by: {ticket['user_id']}", f"Type: {ticket['type_name']}",
            f"Subject: {ticket['subject'] or '-'}", f"Opened: {ticket['created_at']}",
        ]
        if closer_text:
            details.append(closer_text)
        note = "" if self.bot.intents.message_content else "Message text isn't included because the bot's Message Content Intent is off. Turn it on to get full transcripts."
        html_text = render_transcript(f"Ticket {number_label(ticket)} · {ticket['type_name']}", details, messages, note)
        return html_text.encode("utf-8"), len(messages)

    async def close_ticket(self, guild: discord.Guild, ticket, closer, reason: Optional[str]) -> bool:
        now = discord.utils.utcnow()
        changed = await db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ?, close_reason = ? WHERE id = ? AND status = 'open'",
            (now.isoformat(), closer.id if closer else None, reason, ticket["id"]),
        )
        if not changed:
            return False  # already closed or closing
        self.channels.pop(ticket["channel_id"], None)
        cfg = await db.get_ticket_config(guild.id)
        channel = guild.get_channel(ticket["channel_id"]) if ticket["channel_id"] else None

        data, count = None, 0
        closer_text = f"Closed by: {closer.id if closer else 'automatic'} · Reason: {reason or '-'}"
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(embed=simple("🔒 Closing ticket", f"Saving the transcript. This channel will be deleted in {CLOSE_DELAY} seconds.", WARN))
            except discord.HTTPException:
                pass
            try:
                data, count = await self.build_transcript(guild, channel, ticket, closer_text)
            except discord.HTTPException:
                log.exception("Couldn't build the transcript for ticket %s", ticket["id"])

        number = number_label(ticket)
        filename = f"transcript-{ticket['number']:04d}.html"
        summary = ui.card(f"🎫 Ticket {number} closed", color=WARN, section="Tickets", timestamp=True)
        summary.add_field(name="Opened by", value=f"<@{ticket['user_id']}>")
        summary.add_field(name="Type", value=ticket["type_name"])
        summary.add_field(name="Closed by", value=closer.mention if closer else "Automatic")
        summary.add_field(name="Claimed by", value=f"<@{ticket['claimed_by']}>" if ticket["claimed_by"] else "Nobody")
        summary.add_field(name="Open for", value=fmt_duration((now - parse_iso(ticket["created_at"])).total_seconds()))
        summary.add_field(name="Messages", value=str(count) if data else "n/a")
        summary.add_field(name="Subject", value=ticket["subject"] or "-", inline=False)
        summary.add_field(name="Reason", value=reason or "No reason given", inline=False)

        if cfg and cfg["transcript_channel_id"] and data:
            tch = guild.get_channel(cfg["transcript_channel_id"])
            if tch is not None:
                try:
                    await tch.send(embed=summary, file=discord.File(io.BytesIO(data), filename=filename))
                except discord.HTTPException:
                    log.warning("Couldn't post the transcript for ticket %s", ticket["id"])
        elif not (cfg and cfg["transcript_channel_id"]):
            await emit(guild, "tickets", f"🔒 Ticket {number} closed", ui.kv(("👤 Opened by", f"<@{ticket['user_id']}>"), ("🎫 Type", ticket["type_name"]), ("🙋 Claimed by", f"<@{ticket['claimed_by']}>" if ticket["claimed_by"] else None), ("📝 Reason", reason or "None given")), WARN, actor=closer, subject=ticket["user_id"], ids=(("ticket", number),))

        if cfg and cfg["dm_transcript"]:
            user = guild.get_member(ticket["user_id"])
            if user is None:
                try:
                    user = await self.bot.fetch_user(ticket["user_id"])
                except discord.HTTPException:
                    user = None
            if user is not None:
                rs = await db.get_review_settings(guild.id)
                body = f"Thanks for contacting **{guild.name}**.\n\n" + ui.kv(
                    ("🎫 Ticket", f"{number} · {ticket['type_name']}"),
                    ("🛡️ Handled by", f"<@{ticket['claimed_by']}>" if ticket["claimed_by"] else None),
                    ("⏱️ Open for", fmt_duration((now - parse_iso(ticket["created_at"])).total_seconds())),
                    ("📝 Reason", reason),
                )
                if cfg["ask_rating"]:
                    body += f"\n\n{ui.DIVIDER}\n**How was our support?** Tap a star below." + (" Your rating is posted in our reviews channel." if rs and rs["channel_id"] else "")
                dm = ui.card(
                    f"Your ticket {number} was closed", body, color=WARN, thumbnail=guild.icon.url if guild.icon else None,
                    footer="Your transcript is attached. Open it in a web browser." if data else guild.name,
                )
                kwargs = {"embed": dm}
                if data:
                    kwargs["file"] = discord.File(io.BytesIO(data), filename=filename)
                if cfg["ask_rating"]:
                    kwargs["view"] = rating_view(ticket["id"], guild, cfg)
                else:
                    link = support_button(guild, cfg)
                    if link:
                        v = discord.ui.View()
                        v.add_item(link)
                        kwargs["view"] = v
                try:
                    await user.send(**kwargs)
                except discord.HTTPException:
                    pass  # DMs closed

        if channel is not None:
            await asyncio.sleep(CLOSE_DELAY)
            try:
                await channel.delete(reason=f"Ticket {number} closed")
            except discord.HTTPException:
                pass
        return True

    # ----------------------------------------------------------- events ----

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        tid = self.channels.get(message.channel.id)
        if tid is None:
            return
        now = discord.utils.utcnow().isoformat()
        await db.execute("UPDATE tickets SET last_activity = ?, warned = 0 WHERE id = ?", (now, tid))
        t = await db.fetch_one("SELECT user_id, first_response_at FROM tickets WHERE id = ?", (tid,))
        if t and t["first_response_at"] is None and message.author.id != t["user_id"]:
            cfg = await db.get_ticket_config(message.guild.id)
            if cfg and isinstance(message.author, discord.Member) and is_staff(message.author, cfg):
                await db.execute("UPDATE tickets SET first_response_at = ? WHERE id = ? AND first_response_at IS NULL", (now, tid))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        tid = self.channels.pop(channel.id, None)
        if tid is None:
            return
        now = discord.utils.utcnow().isoformat()
        await db.execute(
            "UPDATE tickets SET status = 'closed', closed_at = ?, close_reason = 'Channel was deleted' WHERE id = ? AND status = 'open'", (now, tid)
        )

    @tasks.loop(minutes=5)
    async def sweeper(self):
        """Warn, then close, tickets that have gone quiet (if auto-close is on)."""
        now = discord.utils.utcnow()
        for guild in self.bot.guilds:
            cfg = await db.get_ticket_config(guild.id)
            if not cfg or not cfg["auto_close_hours"]:
                continue
            limit = cfg["auto_close_hours"] * 3600
            rows = await db.fetch_all("SELECT * FROM tickets WHERE guild_id = ? AND status = 'open'", (guild.id,))
            for t in rows:
                channel = guild.get_channel(t["channel_id"]) if t["channel_id"] else None
                if channel is None:
                    self.channels.pop(t["channel_id"], None)
                    await db.execute("UPDATE tickets SET status = 'closed', closed_at = ?, close_reason = 'Channel was deleted' WHERE id = ?", (now.isoformat(), t["id"]))
                    continue
                idle = (now - parse_iso(t["last_activity"])).total_seconds()
                if idle >= limit:
                    await self.close_ticket(guild, t, None, f"Closed automatically after {cfg['auto_close_hours']}h of inactivity")
                elif idle >= limit * 0.75 and not t["warned"]:
                    await db.execute("UPDATE tickets SET warned = 1 WHERE id = ?", (t["id"],))
                    left = fmt_duration(limit - idle)
                    try:
                        await channel.send(
                            content=f"<@{t['user_id']}>",
                            embed=simple("⏳ Still there?", f"This ticket will close automatically in about **{left}** if nobody replies.", WARN),
                            allowed_mentions=discord.AllowedMentions(users=True),
                        )
                    except discord.HTTPException:
                        pass

    @sweeper.before_loop
    async def before_sweeper(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------ admin commands ----

    @app_commands.command(description="Set up the ticket system and post the panel")
    @app_commands.describe(
        channel="Where the ticket panel goes",
        staff_role="Role that can see and answer tickets",
        category="Category for ticket channels (I'll create one if you skip this)",
        transcript_channel="Where transcripts of closed tickets are saved",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        staff_role: discord.Role,
        category: Optional[discord.CategoryChannel] = None,
        transcript_channel: Optional[discord.TextChannel] = None,
    ):
        guild = interaction.guild
        if staff_role.is_default():
            raise UserError("Pick a real staff role, not @everyone.")
        check_can_send(channel, guild.me)
        if transcript_channel:
            check_can_send(transcript_channel, guild.me, files=True)
        await interaction.response.defer(ephemeral=True)

        existing = await db.get_ticket_config(guild.id)
        roles = set(staff_ids(existing)) if existing else set()
        roles.add(staff_role.id)
        category_id = category.id if category else (existing["category_id"] if existing and guild.get_channel(existing["category_id"]) else None)
        if category_id is None:
            try:
                made = await guild.create_category(
                    "🎫 Tickets", reason="Ticket system setup",
                    overwrites={
                        guild.default_role: discord.PermissionOverwrite(view_channel=False),
                        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
                    },
                )
            except discord.HTTPException:
                raise UserError("I couldn't create a ticket category. Create one yourself and pass it as `category`.") from None
            category_id = made.id

        old = existing
        await db.upsert_ticket_config(
            guild.id, category_id=category_id, panel_channel_id=channel.id, staff_roles=",".join(str(r) for r in sorted(roles)),
            transcript_channel_id=transcript_channel.id if transcript_channel else (existing["transcript_channel_id"] if existing else None),
        )
        if not await self.types_for(guild.id):
            for name, emoji, desc, welcome, needs_invoice in DEFAULT_TYPES:
                await db.execute(
                    "INSERT OR IGNORE INTO ticket_types (guild_id, name, emoji, description, welcome, needs_invoice) VALUES (?, ?, ?, ?, ?, ?)",
                    (guild.id, name, emoji, desc, welcome, needs_invoice),
                )
        cfg = await db.get_ticket_config(guild.id)
        await self.post_panel(guild, cfg, old=old)

        warn = "" if self.bot.intents.message_content else (
            "\n\n⚠️ **Transcripts need the Message Content Intent.** Turn it on in the Discord Developer Portal (Bot tab), then set the variable "
            "`MESSAGE_CONTENT=true` and redeploy. Until then transcripts show who spoke and when, but not what was said."
        )
        await interaction.followup.send(
            f"✅ Tickets are live in {channel.mention}.\nStaff role: {staff_role.mention} · Tickets go in <#{category_id}>\n"
            "Next: `/ticket addtype` to add ticket types, `/ticket panel` to restyle the panel, `/ticket settings` for limits and auto-close." + warn,
            ephemeral=True,
        )

    @app_commands.command(description="Restyle the ticket panel (leave everything blank to just re-post it)")
    @app_commands.describe(title="Panel title", text="Text under the title", color="Colour as hex, e.g. #5865F2", image_url="Banner image link (or 'none')")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(
        self,
        interaction: discord.Interaction,
        title: Optional[app_commands.Range[str, 1, 100]] = None,
        text: Optional[app_commands.Range[str, 1, 1000]] = None,
        color: Optional[str] = None,
        image_url: Optional[str] = None,
    ):
        await self.cfg_or_error(interaction.guild_id)
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
            elif image_url.startswith("https://"):
                updates["panel_image"] = image_url
            else:
                raise UserError("The image must be a direct link starting with `https://` (or type `none`).")
        await interaction.response.defer(ephemeral=True)
        if updates:
            await db.upsert_ticket_config(interaction.guild_id, **updates)
        cfg = await db.get_ticket_config(interaction.guild_id)
        message = await self.post_panel(interaction.guild, cfg)
        await interaction.followup.send(f"✅ Panel updated: {message.jump_url}", ephemeral=True)

    @app_commands.command(description="Ticket settings: limits, auto-close, transcripts, pings, ratings")
    @app_commands.describe(
        category="Category where ticket channels are created",
        transcript_channel="Where transcripts of closed tickets are saved",
        max_open="Open tickets allowed per member (1-10)",
        auto_close_hours="Close tickets after this many idle hours (0 = never)",
        ping_staff="Ping the staff roles when a ticket opens",
        dm_transcript="DM the transcript to the member when their ticket closes",
        ask_rating="Ask the member to rate the support in that DM",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings(
        self,
        interaction: discord.Interaction,
        category: Optional[discord.CategoryChannel] = None,
        transcript_channel: Optional[discord.TextChannel] = None,
        max_open: Optional[app_commands.Range[int, 1, 10]] = None,
        auto_close_hours: Optional[app_commands.Range[int, 0, 720]] = None,
        ping_staff: Optional[bool] = None,
        dm_transcript: Optional[bool] = None,
        ask_rating: Optional[bool] = None,
    ):
        guild = interaction.guild
        await self.cfg_or_error(guild.id)
        updates: dict = {}
        if category:
            updates["category_id"] = category.id
        if transcript_channel:
            check_can_send(transcript_channel, guild.me, files=True)
            updates["transcript_channel_id"] = transcript_channel.id
        if max_open is not None:
            updates["max_open"] = max_open
        if auto_close_hours is not None:
            updates["auto_close_hours"] = auto_close_hours
        if ping_staff is not None:
            updates["ping_staff"] = int(ping_staff)
        if dm_transcript is not None:
            updates["dm_transcript"] = int(dm_transcript)
        if ask_rating is not None:
            updates["ask_rating"] = int(ask_rating)
        if updates:
            await db.upsert_ticket_config(guild.id, **updates)
        cfg = await db.get_ticket_config(guild.id)
        types = await self.types_for(guild.id)

        def chan(cid):
            return f"<#{cid}>" if cid else "Not set"

        embed = ui.card("🎫 Ticket settings", color=INFO, section="Tickets")
        embed.add_field(name="Panel", value=chan(cfg["panel_channel_id"]))
        embed.add_field(name="Ticket category", value=chan(cfg["category_id"]))
        embed.add_field(name="Transcripts", value=chan(cfg["transcript_channel_id"]))
        embed.add_field(name="Staff roles", value=" ".join(f"<@&{r}>" for r in staff_ids(cfg)) or "None")
        embed.add_field(name="Open tickets per member", value=str(cfg["max_open"]))
        embed.add_field(name="Auto-close", value=f"After {cfg['auto_close_hours']}h idle" if cfg["auto_close_hours"] else "Off")
        embed.add_field(name="Ping staff", value="On" if cfg["ping_staff"] else "Off")
        embed.add_field(name="DM transcript", value="On" if cfg["dm_transcript"] else "Off")
        embed.add_field(name="Ask for rating", value="On" if cfg["ask_rating"] else "Off")
        embed.add_field(name="Ticket types", value=", ".join(f"{t['emoji'] or ''}{t['name']}" for t in types) or "None", inline=False)
        embed.add_field(name="Message text in transcripts", value="✅ Included" if self.bot.intents.message_content else "⚠️ Off (needs Message Content Intent)", inline=False)
        await interaction.response.send_message(("✅ Saved.\n" if updates else ""), embed=embed, ephemeral=True)

    @app_commands.command(description="Let another role see and answer tickets")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def addstaff(self, interaction: discord.Interaction, role: discord.Role):
        cfg = await self.cfg_or_error(interaction.guild_id)
        if role.is_default():
            raise UserError("Pick a real role, not @everyone.")
        roles = set(staff_ids(cfg))
        roles.add(role.id)
        await db.upsert_ticket_config(interaction.guild_id, staff_roles=",".join(str(r) for r in sorted(roles)))
        await interaction.response.send_message(f"✅ {role.mention} is now ticket staff. It applies to **new** tickets.", ephemeral=True)

    @app_commands.command(description="Remove a staff role from the ticket system")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def removestaff(self, interaction: discord.Interaction, role: discord.Role):
        cfg = await self.cfg_or_error(interaction.guild_id)
        roles = set(staff_ids(cfg))
        if role.id not in roles:
            raise UserError("That role isn't ticket staff.")
        roles.discard(role.id)
        await db.upsert_ticket_config(interaction.guild_id, staff_roles=",".join(str(r) for r in sorted(roles)) or None)
        await interaction.response.send_message(f"✅ Removed {role.mention} from ticket staff.", ephemeral=True)

    @app_commands.command(description="Add or edit a ticket type (shown in the panel)")
    @app_commands.describe(
        name="Type name, e.g. Support or Report",
        emoji="An emoji for it, e.g. 🛠️",
        description="Short description shown in the panel",
        welcome="Message shown at the top of tickets of this type",
        needs_invoice="Ask for an Invoice ID and check it against your shop orders",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def addtype(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 40],
        emoji: Optional[str] = None,
        description: Optional[app_commands.Range[str, 1, 100]] = None,
        welcome: Optional[app_commands.Range[str, 1, 1000]] = None,
        needs_invoice: Optional[bool] = None,
    ):
        await self.cfg_or_error(interaction.guild_id)
        emoji = clean_emoji(emoji)
        existing = await db.fetch_one("SELECT * FROM ticket_types WHERE guild_id = ? AND name = ?", (interaction.guild_id, name.strip()))
        await interaction.response.defer(ephemeral=True)
        if existing:
            await db.execute(
                "UPDATE ticket_types SET emoji = ?, description = ?, welcome = ?, needs_invoice = ? WHERE id = ?",
                (emoji or existing["emoji"], description or existing["description"], welcome or existing["welcome"],
                 existing["needs_invoice"] if needs_invoice is None else int(needs_invoice), existing["id"]),
            )
            verb = "Updated"
        else:
            if len(await self.types_for(interaction.guild_id)) >= MAX_TYPES:
                raise UserError(f"You can have up to {MAX_TYPES} ticket types. Remove one with `/ticket removetype`.")
            await db.execute(
                "INSERT INTO ticket_types (guild_id, name, emoji, description, welcome, needs_invoice) VALUES (?, ?, ?, ?, ?, ?)",
                (interaction.guild_id, name.strip(), emoji, description, welcome, int(bool(needs_invoice))),
            )
            verb = "Added"
        await self.refresh_panel(interaction.guild)
        await interaction.followup.send(f"✅ {verb} the **{name.strip()}** ticket type and refreshed the panel.", ephemeral=True)

    @app_commands.command(description="Remove a ticket type")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def removetype(self, interaction: discord.Interaction, name: str):
        await self.cfg_or_error(interaction.guild_id)
        types = await self.types_for(interaction.guild_id)
        target = next((t for t in types if t["name"].lower() == name.strip().lower()), None)
        if target is None:
            raise UserError("I can't find that ticket type. See `/ticket types`.")
        if len(types) <= 1:
            raise UserError("You need at least one ticket type.")
        await interaction.response.defer(ephemeral=True)
        await db.execute("DELETE FROM ticket_types WHERE id = ?", (target["id"],))
        await self.refresh_panel(interaction.guild)
        await interaction.followup.send(f"🗑️ Removed **{target['name']}** and refreshed the panel. Existing tickets keep working.", ephemeral=True)

    @app_commands.command(description="List your ticket types")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def types(self, interaction: discord.Interaction):
        await self.cfg_or_error(interaction.guild_id)
        types = await self.types_for(interaction.guild_id)
        lines = [f"{t['emoji'] or '🎫'} **{t['name']}**{' · asks for Invoice ID' if t['needs_invoice'] else ''}\n{t['description'] or ''}" for t in types]
        await interaction.response.send_message(embed=ui.card("🎫 Ticket types", "\n\n".join(lines), color=INFO, section="Tickets"), ephemeral=True)

    @app_commands.command(description="Stop someone from opening tickets")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def blacklist(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[app_commands.Range[str, 1, 200]] = None):
        await self.cfg_or_error(interaction.guild_id)
        await db.execute(
            "INSERT OR REPLACE INTO ticket_blacklist (guild_id, user_id, reason, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, member.id, reason, interaction.user.id, discord.utils.utcnow().isoformat()),
        )
        await emit(interaction.guild, "tickets", "Ticket blacklist: added", ui.kv(("👤 Member", f"{member.mention} (`{member.id}`)"), ("📝 Reason", reason or "None given")), color=WARN, actor=interaction.user, subject=member.id)
        await interaction.response.send_message(f"🚫 {member.mention} can no longer open tickets.", ephemeral=True)

    @app_commands.command(description="Let someone open tickets again")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def unblacklist(self, interaction: discord.Interaction, member: discord.Member):
        count = await db.execute("DELETE FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, member.id))
        if not count:
            raise UserError("That member isn't blacklisted.")
        await emit(interaction.guild, "tickets", "Ticket blacklist: removed", ui.kv(("👤 Member", f"{member.mention} (`{member.id}`)")), actor=interaction.user, subject=member.id)
        await interaction.response.send_message(f"✅ {member.mention} can open tickets again.", ephemeral=True)

    # ------------------------------------------------------ ticket commands ----

    @app_commands.command(description="Close this ticket")
    @app_commands.describe(reason="Why it's being closed (optional)")
    async def close(self, interaction: discord.Interaction, reason: Optional[app_commands.Range[str, 1, 300]] = None):
        await self.close_from_interaction(interaction, reason)

    @app_commands.command(description="Claim this ticket (staff)")
    async def claim(self, interaction: discord.Interaction):
        await self.do_claim(interaction)

    @app_commands.command(description="Release your claim on this ticket (staff)")
    async def unclaim(self, interaction: discord.Interaction):
        cfg, ticket = await self.staff_ticket(interaction)
        if not ticket["claimed_by"]:
            raise UserError("Nobody has claimed this ticket.")
        if ticket["claimed_by"] != interaction.user.id and not interaction.user.guild_permissions.manage_guild:
            raise UserError("Only the person who claimed it (or an admin) can release it.")
        await db.execute("UPDATE tickets SET claimed_by = NULL WHERE id = ?", (ticket["id"],))
        ticket = await db.fetch_one("SELECT * FROM tickets WHERE id = ?", (ticket["id"],))
        await self.refresh_controls(interaction.guild, ticket)
        await self.ticket_log(interaction.guild, ticket, "Ticket claim released", interaction.user)
        await interaction.response.send_message(embed=simple("↩️ Claim released", f"{interaction.user.mention} released this ticket. Another staff member can claim it.", INFO))

    @app_commands.command(description="Set this ticket's priority (staff)")
    @app_commands.choices(level=[app_commands.Choice(name=f"{e} {l}", value=k) for k, (e, l) in PRIORITIES.items()])
    async def priority(self, interaction: discord.Interaction, level: app_commands.Choice[str]):
        cfg, ticket = await self.staff_ticket(interaction)
        await db.execute("UPDATE tickets SET priority = ? WHERE id = ?", (level.value, ticket["id"]))
        ticket = await db.fetch_one("SELECT * FROM tickets WHERE id = ?", (ticket["id"],))
        await self.refresh_controls(interaction.guild, ticket)
        await self.ticket_log(interaction.guild, ticket, "Ticket priority changed", interaction.user, ("🚦 Priority", level.name))
        await interaction.response.send_message(embed=simple("🚦 Priority changed", f"Priority is now **{level.name}**.", COLOR))

    @app_commands.command(description="Add someone to this ticket (staff)")
    async def add(self, interaction: discord.Interaction, member: discord.Member):
        cfg, ticket = await self.staff_ticket(interaction)
        await interaction.channel.set_permissions(
            member, overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True), reason=f"Added by {interaction.user}"
        )
        await self.ticket_log(interaction.guild, ticket, "Member added to ticket", interaction.user, ("➕ Added", f"{member.mention} (`{member.id}`)"))
        await interaction.response.send_message(embed=simple("➕ Member added", f"{member.mention} can now see and reply in this ticket.", COLOR))

    @app_commands.command(description="Remove someone from this ticket (staff)")
    async def remove(self, interaction: discord.Interaction, member: discord.Member):
        cfg, ticket = await self.staff_ticket(interaction)
        if member.id == ticket["user_id"]:
            raise UserError("You can't remove the person who opened the ticket. Close it instead.")
        if is_staff(member, cfg):
            raise UserError("That member is ticket staff, so they get access through their role.")
        await interaction.channel.set_permissions(member, overwrite=None, reason=f"Removed by {interaction.user}")
        await self.ticket_log(interaction.guild, ticket, "Member removed from ticket", interaction.user, ("➖ Removed", f"{member.mention} (`{member.id}`)"))
        await interaction.response.send_message(embed=simple("➖ Member removed", f"{member.mention} can no longer see this ticket.", WARN))

    @app_commands.command(description="Rename this ticket channel (staff)")
    @app_commands.describe(name="New name (letters, numbers and dashes)")
    async def rename(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 40]):
        cfg, ticket = await self.staff_ticket(interaction)
        new_name = f"ticket-{ticket['number']:04d}-{slugify(name, 'renamed', 30)}"
        await interaction.response.defer()
        try:
            await asyncio.wait_for(interaction.channel.edit(name=new_name, reason=f"Renamed by {interaction.user}"), timeout=8)
        except asyncio.TimeoutError:
            raise UserError("Discord limits channel renames to 2 every 10 minutes. Please try again a bit later.") from None
        await interaction.followup.send(embed=simple("✏️ Renamed", f"This ticket is now `{new_name}`.", COLOR))

    @app_commands.command(description="Show details about this ticket")
    async def info(self, interaction: discord.Interaction):
        ticket = await self.ticket_here(interaction)
        embed = await self.render_embed(interaction.guild, ticket)
        embed.add_field(name="Opened", value=discord.utils.format_dt(parse_iso(ticket["created_at"]), "R"))
        embed.add_field(name="Last activity", value=discord.utils.format_dt(parse_iso(ticket["last_activity"]), "R"))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Save a transcript of this ticket without closing it (staff)")
    async def transcript(self, interaction: discord.Interaction):
        cfg, ticket = await self.staff_ticket(interaction)
        await interaction.response.defer(ephemeral=True)
        data, count = await self.build_transcript(interaction.guild, interaction.channel, ticket)
        await interaction.followup.send(
            f"📄 Transcript of {count} message(s). Open the file in a web browser.",
            file=discord.File(io.BytesIO(data), filename=f"transcript-{ticket['number']:04d}.html"), ephemeral=True,
        )

    @app_commands.command(description="List tickets (staff)")
    @app_commands.describe(status="Which tickets to show (default: open)", member="Only this member's tickets")
    @app_commands.choices(status=[app_commands.Choice(name="Open", value="open"), app_commands.Choice(name="Closed", value="closed"), app_commands.Choice(name="All", value="all")])
    async def list(self, interaction: discord.Interaction, status: Optional[app_commands.Choice[str]] = None, member: Optional[discord.Member] = None):
        cfg = await self.cfg_or_error(interaction.guild_id)
        if not is_staff(interaction.user, cfg):
            raise UserError("Only ticket staff can do that.")
        wanted = status.value if status else "open"
        sql, params = "SELECT * FROM tickets WHERE guild_id = ?", [interaction.guild_id]
        if wanted != "all":
            sql += " AND status = ?"
            params.append(wanted)
        if member:
            sql += " AND user_id = ?"
            params.append(member.id)
        rows = await db.fetch_all(sql + " ORDER BY id DESC LIMIT 15", tuple(params))
        if not rows:
            raise UserError("No tickets found.")
        lines = []
        for t in rows:
            where = f"<#{t['channel_id']}>" if t["status"] == "open" and t["channel_id"] else "closed"
            claimed = f" · 🙋 <@{t['claimed_by']}>" if t["claimed_by"] else ""
            lines.append(f"{PRIORITIES.get(t['priority'], PRIORITIES['normal'])[0]} `{number_label(t)}` {where} · {t['type_name']} · <@{t['user_id']}>{claimed}")
        await interaction.response.send_message(embed=ui.card(f"🎫 Tickets ({wanted})", "\n".join(lines), color=INFO, section="Tickets"), ephemeral=True)

    @app_commands.command(description="Ticket statistics (staff)")
    async def stats(self, interaction: discord.Interaction):
        cfg = await self.cfg_or_error(interaction.guild_id)
        if not is_staff(interaction.user, cfg):
            raise UserError("Only ticket staff can do that.")
        rows = await db.fetch_all("SELECT * FROM tickets WHERE guild_id = ?", (interaction.guild_id,))
        if not rows:
            raise UserError("No tickets yet.")
        now = discord.utils.utcnow()
        open_rows = [t for t in rows if t["status"] == "open"]
        closed = [t for t in rows if t["status"] == "closed"]
        week = [t for t in rows if parse_iso(t["created_at"]) >= now - timedelta(days=7)]
        durations = [(parse_iso(t["closed_at"]) - parse_iso(t["created_at"])).total_seconds() for t in closed if t["closed_at"]]
        responses = [(parse_iso(t["first_response_at"]) - parse_iso(t["created_at"])).total_seconds() for t in rows if t["first_response_at"]]
        ratings = [t["rating"] for t in rows if t["rating"]]
        by_type = Counter(t["type_name"] for t in rows).most_common(5)
        claimers = Counter(t["claimed_by"] for t in rows if t["claimed_by"]).most_common(3)

        embed = ui.card("📊 Ticket stats", color=INFO, section="Tickets")
        embed.add_field(name="Open now", value=str(len(open_rows)))
        embed.add_field(name="Opened this week", value=str(len(week)))
        embed.add_field(name="All time", value=str(len(rows)))
        embed.add_field(name="Avg first response", value=fmt_duration(sum(responses) / len(responses)) if responses else "n/a")
        embed.add_field(name="Avg time to close", value=fmt_duration(sum(durations) / len(durations)) if durations else "n/a")
        embed.add_field(name="Avg rating", value=f"{sum(ratings) / len(ratings):.1f}/5 ({len(ratings)} ratings)" if ratings else "n/a")
        embed.add_field(name="Top types", value="\n".join(f"{n}: {c}" for n, c in by_type) or "n/a", inline=False)
        if claimers:
            embed.add_field(name="Most claims", value="\n".join(f"<@{u}>: {c}" for u, c in claimers), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
