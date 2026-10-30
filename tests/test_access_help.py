import unittest
from unittest.mock import AsyncMock, Mock

import plugins.help as help_plugin


class AccessAwareHelpTests(unittest.IsolatedAsyncioTestCase):
    def test_access_aware_renderer_annotations_resolve(self):
        from typing import Any, get_type_hints

        self.assertIs(get_type_hints(help_plugin.render_access_aware)["access"], Any)

    def test_help_command_supports_sudo_and_share_routes(self):
        command = next(
            handler.options.command
            for handler in help_plugin.HelpPlugin().declarations()
            if handler.event == "command"
            and not handler.options.bot
            and handler.options.command is not None
            and handler.options.command.trigger == "help"
        )

        self.assertTrue(command.sudo)
        self.assertTrue(command.share)

    async def test_help_command_falls_back_to_direct_render_without_assistant(self):
        plugin = help_plugin.HelpPlugin()
        message = type(
            "Message",
            (),
            {
                "text": "?help stickers",
                "chat": type("Chat", (), {"id": 25})(),
                "id": 99,
                "reply_text": AsyncMock(),
            },
        )()
        context = type(
            "Context",
            (),
            {
                "bot": None,
                "actor_id": 10,
                "actor_is_owner": False,
                "client": type("Client", (), {"me": type("User", (), {"id": 1})()})(),
                "get_config": lambda self, key: None,
                "edit": AsyncMock(),
                "send": AsyncMock(),
            },
        )()
        with unittest.mock.patch.object(
            help_plugin, "render_access_aware", AsyncMock(return_value="fallback help")
        ):
            await plugin.help_command(object(), message, context, None)

        context.send.assert_awaited_once()
        context.edit.assert_not_awaited()
        self.assertEqual(context.send.await_args.args[0], "fallback help")
        self.assertIs(context.send.await_args.kwargs["reply_to"], message)

    async def test_help_command_sends_assistant_inline_result(self):
        plugin = help_plugin.HelpPlugin()
        message = type(
            "Message",
            (),
            {
                "text": "?help stickers",
                "chat": type("Chat", (), {"id": 25})(),
                "id": 99,
                "reply_text": AsyncMock(),
            },
        )()
        inline_results = type(
            "Results", (), {"query_id": 7, "results": [type("Result", (), {"id": "article-1"})()]}
        )()
        client = type(
            "Client",
            (),
            {
                "get_inline_bot_results": AsyncMock(return_value=inline_results),
                "send_inline_bot_result": AsyncMock(),
            },
        )()
        assistant = type(
            "Bot", (), {"get_me": AsyncMock(return_value=type("User", (), {"username": "helperbot"})())}
        )()
        context = type("Context", (), {"bot": assistant, "actor_id": 42})()

        await plugin.help_command(client, message, context, None)

        client.get_inline_bot_results.assert_awaited_once_with("helperbot", "help actor:42 stickers")
        client.send_inline_bot_result.assert_awaited_once()
        self.assertEqual(client.send_inline_bot_result.await_args.args[:3], (25, 7, "article-1"))
        self.assertEqual(client.send_inline_bot_result.await_args.kwargs["reply_parameters"].message_id, 99)
        message.reply_text.assert_not_awaited()

    async def test_assistant_inline_help_uses_delegated_actor_id(self):
        plugin = help_plugin.HelpPlugin()
        query = type(
            "Query",
            (),
            {
                "query": "help actor:42 stickers",
                "from_user": type("User", (), {"id": 1})(),
                "answer": AsyncMock(),
            },
        )()
        bot = type("Bot", (), {})()
        context = type(
            "Context",
            (),
            {"get_config": lambda self, key: None},
        )()
        with unittest.mock.patch.object(
            help_plugin,
            "render_access_aware",
            AsyncMock(return_value="help"),
        ) as render:
            await plugin.help_inline_query(bot, query, context)

        render.assert_awaited_once_with(
            "stickers", actor_id=42, owner_id=None, access=None
        )
        result = query.answer.await_args.args[0][0]
        self.assertEqual(result.reply_markup.inline_keyboard, [])

    def setUp(self):
        self.original_commands = help_plugin._managed_commands
        help_plugin._managed_commands = lambda: [
            {
                "plugin": "demo", "plugin_category": "Core", "plugin_description": "Demo commands.",
                "plugin_display_name": "Demo", "trigger": "safe", "aliases": (), "prefix": ".",
                "prefixes": (".",), "signature": "", "description": "A safe command.",
                "short_description": "Safe command.", "examples": (), "example_notes": (),
                "options": (), "help_options": (), "sudo": False, "share": True, "grantable": True,
            },
            {
                "plugin": "demo", "plugin_category": "Core", "plugin_description": "Demo commands.",
                "plugin_display_name": "Demo", "trigger": "danger", "aliases": (), "prefix": ".",
                "prefixes": (".",), "signature": "", "description": "Dangerous command.",
                "short_description": "Dangerous command.", "examples": (), "example_notes": (),
                "options": (), "help_options": (), "sudo": True, "share": False, "grantable": True,
            },
        ]

    def tearDown(self):
        help_plugin._managed_commands = self.original_commands

    async def test_owner_help_shows_all_configured_prefixes(self):
        access = type("Access", (), {})()
        rendered = await help_plugin.render_access_aware(
            "root", actor_id=1, owner_id=1, access=access
        )
        self.assertIn("Prefixes:", rendered)
        self.assertIn("Sudo/Share/Grant Prefixes:", rendered)
        self.assertIn(".`", rendered)
        self.assertIn("`,#?`", rendered)

    async def test_owner_help_shows_full_configured_prefix_strings(self):
        access = type("Access", (), {})()
        with unittest.mock.patch.object(help_plugin.config, "CMD_PREFIX", ".$"):
            with unittest.mock.patch.object(
                help_plugin.config, "CMD_PREFIX_FOR_SUDO_AND_SHARE", ",#?"
            ):
                rendered = await help_plugin.render_access_aware(
                    "root", actor_id=1, owner_id=1, access=access
                )
        self.assertIn("Prefixes: `.$`", rendered)
        self.assertIn("Sudo/Share/Grant Prefixes: `,#?`", rendered)

    async def test_non_owner_help_shows_only_their_access_prefix(self):
        access = type(
            "Access", (),
            {
                "is_sudoer": AsyncMock(return_value=False),
                "is_share_user": AsyncMock(return_value=True),
                "has_grant": AsyncMock(return_value=False),
            },
        )()
        rendered = await help_plugin.render_access_aware(
            "root", actor_id=10, owner_id=1, access=access
        )
        self.assertIn("Prefix: `?`", rendered)
        self.assertNotIn("Sudo/Share/Grant Prefixes:", rendered)

    async def test_sudo_help_shows_sudo_prefix(self):
        access = type(
            "Access", (),
            {
                "is_sudoer": AsyncMock(return_value=True),
                "is_share_user": AsyncMock(return_value=False),
                "has_grant": AsyncMock(return_value=False),
            },
        )()
        rendered = await help_plugin.render_access_aware(
            "root", actor_id=10, owner_id=1, access=access
        )
        self.assertIn("Prefix: `#`", rendered)

    async def test_unavailable_command_is_removed_from_help(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=False), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("category:core", actor_id=10, owner_id=1, access=access)
        self.assertNotIn(".danger", rendered)
        self.assertIn("NOTE: You can only see what you have access to use", rendered)
        self.assertNotIn("tg-spoiler", help_plugin._html(rendered))

    async def test_available_share_command_uses_share_prefix(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=True), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("category:core", actor_id=10, owner_id=1, access=access)
        self.assertIn("?safe", rendered)
        self.assertNotIn(".danger", rendered)

    async def test_callback_plugin_page_uses_access_aware_routes(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=True), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("plugin:core:demo", actor_id=10, owner_id=1, access=access)
        self.assertIn("?safe", rendered)
        self.assertNotIn(".danger", rendered)

    async def test_callback_root_hides_unavailable_plugins_and_empty_categories(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=False), "has_grant": AsyncMock(return_value=False)})()
        original_categories = help_plugin.CATEGORIES.copy()
        original_root = help_plugin.render_root
        original_header = help_plugin._header
        help_plugin.CATEGORIES = {"Core": {"emoji": "🧭", "title": "Core", "desc": "Basic bot commands."}}
        help_plugin._header = lambda: "Help"
        try:
            rendered = await help_plugin.render_access_aware("root", actor_id=10, owner_id=1, access=access)
        finally:
            help_plugin.CATEGORIES = original_categories
            help_plugin._header = original_header
        self.assertNotIn("Demo", rendered)
        self.assertNotIn("Core", rendered)
        self.assertIn("NOTE: You can only see what you have access to use", rendered)

    async def test_callback_command_page_uses_access_aware_routes(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=True), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("command:core:demo:danger", actor_id=10, owner_id=1, access=access)
        self.assertNotIn(".danger", rendered)
        self.assertIn("NOTE: You can only see what you have access to use", rendered)


if __name__ == "__main__":
    unittest.main()
