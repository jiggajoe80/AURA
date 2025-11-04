import json
from datetime import datetime
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands

GALLERY_PATH = Path("data/gallery/gallery.json")

def _ensure_gallery_file():
    GALLERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not GALLERY_PATH.exists():
        GALLERY_PATH.write_text(json.dumps({"entries": []}, indent=2), encoding="utf-8")

def _load_gallery():
    _ensure_gallery_file()
    try:
        return json.loads(GALLERY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": []}

def _save_gallery(data: dict):
    GALLERY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

class GallerySeed(commands.Cog):
    """Manually seed gallery entries."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="gallery_seed", description="Add a single gallery entry manually.")
    @app_commands.describe(
        url="Direct media URL (image/gif/mp4).",
        kind="Type of media: image, gif, or video.",
        tag="Optional tag or category."
    )
    async def gallery_seed(self, interaction: discord.Interaction, url: str, kind: str, tag: str = None):
        data = _load_gallery()
        entries = data.get("entries", [])

        # dedupe
        if any(e.get("url") == url for e in entries if isinstance(e, dict)):
            await interaction.response.send_message("⚠️ That URL already exists in the gallery.", ephemeral=True)
            return

        new_entry = {
            "url": url,
            "kind": kind.lower(),
            "ext": url.split(".")[-1].lower(),
            "filename": url.split("/")[-1],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "author": str(interaction.user),
            "tags": [tag] if tag else []
        }

        entries.append(new_entry)
        data["entries"] = entries
        _save_gallery(data)

        await interaction.response.send_message(
            f"✅ Added `{new_entry['filename']}` as `{new_entry['kind']}` with tag `{tag or 'none'}`.",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(GallerySeed(bot))
