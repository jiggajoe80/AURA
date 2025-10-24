# cogs/timezones.py
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from zoneinfo import ZoneInfo

US_ZONES = [
    ("EST", "America/New_York"),
    ("CST", "America/Chicago"),
    ("MST", "America/Denver"),
    ("PST", "America/Los_Angeles"),
]

class Timezones(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="time", description="Show current US time zones.")
    async def time(self, interaction: discord.Interaction):
        now = datetime.now()
        lines = []
        header_day = now.astimezone(ZoneInfo("America/New_York")).strftime("%A")
        header_date = now.astimezone(ZoneInfo("America/New_York")).strftime("%B, %-d, %Y")
        lines.append(f"Today is **{header_day}** — {header_date} — and the current time is:")

        for label, zone in US_ZONES:
            tz = ZoneInfo(zone)
            local = now.astimezone(tz)
            lines.append(f"• 🕒 **{local.strftime('%-I:%M%p').lower()}** —**{label}**—")

        await interaction.response.send_message("\n".join(lines))

async def setup(bot):
    await bot.add_cog(Timezones(bot))
