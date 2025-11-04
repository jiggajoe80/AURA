"""Gallery admin + diagnostics commands."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
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
LOG_CHANNEL_ID = 1434273148856963072

logger = logging.getLogger("Aura.GalleryDiag")


def _load_gallery() -> List[dict]:
    try:
        data = json.loads(GALLERY_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except FileNotFoundError:
        logger.info("gallery.json not found; starting with empty list")
    except Exception as exc:
        logger.warning("Failed to load gallery.json: %s", exc)
    return []


def _write_gallery(entries: List[dict]) -> None:
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(entries, indent=2, ensure_ascii=False)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=GALLERY_DIR, delete=False) as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        temp_name = tmp.name
    os.replace(temp_name, GALLERY_FILE)


def _infer_type(url: str) -> str:
    path = urlparse(url).path
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in VIDEO_EXTS:
        return "video"
    return "image"


def _validate_url(url: str) -> Optional[str]:
    if len(url) > 2000:
        return "URL is too long."
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return "URL must use HTTPS."
    if not parsed.netloc:
        return "URL must include a host."
    ext = parsed.path.rsplit(".", 1)[-1].lower() if "." in parsed.path else ""
    if ext not in IMAGE_EXTS | VIDEO_EXTS:
        return "Unsupported media type."
    return None


def _ensure_unique_title(title: str, entries: List[dict]) -> str:
    existing = {item.get("title", "") for item in entries}
    if title not in existing:
        return title
    base = title
    idx = 2
    while True:
        candidate = f"{base} ({idx})"
        if candidate not in existing:
            return candidate
        idx += 1


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


def _format_datetime(dt_str: str) -> str:
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return dt_str


class GalleryDiag(commands.Cog):
    """Admin commands for gallery management."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_gallery_cog(self) -> Optional[commands.Cog]:
        return self.bot.get_cog("Gallery")

    def _reload_public_cog(self) -> None:
        cog = self._get_gallery_cog()
        if cog and hasattr(cog, "reload_entries"):
            try:
                cog.reload_entries()  # type: ignore[attr-defined]
            except Exception:
                logger.exception("Failed to refresh Gallery cog after update")

    async def _log_action(self, action: str, interaction: Interaction, *, title: Optional[str] = None, url: Optional[str] = None, decision: str) -> None:
        payload = {
            "event": f"gallery.{action}",
            "guild_id": getattr(interaction.guild, "id", None),
            "channel_id": getattr(interaction.channel, "id", None),
            "user_id": getattr(interaction.user, "id", None),
            "title": title,
            "url": url,
            "decision": decision,
        }
        logger.info(json.dumps(payload, ensure_ascii=False))
        if interaction.guild:
            channel = self.bot.get_channel(LOG_CHANNEL_ID)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                embed = discord.Embed(title=f"Gallery {action}", color=EMBED_COLOR)
                embed.add_field(name="Guild", value=f"{interaction.guild.name} ({interaction.guild.id})", inline=False)
                embed.add_field(name="User", value=f"{interaction.user} ({interaction.user.id})", inline=False)
                if title:
                    embed.add_field(name="Title", value=title, inline=False)
                if url:
                    embed.add_field(name="URL", value=url, inline=False)
                embed.add_field(name="Decision", value=decision, inline=False)
                try:
                    await channel.send(embed=embed)
                except Exception:
                    logger.exception("Failed to send gallery log to channel")

    @staticmethod
    def _parse_tags(raw: Optional[str]) -> List[str]:
        if not raw:
            return []
        parts = [segment.strip() for segment in raw.split(",") if segment.strip()]
        return parts

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.command(name="gallery_add", description="Add a new gallery entry.")
    @app_commands.describe(
        title="Title for the entry",
        url="HTTPS media URL",
        caption="Optional caption",
        author="Optional author credit",
        tags="Comma-separated tags",
        nsfw="Mark as NSFW",
        pinned="Mark as pinned"
    )
    async def gallery_add(
        self,
        interaction: Interaction,
        title: str,
        url: str,
        caption: Optional[str] = None,
        author: Optional[str] = None,
        tags: Optional[str] = None,
        nsfw: bool = False,
        pinned: bool = False,
    ):
        title = title.strip()
        if not title:
            await interaction.response.send_message("Title cannot be empty.", ephemeral=True)
            return
        url = url.strip()
        err = _validate_url(url)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        entries = _load_gallery()
        stored_title = _ensure_unique_title(title, entries)
        tags_list = self._parse_tags(tags)
        media_type = _infer_type(url)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = {
            "title": stored_title,
            "url": url,
            "caption": caption or None,
            "author": author or None,
            "tags": tags_list,
            "type": media_type,
            "nsfw": bool(nsfw),
            "pinned": bool(pinned),
            "added_at": now.isoformat().replace("+00:00", "Z"),
        }
        host = urlparse(url).netloc
        if "discordapp" in host:
            payload["source"] = "discord-attachment"
        entries.append(payload)
        _write_gallery(entries)
        self._reload_public_cog()

        embed = discord.Embed(title="Gallery entry stored", color=EMBED_COLOR)
        embed.add_field(name="Title", value=stored_title, inline=False)
        embed.add_field(name="Type", value=media_type, inline=True)
        embed.add_field(name="NSFW", value="Yes" if nsfw else "No", inline=True)
        if tags_list:
            embed.add_field(name="Tags", value=", ".join(tags_list), inline=False)
        if caption:
            embed.add_field(name="Caption", value=caption, inline=False)
        embed.add_field(name="URL", value=url, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self._log_action("add", interaction, title=stored_title, url=url, decision="stored")

    @gallery_add.autocomplete("title")
    async def gallery_add_title_autocomplete(self, interaction: Interaction, current: str):
        # Provide suggestions of existing titles for quick reference.
        entries = _load_gallery()
        current_lower = current.lower()
        results: List[app_commands.Choice[str]] = []
        for entry in entries:
            title = entry.get("title", "")
            if current_lower in title.lower():
                results.append(app_commands.Choice(name=title[:100], value=title))
            if len(results) >= 25:
                break
        return results

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.command(name="gallery_remove", description="Remove a gallery entry by title or index.")
    @app_commands.describe(title="Exact title to remove", index="1-based index from /gallery_list")
    async def gallery_remove(self, interaction: Interaction, title: Optional[str] = None, index: Optional[int] = None):
        entries = _load_gallery()
        if title:
            target_index = next((i for i, entry in enumerate(entries) if entry.get("title", "").lower() == title.lower()), None)
        elif index is not None:
            if index < 1 or index > len(entries):
                await interaction.response.send_message("Index out of range.", ephemeral=True)
                return
            target_index = index - 1
        else:
            await interaction.response.send_message("Provide a title or an index.", ephemeral=True)
            return

        if target_index is None:
            await interaction.response.send_message("Entry not found.", ephemeral=True)
            return

        removed = entries.pop(target_index)
        _write_gallery(entries)
        self._reload_public_cog()

        embed = discord.Embed(title="Gallery entry removed", color=EMBED_COLOR)
        embed.add_field(name="Title", value=removed.get("title", ""), inline=False)
        embed.add_field(name="URL", value=removed.get("url", ""), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self._log_action("remove", interaction, title=removed.get("title"), url=removed.get("url"), decision="removed")

    @gallery_remove.autocomplete("title")
    async def gallery_remove_autocomplete(self, interaction: Interaction, current: str):
        entries = _load_gallery()
        current_lower = current.lower()
        results: List[app_commands.Choice[str]] = []
        for entry in entries:
            title = entry.get("title", "")
            if current_lower in title.lower():
                results.append(app_commands.Choice(name=title[:100], value=title))
            if len(results) >= 25:
                break
        return results

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.command(name="gallery_reload", description="Reload gallery data from disk.")
    async def gallery_reload(self, interaction: Interaction):
        entries = _load_gallery()
        self._reload_public_cog()
        await interaction.response.send_message(f"Reloaded {len(entries)} entries from disk.", ephemeral=True)
        await self._log_action("reload", interaction, decision="reloaded")

    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    @app_commands.command(name="gallery_diag", description="Show gallery diagnostics.")
    async def gallery_diag(self, interaction: Interaction):
        entries = _load_gallery()
        allow_nsfw = _channel_allows_nsfw(interaction.channel)
        total = len(entries)
        nsfw_count = sum(1 for e in entries if e.get("nsfw"))
        video_count = sum(1 for e in entries if _infer_type(e.get("url", "")) == "video")
        image_count = total - video_count
        tag_counter: Counter[str] = Counter()
        for entry in entries:
            for tag in entry.get("tags", []):
                if isinstance(tag, str):
                    tag_counter[tag.lower()] += 1
        top_tags = tag_counter.most_common(10)
        sorted_entries = sorted(
            entries,
            key=lambda e: e.get("added_at", ""),
            reverse=True,
        )
        last_entries = sorted_entries[:5]

        embed = discord.Embed(title="Gallery diagnostics", color=EMBED_COLOR)
        embed.add_field(name="Total entries", value=str(total), inline=True)
        embed.add_field(name="NSFW entries", value=str(nsfw_count), inline=True)
        embed.add_field(name="Images", value=str(image_count), inline=True)
        embed.add_field(name="Videos", value=str(video_count), inline=True)

        if top_tags:
            tags_text = "\n".join(f"{tag} — {count}" for tag, count in top_tags)
        else:
            tags_text = "(no tags)"
        embed.add_field(name="Top tags", value=tags_text, inline=False)

        if last_entries:
            lines = []
            for entry in last_entries:
                title = entry.get("title", "")
                marker = " 🔞" if entry.get("nsfw") and not allow_nsfw else ""
                lines.append(f"• {title}{marker} — {_format_datetime(entry.get('added_at', ''))}")
            embed.add_field(name="Last added", value="\n".join(lines), inline=False)

        if not allow_nsfw and nsfw_count:
            embed.set_footer(text="NSFW entries hidden in this channel. Use an NSFW channel to preview.")

        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self._log_action("diag", interaction, decision="shown")


async def setup(bot: commands.Bot):
    await bot.add_cog(GalleryDiag(bot))
