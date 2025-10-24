# --- add/replace this helper in cogs/events.py ---

from datetime import datetime, timezone
import pytz  # you already import this earlier for the zone conversions

CLOCK = "🕒"

def _fmt_clock(dt: datetime, tz: str) -> tuple[str, str]:
    """Return (time_str_bold, day_str_bold) for a dt localized to tz."""
    z = pytz.timezone(tz)
    local = dt.astimezone(z)
    # 9:07pm style, bold
    return f"**{local.strftime('%-I:%M%p').lower()}**", f"**{local.strftime('%A')}**"

def _fmt_remaining(now_utc: datetime, target_utc: datetime, tz: str) -> str:
    """Return italicized 'time remaining' line for tz."""
    z = pytz.timezone(tz)
    now_local = now_utc.astimezone(z)
    tgt_local = target_utc.astimezone(z)
    delta = max(tgt_local - now_local, datetime.resolution)
    hours = int(delta.total_seconds() // 3600)
    mins = int((delta.total_seconds() % 3600) // 60)
    return f"*• time remaining {hours}h {mins}m*"

def render_event_message(title: str, start_dt_utc: datetime, now_utc: datetime) -> str:
    """
    Returns a message with:
    1) headline + four timezones (EST/CST/MST/PST) with **bold** times and **bold** day
    2) 'Today is <Day> — <date>' block with current times and *italic* time-remaining
    """
    tzs = [
        ("EST", "America/New_York"),
        ("CST", "America/Chicago"),
        ("MST", "America/Denver"),
        ("PST", "America/Los_Angeles"),
    ]

    # headline with event day (bold) and date
    event_day_bold = f"**{start_dt_utc.astimezone(pytz.timezone('America/New_York')).strftime('%A')}**"
    event_date = start_dt_utc.astimezone(pytz.timezone('America/New_York')).strftime("%B, %-d, %Y")
    lines = []
    lines.append(f"**{title}** is on {event_day_bold} — {event_date} — at:")

    # event times per tz
    for label, zone in tzs:
        t_str, day_str = _fmt_clock(start_dt_utc, zone)
        lines.append(f"{CLOCK} {t_str} —{label}— {day_str}")

    lines.append("")  # blank line

    # 'Today is' block (mirror /time style)
    today_label = f"**{now_utc.astimezone(pytz.timezone('America/New_York')).strftime('%A')}**"
    today_date  = now_utc.astimezone(pytz.timezone('America/New_York')).strftime("%B, %-d, %Y")
    lines.append(f"• Today is {today_label} — {today_date} — and the current time is:")

    for label, zone in tzs:
        now_str, _ = _fmt_clock(now_utc, zone)   # bold time
        rem = _fmt_remaining(now_utc, start_dt_utc, zone)  # *italic* remaining
        lines.append(f"{CLOCK} {now_str} —{label}—  {rem}")

    return "\n".join(lines)
