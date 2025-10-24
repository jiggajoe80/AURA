# cogs/events.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from zoneinfo import ZoneInfo


DATA_FILE = Path("data/events.json")

# Four core US timezones we’ll display
TZS: Dict[str, ZoneInfo] = {
    "EST": ZoneInfo("America/New_York"),
    "CST": ZoneInfo("America/Chicago"),
    "MST": ZoneInfo("America/Denver"),
    "PST": ZoneInfo("America/Los_Angeles"),
}


@dataclass
class EventConfig:
    title: str
    iso: str  # ISO-8601 string (may include offset, e.g. 2025-10-24T20:00:00-04:00)


def _load_first_event() -> EventConfig | None:
    """
    events.json can be either:
      • {"My Event Title": "2025-10-24T20:00:00-04:00"}
      • [{"title": "My Event Title", "start_at": "2025-10-24T20:00:00-04:00"}, ...]
    We grab the first entry and ignore the rest for the simple /event command.
    """
    if not DATA_FILE.exists():
        return None

    with DATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # dict schema
    if isinstance(data, dict):
        if not data:
            return None
        title, iso = next(iter(data.items()))
        return EventConfig(title=title, iso=iso)

    # list schema
    if isinstance(data, list) and data:
        first = data[0]
        title = first.get("title") or first.get("name") or "Untitled Event"
        iso = first.get("start_at") or first.get("start") or ""
        if iso:
            return EventConfig(title=title, iso=iso)

    return None


def _fmt_clock(dt: datetime, label: str, with_weekday: bool = False) -> str:
    """12h clock like '8:00pm —EST— Friday' (weekday optional)."""
    # %-I is not portable on Windows; use %#I for Windows. Render runs Linux, but we’ll be safe.
    hour_fmt = "%-I:%M%p" if hasattr(dt, "strftime") else "%I:%M%p"
    try:
        time_txt = dt.strftime(hour_fmt)  # Linux
    except ValueError:
        time_txt = dt.strftime("%#I:%M%p")  # Windows fallback

    time_txt = time_txt.lower()
    if with_weekday:
        return f"🕒 {time_txt} —{label}— {dt.strftime('%A')}"
    return f"🕒 {time_txt} —{label}—"


def _fmt_remaining(event_local: datetime, now_local: datetime) -> str:
    """'time remaining 21h 15m' or 'started 2h 3m ago'."""
    delta = event_local - now_local
    secs = int(delta.total_seconds())
    sign = "" if secs >= 0 else "started "
    secs = abs(secs)
    hours, rem = divmod(secs, 3600)
    mins, _ = divmod(rem, 60)
    if sign == "":
        return f"• time remaining {hours}h {mins}m"
    return f"• started {hours}h {mins}m ago"


class Events(commands.Cog):
    """Simple /event readout using the first entry in data/events.json."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="event", description="Show the next configured event with local times.")
    async def event(self, interaction: discord.Interaction) -> None:
        cfg = _load_first_event()
        if not cfg or not cfg.iso:
            await interaction.response.send_message(
                "No event configured yet. Add one to `data/events.json`.", ephemeral=True
            )
            return

        # Parse the ISO string. If no tz, assume UTC.
        try:
            event_dt = datetime.fromisoformat(cfg.iso)
        except ValueError:
            await interaction.response.send_message(
                "Event time in `events.json` is not valid ISO-8601.", ephemeral=True
            )
            return

        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)

        # Build the header date in the event’s own timezone (its offset/zone)
        header_date = event_dt.strftime("%A — %B, %d, %Y")

        lines: list[str] = []
        lines.append(f"**{cfg.title}** is on **{event_dt.strftime('%A')}** — {event_dt.strftime('%B, %d, %Y')} — at:")

        # Show per-zone time with weekday label after each line
        for label, zone in TZS.items():
            local = event_dt.astimezone(zone)
            lines.append(_fmt_clock(local, label, with_weekday=True))

        # Current time section + remaining per zone
        lines.append("")  # spacer
        now_utc = datetime.now(timezone.utc)
        today_line = f"• Today is **{now_utc.astimezone(TZS['EST']).strftime('%A')}** — {now_utc.astimezone(TZS['EST']).strftime('%B, %d, %Y')} — and the current time is:"
        lines.append(today_line)

        for label, zone in TZS.items():
            now_local = now_utc.astimezone(zone)
            event_local = event_dt.astimezone(zone)
            clock = _fmt_clock(now_local, label, with_weekday=False)
            rem = _fmt_remaining(event_local, now_local)
            lines.append(f"{clock}  {rem}")

        await interaction.response.send_message("\n".join(lines))

    # Optional admin helper to reload without redeploy (kept simple)
    @app_commands.command(name="event_status", description="Admin: show which event is configured.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def event_status(self, interaction: discord.Interaction) -> None:
        cfg = _load_first_event()
        if not cfg:
            await interaction.response.send_message("No event configured.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Loaded event: **{cfg.title}** → `{cfg.iso}` from `data/events.json`.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
