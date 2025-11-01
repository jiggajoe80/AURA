# cogs/emoji_ids.py
import io
import csv
import json
import discord
from discord import app_commands
from discord.ext import commands

# Gate: only allow server admins (adjust if you want a different rule)
def is_admin(inter: discord.Interaction) -> bool:
    return inter.user.guild_permissions.administrator

class EmojiIDs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="emoji", description="Emoji tools")

    @group.command(name="ids", description="Show custom emoji IDs for this server (optionally filter by name).")
    @app_commands.describe(
        filter="Case-insensitive name filter (e.g. 'raccoon')",
        format="How to display results"
    )
    @app_commands.choices(
        format=[
            app_commands.Choice(name="text (raw <:name:id>, comma list)", value="text"),
            app_commands.Choice(name="json (inline)", value="json"),
            app_commands.Choice(name="csv (inline)", value="csv")
        ]
    )
    async def ids(
        self,
        inter: discord.Interaction,
        filter: str | None = None,
        format: app_commands.Choice[str] | None = None
    ):
        if not is_admin(inter):
            return await inter.response.send_message("Admin only.", ephemeral=True)

        await inter.response.defer(ephemeral=True)
        guild = inter.guild
        if guild is None:
            return await inter.followup.send("Run this in a server.", ephemeral=True)

        emjs = list(guild.emojis)
        if filter:
            f = filter.lower()
            emjs = [e for e in emjs if f in e.name.lower()]

        # Build canonical rows
        rows = [{
            "name": e.name,
            "id": e.id,
            "animated": e.animated,
            "available": e.available,
            "raw": f"<{'a' if e.animated else ''}:{e.name}:{e.id}>"
        } for e in emjs]

        fmt = (format.value if format else "text")
        if fmt == "text":
            text = ", ".join(r["raw"] for r in rows) if rows else "(none)"
            return await inter.followup.send(text, ephemeral=True)

        if fmt == "json":
            return await inter.followup.send(
                f"```json\n{json.dumps(rows, indent=2)}\n```", ephemeral=True
            )

        # csv inline
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["name", "id", "animated", "available", "raw"])
        writer.writeheader()
        writer.writerows(rows)
        output.seek(0)
        preview = output.read()
        if len(preview) > 1800:
            preview = preview[:1800] + "\n... (truncated)"
        return await inter.followup.send(f"```csv\n{preview}\n```", ephemeral=True)

    @group.command(name="ids_file", description="Download the server's custom emoji list as a file.")
    @app_commands.describe(
        filter="Case-insensitive name filter (e.g. 'raccoon')",
        filetype="File type to download"
    )
    @app_commands.choices(
        filetype=[
            app_commands.Choice(name="json", value="json"),
            app_commands.Choice(name="csv", value="csv"),
            app_commands.Choice(name="txt (raw <:name:id>)", value="txt"),
        ]
    )
    async def ids_file(
        self,
        inter: discord.Interaction,
        filter: str | None = None,
        filetype: app_commands.Choice[str] | None = None
    ):
        if not is_admin(inter):
            return await inter.response.send_message("Admin only.", ephemeral=True)

        await inter.response.defer(ephemeral=True)
        guild = inter.guild
        if guild is None:
            return await inter.followup.send("Run this in a server.", ephemeral=True)

        emjs = list(guild.emojis)
        if filter:
            f = filter.lower()
            emjs = [e for e in emjs if f in e.name.lower()]

        rows = [{
            "name": e.name,
            "id": e.id,
            "animated": e.animated,
            "available": e.available,
            "raw": f"<{'a' if e.animated else ''}:{e.name}:{e.id}>"
        } for e in emjs]

        choice = (filetype.value if filetype else "json")
        if choice == "json":
            buf = io.BytesIO(json.dumps(rows, indent=2).encode("utf-8"))
            return await inter.followup.send(file=discord.File(buf, filename=f"emoji_{guild.id}.json"), ephemeral=True)

        if choice == "csv":
            sio = io.StringIO()
            writer = csv.DictWriter(sio, fieldnames=["name", "id", "animated", "available", "raw"])
            writer.writeheader()
            writer.writerows(rows)
            buf = io.BytesIO(sio.getvalue().encode("utf-8"))
            return await inter.followup.send(file=discord.File(buf, filename=f"emoji_{guild.id}.csv"), ephemeral=True)

        # txt raw
        text = ", ".join(r["raw"] for r in rows) if rows else ""
        buf = io.BytesIO(text.encode("utf-8"))
        return await inter.followup.send(file=discord.File(buf, filename=f"emoji_{guild.id}.txt"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiIDs(bot))
