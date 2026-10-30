import unittest
from unittest import mock
from unittest.mock import AsyncMock

from plugins.sudo_and_share import (
    SudoAndSharePlugin,
    parse_command_names,
    resolve_target_user,
    resolve_target_user_id,
)


class SudoAndShareTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_replied_user_before_text_target(self):
        client = AsyncMock()
        message = type(
            "Message",
            (),
            {
                "reply_to_message": type(
                    "Reply", (), {"from_user": type("User", (), {"id": 42})()}
                )(),
                "text": ".addsudo @ignored",
            },
        )()

        self.assertEqual(await resolve_target_user_id(client, message, "@ignored"), 42)
        client.get_users.assert_not_awaited()

    async def test_resolves_username_with_client(self):
        client = AsyncMock()
        client.get_users.return_value = type("User", (), {"id": 73})()

        user = await resolve_target_user(client, None, "@friend")
        self.assertEqual(user.id, 73)
        client.get_users.assert_awaited_once_with("friend")

    async def test_resolves_numeric_id_and_returns_user_name(self):
        client = AsyncMock()
        client.get_users.return_value = type(
            "User", (), {"id": 100123, "first_name": "Abcd", "last_name": "Zyx"}
        )()

        user = await resolve_target_user(client, None, "100123")

        self.assertEqual((user.id, user.first_name, user.last_name), (100123, "Abcd", "Zyx"))
        client.get_users.assert_awaited_once_with(100123)

    async def test_keeps_numeric_id_when_user_lookup_fails(self):
        client = AsyncMock()
        client.get_users.side_effect = ValueError("not found")

        user = await resolve_target_user(client, None, "-100123")

        self.assertEqual(user.id, -100123)
        self.assertIsNone(user.first_name)
        client.get_users.assert_awaited_once_with(-100123)

    async def test_rejects_unresolvable_target_without_fabricating_id(self):
        client = AsyncMock()
        client.get_users.side_effect = ValueError("not found")

        with self.assertRaises(ValueError):
            await resolve_target_user_id(client, None, "@missing")

    def test_parses_comma_separated_commands_case_insensitively(self):
        self.assertEqual(parse_command_names(".say, TTS, #exec"), ["say", "tts", "exec"])

    def test_grant_names_resolve_aliases_to_canonical_command(self):
        from plugins.sudo_and_share import canonicalize_command_names

        commands = [
            {"trigger": "say", "aliases": ("tts",)},
            {"trigger": "alive", "aliases": ()},
        ]
        self.assertEqual(
            canonicalize_command_names(["tts", "alive"], commands),
            ["say", "alive"],
        )

    def test_rejects_empty_or_malformed_command_names(self):
        for value in ("", "say,,tts", "hello-world"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_command_names(value)

    def test_grant_and_reset_commands_use_sudo_route(self):
        commands = {
            item.options.command.trigger: item.options.command
            for item in SudoAndSharePlugin().declarations()
            if item.options.command is not None
        }

        for name in ("grant", "revoke", "resetgrant"):
            with self.subTest(name=name):
                self.assertTrue(commands[name].sudo)

    async def test_share_list_edits_message_with_linked_names(self):
        plugin = SudoAndSharePlugin()
        store = AsyncMock()
        store.list_share_users.return_value = [
            type("ShareUser", (), {"user_id": 42, "first_name": "Abcd", "last_name": "Zyx"})()
        ]
        message = type("Message", (), {"text": ".sharelist"})()
        context = type(
            "Context",
            (),
            {
                "actor_is_owner": True,
                "get_config": lambda self, key: store if key == "ACCESS_STORE" else None,
                "edit": AsyncMock(),
                "send": AsyncMock(),
            },
        )()

        await plugin.share_list(object(), message, context, None)

        context.edit.assert_awaited_once()
        context.send.assert_not_awaited()
        output = context.edit.await_args.args[0]
        self.assertIn('<a href="tg://user?id=42">Abcd Zyx</a> (ID: <code>42</code>)', output)

    async def test_share_list_replies_when_delegated_user_invokes_it(self):
        plugin = SudoAndSharePlugin()
        store = AsyncMock()
        store.list_share_users.return_value = []
        message = type("Message", (), {"text": ".sharelist"})()
        context = type(
            "Context",
            (),
            {
                "actor_is_owner": False,
                "get_config": lambda self, key: store if key == "ACCESS_STORE" else None,
                "edit": AsyncMock(),
                "send": AsyncMock(),
            },
        )()

        await plugin.share_list(object(), message, context, None)

        context.edit.assert_not_awaited()
        context.send.assert_awaited_once_with("Share users: none", reply_to=message)

    async def test_sudo_list_uses_id_when_name_is_unavailable(self):
        plugin = SudoAndSharePlugin()
        store = AsyncMock()
        store.list_sudoers.return_value = [
            type("Sudoer", (), {"user_id": 73, "first_name": None, "last_name": None})()
        ]
        message = type("Message", (), {"text": ".sudolist"})()
        context = type(
            "Context",
            (),
            {
                "actor_is_owner": True,
                "get_config": lambda self, key: store if key == "ACCESS_STORE" else None,
                "edit": AsyncMock(),
                "send": AsyncMock(),
            },
        )()

        await plugin.sudo_list(object(), message, context, None)

        context.edit.assert_awaited_once()
        self.assertIn("ID: <code>73</code>", context.edit.await_args.args[0])

    def test_sudo_is_default_for_commands_except_sudo_role_management(self):
        from core.plugins import Plugin, handlers as h

        class Defaults(Plugin):
            id = "defaults"

            @h.command("ordinary")
            async def ordinary(self, client, message, ctx, args):
                pass

        self.assertTrue(Defaults().declarations()[0].options.command.sudo)
        commands = {
            item.options.command.trigger: item.options.command
            for item in SudoAndSharePlugin().declarations()
            if item.options.command is not None
        }
        for name in ("addsudo", "rmsudo"):
            with self.subTest(name=name):
                self.assertFalse(commands[name].sudo)
                self.assertFalse(commands[name].grantable)

    def test_only_addsudo_and_rmsudo_are_not_grantable(self):
        commands = {
            item.options.command.trigger: item.options.command
            for item in SudoAndSharePlugin().declarations()
            if item.options.command is not None
        }

        self.assertFalse(commands["addsudo"].grantable)
        self.assertFalse(commands["rmsudo"].grantable)
        for name in ("sudolist", "addshare", "grant", "rmshare", "revoke", "sharelist"):
            with self.subTest(name=name):
                self.assertTrue(commands[name].grantable)

    def test_share_management_commands_are_available_on_sudo_route(self):
        commands = {
            item.options.command.trigger: item.options.command
            for item in SudoAndSharePlugin().declarations()
            if item.options.command is not None
        }

        for name in ("addshare", "grant", "rmshare", "revoke", "sharelist", "sudolist"):
            with self.subTest(name=name):
                self.assertTrue(commands[name].sudo)
                self.assertFalse(commands[name].share)

    def test_role_management_excludes_sudoers_and_share_users(self):
        commands = {
            item.options.command.trigger: item.options.command
            for item in SudoAndSharePlugin().declarations()
            if item.options.command is not None
        }

        for name in ("addsudo", "rmsudo"):
            with self.subTest(name=name):
                self.assertFalse(commands[name].sudo)
                self.assertFalse(commands[name].share)

    async def test_grant_rejects_addsudo_which_is_not_grantable(self):
        plugin = SudoAndSharePlugin()
        client = AsyncMock()
        store = AsyncMock()
        context = type(
            "Context",
            (),
            {
                "get_config": lambda self, key: store if key == "ACCESS_STORE" else None,
                "send": AsyncMock(),
            },
        )()
        message = type(
            "Message",
            (),
            {"reply_to_message": type("Reply", (), {"from_user": type("User", (), {"id": 42})()})()},
        )()
        args = type("Args", (), {"positional": ("addsudo",)})()
        manager = type(
            "Manager",
            (),
            {
                "command_help": lambda self: [
                    {"trigger": "addsudo", "aliases": (), "grantable": False}
                ]
            },
        )()

        with mock.patch.dict(
            "sys.modules",
            {"main": type("Main", (), {"plugin_manager": manager, "private_plugin_manager": None})()},
        ):
            await plugin._change_grants(client, message, context, args, revoke=False)

        store.grant.assert_not_awaited()
        context.send.assert_awaited_once()
        self.assertIn("addsudo", context.send.await_args.args[0])

    async def test_grants_accept_command_list_when_target_is_replied_user(self):
        plugin = SudoAndSharePlugin()
        client = AsyncMock()
        store = AsyncMock()
        store.grant.return_value = True
        context = type(
            "Context",
            (),
            {
                "get_config": lambda self, key: store if key == "ACCESS_STORE" else None,
                "send": AsyncMock(),
            },
        )()
        target_message = type(
            "Message",
            (),
            {"reply_to_message": type("Reply", (), {"from_user": type("User", (), {"id": 42})()})()},
        )()
        args = type("Args", (), {"positional": ("say,tts",)})()

        await plugin._change_grants(client, target_message, context, args, revoke=False)

        store.grant.assert_any_await(42, "say")
        store.grant.assert_any_await(42, "tts")
        self.assertEqual(store.grant.await_count, 2)


if __name__ == "__main__":
    unittest.main()
