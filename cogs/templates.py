import logging
from collections import Counter
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

from common import COLOR, DANGER, INFO, WARN, UserError
import presets
from templateutil import extract_template_code, is_invite, parse_template, safe_permissions, tree_text

log = logging.getLogger("verification-bot")

MAX_ROLES = 100
MAX_CHANNELS = 150  # categories + channels per import (keeps it inside Discord's 15 minute reply window)
REASON = "Imported from a server template"


def simple(title: str, text: str, color: discord.Color = INFO) -> discord.Embed:
    return discord.Embed(title=title, description=text, color=color)


class ConfirmView(discord.ui.View):
    def __init__(self, cog: "Templates", user_id: int, parsed: dict, skip_existing: bool):
        super().__init__(timeout=180)
        self.cog, self.user_id, self.parsed, self.skip_existing = cog, user_id, parsed, skip_existing

    @discord.ui.button(label="Create everything", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the admin who ran the command can confirm this.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(embed=simple("⏳ Building your server…", "This can take a few minutes. Please leave the channels alone until it's done.", WARN), view=None)
        await self.cog.run_import(interaction, self.parsed, self.skip_existing)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the admin who ran the command can cancel this.", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(embed=simple("Cancelled", "Nothing was changed.", INFO), view=None)


@app_commands.guild_only()
class Templates(commands.GroupCog, group_name="template", group_description="Copy and share server layouts with templates"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.running: set[int] = set()
        super().__init__()

    # ----------------------------------------------------------- helpers ----

    async def fetch_template(self, text: str) -> dict:
        if is_invite(text):
            raise UserError(
                "That's a server **invite**, and invites don't include a server's channels. I can only see inside servers I'm in.\n\n"
                "• To copy another server's layout, ask its owner for a **template link** (Server Settings → Server Template → Generate), "
                "which looks like `https://discord.new/AbCdEfGh`.\n"
                "• To make a template of **your own** server, run `/template create` in it."
            )
        code = extract_template_code(text)
        if not code:
            raise UserError("I couldn't find a template link in that. It looks like `https://discord.new/AbCdEfGh`.")
        try:
            return await self.bot.http.get_template(code)
        except discord.NotFound:
            raise UserError("I can't find that template. It may have been deleted, or the link is wrong.") from None
        except discord.HTTPException:
            raise UserError("Discord wouldn't give me that template right now. Please try again in a moment.") from None

    def existing_names(self, guild: discord.Guild, parsed: dict) -> dict:
        roles = {r.name.lower() for r in guild.roles}
        cats = {c.name.lower() for c in guild.categories}
        chans = {(c.name.lower(), c.category_id) for c in guild.channels if not isinstance(c, discord.CategoryChannel)}
        return {"roles": roles, "categories": cats, "channels": chans}

    def summary_embed(self, parsed: dict, title: str, color: discord.Color = INFO) -> discord.Embed:
        embed = discord.Embed(title=title, description=tree_text(parsed, 2800), color=color)
        embed.add_field(name="Template", value=parsed["name"])
        if parsed["source_name"]:
            embed.add_field(name="From server", value=parsed["source_name"])
        embed.add_field(name="Used", value=f"{parsed['usage_count']:,} times")
        embed.add_field(name="Roles", value=str(len(parsed["roles"])))
        embed.add_field(name="Categories", value=str(len(parsed["categories"])))
        embed.add_field(name="Channels", value=str(len(parsed["channels"])))
        if parsed["description"]:
            embed.add_field(name="Description", value=parsed["description"][:1000], inline=False)
        return embed

    # ----------------------------------------------------------- commands ----

    @app_commands.command(description="Make (or update) a template link of this server's roles and channels")
    @app_commands.describe(name="Template name (default: the server name)", description="Short description shown on the template page")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.bot_has_permissions(manage_guild=True)
    async def create(
        self,
        interaction: discord.Interaction,
        name: Optional[app_commands.Range[str, 1, 100]] = None,
        description: Optional[app_commands.Range[str, 1, 120]] = None,
    ):
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        try:
            existing = await guild.templates()
            if existing:
                template = existing[0]
                await template.sync()
                verb = "updated"
            else:
                template = await guild.create_template(name=name or guild.name[:100], description=description)
                verb = "created"
        except discord.Forbidden:
            raise UserError("I need the **Manage Server** permission to make templates. Give my role that permission and try again.") from None
        except discord.HTTPException as e:
            raise UserError(f"Discord wouldn't make the template: {e.text if hasattr(e, 'text') else e}") from None

        embed = discord.Embed(title=f"🧩 Template {verb}", description=f"**Share this link:**\n{template.url}", color=COLOR)
        embed.add_field(name="Includes", value="Channels, categories, roles and their permissions, plus server settings", inline=False)
        embed.add_field(name="Doesn't include", value="Messages, members, bots and integrations", inline=False)
        embed.set_footer(text="Anyone with the link can use it. It shows the server's name and structure.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(description="Preview a template link: its roles, categories and channels")
    @app_commands.describe(link="A template link like https://discord.new/AbCdEfGh")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def info(self, interaction: discord.Interaction, link: str):
        await interaction.response.defer(ephemeral=True)
        parsed = parse_template(await self.fetch_template(link))
        await interaction.followup.send(embed=self.summary_embed(parsed, f"🧩 {parsed['name']}"), ephemeral=True)

    async def start_import(self, interaction: discord.Interaction, parsed: dict, skip_existing: bool) -> None:
        """Check limits, then show the preview with Confirm / Cancel buttons."""
        guild = interaction.guild
        if len(parsed["roles"]) > MAX_ROLES or len(parsed["categories"]) + len(parsed["channels"]) > MAX_CHANNELS:
            raise UserError(f"That's too big to import in one go (limit: {MAX_ROLES} roles and {MAX_CHANNELS} channels and categories).")
        have = self.existing_names(guild, parsed)
        new_roles = [r for r in parsed["roles"] if not (skip_existing and r["name"].lower() in have["roles"])]
        new_cats = [c for c in parsed["categories"] if not (skip_existing and c["name"].lower() in have["categories"])]
        if len(guild.roles) + len(new_roles) > 250:
            raise UserError("Importing would go over Discord's limit of 250 roles in a server.")
        if len(guild.channels) + len(new_cats) + len(parsed["channels"]) > 500:
            raise UserError("Importing would go over Discord's limit of 500 channels in a server.")

        embed = self.summary_embed(parsed, f"🧩 Copy “{parsed['name']}” here?", WARN)
        notes = ["Nothing in your server is deleted or changed. This only **adds** roles, categories and channels."]
        if skip_existing:
            notes.append("Anything with the same name as something you already have is skipped.")
        notes.append("Roles are created without Administrator, and never with permissions I don't have myself.")
        if parsed["converted"]:
            notes.append(f"{parsed['converted']} announcement/stage/forum channel(s) become normal text/voice channels (those need Community features).")
        if parsed["skipped"]:
            notes.append(f"{parsed['skipped']} channel(s) of an unsupported type are skipped.")
        notes.append("Members and messages aren't copied.")
        notes.extend(parsed.get("notes", []))
        embed.add_field(name="What happens", value="\n".join(f"• {n}" for n in notes)[:1024], inline=False)
        await interaction.followup.send(embed=embed, view=ConfirmView(self, interaction.user.id, parsed, skip_existing), ephemeral=True)

    @app_commands.command(name="import", description="Copy a template's roles and channels into this server")
    @app_commands.describe(link="A template link like https://discord.new/AbCdEfGh", skip_existing="Skip roles/channels that already exist here (default: yes)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True, manage_channels=True)
    async def import_template(self, interaction: discord.Interaction, link: str, skip_existing: bool = True):
        if interaction.guild.id in self.running:
            raise UserError("An import is already running in this server. Please wait for it to finish.")
        await interaction.response.defer(ephemeral=True)
        parsed = parse_template(await self.fetch_template(link))
        await self.start_import(interaction, parsed, skip_existing)

    @app_commands.command(description="Build a ready-made server layout: roles, channels and permissions")
    @app_commands.describe(layout="Which layout to build", skip_existing="Skip roles/channels that already exist here (default: yes)")
    @app_commands.choices(layout=[app_commands.Choice(name="Community server (staff, verification, tickets, voice)", value="community")])
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True, manage_channels=True)
    async def preset(self, interaction: discord.Interaction, layout: app_commands.Choice[str], skip_existing: bool = True):
        if interaction.guild.id in self.running:
            raise UserError("An import is already running in this server. Please wait for it to finish.")
        await interaction.response.defer(ephemeral=True)
        await self.start_import(interaction, presets.build(layout.value), skip_existing)

    # ------------------------------------------------------------ import ----

    def make_overwrites(self, items: list, role_map: dict, me=None) -> dict:
        result = {}
        for role_id, allow, deny in items:
            role = role_map.get(role_id)
            if role is not None:
                result[role] = discord.PermissionOverwrite.from_pair(discord.Permissions(allow), discord.Permissions(deny))
        if result and me is not None:
            # Hidden channels must stay visible to me, or logs, panels and tickets can't work there
            result[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, attach_files=True, read_message_history=True)
        return result

    async def create_channel(self, guild: discord.Guild, c: dict, parent, overwrites: dict, stats: Counter):
        kwargs = {"category": parent, "reason": REASON}
        if overwrites:  # no overwrites at all means "inherit from the category"
            kwargs["overwrites"] = overwrites
        if c["kind"] == "text":
            if c["topic"]:
                kwargs["topic"] = c["topic"][:1024]
            kwargs.update(nsfw=c["nsfw"], slowmode_delay=min(c["slowmode"], 21600))
            make = guild.create_text_channel
        else:
            if c["user_limit"]:
                kwargs["user_limit"] = min(c["user_limit"], 99)
            limit = getattr(guild, "bitrate_limit", 96000)
            if c["bitrate"] and c["bitrate"] <= limit:
                kwargs["bitrate"] = int(c["bitrate"])
            make = guild.create_voice_channel
        try:
            return await make(c["name"], **kwargs)
        except discord.Forbidden:
            if "overwrites" not in kwargs:
                raise
            stats["overwrites_skipped"] += 1  # permission rules I'm not allowed to set, so make it without them
            kwargs.pop("overwrites")
            return await make(c["name"], **kwargs)

    async def build(self, guild: discord.Guild, parsed: dict, skip_existing: bool, progress: Callable[[int, int], Awaitable[None]]) -> tuple[Counter, list[str]]:
        stats: Counter = Counter()
        errors: list[str] = []
        have = self.existing_names(guild, parsed)
        bot_perms = guild.me.guild_permissions.value
        roles_by_name = {r.name.lower(): r for r in guild.roles}
        cats_by_name = {c.name.lower(): c for c in guild.categories}
        role_map: dict = {}
        if parsed["everyone_id"] is not None:
            role_map[parsed["everyone_id"]] = guild.default_role
        total = len(parsed["roles"]) + len(parsed["categories"]) + len(parsed["channels"])
        done = 0

        async def tick():
            nonlocal done
            done += 1
            if done % 8 == 0:
                await progress(done, total)

        for r in parsed["roles"]:
            if skip_existing and r["name"].lower() in have["roles"]:
                role_map[r["id"]] = roles_by_name[r["name"].lower()]
                stats["roles_skipped"] += 1
            else:
                try:
                    role = await guild.create_role(
                        name=r["name"], permissions=discord.Permissions(safe_permissions(r["permissions"], bot_perms)), colour=discord.Colour(r["color"]),
                        hoist=r["hoist"], mentionable=r["mentionable"], reason=REASON,
                    )
                    role_map[r["id"]] = role
                    stats["roles_created"] += 1
                except discord.HTTPException as e:
                    errors.append(f"role {r['name']}: {e}")
            await tick()

        cat_map: dict = {}
        for c in parsed["categories"]:
            if skip_existing and c["name"].lower() in have["categories"]:
                cat_map[c["id"]] = cats_by_name[c["name"].lower()]
                stats["categories_skipped"] += 1
            else:
                overwrites = self.make_overwrites(c["overwrites"], role_map, guild.me)
                try:
                    try:
                        cat = await guild.create_category(c["name"], reason=REASON, **({"overwrites": overwrites} if overwrites else {}))
                    except discord.Forbidden:
                        if not overwrites:
                            raise
                        stats["overwrites_skipped"] += 1
                        cat = await guild.create_category(c["name"], reason=REASON)
                    cat_map[c["id"]] = cat
                    stats["categories_created"] += 1
                except discord.HTTPException as e:
                    errors.append(f"category {c['name']}: {e}")
            await tick()

        chan_map: dict = {}
        for c in parsed["channels"]:
            parent = cat_map.get(c["parent_id"])
            if skip_existing and (c["name"].lower(), parent.id if parent else None) in have["channels"]:
                stats["channels_skipped"] += 1
            else:
                try:
                    chan_map[c["id"]] = await self.create_channel(guild, c, parent, self.make_overwrites(c["overwrites"], role_map, guild.me), stats)
                    stats["channels_created"] += 1
                except discord.HTTPException as e:
                    errors.append(f"channel {c['name']}: {e}")
            await tick()

        for m in parsed.get("messages", []):  # e.g. the rules text, posted into freshly created channels only
            channel = chan_map.get(m["channel_id"])
            if channel is None:
                continue
            try:
                await channel.send(embed=discord.Embed(title=m["title"], description=m["text"], color=COLOR))
                stats["messages_posted"] += 1
            except discord.HTTPException as e:
                errors.append(f"message in {m['title']}: {e}")

        return stats, errors

    async def run_import(self, interaction: discord.Interaction, parsed: dict, skip_existing: bool) -> None:
        guild = interaction.guild
        if guild.id in self.running:
            return await interaction.edit_original_response(embed=simple("Already running", "Another import is already running here.", WARN), view=None)
        self.running.add(guild.id)

        async def progress(done: int, total: int) -> None:
            try:
                await interaction.edit_original_response(embed=simple("⏳ Building your server…", f"{done} of {total} items done.", WARN))
            except discord.HTTPException:
                pass

        try:
            stats, errors = await self.build(guild, parsed, skip_existing, progress)
        except Exception:
            log.exception("Template import failed")
            self.running.discard(guild.id)
            return await interaction.edit_original_response(
                embed=simple("Something went wrong", "The import stopped part-way. Anything already created is still there. Check my permissions (Manage Roles and Manage Channels) and try again.", DANGER), view=None
            )
        self.running.discard(guild.id)

        embed = discord.Embed(title="✅ Import finished", color=COLOR if not errors else WARN)
        embed.add_field(name="Roles", value=f"{stats['roles_created']} created · {stats['roles_skipped']} already existed")
        embed.add_field(name="Categories", value=f"{stats['categories_created']} created · {stats['categories_skipped']} already existed")
        embed.add_field(name="Channels", value=f"{stats['channels_created']} created · {stats['channels_skipped']} already existed")
        notes = []
        if stats["overwrites_skipped"]:
            notes.append(f"{stats['overwrites_skipped']} item(s) were created without their special permissions (I wasn't allowed to set them). Check those channels' permissions.")
        if errors:
            notes.append(f"{len(errors)} item(s) failed, for example: " + "; ".join(errors[:3]))
        if stats["messages_posted"]:
            notes.append(f"Posted {stats['messages_posted']} message(s) for you (rules / bot info).")
        if not parsed.get("next_steps"):
            notes.append("Next: drag my role above the new roles if you want me to manage them, and check each channel's permissions.")
        embed.add_field(name="Notes", value="\n".join(f"• {n}" for n in notes)[:1000], inline=False)
        if parsed.get("next_steps"):
            embed.add_field(name="Next steps", value="\n".join(f"{i}. {t}" for i, t in enumerate(parsed["next_steps"], 1))[:1000], inline=False)
        await interaction.edit_original_response(embed=embed, view=None)


async def setup(bot: commands.Bot):
    await bot.add_cog(Templates(bot))
