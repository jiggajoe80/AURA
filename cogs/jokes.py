# cogs/jokes.py
import json
import random
import re
from pathlib import Path
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

JOKES_FILE = Path(__file__).resolve().parent.parent / "data" / "jokes.json"

def _load_jokes():
    try:
        with open(JOKES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Accept either [{"setup": "...","punchline":"..."}] OR {"items":[...]}
        items = data.get("items", data)
        jokes = []
        for j in items:
            # tolerate flat string "setup ||punchline||" too
            if isinstance(j, str):
                m = re.match(r"^(.*?)\s*\|\|(.*?)\|\|\s*$", j)
                if m:
                    jokes.append({"setup": m.group(1).strip(), "punchline": m.group(2).strip()})
                else:
                    jokes.append({"setup": j.strip(), "punchline": "||…||"})
            else:
                jokes.append({"setup": j.get("setup", "").strip(), "punchline": j.get("punchline", "").strip()})
        return [j for j in jokes if j["setup"]]
    except Exception:
        return []

class Jokes(commands.Cog):
    """Phase 4: /joke + optional hourly jokes that mirror hourly-post logic."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.jokes = _load_jokes()
        # Hourly joke track (disabled by default)
        self._hourly_enabled = False
        self._last_hourly_joke = datetime.utcnow() - timedelta(hours=2)

        # cache main’s fields if present
        self._hourly_channel_id = getattr(bot, "hourly_channel_id", None)  # if you set this in main
        self._cooldown_seconds = getattr(bot, "hourly_cooldown_seconds", 10)  # fall back to 10
        self._inactivity_minutes = getattr(bot, "inactivity_minutes", 10)  # fall back to 10

        # start loop
        self._hourly_joke_loop.change_interval(seconds=60)
        self._hourly_joke_loop.start()

    def cog_unload(self):
        self._hourly_joke_loop.cancel()

    # ===== Slash commands =====

    @app_commands.command(name="joke", description="Send a random joke (setup + hidden punchline).")
    async def joke_cmd(self, interaction: discord.Interaction):
        if not self.jokes:
            await interaction.response.send_message("No jokes loaded yet.", ephemeral=True)
            return
        joke = random.choice(self.jokes)
        msg = f"{joke['setup']}  ||{joke['punchline']}||"
        await interaction.response.send_message(msg)

    @app_commands.command(name="joke_status", description="(Admin) Show hourly-joke status.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def joke_status(self, interaction: discord.Interaction):
        ch = self._hourly_channel_id or "same as hourlies"
        await interaction.response.send_message(
            f"Hourly jokes: **{'ON' if self._hourly_enabled else 'OFF'}** • channel={ch}", ephemeral=True
        )

    @app_commands.command(name="joke_hourly", description="(Admin) Turn hourly jokes on or off.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(mode="Choose 'on' or 'off'")
    async def joke_hourly(self, interaction: discord.Interaction, mode: str):
        mode = mode.lower().strip()
        if mode not in ("on", "off"):
            await interaction.response.send_message("Use `on` or `off`.", ephemeral=True)
            return
        self._hourly_enabled = (mode == "on")
        await interaction.response.send_message(f"Hourly jokes **{mode.upper()}**.", ephemeral=True)

    # ===== Background loop =====

    @tasks.loop(seconds=60)
    async def _hourly_joke_loop(self):
        if not self._hourly_enabled:
            return
        # mirror “post only on/after the hour, once” style:
        now = datetime.utcnow()
        if now.minute != 0:  # only at :00
            return
        if (now - self._last_hourly_joke).total_seconds() < 3500:  # safety: ~58m
            return

        # require same inactivity window as hourlies (if main exposes .last_channel_activity)
        last_act = getattr(self.bot, "last_channel_activity", {})
        target_channel_id = self._hourly_channel_id or getattr(self.bot, "hourly_channel_id", None)
        if not target_channel_id:
            # fallback: use the last channel aura saw activity in (your main tracks this)
            target_channel_id = getattr(self.bot, "last_hourly_channel_id", None)
        if not target_channel_id:
            return

        try:
            ch = self.bot.get_channel(int(target_channel_id))
            if not ch:
                return
            # inactivity gate
            last = last_act.get(str(target_channel_id)) or last_act.get(int(target_channel_id))
            if last and (now - last).total_seconds() < (self._inactivity_minutes * 60):
                return

            if not self.jokes:
                return

            joke = random.choice(self.jokes)
            msg = f"{joke['setup']}  ||{joke['punchline']}||"
            await ch.send(msg)
            self._last_hourly_joke = datetime.utcnow()
        except Exception:
            # stay silent; loop keeps running
            pass

async def setup(bot: commands.Bot):
    await bot.add_cog(Jokes(bot))
