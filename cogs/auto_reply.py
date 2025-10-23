import json
import random
import logging
from pathlib import Path
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("Aura")

# === Config ===
COOLDOWN_SECONDS = 10  # you asked for 10s
# Set to an empty set to allow all channels, or include your ID(s) here:
ALLOWED_CHANNELS = {1399840085536407602}  # <-- your channel id

DATA_FILE = Path(__file__).parent.parent / "data" / "auto_reply_quips.json"


def _load_quips(path: Path) -> list[str]:
    """Accepts either:
       { "items": [ { "text": "..." }, ... ] }
       or a simple [ "line1", "line2", ... ] array.
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, str)]
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
            # items may be list of dicts with "text"
            if isinstance(items, list):
                out = []
                for it in items:
                    if isinstance(it, dict) and "text" in it:
                        out.append(str(it["text"]))
                return out
    except Exception as e:
        logger.exception(f"Failed to load quips from {path}: {e}")
    return []


class AutoReply(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.quips: list[str] = _load_quips(DATA_FILE)
        self.last_reply_at: dict[int, datetime] = {}  # channel_id -> time
        logger.info(f"[auto-reply] loaded {len(self.quips)} quips from {DATA_FILE.name}")

    # --- helpers -------------------------------------------------------------

    def _cooldown_ok(self, channel_id: int) -> bool:
        last = self.last_reply_at.get(channel_id)
        if not last:
            return True
        return (datetime.utcnow() - last) >= timedelta(seconds=COOLDOWN_SECONDS)

    def _should_handle(self, message: discord.Message) -> tuple[bool, str]:
        # 1) ignore bots (including Aura)
        if message.author.bot:
            return False, "author_is_bot"

        # 2) channel allow-list
        if ALLOWED_CHANNELS and message.channel.id not in ALLOWED_CHANNELS:
            return False, f"channel_not_allowed:{message.channel.id}"

        # 3) must be a reply to Aura OR @mention Aura
        mentioned = self.bot.user in getattr(message, "mentions", [])
        replied_to_bot = False
        if message.reference and message.reference.resolved:
            replied_to_bot = getattr(message.reference.resolved, "author", None) == self.bot.user

        if not (mentioned or replied_to_bot):
            return False, "no_mention_or_reply"

        # 4) cooldown
        if not self._cooldown_ok(message.channel.id):
            return False, "cooldown"

        return True, "ok"

    # --- listener ------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        ok, reason = self._should_handle(message)
        if not ok:
            # Verbose debug — comment out if you find it noisy
            logger.debug(f"[auto-reply] ignored msg id={message.id} reason={reason}")
            return

        if not self.quips:
            logger.warning("[auto-reply] no quips loaded; nothing to say")
            return

        text = random.choice(self.quips)
        try:
            # Reply to the user’s message (keeps the thread)
            await message.reply(text, mention_author=False)
            self.last_reply_at[message.channel.id] = datetime.utcnow()
            logger.info(f"[auto-reply] replied in #{getattr(message.channel, 'name', message.channel.id)}")
        except Exception as e:
            logger.exception(f"[auto-reply] failed to send reply: {e}")

    # --- slash commands ------------------------------------------------------

    @app_commands.command(name="auto_reply_status", description="Show auto-reply settings/status.")
    async def auto_reply_status(self, interaction: discord.Interaction):
        chs = ", ".join(str(x) for x in ALLOWED_CHANNELS) if ALLOWED_CHANNELS else "ALL"
        cd = f"{COOLDOWN_SECONDS}s"
        count = len(self.quips)
        msg = f"Auto-reply: channels={chs} • cooldown={cd} • quips={count}"
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="auto_reply_test", description="Force a test quip here.")
    async def auto_reply_test(self, interaction: discord.Interaction):
        if not self.quips:
            await interaction.response.send_message("No quips loaded.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        text = random.choice(self.quips)
        try:
            await interaction.channel.send(text)
            await interaction.followup.send("Sent.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Failed to send: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReply(bot))
