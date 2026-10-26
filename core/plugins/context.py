"""Per-update context and convenience methods for managed handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Mapping, TypeVar

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import Message

PluginT = TypeVar("PluginT")


@dataclass(slots=True)
class PluginContext(Generic[PluginT]):
    """Framework helpers for one managed handler invocation."""

    client: Client[Any]
    update: Any
    plugin: PluginT
    config: Mapping[str, Any] = field(default_factory=dict)
    command: Any = None

    async def get_config(self, key: str, default: Any = None) -> Any:
        """Return a plugin setting, falling back to ``default``."""
        return self.config.get(key, default)

    async def get_config_typed(self, key: str, value_type: type[Any]) -> Any:
        """Return a setting coerced to a requested type."""
        value = self.config[key]
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
        kwargs.setdefault("parse_mode", ParseMode.HTML)
        limit = 4096
        chunks = _split_text(text, limit) if len(text) > limit and split else [text]
        if len(text) > limit and not split:
            raise ValueError(f"Message exceeds Telegram's {limit}-character text limit")

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
                sent.append(await self.client.send_message(chat.id, chunk, **send_kwargs))
        return sent[0] if len(sent) == 1 else sent

    async def edit(self, text: str, *, message: Message, **kwargs: Any) -> Any:
        """Edit an existing message using the default HTML parse mode."""
        kwargs.setdefault("parse_mode", ParseMode.HTML)
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
            except Exception:
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
            except Exception:
                pass
        return await self.edit(text, message=message, **kwargs)


def _split_text(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        boundary = max(remaining.rfind("\n", 0, limit + 1), remaining.rfind(" ", 0, limit + 1))
        if boundary <= 0:
            boundary = limit
        chunks.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks
