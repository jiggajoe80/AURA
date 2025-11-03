# cogs/emoji.py
# Aura — Emoji Sprinkles v1+v2
# v1: React to Aura's own posts (autopost), events (event_soon), and user messages (user_message)
# v2: React to Aura's auto-replies (autoreply) immediately after send
#
# Config:   data/emoji/config.json   (per-guild switches/rates/allow+deny+log)
# Pools:    data/emoji/pools/<guild>*.json  (buckets: autopost, user_message, event_soon, optional autoreply)
#
# Buckets used:
#   - "autopost": Aura-authored messages (hourlies, jokes, quotes, etc.)
#   - "event_soon": optional second sprinkle when events are within window (⏰ + one sprinkle)
#   - "user_message": random reactions to user-authored messages
#   - "autoreply": Aura's auto-reply messages (v2). If absent, falls back to "autopost".
#
# Admin commands are under /admin_emoji_* provided by cogs.admin (already in your repo).
# This cog exposes only a tiny health log on startup and a helper API for other cogs.

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("Aura")

CONFIG_PATH = Path("data/emoji/config.json")
POOLS_DIR   = Path("data/emoji/pools")

EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):([0-9]{15,25})>")

# Safe Unicode fallback if a bucket resolves to 0 usable items
SAFE_UNICODE_FALLBACK = ["✨", "🍀", "🐞", "🌟"]


@dataclass
class GuildPools:
    autopost: List[str] = field(default_factory=list)
    user_message: List[str] = field(default_factory=list)
    event_soon: List[str] = field(default_factory=list)
    autoreply: List[str] = field(default_factory=list)  # v2


@dataclass
class GuildCfg:
    guild_id: int
    enabled: bool = False
    rate_channel_seconds: int = 300
    rate_user_seconds: int = 90
    prob_user_message: float = 0.06
    event_window_hours: int = 6
    react_to_bots: bool = False
    channels_allow: List[int] = field(default_factory=list)
    channels_deny: List[int] = field(default_factory=list)
    log_channel_id: Optional[int] = None
    pools_file: Optional[str] = None  # optional; will auto-discover if not provided

    # runtime
    pools: GuildPools = field(default_factory=GuildPools)

    def allow_channel(self, ch_id: int) -> bool:
        if self.channels_allow:
            return ch_id in self.channels_allow
        return ch_id not in self.channels_deny


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _discover_pools_file(guild_id: int) -> Optional[Path]:
    if not POOLS_DIR.exists():
        return None
    prefix = f"{guild_id}"
    for p in sorted(POOLS_DIR.glob("*.json")):
        if p.name.startswith(prefix):
            return p
    return None


def _load_pools(path: Path) -> GuildPools:
    raw = _read_json(path)
    # Accept either flat {"autopost":[...], ...} or nested {"buckets":{...}}
    buckets = raw.get("buckets", raw)
    return GuildPools(
        autopost=buckets.get("autopost", []),
        user_message=buckets.get("user_message", []),
        event_soon=buckets.get("event_soon", []),
        autoreply=buckets.get("autoreply", []),
    )


def _resolve_pool_items(guild: discord.Guild, items: List[str]) -> List[Union[discord.PartialEmoji, str]]:
    """Return usable emoji objects/strings from mixed entries.

    Accepts:
      - Unicode: "🍀"
      - Custom as raw: "<:name:123...>" or "<a:name:...>"
      - Custom as numeric id: "123..." or 123...
    """
    usable: List[Union[discord.PartialEmoji, str]] = []
    for raw in items or []:
        if not raw:
            continue

        if isinstance(raw, str) and not raw.startswith("<") and not raw.isdigit():
            # Unicode
            usable.append(raw)
            continue

        if isinstance(raw, str) and raw.startswith("<"):
            m = EMOJI_RE.fullmatch(raw.strip())
            if m:
                name, id_str = m.group(1), m.group(2)
                eid = int(id_str)
                e = guild.get_emoji(eid)
                if e:
                    usable.append(e)
                else:
                    usable.append(discord.PartialEmoji(name=name, id=eid, animated=raw.startswith("<a:")))
            continue

        # numeric id
        try:
            eid = int(str(raw))
            e = guild.get_emoji(eid)
            if e:
                usable.append(e)
            else:
                usable.append(discord.PartialEmoji(name=None, id=eid))
        except Exception:
            continue

    return usable or SAFE_UNICODE_FALLBACK


