"""Per-update context and convenience methods for managed handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Mapping, TypeVar

from pyrogram.errors import RPCError

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import Message

PluginT = TypeVar("PluginT")


def resolve_parse_mode(value: Any) -> ParseMode | Any:
    """Convert supported string aliases to Kurigram parse mode enums."""
    if isinstance(value, ParseMode):
        return value
    if isinstance(value, str):
        aliases = {
            "html": ParseMode.HTML,
            "markdown": ParseMode.MARKDOWN,
            "md": ParseMode.MARKDOWN,
            "default": ParseMode.DEFAULT,
            "disabled": ParseMode.DISABLED,
            "none": ParseMode.DISABLED,
        }
        return aliases.get(value.strip().lower(), value)
    return value


@dataclass(slots=True)
class PluginContext(Generic[PluginT]):
    """Framework helpers for one managed handler invocation.

    ``config`` is the shared application settings mapping supplied to the
    PluginManager. The app loads environment-backed values in ``config.py``.
    """

    client: Client[Any] | None
    update: Any
    plugin: PluginT
    bot: Client[Any] | None = None
    config: Mapping[str, Any] = field(default_factory=dict)
    command: Any = None
    parse_mode: ParseMode | str = ParseMode.HTML

    def get_config(self, key: str, default: Any = None) -> Any:
        """Return a shared application setting, falling back to ``default``."""
        return self.config.get(key, default)

    def get_config_typed(self, key: str, value_type: type[Any], default: Any = None) -> Any:
        """Return a setting coerced to a requested type."""
        value = self.config.get(key, default)
        if value is None:
            return None
        try:
            return value_type(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Configuration value {key!r} has an invalid type") from error

    async def send(
        self,
        text: str,
        *,
        reply_to: Message | None = None,
        split: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Send text, splitting oversized output when requested."""
        parse_mode = resolve_parse_mode(
            kwargs.setdefault("parse_mode", self.parse_mode)
        )
        kwargs["parse_mode"] = parse_mode
        limit = 4096
        if _utf16_length(text) > limit and not split:
            raise ValueError(f"Message exceeds Telegram's {limit}-character text limit")
        chunks = (
            _split_html(text, limit)
            if _utf16_length(text) > limit and split and _is_html(parse_mode)
            else _split_text(text, limit)
            if _utf16_length(text) > limit and split
            else [text]
        )

        sent: list[Any] = []
        for index, chunk in enumerate(chunks):
            send_kwargs = dict(kwargs)
            if index and "reply_markup" in send_kwargs:
                send_kwargs.pop("reply_markup")
            if reply_to is not None:
                sent.append(await reply_to.reply_text(chunk, **send_kwargs))
            else:
                chat = getattr(self.update, "chat", None)
                if chat is None:
                    raise ValueError("ctx.send requires reply_to or an update with a chat")
                if self.client is None:
                    raise ValueError("ctx.send requires a userbot client or reply_to")
                sent.append(await self.client.send_message(chat.id, chunk, **send_kwargs))
        return sent[0] if len(sent) == 1 else sent

    async def edit(self, text: str, *, message: Message, **kwargs: Any) -> Any:
        """Edit an existing message using the default HTML parse mode."""
        kwargs["parse_mode"] = resolve_parse_mode(
            kwargs.setdefault("parse_mode", self.parse_mode)
        )
        return await message.edit_text(text, **kwargs)

    async def react_or_send(
        self,
        reaction: str,
        text: str,
        *,
        reply_to: Message | None = None,
        **kwargs: Any,
    ) -> Any:
        """React to the current message, falling back to a text reply."""
        target = (
            self.update
            if isinstance(self.update, Message)
            else getattr(self.update, "message", None)
        )
        if target is not None:
            try:
                await target.react(reaction)
                return target
            except RPCError:
                pass
        return await self.send(text, reply_to=reply_to or target, **kwargs)

    async def react_or_edit(
        self,
        reaction: str,
        text: str,
        *,
        message: Message,
        **kwargs: Any,
    ) -> Any:
        """React to the current message, falling back to editing a response."""
        target = (
            self.update
            if isinstance(self.update, Message)
            else getattr(self.update, "message", None)
        )
        if target is not None:
            try:
                await target.react(reaction)
                return target
            except RPCError:
                pass
        return await self.edit(text, message=message, **kwargs)


def _is_html(parse_mode: Any) -> bool:
    return parse_mode == ParseMode.HTML or str(parse_mode).lower() in {
        "html",
        "parsemode.html",
    }


def _split_html(text: str, limit: int) -> list[str]:
    """Split HTML while carrying open tags into each chunk."""
    from html.parser import HTMLParser

    class Splitter(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)
            self.chunks: list[str] = []
            self.current: list[str] = []
            self.stack: list[tuple[str, str]] = []
            self.length = 0

        def _flush(self) -> None:
            if not self.current:
                return
            if self.length == 0 and self.chunks:
                self.current = []
                self.length = 0
                return
            self.current.extend(f"</{tag}>" for tag, _ in reversed(self.stack))
            self.chunks.append("".join(self.current))
            self.current = [opening for _, opening in self.stack]
            self.length = 0

        def _append(self, value: str, visible: int = 0) -> None:
            if visible and self.length + visible > limit and self.length:
                self._flush()
            self.current.append(value)
            self.length += visible

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            raw = self.get_starttag_text()
            self._append(raw)
            if tag not in {"br", "hr", "img", "input", "meta", "link"}:
                self.stack.append((tag, raw))

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            self._append(self.get_starttag_text())

        def handle_endtag(self, tag: str) -> None:
            self._append(f"</{tag}>")
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    break

        def handle_data(self, data: str) -> None:
            for char in data:
                self._append(char, len(char.encode("utf-16-le")) // 2)

        def handle_entityref(self, name: str) -> None:
            from html.entities import html5

            decoded = html5.get(f"{name};", "�")
            self._append(f"&{name};", _utf16_length(decoded))

        def handle_charref(self, name: str) -> None:
            try:
                value = chr(
                    int(name[2:], 16)
                    if name.lower().startswith("#x")
                    else int(name[1:])
                )
                visible = _utf16_length(value)
            except (ValueError, UnicodeEncodeError):
                visible = 1
            self._append(f"&#{name};", visible)

        def handle_comment(self, data: str) -> None:
            self._append(f"<!--{data}-->")

        def handle_decl(self, decl: str) -> None:
            self._append(f"<!{decl}>")

    parser = Splitter()
    try:
        parser.feed(text)
        parser.close()
    except Exception as error:
        raise ValueError("Cannot split malformed HTML message") from error
    parser._flush()
    if any(_visible_length(chunk) > limit for chunk in parser.chunks):
        raise ValueError("HTML markup is too large to split safely")
    return parser.chunks or [""]


def _visible_length(value: str) -> int:
    from html.parser import HTMLParser

    class Counter(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.length = 0

        def handle_data(self, data: str) -> None:
            self.length += _utf16_length(data)

    counter = Counter()
    counter.feed(value)
    counter.close()
    return counter.length


def _split_text(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while _utf16_length(remaining) > limit:
        length = 0
        boundary = 0
        preferred = 0
        for index, char in enumerate(remaining):
            char_length = len(char.encode("utf-16-le")) // 2
            if length + char_length > limit:
                break
            length += char_length
            boundary = index + 1
            if char.isspace():
                preferred = boundary
        if preferred:
            boundary = preferred
        chunks.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
