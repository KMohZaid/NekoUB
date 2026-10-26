import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
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

    def test_log_forwarder_uses_current_event_loop(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        client = Mock()
        previous_config = sys.modules.get("config")
        previous_utils = sys.modules.get("utils")
        config_module = ModuleType("config")
        config_module.DEBUG_CHAT_ID = 0
        config_module.DEBUG_LOG_LEVEL = "INFO"
        utils_module = ModuleType("utils")
        utils_module.get_logger = lambda name: Mock()
        sys.modules.update(config=config_module, utils=utils_module)
        try:
            from importlib.util import module_from_spec, spec_from_file_location

            path = Path(__file__).resolve().parents[1] / "utils" / "telegram_logging.py"
            spec = spec_from_file_location("test_telegram_logging", path)
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertIsNone(module.install(loop, client))
            self.assertEqual(len(asyncio.all_tasks(loop)), 0)
        finally:
            sys.modules.pop("utils.telegram_logging", None)
            for name, previous in (("config", previous_config), ("utils", previous_utils)):
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

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

    def test_help_plugin_declares_aliases_and_command_help(self):
        plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
        previous_config = sys.modules.get("config")
        previous_main = sys.modules.get("main")
        previous_utils = sys.modules.get("utils")
        config_module = ModuleType("config")
        config_module.CMD_PREFIX = "."
        main_module = ModuleType("main")
        main_module.loaded_plugins = {}
        main_module.plugin_manager = None
        utils_module = ModuleType("utils")
        utils_module.get_logger = lambda name: Mock()
        sys.modules.update(config=config_module, main=main_module, utils=utils_module)
        try:
            from importlib.util import module_from_spec, spec_from_file_location

            spec = spec_from_file_location("test_plugins.help", plugins_dir / "help.py")
            module = module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            plugin_class = next(
                value for value in vars(module).values()
                if isinstance(value, type) and issubclass(value, Plugin) and value is not Plugin
            )
            declaration = PluginManager(
                self.client, Path(self.temp_dir.name), prefix="."
            )._validate(plugin_class())
            command = declaration.handlers[0].options.command
            self.assertEqual(command.aliases, ("commands", "cmds", "menu"))
        finally:
            for name, previous in (
                ("config", previous_config),
                ("main", previous_main),
                ("utils", previous_utils),
            ):
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

    def test_help_lists_managed_commands_once(self):
        plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
        previous_config = sys.modules.get("config")
        previous_main = sys.modules.get("main")
        previous_utils = sys.modules.get("utils")
        config_module = ModuleType("config")
        config_module.CMD_PREFIX = "."
        main_module = ModuleType("main")
        main_module.loaded_plugins = {}
        main_module.plugin_manager = None
        utils_module = ModuleType("utils")
        utils_module.get_logger = lambda name: Mock()
        sys.modules.update(config=config_module, main=main_module, utils=utils_module)
        try:
            from importlib.util import module_from_spec, spec_from_file_location

            spec = spec_from_file_location(
                "test_plugins.help_dedupe", plugins_dir / "help.py"
            )
            module = module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            managed_names = {
                "alive", "ping", "reload", "help", "exec", "eval", "kill",
                "info", "id", "json", "save", "spam", "spam_stop", "spam_stat",
                "spam_help",
            }
            listed_names = {
                command["cmd"]
                for category in module.CATEGORIES.values()
                for command in category["commands"]
            }
            self.assertTrue(managed_names.isdisjoint(listed_names))
            main_module.plugin_manager = Mock()
            main_module.plugin_manager.command_help.return_value = [
                {
                    "plugin": "ping",
                    "trigger": "ping",
                    "prefix": ".",
                    "prefixes": (".",),
                    "options": (),
                    "description": "Check latency.",
                    "aliases": (),
                    "examples": (),
                }
            ]
            self.assertEqual(
                module._total_commands(),
                sum(len(cat["commands"]) for cat in module.CATEGORIES.values()) + 1,
            )
            self.assertEqual(module.render_all().count("`.ping`"), 1)
        finally:
            for name, previous in (
                ("config", previous_config),
                ("main", previous_main),
                ("utils", previous_utils),
            ):
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

    def test_simple_command_plugins_declare_managed_handlers(self):
        plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
        modules = {}
        previous_config = sys.modules.get("config")
        previous_utils = sys.modules.get("utils")
        previous_httpx = sys.modules.get("httpx")
        config_module = ModuleType("config")
        config_module.CMD_PREFIX = "."
        config_module.STICKERSET_BASE = ""
        config_module.GROQ_API_KEYS = []
        config_module.GROQ_API_KEY = ""
        config_module.GROQ_API_URL = ""
        config_module.TRANSCRIBE_MODEL = ""
        config_module.TRANSLATE_TARGET = "en"
        config_module.TRANSLATE_API_URL = ""
        sys.modules["config"] = config_module
        httpx_module = ModuleType("httpx")
        httpx_module.AsyncClient = Mock()
        sys.modules["httpx"] = httpx_module
        utils_module = ModuleType("utils")
        utils_module.get_logger = lambda name: Mock()
        sys.modules["utils"] = utils_module
        try:
            for plugin_id in ("ping", "id", "info", "alive", "translate", "sangmata"):
                path = plugins_dir / f"{plugin_id}.py"
                from importlib.util import module_from_spec, spec_from_file_location

                spec = spec_from_file_location(f"test_plugins.{plugin_id}", path)
                module = module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                modules[plugin_id] = module
        finally:
            if previous_config is None:
                sys.modules.pop("config", None)
            else:
                sys.modules["config"] = previous_config
            if previous_utils is None:
                sys.modules.pop("utils", None)
            else:
                sys.modules["utils"] = previous_utils
            if previous_httpx is None:
                sys.modules.pop("httpx", None)
            else:
                sys.modules["httpx"] = previous_httpx
        for plugin_id, module in modules.items():
            plugin_class = next(
                value
                for value in vars(module).values()
                if isinstance(value, type)
                and issubclass(value, Plugin)
                and value is not Plugin
            )
            declaration = PluginManager(
                self.client, Path(self.temp_dir.name), prefix="."
            )._validate(plugin_class())
            self.assertEqual(declaration.plugin_id, plugin_id)
            self.assertTrue(declaration.handlers[0].options.command)

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
