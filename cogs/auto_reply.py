# cogs/auto_reply.py
import json
import random
from pathlib import Path
from datetime import datetime, timedelta

import discord
from discord.ext import commands

# ---------------------------
# Config
# ---------------------------
DATA_FILE = Path("data/auto_reply_quips.json")

# channel allow-list (server text channel IDs)
ALLOWED_CHANNELS = {1399840085536407602}

# seconds between replies in the same channel
COOLDOWN_SECONDS = 5

# cooldown UX
COOLDOWN_REACTION = "⏳"       # reaction to add when on cooldown
SELF_DELETE_NOTE = True       # set True to also send a short note
SELF_DELETE_AFTER = 5          # seconds to keep the note, if enabled


def _load_quips(path: Path) -> list[str]:
    """Load quips from minimal JSON.

    Accepts either:
      { "items": [ { "text": "..." }, ... ] }
    or
      [ "line1", "line2", ... ]
    """
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    if isinstance(data, list):
        # simple list of strings
        return [str(x) for x in data if isinstance(x, str)]

    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        out = []
        for item in data["items"]:
            if isinstance(item, dict) and "text" in item:
                out.append(str(item["text"]))
        return out

    return []


class AutoReply(commands.Cog):
    """Ping/reply auto-responder with per-channel cooldown."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.quips: list[str] = _load_quips(DATA_FILE)
        self.last_reply_at: dict[int, datetime] = {}  # channel_id -> time

    # ---------------------------
    # Helpers
    # ---------------------------
    def _is_allowed_channel(self, channel_id: int) -> bool:
        return channel_id in ALLOWED_CHANNELS

    def _is_cooldown(self, channel_id: int) -> bool:
        now = datetime.utcnow()
        last = self.last_reply_at.get(channel_id)
        if not last:
            return False
        return (now - last) < timedelta(seconds=COOLDOWN_SECONDS)

    def _mark_replied(self, channel_id: int) -> None:
        self.last_reply_at[channel_id] = datetime.utcnow()

    def _message_mentions_or_replies_to_bot(self, message: discord.Message) -> bool:
        # Mention
        if self.bot.user and self.bot.user in message.mentions:
            return True
        # Reply to Aura
        if message.reference and isinstance(message.reference.resolved, discord.Message):
            ref: discord.Message = message.reference.resolved
            if ref.author and self.bot.user and ref.author.id == self.bot.user.id:
                return True
        return False

    # ---------------------------
    # Core listener
    # ---------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore own messages and DMs
        if message.author.bot or not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return

        channel_id = message.channel.id if isinstance(message.channel, discord.TextChannel) else message.channel.parent_id
        if not channel_id:
            return

        if not self._is_allowed_channel(channel_id):
            return

        if not self._message_mentions_or_replies_to_bot(message):
            return

        # cooldown handling
        if self._is_cooldown(channel_id):
            try:
                if COOLDOWN_REACTION:
                    await message.add_reaction(COOLDOWN_REACTION)
            except Exception:
                pass

            if SELF_DELETE_NOTE:
                try:
                    note = await message.reply(f"⏳ Cooling down {COOLDOWN_SECONDS}s…", mention_author=False)
                    await note.delete(delay=SELF_DELETE_AFTER)
                except Exception:
                    pass
            return

        # pick a quip and reply
        if not self.quips:
            return  # nothing to say

        quip = random.choice(self.quips)
        try:
            await message.reply(quip, mention_author=False)
        finally:
            self._mark_replied(channel_id)

    # ---------------------------
    # Diagnostics (global slash)
    # ---------------------------
    @discord.app_commands.command(name="auto_reply_status", description="Show auto-reply configuration.")
    async def auto_reply_status(self, interaction: discord.Interaction):
        alive = "loaded" if self.quips else "empty"
        chans = ", ".join(str(cid) for cid in sorted(ALLOWED_CHANNELS)) or "none"
        text = f"channels={chans} • cooldown={COOLDOWN_SECONDS}s • quips={alive}"
        await interaction.response.send_message(text, ephemeral=True)

    @discord.app_commands.command(name="auto_reply_test", description="Post a sample quip now.")
    async def auto_reply_test(self, interaction: discord.Interaction):
        # try to post where the command ran
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await interaction.response.send_message("Wrong channel type.", ephemeral=True)

        channel_id = channel.id if isinstance(channel, discord.TextChannel) else channel.parent_id
        if channel_id not in ALLOWED_CHANNELS:
            return await interaction.response.send_message("This channel is not in the allow-list.", ephemeral=True)

        if not self.quips:
            return await interaction.response.send_message("No quips loaded.", ephemeral=True)

        quip = random.choice(self.quips)
        await interaction.response.send_message("Sending…", ephemeral=True)
        try:
            await channel.send(quip)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoReply(bot))
