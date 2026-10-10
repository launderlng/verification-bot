import asyncio
import io
import logging
import os
import re
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Optional

import aiohttp
import aiosqlite
import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import SUCCESS, WARN, UserError
from fileutil import (ATTACH_LIMIT, LINK_SECONDS, blocked_extension, check_public_url, filename_from_url, human_size, make_token,
                      max_file_bytes, public_base_url, safe_filename, signing_key, storage_cap_bytes, verify_token)
from logutil import emit

log = logging.getLogger("verification-bot")

COOLDOWN_SECONDS = 10
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,59}$")
NEEDS_WEB = (
    "Files over 25 MB are sent as a private download link, so the bot needs a public web address. "
    "In Railway open your service → **Settings → Networking → Generate Domain**, then try again."
)


def files_dir() -> str:
    """Big files live on disk next to the database (on your Railway volume), not inside the database."""
    return os.getenv("FILES_DIR") or os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "files")


def link_key() -> bytes:
    return signing_key(os.getenv("DISCORD_TOKEN", ""))


def check_filename(filename: str) -> None:
    ext = blocked_extension(filename)
    if ext:
        raise UserError(f"`{ext}` files are blocked so the bot can't be used to send programs to your members. Put it in a `.zip` first if it's a normal pack.")


def new_path(guild_id: int) -> str:
    os.makedirs(files_dir(), exist_ok=True)
    return os.path.join(files_dir(), f"{guild_id}-{uuid.uuid4().hex}.bin")


def inside_files_dir(path: Optional[str]) -> bool:
    if not path:
        return False
    root = os.path.realpath(files_dir())
    return os.path.realpath(path).startswith(root + os.sep)


def delete_disk_file(path: Optional[str]) -> None:
    if inside_files_dir(path):
        try:
            os.remove(path)
        except OSError:
            pass


async def read_bytes(f) -> bytes:
    if f["path"]:
        return await asyncio.to_thread(Path(f["path"]).read_bytes)
    return bytes(f["data"])


async def file_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    rows = await db.fetch_all(
        "SELECT name FROM stored_files WHERE guild_id = ? AND name LIKE ? ORDER BY name LIMIT 25", (interaction.guild_id, f"%{current}%")
    )
    return [app_commands.Choice(name=r["name"], value=r["name"]) for r in rows]


async def used_bytes(guild_id: int) -> int:
    return (await db.fetch_one("SELECT COALESCE(SUM(size), 0) AS n FROM stored_files WHERE guild_id = ?", (guild_id,)))["n"]


def dm_card(guild: discord.Guild, f, title: str = "📥 Your file", note: Optional[str] = None, linked: bool = False) -> discord.Embed:
    body = ui.kv(("📦 File", f["name"]), ("📄 Filename", f"`{f['filename']}`"), ("📏 Size", human_size(f["size"])), ("📝 About", f["description"]))
    if linked:
        body += f"\n\n{ui.DIVIDER}\n🔗 This file is {human_size(f['size'])}, too big to attach, so here's a **private download link**. It works for {LINK_SECONDS // 60} minutes. Press the Get file button again for a fresh one."
    if note:
        body += f"\n\n{ui.DIVIDER}\n{note}"
    return ui.card(
        title, body, color=SUCCESS, thumbnail=guild.icon.url if guild.icon else None,
        footer=f"{guild.name} · " + ("Private download link" if linked else "The file is attached to this message"),
    )


