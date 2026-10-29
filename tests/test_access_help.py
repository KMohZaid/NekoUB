import unittest
from unittest.mock import AsyncMock, Mock

import plugins.help as help_plugin


class AccessAwareHelpTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_unavailable_command_is_spoilered_with_footer(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=False), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("category:core", actor_id=10, owner_id=1, access=access)
        self.assertIn("||.danger||", rendered)
        self.assertIn("> **Commands hidden in spoilers are unavailable to you.**", rendered)
        markup = help_plugin._html(rendered)
        self.assertIn("<tg-spoiler>.danger</tg-spoiler>", markup)
        self.assertIn("<blockquote><b>Commands hidden in spoilers are unavailable to you.</b></blockquote>", markup)

    async def test_available_share_command_uses_share_prefix(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=True), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("category:core", actor_id=10, owner_id=1, access=access)
        self.assertIn("?safe", rendered)
        self.assertIn("||.danger||", rendered)

    async def test_callback_plugin_page_uses_access_aware_routes(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=True), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("plugin:core:demo", actor_id=10, owner_id=1, access=access)
        self.assertIn("?safe", rendered)
        self.assertIn("||.danger||", rendered)

    async def test_callback_root_page_spoilers_unavailable_commands(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=False), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("root", actor_id=10, owner_id=1, access=access)
        self.assertIn("Commands hidden in spoilers", rendered)

    async def test_callback_command_page_uses_access_aware_routes(self):
        access = type("Access", (), {"is_sudoer": AsyncMock(return_value=False), "is_share_user": AsyncMock(return_value=True), "has_grant": AsyncMock(return_value=False)})()
        rendered = await help_plugin.render_access_aware("command:core:demo:danger", actor_id=10, owner_id=1, access=access)
        self.assertIn("||.danger||", rendered)
        self.assertIn("Commands hidden in spoilers", rendered)


if __name__ == "__main__":
    unittest.main()
