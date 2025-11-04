# cogs/gallery_import.py
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Set, Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

GALLERY_PATH = Path("data/gallery.json")
LOG = logging.getLogger("Aura.gallery")

MEDIA_MIME_PREFIXES = ("image/", "video/")  # what we index in v1
MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".webm"}

@dataclass
class GalleryEntry:
    url: str
    kind: str           # "image" | "video" | "file"
    filename: str
    message_id: int
    channel_id: int
    author_id: int
    timestamp: str      # ISO from Discord Message.created_at.isoformat()

def _load_gallery() -> Dict[str, Any]:
    """
    Load gallery tolerantly:
    - If file missing -> return {"entries":[]}
    - If file is a list -> wrap as {"entries": <that list>}
    - If file is a dict missing 'entries' -> add it
    """
    if not GALLERY_PATH.exists():
        return {"entries": []}

    try:
        data = json.loads(GALLERY_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        LOG.error("[gallery] failed to read JSON: %s", e, exc_info=True)
        return {"entries": []}

    if isinstance(data, list):
        # legacy v0 shape
        return {"entries": data}
    if not isinstance(data, dict):
        return {"entries": []}
    if "entries" not in data or not isinstance(data["entries"], list):
        data["entries"] = []
    return data

def _save_gallery(gallery: Dict[str, Any]) -> None:
    gallery.setdefault("entries", [])
    # pretty but compact enough
    GALLERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    GALLERY_PATH.write_text(json.dumps(gallery, ensure_ascii=False, indent=2), encoding="utf-8")

def _looks_like_media(attachment: discord.Attachment) -> bool:
    # Prefer content_type when Discord provides it
    if attachment.content_type:
        if any(attachment.content_type.startswith(p) for p in MEDIA_MIME_PREFIXES):
            return True
    # Fallback to extension check
    if attachment.filename:
        if Path(attachment.filename).suffix.lower() in MEDIA_EXTS:
            return True
    return False

def _entry_kind(attachment: discord.Attachment) -> str:
    ct = attachment.content_type or ""
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    # fallback by extension
    ext = Path(attachment.filename or "").suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return "image"
    if ext in {".mp4", ".mov", ".webm"}:
        return "video"
    return "file"

class GalleryImport(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = LOG

    @app_commands.command(name="gallery_import", description="Scan a channel and import attached images/videos into the gallery.")
    @app_commands.describe(
        channel="Channel to scan",
        limit="Max messages to scan (1–500, default 100)",
        oldest_first="Scan oldest first (helps on first-time bulk imports)"
    )
    async def gallery_import(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        limit: Optional[int] = 100,
        oldest_first: Optional[bool] = False,
    ):
        # Validate inputs early
        limit = int(limit or 100)
        if limit < 1:
            limit = 1
        if limit > 500:
            limit = 500

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Perm sanity: View + Read History
        perms = channel.permissions_for(channel.guild.me)
        if not (perms.view_channel and perms.read_message_history):
            return await interaction.followup.send(
                f"❌ I need View Channel + Read Message History in {channel.mention}.",
                ephemeral=True,
            )

        gallery = _load_gallery()
        # entries can be raw dicts; normalize to a URL set for de-duplication
        existing_urls: Set[str] = set()
        for e in gallery.get("entries", []):
            if isinstance(e, dict):
                url = str(e.get("url", "")).strip()
                if url:
                    existing_urls.add(url)

        added: List[GalleryEntry] = []
        scanned = 0

        try:
            async for msg in channel.history(limit=limit, oldest_first=oldest_first):
                scanned += 1
                if not msg.attachments:
                    continue
                for att in msg.attachments:
                    if not _looks_like_media(att):
                        continue
                    url = att.url
                    if url in existing_urls:
                        continue
                    entry = GalleryEntry(
                        url=url,
                        kind=_entry_kind(att),
                        filename=att.filename or "",
                        message_id=msg.id,
                        channel_id=channel.id,
                        author_id=msg.author.id,
                        timestamp=msg.created_at.isoformat(),
                    )
                    added.append(entry)
                    existing_urls.add(url)

        except discord.Forbidden:
            return await interaction.followup.send(
                f"❌ I don't have permission to read history in {channel.mention}.",
                ephemeral=True,
            )
        except Exception as e:
            self.log.error("[gallery] import failed: %s", e, exc_info=True)
            return await interaction.followup.send(
                f"❌ Import failed with: {e.__class__.__name__}. Check logs.",
                ephemeral=True,
            )

        if added:
            # normalize and extend
            normalized = [e if isinstance(e, dict) else dict(e) for e in gallery.get("entries", [])]
            normalized.extend(asdict(a) for a in added)
            gallery["entries"] = normalized
            _save_gallery(gallery)

        # Summary
        await interaction.followup.send(
            f"✅ Scanned {scanned} messages in {channel.mention}. "
            f"Found {len(added)} new media item(s). "
            f"Total now: {len(gallery.get('entries', []))}.",
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(GalleryImport(bot))
