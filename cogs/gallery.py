# cogs/gallery.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands


DATA_DIR = Path("data/gallery")
STORE_PATH = DATA_DIR / "gallery.json"
CFG_PATH = DATA_DIR / "config.json"

# ---------- basic store helpers ----------

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

def _filter_by_channel_policy(
    entries: List[Dict[str, Any]],
    channel: discord.abc.GuildChannel,
) -> List[Dict[str, Any]]:
    cfg = _cfg()
    allow_nsfw = bool(cfg.get("allow_nsfw", False))
    ch_is_nsfw = getattr(channel, "is_nsfw", lambda: False)()  # text channels implement this

    # In non-NSFW channels or when the system is globally disabled, only allow SFW entries.
    if not allow_nsfw or not ch_is_nsfw:
        return [e for e in entries if not _is_nsfw_entry(e)]
    return entries

def _pick_random(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    import random
    return random.choice(entries) if entries else None

def _short_url(u: str, width: int = 42) -> str:
    return u if len(u) <= width else u[: width - 1] + "…"

def _entry_label(idx: int, e: Dict[str, Any]) -> str:
    etype = e.get("type", "image")
    tags = ",".join(map(str, e.get("tags", []))) or "untagged"
    return f"{idx:02d} • {etype} • {tags}"

# ---------- the Cog ----------

class Gallery(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- helpers to present media
    async def _send_entry(self, interaction: discord.Interaction, e: Dict[str, Any]):
        url = e.get("url", "")
        etype = e.get("type", "image")
        tags = e.get("tags", [])
        ts = e.get("added_at", "")
        embed = discord.Embed(
            title="Gallery",
            description=f"`{etype}`  •  {', '.join(tags) if tags else 'untagged'}",
        )
        if ts:
            embed.set_footer(text=str(ts))
        # Let Discord auto-embed for platforms like YouTube/TikTok.
        await interaction.response.send_message(url, embed=embed, ephemeral=False)

    def _visible_entries(self, channel: discord.abc.GuildChannel) -> List[Dict[str, Any]]:
        store = _store()
        return _filter_by_channel_policy(store["entries"], channel)

    # ---- list (pretty) ----
    @app_commands.command(name="gallery_list", description="List stored items visible in this channel.")
    async def gallery_list(self, interaction: discord.Interaction):
        visible = self._visible_entries(interaction.channel)
        if not visible:
            await interaction.response.send_message("No gallery entries available yet.", ephemeral=True)
            return

        # Show up to the 25 most recent (Discord modal-ish best practice)
        recent = list(reversed(visible))[:25]
        lines = []
        for i, e in enumerate(recent, start=1):
            label = _entry_label(i, e)
            url = _short_url(e.get("url", ""))
            lines.append(f"`{label}`\n{url}")

        msg = "**Gallery visible here:**\n" + "\n".join(lines)
        await interaction.response.send_message(msg, ephemeral=True)

    # ---- random (respects NSFW gate) ----
    @app_commands.command(name="gallery_random", description="Show a random gallery item (SFW in non-NSFW channels).")
    async def gallery_random(self, interaction: discord.Interaction):
        visible = self._visible_entries(interaction.channel)
        if not visible:
            await interaction.response.send_message("No safe gallery entries available yet.", ephemeral=True)
            return
        e = _pick_random(visible)
        await self._send_entry(interaction, e)

    # ---- show (with autocomplete + fallback picker) ----
    @app_commands.command(name="gallery_show", description="Show one item by URL, or pick from a list.")
    @app_commands.describe(url="Start typing to search, or leave blank to pick.")
    async def gallery_show(self, interaction: discord.Interaction, url: Optional[str] = None):
        visible = self._visible_entries(interaction.channel)

        # if a URL was provided, try to show it
        if url:
            match = next((x for x in visible if x.get("url") == url), None)
            if not match:
                await interaction.response.send_message(
                    "That URL isn’t in the gallery or isn’t visible in this channel.", ephemeral=True
                )
                return
            await self._send_entry(interaction, match)
            return

        # otherwise present a quick picker
        if not visible:
            await interaction.response.send_message("No gallery entries available yet.", ephemeral=True)
            return

        # show most recent 25
        recent = list(reversed(visible))[:25]

        class Picker(discord.ui.View):
            def __init__(self, parent: "Gallery"):
                super().__init__(timeout=60)
                self.parent = parent

            @discord.ui.select(
                placeholder="Pick a gallery item…",
                min_values=1,
                max_values=1,
                options=[
                    discord.SelectOption(
                        label=_entry_label(i, e),
                        description=_short_url(e.get("url", "")),
                        value=e.get("url", ""),
                    )
                    for i, e in enumerate(recent, start=1)
                ],
            )
            async def _choose(self, interaction2: discord.Interaction, select: discord.ui.Select):
                # consume selection and show the item
                chosen_url = select.values[0]
                entry = next((x for x in recent if x.get("url") == chosen_url), None)
                for child in self.children:
                    child.disabled = True
                await interaction2.response.defer()  # free the callback
                if entry:
                    # send as a followup so the original response remains the picker
                    await interaction.followup.send(entry["url"], ephemeral=False)
                else:
                    await interaction.followup.send("Selected item is no longer available.", ephemeral=True)

        await interaction.response.send_message("Pick an item:", view=Picker(self), ephemeral=True)

    @gallery_show.autocomplete("url")
    async def _ac_gallery_url(self, interaction: discord.Interaction, current: str):
        # Build suggestions from visible entries; search inside the URL and tags.
        visible = self._visible_entries(interaction.channel)
        current_low = (current or "").lower()

        def matches(e: Dict[str, Any]) -> bool:
            u = e.get("url", "")
            tags = ",".join(map(str, e.get("tags", [])))
            return (current_low in u.lower()) or (current_low in tags.lower())

        # most recent first; return up to 20 choices
        recent = list(reversed(visible))
        choices: List[app_commands.Choice[str]] = []
        shown = 0
        for idx, e in enumerate(recent, start=1):
            if current and not matches(e):
                continue
            label = _entry_label(idx, e)
            desc = _short_url(e.get("url", ""))
            # Choice name max is 100; keep it tight
            name = f"{label} — {desc}"
            choices.append(app_commands.Choice(name=name[:100], value=e.get("url", "")))
            shown += 1
            if shown >= 20:
                break
        return choices

    # ---- reload ----
    @app_commands.command(name="gallery_reload", description="Reload gallery config/store from disk.")
    async def gallery_reload(self, interaction: discord.Interaction):
        store = _store()
        cfg = _cfg()
        await interaction.response.send_message(
            f"Reloaded. entries={len(store['entries'])}, "
            f"allow_nsfw={cfg.get('allow_nsfw', False)}, "
            f"random_include_nsfw={cfg.get('random_include_nsfw', False)}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Gallery(bot))
