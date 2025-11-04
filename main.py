import discord
from discord import app_commands
from discord.ext import tasks, commands
import os, json, random, asyncio, logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# ───────── CONFIG ─────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("Aura")

INITIAL_EXTENSIONS = [
    "cogs.auto_reply",
    "cogs.jokes",
    "cogs.events",
    "cogs.fortunes",
    "cogs.say",
    "cogs.timezones",
    "cogs.remind",
    "cogs.admin",
    "cogs.namegen",
    "cogs.quote",
    "cogs.polls",
    "cogs.emoji",
    "cogs.emoji_ids",
    "cogs.emoji_diag",
    "cogs.gallery",
    "cogs.gallery_diag",
]

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR / "data"
PRESENCE_FILE = "AURA.PRESENCE.v2.json"
HOURLIES_FILE = "AURA.HOURLIES.v2.json"
JOKES_FILE    = "jokes.json"

# Per-guild config files
AUTOPOST_MAP_FILE = DATA_DIR / "autopost_map.json"
GUILD_FLAGS_FILE  = DATA_DIR / "guild_flags.json"

LOG_CHANNEL_ID = 1427716795615285329
REMINDERS_FILE = DATA_DIR / "reminders.json"

# Persisted alternator state (hardens rotation across restarts)
AUTOPOST_STATE_FILE = DATA_DIR / "autopost_state.json"

def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def _save_json(p: Path, obj):
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

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

# ---- joke formatting for hourly ---------------------------------------------
def _split_joke_clean(line: str) -> tuple[str, str]:
    raw = str(line).strip()
    parts = raw.split("||", 1)
    if len(parts) == 2:
        setup = parts[0].strip().rstrip("|").rstrip()
        punch = parts[1].strip()
        while punch.endswith("||") or punch.endswith("|"):
            punch = punch[:-1].rstrip("|").rstrip()
        return setup, punch
    return raw, ""

def _format_joke(line: str) -> str:
    q, a = _split_joke_clean(line)
    return f"**Q:** {q} →\n**A:** ||{a}||" if a else q
# -----------------------------------------------------------------------------

# ───────── keepalive ─────────
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

# ───────── alternator persistence ─────────
def _load_autopost_state() -> dict:
    try:
        return json.loads(AUTOPOST_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"last_type": None}  # None → will start with "joke"

def _save_autopost_state(state: dict) -> None:
    try:
        AUTOPOST_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass

# ───────── bot ─────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

class AuraBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.reminders: list[dict] = []
        self.presence_pool = []
        self.hourly_pool = []
        self.jokes_pool = []
        self.used_presence_today = []
        self.used_hourly_today = []
        self.used_jokes_today = []
        self.last_reset_date = None
        self.last_channel_activity: dict[int, datetime] = {}  # channel_id -> last msg time
        self.last_hourly_post: dict[int, datetime] = {}       # channel_id -> last hourly time
        self.cooldowns = {}
        self._hourly_enabled = True
        # alternator state (persisted)
        self.autopost_state: dict = _load_autopost_state()

    async def setup_hook(self):
        for ext in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.exception(f"Failed to load {ext}: {e}")
        try:
            await self.tree.sync()
            logger.info("Slash commands synced.")
        except Exception as e:
            logger.exception(f"Failed to sync slash commands: {e}")

    # per-guild config
    @property
    def autopost_map(self) -> dict:
        # guild_id -> channel_id
        return _load_json(AUTOPOST_MAP_FILE, {})

    @property
    def guild_flags(self) -> dict:
        # guild_id -> { silent: bool }
        return _load_json(GUILD_FLAGS_FILE, {})

    def is_silent(self, guild_id: int) -> bool:
        return bool(self.guild_flags.get(str(guild_id), {}).get("silent", False))

    # pools
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
            logger.info(f"Daily pools reset at UTC midnight: {now_utc}")

    def get_next_presence(self) -> str:
        self.reset_daily_pools()
        available = [p for p in self.presence_pool if p not in self.used_presence_today] or self.presence_pool.copy()
        choice = random.choice(available)
        self.used_presence_today.append(choice)
        return choice

    def get_next_hourly(self) -> str:
        self.reset_daily_pools()
        available = [h for h in self.hourly_pool if h not in self.used_hourly_today] or self.hourly_pool.copy()
        choice = random.choice(available)
        self.used_hourly_today.append(choice)
        return choice

    def get_next_joke(self) -> str | None:
        self.reset_daily_pools()
        if not self.jokes_pool:
            return None
        candidates = [j for j in self.jokes_pool if j not in self.used_jokes_today] or self.jokes_pool
        raw = random.choice(candidates)
        line = raw.get("text") if isinstance(raw, dict) else str(raw)
        self.used_jokes_today.append(line)
        return _format_joke(line)

    # cooldown helper (used by /ping etc.)
    def check_cooldown(self, user_id, command_name):
        key = f"{user_id}_{command_name}"
        now = datetime.utcnow()
        if key in self.cooldowns and now < self.cooldowns[key]:
            return False, (self.cooldowns[key] - now).total_seconds()
        self.cooldowns[key] = now + timedelta(seconds=5)
        return True, 0

    # reminders save/load (UTC aware)
    def load_reminders(self):
        try:
            if os.path.exists(REMINDERS_FILE):
                data = json.loads(open(REMINDERS_FILE, "r", encoding="utf-8").read())
                self.reminders = []
                for r in data:
                    t = datetime.fromisoformat(r["time"])
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    else:
                        t = t.astimezone(timezone.utc)
                    self.reminders.append({
                        "user_id": r["user_id"],
                        "channel_id": r["channel_id"],
                        "message": r["message"],
                        "time": t,
                    })
                logger.info(f"Loaded {len(self.reminders)} reminders")
            else:
                self.reminders = []
        except Exception as e:
            logger.error(f"Error loading reminders: {e}")
            self.reminders = []

    def save_reminders(self):
        try:
            json.dump(
                [
                    {
                        "user_id": r["user_id"],
                        "channel_id": r["channel_id"],
                        "message": r["message"],
                        "time": (
                            r["time"].astimezone(timezone.utc)
                            if r["time"].tzinfo else r["time"].replace(tzinfo=timezone.utc)
                        ).isoformat(),
                    } for r in self.reminders
                ],
                open(REMINDERS_FILE, "w", encoding="utf-8"),
                indent=2,
                ensure_ascii=False
            )
        except Exception as e:
            logger.error(f"Error saving reminders: {e}")

bot = AuraBot()

# pools init
PRESENCE_LINES = load_lines_or_default(
    PRESENCE_FILE,
    ["watching the quiet threads", "calm loop, steady soul", "carrying calm in a mug ☘️"]
)
HOURLY_LINES = load_lines_or_default(
    HOURLIES_FILE,
    ["🍀 Clover check-in: unclench your shoulders.", "Cozy reminder: tiny progress counts.", "You’re allowed to ask for help."]
)
JOKES_LINES = load_lines_or_default(
    JOKES_FILE,
    ["Why did the clover smile?||Because it felt lucky!"]
)
bot.jokes_pool = JOKES_LINES.copy()

# events
@bot.event
async def on_ready():
    logger.info(f"{bot.user} has connected to Discord!")
    # diagnostic: list all guilds Aura is currently in
    logger.info(f"Guilds: {', '.join(f'{g.name} ({g.id})' for g in bot.guilds) or 'NONE'}")

    bot.load_reminders()
    bot.reset_daily_pools()

    # set initial presence
    presence_text = bot.get_next_presence()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=presence_text))

    # start tasks if not already
    if not check_reminders.is_running(): check_reminders.start()
    if not rotate_presence.is_running(): rotate_presence.start()
    if not check_hourlies.is_running():   check_hourlies.start()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    bot.last_channel_activity[message.channel.id] = datetime.utcnow()

