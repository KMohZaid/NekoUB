import unittest

from core.plugins import Example, Option, Plugin, PluginRegistry, handlers as h
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
