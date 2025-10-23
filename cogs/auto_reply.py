# cogs/auto_reply.py
import json
import time
import random
import discord
from discord.ext import commands

ALLOWED_CHANNELS = {1399840085536407602}  # <-- your channel ID here
USER_COOLDOWN_SECONDS = 10

def _load_quips(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
        out: list[str] = []
        for it in items:
            if isinstance(it, dict) and "text" in it:
                out.append(str(it["text"]))
            elif isinstance(it, str):
                out.append(it)
        return [s.strip() for s in out if s and s.strip()]
    except Exception:
        return []

class AutoReply(commands.Cog):
    """Reply with a light quip when mentioned or when users reply to Aura."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.quips_path = "data/auto_reply_quips.json"
        self.quips = _load_quips(self.quips_path) or [
            "Beep boop.",
            "Affirmative.",
            "On it.",
            "Noted!",
        ]
        self.user_cooldowns: dict[int, float] = {}

    def _on_cooldown(self, user_id: int) -> bool:
        now = time.time()
        last = self.user_cooldowns.get(user_id, 0.0)
        if now - last < USER_COOLDOWN_SECONDS:
            return True
        self.user_cooldowns[user_id] = now
        return False

    def _should_handle(self, msg: discord.Message) -> bool:
        # ignore bots & DMs
        if msg.author.bot or not msg.guild:
            return False
        # channel allowlist
        if msg.channel.id not in ALLOWED_CHANNELS:
            return False
        # mention path
        if msg.mention_everyone:
            return False
        if self.bot.user and self.bot.user in msg.mentions:
            return True
        # reply-to-Aura path
        if msg.reference and isinstance(msg.reference.resolved, discord.Message):
            ref = msg.reference.resolved
            if ref.author and ref.author.id == self.bot.user.id:
                return True
        return False

    @commands.Cog.listener("on_message")
    async def handle_message(self, msg: discord.Message):
        if not self._should_handle(msg):
            return
        if self._on_cooldown(msg.author.id):
            return
        # random quip
        quip = random.choice(self.quips) if self.quips else "✨"
        try:
            await msg.reply(quip, mention_author=False, suppress=True)
        except Exception:
            # fallback (e.g., missing perms)
            try:
                await msg.channel.send(quip, suppress=True)
            except Exception:
                pass

    # small helper slash cmds (optional)
    @commands.hybrid_command(name="autoreply_status", with_app_command=True, description="Show auto-reply status.")
    async def autoreply_status(self, ctx: commands.Context):
        await ctx.reply(
            f"Auto-reply is active in {len(ALLOWED_CHANNELS)} channel(s). Loaded quips: {len(self.quips)}.",
            mention_author=False,
            suppress=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReply(bot))
