# cogs/emoji_diag.py
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

ROOT_DIR = Path(__file__).parent.parent
EMOJI_DIR = ROOT_DIR / "data" / "emoji"
CONFIG_PATH = EMOJI_DIR / "config.json"
POOLS_DIR = EMOJI_DIR / "pools"

CUSTOM_RE = re.compile(r"^<a?:([A-Za-z0-9_]+):(\d+)>$")


def _is_unicode_emoji(token: str) -> bool:
    # crude but reliable: anything that's not a custom <...:id> token and is 1–3 chars we accept as unicode emoji
    if CUSTOM_RE.match(token):
        return False
    # allow multi-codepoint emoji like "✨" or "🌟" etc.
    return any(ord(ch) > 0x1FFF for ch in token)


def _parse_custom(token: str) -> Tuple[str, int] | None:
    m = CUSTOM_RE.match(token)
    if not m:
        return None
    name, id_str = m.group(1), m.group(2)
    try:
        return name, int(id_str)
    except ValueError:
        return None


class EmojiDiag(commands.Cog):
    """Diagnostics for Aura emoji pools and config."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- internal helpers ----------

    def _load_config(self) -> Dict:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _load_pool(self, pool_file: str) -> Dict[str, List[str]]:
        path = POOLS_DIR / pool_file
        if not path.exists():
            return {"autopost": [], "user_message": [], "event_soon": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"autopost": [], "user_message": [], "event_soon": []}
        # normalize keys we care about
        return {
            "autopost": list(data.get("autopost", [])),
            "user_message": list(data.get("user_message", [])),
            "event_soon": list(data.get("event_soon", [])),
        }

    def _bot_emoji_id_map(self) -> Dict[int, discord.Guild]:
        """Map custom emoji id -> owning guild that the bot can see."""
        mapping: Dict[int, discord.Guild] = {}
        for g in self.bot.guilds:
            for e in g.emojis:
                mapping[e.id] = g
        return mapping

    def _usable_in_guild(self, token: str, guild: discord.Guild, id_map: Dict[int, discord.Guild]) -> bool:
        if _is_unicode_emoji(token):
            return True
        parsed = _parse_custom(token)
        if not parsed:
            return False
        _, eid = parsed
        # If bot can see the emoji somewhere, Discord will allow use in this guild if the bot has "Use External Emojis".
        # We can't evaluate channel-specific perms here; this is a coarse check for pool sanity.
        return eid in id_map

    def _pick_usable(self, items: List[str], guild: discord.Guild, limit: int) -> List[str]:
        id_map = self._bot_emoji_id_map()
        usable = [t for t in items if self._usable_in_guild(t, guild, id_map)]
        return usable[: max(0, limit)]

    # ---------- slash commands ----------

    group = app_commands.Group(name="emoji_diag", description="Emoji diagnostics")

    @group.command(name="peek", description="Quick status: enabled, pool file, bucket sizes, and a small sample.")
    async def peek(self, inter: discord.Interaction) -> None:
        cfg = self._load_config()
        g = inter.guild
        if g is None:
            return await inter.response.send_message("Run this in a server.", ephemeral=True)

        entry = cfg.get(str(g.id))
        if not entry:
            return await inter.response.send_message("No emoji config for this guild.", ephemeral=True)

        pool_file = entry.get("pool_file", "(none)")
        enabled = bool(entry.get("enabled", False))
        pool = self._load_pool(pool_file)

        # small samples of what looks usable from each bucket
        sample_autopost = self._pick_usable(pool.get("autopost", []), g, 6)
        sample_user = self._pick_usable(pool.get("user_message", []), g, 6)
        sample_event = self._pick_usable(pool.get("event_soon", []), g, 6)

        lines = [
            f"Guild: **{g.name}** (`{g.id}`)",
            f"Enabled: **{enabled}**",
            f"Pool file: `{pool_file}`",
            "",
            f"Buckets:",
            f"• autopost: {len(pool.get('autopost', []))} total | sample → {' '.join(sample_autopost) if sample_autopost else '(none usable)'}",
            f"• user_message: {len(pool.get('user_message', []))} total | sample → {' '.join(sample_user) if sample_user else '(none usable)'}",
            f"• event_soon: {len(pool.get('event_soon', []))} total | sample → {' '.join(sample_event) if sample_event else '(none usable)'}",
        ]
        await inter.response.send_message("\n".join(lines), ephemeral=True)

    @group.command(name="usable", description="Show first N usable emojis from a bucket for this guild.")
    @app_commands.describe(
        bucket="Which bucket to inspect",
        limit="Max items to display (default 10)"
    )
    @app_commands.choices(
        bucket=[
            app_commands.Choice(name="autopost", value="autopost"),
            app_commands.Choice(name="user_message", value="user_message"),
            app_commands.Choice(name="event_soon", value="event_soon"),
        ]
    )
    async def usable(self, inter: discord.Interaction, bucket: app_commands.Choice[str], limit: int = 10) -> None:
        cfg = self._load_config()
        g = inter.guild
        if g is None:
            return await inter.response.send_message("Run this in a server.", ephemeral=True)

        entry = cfg.get(str(g.id))
        if not entry:
            return await inter.response.send_message("No emoji config for this guild.", ephemeral=True)

        pool_file = entry.get("pool_file", "(none)")
        pool = self._load_pool(pool_file)
        items = pool.get(bucket.value, [])

        picked = self._pick_usable(items, g, max(1, min(50, limit)))
        if not picked:
            return await inter.response.send_message(
                f"`{bucket.value}`: no usable emojis found (check pool contents or external-emoji permission).",
                ephemeral=True,
            )
        await inter.response.send_message(f"`{bucket.value}` usable → {' '.join(picked)}", ephemeral=True)

    @group.command(name="sample", description="Show first N raw entries from a bucket (no usability check).")
    @app_commands.describe(
        bucket="Which bucket to sample",
        limit="Max items to display (default 10)"
    )
    @app_commands.choices(
        bucket=[
            app_commands.Choice(name="autopost", value="autopost"),
            app_commands.Choice(name="user_message", value="user_message"),
            app_commands.Choice(name="event_soon", value="event_soon"),
        ]
    )
    async def sample(self, inter: discord.Interaction, bucket: app_commands.Choice[str], limit: int = 10) -> None:
        cfg = self._load_config()
        g = inter.guild
        if g is None:
            return await inter.response.send_message("Run this in a server.", ephemeral=True)

        entry = cfg.get(str(g.id))
        if not entry:
            return await inter.response.send_message("No emoji config for this guild.", ephemeral=True)

        pool = self._load_pool(entry.get("pool_file", "(none)"))
        items = pool.get(bucket.value, [])[: max(1, min(50, limit))]
        if not items:
            return await inter.response.send_message(f"`{bucket.value}` is empty.", ephemeral=True)

        await inter.response.send_message(f"`{bucket.value}` sample → {' '.join(items)}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiDiag(bot))
