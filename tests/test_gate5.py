import asyncio
import base64
import copy
import json
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import discord
import main
from cogs.emoji import EmojiCog
from cogs.admin import AdminCog
from cogs.say import DeliveryRejected, Say, send_public_once
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
            public = {"event", "flip", "fortune", "joke", "poll", "profile", "quote", "say", "time"}
            private = {
                "admin", "admin_status", "admin_silent", "admin_autopost_set",
                "admin_autopost_remove", "admin_autopost_remove_id", "admin_autopost_clear",
                "admin_emoji", "admin_say_audit", "archiveforward", "emoji",
                "emoji_diag_peek", "emoji_diag_bucket", "fortune_status",
            }
            commands = {command.name: command for command in tree.get_commands()}
            self.assertEqual(set(commands), public | private)
            self.assertIn("cogs.archive_forward", main.bot.extensions)
            for name in public:
                self.assertIsNone(commands[name].to_dict(tree)["default_member_permissions"])
                self.assertFalse(commands[name].checks)
            for name in private:
                command = commands[name]
                self.assertTrue(command.guild_only)
                self.assertEqual(command.to_dict(tree)["default_member_permissions"],
                                 discord.Permissions(administrator=True).value)

            emoji = commands["emoji"]
            children = {command.name: command for command in emoji.commands}
            self.assertEqual(set(children), {"ids", "capture_all", "capture_filter"})
            member = SimpleNamespace(permissions=discord.Permissions.none())
            manager = SimpleNamespace(permissions=discord.Permissions(manage_guild=True))
            administrator = SimpleNamespace(permissions=discord.Permissions(administrator=True))
            for command in [commands[name] for name in private - {"emoji"}] + list(children.values()):
                with self.subTest(command=command.qualified_name):
                    for interaction in (member, manager):
                        with self.assertRaises(discord.app_commands.MissingPermissions):
                            await command._check_can_run(interaction)
                    self.assertTrue(await command._check_can_run(administrator))
            for name in ("admin_autopost_set", "admin_autopost_remove"):
                self.assertEqual(tree.get_command(name).parameters[0].type, discord.AppCommandOptionType.channel)
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

    async def test_admin_changes_persist_and_status_resolves_channels(self):
        store = Settings()
        original = doc(**{"1": {"selected_channel_ids": ["11"], "say_log_channel_id": "20"},
                          "2": {"selected_channel_ids": ["99"]}})
        state = original
        async def github(method, **kwargs):
            nonlocal state
            if method == "PUT":
                state = json.loads(base64.b64decode(kwargs["payload"]["content"]))
                return 200, None, b""
            return file_response(state)
        store.request = AsyncMock(side_effect=github)
        channel = SimpleNamespace(id=12, guild=SimpleNamespace(id=1), mention="<#12>")
        guild = SimpleNamespace(get_channel={12:channel, 20:SimpleNamespace(mention="<#20>")}.get)
        itx = SimpleNamespace(guild_id=1, guild=guild,
                              response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                              followup=SimpleNamespace(send=AsyncMock()))
        cog = AdminCog(SimpleNamespace(settings=store))
        await cog.add_channel.callback(cog, itx, channel)
        restarted = Settings()
        restarted.request = store.request
        await restarted.refresh()
        self.assertEqual(restarted.get(1)["selected_channel_ids"], ["11", "12"])
        await cog.status.callback(cog, itx)
        report = itx.response.send_message.await_args.args[0]
        for value in ("<#12>", "Unavailable channel (11)", "<#20>", "Silent: ON", "Emoji: OFF"):
            self.assertIn(value, report)
        calls = store.request.await_count
        await cog.remove_channel.callback(cog, itx, SimpleNamespace(id=99, guild=SimpleNamespace(id=2)))
        self.assertEqual(store.request.await_count, calls)
        await cog.remove_channel.callback(cog, itx, channel)
        await cog.remove_channel_id.callback(cog, itx, "11")
        self.assertEqual(state["guilds"]["1"]["selected_channel_ids"], [])
        await cog.add_channel.callback(cog, itx, channel)
        await cog.clear_channels.callback(cog, itx)
        self.assertEqual(state["guilds"]["1"]["selected_channel_ids"], [])
        self.assertEqual(state["guilds"]["2"], original["guilds"]["2"])

    async def test_say_audit_first_mentions_and_delivery_outcomes(self):
        guild = SimpleNamespace(id=1)
        log, target = (Mock(spec=discord.TextChannel) for _ in range(2))
        log.guild = target.guild = guild
        log.id, target.id, target.mention = 20, 21, "<#21>"
        audit = SimpleNamespace(edit=AsyncMock())
        order = []
        async def public(*args):
            order.append("public")
            return 22
        async def pending(**kwargs):
            order.append("pending")
            self.assertEqual(kwargs["embed"].footer.text, "Pending delivery")
            return audit
        log.send = AsyncMock(side_effect=pending)
        bot = SimpleNamespace(settings=SimpleNamespace(get=lambda _:{**DEFAULT,"say_log_channel_id":"20"}),
                              get_channel=lambda _:log)
        itx = SimpleNamespace(guild_id=1, channel=target, user=SimpleNamespace(mention="<@1>"),
                              response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        cog = Say(bot)
        body = "@everyone @here <@1> <@&2>"
        send = AsyncMock(side_effect=public)
        with patch.dict(cog.say.callback.__globals__, {"send_public_once": send}):
            await cog.say.callback(cog, itx, body)
            self.assertEqual(order, ["pending", "public"])
            self.assertEqual(send.await_args.args[2], body)
            for mentions in (send.await_args.args[3], log.send.await_args.kwargs["allowed_mentions"],
                             audit.edit.await_args.kwargs["allowed_mentions"]):
                self.assertEqual(mentions.to_dict(), {"parse": []})
            self.assertEqual(audit.edit.await_args.kwargs["embed"].footer.text, "Sent | message 22")
            for error, expected in [(DeliveryRejected, "rejected"), (TimeoutError, "uncertain")]:
                send.reset_mock()
                send.side_effect = error
                await cog.say.callback(cog, itx, body)
                send.assert_awaited_once()
                self.assertIn(expected, itx.followup.send.await_args.args[0])
            send.side_effect = public
            audit.edit.side_effect = TimeoutError
            await cog.say.callback(cog, itx, body)
            self.assertIn("Do not resend", itx.followup.send.await_args.args[0])
            send.reset_mock()
            log.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
            await cog.say.callback(cog, itx, body)
            send.assert_not_awaited()

    async def test_say_transport_posts_once_without_redirect_or_mentions(self):
        session = AsyncMock()
        session.post = MagicMock()
        response = session.post.return_value.__aenter__.return_value
        bot = SimpleNamespace(http=SimpleNamespace(token="fake", user_agent="test"))
        target = SimpleNamespace(id=21)
        with patch("cogs.say.aiohttp.ClientSession") as factory:
            factory.return_value.__aenter__.return_value = session
            for status in (403, 429, 503, 307, 200):
                session.post.reset_mock()
                response.status = status
                response.json.return_value = {"id": "22"}
                call = send_public_once(bot, target, "@everyone", discord.AllowedMentions.none())
                if status == 200:
                    self.assertEqual(await call, 22)
                else:
                    with self.assertRaises(DeliveryRejected if status < 500 and status >= 400 else ValueError):
                        await call
                session.post.assert_called_once()
                self.assertFalse(session.post.call_args.kwargs["allow_redirects"])
                self.assertEqual(session.post.call_args.kwargs["json"],
                                 {"content": "@everyone", "allowed_mentions": {"parse": []}})

    async def test_quote_fallback(self):
        with patch("cogs.quote.DATA_FILE",SimpleNamespace(read_text=lambda **_:"[]")):
            self.assertTrue(_load_quotes())
