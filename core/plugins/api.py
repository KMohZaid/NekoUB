"""Declarations and decorators for typed plugins."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Mapping, Sequence, TypeVar

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.handlers import (
    CallbackQueryHandler,
    EditedMessageHandler,
    InlineQueryHandler,
    MessageHandler,
)
from pyrogram.types import CallbackQuery, InlineQuery, Message

from .context import PluginContext
from .errors import OptionParseError, parse_options
from .registry import HandlerDeclaration

Callback = TypeVar("Callback", bound=Callable[..., Any])


@dataclass(frozen=True, slots=True)
class Example:
    """A command example rendered using `{cmd}` and `{prefix}` placeholders."""

    text: str
    note: str | None = None

    def render(self, command: str, prefix: str) -> str:
        """Render recognized placeholders without interpreting other braces."""
        return self.text.replace("{cmd}", f"{prefix}{command}").replace(
            "{prefix}", prefix
        )


@dataclass(frozen=True, slots=True)
class Option:
    """Typed option declaration for a command's argument schema."""

    name: str
    short: str | None = None
    type: type[Any] = str
    required: bool = False
    default: Any = None
    action: str = "store"
    append: bool = False
    help: str = ""

    def __post_init__(self) -> None:
        if (
            not self.name
            or self.name.startswith("-")
            or not self.name.replace("_", "a").isalnum()
        ):
            raise ValueError("Option name must be a non-empty long name without dashes")
        if self.required and self.default is not None:
            raise ValueError("Required options cannot also define a default")
        if self.short is not None and (
            len(self.short) != 2 or not self.short.startswith("-")
        ):
            raise ValueError("Short option must use a form such as '-v'")
        if self.action not in {"store", "store_true", "store_false"}:
            raise ValueError(f"Unsupported option action: {self.action}")
        if self.append and self.action != "store":
            raise ValueError("append=True is only valid for value options")


@dataclass(frozen=True, slots=True)
class CommandOptions:
    """Command metadata and parser schema."""

    trigger: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    category: str | None = None
    examples: tuple[Example, ...] = ()
    options: tuple[Option, ...] = ()
    parse_mode: str = "html"
    prefixes: str | tuple[str, ...] = "."


@dataclass(frozen=True, slots=True)
class HandlerOptions:
    """Options passed when building the underlying Kurigram handler."""

    filters: Any = None
    handler_kwargs: Mapping[str, Any] = field(default_factory=dict)
    command: CommandOptions | None = None
    bot: bool = False


def command(
    trigger: str,
    *,
    aliases: Sequence[str] = (),
    description: str = "",
    category: str | None = None,
    examples: Sequence[Example] = (),
    options: Sequence[Option] = (),
    filters: Any = None,
    parse_mode: str = "html",
    prefixes: str | tuple[str, ...] = ".",
    bot: bool = False,
    **handler_kwargs: Any,
) -> Callable[[Callback], Callback]:
    """Declare a command; the plugin manager installs it later."""
    metadata = CommandOptions(
        trigger=trigger,
        aliases=tuple(aliases),
        description=description,
        category=category,
        examples=tuple(examples),
        options=tuple(options),
        parse_mode=parse_mode,
        prefixes=prefixes,
    )
    return _declare(
        "command",
        HandlerOptions(
            filters=filters,
            handler_kwargs=handler_kwargs,
            command=metadata,
            bot=bot,
        ),
    )


def _event_decorator(
    event: str,
    *,
    filters: Any = None,
    bot: bool = False,
    **handler_kwargs: Any,
) -> Callable[[Callback], Callback]:
    return _declare(
        event,
        HandlerOptions(filters=filters, handler_kwargs=handler_kwargs, bot=bot),
    )


def _declare(event: str, options: HandlerOptions) -> Callable[[Callback], Callback]:
    def decorate(callback: Callback) -> Callback:
        declarations = list(getattr(callback, "__nekoub_handlers__", ()))
        declarations.append((event, options))
        setattr(callback, "__nekoub_handlers__", tuple(declarations))
        return callback

    return decorate


def _method_declarations(plugin: Plugin) -> list[HandlerDeclaration]:
    declarations: list[HandlerDeclaration] = []
    for name in dir(type(plugin)):
        callback = getattr(type(plugin), name, None)
        for event, options in getattr(callback, "__nekoub_handlers__", ()):
            declarations.append(
                HandlerDeclaration(
                    plugin_id=plugin.id,
                    method_name=name,
                    event=event,
                    options=options,
                )
            )
    return declarations


