import unittest

from core.plugins.context import _split_html

from core.plugins import Example, Option, Plugin, PluginRegistry, handlers as h
from core.plugins.api import _wrap_handler
from core.plugins.registry import HandlerDeclaration
from core.plugins.errors import OptionParseError, parse_options
from pyrogram import filters as F


class DemoPlugin(Plugin):
    id = "demo"
    category = "Examples"

    @h.edited_message(filters=F.text)
    @h.message(filters=F.text)
    async def on_text(self, client, message, ctx, *args, **kwargs):
        return None

    @h.command(
        "find",
        aliases=("search",),
        description="Find something.",
        examples=(Example("{cmd} cats"), Example("{prefix}{cmd} dogs")),
        options=(Option("limit", short="-n", type=int, default=5),),
    )
    async def find(self, client, message, ctx, args, *handler_args, **handler_kwargs):
        return args


class PluginApiTests(unittest.TestCase):
    def test_decorators_create_inert_declarations_and_stack_events(self):
        plugin = DemoPlugin()
        declarations = plugin.declarations()
        self.assertEqual({item.event for item in declarations}, {"message", "edited_message", "command"})
        self.assertEqual(sum(item.method_name == "on_text" for item in declarations), 2)
        self.assertEqual(declarations[0].plugin_id, "demo")

    def test_registry_validates_and_tracks_plugin_metadata(self):
        registry = PluginRegistry()
        declaration = registry.register(DemoPlugin())
        self.assertEqual(declaration.plugin_id, "demo")
        with self.assertRaisesRegex(ValueError, "Duplicate plugin id"):
            registry.register(DemoPlugin())
        self.assertEqual(registry.unregister("demo"), declaration)

    def test_example_placeholders_use_configured_prefix_and_command(self):
        self.assertEqual(Example("{cmd} cats").render("find", "."), ".find cats")
        self.assertEqual(Example("{prefix}{cmd} cats").render("find", "!"), "!!find cats")

    def test_options_parse_typed_values_and_required_errors(self):
        parsed = parse_options(
            ".find --source web -n 12 cats",
            (Option("source", type=str, required=True), Option("limit", short="-n", type=int, default=5)),
            "find",
        )
        self.assertEqual(parsed.source, "web")
        self.assertEqual(parsed.limit, 12)
        self.assertEqual(parsed.positional, ("cats",))

    def test_options_report_unknown_invalid_and_missing_values(self):
        schema = (Option("count", short="-c", type=int, required=True),)
        for text, expected in (
            (".find --other x", "Unknown option"),
            (".find --count nope", "valid int"),
            (".find", "Missing required options"),
        ):
            with self.subTest(text=text), self.assertRaises(OptionParseError) as raised:
                parse_options(text, schema, "find")
            self.assertIn(expected, str(raised.exception))

    def test_command_without_options_receives_empty_parsed_options(self):
        class NoOptionsPlugin(Plugin):
            id = "no_options"
            parsed = None

            @h.command("ping")
            async def ping(self, client, update, ctx, args):
                self.parsed = args

        plugin = NoOptionsPlugin()
        declaration = plugin.declarations()[0]
        callback = _wrap_handler(plugin, declaration)
        update = type("Message", (), {"text": ".ping"})()
        with self.assertRaises(Exception) as raised:
            import asyncio

            asyncio.run(callback(object(), update))
        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        self.assertEqual(plugin.parsed.values, {})
        self.assertEqual(plugin.parsed.positional, ())

    def test_no_schema_command_keeps_raw_argument_tail(self):
        class RawArgsPlugin(Plugin):
            id = "raw_args"
            parsed = None

            @h.command("exec")
            async def execute(self, client, update, ctx, args):
                self.parsed = args

        plugin = RawArgsPlugin()
        declaration = plugin.declarations()[0]
        callback = _wrap_handler(plugin, declaration)
        update = type("Message", (), {"text": ".exec echo hello world"})()
        with self.assertRaises(Exception):
            import asyncio

            asyncio.run(callback(object(), update))
        self.assertEqual(plugin.parsed.positional, ("echo hello world",))

    def test_bot_handler_receives_bot_as_client_and_context_exposes_both(self):
        class BotPlugin(Plugin):
            id = "bot_handler"
            captured = None

            @h.bot_command("hello")
            async def hello(self, bot, update, ctx, args):
                self.captured = (bot, update, ctx, args)

        plugin = BotPlugin()
        declaration = plugin.declarations()[0]
        user_client = object()
        bot_client = type("Bot", (), {"is_bot": True})()
        callback = _wrap_handler(
            plugin,
            declaration,
            user_client=user_client,
            bot_client=bot_client,
        )
        update = type("Message", (), {"text": "/hello"})()
        with self.assertRaises(Exception) as raised:
            import asyncio
            asyncio.run(callback(bot_client, update))
        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        actual_bot, actual_update, context, args = plugin.captured
        self.assertIs(actual_bot, bot_client)
        self.assertIs(actual_update, update)
        self.assertIs(context.client, user_client)
        self.assertIs(context.bot, bot_client)
        self.assertEqual(args.positional, ())

    def test_managed_handler_receives_context_update_args_and_continues(self):
        class HandlerPlugin(Plugin):
            id = "handler"
            captured = None

            @h.message()
            async def callback(self, client, update, ctx, *args, **kwargs):
                self.captured = (client, update, ctx, args, kwargs)

        plugin = HandlerPlugin()
        declaration = plugin.declarations()[0]
        callback = _wrap_handler(plugin, declaration, config={"flag": "yes"})
        client, update = object(), object()
        with self.assertRaises(Exception) as raised:
            import asyncio
            asyncio.run(callback(client, update, "extra", key="value"))
        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        actual_client, actual_update, context, args, kwargs = plugin.captured
        self.assertIs(actual_client, client)
        self.assertIs(actual_update, update)
        self.assertEqual(args, ("extra",))
        self.assertEqual(kwargs, {"key": "value"})
        self.assertEqual(context.config["flag"], "yes")

    def test_html_splitter_counts_utf16_and_keeps_entities_whole(self):
        chunks = _split_html("<b>😀😀 &amp; z</b>", 3)
        self.assertEqual(chunks, ["<b>😀</b>", "<b>😀 </b>", "<b>&amp; z</b>"])

    def test_html_splitter_closes_and_reopens_active_tags(self):
        chunks = _split_html("<b>abcd efgh</b>", 5)
        self.assertEqual(chunks, ["<b>abcd </b>", "<b>efgh</b>"])

    def test_options_strip_aliases_and_configured_prefixes(self):
        parsed = parse_options(
            "!search --limit 3 cats", (Option("limit", type=int),),
            ("find", "search"), prefixes=("!", "."),
        )
        self.assertEqual(parsed.limit, 3)
        self.assertEqual(parsed.positional, ("cats",))

    def test_options_support_clusters_and_end_of_options(self):
        schema = (
            Option("append", short="-a", action="store_true"),
            Option("show", short="-s", action="store_true"),
        )
        parsed = parse_options(".find -as -- -literal", schema, "find")
        self.assertTrue(parsed.append)
        self.assertTrue(parsed.show)
        self.assertEqual(parsed.positional, ("-literal",))


if __name__ == "__main__":
    unittest.main()
