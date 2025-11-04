"""Gallery import helpers."""

from __future__ import annotations

import logging
from typing import List
from urllib.parse import urlparse

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from utils.gallery_store import merge_entries

IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
VIDEO_EXTS = {"mp4", "webm", "mov"}

logger = logging.getLogger("Aura.GalleryImport")


def _infer_type(url: str) -> str:
    path = urlparse(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    if ext == "gif":
        return "gif"
    if ext in VIDEO_EXTS:
        return "video"
    return "image"


def _is_supported(url: str) -> bool:
    path = urlparse(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return ext in IMAGE_EXTS or ext in VIDEO_EXTS


class GalleryImport(commands.Cog):
    """Administrative import tools for the gallery."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.command(name="gallery_import", description="Import recent media from a channel into the gallery.")
    @app_commands.describe(
        channel="Channel to scan for media",
        limit="Number of recent messages to inspect",
    )
    async def gallery_import(
        self,
        interaction: Interaction,
        channel: discord.TextChannel,
        limit: app_commands.Range[int, 1, 200] = 50,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        scanned_count = 0
        found: List[dict] = []
        seen_urls: set[str] = set()
        nsfw_flag = getattr(channel, "is_nsfw", lambda: False)()

        async for message in channel.history(limit=limit):
            scanned_count += 1
            for attachment in message.attachments:
                url = attachment.url
                if not url or url in seen_urls or not _is_supported(url):
                    continue
                entry_type = _infer_type(url)
                entry = {
                    "title": attachment.filename or attachment.url,
                    "url": url,
                    "type": entry_type,
                    "nsfw": bool(nsfw_flag),
                    "tags": [],
                }
                seen_urls.add(url)
                found.append(entry)

        added = merge_entries(found)
        logger.info(
            "gallery_import scanned %s messages in %s and added %s entries",
            scanned_count,
            getattr(channel, "id", "unknown"),
            added,
        )
        await interaction.followup.send(
            f"Scanned {scanned_count} messages in {channel.mention}. Found {added} new media item(s).",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GalleryImport(bot))
