# cogs/auto_reply.py
# Aura — Auto-Reply with Emoji v2 hook

from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import List

import discord
from discord.ext import commands

logger = logging.getLogger("Aura")

QUIPS_PATH = Path("data/auto_reply_quips.json")
COOLDOWN_SECONDS = 30  # your existing setting

def _load_quips() -> List[str]:
    try:
        data = json.loads(QUIPS_PATH.read_text(encoding="utf-8"))
        # allow simple array or [{"text": "..."}]
        if data and isinstance(data[0], dict):
            return [x.get("text", "").strip() for x in data if x.get("text")]
        return [str(x).strip() for x in data if str(x).strip()]
    except Exception as e:
        logger.error("[auto_reply] failed to load quips: %s", e)
        return ["Here. Listening.", "Ping received.", "I'm here."]


class AutoReply(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.quips = _load_quips()
        self._last_by_user: dict[int, float] = {}

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        if not msg.guild or msg.author.bot:
            return

        # Trigger: mention Aura or reply to Aura
        if self.bot.user.id not in [u.id for u in msg.mentions] and (not msg.reference or not msg.reference.cached_message or msg.reference.cached_message.author.id != self.bot.user.id):
            return

        # Cooldown per-user
        now = asyncio.get_event_loop().time()
        last = self._last_by_user.get(msg.author.id, 0)
        if now - last < COOLDOWN_SECONDS:
            return
        self._last_by_user[msg.author.id] = now

        # Choose a line
        text = random.choice(self.quips)

        try:
            sent = await msg.channel.send(text)

            # v2: sprinkle after send using emoji cog, bucket=autoreply
            try:
                emoji_cog = self.bot.get_cog("EmojiCog")
                if emoji_cog:
                    await emoji_cog.sprinkle_after_send(sent, bucket="autoreply")
            except Exception:
                logger.exception("[auto_reply] sprinkle hook failed")

        except Exception as e:
            logger.error("[auto_reply] failed to send: %s", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReply(bot))
