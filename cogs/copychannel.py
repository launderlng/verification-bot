import asyncio
import io
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import ui
from common import SUCCESS, WARN, UserError, check_can_send
from fileutil import human_size
from logutil import emit

log = logging.getLogger("verification-bot")

MAX_FILES_PER_MESSAGE = 10
PAUSE_SECONDS = 0.8  # between messages, to stay well inside Discord's rate limits


def plan_uploads(sizes: list[int], limit: int, max_files: int = MAX_FILES_PER_MESSAGE) -> tuple[list[list[int]], list[int]]:
    """Split attachments into groups that each fit in ONE message (at most 10 files and `limit` bytes together).
    Returns (groups of attachment positions, positions of files that are too big to re-upload at all)."""
    groups: list[list[int]] = []
    current: list[int] = []
    total = 0
    too_big: list[int] = []
    for position, size in enumerate(sizes):
        if size > limit:
            too_big.append(position)
            continue
        if current and (len(current) >= max_files or total + size > limit):
            groups.append(current)
            current, total = [], 0
        current.append(position)
        total += size
    if current:
        groups.append(current)
    return groups, too_big


def is_normal(message) -> bool:
    """Real messages and replies only (not joins, pins, boosts and other system messages)."""
    return getattr(getattr(message, "type", None), "name", "default") in ("default", "reply")


class Job:
    def __init__(self, user_id: int, total: int):
        self.user_id, self.total = user_id, total
        self.cancelled = False
        self.done = self.messages = self.files = self.bytes = self.skipped = self.failed = 0
        self.too_big: list[str] = []


class StopView(discord.ui.View):
    def __init__(self, job: Job):
        super().__init__(timeout=None)
        self.job = job

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.job.user_id:
            return await interaction.response.send_message("Only the person who started the copy can stop it.", ephemeral=True)
        self.job.cancelled = True
        await interaction.response.send_message("⏹️ Stopping after the current message…", ephemeral=True)


