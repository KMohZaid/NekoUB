"""Plugin declaration records and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .api import HandlerOptions


@dataclass(frozen=True, slots=True)
class HandlerDeclaration:
    plugin_id: str
    method_name: str
    event: str
    options: HandlerOptions


@dataclass(frozen=True, slots=True)
class PluginDeclaration:
    plugin_id: str
    category: str | None
    description: str
    handlers: tuple[HandlerDeclaration, ...]


@dataclass
class PluginRegistry:
    """In-memory declaration registry."""

    plugins: dict[str, PluginDeclaration] = field(default_factory=dict)

    def register(self, plugin: Any) -> PluginDeclaration:
        plugin_id = plugin.id
        if not plugin_id:
            raise ValueError("Plugin id must not be empty")
        if plugin_id in self.plugins:
            raise ValueError(f"Duplicate plugin id: {plugin_id}")

        handlers = plugin.declarations()
        if not handlers:
            raise ValueError(f"Plugin {plugin_id!r} declares no handlers")
        declaration = PluginDeclaration(
            plugin_id=plugin_id,
            category=plugin.category,
            description=plugin.description,
            handlers=handlers,
        )
        self.plugins[plugin_id] = declaration
        return declaration

    def unregister(self, plugin_id: str) -> PluginDeclaration | None:
        return self.plugins.pop(plugin_id, None)
