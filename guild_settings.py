"""Durable guild configuration. No legacy runtime files are consulted."""
import asyncio
import base64
import copy
import json
import logging
import math
import os
import time

import aiohttp

URL = "https://api.github.com/repos/jiggajoe80/aura-guild-settings/contents/guild-settings.json"
LOG = logging.getLogger("Aura")
DEFAULT = {
    "silent": True, "selected_channel_ids": [], "emoji_enabled": False,
    "prob_user_message": 0.15, "rate_channel_seconds": 120,
    "rate_user_seconds": 45, "react_to_bots": False,
    "say_log_channel_id": None, "log_channel_id": None,
}


class SettingsError(Exception):
    pass


def valid_id(value):
    return isinstance(value, str) and 1 <= len(value) <= 20 and not value.startswith("0") and value.isascii() and value.isdecimal() and 0 < int(value) < 2**64


def validate(doc):
    if (not isinstance(doc, dict) or set(doc) != {"schema_version", "guilds"}
            or type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or not isinstance(doc["guilds"], dict)):
        raise SettingsError("Invalid settings schema")
    for gid, row in doc["guilds"].items():
        if not valid_id(gid) or not isinstance(row, dict) or set(row) != set(DEFAULT):
            raise SettingsError("Invalid guild record")
        for key in ("silent", "emoji_enabled", "react_to_bots"):
            if type(row[key]) is not bool:
                raise SettingsError("Invalid flag")
        for key in ("rate_channel_seconds", "rate_user_seconds"):
            if type(row[key]) is not int or not 0 <= row[key] <= 86400:
                raise SettingsError("Invalid cooldown")
        p = row["prob_user_message"]
        if type(p) not in (int, float) or not 0 <= p <= 1 or not math.isfinite(p):
            raise SettingsError("Invalid probability")
        ids = row["selected_channel_ids"]
        if (not isinstance(ids, list) or not all(valid_id(x) for x in ids)
                or len(set(ids)) != len(ids)):
            raise SettingsError("Invalid selected channels")
        for key in ("say_log_channel_id", "log_channel_id"):
            if row[key] is not None and (not valid_id(row[key]) or row[key] in ids):
                raise SettingsError("Invalid audit channel")
    return doc


class Settings:
    def __init__(self):
        self.doc = self.etag = None
        self.checked = -float("inf")
        self.retry_after = 0
        self.lock = asyncio.Lock()

    def available(self):
        return self.doc is not None and time.monotonic() - self.checked < 900

    def get(self, guild_id):
        if not self.available():
            return copy.deepcopy(DEFAULT)
        return copy.deepcopy(self.doc["guilds"].get(str(guild_id), DEFAULT))

    async def request(self, method, *, etag=None, payload=None):
        token = os.getenv("AURA_SETTINGS_GITHUB_TOKEN")
        if not token:
            self.doc = self.etag = None
            raise SettingsError("Settings credential missing")
        if time.monotonic() < self.retry_after:
            raise SettingsError("Settings API retry window")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if etag:
            headers["If-None-Match"] = etag
        params = {"ref": "main"} if method == "GET" else None
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.request(method, URL, headers=headers, params=params,
                                           json=payload, allow_redirects=False) as response:
                    status = response.status
                    if status in (403, 429) and (status == 429 or response.headers.get("Retry-After")
                            or response.headers.get("X-RateLimit-Remaining") == "0"):
                        reset = int(response.headers.get("X-RateLimit-Reset") or 0)
                        wait = int(response.headers.get("Retry-After") or 60)
                        self.retry_after = time.monotonic() + max(wait, reset - time.time())
                    elif status in (401, 403, 404):
                        self.doc = self.etag = None
                    return status, response.headers.get("ETag"), await response.read()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise SettingsError("Settings API transport error") from exc

    async def read(self, *, conditional=False):
        status, etag, body = await self.request("GET", etag=self.etag if conditional else None)
        if status == 304 and self.doc is not None:
            self.checked = time.monotonic()
            return self.doc, None
        if status != 200:
            raise SettingsError(f"Settings read HTTP {status}")
        try:
            entry = json.loads(body)
            doc = validate(json.loads(base64.b64decode(entry["content"])))
            sha = entry["sha"]
            if not isinstance(sha, str) or len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
                raise ValueError("Invalid blob SHA")
        except (TypeError, ValueError, KeyError, SettingsError) as exc:
            self.doc = self.etag = None
            raise SettingsError("Invalid settings document") from exc
        self.doc, self.etag, self.checked = doc, etag, time.monotonic()
        return doc, sha

    async def refresh(self):
        async with self.lock:
            try:
                await self.read(conditional=True)
            except SettingsError as exc:
                LOG.warning("Guild settings refresh: %s", exc)

    async def update(self, guild_id, change):
        gid = str(guild_id)
        async with self.lock:
            for attempt in range(2):
                current, sha = await self.read()
                candidate = copy.deepcopy(current)
                row = candidate["guilds"].setdefault(gid, copy.deepcopy(DEFAULT))
                change(row)
                validate(candidate)
                if candidate == current:
                    return
                payload = {"branch": "main", "sha": sha, "message": "Update Aura guild settings",
                           "content": base64.b64encode(json.dumps(candidate, indent=2).encode()).decode()}
                try:
                    status, _, _ = await self.request("PUT", payload=payload)
                except SettingsError:
                    status = None
                if status in (None, 200) or (status is not None and status >= 500):
                    observed, _ = await self.read()
                    if observed["guilds"].get(gid) == row:
                        return
                    raise SettingsError("Settings write could not be confirmed")
                if status != 409 or attempt:
                    raise SettingsError(f"Settings write HTTP {status}")
                await asyncio.sleep(1)
