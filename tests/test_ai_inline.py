import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from pyrogram.types import InlineQueryResultArticle, InputRichMessage, InputRichMessageContent

from plugins import ai


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

    def test_collapses_before_third_main_heading(self):
        answer = (
            "# Overview\n\nIntroduction.\n\n"
            "## First section\n\nFirst details.\n\n"
            "## Second section\n\nSecond details.\n\n"
            "## Third section\n\nThird details.\n\n"
            "## Fourth section\n\nMore details."
        )

        prepared = ai._collapse_long_answer(answer, threshold=40)

        self.assertIn("## Second section", prepared.split("<details>", 1)[0])
        self.assertTrue(
            prepared.split("<details><summary>Show more</summary>", 1)[1]
            .lstrip()
            .startswith("## Third section")
        )

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

    def test_detailed_requests_prioritize_depth_and_conversational_rich_format(self):
        for instruction in (
            "When the user asks for a detailed answer, prioritize depth over the default brevity",
            "Keep the tone conversational and enjoyable",
            "Use clean Rich Markdown sections",
            "large code blocks in relevant collapsed sections",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, ai.RICH_MARKDOWN_DIRECTIVE)

    def test_prompt_uses_casual_tone_and_relevant_emojis(self):
        for instruction in (
            "Use at least one relevant emoji in a substantial answer",
            "Use emoji sparingly, not on every line",
            "Keep technical and coding explanations casual",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, ai.RICH_MARKDOWN_DIRECTIVE)

    def test_rich_markdown_prompt_requires_formatted_structured_answers(self):
        for instruction in (
            "Every response MUST use Telegram Rich Markdown",
            "Never answer in plain text when Rich Markdown can express the structure",
            "Use a descriptive heading",
            "Use real Markdown lists",
            "Do not output a list of labels as unformatted paragraphs",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, ai.RICH_MARKDOWN_DIRECTIVE)

    def test_rich_markdown_prompt_sets_a_casual_general_audience_style(self):
        for instruction in (
            "friendly, natural, and conversational",
            "Do not default to a coding or tutorial answer",
            "Match the structure to the question",
            "Use emoji",
            "Do not add emojis",
            "Use a table for a real comparison",
            "Do not use a table",
            "short direct answer",
            "For a detailed explanation, use this visual pattern",
            "Never leave list items as bare lines",
            "Do not copy plain-text formatting from conversation history",
            "For detailed explanations, Markdown structure is required, not optional",
            "Use actual Markdown headings and lists",
            "Never leave list items as bare lines",
            "same number of cells in every row",
            "[Pokémon Official](https://www.pokemon.com/)",
            "[Pokédex](https://www.pokemon.com/us/pokedex/)",
            "Prefer Telegram URL buttons for important resources whose",
            "the official Pokédex with url=\"https://www.pokemon.com/us/pokedex/\"",
            "End detailed overviews with a useful, concise **TL;DR**",
            "For answers longer than about 2,500 visible characters",
            "the continuation at the nearest completed paragraph or section boundary",
            "For every detailed franchise overview, include a Markdown comparison table",
            "| Part | What it is | What people do with it |",
            "| Video games | ... | ... |",
            "Do not copy plain-text formatting from conversation history",
            "Prefer Telegram URL buttons for important resources whose",
            "the official Pokédex with url=\"https://www.pokemon.com/us/pokedex/\"",
        ):
            with self.subTest(instruction=instruction):
                self.assertIn(instruction, ai.RICH_MARKDOWN_DIRECTIVE)

    def test_usage_footer_does_not_expose_model_name(self):
        self.assertNotIn(
            "Model:",
            ai._append_token_usage("Answer", {"prompt_tokens": 10, "completion_tokens": 2}),
        )

    def test_rich_markdown_prompt_encourages_native_syntax(self):
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
                self.assertIn(syntax, ai.RICH_MARKDOWN_DIRECTIVE)
