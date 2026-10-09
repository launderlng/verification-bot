import asyncio
import logging
import re
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from cogs.files import NAME_RE, check_filename, new_path
from cogs.posts import Draft, publish_draft
from common import SUCCESS, WARN, UserError, check_can_send
from fileutil import ATTACH_LIMIT, human_size, max_file_bytes, public_base_url, storage_cap_bytes
from logutil import emit

log = logging.getLogger("verification-bot")

NEEDS_WEB = "Files over 25 MB need a public web address to deliver as a link. In Railway: Settings → Networking → Generate Domain."
INVITE_PERMS = 8  # Administrator -- the only permission that lets the bot see channels in locked-down categories it isn't explicitly added to
FIX_PERM_CATEGORIES = ("FIVEM", "RZ", "Boosters")


def clean_name(filename: str, taken: set) -> str:
    """Turn a filename into a valid, unique stored-file name (letters, numbers, spaces, dashes, underscores)."""
    base = re.sub(r"\.[^.]+$", "", filename)
    base = re.sub(r"[^A-Za-z0-9 _.\-]", "", base).strip()[:60] or "file"
    if not NAME_RE.match(base):
        base = (re.sub(r"^[^A-Za-z0-9]+", "", base) or "file")[:60]
    name, n = base, 2
    while name.lower() in taken:
        name = f"{base}-{n}"[:60]
        n += 1
    taken.add(name.lower())
    return name


def clean_title(filename: str) -> str:
    base = re.sub(r"\.[^.]+$", "", filename)
    base = re.sub(r"[_\-]+", " ", base).strip() or "New file"
    return (base[0].upper() + base[1:])[:256]


def normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


async def fetch_live(guild: discord.Guild) -> tuple[list, list]:
    """Fetch a guild's channels straight from the API instead of trusting the gateway cache, and return
    (categories, text_channels) -- both from this SAME fetch, never mixed with cached objects.

    Mixing matters: a TextChannel's `.category` property looks its category up in the gateway cache by ID
    (`guild.get_channel(category_id)`), not from wherever the channel itself came from. If the cache's copy of a
    category is stale/wrong (e.g. a leftover category object with a different ID than the real current one -- the
    kind of gap that shows up right after the bot is re-invited with new permissions or added to a category by
    hand), `.category` silently returns None or the wrong category, and anything keyed off it comes back empty.
    Matching categories and channels from one live list sidesteps that entirely."""
    try:
        channels = await guild.fetch_channels()
    except discord.HTTPException:
        return list(guild.categories), list(guild.text_channels)  # fall back to cache if the API call itself fails
    cats = [c for c in channels if isinstance(c, discord.CategoryChannel)]
    texts = [c for c in channels if isinstance(c, discord.TextChannel)]
    return cats, texts


def find_match(name: str, candidates: list) -> Optional[discord.TextChannel]:
    """Exact (normalized) name match only. A loose substring fallback used to live here, but it's actively
    dangerous for this use: "reshades" is a substring of "vault-reshades", so a channel named "reshades" would
    silently match an unrelated "vault-reshades" channel and get wired to the wrong destination -- exactly what
    happened in practice. Since every caller here can fall back to creating a brand-new channel when nothing
    matches, there's no need to guess; a wrong match that overwrites a correct pairing is worse than no match."""
    norm = normalize(name)
    for c in candidates:
        if normalize(c.name) == norm:
            return c
    return None


def resolve_channel_id(raw: str) -> int:
    raw = raw.strip().strip("<#>").strip()
    if not raw.isdigit():
        raise UserError("That doesn't look like a channel ID. Right-click the channel → **Copy Channel ID** (turn on **Developer Mode** in Discord's App Settings → Advanced first).")
    return int(raw)


