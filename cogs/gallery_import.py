# cogs/gallery_import.py
from __future__ import annotations
import json, re
from typing import Any, Dict, List, Set
from pathlib import Path
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

DATA_DIR = Path("data/gallery")
STORE_PATH = DATA_DIR / "gallery.json"

URL_RE = re.compile(r"(https?://\S+)")

def _load_store() -> Dict[str, Any]:
    try:
        obj = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):  # normalize old []
            obj = {"entries": []}
        obj.setdefault("entries", [])
        return obj
    except Exception:
        return {"entries": []}

def _save_store(obj: Dict[str, Any]):
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)

def _classify(url: str) -> str:
    u = url.lower()
    if any(u.endswith(x) for x in (".png", ".jpg", ".jpeg", ".webp")):
        return "image"
    if u.endswith(".gif"):
        return "gif"
    if any(u.endswith(x) for x in (".mp4", ".mov", ".webm", ".m4v")):
        return "video"
    # let platforms auto-embed
    if "youtube.com" in u or "youtu.be" in u or "tiktok.com" in u:
        return "video"
    return "link"

class GalleryImport(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="gallery_import", description="Scan a channel and import media links.")
    @app_commands.describe(channel="Channel to scan", limit="Max messages to scan (default 50)")
    async def gallery_import(self, interaction: discord.Interaction, channel: discord.TextChannel, limit: int = 50):
        await interaction.response.defer(ephemeral=True, thinking=True)

        store = _load_store()
        existing: Set[str] = {e.get("url", "") for e in store["entries"] if isinstance(e, dict)}
        added = 0
        scanned = 0

        async for msg in channel.history(limit=limit, oldest_first=False):
            scanned += 1

            # attachments
            for a in msg.attachments:
                url = a.url
                if url not in existing:
                    store["entries"].append({
                        "url": url,
                        "type": _classify(url),
                        "tags": [],
                        "source": f"discord:{channel.id}",
                        "added_at": datetime.now(timezone.utc).isoformat(),
                        "nsfw": bool(channel.is_nsfw())
                    })
                    existing.add(url)
                    added += 1

            # plain urls in content
            for m in URL_RE.findall(msg.content or ""):
                url = m.rstrip(">)].,")
                if url not in existing:
                    store["entries"].append({
                        "url": url,
                        "type": _classify(url),
                        "tags": [],
                        "source": f"discord:{channel.id}",
                        "added_at": datetime.now(timezone.utc).isoformat(),
                        "nsfw": bool(channel.is_nsfw())
                    })
                    existing.add(url)
                    added += 1

        _save_store(store)
        await interaction.followup.send(f"Scanned {scanned} messages in {channel.mention}. Added {added} new media item(s). Total now: {len(store['entries'])}.", ephemeral=True)

    @app_commands.command(name="gallery_seed", description="Seed a single URL into the gallery.")
    @app_commands.describe(url="The media URL", tags="Comma-separated tags (optional)")
    async def gallery_seed(self, interaction: discord.Interaction, url: str, tags: str | None = None):
        store = _load_store()
        urls = {e.get("url", "") for e in store["entries"]}
        if url in urls:
            await interaction.response.send_message("Already in gallery.", ephemeral=True)
            return
        tlist = [t.strip() for t in (tags or "").split(",") if t.strip()]
        store["entries"].append({
            "url": url,
            "type": _classify(url),
            "tags": tlist,
            "source": "seed",
            "added_at": datetime.now(timezone.utc).isoformat(),
            "nsfw": False
        })
        _save_store(store)
        await interaction.response.send_message(f"Added {url} with tags {tlist or '[none]'}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(GalleryImport(bot))
