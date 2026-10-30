import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from core.plugins import handlers as h
from plugins.eval import EvalPlugin
from plugins.exec import ExecPlugin
from plugins.tts import TTSPlugin


class DelegatedCommandTests(unittest.TestCase):
    def test_eval_and_exec_are_sudo_routes(self):
        for plugin_type in (EvalPlugin, ExecPlugin):
            command = plugin_type().declarations()[0].options.command
            with self.subTest(plugin=plugin_type.id):
                self.assertTrue(command.sudo)
                self.assertFalse(command.share)
                self.assertFalse(command.grantable)

    def test_tts_and_alias_are_share_routes_without_owner_filter(self):
        plugin = TTSPlugin()
        declaration = plugin.declarations()[0]
        command = declaration.options.command

        self.assertTrue(command.share)
        self.assertTrue(command.grantable)
        self.assertIn("tts", command.aliases)
        self.assertIsNone(declaration.options.filters)

    def test_delegated_eval_replies_instead_of_editing_command(self):
        plugin = EvalPlugin()
        message = type(
            "Message",
            (),
            {
                "text": "#eval 1 + 1",
                "chat": type("Chat", (), {"id": 5})(),
                "id": 10,
                "edit": AsyncMock(),
                "reply_text": AsyncMock(),
                "from_user": type("User", (), {"id": 2})(),
            },
        )()
        client = object()
        context = type("Context", (), {"actor_is_owner": False, "send": AsyncMock()})()

        async def run_task(code, client, message, ctx=None):
            return await ctx.send("result", reply_to=message)

        with patch("plugins.eval.run_eval_task", run_task):
            asyncio.run(plugin.eval_command(client, message, context, None))

        self.assertEqual(context.send.await_count, 2)
        self.assertTrue(all(call.kwargs.get("reply_to") is message for call in context.send.await_args_list))
        message.edit.assert_not_awaited()

    def test_delegated_tts_does_not_delete_command(self):
        declaration = TTSPlugin().declarations()[0]
        self.assertTrue(declaration.options.command.share)

    def test_delegated_eval_error_replies_instead_of_editing_command(self):
        from plugins.eval import run_eval_task

        message = type("Message", (), {"edit": AsyncMock()})()
        context = type("Context", (), {"actor_is_owner": False, "send": AsyncMock()})()
        with patch("plugins.eval.format_output", side_effect=RuntimeError("format failed")):
            asyncio.run(run_eval_task("1", object(), message, context))

        message.edit.assert_not_awaited()
        context.send.assert_awaited_once()
        self.assertIs(context.send.await_args.kwargs["reply_to"], message)

    def test_delegated_exec_error_replies_instead_of_editing_command(self):
        from plugins.exec import run_exec_task

        message = type("Message", (), {"edit": AsyncMock()})()
        context = type("Context", (), {"actor_is_owner": False, "send": AsyncMock()})()
        with patch("plugins.exec.asyncio.create_subprocess_shell", side_effect=RuntimeError("exec failed")):
            asyncio.run(run_exec_task("false", message, context))

        message.edit.assert_not_awaited()
        context.send.assert_awaited_once()
        self.assertIs(context.send.await_args.kwargs["reply_to"], message)


if __name__ == "__main__":
    unittest.main()
