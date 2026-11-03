"""Starter template for a typed NekoUB managed plugin.

Copy this module into the public plugins/ directory and customize its
metadata and handlers.
"""

from pyrogram import Client
from pyrogram.types import Message

from core.plugins import (
    Example,
    F,
    Option,
    ParsedOptions,
    Plugin,
    PluginContext,
    handlers as h,
)


class GreetingPlugin(Plugin):
    id = "greeting"
    category = "Examples"
    description = "A typed example plugin."

    @h.command(
        "hello",
        aliases=("hi",),
        description="Send a greeting.",
        examples=(Example("{cmd}"), Example("{cmd} --name Ada")),
        options=(
            Option(
                "name",
                type=str,
                required=False,
                default="there",
                help="Name to greet.",
            ),
        ),
        filters=F.text,
    )
    async def hello(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["GreetingPlugin"],
        args: ParsedOptions,
    ) -> None:
        await ctx.send(f"Hello, {args.name}!", reply_to=message)

    @h.edited_message(filters=F.text)
    @h.message(filters=F.text)
    async def observe_text(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["GreetingPlugin"],
        *handler_args: object,
        **handler_kwargs: object,
    ) -> None:
        """One method can handle new and edited messages."""
        if message.text:
            await ctx.send(
                f"Observed text: {message.text}",
                reply_to=message,
            )
