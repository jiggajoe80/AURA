# cogs/events.py
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
import discord
from discord import app_commands
from discord.ext import commands
from zoneinfo import ZoneInfo

US_ZONES = [
    ("EST", "America/New_York"),
    ("CST", "America/Chicago"),
    ("MST", "America/Denver"),
    ("PST", "America/Los_Angeles"),
]

def _safe_read_events() -> dict[str, str]:
    p = Path("data/events.json")
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _human_day(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%A")  # e.g. Friday

def _fmt_time(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%-I:%M%p").lower()  # 8:00pm

def _fmt_date(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%B, %-d, %Y")  # October, 24, 2025

def _fmt_remaining(now_utc: datetime, target_utc: datetime, tz: ZoneInfo) -> str:
    # clamp negative to 0
    delta = target_utc - now_utc
    if delta.total_seconds() < 0:
        return "*started already*"
    hours = int(delta.total_seconds() // 3600)
    mins = int((delta.total_seconds() % 3600) // 60)
    return f"*time remaining {hours}h {mins}m*"

def render_event_message(title: str, start_dt_utc: datetime, now_utc: datetime) -> str:
    # Header (EST for the date line)
    est = ZoneInfo("America/New_York")
    pretty_date = _fmt_date(start_dt_utc, est)
    header_day = _human_day(start_dt_utc, est)
    lines = []
    lines.append(f"**{title}** is on **{header_day}** — {pretty_date} — at:")

    # Times per zone (bold time + day)
    for label, zone in US_ZONES:
        tz = ZoneInfo(zone)
        t = _fmt_time(start_dt_utc, tz)
        d = _human_day(start_dt_utc, tz)
        lines.append(f"• 🕒 **{t}** —**{label}**— **{d}**")

    # Now block + remaining (italic)
    now_day = _human_day(now_utc, est)
    now_date = _fmt_date(now_utc, est)
    lines.append("")
    lines.append(f"• Today is **{now_day}** — {now_date} — and the current time is:")

    for label, zone in US_ZONES:
        tz = ZoneInfo(zone)
        now_t = _fmt_time(now_utc, tz)
        rem = _fmt_remaining(now_utc, start_dt_utc, tz)
        lines.append(f"• 🕒 **{now_t}** —**{label}**—  • {rem}")

    return "\n".join(lines)

class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_next_event(self) -> tuple[str, datetime] | None:
        """
        data/events.json:
        {
          "Fight Night on Dallas PC": "2025-10-24T20:00:00-04:00",
          "Another Title": "2025-11-02T19:30:00-05:00"
        }
        The first entry is treated as “next”.
        """
        data = _safe_read_events()
        if not data:
            return None
        title, iso_str = next(iter(data.items()))
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_utc = dt.astimezone(timezone.utc)
            return (title, dt_utc)
        except Exception:
            return None

    @app_commands.command(name="event", description="Show the next event time in US zones.")
    async def event(self, interaction: discord.Interaction):
        got = self._get_next_event()
        if not got:
            await interaction.response.send_message("No event found in data/events.json.", ephemeral=True)
            return
        title, start_dt_utc = got
        message = render_event_message(title, start_dt_utc, datetime.now(timezone.utc))
        await interaction.response.send_message(message)

async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
