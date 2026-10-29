import unittest
from unittest.mock import AsyncMock

from plugins.sudo_and_share import (
    SudoAndSharePlugin,
    parse_command_names,
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

        self.assertEqual(await resolve_target_user_id(client, None, "@friend"), 73)
        client.get_users.assert_awaited_once_with("friend")

    async def test_resolves_numeric_id_without_lookup(self):
        client = AsyncMock()

        self.assertEqual(await resolve_target_user_id(client, None, "-100123"), -100123)
        client.get_users.assert_not_awaited()

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

    def test_grant_and_reset_commands_use_sudo_route_not_standalone_grants(self):
        commands = {
            item.options.command.trigger: item.options.command
            for item in SudoAndSharePlugin().declarations()
            if item.options.command is not None
        }

        for name in ("grant", "revoke", "resetgrant"):
            with self.subTest(name=name):
                self.assertTrue(commands[name].sudo)
                self.assertFalse(commands[name].grantable)

    def test_role_management_uses_owner_only_route(self):
        commands = {
            item.options.command.trigger: item.options.command
            for item in SudoAndSharePlugin().declarations()
            if item.options.command is not None
        }

        for name in ("addsudo", "rmsudo", "sudolist", "addshare", "rmshare", "sharelist"):
            with self.subTest(name=name):
                self.assertFalse(commands[name].sudo)
                self.assertFalse(commands[name].share)
                self.assertFalse(commands[name].grantable)

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