def _choose(pool: List[Union[discord.PartialEmoji, str]]) -> Optional[Union[discord.PartialEmoji, str]]:
    return random.choice(pool) if pool else None


class EmojiCog(commands.Cog):
    """Emoji reaction engine with per-guild config and pools."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cfg_by_guild: Dict[int, GuildCfg] = {}
        self._last_channel_s: Dict[int, float] = {}
        self._last_user_s: Dict[tuple[int, int], float] = {}
        self._load_config()

    # --------------------------------------------------------------------- load

    def _load_config(self) -> None:
        self.cfg_by_guild.clear()
        try:
            raw = _read_json(CONFIG_PATH)
        except Exception as e:
            logger.error("[emoji] failed to load config: %s", e, exc_info=True)
            return

        for k, v in raw.items():
            try:
                gid = int(k)
            except Exception:
                continue
            cfg = GuildCfg(
                guild_id=gid,
                enabled=bool(v.get("enabled", False)),
                rate_channel_seconds=int(v.get("rate_channel_seconds", 300)),
                rate_user_seconds=int(v.get("rate_user_seconds", 90)),
                prob_user_message=float(v.get("prob_user_message", 0.06)),
                event_window_hours=int(v.get("event_window_hours", 6)),
                react_to_bots=bool(v.get("react_to_bots", False)),
                channels_allow=[int(x) for x in v.get("channels_allow", [])],
                channels_deny=[int(x) for x in v.get("channels_deny", [])],
                log_channel_id=(int(v["log_channel_id"]) if v.get("log_channel_id") else None),
                pools_file=v.get("pools_file"),
            )
            # load pools
            pools_path = None
            if cfg.pools_file:
                pools_path = (POOLS_DIR / cfg.pools_file)
                if not pools_path.exists():
                    pools_path = None
            if pools_path is None:
                pools_path = _discover_pools_file(cfg.guild_id)

            if pools_path and pools_path.exists():
                try:
                    cfg.pools = _load_pools(pools_path)
                except Exception as e:
                    logger.error("[emoji] failed to load pools for %s: %s", gid, e)
            self.cfg_by_guild[gid] = cfg

        # health probe
        for gid, cfg in self.cfg_by_guild.items():
            try:
                name = next((g.name for g in self.bot.guilds if g.id == gid), str(gid))
            except Exception:
                name = str(gid)
            logger.info("[emoji] cfg guild=%s enabled=%s allow=%d deny=%d",
                        name, cfg.enabled, len(cfg.channels_allow), len(cfg.channels_deny))
            if cfg.log_channel_id:
                ch = self.bot.get_channel(cfg.log_channel_id)
                if isinstance(ch, discord.TextChannel):
                    try:
                        asyncio.create_task(ch.send(f"[emoji] online • enabled={cfg.enabled} • guild={name}"))
                    except Exception:
                        pass

    # ----------------------------------------------------------- external API v2

    async def sprinkle_after_send(self, message: discord.Message, bucket: str = "autopost") -> None:
        """Called by other cogs immediately after sending a message (v2 for autoreply)."""
        try:
            await self._maybe_react(message, force_bucket=bucket)
        except Exception:
            logger.exception("[emoji] sprinkle_after_send failed")

    # ---------------------------------------------------------------- listeners

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        # ignore DMs or missing guild
        if not msg.guild or msg.author is None:
            return

        cfg = self.cfg_by_guild.get(msg.guild.id)
        if not cfg or not cfg.enabled:
            return

        if not cfg.allow_channel(msg.channel.id):
            return

        # bot-authored?
        if msg.author.id == self.bot.user.id:
            # Treat as autopost by default (hourly/joke/quote). Event-specific logic
            # can be added by calling sprinkle_after_send with bucket='event_soon' from events cog.
            await self._maybe_react(msg, force_bucket="autopost")
            return

        # other bots
        if msg.author.bot and not cfg.react_to_bots:
            return

        # user-authored reaction gate
        if random.random() > cfg.prob_user_message:
            return

        # per-user rate
        if not self._check_user_rate(msg.guild.id, msg.author.id, cfg.rate_user_seconds):
            return

        await self._maybe_react(msg, force_bucket="user_message")

    # ----------------------------------------------------------------- core impl

    def _check_channel_rate(self, ch_id: int, window_s: int) -> bool:
        now = asyncio.get_event_loop().time()
        last = self._last_channel_s.get(ch_id, 0)
        if now - last < window_s:
            return False
        self._last_channel_s[ch_id] = now
        return True

    def _check_user_rate(self, gid: int, uid: int, window_s: int) -> bool:
        now = asyncio.get_event_loop().time()
        key = (gid, uid)
        last = self._last_user_s.get(key, 0)
        if now - last < window_s:
            return False
        self._last_user_s[key] = now
        return True

    async def _maybe_react(self, msg: discord.Message, force_bucket: Optional[str] = None) -> None:
        cfg = self.cfg_by_guild.get(msg.guild.id)
        if not cfg or not cfg.enabled:
            return
        if not cfg.allow_channel(msg.channel.id):
            return
        # channel rate guard
        if not self._check_channel_rate(msg.channel.id, cfg.rate_channel_seconds):
            await self._log(msg.guild, f"[emoji] skipped: rate channel={msg.channel.id}")
            return

        # choose bucket
        bucket_name = force_bucket or "autopost"
        buckets = cfg.pools
        raw_pool: List[str]
        if bucket_name == "user_message":
            raw_pool = buckets.user_message or buckets.autopost
        elif bucket_name == "event_soon":
            raw_pool = buckets.event_soon or buckets.autopost
        elif bucket_name == "autoreply":
            raw_pool = buckets.autoreply or buckets.autopost
        else:
            raw_pool = buckets.autopost

        usable = _resolve_pool_items(msg.guild, raw_pool)
        choice = _choose(usable)
        if not choice:
            await self._log(msg.guild, f"[emoji] skipped: empty pool bucket={bucket_name}")
            return

        try:
            await msg.add_reaction(choice)  # works for Unicode, Emoji, or PartialEmoji
            await self._log(msg.guild, f"[emoji] sprinkle bucket={bucket_name} ch={msg.channel.id} msg={msg.id} emoji={choice}")
        except discord.Forbidden:
            await self._log(msg.guild, "[emoji] skipped: perms (Add Reactions / External Emoji?)")
        except Exception as e:
            await self._log(msg.guild, f"[emoji] error: {e}")

    async def _log(self, guild: discord.Guild, text: str) -> None:
        cfg = self.cfg_by_guild.get(guild.id)
        if not cfg or not cfg.log_channel_id:
            return
        ch = guild.get_channel(cfg.log_channel_id)
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.send(text)
            except Exception:
                pass

    # -------------------------------------------------------- tiny debug surface

    @app_commands.command(name="admin_emoji_debug", description="Show pool vs usable counts for a bucket.")
    @app_commands.describe(bucket="autopost | user_message | event_soon | autoreply")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_emoji_debug(self, inter: discord.Interaction, bucket: str):
        cfg = self.cfg_by_guild.get(inter.guild.id)
        if not cfg:
            return await inter.response.send_message("No emoji config for this guild.", ephemeral=True)
        b = (bucket or "autopost").lower()
        raw = {
            "autopost": cfg.pools.autopost,
            "user_message": cfg.pools.user_message,
            "event_soon": cfg.pools.event_soon,
            "autoreply": cfg.pools.autoreply,
        }.get(b, cfg.pools.autopost)
        usable = _resolve_pool_items(inter.guild, raw)
        sample = ", ".join(str(x) for x in usable[:10]) or "(none)"
        await inter.response.send_message(
            f"bucket={b} raw={len(raw)} usable={len(usable)} sample={sample}",
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiCog(bot))
