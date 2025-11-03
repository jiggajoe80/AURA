# cogs/emoji_diag.py
# Viewer — zero-arg peek + mixed sampler (up to 2 custom + up to 2 unicode)

from __future__ import annotations

import random
from typing import List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

EMOJI_COG_NAME = "EmojiCog"


class EmojiDiag(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ----- helpers -----

    def _engine(self):
        cog = self.bot.get_cog(EMOJI_COG_NAME)
        if not cog:
            raise RuntimeError("Emoji engine not loaded.")
        return cog

    def _title(self, text: str) -> str:
        return f"**{text}**"

    def _fmt_line(self, k: str, v: str) -> str:
        return f"{self._title(k)}\n{v}"

    # ----- slash: zero-argument peek -----

    @app_commands.command(name="emoji_diag_peek", description="Show emoji config, pool file, and bucket samples.")
    async def emoji_diag_peek(self, inter: discord.Interaction):
        eng = self._engine()
        guild = inter.guild
        if not guild:
            await inter.response.send_message("Use in a server.", ephemeral=True)
            return

        gc = eng.get_guild_config(guild.id)
        enabled = bool(gc.get("enabled", False))
        pool_file = eng.get_pool_file_for(guild.id)

        # samples
        def line_for(bucket: str) -> str:
            raw, usable, sample = eng.sample_bucket(guild, bucket, k_unicode=2, k_custom=2)
            if usable == 0:
                return f"• {bucket}: {raw} total | sample → *(none usable)*"
            return f"• {bucket}: {raw} total | sample → {' '.join(sample)}"

        msg = []
        msg.append(self._title("Emoji config"))
        msg.append(self._fmt_line("Guild", f"{guild.name} ({guild.id})"))
        msg.append(self._fmt_line("Enabled", "True" if enabled else "False"))
        msg.append(self._fmt_line("Pool file", f"{pool_file}"))
        msg.append("")
        msg.append(self._title("Buckets:"))
        msg.append(line_for("autopost"))
        msg.append(line_for("user_message"))
        msg.append(line_for("event_soon"))

        await inter.response.send_message("\n".join(msg), ephemeral=True)

    # ----- optional: bucket-only quick look with explicit name -----

    @app_commands.command(name="emoji_diag_bucket", description="Show counts + mixed sample for a specific bucket.")
    @app_commands.describe(bucket="Bucket name: autopost, user_message, or event_soon")
    @app_commands.choices(bucket=[
        app_commands.Choice(name="autopost", value="autopost"),
        app_commands.Choice(name="user_message", value="user_message"),
        app_commands.Choice(name="event_soon", value="event_soon"),
    ])
    async def emoji_diag_bucket(self, inter: discord.Interaction, bucket: app_commands.Choice[str]):
        eng = self._engine()
        guild = inter.guild
        if not guild:
            await inter.response.send_message("Use in a server.", ephemeral=True)
            return
        raw, usable, sample = eng.sample_bucket(guild, bucket.value, k_unicode=2, k_custom=2)
        if usable == 0:
            msg = f"{bucket.value}: {raw} total | sample → *(none usable)*"
        else:
            msg = f"{bucket.value}: {raw} total | usable={usable} | sample → {' '.join(sample)}"
        await inter.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiDiag(bot))
