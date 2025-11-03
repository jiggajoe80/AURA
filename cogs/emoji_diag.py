# cogs/emoji_diag.py
import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "emoji"
POOLS_DIR = DATA_DIR / "pools"
CONFIG_PATH = DATA_DIR / "config.json"

def _is_custom(emoji_str: str) -> bool:
    # Custom static: <:name:id>, animated: <a:name:id>
    return emoji_str.startswith("<:" ) or emoji_str.startswith("<a:")

def _sample_mixed(pool: List[str], max_custom: int = 2, max_unicode: int = 2) -> List[str]:
    """Return up to 2 custom + up to 2 unicode (shuffled)."""
    customs  = [e for e in pool if _is_custom(e)]
    unicodes = [e for e in pool if not _is_custom(e)]
    picks: List[str] = []
    if customs:
        picks += random.sample(customs, min(max_custom, len(customs)))
    if unicodes:
        picks += random.sample(unicodes, min(max_unicode, len(unicodes)))
    random.shuffle(picks)
    return picks

def _slug_from_name(name: str) -> str:
    # basic slug that matches Pool.AURA_STARTER_2.json style
    keep = []
    for ch in name.upper().replace(" ", "_"):
        if ch.isalnum() or ch == "_":
            keep.append(ch)
    return "".join(keep)

def _resolve_pool_file_for_guild(g: discord.Guild) -> Optional[Path]:
    """Best-effort: prefer files that start with guild_id, else Pool.<SLUG>.json"""
    gid = str(g.id)
    id_matches = sorted(POOLS_DIR.glob(f"{gid}*.json"))
    if id_matches:
        return id_matches[0]
    alt = POOLS_DIR / f"Pool.{_slug_from_name(g.name)}.json"
    if alt.exists():
        return alt
    return None

def _load_config() -> Dict[str, dict]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _enabled_from_config(cfg: Dict[str, dict], guild_id: int) -> Optional[bool]:
    entry = cfg.get(str(guild_id))
    if isinstance(entry, dict) and "enabled" in entry:
        return bool(entry["enabled"])
    return None

class EmojiDiag(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    group = app_commands.Group(name="emoji_diag", description="Emoji diagnostic tools")

    # ---------- helpers to talk to the Emoji cog (if available) ----------
    def _fetch_state_from_cog(self, guild_id: int) -> Optional[dict]:
        """
        Ask the Emoji cog for its in-memory view.
        Tries a few method names to stay compatible with your current file.
        Expected shape:
        {
          "enabled": bool,
          "pool_file": "filename.json",
          "buckets": {"autopost": [...], "user_message": [...], "event_soon": [...]}
        }
        """
        cog = self.bot.get_cog("Emoji")
        if not cog:
            return None
        candidates = ("peek_state", "debug_state", "peek_guild", "state_for_guild")
        for name in candidates:
            fn = getattr(cog, name, None)
            if callable(fn):
                try:
                    return fn(guild_id)
                except Exception:
                    continue
        return None

    def _hydrate_state_fallback(self, guild: discord.Guild) -> Optional[dict]:
        """Fallback if the Emoji cog doesn't expose a state accessor."""
        pool_path = _resolve_pool_file_for_guild(guild)
        if not pool_path:
            return None
        try:
            pools = json.loads(pool_path.read_text(encoding="utf-8"))
            # normalize expected buckets
            buckets = {
                "autopost": pools.get("autopost", []) or [],
                "user_message": pools.get("user_message", []) or [],
                "event_soon": pools.get("event_soon", []) or [],
            }
            cfg = _load_config()
            enabled = _enabled_from_config(cfg, guild.id)
            return {
                "enabled": bool(enabled) if enabled is not None else False,
                "pool_file": pool_path.name,
                "buckets": buckets,
            }
        except Exception:
            return None

    def _guild_state(self, guild: discord.Guild) -> Tuple[Optional[dict], Optional[str]]:
        """
        Returns (state, error_message). One of them will be None.
        """
        if not guild:
            return None, "No guild context."
        state = self._fetch_state_from_cog(guild.id)
        if state:
            return state, None
        # fallback to disk
        state = self._hydrate_state_fallback(guild)
        if state:
            return state, None
        return None, "No emoji config for this guild."

    # ---------------- commands ----------------
    @group.command(name="peek", description="Show current emoji status and a small mixed sample per bucket.")
    async def peek(self, inter: discord.Interaction):
        state, err = self._guild_state(inter.guild)
        if err:
            return await inter.response.send_message(err, ephemeral=True)

        enabled = state.get("enabled", False)
        pool_file = state.get("pool_file", "unknown")
        buckets: Dict[str, List[str]] = state.get("buckets", {})

        # Build embed
        emb = discord.Embed(
            title="Emoji config",
            color=discord.Color.blurple()
        )
        emb.add_field(
            name="Guild",
            value=f"{inter.guild.name} (`{inter.guild.id}`)",
            inline=False
        )
        emb.add_field(name="Enabled", value=str(bool(enabled)), inline=False)
        emb.add_field(name="Pool file", value=f"`{pool_file}`", inline=False)

        # Per-bucket totals + mixed sample
        lines = []
        for bucket_name in ("autopost", "user_message", "event_soon"):
            pool = buckets.get(bucket_name, []) or []
            total = len(pool)
            if total == 0:
                lines.append(f"• **{bucket_name}**: 0 total | sample → *(none usable)*")
                continue
            sample = _sample_mixed(pool, max_custom=2, max_unicode=2)
            sample_text = " ".join(sample) if sample else "—"
            lines.append(f"• **{bucket_name}**: {total} total | sample → {sample_text}")
        emb.add_field(name="Buckets:", value="\n".join(lines), inline=False)

        await inter.response.send_message(embed=emb, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(EmojiDiag(bot))
