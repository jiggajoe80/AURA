# main.py
import discord
from discord import app_commands
from discord.ext import tasks, commands
import os, json, random, asyncio, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# ────────────── CONFIG ──────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("Aura")

INITIAL_EXTENSIONS = [
    "cogs.admin",
    "cogs.auto_reply",
    "cogs.jokes",
    "cogs.events",
    "cogs.fortunes",
    "cogs.say",
]

DATA_DIR = Path(__file__).parent / "data"
PRESENCE_FILE = "AURA.PRESENCE.v2.json"
HOURLIES_FILE = "AURA.HOURLIES.v2.json"
JOKES_FILE = "jokes.json"

AUTOPOST_MAP_FILE = DATA_DIR / "autopost_map.json"
GUILD_FLAGS_FILE = DATA_DIR / "guild_flags.json"

LOG_CHANNEL_ID = 1427716795615285329
REMINDERS_FILE = "reminders.json"

# ────────────── HELPERS ──────────────
def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def _load_items_from_json(filename: str):
    fp = DATA_DIR / filename
    try:
        obj = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "items" in obj:
            return [it["text"] for it in obj["items"] if isinstance(it, dict) and "text" in it]
        if isinstance(obj, list):
            return [str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in obj]
    except Exception as e:
        logger.warning(f"[WARN] Failed to load {filename}: {e}")
    return []

def load_lines_or_default(file, fallback):
    lines = _load_items_from_json(file)
    if not lines:
        lines = fallback
    logger.info(f"Loaded {len(lines)} lines from {file}")
    return lines

def _split_joke(line: str) -> tuple[str, str]:
    parts = [p.strip() for p in str(line).split("||", 1)]
    return (parts[0], parts[1]) if len(parts) == 2 else (str(line).strip(), "")

def _format_joke(line: str) -> str:
    q, a = _split_joke(line)
    return f"**Q:** {q} →\n**A:** ||{a}||" if a else q

# ────────────── KEEP ALIVE ──────────────
app = Flask("")

@app.route("/")
def home(): return "Aura is awake ☘️"

@app.route("/health")
def health(): return "ok", 200

def _run():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    Thread(target=_run, daemon=True).start()

# ────────────── CORE BOT ──────────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

class AuraBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.reminders = []
        self.presence_pool = []
        self.hourly_pool = []
        self.jokes_pool = []
        self.used_presence_today = []
        self.used_hourly_today = []
        self.used_jokes_today = []
        self._hourly_flip = False
        self.last_reset_date = None
        self.last_channel_activity = {}
        self.last_hourly_post = None

        # HARD BOOT GUARANTEE
        self.autopost_enabled = False
        self.autopost_channel_id = None

        self.cooldowns = {}

    async def setup_hook(self):
        for ext in INITIAL_EXTENSIONS:
            await self.load_extension(ext)
        await self.tree.sync()

    # ───── JSON LOADERS ─────
    def reset_daily_pools(self):
        now_utc = datetime.utcnow().date()
        if self.last_reset_date != now_utc:
            self.used_presence_today = []
            self.used_hourly_today = []
            self.used_jokes_today = []

            self.presence_pool = PRESENCE_LINES.copy()
            self.hourly_pool   = HOURLY_LINES.copy()
            self.jokes_pool    = JOKES_LINES.copy()

            random.shuffle(self.presence_pool)
            random.shuffle(self.hourly_pool)
            random.shuffle(self.jokes_pool)

            self.last_reset_date = now_utc

    def get_next_presence(self) -> str:
        self.reset_daily_pools()
        choice = random.choice(self.presence_pool)
        self.used_presence_today.append(choice)
        return choice

    def get_next_hourly(self) -> str:
        self.reset_daily_pools()
        choice = random.choice(self.hourly_pool)
        self.used_hourly_today.append(choice)
        return choice

    def get_next_joke(self) -> str | None:
        self.reset_daily_pools()
        if not self.jokes_pool:
            return None
        raw = random.choice(self.jokes_pool)
        line = raw.get("text") if isinstance(raw, dict) else str(raw)
        self.used_jokes_today.append(line)
        return _format_joke(line)

    def load_reminders(self):
        if os.path.exists(REMINDERS_FILE):
            data = json.loads(open(REMINDERS_FILE, "r", encoding="utf-8").read())
            self.reminders = [
                {
                    "user_id": r["user_id"],
                    "channel_id": r["channel_id"],
                    "message": r["message"],
                    "time": datetime.fromisoformat(r["time"])
                } for r in data
            ]

bot = AuraBot()

# ────────────── JSON POOLS INIT ──────────────
PRESENCE_LINES = load_lines_or_default(
    PRESENCE_FILE,
    ["watching the quiet threads", "calm loop, steady soul", "carrying calm in a mug ☘️"]
)
HOURLY_LINES = load_lines_or_default(
    HOURLIES_FILE,
    ["🍀 Clover check-in: unclench your shoulders."]
)
JOKES_LINES = load_lines_or_default(
    JOKES_FILE,
    ["Why did the clover smile?||Because it felt lucky!"]
)

# ────────────── EVENTS ──────────────
@bot.event
async def on_ready():
    bot.load_reminders()
    bot.reset_daily_pools()

    presence_text = bot.get_next_presence()
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name=presence_text
    ))

    # DO NOT ENABLE AUTPOST ON BOOT
    bot.autopost_enabled = False
    bot.autopost_channel_id = None
    bot.last_hourly_post = None
    bot._hourly_flip = False

    if not check_hourly_post.is_running():
        check_hourly_post.start()

@tasks.loop(minutes=1)
async def check_hourly_post():
    if not bot.autopost_enabled:
        return

    now = datetime.utcnow()
    channel = bot.get_channel(bot.autopost_channel_id)
    if not channel:
        return

    last_activity = bot.last_channel_activity.get(bot.autopost_channel_id)
    inactive_seconds = (now - last_activity).total_seconds() if last_activity else float("inf")
    since_last = (now - bot.last_hourly_post).total_seconds() if bot.last_hourly_post else float("inf")

    if inactive_seconds >= 1800 and since_last >= 3600:
        bot._hourly_flip = not bot._hourly_flip
        message = bot.get_next_joke() if bot._hourly_flip else bot.get_next_hourly()
        await channel.send(message)
        bot.last_hourly_post = now

# ────────────── ENTRY ──────────────
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    keep_alive()
    bot.run(token)
