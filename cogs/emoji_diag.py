import json
import logging
from pathlib import Path
from typing import Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("Aura.emoji_diag")

DATA = Path(__file__).resolve().parents[1] / "data"
CONFIG_PATH = DATA / "emoji" / "config.json"
POOLS_DIR = DATA / "emoji" / "pools"

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"[emoji_diag] failed to read config.json: {e}")
        return {}

def _pool_for_guild(guild_id: int) -> Optional[dict]:
    cfg = _load_config()
    # config maps guild_id (as string) -> filename
    filename = cfg.get(str(guild_id))
    if not filename:
        return None
    try:
        pool = json.loads((POOLS_DIR / filename).read_text(encoding="utf-8"))
        return pool if isinstance(pool, dict) else None
    except Exception as e:
        log.error(f"[emoji_diag] failed to read pool {filename}: {e}")
        return None

def _parse_emoji(s: str) -> Tuple[Optional[discord.PartialEmoji], Optional[str]]:
    """
    Accepts: "✨" or "<:raccoon_wink:123456789012345678>" or "<a:name:ID>"
    Returns (partial, unicode) where only one is non-None.
    """
    s = s.strip()
    if s.startswith("<") and s.endswith(">"):
        try:
            pe = discord.PartialEmoji.from_str(s)
            return pe, None
        except Exception:
            return None, None
    # assume unicode
    return None, s if s else None

class EmojiDiag(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    diag = app_commands.Group(name="emoji_diag", description="Diagnostics for emoji availability")

    @diag.command(name="usable", description="List usable emoji from this guild's configured pool")
    @app_commands.describe(bucket="Which bucket to check (autopost, user_message, event_soon)", limit="How many to sample (1-25)")
    async def usable(self, interaction: discord.Interaction, bucket: str = "autopost", limit: int = 10):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            return await interaction.followup.send("Run this in a server.", ephemeral=True)

        pool = _pool_for_guild(interaction.guild.id)
        if not pool or bucket not in pool:
            return await interaction.followup.send(f"No pool configured or bucket '{bucket}' missing.", ephemeral=True)

        items = list(pool[bucket])[: max(1, min(25, limit))]
        ok, bad = [], []
        for s in items:
            pe, uni = _parse_emoji(s)
            try:
                if pe:
                    # To be usable here: Aura must see the source guild AND target guild must allow external emoji
                    # add_reaction on a dummy ephemeral won't work; we probe via to_dict() and presence only.
                    # Practical probe: try to send then delete a tiny message with the emoji.
                    msg = await interaction.channel.send(s)
                    await msg.delete()
                    ok.append(s)
                elif uni:
                    msg = await interaction.channel.send(uni)
                    await msg.delete()
                    ok.append(uni)
                else:
                    bad.append(s)
            except Exception as e:
                bad.append(f"{s}  —  {type(e).__name__}: {e}")

        lines = []
        lines.append(f"Guild: **{interaction.guild.name}** ({interaction.guild.id}) • Bucket: `{bucket}` • Sample: {len(items)}")
        lines.append(f"✅ Usable: {len(ok)}")
        if ok:
            lines.append("  " + ", ".join(ok))
        lines.append(f"⛔ Not usable here: {len(bad)}")
        if bad:
            lines.append("  " + "\n  ".join(bad))

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    @diag.command(name="try", description="Try reacting here with a specific emoji (unicode or <:name:id>)")
    @app_commands.describe(emoji="Unicode or custom syntax like <:name:123...>", to_last="React to your last message in this channel instead of this command")
    async def try_react(self, interaction: discord.Interaction, emoji: str, to_last: bool = False):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            return await interaction.followup.send("Run this in a server.", ephemeral=True)

        pe, uni = _parse_emoji(emoji)
        target = None

        try:
            if to_last:
                # Fetch the user's last message in this channel (excluding this slash)
                async for m in interaction.channel.history(limit=20):
                    if m.author.id == interaction.user.id and m.id != interaction.message.id:
                        target = m
                        break

            if target is None:
                target = await interaction.channel.send("emoji_diag probe")

            try:
                if pe:
                    await target.add_reaction(pe)
                elif uni:
                    await target.add_reaction(uni)
                else:
                    raise ValueError("Could not parse emoji")
                await interaction.followup.send(f"✅ Reacted with {emoji}", ephemeral=True)
            finally:
                # Clean the probe message if we created it
                if target.content == "emoji_diag probe":
                    try:
                        await target.delete()
                    except Exception:
                        pass
        except Exception as e:
            await interaction.followup.send(f"⛔ Failed to use {emoji} here — {type(e).__name__}: {e}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiDiag(bot))