async def file_payload(user: discord.abc.User, guild: discord.Guild, f, embed: Optional[discord.Embed] = None, title: str = "📥 Your file",
                       note: Optional[str] = None, support_channel_id: Optional[int] = None) -> Optional[dict]:
    """Everything needed to DM a stored file as keyword arguments for user.send(): the file attached (25 MB or less) or a private
    Download button (bigger), plus a Support button. Pass your own `embed` to put the file in an existing message (e.g. a receipt).
    Returns None if the file can't be sent (missing from disk, or a big file with no web address to serve it from)."""
    cfg = await db.get_ticket_config(guild.id)
    view = discord.ui.View()
    kwargs: dict = {}
    linked = f["size"] > ATTACH_LIMIT
    if not linked:
        try:
            data = await read_bytes(f)
        except OSError:
            log.exception("Stored file %s is missing from disk", f["id"])
            return None
        kwargs["file"] = discord.File(io.BytesIO(data), filename=f["filename"])
    else:
        base = public_base_url()
        if not base or not f["path"] or not os.path.exists(f["path"]):
            return None
        url = f"{base}/dl/{make_token(f['id'], user.id, link_key())}"
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Download", emoji="⬇️", url=url))
    main_embed = embed if embed is not None else dm_card(guild, f, title, note, linked=linked)
    gif_url = f["gif_url"] if "gif_url" in f.keys() else None
    if gif_url:
        kwargs["embeds"] = [main_embed, ui.card(None, None, color=SUCCESS, image=gif_url)]
    else:
        kwargs["embed"] = main_embed
    channel_id = support_channel_id or (cfg["panel_channel_id"] if cfg else None)
    if channel_id:
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Support", emoji="🎫", url=f"https://discord.com/channels/{guild.id}/{channel_id}"))
    if view.children:
        kwargs["view"] = view
    return kwargs


async def send_file_dm(user: discord.abc.User, guild: discord.Guild, f, title: str = "📥 Your file", note: Optional[str] = None) -> bool:
    """DM the stored file. Returns False if the person has DMs closed or the file can't be sent."""
    kwargs = await file_payload(user, guild, f, title=title, note=note)
    if kwargs is None:
        return False
    try:
        await user.send(**kwargs)
        return True
    except discord.HTTPException:
        return False


async def record_delivery(file_id: int, user_id: int) -> None:
    await db.execute(
        "INSERT INTO file_deliveries (file_id, user_id, count, last_at) VALUES (?, ?, 1, ?) "
        "ON CONFLICT(file_id, user_id) DO UPDATE SET count = count + 1, last_at = excluded.last_at",
        (file_id, user_id, discord.utils.utcnow().isoformat()),
    )


async def download_to_disk(url: str, dest: str, limit: int, session=None) -> tuple[str, int, str]:
    """Download a public https file straight to disk, following redirects safely. Returns (filename, size, content type)."""
    own = session is None
    session = session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900, connect=15, sock_read=60))
    try:
        current = url
        for _ in range(5):
            async with session.get(current, allow_redirects=False) as resp:
                location = resp.headers.get("Location")
                if resp.status in (301, 302, 303, 307, 308) and location:
                    try:
                        current = await asyncio.to_thread(check_public_url, urllib.parse.urljoin(current, location))
                    except ValueError as e:
                        raise UserError(str(e)) from None
                    continue
                if resp.status != 200:
                    raise UserError(f"That website answered with an error ({resp.status}). Check the link works in a browser.")
                content_type = resp.headers.get("Content-Type", "")
                if content_type.lower().startswith("text/html"):
                    raise UserError("That link opens a web page, not a file. Use a direct download link that starts the download straight away.")
                length = resp.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > limit:
                    raise UserError(f"That file is {human_size(int(length))}, over the {human_size(limit)} allowed here.")
                size = 0
                with open(dest, "wb") as out:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise UserError(f"That file is bigger than the {human_size(limit)} allowed here.")
                        out.write(chunk)
                return filename_from_url(current, resp.headers.get("Content-Disposition")), size, content_type
        raise UserError("That link redirects too many times.")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise UserError("The download failed or timed out. Try again, or use a faster host.") from None
    except OSError:
        raise UserError("I couldn't save the file: the disk is full or not writable. Check your Railway volume size.") from None
    finally:
        if own:
            await session.close()


