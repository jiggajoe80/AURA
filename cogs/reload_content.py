# cogs/reload_content.py
# Adds 3 slash commands to your existing Client:
# /content_status, /presence_reload, /hourly_reload

from __future__ import annotations
from typing import Callable, Optional
import random
import sys
import discord
from discord import app_commands

def setup(
    client: discord.Client,
    load_presence_cb: Callable[[], list[str]],
    load_hourly_cb: Callable[[], list[str]],
    after_reload: Optional[Callable[[], None]] = None,
) -> None:
    """
    Attach slash commands to the provided client.
    We update __main__.PRESENCE_LINES / HOURLY_LINES so your
    daily reset logic continues to use fresh data.
    """
    tree = client.tree

    def _is_admin(inter: discord.Interaction) -> bool:
        # simple/for now: manage_guild = true
        perms = getattr(getattr(inter.user, "guild_permissions", None), "manage_guild", False)
        return bool(perms)

    @tree.command(name="content_status", description="Show how many lines are in memory (presence/hourly).")
    async def content_status(inter: discord.Interaction):
        pres_loaded = getattr(client, "presence_pool", None)
        hour_loaded = getattr(client, "hourly_pool", None)
        pres_n = len(pres_loaded) if isinstance(pres_loaded, list) else 0
        hour_n = len(hour_loaded) if isinstance(hour_loaded, list) else 0
        await inter.response.send_message(
            f"🧪 Loaded now → Presence: **{pres_n}** | Hourly: **{hour_n}**",
            ephemeral=True,
        )

    @tree.command(name="presence_reload", description="Reload presence JSON (admins only).")
    async def presence_reload(inter: discord.Interaction):
        if not _is_admin(inter):
            await inter.response.send_message("❌ Admins only (Manage Server).", ephemeral=True)
            return

        # Refresh from disk via the callback
        new_lines = load_presence_cb()
        if not new_lines:
            await inter.response.send_message("⚠️ No lines found in presence file.", ephemeral=True)
            return

        # Update globals on the running main module so midnight resets pick it up
        main_mod = sys.modules.get("__main__")
        if main_mod is not None:
            main_mod.PRESENCE_LINES = list(new_lines)

        # Immediately refresh today's pool so it’s live now
        client.presence_pool = list(new_lines)
        random.shuffle(client.presence_pool)
        client.used_presence_today = []

        if after_reload:
            after_reload()

        await inter.response.send_message(f"✅ Presence reloaded: **{len(new_lines)}** lines.", ephemeral=True)

    @tree.command(name="hourly_reload", description="Reload hourly JSON (admins only).")
    async def hourly_reload(inter: discord.Interaction):
        if not _is_admin(inter):
            await inter.response.send_message("❌ Admins only (Manage Server).", ephemeral=True)
            return

        new_lines = load_hourly_cb()
        if not new_lines:
            await inter.response.send_message("⚠️ No lines found in hourly file.", ephemeral=True)
            return

        main_mod = sys.modules.get("__main__")
        if main_mod is not None:
            main_mod.HOURLY_LINES = list(new_lines)

        client.hourly_pool = list(new_lines)
        random.shuffle(client.hourly_pool)
        client.used_hourly_today = []

        if after_reload:
            after_reload()

        await inter.response.send_message(f"✅ Hourly reloaded: **{len(new_lines)}** lines.", ephemeral=True)