async def used_bytes(guild_id: int) -> int:
    return (await db.fetch_one("SELECT COALESCE(SUM(size), 0) AS n FROM stored_files WHERE guild_id = ?", (guild_id,)))["n"]


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class AutoUpload(commands.GroupCog, group_name="autoupload", group_description="Drop files in a channel (even on another server) and auto-add + auto-post them"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def room_left(self, guild_id: int) -> int:
        return storage_cap_bytes() - await used_bytes(guild_id)

    # ----------------------------------------------------------- listener ----

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        # A forwarded message (Discord's Forward feature) carries its attachments in message_snapshots, not in
        # message.attachments -- the original message's content is "snapshotted" into the forward rather than
        # duplicated as regular attachments. Gather both so dropping a file in directly and forwarding one from
        # elsewhere both work the same way.
        attachments = list(message.attachments)
        for snap in getattr(message, "message_snapshots", None) or getattr(message, "snapshots", None) or []:
            attachments.extend(getattr(snap, "attachments", None) or [])
        if not attachments:
            return
        row = await db.fetch_one("SELECT * FROM upload_channels WHERE guild_id = ? AND channel_id = ?", (message.guild.id, message.channel.id))
        if row is None:
            return
        perms = message.author.guild_permissions
        if not (perms.manage_guild or perms.manage_messages):
            return  # the drop zone only triggers for staff; anyone else's message there is left alone

        post_channel = self.bot.get_channel(row["post_channel_id"])  # may be on a different server than this message
        if post_channel is None:
            await message.reply(embed=ui.card("⚠️ Can't post", "The channel this is set to post in doesn't exist any more, or I've lost access to it. Ask staff to run `/autoupload add` again.", color=WARN), mention_author=False)
            return
        required_role = post_channel.guild.get_role(row["required_role_id"]) if row["required_role_id"] else None

        store_guild_id = post_channel.guild.id  # the file belongs to the server people actually claim it in
        taken = {r["name"].lower() for r in await db.fetch_all("SELECT name FROM stored_files WHERE guild_id = ?", (store_guild_id,))}
        posted, failed = [], []
        for att in attachments:
            try:
                await self.ingest(message, att, row, post_channel, required_role, taken, store_guild_id)
                posted.append(att.filename)
            except UserError as e:
                failed.append(f"**{att.filename}:** {e}")
            except discord.HTTPException as e:
                failed.append(f"**{att.filename}:** Discord wouldn't let me post that ({e.status}).")

        if posted:
            await message.add_reaction("✅")
        if failed:
            await message.reply(embed=ui.card("⚠️ Some files didn't make it", "\n".join(failed), color=WARN), mention_author=False)

    async def ingest(self, message: discord.Message, att: discord.Attachment, row, post_channel: discord.TextChannel,
                      required_role: Optional[discord.Role], taken: set, store_guild_id: int) -> None:
        check_filename(att.filename)
        if att.size > max_file_bytes():
            raise UserError(f"it's {human_size(att.size)}, over the {human_size(max_file_bytes())} limit")
        if att.size > await self.room_left(store_guild_id):
            raise UserError(f"that would go over the {human_size(storage_cap_bytes())} storage limit")
        if att.size > ATTACH_LIMIT and not public_base_url():
            raise UserError(NEEDS_WEB)

        name = clean_name(att.filename, taken)
        data = await att.read()
        path = None
        stored_data = data
        if len(data) > ATTACH_LIMIT:
            path = new_path(store_guild_id)
            await asyncio.to_thread(Path(path).write_bytes, data)
            stored_data = b""

        await db.execute(
            "INSERT INTO stored_files (guild_id, name, filename, content_type, size, data, description, required_role_id, once_per_user, uploaded_by, created_at, path) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (store_guild_id, name, att.filename, att.content_type, len(data), stored_data, f"Auto-added from #{message.channel.name} ({message.guild.name})",
             required_role.id if required_role else None, int(row["once_per_user"]), message.author.id, discord.utils.utcnow().isoformat(), path),
        )
        file_row = await db.fetch_one("SELECT id FROM stored_files WHERE guild_id = ? AND name = ?", (store_guild_id, name))

        # Show an inline preview when the file is small enough to attach directly: images go in the embed itself
        # (the big photo), video/audio get attached to the message so Discord renders its native player. Either
        # way the Get-file button is always there too, as the permanent, trackable way to claim a copy.
        ctype = (att.content_type or "").lower()
        photo = file_attach = None
        if len(data) <= ATTACH_LIMIT:
            if ctype.startswith("image/"):
                photo = (att.filename, data, len(data))
            elif ctype.startswith("video/") or ctype.startswith("audio/"):
                file_attach = (att.filename, data, len(data))

        # Title the post after the pack/source channel, not the raw filename (which is often a meaningless name
        # like "V1" or "Cielo_15") -- the filename is still shown as the footer and the stored file's own name.
        draft = Draft(post_channel, row["pack_name"], clean_title(att.filename), f"Uploaded in #{message.channel.name}", None,
                       photo=photo, file=file_attach, deliver=(file_row["id"], name), pack=row["pack_name"])
        await publish_draft(post_channel.guild, message.author, draft)
        await emit(
            post_channel.guild, "files", "File auto-uploaded",
            ui.kv(("👤 By", message.author.mention), ("📦 Stored as", name), ("📍 Dropped in", f"#{message.channel.name} ({message.guild.name})"), ("📬 Posted in", post_channel.mention)),
            subject=message.author.id,
        )

    # ----------------------------------------------------------- commands ----

    @app_commands.command(description="Turn a channel into a drop zone (it can be on another server you've invited me to)")
    @app_commands.describe(
        upload_channel_id="The upload channel's ID (right-click it → Copy Channel ID). Can be on another server.",
        post_channel="Where the auto-post goes, in THIS server, e.g. #reshades",
        pack="What to call this pack/category, e.g. ReShade",
        required_role="Only members with this role can claim the file (optional)",
        once_per_user="Each member can only claim it once (default: no)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(
        self, interaction: discord.Interaction, upload_channel_id: str, post_channel: discord.TextChannel,
        pack: app_commands.Range[str, 1, 60], required_role: Optional[discord.Role] = None, once_per_user: bool = False,
    ):
        channel = self.bot.get_channel(resolve_channel_id(upload_channel_id))
        if channel is None:
            raise UserError("I can't see that channel. Make sure I've been invited to the server it's on — run `/autoupload inviteinfo` for an invite link.")
        if not isinstance(channel, discord.TextChannel):
            raise UserError("That needs to be a text channel.")
        check_can_send(post_channel, interaction.guild.me, files=False)
        await db.execute(
            "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
            "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user",
            (channel.guild.id, channel.id, post_channel.id, pack.strip(), required_role.id if required_role else None,
             int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat()),
        )
        where = f"**#{channel.name}** on **{channel.guild.name}**" if channel.guild.id != interaction.guild_id else channel.mention
        body = (f"Any file a staff member drops in {where} now gets stored in the file library and auto-posted in {post_channel.mention} "
                f"with a **📥 Get file** button, tagged as **{pack.strip()}**."
                + (f"\n\n🔒 Needs {required_role.mention} to claim." if required_role else "") + (" 🔁 Once per member." if once_per_user else ""))
        await interaction.response.send_message(embed=ui.card("✅ Drop zone ready", body, color=SUCCESS, guild=interaction.guild, section="Files"), ephemeral=True)

    async def pair_category(self, interaction: discord.Interaction, vault: discord.Guild, category: Optional[str], skip: list,
                             required_role: Optional[discord.Role], once_per_user: bool, create_missing: bool = True,
                             dest_category_name: Optional[str] = None) -> tuple[list, list, list]:
        """Match one category's (or the whole server's) channels on `vault` to same/similarly-named channels in
        the server the command was run in, and save a drop zone for every match. When `create_missing` is True
        (the default), any vault channel with no existing match gets a brand-new channel created for it here,
        under a category named after the vault channel's own category (or `dest_category_name`, when given, e.g.
        for mirroring within a single server where the source and destination can't share a category name),
        instead of being left unmatched. When `dest_category_name` is given, matching against an existing channel
        is also scoped to just that destination category, rather than the whole server -- important for same-guild
        mirroring, where searching the whole server by name could accidentally latch onto an unrelated channel
        (or the source channel itself).
        Returns (matched, unmatched, created) -- created is the subset of matched that are newly-made channels."""
        vault_cats, all_vault_channels = await fetch_live(vault)
        cat_ids = None
        if category:
            cat_ids = {c.id for c in vault_cats if category.strip().lower() in c.name.lower()}
        vault_channels = [
            c for c in all_vault_channels
            if (cat_ids is None or c.category_id in cat_ids)
            and not any(s in c.name.lower() for s in skip)
        ]
        if dest_category_name:
            existing_dest_cat = discord.utils.find(lambda c: c.name.lower() == dest_category_name.lower(), interaction.guild.categories)
            main_channels = list(existing_dest_cat.text_channels) if existing_dest_cat else []
        else:
            main_channels = list(interaction.guild.text_channels)
        matched, unmatched, created = [], [], []
        new_category_cache: dict = {}  # destination category name (lowered) -> main-guild CategoryChannel
        for vc in vault_channels:
            target = find_match(vc.name, main_channels)
            if target and target.id != vc.id:
                matched.append((vc, target))
                continue
            if not create_missing:
                unmatched.append(vc)
                continue
            vault_cat = next((c for c in vault_cats if c.id == vc.category_id), None)
            want_name = dest_category_name or (vault_cat.name if vault_cat else None)
            cat_key = (want_name or "").lower()
            try:
                dest_cat = new_category_cache.get(cat_key)
                if dest_cat is None:
                    dest_cat = discord.utils.find(
                        lambda c: want_name and c.name.lower() == want_name.lower(), interaction.guild.categories
                    ) if want_name else None
                    if dest_cat is None and want_name:
                        dest_cat = await interaction.guild.create_category(want_name, reason="Auto-created by /autoupload")
                    new_category_cache[cat_key] = dest_cat
                new_channel = await interaction.guild.create_text_channel(
                    vc.name, category=dest_cat, reason=f"Auto-created by /autoupload to mirror #{vc.name}"
                )
            except discord.Forbidden:
                unmatched.append(vc)
                continue
            main_channels.append(new_channel)
            matched.append((vc, new_channel))
            created.append((vc, new_channel))
        for vc, target in matched:
            check_can_send(target, interaction.guild.me, files=False)
            await db.execute(
                "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
                "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user",
                (vc.guild.id, vc.id, target.id, vc.name.replace("-", " ").title(), required_role.id if required_role else None,
                 int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat()),
            )
        return matched, unmatched, created

    def pairing_result_embed(self, guild: discord.Guild, title: str, matched: list, unmatched: list, created: Optional[list] = None) -> discord.Embed:
        created_ids = {vc.id for vc, _ in (created or [])}
        body = ""
        if matched:
            body += "**✅ Paired**\n" + "\n".join(
                f"#{vc.name} → {t.mention}" + (" 🆕 (new channel)" if vc.id in created_ids else "") for vc, t in matched
            ) + "\n\n"
        if unmatched:
            body += "**⚠️ No match found on this server** (add these by hand with `/autoupload add`)\n" + "\n".join(f"#{vc.name} (`{vc.id}`)" for vc in unmatched)
        return ui.card(title, body or "Nothing matched.", color=SUCCESS if matched else WARN, guild=guild, section="Files")

    @app_commands.command(description="Pair up a whole category from another server by matching channel names to this server's channels")
    @app_commands.describe(
        vault_guild_id="The other server's ID (right-click its icon → Copy Server ID)",
        category="Only match channels in this category on the other server (optional, e.g. RZ)",
        exclude="Skip channel names containing any of these, comma-separated (e.g. no-,chat)",
        required_role="Only members with this role can claim any matched file (optional)",
        once_per_user="Each member can only claim a matched file once (default: no)",
        create_missing="Create a new channel here for any vault channel with no match yet (default: yes)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bulkadd(
        self, interaction: discord.Interaction, vault_guild_id: str, category: Optional[str] = None, exclude: Optional[str] = None,
        required_role: Optional[discord.Role] = None, once_per_user: bool = False, create_missing: bool = True,
    ):
        if not vault_guild_id.strip().isdigit():
            raise UserError("That doesn't look like a server ID. Right-click the server's icon → **Copy Server ID**.")
        vault = self.bot.get_guild(int(vault_guild_id.strip()))
        if vault is None:
            raise UserError("I'm not in that server. Run `/autoupload inviteinfo` for an invite link, add me there, then try again.")
        skip = [s.strip().lower() for s in (exclude or "").split(",") if s.strip()]
        await interaction.response.defer(ephemeral=True)
        matched, unmatched, created = await self.pair_category(interaction, vault, category, skip, required_role, once_per_user, create_missing)
        if not matched and not unmatched:
            raise UserError("No text channels found over there. Check the category name, or leave it blank to scan every channel.")
        await interaction.followup.send(embed=self.pairing_result_embed(interaction.guild, f"📥 {len(matched)} drop zone(s) paired", matched, unmatched, created), ephemeral=True)

    @app_commands.command(description="One-click setup for your FIVEM/RZ/Boosters vault: pairs everything except NO PROPS and chat channels")
    @app_commands.describe(
        vault_guild_id="The vault server's ID (right-click its icon → Copy Server ID)",
        required_role="Only members with this role can claim any matched file (optional)",
        once_per_user="Each member can only claim a matched file once (default: no)",
        create_missing="Create a new channel here for any vault channel with no match yet (default: yes)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def preset(
        self, interaction: discord.Interaction, vault_guild_id: str,
        required_role: Optional[discord.Role] = None, once_per_user: bool = False, create_missing: bool = True,
    ):
        """Pairs the FIVEM, RZ and Boosters categories in one go. Skips NO PROPS entirely (those are toggle/removal
        settings, not files to post) and skips any channel with "chat" in its name (e.g. booster-chat)."""
        if not vault_guild_id.strip().isdigit():
            raise UserError("That doesn't look like a server ID. Right-click the server's icon → **Copy Server ID**.")
        vault = self.bot.get_guild(int(vault_guild_id.strip()))
        if vault is None:
            raise UserError("I'm not in that server. Run `/autoupload inviteinfo` for an invite link, add me there, then try again.")

        await interaction.response.defer(ephemeral=True)
        all_matched, all_unmatched, all_created = [], [], []
        for category in ("FIVEM", "RZ", "Boosters"):
            matched, unmatched, created = await self.pair_category(interaction, vault, category, ["chat"], required_role, once_per_user, create_missing)
            all_matched += matched
            all_unmatched += unmatched
            all_created += created

        await interaction.followup.send(
            embed=self.pairing_result_embed(interaction.guild, f"⚡ Preset applied: {len(all_matched)} drop zone(s) paired", all_matched, all_unmatched, all_created),
            ephemeral=True,
        )

    async def seed_vault(self, interaction: discord.Interaction, vault: discord.Guild, category: str, skip: list,
                          required_role: Optional[discord.Role], once_per_user: bool) -> tuple[list, list]:
        """The reverse of pair_category: for when the vault doesn't have the channels yet and the REAL content
        lives here instead. Scans `category` in THIS server (where the real channels already are), and for each
        one creates a same-named channel over in `vault` (under a same-named category there, created if needed)
        if nothing already matches, then pairs the new/matched vault channel to the existing channel here.
        Returns (paired, created) as (vault_channel, main_channel) tuples -- created is the subset of paired
        that are newly-made vault channels."""
        main_cats, main_channels_all = await fetch_live(interaction.guild)
        cat_ids = {c.id for c in main_cats if category.strip().lower() in c.name.lower()}
        main_channels = [c for c in main_channels_all if c.category_id in cat_ids and not any(s in c.name.lower() for s in skip)]
        vault_cats, vault_channels_all = await fetch_live(vault)
        paired, created = [], []
        new_category_cache: dict = {}
        for mc in main_channels:
            main_cat = next((c for c in main_cats if c.id == mc.category_id), None)
            cat_key = (main_cat.name.lower() if main_cat else "")
            vc = find_match(mc.name, vault_channels_all)
            if vc is None:
                try:
                    dest_cat = new_category_cache.get(cat_key)
                    if dest_cat is None:
                        dest_cat = discord.utils.find(
                            lambda c: main_cat and c.name.lower() == main_cat.name.lower(), vault_cats
                        ) if main_cat else None
                        if dest_cat is None and main_cat:
                            dest_cat = await vault.create_category(main_cat.name, reason="Auto-created by /autoupload to seed the vault")
                        new_category_cache[cat_key] = dest_cat
                    vc = await vault.create_text_channel(mc.name, category=dest_cat, reason=f"Auto-created by /autoupload to seed the vault, mirrors #{mc.name}")
                except discord.Forbidden:
                    continue
                vault_channels_all.append(vc)
                created.append((vc, mc))
            check_can_send(mc, interaction.guild.me, files=False)
            await db.execute(
                "INSERT INTO upload_channels (guild_id, channel_id, post_channel_id, pack_name, required_role_id, once_per_user, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(guild_id, channel_id) DO UPDATE SET post_channel_id = excluded.post_channel_id, pack_name = excluded.pack_name, "
                "required_role_id = excluded.required_role_id, once_per_user = excluded.once_per_user",
                (vc.guild.id, vc.id, mc.id, mc.name.replace("-", " ").title(), required_role.id if required_role else None,
                 int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat()),
            )
            paired.append((vc, mc))
        return paired, created

    @app_commands.command(description="Fill an empty vault with channels copied from FIVEM/RZ/Boosters here, wired to post back here")
    @app_commands.describe(
        vault_guild_id="The (new/empty) vault server's ID (right-click its icon → Copy Server ID)",
        required_role="Only members with this role can claim any matched file (optional)",
        once_per_user="Each member can only claim a matched file once (default: no)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def seedvault(
        self, interaction: discord.Interaction, vault_guild_id: str,
        required_role: Optional[discord.Role] = None, once_per_user: bool = False,
    ):
        """For when the real FIVEM/RZ/Boosters channels live in THIS server, not a separate vault: creates the
        matching (empty) channel structure over in the vault server and pairs it back here, so staff can start
        dropping files into the vault and have them post here, without needing to have built the vault by hand."""
        if not vault_guild_id.strip().isdigit():
            raise UserError("That doesn't look like a server ID. Right-click the server's icon → **Copy Server ID**.")
        vault = self.bot.get_guild(int(vault_guild_id.strip()))
        if vault is None:
            raise UserError("I'm not in that server. Run `/autoupload inviteinfo` for an invite link, add me there, then try again.")
        if vault.id == interaction.guild.id:
            raise UserError("That's this server's own ID -- `vault_guild_id` needs to be the *other*, empty server you want to seed.")

        await interaction.response.defer(ephemeral=True)
        all_paired, all_created = [], []
        for category in ("FIVEM", "RZ", "Boosters"):
            paired, created = await self.seed_vault(interaction, vault, category, ["chat"], required_role, once_per_user)
            all_paired += paired
            all_created += created

        await interaction.followup.send(
            embed=self.pairing_result_embed(
                interaction.guild, f"🌱 Seeded {vault.name}: {len(all_paired)} channel(s) wired up", all_paired, [], all_created
            ),
            ephemeral=True,
        )

    @app_commands.command(description="Same-server bulkadd: mirror a category here into a new public category, auto-posting across")
    @app_commands.describe(
        source_category="Category in THIS server to watch for uploads (e.g. RZ)",
        dest_category="Category to post into (created if it doesn't exist, e.g. 'RZ Downloads')",
        exclude="Skip channel names containing any of these, comma-separated (e.g. no-,chat)",
        required_role="Only members with this role can claim any matched file (optional)",
        once_per_user="Each member can only claim a matched file once (default: no)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def mirror(
        self, interaction: discord.Interaction, source_category: str, dest_category: str, exclude: Optional[str] = None,
        required_role: Optional[discord.Role] = None, once_per_user: bool = False,
    ):
        """For staff-only categories that already live in this server (not a separate vault): watches each channel
        in `source_category` and auto-posts uploads into a same-named channel under `dest_category`, a new or
        existing category kept separate so the public destination channels don't collide names with the private
        source ones."""
        if source_category.strip().lower() == dest_category.strip().lower():
            raise UserError("Source and destination categories need different names, or uploads would post right back into themselves.")
        await interaction.response.defer(ephemeral=True)
        skip = [s.strip().lower() for s in (exclude or "").split(",") if s.strip()]
        matched, unmatched, created = await self.pair_category(
            interaction, interaction.guild, source_category, skip, required_role, once_per_user,
            create_missing=True, dest_category_name=dest_category,
        )
        if not matched and not unmatched:
            raise UserError(f"No channels found in a category matching \"{source_category}\" on this server.")
        await interaction.followup.send(
            embed=self.pairing_result_embed(interaction.guild, f"🪞 {len(matched)} channel(s) mirrored into \"{dest_category}\"", matched, unmatched, created),
            ephemeral=True,
        )

    @app_commands.command(description="Stop a channel being a drop zone")
    @app_commands.describe(upload_channel_id="The upload channel's ID (right-click it → Copy Channel ID)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, upload_channel_id: str):
        cid = resolve_channel_id(upload_channel_id)
        row = await db.fetch_one("SELECT guild_id FROM upload_channels WHERE channel_id = ?", (cid,))
        if not row:
            raise UserError("That channel isn't a drop zone.")
        await db.execute("DELETE FROM upload_channels WHERE channel_id = ?", (cid,))
        await interaction.response.send_message(embed=ui.card("🗑️ Removed", f"`{cid}` is no longer a drop zone.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="See every drop zone that posts into this server")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        all_rows = await db.fetch_all("SELECT * FROM upload_channels ORDER BY pack_name")
        # upload_channels isn't keyed by the posting guild (the upload side may be on another server),
        # so filter here to just the rows that actually post into this server.
        rows = [r for r in all_rows if interaction.guild.get_channel(r["post_channel_id"]) is not None]
        if not rows:
            raise UserError("No drop zones post into this server yet. Add one with `/autoupload add` or `/autoupload bulkadd`.")
        lines = []
        for r in rows:
            src = self.bot.get_channel(r["channel_id"])
            src_label = f"#{src.name} ({src.guild.name})" if src and src.guild.id != interaction.guild_id else (f"#{src.name}" if src else f"`{r['channel_id']}`")
            lines.append(
                f"📦 **{r['pack_name']}** · {src_label} → <#{r['post_channel_id']}>"
                + (f" · 🔒 <@&{r['required_role_id']}>" if r["required_role_id"] else "") + (" · 🔁 once" if r["once_per_user"] else "")
            )
        await interaction.response.send_message(embed=ui.card("📥 Drop zones", "\n".join(lines), guild=interaction.guild, section="Files"), ephemeral=True)

    @app_commands.command(description="Get an invite link to add me to another server (e.g. your vault/source server)")
    async def inviteinfo(self, interaction: discord.Interaction):
        url = f"https://discord.com/oauth2/authorize?client_id={self.bot.user.id}&scope=bot%20applications.commands&permissions={INVITE_PERMS}"
        await interaction.response.send_message(
            embed=ui.card(
                "🔗 Invite link", f"Open this on the account that manages your other server, pick the server, and authorize:\n\n{url}\n\n"
                "This asks for **Administrator**. That's the one permission that lets me see channels even in locked-down "
                "categories like a private vault — anything less (even Manage Channels) still can't see a category unless "
                "someone adds my role to it by hand in Discord's permission UI, since Discord hides channels from bots the "
                "same way it hides them from members who aren't allowed in. If you'd rather not grant that, the alternative "
                "is adding my role to each of FIVEM/RZ/Boosters' permissions yourself (right-click category → Edit Category → "
                "Permissions → add my role → allow View Channel) and I can use a smaller invite instead — just ask.",
                guild=interaction.guild, section="Files",
            ), ephemeral=True,
        )

    @app_commands.command(description="List every server I'm in with real channel counts, to spot a wrong/duplicate vault ID")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def servers(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        lines = []
        for g in self.bot.guilds:
            try:
                raw = await self.bot.http.get_all_guild_channels(g.id)
                count = sum(1 for ch in raw if ch.get("type") in (0, 5))  # 0 = text, 5 = announcement
            except Exception:
                count = "?"
            me = g.me
            admin = " 👑" if me and me.guild_permissions.administrator else ""
            lines.append(f"**{g.name}**{admin} — `{g.id}` — {count} text channel(s)")
        body = "\n".join(lines) or "I'm not in any servers."
        body += (
            "\n\n👑 = I have Administrator there. If two servers share a name, compare channel counts and IDs here "
            "against the real one in Discord (right-click its icon → **Copy Server ID**) to find the right `vault_guild_id`."
        )
        await interaction.followup.send(embed=ui.card("🌐 My servers", body, guild=interaction.guild, section="Files"), ephemeral=True)

    @app_commands.command(description="Check whether I can actually see a server's FIVEM/RZ/Boosters categories")
    @app_commands.describe(vault_guild_id="The vault server's ID (right-click its icon → Copy Server ID)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def fixperms(self, interaction: discord.Interaction, vault_guild_id: str):
        if not vault_guild_id.strip().isdigit():
            raise UserError("That doesn't look like a server ID. Right-click the server's icon → **Copy Server ID**.")
        vault = self.bot.get_guild(int(vault_guild_id.strip()))
        if vault is None:
            raise UserError("I'm not in that server. Run `/autoupload inviteinfo` for an invite link, add me there, then try again.")
        me = vault.me
        try:
            vault_cats, vault_texts = await fetch_live(vault)
        except discord.HTTPException as e:
            raise UserError(f"Couldn't list that server's channels (Discord said: {e}). Try again in a moment.")
        found_cats = [cat for cat in vault_cats if any(c.lower() in cat.name.lower() for c in FIX_PERM_CATEGORIES)]
        if found_cats:
            # Categories and their channels come from the SAME live fetch above, so this can't suffer the
            # cache-mismatch bug that made every category look empty before.
            by_cat: dict = {}
            for ch in vault_texts:
                by_cat.setdefault(ch.category_id, []).append(ch)
            lines = []
            for cat in found_cats:
                kinds = {}
                for ch in by_cat.get(cat.id, []):
                    kinds[type(ch).__name__] = kinds.get(type(ch).__name__, 0) + 1
                breakdown = ", ".join(f"{n} {k}" for k, n in kinds.items()) if kinds else "no channels inside"
                lines.append(f"📁 {cat.name} — {breakdown}")
            admin_note = f"Checking **{vault.name}** (`{vault.id}`). " + ("I'm an Administrator there.\n\n" if me and me.guild_permissions.administrator else "\n\n")
            found_cat_ids = {c.id for c in found_cats}
            text_total = sum(1 for ch in vault_texts if ch.category_id in found_cat_ids)
            if text_total:
                tail = "\n\nRun `/autoupload preset` — it should find channels now."
            else:
                # Zero TextChannels anywhere in the server (not just these categories) despite the user seeing
                # plenty in their own client -- bypass discord.py's parsing entirely and look at Discord's raw
                # JSON for this guild's channels, which shows the real numeric `type` for every channel regardless
                # of how (or whether) this library version's object model classifies it.
                try:
                    raw = await self.bot.http.get_all_guild_channels(vault.id)
                except Exception as e:
                    raw = None
                    raw_err = str(e)
                if raw is not None:
                    type_counts: dict = {}
                    for ch in raw:
                        type_counts[ch.get("type")] = type_counts.get(ch.get("type"), 0) + 1
                    sample = "\n".join(f"`{ch.get('name')}` — type `{ch.get('type')}`, parent_id `{ch.get('parent_id')}`" for ch in raw[:10]) or "(none at all)"
                    tail = (
                        f"\n\n⚠️ No plain TextChannels parsed from the API.\n\n"
                        f"Raw debug: Discord reports {len(raw)} channel(s) total in this server, by type: "
                        f"{type_counts}.\nMatched category IDs: {', '.join(str(i) for i in found_cat_ids) or '(none)'}.\n"
                        f"Sample (raw, unfiltered):\n{sample}"
                    )
                else:
                    tail = f"\n\n⚠️ Couldn't even fetch the raw channel list (Discord said: {raw_err})."
            return await interaction.response.send_message(
                embed=ui.card("✅ Categories found", admin_note + "\n".join(lines) + tail,
                               color=SUCCESS if text_total else WARN, guild=interaction.guild, section="Files"),
                ephemeral=True,
            )
        # Can't see any category matching the expected names. Show exactly what server and what categories I DO see,
        # so a wrong vault_guild_id (easy to mix up with the main server's ID) is obvious instead of guessed at.
        all_cats = [cat.name for cat in vault_cats]
        body = (
            f"Checking **{vault.name}** (`{vault.id}`) — "
            f"I don't see any category matching {', '.join(FIX_PERM_CATEGORIES)} there.\n\n"
        )
        if all_cats:
            body += "Categories I *can* see on this server:\n" + "\n".join(f"📁 {n}" for n in all_cats[:25])
            body += (
                "\n\nIf none of those look like your vault's FIVEM/RZ/Boosters categories, double-check `vault_guild_id` — "
                "it needs to be the **vault server's** ID, not this bot's main server. Right-click the vault server's icon "
                "(not a channel) → **Copy Server ID**.\n\nIf one of them *is* meant to be FIVEM/RZ/Boosters but is named "
                "differently (emojis, abbreviations), tell me the exact name and I can match on that instead."
            )
        else:
            body += (
                "In fact I can't see **any** categories on this server at all, which points at `vault_guild_id` being wrong "
                "rather than a permissions issue — if this really is the vault, right-click its icon (not a channel) → "
                "**Copy Server ID** and double check against what you pasted.\n\n"
            )
            if me and me.guild_permissions.administrator:
                body += "That's despite me having Administrator here, so this genuinely looks like the wrong server ID rather than a permission problem."
            else:
                body += (
                    "**Two ways to fix it if this is the right server and it's a permissions issue:**\n"
                    "**1.** Re-invite me with `/autoupload inviteinfo` (now asks for Administrator, which bypasses hidden-channel limits).\n"
                    "**2.** Or, without changing my permissions: right-click each category on that server → **Edit Category** → "
                    "**Permissions** → add my role → allow **View Channel** (and ideally Send Messages, Read Message History, Add Reactions)."
                )
        raise UserError(body)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoUpload(bot))
