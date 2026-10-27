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
    GuestMessageHandler,
    InlineQueryHandler,
    MessageHandler,
)
from pyrogram.types import CallbackQuery, InlineQuery, Message

from .context import PluginContext, resolve_parse_mode
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
    aliases: tuple[str, ...] = ()
    label: str | None = None

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
            not self.short.startswith("-") or len(self.short) < 2
        ):
            raise ValueError("Short option must start with '-' and include a name")
        if any(not alias or not alias.startswith(("-", "#")) for alias in self.aliases):
            raise ValueError("Option aliases must use a command option or marker prefix")
        if self.action not in {"store", "store_true", "store_false"}:
            raise ValueError(f"Unsupported option action: {self.action}")
        if self.append and self.action != "store":
            raise ValueError("append=True is only valid for value options")


@dataclass(frozen=True, slots=True)
class CommandOptions:
    """Command metadata and parser schema."""

    trigger: str
    aliases: tuple[str, ...] = ()
    signature: str = ""
    description: str = ""
    short_description: str = ""
    help_entries: tuple[Mapping[str, Any], ...] = ()
    help_options: tuple[Option | Mapping[str, str], ...] = ()
    example_notes: tuple[str, ...] = ()
    category: str | None = None
    examples: tuple[Example, ...] = ()
    options: tuple[Option, ...] = ()
    hidden_from_help: bool = False
    parse_mode: ParseMode | str = ParseMode.HTML
    prefixes: str | tuple[str, ...] = "."

    def __post_init__(self) -> None:
        object.__setattr__(self, "parse_mode", resolve_parse_mode(self.parse_mode))


@dataclass(frozen=True, slots=True)
class HandlerOptions:
    """Options passed when building the underlying Kurigram handler."""

    filters: Any = None
    handler_kwargs: Mapping[str, Any] = field(default_factory=dict)
    command: CommandOptions | None = None
    bot: bool = False
    public: bool = False


def command(
    trigger: str,
    *,
    aliases: Sequence[str] = (),
    signature: str = "",
    description: str = "",
    short_description: str = "",
    help_entries: Sequence[Mapping[str, Any]] = (),
    help_options: Sequence[Option | Mapping[str, str]] = (),
    example_notes: Sequence[str] = (),
    category: str | None = None,
    examples: Sequence[Example] = (),
    options: Sequence[Option] = (),
    hidden_from_help: bool = False,
    filters: Any = None,
    parse_mode: ParseMode | str = ParseMode.HTML,
    prefixes: str | tuple[str, ...] = ".",
    bot: bool = False,
    public: bool = False,
    **handler_kwargs: Any,
) -> Callable[[Callback], Callback]:
    """Declare a command; the plugin manager installs it later."""
    metadata = CommandOptions(
        trigger=trigger,
        aliases=tuple(aliases),
        signature=signature,
        description=description,
        short_description=short_description,
        help_entries=tuple(dict(entry) for entry in help_entries),
        help_options=tuple(
            option if isinstance(option, Option) else dict(option)
            for option in help_options
        ),
        example_notes=tuple(example_notes),
        category=category,
        examples=tuple(examples),
        options=tuple(options),
        hidden_from_help=hidden_from_help,
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
            public=public,
        ),
    )


def _event_decorator(
    event: str,
    *,
    filters: Any = None,
    bot: bool = False,
    public: bool = False,
    **handler_kwargs: Any,
) -> Callable[[Callback], Callback]:
    return _declare(
        event,
        HandlerOptions(
            filters=filters,
            handler_kwargs=handler_kwargs,
            bot=bot,
            public=public,
        ),
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
        from pyrogram import ContinuePropagation

        is_bot_handler = declaration.options.bot or declaration.event.startswith("bot_")
        if is_bot_handler:
            is_configured_bot = (
                client is bot_client
                if bot_client is not None
                else bool(
                    getattr(client, "is_bot", False)
                    or getattr(client, "bot_token", None)
                )
            )
            if not is_configured_bot:
                raise RuntimeError("Bot handler was attached to a non-bot client")
        if declaration.event in {
            "callback_query",
            "inline_query",
            "bot_callback_query",
            "bot_inline_query",
        } and not declaration.options.public or (
            is_bot_handler
            and declaration.event == "command"
            and not declaration.options.public
        ):
            owner_client = user_client or (None if is_bot_handler else client)
            owner = getattr(owner_client, "me", None) if owner_client else None
            if owner is None and owner_client is not None:
                try:
                    owner = await owner_client.get_me()
                except Exception:
                    owner = None
            sender = getattr(update, "from_user", None)
            is_owner = bool(owner and sender and owner.id == sender.id)
            if not is_owner:
                if "inline_query" in declaration.event:
                    from pyrogram.types import InlineQueryResultArticle, InputTextMessageContent

                    await update.answer(
                        [InlineQueryResultArticle(
                            title="Owner only",
                            input_message_content=InputTextMessageContent(
                                "You can't perform this action."
                            ),
                            description="You can't perform this action.",
                        )],
                        cache_time=0,
                        is_personal=True,
                    )
                else:
                    await update.answer("You can't perform this action.", show_alert=True)
                raise ContinuePropagation
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
            context.parse_mode = resolve_parse_mode(command_options.parse_mode)
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
    display_name: str = ""

    def disabled_reason(self) -> str | None:
        """Return why this plugin should not load, or None when it is ready."""
        return None

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
        *, filters: Any = None, public: bool = False, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator(
            "bot_callback_query",
            filters=filters,
            bot=True,
            public=public,
            **handler_kwargs,
        )

    @staticmethod
    def bot_inline_query(
        *, filters: Any = None, public: bool = False, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator(
            "bot_inline_query",
            filters=filters,
            bot=True,
            public=public,
            **handler_kwargs,
        )

    @staticmethod
    def bot_guest_message(
        *, filters: Any = None, **handler_kwargs: Any
    ) -> Callable[[Callback], Callback]:
        return _event_decorator(
            "bot_guest_message",
            filters=filters,
            bot=True,
            **handler_kwargs,
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
    "bot_guest_message": GuestMessageHandler,
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
    "bot_guest_message": Message,
}