def _wrap_handler(
    plugin: Plugin,
    declaration: HandlerDeclaration,
    *,
    config: Mapping[str, Any] | None = None,
    command_prefixes: Sequence[str] = (".",),
    user_client: Client | None = None,
    bot_client: Client | None = None,
) -> Callback:
    original = getattr(plugin, declaration.method_name)
    command_options = declaration.options.command

    @wraps(original)
    async def invoke(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        is_bot_handler = declaration.options.bot or declaration.event.startswith("bot_")
        if is_bot_handler and not getattr(client, "is_bot", False):
            raise RuntimeError("Bot handler was attached to a non-bot client")
        context = PluginContext(
            client=(
                user_client
                if user_client is not None
                else None if is_bot_handler else client
            ),
            update=update,
            bot=(
                bot_client
                if bot_client is not None
                else client if is_bot_handler else None
            ),
            plugin=plugin,
            config=config if config is not None else {},
        )
        if command_options is not None:
            context.command = command_options
            context.parse_mode = command_options.parse_mode
        handler_args = args
        if command_options is not None and declaration.event == "command":
            raw_text = getattr(update, "text", None) or getattr(update, "caption", "") or ""
            if command_options.options:
                try:
                    parsed = parse_options(
                        getattr(update, "text", None) or getattr(update, "caption", "") or "",
                        command_options.options,
                        (command_options.trigger, *command_options.aliases),
                        prefixes=(
                            command_prefixes
                            if command_options.prefixes == "."
                            else command_options.prefixes
                        ),
                    )
                except OptionParseError as error:
                    await context.send(
                        error.as_html(), reply_to=update, parse_mode=ParseMode.HTML
                    )
                    from pyrogram import ContinuePropagation

                    raise ContinuePropagation
            else:
                from .errors import ParsedOptions

                parsed = ParsedOptions(
                    values={},
                    positional=tuple(raw_text.split(maxsplit=1)[1:]),
                )
            handler_args = (parsed, *args)
        from pyrogram import ContinuePropagation

        handler_client = (
            client
            if is_bot_handler
            else user_client if user_client is not None else client
        )
        try:
            await original(handler_client, update, context, *handler_args, **kwargs)
        except Exception as error:
            logging.getLogger("core.plugins").exception(
                "Managed plugin handler failed: %s.%s",
                plugin.id,
                declaration.method_name,
            )
            raise ContinuePropagation from error
        raise ContinuePropagation

    return invoke


class Plugin:
    """Base class for a managed plugin declaration."""

    id: str = ""
    category: str | None = None
    description: str = ""

    def declarations(self) -> tuple[HandlerDeclaration, ...]:
        return tuple(_method_declarations(self))


class HandlerCollection:
    """Collection of supported managed handler decorators."""

    command = staticmethod(command)

    @staticmethod
    def message(
        *, filters: Any = None, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator("message", filters=filters, **handler_kwargs)

    @staticmethod
    def edited_message(
        *, filters: Any = None, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator("edited_message", filters=filters, **handler_kwargs)

    @staticmethod
    def callback_query(
        *, filters: Any = None, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator("callback_query", filters=filters, **handler_kwargs)

    @staticmethod
    def inline_query(
        *, filters: Any = None, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator("inline_query", filters=filters, **handler_kwargs)

    @staticmethod
    def bot_command(
        trigger: str, *, prefixes: str | tuple[str, ...] = "/", **kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return command(trigger, bot=True, prefixes=prefixes, **kwargs)

    @staticmethod
    def bot_callback_query(
        *, filters: Any = None, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator(
            "bot_callback_query", filters=filters, bot=True, **handler_kwargs
        )

    @staticmethod
    def bot_inline_query(
        *, filters: Any = None, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator(
            "bot_inline_query", filters=filters, bot=True, **handler_kwargs
        )


handlers = HandlerCollection()

HANDLER_TYPES = {
    "command": MessageHandler,
    "message": MessageHandler,
    "edited_message": EditedMessageHandler,
    "callback_query": CallbackQueryHandler,
    "inline_query": InlineQueryHandler,
    "bot_command": MessageHandler,
    "bot_callback_query": CallbackQueryHandler,
    "bot_inline_query": InlineQueryHandler,
}

UPDATE_TYPES = {
    "command": Message,
    "message": Message,
    "edited_message": Message,
    "callback_query": CallbackQuery,
    "inline_query": InlineQuery,
    "bot_command": Message,
    "bot_callback_query": CallbackQuery,
    "bot_inline_query": InlineQuery,
}
