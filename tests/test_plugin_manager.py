import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from core.plugins import Example, Plugin, handlers as h
from core.plugins.manager import PluginManager
from pyrogram import filters


class ManagedPlugin(Plugin):
    id = "managed"

    @h.command("hello", description="Say hello")
    async def hello(self, client, message, ctx, args):
        return None

    @h.message(filters=filters.text)
    async def messages(self, client, message, ctx, *args, **kwargs):
        return None


class PluginManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.client = Mock()
        self.client.register_managed_handler.side_effect = (
            lambda handler, group, **metadata: (handler, group)
        )
        self.manager = PluginManager(self.client, Path(self.temp_dir.name), prefix=".! ")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_activation_tracks_handler_ownership(self):
        self.manager.activate(ManagedPlugin())
        active = self.manager.active["managed"]
        self.assertEqual(len(active.handlers), 2)
        self.assertEqual(self.manager.status["managed"], "active")
        self.manager._deactivate(active)
        self.assertNotIn("managed", self.manager.registry.plugins)
        self.assertEqual(self.client.remove_managed_handler.call_count, 2)

    def test_failed_staging_removes_only_staged_handlers(self):
        self.client.register_managed_handler.side_effect = [
            (object(), 0),
            RuntimeError("broken"),
        ]
        with self.assertRaisesRegex(RuntimeError, "broken"):
            self.manager.activate(ManagedPlugin())
        self.client.remove_managed_handler.assert_called_once()
        self.assertFalse(self.manager.registry.plugins)

    def test_duplicate_command_metadata_is_rejected_before_install(self):
        class Duplicate(Plugin):
            id = "duplicate"

            @h.command("same")
            async def first(self, client, message, ctx, args):
                pass

            @h.command("same")
            async def second(self, client, message, ctx, args):
                pass

        with self.assertRaisesRegex(ValueError, "Duplicate command"):
            self.manager.activate(Duplicate())
        self.client.register_managed_handler.assert_not_called()

    def test_command_help_includes_all_prefixes_and_renders_examples(self):
        class PrefixPlugin(Plugin):
            id = "prefixes"

            @h.command(
                "hello",
                prefixes=(".", "!"),
                aliases=("hi",),
                examples=(Example("{cmd}"),),
            )
            async def hello(self, client, message, ctx, args):
                pass

        self.manager.activate(PrefixPlugin())
        result = self.manager.command_help()[0]
        self.assertEqual(result["prefixes"], (".", "!"))
        self.assertEqual(result["examples"], (".hello",))

    def test_reload_uses_source_filename_not_plugin_id(self):
        plugin_path = Path(self.temp_dir.name) / "demo_plugin.py"
        plugin_path.write_text(
            "from core.plugins import Plugin, handlers as h\n"
            "class Demo(Plugin):\n"
            "    id = 'demo'\n"
            "    @h.command('hello')\n"
            "    async def hello(self, client, message, ctx, args): pass\n",
            encoding="utf-8",
        )
        self.manager.load_path(plugin_path)
        self.manager.reload("demo")
        self.assertEqual(self.manager.active["demo"].source_path, plugin_path)
        self.assertEqual(self.client.register_managed_handler.call_count, 2)

    def test_load_all_discovers_only_direct_public_modules(self):
        (Path(self.temp_dir.name) / "demo.py").write_text(
            "from core.plugins import Plugin, handlers as h\n"
            "class Demo(Plugin):\n"
            "    id = 'demo'\n"
            "    @h.command('hello')\n"
            "    async def hello(self, client, message, ctx, args): pass\n",
            encoding="utf-8",
        )
        (Path(self.temp_dir.name) / "_ignored.py").write_text("raise RuntimeError()")
        status = self.manager.load_all()
        self.assertEqual(status["demo"], "active")
        self.assertNotIn("_ignored", status)
        self.assertEqual(list(self.manager.active), ["demo"])


if __name__ == "__main__":
    unittest.main()
