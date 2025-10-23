# cogs/auto_reply.py
from __future__ import annotations
import random
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Set

import discord
from discord import app_commands

DATA_DIR = Path(__file__).parents[1] / "data"
QUIPS_FILE = "quips.json"

# Config (you can move these into ENV later if you want)
ALLOW_CHANNELS: Set[int] = {1399840085536407602}  # your allowed channel(s)
COOLDOWN_SECONDS = 10
SERVER_ONLY = True

def _load_quips() -> List[str]:
    fp = DATA_DIR / QUIPS_FILE
    try:
        with fp.open("r", encoding="utf-8") as f:
            obj = json.load(f)
        items = obj.get("items", [])
        quips = [it["text"] for it in items if isinstance(it, dict) and isinstance(it.get("text"), str)]
        if quips:
            return quips
    except Exception as e:
        print(f"[auto_reply] WARN: failed to load {fp.name}: {e}")
    # safe fallback
    return [
        "👋 hey, I heard my name.",
        "I’m here—be gentle. ☘️",
        "mm? oh! hi.",
        "noted.",
    ]

class AutoReply(discord.Client):
    ...

# We don’t subclass the client; we *attach* behavior to the existing client

def setup(client: discord.Client) -> None:
    """
    Attach auto-reply behavior + slash commands to an existing Client.
    """
    tree = client.tree

    # runtime state held on the client
    if not hasattr(client, "_ar_enabled"):
        client._ar_enabled = True
    if not hasattr(client, "_ar_quips"):
        client._ar_quips = _load_quips()
    if not hasattr(client, "_ar_user_cooldowns"):
        client._ar_user_cooldowns = {}  # user_id -> datetime

    # --- helpers ---
    def _is_allowed_channel(channel_id: int) -> bool:
        return channel_id in ALLOW_CHANNELS

    def _on_cooldown(user_id: int) -> bool:
        now = datetime.utcnow()
        until = client._ar_user_cooldowns.get(user_id)
        if until and now < until:
            return True
        client._ar_user_cooldowns[user_id] = now + timedelta(seconds=COOLDOWN_SECONDS)
        return False

    # --- event hook: auto replies on mention or reply-to-Aura ---
    @client.event
    async def on_message(message: discord.Message):
        # preserve existing on_message chain
        if hasattr(client, "_orig_on_message"):
            try:
                await client._orig_on_message(message)
            except Exception:
                pass

        if message.author.bot:
            return
        if SERVER_ONLY and (message.guild is None):
            return
        if not getattr(client, "_ar_enabled", True):
            return
        if not _is_allowed_channel(message.channel.id):
            return

        # Trigger if: mentions Aura OR replying to Aura
        mentioned = client.user in message.mentions if client.user else False
        is_reply_to_aura = False
        if message.reference and isinstance(message.reference.resolved, discord.Message):
            is_reply_to_aura = (message.reference.resolved.author == client.user)

        if not (mentioned or is_reply_to_aura):
            return

        # Cooldown per user
        if _on_cooldown(message.author.id):
            return

        # Pick a quip and respond
        quips = getattr(client, "_ar_quips", None) or _load_quips()
        text = random.choice(quips)
        try:
            await message.reply(text, mention_author=False)
        except Exception as e:
            print(f"[auto_reply] ERROR sending reply: {e}")

    # preserve any pre-existing on_message from main.py
    if not hasattr(client, "_orig_on_message"):
        client._orig_on_message = client.on_message

    # --- slash commands (admins only) ---
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    @tree.command(name="autoreply", description="Control Aura's auto-replies.")
    async def autoreply(inter: discord.Interaction, mode: str):
        """
        /autoreply on | off | status
        """
        mode = (mode or "").lower().strip()
        if mode not in {"on", "off", "status"}:
            await inter.response.send_message("Use: `/autoreply on`, `/autoreply off`, or `/autoreply status`.", ephemeral=True)
            return

        if mode == "status":
            state = "ON" if getattr(client, "_ar_enabled", True) else "OFF"
            await inter.response.send_message(f"Auto-replies are **{state}**.\nAllowed channels: {', '.join(f'<#{cid}>' for cid in ALLOW_CHANNELS)}\nCooldown: {COOLDOWN_SECONDS}s", ephemeral=True)
            return

        client._ar_enabled = (mode == "on")
        await inter.response.send_message(f"Auto-replies **{mode.upper()}**.", ephemeral=True)

    # add a small /autoreply_reload to re-read quips.json (admins only)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    @tree.command(name="autoreply_reload", description="Reload quips.json on the fly.")
    async def autoreply_reload(inter: discord.Interaction):
        client._ar_quips = _load_quips()
        await inter.response.send_message(f"Reloaded quips: **{len(client._ar_quips)}** lines.", ephemeral=True)
