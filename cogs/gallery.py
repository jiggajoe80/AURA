"""Gallery public commands (NSFW off by default, random never NSFW)."""

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

# Paths
ROOT = Path(__file__).resolve().parent.parent
GALLERY_DIR = ROOT / "data" / "gallery"
GALLERY_FILE = GALLERY_DIR / "gallery.json"
CONFIG_FILE = GALLERY_DIR / "config.json"

# UI
EMBED_COLOR = 0x355E3B
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
VIDEO_EXTS = {"mp4", "webm", "mov"}

logger = logging.getLogger("Aura.Gallery")

# --------- storage helpers --------- #

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


_DEFAULT_CFG = {
    "enabled": False,
    "rate_hours": 24,
    "channels_allow": [],
    "channels_deny": [],
    "log_channel_id": None,
    # New toggles:
    "nsfw_enabled": False,         # Global: allow NSFW at all? default OFF
    "nsfw_random_enabled": False,  # Whether random/tag may pick NSFW (we hard-force False anyway)
}


def _load_cfg() -> dict:
    cfg = dict(_DEFAULT_CFG)
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        logger.info("gallery config.json not found; using defaults")
    except Exception as exc:
        logger.warning("Failed to load gallery config.json: %s", exc)
    return cfg

# --------- inference helpers --------- #

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


def _channel_is_nsfw(chan: Optional[discord.abc.GuildChannel | discord.Thread | discord.DMChannel]) -> bool:
    if isinstance(chan, discord.Thread):
        parent = chan.parent
        return bool(parent and getattr(parent, "is_nsfw", lambda: False)())
    if chan and hasattr(chan, "is_nsfw"):
        try:
            return bool(chan.is_nsfw())  # type: ignore[attr-defined]
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
        self.cfg = _load_cfg()
        self.entries: List[dict] = []
        self.reload_entries()

    def reload_entries(self) -> None:
        self.entries = _load_gallery()
        logger.info("Loaded %s gallery entries", len(self.entries))

    # ---- filtering ---- #

    def _filter_entries(self, *, allow_nsfw: bool, tag: Optional[str] = None) -> List[dict]:
        pool = self.entries
        if tag:
            tag_lower = tag.lower()
            pool = [
                e for e in pool
                if any(str(t).lower() == tag_lower or tag_lower in str(t).lower() for t in e.get("tags", []))
            ]
        if not allow_nsfw:
            pool = [e for e in pool if not _is_nsfw(e)]
        return pool

    # ---- rendering ---- #

    def _render_entry(self, entry: dict) -> RenderedEntry:
        return RenderedEntry(entry=entry, media_type=_infer_type(entry))

    async def _send_entry(self, interaction: Interaction, rendered: RenderedEntry) -> None:
        e = rendered.entry
        embed = discord.Embed(
            title=e.get("title", "Untitled"),
            description=e.get("caption") or None,
            color=EMBED_COLOR,
            url=e.get("url"),
        )
        author = e.get("author")
        if author:
            embed.set_author(name=str(author))
        tags = e.get("tags") or []
        if tags:
            embed.add_field(name="Tags", value=", ".join(str(t) for t in tags), inline=False)
        if e.get("pinned"):
            embed.set_footer(text="Pinned")

        content: Optional[str] = None
        if rendered.media_type == "video":
            content = e.get("url")
        else:
            embed.set_image(url=e.get("url"))

        await interaction.response.send_message(content=content, embed=embed)

    # ---- logging (to file/logger; your log channel cog can mirror if desired) ---- #

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

    # ---- commands ---- #

    @app_commands.command(name="gallery_random", description="Show a random gallery entry.")
    async def gallery_random(self, interaction: Interaction):
        # HARD RULE: random never serves NSFW
        eligible = self._filter_entries(allow_nsfw=False)
        if not eligible:
            msg = "No gallery entries available yet."
            if self.entries:
                msg = "No SFW entries available for random."
            await interaction.response.send_message(msg, ephemeral=True)
            self._log("random", interaction, decision="blocked:empty_or_all_nsfw", entry=None)
            return

        choice = random.choice(eligible)
        await self._send_entry(interaction, self._render_entry(choice))
        self._log("random", interaction, decision="served", entry=choice)

    @app_commands.command(name="gallery_show", description="Show a specific gallery entry by title.")
    @app_commands.describe(title="Exact title of the gallery entry")
    async def gallery_show(self, interaction: Interaction, title: str):
        target = next((e for e in self.entries if e.get("title", "").lower() == title.lower()), None)
        if not target:
            await interaction.response.send_message("That entry was not found.", ephemeral=True)
            self._log("show", interaction, decision="blocked:not_found", entry=None)
            return

        # NSFW policy: require config.nsfw_enabled AND channel itself must be NSFW
        if _is_nsfw(target):
            cfg_nsfw = bool(self.cfg.get("nsfw_enabled", False))
            chan_ok = _channel_is_nsfw(interaction.channel)
            if not (cfg_nsfw and chan_ok):
                await interaction.response.send_message("Blocked by channel policy.", ephemeral=True)
                self._log("show", interaction, decision="blocked:nsfw", entry=target)
                return

        await self._send_entry(interaction, self._render_entry(target))
        self._log("show", interaction, decision="served", entry=target)

    @gallery_show.autocomplete("title")
    async def gallery_show_autocomplete(self, interaction: Interaction, current: str):
        cl = current.lower()
        results: List[app_commands.Choice[str]] = []
        for e in self.entries:
            title = e.get("title", "")
            if cl in title.lower():
                results.append(app_commands.Choice(name=title[:100], value=title))
            if len(results) >= 25:
                break
        return results

    @app_commands.command(name="gallery_tag", description="Show a random entry for a specific tag.")
    @app_commands.describe(tag="Tag to search for")
    async def gallery_tag(self, interaction: Interaction, tag: str):
        # HARD RULE: tag also never serves NSFW
        eligible = self._filter_entries(allow_nsfw=False, tag=tag)
        if not eligible:
            decision = "blocked:no_tag" if self.entries else "blocked:empty"
            note = "No entries found for that tag." if self.entries else "No gallery entries available yet."
            await interaction.response.send_message(note, ephemeral=True)
            self._log("tag", interaction, decision=decision, entry=None)
            return

        choice = random.choice(eligible)
        await self._send_entry(interaction, self._render_entry(choice))
        self._log("tag", interaction, decision="served", entry=choice)

    @app_commands.command(name="gallery_list", description="List gallery entries (first 25).")
    async def gallery_list(self, interaction: Interaction):
        if not self.entries:
            await interaction.response.send_message("No gallery entries available yet.", ephemeral=True)
            self._log("list", interaction, decision="blocked:empty", entry=None)
            return

        lines = []
        for idx, e in enumerate(self.entries[:25], start=1):
            lines.append(f"{idx}. {e.get('title', 'Untitled')} • {_infer_type(e)} • {_first_tag(e)}")
        if len(self.entries) > 25:
            lines += ["", "Showing first 25. Use `/gallery_tag <tag>` to drill down."]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
        self._log("list", interaction, decision="served", entry=None)


async def setup(bot: commands.Bot):
    await bot.add_cog(Gallery(bot))
