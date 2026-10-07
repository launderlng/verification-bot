import io
import logging
import os
import re
import time
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from common import COLOR, SUCCESS, WARN, UserError, support_button
from logutil import emit

log = logging.getLogger("verification-bot")

MAX_FILE_BYTES = 25 * 1024 * 1024  # Discord's upload limit for bots
STORAGE_CAP_MB = int(os.getenv("FILE_STORAGE_MB", "200"))  # per server
COOLDOWN_SECONDS = 10
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,59}$")
# Executables are refused so the bot can't be used to hand members programs. Archives and game assets are fine.
BLOCKED_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".pif", ".lnk", ".hta", ".cpl", ".reg", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".jar", ".apk", ".dll", ".sh", ".app", ".dmg", ".pkg", ".msp", ".gadget",
}


def human_size(n: int) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def check_filename(filename: str) -> None:
    ext = os.path.splitext(filename.lower())[1]
    if ext in BLOCKED_EXTENSIONS:
        raise UserError(f"`{ext}` files are blocked so the bot can't be used to send programs to your members. Put it in a `.zip` first if it's a normal pack.")


async def file_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    rows = await db.fetch_all(
        "SELECT name FROM stored_files WHERE guild_id = ? AND name LIKE ? ORDER BY name LIMIT 25", (interaction.guild_id, f"%{current}%")
    )
    return [app_commands.Choice(name=r["name"], value=r["name"]) for r in rows]


async def used_bytes(guild_id: int) -> int:
    return (await db.fetch_one("SELECT COALESCE(SUM(size), 0) AS n FROM stored_files WHERE guild_id = ?", (guild_id,)))["n"]


def dm_card(guild: discord.Guild, f, title: str = "📥 Your file", note: Optional[str] = None) -> discord.Embed:
    body = ui.kv(("📦 File", f["name"]), ("📄 Filename", f"`{f['filename']}`"), ("📏 Size", human_size(f["size"])), ("📝 About", f["description"]))
    if note:
        body += f"\n\n{ui.DIVIDER}\n{note}"
    return ui.card(title, body, color=SUCCESS, thumbnail=guild.icon.url if guild.icon else None, footer=f"{guild.name} · The file is attached to this message")


