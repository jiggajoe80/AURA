# cogs/timezones.py
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from zoneinfo import ZoneInfo

NEUTRAL_CLOCK = "🕒"

# Fixed labels by request (always EST/CST/MST/PST)
ZONES = [
    ("—EST—", "America/New_York"),
    ("—CST—", "America/Chicago"),
    ("—MST—", "America/Denver"),
    ("—PST—", "America/Los_Angeles"),
]

def fmt_date(dt: datetime) -> str:
    # Example: Wednesday — October, 22, 2025
    # (note the comma after the month, per your spec)
    day = dt.strftime("%A")
    month = dt.strftime("%B")
    d = dt.strftime("%d").lstrip("0")
    year = dt.strftime("%Y")
    return f"**{day}** — {month}, {d}, {year}"

def fmt_time(dt: datetime) -> str:
    # 12-hour, lowercase am/pm, no leading zero (e.g., 6:45am)
    t = dt.strftime("%I:%M%p")  # e.g., '06:45AM'
    t = t.lstrip("0")           # '6:45AM'
    return f"**{t.lower()}**"   # '6:45am' bolded

class TimezonesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="time", description="Show current time in ET/CT/MT/PT (12-hour).")
    async def time(self, interaction: discord.Interaction):
        # Use ET for the date stamp line (consistent anchor)
        now_et = datetime.now(ZoneInfo("America/New_York"))

        header = f"Today is {fmt_date(now_et)} — and the current time is:"
        lines = []

        for label, tz in ZONES:
            t_local = datetime.now(ZoneInfo(tz))
            lines.append(f"• {NEUTRAL_CLOCK} {fmt_time(t_local)}  {label}")

        out = header + "\n" + "\n".join(lines)
        await interaction.response.send_message(out)

async def setup(bot: commands.Bot):
    await bot.add_cog(TimezonesCog(bot))
