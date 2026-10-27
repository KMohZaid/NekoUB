import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

from core.plugins import Example, Option, Plugin, handlers as h
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

    def test_bot_handlers_register_on_bot_client(self):
        user_client = Mock()
        bot_client = Mock()
        user_client.register_managed_handler.side_effect = (
            lambda handler, group, **metadata: (handler, group)
        )
        bot_client.register_managed_handler.side_effect = (
            lambda handler, group, **metadata: (handler, group)
        )
        manager = PluginManager(
            user_client,
            Path(self.temp_dir.name),
            user_client=user_client,
            bot_client=bot_client,
        )

        class DualClientPlugin(Plugin):
            id = "dual_client"

            @h.command("user")
            async def user_command(self, client, message, ctx, args):
                pass

            @h.bot_command("bot")
            async def bot_command(self, bot, message, ctx, args):
                pass

        manager.activate(DualClientPlugin())
        self.assertEqual(user_client.register_managed_handler.call_count, 1)
        self.assertEqual(bot_client.register_managed_handler.call_count, 1)
        self.assertEqual(len(manager.active["dual_client"].handlers), 2)
        manager._deactivate(manager.active["dual_client"])
        user_client.remove_managed_handler.assert_called_once()
        bot_client.remove_managed_handler.assert_called_once()

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

    def test_command_help_expands_documented_command_forms(self):
        class FormsPlugin(Plugin):
            id = "forms"

            @h.command(
                "am",
                help_entries=(
                    {"signature": "", "short_description": "Show help."},
                    {"signature": "<match> on <output>", "short_description": "Match a keyword."},
                ),
            )
            async def automations(self, client, message, ctx, args):
                pass

        self.manager.activate(FormsPlugin())
        commands = self.manager.command_help()
        self.assertEqual([item["signature"] for item in commands], ["", "<match> on <output>"])
        self.assertEqual(commands[1]["short_description"], "Match a keyword.")

    def test_command_help_exposes_option_metadata(self):
        class OptionPlugin(Plugin):
            id = "option_help"

            @h.command(
                "sample",
                options=(Option("count", short="-c", type=int, help="Repeat count."),),
                help_options=(Option("count", short="-c", type=int, help="Repeat count."),),
            )
            async def sample(self, client, message, ctx, args):
                pass

        self.manager.activate(OptionPlugin())
        command = self.manager.command_help()[0]
        self.assertEqual(command["help_options"][0].help, "Repeat count.")
        from importlib.util import module_from_spec, spec_from_file_location
        help_path = Path(__file__).resolve().parents[1] / "plugins" / "help.py"
        previous_config = sys.modules.get("config")
        previous_main = sys.modules.get("main")
        previous_utils = sys.modules.get("utils")
        config_module = ModuleType("config")
        config_module.CMD_PREFIX = "."
        main_module = ModuleType("main")
        main_module.loaded_plugins = {}
        main_module.public_plugin_status = {}
        main_module.plugin_manager = self.manager
        utils_module = ModuleType("utils")
        utils_module.get_logger = lambda name: Mock()
        sys.modules.update(config=config_module, main=main_module, utils=utils_module)
        try:
            spec = spec_from_file_location("test_plugins.option_help", help_path)
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            detail = module.render_command_lookup("sample")
            self.assertIn("• `-c, --count` — Repeat count.", detail)
        finally:
            for name, previous in (("config", previous_config), ("main", previous_main), ("utils", previous_utils)):
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

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
        main_module.public_plugin_status = {
            "todoist.py": {"state": "skipped", "note": "missing TODOIST_TOKEN <set>"},
            "alive.py": {"state": "loaded", "note": None},
        }
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
            bot_client = Mock()
            bot_client.is_bot = True
            declaration = PluginManager(
                self.client,
                Path(self.temp_dir.name),
                prefix=".",
                bot_client=bot_client,
            )._validate(plugin_class())
            command = next(
                handler.options.command
                for handler in declaration.handlers
                if handler.event == "command"
                and handler.options.command is not None
                and not handler.options.bot
            )
            self.assertEqual(command.aliases, ("commands", "cmds", "menu"))
            callback_declaration = next(
                handler for handler in declaration.handlers
                if handler.event == "bot_callback_query"
            )
            inline_declaration = next(
                handler for handler in declaration.handlers
                if handler.event == "bot_inline_query"
            )
            self.assertFalse(callback_declaration.options.public)
            self.assertFalse(inline_declaration.options.public)
            bot_help_command = next(
                handler for handler in declaration.handlers
                if handler.options.command is not None
                and handler.options.command.trigger == "help"
            )
            self.assertFalse(bot_help_command.options.public)
            keyboard = module._help_keyboard()
            self.assertEqual([len(row) for row in keyboard.inline_keyboard], [1])
            self.assertIn("Disabled", module.render_root())
            category_keyboard = module._help_keyboard("category:disabled")
            category_buttons = [button for row in category_keyboard.inline_keyboard[:-1] for button in row]
            self.assertEqual(category_buttons, [])
            self.assertEqual(category_keyboard.inline_keyboard[-1][0].callback_data, "help.root")
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
        main_module.public_plugin_status = {
            "todoist.py": {"state": "skipped", "note": "missing TODOIST_TOKEN <set>"},
            "alive.py": {"state": "loaded", "note": None},
        }
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
            self.assertEqual(module._total_commands(), 0)
            main_module.loaded_plugins = {}
            main_module.plugin_manager = Mock()
            main_module.plugin_manager.command_help.return_value = [
                {
                    "plugin": "ai",
                    "plugin_category": "AI & Tools",
                    "plugin_description": "Chat with AI.",
                    "trigger": "ai",
                    "prefix": ".",
                    "prefixes": (".",),
                    "signature": "<prompt>",
                    "options": (),
                    "description": "Chat with the configured AI model.",
                    "short_description": "Ask the AI a question.",
                    "aliases": (),
                    "examples": (".ai Explain this error",),
                    "example_notes": ("Ask the configured model directly.",),
                },
                {
                    "plugin": "alive",
                    "plugin_category": "Core",
                    "plugin_description": "Show userbot status.",
                    "trigger": "alive",
                    "prefix": ".",
                    "prefixes": (".",),
                    "options": (),
                    "description": "Show status.",
                    "aliases": (),
                    "examples": (".alive",),
                },
                {
                    "plugin": "ping",
                    "plugin_category": "Core",
                    "plugin_description": "Check userbot latency.",
                    "trigger": "ping",
                    "prefix": ".",
                    "prefixes": (".",),
                    "options": (),
                    "description": "Check latency.",
                    "aliases": (),
                    "examples": (".ping",),
                },
                {
                    "plugin": "sticker",
                    "plugin_category": "Stickers",
                    "plugin_description": "Manage stickers.",
                    "trigger": "kang",
                    "prefix": ".",
                    "prefixes": (".",),
                    "signature": "[pack] [emoji] [-a] [-t theme] [-d] [-s]",
                    "options": (),
                    "help_options": (
                        {"name": "append", "short": "-a", "help": "Append emoji."},
                    ),
                    "example_notes": ("Use the automatic pack.", "Select a pack."),
                    "description": "Add replied media to a pack.",
                    "short_description": "Short sticker plugin command.",
                    "aliases": (),
                    "examples": (".kang", ".kang <pack>"),
                },
                {
                    "plugin": "sticker",
                    "plugin_category": "Stickers",
                    "plugin_description": "Manage stickers.",
                    "trigger": "sticker",
                    "prefix": ".",
                    "prefixes": (".",),
                    "signature": "(reply to media)",
                    "options": (),
                    "description": "Detailed sticker help.",
                    "short_description": "Convert replied media into a sticker.",
                    "aliases": (),
                    "examples": (".sticker",),
                },
                {
                    "plugin": "todoist",
                    "plugin_category": "Productivity",
                    "plugin_description": "Todoist tasks.",
                    "trigger": "todo",
                    "prefix": ".",
                    "prefixes": (".",),
                    "options": (),
                    "description": "Create tasks.",
                    "aliases": (),
                    "examples": (),
                },
                {
                    "plugin": "todoist",
                    "plugin_category": "Productivity",
                    "plugin_description": "Todoist tasks.",
                    "trigger": "todo list",
                    "prefix": ".",
                    "prefixes": (".",),
                    "options": (),
                    "description": "List tasks.",
                    "aliases": (),
                    "examples": (),
                },
                {
                    "plugin": "web",
                    "plugin_category": "AI & Tools",
                    "plugin_description": "Search the web.",
                    "trigger": "web",
                    "prefix": ".",
                    "prefixes": (".",),
                    "options": (),
                    "description": "Search the web.",
                    "aliases": (),
                    "examples": (),
                },
                {
                    "plugin": "translate",
                    "plugin_category": "AI & Tools",
                    "plugin_description": "Translate text.",
                    "trigger": "translate",
                    "prefix": ".",
                    "prefixes": (".",),
                    "options": (),
                    "description": "Translate text.",
                    "aliases": (),
                    "examples": (),
                },
            ]
            main_module.loaded_plugins = {name: object() for name in ("ai", "alive", "ping", "sticker", "todoist", "web", "translate")}
            self.assertEqual(module._total_commands(), 7)
            ai_detail = module.render_category("ai_tools")
            self.assertIn("Choose a plugin:", ai_detail)
            self.assertIn("**Ai**", ai_detail)
            self.assertIn("**Translate**", ai_detail)
            self.assertIn("**Web**", ai_detail)
            ai_keyboard = module._help_keyboard("category:ai_tools")
            self.assertEqual(
                [button.text for row in ai_keyboard.inline_keyboard[:-1] for button in row],
                ["Ai", "Translate", "Web"],
            )
            self.assertEqual(
                ai_keyboard.inline_keyboard[-1][0].callback_data,
                "help.root",
            )
            self.assertEqual(
                [item["id"] for item in module._plugins_for_category("Core")],
                ["alive", "ping"],
            )
            root = module.render_root()
            self.assertIn("2 plugins", root)
            self.assertIn("**Core** — 2 plugins (2 commands)", root)
            self.assertIn("**AI & Tools** — 3 plugins (3 commands)", root)
            self.assertIn("**Stickers** — 1 plugin (2 commands)", root)
            self.assertNotIn("**Tools**", root)
            core_keyboard = module._help_keyboard("category:core")
            self.assertEqual(
                [button.callback_data for row in core_keyboard.inline_keyboard[:-1] for button in row],
                ["help.plugin:core:alive", "help.plugin:core:ping"],
            )
            sticker_plugin_help = module.render("sticker")
            self.assertIn("**Sticker**", sticker_plugin_help)
            self.assertEqual(module._help_page_action("sticker"), "plugin:stickers:sticker")
            self.assertEqual(module._help_page_action("ai"), "command:ai_tools:ai:ai")
            self.assertEqual(
                module._help_page_action("kang"), "command:stickers:sticker:kang"
            )
            ai_action = module._help_page_action("ai")
            self.assertEqual(ai_action, "command:ai_tools:ai:ai")
            ai_back_button = module._help_keyboard(ai_action).inline_keyboard[-1][0]
            self.assertEqual(ai_back_button.text, "⬅️ Back to Ai")
            self.assertEqual(ai_back_button.callback_data, "help.plugin:ai_tools:ai")
            ai_plugin_keyboard = module._help_keyboard("plugin:ai_tools:ai")
            ai_plugin_buttons = [
                button
                for row in ai_plugin_keyboard.inline_keyboard[:-1]
                for button in row
            ]
            self.assertEqual(
                [button.callback_data for button in ai_plugin_buttons],
                ["help.command:ai_tools:ai:ai"],
            )
            self.assertIn(".kang", sticker_plugin_help)
            self.assertIn(".sticker", sticker_plugin_help)
            sticker_plugin_keyboard = module._help_keyboard("plugin:stickers:sticker")
            sticker_plugin_buttons = [
                button
                for row in sticker_plugin_keyboard.inline_keyboard[:-1]
                for button in row
            ]
            self.assertEqual(
                [button.callback_data for button in sticker_plugin_buttons],
                [
                    "help.command:stickers:sticker:kang",
                    "help.command:stickers:sticker:sticker",
                ],
            )
            self.assertEqual(
                [button.text for button in sticker_plugin_buttons],
                ["1. .kang", "2. .sticker"],
            )
            self.assertEqual(
                sticker_plugin_keyboard.inline_keyboard[-1][0].text,
                "⬅️ Back to categories",
            )
            self.assertEqual(
                module._help_keyboard("command:stickers:sticker:kang").inline_keyboard[0][0].text,
                "⬅️ Back to Sticker",
            )
            sticker_category = module.render_category("stickers")
            self.assertIn(".kang", sticker_category)
            self.assertEqual(sticker_category.count(".kang"), 1)
            self.assertNotIn(".help_sticker", sticker_category)
            self.assertIn("\n\n", sticker_category)
            self.assertIn("Short sticker plugin command.", sticker_category)
            sticker_keyboard = module._help_keyboard("category:stickers")
            self.assertEqual(
                [button.callback_data for row in sticker_keyboard.inline_keyboard[:-1] for button in row],
                [
                    "help.command:stickers:sticker:kang",
                    "help.command:stickers:sticker:sticker",
                ],
            )
            detail = module.render_command_lookup("kang")
            self.assertIn("Add replied media to a pack.", detail)
            self.assertIn("Options:", detail)
            self.assertIn("• `-a` — Append emoji.", detail)
            self.assertIn("• `.kang` — Use the automatic pack.", detail)
            self.assertIn("• `.kang <pack>` — Select a pack.", detail)
            sticker_name_help = module.render("sticker")
            self.assertIn("**Sticker**", sticker_name_help)
            self.assertIn("Manage stickers.", sticker_name_help)
            self.assertIn("Convert replied media", sticker_name_help)
            self.assertNotIn("Detailed sticker help.", sticker_name_help)
            self.assertEqual(
                module._help_keyboard("command:stickers:sticker:kang")
                .inline_keyboard[0][0].callback_data,
                "help.plugin:stickers:sticker",
            )
            self.assertEqual(
                module._help_keyboard("command:core:alive:alive")
                .inline_keyboard[0][0].callback_data,
                "help.plugin:core:alive",
            )
            self.assertIn("Disabled", module.render_root())
            disabled_keyboard = module._help_keyboard("category:disabled")
            self.assertFalse(any(
                button.callback_data.endswith(":todoist")
                for row in disabled_keyboard.inline_keyboard for button in row
            ))
            report = module.render_plugins()
            self.assertIn("Loaded: 1", report)
            self.assertIn("Not loaded: 1", report)
            self.assertIn("Total commands available: 7", report)
            self.assertIn("<blockquote>1. <b>todoist.py</b>: missing TODOIST_TOKEN &lt;set&gt;</blockquote>", report)
            self.assertIn("Private plugins:</b>\nStatus unavailable.", report)
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
