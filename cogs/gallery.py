"""Gallery public commands."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import discord
from discord import Interaction, app_commands
from discord.ext import commands

GALLERY_DIR = Path(__file__).resolve().parent.parent / "data" / "gallery"
GALLERY_FILE = GALLERY_DIR / "gallery.json"
EMBED_COLOR = 0x355E3B
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
VIDEO_EXTS = {"mp4", "webm", "mov"}

logger = logging.getLogger("Aura.Gallery")


def _load_gallery() -> List[dict]:
    try:
        raw = GALLERY_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except FileNotFoundError:
        logger.info("gallery.json not found; starting with empty list")
    except Exception as exc:
        logger.warning("Failed to load gallery.json: %s", exc)
    return []


def _infer_type(entry: dict) -> str:
    explicit = str(entry.get("type", "")).strip().lower()
    if explicit in {"image", "video"}:
        return explicit
    url = str(entry.get("url", ""))
    path = urlparse(url).path
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in VIDEO_EXTS:
        return "video"
    return "image"


def _is_nsfw(entry: dict) -> bool:
    return bool(entry.get("nsfw", False))


def _channel_allows_nsfw(channel: Optional[discord.abc.GuildChannel | discord.Thread | discord.DMChannel]) -> bool:
    if isinstance(channel, discord.Thread):
        parent = channel.parent
        return bool(parent and getattr(parent, "is_nsfw", lambda: False)())
    if channel and hasattr(channel, "is_nsfw"):
        try:
            return bool(channel.is_nsfw())  # type: ignore[attr-defined]
        except TypeError:
            return False
    return False


def _first_tag(entry: dict) -> str:
    tags = entry.get("tags") or []
    if isinstance(tags, list) and tags:
        return str(tags[0])
    return "—"


@dataclass
class RenderedEntry:
    entry: dict
    media_type: str


class Gallery(commands.Cog):
    """Public gallery commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.entries: List[dict] = []
        self.reload_entries()

    def reload_entries(self) -> None:
        self.entries = _load_gallery()
        logger.info("Loaded %s gallery entries", len(self.entries))

    def _filter_entries(self, *, allow_nsfw: bool, tag: Optional[str] = None) -> List[dict]:
        pool = self.entries
        if tag:
            tag_lower = tag.lower()
            pool = [
                entry for entry in pool
                if any(str(t).lower() == tag_lower or tag_lower in str(t).lower() for t in entry.get("tags", []))
            ]
        if not allow_nsfw:
            pool = [entry for entry in pool if not _is_nsfw(entry)]
        return pool

    def _render_entry(self, entry: dict) -> RenderedEntry:
        media_type = _infer_type(entry)
        return RenderedEntry(entry=entry, media_type=media_type)

    async def _send_entry(self, interaction: Interaction, rendered: RenderedEntry) -> None:
        entry = rendered.entry
        embed = discord.Embed(
            title=entry.get("title", "Untitled"),
            description=entry.get("caption") or None,
            color=EMBED_COLOR,
            url=entry.get("url"),
        )
        author = entry.get("author")
        if author:
            embed.set_author(name=str(author))
        tags = entry.get("tags") or []
        if tags:
            embed.add_field(name="Tags", value=", ".join(str(t) for t in tags), inline=False)
        if entry.get("pinned"):
            embed.set_footer(text="Pinned")

        content: Optional[str] = None
        if rendered.media_type == "video":
            content = entry.get("url")
        else:
            embed.set_image(url=entry.get("url"))

        await interaction.response.send_message(content=content, embed=embed)

    def _log(self, action: str, interaction: Interaction, *, decision: str, entry: Optional[dict]) -> None:
        payload = {
            "event": f"gallery.{action}",
            "guild_id": getattr(interaction.guild, "id", None),
            "channel_id": getattr(interaction.channel, "id", None),
            "user_id": getattr(interaction.user, "id", None),
            "title": entry.get("title") if entry else None,
            "url": entry.get("url") if entry else None,
            "decision": decision,
        }
        logger.info(json.dumps(payload, ensure_ascii=False))

    @app_commands.command(name="gallery_random", description="Show a random gallery entry.")
    async def gallery_random(self, interaction: Interaction):
        allow_nsfw = _channel_allows_nsfw(interaction.channel)
        eligible = self._filter_entries(allow_nsfw=allow_nsfw)
        if not eligible:
            if self.entries and not allow_nsfw:
                msg = "Blocked by channel policy."
            else:
                msg = "No gallery entries available yet."
            await interaction.response.send_message(msg, ephemeral=True)
            self._log("random", interaction, decision="blocked:nsfw" if self.entries else "blocked:empty", entry=None)
            return
        choice = random.choice(eligible)
        rendered = self._render_entry(choice)
        await self._send_entry(interaction, rendered)
        self._log("random", interaction, decision="served", entry=choice)

    @app_commands.command(name="gallery_show", description="Show a specific gallery entry by title.")
    @app_commands.describe(title="Exact title of the gallery entry")
    async def gallery_show(self, interaction: Interaction, title: str):
        allow_nsfw = _channel_allows_nsfw(interaction.channel)
        target = next((e for e in self.entries if e.get("title", "").lower() == title.lower()), None)
        if not target:
            await interaction.response.send_message("That entry was not found.", ephemeral=True)
            self._log("show", interaction, decision="blocked:not_found", entry=None)
            return
        if _is_nsfw(target) and not allow_nsfw:
            await interaction.response.send_message("Blocked by channel policy.", ephemeral=True)
            self._log("show", interaction, decision="blocked:nsfw", entry=target)
            return
        rendered = self._render_entry(target)
        await self._send_entry(interaction, rendered)
        self._log("show", interaction, decision="served", entry=target)

    @gallery_show.autocomplete("title")
    async def gallery_show_autocomplete(self, interaction: Interaction, current: str):
        current_lower = current.lower()
        results: List[app_commands.Choice[str]] = []
        for entry in self.entries:
            title = entry.get("title", "")
            if current_lower in title.lower():
                results.append(app_commands.Choice(name=title[:100], value=title))
            if len(results) >= 25:
                break
        return results

    @app_commands.command(name="gallery_tag", description="Show a random entry for a specific tag.")
    @app_commands.describe(tag="Tag to search for")
    async def gallery_tag(self, interaction: Interaction, tag: str):
        allow_nsfw = _channel_allows_nsfw(interaction.channel)
        eligible = self._filter_entries(allow_nsfw=allow_nsfw, tag=tag)
        if not eligible:
            if self.entries:
                await interaction.response.send_message("No entries found for that tag.", ephemeral=True)
                decision = "blocked:no_tag"
            else:
                await interaction.response.send_message("No gallery entries available yet.", ephemeral=True)
                decision = "blocked:empty"
            self._log("tag", interaction, decision=decision, entry=None)
            return
        choice = random.choice(eligible)
        rendered = self._render_entry(choice)
        await self._send_entry(interaction, rendered)
        self._log("tag", interaction, decision="served", entry=choice)

    @app_commands.command(name="gallery_list", description="List gallery entries (first 25).")
    async def gallery_list(self, interaction: Interaction):
        if not self.entries:
            await interaction.response.send_message("No gallery entries available yet.", ephemeral=True)
            self._log("list", interaction, decision="blocked:empty", entry=None)
            return
        lines = []
        for idx, entry in enumerate(self.entries[:25], start=1):
            media_type = _infer_type(entry)
            lines.append(f"{idx}. {entry.get('title', 'Untitled')} • {media_type} • {_first_tag(entry)}")
        if len(self.entries) > 25:
            lines.append("")
            lines.append("Showing first 25. Use `/gallery_tag <tag>` to drill down.")
        message = "\n".join(lines)
        await interaction.response.send_message(message, ephemeral=True)
        self._log("list", interaction, decision="served", entry=None)


async def setup(bot: commands.Bot):
    await bot.add_cog(Gallery(bot))
