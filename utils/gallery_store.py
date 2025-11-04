from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import List, Dict, Any

# Paths
GALLERY_DIR = Path("data/gallery")
GALLERY_FILE = GALLERY_DIR / "gallery.json"

# Simple in-process lock for atomic writes
_lock = threading.Lock()


def _ensure_file() -> None:
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    if not GALLERY_FILE.exists():
        GALLERY_FILE.write_text("[]", encoding="utf-8")


def read_gallery() -> List[Dict[str, Any]]:
    """
    Load the gallery file and normalize it to a list of dicts.
    Accepts:
      - bare list: [ {...}, {...} ]
      - legacy dict: { "entries": [ {...} ] }
    Returns an empty list on any parse or shape error.
    """
    _ensure_file()
    try:
        raw = GALLERY_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return []

    if isinstance(data, dict):
        data = data.get("entries", [])

    if not isinstance(data, list):
        return []

    # Filter only dict entries
    return [e for e in data if isinstance(e, dict)]


def write_gallery(entries: List[Dict[str, Any]]) -> None:
    """Atomically write a normalized list of entries."""
    _ensure_file()
    with _lock:
        tmp = GALLERY_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(GALLERY_FILE)


def merge_entries(new_entries: List[Dict[str, Any]]) -> int:
    """
    Merge in unique entries by URL, keeping existing order.
    Returns the number of new entries added.
    """
    existing = read_gallery()
    existing_urls = {e.get("url", "") for e in existing if isinstance(e, dict)}
    added = 0

    for e in new_entries:
        if not isinstance(e, dict):
            continue
        url = (e.get("url") or "").strip()
        if url and url not in existing_urls:
            existing.append(e)
            existing_urls.add(url)
            added += 1

    write_gallery(existing)
    return added
