# cogs/auto_reply.py
import json
import random
import asyncio
from pathlib import Path
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

# ---- CONFIG (your choices) ----
ALLOWED_CHANNEL_IDS = {1399840085536407602}  # only reply in these channels
USER_COOLDOWN_SECONDS = 10                   # per-user cooldown
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "auto_reply_quips.json"
# --------------------------------

def _load_quips() -> list[str]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "quips" in data:
            return [str(x).strip() for x in data["quips"] if str(x).strip()]
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    return []

class AutoReply(commands.Cog):
    def __init__(self, bot: commands.Bot | discord.Client):
        self.bot = bot
        self.quips = _load_quips()
        self._cooldowns: dict[tuple[int, int], datetime] = {}  # (user_id, channel_id) -> until

    def _on_cooldown(self, user_id: int, channel_id: int) -> bool:
        key = (user_id, channel_id)
        now = datetime.utcnow()
        until = self._cooldowns.get(key)
        return until is not None and until > now

    def _start_cooldown(self, user_id: int, channel_id: int):
        self._cooldowns[(user_id, channel_id)] = datetime.utcnow() + timedelta(seconds=USER_COOLDOWN_SECONDS)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore self and DMs
        if message.author.bot:
            return
        if message.guild is None:
            return

        # Channel gate
        if message.channel.id not in ALLOWED_CHANNEL_IDS:
            return

        # Only react when a user replies to Aura (replying to a bot message or pinging Aura)
        # a) direct reply to a message from Aura
        target_is_aura = False
        if message.reference and message.reference.resolved:
            ref = message.reference.resolved
            # resolved can be PartialMessage or Message
            author = getattr(ref, "author", None)
            target_is_aura = bool(author and author.id == self.bot.user.id)

        # b) contains a direct mention of Aura
        mentions_aura = self.bot.user in message.mentions if self.bot.user else False

        if not (target_is_aura or mentions_aura):
            return

        # Cooldown
        if self._on_cooldown(message.author.id, message.channel.id):
            return

        # Pick a quip
        if not self.quips:
            return
        text = random.choice(self.quips)

        try:
            await message.reply(text, mention_author=False)
        except Exception:
            return
        finally:
            self._start_cooldown(message.author.id, message.channel.id)

async def setup(bot: commands.Bot):  # discord.py 2.x dynamic extension entrypoint
    await bot.add_cog(AutoReply(bot))