class GetFileButton(discord.ui.DynamicItem[discord.ui.Button], template=r"file:get:(?P<id>[0-9]+)"):
    """The '📥 Get file' button for posts. Pressing it DMs the stored file to the member."""

    def __init__(self, file_id: int, label: str = "Get file"):
        super().__init__(discord.ui.Button(label=label, emoji="📥", style=discord.ButtonStyle.success, custom_id=f"file:get:{file_id}"))
        self.file_id = file_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Files")
        if cog is None:
            return await interaction.response.send_message("Files aren't available right now.", ephemeral=True)
        await cog.handle_button(interaction, self.file_id)


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Files(commands.GroupCog, group_name="files", group_description="Store files and send them to members by DM"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_sent: dict[tuple[int, int], float] = {}
        super().__init__()

    async def cog_load(self):
        self.bot.add_dynamic_items(GetFileButton)
        app = getattr(self.bot, "web_app", None)
        if app is not None:
            app.router.add_get("/dl/{token}", self.handle_download)

    async def cog_unload(self):
        try:
            self.bot.remove_dynamic_items(GetFileButton)
        except Exception:
            pass

    # ------------------------------------------------- big-file downloads ----

    async def handle_download(self, request: web.Request) -> web.StreamResponse:
        verified = verify_token(request.match_info.get("token", ""), link_key())
        if not verified:
            return web.Response(status=410, text="This download link has expired or isn't valid. Press the Get file button in Discord for a new one.")
        f = await db.fetch_one("SELECT id, filename, path FROM stored_files WHERE id = ?", (verified[0],))
        if not f or not f["path"] or not inside_files_dir(f["path"]) or not os.path.exists(f["path"]):
            return web.Response(status=404, text="That file isn't available any more.")
        headers = {
            "Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(f["filename"]),
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        }
        return web.FileResponse(f["path"], headers=headers)

    # --------------------------------------------------------- delivery ----

    async def handle_button(self, interaction: discord.Interaction, file_id: int) -> None:
        guild, member = interaction.guild, interaction.user
        f = await db.fetch_one("SELECT * FROM stored_files WHERE id = ? AND guild_id = ?", (file_id, interaction.guild_id))
        if not f:
            return await interaction.response.send_message(embed=ui.card("Not available", "That file isn't available any more.", color=WARN), ephemeral=True)
        if f["required_role_id"] and f["required_role_id"] not in {r.id for r in getattr(member, "roles", [])}:
            return await interaction.response.send_message(embed=ui.card("🔒 This file is for members with a role", f"You need <@&{f['required_role_id']}> to get **{f['name']}**.", color=WARN), ephemeral=True)
        if f["once_per_user"] and await db.fetch_one("SELECT 1 FROM file_deliveries WHERE file_id = ? AND user_id = ?", (file_id, member.id)):
            return await interaction.response.send_message(embed=ui.card("📥 Already sent", "You've already received this file. Check your DMs, or ask staff if you lost it.", color=WARN), ephemeral=True)
        if f["size"] > ATTACH_LIMIT and not public_base_url():
            return await interaction.response.send_message(embed=ui.card("⚙️ Not ready yet", "This file is too big to attach and the bot's download address isn't set up. Please tell a staff member.", color=WARN), ephemeral=True)
        wait = COOLDOWN_SECONDS - (time.monotonic() - self.last_sent.get((file_id, member.id), -1e9))
        if wait > 0:
            return await interaction.response.send_message(embed=ui.card("⏳ One moment", f"Please wait {int(wait) + 1} seconds before asking again.", color=WARN), ephemeral=True)
        self.last_sent[(file_id, member.id)] = time.monotonic()

        await interaction.response.defer(ephemeral=True)
        if await send_file_dm(member, guild, f):
            await record_delivery(file_id, member.id)
            how = "a private download link" if f["size"] > ATTACH_LIMIT else "the file"
            await interaction.followup.send(embed=ui.card("✅ Sent to your DMs", f"I sent **{f['name']}** ({how}) to your direct messages. Check your DMs!", color=SUCCESS), ephemeral=True)
            await emit(guild, "files", "File sent", ui.kv(("👤 Member", member.mention), ("📦 File", f["name"]), ("📏 Size", human_size(f["size"]))), footer=f"File #{f['id']}", subject=member.id)
        else:
            self.last_sent.pop((file_id, member.id), None)
            await interaction.followup.send(
                embed=ui.card("📪 I couldn't DM you", "Your DMs are closed. Open **Server Settings → Privacy → Direct Messages** (allow DMs from server members), then press the button again.", color=WARN), ephemeral=True
            )

    # --------------------------------------------------------- commands ----

    async def get_row(self, interaction: discord.Interaction, name: str, with_data: bool = False):
        cols = "*" if with_data else "id, guild_id, name, filename, content_type, size, description, required_role_id, once_per_user, uploaded_by, created_at, path"
        row = await db.fetch_one(f"SELECT {cols} FROM stored_files WHERE guild_id = ? AND name = ?", (interaction.guild_id, name.strip()))
        if not row:
            raise UserError(f"I can't find a stored file called **{name}**. Start typing in the box and pick one from the list.")
        return row

    async def room_left(self, guild_id: int, replacing: int = 0) -> int:
        return storage_cap_bytes() - (await used_bytes(guild_id) - replacing)

    async def insert_row(self, interaction, name, filename, content_type, size, data, path, description, role, once) -> None:
        try:
            await db.execute(
                "INSERT INTO stored_files (guild_id, name, filename, content_type, size, data, description, required_role_id, once_per_user, uploaded_by, created_at, path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (interaction.guild_id, name.strip(), filename, content_type, size, data, description, role.id if role else None, int(once),
                 interaction.user.id, discord.utils.utcnow().isoformat(), path),
            )
        except aiosqlite.IntegrityError:
            delete_disk_file(path)
            raise UserError(f"You already have a file called **{name.strip()}**. Use `/files replace` to change its file.") from None

    async def store_bytes(self, interaction, name, filename, content_type, data: bytes, description, role, once) -> str:
        """Small files go in the database, big ones on disk. Returns how it will be delivered."""
        if len(data) <= ATTACH_LIMIT:
            await self.insert_row(interaction, name, filename, content_type, len(data), data, None, description, role, once)
            return "📎 sent as an attachment"
        path = new_path(interaction.guild_id)
        try:
            await asyncio.to_thread(Path(path).write_bytes, data)
        except OSError:
            delete_disk_file(path)
            raise UserError("I couldn't save the file: the disk is full or not writable. Check your Railway volume size.") from None
        await self.insert_row(interaction, name, filename, content_type, len(data), b"", path, description, role, once)
        return "🔗 sent as a private download link"

    def stored_card(self, interaction, name, filename, size, delivery, role, once) -> discord.Embed:
        return ui.card(
            "✅ File stored",
            ui.kv(("📦 Name", name.strip()), ("📄 Filename", f"`{filename}`"), ("📏 Size", human_size(size)), ("🚚 Delivery", delivery),
                  ("🔒 Needs role", role.mention if role else None), ("🔁 Once per member", "Yes" if once else None))
            + "\n\nAdd a **📥 Get file** button to a post with `/post library_file:" + name.strip() + "`, or attach it to a product with `/shop edit delivery_file:...`.",
            color=SUCCESS, guild=interaction.guild, section="Files",
        )

    @app_commands.command(description="Store a file so posts and shop products can send it by DM")
    @app_commands.describe(
        name="A short name to pick it by, e.g. clothing-pack-1",
        file="The file to store (big files get a private download link)",
        description="Shown to members in the DM",
        required_role="Only members with this role can get it",
        once_per_user="Each member can only receive it once",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 60],
        file: discord.Attachment,
        description: Optional[app_commands.Range[str, 1, 300]] = None,
        required_role: Optional[discord.Role] = None,
        once_per_user: bool = False,
    ):
        if not NAME_RE.match(name.strip()):
            raise UserError("Use letters, numbers, spaces, dashes and underscores for the name.")
        check_filename(file.filename)
        if file.size > max_file_bytes():
            raise UserError(f"That file is {human_size(file.size)}. The limit is {human_size(max_file_bytes())}. Raise it with the `MAX_FILE_MB` variable.")
        if file.size > ATTACH_LIMIT and not public_base_url():
            raise UserError(NEEDS_WEB)
        if file.size > await self.room_left(interaction.guild_id):
            raise UserError(f"That would go over the {human_size(storage_cap_bytes())} storage limit. Remove an old file with `/files remove` or raise `FILE_STORAGE_MB`.")
        await interaction.response.defer(ephemeral=True)
        data = await file.read()
        delivery = await self.store_bytes(interaction, name, file.filename, file.content_type, data, description, required_role, once_per_user)
        await interaction.followup.send(embed=self.stored_card(interaction, name, file.filename, len(data), delivery, required_role, once_per_user), ephemeral=True)

    @app_commands.command(name="add_link", description="Store a big file by downloading it from a direct link")
    @app_commands.describe(
        name="A short name to pick it by, e.g. clothing-pack-1",
        url="A direct https download link (it must start the download right away)",
        description="Shown to members in the DM",
        required_role="Only members with this role can get it",
        once_per_user="Each member can only receive it once",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_link(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 60],
        url: str,
        description: Optional[app_commands.Range[str, 1, 300]] = None,
        required_role: Optional[discord.Role] = None,
        once_per_user: bool = False,
    ):
        if not NAME_RE.match(name.strip()):
            raise UserError("Use letters, numbers, spaces, dashes and underscores for the name.")
        try:
            url = await asyncio.to_thread(check_public_url, url)
        except ValueError as e:
            raise UserError(str(e)) from None
        if await db.fetch_one("SELECT 1 FROM stored_files WHERE guild_id = ? AND name = ?", (interaction.guild_id, name.strip())):
            raise UserError(f"You already have a file called **{name.strip()}**. Pick another name, or use `/files replace`.")
        limit = min(max_file_bytes(), await self.room_left(interaction.guild_id))
        if limit <= 0:
            raise UserError(f"You've used all of the {human_size(storage_cap_bytes())} storage. Remove an old file or raise `FILE_STORAGE_MB`.")
        await interaction.response.defer(ephemeral=True)
        dest = new_path(interaction.guild_id)
        try:
            filename, size, content_type = await download_to_disk(url, dest, limit)
            check_filename(filename)
            if size > ATTACH_LIMIT and not public_base_url():
                raise UserError(NEEDS_WEB)
            if size <= ATTACH_LIMIT:
                data = await asyncio.to_thread(Path(dest).read_bytes)
                delete_disk_file(dest)
                delivery = await self.store_bytes(interaction, name, filename, content_type, data, description, required_role, once_per_user)
            else:
                await self.insert_row(interaction, name, filename, content_type, size, b"", dest, description, required_role, once_per_user)
                delivery = "🔗 sent as a private download link"
        except UserError:
            delete_disk_file(dest)
            raise
        await interaction.followup.send(embed=self.stored_card(interaction, name, filename, size, delivery, required_role, once_per_user), ephemeral=True)

    @app_commands.command(description="Replace the file inside a stored entry (posts and products keep working)")
    @app_commands.describe(name="Which stored file", file="The new file")
    @app_commands.autocomplete(name=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def replace(self, interaction: discord.Interaction, name: str, file: discord.Attachment):
        row = await self.get_row(interaction, name)
        check_filename(file.filename)
        if file.size > max_file_bytes():
            raise UserError(f"That file is {human_size(file.size)}. The limit is {human_size(max_file_bytes())}.")
        if file.size > ATTACH_LIMIT and not public_base_url():
            raise UserError(NEEDS_WEB)
        if file.size > await self.room_left(interaction.guild_id, replacing=row["size"]):
            raise UserError(f"That would go over the {human_size(storage_cap_bytes())} storage limit.")
        await interaction.response.defer(ephemeral=True)
        data = await file.read()
        path = None
        if len(data) > ATTACH_LIMIT:
            path = new_path(interaction.guild_id)
            try:
                await asyncio.to_thread(Path(path).write_bytes, data)
            except OSError:
                delete_disk_file(path)
                raise UserError("I couldn't save the file: the disk is full or not writable.") from None
        await db.execute(
            "UPDATE stored_files SET filename = ?, content_type = ?, size = ?, data = ?, path = ? WHERE id = ?",
            (file.filename, file.content_type, len(data), b"" if path else data, path, row["id"]),
        )
        delete_disk_file(row["path"])
        await interaction.followup.send(embed=ui.card("✅ File replaced", f"**{row['name']}** now contains `{file.filename}` ({human_size(len(data))}). Existing buttons send the new file.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Change a stored file's description, role requirement or once-per-member rule")
    @app_commands.describe(name="Which stored file", description="New description", required_role="Only this role can get it", clear_role="Remove the role requirement", once_per_user="Each member can only receive it once")
    @app_commands.autocomplete(name=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        description: Optional[app_commands.Range[str, 1, 300]] = None,
        required_role: Optional[discord.Role] = None,
        clear_role: bool = False,
        once_per_user: Optional[bool] = None,
    ):
        row = await self.get_row(interaction, name)
        updates: dict = {}
        if description:
            updates["description"] = description
        if clear_role:
            updates["required_role_id"] = None
        elif required_role:
            updates["required_role_id"] = required_role.id
        if once_per_user is not None:
            updates["once_per_user"] = int(once_per_user)
        if not updates:
            raise UserError("Nothing to change. Fill in at least one option.")
        assignments = ", ".join(f"{col} = ?" for col in updates)
        await db.execute(f"UPDATE stored_files SET {assignments} WHERE id = ?", (*updates.values(), row["id"]))
        await interaction.response.send_message(embed=ui.card("✅ Saved", f"Updated **{row['name']}**.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="See your stored files and how often they were sent")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        rows = await db.fetch_all(
            "SELECT f.id, f.name, f.filename, f.size, f.path, f.required_role_id, f.once_per_user, COALESCE(SUM(d.count), 0) AS sent, COUNT(d.user_id) AS people "
            "FROM stored_files f LEFT JOIN file_deliveries d ON d.file_id = f.id WHERE f.guild_id = ? GROUP BY f.id ORDER BY f.name",
            (interaction.guild_id,),
        )
        if not rows:
            raise UserError("No files stored yet. Add one with `/files add`, or `/files add_link` for a big one.")
        lines = [
            f"**{r['name']}** · `{r['filename']}` · {human_size(r['size'])} · {'🔗 link' if r['size'] > ATTACH_LIMIT else '📎 attached'}\n　📥 sent {r['sent']:,}× to {r['people']:,} people"
            + (f" · 🔒 <@&{r['required_role_id']}>" if r["required_role_id"] else "") + (" · 🔁 once" if r["once_per_user"] else "")
            for r in rows
        ]
        used = await used_bytes(interaction.guild_id)
        embed = ui.card("📦 Stored files", "\n\n".join(lines), guild=interaction.guild, footer=f"{human_size(used)} of {human_size(storage_cap_bytes())} used · {interaction.guild.name}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(description="Delete a stored file (buttons that used it stop working)")
    @app_commands.describe(name="Which stored file")
    @app_commands.autocomplete(name=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove(self, interaction: discord.Interaction, name: str):
        row = await self.get_row(interaction, name)
        await db.execute("UPDATE products SET file_id = NULL WHERE file_id = ?", (row["id"],))
        await db.execute("DELETE FROM file_deliveries WHERE file_id = ?", (row["id"],))
        await db.execute("DELETE FROM stored_files WHERE id = ?", (row["id"],))
        delete_disk_file(row["path"])
        await interaction.response.send_message(embed=ui.card("🗑️ File removed", f"**{row['name']}** was deleted. Any shop product that used it no longer sends a file.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Send a stored file to one member by DM right now")
    @app_commands.describe(name="Which stored file", member="Who should get it")
    @app_commands.autocomplete(name=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def send(self, interaction: discord.Interaction, name: str, member: discord.Member):
        row = await self.get_row(interaction, name, with_data=True)
        if row["size"] > ATTACH_LIMIT and not public_base_url():
            raise UserError(NEEDS_WEB)
        await interaction.response.defer(ephemeral=True)
        if await send_file_dm(member, interaction.guild, row, note=f"Sent to you by {interaction.user.display_name}."):
            await record_delivery(row["id"], member.id)
            await emit(interaction.guild, "files", "File sent by staff", ui.kv(("👤 Member", member.mention), ("📦 File", row["name"]), ("🛡️ By", interaction.user.mention)), footer=f"File #{row['id']}", subject=member.id)
            await interaction.followup.send(embed=ui.card("✅ Sent", f"**{row['name']}** was sent to {member.mention}.", color=SUCCESS), ephemeral=True)
        else:
            await interaction.followup.send(embed=ui.card("📪 Couldn't DM them", f"{member.mention} has DMs closed, so I couldn't send it.", color=WARN), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Files(bot))
