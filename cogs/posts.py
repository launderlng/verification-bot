import asyncio
import io
import json
import logging
import os
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import db
import ui
from cogs.files import GetFileButton, file_autocomplete
from common import COLOR, UserError, check_can_send, parse_color
from logutil import emit

log = logging.getLogger("verification-bot")
LINK_RE = re.compile(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def https_url(url: str, what: str) -> str:
    url = url.strip()
    if not url.startswith("https://") or " " in url or len(url) > 512:
        raise UserError(f"The {what} link must start with `https://` and be under 512 characters.")
    return url


def unique_name(wanted: str, taken: set) -> str:
    name = wanted
    while name in taken:
        name = f"file_{name}"
    taken.add(name)
    return name


MAX_EMBEDS = 10  # Discord's limit per message
GALLERY_URL = "https://discord.com/"  # any fixed link: embeds sharing it render as one image grid


def chunk_files(items: list, size_limit: int, per_message: int = 10) -> list[list]:
    """Groups (name, bytes, size) items into messages of at most 10 files that stay under the upload limit."""
    chunks, cur, cur_size = [], [], 0
    for item in items:
        if item[1] is None:
            continue
        if cur and (len(cur) >= per_message or cur_size + item[2] > size_limit):
            chunks.append(cur)
            cur, cur_size = [], 0
        cur.append(item)
        cur_size += item[2]
    if cur:
        chunks.append(cur)
    return chunks


class Draft:
    """Everything a post is made of. New media is kept as bytes; media already on a posted message is kept by name only."""

    def __init__(self, channel, title: str, description: Optional[str], footer: Optional[str], color: Optional[int], photo=None, photo_url=None,
                 gif=None, gif_url=None, file=None, button=None, deliver=None, ping=None, pack: Optional[str] = None, mention_all: Optional[str] = None,
                 show_file_field: bool = True, content: Optional[str] = None, extra_file=None, more_photos=None):
        self.channel, self.title, self.description, self.footer, self.color = channel, title, description, footer, color
        self.photo, self.photo_url, self.gif, self.gif_url, self.file, self.button = photo, photo_url, gif, gif_url, file, button
        self.deliver = deliver  # (stored file id, name): a 📥 Get file button that DMs the file
        self.more_photos = list(more_photos or [])  # extra images shown with the main photo as a gallery
        self.extra_file = extra_file  # (name, bytes, size): a second raw attachment alongside self.file -- e.g.
        # the real deliverable attached straight to the post when it's small enough, on top of a video preview
        # already occupying self.file
        self.ping, self.pack = ping, pack  # a role to ping, and what pack this is (for the log)
        self.mention_all = mention_all  # 'everyone' or 'here'
        self.show_file_field = show_file_field  # False hides the "📎 File <name>" field even when self.file is set
        self.content = content  # plain message text shown above everything -- Discord always renders this (and
        # raw file attachments) above any embeds, regardless of API call order, so this is the only way to put a
        # title-like line ahead of a raw video/audio attachment instead of it looking stuck below one

    # photo / gif / file / extra_file are (name, bytes-or-None, size)
    def gallery(self) -> tuple[list, list]:
        """Splits the extra photos into the ones shown in the post's embeds and the overflow that goes in its
        own message. A message holds at most 10 embeds (main card + GIF + gallery images)."""
        room = MAX_EMBEDS - 1 - (1 if (self.gif or self.gif_url) else 0)
        try:
            cap = int(self.channel.guild.filesize_limit)
        except Exception:
            cap = 10 * 1024 * 1024
        used = sum(item[2] for item in (self.photo, self.gif, self.file) if item and item[1] is not None)
        shown = []
        for i, item in enumerate(self.more_photos):
            new = item[2] if item[1] is not None else 0  # photos already on the message don't upload again
            if len(shown) >= room or used + new > cap:
                return shown, self.more_photos[i:]
            shown.append(item)
            used += new
        return shown, []

    def files(self, include_extra: bool = True) -> list[discord.File]:
        shown, _ = self.gallery()
        items = [self.photo, *shown, self.gif, self.file] + ([self.extra_file] if include_extra else [])
        return [discord.File(io.BytesIO(item[1]), filename=item[0]) for item in items if item and item[1] is not None]

    def file_below(self) -> bool:
        """The deliverable (extra_file) goes in its own message right under the post. Discord always draws a
        message's attachments ABOVE its embeds, so sending it separately is the only way to get the file at the bottom."""
        return bool(self.extra_file and self.extra_file[1] is not None)

    def embeds(self) -> list[discord.Embed]:
        color = self.color if self.color is not None else COLOR
        main_image = f"attachment://{self.photo[0]}" if self.photo else self.photo_url
        has_file_field = bool(self.file and self.show_file_field)
        embeds = []
        if self.title or self.description or self.footer or main_image or has_file_field:
            main = ui.card(self.title, self.description, color=color, footer=self.footer)
            if main_image:
                main.set_image(url=main_image)  # the big photo
            if has_file_field:
                main.add_field(name="📎 File", value=f"`{self.file[0]}` · {human_size(self.file[2])}\nAttached to this post ⬇️", inline=False)
            embeds.append(main)
        shown, _ = self.gallery()
        if shown and embeds and main_image:
            # Embeds sharing one url are drawn by Discord as a single image grid (up to 4 per grid), so the
            # extra photos sit together with the main one instead of as a stack of separate cards.
            for i, item in enumerate(shown):
                group = (i + 1) // 4  # the main photo is image 0 of grid 0
                anchor = f"{GALLERY_URL}?g={group}"
                if group == 0:
                    embeds[0].url = anchor
                embeds.append(discord.Embed(url=anchor, color=color).set_image(url=f"attachment://{item[0]}"))
        gif_image = f"attachment://{self.gif[0]}" if self.gif else self.gif_url
        if gif_image:
            embeds.append(ui.card(None, None, color=color, image=gif_image))  # the GIF gets its own spot under the card
        return embeds

    def view(self) -> Optional[discord.ui.View]:
        if not self.button and not self.deliver:
            return None
        view = discord.ui.View(timeout=None)
        if self.deliver:
            view.add_item(GetFileButton(self.deliver[0]))
        if self.button:
            view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=self.button[0], url=self.button[1]))
        return view

    def kwargs(self, include_extra: bool = True) -> dict:
        if self.ping or self.mention_all:
            mentions = discord.AllowedMentions(roles=[self.ping] if self.ping else False, users=False, everyone=bool(self.mention_all))
        else:
            mentions = discord.AllowedMentions.none()
        kw = {"embeds": self.embeds(), "allowed_mentions": mentions}
        mention_text = " ".join(x for x in ({"everyone": "@everyone", "here": "@here"}.get(self.mention_all), self.ping.mention if self.ping else None) if x)
        content = " ".join(x for x in (self.content, mention_text) if x)
        if content:
            kw["content"] = content
        files = self.files(include_extra)
        if files:
            kw["files"] = files
        view = self.view()
        if view is not None:
            kw["view"] = view
        return kw

    def data(self) -> str:
        """What's saved in the database so the post can be edited later."""
        return json.dumps({
            "title": self.title, "description": self.description, "footer": self.footer, "color": self.color,
            "photo": [self.photo[0], self.photo[2]] if self.photo else None, "photo_url": self.photo_url,
            "gif": [self.gif[0], self.gif[2]] if self.gif else None, "gif_url": self.gif_url,
            "file": [self.file[0], self.file[2]] if self.file else None, "button": list(self.button) if self.button else None,
            "extra_file": [self.extra_file[0], self.extra_file[2]] if self.extra_file else None,
            "more_photos": [[p[0], p[2]] for p in self.more_photos] or None,
            "deliver": list(self.deliver) if self.deliver else None, "ping": self.ping.id if self.ping else None, "pack": self.pack, "mention_all": self.mention_all,
            "content": self.content,
        })

    @classmethod
    def from_data(cls, channel, raw: str, guild: discord.Guild) -> "Draft":
        d = json.loads(raw)
        ping = guild.get_role(d["ping"]) if d.get("ping") else None
        return cls(channel, d["title"], d["description"], d["footer"], d["color"], photo=(d["photo"][0], None, d["photo"][1]) if d["photo"] else None, photo_url=d["photo_url"],
                   gif=(d["gif"][0], None, d["gif"][1]) if d["gif"] else None, gif_url=d["gif_url"], file=(d["file"][0], None, d["file"][1]) if d["file"] else None,
                   extra_file=(d["extra_file"][0], None, d["extra_file"][1]) if d.get("extra_file") else None,
                   more_photos=[(p[0], None, p[1]) for p in (d.get("more_photos") or [])],
                   content=d.get("content"),
                   button=tuple(d["button"]) if d["button"] else None, deliver=tuple(d["deliver"]) if d["deliver"] else None, ping=ping, pack=d.get("pack"), mention_all=d.get("mention_all"))


