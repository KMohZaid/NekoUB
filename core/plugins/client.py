"""Kurigram client extension used by the plugin manager."""

from __future__ import annotations

from typing import Any, TypeVar

from pyrogram import Client

HandlerT = TypeVar("HandlerT")


class PluginClient(Client):
    """Client subclass exposing the managed handler registration boundary."""

    def register_managed_handler(
        self,
        handler: HandlerT,
        group: int = 0,
        *,
        plugin_id: str,
        event: str,
    ) -> tuple[Any, int]:
        """Install a handler that has already passed the plugin manager checks."""
        if not plugin_id or not event:
            raise ValueError("Managed registrations require plugin and event metadata")
        registration = super().add_handler(handler, group)
        if not hasattr(self, "_managed_plugin_handlers"):
            self._managed_plugin_handlers = {}
        self._managed_plugin_handlers.setdefault(plugin_id, []).append(
            (handler, group, event)
        )
        return registration

    def remove_managed_handler(
        self, handler: HandlerT, group: int
    ) -> None:
        """Remove one handler instance previously installed by the manager."""
        super().remove_handler(handler, group)
        registration = (handler, group)
        for plugin_id, handlers in list(
            getattr(self, "_managed_plugin_handlers", {}).items()
        ):
            remaining = [item for item in handlers if item[:2] != (handler, group)]
            if remaining:
                self._managed_plugin_handlers[plugin_id] = remaining
            else:
                del self._managed_plugin_handlers[plugin_id]
        return registration
