# cogs/reload_content.py
# Lightweight “reload JSON” utilities wired into the existing CommandTree.
# Works with a discord.Client subclass that owns .tree (app_commands.CommandTree)

from __future__ import annotations

import json
import os
from pathlib import Path

import discord
from discord import app_commands


def setup(*, client: discord.Client, tree: app_commands.CommandTree,
          presence_ref: list[str], hourly_ref: list[str], logger) -> None:
    """
    Attach three slash commands to the provided tree:

      /content_status   -> anyone; shows counts from in-memory pools
      /presence_reload  -> admins only; reloads data/AURA.PRESENCE.v2.json
      /hourly_reload    -> admins only; reloads data/AURA.HOURLIES.v2.json
    """

    # Where the JSON files live on disk
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"
    PRESENCE_FILE = os.getenv("AURA_PRESENCE_FILE", "AURA.PRESENCE.v2.json")
    HOURLIES_FILE = os.getenv("AURA_HOURLIES_FILE", "AURA.HOURLIES.v2.json")

    def _load_lines(path: Path) -> list[str]:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Accept either {"presence":[...]} / {"hourlies":[...]} or a raw list
        if isinstance(data, dict):
            # take first list-like value
            for v in data.values():
                if isinstance(v, list):
                    return [str(x).strip() for x in v if str(x).strip()]
            return []
        return [str(x).strip() for x in data if str(x).strip()]

    async def _require_admin(inter: discord.Interaction) -> bool:
        # DMs: deny; Guild: only admins
        if not inter.guild:
            await inter.response.send_message("Server only.", ephemeral=True)
            return False
        if not getattr(inter.user, "guild_permissions", None) or not inter.user.guild_permissions.administrator:
            await inter.response.send_message("Admins only.", ephemeral=True)
            return False
        return True

    # ---- callbacks ---------------------------------------------------------

    async def content_status(inter: discord.Interaction):
        await inter.response.send_message(
            f"Presence lines loaded: **{len(presence_ref)}**\n"
            f"Hourly lines loaded: **{len(hourly_ref)}**",
            ephemeral=True,
        )

    async def presence_reload(inter: discord.Interaction):
        if not await _require_admin(inter):
            return
        try:
            lines = _load_lines(DATA_DIR / PRESENCE_FILE)
            presence_ref.clear()
            presence_ref.extend(lines)
            logger.info("Presence JSON reloaded: %d lines", len(lines))
            await inter.followup.send(f"Presence reloaded: **{len(lines)}** lines.", ephemeral=True)
        except Exception as e:
            logger.exception("Presence reload failed: %s", e)
            await inter.followup.send(f"Presence reload failed: `{e}`", ephemeral=True)

    async def hourly_reload(inter: discord.Interaction):
        if not await _require_admin(inter):
            return
        try:
            lines = _load_lines(DATA_DIR / HOURLIES_FILE)
            hourly_ref.clear()
            hourly_ref.extend(lines)
            logger.info("Hourlies JSON reloaded: %d lines", len(lines))
            await inter.followup.send(f"Hourlies reloaded: **{len(lines)}** lines.", ephemeral=True)
        except Exception as e:
            logger.exception("Hourlies reload failed: %s", e)
            await inter.followup.send(f"Hourlies reload failed: `{e}`", ephemeral=True)

    # ---- register commands on the provided tree ----------------------------

    tree.add_command(app_commands.Command(
        name="content_status",
        description="Show how many lines are loaded (presence & hourly).",
        callback=content_status,
    ))

    tree.add_command(app_commands.Command(
        name="presence_reload",
        description="Reload presence JSON (admins only).",
        callback=presence_reload,
    ))

    tree.add_command(app_commands.Command(
        name="hourly_reload",
        description="Reload hourly JSON (admins only).",
        callback=hourly_reload,
    ))

    logger.info("Reload cog attached.")