async def send_file_dm(user: discord.abc.User, guild: discord.Guild, f, title: str = "📥 Your file", note: Optional[str] = None) -> bool:
    """DM the stored file (a row that includes `data`). Returns False if the person has DMs closed or Discord refuses the upload."""
    cfg = await db.get_ticket_config(guild.id)
    link = support_button(guild, cfg)
    kwargs = {"embed": dm_card(guild, f, title, note), "file": discord.File(io.BytesIO(bytes(f["data"])), filename=f["filename"])}
    if link is not None:
        view = discord.ui.View()
        view.add_item(link)
        kwargs["view"] = view
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
class Files(commands.GroupCog, group_name="files", group_description="Store files and send them to members by DM"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.last_sent: dict[tuple[int, int], float] = {}
        super().__init__()

    async def cog_load(self):
        self.bot.add_dynamic_items(GetFileButton)

    async def cog_unload(self):
        try:
            self.bot.remove_dynamic_items(GetFileButton)
        except Exception:
            pass

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
        wait = COOLDOWN_SECONDS - (time.monotonic() - self.last_sent.get((file_id, member.id), -1e9))
        if wait > 0:
            return await interaction.response.send_message(embed=ui.card("⏳ One moment", f"Please wait {int(wait) + 1} seconds before asking again.", color=WARN), ephemeral=True)
        self.last_sent[(file_id, member.id)] = time.monotonic()

        await interaction.response.defer(ephemeral=True)
        if await send_file_dm(member, guild, f):
            await record_delivery(file_id, member.id)
            await interaction.followup.send(embed=ui.card("✅ Sent to your DMs", f"I sent **{f['name']}** to your direct messages. Check your DMs!", color=SUCCESS), ephemeral=True)
            await emit(guild, "files", "File sent", ui.kv(("👤 Member", member.mention), ("📦 File", f["name"]), ("📏 Size", human_size(f["size"]))), footer=f"File #{f['id']}", subject=member.id)
        else:
            self.last_sent.pop((file_id, member.id), None)
            await interaction.followup.send(
                embed=ui.card("📪 I couldn't DM you", "Your DMs are closed. Open **Server Settings → Privacy → Direct Messages** (allow DMs from server members), then press the button again.", color=WARN), ephemeral=True
            )

    # --------------------------------------------------------- commands ----

    async def get_row(self, interaction: discord.Interaction, name: str, with_data: bool = False):
        cols = "*" if with_data else "id, guild_id, name, filename, content_type, size, description, required_role_id, once_per_user, uploaded_by, created_at"
        row = await db.fetch_one(f"SELECT {cols} FROM stored_files WHERE guild_id = ? AND name = ?", (interaction.guild_id, name.strip()))
        if not row:
            raise UserError(f"I can't find a stored file called **{name}**. Start typing in the box and pick one from the list.")
        return row

    @app_commands.command(description="Store a file so posts and shop products can send it by DM")
    @app_commands.describe(
        name="A short name to pick it by, e.g. clothing-pack-1",
        file="The file to store (up to 25 MB)",
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
        if file.size > MAX_FILE_BYTES:
            raise UserError(f"That file is {human_size(file.size)}. The limit is {human_size(MAX_FILE_BYTES)}.")
        cap = STORAGE_CAP_MB * 1024 * 1024
        used = await used_bytes(interaction.guild_id)
        if used + file.size > cap:
            raise UserError(f"That would use {human_size(used + file.size)} of the {human_size(cap)} storage limit. Remove an old file with `/files remove` first.")
        await interaction.response.defer(ephemeral=True)
        data = await file.read()
        try:
            await db.execute(
                "INSERT INTO stored_files (guild_id, name, filename, content_type, size, data, description, required_role_id, once_per_user, uploaded_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (interaction.guild_id, name.strip(), file.filename, file.content_type, len(data), data, description, required_role.id if required_role else None,
                 int(once_per_user), interaction.user.id, discord.utils.utcnow().isoformat()),
            )
        except aiosqlite.IntegrityError:
            raise UserError(f"You already have a file called **{name.strip()}**. Use `/files replace` to change its file.") from None
        embed = ui.card(
            "✅ File stored",
            ui.kv(("📦 Name", name.strip()), ("📄 Filename", f"`{file.filename}`"), ("📏 Size", human_size(len(data))),
                  ("🔒 Needs role", required_role.mention if required_role else None), ("🔁 Once per member", "Yes" if once_per_user else None))
            + "\n\nAdd a **📥 Get file** button to a post with `/post library_file:" + name.strip() + "`, or attach it to a product with `/shop edit delivery_file:...`.",
            color=SUCCESS, guild=interaction.guild, section="Files",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(description="Replace the file inside a stored entry (posts and products keep working)")
    @app_commands.describe(name="Which stored file", file="The new file")
    @app_commands.autocomplete(name=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def replace(self, interaction: discord.Interaction, name: str, file: discord.Attachment):
        row = await self.get_row(interaction, name)
        check_filename(file.filename)
        if file.size > MAX_FILE_BYTES:
            raise UserError(f"That file is {human_size(file.size)}. The limit is {human_size(MAX_FILE_BYTES)}.")
        cap = STORAGE_CAP_MB * 1024 * 1024
        if await used_bytes(interaction.guild_id) - row["size"] + file.size > cap:
            raise UserError(f"That would go over the {human_size(cap)} storage limit.")
        await interaction.response.defer(ephemeral=True)
        data = await file.read()
        await db.execute("UPDATE stored_files SET filename = ?, content_type = ?, size = ?, data = ? WHERE id = ?", (file.filename, file.content_type, len(data), data, row["id"]))
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
            "SELECT f.id, f.name, f.filename, f.size, f.required_role_id, f.once_per_user, COALESCE(SUM(d.count), 0) AS sent, COUNT(d.user_id) AS people "
            "FROM stored_files f LEFT JOIN file_deliveries d ON d.file_id = f.id WHERE f.guild_id = ? GROUP BY f.id ORDER BY f.name",
            (interaction.guild_id,),
        )
        if not rows:
            raise UserError("No files stored yet. Add one with `/files add`.")
        lines = [
            f"**{r['name']}** · `{r['filename']}` · {human_size(r['size'])}\n　📥 sent {r['sent']:,}× to {r['people']:,} people"
            + (f" · 🔒 <@&{r['required_role_id']}>" if r["required_role_id"] else "") + (" · 🔁 once" if r["once_per_user"] else "")
            for r in rows
        ]
        used, cap = await used_bytes(interaction.guild_id), STORAGE_CAP_MB * 1024 * 1024
        embed = ui.card("📦 Stored files", "\n\n".join(lines), guild=interaction.guild, footer=f"{human_size(used)} of {human_size(cap)} used · {interaction.guild.name}")
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
        await interaction.response.send_message(embed=ui.card("🗑️ File removed", f"**{row['name']}** was deleted. Any shop product that used it no longer sends a file.", color=SUCCESS), ephemeral=True)

    @app_commands.command(description="Send a stored file to one member by DM right now")
    @app_commands.describe(name="Which stored file", member="Who should get it")
    @app_commands.autocomplete(name=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def send(self, interaction: discord.Interaction, name: str, member: discord.Member):
        row = await self.get_row(interaction, name, with_data=True)
        await interaction.response.defer(ephemeral=True)
        if await send_file_dm(member, interaction.guild, row, note=f"Sent to you by {interaction.user.display_name}."):
            await record_delivery(row["id"], member.id)
            await emit(interaction.guild, "files", "File sent by staff", ui.kv(("👤 Member", member.mention), ("📦 File", row["name"]), ("🛡️ By", interaction.user.mention)), footer=f"File #{row['id']}", subject=member.id)
            await interaction.followup.send(embed=ui.card("✅ Sent", f"**{row['name']}** was sent to {member.mention}.", color=SUCCESS), ephemeral=True)
        else:
            await interaction.followup.send(embed=ui.card("📪 Couldn't DM them", f"{member.mention} has DMs closed, so I couldn't send it.", color=WARN), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Files(bot))
