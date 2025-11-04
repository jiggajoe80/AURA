# cogs/gallery_import.py
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Iterable, List, Set, Tuple

import discord
from discord import app_commands
from discord.ext import commands

GALLERY_JSON = Path("data/gallery/gallery.json")
SUPPORTED_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".webm")

MEDIA_URL_RE = re.compile(
    r"https://[^\s)<>]+(?:\.jpg|\.jpeg|\.png|\.webp|\.gif|\.mp4|\.webm)(?:\?[^\s<>)]*)?",
    re.IGNORECASE,
)

def _load_gallery() -> dict:
    if not GALLERY_JSON.exists():
        GALLERY_JSON.parent.mkdir(parents=True, exist_ok=True)
        return {"entries": []}
    return json.loads(GALLERY_JSON.read_text(encoding="utf-8") or '{"entries": []}')

def _atomic_write_gallery(data: dict) -> None:
    tmp = GALLERY_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(GALLERY_JSON)

def _is_media_url(url: str) -> bool:
    if not url:
        return False
    low = url.lower()
    # Fast extension check first
    for ext in SUPPORTED_EXTS:
        if low.endswith(ext) or f"{ext}?" in low:
            return True
    # Fallback: regex for safety
    return bool(MEDIA_URL_RE.search(url))

def _tags_from_text(text: str) -> List[str]:
    raw = re.findall(r"#([A-Za-z0-9_]+)", text or "")
    return list({t.lower() for t in raw})

def _parse_tag_input(tag_text: str | None) -> List[str]:
    if not tag_text:
        return []
    # split on commas or spaces, normalize and dedupe
    parts = re.split(r"[,\s]+", tag_text.strip())
    return [t.lower() for t in dict.fromkeys(p for p in parts if p)]

class GalleryImportCog(commands.Cog):
    """Admin importer for Gallery — scrape a channel for media and add to the pool."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        channel="Channel to scan for media (attachments/embeds/links).",
        limit="How many recent messages to scan (max 1000). Default 200.",
        tags="Optional tags to add to each imported item (comma or space separated).",
    )
    @app_commands.command(name="gallery_import", description="Import media from a channel into the Gallery.")
    async def gallery_import(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        limit: app_commands.Range[int, 1, 1000] = 200,
        tags: str | None = None,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        # Basic permission sanity: bot must read history
        perms = channel.permissions_for(channel.guild.me)  # type: ignore
        if not perms.read_message_history or not perms.view_channel:
            return await interaction.followup.send(
                f"I can’t read {channel.mention}. Please grant View Channel + Read Message History.", ephemeral=True
            )

        extra_tags = _parse_tag_input(tags)
        # Always include 'safe' tag in v1
        if "safe" not in extra_tags:
            extra_tags.insert(0, "safe")

        # Load existing gallery and build a set of existing URLs
        gallery = _load_gallery()
        existing_urls: Set[str] = {e.get("url", "") for e in gallery.get("entries", []) if isinstance(e, dict)}

        found: List[Tuple[str, List[str]]] = []

        async for msg in channel.history(limit=limit, oldest_first=False):
            # 1) attachments
            for a in msg.attachments:
                url = a.url
                if _is_media_url(url):
                    tags_from_msg = _tags_from_text(msg.content)
                    found.append((url, tags_from_msg))

            # 2) obvious embed URLs (image/video)
            for emb in msg.embeds:
                # Try the common fields
                for candidate in (emb.url, getattr(getattr(emb.image, "url", None), "strip", lambda: "")(),
                                  getattr(getattr(emb.video, "url", None), "strip", lambda: "")()):
                    if candidate and isinstance(candidate, str) and _is_media_url(candidate):
                        tags_from_msg = _tags_from_text(msg.content)
                        found.append((candidate, tags_from_msg))

            # 3) any plain links in message content that look like media
            for m in MEDIA_URL_RE.findall(msg.content or ""):
                if _is_media_url(m):
                    tags_from_msg = _tags_from_text(msg.content)
                    found.append((m, tags_from_msg))

        # Dedupe in this batch and against existing
        batch_unique: List[Tuple[str, List[str]]] = []
        seen_batch: Set[str] = set()
        for url, msg_tags in found:
            if url in existing_urls or url in seen_batch:
                continue
            seen_batch.add(url)
            batch_unique.append((url, msg_tags))

        # Append entries
        for url, msg_tags in batch_unique:
            entry_tags = list(dict.fromkeys(extra_tags + msg_tags))  # keep order, dedupe
            gallery["entries"].append({"url": url, "tags": entry_tags})

        _atomic_write_gallery(gallery)

        added = len(batch_unique)
        scanned = len(found)
        await interaction.followup.send(
            f"Scanned {limit} messages in {channel.mention}.\n"
            f"Media candidates: {scanned} | Imported (new): {added}\n"
            f"Tags applied to each: {', '.join(extra_tags)}",
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(GalleryImportCog(bot))
