from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

import discord
from discord import app_commands
from discord.ext import commands

GALLERY_PATH = Path("data/gallery/gallery.json")

IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}
GIF_EXT   = {"gif"}
VIDEO_EXT = {"mp4", "webm", "mov", "m4v"}

URL_RE = re.compile(r"https?://\S+")

def _ensure_gallery_file():
    GALLERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not GALLERY_PATH.exists():
        GALLERY_PATH.write_text(json.dumps({"entries": []}, indent=2), encoding="utf-8")

def _load_gallery() -> Dict:
    _ensure_gallery_file()
    try:
        return json.loads(GALLERY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": []}

def _save_gallery(data: Dict):
    GALLERY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _infer_kind(url: str) -> str:
    ext = url.split("?")[0].split(".")[-1].lower()
    if ext in GIF_EXT:   return "gif"
    if ext in IMAGE_EXT: return "image"
    if ext in VIDEO_EXT: return "video"
    # default to video for discord cdn mp4/webm-like; else image as safe fallback
    return "video" if "cdn.discordapp.com" in url else "image"

class GalleryBulkSeed(commands.Cog):
    """Seed multiple gallery entries at once."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="gallery_seed_bulk", description="Bulk add gallery URLs (whitespace/comma/newline separated).")
    @app_commands.describe(
        urls="Paste many direct media URLs. Whitespace/comma/newline separated.",
        tag="Optional tag to apply to all items"
    )
    async def gallery_seed_bulk(self, interaction: discord.Interaction, urls: str, tag: str | None = None):
        await interaction.response.defer(ephemeral=True, thinking=True)

        # collect urls
        raw_urls = [u.strip(", \n\r\t") for u in URL_RE.findall(urls)]
        unique_in_input: List[str] = []
        seen: Set[str] = set()
        for u in raw_urls:
            if u and u not in seen:
                seen.add(u)
                unique_in_input.append(u)

        data = _load_gallery()
        entries = data.get("entries", [])
        existing: Set[str] = {e.get("url", "") for e in entries if isinstance(e, dict)}

        added, dupes, skipped = 0, 0, 0
        new_items = []

        for url in unique_in_input:
            if url in existing:
                dupes += 1
                continue

            kind = _infer_kind(url)
            filename = url.split("/")[-1].split("?")[0]
            ext = filename.split(".")[-1].lower() if "." in filename else ""

            # Basic guard on allowed kinds
            if not ext and "cdn.discordapp.com" not in url:
                skipped += 1
                continue

            new_items.append({
                "url": url,
                "kind": kind,
                "ext": ext,
                "filename": filename or "media",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "author": str(interaction.user),
                "tags": [tag] if tag else []
            })

        if new_items:
            entries.extend(new_items)
            data["entries"] = entries
            _save_gallery(data)
            added = len(new_items)

        msg = f"✅ Bulk seed complete. Added {added} · duplicates {dupes} · skipped {skipped}. Total now: {len(entries)}."
        await interaction.followup.send(msg, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(GalleryBulkSeed(bot))
