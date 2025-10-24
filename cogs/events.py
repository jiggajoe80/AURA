# cogs/events.py
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands
from discord.ext import commands

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EVENTS_FILE = DATA_DIR / "events.json"

TZ_MAP = {
    "ET": timezone(timedelta(hours=-4)),   # naive handling; Render runs UTC; adjust if DST concerns
    "CT": timezone(timedelta(hours=-5)),
    "MT": timezone(timedelta(hours=-6)),
    "PT": timezone(timedelta(hours=-7)),
}

CLOCK = "🕐"  # neutral per your preference

def _load_events():
    if EVENTS_FILE.exists():
        try:
            with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_events(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def _et_naive_to_all(dt_et: datetime):
    # dt_et is timezone-aware ET; compute others
    results = {}
    for label, tz in TZ_MAP.items():
        results[label] = dt_et.astimezone(tz)
    return results

def _fmt_day_stamp(dt_et: datetime):
    # Example: Today is **Wednesday** — October, 22, 2025 —
    weekday = dt_et.strftime("%A")
    return f"Today is **{weekday}** — {dt_et.strftime('%B, %d, %Y')} —"

def _fmt_event_stamp(title: str, dt_et: datetime):
    weekday = dt_et.strftime("%A")
    return f"{title} is on **{weekday}** — {dt_et.strftime('%B, %d, %Y')} — at:"

def _fmt_lines_for(dt_map: dict, now_map: dict):
    lines = []
    for label in ("ET", "CT", "MT", "PT"):
        d = dt_map[label]
        n = now_map[label]
        diff = d - n
        if diff.total_seconds() >= 0:
            # time remaining
            days = diff.days
            secs = diff.seconds
            hrs = secs // 3600
            mins = (secs % 3600) // 60
            remain = []
            if days: remain.append(f"{days}d")
            if hrs:  remain.append(f"{hrs}h")
            if mins: remain.append(f"{mins}m")
            remain_s = " ".join(remain) or "0m"
            lines.append(f"• {CLOCK} **{d.strftime('%-I:%M%p').lower()}**  —{label}—  Time remaining {remain_s}")
        else:
            # already happened
            diff = -diff
            days = diff.days
            secs = diff.seconds
            hrs = secs // 3600
            mins = (secs % 3600) // 60
            ago = []
            if days: ago.append(f"{days}d")
            if hrs:  ago.append(f"{hrs}h")
            if mins: ago.append(f"{mins}m")
            ago_s = " ".join(ago) or "0m"
            lines.append(f"• {CLOCK} **{d.strftime('%-I:%M%p').lower()}**  —{label}—  already happened (+{ago_s})")
    return "\n".join(lines)

class EventGroup(app_commands.Group):
    def __init__(self, bot: commands.Bot):
        super().__init__(name="event", description="Create, save, and show events")
        self.bot = bot

    @app_commands.command(name="when", description="Show times for an event across US time zones.")
    @app_commands.describe(
        title="Event title",
        date="YYYY-MM-DD (interpreted in ET)",
        time_et="hh:mm am/pm (ET)",
        tz="Input timezone (ET/CT/MT/PT). Default ET"
    )
    async def when(
        self,
        interaction: discord.Interaction,
        title: str,
        date: str,
        time_et: str,
        tz: str = "ET"
    ):
        # parse ET timestamp
        tz = tz.upper()
        base_tz = TZ_MAP.get(tz, TZ_MAP["ET"])
        try:
            dt_naive = datetime.strptime(f"{date} {time_et}", "%Y-%m-%d %I:%M %p")
        except ValueError:
            await interaction.response.send_message("Use `YYYY-MM-DD` and `hh:mm am/pm`.", ephemeral=True)
            return
        dt_input = dt_naive.replace(tzinfo=base_tz)
        # normalize as ET (spec asks to treat date/time as ET for final display)
        dt_et = dt_input.astimezone(TZ_MAP["ET"])

        # now stamps
        now_map = {k: datetime.now(tz=v) for k, v in TZ_MAP.items()}
        dt_map = _et_naive_to_all(dt_et)

        # Build message
        head_event = _fmt_event_stamp(title, dt_et)
        head_now = _fmt_day_stamp(now_map["ET"])
        lines_event = "\n" + _fmt_lines_for(dt_map, now_map)
        lines_now = "\n" + _fmt_lines_for(now_map, now_map)

        msg = f"{head_event}\n{lines_event}\n\n{head_now}\n{lines_now}"
        await interaction.response.send_message(msg)

    @app_commands.command(name="save", description="(Admin) Save an event name → timestamp (ET).")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def save(
        self,
        interaction: discord.Interaction,
        title: str,
        date: str,
        time_et: str
    ):
        try:
            dt_naive = datetime.strptime(f"{date} {time_et}", "%Y-%m-%d %I:%M %p")
        except ValueError:
            await interaction.response.send_message("Use `YYYY-MM-DD` and `hh:mm am/pm`.", ephemeral=True)
            return
        dt_et = dt_naive.replace(tzinfo=TZ_MAP["ET"]).isoformat()
        data = _load_events()
        data[title] = dt_et
        _save_events(data)
        await interaction.response.send_message(f"Saved **{title}** → {date} {time_et} ET.", ephemeral=True)

    @app_commands.command(name="show", description="Show a saved event by title.")
    async def show(self, interaction: discord.Interaction, title: str):
        data = _load_events()
        iso = data.get(title)
        if not iso:
            await interaction.response.send_message("Not found.", ephemeral=True)
            return
        dt_et = datetime.fromisoformat(iso)
        now_map = {k: datetime.now(tz=v) for k, v in TZ_MAP.items()}
        dt_map = _et_naive_to_all(dt_et)

        head_event = _fmt_event_stamp(title, dt_et)
        head_now = _fmt_day_stamp(now_map["ET"])
        lines_event = "\n" + _fmt_lines_for(dt_map, now_map)
        lines_now = "\n" + _fmt_lines_for(now_map, now_map)
        msg = f"{head_event}\n{lines_event}\n\n{head_now}\n{lines_now}"
        await interaction.response.send_message(msg)

class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.group = EventGroup(bot)
        self.bot.tree.add_command(self.group)

async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
