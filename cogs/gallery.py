# cogs/gallery.py
from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

DATA_DIR = Path("data/gallery")
STORE_PATH = DATA_DIR / "gallery.json"
CFG_PATH = DATA_DIR / "config.json"

def _load_json(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def _save_json(path: Path, obj: Any):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def _cfg() -> Dict[str, Any]:
    cfg = _load_json(CFG_PATH, {})
    # hard defaults
    cfg.setdefault("enabled", False)
    cfg.setdefault("rate_hours", 24)
    cfg.setdefault("channels_allow", [])
    cfg.setdefault("channels_deny", [])
    cfg.setdefault("log_channel_id", "")
    cfg.setdefault("allow_nsfw", False)
    cfg.setdefault("random_include_nsfw", False)
    return cfg

def _store() -> Dict[str, Any]:
    store = _load_json(STORE_PATH, {"entries": []})
    store.setdefault("entries", [])
    return store

def _is_nsfw_entry(e: Dict[str, Any]) -> bool:
    # optional flag, default False
    return bool(e.get("nsfw", False))

def _filter_by_channel_policy(entries: List[Dict[str, Any]], channel: discord.abc.GuildChannel) -> List[Dict[str, Any]]:
    cfg = _cfg()
    allow_nsfw = bool(cfg.get("allow_nsfw", False))
    ch_is_nsfw = getattr(channel, "is_nsfw", lambda: False)()  # works for text channels

    if not allow_nsfw or not ch_is_nsfw:
        # SFW only in non-NSFW channels, or if NSFW system is globally disabled
        return [e for e in entries if not _is_nsfw_entry(e)]
    return entries

def _pick_random(entries: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    import random
    return random.choice(entries) if entries else None

class Gallery(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- helpers to present media
    async def _send_entry(self, interaction: discord.Interaction, e: Dict[str, Any]):
        url = e.get("url", "")
        etype = e.get("type", "image")
        tags = e.get("tags", [])
        ts = e.get("added_at", "")
        embed = discord.Embed(title="Gallery", description=f"`{etype}`  •  {', '.join(tags) if tags else 'untagged'}")
        embed.set_footer(text=ts or "")
        # Let Discord auto-embed YouTube/TikTok links; for images/gifs/mp4 just send the URL
        await interaction.response.send_message(url, embed=embed, ephemeral=False)

    # ---- slash commands
    @app_commands.command(name="gallery_list", description="List how many items are stored.")
    async def gallery_list(self, interaction: discord.Interaction):
        store = _store()
        entries = _filter_by_channel_policy(store["entries"], interaction.channel)  # respect NSFW gate
        await interaction.response.send_message(f"Gallery entries visible here: **{len(entries)}**.", ephemeral=True)

    @app_commands.command(name="gallery_random", description="Show a random gallery item (SFW in non-NSFW channels).")
    async def gallery_random(self, interaction: discord.Interaction):
        store = _store()
        visible = _filter_by_channel_policy(store["entries"], interaction.channel)
        if not visible:
            await interaction.response.send_message("No safe gallery entries available yet.", ephemeral=True)
            return
        e = _pick_random(visible)
        await self._send_entry(interaction, e)

    @app_commands.command(name="gallery_show", description="Show one item by URL (if it exists).")
    @app_commands.describe(url="Exact URL previously imported/seeded")
    async def gallery_show(self, interaction: discord.Interaction, url: str):
        store = _store()
        entries = _filter_by_channel_policy(store["entries"], interaction.channel)
        e = next((x for x in entries if x.get("url") == url), None)
        if not e:
            await interaction.response.send_message("That URL is not in the gallery or is hidden here.", ephemeral=True)
            return
        await self._send_entry(interaction, e)

    @app_commands.command(name="gallery_reload", description="Reload gallery config/store from disk.")
    async def gallery_reload(self, interaction: discord.Interaction):
        # just touch the loaders; responses show sizes
        store = _store()
        cfg = _cfg()
        await interaction.response.send_message(
            f"Reloaded. entries={len(store['entries'])}, allow_nsfw={cfg.get('allow_nsfw', False)}, random_include_nsfw={cfg.get('random_include_nsfw', False)}",
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Gallery(bot))
