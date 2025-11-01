import asyncio
import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import discord
from discord.ext import commands
from discord import app_commands

logger = logging.getLogger("Aura")

# Hardcoded log channel: emote-sprinkles-log (PROD)
EMOJI_LOG_CHANNEL_ID = 1434273148856963072

DATA_DIR = Path(__file__).parent.parent / "data" / "emoji"
CONFIG_PATH = DATA_DIR / "config.json"
POOLS_DIR = DATA_DIR / "pools"

# Utility: parse "<:name:123>" or "<a:name:123>"
CUSTOM_EMOJI_RE = re.compile(r"<a?:[A-Za-z0-9_~]+:(\d+)>")

@dataclass
class EmojiPools:
    """Unicode and custom emoji pools per bucket."""
    autopost: List[Union[str, int]] = field(default_factory=list)
    user_message: List[Union[str, int]] = field(default_factory=list)
    event_soon: List[Union[str, int]] = field(default_factory=list)

@dataclass
class GuildEmojiConfig:
    enabled: bool = False
    channels_allow: List[int] = field(default_factory=list)
    channels_deny: List[int] = field(default_factory=list)
    rate_channel_seconds: int = 300
    rate_user_seconds: int = 120
    prob_user_message: float = 0.06
    event_window_hours: int = 12
    react_to_bots: bool = False
    pools_file: Optional[str] = None   # path under /data/emoji/pools
    # Optional hint triggers to add ⏰ for event-like posts
    event_hints: List[str] = field(default_factory=lambda: ["when:", "starts", "start:", "event", "go live"])

class EmojiCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config: Dict[int, GuildEmojiConfig] = {}
        self.pools: Dict[int, EmojiPools] = {}
        self.last_channel_ts: Dict[Tuple[int, int], float] = {}  # (guild_id, channel_id) -> ts
        self.last_user_ts: Dict[Tuple[int, int], float] = {}     # (guild_id, user_id) -> ts
        self._load_config()

    # ---------- loading ----------
    def _load_config(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            POOLS_DIR.mkdir(parents=True, exist_ok=True)
            if CONFIG_PATH.exists():
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            else:
                cfg = {"guilds": {}}
            self.config.clear()
            for gid_str, gcfg in (cfg.get("guilds") or {}).items():
                gid = int(gid_str)
                self.config[gid] = GuildEmojiConfig(
                    enabled = bool(gcfg.get("enabled", False)),
                    channels_allow = [int(x) for x in gcfg.get("channels_allow", [])],
                    channels_deny  = [int(x) for x in gcfg.get("channels_deny", [])],
                    rate_channel_seconds = int(gcfg.get("rate_channel_seconds", 300)),
                    rate_user_seconds    = int(gcfg.get("rate_user_seconds", 120)),
                    prob_user_message    = float(gcfg.get("prob_user_message", 0.06)),
                    event_window_hours   = int(gcfg.get("event_window_hours", 12)),
                    react_to_bots        = bool(gcfg.get("react_to_bots", False)),
                    pools_file           = gcfg.get("pools_file"),
                    event_hints          = [str(x).lower() for x in gcfg.get("event_hints", ["when:", "starts", "event"])],
                )
                # load pools
                pools_path = POOLS_DIR / (self.config[gid].pools_file or f"{gid}.json")
                if pools_path.exists():
                    pdata = json.loads(pools_path.read_text(encoding="utf-8"))
                else:
                    pdata = {}
                self.pools[gid] = EmojiPools(
                    autopost     = list(pdata.get("autopost", [])),
                    user_message = list(pdata.get("user_message", [])),
                    event_soon   = list(pdata.get("event_soon", [])),
                )
            logger.info(f"[emoji] loaded config for {len(self.config)} guild(s)")
        except Exception as e:
            logger.exception(f"[emoji] failed to load config: {e}")

    def _save_config(self) -> None:
        try:
            doc = {"guilds": {}}
            for gid, gcfg in self.config.items():
                doc["guilds"][str(gid)] = {
                    "enabled": gcfg.enabled,
                    "channels_allow": gcfg.channels_allow,
                    "channels_deny": gcfg.channels_deny,
                    "rate_channel_seconds": gcfg.rate_channel_seconds,
                    "rate_user_seconds": gcfg.rate_user_seconds,
                    "prob_user_message": gcfg.prob_user_message,
                    "event_window_hours": gcfg.event_window_hours,
                    "react_to_bots": gcfg.react_to_bots,
                    "pools_file": gcfg.pools_file,
                    "event_hints": gcfg.event_hints,
                }
            CONFIG_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.exception(f"[emoji] failed to save config: {e}")

    # ---------- helpers ----------
    def _allowed_in_channel(self, gcfg: GuildEmojiConfig, channel_id: int) -> bool:
        if gcfg.channels_allow:
            return channel_id in gcfg.channels_allow
        return channel_id not in set(gcfg.channels_deny)

    def _rate_ok_channel(self, guild_id: int, channel_id: int, gcfg: GuildEmojiConfig, now: float) -> bool:
        key = (guild_id, channel_id)
        last = self.last_channel_ts.get(key, 0.0)
        if now - last >= gcfg.rate_channel_seconds:
            self.last_channel_ts[key] = now
            return True
        return False

    def _rate_ok_user(self, guild_id: int, user_id: int, gcfg: GuildEmojiConfig, now: float) -> bool:
        key = (guild_id, user_id)
        last = self.last_user_ts.get(key, 0.0)
        if now - last >= gcfg.rate_user_seconds:
            self.last_user_ts[key] = now
            return True
        return False

    def _choose_emoji(self, guild: discord.Guild, items: List[Union[str, int]]) -> Optional[Union[str, discord.PartialEmoji]]:
        candidates: List[Union[str, discord.PartialEmoji]] = []
        for it in items:
            if isinstance(it, int):
                pe = discord.PartialEmoji(name=None, id=it, animated=False)
                candidates.append(pe)
            elif isinstance(it, str):
                m = CUSTOM_EMOJI_RE.fullmatch(it.strip())
                if m:
                    pe = discord.PartialEmoji(name=None, id=int(m.group(1)), animated=it.startswith("<a:"))
                    candidates.append(pe)
                else:
                    # unicode literal
                    candidates.append(it)
        if not candidates:
            return None
        return random.choice(candidates)

    async def _log(self, guild_id: int, text: str) -> None:
        ch = self.bot.get_channel(EMOJI_LOG_CHANNEL_ID)
        if ch and isinstance(ch, (discord.TextChannel, discord.Thread)):
            try:
                await ch.send(text)
            except Exception:
                pass

    def _looks_like_event(self, message: discord.Message, gcfg: GuildEmojiConfig) -> bool:
        # weak heuristic to tag event posts for the ⏰
        try:
            content = (message.content or "").lower()
            if any(h in content for h in gcfg.event_hints):
                return True
            for e in message.embeds:
                bloc = " ".join(filter(None, [e.title, e.description, e.footer.text if e.footer else ""])).lower()
                if any(h in bloc for h in gcfg.event_hints):
                    return True
        except Exception:
            pass
        return False

    # ---------- lifecycle ----------
    @commands.Cog.listener()
    async def on_ready(self):
        # health probe
        await asyncio.sleep(1.0)
        names = ", ".join(f"{g.name} ({g.id})" for g in self.bot.guilds) or "NONE"
        await self._log(0, f"🟢 emoji-cog online | guilds: {names}")

    # ---------- reaction engine ----------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if not message.guild or message.author.id == self.bot.user.id:
                # own message handled below, but skip here to decide bucket cleanly
                pass
        except Exception:
            return

        guild = message.guild
        if not guild or guild.id not in self.config:
            return
        gcfg = self.config[guild.id]
        if not gcfg.enabled:
            return

        # channel policy
        if not self._allowed_in_channel(gcfg, message.channel.id):
            return

        # destination bucket and probability
        bucket = "autopost" if message.author.id == self.bot.user.id else "user_message"

        # bot filtering for user messages
        if bucket == "user_message":
            if message.author.bot and not gcfg.react_to_bots:
                return
            if random.random() > gcfg.prob_user_message:
                return

        now = discord.utils.utcnow().timestamp()

        # rate limiting
        if not self._rate_ok_channel(guild.id, message.channel.id, gcfg, now):
            await self._log(guild.id, f"⏱️ skip channel-rate g:{guild.id} c:{message.channel.id} m:{message.id}")
            return
        if bucket == "user_message" and not self._rate_ok_user(guild.id, message.author.id, gcfg, now):
            await self._log(guild.id, f"⏱️ skip user-rate g:{guild.id} u:{message.author.id} m:{message.id}")
            return

        # select pool
        pools = self.pools.get(guild.id, EmojiPools())
        items = getattr(pools, bucket, [])
        if not items:
            # fallback to unicode sprinkles
            items = ["✨", "🍀", "🐞", "🌟"]

        # event handling: add ⏰ + sprinkle for event-like own posts
        emojis_to_send: List[Union[str, discord.PartialEmoji]] = []
        if bucket == "autopost" and self._looks_like_event(message, gcfg):
            emojis_to_send.append("⏰")
            pick = self._choose_emoji(guild, pools.event_soon or items)
            if pick:
                emojis_to_send.append(pick)
        else:
            pick = self._choose_emoji(guild, items)
            if pick:
                emojis_to_send.append(pick)

        # react
        for emo in emojis_to_send:
            try:
                await message.add_reaction(emo)
            except Exception:
                # silently skip on perms or missing external emoji access
                pass

        # log
        used = []
        for emo in emojis_to_send:
            if isinstance(emo, discord.PartialEmoji):
                used.append(f"<:{emo.id}>")
            else:
                used.append(str(emo))
        await self._log(guild.id, f"✨ reacted | g:{guild.id} c:{message.channel.id} m:{message.id} by:{message.author.id} bucket:{bucket} -> {' '.join(used) if used else 'none'}")

    # ---------- admin surface ----------
    admin = app_commands.Group(name="admin_emoji", description="Emoji toolkit controls")

    @admin.command(name="status", description="Show emoji settings for this guild")
    async def status(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        if gid not in self.config:
            await interaction.response.send_message("No emoji config for this guild.", ephemeral=True)
            return
        gcfg = self.config[gid]
        pools = self.pools.get(gid, EmojiPools())
        text = (
            f"**Enabled**: {gcfg.enabled}\n"
            f"**Allow**: {', '.join(f'<#{c}>' for c in gcfg.channels_allow) or '—'}\n"
            f"**Deny**: {', '.join(f'<#{c}>' for c in gcfg.channels_deny) or '—'}\n"
            f"**rate_channel_seconds**: {gcfg.rate_channel_seconds}\n"
            f"**rate_user_seconds**: {gcfg.rate_user_seconds}\n"
            f"**prob_user_message**: {gcfg.prob_user_message:.2f}\n"
            f"**event_window_hours**: {gcfg.event_window_hours}\n"
            f"**react_to_bots**: {gcfg.react_to_bots}\n"
            f"**pools_file**: {gcfg.pools_file or f'{gid}.json'}\n"
            f"**pool sizes** — autopost:{len(pools.autopost)} user:{len(pools.user_message)} event:{len(pools.event_soon)}"
        )
        await interaction.response.send_message(text, ephemeral=True)

    @admin.command(name="on", description="Enable emoji reactions in this guild")
    async def on(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        self.config.setdefault(gid, GuildEmojiConfig())
        self.config[gid].enabled = True
        self._save_config()
        await interaction.response.send_message("Emoji reactions ENABLED for this guild.", ephemeral=True)

    @admin.command(name="off", description="Disable emoji reactions in this guild")
    async def off(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        self.config.setdefault(gid, GuildEmojiConfig())
        self.config[gid].enabled = False
        self._save_config()
        await interaction.response.send_message("Emoji reactions DISABLED for this guild.", ephemeral=True)

    @admin.command(name="rate", description="Set per-channel rate limit (seconds)")
    @app_commands.describe(seconds="60–3600")
    async def rate(self, interaction: discord.Interaction, seconds: int):
        seconds = max(60, min(3600, seconds))
        gid = interaction.guild_id
        self.config.setdefault(gid, GuildEmojiConfig())
        self.config[gid].rate_channel_seconds = seconds
        self._save_config()
        await interaction.response.send_message(f"Set channel rate to {seconds}s.", ephemeral=True)

    @admin.command(name="rate_user", description="Set per-user rate limit (seconds)")
    @app_commands.describe(seconds="30–3600")
    async def rate_user(self, interaction: discord.Interaction, seconds: int):
        seconds = max(30, min(3600, seconds))
        gid = interaction.guild_id
        self.config.setdefault(gid, GuildEmojiConfig())
        self.config[gid].rate_user_seconds = seconds
        self._save_config()
        await interaction.response.send_message(f"Set user rate to {seconds}s.", ephemeral=True)

    @admin.command(name="prob_user", description="Set user-message reaction probability")
    @app_commands.describe(probability="0.00–0.50")
    async def prob_user(self, interaction: discord.Interaction, probability: float):
        probability = max(0.0, min(0.5, probability))
        gid = interaction.guild_id
        self.config.setdefault(gid, GuildEmojiConfig())
        self.config[gid].prob_user_message = probability
        self._save_config()
        await interaction.response.send_message(f"Set prob_user_message to {probability:.2f}.", ephemeral=True)

    @admin.command(name="allow", description="Allow a channel")
    async def allow(self, interaction: discord.Interaction, channel: discord.TextChannel):
        gid = interaction.guild_id
        self.config.setdefault(gid, GuildEmojiConfig())
        gcfg = self.config[gid]
        if channel.id not in gcfg.channels_allow:
            gcfg.channels_allow.append(channel.id)
        # remove from deny if present
        gcfg.channels_deny = [c for c in gcfg.channels_deny if c != channel.id]
        self._save_config()
        await interaction.response.send_message(f"Allowed {channel.mention}.", ephemeral=True)

    @admin.command(name="deny", description="Deny a channel")
    async def deny(self, interaction: discord.Interaction, channel: discord.TextChannel):
        gid = interaction.guild_id
        self.config.setdefault(gid, GuildEmojiConfig())
        gcfg = self.config[gid]
        if channel.id not in gcfg.channels_deny:
            gcfg.channels_deny.append(channel.id)
        # remove from allow if present
        gcfg.channels_allow = [c for c in gcfg.channels_allow if c != channel.id]
        self._save_config()
        await interaction.response.send_message(f"Denied {channel.mention}.", ephemeral=True)

    @admin.command(name="clear", description="Clear allow/deny lists")
    async def clear(self, interaction: discord.Interaction):
        gid = interaction.guild_id
        self.config.setdefault(gid, GuildEmojiConfig())
        gcfg = self.config[gid]
        gcfg.channels_allow.clear()
        gcfg.channels_deny.clear()
        self._save_config()
        await interaction.response.send_message("Cleared allow/deny.", ephemeral=True)

    @admin.command(name="pool_add", description="Add emojis to a bucket")
    @app_commands.describe(bucket="autopost | user_message | event_soon",
                           list="Comma list: 🍀,✨,<:name:123>,<a:name:456>")
    async def pool_add(self, interaction: discord.Interaction, bucket: str, list: str):
        gid = interaction.guild_id
        bucket = bucket.strip().lower()
        if bucket not in ("autopost", "user_message", "event_soon"):
            await interaction.response.send_message("Bucket must be autopost | user_message | event_soon", ephemeral=True)
            return
        self.config.setdefault(gid, GuildEmojiConfig())
        pools = self.pools.setdefault(gid, EmojiPools())
        target = getattr(pools, bucket)

        added = 0
        for raw in [x.strip() for x in list.split(",") if x.strip()]:
            # convert <:name:123> to int id when possible, else keep unicode string
            m = CUSTOM_EMOJI_RE.fullmatch(raw)
            if m:
                eid = int(m.group(1))
                if eid not in target:
                    target.append(eid)
                    added += 1
            else:
                if raw not in target:
                    target.append(raw)
                    added += 1

        # save pools file
        gcfg = self.config[gid]
        ppath = POOLS_DIR / (gcfg.pools_file or f"{gid}.json")
        doc = {
            "autopost": pools.autopost,
            "user_message": pools.user_message,
            "event_soon": pools.event_soon,
        }
        ppath.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        await interaction.response.send_message(f"Added {added} item(s) to {bucket}.", ephemeral=True)

    @admin.command(name="pool_list", description="List pool sizes / sample")
    async def pool_list(self, interaction: discord.Interaction, bucket: Optional[str] = None):
        gid = interaction.guild_id
        pools = self.pools.get(gid, EmojiPools())
        def fmt(items):
            show = ", ".join(str(x) for x in items[:10]) + (" …" if len(items) > 10 else "")
            return f"{len(items)} | {show or '—'}"
        if bucket:
            bucket = bucket.strip().lower()
            items = getattr(pools, bucket, [])
            await interaction.response.send_message(f"{bucket}: {fmt(items)}", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"autopost: {fmt(pools.autopost)}\nuser_message: {fmt(pools.user_message)}\nevent_soon: {fmt(pools.event_soon)}",
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiCog(bot))
