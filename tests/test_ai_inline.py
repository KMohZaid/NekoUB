import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

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

    async def test_valid_key_answers_with_one_rich_article(self):
        key = ai._stage_inline_response("**Rich answer**")
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": True}
        client = AsyncMock()
        client.post.return_value = response

        await ai._answer_ai_inline_query(
            client, "query-id", key, "123:secret-token"
        )

        client.post.assert_awaited_once()
        url, = client.post.await_args.args
        self.assertEqual(url, "https://api.telegram.org/bot123:secret-token/answerInlineQuery")
        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(payload["inline_query_id"], "query-id")
        self.assertEqual(payload["cache_time"], 0)
        self.assertTrue(payload["is_personal"])
        self.assertEqual(len(payload["results"]), 1)
        result = payload["results"][0]
        self.assertEqual(result["type"], "article")
        self.assertEqual(
            result["input_message_content"],
            {"rich_message": {"markdown": "**Rich answer**"}},
        )

    async def test_unknown_key_answers_with_empty_result_list(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"ok": True, "result": True}
        client = AsyncMock()
        client.post.return_value = response

        await ai._answer_ai_inline_query(client, "query-id", "unknown", "token")

        payload = client.post.await_args.kwargs["json"]
        self.assertEqual(payload["results"], [])

    async def test_bot_api_failure_is_raised(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "ok": False,
            "description": "Bad Request",
        }
        client = AsyncMock()
        client.post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "Bad Request"):
            await ai._answer_ai_inline_query(client, "query-id", "unknown", "token")


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

    def test_rejects_empty_answer(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            ai._prepare_rich_answer("  \n")

    def test_token_usage_footer_formats_counts_and_includes_duration(self):
        self.assertEqual(
            ai._append_token_usage(
                "Answer",
                {"prompt_tokens": 1864, "completion_tokens": 100398000},
                elapsed_seconds=2.4,
            ),
            "Answer\n\nInput tokens: 1,864 (1.9k) · "
            "Output tokens: 100,398,000 (100.4M) · Total: 100,399,864 (100.4M) · Time: 2.4s",
        )

    def test_missing_token_usage_still_shows_elapsed_time(self):
        self.assertEqual(
            ai._append_token_usage("Answer", None, elapsed_seconds=1.2),
            "Answer\n\nTime: 1.2s",
        )

    def test_rich_markdown_prompt_encourages_native_syntax(self):
        for syntax in (
            "<details open>", "table", "$$", "```math", '<tg-button type="url"',
            'style="success"', "blockquote", "footnote", "spoiler", "underline",
            "superscript", "subscript", "do not claim highlighting is guaranteed",
            "Prefer URL buttons", "supported language tag", "libprisma",
            "For answers longer than about 3,500 characters",
            '<summary>Show more</summary>',
        ):
            with self.subTest(syntax=syntax):
                self.assertIn(syntax, ai.RICH_MARKDOWN_DIRECTIVE)
