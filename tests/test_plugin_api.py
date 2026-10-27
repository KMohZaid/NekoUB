import unittest
from unittest.mock import AsyncMock

from core.plugins.context import PluginContext, _split_html, resolve_parse_mode

from core.plugins import Example, Option, Plugin, PluginRegistry, handlers as h
from core.plugins.api import _wrap_handler
from core.plugins.registry import HandlerDeclaration
from core.plugins.errors import OptionParseError, parse_options
from pyrogram import filters as F
from pyrogram.enums import ParseMode


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
    def test_plugin_disabled_reason_defaults_to_none_and_can_be_overridden(self):
        class DisabledPlugin(Plugin):
            id = "disabled"

            def disabled_reason(self):
                return "missing API_TOKEN"

        self.assertIsNone(Plugin().disabled_reason())
        self.assertEqual(DisabledPlugin().disabled_reason(), "missing API_TOKEN")

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

    def test_options_accept_long_form_aliases(self):
        parsed = parse_options(
            ".kang --duplicate -show -t anime cat",
            (
                Option("duplicate", short="-d", aliases=("-duplicate",), action="store_true"),
                Option("show", short="-s", aliases=("-show", "--show", "-send", "--send"), action="store_true"),
                Option("theme", short="-t", type=str),
            ),
            "kang",
        )
        self.assertTrue(parsed.duplicate)
        self.assertTrue(parsed.show)
        self.assertEqual(parsed.theme, "anime")
        self.assertEqual(parsed.positional, ("cat",))

    def test_option_aliases_accept_long_single_dash_spellings(self):
        aliases = ("-s", "--show", "--send")
        option = Option("show", short="-s", aliases=("--show", "--send"), action="store_true")
        parsed = parse_options(".find --send", (option,), "find")
        self.assertTrue(parsed.show)
        self.assertEqual(aliases, (option.short, *option.aliases))

    def test_boolean_options_default_to_false(self):
        parsed = parse_options(
            ".packs 2",
            (Option("count", action="store_true"),),
            "packs",
        )
        self.assertFalse(parsed.count)
        self.assertEqual(parsed.positional, ("2",))

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

    def test_plugin_context_exposes_shared_application_config(self):
        shared_config = {"CMD_PREFIX": ".", "MAX_SPAM_COUNT": 20}
        context = PluginContext(
            client=None,
            update=object(),
            plugin=DemoPlugin(),
            config=shared_config,
        )
        self.assertIs(context.config, shared_config)
        self.assertEqual(context.get_config("CMD_PREFIX"), ".")
        self.assertEqual(context.get_config_typed("MAX_SPAM_COUNT", int), 20)

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

    def test_bot_command_rejects_non_owner(self):
        class BotHelpPlugin(Plugin):
            id = "bot_help"
            called = False

            @h.bot_command("help", public=False)
            async def help_command(self, bot, message, ctx, args):
                self.called = True

        import asyncio

        plugin = BotHelpPlugin()
        declaration = plugin.declarations()[0]
        owner_client = type(
            "OwnerClient",
            (),
            {"me": type("User", (), {"id": 100})()},
        )()
        bot_client = type("Bot", (), {"is_bot": True})()
        callback = _wrap_handler(
            plugin,
            declaration,
            user_client=owner_client,
            bot_client=bot_client,
        )
        update = type(
            "Message",
            (),
            {
                "from_user": type("User", (), {"id": 200})(),
                "answer": AsyncMock(),
            },
        )()
        with self.assertRaises(Exception) as raised:
            asyncio.run(callback(bot_client, update))
        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        self.assertFalse(plugin.called)
        update.answer.assert_awaited_once()

    def test_non_owner_bot_inline_query_is_rejected(self):
        class BotInlinePlugin(Plugin):
            id = "bot_inline"
            called = False

            @h.bot_inline_query(public=False)
            async def inline(self, bot, query, ctx):
                self.called = True

        import asyncio

        plugin = BotInlinePlugin()
        declaration = plugin.declarations()[0]
        owner_client = type(
            "OwnerClient",
            (),
            {"me": type("User", (), {"id": 100})()},
        )()
        bot_client = type("Bot", (), {"is_bot": True})()
        callback = _wrap_handler(
            plugin,
            declaration,
            user_client=owner_client,
            bot_client=bot_client,
        )
        query = type(
            "InlineQuery",
            (),
            {"from_user": type("User", (), {"id": 200})(), "answer": AsyncMock()},
        )()
        with self.assertRaises(Exception) as raised:
            asyncio.run(callback(bot_client, query))
        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        self.assertFalse(plugin.called)
        query.answer.assert_awaited_once()
        self.assertTrue(query.answer.await_args.kwargs["is_personal"])

    def test_bot_handler_accepts_configured_bot_before_startup_identity_is_set(self):
        class BotPlugin(Plugin):
            id = "bot_handler_before_start"
            called = False

            @h.bot_inline_query(public=True)
            async def inline(self, bot, query, ctx):
                self.called = True

        plugin = BotPlugin()
        declaration = plugin.declarations()[0]
        bot_client = type("Bot", (), {"is_bot": None, "bot_token": "token"})()
        callback = _wrap_handler(
            plugin,
            declaration,
            user_client=object(),
            bot_client=bot_client,
        )
        query = type("InlineQuery", (), {})()
        with self.assertRaises(Exception) as raised:
            import asyncio
            asyncio.run(callback(bot_client, query))
        self.assertEqual(type(raised.exception).__name__, "ContinuePropagation")
        self.assertTrue(plugin.called)

    def test_bot_handler_receives_bot_as_client_and_context_exposes_both(self):
        class BotPlugin(Plugin):
            id = "bot_handler"
            captured = None

            @h.bot_command("hello", public=True)
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

    def test_parse_mode_strings_resolve_to_kurigram_enums(self):
        for value, expected in (
            ("html", ParseMode.HTML),
            ("HTML", ParseMode.HTML),
            ("markdown", ParseMode.MARKDOWN),
            ("md", ParseMode.MARKDOWN),
            ("disabled", ParseMode.DISABLED),
        ):
            with self.subTest(value=value):
                self.assertIs(resolve_parse_mode(value), expected)
        self.assertIs(resolve_parse_mode(ParseMode.HTML), ParseMode.HTML)
        self.assertEqual(resolve_parse_mode("custom"), "custom")

    def test_context_edit_and_send_normalize_explicit_parse_modes(self):
        import asyncio

        context = PluginContext(client=object(), update=object(), plugin=object())
        message = type("Message", (), {"edit_text": AsyncMock()})()
        asyncio.run(context.edit("<b>hello</b>", message=message, parse_mode="html"))
        self.assertIs(message.edit_text.await_args.kwargs["parse_mode"], ParseMode.HTML)

        context.update = type("Update", (), {"chat": type("Chat", (), {"id": 1})()})()
        context.client = type("Client", (), {"send_message": AsyncMock()})()
        asyncio.run(context.send("hello", parse_mode="markdown"))
        self.assertIs(
            context.client.send_message.await_args.kwargs["parse_mode"],
            ParseMode.MARKDOWN,
        )

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

    def test_option_aliases_must_have_option_or_marker_prefix(self):
        with self.assertRaisesRegex(ValueError, "prefix"):
            Option("list", aliases=("list",))

    def test_aliases_cannot_conflict_with_other_option_names(self):
        with self.assertRaisesRegex(ValueError, "Duplicate option"):
            parse_options(
                ".find --show",
                (
                    Option("show", aliases=("--other",), action="store_true"),
                    Option("other", action="store_true"),
                ),
                "find",
            )

    def test_tts_schema_options_parse_aliases_and_leave_speech_tail_literal(self):
        from plugins.tts import TTS_OPTIONS, _parse_command

        self.assertEqual(
            _parse_command("--voice=heart --speed=0.9 Hello there -i stays spoken"),
            (False, "af_heart", 0.9, False, False, "Hello there -i stays spoken"),
        )
        self.assertEqual(
            _parse_command("-m Adam: hello\nGeorge: hi"),
            (True, "am_adam", 1.0, False, False, "Adam: hello\nGeorge: hi"),
        )


if __name__ == "__main__":
    unittest.main()