# basic ping
@bot.tree.command(name="ping", description="Check latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! {round(bot.latency*1000)}ms")

# tasks
@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.now(timezone.utc)
    done: list[dict] = []
    for r in bot.reminders:
        try:
            when = r["time"]
            if isinstance(when, str):
                try:
                    when = datetime.fromisoformat(when)
                except Exception:
                    done.append(r); continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            else:
                when = when.astimezone(timezone.utc)
            if now < when:
                continue

            user = None
            try:
                user = await bot.fetch_user(r["user_id"])
            except Exception as e:
                logger.error(f"Reminder fetch_user fail: {e}")

            ch = bot.get_channel(r["channel_id"])
            if ch is None:
                try:
                    ch = await bot.fetch_channel(r["channel_id"])
                except Exception as e:
                    logger.error(f"Reminder fetch_channel fail: {e}")
                    ch = None

            text = f"⏰ {(user.mention if user else '')} Reminder: {r['message']}".strip()
            sent = False
            if ch and isinstance(ch, (discord.TextChannel, discord.Thread)):
                try: await ch.send(text); sent = True
                except Exception as e: logger.error(f"Reminder channel send fail: {e}")
            if not sent and user:
                try: await user.send(text); sent = True
                except Exception as e: logger.error(f"Reminder DM send fail: {e}")

            done.append(r)
        except Exception as e:
            logger.error(f"Reminder processing error: {e}")
            done.append(r)

    for r in done:
        try: bot.reminders.remove(r)
        except ValueError: pass
    if done: bot.save_reminders()

@tasks.loop(hours=1)
async def rotate_presence():
    text = bot.get_next_presence()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=text))
    logger.info(f"Presence rotated to: {text}")

@tasks.loop(minutes=1)
async def check_hourlies():
    """
    Every minute:
      - For each guild's target channel:
          * If quiet ≥ 30 min AND ≥ 60 min since last post
            → post next item based on persisted alternator:
                 joke → hourly → joke → hourly ...
      - Honors per-guild 'silent' flag.
    """
    if not bot._hourly_enabled:
        return

    now = datetime.utcnow()
    ap_map = bot.autopost_map  # guild_id -> channel_id

    for guild in bot.guilds:
        if bot.is_silent(guild.id):
            continue

        ch_id = ap_map.get(str(guild.id))
        if not ch_id:
            continue

        try:
            ch = guild.get_channel(int(ch_id)) or await bot.fetch_channel(int(ch_id))
        except Exception:
            ch = None
        if not ch or not isinstance(ch, (discord.TextChannel, discord.Thread)):
            continue

        last_activity = bot.last_channel_activity.get(ch.id)
        inactive_seconds = (now - last_activity).total_seconds() if last_activity else float("inf")
        last_hourly = bot.last_hourly_post.get(ch.id, now - timedelta(hours=2))
        since_last = (now - last_hourly).total_seconds()

        if inactive_seconds >= 1800 and since_last >= 3600:
            try:
                # persisted alternator
                last_type = (bot.autopost_state or {}).get("last_type")
                next_type = "hourly" if last_type == "joke" else "joke"

                message: str | None = None
                if next_type == "joke":
                    message = bot.get_next_joke() or bot.get_next_hourly()
                else:
                    message = bot.get_next_hourly()

                await ch.send(message)
                bot.last_hourly_post[ch.id] = now

                # save alternator state
                bot.autopost_state["last_type"] = next_type
                _save_autopost_state(bot.autopost_state)

                logger.info(f"[{guild.name}] hourly posted in #{getattr(ch, 'name', ch.id)} ({next_type})")
            except Exception as e:
                logger.error(f"Hourly post error in {guild.name}: {e}")

# entry
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("No DISCORD_TOKEN in environment!")
        print("ERROR: Missing DISCORD_TOKEN in .env")
    else:
        keep_alive()
        bot.run(token)
