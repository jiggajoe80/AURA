import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
EVENTS_FILE = DATA_DIR / "events.json"

CLOCK = "🕒"

# Labels per your spec (fixed EST/CST/MST/PST style tags)
ZONE_ORDER = [
    ("ET", ZoneInfo("America/New_York"), "EST"),
    ("CT", ZoneInfo("America/Chicago"), "CST"),
    ("MT", ZoneInfo("America/Denver"), "MST"),
    ("PT", ZoneInfo("America/Los_Angeles"), "PST"),
]

def _load_events() -> dict[str, str]:
    try:
        with open(EVENTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Expecting {"Title": "2025-10-24T20:00:00-04:00", ...}
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}

def _pick_active_event(evmap: dict[str, str]) -> tuple[str, datetime] | None:
    """Return (title, dt_aware) for the next upcoming event, else latest past."""
    now = datetime.now(timezone.utc)
    parsed: list[tuple[str, datetime]] = []
    for title, iso in evmap.items():
        try:
            dt = datetime.fromisoformat(iso)  # respects the offset in the string
            if dt.tzinfo is None:
                # If someone forgot the offset, assume ET
                dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
            parsed.append((title, dt))
        except Exception:
            continue

    if not parsed:
        return None

    future = [(t, d) for (t, d) in parsed if d.astimezone(timezone.utc) >= now]
    if future:
        # choose the soonest upcoming
        title, dt = min(future, key=lambda td: td[1])
        return title, dt
    # else choose the most recent past
    title, dt = max(parsed, key=lambda td: td[1])
    return title, dt

def _fmt_date_line(dt_et: datetime) -> str:
    # dt_et is ET-aware here
    day = dt_et.strftime("%A")
    month = dt_et.strftime("%B")
    date_num = dt_et.strftime("%d").lstrip("0")
    year = dt_et.strftime("%Y")
    return f"Today is **{day}** — {month}, {date_num}, {year} — and the current time is:"

def _fmt_time(dt: datetime) -> str:
    # "9:10pm" style, no leading zero, lower-case am/pm
    s = dt.strftime("%I:%M%p").lower()
    return f"**{s.lstrip('0')}**"

def _human_delta(delta: timedelta) -> str:
    secs = int(abs(delta).total_seconds())
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if mins or not parts: parts.append(f"{mins}m")
    return " ".join(parts)

class EventCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="event", description="Show the configured event time across ET/CT/MT/PT + countdown.")
    async def event(self, interaction: discord.Interaction):
        evmap = _load_events()
        picked = _pick_active_event(evmap)
        if not picked:
            await interaction.response.send_message(
                "No events are configured yet. Add one in `data/events.json`.",
                ephemeral=True
            )
            return

        title, dt_event = picked

        # Normalize event dt to ET for the header line
        dt_et = dt_event.astimezone(ZoneInfo("America/New_York"))

        # Build the EVENT stanza
        lines: list[str] = []
        lines.append(f"**{title}** is on **{dt_et.strftime('%A')}** — {dt_et.strftime('%B')}, {dt_et.strftime('%d').lstrip('0')}, {dt_et.strftime('%Y')} — at:")
        for _, tz, label in ZONE_ORDER:
            t_local = dt_event.astimezone(tz)
            lines.append(f"{CLOCK} {_fmt_time(t_local)} —{label}—")

        # Build the CURRENT TIME + countdown stanza
        now_utc = datetime.now(timezone.utc)
        lines.append("")  # blank line
        lines.append(_fmt_date_line(now_utc.astimezone(ZoneInfo("America/New_York"))))

        for _, tz, label in ZONE_ORDER:
            now_local = now_utc.astimezone(tz)
            evt_local = dt_event.astimezone(tz)
            delta = evt_local - now_local
            if delta.total_seconds() >= 0:
                remain = _human_delta(delta)
                lines.append(f"{CLOCK} {_fmt_time(now_local)} —{label}—  • time remaining {remain}")
            else:
                ago = _human_delta(delta)
                lines.append(f"{CLOCK} {_fmt_time(now_local)} —{label}—  • already happened ({ago} ago)")

        await interaction.response.send_message("\n".join(f"• {ln}" if ln.startswith(CLOCK) is False and ln and not ln.startswith("**") else ln for ln in lines))

async def setup(bot: commands.Bot):
    await bot.add_cog(EventCog(bot))
