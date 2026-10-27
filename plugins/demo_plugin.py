"""Public example for the managed typed plugin API."""

from pyrogram import Client
from pyrogram.types import CallbackQuery, InlineQuery, Message

from core.plugins import (
    Example,
    F,
    Option,
    ParsedOptions,
    Plugin,
    PluginContext,
    handlers as h,
)


class DemoPlugin(Plugin):
    id = "demo"
    category = "Examples"
    description = "Demonstrates typed managed handlers and context helpers."

    @h.command(
        "hello",
        aliases=("hi",),
        description="Send a greeting.",
        examples=(Example("{cmd}"), Example("{cmd} --name Ada")),
        options=(
            Option("name", type=str, default="there", help="Name to greet."),
        ),
        filters=F.text,
    )
    async def hello(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["DemoPlugin"],
        args: ParsedOptions,
    ) -> None:
        await ctx.send(f"Hello, {args.name}!", reply_to=message)

    @h.message(filters=F.text & F.me)
    async def observe_messages(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["DemoPlugin"],
        *handler_args: object,
        **handler_kwargs: object,
    ) -> None:
        """Demonstrate broad capture; intentionally does not answer."""

    @h.edited_message(filters=F.text & F.me)
    async def observe_edits(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["DemoPlugin"],
        *handler_args: object,
        **handler_kwargs: object,
    ) -> None:
        """Demonstrate edited-message capture."""

    @h.callback_query()
    async def callbacks(
        self,
        client: Client,
        callback: CallbackQuery,
        ctx: PluginContext["DemoPlugin"],
        *handler_args: object,
        **handler_kwargs: object,
    ) -> None:
        if callback.data == "demo:hello":
            await callback.answer("Hello from the demo plugin!")

    @h.inline_query()
    async def inline_queries(
        self,
        client: Client,
        query: InlineQuery,
        ctx: PluginContext["DemoPlugin"],
        *handler_args: object,
        **handler_kwargs: object,
    ) -> None:
        """Example typed entry point; no inline result is sent by default."""
