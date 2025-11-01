# cogs/emoji_ids.py
# Aura — Emoji ID tools (DEV utility)
# Provides:
#   /emoji ids                → inline list (text/json/csv) with optional name filter
#   /emoji capture_all        → admin-only, downloads a file (json/csv/txt)
#   /emoji capture_filter     → admin-only, downloads a file for a name filter

from __future__ import annotations

import io
import csv
import json
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands


class EmojiIDs(commands.Cog):
    """Utility cog to list or download server emoji IDs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Slash command group: /emoji
    group = app_commands.Group(name="emoji", description="Emoji tools")

    # -------------------------------------------------------------------------
    # Shared helper: builds inline or file output from the guild's custom emojis
    # -------------------------------------------------------------------------
    async def ids_file_callback(
        self,
        inter: discord.Interaction,
        name_filter: Optional[str],
        filetype: str,
        as_file: bool = True,
    ) -> None:
        """
        name_filter: case-insensitive substring for emoji names (or None)
        filetype: 'text'|'txt'|'json'|'csv'
        as_file: if True -> upload file; if False -> inline result
        """
        if inter.guild is None:
            await inter.followup.send("This command must be used in a server.", ephemeral=True)
            return

        filter_l = (name_filter or "").lower()
        rows = []
        for e in inter.guild.emojis:
            if filter_l and filter_l not in e.name.lower():
                continue
            raw = f"<{'a' if e.animated else ''}:{e.name}:{e.id}>"
            rows.append(
                {
                    "name": e.name,
                    "id": e.id,
                    "animated": e.animated,
                    "string": raw,
                }
            )

        if not rows:
            await inter.followup.send("No matching custom emojis found.", ephemeral=True)
            return

        filetype = (filetype or "json").lower()

        # TEXT / TXT (raw <:name:id>, comma-separated)
        if filetype in ("text", "txt"):
            payload = ", ".join(r["string"] for r in rows)
            if as_file:
                buf = io.BytesIO(payload.encode("utf-8"))
                await inter.followup.send(file=discord.File(buf, filename="emoji.txt"), ephemeral=True)
            else:
                # keep inline short to avoid hitting message limits
                await inter.followup.send(
                    payload if len(payload) < 1800 else f"{payload[:1750]}…", ephemeral=True
                )
            return

        # JSON
        if filetype == "json":
            payload = json.dumps(rows, ensure_ascii=False, indent=2)
            if as_file:
                buf = io.BytesIO(payload.encode("utf-8"))
                await inter.followup.send(file=discord.File(buf, filename="emoji.json"), ephemeral=True)
            else:
                # pretty inline view
                snippet = payload if len(payload) < 1900 else payload[:1900]
                await inter.followup.send(f"```json\n{snippet}\n```", ephemeral=True)
            return

        # CSV
        if filetype == "csv":
            s = io.StringIO()
            writer = csv.DictWriter(s, fieldnames=["name", "id", "animated", "string"])
            writer.writeheader()
            writer.writerows(rows)
            data = s.getvalue().encode("utf-8")
            if as_file:
                await inter.followup.send(file=discord.File(io.BytesIO(data), filename="emoji.csv"), ephemeral=True)
            else:
                await inter.followup.send("CSV generated. Use the download option for full content.", ephemeral=True)
            return

        await inter.followup.send("Unsupported format. Use text/json/csv.", ephemeral=True)

    # -------------------------------------------------------------------------
    # /emoji ids  → inline quick view, safe for everyone
    # -------------------------------------------------------------------------
    @group.command(
        name="ids",
        description="Show custom emoji IDs for this server (optionally filter by name).",
    )
    @app_commands.describe(
        filter="Case-insensitive name filter (e.g., 'raccoon')",
        format="How to display results",
    )
    @app_commands.choices(
        format=[
            app_commands.Choice(name="text (raw <:name:id>, comma list)", value="text"),
            app_commands.Choice(name="json (inline)", value="json"),
            app_commands.Choice(name="csv (inline)", value="csv"),
        ]
    )
    async def ids(
        self,
        inter: discord.Interaction,
        filter: Optional[str] = None,
        format: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await inter.response.defer(ephemeral=True)
        await self.ids_file_callback(inter, filter, format.value if format else "text", as_file=False)

    # -------------------------------------------------------------------------
    # /emoji capture_all  → admin-only download as file
    # -------------------------------------------------------------------------
    @group.command(
        name="capture_all",
        description="Download ALL custom emojis in this server as a file.",
    )
    @app_commands.choices(
        filetype=[
            app_commands.Choice(name="json", value="json"),
            app_commands.Choice(name="csv", value="csv"),
            app_commands.Choice(name="txt (raw <:name:id>)", value="txt"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def capture_all(
        self,
        inter: discord.Interaction,
        filetype: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await inter.response.defer(ephemeral=True)
        await self.ids_file_callback(inter, None, (filetype.value if filetype else "json"), as_file=True)

    # -------------------------------------------------------------------------
    # /emoji capture_filter  → admin-only download as file (name filter)
    # -------------------------------------------------------------------------
    @group.command(
        name="capture_filter",
        description="Download filtered custom emojis (by name) as a file.",
    )
    @app_commands.describe(
        filter="Case-insensitive text to match in emoji names (e.g., 'raccoon', 'clover', 'wink')."
    )
    @app_commands.choices(
        filetype=[
            app_commands.Choice(name="json", value="json"),
            app_commands.Choice(name="csv", value="csv"),
            app_commands.Choice(name="txt (raw <:name:id>)", value="txt"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def capture_filter(
        self,
        inter: discord.Interaction,
        filter: str,
        filetype: Optional[app_commands.Choice[str]] = None,
    ) -> None:
        await inter.response.defer(ephemeral=True)
        await self.ids_file_callback(inter, filter, (filetype.value if filetype else "json"), as_file=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EmojiIDs(bot))
