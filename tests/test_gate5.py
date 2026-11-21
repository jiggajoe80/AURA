import asyncio
import base64
import copy
import json
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import main
from cogs.emoji import EmojiCog
from cogs.say import Say
from cogs.quote import _load_quotes
from guild_settings import DEFAULT, Settings, SettingsError, validate


def doc(**rows):
    return {"schema_version": 1, "guilds": {key: {**copy.deepcopy(DEFAULT), **val} for key, val in rows.items()}}


def file_response(value):
    return 200, "etag", json.dumps({"sha": "1"*40,
        "content": base64.b64encode(json.dumps(value).encode()).decode()}).encode()


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_expiry_isolation(self):
        store = Settings()
        self.assertTrue(store.get(1)["silent"])
        store.request = AsyncMock(return_value=file_response(doc(**{"1":{"silent":False}})))
        await store.refresh()
        self.assertFalse(store.get(1)["silent"])
        self.assertTrue(store.get(2)["silent"])
        store.get(1)["selected_channel_ids"].append("10")
        self.assertEqual(store.get(1)["selected_channel_ids"], [])
        store.checked -= 901
        self.assertTrue(store.get(1)["silent"])

    async def test_invalid_document_fails_closed(self):
        store = Settings()
        store.request = AsyncMock(side_effect=[file_response(doc(**{"1":{"silent":False}})),
                                                 file_response(doc(**{"1":{"silent":"false"}}))])
        await store.refresh()
        await store.refresh()
        self.assertTrue(store.get(1)["silent"])

    async def test_conflict_preserves_another_guild(self):
        store = Settings()
        original = doc(**{"1":{}, "2":{}})
        concurrent = doc(**{"1":{}, "2":{"emoji_enabled":True}})
        final = copy.deepcopy(concurrent)
        final["guilds"]["1"]["silent"] = False
        store.request = AsyncMock(side_effect=[file_response(original), (409,None,b""),
            file_response(concurrent), (200,None,b""), file_response(final)])
        with patch("guild_settings.asyncio.sleep", new=AsyncMock()):
            await store.update(1, lambda row: row.update(silent=False))
        payload = store.request.await_args_list[3].kwargs["payload"]
        self.assertEqual(json.loads(base64.b64decode(payload["content"])), final)

    async def test_timeout_readback(self):
        store = Settings()
        final = doc(**{"1":{"silent":False}})
        store.request = AsyncMock(side_effect=[file_response(doc(**{"1":{}})),
            SettingsError("timeout"), file_response(final)])
        await store.update(1, lambda row: row.update(silent=False))
        self.assertFalse(store.get(1)["silent"])

    async def test_conflicting_fields_rejected(self):
        with self.assertRaises(SettingsError):
            validate(doc(**{"1":{"selected_channel_ids":["2"],"say_log_channel_id":"2"}}))
        with self.assertRaises(SettingsError):
            validate(doc(**{"1":{"selected_channel_ids":["2","2"]}}))

    async def test_conditional_cache_and_process_recovery(self):
        store = Settings()
        seed = doc(**{"1":{"silent":False}})
        store.request = AsyncMock(side_effect=[file_response(seed), (304,None,b""),
                                                SettingsError("offline")])
        await store.refresh()
        await store.refresh()
        self.assertEqual(store.request.await_args.kwargs["etag"], "etag")
        await store.refresh()
        self.assertFalse(store.get(1)["silent"])
        restarted = Settings()
        self.assertTrue(restarted.get(1)["silent"])
        restarted.request = AsyncMock(return_value=file_response(seed))
        await restarted.refresh()
        self.assertFalse(restarted.get(1)["silent"])


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_and_runtime_checks(self):
        with patch.dict(os.environ, {"AURA_SETTINGS_GITHUB_TOKEN":""}):
            await main.bot.setup_hook()
        try:
            self.assertEqual(len(main.bot.cogs), 15)
            tree = main.bot.tree
            for name in ("emoji_diag_peek","emoji","quote","poll","admin_autopost_remove"):
                self.assertIsNotNone(tree.get_command(name))
            for name in ("admin_autopost_set","admin_autopost_remove","admin_autopost_clear","admin_emoji","admin_say_audit","admin_silent"):
                cmd = tree.get_command(name)
                self.assertTrue(cmd.guild_only)
                with self.assertRaises(discord.app_commands.MissingPermissions):
                    await cmd.checks[0](SimpleNamespace(permissions=discord.Permissions.none()))
            capture = tree.get_command("emoji").get_command("capture_all")
            with self.assertRaises(discord.app_commands.MissingPermissions):
                await capture.checks[0](SimpleNamespace(permissions=discord.Permissions.none()))
        finally:
            main.settings_refresh.cancel()

    async def test_reconnect_preserves_clocks(self):
        bot = SimpleNamespace(presence_pool=["hello"], change_presence=AsyncMock(), tree=SimpleNamespace(
            sync=AsyncMock(return_value=[])), booted_at=None, rotation_index=3, last_post_per_channel={8:1})
        loop = Mock()
        loop.is_running.return_value = True
        with patch.object(main,"bot",bot), patch.object(main,"autopost_loop",loop):
            await main.on_ready()
            await main.on_ready()
        self.assertEqual(bot.rotation_index,3)
        self.assertEqual(bot.last_post_per_channel,{8:1})
        bot.tree.sync.assert_awaited_once()

    async def test_scheduler_cold_start_then_send(self):
        guild=SimpleNamespace(id=1)
        channel=Mock(spec=discord.TextChannel)
        channel.guild=guild
        channel.send=AsyncMock()
        bot=SimpleNamespace(scheduled_posts_after=datetime.utcnow()+timedelta(minutes=5),
            guilds=[guild], get_cog=lambda _:SimpleNamespace(get_random_joke=lambda:"joke"),
            settings=SimpleNamespace(get=lambda _:{**DEFAULT,"silent":False,"selected_channel_ids":["9"]}),
            get_channel=lambda _:channel, last_channel_activity={},last_post_per_channel={},rotation_index=0)
        with patch.object(main,"bot",bot):
            await main.autopost_loop.coro()
            channel.send.assert_not_awaited()
            bot.scheduled_posts_after-=timedelta(minutes=10)
            await main.autopost_loop.coro()
        channel.send.assert_awaited_once_with("joke")
        self.assertIn(9,bot.last_post_per_channel)

    async def test_emoji_selection_and_cross_guild_cooldown(self):
        cog=EmojiCog(Mock())
        channel=Mock(spec=discord.TextChannel)
        channel.id=4
        first=SimpleNamespace(guild=SimpleNamespace(id=1),channel=channel,author=SimpleNamespace(id=10))
        other=SimpleNamespace(guild=SimpleNamespace(id=2),channel=SimpleNamespace(id=5),author=first.author)
        cfg={**DEFAULT,"selected_channel_ids":["4"]}
        self.assertTrue(cog._channel_allowed(first,cfg))
        self.assertFalse(cog._channel_allowed(first,DEFAULT))
        cog._mark_cooldowns(first,cfg)
        self.assertFalse(cog._cooldowns_ok(first,cfg))
        self.assertTrue(cog._cooldowns_ok(other,cfg))

    async def test_say_audit_first_and_failure(self):
        guild=SimpleNamespace(id=1)
        log=Mock(spec=discord.TextChannel)
        target=Mock(spec=discord.TextChannel)
        log.guild=target.guild=guild
        log.id,target.id=20,21
        target.mention="#test"
        audit=SimpleNamespace(edit=AsyncMock(side_effect=TimeoutError))
        order=[]
        async def public(*args,**kwargs):
            order.append("public")
            return SimpleNamespace(id=22)
        async def pending(*args,**kwargs):
            order.append("pending")
            return audit
        log.send=AsyncMock(side_effect=pending)
        target.send=AsyncMock(side_effect=public)
        bot=SimpleNamespace(settings=SimpleNamespace(get=lambda _:{**DEFAULT,"say_log_channel_id":"20"}),get_channel=lambda _:log)
        itx=SimpleNamespace(guild_id=1,channel=target,user=SimpleNamespace(mention="@member"),
                            response=SimpleNamespace(defer=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        cog=Say(bot)
        await cog.say.callback(cog,itx,"test")
        self.assertEqual(order,["pending","public"])
        self.assertIn("Do not resend",itx.followup.send.await_args.args[0])
        log.send.side_effect=TimeoutError
        target.send.reset_mock()
        await cog.say.callback(cog,itx,"test")
        target.send.assert_not_awaited()

    async def test_quote_fallback(self):
        with patch("cogs.quote.DATA_FILE",SimpleNamespace(read_text=lambda **_:"[]")):
            self.assertTrue(_load_quotes())