@app_commands.guild_only()
class CopyChannel(commands.Cog):
    """/copychannel: copy a channel's messages, files and videos into another channel of this server."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.running: dict[int, Job] = {}

    # ----------------------------------------------------------- building ----

    def header_embed(self, message, show_author: bool, content: str) -> Optional[discord.Embed]:
        if not content and not show_author:
            return None
        embed = ui.card(None, content or None)
        if show_author:
            embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
            embed.timestamp = message.created_at
        return embed

    @staticmethod
    def reply_prefix(message) -> str:
        ref = getattr(message, "reference", None)
        resolved = getattr(ref, "resolved", None)
        author = getattr(resolved, "author", None)
        return f"↩️ *replying to **{author.display_name}***\n" if author is not None else ""

    async def make_file(self, attachment) -> discord.File:
        data = await attachment.read()
        return discord.File(io.BytesIO(data), filename=attachment.filename, spoiler=attachment.is_spoiler(), description=getattr(attachment, "description", None))

    async def copy_message(self, message, destination: discord.TextChannel, limit: int, show_author: bool, job: Job) -> None:
        attachments = list(message.attachments)
        content = (self.reply_prefix(message) + (message.content or "")).strip()
        copied_embeds = [discord.Embed.from_dict(e.to_dict()) for e in message.embeds if getattr(e, "type", "rich") == "rich"][:9]
        if not content and not attachments and not copied_embeds:
            job.skipped += 1
            return
        groups, too_big = plan_uploads([a.size for a in attachments], limit)
        header = self.header_embed(message, show_author, content)
        if too_big:
            # Too large to re-upload: keep a link to the original so nothing is lost (it works while the source message exists)
            lines = [f"[{attachments[i].filename}]({attachments[i].url}) · {human_size(attachments[i].size)}" for i in too_big]
            header = header or ui.card(None, None)
            header.add_field(name="⚠️ Too big to copy", value="\n".join(lines)[:1000], inline=False)
            job.too_big += [attachments[i].filename for i in too_big]
        sent_any = False
        for index, group in enumerate(groups or [[]]):
            files = []
            for position in group:
                try:
                    files.append(await self.make_file(attachments[position]))
                except discord.HTTPException:
                    job.failed += 1  # the original file is gone or unreadable
            embeds = ([header] if header is not None else []) + copied_embeds if index == 0 else []
            if not files and not embeds:
                continue
            kwargs: dict = {"allowed_mentions": discord.AllowedMentions.none()}
            if embeds:
                kwargs["embeds"] = embeds
            if files:
                kwargs["files"] = files
            await destination.send(**kwargs)
            sent_any = True
            job.files += len(files)
            job.bytes += sum(attachments[p].size for p in group)
            if index + 1 < len(groups):
                await asyncio.sleep(PAUSE_SECONDS)
        if sent_any:
            job.messages += 1
        else:
            job.skipped += 1

    # ----------------------------------------------------------- progress ----

    def progress_embed(self, job: Job, source, destination, finished: bool = False) -> discord.Embed:
        fraction = job.done / job.total if job.total else 1
        body = (
            f"{source.mention} → {destination.mention}\n\n`{ui.bar(fraction)}` **{job.done:,}** of **{job.total:,}** messages\n\n"
            + ui.kv(("💬 Copied", f"{job.messages:,}"), ("📎 Files & videos", f"{job.files:,} ({human_size(job.bytes)})"), ("⏭️ Skipped", f"{job.skipped:,}" if job.skipped else None),
                    ("❌ Failed", f"{job.failed:,}" if job.failed else None), ("⚠️ Too big to copy", f"{len(job.too_big):,} (linked instead)" if job.too_big else None))
        )
        if finished:
            title = "⏹️ Copy stopped" if job.cancelled else "✅ Copy finished"
            if job.too_big:
                body += "\n\nFiles over this server's upload limit were left as links to the originals: " + ", ".join(f"`{n}`" for n in job.too_big[:5]) + ("…" if len(job.too_big) > 5 else "")
        else:
            title = "📦 Copying…"
        extra = {"color": SUCCESS} if finished and not job.cancelled else {}
        return ui.card(title, body, section="Channel copy", **extra)

    async def update(self, interaction: discord.Interaction, embed: discord.Embed, view=None) -> bool:
        try:
            await interaction.edit_original_response(embed=embed, view=view)
            return True
        except discord.HTTPException:
            return False  # Discord only lets a command edit its reply for 15 minutes

    # ------------------------------------------------------------- command ----

    @app_commands.command(name="copychannel", description="Copy a channel's messages, files and videos into another channel")
    @app_commands.describe(
        source="The channel to copy from",
        destination="The channel to post the copy in",
        limit="How many of the most recent messages to look through (default 100, copied oldest first)",
        only_files="Only copy messages that have files, images or videos",
        show_author="Show who posted each message and when (default: yes)",
        skip_bots="Leave out messages posted by bots (default: yes)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def copychannel(
        self,
        interaction: discord.Interaction,
        source: discord.TextChannel,
        destination: discord.TextChannel,
        limit: app_commands.Range[int, 1, 1000] = 100,
        only_files: bool = False,
        show_author: bool = True,
        skip_bots: bool = True,
    ):
        guild = interaction.guild
        if not self.bot.intents.message_content:
            raise UserError(
                "To read other people's messages and files I need the **Message Content Intent**. Turn it on in the Discord Developer Portal "
                "(your app → Bot → Message Content Intent), add the Railway variable `MESSAGE_CONTENT` = `true`, and let the bot redeploy."
            )
        if source.id == destination.id:
            raise UserError("Pick two different channels.")
        perms = source.permissions_for(guild.me)
        if not (perms.view_channel and perms.read_message_history):
            raise UserError(f"I need **View Channel** and **Read Message History** in {source.mention}.")
        check_can_send(destination, guild.me, files=True)
        if guild.id in self.running:
            raise UserError("A copy is already running in this server. Wait for it to finish, or press **Stop** on it.")

        await interaction.response.defer(ephemeral=True)
        messages = [m async for m in source.history(limit=limit)]
        messages.reverse()  # oldest first, so the copy reads in the same order
        picked = [m for m in messages if is_normal(m) and not (skip_bots and m.author.bot) and (not only_files or m.attachments)]
        if not picked:
            raise UserError("There's nothing to copy: no matching messages in that range.")
        job = Job(interaction.user.id, len(messages))
        job.skipped = len(messages) - len(picked)
        job.done = job.skipped
        self.running[guild.id] = job
        view = StopView(job)
        try:
            await destination.send(embed=ui.card("📦 Copied channel", f"Messages from {source.mention}, oldest first.", footer=f"Copied by {interaction.user.display_name}"), allowed_mentions=discord.AllowedMentions.none())
            last_update = 0.0
            for message in picked:
                if job.cancelled:
                    break
                try:
                    await self.copy_message(message, destination, guild.filesize_limit, show_author, job)
                except discord.HTTPException:
                    job.failed += 1
                    log.exception("Couldn't copy message %s", message.id)
                job.done += 1
                if time.monotonic() - last_update >= 4:
                    last_update = time.monotonic()
                    await self.update(interaction, self.progress_embed(job, source, destination), view)
                await asyncio.sleep(PAUSE_SECONDS)
        finally:
            self.running.pop(guild.id, None)
        summary = self.progress_embed(job, source, destination, finished=True)
        if not await self.update(interaction, summary, None):
            await interaction.channel.send(embed=summary)
        await emit(
            guild, "server", "Channel copied",
            ui.kv(("🛡️ By", interaction.user.mention), ("📤 From", source.mention), ("📥 To", destination.mention), ("💬 Messages", f"{job.messages:,}"), ("📎 Files", f"{job.files:,}")),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(CopyChannel(bot))
