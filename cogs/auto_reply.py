import discord
from discord.ext import commands
import json
import random
import asyncio
from pathlib import Path

class AutoReply(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cooldown = 10  # seconds between replies per user
        self.cooldowns = {}
        self.quips_path = Path("data/auto_reply_quips.json")
        self.allowed_channels = {
            1399840085536407602,  # replace or add more channel IDs here if needed
        }
        self.quips = self.load_quips()

    def load_quips(self):
        try:
            with open(self.quips_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[AutoReply] Failed to load quips: {e}")
            return []

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bot messages
        if message.author.bot:
            return

        # Restrict to certain channels
        if message.channel.id not in self.allowed_channels:
            return

        # Apply cooldown per user
        now = asyncio.get_event_loop().time()
        last = self.cooldowns.get(message.author.id, 0)
        if now - last < self.cooldown:
            return
        self.cooldowns[message.author.id] = now

        # Randomly reply
        if random.random() < 0.25:  # 25% chance to reply
            reply = random.choice(self.quips) if self.quips else "..."
            await message.reply(reply)

async def setup(bot):
    await bot.add_cog(AutoReply(bot))
    print("[AutoReply] Cog loaded successfully")
