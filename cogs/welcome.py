import asyncio
import io
import logging
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from cogs.pages import check_attachment, drop_image, store_image
from common import ACCENT, CARD_ACCENT_HEX, COLOR, DANGER, INFO, SUCCESS, UserError, WARN, check_can_send, check_role, compact, ordinal, parse_color, render
from welcomecard import render_card

log = logging.getLogger("verification-bot")

DEFAULT_JOIN = "Welcome to **{server}**, {user}! We're glad you're here. 🎉"
DEFAULT_LEAVE = "**{user}** has left the server. 👋"
PLACEHOLDERS = "Placeholders: `{user}` `{username}` `{server}` `{count}` `{ordinal}`"
STYLES = {"card": "Image card (banner with avatar)", "embed": "Embed", "text": "Plain text"}
BANNERS = {
    "card": "Image card with their avatar",
    "card_banner": "Image card on your server banner",
    "server_banner": "Your server banner",
    "image": "Your own picture (upload or link)",
    "none": "No picture",
}
MAX_BG_BYTES = 8_000_000


def style_of(w) -> str:
    return w["style"] or ("embed" if w["use_embed"] else "text")


def accent_hex(w) -> str:
    """Colour used for the banner image (a custom colour if the admin set one)."""
    return w["embed_color"] or CARD_ACCENT_HEX


def embed_color(w) -> int:
    """Colour of the embed's side bar: the admin's custom colour, otherwise the theme (black)."""
    return int(w["embed_color"], 16) if w["embed_color"] else ACCENT.value


def link_view(guild: discord.Guild, w) -> Optional[discord.ui.View]:
    """Link buttons under the welcome message (Rules / Start here)."""
    buttons = []
    if w["rules_channel_id"]:
        buttons.append(("Rules", "📜", w["rules_channel_id"]))
    if w["extra_channel_id"]:
        buttons.append((w["extra_label"] or "Start here", "🚀", w["extra_channel_id"]))
    if not buttons:
        return None
    view = discord.ui.View(timeout=None)
    for label, emoji, channel_id in buttons:
        view.add_item(discord.ui.Button(
            style=discord.ButtonStyle.link, label=label, emoji=emoji,
            url=f"https://discord.com/channels/{guild.id}/{channel_id}",
        ))
    return view


