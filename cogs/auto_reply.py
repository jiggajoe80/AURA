# cogs/auto_reply.py
import asyncio
import json
import logging
import random
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Deque, Dict, Optional

import discord
from discord.ext import commands

logger = logging.getLogger("Aura.auto_reply")

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
QUIPS_FILE = DATA_DIR / "auto_reply_quips.json"

# ===================== CONFIG (v3.3 hardened) ================================
COOLDOWN_SECONDS = 5           # fixed rule — universal 5s
CHANNEL_COOLDOWN_SECONDS = 5   # also guard the channel for 5s
RECENT_DEDUP = 6               # avoid repeating the same quip in a channel
HOURGLASS = "⏳"

FALLBACK_QUIPS = [
    "My patience is a limited-time offer.",
    "Fine. You have my attention. Briefly.",
    "Go on, say the dramatic part again 🎭",
    "You again? I was just settling into silence.",
    "Careful, I might start caring.",
    "If sarcasm were fuel, I’d be solar powered ☀️",
    "That energy? Chaotic neutral.",
    "Oh good, another mystery in lowercase.",
    "You woke my code. I hope it was worth it.",
]
# ============================================================================

def now() -> datetime:
    return datetime.utcnow()

def load_quips() -> list[str]:
    try:
        raw = json.loads(QUIPS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            out = []
            for r in raw:
                if isinstance(r, dict) and "text" in r:
                    out.append(str(r["text"]))
                else:
                    out.append(str(r))
            return [q for q in out if q.strip()]
    except Exception as e:
        logger.warning(f"[auto_reply] Failed to load {QUIPS_FILE.name}: {e}")
    return FALLBACK_QUIPS.copy()

def mentioned_me(msg: discord.Message, me: discord.ClientUser) -> bool:
    if me in msg.mentions:
        return True
    c = (msg.content or "").lower()
    return "@aura" in c or "@aura-bot" in c or "aura-bot" in c

async def is_reply_to_me(msg: discord.Message, me_id: int) -> bool:
    if not msg.reference or not msg.reference.message_id:
        return False
    try:
        ref = msg.reference.cached_message
        if ref is None:
            ch = msg.channel
            if isinstance(ch, (discord.TextChannel, discord.Thread)):
                ref = await ch.fetch_message(msg.reference.message_id)
        return (ref.author.id == me_id) if ref else False
    except Exception:
        return False

class AutoReply(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.quips: list[str] = load_quips()
        self.recent: Dict[int, Deque[str]] = {}
        # cooldown clocks
        self.user_cd_until: Dict[int, datetime] = {}
        self.chan_cd_until: Dict[int, datetime] = {}
        # active countdown messages per-user (to avoid dup spam)
        self.user_countdown_msg: Dict[int, discord.Message] = {}
        # lightweight gate to reduce “race sends”
        self._send_lock = asyncio.Lock()

        logger.info(f"[auto_reply] quips loaded: {len(self.quips)}")

    # ------------------------- helpers ---------------------------------------
    def _next_quip(self, channel_id: int) -> str:
        pool = self.quips or FALLBACK_QUIPS
        dq = self.recent.setdefault(channel_id, deque(maxlen=RECENT_DEDUP))
        candidates = [q for q in pool if q not in dq] or pool
        pick = random.choice(candidates)
        dq.append(pick)
        return pick

    def _user_left(self, user_id: int) -> float:
        t = self.user_cd_until.get(user_id)
        return max(0.0, (t - now()).total_seconds()) if t else 0.0

    def _chan_left(self, channel_id: int) -> float:
        t = self.chan_cd_until.get(channel_id)
        return max(0.0, (t - now()).total_seconds()) if t else 0.0

    def _arm_user(self, user_id: int):
        self.user_cd_until[user_id] = now() + timedelta(seconds=COOLDOWN_SECONDS)

    def _arm_chan(self, channel_id: int):
        self.chan_cd_until[channel_id] = now() + timedelta(seconds=CHANNEL_COOLDOWN_SECONDS)

    async def _react_hourglass(self, msg: discord.Message):
        try:
            await msg.add_reaction(HOURGLASS)
        except Exception as e:
            logger.debug(f"[auto_reply] hourglass react failed: {e}")

    async def _countdown_nudge(self, origin: discord.Message, user_id: int, seconds: int):
        """Create or update a per-user countdown, then delete it when done."""
        try:
            existing = self.user_countdown_msg.get(user_id)
            if existing and existing.channel.id == origin.channel.id:
                # Update existing countdown (reset to full)
                m = existing
            else:
                m = await origin.reply(f"Cooling down {HOURGLASS} {seconds}s", mention_author=False)
                self.user_countdown_msg[user_id] = m

            # Drive the visible countdown
            for rem in range(seconds - 1, -1, -1):
                await asyncio.sleep(1)
                try:
                    await m.edit(content=f"Cooling down {HOURGLASS} {rem}s")
                except Exception:
                    break

            # delete and clear
            try:
                await m.delete()
            except Exception:
                pass
        finally:
            # clear slot so a new countdown can be created later
            self.user_countdown_msg.pop(user_id, None)

    # ------------------------ listener ---------------------------------------
    @commands.Cog.listener("on_message")
    async def handle_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild is None:
            return

        me = self.bot.user
        if me is None:
            return

        got_mention = mentioned_me(message, me)
        got_reply = await is_reply_to_me(message, me.id)
        if not (got_mention or got_reply):
            return

        user_left = self._user_left(message.author.id)
        chan_left = self._chan_left(message.channel.id)

        # If cooling down -> ⏳ react + countdown (one active per user)
        if user_left > 0 or chan_left > 0:
            logger.info(
                f"[auto_reply] cooldown hit in #{getattr(message.channel,'name',message.channel.id)} "
                f"user_left={user_left:.1f}s chan_left={chan_left:.1f}s"
            )
            await self._react_hourglass(message)

            # Only start a countdown if this user doesn't have one active
            if message.author.id not in self.user_countdown_msg:
                seconds = int(max(user_left, chan_left, COOLDOWN_SECONDS))  # always show a full 5s window UX
                asyncio.create_task(self._countdown_nudge(message, message.author.id, seconds))
            return

        # Normal reply path
        async with self._send_lock:
            try:
                text = self._next_quip(message.channel.id)
                await message.reply(text, mention_author=False)
                self._arm_user(message.author.id)
                self._arm_chan(message.channel.id)
                logger.info(
                    f"[auto_reply] replied in #{getattr(message.channel,'name',message.channel.id)}: '{text[:60]}'"
                )
            except Exception as e:
                logger.error(f"[auto_reply] send failed: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReply(bot))
