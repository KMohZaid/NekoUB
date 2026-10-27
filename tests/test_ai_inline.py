import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from pyrogram.types import InlineQueryResultArticle, InputRichMessage, InputRichMessageContent

from plugins import ai


class RecentContextTests(unittest.IsolatedAsyncioTestCase):
    def test_ai_help_documents_context_count_option(self):
        declarations = ai.AIPlugin.ai_command.__nekoub_handlers__
        command_options = next(options.command for event, options in declarations if options.command)

        context_option = next(
            option for option in command_options.help_options if option.name == "context"
        )
        self.assertEqual(context_option.short, "-c")
        self.assertIn("--context", context_option.aliases)
        self.assertEqual(context_option.type, int)
        self.assertIn("last n text messages", context_option.help.lower())

    async def test_fetches_latest_text_and_caption_messages_with_sender_names(self):
        messages = [
            SimpleNamespace(id=12, text=None, caption=None, from_user=SimpleNamespace(first_name="Media")),
            SimpleNamespace(id=11, text="second", caption=None, from_user=SimpleNamespace(first_name="Sam")),
            SimpleNamespace(id=10, text=None, caption="first", from_user=SimpleNamespace(first_name="Lee")),
        ]
        async def history(**_kwargs):
            for item in messages:
                yield item

        client = Mock()
        client.search_messages = Mock(side_effect=lambda _chat, **kwargs: history(**kwargs))
        command = Mock(chat=Mock(id=7), id=13)

        result = await ai._recent_context(client, command, 2)

        self.assertEqual(result, "Lee: first\nSam: second")
        client.search_messages.assert_called_once_with(
            7, query="", max_id=13, limit=100
        )

    async def test_paginates_using_oldest_returned_message_id(self):
        pages = [
            [SimpleNamespace(id=12, text=None, caption=None), SimpleNamespace(id=11, text="new", caption=None, from_user=None)],
            [SimpleNamespace(id=10, text="old", caption=None, from_user=None)],
        ]

        async def history(**_kwargs):
            for item in pages.pop(0):
                yield item

        client = Mock()
        client.search_messages = Mock(side_effect=lambda _chat, **kwargs: history(**kwargs))
        command = Mock(chat=Mock(id=7), id=13)

        result = await ai._recent_context(client, command, 2)

        self.assertEqual(result, "Unknown: old\nUnknown: new")
        self.assertEqual(
            client.search_messages.call_args_list[1].kwargs["max_id"], 11
        )


class InlineResponseStagingTests(unittest.TestCase):
    def setUp(self):
        ai._inline_responses.clear()

    def tearDown(self):
        ai._inline_responses.clear()

    def test_staged_responses_have_unique_keys_and_are_consumed_once(self):
        first = ai._stage_inline_response("first answer")
        second = ai._stage_inline_response("second answer")

        self.assertNotEqual(first, second)
        self.assertEqual(ai._take_inline_response(first), "first answer")
        self.assertIsNone(ai._take_inline_response(first))
        self.assertEqual(ai._take_inline_response(second), "second answer")

    def test_expired_response_is_not_returned(self):
        with patch.object(ai.time, "monotonic", side_effect=[10.0, 311.0]):
            key = ai._stage_inline_response("expired answer")
            result = ai._take_inline_response(key)

        self.assertIsNone(result)

    def test_unknown_key_returns_none(self):
        self.assertIsNone(ai._take_inline_response("unknown"))


class InlineQueryAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ai._inline_responses.clear()

    async def asyncTearDown(self):
        ai._inline_responses.clear()

    async def test_valid_key_answers_with_one_kurigram_rich_article(self):
        key = ai._stage_inline_response("**Rich answer**")
        query = AsyncMock()

        await ai._answer_ai_inline_query(query, key)

        query.answer.assert_awaited_once()
        results = query.answer.await_args.args[0]
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertIsInstance(result, InlineQueryResultArticle)
        self.assertEqual(result.title, "AI response")
        self.assertIsInstance(result.input_message_content, InputRichMessageContent)
        self.assertIsInstance(result.input_message_content.rich_message, InputRichMessage)
        self.assertEqual(
            result.input_message_content.rich_message.markdown, "**Rich answer**"
        )
        self.assertEqual(query.answer.await_args.kwargs["cache_time"], 0)
        self.assertTrue(query.answer.await_args.kwargs["is_personal"])

    async def test_unknown_key_answers_with_empty_result_list(self):
        query = AsyncMock()

        await ai._answer_ai_inline_query(query, "unknown")

        self.assertEqual(query.answer.await_args.args[0], [])

    async def test_kurigram_failure_is_propagated(self):
        query = AsyncMock()
        query.answer.side_effect = RuntimeError("Kurigram failure")

        with self.assertRaisesRegex(RuntimeError, "Kurigram failure"):
            await ai._answer_ai_inline_query(query, "unknown")


class AIProviderFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_numbered_provider_settings_load_in_numeric_order(self):
        providers = ai.config._load_ai_providers(
            {
                "AI_API_URL_2": "https://groq.example/openai/v1",
                "AI_API_KEY_2": "groq-key",
                "AI_MODEL_2": "groq-model",
                "AI_API_URL_1": "https://custom.example/v1",
                "AI_API_KEY_1": "custom-key",
                "AI_MODEL_1": "custom-model",
            }
        )

        self.assertEqual(
            providers,
            [
                {
                    "url": "https://custom.example/v1",
                    "key": "custom-key",
                    "model": "custom-model",
                },
                {
                    "url": "https://groq.example/openai/v1",
                    "key": "groq-key",
                    "model": "groq-model",
                },
            ],
        )

    async def test_numbered_provider_settings_skip_incomplete_slots(self):
        providers = ai.config._load_ai_providers(
            {
                "AI_API_KEY_1": "key-without-provider-is-ignored",
            "AI_MODEL_1": "incomplete-model",
                "AI_API_URL_2": "https://groq.example/openai/v1",
                "AI_API_KEY_2": "groq-key",
                "AI_MODEL_2": "groq-model",
            }
        )

        self.assertEqual(
            providers,
            [{
                "url": "https://groq.example/openai/v1",
                "key": "groq-key",
                "model": "groq-model",
            }],
        )

    async def test_first_numbered_provider_is_primary_and_second_is_fallback(self):
        custom_response = Mock()
        custom_response.status_code = 429
        custom_response.json.return_value = {"error": {"message": "busy"}}
        groq_response = Mock()
        groq_response.status_code = 200
        groq_response.json.return_value = {
            "choices": [{"message": {"content": "Groq answer"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }
        client = AsyncMock()
        client.post.side_effect = [custom_response, groq_response]
        async_client = AsyncMock()
        async_client.__aenter__.return_value = client

        with (
            patch.object(ai.httpx, "AsyncClient", return_value=async_client),
            patch.object(
                ai.config,
                "AI_PROVIDERS",
                [
                    {"url": "https://custom.example/v1", "key": "custom-key", "model": "custom-model"},
                    {"url": "https://groq.example/openai/v1", "key": "groq-key", "model": "groq-model"},
                ],
            ),
        ):
            result = await ai._complete([{"role": "user", "content": "Hi"}], 1)

        self.assertEqual(result, ("Groq answer", None, {"prompt_tokens": 12, "completion_tokens": 3}))
        self.assertEqual(client.post.await_count, 2)
        first, second = (call.kwargs for call in client.post.await_args_list)
        self.assertEqual(client.post.await_args_list[0].args[0], "https://custom.example/v1/chat/completions")
        self.assertEqual(first["json"]["model"], "custom-model")
        self.assertEqual(first["headers"]["Authorization"], "Bearer custom-key")
        self.assertEqual(client.post.await_args_list[1].args[0], "https://groq.example/openai/v1/chat/completions")
        self.assertEqual(second["json"]["model"], "groq-model")
        self.assertEqual(second["headers"]["Authorization"], "Bearer groq-key")

    async def test_custom_endpoint_success_does_not_call_groq(self):
        custom_response = Mock()
        custom_response.status_code = 200
        custom_response.json.return_value = {
            "choices": [{"message": {"content": "OmniRoute answer"}}],
        }
        client = AsyncMock()
        client.post.return_value = custom_response
        async_client = AsyncMock()
        async_client.__aenter__.return_value = client

        with (
            patch.object(ai.httpx, "AsyncClient", return_value=async_client),
            patch.object(
                ai.config,
                "AI_PROVIDERS",
                [
                    {"url": "https://custom.example/v1", "key": "custom-key", "model": "custom-model"},
                    {"url": "https://groq.example/openai/v1", "key": "groq-key", "model": "groq-model"},
                ],
            ),
        ):
            result = await ai._complete([{"role": "user", "content": "Hi"}], 1)

        self.assertEqual(result, ("OmniRoute answer", None, None))
        client.post.assert_awaited_once()
        self.assertEqual(client.post.await_args.args[0], "https://custom.example/v1/chat/completions")


class AIStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_option_metadata_and_combined_context_parse(self):
        declarations = ai.AIPlugin.ai_command.__nekoub_handlers__
        command = next(options.command for event, options in declarations if options.command)
        option = next(item for item in command.help_options if item.name == "stream")

        self.assertEqual(option.short, "-s")
        self.assertIn("--stream", option.aliases)
        self.assertIn("BotFather mini app", option.help)
        parsed_first = ai.parse_options(".ai -c 5 -s hello", command.options, "ai")
        parsed_second = ai.parse_options(".ai -s -c 5 hello", command.options, "ai")
        self.assertEqual(parsed_first.context, 5)
        self.assertTrue(parsed_first.stream)
        self.assertEqual(parsed_first.positional, ("hello",))
        self.assertEqual(parsed_second.context, 5)
        self.assertTrue(parsed_second.stream)
        self.assertEqual(parsed_second.positional, ("hello",))
        with self.assertRaises(ValueError):
            ai._validate_context_count(0)

    async def test_stream_request_claim_checks_originating_sender(self):
        request = ai.PendingAIRequest(chat_id=42, messages=[], prompt="hi", sender_id=100)
        request_id = await ai._register_stream_request(request)

        self.assertIsNone(await ai._claim_stream_request(request_id, 42, sender_id=200))
        self.assertIs(await ai._claim_stream_request(request_id, 42, sender_id=100), request)

    async def test_stream_request_can_only_be_claimed_once_for_source_chat(self):
        request = ai.PendingAIRequest(chat_id=42, messages=[], prompt="hi")
        request_id = await ai._register_stream_request(request)

        self.assertIsNone(await ai._claim_stream_request(request_id, 7))
        self.assertIs(await ai._claim_stream_request(request_id, 42), request)
        self.assertIsNone(await ai._claim_stream_request(request_id, 42))
        self.assertIsNone(await ai._claim_stream_request("unknown", 42))

    async def test_guest_handler_ignores_wrong_bot_mention(self):
        request = ai.PendingAIRequest(chat_id=42, messages=[], prompt="hi")
        request_id = await ai._register_stream_request(request)
        bot = Mock()
        bot.me = SimpleNamespace(username="assistantbot")
        message = SimpleNamespace(
            text=f"@differentbot {request_id}",
            chat=SimpleNamespace(id=42),
        )

        await ai.AIPlugin().guest_message(bot, message, SimpleNamespace(bot=bot))

        self.assertEqual(request.state, "pending")
        bot.answer_guest_query.assert_not_called()

    async def test_guest_claim_notifies_waiting_command(self):
        loop = __import__("asyncio").get_running_loop()
        request = ai.PendingAIRequest(chat_id=42, messages=[], prompt="hi", future=loop.create_future())
        request_id = await ai._register_stream_request(request)

        claimed = await ai._claim_stream_request(request_id, 42)

        self.assertIs(claimed, request)
        self.assertTrue(request.future.done())
        self.assertIs(request.future.result(), request)

    async def test_guest_stream_final_edit_includes_token_usage_footer(self):
        request = ai.PendingAIRequest(
            chat_id=42,
            messages=[{"role": "user", "content": "hi"}],
            prompt="hi",
        )
        request_id = await ai._register_stream_request(request)
        bot = Mock()
        bot.me = SimpleNamespace(username="assistantbot")
        bot.answer_guest_query = AsyncMock(return_value=SimpleNamespace(inline_message_id="inline-id"))
        bot.edit_inline_text = AsyncMock()
        message = SimpleNamespace(
            text=f"@assistantbot {request_id}",
            chat=SimpleNamespace(id=42),
            guest_query_id="guest-query-id",
        )

        async def stream(_messages, _chat_id, on_chunk):
            await on_chunk("Hello")
            return "Hello", None, {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
            }

        with patch.object(ai, "_stream_complete", side_effect=stream):
            await ai.AIPlugin().guest_message(bot, message, SimpleNamespace(bot=bot))

        final = bot.edit_inline_text.await_args.kwargs["rich_message"].markdown
        self.assertIn("Hello", final)
        self.assertIn("Input tokens: 10", final)
        self.assertIn("Output tokens: 2", final)
        self.assertIn("Total: 12", final)
        self.assertIn("Prompt tokens: ~1 (history ~0 + current ~1)", final)
        self.assertRegex(final, r"Time: [0-9]+\.[0-9]s")

    async def test_guest_trigger_answers_query_then_edits_inline_response(self):
        request = ai.PendingAIRequest(chat_id=42, messages=[], prompt="hi")
        request_id = await ai._register_stream_request(request)
        bot = Mock()
        bot.me = SimpleNamespace(username="assistantbot")
        bot.answer_guest_query = AsyncMock(return_value=SimpleNamespace(inline_message_id="inline-id"))
        bot.edit_inline_text = AsyncMock()
        context = SimpleNamespace(bot=bot)
        message = SimpleNamespace(
            text=f"@assistantbot {request_id}",
            chat=SimpleNamespace(id=42),
            guest_query_id="guest-query-id",
        )
        plugin = ai.AIPlugin()

        async def stream(_messages, _chat_id, on_chunk):
            await on_chunk("Hello")
            return "Hello", None, None

        with patch.object(ai, "_stream_complete", side_effect=stream):
            await plugin.guest_message(bot, message, context)

        bot.answer_guest_query.assert_awaited_once()
        self.assertEqual(bot.answer_guest_query.await_args.args[0], message.guest_query_id)
        self.assertEqual(
            bot.answer_guest_query.await_args.kwargs["result"].input_message_content.rich_message.markdown,
            "🤖 Thinking…",
        )
        self.assertGreater(bot.edit_inline_text.await_count, 0)
        self.assertTrue(all(call.args[0] == "inline-id" for call in bot.edit_inline_text.await_args_list))
        self.assertTrue(
            bot.edit_inline_text.await_args.kwargs["rich_message"].markdown.startswith("Hello")
        )
        self.assertEqual(request.state, "completed")
        self.assertEqual(ai._history[42][-2:], [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello"},
        ])

    async def test_initial_draft_failure_does_not_abort_stream_or_final_message(self):
        request = ai.PendingAIRequest(chat_id=42, messages=[], prompt="hi")
        request_id = await ai._register_stream_request(request)
        bot = Mock()
        bot.me = SimpleNamespace(username="assistantbot")
        bot.answer_guest_query = AsyncMock(side_effect=RuntimeError("guest answer unavailable"))
        bot.edit_inline_text = AsyncMock()
        bot.send_rich_message = AsyncMock()
        message = SimpleNamespace(
            text=f"@assistantbot {request_id}",
            chat=SimpleNamespace(id=42),
            guest_query_id="guest-query-id",
        )

        async def stream(_messages, _chat_id, _on_chunk):
            return "answer", None, None

        with patch.object(ai, "_stream_complete", side_effect=stream):
            await ai.AIPlugin().guest_message(bot, message, SimpleNamespace(bot=bot))

        bot.answer_guest_query.assert_awaited_once()
        bot.edit_inline_text.assert_not_awaited()
        bot.send_rich_message.assert_awaited_once()
        self.assertTrue(
            bot.send_rich_message.await_args.kwargs["rich_message"].markdown.startswith("answer")
        )
        self.assertEqual(request.state, "completed")

    async def test_partial_guest_stream_error_persists_collapsed_error_block(self):
        request = ai.PendingAIRequest(chat_id=42, messages=[], prompt="hi")
        request_id = await ai._register_stream_request(request)
        bot = Mock()
        bot.me = SimpleNamespace(username="assistantbot")
        bot.answer_guest_query = AsyncMock(return_value=SimpleNamespace(inline_message_id="inline-error"))
        bot.edit_inline_text = AsyncMock()
        message = SimpleNamespace(
            text=f"@assistantbot {request_id}",
            chat=SimpleNamespace(id=42),
            guest_query_id="guest-query-id",
        )

        async def failed_stream(_messages, _chat_id, on_chunk):
            await on_chunk("partial")
            return "partial", "provider broke", None

        with (
            patch.object(ai, "_stream_complete", side_effect=failed_stream),
            patch.object(ai, "_prepare_rich_answer", side_effect=lambda text: text),
        ):
            await ai.AIPlugin().guest_message(bot, message, SimpleNamespace(bot=bot))

        final = bot.edit_inline_text.await_args.kwargs["rich_message"].markdown
        self.assertIn("partial", final)
        self.assertIn("<details><summary>❌ Error occurred</summary>", final)
        self.assertIn("provider broke", final)

    async def test_fallback_and_guest_claim_are_mutually_exclusive(self):
        request = ai.PendingAIRequest(chat_id=42, messages=[], prompt="hi")
        request_id = await ai._register_stream_request(request)

        bot_claim = await ai._claim_stream_request(request_id, 42)
        fallback_claim = await ai._claim_stream_request(request_id, 42, owner="fallback")

        self.assertIs(bot_claim, request)
        self.assertIsNone(fallback_claim)

    async def test_deferred_stream_response_uses_full_response_completion(self):
        client = _StreamingHTTPClient([], status_code=202)
        async_client = _StreamingAsyncClient(client)
        chunks = []

        async def on_chunk(chunk):
            chunks.append(chunk)

        with (
            patch.object(ai.httpx, "AsyncClient", return_value=async_client),
            patch.object(ai.config, "AI_PROVIDERS", [
                {"url": "https://one.example/v1", "key": "key", "model": "m"}
            ]),
            patch.object(ai, "_complete", new=AsyncMock(return_value=("deferred answer", None, None))) as complete,
        ):
            result = await ai._stream_complete([], 1, on_chunk)

        complete.assert_awaited_once_with([], 1)
        self.assertEqual(result, ("deferred answer", None, None))
        self.assertEqual(chunks, ["deferred answer"])

    async def test_ignores_non_object_sse_json_frames(self):
        client = _StreamingHTTPClient([
            'data: ["invalid shape"]\\n\\n',
            'data: {"choices":[{"delta":{"content":"ok"}}]}\\n\\n',
        ])
        async_client = _StreamingAsyncClient(client)

        async def on_chunk(_chunk):
            return None

        with (
            patch.object(ai.httpx, "AsyncClient", return_value=async_client),
            patch.object(ai.config, "AI_PROVIDERS", [
                {"url": "https://one.example/v1", "key": "key", "model": "m"}
            ]),
        ):
            result = await ai._stream_complete([], 1, on_chunk)

        self.assertEqual(result, ("ok", None, None))

    async def test_parses_sse_deltas_and_usage(self):
        frames = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
            'data: {"choices":[],"usage":{"prompt_tokens":4}}\n\n',
            "data: [DONE]\n\n",
        ]
        chunks = []

        async def on_chunk(chunk):
            chunks.append(chunk)

        client = _StreamingHTTPClient(frames)
        async_client = _StreamingAsyncClient(client)

        with (
            patch.object(ai.httpx, "AsyncClient", return_value=async_client),
            patch.object(ai.config, "AI_PROVIDERS", [
                {"url": "https://one.example/v1", "key": "key", "model": "m"}
            ]),
        ):
            result = await ai._stream_complete(
                [{"role": "user", "content": "Hi"}], 1, on_chunk
            )

        self.assertEqual(result, ("Hello", None, {"prompt_tokens": 4}))
        self.assertEqual(chunks, ["Hel", "lo"])
        self.assertTrue(client.request["json"]["stream"])

    async def test_falls_back_to_next_provider_before_any_content(self):
        first = _StreamingHTTPClient([], status_code=429, text="busy")
        second = _StreamingHTTPClient(['data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'])
        clients = [first, second]
        async_client = _StreamingAsyncClient(clients)
        chunks = []

        async def on_chunk(chunk):
            chunks.append(chunk)

        with (
            patch.object(ai.httpx, "AsyncClient", return_value=async_client),
            patch.object(ai.config, "AI_PROVIDERS", [
                {"url": "https://one.example/v1", "key": "k1", "model": "m1"},
                {"url": "https://two.example/v1", "key": "k2", "model": "m2"},
            ]),
        ):
            result = await ai._stream_complete([], 1, on_chunk)

        self.assertEqual(result, ("ok", None, None))
        self.assertEqual(async_client.stream_calls, 2)

    async def test_partial_stream_error_is_returned_without_provider_retry(self):
        first = _StreamingHTTPClient(
            ['data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'],
            stream_error=RuntimeError("connection lost"),
        )
        async_client = _StreamingAsyncClient([first])
        chunks = []

        async def on_chunk(chunk):
            chunks.append(chunk)

        with (
            patch.object(ai.httpx, "AsyncClient", return_value=async_client),
            patch.object(ai.config, "AI_PROVIDERS", [
                {"url": "https://one.example/v1", "key": "k1", "model": "m1"},
                {"url": "https://two.example/v1", "key": "k2", "model": "m2"},
            ]),
        ):
            result = await ai._stream_complete([], 1, on_chunk)

        self.assertEqual(result[0], "partial")
        self.assertIn("connection lost", result[1])
        self.assertEqual(async_client.stream_calls, 1)
        self.assertEqual(chunks, ["partial"])


class _StreamingAsyncClient:
    def __init__(self, clients):
        self.clients = clients if isinstance(clients, list) else [clients]
        self.stream_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, *args, **kwargs):
        self.stream_calls += 1
        return self.clients.pop(0)(*args, **kwargs)


class _StreamingHTTPClient:
    def __init__(self, lines, status_code=200, text="", stream_error=None):
        self.lines = lines
        self.status_code = status_code
        self.text = text
        self.stream_error = stream_error
        self.request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def __call__(self, *_args, **kwargs):
        self.request = kwargs
        return self

    async def aiter_lines(self):
        for line in self.lines:
            yield line.rstrip("\\n")
        if self.stream_error:
            raise self.stream_error

    def json(self):
        return {"error": {"message": self.text}}


class RichAnswerPreparationTests(unittest.TestCase):
    def test_preserves_rich_markdown_and_does_not_truncate_at_four_thousand(self):
        text = "**bold**\n\n[Open](https://example.com)\n\n" + "x" * 4100

        prepared = ai._prepare_rich_answer(text)

        self.assertEqual(prepared, text)
        self.assertGreater(len(prepared), 4000)

    def test_shortens_content_over_telegram_limit_by_utf8_length(self):
        prepared = ai._prepare_rich_answer("🙂" * 9000)

        self.assertLessEqual(len(prepared.encode("utf-8")), 32768)
        self.assertIn("[Response shortened to fit Telegram's rich-message limit.]", prepared)

    def test_collapses_continuation_after_visible_text_threshold_at_block_boundary(self):
        intro = "A" * 1300
        next_block = "B" * 1300
        continuation = "C" * 900
        answer = f"{intro}\n\n{next_block}\n\n{continuation}"

        prepared = ai._collapse_long_answer(answer, threshold=2500)

        self.assertTrue(prepared.startswith(f"{intro}\n\n{next_block}\n\n"))
        self.assertIn("<details><summary>Show more</summary>", prepared)
        self.assertIn(continuation, prepared)
        self.assertTrue(prepared.endswith("</details>"))

    def test_keeps_following_table_visible_when_threshold_crosses_before_it(self):
        intro = "A" * 2600
        table = (
            "| Part | Details |\n"
            "| --- | --- |\n"
            "| Games | " + "B" * 100 + " |\n"
            "| Anime | " + "C" * 100 + " |"
        )
        continuation = "More details."
        answer = f"{intro}\n\n{table}\n\n{continuation}"

        prepared = ai._collapse_long_answer(answer, threshold=2500)

        visible, collapsed = prepared.split(
            "<details><summary>Show more</summary>", maxsplit=1
        )
        self.assertIn(table, visible)
        self.assertIn(continuation, collapsed)

    def test_collapses_before_third_numbered_section_without_character_threshold(self):
        answer = (
            "Python in depth: build a bot 🐍\n\n"
            "Introduction.\n\n"
            "1. Set up Python\n\nInstall it.\n\n"
            "2. Python’s basic building blocks\n\nLearn variables.\n\n"
            "3. Build the bot\n\nWrite code."
        )

        prepared = ai._collapse_long_answer(answer, threshold=100000)

        visible, collapsed = prepared.split(
            "<details><summary>Show more</summary>", maxsplit=1
        )
        self.assertIn("2. Python’s basic building blocks", visible)
        self.assertTrue(collapsed.lstrip().startswith("3. Build the bot"))

    def test_visible_length_ignores_markdown_and_link_url_syntax(self):
        self.assertEqual(
            ai._visible_rich_text_length("**Bold** [Pokémon](https://example.com)"),
            len("Bold Pokémon"),
        )

    def test_does_not_collapse_short_answer(self):
        answer = "Short **answer** with [link](https://example.com)."
        self.assertEqual(ai._collapse_long_answer(answer, threshold=2500), answer)

    def test_rejects_empty_answer(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            ai._prepare_rich_answer("  \n")

    def test_token_usage_footer_includes_cached_input_tokens(self):
        self.assertEqual(
            ai._append_token_usage(
                "Answer",
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "prompt_tokens_details": {"cached_tokens": 64},
                },
            ),
            "Answer\n\n---\n\nInput tokens: 100 · Output tokens: 20\n"
            "Cached tokens: 64\n"
            "Total: 120",
        )

    def test_prompt_estimate_separates_history_and_current_prompt(self):
        self.assertEqual(
            ai._estimate_prompt_tokens(
                [
                    {"role": "system", "content": "s" * 40},
                    {"role": "user", "content": "p" * 20},
                ]
            ),
            (10, 5),
        )

    def test_token_usage_footer_shows_estimated_history_and_current_prompt(self):
        self.assertEqual(
            ai._append_token_usage(
                "Answer",
                {"prompt_tokens": 100, "completion_tokens": 20},
                elapsed_seconds=1.2,
                prompt_estimate=(10, 5),
            ),
            "Answer\n\n---\n\nInput tokens: 100 · Output tokens: 20\n"
            "Prompt tokens: ~15 (history ~10 + current ~5)\n"
            "Total: 120 · Time: 1.2s",
        )

    def test_token_usage_footer_formats_counts_and_includes_duration(self):
        self.assertEqual(
            ai._append_token_usage(
                "Answer",
                {"prompt_tokens": 1864, "completion_tokens": 100398000},
                elapsed_seconds=2.4,
            ),
            "Answer\n\n---\n\nInput tokens: 1,864 (1.9k) · Output tokens: 100,398,000 (100.4M)\n"
            "Total: 100,399,864 (100.4M) · Time: 2.4s",
        )

    def test_missing_token_usage_still_shows_elapsed_time(self):
        self.assertEqual(
            ai._append_token_usage("Answer", None, elapsed_seconds=1.2),
            "Answer\n\n---\n\nTime: 1.2s",
        )

    def test_active_prompt_requires_complete_markdown_table_syntax(self):
        self.assertIn("header row", ai.RICH_MARKDOWN_DIRECTIVE)
        self.assertIn("separator row", ai.RICH_MARKDOWN_DIRECTIVE)

    def test_active_prompt_explains_url_buttons_and_spoilers_explicitly(self):
        directive = ai.RICH_MARKDOWN_DIRECTIVE
        self.assertIn('<tg-button type="url"', directive)
        self.assertIn("A Markdown [label](url) is only an inline text link, not a button", directive)
        self.assertIn("never put spoiler content or its formatting inside inline code or a fenced code block", directive)
        self.assertIn("Click on spoiler tag to reveal them!", directive)
        self.assertIn("Do not explain spoiler syntax or show its delimiters", directive)

    def test_active_prompt_is_the_original_rich_markdown_directive(self):
        self.assertTrue(ai.RICH_MARKDOWN_DIRECTIVE.startswith(
            "Write answers using Telegram Rich Markdown, preserving its syntax rather "
            "than flattening it."
        ))
        self.assertNotIn("friendly, natural, and conversational", ai.RICH_MARKDOWN_DIRECTIVE)
        self.assertIs(ai.PLAIN_TEXT_DIRECTIVE, ai.RICH_MARKDOWN_DIRECTIVE)

    def test_expanded_prompt_is_retained_but_not_active(self):
        self.assertIn("friendly, natural, and conversational", ai.DEPRECATED_RICH_MARKDOWN_DIRECTIVE)
        self.assertIsNot(ai.DEPRECATED_RICH_MARKDOWN_DIRECTIVE, ai.RICH_MARKDOWN_DIRECTIVE)
        self.assertNotIn("friendly, natural, and conversational", ai.PLAIN_TEXT_DIRECTIVE)

    def test_usage_footer_does_not_expose_model_name(self):
        self.assertNotIn(
            "Model:",
            ai._append_token_usage("Answer", {"prompt_tokens": 10, "completion_tokens": 2}),
        )

    def test_expanded_prompt_encourages_native_syntax(self):
        for syntax in (
            "<details open>", "table", "$$", "```math", '<tg-button type="url"',
            'style="success"', "blockquote", "footnote", "spoiler", "underline",
            "superscript", "subscript", "do not claim highlighting is guaranteed",
            "Prefer URL buttons", "supported language tag", "libprisma",
            "For answers longer than about 2,500 visible characters",
            "Count visible text rather than Markdown syntax or link URLs",
            '<summary>Show more</summary>',
        ):
            with self.subTest(syntax=syntax):
                self.assertIn(syntax, ai.DEPRECATED_RICH_MARKDOWN_DIRECTIVE)
