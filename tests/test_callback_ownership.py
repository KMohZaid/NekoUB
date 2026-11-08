import asyncio
import unittest
from unittest.mock import AsyncMock

from plugins.help import HelpPlugin, _help_keyboard, callback_belongs_to_user, encode_callback_data


class CallbackOwnershipTests(unittest.TestCase):
    def test_command_metadata_defaults_to_sudo_role_access_and_grants(self):
        from core.plugins import Plugin, handlers as h

        class DefaultAccessPlugin(Plugin):
            id = "default_access"

            @h.command("defaulted")
            async def defaulted(self, client, message, ctx, args):
                pass

        metadata = next(
            item.options.command
            for item in DefaultAccessPlugin().declarations()
            if item.options.command is not None
        )
        self.assertTrue(metadata.sudo)
        self.assertFalse(metadata.share)
        self.assertTrue(metadata.grantable)

    def test_callback_data_carries_owner_and_checks_clicker(self):
        payload = encode_callback_data("help", 101, "root")

        self.assertTrue(callback_belongs_to_user(payload, 101))
        self.assertFalse(callback_belongs_to_user(payload, 202))

    def test_missing_malformed_and_stale_callback_owners_are_rejected(self):
        for payload in ("help.root", "help:NaN:root", "other:1:root"):
            with self.subTest(payload=payload):
                self.assertFalse(callback_belongs_to_user(payload, 1))
        self.assertFalse(callback_belongs_to_user(None, 1))
        self.assertFalse(callback_belongs_to_user("help:1:root", None))

    def test_every_help_keyboard_navigation_callback_keeps_initiator_binding(self):
        for page in (
            "root",
            "category:core",
            "plugin:core:demo",
            "command:core:demo:safe",
        ):
            with self.subTest(page=page):
                markup = _help_keyboard(page, user_id=101)
                for row in markup.inline_keyboard:
                    for button in row:
                        self.assertTrue(
                            callback_belongs_to_user(button.callback_data, 101),
                            button.callback_data,
                        )

    def test_help_inline_query_is_public_for_access_aware_answers(self):
        declaration = next(
            item
            for item in HelpPlugin().declarations()
            if item.event == "bot_inline_query"
        )
        self.assertTrue(declaration.options.public)

    def test_help_callback_refuses_other_user_before_editing(self):
        plugin = HelpPlugin()
        declaration = next(item for item in plugin.declarations() if item.event == "bot_callback_query")
        bot = type("Bot", (), {"is_bot": True})()
        callback = __import__("core.plugins.api", fromlist=["_wrap_handler"])._wrap_handler(
            plugin,
            declaration,
            bot_client=bot,
        )
        update = type(
            "Callback",
            (),
            {
                "data": encode_callback_data("help", 101, "root"),
                "from_user": type("User", (), {"id": 202})(),
                "answer": AsyncMock(),
                "message": type("Message", (), {"edit_text": AsyncMock()})(),
            },
        )()
        client = bot

        with self.assertRaises(Exception) as raised:
            asyncio.run(callback(client, update))

        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        update.answer.assert_awaited_once_with(
            "This help menu belongs to someone else.", show_alert=True
        )
        update.message.edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
