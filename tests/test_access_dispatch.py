import asyncio
import unittest
from unittest.mock import AsyncMock

from core.plugins import Plugin, handlers as h
from core.plugins.api import _wrap_handler


class GrantableCommandPlugin(Plugin):
    id = "grantable_command"
    called = False

    @h.command("alive")
    async def alive(self, client, message, ctx, args):
        self.called = True
        self.context = ctx


class AccessDispatchTests(unittest.TestCase):
    def test_explicit_grant_works_for_owner_only_declared_command(self):
        plugin = GrantableCommandPlugin()
        declaration = plugin.declarations()[0]
        store = AsyncMock()
        store.has_grant.return_value = True
        client = type("Client", (), {"me": type("User", (), {"id": 1})()})()
        callback = _wrap_handler(
            plugin, declaration, user_client=client, access_store=store
        )
        message = type(
            "Message",
            (),
            {
                "text": "?alive",
                "caption": None,
                "from_user": type("User", (), {"id": 2})(),
            },
        )()

        with self.assertRaises(Exception) as raised:
            asyncio.run(callback(client, message))

        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        self.assertTrue(plugin.called)
        self.assertEqual(plugin.context.actor_id, 2)
        self.assertFalse(plugin.context.actor_is_owner)
        store.has_grant.assert_awaited_once_with(2, "alive")

    def test_ungranted_owner_only_command_silently_stops_before_handler(self):
        plugin = GrantableCommandPlugin()
        declaration = plugin.declarations()[0]
        store = AsyncMock()
        store.has_grant.return_value = False
        client = type("Client", (), {"me": type("User", (), {"id": 1})()})()
        callback = _wrap_handler(
            plugin, declaration, user_client=client, access_store=store
        )
        message = type(
            "Message",
            (),
            {
                "text": "?alive",
                "caption": None,
                "from_user": type("User", (), {"id": 2})(),
            },
        )()

        with self.assertRaises(Exception) as raised:
            asyncio.run(callback(client, message))

        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        self.assertFalse(plugin.called)
        store.has_grant.assert_awaited_once_with(2, "alive")


if __name__ == "__main__":
    unittest.main()