def describe_post(draft: Draft) -> list:
    return [
        ("📝 Title", draft.title), ("📦 Pack", draft.pack), ("🖼️ Photo", draft.photo[0] if draft.photo else draft.photo_url),
        ("🖼️ More photos", str(len(draft.more_photos)) if draft.more_photos else None), ("🎞️ GIF", draft.gif[0] if draft.gif else draft.gif_url),
        ("📎 File", f"{draft.file[0]} ({human_size(draft.file[2])})" if draft.file else None), ("📥 Get-file button", draft.deliver[1] if draft.deliver else None),
        ("🔗 Button", f"{draft.button[0]} → {draft.button[1]}" if draft.button else None), ("🔔 Pinged", " ".join(x for x in ({"everyone": "@everyone", "here": "@here"}.get(draft.mention_all), draft.ping.mention if draft.ping else None) if x) or None),
    ]


async def publish_draft(guild: discord.Guild, user, draft: Draft) -> discord.Message:
    """Send a post, record it so it can be edited later, and log it. Used by /post new and /post bulk."""
    below = draft.file_below()
    message = await draft.channel.send(**draft.kwargs(include_extra=not below))
    _, overflow = draft.gallery()
    for chunk in (chunk_files(overflow, draft.channel.guild.filesize_limit) if overflow else []):  # photos that didn't fit
        try:
            await draft.channel.send(files=[discord.File(io.BytesIO(d), filename=n) for n, d, _ in chunk], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Couldn't post extra photos under post %s", message.id)
    if below:  # the file goes underneath the card, image and GIF
        name, data, _ = draft.extra_file
        try:
            await draft.channel.send(file=discord.File(io.BytesIO(data), filename=name), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            log.exception("Couldn't post the file under post %s", message.id)
    await db.execute(
        "INSERT INTO posts (guild_id, channel_id, message_id, author_id, title, pack_name, file_name, created_at, data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (guild.id, draft.channel.id, message.id, user.id, draft.title, draft.pack, draft.file[0] if draft.file else None, discord.utils.utcnow().isoformat(), draft.data()),
    )
    await emit(
        guild, "posts", "Post published",
        ui.kv(("🛡️ By", user.mention), ("📍 Channel", draft.channel.mention), *describe_post(draft), ("🔗 Link", f"[Jump to post]({message.jump_url})")), subject=user.id,
    )
    return message


class ConfirmView(discord.ui.View):
    def __init__(self, user_id: int, draft: Draft):
        super().__init__(timeout=300)
        self.user_id, self.draft = user_id, draft

    @discord.ui.button(label="Post it", emoji="📨", style=discord.ButtonStyle.success)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who made this post can confirm it.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content="⏳ Posting…", embeds=[], attachments=[], view=None)
        try:
            message = await publish_draft(interaction.guild, interaction.user, self.draft)
        except discord.HTTPException as e:
            return await interaction.edit_original_response(content=f"I couldn't post that: {e}. Check my permissions and the file sizes.")
        await interaction.edit_original_response(content=f"✅ Posted in {self.draft.channel.mention}: {message.jump_url}\nEdit it later with `/post edit`.")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who made this post can cancel it.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was posted.", embeds=[], attachments=[], view=None)


class PostModal(discord.ui.Modal):
    """The writing pop-up. For a new post it's blank; when editing, it's filled with what the post says now."""

    def __init__(self, cog: "Posts", channel, color, photo, photo_url, gif, gif_url, file, deliver=None, ping=None, pack=None, existing: Optional[Draft] = None, edit_target=None, mention_all=None):
        super().__init__(title="Edit your post" if existing else "Write your post")
        self.cog, self.channel, self.color, self.deliver, self.ping, self.pack = cog, channel, color, deliver, ping, pack
        self.mention_all = mention_all
        self.photo, self.photo_url, self.gif, self.gif_url, self.file = photo, photo_url, gif, gif_url, file
        self.existing, self.edit_target = existing, edit_target
        e = existing
        self.title_input = discord.ui.TextInput(label="Title", max_length=256, placeholder="Headline of the card", default=(e.title if e else None) or None)
        self.text_input = discord.ui.TextInput(label="Text", style=discord.TextStyle.paragraph, required=False, max_length=3500, placeholder="Write as much as you like. Multiple lines are fine.", default=(e.description if e else None) or None)
        self.footer_input = discord.ui.TextInput(label="Footer (small text at the bottom)", required=False, max_length=200, default=(e.footer if e else None) or None)
        self.button_label = discord.ui.TextInput(label="Button text (optional)", required=False, max_length=30, placeholder="e.g. Download, Join, Read more", default=(e.button[0] if e and e.button else None))
        self.button_url = discord.ui.TextInput(label="Button link (optional, https://…)", required=False, max_length=512, default=(e.button[1] if e and e.button else None))
        for item in (self.title_input, self.text_input, self.footer_input, self.button_label, self.button_url):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await (self.cog.apply_edit(interaction, self) if self.edit_target else self.cog.preview(interaction, self))
        except UserError as e:
            if interaction.response.is_done():
                await interaction.followup.send(str(e), ephemeral=True)
            else:
                await interaction.response.send_message(str(e), ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Post modal error", exc_info=error)


BULK_PAUSE = 0.6
MAX_BULK = 25
BULK_EXAMPLE = [
    {"title": "14K Triad Clothing Pack", "text": "21 pieces for male and female, 2 variants each.", "channel": "fivem-clothing", "photo_url": "https://example.com/preview.png",
     "library_file": "14k-triad", "ping": "New RZ Releases", "pack_name": "14K Triad", "color": "#111111", "button": {"label": "Install guide", "url": "https://example.com/guide"}, "footer": "14K"},
    {"title": "New ReShade: Midnight", "text": "A dark, cinematic preset.", "channel": "reshades", "gif_url": "https://example.com/before-after.gif", "ping": "New ReShades", "pack_name": "Midnight ReShade"},
]


def find_channel(guild: discord.Guild, wanted) -> tuple:
    """A channel by ID, #name or name. Returns (channel or None, problem or None)."""
    text = str(wanted or "").strip().lstrip("#<>").rstrip(">")
    if text.isdigit():
        channel = guild.get_channel(int(text))
        return (channel, None) if isinstance(channel, discord.TextChannel) else (None, f"no text channel with ID {text}")
    matches = [c for c in guild.text_channels if c.name.lower() == text.lower() or c.name.lower().endswith(("・" + text.lower(), "│" + text.lower(), "┃" + text.lower()))]
    if len(matches) == 1:
        return matches[0], None
    return None, f"no channel called #{text}" if not matches else f"{len(matches)} channels are called #{text}, use the channel ID instead"


async def parse_bulk(guild: discord.Guild, raw: bytes) -> tuple[list, list]:
    """Turn a JSON file of packs into drafts. Returns (list of (draft, skipped-already-posted), problems). Nothing is posted here."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return [], ["That isn't valid JSON. Run `/post template` to get a working example."]
    items = data.get("packs") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return [], ["The file needs a list of packs. Run `/post template` to see the format."]
    if len(items) > MAX_BULK:
        return [], [f"That's {len(items)} packs. Post at most {MAX_BULK} at a time."]
    drafts, problems = [], []
    for number, item in enumerate(items, start=1):
        label = f"Pack {number}" + (f" ({item.get('title')})" if isinstance(item, dict) and item.get("title") else "")
        if not isinstance(item, dict):
            problems.append(f"{label}: each pack must be an object with a title and a channel.")
            continue
        here = []
        title = str(item.get("title") or "").strip()
        if not title or len(title) > 256:
            here.append("needs a `title` (up to 256 characters)")
        channel, why = find_channel(guild, item.get("channel"))
        if channel is None:
            here.append(why or "needs a `channel`")
        else:
            try:
                check_can_send(channel, guild.me)
            except UserError as e:
                here.append(str(e))
        urls = {}
        for key in ("photo_url", "gif_url"):
            if item.get(key):
                try:
                    urls[key] = https_url(str(item[key]), key.replace("_url", ""))
                except UserError as e:
                    here.append(str(e))
        deliver = None
        if item.get("library_file"):
            row = await db.fetch_one("SELECT id, name FROM stored_files WHERE guild_id = ? AND name = ?", (guild.id, str(item["library_file"]).strip()))
            if row:
                deliver = (row["id"], row["name"])
            else:
                here.append(f"no stored file called `{item['library_file']}` (add it with `/files add_link` first)")
        ping, mention_all = None, None
        if item.get("ping"):
            wanted = str(item["ping"]).strip().lstrip("@")
            if wanted.lower() in ("everyone", "here"):
                mention_all = wanted.lower()
                if not guild.me.guild_permissions.mention_everyone:
                    here.append("I don't have the Mention Everyone permission, so I can't ping @" + wanted.lower())
            else:
                ping = next((r for r in guild.roles if r.name.lower().lstrip("@") == wanted.lower() or str(r.id) == wanted), None)
                if ping is None:
                    here.append(f"no role called `{wanted}`")
                elif not (ping.mentionable or guild.me.guild_permissions.mention_everyone):
                    here.append(f"{ping.mention} isn't mentionable")
        button = None
        if item.get("button"):
            b = item["button"]
            if isinstance(b, dict) and b.get("label") and b.get("url"):
                try:
                    button = (str(b["label"])[:30], https_url(str(b["url"]), "button"))
                except UserError as e:
                    here.append(str(e))
            else:
                here.append("`button` needs a `label` and a `url`")
        color = None
        if item.get("color"):
            try:
                color = parse_color(str(item["color"]))
            except UserError as e:
                here.append(str(e))
        if here:
            problems.append(f"{label}: " + "; ".join(here))
            continue
        pack = str(item.get("pack_name") or title)[:80]
        already = await db.fetch_one("SELECT 1 FROM posts WHERE guild_id = ? AND channel_id = ? AND pack_name = ?", (guild.id, channel.id, pack))
        draft = Draft(channel, title, str(item.get("text") or "").strip() or None, str(item.get("footer") or "").strip()[:200] or None, color, photo_url=urls.get("photo_url"),
                      gif_url=urls.get("gif_url"), button=button, deliver=deliver, ping=ping, pack=pack, mention_all=mention_all)
        drafts.append((draft, bool(already) and not item.get("repost")))
    return drafts, problems


class BulkConfirm(discord.ui.View):
    def __init__(self, cog: "Posts", user_id: int, drafts: list):
        super().__init__(timeout=600)
        self.cog, self.user_id, self.drafts = cog, user_id, drafts

    @discord.ui.button(label="Post them all", emoji="📨", style=discord.ButtonStyle.success)
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who started this can confirm it.", ephemeral=True)
        self.stop()
        todo = [d for d, skipped in self.drafts if not skipped]
        await interaction.response.edit_message(content=f"⏳ Posting {len(todo)} pack(s)…", embed=None, view=None)
        done, failed = [], []
        for draft in todo:
            try:
                message = await publish_draft(interaction.guild, interaction.user, draft)
                done.append(f"✅ **{draft.title}** → {message.jump_url}")
            except discord.HTTPException as e:
                failed.append(f"❌ **{draft.title}**: {e}")
            await asyncio.sleep(BULK_PAUSE)
        skipped = [f"⏭️ **{d.title}** (already posted in {d.channel.mention})" for d, was in self.drafts if was]
        body = "\n".join(done + failed + skipped) or "Nothing to post."
        await interaction.edit_original_response(content=None, embed=ui.card("📦 Packs posted" if not failed else "⚠️ Posted with problems", body[:4000], section="Posts"))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the person who started this can cancel it.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content="Cancelled. Nothing was posted.", embed=None, view=None)


def check_media(photo, gif, photo_url, gif_url):
    if photo is not None and not (photo.content_type or "").startswith("image/"):
        raise UserError("The photo has to be an image (png, jpg, webp or gif).")
    if gif is not None and not ((gif.content_type or "") == "image/gif" or gif.filename.lower().endswith(".gif")):
        raise UserError("That isn't a GIF. Put a `.gif` file in `gif`, or use `photo` for normal images.")
    return (https_url(photo_url, "photo") if photo_url else None), (https_url(gif_url, "GIF") if gif_url else None)


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Posts(commands.GroupCog, group_name="post", group_description="Posts with a big photo, a GIF and a file: write, post and edit"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

    async def read(self, attachment: discord.Attachment, limit: int) -> bytes:
        if attachment.size > limit:
            raise UserError(f"**{attachment.filename}** is {human_size(attachment.size)}. This server's upload limit is {human_size(limit)}.")
        return await attachment.read()

    async def read_media(self, m: PostModal, guild: discord.Guild, taken: set):
        """Read any new photo, GIF and file into (name, bytes, size) triples, with names that can't clash."""
        limit = guild.filesize_limit
        photo = gif = file = None
        if m.photo:
            ext = os.path.splitext(m.photo.filename)[1].lower() or ".png"
            data = await self.read(m.photo, limit)
            photo = (unique_name(f"photo{ext}", taken), data, len(data))
        if m.gif:
            data = await self.read(m.gif, limit)
            gif = (unique_name("animation.gif", taken), data, len(data))
        if m.file:
            data = await self.read(m.file, limit)
            file = (unique_name(m.file.filename, taken), data, len(data))
        total = sum(len(item[1]) for item in (photo, gif, file) if item)
        if total > limit:
            raise UserError(f"Together the files are {human_size(total)}, over this server's {human_size(limit)} limit. Use smaller files.")
        return photo, gif, file

    def read_button(self, m: PostModal):
        label, link = (m.button_label.value or "").strip(), (m.button_url.value or "").strip()
        if bool(label) != bool(link):
            raise UserError("A button needs both a text and a link. Fill in both or leave both empty.")
        return (label, https_url(link, "button")) if label else None

    async def preview(self, interaction: discord.Interaction, m: PostModal) -> None:
        button = self.read_button(m)
        await interaction.response.defer(ephemeral=True)
        photo, gif, file = await self.read_media(m, interaction.guild, set())
        draft = Draft(m.channel, m.title_input.value.strip(), (m.text_input.value or "").strip() or None, (m.footer_input.value or "").strip() or None, m.color,
                      photo=photo, photo_url=m.photo_url if not photo else None, gif=gif, gif_url=m.gif_url if not gif else None, file=file, button=button,
                      deliver=m.deliver, ping=m.ping, pack=m.pack, mention_all=m.mention_all)
        note = f"**Preview.** This is exactly what will be posted in {m.channel.mention}:"
        if m.ping or m.mention_all:
            note += "\n🔔 It will ping " + " ".join(x for x in ({"everyone": "@everyone", "here": "@here"}.get(m.mention_all), m.ping.mention if m.ping else None) if x) + "."
        if m.deliver:
            note += f"\n📥 It will have a **Get file** button that sends **{m.deliver[1]}** to members by DM."
        if button:
            note += f"\n🔗 It will have a **{button[0]}** button linking to {button[1]}"
        await interaction.followup.send(content=note, ephemeral=True, view=ConfirmView(interaction.user.id, draft), embeds=draft.embeds(), files=draft.files())

    async def apply_edit(self, interaction: discord.Interaction, m: PostModal) -> None:
        message, row = m.edit_target
        old = m.existing
        button = self.read_button(m)
        await interaction.response.defer(ephemeral=True)
        # media that stays on the message keeps its name; anything new gets a name that doesn't clash with it
        taken = {a.filename for a in message.attachments}
        for slot, replaced in (("photo", m.photo), ("gif", m.gif)):
            if replaced is not None and getattr(old, slot):
                taken.discard(getattr(old, slot)[0])
        if m.file is not None and old.file:
            taken.discard(old.file[0])
        photo, gif, file = await self.read_media(m, interaction.guild, taken)
        draft = Draft(
            message.channel, m.title_input.value.strip(), (m.text_input.value or "").strip() or None, (m.footer_input.value or "").strip() or None, m.color if m.color is not None else old.color,
            photo=photo or (old.photo if not m.photo_url else None), photo_url=m.photo_url if m.photo_url and not photo else (None if photo else old.photo_url),
            gif=gif or (old.gif if not m.gif_url else None), gif_url=m.gif_url if m.gif_url and not gif else (None if gif else old.gif_url),
            file=file or old.file, button=button, deliver=old.deliver, ping=old.ping, pack=old.pack, mention_all=old.mention_all,
            more_photos=old.more_photos,
        )
        kept = {item[0] for item in (draft.photo, draft.gif, draft.file, *draft.gallery()[0]) if item and item[1] is None}
        keep = [a for a in message.attachments if a.filename in kept]
        try:
            await message.edit(embeds=draft.embeds(), attachments=keep + draft.files(), view=draft.view())
        except discord.HTTPException as e:
            raise UserError(f"I couldn't edit that post: {e}. Check my permissions and the file sizes.") from None
        await db.execute("UPDATE posts SET title = ?, pack_name = ?, file_name = ?, data = ? WHERE id = ?", (draft.title, draft.pack, draft.file[0] if draft.file else None, draft.data(), row["id"]))
        await emit(
            interaction.guild, "posts", "Post edited",
            ui.kv(("🛡️ By", interaction.user.mention), ("📍 Channel", f"<#{message.channel.id}>"), *describe_post(draft), ("🔗 Link", f"[Jump to post]({message.jump_url})")), subject=interaction.user.id,
        )
        await interaction.followup.send(embed=ui.card("✅ Post updated", f"[Jump to it]({message.jump_url})", color=COLOR), ephemeral=True)

    @app_commands.command(name="new", description="Write a post: a card with a big photo, a GIF and a file at the bottom")
    @app_commands.describe(
        channel="Where to post it",
        photo="The big photo shown in the card",
        gif="An animated GIF, shown in its own card under the photo",
        file="A file attached at the bottom (zip, pdf, anything)",
        photo_url="Or a direct https link to the photo",
        gif_url="Or a direct https link to a GIF (must end in .gif)",
        color="Card colour as hex, e.g. #5865F2 (default: black)",
        library_file="A stored file (see /files add) that a 📥 Get file button sends to members by DM",
        ping="A role to ping with the post, e.g. your New ReShades role",
        mention="Ping @everyone or @here (needs the Mention Everyone permission)",
        pack_name="What this is, e.g. 14K Triad clothing pack (shown in the logs)",
    )
    @app_commands.choices(mention=[app_commands.Choice(name="@everyone", value="everyone"), app_commands.Choice(name="@here", value="here")])
    @app_commands.autocomplete(library_file=file_autocomplete)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def new(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        photo: Optional[discord.Attachment] = None,
        gif: Optional[discord.Attachment] = None,
        file: Optional[discord.Attachment] = None,
        photo_url: Optional[str] = None,
        gif_url: Optional[str] = None,
        color: Optional[str] = None,
        library_file: Optional[str] = None,
        ping: Optional[discord.Role] = None,
        pack_name: Optional[app_commands.Range[str, 1, 80]] = None,
        mention: Optional[app_commands.Choice[str]] = None,
    ):
        guild = interaction.guild
        if mention is not None:
            if not interaction.user.guild_permissions.mention_everyone:
                raise UserError("You need the **Mention Everyone** permission to ping @everyone or @here.")
            if not guild.me.guild_permissions.mention_everyone:
                raise UserError("I need the **Mention Everyone** permission to ping @everyone or @here.")
        check_can_send(channel, guild.me, files=bool(photo or gif or file))
        deliver = None
        if library_file:
            row = await db.fetch_one("SELECT id, name FROM stored_files WHERE guild_id = ? AND name = ?", (interaction.guild_id, library_file.strip()))
            if not row:
                raise UserError(f"I can't find a stored file called **{library_file}**. Add one with `/files add`, then pick it from the list.")
            deliver = (row["id"], row["name"])
        if ping is not None and not (ping.mentionable or guild.me.guild_permissions.mention_everyone):
            raise UserError(f"I can't ping {ping.mention} because it isn't mentionable. Turn on *Allow anyone to @mention this role* for it, or run `/pingroles setup`.")
        photo_url, gif_url = check_media(photo, gif, photo_url, gif_url)
        value = parse_color(color) if color else None
        await interaction.response.send_modal(PostModal(self, channel, value, photo, photo_url, gif, gif_url, file, deliver, ping, pack_name.strip() if pack_name else None, mention_all=mention.value if mention else None))

    @app_commands.command(description="Edit a post made with /post new: its text, button, colour or its photo, GIF and file")
    @app_commands.describe(
        message_link="Right-click the post, then Copy Message Link",
        photo="A new big photo",
        gif="A new GIF",
        file="A new file for the bottom",
        photo_url="Or a direct https link for the photo",
        gif_url="Or a direct https link for the GIF",
        color="A new card colour as hex",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def edit(
        self,
        interaction: discord.Interaction,
        message_link: str,
        photo: Optional[discord.Attachment] = None,
        gif: Optional[discord.Attachment] = None,
        file: Optional[discord.Attachment] = None,
        photo_url: Optional[str] = None,
        gif_url: Optional[str] = None,
        color: Optional[str] = None,
    ):
        match = LINK_RE.search(message_link)
        if not match or int(match.group(1)) != interaction.guild_id:
            raise UserError("Paste a message link from this server (right-click the post, then **Copy Message Link**).")
        channel = interaction.guild.get_channel(int(match.group(2)))
        row = await db.fetch_one("SELECT * FROM posts WHERE guild_id = ? AND message_id = ?", (interaction.guild_id, int(match.group(3))))
        if channel is None or row is None or not row["data"]:
            raise UserError("I can only edit posts made with `/post new`. That message isn't one of them (or it was posted before this feature existed).")
        try:
            message = await channel.fetch_message(row["message_id"])
        except discord.HTTPException:
            raise UserError("I can't find that message. Was it deleted?") from None
        photo_url, gif_url = check_media(photo, gif, photo_url, gif_url)
        value = parse_color(color) if color else None
        existing = Draft.from_data(channel, row["data"], interaction.guild)
        await interaction.response.send_modal(PostModal(self, channel, value, photo, photo_url, gif, gif_url, file, existing.deliver, existing.ping, existing.pack, existing, (message, row)))

    @app_commands.command(description="Post many packs at once from a JSON file (everything is checked first)")
    @app_commands.describe(file="A .json file listing your packs (get an example with /post template)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def bulk(self, interaction: discord.Interaction, file: discord.Attachment):
        if file.size > 1_000_000 or not file.filename.lower().endswith(".json"):
            raise UserError("Upload a `.json` file under 1 MB. Run `/post template` for an example.")
        await interaction.response.defer(ephemeral=True)
        drafts, problems = await parse_bulk(interaction.guild, await file.read())
        if problems:
            shown = "\n".join(f"• {p}" for p in problems[:15]) + (f"\n…and {len(problems) - 15} more." if len(problems) > 15 else "")
            return await interaction.followup.send(embed=ui.card("❌ Nothing was posted", f"Fix these in your file and try again:\n\n{shown}", section="Posts"), ephemeral=True)
        lines = [f"{'⏭️' if skipped else '📦'} **{d.title}** → {d.channel.mention}" + (" · already posted, will be skipped" if skipped else "") + (f" · 📥 {d.deliver[1]}" if d.deliver else "")
                 + (" · 🔔" if (d.ping or d.mention_all) else "") for d, skipped in drafts]
        todo = sum(1 for _, skipped in drafts if not skipped)
        await interaction.followup.send(embed=ui.card(f"📦 Ready to post {todo} pack(s)", "\n".join(lines)[:4000] + "\n\nEverything checked out. Each post is recorded and logged.", section="Posts"),
                                        view=BulkConfirm(self, interaction.user.id, drafts), ephemeral=True)

    @app_commands.command(description="Get an example JSON file for /post bulk")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def template(self, interaction: discord.Interaction):
        data = json.dumps({"packs": BULK_EXAMPLE}, indent=2, ensure_ascii=False).encode()
        await interaction.response.send_message(
            content="Edit this file (channel names, titles, links, the stored file's name and the role to ping), then run `/post bulk` with it. Delete any keys you don't need: only `title` and `channel` are required.",
            file=discord.File(io.BytesIO(data), filename="packs-template.json"), ephemeral=True,
        )

    @app_commands.command(description="See the posts made with /post new, with who made them")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def list(self, interaction: discord.Interaction):
        rows = await db.fetch_all("SELECT * FROM posts WHERE guild_id = ? ORDER BY id DESC LIMIT 15", (interaction.guild_id,))
        if not rows:
            raise UserError("No posts yet. Make one with `/post new`.")
        lines = [f"[{r['title'] or 'Untitled'}](https://discord.com/channels/{interaction.guild_id}/{r['channel_id']}/{r['message_id']}) · <#{r['channel_id']}> · <@{r['author_id']}>"
                 + (f" · 📦 {r['pack_name']}" if r["pack_name"] else "") + (f" · 📎 {r['file_name']}" if r["file_name"] else "") for r in rows]
        await interaction.response.send_message(embed=ui.card("📢 Posts", "\n".join(lines), guild=interaction.guild, section="Posts"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Posts(bot))
