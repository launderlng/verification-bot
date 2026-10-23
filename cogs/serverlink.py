import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import COLOR, INFO, UserError
from logutil import emit

log = logging.getLogger("verification-bot")

REASON_CREATE = "Mirrored from the linked main server"
REASON_UPDATE = "Renamed/moved on the linked main server"
REASON_DELETE = "Removed: deleted on the linked main server"
MAX_MIRROR_ITEMS = 150  # keeps the first sync inside Discord's 15 minute reply window
MIRRORABLE = (discord.TextChannel, discord.VoiceChannel)
MAX_FORWARD_FILES = 10


def _is_normal(message: discord.Message) -> bool:
    """Real messages and replies only (not joins, pins, boosts and other system messages)."""
    return getattr(getattr(message, "type", None), "name", "default") in ("default", "reply")


def simple(title: str, text: str, color: discord.Color = INFO) -> discord.Embed:
    return ui.card(title, text, color=color, section="Server link")


class CategoryGifModal(discord.ui.Modal, title="🎬 Branding GIF"):
    """Opened from CategoryLinkView's GIF button -- a single optional field, kept separate from the picker's
    select/buttons since Discord can't put a text input directly on a view."""

    gif_url = discord.ui.TextInput(label="GIF URL (optional)", placeholder="https://....gif", required=False, max_length=512)

    def __init__(self, view: "CategoryLinkView"):
        super().__init__()
        self.view_ref = view
        self.gif_url.default = view.gif_url

    async def on_submit(self, interaction: discord.Interaction):
        self.view_ref.gif_url = self.gif_url.value.strip() or None
        self.view_ref.gif_btn.label = "🎬 GIF set ✅" if self.view_ref.gif_url else "🎬 Add branding GIF"
        self.view_ref.gif_btn.style = discord.ButtonStyle.success if self.view_ref.gif_url else discord.ButtonStyle.secondary
        if interaction.message is not None:
            await interaction.response.edit_message(view=self.view_ref)
        else:
            await interaction.response.send_message(
                "🎬 GIF saved — it'll be attached to auto-posted drops." if self.view_ref.gif_url else "GIF cleared.",
                ephemeral=True,
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Branding GIF modal error", exc_info=error)


class CategoryLinkView(discord.ui.View):
    """/linkserver's category picker: a dropdown of the main server's categories (plus a "whole server"
    option) so staff choose exactly what gets mirrored, instead of always linking everything. Also offers,
    for the same picked scope, turning each mirrored channel into an auto-upload drop zone (file dropped in
    the vault/staff channel -> stored + auto-posted with a Get-file button on the main server), optionally
    branded with a GIF -- combining what used to be two separate setup steps (/linkserver then /autoupload
    bulkadd) into one."""

    def __init__(self, cog: "ServerLink", invoker_id: int, main_guild: discord.Guild, target_guild: discord.Guild):
        super().__init__(timeout=180)
        self.cog = cog
        self.invoker_id = invoker_id
        self.main_guild = main_guild
        self.target_guild = target_guild
        self.message: Optional[discord.Message] = None
        self.autopost_enabled = False
        self.gif_url: Optional[str] = None

        cats = main_guild.categories[:24]  # Discord caps a select at 25 options — leave one for "whole server"
        options = [discord.SelectOption(label="🌐 Mirror the whole server", value="__ALL__",
                                        description="Every category and channel, including uncategorized ones")]
        options += [discord.SelectOption(label=c.name[:100], value=str(c.id)) for c in cats]
        select = discord.ui.Select(placeholder="Choose categories to mirror (or the whole server)…",
                                   min_values=1, max_values=len(options), options=options, row=0)
        select.callback = self._on_select
        self.select = select
        self.add_item(select)

        autopost_btn = discord.ui.Button(label="📤 Also auto-post dropped files", style=discord.ButtonStyle.secondary, row=1)
        autopost_btn.callback = self._on_toggle_autopost
        self.autopost_btn = autopost_btn
        self.add_item(autopost_btn)

        gif_btn = discord.ui.Button(label="🎬 Add branding GIF", style=discord.ButtonStyle.secondary, row=1)
        gif_btn.callback = self._on_set_gif
        self.gif_btn = gif_btn
        self.add_item(gif_btn)

        confirm_btn = discord.ui.Button(label="Link it", style=discord.ButtonStyle.success, emoji="🔗", disabled=True, row=2)
        confirm_btn.callback = self._on_confirm
        self.confirm_btn = confirm_btn
        self.add_item(confirm_btn)

        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=2)
        cancel_btn.callback = self._on_cancel
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message("Only the admin who ran `/linkserver` can use this.", ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        self.confirm_btn.disabled = False
        if "__ALL__" in self.select.values:
            self.confirm_btn.label = "Link whole server"
        else:
            n = len(self.select.values)
            self.confirm_btn.label = f"Link {n} categor{'y' if n == 1 else 'ies'}"
        await interaction.response.edit_message(view=self)

    async def _on_toggle_autopost(self, interaction: discord.Interaction):
        self.autopost_enabled = not self.autopost_enabled
        self.autopost_btn.style = discord.ButtonStyle.success if self.autopost_enabled else discord.ButtonStyle.secondary
        self.autopost_btn.label = "✅ Auto-posting dropped files" if self.autopost_enabled else "📤 Also auto-post dropped files"
        await interaction.response.edit_message(view=self)

    async def _on_set_gif(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CategoryGifModal(self))

    async def _on_cancel(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Cancelled — nothing was linked.", view=self)
        self.stop()

    async def _on_confirm(self, interaction: discord.Interaction):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="🔄 Linking and syncing…", view=self)

        values = self.select.values
        category_ids = None if "__ALL__" in values else {int(v) for v in values if v.isdigit()}

        if self.target_guild.id in self.cog.syncing:
            await interaction.edit_original_response(content="A sync is already running here. Wait for it to finish.", view=None)
            self.stop()
            return
        try:
            embed = await self.cog._run_sync(self.target_guild, self.main_guild, category_ids, self.invoker_id)
            if self.autopost_enabled:
                wired = await self.cog._wire_autopost(self.target_guild, self.main_guild, category_ids, self.gif_url, self.invoker_id)
                embed.add_field(
                    name="📤 Auto-posting enabled",
                    value=(
                        f"Files dropped in **{wired}** linked channel{'s' if wired != 1 else ''} here will also be stored and "
                        "auto-posted with a **Get file** button on the main server"
                        + (", branded with your GIF." if self.gif_url else ".")
                    ) if wired else "No text channels ended up mirrored in this scope, so nothing was wired up as a drop zone.",
                    inline=False,
                )
        except UserError as e:
            await interaction.edit_original_response(content=f"⚠️ {e}", view=None)
            self.stop()
            return
        await interaction.edit_original_response(content=None, embed=embed, view=None)
        self.stop()

    async def on_timeout(self):
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.exception("Server-link category picker error", exc_info=error)
        msg = f"⚠️ {error}" if isinstance(error, UserError) else "Something went wrong linking that. Try `/linkserver` again."
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(content=msg, embed=None, view=None)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass
        self.cog.syncing.discard(self.target_guild.id)
        self.stop()


@app_commands.guild_only()
class ServerLink(commands.Cog):
    """/linkserver: mirror a main server's channels into this (second, "vault") server, live. You pick which
    categories to mirror (or the whole server) from a dropdown; after the first catch-up sync, any channel
    created, renamed, moved or deleted on the main server automatically happens here too, scoped to whatever
    was picked — channels made directly here are never touched or copied back. CONTENT forwarding, though,
    goes both ways: a message posted in a mirrored vault channel is forwarded into the matching channel on
    the main server, and a message posted in a mirrored main-server channel is forwarded back into the
    matching vault channel too — each copy is tagged with who sent it and which server it came from. The same
    picker also has an optional **📤 Also auto-post dropped files** toggle (with an optional **🎬 branding
    GIF**) that additionally wires every mirrored channel as an /autoupload drop zone, so a file dropped in a
    vault channel is also stored and auto-posted with a Get-file button on the matching main-server channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.syncing: set[int] = set()

    # ------------------------------------------------------------ mirroring ----

    async def _ensure_category(self, target_guild: discord.Guild, main_category: Optional[discord.CategoryChannel]) -> Optional[discord.CategoryChannel]:
        if main_category is None:
            return None
        mapped_id = await db.get_target_channel_id(target_guild.id, main_category.id)
        if mapped_id:
            existing = target_guild.get_channel(mapped_id)
            if existing is not None:
                return existing
        by_name = next((c for c in target_guild.categories if c.name.lower() == main_category.name.lower()), None)
        if by_name is not None:
            await db.set_channel_map(target_guild.id, main_category.id, by_name.id)
            return by_name
        created = await target_guild.create_category(main_category.name, reason=REASON_CREATE)
        await db.set_channel_map(target_guild.id, main_category.id, created.id)
        return created

    async def _mirror_channel(self, target_guild: discord.Guild, channel) -> Optional[discord.abc.GuildChannel]:
        if isinstance(channel, discord.CategoryChannel):
            return await self._ensure_category(target_guild, channel)
        if not isinstance(channel, MIRRORABLE):
            return None  # forum/stage/announcement channels aren't mirrored, to keep this simple and reliable
        parent = await self._ensure_category(target_guild, channel.category)

        # Already got a same-name channel sitting there (made by hand before this was linked)? Link to that
        # instead of creating a duplicate — only genuinely missing channels get created.
        mapped_target_ids = set((await db.get_channel_map(target_guild.id)).values())
        pool = parent.channels if parent is not None else [
            c for c in target_guild.channels if isinstance(c, MIRRORABLE) and c.category_id is None
        ]
        by_name = next(
            (c for c in pool if c.id not in mapped_target_ids
             and type(c) is type(channel) and c.name.lower() == channel.name.lower()),
            None,
        )
        if by_name is not None:
            await db.set_channel_map(target_guild.id, channel.id, by_name.id)
            return by_name

        kwargs = {"category": parent, "reason": REASON_CREATE}
        if isinstance(channel, discord.TextChannel):
            if channel.topic:
                kwargs["topic"] = channel.topic[:1024]
            kwargs["nsfw"] = channel.nsfw
            created = await target_guild.create_text_channel(channel.name, **kwargs)
        else:
            if channel.user_limit:
                kwargs["user_limit"] = min(channel.user_limit, 99)
            created = await target_guild.create_voice_channel(channel.name, **kwargs)
        await db.set_channel_map(target_guild.id, channel.id, created.id)
        return created

    async def _full_sync(self, main_guild: discord.Guild, target_guild: discord.Guild, category_ids: Optional[set] = None) -> int:
        """Mirror categories/channels from main_guild into target_guild. category_ids=None mirrors everything
        (including uncategorized channels); a set scopes this to just those main-server categories and the
        channels inside them."""
        cats = main_guild.categories if category_ids is None else [c for c in main_guild.categories if c.id in category_ids]
        if category_ids is None:
            chans = [c for c in main_guild.channels if not isinstance(c, discord.CategoryChannel)]
        else:
            chans = [c for c in main_guild.channels
                     if not isinstance(c, discord.CategoryChannel) and c.category_id in category_ids]
        total = len(cats) + len(chans)
        if total > MAX_MIRROR_ITEMS:
            raise UserError(f"That's more than {MAX_MIRROR_ITEMS} channels/categories — too many to mirror in one go. Pick fewer categories.")
        existing_map = await db.get_channel_map(target_guild.id)
        count = 0
        for cat in cats:
            if cat.id in existing_map and target_guild.get_channel(existing_map[cat.id]):
                continue
            await self._ensure_category(target_guild, cat)
            count += 1
        for ch in chans:
            if ch.id in existing_map and target_guild.get_channel(existing_map[ch.id]):
                continue
            if await self._mirror_channel(target_guild, ch) is not None:
                count += 1
        return count

    def _in_scope(self, category_ids: Optional[set], channel: discord.abc.GuildChannel) -> bool:
        """Whether `channel` (or the category itself) falls inside a link's scope. None scope = whole server."""
        if category_ids is None:
            return True
        if isinstance(channel, discord.CategoryChannel):
            return channel.id in category_ids
        return getattr(channel, "category_id", None) in category_ids

    # ------------------------------------------------------------- commands ----

    @app_commands.command(name="linkserver", description="Mirror a main server's channels into this server, live — pick categories or the whole thing")
    @app_commands.describe(main_server_id="The ID of the main server to mirror channels from (I must be in it too)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def linkserver(self, interaction: discord.Interaction, main_server_id: str):
        target_guild = interaction.guild
        raw = main_server_id.strip().strip("<#>").strip()
        if not raw.isdigit():
            raise UserError(
                "That doesn't look like a server ID. Right-click the main server's icon → **Copy Server ID** "
                "(turn on Developer Mode in Discord's App Settings → Advanced first)."
            )
        main_guild = self.bot.get_guild(int(raw))
        if main_guild is None:
            raise UserError("I'm not in a server with that ID. I need to be a member of the main server to mirror its channels.")
        if main_guild.id == target_guild.id:
            raise UserError("That's this server. Give me the ID of the **main** server you want to mirror from.")
        if not target_guild.me.guild_permissions.manage_channels:
            raise UserError("I need the **Manage Channels** permission here.")
        if not main_guild.me.guild_permissions.view_channel:
            raise UserError(f"I need **View Channel** in **{main_guild.name}** to see its channels.")
        if target_guild.id in self.syncing:
            raise UserError("A sync is already running here. Wait for it to finish.")

        await interaction.response.defer(ephemeral=True)

        if not main_guild.categories:
            # nothing to choose between — mirror the (flat) server straight away
            result = await self._run_sync(target_guild, main_guild, None, interaction.user.id)
            await interaction.followup.send(embed=result, ephemeral=True)
            return

        view = CategoryLinkView(self, interaction.user.id, main_guild, target_guild)
        view.message = await interaction.followup.send(
            f"Which parts of **{main_guild.name}** should this server mirror? Pick one or more categories below, "
            "or **🌐 Mirror the whole server** for everything (including uncategorized channels).\n\n"
            "**📤 Also auto-post dropped files** additionally wires each linked channel as an auto-upload drop "
            "zone — a file dropped here gets stored and auto-posted with a **Get file** button on the main "
            "server, on top of the normal live message mirroring. **🎬 Add branding GIF** sets an optional GIF "
            "shown on those auto-posts.",
            view=view, ephemeral=True,
        )

    async def _run_sync(self, target_guild: discord.Guild, main_guild: discord.Guild,
                         category_ids: Optional[set], invoker_id: int) -> discord.Embed:
        """Sets the link + its scope, runs the catch-up sync, and returns the result embed. Raises UserError
        on failure (caller decides how to surface it)."""
        await db.set_server_link(target_guild.id, main_guild.id, invoker_id)
        await db.set_linked_category_ids(target_guild.id, category_ids)
        self.syncing.add(target_guild.id)
        try:
            created = await self._full_sync(main_guild, target_guild, category_ids)
        except UserError:
            self.syncing.discard(target_guild.id)
            raise
        except Exception:
            log.exception("Initial server-link sync failed")
            self.syncing.discard(target_guild.id)
            raise UserError(
                "The first sync hit an error partway through. What was already created is still linked and will keep syncing live for the picked scope — "
                "check my Manage Channels permission in both servers and run `/linkserver` again to catch up on the rest."
            ) from None
        self.syncing.discard(target_guild.id)

        noun = "channel/category" if created == 1 else "channels/categories"
        scope_text = "the **whole server**" if category_ids is None else (
            f"**{len(category_ids)}** selected categor{'y' if len(category_ids) == 1 else 'ies'}"
        )
        embed = simple(
            "🔗 Server linked",
            f"This server now mirrors {scope_text} from **{main_guild.name}**. Created **{created}** {noun} to catch up.\n\n"
            "From now on, channels created, renamed, moved or deleted within that scope on the main server automatically "
            "happen here too — one-way only, nothing made directly in this server is touched or copied back.",
            COLOR,
        )
        await emit(target_guild, "server", "🔗 Server link set up", ui.kv(
            ("🛡️ By", f"<@{invoker_id}>"), ("Main server", main_guild.name),
            ("Scope", "Whole server" if category_ids is None else f"{len(category_ids)} categories"),
        ))
        return embed

    async def _wire_autopost(self, target_guild: discord.Guild, main_guild: discord.Guild,
                              category_ids: Optional[set], gif_url: Optional[str], invoker_id: int) -> int:
        """On top of the live mirror's plain-text message forwarding, also turn every mapped TEXT channel
        (within the picked scope) into an /autoupload drop zone: a file dropped in the vault/staff channel is
        stored and auto-posted into the matching main-server channel behind a Get-file button, optionally
        branded with `gif_url` -- the same mechanism /autoupload's own bulk-add commands use, just wired up
        from the channel pairs /linkserver already mirrored instead of a separate manual pairing pass.
        Returns how many drop zones were wired."""
        channel_map = await db.get_channel_map(target_guild.id)  # main_channel_id -> target_channel_id
        wired = 0
        for main_id, target_id in channel_map.items():
            main_channel = main_guild.get_channel(main_id)
            target_channel = target_guild.get_channel(target_id)
            if not isinstance(main_channel, discord.TextChannel) or not isinstance(target_channel, discord.TextChannel):
                continue
            if not self._in_scope(category_ids, main_channel):
                continue
            await db.execute(
                "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, post_guild_id, pack_name, required_role_id, once_per_user, created_by, created_at, gif_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, post_guild_id = excluded.post_guild_id, "
                "pack_name = excluded.pack_name, gif_url = excluded.gif_url",
                (target_guild.id, target_channel.id, main_channel.id, main_channel.guild.id, main_channel.name.replace("-", " ").title(),
                 None, 0, invoker_id, discord.utils.utcnow().isoformat(), gif_url),
            )
            wired += 1
        return wired

    @app_commands.command(name="unlinkserver", description="Stop mirroring channels from the linked main server")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def unlinkserver(self, interaction: discord.Interaction):
        link = await db.get_server_link(interaction.guild.id)
        if not link:
            raise UserError("This server isn't linked to a main server.")
        main_guild = self.bot.get_guild(link["main_guild_id"])
        await db.delete_server_link(interaction.guild.id)
        await interaction.response.send_message(embed=simple(
            "Unlinked", f"This server will no longer mirror channel changes from **{main_guild.name if main_guild else 'the main server'}**. "
            "Channels already created here are left as they are.", COLOR,
        ), ephemeral=True)

    @app_commands.command(name="linkstatus", description="Show whether this server is linked to a main server")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def linkstatus(self, interaction: discord.Interaction):
        link = await db.get_server_link(interaction.guild.id)
        if not link:
            return await interaction.response.send_message(embed=simple("Not linked", "This server isn't mirroring any main server. Use `/linkserver` to set it up.", INFO), ephemeral=True)
        main_guild = self.bot.get_guild(link["main_guild_id"])
        mapped = len(await db.get_channel_map(interaction.guild.id))
        category_ids = db.parse_category_ids(link["category_ids"])
        if category_ids is None:
            scope_line = "Scope: the **whole server**."
        else:
            names = []
            if main_guild:
                names = [c.name for c in main_guild.categories if c.id in category_ids]
            scope_line = f"Scope: **{len(category_ids)}** categor{'y' if len(category_ids) == 1 else 'ies'}" + (
                f" ({', '.join(names)})" if names else ""
            ) + "."
        await interaction.response.send_message(embed=simple(
            "🔗 Linked",
            f"Mirroring **{main_guild.name if main_guild else link['main_guild_id']}** live. "
            f"**{mapped}** channel(s)/category(s) tracked so far.\n{scope_line}",
            COLOR,
        ), ephemeral=True)

    @app_commands.command(name="linkrefresh", description="Catch this server up with the linked main server — no new duplicates")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def linkrefresh(self, interaction: discord.Interaction):
        target_guild = interaction.guild
        link = await db.get_server_link(target_guild.id)
        if not link:
            raise UserError("This server isn't linked to a main server yet. Use `/linkserver` to set that up first.")
        main_guild = self.bot.get_guild(link["main_guild_id"])
        if main_guild is None:
            raise UserError("I'm no longer in the linked main server, so I can't refresh from it.")
        if not target_guild.me.guild_permissions.manage_channels:
            raise UserError("I need the **Manage Channels** permission here.")
        if not main_guild.me.guild_permissions.view_channel:
            raise UserError(f"I need **View Channel** in **{main_guild.name}** to see its channels.")
        if target_guild.id in self.syncing:
            raise UserError("A sync is already running here. Wait for it to finish.")

        await interaction.response.defer(ephemeral=True)
        category_ids = db.parse_category_ids(link["category_ids"])
        self.syncing.add(target_guild.id)
        try:
            created = await self._full_sync(main_guild, target_guild, category_ids)
        except UserError:
            self.syncing.discard(target_guild.id)
            raise
        except Exception:
            log.exception("linkrefresh sync failed")
            self.syncing.discard(target_guild.id)
            raise UserError("The refresh hit an error partway through. What's already linked is unaffected — try again in a moment.") from None
        self.syncing.discard(target_guild.id)

        scope_text = "the whole server" if category_ids is None else f"{len(category_ids)} linked categories"
        if created == 0:
            text = f"Already caught up — nothing new found in {scope_text} on **{main_guild.name}**."
        else:
            noun = "channel/category" if created == 1 else "channels/categories"
            text = (f"Caught up with **{main_guild.name}** ({scope_text}). Linked/created **{created}** new {noun} — "
                    "existing channels with matching names were reused, not duplicated.")
        await interaction.followup.send(embed=simple("🔄 Refreshed", text, COLOR), ephemeral=True)

    # --------------------------------------------------------- forwarding ----

    async def _forward_message(self, message: discord.Message, dest: discord.TextChannel) -> None:
        content = (message.content or "").strip()
        attachments = list(message.attachments)
        if not content and not attachments:
            return
        perms = dest.permissions_for(dest.guild.me)
        if not perms.send_messages or (attachments and not perms.attach_files):
            return
        limit = dest.guild.filesize_limit
        files = []
        for a in attachments[:MAX_FORWARD_FILES]:
            if a.size > limit:
                continue
            try:
                files.append(discord.File(io.BytesIO(await a.read()), filename=a.filename, spoiler=a.is_spoiler()))
            except discord.HTTPException:
                pass
        prefix = f"**{message.author.display_name}** ({message.guild.name}):\n" if content else f"**{message.author.display_name}** ({message.guild.name}):"
        body = (prefix + content)[:2000]
        try:
            kwargs = {"files": files} if files else {}
            await dest.send(content=body, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
        except discord.HTTPException:
            log.exception("Message mirror failed: %s -> %s", message.channel.id, dest.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not _is_normal(message):
            return

        # This server is a linked (vault/staff) server -> forward into the matching main-server channel
        link = await db.get_server_link(message.guild.id)
        if link is not None:
            main_channel_id = await db.get_main_channel_id(message.guild.id, message.channel.id)
            if main_channel_id is not None:
                main_guild = self.bot.get_guild(link["main_guild_id"])
                main_channel = main_guild.get_channel(main_channel_id) if main_guild else None
                if isinstance(main_channel, discord.TextChannel):
                    await self._forward_message(message, main_channel)

        # This server is the main server for one or more links -> forward into each linked channel too
        for sublink in await db.get_server_links_for_main(message.guild.id):
            target_guild = self.bot.get_guild(sublink["target_guild_id"])
            if target_guild is None:
                continue
            target_channel_id = await db.get_target_channel_id(target_guild.id, message.channel.id)
            if target_channel_id is None:
                continue
            target_channel = target_guild.get_channel(target_channel_id)
            if isinstance(target_channel, discord.TextChannel):
                await self._forward_message(message, target_channel)

    # ------------------------------------------------------------- listeners ----

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        for link in await db.get_server_links_for_main(channel.guild.id):
            target_guild = self.bot.get_guild(link["target_guild_id"])
            if target_guild is None or not target_guild.me.guild_permissions.manage_channels:
                continue
            if not self._in_scope(db.parse_category_ids(link["category_ids"]), channel):
                continue  # outside this link's picked categories — leave it alone
            try:
                await self._mirror_channel(target_guild, channel)
            except discord.HTTPException:
                log.exception("Mirror create failed: %s -> guild %s", channel.id, target_guild.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        for link in await db.get_server_links_for_main(channel.guild.id):
            target_guild_id = link["target_guild_id"]
            mapped_id = await db.get_target_channel_id(target_guild_id, channel.id)
            if mapped_id is None:
                continue
            target_guild = self.bot.get_guild(target_guild_id)
            target_channel = target_guild.get_channel(mapped_id) if target_guild else None
            if target_channel is not None:
                try:
                    await target_channel.delete(reason=REASON_DELETE)
                except discord.HTTPException:
                    log.exception("Mirror delete failed: %s -> %s", channel.id, mapped_id)
            await db.delete_channel_map_by_main(target_guild_id, channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
        if before.name == after.name and getattr(before, "category_id", None) == getattr(after, "category_id", None):
            return
        for link in await db.get_server_links_for_main(after.guild.id):
            target_guild_id = link["target_guild_id"]
            target_guild = self.bot.get_guild(target_guild_id)
            if target_guild is None:
                continue
            category_ids = db.parse_category_ids(link["category_ids"])
            mapped_id = await db.get_target_channel_id(target_guild_id, after.id)
            now_in_scope = self._in_scope(category_ids, after)

            if mapped_id is None:
                if now_in_scope:
                    # moved into a picked category it wasn't mirrored from before — start mirroring it now
                    try:
                        await self._mirror_channel(target_guild, after)
                    except discord.HTTPException:
                        log.exception("Mirror create-on-move failed: %s -> guild %s", after.id, target_guild_id)
                continue

            target_channel = target_guild.get_channel(mapped_id)
            if target_channel is None:
                continue

            if not now_in_scope:
                # moved out of this link's picked categories — stop mirroring it
                try:
                    await target_channel.delete(reason=REASON_DELETE)
                except discord.HTTPException:
                    log.exception("Mirror delete-on-move failed: %s -> %s", after.id, mapped_id)
                await db.delete_channel_map_by_main(target_guild_id, after.id)
                continue

            try:
                kwargs = {}
                if before.name != after.name:
                    kwargs["name"] = after.name
                if after.category_id != before.category_id:
                    kwargs["category"] = await self._ensure_category(target_guild, after.category)
                if kwargs:
                    await target_channel.edit(reason=REASON_UPDATE, **kwargs)
            except discord.HTTPException:
                log.exception("Mirror update failed: %s -> %s", after.id, mapped_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerLink(bot))
