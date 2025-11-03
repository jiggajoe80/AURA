# cogs/emoji.py
# Engine — global pool resolver + cross-guild custom resolution + simple auto-reactor
# Requires: data/emoji/config.json (+ optional "default_pool_file"),
#           pools in data/emoji/pools/*.json

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import discord
from discord.ext import commands

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "emoji"
POOLS_DIR = DATA_DIR / "pools"
CONFIG_PATH = DATA_DIR / "config.json"

EMOJI_TAG_RE = re.compile(r"<a?:([a-zA-Z0-9_]+):(\d+)>")

BucketName = str  # 'autopost' | 'user_message' | 'event_soon'
Pool = Dict[BucketName, List[str]]


def _now() -> float:
    return time.monotonic()


class EmojiCog(commands.Cog):
    """Emoji engine: load config, load pools, pick usable emojis, auto-react."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = logging.getLogger("Aura")
        # config state
        self.config: Dict[str, dict] = {}
        self.default_pool_file: str = "POOL.GLOBAL.json"
        # runtime caches
        self._pool_cache: Dict[str, Tuple[float, Pool]] = {}  # pool_file -> (loaded_at, pool)
        self._usable_cache: Dict[Tuple[int, str], Tuple[float, List[str]]] = {}  # (guild_id, bucket) -> (t, list)
        # cooldowns
        self._chan_cool: Dict[int, float] = {}
        self._user_cool: Dict[int, float] = {}

        self._load_config()

    # ---------- config & pools ----------

    def _load_config(self) -> None:
        """Load JSON config with an optional top-level 'default_pool_file'."""
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            self.log.error("[emoji] failed to load config: %s", e, exc_info=True)
            self.config = {}
            return

        self.default_pool_file = cfg.get("default_pool_file", self.default_pool_file)
        self.config = {k: v for k, v in cfg.items() if k != "default_pool_file"}

        self.log.info("[emoji] default_pool_file=%s", self.default_pool_file)
        for gid, gc in self.config.items():
            allow = len(gc.get("channels_allow", []))
            deny = len(gc.get("channels_deny", []))
            self.log.info(
                "[emoji] cfg guild=%s enabled=%s allow=%d deny=%d",
                gid, bool(gc.get("enabled")), allow, deny
            )

    def _resolve_pool_file(self, guild_id: int) -> str:
        gc = self.config.get(str(guild_id), {}) if self.config else {}
        return (gc.get("pool_file") or self.default_pool_file or "POOL.GLOBAL.json").strip()

    def _load_pool(self, pool_file: str) -> Pool:
        """Load a pool JSON file with simple cache."""
        try:
            cached = self._pool_cache.get(pool_file)
            if cached:
                return cached[1]
            path = POOLS_DIR / pool_file
            data: Pool = json.loads(path.read_text(encoding="utf-8"))
            # normalize buckets
            data.setdefault("autopost", [])
            data.setdefault("user_message", [])
            data.setdefault("event_soon", [])
            self._pool_cache[pool_file] = (_now(), data)
            return data
        except Exception as e:
            self.log.error("[emoji] load pool %s failed: %s", pool_file, e, exc_info=True)
            # safe fallback
            return {"autopost": ["✨", "🍀", "🐞", "🌟"], "user_message": ["✨", "🍀", "🐞", "🌟"], "event_soon": ["⏰", "✨"]}

    def _pool_for_guild(self, guild_id: int) -> Tuple[str, Pool]:
        pf = self._resolve_pool_file(guild_id)
        return pf, self._load_pool(pf)

    # ---------- usable emoji resolution ----------

    def _split_custom_unicode(self, items: List[str]) -> Tuple[List[str], List[str]]:
        custom, uni = [], []
        for s in items:
            if EMOJI_TAG_RE.fullmatch(s):
                custom.append(s)
            else:
                uni.append(s)
        return custom, uni

    def _resolve_custom_ids(self, items: List[str]) -> List[int]:
        ids = []
        for s in items:
            m = EMOJI_TAG_RE.fullmatch(s)
            if m:
                ids.append(int(m.group(2)))
        return ids

    def _usable_emojis(self, guild: discord.Guild, bucket_items: List[str]) -> List[str]:
        """Filter to emoji the bot can actually use in this guild."""
        custom_text, unicode_text = self._split_custom_unicode(bucket_items)
        if not custom_text:
            return unicode_text[:]  # only unicode provided

        wanted_ids = set(self._resolve_custom_ids(custom_text))
        # Build set of emoji IDs the bot can use anywhere (across all guilds where it has access)
        available_ids = {e.id for e in self.bot.emojis if e.is_usable()}
        usable_custom = [f"<:{e.name}:{e.id}>" if not e.animated else f"<a:{e.name}:{e.id}>"
                         for e in self.bot.emojis
                         if e.id in wanted_ids and e.id in available_ids]

        return unicode_text + usable_custom

    # ---------- public helpers used by diag ----------

    def get_guild_config(self, guild_id: int) -> dict:
        return self.config.get(str(guild_id), {}) if self.config else {}

    def get_pool_file_for(self, guild_id: int) -> str:
        return self._resolve_pool_file(guild_id)

    def sample_bucket(self, guild: discord.Guild, bucket: BucketName, k_unicode: int = 2, k_custom: int = 2) -> Tuple[int, int, List[str]]:
        """Return (raw_count, usable_count, sample_list) for a bucket with a mixed sampler."""
        pool_file, pool = self._pool_for_guild(guild.id)
        raw = pool.get(bucket, [])
        if not raw:
            return 0, 0, []

        usable = self._usable_emojis(guild, raw)
        usable_cnt = len(usable)
        if usable_cnt == 0:
            return len(raw), 0, []

        # mixed sampler: up to 2 custom + up to 2 unicode
        custom, uni = self._split_custom_unicode(usable)
        sample: List[str] = []
        if custom:
            sample.extend(random.sample(custom, min(k_custom, len(custom))))
        if uni:
            sample.extend(random.sample(uni, min(k_unicode, len(uni))))
        if not sample:
            sample = random.sample(usable, min(4, len(usable)))
        return len(raw), usable_cnt, sample

    # ---------- simple auto-reactor ----------

    def _cooldowns_ok(self, message: discord.Message, gc: dict) -> bool:
        # per-channel
        ch_key = message.channel.id
        ch_rate = int(gc.get("rate_channel_seconds", 120))
        last = self._chan_cool.get(ch_key, 0.0)
        if _now() - last < ch_rate:
            return False
        # per-user
        u_key = message.author.id
        u_rate = int(gc.get("rate_user_seconds", 45))
        last_u = self._user_cool.get(u_key, 0.0)
        if _now() - last_u < u_rate:
            return False
        return True

    def _mark_cooldowns(self, message: discord.Message, gc: dict) -> None:
        self._chan_cool[message.channel.id] = _now()
        self._user_cool[message.author.id] = _now()

    def _channel_allowed(self, message: discord.Message, gc: dict) -> bool:
        allow = gc.get("channels_allow") or []
        deny = gc.get("channels_deny") or []
        if allow and str(message.channel.id) not in allow:
            return False
        if deny and str(message.channel.id) in deny:
            return False
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore DMs
        if not message.guild or message.author == self.bot.user:
            return

        gc = self.get_guild_config(message.guild.id)
        if not gc or not gc.get("enabled", False):
            return
        if not self._channel_allowed(message, gc):
            return
        if message.author.bot and not gc.get("react_to_bots", False):
            return

        # probability gate for user messages
        if not message.author.bot:
            prob = float(gc.get("prob_user_message", 0.15))
            if random.random() > prob:
                return

        if not self._cooldowns_ok(message, gc):
            return

        # choose bucket based on author type
        bucket: BucketName = "user_message" if not message.author.bot else "autopost"
        pool_file, pool = self._pool_for_guild(message.guild.id)
        raw = pool.get(bucket, [])
        if not raw:
            return

        usable = self._usable_emojis(message.guild, raw)
        if not usable:
            return

        choice = random.choice(usable)
        try:
            # add reaction
            m = EMOJI_TAG_RE.fullmatch(choice)
            if m:
                # custom
                emoji_id = int(m.group(2))
                emoji_obj = discord.utils.get(self.bot.emojis, id=emoji_id)
                if emoji_obj:
                    await message.add_reaction(emoji_obj)
            else:
                # unicode
                await message.add_reaction(choice)
            self._mark_cooldowns(message, gc)
        except Exception as e:
            self.log.warning("[emoji] react failed in %s with %s (%s)", bucket, pool_file, e)

    # ---------- small admin utility ----------

    @commands.command(name="emoji_reload")
    @commands.has_permissions(manage_guild=True)
    async def emoji_reload(self, ctx: commands.Context):
        """Reload emoji config + clear caches."""
        self._pool_cache.clear()
        self._usable_cache.clear()
        self._load_config()
        await ctx.reply("Emoji config reloaded.", mention_author=False)

    # ---------- Cog boilerplate ----------

async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiCog(bot))