class MessageModal(discord.ui.Modal):
    def __init__(self, w):
        super().__init__(title="Welcome message")
        self.message_input = discord.ui.TextInput(
            label="Message", style=discord.TextStyle.paragraph, max_length=1500,
            placeholder="Use {user} {username} {server} {count} {ordinal}",
            default=((w["message"] if w else None) or DEFAULT_JOIN)[:1500],
        )
        self.title_input = discord.ui.TextInput(
            label="Title (embed and card styles)", max_length=100, required=False,
            default=((w["embed_title"] if w else None) or "Welcome!")[:100],
        )
        self.add_item(self.message_input)
        self.add_item(self.title_input)

    async def on_submit(self, interaction: discord.Interaction):
        await db.upsert_welcome(
            interaction.guild_id, message=self.message_input.value.strip(), embed_title=self.title_input.value.strip() or None
        )
        await interaction.response.send_message(embed=ui.card("✅ Welcome message saved", "Use `/welcome test` to preview it.", color=SUCCESS), ephemeral=True)


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Welcome(commands.GroupCog, group_name="welcome", group_description="Welcome cards, goodbyes, DMs and auto-roles"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bg_cache: dict[str, bytes] = {}
        super().__init__()

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def cog_unload(self):
        await self.session.close()

    # --------------------------------------------------------- building ----

    async def fetch_bytes(self, url: str) -> Optional[bytes]:
        if url in self.bg_cache:
            return self.bg_cache[url]
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.content.read(MAX_BG_BYTES + 1)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
        if len(data) > MAX_BG_BYTES:
            return None
        if len(self.bg_cache) >= 10:
            self.bg_cache.pop(next(iter(self.bg_cache)))
        self.bg_cache[url] = data
        return data

    async def make_card(self, member: discord.Member, w, background: Optional[bytes] = None) -> discord.File:
        avatar = await member.display_avatar.replace(size=256, format="png").read()
        if background is None and w["bg_url"]:
            background = await self.fetch_bytes(w["bg_url"])
        png = await asyncio.to_thread(
            render_card, avatar, member.display_name, member.guild.name,
            f"Member #{member.guild.member_count:,}", accent_hex(w), background,
        )
        return discord.File(png, filename="welcome.png")

    async def build_welcome(self, member: discord.Member, w) -> dict:
        """Returns keyword arguments for channel.send(): content / embed / file / view."""
        guild = member.guild
        text = render(w["message"] or DEFAULT_JOIN, member)
        view = link_view(guild, w)
        style = style_of(w)
        if style == "text":
            return compact(content=text, view=view)

        embed = ui.card(
            render(w["embed_title"] or "Welcome!", member)[:256], text, color=embed_color(w),
            guild=guild, footer=guild.name, author=(f"{member.display_name} joined the server", member.display_avatar.url),
        )
        embed.add_field(name="👥 Member", value=f"**{ordinal(guild.member_count or 0)}**", inline=True)
        embed.add_field(name="📅 Account created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        start_here = []
        if w["rules_channel_id"]:
            start_here.append(f"📜 Read the rules in <#{w['rules_channel_id']}>")
        if w["extra_channel_id"]:
            start_here.append(f"🚀 {w['extra_label'] or 'Start here'}: <#{w['extra_channel_id']}>")
        if start_here:
            embed.add_field(name="📍 Get started", value="\n".join(start_here), inline=False)

        file, image_ref = await self.banner_for(member, w, style)
        if image_ref:
            embed.set_image(url=image_ref)
        if file is None or file.filename != "welcome.png":
            embed.set_thumbnail(url=member.display_avatar.url)  # the card already shows the avatar, so only add it for other pictures
        return compact(embed=embed, file=file, view=view)

    def banner_mode(self, w, style: str) -> str:
        """Which picture to use. An explicit /welcome banner choice wins; otherwise the old style setting decides."""
        if w["banner_mode"]:
            return w["banner_mode"]
        if style == "embed":
            return "image" if w["image_url"] else "none"
        return "card"

    async def banner_for(self, member: discord.Member, w, style: str):
        """Returns (file to attach or None, the picture reference for the embed or None)."""
        guild = member.guild
        mode = self.banner_mode(w, style)
        server_banner = guild.banner.url if getattr(guild, "banner", None) else None
        if mode == "none":
            return None, None
        if mode == "server_banner" and server_banner:
            return None, server_banner
        if mode == "image":
            stored = await db.fetch_one("SELECT filename, data FROM stored_files WHERE id = ?", (w["banner_file_id"],)) if w["banner_file_id"] else None
            if stored:
                return discord.File(io.BytesIO(bytes(stored["data"])), filename=stored["filename"]), f"attachment://{stored['filename']}"
            return None, (w["banner_url"] or (w["image_url"] if style == "embed" else None))
        # card, card_banner, or a server banner that doesn't exist yet: the generated image card
        background = await self.fetch_bytes(server_banner) if mode == "card_banner" and server_banner else None
        try:
            return await self.make_card(member, w, background), "attachment://welcome.png"
        except Exception:
            log.exception("Couldn't render the welcome card, falling back to a plain embed")
            return None, None

    def build_leave(self, member: discord.Member, w) -> discord.Embed:
        return ui.card(
            None, render(w["leave_message"] or DEFAULT_LEAVE, member, mention=False), color=COLOR,
            author=(f"{member.display_name} left", member.display_avatar.url),
            footer=f"{ui.plural(member.guild.member_count or 0, 'member')} now · {member.guild.name}",
        )

    def build_dm(self, member: discord.Member, w) -> dict:
        guild = member.guild
        embed = ui.card(
            f"Welcome to {guild.name}!", render(w["dm_message"], member), color=embed_color(w),
            guild=guild, footer=guild.name, thumbnail=guild.icon.url if guild.icon else None,
        )
        return compact(embed=embed, view=link_view(guild, w))

    # ----------------------------------------------------------- events ----

    async def greet(self, member: discord.Member, w) -> None:
        if w["enabled"] and w["channel_id"]:
            channel = member.guild.get_channel(w["channel_id"])
            if channel is not None:
                try:
                    kwargs = await self.build_welcome(member, w)
                    await channel.send(**kwargs, allowed_mentions=discord.AllowedMentions(users=[member]))
                except discord.HTTPException:
                    log.warning("Couldn't send the welcome message in %s", member.guild)
        if w["dm_enabled"] and w["dm_message"]:
            try:
                await member.send(**self.build_dm(member, w))
            except discord.HTTPException:
                pass  # the member has DMs closed

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        w = await db.get_welcome(member.guild.id)
        if not w:
            return
        role_id = w["botrole_id"] if member.bot else w["autorole_id"]
        role = member.guild.get_role(role_id) if role_id else None
        if role:
            try:
                await member.add_roles(role, reason="Auto-role on join")
            except discord.HTTPException:
                pass
        if not member.bot and (w["send_on"] or "join") == "join":
            await self.greet(member, w)

    @commands.Cog.listener()
    async def on_member_verified(self, member: discord.Member):
        w = await db.get_welcome(member.guild.id)
        if w and w["send_on"] == "verified":
            await self.greet(member, w)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        w = await db.get_welcome(member.guild.id)
        if not w or not w["leave_enabled"] or not w["leave_channel_id"]:
            return
        channel = member.guild.get_channel(w["leave_channel_id"])
        if channel is not None:
            try:
                await channel.send(embed=self.build_leave(member, w))
            except discord.HTTPException:
                pass

    # --------------------------------------------------------- commands ----

    @app_commands.command(description="Turn on welcome messages and choose where they go")
    @app_commands.describe(
        channel="Where welcome messages go (turns the welcome on)",
        enabled="Turn the welcome message on or off",
        send_on="When to send it (default: as soon as they join)",
        color="Accent colour as hex, e.g. #5865F2",
    )
    @app_commands.choices(send_on=[
        app_commands.Choice(name="As soon as they join", value="join"),
        app_commands.Choice(name="After they verify", value="verified"),
    ])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def settings(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        enabled: Optional[bool] = None,
        send_on: Optional[app_commands.Choice[str]] = None,
        color: Optional[str] = None,
    ):
        existing = await db.get_welcome(interaction.guild_id)
        updates: dict = {}
        if existing is None:
            updates["style"] = "card"  # new setups start with the image card
        if channel:
            check_can_send(channel, interaction.guild.me, files=True)
            updates["channel_id"] = channel.id
            if enabled is None:
                enabled = True
        if enabled is not None:
            updates["enabled"] = int(enabled)
        if send_on:
            updates["send_on"] = send_on.value
        if color:
            updates["embed_color"] = f"{parse_color(color):06X}"
        if not any(k != "style" for k in updates):
            raise UserError("Nothing to change. Fill in at least one option.")

        await db.upsert_welcome(interaction.guild_id, **updates)
        w = await db.get_welcome(interaction.guild_id)
        notes = []
        if w["enabled"] and not w["channel_id"]:
            notes.append("⚠️ Pick a channel too, or nothing will be sent.")
        notes.append("**Next:** `/welcome message` to write it · `/welcome banner` for the picture · `/welcome style` for the look · `/welcome test` to preview.")
        await interaction.response.send_message(embed=ui.card("✅ Welcome settings saved", "\n".join(notes), color=SUCCESS, guild=interaction.guild, section="Welcome"), ephemeral=True)

    @app_commands.command(description="Write the welcome message in a pop-up (multi-line supported)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def message(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MessageModal(await db.get_welcome(interaction.guild_id)))

    @app_commands.command(description="Change the look: image card, embed or text, plus buttons")
    @app_commands.describe(
        look="Image card, embed or plain text",
        background_url="Custom background image for the card (direct https link, or 'none')",
        image_url="Banner image for the embed look (direct https link, or 'none')",
        rules_channel="Adds a 📜 Rules button linking to this channel",
        extra_channel="Adds a second button linking to this channel (e.g. #roles)",
        extra_label="Text for that second button (default: Start here)",
    )
    @app_commands.choices(look=[app_commands.Choice(name=v, value=k) for k, v in STYLES.items()])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def style(
        self,
        interaction: discord.Interaction,
        look: Optional[app_commands.Choice[str]] = None,
        background_url: Optional[str] = None,
        image_url: Optional[str] = None,
        rules_channel: Optional[discord.TextChannel] = None,
        extra_channel: Optional[discord.TextChannel] = None,
        extra_label: Optional[app_commands.Range[str, 1, 30]] = None,
    ):
        def url_or_clear(value: str):
            if value.strip().lower() == "none":
                return None
            if not value.startswith("https://") or " " in value:
                raise UserError("Images must be direct links starting with `https://` (or type `none` to remove).")
            return value

        updates: dict = {}
        if look:
            updates["style"] = look.value
        if background_url:
            updates["bg_url"] = url_or_clear(background_url)
        if image_url:
            updates["image_url"] = url_or_clear(image_url)
        if rules_channel:
            updates["rules_channel_id"] = rules_channel.id
        if extra_channel:
            updates["extra_channel_id"] = extra_channel.id
        if extra_label:
            updates["extra_label"] = extra_label
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option.")
        await db.upsert_welcome(interaction.guild_id, **updates)
        await interaction.response.send_message(embed=ui.card("✅ Style saved", "Use `/welcome test` to see it.", color=SUCCESS, guild=interaction.guild, section="Welcome"), ephemeral=True)

    @app_commands.command(description="Choose the welcome picture: card, your server banner, or your own image")
    @app_commands.describe(
        mode="Which picture to show",
        image="Upload your own picture (stored safely in the database)",
        image_url="Or a direct https link to a picture",
    )
    @app_commands.choices(mode=[app_commands.Choice(name=v, value=k) for k, v in BANNERS.items()])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def banner(self, interaction: discord.Interaction, mode: Optional[app_commands.Choice[str]] = None, image: Optional[discord.Attachment] = None, image_url: Optional[str] = None):
        guild = interaction.guild
        w = await db.get_welcome(guild.id)
        chosen = mode.value if mode else ("image" if (image or image_url) else None)
        if chosen is None:
            raise UserError("Pick a **mode**, or give an **image** or **image_url**.")
        updates: dict = {"banner_mode": chosen}
        if image is not None:
            await check_attachment(image)
        if image_url and (not image_url.startswith("https://") or " " in image_url):
            raise UserError("The picture link must be a direct link starting with `https://`.")
        if chosen == "image":
            if image is None and not image_url and not (w and (w["banner_file_id"] or w["banner_url"])):
                raise UserError("For **your own picture**, upload one in `image` or give a link in `image_url`.")
            if image is not None:
                if w and w["banner_file_id"]:
                    await drop_image(w["banner_file_id"])
                updates.update(banner_file_id=await store_image(guild.id, interaction.user.id, image), banner_url=None)
            elif image_url:
                if w and w["banner_file_id"]:
                    await drop_image(w["banner_file_id"])
                updates.update(banner_file_id=None, banner_url=image_url)
        warn = ""
        if chosen in ("server_banner", "card_banner") and not getattr(guild, "banner", None):
            warn = "\n\n⚠️ Your server has **no banner** yet (it needs Server Boost Level 2), so the image card is used until you add one."
        await db.upsert_welcome(guild.id, **updates)
        await interaction.response.send_message(embed=ui.card("✅ Welcome picture saved", f"**{BANNERS[chosen]}**.{warn}\n\nUse `/welcome test` to preview it.", color=SUCCESS, guild=guild, section="Welcome"), ephemeral=True)

    @app_commands.command(description="Set up the goodbye message when someone leaves")
    @app_commands.describe(channel="Where goodbye messages go (turns it on)", message="Text. Placeholders as above", enabled="Turn it on or off")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def leave(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        message: Optional[app_commands.Range[str, 1, 1000]] = None,
        enabled: Optional[bool] = None,
    ):
        updates: dict = {}
        if channel:
            check_can_send(channel, interaction.guild.me)
            updates["leave_channel_id"] = channel.id
            if enabled is None:
                enabled = True
        if enabled is not None:
            updates["leave_enabled"] = int(enabled)
        if message:
            updates["leave_message"] = message
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option.")
        await db.upsert_welcome(interaction.guild_id, **updates)
        w = await db.get_welcome(interaction.guild_id)
        extra = "\n⚠️ Pick a channel too, or nothing will be sent." if w["leave_enabled"] and not w["leave_channel_id"] else ""
        await interaction.response.send_message(embed=ui.card("✅ Goodbye settings saved", f"{extra.strip()}\n{PLACEHOLDERS}".strip(), color=SUCCESS, guild=interaction.guild, section="Welcome"), ephemeral=True)

    @app_commands.command(description="Send new members a private welcome DM")
    @app_commands.describe(enabled="Turn the DM on or off", message="DM text. Placeholders as above")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def dm(self, interaction: discord.Interaction, enabled: bool, message: Optional[app_commands.Range[str, 1, 1500]] = None):
        updates: dict = {"dm_enabled": int(enabled)}
        if message:
            updates["dm_message"] = message
        await db.upsert_welcome(interaction.guild_id, **updates)
        w = await db.get_welcome(interaction.guild_id)
        extra = "\n⚠️ Add a `message` too, or nothing will be sent." if w["dm_enabled"] and not w["dm_message"] else ""
        await interaction.response.send_message(embed=ui.card(f"✅ Welcome DM {'on' if enabled else 'off'}", f"{extra.strip()}\n{PLACEHOLDERS}".strip(), color=SUCCESS, guild=interaction.guild, section="Welcome"), ephemeral=True)

    @app_commands.command(description="Give roles automatically when someone joins")
    @app_commands.describe(members="Role for new human members", bots="Role for new bots", clear="Remove both auto-roles")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def autorole(
        self,
        interaction: discord.Interaction,
        members: Optional[discord.Role] = None,
        bots: Optional[discord.Role] = None,
        clear: bool = False,
    ):
        if clear:
            await db.upsert_welcome(interaction.guild_id, autorole_id=None, botrole_id=None)
            return await interaction.response.send_message(embed=ui.card("🧹 Auto-roles cleared", "New members won't get a role automatically.", color=SUCCESS, section="Welcome"), ephemeral=True)
        updates: dict = {}
        if members:
            check_role(interaction, members)
            updates["autorole_id"] = members.id
        if bots:
            check_role(interaction, bots)
            updates["botrole_id"] = bots.id
        if not updates:
            raise UserError("Pick a role for `members` and/or `bots`.")
        await db.upsert_welcome(interaction.guild_id, **updates)
        await interaction.response.send_message(embed=ui.card("✅ Auto-roles saved", "New members and bots get their roles when they join.", color=SUCCESS, section="Welcome"), ephemeral=True)

    @app_commands.command(description="Preview your welcome, goodbye and DM (only you see it)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test(self, interaction: discord.Interaction):
        w = await db.get_welcome(interaction.guild_id)
        if not w:
            raise UserError("Nothing set up yet. Start with `/welcome settings`.")
        await interaction.response.defer(ephemeral=True)
        kwargs = await self.build_welcome(interaction.user, w)
        header = "**👋 Join message preview**"
        kwargs["content"] = f"{header}\n{kwargs['content']}" if "content" in kwargs else header
        await interaction.followup.send(ephemeral=True, **kwargs)
        await interaction.followup.send("**🚪 Goodbye preview**", embed=self.build_leave(interaction.user, w), ephemeral=True)
        if w["dm_message"]:
            await interaction.followup.send("**✉️ Welcome DM preview**", ephemeral=True, **self.build_dm(interaction.user, w))

    @app_commands.command(description="Show the current welcome settings")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(self, interaction: discord.Interaction):
        w = await db.get_welcome(interaction.guild_id)
        if not w:
            raise UserError("Nothing set up yet. Start with `/welcome settings`.")
        guild = interaction.guild

        def chan(cid):
            return f"<#{cid}>" if cid else "Not set"

        def role(rid):
            r = guild.get_role(rid) if rid else None
            return r.mention if r else "None"

        buttons = []
        if w["rules_channel_id"]:
            buttons.append(f"📜 Rules → {chan(w['rules_channel_id'])}")
        if w["extra_channel_id"]:
            buttons.append(f"🚀 {w['extra_label'] or 'Start here'} → {chan(w['extra_channel_id'])}")
        embed = ui.card(
            "👋 Welcome settings",
            ui.kv(
                ("Join message", f"{'✅ On' if w['enabled'] else '❌ Off'} in {chan(w['channel_id'])}"),
                ("Sent", "After verifying" if w["send_on"] == "verified" else "As soon as they join"),
                ("Look", STYLES[style_of(w)]), ("Picture", BANNERS[w["banner_mode"] or "card"] if style_of(w) != "text" else None),
                ("Goodbye", f"{'✅ On' if w['leave_enabled'] else '❌ Off'} in {chan(w['leave_channel_id'])}"),
                ("Welcome DM", "✅ On" if w["dm_enabled"] else "❌ Off"),
                ("Auto-roles", f"members {role(w['autorole_id'])} · bots {role(w['botrole_id'])}"),
                ("Buttons", ", ".join(buttons) if buttons else "None"),
            ),
            color=embed_color(w), guild=guild, section="Welcome",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Turn off all welcome, goodbye and DM messages")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction):
        await db.upsert_welcome(interaction.guild_id, enabled=0, leave_enabled=0, dm_enabled=0)
        await interaction.response.send_message(embed=ui.card("🛑 Welcome messages are off", "Welcome, goodbye and DM messages are off. Your text and pictures are saved if you turn them back on.", color=WARN, section="Welcome"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
